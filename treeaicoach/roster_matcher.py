"""Roster-driven champion detection on the minimap (no machine learning).

The Live Client Data API tells us exactly which 10 champions (and skins) are in the game,
and :meth:`ChampionDB.load_icon` gives their round portraits. Instead of a generic
"is this a champion icon?" detector, :class:`RosterMatcher` looks for *those* portraits:

1. :meth:`RosterMatcher.set_roster` keeps one portrait per player with its relation to the
   local player. Templates are the portrait's inner disc only (``INNER_RATIO`` of the icon
   radius): the coloured ring and the dark line under it are excluded.
2. **Live scale calibration** (:meth:`RosterMatcher.calibrate`): the icon diameter / minimap
   width ratio differs between clients, resolutions and HUD scales. On the first frames
   of a game (and again when the matches become weak) every portrait is searched over
   diameters of ``SCALE_MIN``..``SCALE_MAX`` of the minimap width; the ratio where the
   roster portraits stand out best from the background (robust median over the
   champions) wins. It is kept in memory and in ``scale_store`` (the config's
   ``icon_scale_by_res``, keyed by the minimap size in pixels) as the prior of the next game.
3. **Per frame** at the calibrated scale: masked zero-mean normalized cross-correlation
   (NCC) of every portrait over the minimap (Lab channels; the image is downscaled so that
   the matched disc is at most ``WORK_INNER_PX`` pixels wide; all correlations are done in
   the Fourier domain: a few milliseconds for 10 champions), local maxima (NMS), a contrast
   consistency check (a dark portrait must not match a flat dark area). The evidence of a
   peak is its NCC + a uniqueness bonus (a champion is on the map once: its true peak stands
   out from its other peaks) + the ring colour agreement (step 4); it is accepted above an
   adaptive threshold derived from the background score distribution (the champions'
   secondary peaks over the recent frames), then conflicts are resolved (one position per
   champion; two champions cannot share a spot unless they are visibly stacked); sub-pixel
   refinement of the positions.
4. **Ring colour second opinion**: the annulus of each match is sampled and compared to the
   enemy / ally ring colours, which are *learned live* from confident matches (adaptive Lab
   centroids: any client colour, colourblind mode, JPEG or photo colour shifts). A match
   whose ring clearly has the other team's colour is rejected unless the portrait match is
   very strong. The learned colours are exposed by :attr:`RosterMatcher.ring_colors` to
   calibrate the no-roster fallback detector (:meth:`ClassicDetector.set_ring_colors`).

Detections carry ``alias`` (the champion) and ``cls`` from the roster relation
(``enemy`` / ``ally``; the local player is ``ally`` visually and ``self`` by identity, see
identifier.py). Nothing raises: errors are logged (rate limited) and give no detection.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

import cv2
import numpy as np

from treeaicoach.detector import Detection, _as_bgr, _RateLimitedLog

log = logging.getLogger(__name__)

# ======================================================================================
# Constants (geometry from docs/MINIMAP_FACTS.md and render.py)
# ======================================================================================

#: Matched inner disc / icon (ring outer) radius: excludes the ring (13 %), the dark line
#: (6 %) and a margin for blur and position errors.
INNER_RATIO = 0.70
#: Portrait radius / icon radius (ring 13 % + dark line 6 %).
PORTRAIT_RATIO = 0.81
#: Part of the square portrait image visible in the portrait disc (render.PORTRAIT_FILL).
PORTRAIT_FILL = 0.94
#: Searched icon DIAMETER / minimap width (calibration).
SCALE_MIN = 0.06
SCALE_MAX = 0.14
SCALE_STEP = 1.06                  # multiplicative step of the coarse sweep
#: Prior ratio before any calibration (real clients: 0.088-0.10).
DEFAULT_SCALE = 0.094
#: Fine calibration steps around the best coarse scale; the result is the centre of the
#: quality plateau (values within PLATEAU of the best).
FINE_STEPS = (0.92, 0.95, 0.98, 1.0, 1.02, 1.05, 1.08)
PLATEAU = 0.012
#: Working resolutions: diameter (px) of the matched disc (detection / calibration sweep).
WORK_INNER_PX = 14.0
CALIB_INNER_PX = 10.0
#: Weight of the lightness NCC in the score (the rest: chroma NCC, a and b jointly).
LIGHTNESS_WEIGHT = 0.5
#: Gaussian blur (working px) of image and templates (JPEG / photo robustness).
BLUR_SIGMA = 0.7
#: Regularization of the local variance (per pixel, Lab units^2, per channel): flat areas
#: (walls, fog, colourless terrain) cannot produce high scores.
VAR_EPS = (12.0, 12.0)            # lightness, chroma (a + b)
#: Local contrast / template contrast accepted (portrait vs flat or busy area).
CONTRAST_RANGE = (0.45, 2.4)
#: Peaks per champion considered for the assignment.
PEAKS_PER_CHAMP = 3
#: Evidence of a match = NCC + UNIQUE_WEIGHT * min(NCC - the champion's next best peak,
#: UNIQUE_CAP): a champion is on the map at most once, so its true position stands out from
#: its other peaks, while a portrait matching the terrain by chance matches it in many places.
UNIQUE_WEIGHT = 0.8
UNIQUE_CAP = 0.15
#: Adaptive acceptance threshold (evidence units) = high percentile of the background
#: peaks (the champions' secondary peaks, recent frames) + THR_MARGIN, within bounds.
THR_MIN = 0.64
THR_MAX = 0.90
THR_MARGIN = 0.14
#: Verified zone: with the patch verifier (patch_classifier.py, icon vs distractor), a
#: candidate may be accepted down to VERIFY_ZONE below the threshold when the verifier says
#: it is a champion icon (p_icon >= VERIFY_MIN) of the candidate's team (the team
#: probability >= VERIFY_TEAM of the icon probability). Measured (det_gym + real crops): the
#: matches a lower threshold adds on structure glyphs / terrain get p_icon 0.00-0.01, real
#: visible icons 0.6-1.0.
VERIFY_ZONE = 0.14
VERIFY_MIN = 0.7
VERIFY_TEAM = 0.5
#: Appearance veto: a champion that was not tracked a moment ago (no match for
#: VETO_FRESH_S), or a far jump, accepted with less than VETO_MARGIN above the threshold
#: is dropped when the verifier gives it an icon probability below VETO_MAX (measured,
#: det_gym: red pings / glyphs matching a fogged champion's portrait at 0.002-0.008, real
#: icons, stacked ones included, >= 0.01 at such scores).
VETO_FRESH_S = 1.0
#: Ring second opinion (see RosterMatcher._ring_second_opinion): candidates whose ring has
#: less than RING_VERIFY_OWN of their team's colour get the verifier's team probability
#: instead when it is >= RING_VERIFY_TEAM (at most RING_VERIFY_MAX per frame).
RING_VERIFY_MAX = 8
RING_VERIFY_OWN = 0.3
RING_VERIFY_TEAM = 0.95
VETO_MARGIN = 0.12
VETO_MAX = 0.03
THR_BG_PCT = 97
#: Weight of the ring colour agreement (own colour fraction - other team's fraction).
RING_WEIGHT = 0.2
#: Penalty when (almost) no ring pixel has the champion's team colour: a portrait-like
#: patch of terrain or a structure glyph has no ring around it.
NO_RING_PENALTY = 0.12
#: Peaks below this NCC are ignored.
PEAK_FLOOR = 0.35
#: A match this strong (evidence) is accepted even when the ring colour disagrees.
STRONG_SCORE = 0.95
#: Matches at least this confident (and with a consistent ring) teach the ring colours.
LEARN_SCORE = 0.88
#: Two accepted icons closer than STACK_FRAC x diameter must both be confident (stacked
#: icons); closer than MIN_SEP_FRAC x diameter they are the same spot.
STACK_FRAC = 0.75
MIN_SEP_FRAC = 0.33
#: ... a candidate that close to an accepted icon is still a (stacked) champion when its
#: score clears the threshold by this margin and its ring has its own team's colour
STACK_RING_MARGIN = 0.05
STACK_RING_OWN = 0.6
STACK_RING_OPP = 0.12
#: Re-calibrate when the mean number of confident matches over the recent frames drops
#: below this fraction of the value measured right after calibration.
RECAL_DROP = 0.5
RECAL_WINDOW = 24
RECAL_MIN_FRAMES = 48             # frames between two automatic re-calibrations
RECAL_WEAK_FRAMES = 240           # ... triggered by weak matches (narrow, ~30 s at 8 fps)
CALIB_FRAMES = 3                  # first frames combined for the initial calibration
REFRESH_FRAMES = 80               # (kept for compatibility)
SKIN_CHECK_FRAMES = 24            # period of the check for newly downloaded skin portraits
#: Calibration quality (evidence units) below which the result is ignored.
MIN_CALIB_QUALITY = 0.62
#: Champions averaged by the calibration quality, and weight of the log-normal scale prior.
CALIB_TOP_K = 3
CALIB_PRIOR_WEIGHT = 1.5

# --- temporal tracking (per champion) -------------------------------------------------
#: Frames between two full searches (every champion over the whole map: refreshes the
#: uniqueness margins and the background statistics, catches a track stuck on a wrong spot).
FULL_EVERY = 24
#: A tracked champion is searched only in a small window around its predicted position;
#: after this many consecutive local misses (or TRACK_FRESH_S without a match) it is
#: searched over the whole map again (in the same frame when the local search fails).
TRACK_FRESH_S = 1.5
#: Champion speed bound (normalized map units / s: ~1350 game units / s, fast champion
#: with haste) and slack (flash / dash / detection noise), for the impossible-jump test.
MAX_SPEED = 0.09
JUMP_SLACK = 0.035
#: Local search window radius = LOCAL_SLACK + MAX_SPEED x dt (normalized).
LOCAL_SLACK = 0.015
#: Evidence penalty of an impossible jump (far from the track, not confirmed by a second
#: frame, not to the champion's fountain): a strong match still passes.
JUMP_PENALTY = 0.15
#: Tracks older than this are forgotten for the jump test (long fog: anywhere is possible).
JUMP_MEMORY_S = 6.0
#: An impossible jump (teleport) is believed once the far candidate was seen at the same
#: spot over JUMP_CONFIRM_S in at least JUMP_CONFIRM_N frames (measured, det_gym: a single
#: confirming frame let a structure glyph matching an occluded champion's portrait steal
#: his identity for seconds; a glyph confirms itself on every frame).
JUMP_CONFIRM_S = 1.5
RECALL_STILL_SPEED = 0.006          # (ally / me) last known speed below this: may be recalling
JUMP_CONFIRM_N = 4
#: Smoothing of the reported confidence (weight of the new frame).
CONF_SMOOTH = 0.5
#: The local player: kept at its predicted position for up to SELF_COAST_S when its icon is
#: momentarily not matched (stacked under an enemy, ping on it...), threshold relaxed by
#: SELF_RELAX near its track or near the camera rectangle centre (camera locked).
SELF_COAST_S = 1.2
SELF_COAST_VEL_S = 0.25            # ... extrapolating the last velocity for this long at most
SELF_RELAX = 0.1
#: Me under an ally icon: the camera point stays on the same ally icon this many frames while
#: my icon is not seen -> my position is that icon's (detection score UNDER_SCORE).
UNDER_CONFIRM = 6
UNDER_SCORE = 0.3
UNDER_STABLE = 0.012                # ... at a stable offset from the camera point
UNDER_VERIFY = 0.8                  # ... or the verifier's icon probability at the camera point
SELF_COAST_UNDER = True             # no coasting while an accepted icon covers my track
SELF_CAM_DIST = 0.06
#: Track hysteresis (every champion): a champion matched in the previous frames
#: (TRACK_RELAX_HITS hits, last match <= TRACK_RELAX_S ago) is accepted TRACK_RELAX below
#: the threshold at its predicted position (within TRACK_RELAX_DIST + walking) when the
#: ring around the match has its team's colour (own >= TRACK_RELAX_OWN, other team's
#: colour <= TRACK_RELAX_OPP): a walking champion keeps its identity through weaker frames
#: (blur, JPEG, a label or a ping on it) while a fogged one (no ring) is not followed.
TRACK_RELAX = 0.12
TRACK_RELAX_S = 0.6
TRACK_RELAX_HITS = 2
TRACK_RELAX_DIST = 0.012
TRACK_RELAX_OWN = 0.3
TRACK_RELAX_OPP = 0.12
#: Occlusion: candidates down to OCC_RANGE below the threshold are re-scored on their
#: visible part only (partial disc masks, without the pixels of overlapping accepted icons
#: and of white lines / texts), minus OCC_PENALTY (fewer pixels: chance matches are easier).
OCC_RANGE = 0.3
OCC_PENALTY = 0.05
OCC_MIN_NCC = 0.4
OCC_MIN_AREA = 0.45
OCC_SEARCH = 3                     # re-scoring window (+- working px) around a stacked icon
OCC_AREA_PENALTY = 0.15
#: Unexplained occluders (our own overlay's labels / rings, pings): the worst-fitting
#: TRIM_FRAC of the pixels are dropped and the NCC re-computed (occlusion penalty), for a
#: candidate whose ring clearly has its own team's colour (TRIM_RING_OWN / _OPP).
TRIM_FRAC = 0.25
#: Ring proposals (stage 5c): annuli (x icon radius) of the ring / inside / outside, Lab
#: lightness weight and width of the colour closeness, minimum ring evidence of a proposal,
#: proposals this close (x diameter) to an accepted icon are explained, identity score needed
#: (occlusion-tolerant NCC) and margin over the second best champion of that team.
RING_PROP_ANNULI = ((0.86, 1.02), (0.62, 0.78), (1.12, 1.3))
RING_PROP_LW = 0.3
RING_PROP_SIGMA = 18.0
RING_PROP_MIN = 0.12
RING_PROP_EXPLAINED = 0.4
RING_PROP_MAX = 6
#: proposals the patch verifier gives less than this icon probability are not re-scored
#: (measured on the real crops: glyphs 0.00-0.01, stacked icons >= 0.1)
RING_PROP_VERIFY = 0.05
#: Stack proposals (stage 5d, RosterMatcher._stack_proposals): verifier probes at
#: STACKV_DIST x diameter around each accepted icon (STACKV_ANGLES directions), kept when the
#: icon probability >= STACKV_MIN with a clear team; identity: occlusion-tolerant NCC >=
#: STACKV_ID_MIN and STACKV_ID_GAP above the second best of that team's missing champions.
STACKV_EVERY = 2
STACKV_DIST = (0.5, 0.8)
STACKV_ANGLES = 8
STACKV_MIN_SEP = 0.4
STACKV_MIN = 0.7
STACKV_TEAM = 0.8
STACKV_MAX = 4
STACKV_ID_MIN = 0.6
STACKV_ID_GAP = 0.3
STACKV_SEARCH = 3
RING_PROP_EVERY = 2                 # (cost) every N frames; the tracker holds them between
RING_PROP_SEARCH = 1                # re-scoring window (+- working px): the ring peak is precise
RING_PROP_ID_MIN = 0.55
RING_PROP_ID_GAP = 0.1
RING_PROP_ID_W = 0.5                # accepted when ring + w x (identity - ID_MIN) >= ACCEPT
RING_PROP_ACCEPT = 0.19             # (measured: no proposal on an empty spot accepted)
RING_DEBUG: list | None = None
TRIM_RING_OWN = 0.65
TRIM_RING_OPP = 0.12
TRIM_MIN_SCORE = 0.75              # trimmed NCC needed (synthetic: no false accept above)
TRIM_ID_GAP = 0.08                 # ... and must beat every other champion of its side by this
#: Stacks (an icon drawn UNDER another one, e.g. ADC + support): a champion tracked less
#: than STACK_HOLD_S ago whose predicted position is within STACK_NEAR icon diameters of an
#: accepted icon is searched under it (see ``RosterMatcher._stack_search``): the visible
#: arc of his ring (champion's team colour, outside the covering icons' discs +
#: STACK_EXCL) is fitted with the known icon radius, and the visible part of the portrait
#: (masked NCC) must not contradict it. STACK_ARC_* bound the arc evidence.
STACK_HOLD_S = 3.0
STACK_NEAR = 1.15
STACK_EXCL = 1.14                   # x covering icon radius (ring + dark line + blur)
STACK_EXCL_SELF = 1.4               # ... my icon has a glowing teal outline
STACK_ARC_VIS = 0.14                # visible part of the ring (fraction of its samples)
STACK_ARC_OWN = 0.8                 # own-colour fraction of the visible ring directions
STACK_ARC_OPP = 0.15                # other-team-colour fraction of the visible ring
STACK_NCC_AREA = 0.15               # portrait visible this much: its NCC must agree ...
STACK_NCC_MIN = (0.2, 0.35, 0.5)    # ... at least a + b x min(1, (area - AREA) / c)
STACK_NCC_STRONG = 0.62             # ... or a strong partial-portrait match alone is enough
STACK_NCC_STRONG_AREA = 0.38
#: Tracked mode, champions not tracked: whole-map search at a lower resolution (matched
#: disc COARSE_INNER_PX wide); its peaks above COARSE_VERIFY_MIN are verified at full res.
COARSE_INNER_PX = 9.0
COARSE_VERIFY_MIN = 0.45
COARSE_VERIFY_GAP = 0.03
#: Structure glyphs (turrets, inhibitors, nexus) have team-coloured rings: a candidate
#: this close (normalized) to a structure of the same colour needs STRUCT_PENALTY more.
STRUCT_DIST = 0.03
STRUCT_PENALTY = 0.0                # disabled: hurts real champions on turrets (measured)
#: Fountains (normalized) for the recall jump exception.
_FOUNTAINS = {"ORDER": (0.045, 0.955), "CHAOS": (0.955, 0.045)}
FOUNTAIN_DIST = 0.09
#: Dead champions (Live Client ``isDead``) have no icon on the minimap: they are not
#: searched until RESPAWN_LEAD_S before their respawn time (``respawnTimer``), then their
#: track is re-seeded at their fountain. A dead flag without a usable timer expires after
#: DEAD_UNKNOWN_S (the next API poll refreshes it).
RESPAWN_LEAD_S = 0.3
DEAD_UNKNOWN_S = 3.0

#: Camera lock (camera centred on me, "Y"): when my strong matches sit on the camera point
#: (``self_icon.CAM_V_FRAC`` of the rectangle height) CAMLOCK_CONFIRM frames in a row, the
#: camera is locked: while my icon is not matched (stacked, ping, custom icon not learned
#: yet...) my position is the camera point + the learned offset, for CAMLOCK_HOLD_S after
#: the last confirmation, unless the camera point jumps (panning) faster than me.
CAMLOCK_MARGIN = 0.05
CAMLOCK_TOL = 0.025
CAMLOCK_CONFIRM = 4
CAMLOCK_HOLD_S = 6.0
CAMLOCK_JUMP = 0.03
CAMLOCK_SCORE = 0.5
#: While I am not confirmed, the camera point may only move as fast as a champion (+ the
#: rectangle detection jitter) since my last confirmed match: any faster (edge scroll,
#: dragging, minimap click) and the camera is no longer on me -> unlocked at once.
CAMLOCK_CAM_SPEED = 0.06
CAMLOCK_CAM_SLACK = 0.008
CAMLOCK_STEP_SLACK = 0.005          # ... per camera step (frame to frame)

#: Greyscale minimap (death filter on some clients, desaturated capture): the ring colour
#: cannot vote, the chroma half of the NCC is meaningless. Detected when the 95th
#: percentile of the chroma is below GREY_CHROMA; the match then uses the lightness only
#: and every candidate gets the neutral ring bonus GREY_RING_BONUS (= a 30 % own ring).
GREY_CHROMA = 9.0
GREY_RING_BONUS = 0.06
GREY_THR_ADD = 0.1
GREY_RING_DL = 25.0
GREY_NO_RING = 0.3

#: Change gate (cost): champions not tracked are searched over the whole map only when an
#: icon-sized change appeared (pixel difference > CHANGE_DIFF surviving an opening of
#: CHANGE_OPEN x the icon diameter, away from the tracked champions), or every LOST_EVERY
#: frames anyway.
CHANGE_DIFF = 14
CHANGE_OPEN = 0.3
LOST_EVERY = 4

# Initial ring colours (BGR), learned live afterwards.
_RING_INIT_BGR: dict[str, tuple[int, int, int]] = {
    "enemy": (51, 51, 200),
    "ally": (218, 152, 78),
    "self": (215, 200, 60),       # teal / cyan outline of the local player (2025+)
}
#: Other plausible colours (JPEG-desaturated, pale 2024 ally ring, colourblind), fixed.
_RING_EXTRA_BGR: dict[str, list[tuple[int, int, int]]] = {
    "enemy": [(80, 80, 170), (203, 79, 255)],
    "ally": [(183, 165, 158), (160, 150, 110), (255, 117, 24)],
    "self": [(214, 180, 106), (225, 235, 110)],
}
_LEARN_RATE = 0.15
_MAX_LEARN_DRIFT = 60.0            # chroma distance from the seed needing confirmation


_K3 = np.ones((3, 3), np.uint8)
_RING_ANG = np.linspace(0, 2 * np.pi, 40, endpoint=False, dtype=np.float32)
_RING_DX = (np.asarray([0.86, 0.93, 1.0], np.float32)[:, None] * np.cos(_RING_ANG)[None]
            ).astype(np.float32)
_RING_DY = (np.asarray([0.86, 0.93, 1.0], np.float32)[:, None] * np.sin(_RING_ANG)[None]
            ).astype(np.float32)
_STACK_LEAK_R = (1.04, 1.12, 1.2, 1.28, 1.36, 1.44)
_LEAK_COS = np.cos(np.linspace(0, 2 * np.pi, 48, endpoint=False)).astype(np.float32)
_LEAK_SIN = np.sin(np.linspace(0, 2 * np.pi, 48, endpoint=False)).astype(np.float32)


def _lab1(bgr: Sequence[int]) -> np.ndarray:
    px = np.asarray(bgr, np.uint8).reshape(1, 1, 3)
    return cv2.cvtColor(px, cv2.COLOR_BGR2LAB).reshape(3).astype(np.float32)


def _bgr1(lab: np.ndarray) -> tuple[int, int, int]:
    px = np.clip(np.asarray(lab, np.float32), 0, 255).astype(np.uint8).reshape(1, 1, 3)
    b, g, r = cv2.cvtColor(px, cv2.COLOR_LAB2BGR).reshape(3)
    return int(b), int(g), int(r)


# ======================================================================================
# Templates and FFT correlation
# ======================================================================================


@dataclass(frozen=True)
class RosterEntry:
    """One player of the match: champion alias, relation ("self"/"ally"/"enemy"), portrait."""

    alias: str
    relation: str
    icon: np.ndarray                    # RGBA (or BGR) uint8 round portrait
    skin_id: int = 0
    team: str | None = None
    #: False when the skin's own portrait is not available yet (base portrait used).
    exact: bool = True
    #: True when ``icon`` was captured from the minimap (custom skin, see self_icon.py).
    learned: bool = False


@dataclass
class _Bank:
    """Templates of every roster entry for one working scale."""

    size: int                           # template side (px)
    mask: np.ndarray                    # [s, s] float32 disc
    n: float                            # mask area
    tmpl: list[np.ndarray]              # zero-mean masked templates [s, s, 3] float32
    norms: np.ndarray                   # [n, 2] L2 norms (lightness, chroma) of each template
    stds: np.ndarray                    # per-pixel std of each template (contrast check)
    specs: dict = field(default_factory=dict)   # DFT shape -> (mask spec, template specs)
    raw: list = field(default_factory=list)     # template features before centring [s, s, 3]
    #: Contiguous channel views for cv2.matchTemplate: (lightness [s, s], chroma [s, s, 2]).
    split: list = field(default_factory=list)
    #: Partial masks [k, s, s] (disc minus one side) for the occlusion-tolerant re-scoring.
    caps: np.ndarray | None = None


def _icon_bgr(icon: np.ndarray) -> np.ndarray:
    """RGBA portrait -> BGR on a dark background; BGR arrays are returned as is."""
    if icon.shape[2] == 3:
        return np.ascontiguousarray(icon, np.uint8)
    rgb = icon[:, :, :3].astype(np.float32)
    a = icon[:, :, 3:4].astype(np.float32) / 255.0
    rgb = rgb * a + 40.0 * (1.0 - a)
    return np.ascontiguousarray(rgb[:, :, ::-1]).clip(0, 255).astype(np.uint8)


def _features(bgr: np.ndarray) -> np.ndarray:
    """BGR uint8 -> float32 Lab feature image (L, w*a, w*b), lightly blurred."""
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    lab[:, :, 1:] -= 128.0
    if BLUR_SIGMA > 0:
        lab = cv2.GaussianBlur(lab, (0, 0), BLUR_SIGMA)
    return lab


def _disc_mask(size: int, radius: float) -> np.ndarray:
    c = (size - 1) / 2.0
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    d = np.sqrt((xx - c) ** 2 + (yy - c) ** 2)
    return np.clip(radius + 0.5 - d, 0.0, 1.0).astype(np.float32)


def _make_bank(entries: Sequence[RosterEntry], inner_px: float) -> _Bank:
    """Templates whose matched disc diameter is ``inner_px`` working pixels."""
    size = max(5, int(math.ceil(inner_px)) | 1)            # odd side -> centred peaks
    rin = inner_px / 2.0
    mask = _disc_mask(size, rin)
    n = float(mask.sum())
    R = rin / INNER_RATIO                                  # icon radius (working px)
    side = 2.0 * PORTRAIT_RATIO * R / PORTRAIT_FILL        # portrait image side
    c = (size - 1) / 2.0
    tmpls: list[np.ndarray] = []
    raws: list[np.ndarray] = []
    norms: list[np.ndarray] = []
    stds: list[float] = []
    for e in entries:
        bgr = _icon_bgr(e.icon)
        h, w = bgr.shape[:2]
        m0 = min(h, w)
        # area pre-shrink (realistic blur), then an exact sub-pixel affine placement
        mid = max(8, int(round(min(m0, 2.0 * side))))
        small = cv2.resize(bgr, (mid, mid), interpolation=cv2.INTER_AREA) if mid < m0 \
            else cv2.resize(bgr, (m0, m0), interpolation=cv2.INTER_AREA) if h != w else bgr
        kk = side / float(small.shape[0])
        sc = (small.shape[0] - 1) / 2.0
        M = np.float32([[kk, 0, c - kk * sc], [0, kk, c - kk * sc]])
        t = cv2.warpAffine(small, M, (size, size), flags=cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_REPLICATE)
        f = _features(t)
        mean = (f * mask[:, :, None]).sum(axis=(0, 1)) / n
        t0 = (f - mean) * mask[:, :, None]
        sq = (t0 ** 2).sum(axis=(0, 1))
        tmpls.append(np.ascontiguousarray(t0, np.float32))
        raws.append(np.ascontiguousarray(f, np.float32))
        norms.append(np.sqrt([sq[0], sq[1] + sq[2]]) + 1e-6)
        stds.append(float(np.sqrt(sq.sum() / (3.0 * n))) + 1e-6)
    split = [(np.ascontiguousarray(t[:, :, 0]), np.ascontiguousarray(t[:, :, 1:]))
             for t in tmpls]
    return _Bank(size=size, mask=mask, n=n, tmpl=tmpls, norms=np.asarray(norms, np.float32),
                 stds=np.asarray(stds, np.float32), raw=raws, split=split,
                 caps=_cap_masks(size, rin))


def _annulus(R: float, r0: float, r1: float) -> np.ndarray:
    """Normalized annulus kernel (radii r0..r1 x R)."""
    n = 2 * int(math.ceil(R * 1.35)) + 1
    c = n // 2
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32) - c
    d = np.hypot(xx, yy)
    k = ((d >= r0 * R) & (d <= r1 * R)).astype(np.float32)
    return k / max(float(k.sum()), 1.0)


def _cap_masks(size: int, radius: float) -> np.ndarray:
    """The disc minus one side (4 directions + 4 diagonals): ~62 % of the disc each."""
    c = (size - 1) / 2.0
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    disc = _disc_mask(size, radius)
    out = []
    for k in range(8):
        a = k * math.pi / 4.0
        proj = (xx - c) * math.cos(a) + (yy - c) * math.sin(a)
        out.append(disc * (proj >= -0.3 * radius))
    return np.asarray(out, np.float32)


def _spec(a: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    buf = np.zeros(shape, np.float32)
    buf[:a.shape[0], :a.shape[1]] = a
    return cv2.dft(buf)                       # packed CCS spectrum


def _bank_specs(bank: _Bank, shape: tuple[int, int]) -> tuple[np.ndarray, list[list[np.ndarray]]]:
    got = bank.specs.get(shape)
    if got is None:
        got = (_spec(bank.mask, shape),
               [[_spec(np.ascontiguousarray(t[:, :, ch]), shape) for ch in range(3)]
                for t in bank.tmpl])
        if len(bank.specs) > 4:
            bank.specs.clear()
        bank.specs[shape] = got
    return got


def ncc_maps(feat: np.ndarray, bank: _Bank, idx: Sequence[int] | None = None,
             wl: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Masked zero-mean NCC maps ``[k, H-s+1, W-s+1]`` + local contrast ratio base (std map).

    Map pixel ``(y, x)`` = template centred at feature pixel ``(y + s//2, x + s//2)``.
    """
    H, W = feat.shape[:2]
    s = bank.size
    oh, ow = H - s + 1, W - s + 1
    idx = list(range(len(bank.tmpl))) if idx is None else list(idx)
    if oh < 1 or ow < 1:
        return np.zeros((len(idx), 1, 1), np.float32), np.zeros((1, 1), np.float32)
    shape = (cv2.getOptimalDFTSize(H), cv2.getOptimalDFTSize(W))
    mspec, tspecs = _bank_specs(bank, shape)
    chans = [np.ascontiguousarray(feat[:, :, ch]) for ch in range(3)]
    fs = [_spec(ch, shape) for ch in chans]

    def corr(spec: np.ndarray) -> np.ndarray:
        return cv2.idft(spec, flags=cv2.DFT_REAL_OUTPUT | cv2.DFT_SCALE)[:oh, :ow]

    # local variance under the mask: lightness, and chroma (a + b jointly)
    var = []
    for f, ch in zip(fs, chans):
        s1 = corr(cv2.mulSpectrums(f, mspec, 0, conjB=True))
        s2 = corr(cv2.mulSpectrums(_spec(ch * ch, shape), mspec, 0, conjB=True))
        var.append(np.maximum(s2 - s1 * s1 / bank.n, 0.0))
    var_l, var_c = var[0], var[1] + var[2]
    std = np.sqrt((var_l + var_c) / (3.0 * bank.n))
    # NCC of the lightness and of the chroma, weighted mean: the chroma counts even where
    # its variance is small (terrain has lightness contrast but almost no colour)
    den_l = np.sqrt(var_l + VAR_EPS[0] * bank.n)
    den_c = np.sqrt(var_c + VAR_EPS[1] * bank.n)
    wl = LIGHTNESS_WEIGHT if wl is None else float(wl)
    wc = 1.0 - wl
    out = np.empty((len(idx), oh, ow), np.float32)
    for k, i in enumerate(idx):
        ts = tspecs[i]
        nl, nc = bank.norms[i]
        num_l = corr(cv2.mulSpectrums(fs[0], ts[0], 0, conjB=True))
        spc = cv2.mulSpectrums(fs[1], ts[1], 0, conjB=True)
        spc += cv2.mulSpectrums(fs[2], ts[2], 0, conjB=True)
        num_c = corr(spc)
        out[k] = num_l * (wl / nl) / den_l + num_c * (wc / nc) / den_c
    return out, std


def local_ncc(roi: np.ndarray, bank: _Bank, i: int, wl: float | None = None
              ) -> tuple[np.ndarray, np.ndarray] | None:
    """Same NCC as :func:`ncc_maps` for one template over a small feature window ``roi``.

    Direct (spatial) correlation with ``cv2.matchTemplate``: much faster than the Fourier
    path for the small search windows of the tracked champions. Returns
    ``(ncc [h-s+1, w-s+1], std map)`` or None when the window is smaller than the template.
    """
    s = bank.size
    if roi.shape[0] < s or roi.shape[1] < s:
        return None
    L = np.ascontiguousarray(roi[:, :, 0])
    A = np.ascontiguousarray(roi[:, :, 1])
    B = np.ascontiguousarray(roi[:, :, 2])
    C = np.ascontiguousarray(roi[:, :, 1:])
    m, n = bank.mask, bank.n
    tl, tc = bank.split[i]
    mt = cv2.TM_CCORR
    s1l = cv2.matchTemplate(L, m, mt)
    s1a = cv2.matchTemplate(A, m, mt)
    s1b = cv2.matchTemplate(B, m, mt)
    s2l = cv2.matchTemplate(L * L, m, mt)
    s2c = cv2.matchTemplate(A * A + B * B, m, mt)
    var_l = np.maximum(s2l - s1l * s1l / n, 0.0)
    var_c = np.maximum(s2c - (s1a * s1a + s1b * s1b) / n, 0.0)
    std = np.sqrt((var_l + var_c) / (3.0 * n))
    nl, nc = bank.norms[i]
    num_l = cv2.matchTemplate(L, tl, mt)
    num_c = cv2.matchTemplate(C, tc, mt)
    wl = LIGHTNESS_WEIGHT if wl is None else float(wl)
    wc = 1.0 - wl
    out = num_l * (wl / nl) / np.sqrt(var_l + VAR_EPS[0] * n) + \
        num_c * (wc / nc) / np.sqrt(var_c + VAR_EPS[1] * n)
    return out.astype(np.float32, copy=False), std


def masked_ncc(patches: np.ndarray, raw: np.ndarray, masks: np.ndarray,
               wl: float | None = None) -> np.ndarray:
    """NCC (same definition as :func:`ncc_maps`) of ``patches`` ``[p, s, s, 3]`` against the
    raw template features ``raw`` ``[s, s, 3]`` under arbitrary masks ``[p, k, s, s]``
    (or ``[k, s, s]`` shared by all patches) -> scores ``[p, k]``.

    Used to re-score occluded icons on their visible part only (stacked icons, pings,
    camera lines, timer texts).
    """
    P = patches.astype(np.float32, copy=False)
    M = masks if masks.ndim == 4 else np.broadcast_to(masks, (P.shape[0],) + masks.shape)
    p_, k_ = M.shape[:2]
    S = P.shape[1] * P.shape[2]
    Mf = np.ascontiguousarray(M, dtype=np.float32).reshape(p_, k_, S)
    n = Mf.sum(axis=2) + 1e-6                                           # [p, k]
    T = raw.astype(np.float32, copy=False)
    # (one batched matmul for the 9 patch sums, one for the 6 template sums: ~4x faster
    # than five einsums)
    F = np.concatenate([P, P * P, P * T[None]], axis=3).reshape(p_, S, 9)
    sums = np.matmul(Mf, F)                                             # [p, k, 9]
    tsum = np.matmul(Mf, np.concatenate([T, T * T], axis=2).reshape(S, 6))   # [p, k, 6]
    sP, sPP, sPT = sums[:, :, 0:3], sums[:, :, 3:6], sums[:, :, 6:9]
    sT, sTT = tsum[:, :, 0:3], tsum[:, :, 3:6]
    nn = n[:, :, None]
    num = sPT - sP * sT / nn
    vp = np.maximum(sPP - sP * sP / nn, 0.0)
    vt = np.maximum(sTT - sT * sT / nn, 0.0)
    wl = LIGHTNESS_WEIGHT if wl is None else float(wl)
    wc = 1.0 - wl
    sl = num[:, :, 0] / (np.sqrt(vp[:, :, 0] + VAR_EPS[0] * n) * np.sqrt(vt[:, :, 0]) + 1e-6)
    sc = (num[:, :, 1] + num[:, :, 2]) / (
        np.sqrt(vp[:, :, 1] + vp[:, :, 2] + VAR_EPS[1] * n)
        * np.sqrt(vt[:, :, 1] + vt[:, :, 2]) + 1e-6)
    return (wl * sl + wc * sc).astype(np.float32)


def clean_camera_lines(bgr: np.ndarray, rect: Any, band: int = 2) -> np.ndarray:
    """Copy of ``bgr`` without the white camera-rectangle lines of ``rect``
    (``camera_proj.CameraRect``, normalized; None -> ``bgr`` itself).

    Only white, unsaturated pixels within ``band`` px of a side are replaced, by the mean of
    the pixels just outside the line on both sides (above / below a horizontal side, left /
    right of a vertical one): an icon drawn over the line keeps its pixels, an icon under
    the line gets its covered row back approximately. ~0.3 ms on a 300 px minimap.
    """
    if rect is None:
        return bgr
    try:
        from treeaicoach.camera_proj import white_mask

        H, W = bgr.shape[:2]
        wm = cv2.dilate(white_mask(bgr), np.ones((3, 3), np.uint8))   # + anti-aliased edges
        out = None
        d = band + 1
        sides = (("h", rect.v0, rect.u0, rect.u1), ("h", rect.v1, rect.u0, rect.u1),
                 ("v", rect.u0, rect.v0, rect.v1), ("v", rect.u1, rect.v0, rect.v1))
        for kind, c, a0, a1 in sides:
            n_c, n_a = (H, W) if kind == "h" else (W, H)
            pc = int(round(c * n_c - 0.5))
            if pc < -band or pc > n_c - 1 + band:
                continue
            lo = max(0, int(math.floor(a0 * n_a)) - 1)
            hi = min(n_a, int(math.ceil(a1 * n_a)) + 2)
            if hi - lo < 2:
                continue
            c0, c1 = max(0, pc - band), min(n_c, pc + band + 1)
            if c1 <= c0:
                continue
            if kind == "h":
                m = wm[c0:c1, lo:hi]
            else:
                m = wm[lo:hi, c0:c1].T
            if not m.any():
                continue
            if out is None:
                out = bgr.copy()
            pa, pb = max(0, pc - d), min(n_c - 1, pc + d)
            # (from the image cleaned so far: at a corner the other side's line is gone)
            if kind == "h":
                fill = (out[pa, lo:hi].astype(np.uint16) + out[pb, lo:hi]) // 2
                for k, row in enumerate(range(c0, c1)):
                    sel = m[k] > 0
                    out[row, lo:hi][sel] = fill[sel].astype(np.uint8)
            else:
                fill = (out[lo:hi, pa].astype(np.uint16) + out[lo:hi, pb]) // 2
                for k, col in enumerate(range(c0, c1)):
                    sel = m[k] > 0
                    out[lo:hi, col][sel] = fill[sel].astype(np.uint8)
        return bgr if out is None else out
    except Exception:
        log.debug("clean_camera_lines failed", exc_info=True)
        return bgr


# ======================================================================================
# Ring colour model (learned live)
# ======================================================================================


class RingColorModel:
    """Adaptive Lab centroids of the ring colours per relation ("enemy"/"ally"/"self")."""

    NEAR = 32.0                        # weighted Lab distance of a ring pixel to a colour

    def __init__(self) -> None:
        self.seed = {k: _lab1(v) for k, v in _RING_INIT_BGR.items()}
        self.centroid = {k: v.copy() for k, v in self.seed.items()}
        self.extra = {k: [_lab1(c) for c in v] for k, v in _RING_EXTRA_BGR.items()}
        self.samples = {k: 0 for k in self.seed}

    def reset(self) -> None:
        self.__init__()

    def colors_bgr(self) -> dict[str, tuple[int, int, int]]:
        return {k: _bgr1(v) for k, v in self.centroid.items()}

    def learned(self) -> dict[str, tuple[int, int, int]]:
        """Only the centroids learned from at least 3 confident matches."""
        return {k: _bgr1(v) for k, v in self.centroid.items() if self.samples[k] >= 3}

    def _protos(self, rel: str) -> list[np.ndarray]:
        return [self.centroid[rel]] + self.extra[rel]

    def classify(self, lab_px: np.ndarray) -> tuple[float, float]:
        """Fractions of ring pixels (``[n, 3]`` Lab) with the enemy / ally-side colours."""
        if lab_px.size == 0:
            return 0.0, 0.0
        w = np.asarray([0.35, 1.0, 1.0], np.float32)   # lightness matters less than hue
        pe, pa = self._protos("enemy"), self._protos("ally") + self._protos("self")
        P = np.asarray(pe + pa, np.float32) * w                          # [k, 3]
        X = lab_px.astype(np.float32, copy=False) * w                   # [n, 3]
        d2 = (X * X).sum(axis=1)[:, None] - 2.0 * (X @ P.T) + (P * P).sum(axis=1)[None]
        D = np.sqrt(np.maximum(d2, 0.0))
        de = D[:, :len(pe)].min(axis=1)
        da = D[:, len(pe):].min(axis=1)
        f_en = float(np.mean((de < self.NEAR) & (de < 0.8 * da)))
        f_al = float(np.mean((da < self.NEAR) & (da < 0.8 * de)))
        return f_en, f_al

    def learn(self, rel: str, lab_px: np.ndarray) -> bool:
        """Update ``rel``'s centroid from the ring pixels of a confident match."""
        if rel not in self.centroid or lab_px.shape[0] < 6:
            return False
        chroma = np.hypot(lab_px[:, 1] - 128.0, lab_px[:, 2] - 128.0)
        top = lab_px[chroma >= np.percentile(chroma, 50)]
        if top.shape[0] < 3:
            return False
        med = np.median(top, axis=0)
        spread = float(np.median(np.abs(top[:, 1:] - med[1:])))
        if spread > 18.0 or float(np.hypot(med[1] - 128.0, med[2] - 128.0)) < 8.0:
            return False     # inconsistent or grey ring (dead icon, occlusion)
        other = "enemy" if rel != "enemy" else "ally"
        if float(np.linalg.norm(med[1:] - self.centroid[other][1:])) < 20.0:
            return False     # looks like the other team's colour: never learn that
        if float(np.linalg.norm(med[1:] - self.seed[rel][1:])) > _MAX_LEARN_DRIFT:
            # far from the expected colour (colourblind mode...): learn slowly
            rate = _LEARN_RATE * 0.5
        else:
            rate = _LEARN_RATE
        self.centroid[rel] = (1 - rate) * self.centroid[rel] + rate * med
        self.samples[rel] += 1
        return True


# ======================================================================================
# Matcher
# ======================================================================================


@dataclass
class MatchInfo:
    """Diagnostics of one accepted or rejected match (normalized coordinates)."""

    alias: str
    relation: str
    u: float
    v: float
    r: float
    ncc: float                          # evidence (NCC + uniqueness bonus)
    ring_enemy: float
    ring_ally: float
    accepted: bool
    reason: str = ""


@dataclass
class _Track:
    """Temporal state of one roster champion (normalized coordinates)."""

    u: float
    v: float
    t: float                            # time of the last match
    vu: float = 0.0                     # velocity (normalized units / s)
    vv: float = 0.0
    conf: float = 0.0                   # smoothed confidence
    margin: float = UNIQUE_CAP          # uniqueness margin of the last whole-map search
    hits: int = 0
    misses: int = 0                     # consecutive frames without a match
    pend: tuple | None = None           # (u, v, t_first, n, t_last) far candidate awaiting confirmation

    def predict(self, t: float) -> tuple[float, float]:
        dt = min(max(t - self.t, 0.0), 0.5)
        return self.u + self.vu * dt, self.v + self.vv * dt


class CameraLock:
    """Is the camera locked on me? (see CAMLOCK_*). Normalized minimap coordinates."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.streak = 0
        self.locked_t: float | None = None
        self.prev: tuple[float, float, float] | None = None
        self.off = (0.0, 0.0)
        #: camera point at my last confirmed match (u, v, t)
        self.anchor: tuple[float, float, float] | None = None

    def _unlock(self) -> None:
        self.locked_t = None
        self.streak = 0
        self.anchor = None

    @property
    def locked(self) -> bool:
        return self.locked_t is not None

    def feed_cam(self, p: tuple[float, float] | None, now: float) -> None:
        """Camera point of this frame: a jump faster than a champion = the camera moved."""
        if p is None:
            return
        if self.prev is not None:
            dt = now - self.prev[2]
            if dt < 0 or math.hypot(p[0] - self.prev[0], p[1] - self.prev[1]) > \
                    MAX_SPEED * min(dt, 2.0) + CAMLOCK_JUMP:
                self._unlock()
            elif self.locked_t is not None and 0 < dt <= 1.0 and math.hypot(
                    p[0] - self.prev[0], p[1] - self.prev[1]) > \
                    CAMLOCK_CAM_SPEED * dt + CAMLOCK_STEP_SLACK:
                self._unlock()             # one camera step faster than a champion walks
        a = self.anchor
        if a is not None and self.locked_t is not None and (now < a[2] or math.hypot(
                p[0] - a[0], p[1] - a[1]) > CAMLOCK_CAM_SPEED * (now - a[2]) + CAMLOCK_CAM_SLACK):
            self._unlock()                 # the camera moves faster than I can: not on me
        self.prev = (float(p[0]), float(p[1]), float(now))

    def confirm(self, me: tuple[float, float], p: tuple[float, float] | None, now: float) -> None:
        """A strong match of my icon at ``me`` while the camera point is ``p``."""
        if p is None:
            return
        du, dv = me[0] - p[0], me[1] - p[1]
        if math.hypot(du - self.off[0], dv - self.off[1]) < CAMLOCK_TOL or \
                (self.streak == 0 and math.hypot(du, dv) < CAMLOCK_TOL):
            self.streak += 1
            a = 0.3
            self.off = ((1 - a) * self.off[0] + a * du, (1 - a) * self.off[1] + a * dv)
            self.anchor = (float(p[0]), float(p[1]), float(now))
            if self.streak >= CAMLOCK_CONFIRM:
                self.locked_t = now
        else:
            self._unlock()
            self.off = (0.0, 0.0)

    def position(self, p: tuple[float, float] | None, now: float) -> tuple[float, float] | None:
        if p is None or self.locked_t is None or not 0 <= now - self.locked_t <= CAMLOCK_HOLD_S:
            return None
        a = self.anchor
        if a is not None and math.hypot(p[0] - a[0], p[1] - a[1]) > \
                CAMLOCK_CAM_SPEED * max(0.0, now - a[2]) + CAMLOCK_CAM_SLACK:
            return None
        return (min(1.0, max(0.0, p[0] + self.off[0])), min(1.0, max(0.0, p[1] + self.off[1])))


@dataclass
class _Cand:
    """A candidate position of one champion (working px, continuous centre)."""

    i: int
    x: float
    y: float
    ncc: float
    ev: float                           # evidence (NCC + uniqueness / track margin)
    local: bool = False
    tot: float = 0.0                    # final score (evidence + ring + penalties)
    f_en: float = 0.0
    f_al: float = 0.0
    ring: Any = None
    note: str = ""
    margin: float | None = None         # uniqueness margin (whole-map search only)
    coarse: float = 0.0                 # NCC of the low-resolution proposal (diagnostics)


@dataclass
class _State:
    scale: float | None = None          # calibrated icon diameter / minimap width
    frames: int = 0
    last_full: int = -10 ** 9           # frame of the last full search
    since_calib: int = 0
    calib: list = field(default_factory=list)       # (scale, quality) of the first frames
    conf_hist: list = field(default_factory=list)   # confident matches per frame
    ref_conf: float = 0.0
    bg: list = field(default_factory=list)          # recent background peak scores
    bg_grey: list = field(default_factory=list)     # ... of the greyscale frames
    full_parity: int = 0                            # half of the roster of the next full search


class RosterMatcher:
    """Find the roster champions' portraits on the minimap (see module doc). Never raises."""

    name = "roster"

    def __init__(self, db: Any = None, scale_store: dict | None = None,
                 on_scale: Callable[[str, float], None] | None = None,
                 learn_cache: Any = None) -> None:
        self.db = db
        self.scale_store = scale_store
        self.on_scale = on_scale
        self._lock = threading.RLock()
        self._entries: tuple[RosterEntry, ...] = ()
        self._roster_key: tuple | None = None
        self._banks: dict[int, _Bank] = {}
        self._state = _State()
        self._size_key: str | None = None
        self._game: Any = None
        self.rings = RingColorModel()
        self._errors = _RateLimitedLog()
        #: Diagnostics of the last detect() call.
        self.last_matches: list[MatchInfo] = []
        self.last_time_ms: float = 0.0
        self.last_threshold: float = THR_MIN
        self.last_calib_ms: float = 0.0
        #: "full" / "tracked" search of the last detect() call (diagnostics, benchmarks).
        self.last_mode: str = ""
        self._tracks: dict[int, _Track] = {}
        self._my_team: str | None = None
        self._cam: tuple[int, tuple[float, float] | None] = (-10 ** 9, None)
        self._structs: list[tuple[float, float, str]] | None = None
        #: Learned / guessed icons (custom skins): alias -> (icon, source), see self_icon.py.
        self._overrides: dict[str, tuple[np.ndarray, str]] = {}
        self._camlock: tuple[int, tuple[float, float] | None] = (-10 ** 9, None)
        #: (frame, CameraRect | None) of the current frame (see _camera_rect_now).
        self._camrect: tuple[int, Any] = (-10 ** 9, None)
        #: Erase the white camera-rectangle lines before matching (see clean_camera_lines).
        self.clean_camera: bool = True
        #: alias -> respawn deadline (time.monotonic()) of the dead champions (Live API).
        self._dead: dict[str, float] = {}
        self._dead_idx: set[int] = set()
        #: Diagnostics: champions skipped as dead by the last detect() call.
        self.last_dead: list[str] = []
        #: Search champions hidden under other icons (stacks), see _stack_search.
        self.stack_search: bool = True
        #: occlusion re-scoring also drops the worst-fitting pixels (unknown occluders)
        self.trim_rescue: bool = True
        #: unexplained team-coloured rings are given to that team's missing champions
        self.ring_proposals: bool = True
        #: Camera locked on me (my matches follow the camera point): my position.
        self.camlock = CameraLock()
        #: Last frame was colourless (lightness-only matching).
        self.grey = False
        self._grey_L: np.ndarray | None = None
        self._prev_gray: np.ndarray | None = None
        self._open_k: np.ndarray | None = None
        #: Diagnostics: change gate result / number of whole-map searched champions.
        self.last_changed = True
        self.last_searched = 0
        self._wl: float = LIGHTNESS_WEIGHT
        self.last_stack: tuple | None = None
        self.learner: Any = None
        #: Icon / distractor verifier (lazy, None when unavailable): see VERIFY_ZONE.
        self.verifier: Any = None
        self._verifier_tried = False
        self._ring_img: np.ndarray | None = None
        self._ring_specs: dict = {}
        self._under: tuple = (None, 0)
        try:
            from treeaicoach.self_icon import IconLearner

            self.learner = IconLearner(cache_dir=learn_cache)
        except Exception:
            log.exception("Icon learner unavailable")

    # ------------------------------------------------------------------ roster
    @property
    def has_roster(self) -> bool:
        return bool(self._entries)

    @property
    def entries(self) -> tuple[RosterEntry, ...]:
        return self._entries

    @property
    def aliases(self) -> list[str]:
        return [e.alias for e in self._entries]

    @property
    def scale(self) -> float | None:
        """Calibrated icon diameter / minimap width (None before calibration)."""
        return self._state.scale

    @property
    def ring_colors(self) -> dict[str, tuple[int, int, int]]:
        """Ring colours (BGR) learned from confident matches (relation -> colour)."""
        return self.rings.learned()

    def set_entries(self, entries: Iterable[RosterEntry]) -> None:
        """Set the roster directly (tests, demo). Idempotent for an unchanged roster."""
        ents = tuple(e for e in entries if isinstance(e, RosterEntry)
                     and isinstance(e.icon, np.ndarray) and e.icon.ndim == 3
                     and e.icon.shape[2] in (3, 4) and min(e.icon.shape[:2]) >= 8)
        key = tuple((e.alias, e.skin_id, e.relation, id(e.icon)) for e in ents)
        with self._lock:
            if key == self._roster_key:
                return
            same_players = self._roster_key is not None and \
                [k[:3] for k in key] == [k[:3] for k in self._roster_key]
            self._entries = ents
            self._roster_key = key
            self._banks = {}
            if not same_players:
                # a new game: new calibration (the stored ratio is the prior), new colours
                self._state = _State()
                self.rings.reset()
                self._tracks = {}
                self._cam = (-10 ** 9, None)
                self._camlock = (-10 ** 9, None)
                self.camlock.reset()
                if self.learner is not None:
                    self.learner.on_roster(ents)

    def set_roster(self, game: Any) -> None:
        """Build the portrait templates from a ``GameInfo`` (None clears). Never raises."""
        try:
            if game is None:
                self._game = None
                self.set_entries(())
                return
            me = getattr(game, "me", None)
            players = ([(me, "self")] if me is not None else []) + \
                [(p, "ally") for p in (getattr(game, "allies", None) or [])] + \
                [(p, "enemy") for p in (getattr(game, "enemies", None) or [])]
            db = self.db
            if db is None:
                from treeaicoach.champions import get_default_db

                db = self.db = get_default_db()
            ents: list[RosterEntry] = []
            seen: set[str] = set()
            for p, rel in players:
                alias = str(getattr(p, "champion_alias", "") or "")
                if not alias or alias in seen:
                    continue
                try:
                    skin = int(getattr(p, "skin_id", 0) or 0)
                except (TypeError, ValueError):
                    skin = 0
                icon, exact = None, True
                try:
                    if skin > 0:
                        path = db.cached_icon_path(alias, skin)
                        exact = bool(path is not None and path.is_file())
                    icon = db.load_icon(alias, skin)
                    if icon is None and skin:
                        icon, exact = db.load_icon(alias, 0), False
                except Exception:
                    icon = None
                if icon is None:
                    log.warning("Roster matcher: no portrait for %r", alias)
                    continue
                seen.add(alias)
                ents.append(RosterEntry(alias=alias, relation=rel, icon=icon, skin_id=skin,
                                        team=str(getattr(p, "team", "") or "") or None,
                                        exact=exact))
            self._game = game
            self._my_team = str(getattr(me, "team", "") or "") or None
            ents = self._apply_overrides(ents)
            old = [(e.alias, e.skin_id, e.relation, e.exact, e.learned) for e in self._entries]
            if old == [(e.alias, e.skin_id, e.relation, e.exact, e.learned) for e in ents]:
                return
            self.set_entries(ents)
            for i, e in enumerate(self._entries):
                ov = self._overrides.get(e.alias)
                if ov is not None and ov[1] == "cached" and self.learner is not None and \
                        i not in self.learner.learned:
                    self.learner.adopt(i, e.alias, ov[0], time.monotonic(), "cached")
            log.info("Roster matcher: %d portraits", len(ents))
        except Exception:
            self._errors.exception("Roster matcher set_roster failed")

    # ------------------------------------------------------------------ learned icons
    def _apply_overrides(self, ents: list[RosterEntry]) -> list[RosterEntry]:
        """Learned icons on the official roster; a new game drops them and loads my
        persisted icon (same champion + skin) from the learner's cache."""
        import dataclasses

        cur = [(e.alias, e.skin_id, e.relation) for e in self._entries]
        if cur != [(e.alias, e.skin_id, e.relation) for e in ents]:
            self._overrides = {}
            for e in ents:
                if e.relation == "self" and self.learner is not None:
                    icon = self.learner.load_cached(e.alias, e.skin_id)
                    if icon is not None:
                        self._overrides[e.alias] = (icon, "cached")
                        log.info("Roster matcher: learned icon of %s (skin %d) from the cache",
                                 e.alias, e.skin_id)
        out = []
        for e in ents:
            ov = self._overrides.get(e.alias)
            out.append(dataclasses.replace(e, icon=ov[0], exact=True, learned=ov[1] != "skin")
                       if ov is not None else e)
        return out

    def register_icon(self, i: int, icon: np.ndarray, source: str = "learned") -> bool:
        """Entry ``i`` is matched with ``icon`` from now on (BGR crop in the template
        geometry, or an RGBA official portrait for ``source="skin"``). Keeps the
        calibration and the tracks. Never raises."""
        import dataclasses

        try:
            with self._lock:
                if not 0 <= i < len(self._entries) or not isinstance(icon, np.ndarray):
                    return False
                e = self._entries[i]
                ne = dataclasses.replace(e, icon=icon, exact=True, learned=source != "skin")
                ents = list(self._entries)
                ents[i] = ne
                self._overrides[e.alias] = (icon, source)
                self.set_entries(ents)
                # the old track may sit next to the icon (lookalike): search it everywhere
                self._tracks.pop(i, None)
                self._state.last_full = -10 ** 9
                return True
        except Exception:
            self._errors.exception("Roster matcher register_icon failed")
            return False

    def revert_icon(self, i: int) -> bool:
        """Entry ``i`` back to its official portrait. Never raises."""
        import dataclasses

        try:
            with self._lock:
                if not 0 <= i < len(self._entries):
                    return False
                e = self._entries[i]
                self._overrides.pop(e.alias, None)
                db = self.db
                icon = None
                if db is not None:
                    icon = db.load_icon(e.alias, e.skin_id) if e.skin_id else None
                    if icon is None:
                        icon = db.load_icon(e.alias, 0)
                if icon is None:
                    return False
                ents = list(self._entries)
                ents[i] = dataclasses.replace(e, icon=icon, learned=False)
                self.set_entries(ents)
                return True
        except Exception:
            self._errors.exception("Roster matcher revert_icon failed")
            return False

    def learned_aliases(self) -> dict[str, str]:
        """alias -> source ("learned" / "cached" / "skin") of the non-official templates."""
        with self._lock:
            return {a: src for a, (_ic, src) in self._overrides.items()}

    def set_status(self, game: Any = None, me_dead: bool | None = None,
                   game_time: float | None = None) -> None:
        """Dead players (Live API) and my HUD dead flag for the icon learner. Never raises."""
        try:
            self.set_game_status(game)
            if self.learner is None:
                return
            dead = []
            if game is not None:
                for p in [getattr(game, "me", None)] + list(getattr(game, "allies", None) or []) + \
                        list(getattr(game, "enemies", None) or []):
                    if p is not None and bool(getattr(p, "is_dead", False)):
                        dead.append(str(getattr(p, "champion_alias", "") or ""))
                if me_dead is None and getattr(game, "me", None) is not None:
                    me_dead = bool(getattr(game.me, "is_dead", False))
            self.learner.set_status(dead, me_dead, game_time)
        except Exception:
            self._errors.exception("Roster matcher set_status failed")

    def set_dead(self, aliases: Any, now: float | None = None) -> None:
        """Dead champions: ``{alias: seconds to respawn}`` (or an iterable of aliases: dead
        until the next call). Their portraits are not searched while dead (no icon on the
        map: any match would be a false one). Deadlines use ``time.monotonic()``. Never
        raises."""
        try:
            mono = time.monotonic() if now is None else float(now)
            if isinstance(aliases, dict):
                items = aliases.items()
            else:
                items = ((a, math.inf) for a in (aliases or ()))
            dead: dict[str, float] = {}
            for a, rem in items:
                a = str(a or "")
                if not a:
                    continue
                try:
                    r = float(rem)
                except (TypeError, ValueError):
                    r = math.nan
                if r == math.inf:
                    r = 1e9                        # dead until the next call
                elif not math.isfinite(r) or r <= 0.0:
                    r = DEAD_UNKNOWN_S             # no usable respawn timer
                dead[a] = mono + r
            with self._lock:
                self._dead = dead
        except Exception:
            self._errors.exception("Roster matcher set_dead failed")

    def set_game_status(self, game: Any) -> None:
        """Dead champions from a ``GameInfo`` (``is_dead`` + ``respawn_timer``, measured from
        the poll time ``fetched_at``). Cheap: call it on every frame. Never raises."""
        try:
            if game is None:
                return
            mono = time.monotonic()
            fetched = getattr(game, "fetched_at", None)
            try:
                age = mono - float(fetched)
                if not math.isfinite(age) or not -1.0 <= age <= 10.0:
                    age = 0.0
            except (TypeError, ValueError):
                age = 0.0
            dead: dict[str, float] = {}
            for p in [getattr(game, "me", None)] + list(getattr(game, "allies", None) or []) + \
                    list(getattr(game, "enemies", None) or []):
                if p is None or not bool(getattr(p, "is_dead", False)):
                    continue
                alias = str(getattr(p, "champion_alias", "") or "")
                try:
                    timer = float(getattr(p, "respawn_timer", 0.0) or 0.0)
                except (TypeError, ValueError):
                    timer = 0.0
                if not alias:
                    continue
                if math.isfinite(timer) and timer > 0:
                    if timer - age > 0:            # else: respawned since the poll
                        dead[alias] = timer - age
                else:
                    dead[alias] = math.nan
            self.set_dead(dead, mono)
        except Exception:
            self._errors.exception("Roster matcher set_game_status failed")

    def _dead_now(self, now: float) -> set[int]:
        """Indices of the roster entries dead right now; re-seeds the track of a champion
        that just respawned at his fountain (detection time ``now``)."""
        if not self._dead and not self._dead_idx:
            return set()
        mono = time.monotonic()
        dead = {i for i, e in enumerate(self._entries)
                if mono < self._dead.get(e.alias, -math.inf) - RESPAWN_LEAD_S}
        for i in self._dead_idx - dead:
            if i >= len(self._entries):
                continue
            team = self._team_of(self._entries[i])
            if team in _FOUNTAINS:
                fu, fv = _FOUNTAINS[team]
                self._tracks[i] = _Track(fu, fv, now, conf=0.3, margin=0.0)
        for i in dead:
            self._tracks.pop(i, None)
        self._dead_idx = dead
        self.last_dead = [self._entries[i].alias for i in sorted(dead)]
        return dead

    def _camera_rect_now(self, bgr: np.ndarray) -> Any:
        """White camera rectangle of this frame (``camera_proj.find_camera_rect``, one search
        per frame, shared by the line cleaning, the camera lock and the self fallback)."""
        f = self._state.frames
        if self._camrect[0] != f:
            r = None
            try:
                from treeaicoach.camera_proj import find_camera_rect

                r = find_camera_rect(bgr)
            except Exception:
                r = None
            self._camrect = (f, r)
        return self._camrect[1]

    def _cam_point(self, bgr: np.ndarray) -> tuple[float, float] | None:
        """Where my icon is when the camera is locked on me (camera rectangle), cached."""
        f = self._state.frames
        if f != self._camlock[0]:
            p = None
            try:
                from treeaicoach.self_icon import CAM_V_FRAC

                r = self._camera_rect_now(bgr)
                if r is not None:
                    p = (0.5 * (r.u0 + r.u1), r.v0 + CAM_V_FRAC * (r.v1 - r.v0))
                    self._cam = (f, (0.5 * (r.u0 + r.u1), 0.5 * (r.v0 + r.v1)))
            except Exception:
                c = self._camera_centre(bgr)
                p = (c[0], c[1] + 0.022) if c is not None else None
            self._camlock = (f, p)
        return self._camlock[1]

    def _learn(self, bgr: np.ndarray, now: float, R_px: float, r_norm: float,
               accepted: list, thr: float, kx: float, ky: float, used: set,
               dets_extra: list) -> list[Detection]:
        """Icon learner step (self_icon.py): registrations, reverts, bootstrap position of
        me (replaces the coasting one). Returns the extra detections."""
        lr = self.learner
        if lr is None:
            return dets_extra
        ents = self._entries
        acc = {c.i: (c.x / kx, c.y / ky, c.tot - thr) for c in accepted}
        out = lr.step(bgr, now, R_px, ents, acc, self.rings, self._cam_point(bgr), self.db)
        me = next((i for i, e in enumerate(ents) if e.relation == "self"), None)
        if out.self_pos is not None and me is not None and me in used:
            # my official portrait matched off my icon (lookalike custom skin): move it
            u, v, _sc = out.self_pos
            for c in accepted:
                if c.i == me:
                    c.x, c.y = u * kx, v * ky
            tr = self._tracks.get(me)
            if tr is not None:
                tr.u, tr.v = u, v
        elif out.self_pos is not None and me is not None:
            u, v, sc = out.self_pos
            e = ents[me]
            dets_extra = [d for d in dets_extra if d.alias != e.alias]
            dets_extra.append(Detection(u=u, v=v, r=r_norm, score=float(sc), cls="ally",
                                        cls_probs=(0.03, 0.97, 0.0), alias=e.alias))
            self.last_matches.append(MatchInfo(e.alias, e.relation, u, v, r_norm, float(sc),
                                               0.0, 0.0, True, "bootstrap"))
            tr = self._tracks.get(me)
            if tr is None:
                self._tracks[me] = _Track(u, v, now, conf=float(sc), margin=0.0)
            else:
                tr.u, tr.v, tr.t, tr.vu, tr.vv = u, v, now, 0.0, 0.0
        for i in out.revert:
            self.revert_icon(i)
        for i, (src, skin, icon) in out.register.items():
            if self.register_icon(i, icon, "skin" if src == "skin" else "learned") and \
                    src == "skin":
                lr.adopt(i, ents[i].alias, cv2.resize(_icon_bgr(icon), (48, 48)), now, "skin")
            if src == "skin":
                log.info("Roster matcher: %s tried with its skin %s portrait", ents[i].alias, skin)
        return dets_extra

    # ------------------------------------------------------------------ internals
    def _bank(self, inner_px: float) -> _Bank:
        key = int(round(inner_px * 4))
        b = self._banks.get(key)
        if b is None:
            b = _make_bank(self._entries, key / 4.0)
            if len(self._banks) > 48:
                self._banks.clear()
            self._banks[key] = b
        return b

    @staticmethod
    def _work(bgr: np.ndarray, factor: float) -> np.ndarray:
        h, w = bgr.shape[:2]
        nw, nh = max(8, int(round(w * factor))), max(8, int(round(h * factor)))
        if (nw, nh) == (w, h):
            return bgr
        interp = cv2.INTER_AREA if factor < 1 else cv2.INTER_LINEAR
        return cv2.resize(bgr, (nw, nh), interpolation=interp)

    def _maps(self, bgr: np.ndarray, scale: float, inner_cap: float
              ) -> tuple[np.ndarray, np.ndarray, _Bank, float, float]:
        """NCC maps at ``scale`` -> (maps, std map, bank, factor x, factor y)."""
        H, W = bgr.shape[:2]
        inner_full = INNER_RATIO * scale * W
        factor = min(1.0, inner_cap / max(inner_full, 1e-6))
        work = self._work(bgr, factor)
        fx, fy = work.shape[1] / float(W), work.shape[0] / float(H)
        bank = self._bank(inner_full * fx)
        maps, std = ncc_maps(_features(work), bank, wl=self._wl)
        return maps, std, bank, fx, fy

    @staticmethod
    def _peaks(score: np.ndarray, rad: int, k: int, floor: float
               ) -> list[tuple[int, int, float]]:
        """Top-``k`` local maxima (greedy NMS radius ``rad``) above ``floor``: (x, y, score)."""
        dil = cv2.dilate(score, _K3)
        ys, xs = np.nonzero((score >= dil) & (score > floor))
        if ys.size == 0:
            return []
        vals = score[ys, xs]
        out: list[tuple[int, int, float]] = []
        r2 = rad * rad
        for j in np.argsort(-vals)[: 12 * k]:
            x, y, v = int(xs[j]), int(ys[j]), float(vals[j])
            if any((x - a) ** 2 + (y - b) ** 2 < r2 for a, b, _ in out):
                continue
            out.append((x, y, v))
            if len(out) >= k:
                break
        return out

    # ------------------------------------------------------------------ calibration
    def _scale_quality(self, bgr: np.ndarray, scale: float, inner_cap: float,
                       prior: float = DEFAULT_SCALE) -> float:
        """How well the best roster portraits stand out at ``scale``.

        Mean evidence (best NCC + uniqueness margin, like the detection) of the
        ``CALIB_TOP_K`` best champions (often only a few are visible), minus a gentle
        log-normal prior around ``prior`` (stored ratio, else ``DEFAULT_SCALE``).
        """
        maps, std, bank, _, _ = self._maps(bgr, scale, inner_cap)
        n = maps.shape[0]
        if n == 0 or maps.shape[1] < 3 or maps.shape[2] < 3:
            return -1.0
        # contrast consistency: flat / very busy areas do not count
        ratio = std[None] / bank.stds[:, None, None]
        valid = (ratio > CONTRAST_RANGE[0]) & (ratio < CONTRAST_RANGE[1])
        mm = np.where(valid, maps, -1.0)
        rad = max(2, int(round(0.5 * bank.size)))
        evs = []
        for i in range(n):
            m = mm[i]
            y, x = divmod(int(np.argmax(m)), m.shape[1])
            best = float(m[y, x])
            m[max(0, y - rad):y + rad + 1, max(0, x - rad):x + rad + 1] = -1.0
            evs.append(best + UNIQUE_WEIGHT * min(best - float(m.max()), UNIQUE_CAP))
        top = np.sort(evs)[::-1][:CALIB_TOP_K]
        return float(np.mean(top)) - CALIB_PRIOR_WEIGHT * math.log(scale / prior) ** 2

    def calibrate(self, minimap_bgr: np.ndarray, store: bool = True) -> float | None:
        """Search the icon scale over ``SCALE_MIN``..``SCALE_MAX``; the ratio or None."""
        try:
            bgr = _as_bgr(minimap_bgr)
            if bgr is None or not self._entries:
                return None
            with self._lock:
                return self._calibrate(bgr, store)
        except Exception:
            self._errors.exception("Roster matcher calibration failed")
            return None

    def _calibrate(self, bgr: np.ndarray, store: bool, around: float | None = None
                   ) -> float | None:
        """Full sweep, or a narrow one (+-12 %) ``around`` a previous scale."""
        t0 = time.perf_counter()
        n_steps = int(math.log(SCALE_MAX / SCALE_MIN) / math.log(SCALE_STEP)) + 1
        scales = [SCALE_MIN * SCALE_STEP ** i for i in range(n_steps)]
        if around is not None:
            scales = [s for s in scales if abs(math.log(s / around)) <= 0.12] or [around]
        prior = self._stored_scale(bgr) or DEFAULT_SCALE
        quals = [self._scale_quality(bgr, s, CALIB_INNER_PX, prior) for s in scales]
        i = int(np.argmax(quals))
        # refine at the detection resolution around the best coarse scale
        fine = [min(SCALE_MAX, max(SCALE_MIN, scales[i] * f)) for f in FINE_STEPS]
        fq = [self._scale_quality(bgr, s, WORK_INNER_PX, prior) for s in fine]
        # NCC tolerates a few % of scale error, so the quality curve has a plateau: take
        # the (quality-weighted, log-scale) centre of the plateau rather than its argmax
        q = max(fq)
        w = np.clip(np.asarray(fq) - (q - PLATEAU), 0.0, None)
        best = float(np.exp(np.sum(w * np.log(fine)) / max(float(w.sum()), 1e-9)))
        self.last_calib_ms = ms = 1000 * (time.perf_counter() - t0)
        st = self._state
        if q < MIN_CALIB_QUALITY:
            log.info("Roster matcher: calibration inconclusive (q=%.3f, %.0f ms)", q, ms)
            st.calib.append((None, 0.0))
            return None
        st.calib.append((best, q))
        # combine the calibrations of the first frames (quality-weighted median)
        vals = sorted([c for c in st.calib[-CALIB_FRAMES:] if c[0] is not None],
                      key=lambda x: x[0])
        tot = sum(w for _, w in vals)
        acc, med = 0.0, vals[0][0]
        for s, w in vals:
            acc += w
            if acc >= tot / 2:
                med = s
                break
        st.scale = float(med)
        st.since_calib = 0
        st.conf_hist.clear()
        st.ref_conf = 0.0
        log.info("Roster matcher: icon scale %.4f (q=%.3f, %.0f ms)", st.scale, q, ms)
        if store:
            self._store_scale(bgr, st.scale)
        return st.scale

    @staticmethod
    def _key(bgr: np.ndarray) -> str:
        return f"{bgr.shape[1]}x{bgr.shape[0]}"

    def _stored_scale(self, bgr: np.ndarray) -> float | None:
        try:
            if isinstance(self.scale_store, dict):
                v = self.scale_store.get(self._key(bgr))
                if v is not None and SCALE_MIN <= float(v) <= SCALE_MAX:
                    return float(v)
        except Exception:
            pass
        return None

    def _store_scale(self, bgr: np.ndarray, scale: float) -> None:
        try:
            key = self._key(bgr)
            if isinstance(self.scale_store, dict):
                self.scale_store[key] = round(float(scale), 5)
            if self.on_scale is not None:
                self.on_scale(key, float(scale))
        except Exception:
            log.debug("Could not store the icon scale", exc_info=True)

    # ------------------------------------------------------------------ detection
    def detect(self, minimap_bgr: np.ndarray, t: float | None = None) -> list[Detection]:
        """Roster champions found on a BGR minimap; [] without roster or on error.

        ``t``: capture time (s, monotonic clock by default) used by the tracking.
        """
        t0 = time.perf_counter()
        try:
            bgr = _as_bgr(minimap_bgr)
            if bgr is None or not self._entries:
                return []
            now = time.monotonic() if t is None else float(t)
            with self._lock:
                if self._state.frames % SKIN_CHECK_FRAMES == SKIN_CHECK_FRAMES - 1:
                    self._refresh_skins()
                return self._detect(bgr, now)
        except Exception:
            self._errors.exception("Roster matcher detection failed")
            return []
        finally:
            self.last_time_ms = 1000 * (time.perf_counter() - t0)

    def _refresh_skins(self) -> None:
        """Skin portraits downloaded since set_roster: rebuild (keeps calibration, tracks)."""
        if self._game is None or all(e.exact for e in self._entries) or self.db is None:
            return
        for e in self._entries:
            if not e.exact and e.skin_id > 0:
                try:
                    path = self.db.cached_icon_path(e.alias, e.skin_id)
                    if path is not None and path.is_file():
                        try:
                            self.db.clear_icon_cache()      # the base portrait was cached
                        except Exception:
                            pass
                        self.set_roster(self._game)
                        return
                except Exception:
                    return

    def _current_scale(self, bgr: np.ndarray) -> float:
        st = self._state
        key = self._key(bgr)
        if self._size_key != key:            # minimap size changed: recalibrate
            if self._size_key is not None:
                st.scale = None
                st.calib.clear()
            self._size_key = key
        around: float | None = None              # None: full sweep
        need = False
        if st.scale is None:
            # not calibrated yet: first frames, then a retry from time to time; a stored
            # ratio for this minimap size (previous game) is only confirmed narrowly first
            need = st.frames < CALIB_FRAMES or st.frames % RECAL_MIN_FRAMES == 0
            if need and st.frames == 0:
                around = self._stored_scale(bgr)
        elif len(st.calib) < CALIB_FRAMES and st.frames < 2 * CALIB_FRAMES:
            need, around = True, st.scale         # initial phase: confirm narrowly
        elif st.since_calib >= RECAL_WEAK_FRAMES and len(st.conf_hist) >= RECAL_WINDOW:
            recent = float(np.mean(st.conf_hist[-RECAL_WINDOW:]))
            if recent < max(RECAL_DROP * st.ref_conf, 0.5):
                # matches became weak (or never were): the scale may be a bit off. Narrow
                # sweep around it only (cost): a new minimap size is a new key (full sweep)
                need, around = True, st.scale
                log.info("Roster matcher: weak matches (%.1f, reference %.1f): re-calibrating",
                         recent, st.ref_conf)
                st.calib.clear()
                st.calib.append((st.scale, 0.2))     # the old scale keeps a vote
        if need:
            self._calibrate(bgr, store=True, around=around)
        if st.scale is not None:
            return st.scale
        return self._stored_scale(bgr) or DEFAULT_SCALE

    def _threshold(self, current_bg: list[float]) -> float:
        """Adaptive threshold from the background peaks (recent frames + this one)."""
        bg = (self._state.bg_grey if self.grey else self._state.bg)[-400:] + current_bg
        if len(bg) < 8:
            return THR_MIN + 0.05
        thr = float(np.percentile(bg, THR_BG_PCT)) + THR_MARGIN
        if self.grey:
            return float(min(THR_MAX + GREY_THR_ADD, max(THR_MIN, thr) + GREY_THR_ADD))
        return float(min(THR_MAX, max(THR_MIN, thr)))

    @staticmethod
    def _ring_pixels(bgr: np.ndarray, cx: float, cy: float, R: float,
                     exclude: Sequence[tuple[float, float]] = ()) -> np.ndarray:
        """Lab pixels of the ring annulus (0.86-1.0 R) inside the image, ``[n, 3]``;
        without the arc covered by the icons centred at ``exclude`` (original px)."""
        xs = (np.float32(cx - 0.5) + np.float32(R) * _RING_DX).astype(np.float32)
        ys = (np.float32(cy - 0.5) + np.float32(R) * _RING_DY).astype(np.float32)
        H, W = bgr.shape[:2]
        ok = (xs >= 0) & (xs <= W - 1) & (ys >= 0) & (ys <= H - 1)
        for ex, ey in exclude:
            ok &= (xs + 0.5 - ex) ** 2 + (ys + 0.5 - ey) ** 2 > (1.05 * R) ** 2
        samp = cv2.remap(bgr, xs, ys, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        lab = cv2.cvtColor(samp, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
        return lab[ok.reshape(-1)]

    @staticmethod
    def _subpixel(m: np.ndarray, x: int, y: int) -> tuple[float, float]:
        sx, sy = float(x), float(y)
        if 0 < x < m.shape[1] - 1:
            a, b, c = float(m[y, x - 1]), float(m[y, x]), float(m[y, x + 1])
            den = a - 2 * b + c
            if den < 0:
                sx += float(np.clip(0.5 * (a - c) / den, -0.5, 0.5))
        if 0 < y < m.shape[0] - 1:
            a, b, c = float(m[y - 1, x]), float(m[y, x]), float(m[y + 1, x])
            den = a - 2 * b + c
            if den < 0:
                sy += float(np.clip(0.5 * (a - c) / den, -0.5, 0.5))
        return sx, sy

    # ------------------------------------------------------------------ search stages
    def _global_search(self, feat: np.ndarray, bank: _Bank, idx: list[int]
                       ) -> tuple[list[_Cand], list[float]]:
        """Whole-map search (Fourier NCC) of the champions ``idx``: candidates + background."""
        maps, std = ncc_maps(feat, bank, idx, wl=self._wl)
        if maps.shape[1] < 2 or maps.shape[2] < 2:
            return [], []
        half = (bank.size - 1) / 2.0
        rad = max(2, int(round(0.4 * bank.size)))
        cands: list[_Cand] = []
        bg: list[float] = []
        for k, i in enumerate(idx):
            m = maps[k]
            pk = [(x, y, v) for x, y, v in self._peaks(m, rad, PEAKS_PER_CHAMP + 1, PEAK_FLOOR)
                  if CONTRAST_RANGE[0] < float(std[y, x]) / float(bank.stds[i])
                  < CONTRAST_RANGE[1]]
            for j, (x, y, v) in enumerate(pk[:PEAKS_PER_CHAMP]):
                other = max((q[2] for kk, q in enumerate(pk) if kk != j), default=PEAK_FLOOR)
                ev = v + UNIQUE_WEIGHT * min(v - other, UNIQUE_CAP)
                sx, sy = self._subpixel(m, x, y)
                cands.append(_Cand(i, sx + half + 0.5, sy + half + 0.5, v, ev,
                                   margin=v - other))
                if j > 0:
                    bg.append(v)
        return cands, bg

    def _coarse_search(self, feat: np.ndarray, bank: _Bank, idx: list[int]
                       ) -> list[_Cand]:
        """Fast whole-map search of the champions ``idx`` (tracked mode): Fourier NCC at a
        lower resolution (matched disc COARSE_INNER_PX wide) proposes a few peaks per
        champion, each verified by the exact NCC at the working resolution."""
        s = bank.size
        k = COARSE_INNER_PX / max(INNER_RATIO * self._bank_inner(bank), 1e-6)
        if k >= 0.9:
            return self._global_search(feat, bank, idx)[0]
        Hf, Wf = feat.shape[:2]
        Wc, Hc = max(8, int(round(Wf * k))), max(8, int(round(Hf * k)))
        featc = cv2.resize(feat, (Wc, Hc), interpolation=cv2.INTER_AREA)
        kc = Wc / float(Wf)
        bank_c = self._bank(INNER_RATIO * self._bank_inner(bank) * kc)
        maps, std = ncc_maps(featc, bank_c, idx, wl=self._wl)
        if maps.shape[1] < 2 or maps.shape[2] < 2:
            return []
        half_c = (bank_c.size - 1) / 2.0
        half = (s - 1) / 2.0
        rad = max(2, int(round(0.4 * bank_c.size)))
        r_ver = int(math.ceil(0.5 / kc)) + 1
        out: list[_Cand] = []
        for j, i in enumerate(idx):
            m = maps[j]
            pk = [(x, y, v) for x, y, v in self._peaks(m, rad, PEAKS_PER_CHAMP + 1, PEAK_FLOOR)
                  if CONTRAST_RANGE[0] < float(std[y, x]) / float(bank_c.stds[i])
                  < CONTRAST_RANGE[1]]
            for q, (x, y, v) in enumerate(pk[:PEAKS_PER_CHAMP]):
                # the true icon is the champion's best low-res peak (measured): verify it
                # and only the peaks almost as good
                if v < COARSE_VERIFY_MIN or v < pk[0][2] - COARSE_VERIFY_GAP:
                    break
                other = max((p[2] for kk, p in enumerate(pk) if kk != q), default=PEAK_FLOOR)
                margin = v - other
                cx = (x + half_c + 0.5) / kc - half - 0.5        # full-res map index
                cy = (y + half_c + 0.5) / kc - half - 0.5
                xm0 = max(0, int(math.floor(cx)) - r_ver)
                xm1 = min(Wf - s, int(math.ceil(cx)) + r_ver)
                ym0 = max(0, int(math.floor(cy)) - r_ver)
                ym1 = min(Hf - s, int(math.ceil(cy)) + r_ver)
                if xm1 < xm0 or ym1 < ym0:
                    continue
                res = local_ncc(feat[ym0:ym1 + s, xm0:xm1 + s], bank, i, wl=self._wl)
                if res is None:
                    continue
                lm, lstd = res
                ratio = lstd / float(bank.stds[i])
                mm = np.where((ratio > CONTRAST_RANGE[0]) & (ratio < CONTRAST_RANGE[1]), lm, -1.0)
                yy, xx = divmod(int(np.argmax(mm)), mm.shape[1])
                vf = float(mm[yy, xx])
                if vf < PEAK_FLOOR:
                    continue
                sx, sy = self._subpixel(mm, xx, yy)
                out.append(_Cand(i, sx + xm0 + half + 0.5, sy + ym0 + half + 0.5, vf,
                                 vf + UNIQUE_WEIGHT * min(margin, UNIQUE_CAP), margin=margin,
                                 coarse=v))
        return out

    @staticmethod
    def _bank_inner(bank: _Bank) -> float:
        """Matched disc diameter (working px) of a bank."""
        return 2.0 * math.sqrt(bank.n / math.pi) / INNER_RATIO

    def _local_search(self, feat: np.ndarray, bank: _Bank, i: int, tr: _Track, now: float,
                      kx: float, ky: float) -> list[_Cand]:
        """Search champion ``i`` in small windows around its predicted position (and around
        an unconfirmed far candidate). ``kx``, ``ky``: normalized -> working px."""
        s = bank.size
        half = (s - 1) / 2.0
        Hf, Wf = feat.shape[:2]
        pu, pv = tr.predict(now)
        dt = min(max(now - tr.t, 0.0), TRACK_FRESH_S)
        wins = [(pu, pv, LOCAL_SLACK + MAX_SPEED * dt)]
        if tr.pend is not None and now - tr.pend[4] < 1.0:
            wins.append((tr.pend[0], tr.pend[1], LOCAL_SLACK))
        out: list[_Cand] = []
        bonus = UNIQUE_WEIGHT * min(max(tr.margin, 0.0), UNIQUE_CAP)
        for k_win, (wu, wv, rn) in enumerate(wins):
            cx, cy = wu * kx - half - 0.5, wv * ky - half - 0.5     # map index of the centre
            rw = rn * kx
            xm0, xm1 = max(0, int(math.floor(cx - rw))), min(Wf - s, int(math.ceil(cx + rw)))
            ym0, ym1 = max(0, int(math.floor(cy - rw))), min(Hf - s, int(math.ceil(cy + rw)))
            if xm1 < xm0 or ym1 < ym0:
                continue
            res = local_ncc(feat[ym0:ym1 + s, xm0:xm1 + s], bank, i, wl=self._wl)
            if res is None:
                continue
            m, std = res
            ratio = std / float(bank.stds[i])
            mm = np.where((ratio > CONTRAST_RANGE[0]) & (ratio < CONTRAST_RANGE[1]), m, -1.0)
            y, x = divmod(int(np.argmax(mm)), mm.shape[1])
            v = float(mm[y, x])
            if v < PEAK_FLOOR:
                continue
            sx, sy = self._subpixel(mm, x, y)
            # (the window around an unconfirmed far candidate is not the track's own: the
            # jump penalty still applies there until the jump is confirmed)
            out.append(_Cand(i, sx + xm0 + half + 0.5, sy + ym0 + half + 0.5, v, v + bonus,
                             local=k_win == 0))
        return out

    def _structures(self) -> list[tuple[float, float, str]]:
        if self._structs is None:
            try:
                from treeaicoach.render import iter_structures

                self._structs = [(float(u), float(v), str(t))
                                 for _, u, v, _, t in iter_structures()]
            except Exception:
                self._structs = []
        return self._structs

    def _team_of(self, e: RosterEntry) -> str | None:
        if e.team in ("ORDER", "CHAOS"):
            return e.team
        mt = self._my_team
        if mt not in ("ORDER", "CHAOS"):
            return None
        return mt if e.relation != "enemy" else ("CHAOS" if mt == "ORDER" else "ORDER")

    def _struct_penalty(self, e: RosterEntry, u: float, v: float) -> float:
        """A structure glyph of the champion's ring colour right there: needs more evidence."""
        mt = self._my_team
        for su, sv, st in self._structures():
            if abs(u - su) < STRUCT_DIST and abs(v - sv) < STRUCT_DIST \
                    and math.hypot(u - su, v - sv) < STRUCT_DIST:
                if mt not in ("ORDER", "CHAOS") or (st == mt) == (e.relation != "enemy"):
                    return STRUCT_PENALTY
        return 0.0

    def _jump_penalty(self, e: RosterEntry, tr: _Track, u: float, v: float,
                      now: float) -> float:
        """Penalty of a position the champion cannot have reached since its last match."""
        age = now - tr.t
        if age > JUMP_MEMORY_S:
            return 0.0
        pu, pv = tr.predict(now)
        if math.hypot(u - pu, v - pv) <= JUMP_SLACK + MAX_SPEED * max(age, 0.0):
            return 0.0
        team = self._team_of(e)
        # recall: back to the fountain. An ally is never in the fog: he recalls standing still
        # (8 s channel) where we see him, so a walking ally does not jump home (measured,
        # det_gym: an ally portrait matching the fountain glyph while he walked mid)
        still = e.relation == "enemy" or math.hypot(tr.vu, tr.vv) < RECALL_STILL_SPEED
        for t_, (fu, fv) in _FOUNTAINS.items():
            if still and (team is None or t_ == team) and math.hypot(u - fu, v - fv) < FOUNTAIN_DIST:
                return 0.0
        p = tr.pend
        if p is not None and now - p[4] < 1.0 and math.hypot(u - p[0], v - p[1]) < 0.03 and \
                now - p[2] >= JUMP_CONFIRM_S and p[3] >= JUMP_CONFIRM_N:
            return 0.0             # confirmed: seen there over JUMP_CONFIRM_S (teleport)
        return JUMP_PENALTY

    def _camera_centre(self, bgr: np.ndarray) -> tuple[float, float] | None:
        """Camera rectangle centre: this frame's rectangle, else the older finder (cached a
        few frames)."""
        f = self._state.frames
        r = self._camera_rect_now(bgr)
        if r is not None:
            self._cam = (f, r.center)
            return r.center
        if f - self._cam[0] >= 4:
            try:
                from treeaicoach.identifier import find_camera_center

                c = find_camera_center(bgr)
            except Exception:
                c = None
            self._cam = (f, c)
        return self._cam[1]

    def _score_cand(self, c: _Cand, bgr: np.ndarray, kx: float, ky: float, R_px: float,
                    W: int, H: int, now: float, floor: float,
                    exclude: Sequence[tuple[float, float]] = ()) -> None:
        """Final score: evidence - penalties + ring colour agreement (in place)."""
        e = self._entries[c.i]
        u, v = c.x / kx, c.y / ky
        pen = self._struct_penalty(e, u, v)
        tr = self._tracks.get(c.i)
        if tr is not None and not c.local:
            jp = self._jump_penalty(e, tr, u, v, now)
            if jp:
                c.note = "jump"
            pen += jp
        base = c.ev - pen
        if base < floor:
            c.tot = base
            return
        if self.grey:
            # no colour: the ring cannot vote; its drawing still can (a ring lighter than
            # the dark line inside it, which terrain / glyphs do not have)
            c.tot = base + (GREY_RING_BONUS if self._grey_ring(bgr, u * W, v * H, R_px)
                            >= GREY_RING_DL else -GREY_NO_RING)
            return
        ring = self._ring_pixels(bgr, u * W, v * H, R_px, exclude)
        f_en, f_al = self.rings.classify(ring)
        own, opp = (f_en, f_al) if e.relation == "enemy" else (f_al, f_en)
        c.tot = base + RING_WEIGHT * (own - opp) - NO_RING_PENALTY * max(0.0, 1.0 - own / 0.3)
        c.f_en, c.f_al, c.ring = f_en, f_al, ring

    def _get_verifier(self) -> Any:
        if not self._verifier_tried:
            self._verifier_tried = True
            try:
                from treeaicoach.patch_classifier import PatchVerifier

                self.verifier = PatchVerifier.load()
            except Exception:
                self.verifier = None
        return self.verifier

    def _ring_second_opinion(self, cands: list, bgr: np.ndarray, kx: float, ky: float,
                             r_norm: float, thr: float, relax: dict) -> None:
        """Team of a candidate whose ring colour did not vote for its champion's team, from
        the patch verifier (whole icon patch, trained on the real 2026 art): a thin dark ring
        blurred / JPEG'd to almost grey (a dark red read as the ally colour) no longer sinks a
        good portrait match. Only candidates a correct ring would make acceptable are asked
        (at most RING_VERIFY_MAX per frame, one batched call)."""
        ver = self._get_verifier()
        if ver is None:
            return
        ents = self._entries
        todo = []
        for c in cands:
            # (not my own portrait: a custom skin makes it match other allies weakly; my
            # icon has the camera lock / learner)
            if c.ring is None or ents[c.i].relation == "self":
                continue
            own, opp = (c.f_en, c.f_al) if ents[c.i].relation == "enemy" else (c.f_al, c.f_en)
            if own >= RING_VERIFY_OWN:
                continue
            ring_term = RING_WEIGHT * (own - opp) - NO_RING_PENALTY * max(0.0, 1.0 - own / 0.3)
            base = c.tot - ring_term
            if base + RING_WEIGHT < thr - relax.get(id(c), 0.0) - VERIFY_ZONE:
                continue                    # even a perfect ring would not make it
            todo.append((base, c))
        if not todo:
            return
        todo = sorted(todo, key=lambda z: -z[0])[:RING_VERIFY_MAX]
        try:
            P = ver.verify_candidates(bgr, [(c.x / kx, c.y / ky, r_norm) for _b, c in todo])
        except Exception:
            return
        for (base, c), p in zip(todo, P):
            icon = 1.0 - float(p[0])
            if icon < VERIFY_MIN:
                continue
            p_en, p_al = float(p[1]) / icon, float(p[2]) / icon
            enemy = ents[c.i].relation == "enemy"
            own, opp = (p_en, p_al) if enemy else (p_al, p_en)
            if own < RING_VERIFY_TEAM:
                continue
            c.f_en, c.f_al = (own, opp) if enemy else (opp, own)
            c.tot = base + RING_WEIGHT * (own - opp)
            c.note = c.note or "ring-verified"

    @staticmethod
    def _icon_prob(ver: Any, bgr: np.ndarray, c: _Cand, kx: float, ky: float,
                   r_norm: float) -> float:
        try:
            return float(ver.icon_prob(bgr, [(c.x / kx, c.y / ky, r_norm)])[0])
        except Exception:
            return 1.0

    def _verified(self, ver: Any, bgr: np.ndarray, c: _Cand, kx: float, ky: float,
                  r_norm: float) -> bool:
        """The verifier says candidate ``c`` is a champion icon of its champion's team."""
        if self.grey:
            return False
        try:
            p = ver.verify_candidates(bgr, [(c.x / kx, c.y / ky, r_norm)])[0]
        except Exception:
            return False
        icon = 1.0 - float(p[0])
        team = float(p[1] if self._entries[c.i].relation == "enemy" else p[2])
        if icon >= VERIFY_MIN and team >= VERIFY_TEAM * icon:
            c.note = c.note or "verified"
            return True
        return False

    @staticmethod
    def _has_white(feat: np.ndarray, x: float, y: float, r: float) -> bool:
        """White unsaturated pixels (camera lines, timer texts) on the disc at (x, y)."""
        x0, x1 = max(0, int(x - r)), min(feat.shape[1], int(x + r) + 1)
        y0, y1 = max(0, int(y - r)), min(feat.shape[0], int(y + r) + 1)
        p = feat[y0:y1, x0:x1]
        if p.size == 0:
            return False
        w = (p[:, :, 0] > 215.0) & (np.abs(p[:, :, 1]) < 14.0) & (np.abs(p[:, :, 2]) < 14.0)
        return float(w.mean()) > 0.04

    def _rescue(self, c: _Cand, feat: np.ndarray, bank: _Bank, work_centres: list,
                D_work: float, caps: bool = False, trim: bool = False,
                search: int | None = None) -> float | None:
        """Occlusion-tolerant evidence of candidate ``c``: best NCC on the visible part of
        the icon (partial discs, without overlapping accepted icons and white lines /
        texts), over +-1 working px. None when too little of the icon is visible."""
        s = bank.size
        half = (s - 1) / 2.0
        Hf, Wf = feat.shape[:2]
        x0 = int(round(c.x - half - 0.5))
        y0 = int(round(c.y - half - 0.5))
        rr = OCC_SEARCH if work_centres else 1     # a covered icon's NCC peak drifts away
        if search is not None:
            rr = min(rr, int(search))
        offs = [(dx, dy) for dy in range(-rr, rr + 1) for dx in range(-rr, rr + 1)
                if 0 <= x0 + dx <= Wf - s and 0 <= y0 + dy <= Hf - s]
        if not offs:
            return None
        P = np.stack([feat[y0 + dy:y0 + dy + s, x0 + dx:x0 + dx + s] for dx, dy in offs])
        raw = bank.raw[c.i]
        yy, xx = np.mgrid[0:s, 0:s].astype(np.float32)
        # known occluders (accepted icons on top, white lines / texts) are masked out; the
        # partial discs (unknown occluder on one side: ping...) only for a tracked champion
        base = np.concatenate([bank.mask[None], bank.caps], axis=0) if caps and \
            bank.caps is not None else bank.mask[None]                    # [k, s, s]
        # white lines / texts on the patch where the portrait is not white (all offsets at once)
        tl = raw[:, :, 0]
        white = (P[:, :, :, 0] > 215.0) & (np.abs(P[:, :, :, 1]) < 14.0) & \
            (np.abs(P[:, :, :, 2]) < 14.0) & (tl < 190.0)[None]
        keep = ~white                                                    # [p, s, s]
        if work_centres:
            ox = np.asarray([x0 + dx for dx, _dy in offs], np.float32)[:, None, None]
            oy = np.asarray([y0 + dy for _dx, dy in offs], np.float32)[:, None, None]
            r2 = (0.53 * D_work) ** 2
            for (ax, ay) in work_centres:          # accepted icons drawn over this one
                keep &= (xx[None] + ox + 0.5 - ax) ** 2 + (yy[None] + oy + 0.5 - ay) ** 2 >= r2
        masks = base[None] * keep[:, None].astype(np.float32)
        if trim:
            masks = np.concatenate([masks, self._trimmed_masks(P, raw, masks[:, 0])], axis=1)
        area = masks.sum(axis=(2, 3)) / max(bank.n, 1e-6)                 # [p, k]
        sc = masked_ncc(P, raw, masks, wl=self._wl)
        # fewer pixels -> chance matches are easier: penalty growing with the hidden part
        sc = np.where(area >= OCC_MIN_AREA,
                      sc - (OCC_PENALTY + OCC_AREA_PENALTY * (1.0 - area)) * (area < 0.97), -1.0)
        j = int(np.argmax(sc))
        best = float(sc.flat[j])
        self.last_rescue = (best, float(area.flat[j]))
        dx, dy = offs[j // sc.shape[1]]
        self.last_rescue_pos = (x0 + dx + half + 0.5, y0 + dy + half + 0.5)
        return best if best > -1.0 else None

    def ring_map(self, feat: np.ndarray, R_w: float) -> dict[str, np.ndarray]:
        """Per team side ("ally" / "enemy"): ring evidence map at the working resolution =
        mean closeness to the side's (learned) ring colour on the ring annulus minus the
        larger of the same inside / outside it (a thin ring, not an area of that colour)."""
        H, W = feat.shape[:2]
        # the three annulus filters share one forward DFT per side (kernel spectra cached):
        # 8 DFTs per frame instead of 18 with cv2.filter2D (same values, zero borders)
        n = 2 * int(math.ceil(R_w * 1.35)) + 1
        h0 = n // 2
        shape = (cv2.getOptimalDFTSize(H + n - 1), cv2.getOptimalDFTSize(W + n - 1))
        key = (round(float(R_w), 4), shape)
        specs = self._ring_specs.get(key)
        if specs is None:
            specs = [_spec(_annulus(R_w, a, b), shape) for a, b in RING_PROP_ANNULI]
            self._ring_specs = {key: specs}
        out: dict[str, np.ndarray] = {}
        cent = self.rings.centroid
        buf = np.zeros(shape, np.float32)
        for side, rels in (("ally", ("ally", "self")), ("enemy", ("enemy",))):
            close = buf[:H, :W]
            close[:] = 0.0
            for rel in rels:
                c = cent[rel] - np.float32([0.0, 128.0, 128.0])
                d2 = RING_PROP_LW * (feat[:, :, 0] - c[0]) ** 2 + (feat[:, :, 1] - c[1]) ** 2 + \
                    (feat[:, :, 2] - c[2]) ** 2
                np.maximum(close, np.exp(-d2 / RING_PROP_SIGMA ** 2), out=close)
            F = cv2.dft(buf)
            r, i_, o_ = (cv2.idft(cv2.mulSpectrums(F, sp, 0),
                                  flags=cv2.DFT_SCALE | cv2.DFT_REAL_OUTPUT)[h0:h0 + H, h0:h0 + W]
                         for sp in specs)
            out[side] = r - np.maximum(i_, o_)
        return out

    def _ring_proposals(self, feat: np.ndarray, bank: _Bank, accepted: list, used: set,
                        dead: set, kx: float, ky: float, R_w: float, D_work: float,
                        thr: float) -> list[_Cand]:
        """Stage 5c (see _detect): candidates from unexplained rings, identity among the
        ring team's missing alive champions only, one-to-one (best score first)."""
        ents = self._entries
        maps = self.ring_map(feat, R_w)
        k = max(2, int(round(R_w)))
        K = np.ones((k, k), np.uint8)
        structs = self._structures()
        props: list[tuple[float, str, float, float]] = []
        for side, S in maps.items():
            pk = (S >= RING_PROP_MIN) & (S >= cv2.dilate(S, K))
            ys, xs = np.nonzero(pk)
            for y, x in zip(ys.tolist(), xs.tolist()):
                cx, cy = x + 0.5, y + 0.5
                if any(math.hypot(cx - a.x, cy - a.y) < RING_PROP_EXPLAINED * D_work for a in accepted):
                    continue
                u, v = cx / kx, cy / ky
                if any(math.hypot(u - su, v - sv) < STRUCT_DIST for su, sv, _t in structs) or \
                        any(math.hypot(u - fu, v - fv) < FOUNTAIN_DIST for fu, fv in _FOUNTAINS.values()):
                    continue        # structure glyphs / fountains have team-coloured outlines
                props.append((float(S[y, x]), side, cx, cy))
        out: list[_Cand] = []
        taken = set(used)
        props = sorted(props, reverse=True)[:RING_PROP_MAX]
        ver = self._get_verifier() if RING_PROP_VERIFY > 0 else None
        if ver is not None and props and self._ring_img is not None:
            # (cost) rings of glyphs / terrain are dropped before the per-champion re-scoring
            try:
                p_icon = ver.icon_prob(self._ring_img, [(cx / kx, cy / ky, R_w / kx)
                                                        for _s, _d, cx, cy in props])
                props = [pr for pr, pi in zip(props, p_icon) if pi >= RING_PROP_VERIFY]
            except Exception:
                pass
        for score, side, cx, cy in props:
            pool = [j for j, e in enumerate(ents) if j not in taken and j not in dead
                    and (e.relation == "enemy") == (side == "enemy")]
            if not pool:
                continue
            near = [(a.x, a.y) for a in accepted + out
                    if math.hypot(cx - a.x, cy - a.y) < 1.05 * D_work]
            if any(math.hypot(cx - a.x, cy - a.y) < RING_PROP_EXPLAINED * D_work for a in out):
                continue
            scored = []
            for j in pool:
                sj = self._rescue(_Cand(j, cx, cy, 0.0, 0.0), feat, bank, near, D_work, caps=True,
                                  search=RING_PROP_SEARCH)
                if sj is not None:
                    scored.append((sj, j, self.last_rescue_pos))
            if not scored:
                continue
            scored.sort(key=lambda z: -z[0])
            best, j, pos = scored[0]
            second = scored[1][0] if len(scored) > 1 else RING_PROP_ID_MIN - RING_PROP_ID_GAP
            if RING_DEBUG is not None:
                RING_DEBUG.append((ents[j].alias, side, cx / kx, cy / ky, score, best, second))
            if best < RING_PROP_ID_MIN or best - second < RING_PROP_ID_GAP or \
                    score + RING_PROP_ID_W * (best - RING_PROP_ID_MIN) < RING_PROP_ACCEPT:
                continue
            c = _Cand(j, pos[0], pos[1], best, best, note="ring")
            taken.add(j)
            out.append(c)
        return out

    def _me_under(self, accepted: list, cam_pt: tuple | None, kx: float, ky: float, me: int,
                  now: float, bgr: np.ndarray | None = None, r_norm: float = 0.0
                  ) -> tuple[float, float] | None:
        """My position when my portrait is not matched at all (custom skin not learned yet,
        icon under my support) but the camera point has stayed for UNDER_CONFIRM frames on
        the same accepted ally icon, or on an unexplained icon the verifier calls an ally icon
        (camera locked on me)."""
        if UNDER_CONFIRM <= 0 or cam_pt is None:
            self._under = (None, 0)
            return None
        best = None
        for a in accepted:
            if a.i == me or self._entries[a.i].relation == "enemy":
                continue
            d = math.hypot(a.x / kx - cam_pt[0], a.y / ky - cam_pt[1])
            if d < SELF_CAM_DIST and (best is None or d < best[0]):
                best = (d, a)
        # an unexplained ally icon right at the camera point (my custom icon) first; an accepted
        # ally icon only when nothing else is there (mine drawn under it)
        pos = self._verified_ally_at(bgr, cam_pt, r_norm, accepted, kx, ky)
        key = "cam"
        if pos is None:
            if best is None:
                self._under = (None, 0)
                return None
            a = best[1]
            key, pos = a.i, (a.x / kx, a.y / ky)
        # the camera must FOLLOW that icon (same offset), not sweep over it (dragged camera)
        off = (pos[0] - cam_pt[0], pos[1] - cam_pt[1])
        prev = self._under
        same = prev[0] == key and len(prev) > 2 and \
            math.hypot(off[0] - prev[2][0], off[1] - prev[2][1]) < UNDER_STABLE
        n = prev[1] + 1 if same else 1
        self._under = (key, n, prev[2] if same else off)
        return pos if n >= UNDER_CONFIRM else None

    def _verified_ally_at(self, bgr: np.ndarray | None, p: tuple, r_norm: float, accepted: list,
                          kx: float, ky: float) -> tuple[float, float] | None:
        """An ally-side icon (patch verifier) within SELF_CAM_DIST of ``p`` not explained by an
        accepted match: its centre (best of a few probes), else None."""
        ver = self._get_verifier()
        if ver is None or bgr is None or r_norm <= 0:
            return None
        d = 0.35 * 2 * r_norm
        pts = [(p[0] + dx, p[1] + dy) for dx in (-d, 0.0, d) for dy in (-d, 0.0, d)]
        pts = [q for q in pts if not any(math.hypot(q[0] - a.x / kx, q[1] - a.y / ky) < 2 * r_norm * 0.6
                                         for a in accepted)]
        if not pts:
            return None
        P = ver.verify_candidates(bgr, [(u, v, r_norm) for u, v in pts])
        best = None
        for q, pr in zip(pts, P):
            icon = 1.0 - float(pr[0])
            if icon >= UNDER_VERIFY and float(pr[2]) >= STACKV_TEAM * icon and \
                    (best is None or icon > best[0]):
                best = (icon, q)
        return best[1] if best is not None else None

    def _stack_proposals(self, bgr: np.ndarray, feat: np.ndarray, bank: _Bank, accepted: list,
                         used: set, dead: set, kx: float, ky: float, r_norm: float,
                         D_work: float) -> list[_Cand]:
        """Stage 5d (see _detect): verifier probes around the accepted icons -> proposals of
        partly covered icons -> identity among the proposal team's missing alive champions."""
        ver = self._get_verifier()
        if ver is None:
            return []
        ents = self._entries
        D = D_work / kx                                   # icon diameter (normalized)
        pts = []
        for a in accepted:
            au, av = a.x / kx, a.y / ky
            for d in STACKV_DIST:
                for k in range(STACKV_ANGLES):
                    ang = 2.0 * math.pi * (k + 0.5 * (d != STACKV_DIST[0])) / STACKV_ANGLES
                    u, v = au + d * D * math.cos(ang), av + d * D * math.sin(ang)
                    if not (0.02 < u < 0.98 and 0.02 < v < 0.98):
                        continue
                    if any(math.hypot(u - b.x / kx, v - b.y / ky) < STACKV_MIN_SEP * D for b in accepted):
                        continue
                    pts.append((u, v))
        if not pts:
            return []
        structs = self._structures()
        pts = [p for p in pts if not any(math.hypot(p[0] - su, p[1] - sv) < STRUCT_DIST for su, sv, _t in structs)
               and not any(math.hypot(p[0] - fu, p[1] - fv) < FOUNTAIN_DIST for fu, fv in _FOUNTAINS.values())]
        if not pts:
            return []
        P = ver.verify_candidates(bgr, [(u, v, r_norm) for u, v in pts])
        props = []
        for (u, v), p in zip(pts, P):
            icon = 1.0 - float(p[0])
            if icon < STACKV_MIN:
                continue
            side = "enemy" if p[1] >= p[2] else "ally"
            if max(p[1], p[2]) < STACKV_TEAM * icon:
                continue
            props.append((icon, side, u, v))
        props.sort(reverse=True)
        kept: list = []
        for pr in props:
            if all(math.hypot(pr[2] - q[2], pr[3] - q[3]) > 0.5 * D for q in kept):
                kept.append(pr)
        out: list[_Cand] = []
        taken = set(used)
        for icon, side, u, v in kept[:STACKV_MAX]:
            pool = [j for j, e in enumerate(ents) if j not in taken and j not in dead
                    and (e.relation == "enemy") == (side == "enemy")]
            if not pool:
                continue
            cx, cy = u * kx, v * ky
            if any(math.hypot(cx - c.x, cy - c.y) < 0.5 * D_work for c in out):
                continue
            near = [(a.x, a.y) for a in accepted + out if math.hypot(cx - a.x, cy - a.y) < 1.05 * D_work]
            scored = []
            for j in pool:
                sj = self._rescue(_Cand(j, cx, cy, 0.0, 0.0), feat, bank, near, D_work, caps=True,
                                  search=STACKV_SEARCH)
                if sj is not None:
                    scored.append((sj, j, self.last_rescue_pos))
            if not scored:
                continue
            scored.sort(key=lambda z: -z[0])
            best, j, pos = scored[0]
            second = scored[1][0] if len(scored) > 1 else STACKV_ID_MIN - STACKV_ID_GAP
            if best < STACKV_ID_MIN or best - second < STACKV_ID_GAP:
                continue
            c = _Cand(j, pos[0], pos[1], best, best, note="stacked")
            taken.add(j)
            out.append(c)
        return out

    def _best_identity(self, i: int, score: float, px: float, py: float, feat: np.ndarray,
                       bank: _Bank, near: list, D_work: float, caps: bool, dead: set) -> bool:
        """A trimmed re-score keeps only the best part of an icon: chance matches of the
        wrong champion of the same team get easier. True when champion ``i`` explains the
        icon at ``(px, py)`` (working px) better than every other champion of its side by
        TRIM_ID_GAP (each re-scored the same way)."""
        ents = self._entries
        side = ents[i].relation == "enemy"
        saved = (self.last_rescue, self.last_rescue_pos)
        try:
            for j, e in enumerate(ents):
                if j == i or j in dead or (e.relation == "enemy") != side:
                    continue
                cj = _Cand(j, px, py, 0.0, 0.0)
                sj = self._rescue(cj, feat, bank, near, D_work, caps=caps, trim=True)
                if sj is not None and sj > score - TRIM_ID_GAP:
                    return False
            return True
        finally:
            self.last_rescue, self.last_rescue_pos = saved

    def _trimmed_masks(self, P: np.ndarray, raw: np.ndarray, m0: np.ndarray) -> np.ndarray:
        """Masks ``[p, 1, s, s]``: ``m0`` without the TRIM_FRAC worst-fitting pixels of each
        patch (z-scored residual against the template). Unknown occluders that are not white
        (overlay labels on dark boxes, thin rings, pings) are dropped this way."""
        wl = self._wl
        out = np.empty((P.shape[0], 1) + m0.shape[1:], np.float32)
        for j in range(P.shape[0]):
            m = m0[j]
            n = float(m.sum())
            if n < 8:
                out[j, 0] = m
                continue
            d = np.zeros(m.shape, np.float32)
            for chs, w in (((0,), wl), ((1, 2), 1.0 - wl)):
                for ch in chs:
                    a, b = P[j][:, :, ch], raw[:, :, ch]
                    ma, mb = float((a * m).sum() / n), float((b * m).sum() / n)
                    sa = math.sqrt(max(float((((a - ma) ** 2) * m).sum() / n), 0.0)) + 3.0
                    sb = math.sqrt(max(float((((b - mb) ** 2) * m).sum() / n), 0.0)) + 3.0
                    d += (w / len(chs)) * ((a - ma) / sa - (b - mb) / sb) ** 2
            vals = d[m > 0.5]
            cut = float(np.percentile(vals, 100.0 * (1.0 - TRIM_FRAC))) if vals.size else 0.0
            out[j, 0] = m * (d <= cut)
        return out

    def _ring_membership(self, lab: np.ndarray, rel: str) -> tuple[np.ndarray, np.ndarray]:
        """Per-pixel (own, other team) ring-colour masks of a Lab crop ``[h, w, 3]``."""
        w = np.asarray([0.35, 1.0, 1.0], np.float32)
        if rel == "enemy":
            pe, po = self.rings._protos("enemy"), self.rings._protos("ally") + self.rings._protos("self")
        else:
            pe, po = self.rings._protos("ally") + self.rings._protos("self"), self.rings._protos("enemy")
        X = lab.reshape(-1, 3).astype(np.float32) * w
        out = []
        for P in (pe, po):
            Pm = np.asarray(P, np.float32) * w
            d2 = (X * X).sum(1)[:, None] - 2.0 * (X @ Pm.T) + (Pm * Pm).sum(1)[None]
            out.append(np.sqrt(np.maximum(d2, 0.0)).min(axis=1))
        d_own, d_opp = out
        near = RingColorModel.NEAR
        own = (d_own < near) & (d_own < 0.8 * d_opp)
        opp = (d_opp < near) & (d_opp < 0.8 * d_own)
        h, w_ = lab.shape[:2]
        return own.reshape(h, w_), opp.reshape(h, w_)

    def _stack_search(self, bgr: np.ndarray, feat: np.ndarray, bank: _Bank,
                      accepted: list, used: set, dead: set, now: float, kx: float, ky: float,
                      R_px: float, D_work: float, thr: float) -> list[_Cand]:
        """Champions hidden UNDER accepted icons (stacks). For each champion tracked less
        than STACK_HOLD_S ago whose predicted position is next to an accepted icon: the
        visible arc of its ring (own team colour, outside the covering icons) is fitted
        with the known radius over the speed-bounded window around the prediction; the
        best fits are checked against the visible part of the portrait (masked NCC).
        Returns accepted candidates (working px), ``note == "stacked"``."""
        H, W = bgr.shape[:2]
        ents = self._entries
        D_n = 2.0 * R_px / W
        out: list[_Cand] = []
        s = bank.size
        half = (s - 1) / 2.0
        Hf, Wf = feat.shape[:2]
        for i, tr in list(self._tracks.items()):
            if i in used or i in dead or i >= len(ents):
                continue
            age = now - tr.t
            if age > STACK_HOLD_S or age < 0 or tr.hits < 2:
                continue
            pu, pv = tr.predict(now)
            rs = LOCAL_SLACK + MAX_SPEED * min(age, 1.0)
            cover = [a for a in accepted + out
                     if math.hypot(a.x / kx - pu, a.y / ky - pv) < STACK_NEAR * D_n + rs]
            if not cover:
                continue
            e = ents[i]
            # candidate centres (original px) in the window, next to / under a cover
            cxp, cyp, rsp = pu * W, pv * H, rs * W
            step = max(1.0, R_px / 6.0)
            g = np.arange(-rsp, rsp + 1e-6, step, dtype=np.float32)
            gx, gy = np.meshgrid(g, g)
            keep = gx * gx + gy * gy <= rsp * rsp
            C = np.stack([cxp + gx[keep], cyp + gy[keep]], axis=1)          # [n, 2]
            cov = np.asarray([(a.x / kx * W, a.y / ky * H) for a in cover], np.float32)
            cov_ex = np.asarray([(STACK_EXCL_SELF if ents[a.i].relation == "self" else STACK_EXCL)
                                 * R_px for a in cover], np.float32)
            dc = np.sqrt(((C[:, None, :] - cov[None]) ** 2).sum(-1))       # [n, k]
            C = C[(dc.min(axis=1) < 2.0 * R_px) & (dc.min(axis=1) > 0.3 * R_px)]
            if len(C) == 0:
                continue
            # ring-colour masks of the region (candidates + the covering icons' halos)
            m = int(math.ceil(1.2 * R_px)) + 2
            mc = int(math.ceil(1.5 * R_px)) + 2
            x0 = max(0, min(int(C[:, 0].min()) - m, int(cov[:, 0].min()) - mc))
            x1 = min(W, max(int(C[:, 0].max()) + m, int(cov[:, 0].max()) + mc) + 1)
            y0 = max(0, min(int(C[:, 1].min()) - m, int(cov[:, 1].min()) - mc))
            y1 = min(H, max(int(C[:, 1].max()) + m, int(cov[:, 1].max()) + mc) + 1)
            if x1 - x0 < 4 or y1 - y0 < 4:
                continue
            lab = cv2.cvtColor(np.ascontiguousarray(bgr[y0:y1, x0:x1]), cv2.COLOR_BGR2LAB)
            own_m, opp_m = self._ring_membership(lab, e.relation)
            # adaptive exclusion: how far each covering icon's own ring / glow bleeds (blur,
            # JPEG, outline) = first radius where that colour is on few of its directions
            # (on the side away from the hidden icon's predicted position: not its own ring)
            for k in range(len(cov)):
                ddx, ddy = cxp - cov[k, 0], cyp - cov[k, 1]
                dn = math.hypot(ddx, ddy)
                far = (_LEAK_COS * ddx + _LEAK_SIN * ddy < -0.2 * dn) if dn > 0.15 * R_px \
                    else np.ones(_LEAK_COS.shape, bool)
                for rr in _STACK_LEAK_R:
                    if rr * R_px < cov_ex[k]:
                        continue
                    qx = np.floor(cov[k, 0] + rr * R_px * _LEAK_COS).astype(np.int32)
                    qy = np.floor(cov[k, 1] + rr * R_px * _LEAK_SIN).astype(np.int32)
                    ok = (qx >= x0) & (qx < x1) & (qy >= y0) & (qy < y1) & far
                    if not ok.any():
                        break
                    frac = float(own_m[qy[ok] - y0, qx[ok] - x0].mean())
                    if frac < 0.35:
                        break
                    cov_ex[k] = (rr + 0.06) * R_px
            px = C[:, None, 0] + R_px * _RING_DX.reshape(1, -1)              # [n, 120]
            py = C[:, None, 1] + R_px * _RING_DY.reshape(1, -1)
            xi, yi = np.floor(px).astype(np.int32), np.floor(py).astype(np.int32)
            vis = (xi >= x0) & (xi < x1) & (yi >= y0) & (yi < y1)
            for (ax, ay), ex in zip(cov, cov_ex):
                vis &= (px - ax) ** 2 + (py - ay) ** 2 > ex * ex
            xc = np.clip(xi - x0, 0, x1 - x0 - 1)
            yc = np.clip(yi - y0, 0, y1 - y0 - 1)
            # per direction (40 angles): own / other colour on any of the 3 radii (robust
            # to a sub-pixel radius error), visible at the middle radius
            n = len(C)
            own_s, opp_s = own_m[yc, xc] & vis, opp_m[yc, xc] & vis
            own = own_s.reshape(n, 3, -1).any(axis=1)
            opp = opp_s.reshape(n, 3, -1).any(axis=1)
            vis = vis.reshape(n, 3, -1)[:, 1]
            own &= vis
            opp &= vis
            nvis = vis.sum(axis=1)
            n_own, n_opp = own.sum(axis=1), opp.sum(axis=1)
            dpred = np.hypot(C[:, 0] - cxp, C[:, 1] - cyp) / max(R_px, 1e-6)
            # ring fit: own-colour samples on the circle (all radii), other colour penalized
            score = own_s.sum(axis=1) - 1.5 * opp_s.sum(axis=1) - 0.5 * dpred
            for j in np.argsort(-score)[:3]:
                if n_own[j] < 3:
                    break
                vis_f = nvis[j] / float(vis.shape[1])
                own_f = n_own[j] / max(1.0, float(nvis[j]))
                opp_f = n_opp[j] / max(1.0, float(nvis[j]))
                # visible part of the portrait (working px)
                xw, yw = C[j, 0] * kx / W, C[j, 1] * ky / H
                tx, ty = int(round(xw - half - 0.5)), int(round(yw - half - 0.5))
                area, nv = 0.0, 0.0
                if 0 <= tx <= Wf - s and 0 <= ty <= Hf - s:
                    P = feat[ty:ty + s, tx:tx + s]
                    yy, xx = np.mgrid[0:s, 0:s].astype(np.float32)
                    mk = bank.mask.copy()
                    for a in cover:
                        d2 = (xx + tx + 0.5 - a.x) ** 2 + (yy + ty + 0.5 - a.y) ** 2
                        mk[d2 < (0.53 * D_work) ** 2] = 0.0
                    white = (P[:, :, 0] > 215.0) & (np.abs(P[:, :, 1]) < 14.0) & \
                        (np.abs(P[:, :, 2]) < 14.0) & (bank.raw[i][:, :, 0] < 190.0)
                    mk[white] = 0.0
                    area = float(mk.sum()) / max(bank.n, 1e-6)
                    if area >= 0.12:
                        nv = float(masked_ncc(P[None], bank.raw[i], mk[None], wl=self._wl)[0, 0])
                arc_ok = vis_f >= STACK_ARC_VIS and own_f >= STACK_ARC_OWN and \
                    opp_f <= STACK_ARC_OPP
                a_, b_, c_ = STACK_NCC_MIN
                ncc_ok = area < STACK_NCC_AREA or \
                    nv >= a_ + b_ * min(1.0, (area - STACK_NCC_AREA) / c_)
                strong = area >= STACK_NCC_STRONG_AREA and nv >= STACK_NCC_STRONG and \
                    own_f >= 0.3 and opp_f <= own_f
                self.last_stack = (e.alias, round(vis_f, 3), round(own_f, 3), round(opp_f, 3),
                                   round(area, 3), round(nv, 3), bool(arc_ok), bool(ncc_ok))
                if not ((arc_ok and ncc_ok) or strong):
                    continue
                c = _Cand(i, xw, yw, nv, thr + 0.02, local=True, tot=thr + 0.02,
                          f_en=own_f if e.relation == "enemy" else opp_f,
                          f_al=opp_f if e.relation == "enemy" else own_f, note="stacked")
                if any(math.hypot(c.x - a.x, c.y - a.y) < 0.2 * D_work for a in accepted + out):
                    continue
                out.append(c)
                break
        return out

    def _grey_ring(self, bgr: np.ndarray, cx: float, cy: float, R_px: float) -> float:
        """Ring lightness - dark line lightness (best over +-1 px) at an icon centre."""
        if self._grey_L is None:
            self._grey_L = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)[:, :, 0].astype(np.float32)
        best = -1e9
        ang = _LEAK_COS, _LEAK_SIN
        for dx in (-1.0, 0.0, 1.0):
            for dy in (-1.0, 0.0, 1.0):
                vals = []
                for r in (0.93, 1.0, 0.75, 0.82):
                    xs = (cx - 0.5 + dx + r * R_px * ang[0]).astype(np.float32).reshape(1, -1)
                    ys = (cy - 0.5 + dy + r * R_px * ang[1]).astype(np.float32).reshape(1, -1)
                    vals.append(float(np.median(cv2.remap(self._grey_L, xs, ys, cv2.INTER_LINEAR,
                                                           borderMode=cv2.BORDER_REPLICATE))))
                best = max(best, max(vals[0], vals[1]) - min(vals[2], vals[3]))
        return best

    def _changed(self, work: np.ndarray, D_work: float, kx: float, ky: float,
                 now: float) -> bool:
        """Did an icon-sized change appear since the previous frame, away from the tracked
        champions? (absolute difference of the working image, opened with a disc of
        CHANGE_OPEN x the icon diameter: camera lines, minion dots, pings' thin parts and
        fog edges vanish; a champion appearing from the fog does not). ~0.3 ms."""
        g = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
        prev, self._prev_gray = self._prev_gray, g
        if prev is None or prev.shape != g.shape:
            return True
        m = (cv2.absdiff(g, prev) > CHANGE_DIFF).astype(np.uint8)
        k = max(3, int(round(CHANGE_OPEN * D_work)) | 1)
        if self._open_k is None or self._open_k.shape[0] != k:
            self._open_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        if not m.any():
            return False
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, self._open_k)
        if not m.any():
            return False
        r = int(math.ceil(0.8 * D_work))
        for tr in self._tracks.values():          # tracked champions moving: explained
            if now - tr.t <= TRACK_FRESH_S:
                pu, pv = tr.predict(now)
                cv2.circle(m, (int(round(pu * kx)), int(round(pv * ky))), r, 0, -1)
        return bool(m.any())

    @staticmethod
    def is_grey(bgr: np.ndarray) -> bool:
        """A colourless minimap (death greyscale filter, desaturated capture): the 95th
        percentile of the Lab chroma of a small copy is below GREY_CHROMA."""
        small = cv2.resize(bgr, (48, 48), interpolation=cv2.INTER_NEAREST)
        lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB).astype(np.float32)
        chroma = np.hypot(lab[:, :, 1] - 128.0, lab[:, :, 2] - 128.0)
        return float(np.percentile(chroma, 95)) < GREY_CHROMA

    def _detect(self, bgr: np.ndarray, now: float) -> list[Detection]:
        st = self._state
        H, W = bgr.shape[:2]
        # greyscale frames: lightness-only NCC, no ring colour vote (see GREY_*)
        self.grey = self.is_grey(bgr)
        self._grey_L = None
        self._wl = 1.0 if self.grey else LIGHTNESS_WEIGHT
        old_scale = st.scale
        scale = self._current_scale(bgr)
        st.frames += 1
        st.since_calib += 1
        raw_bgr = bgr
        if self.clean_camera:
            # the minimap never moves, the camera rectangle does: its 1-2 px white lines
            # crossing an icon must not break the match (nor be a ring colour)
            bgr = clean_camera_lines(bgr, self._camera_rect_now(raw_bgr))
        ents = self._entries
        n_e = len(ents)
        inner_full = INNER_RATIO * scale * W
        factor = min(1.0, WORK_INNER_PX / max(inner_full, 1e-6))
        work = self._work(bgr, factor)
        fx, fy = work.shape[1] / float(W), work.shape[0] / float(H)
        bank = self._bank(inner_full * fx)
        feat = _features(work)
        s = bank.size
        if len(bank.tmpl) != n_e or feat.shape[0] < s + 1 or feat.shape[1] < s + 1:
            return []
        kx, ky = W * fx, H * fy                         # normalized -> working px
        R_px = 0.5 * scale * W                          # icon radius (original px)
        r_norm = R_px / W
        D_work = scale * W * fx                         # icon diameter (working px)
        for i in [i for i, tr in self._tracks.items()
                  if i >= n_e or now - tr.t > JUMP_MEMORY_S or now < tr.t - 1.0]:
            del self._tracks[i]
        dead = self._dead_now(now)
        # whole-map (Fourier) search: every champion at the start / after a scale change;
        # then half of the roster every FULL_EVERY / 2 frames (each champion every
        # FULL_EVERY frames, half the cost spike)
        if st.scale != old_scale or st.frames <= 2 * CALIB_FRAMES or st.last_full < 0:
            full_set = set(range(n_e))
            st.last_full = st.frames
        elif st.frames - st.last_full >= max(1, FULL_EVERY // 2):
            full_set = {i for i in range(n_e) if i % 2 == st.full_parity}
            st.full_parity ^= 1
            st.last_full = st.frames
        else:
            full_set = set()
        full_set -= dead
        full = bool(full_set)
        self.last_mode = "full" if full else "tracked"

        # 1. tracked champions: small windows around the predicted positions
        cands: list[_Cand] = []
        bg_scores: list[float] = []
        found_local: set[int] = set()
        prev_thr = self.last_threshold
        for i, tr in self._tracks.items():
            if i in full_set or now - tr.t > TRACK_FRESH_S or tr.misses >= 2:
                continue
            lc = self._local_search(feat, bank, i, tr, now, kx, ky)
            cands.extend(lc)
            if any(c.ev >= prev_thr for c in lc):
                found_local.add(i)
        # 2. the others (lost, in the fog, local miss): whole map. Champions hidden for a
        #    while are only searched when something appeared on the map (change gate) or
        #    every LOST_EVERY frames: a champion coming out of the fog changes the pixels
        changed = self._changed(work, D_work, kx, ky, now)
        self.last_changed = changed
        glob = [i for i in range(n_e) if i not in found_local and i not in dead
                and i not in full_set]
        if not changed and st.frames % LOST_EVERY != 0:
            glob = [i for i in glob if i in self._tracks
                    and now - self._tracks[i].t <= TRACK_FRESH_S]
        self.last_searched = len(glob) + len(full_set)
        if full_set:
            gc, bg_scores = self._global_search(feat, bank, sorted(full_set))
            cands.extend(gc)
        if glob:
            cands.extend(self._coarse_search(feat, bank, glob))
        thr = self._threshold(bg_scores)
        self.last_threshold = thr

        # 3. final scores (penalties, ring colour); self threshold relaxed near its track
        relax: dict[int, float] = {}
        floor = thr - RING_WEIGHT - 0.05
        for c in cands:
            is_self = ents[c.i].relation == "self"
            self._score_cand(c, bgr, kx, ky, R_px, W, H, now,
                             floor - max(SELF_RELAX if is_self else 0.0, TRACK_RELAX))
            tr = self._tracks.get(c.i)
            if is_self:
                if tr is not None and now - tr.t <= SELF_COAST_S:
                    pu, pv = tr.predict(now)
                    if math.hypot(c.x / kx - pu, c.y / ky - pv) <= JUMP_SLACK + \
                            MAX_SPEED * (now - tr.t):
                        relax[id(c)] = SELF_RELAX
            if TRACK_RELAX > 0 and tr is not None and tr.hits >= TRACK_RELAX_HITS and \
                    0.0 <= now - tr.t <= TRACK_RELAX_S and c.ring is not None:
                pu, pv = tr.predict(now)
                own, opp = (c.f_en, c.f_al) if ents[c.i].relation == "enemy" else (c.f_al, c.f_en)
                if own >= TRACK_RELAX_OWN and opp <= TRACK_RELAX_OPP and math.hypot(
                        c.x / kx - pu, c.y / ky - pv) <= TRACK_RELAX_DIST + 0.5 * MAX_SPEED * (now - tr.t):
                    relax[id(c)] = max(relax.get(id(c), 0.0), TRACK_RELAX)
        if RING_VERIFY_MAX > 0 and not self.grey:
            self._ring_second_opinion(cands, raw_bgr, kx, ky, R_px / W, thr, relax)
        cands.sort(key=lambda c: -c.tot)

        # 4. assignment: one position per champion, no two champions on one spot
        used: set[int] = set()
        accepted: list[_Cand] = []
        infos: list[MatchInfo] = []
        rejected: dict[int, _Cand] = {}

        def info(c: _Cand, ok: bool, reason: str = "") -> None:
            e = ents[c.i]
            infos.append(MatchInfo(e.alias, e.relation, c.x / kx, c.y / ky, r_norm, c.tot,
                                   c.f_en, c.f_al, ok, reason))

        def conflict(c: _Cand, tot: float, stacked_ok: bool) -> bool:
            # a partly covered icon with a clear match AND its own team's ring (real
            # screenshots: bot duo / siege stacks, 0.6-0.75 diameter apart) is a stack
            e_c = ents[c.i]
            own_c, opp_c = (c.f_en, c.f_al) if e_c.relation == "enemy" else (c.f_al, c.f_en)
            ringed = tot >= thr + STACK_RING_MARGIN and own_c >= STACK_RING_OWN and \
                opp_c <= STACK_RING_OPP
            for a in accepted:
                dd = math.hypot(c.x - a.x, c.y - a.y) / max(D_work, 1e-6)
                if dd < MIN_SEP_FRAC:
                    return True
                if dd < STACK_FRAC and not stacked_ok and not ringed and \
                        tot < max(thr + 0.1, 0.85 * a.tot):
                    return True
            return False

        ver = self._get_verifier() if VERIFY_ZONE > 0 else None
        for c in cands:
            if c.i in used:
                continue
            t_i = thr - relax.get(id(c), 0.0)
            if c.tot < t_i and not (ver is not None and c.tot >= t_i - VERIFY_ZONE
                                    and self._verified(ver, raw_bgr, c, kx, ky, r_norm)):
                rejected.setdefault(c.i, c)
                info(c, False, "score")
                continue
            if conflict(c, c.tot, False):
                rejected.setdefault(c.i, c)
                info(c, False, "conflict")
                continue
            if ver is not None and c.tot < thr + VETO_MARGIN and not self.grey:
                tr_c = self._tracks.get(c.i)
                if tr_c is None or now - tr_c.t > VETO_FRESH_S or c.note == "jump":
                    appear = True
                else:                        # (a recall to the fountain is no "jump")
                    pu, pv = tr_c.predict(now)
                    appear = math.hypot(c.x / kx - pu, c.y / ky - pv) > \
                        JUMP_SLACK + MAX_SPEED * max(0.0, now - tr_c.t)
                if appear and self._icon_prob(ver, raw_bgr, c, kx, ky, r_norm) < VETO_MAX:
                    rejected.setdefault(c.i, c)
                    info(c, False, "veto")
                    continue
            e = ents[c.i]
            own, opp = (c.f_en, c.f_al) if e.relation == "enemy" else (c.f_al, c.f_en)
            if c.ev < STRONG_SCORE and opp > 0.3 and opp > 2.0 * own + 0.05:
                info(c, False, "ring")
                continue
            used.add(c.i)
            accepted.append(c)
            info(c, True)
            if c.ncc >= LEARN_SCORE - 0.1 and c.ev >= LEARN_SCORE and opp < 0.15 \
                    and c.ring is not None and (st.frames % 4 == 0 or
                                                self.rings.samples.get(e.relation, 0) < 12):
                self.rings.learn(e.relation, c.ring)

        # 5. occlusion: re-score the best rejected candidate of each missing champion on
        #    the visible part of its icon (stacks: the accepted icons are drawn over it)
        for i, c in sorted(rejected.items(), key=lambda kv: -kv[1].tot):
            if i in used or c.ncc < OCC_MIN_NCC:
                continue
            t_i = thr - relax.get(id(c), 0.0)
            if c.tot < t_i - OCC_RANGE:
                continue
            near = [(a.x, a.y) for a in accepted
                    if math.hypot(c.x - a.x, c.y - a.y) < 1.05 * D_work]
            # only where an occluder is likely: an accepted icon over it, white lines /
            # texts on it, or the champion was right there a moment ago (ping on it...)
            tr = self._tracks.get(i)
            tracked = tr is not None and now - tr.t <= TRACK_FRESH_S and math.hypot(
                c.x / kx - tr.u, c.y / ky - tr.v) <= LOCAL_SLACK + MAX_SPEED * (now - tr.t)
            if c.ring is None:
                self._score_cand(c, bgr, kx, ky, R_px, W, H, now, -10.0)
            own0, opp0 = (c.f_en, c.f_al) if ents[i].relation == "enemy" else (c.f_al, c.f_en)
            # ... or a clear ring of its own team's colour (an unknown occluder on it: our
            # overlay's labels / rings when it is captured, a ping)
            ringed = own0 >= TRIM_RING_OWN and opp0 <= TRIM_RING_OPP
            if not near and not tracked and not ringed:
                continue
            if own0 < 0.12 or opp0 > own0 + 0.1:
                continue                   # no ring of the champion's colour around it
            # (trimmed only when nothing else explains the miss: measured, a trimmed re-score
            # next to other icons mostly helps the wrong champion)
            trim = self.trim_rescue and ringed and not near and not tracked
            occ = self._rescue(c, feat, bank, near, D_work, caps=tracked, trim=trim)
            if occ is None or occ + (c.ev - c.ncc) <= c.ev:
                continue
            px, py = self.last_rescue_pos
            if trim and (occ < TRIM_MIN_SCORE or not self._best_identity(
                    i, occ, px, py, feat, bank, near, D_work, tracked, dead)):
                continue
            c2 = _Cand(c.i, px, py, occ, occ + (c.ev - c.ncc), c.local, margin=c.margin)
            excl = [(a.x / kx * W, a.y / ky * H) for a in accepted
                    if math.hypot(px - a.x, py - a.y) < 1.05 * D_work]
            self._score_cand(c2, bgr, kx, ky, R_px, W, H, now, -10.0, exclude=excl)
            e = ents[i]
            own, opp = (c2.f_en, c2.f_al) if e.relation == "enemy" else (c2.f_al, c2.f_en)
            if c2.tot < t_i or own < 0.15 or opp > own or conflict(c2, c2.tot, True):
                continue
            used.add(i)
            accepted.append(c2)
            info(c2, True, "occluded")

        # 5b. stacks: tracked champions drawn under an accepted icon (ring arc + portrait)
        if accepted and self.stack_search and not self.grey:
            try:
                for c in self._stack_search(bgr, feat, bank, accepted, used, dead, now, kx, ky,
                                            R_px, D_work, thr):
                    used.add(c.i)
                    accepted.append(c)
                    info(c, True, "stacked")
            except Exception:
                self._errors.exception("Roster matcher stack search failed")

        # 5c. ring proposals: an icon-like ring of one team's colour that no accepted match
        #     explains (an icon partly covered by another one, a ping, a label) is given to
        #     the best of THAT team's missing alive champions (occlusion-tolerant score)
        if self.ring_proposals and not self.grey and len(used) + len(dead) < n_e and \
                st.frames % max(1, RING_PROP_EVERY) == 0:
            try:
                self._ring_img = raw_bgr
                for c in self._ring_proposals(feat, bank, accepted, used, dead, kx, ky,
                                              R_px * fx, D_work, thr):
                    self._score_cand(c, bgr, kx, ky, R_px, W, H, now, -10.0,
                                     exclude=[(a.x / kx * W, a.y / ky * H) for a in accepted])
                    used.add(c.i)
                    accepted.append(c)
                    info(c, True, "ring")
            except Exception:
                self._errors.exception("Roster matcher ring proposals failed")

        # 5d. stack proposals: the patch verifier looks around every accepted icon for an
        #     icon partly drawn under it (bot duo, fights, sieges); each proposal is given to
        #     the best of ITS team's missing alive champions (occlusion-tolerant score)
        if STACKV_EVERY > 0 and accepted and not self.grey and len(used) + len(dead) < n_e and \
                st.frames % STACKV_EVERY == (1 % STACKV_EVERY):
            try:
                for c in self._stack_proposals(raw_bgr, feat, bank, accepted, used, dead, kx, ky,
                                               r_norm, D_work):
                    self._score_cand(c, bgr, kx, ky, R_px, W, H, now, -10.0,
                                     exclude=[(a.x / kx * W, a.y / ky * H) for a in accepted])
                    used.add(c.i)
                    accepted.append(c)
                    info(c, True, "vstack")
            except Exception:
                self._errors.exception("Roster matcher stack proposals failed")

        # 6. the local player: camera lock (confirmed by my own matches), camera rectangle
        #    prior, then coasting on its track
        dets_extra: list[Detection] = []
        cam_pt = self._cam_point(raw_bgr)
        if self._camlock[0] == st.frames:       # (a fresh camera rectangle)
            self.camlock.feed_cam(cam_pt, now)
        for c in accepted:
            if ents[c.i].relation == "self" and not c.note and c.tot >= thr + CAMLOCK_MARGIN:
                self.camlock.confirm((c.x / kx, c.y / ky), cam_pt, now)
        for i, e in enumerate(ents):
            if e.relation != "self" or i in used or i in dead:
                continue
            lp = self.camlock.position(cam_pt, now)
            if lp is not None:
                # camera locked on me: my icon is where the camera says, even hidden
                dets_extra.append(Detection(u=lp[0], v=lp[1], r=r_norm, score=CAMLOCK_SCORE,
                                            cls="ally", cls_probs=(0.03, 0.97, 0.0),
                                            alias=e.alias))
                infos.append(MatchInfo(e.alias, e.relation, lp[0], lp[1], r_norm,
                                       CAMLOCK_SCORE, 0.0, 0.0, True, "camlock"))
                tr = self._tracks.get(i)
                if tr is None:
                    self._tracks[i] = _Track(lp[0], lp[1], now, conf=CAMLOCK_SCORE, margin=0.0)
                else:
                    tr.u, tr.v, tr.t, tr.vu, tr.vv = lp[0], lp[1], now, 0.0, 0.0
                    tr.conf = max(tr.conf, CAMLOCK_SCORE)
                used.add(i)
                continue
            best = rejected.get(i)
            cam = self._camera_centre(raw_bgr) if best is not None else None
            if best is not None and cam is not None and best.ncc >= OCC_MIN_NCC and \
                    math.hypot(best.x / kx - cam[0], best.y / ky - cam[1]) < SELF_CAM_DIST \
                    and best.tot >= thr - SELF_RELAX and not conflict(best, best.tot, True):
                own = best.f_al
                if own >= 0.15 and best.f_en <= own:
                    used.add(i)
                    best.note = "camera"
                    accepted.append(best)
                    info(best, True, "camera")
                    continue
            tr = self._tracks.get(i)
            if tr is not None and now - tr.t <= SELF_COAST_S and tr.hits >= 3:
                # short extrapolation only: a hidden icon's last velocity is often wrong
                k = min(max(now - tr.t, 0.0), SELF_COAST_VEL_S)
                pu, pv = tr.u + tr.vu * k, tr.v + tr.vv * k
                if SELF_COAST_UNDER and any(
                        math.hypot(a.x / kx - pu, a.y / ky - pv) < STACK_NEAR * 2.0 * r_norm
                        for a in accepted):
                    # under another icon (my support...): no frozen position, the tracker's
                    # stacked hold follows the icon drawn over mine (measured, det_gym slow3)
                    continue
                score = float(max(0.05, tr.conf * (1.0 - 0.5 * (now - tr.t) / SELF_COAST_S)))
                dets_extra.append(Detection(u=pu, v=pv, r=r_norm, score=score, cls="ally",
                                            cls_probs=(0.03, 0.97, 0.0), alias=e.alias))
                infos.append(MatchInfo(e.alias, e.relation, pu, pv, r_norm, score, 0.0, 0.0,
                                       True, "coast"))
                continue
            me_dead = getattr(self.learner, "_me_dead", None) is True
            under = None if me_dead else self._me_under(accepted, cam_pt, kx, ky, i, now, raw_bgr, r_norm)
            if under is not None:
                # my icon is under an ally icon the camera has followed for a while (my
                # support on me, camera locked): I am there (a position, not a match)
                dets_extra.append(Detection(u=under[0], v=under[1], r=r_norm, score=UNDER_SCORE,
                                            cls="ally", cls_probs=(0.03, 0.97, 0.0), alias=e.alias))
                infos.append(MatchInfo(e.alias, e.relation, under[0], under[1], r_norm, UNDER_SCORE,
                                       0.0, 0.0, True, "under"))

        # 7. tracks
        for c in accepted:
            e = ents[c.i]
            u, v = c.x / kx, c.y / ky
            enemy = e.relation == "enemy"
            agree = (c.f_en - c.f_al) if enemy else (c.f_al - c.f_en)
            conf_v = float(min(1.0, max(0.05, 0.55 + (c.tot - thr) * 1.5 + 0.1 * agree)))
            tr = self._tracks.get(c.i)
            if tr is None:
                tr = self._tracks[c.i] = _Track(u, v, now, conf=conf_v,
                                                margin=c.margin if c.margin is not None
                                                else 0.5 * UNIQUE_CAP)
            else:
                dt = now - tr.t
                if dt >= 0.03:
                    if c.note in ("stacked", "camera", "ring"):
                        # a position inferred under another icon / from the camera: not
                        # precise enough for a velocity (coasting would run away with it)
                        tr.vu = tr.vv = 0.0
                    elif math.hypot(u - tr.u, v - tr.v) <= JUMP_SLACK + MAX_SPEED * dt:
                        a = 0.5
                        vu = (1 - a) * tr.vu + a * (u - tr.u) / dt
                        vv = (1 - a) * tr.vv + a * (v - tr.v) / dt
                        sp = math.hypot(vu, vv)
                        k = min(1.0, MAX_SPEED / sp) if sp > 0 else 1.0
                        tr.vu, tr.vv = vu * k, vv * k
                    else:
                        tr.vu = tr.vv = 0.0
                tr.u, tr.v, tr.t = u, v, now
                tr.conf = (1 - CONF_SMOOTH) * tr.conf + CONF_SMOOTH * conf_v
                if c.margin is not None:
                    tr.margin = c.margin
            tr.hits += 1
            tr.misses = 0
            tr.pend = None
        for i, tr in self._tracks.items():
            if i in used:
                continue
            tr.misses += 1
            tr.conf *= 0.8
            far = rejected.get(i)
            if far is not None and far.note == "jump" and far.tot + JUMP_PENALTY >= thr:
                fu, fv = far.x / kx, far.y / ky
                p = tr.pend
                if p is not None and now - p[4] < 1.0 and math.hypot(fu - p[0], fv - p[1]) < 0.03:
                    tr.pend = (fu, fv, p[2], p[3] + 1, now)
                else:
                    tr.pend = (fu, fv, now, 1, now)

        self.last_matches = infos
        # 8. learned icons (custom skins): bootstrap / capture / refresh (self_icon.py)
        try:
            dets_extra = self._learn(bgr, now, R_px, r_norm, accepted, thr, kx, ky, used,
                                     dets_extra)
        except Exception:
            self._errors.exception("Roster matcher icon learning failed")

        # statistics for the adaptive threshold and the re-calibration trigger
        conf = [a for a in accepted if a.tot >= thr + 0.08]
        bgl = st.bg_grey if self.grey else st.bg
        bgl.extend(bg_scores)
        del bgl[:-600]
        st.conf_hist.append(float(len(conf)))
        del st.conf_hist[:-4 * RECAL_WINDOW]
        if st.since_calib >= RECAL_WINDOW:
            st.ref_conf = max(st.ref_conf, float(np.mean(st.conf_hist[-RECAL_WINDOW:])))

        dets: list[Detection] = []
        for c in accepted:
            e = ents[c.i]
            enemy = e.relation == "enemy"
            p_en = 0.97 if enemy else 0.03
            dets.append(Detection(u=c.x / kx, v=c.y / ky, r=r_norm,
                                  score=float(getattr(self._tracks.get(c.i), "conf", 0.6)),
                                  cls="enemy" if enemy else "ally",
                                  cls_probs=(p_en, 1.0 - p_en, 0.0), alias=e.alias))
        return dets + dets_extra


__all__ = ["RosterMatcher", "RosterEntry", "RingColorModel", "MatchInfo", "ncc_maps",
           "INNER_RATIO", "SCALE_MIN", "SCALE_MAX", "DEFAULT_SCALE"]


# validated tuning overrides (assets/model/det_params.json, written by tools/det_tune.py)
from treeaicoach import det_params as _det_params  # noqa: E402

_det_params.apply("roster_matcher", globals())
