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
#: Re-calibrate when the mean number of confident matches over the recent frames drops
#: below this fraction of the value measured right after calibration.
RECAL_DROP = 0.5
RECAL_WINDOW = 24
RECAL_MIN_FRAMES = 48             # frames between two automatic re-calibrations
CALIB_FRAMES = 3                  # first frames combined for the initial calibration
REFRESH_FRAMES = 80               # period of the check for newly downloaded skin portraits
#: Calibration quality (evidence units) below which the result is ignored.
MIN_CALIB_QUALITY = 0.62
#: Champions averaged by the calibration quality, and weight of the log-normal scale prior.
CALIB_TOP_K = 3
CALIB_PRIOR_WEIGHT = 1.5

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
        norms.append(np.sqrt([sq[0], sq[1] + sq[2]]) + 1e-6)
        stds.append(float(np.sqrt(sq.sum() / (3.0 * n))) + 1e-6)
    return _Bank(size=size, mask=mask, n=n, tmpl=tmpls, norms=np.asarray(norms, np.float32),
                 stds=np.asarray(stds, np.float32))


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


def ncc_maps(feat: np.ndarray, bank: _Bank, idx: Sequence[int] | None = None
             ) -> tuple[np.ndarray, np.ndarray]:
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
    wl, wc = LIGHTNESS_WEIGHT, 1.0 - LIGHTNESS_WEIGHT
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

        def dist(protos: list[np.ndarray]) -> np.ndarray:
            return np.min(np.stack([np.sqrt((((lab_px - p) * w) ** 2).sum(axis=1))
                                    for p in protos]), axis=0)

        de = dist(self._protos("enemy"))
        da = np.minimum(dist(self._protos("ally")), dist(self._protos("self")))
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
class _State:
    scale: float | None = None          # calibrated icon diameter / minimap width
    frames: int = 0
    since_calib: int = 0
    calib: list = field(default_factory=list)       # (scale, quality) of the first frames
    conf_hist: list = field(default_factory=list)   # confident matches per frame
    ref_conf: float = 0.0
    bg: list = field(default_factory=list)          # recent background peak scores


class RosterMatcher:
    """Find the roster champions' portraits on the minimap (see module doc). Never raises."""

    name = "roster"

    def __init__(self, db: Any = None, scale_store: dict | None = None,
                 on_scale: Callable[[str, float], None] | None = None) -> None:
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
            old = [(e.alias, e.skin_id, e.relation, e.exact) for e in self._entries]
            if old == [(e.alias, e.skin_id, e.relation, e.exact) for e in ents]:
                return
            self.set_entries(ents)
            log.info("Roster matcher: %d portraits", len(ents))
        except Exception:
            self._errors.exception("Roster matcher set_roster failed")

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
        maps, std = ncc_maps(_features(work), bank)
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
    def detect(self, minimap_bgr: np.ndarray) -> list[Detection]:
        """Roster champions found on a BGR minimap; [] without roster or on error."""
        t0 = time.perf_counter()
        try:
            bgr = _as_bgr(minimap_bgr)
            if bgr is None or not self._entries:
                return []
            with self._lock:
                if self._state.frames % REFRESH_FRAMES == REFRESH_FRAMES - 1:
                    self._refresh_skins()
                return self._detect(bgr)
        except Exception:
            self._errors.exception("Roster matcher detection failed")
            return []
        finally:
            self.last_time_ms = 1000 * (time.perf_counter() - t0)

    def _refresh_skins(self) -> None:
        """Skin portraits downloaded since set_roster: rebuild (keeps the calibration)."""
        if self._game is None or all(e.exact for e in self._entries) or self.db is None:
            return
        for e in self._entries:
            if not e.exact and e.skin_id > 0:
                try:
                    path = self.db.cached_icon_path(e.alias, e.skin_id)
                    if path is not None and path.is_file():
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
            # not calibrated yet: first frames, then a retry from time to time
            need = st.frames < CALIB_FRAMES or st.frames % RECAL_MIN_FRAMES == 0
        elif len(st.calib) < CALIB_FRAMES and st.frames < 2 * CALIB_FRAMES:
            need, around = True, st.scale         # initial phase: confirm narrowly
        elif st.since_calib >= RECAL_MIN_FRAMES and len(st.conf_hist) >= RECAL_WINDOW:
            recent = float(np.mean(st.conf_hist[-RECAL_WINDOW:]))
            if recent < max(RECAL_DROP * st.ref_conf, 0.5):
                # matches became weak (or never were): the scale may be wrong
                need = True
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
        bg = self._state.bg[-400:] + current_bg
        if len(bg) < 8:
            return THR_MIN + 0.05
        thr = float(np.percentile(bg, THR_BG_PCT)) + THR_MARGIN
        return float(min(THR_MAX, max(THR_MIN, thr)))

    @staticmethod
    def _ring_pixels(bgr: np.ndarray, cx: float, cy: float, R: float) -> np.ndarray:
        """Lab pixels of the ring annulus (0.86-1.0 R) inside the image, ``[n, 3]``."""
        ang = np.linspace(0, 2 * np.pi, 40, endpoint=False, dtype=np.float32)
        rr = np.asarray([0.86, 0.93, 1.0], np.float32) * np.float32(R)
        xs = (cx - 0.5 + rr[:, None] * np.cos(ang)[None]).astype(np.float32)
        ys = (cy - 0.5 + rr[:, None] * np.sin(ang)[None]).astype(np.float32)
        H, W = bgr.shape[:2]
        ok = (xs >= 0) & (xs <= W - 1) & (ys >= 0) & (ys <= H - 1)
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

    def _detect(self, bgr: np.ndarray) -> list[Detection]:
        st = self._state
        H, W = bgr.shape[:2]
        scale = self._current_scale(bgr)
        st.frames += 1
        st.since_calib += 1
        maps, std, bank, fx, fy = self._maps(bgr, scale, WORK_INNER_PX)
        ents = self._entries
        if maps.shape[0] != len(ents) or maps.shape[1] < 2 or maps.shape[2] < 2:
            return []
        half = (bank.size - 1) / 2.0                   # map index -> template centre
        rad = max(2, int(round(0.4 * bank.size)))
        R_px = 0.5 * scale * W                          # icon radius (original px)
        r_norm = R_px / W
        D_work = scale * W * fx                         # icon diameter (working px)

        # candidates: (evidence, ncc, entry, x, y) in working px (continuous centre)
        cands: list[tuple[float, float, int, float, float]] = []
        bg_scores: list[float] = []
        for i in range(len(ents)):
            m = maps[i]
            pk = [(x, y, v) for x, y, v in self._peaks(m, rad, PEAKS_PER_CHAMP + 1, PEAK_FLOOR)
                  if CONTRAST_RANGE[0] < float(std[y, x]) / float(bank.stds[i])
                  < CONTRAST_RANGE[1]]
            for j, (x, y, v) in enumerate(pk[:PEAKS_PER_CHAMP]):
                other = max((q[2] for k, q in enumerate(pk) if k != j), default=PEAK_FLOOR)
                ev = v + UNIQUE_WEIGHT * min(v - other, UNIQUE_CAP)
                sx, sy = self._subpixel(m, x, y)
                cands.append((ev, v, i, sx + half + 0.5, sy + half + 0.5))
                if j > 0:
                    bg_scores.append(v)
        thr = self._threshold(bg_scores)
        self.last_threshold = thr

        # ring colour second opinion for every candidate that could pass
        scored: list[tuple[float, float, int, float, float, float, float, np.ndarray | None]] = []
        for ev, v, i, x, y in cands:
            if ev < thr - RING_WEIGHT - 0.05:
                scored.append((ev, ev, i, x, y, 0.0, 0.0, None))
                continue
            ring = self._ring_pixels(bgr, x / fx, y / fy, R_px)
            f_en, f_al = self.rings.classify(ring)
            own, opp = (f_en, f_al) if ents[i].relation == "enemy" else (f_al, f_en)
            tot = ev + RING_WEIGHT * (own - opp) - NO_RING_PENALTY * max(0.0, 1.0 - own / 0.3)
            scored.append((tot, ev, i, x, y, f_en, f_al, ring))
        scored.sort(key=lambda c: -c[0])

        used: set[int] = set()
        accepted: list[tuple[float, int, float, float, float, float]] = []
        infos: list[MatchInfo] = []
        for tot, ev, i, x, y, f_en, f_al, ring in scored:
            if i in used:
                continue
            e = ents[i]
            u, vv = x / fx / W, y / fy / H
            if tot < thr:
                infos.append(MatchInfo(e.alias, e.relation, u, vv, r_norm, tot, f_en, f_al,
                                       False, "score"))
                continue
            conflict = False
            for ta, _ia, xa, ya, _fe, _fa in accepted:
                dd = math.hypot(x - xa, y - ya) / max(D_work, 1e-6)
                if dd < MIN_SEP_FRAC or (dd < STACK_FRAC and tot < max(thr + 0.1, 0.85 * ta)):
                    conflict = True
                    break
            if conflict:
                infos.append(MatchInfo(e.alias, e.relation, u, vv, r_norm, tot, f_en, f_al,
                                       False, "conflict"))
                continue
            own, opp = (f_en, f_al) if e.relation == "enemy" else (f_al, f_en)
            if ev < STRONG_SCORE and opp > 0.3 and opp > 2.0 * own + 0.05:
                infos.append(MatchInfo(e.alias, e.relation, u, vv, r_norm, tot, f_en, f_al,
                                       False, "ring"))
                continue
            used.add(i)
            accepted.append((tot, i, x, y, f_en, f_al))
            infos.append(MatchInfo(e.alias, e.relation, u, vv, r_norm, tot, f_en, f_al, True))
            if ev >= LEARN_SCORE and opp < 0.15 and ring is not None:
                self.rings.learn(e.relation, ring)

        # statistics for the adaptive threshold and the re-calibration trigger
        conf = [a[0] for a in accepted if a[0] >= thr + 0.08]
        st.bg.extend(bg_scores)
        del st.bg[:-600]
        st.conf_hist.append(float(len(conf)))
        del st.conf_hist[:-4 * RECAL_WINDOW]
        if st.since_calib >= RECAL_WINDOW:
            st.ref_conf = max(st.ref_conf, float(np.mean(st.conf_hist[-RECAL_WINDOW:])))

        self.last_matches = infos
        dets: list[Detection] = []
        for ev, i, x, y, f_en, f_al in accepted:
            e = ents[i]
            enemy = e.relation == "enemy"
            agree = (f_en - f_al) if enemy else (f_al - f_en)
            conf_v = float(min(1.0, max(0.05, 0.55 + (ev - thr) * 1.5 + 0.1 * agree)))
            p_en = 0.97 if enemy else 0.03
            dets.append(Detection(u=x / fx / W, v=y / fy / H, r=r_norm, score=conf_v,
                                  cls="enemy" if enemy else "ally",
                                  cls_probs=(p_en, 1.0 - p_en, 0.0), alias=e.alias))
        return dets


__all__ = ["RosterMatcher", "RosterEntry", "RingColorModel", "MatchInfo", "ncc_maps",
           "INNER_RATIO", "SCALE_MIN", "SCALE_MAX", "DEFAULT_SCALE"]
