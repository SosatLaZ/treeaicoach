"""Learned minimap icons: who is who on the minimap, even with custom skins (skin mods).

The roster matcher (:mod:`treeaicoach.roster_matcher`) finds the 10 champions by their
OFFICIAL portraits. A client-side skin mod draws something else on the minimap (any image):
the local player is then never matched and the coach does not know where "I" am.
:class:`IconLearner` (owned by the matcher, fed every frame) fixes that without any template:

1. **Bootstrap.** Ring-coloured icons that no accepted roster match explains are found with
   the live ring colours (:func:`ring_candidates`: annulus of ally / self / enemy colour at the
   calibrated icon radius, structures and fountains excluded) and followed over time
   (:class:`_UTrack`, speed-bounded continuity). Each track keeps the *intersection* of the
   roster entries of its side that were not matched (nor dead) on every frame it was seen:
   elimination with consistency. Me: my icon is always on my minimap while I am alive, so an
   unexplained ally-side icon while I am unmatched is a strong hint; the camera rectangle
   (camera locked: I am at ~64 % of its height), my teal outline and the continuity decide
   between several. A confident self track is reported as my position at once.
2. **Capture.** The icon of a bound track (inner disc + a margin, ``LEARN_PX`` square, centre
   refined by alignment) is collected over frames; the per-pixel median of the aligned crops is
   registered as that entry's template in the matcher (:meth:`RosterMatcher.register_icon`).
   Afterwards confident isolated matches refresh it (EMA, re-registered from time to time).
3. **Persistence.** My learned icon is saved in ``<cache>/learned_icons/<Alias>_<skin>.png``
   and used as my template from the start of the next game with the same champion + skin.
4. **Unlearning.** A learned template that stops matching (me alive and unmatched for
   ``REVERT_S``, another champion never matched for ``REVERT_OTHER_S``) is dropped (back to the
   official portrait) and the bootstrap starts again; a newly bound track replaces it.
5. **Official skin guess** (:class:`SkinGuesser`, optional): the HUD portrait (bottom centre,
   :mod:`treeaicoach.hud_reader`) is compared to the minimap circle icons of my champion's
   skins (downloaded lazily from CommunityDragon); a clear winner is tried as my template
   while the learning goes on (the learned icon always wins when the guess does not match).

:meth:`IconLearner.observed_lane` gives the lane where I was seen (1:30-10:00), for
:mod:`treeaicoach.roles`. Pure numpy / OpenCV; nothing raises from the public methods.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

import cv2
import numpy as np

log = logging.getLogger(__name__)

# --- geometry (same conventions as roster_matcher) --------------------------------------
PORTRAIT_RATIO = 0.81
PORTRAIT_FILL = 0.94
INNER_RATIO = 0.70
#: Learned icon image side (px) and crop half side / icon radius: the crop is the square
#: "portrait image" of the matcher's template geometry (portrait disc = PORTRAIT_FILL of it).
LEARN_PX = 48
CROP_HALF = PORTRAIT_RATIO / PORTRAIT_FILL
_RIN = INNER_RATIO / CROP_HALF * LEARN_PX / 2.0          # matched disc radius in the crop

# --- ring candidates ---------------------------------------------------------------------
RING_WORK_D = 18.0          # icon diameter at the working resolution (px)
RING_MIN = 0.42             # annulus fraction of the side colour for a candidate
RING_RATIO = 2.0            # ... and this many times the other side's fraction
INTERIOR_MAX = 0.4          # interior of the same colour: inside a coloured area, not an icon
RELAX_DIST = 0.06           # ... except this close to where my icon is expected
MAX_LOOSE = 4               # filled-interior candidates verified per search (cost bound)
RING_LINE_DL = 15.0         # ... or with a ring this much lighter than the dark line inside it
OUTSIDE_MAX = 0.4           # same outside the ring (a thin ring, not the edge of a blue area)
_NEAR = 26.0
MIN_CHROMA = 16.0           # ring pixels are saturated (terrain, fog, texts are not)
_W = np.asarray([0.35, 1.0, 1.0], np.float32)
STRUCT_DIST = 0.035
FOUNTAIN_DIST = 0.09
_FOUNTAINS = ((0.045, 0.955), (0.955, 0.045))
EXPLAINED_FRAC = 0.7        # candidate this close (x icon diameter) to an accepted match: explained
ISOLATED_FRAC = 1.0         # crops only of icons this far (x diameter) from any other icon

# --- tracks / binding ----------------------------------------------------------------------
MAX_SPEED = 0.09            # normalized units / s (roster_matcher.MAX_SPEED)
JUMP_SLACK = 0.035
TRACK_LOST_S = 2.0
SELF_WEAK_MARGIN = 0.05     # an accepted self match this close to the threshold does not count
SELF_HINT_HITS = 3          # frames before a self track is reported as my position
BIND_HITS_SELF = 10
BIND_HITS_OTHER = 10
BIND_MIN_S_SELF = 1.5
BIND_MIN_S_OTHER = 4.0
MIN_CROPS = 8
MAX_CROPS = 30
CONSIST_MIN = 0.75          # mean NCC of the aligned crops to their median
MOVED_MIN_ALONE = 0.06      # elimination alone: the icon must have moved (not a still glyph)
CAM_DIST = 0.05             # camera-locked point -> my icon
CAM_FRAC = 0.6
SELF_RING_FRAC = 0.5
CAM_V_FRAC = 0.64           # my icon height in the camera rectangle when the camera is locked
ALIGN_SHIFT = 3             # +- px (crop pixels) of the alignment search

# --- refresh / unlearning ---------------------------------------------------------------
EMA_RATE = 0.08
EMA_MARGIN = 0.05
REREGISTER_EVERY = 30       # EMA updates between two re-registrations
REREGISTER_MIN_S = 10.0
SAVE_MIN_S = 60.0
REVERT_S = 25.0             # me alive, my learned icon unmatched this long: back to the official one
REVERT_OTHER_S = 120.0
ELIGIBLE_AFTER_S = 20.0     # another champion matched this recently is not re-learned
OK_MARGIN_OTHER = 0.2       # ... matched this well (a weak match may be a false one)
OTHERS_EVERY = 8            # frames between two searches for the other unmatched champions
OTHERS_PENDING_EVERY = 3    # ... while an unexplained icon is about to be bound
WANT_SELF_S = 2.0           # self unmatched this long -> the bootstrap runs every frame
SKIN_GUESS_AFTER_S = 6.0
CENTRED_SKIP_MARGIN = 0.15  # a learned icon matched this well is not re-checked
CENTRED_EVERY = 3
OFF_CENTRE_UNLEARN = 3      # consecutive off-centre checks of a learned icon: unlearned

LANE_START_GT, LANE_END_GT = 90.0, 600.0


@dataclass
class RingCand:
    """An icon-like ring of one side's colour (normalized centre)."""

    u: float
    v: float
    side: str                 # "ally" (ally or self colour) / "enemy"
    frac: float               # annulus fraction of the side colour
    frac_self: float = 0.0    # annulus fraction of the self (teal) colour


@dataclass
class _UTrack:
    """An unexplained icon followed over frames."""

    tid: int
    u: float
    v: float
    side: str
    t0: float
    t: float
    u0: float
    v0: float
    hits: int = 0
    cam_hits: int = 0
    self_ring: int = 0
    moved: float = 0.0
    inter: set | None = None            # entry indices still possible
    crops: list = field(default_factory=list)
    dead: bool = False                  # empty intersection: not a roster champion

    def cam_frac(self) -> float:
        return self.cam_hits / max(1, self.hits)

    def self_frac(self) -> float:
        return self.self_ring / max(1, self.hits)


@dataclass
class _Learned:
    """A template learned (or guessed) for one roster entry."""

    alias: str
    icon: np.ndarray                    # float32 [LEARN_PX, LEARN_PX, 3] BGR (EMA)
    t: float                            # registration time
    last_ok: float                      # last accepted match
    source: str                         # "learned" / "cached" / "skin"
    updates: int = 0
    last_reg: float = 0.0
    last_save: float = -1e9


@dataclass
class StepOut:
    """What the matcher must do after :meth:`IconLearner.step`."""

    #: entry index -> (source "learned" / "refresh" / "skin", skin number or None, icon)
    register: dict = field(default_factory=dict)
    revert: list = field(default_factory=list)      # entry indices back to the official portrait
    self_pos: tuple | None = None                   # (u, v, score) bootstrap position of me


# ======================================================================================
# Image helpers
# ======================================================================================


def _disc(size: int, r: float) -> np.ndarray:
    c = (size - 1) / 2.0
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    return np.clip(r + 0.5 - np.sqrt((xx - c) ** 2 + (yy - c) ** 2), 0.0, 1.0).astype(np.float32)


_MASK = _disc(LEARN_PX, _RIN)


def crop_icon(bgr: np.ndarray, cx: float, cy: float, R_px: float) -> np.ndarray | None:
    """Square crop (float32 ``LEARN_PX``) of the icon centred at ``(cx, cy)`` (continuous
    original px) with ring radius ``R_px``, in the matcher's portrait-image geometry."""
    if R_px < 2.0:
        return None
    k = LEARN_PX / (2.0 * CROP_HALF * R_px)
    c = (LEARN_PX - 1) / 2.0
    M = np.float32([[k, 0, c - k * (cx - 0.5)], [0, k, c - k * (cy - 0.5)]])
    return cv2.warpAffine(bgr, M, (LEARN_PX, LEARN_PX), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE).astype(np.float32)


def _lab(img: np.ndarray) -> np.ndarray:
    u8 = np.clip(img, 0, 255).astype(np.uint8)
    lab = cv2.cvtColor(u8, cv2.COLOR_BGR2LAB).astype(np.float32)
    lab[:, :, 1:] -= 128.0
    return lab


def icon_ncc(a: np.ndarray, b: np.ndarray, mask: np.ndarray = _MASK) -> float:
    """Masked NCC (Lab, lightness and chroma averaged) of two ``LEARN_PX`` crops."""
    la, lb = _lab(a), _lab(b)
    m = mask[:, :, None]
    n = float(mask.sum()) + 1e-6
    za = (la - (la * m).sum(axis=(0, 1)) / n) * m
    zb = (lb - (lb * m).sum(axis=(0, 1)) / n) * m
    num = (za * zb).sum(axis=(0, 1))
    sa, sb = (za * za).sum(axis=(0, 1)), (zb * zb).sum(axis=(0, 1))
    nl = num[0] / math.sqrt(sa[0] * sb[0] + 1e-6 + 40.0 * n)
    nc = (num[1] + num[2]) / math.sqrt((sa[1] + sa[2]) * (sb[1] + sb[2]) + 1e-6 + 40.0 * n)
    return float(0.5 * nl + 0.5 * nc)


def align_to(crop: np.ndarray, ref: np.ndarray, shift: int = ALIGN_SHIFT
             ) -> tuple[np.ndarray, float]:
    """``crop`` translated (integer px, +-``shift``) to best match ``ref`` -> (aligned, NCC)."""
    h = int(_RIN * 0.72)                                 # inscribed square of the disc
    c = LEARN_PX // 2
    t = np.ascontiguousarray(_lab(ref)[c - h:c + h, c - h:c + h])
    s = np.ascontiguousarray(_lab(crop)[c - h - shift:c + h + shift, c - h - shift:c + h + shift])
    try:
        res = cv2.matchTemplate(s, t, cv2.TM_CCOEFF_NORMED)
        _, best, _, (bx, by) = cv2.minMaxLoc(res)
    except cv2.error:
        return crop, icon_ncc(crop, ref)
    dx, dy = bx - shift, by - shift
    if dx or dy:
        M = np.float32([[1, 0, -dx], [0, 1, -dy]])
        crop = cv2.warpAffine(crop, M, (LEARN_PX, LEARN_PX), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_REPLICATE)
    return crop, icon_ncc(crop, ref)


def median_icon(crops: Sequence[np.ndarray]) -> tuple[np.ndarray, float]:
    """Aligned per-pixel median of ``crops`` and their mean NCC to it (consistency)."""
    ref = crops[len(crops) // 2]
    for _ in range(2):
        al = [align_to(c, ref)[0] for c in crops]
        ref = np.median(np.stack(al), axis=0).astype(np.float32)
    scores = [icon_ncc(a, ref) for a in al]
    return ref, float(np.mean(scores))


# ======================================================================================
# Ring candidates
# ======================================================================================


def _protos(rings: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Lab prototypes (enemy, ally, self) for the whole-image search: the live centroids and
    the seeds only (the pale extra colours of the ring check would match the terrain)."""
    try:
        cen, seed = rings.centroid, rings.seed
        return tuple(np.asarray([cen[k], seed[k]], np.float32)  # type: ignore[return-value]
                     for k in ("enemy", "ally", "self"))
    except Exception:
        from treeaicoach.roster_matcher import RingColorModel

        return _protos(RingColorModel())


_LUT_BITS = 5
LUT_REBUILD_S = 5.0
_LUT_CACHE: dict = {}


def _lut_index(bgr: np.ndarray) -> np.ndarray:
    q = (bgr >> (8 - _LUT_BITS)).astype(np.int32)
    return (q[:, :, 0] << (2 * _LUT_BITS)) | (q[:, :, 1] << _LUT_BITS) | q[:, :, 2]


def _label_lut(rings: Any) -> np.ndarray:
    """Colour class of every quantized BGR colour (bit 1 enemy, 2 ally side, 4 self teal) for
    the current ring colours (cached; the centroids drift slowly)."""
    pe, pa, ps = _protos(rings)
    key = tuple(np.round(np.concatenate([pe, pa, ps]).ravel() / 6.0).astype(int).tolist())
    lut = _LUT_CACHE.get(key)
    if lut is not None:
        return lut
    last = _LUT_CACHE.get("last")
    if last is not None and time.monotonic() - last[0] < LUT_REBUILD_S:
        return last[1]                      # the colours drift slowly: rebuild at most every few s
    n = 1 << _LUT_BITS
    half = 1 << (7 - _LUT_BITS)
    g = (np.arange(n, dtype=np.int32) << (8 - _LUT_BITS)) + half
    b_, g_, r_ = np.meshgrid(g, g, g, indexing="ij")
    cols = np.stack([b_, g_, r_], axis=-1).reshape(-1, 1, 3).astype(np.uint8)
    lab = cv2.cvtColor(cols, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    chroma_ok = np.hypot(lab[:, 1] - 128.0, lab[:, 2] - 128.0) >= MIN_CHROMA
    lab *= _W

    def dist(P: np.ndarray) -> np.ndarray:
        P = P * _W
        d2 = (lab * lab).sum(axis=1)[:, None] - 2.0 * (lab @ P.T) + (P * P).sum(axis=1)[None]
        return np.sqrt(np.maximum(d2, 0.0)).min(axis=1)

    de, da, ds = dist(pe), dist(pa), dist(ps)
    dal = np.minimum(da, ds)
    E = chroma_ok & (de < _NEAR) & (de < 0.8 * dal)
    A = chroma_ok & (dal < _NEAR) & (dal < 0.8 * de)
    S = chroma_ok & (ds < _NEAR) & (ds < 0.8 * da) & (ds < 0.8 * de)
    lut = (E * 1 + A * 2 + S * 4).astype(np.uint8)
    if len(_LUT_CACHE) > 16:
        _LUT_CACHE.clear()
    _LUT_CACHE[key] = lut
    _LUT_CACHE["last"] = (time.monotonic(), lut)
    return lut


def _circle_pts(radii: Sequence[float], n: int) -> np.ndarray:
    """Sample points (units of the icon radius) on circles of ``radii``, ``n`` angles each."""
    ang = np.linspace(0, 2 * np.pi, n, endpoint=False) + 0.1
    return np.concatenate([np.stack([r * np.cos(ang), r * np.sin(ang)], 1) for r in radii])


_RING_PTS = _circle_pts((0.91,), 24)
_CORE_PTS = np.concatenate([[[0.0, 0.0]], _circle_pts((0.3,), 6), _circle_pts((0.55,), 10)])
_OUT_PTS = _circle_pts((1.22, 1.34), 16)


def _ring_mean(m: np.ndarray, Rw: float, pts: np.ndarray) -> np.ndarray:  # noqa: D401
    """Mean of ``m`` (H x W x C) over the points ``pts * Rw`` around every pixel (zero
    outside the image): a cheap sampled annulus / disc filter."""
    h, w = m.shape[:2]
    off = sorted({(int(round(x * Rw)), int(round(y * Rw))) for x, y in pts})
    p = max(max(abs(a), abs(b)) for a, b in off)
    pad = cv2.copyMakeBorder(m, p, p, p, p, cv2.BORDER_CONSTANT, value=0)
    acc = np.zeros_like(m)
    for dx, dy in off:
        acc += pad[p + dy:p + dy + h, p + dx:p + dx + w]
    return acc / float(len(off))


_ANG48 = np.linspace(0, 2 * np.pi, 48, endpoint=False)


_SHIFTS = {k: np.asarray([(0, 0)] + [(dx, dy) for dy in range(-k, k + 1)
                                      for dx in range(-k, k + 1) if dx or dy], np.float32)
           for k in (2, 4)}
_RADII = np.asarray([0.93, 1.0, 0.75, 0.84], np.float32)


def _best_contrast(L: np.ndarray, x: float, y: float, Rw: float, span: int = 2,
                   reject: bool = True) -> tuple[float, int, int]:
    """Best (ring lightness - dark line inside it) over +-``span`` px around ``(x, y)``:
    ``(contrast, dx, dy)``; one vectorized remap. ``reject``: a flat centre stops early."""
    sh = _SHIFTS[span][:1]                                # the centre first (cheap reject)
    for _ in range(2):
        cx = x + sh[:, 0][:, None, None]
        cy = y + sh[:, 1][:, None, None]
        r = (_RADII * Rw)[None, :, None]
        xs = (cx + r * np.cos(_ANG48)[None, None]).astype(np.float32).reshape(1, -1)
        ys = (cy + r * np.sin(_ANG48)[None, None]).astype(np.float32).reshape(1, -1)
        vals = cv2.remap(L, xs, ys, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        med = np.median(vals.reshape(len(sh), 4, -1), axis=2)
        c = np.maximum(med[:, 0], med[:, 1]) - np.minimum(med[:, 2], med[:, 3])
        if reject and len(sh) == 1 and float(c[0]) < 0.4 * RING_LINE_DL:
            return float(c[0]), 0, 0                       # a flat area: no icon drawing
        if len(sh) > 1:
            break
        sh = _SHIFTS[span]
    k = int(np.argmax(c))
    return float(c[k]), int(sh[k, 0]), int(sh[k, 1])


def ring_candidates(bgr: np.ndarray, R_px: float, rings: Any = None,
                    exclude: Sequence[tuple[float, float]] = (),
                    fountains: bool = True,
                    relax: Sequence[tuple[float, float]] = ()) -> list[RingCand]:
    """Icon-like rings of the ally-side / enemy colours at radius ``R_px`` (original px).

    ``exclude``: normalized points (round structures) around which nothing is reported;
    ``relax``: normalized points where my icon is expected (camera lock, last position): a
    blue / cyan portrait is accepted there.
    """
    H, W = bgr.shape[:2]
    if R_px < 2.0 or min(H, W) < 16:
        return []
    f = min(1.0, RING_WORK_D / (2.0 * R_px))
    work = bgr if f >= 0.999 else cv2.resize(bgr, (max(8, int(round(W * f))),
                                                   max(8, int(round(H * f)))),
                                             interpolation=cv2.INTER_AREA)
    h, w = work.shape[:2]
    Rw = R_px * w / float(W)
    lbl = _label_lut(rings)[_lut_index(work)]                      # bit 1 E, 2 A, 4 S
    m3 = np.empty((h, w, 3), np.float32)
    m3[:, :, 0] = (lbl & 2) > 0                                       # ally side (A)
    m3[:, :, 1] = (lbl & 1) > 0                                       # enemy (E)
    m3[:, :, 2] = (lbl & 4) > 0                                       # self teal (S)
    out: list[RingCand] = []
    fr = _ring_mean(m3, Rw, _RING_PTS)
    fa, fe, fs = fr[:, :, 0], fr[:, :, 1], fr[:, :, 2]
    # interior / outside only at the peaks; outside, my teal glow is expected around my
    # icon (only the plain ally colour counts there)
    plain = m3[:, :, 0] - m3[:, :, 2]
    core = sorted({(int(round(x * Rw)), int(round(y * Rw))) for x, y in _CORE_PTS})
    outer = sorted({(int(round(x * Rw)), int(round(y * Rw))) for x, y in _OUT_PTS})

    def around(plane: np.ndarray, x: int, y: int, offs: list) -> float:
        xs_ = np.asarray([x + a for a, _ in offs])
        ys_ = np.asarray([y + b for _, b in offs])
        ins = (xs_ >= 0) & (xs_ < w) & (ys_ >= 0) & (ys_ < h)
        return float(plane[ys_[ins], xs_[ins]].sum()) / len(offs)

    rad = max(2, int(round(0.9 * Rw)))
    K = np.ones((2 * rad + 1, 2 * rad + 1), np.uint8)
    Lw: np.ndarray | None = None
    n_loose = 0
    for side, fm, fo, pin, pout in (("ally", fa, fe, m3[:, :, 0], plain),
                                    ("enemy", fe, fa, m3[:, :, 1], m3[:, :, 1])):
        ok = (fm >= RING_MIN) & (fm >= RING_RATIO * fo + 0.05)
        if not ok.any():
            continue
        peaks = ok & (fm >= cv2.dilate(fm, K))
        ys, xs = np.nonzero(peaks)
        order = np.argsort(-fm[ys, xs])
        taken: list[tuple[float, float, float]] = []
        for j in order[:24]:
            x, y = int(xs[j]), int(ys[j])
            if any((x - a) ** 2 + (y - b) ** 2 < rr * rr for a, b, rr in taken):
                continue
            if around(pout, x, y, outer) > OUTSIDE_MAX:
                continue                # the edge of an area of that colour
            # a filled interior is an area of that colour (river, base), not an icon; where
            # my icon is expected (``relax``) my portrait may be blue / cyan itself (custom
            # skin) inside my teal glow: accepted there
            # elsewhere too when the ring is brighter than the dark line inside it (icon
            # drawing), which a coloured area does not have
            loose = around(pin, x, y, core) > INTERIOR_MAX
            if loose:
                uu, vv = (x + 0.5) / w, (y + 0.5) / h
                relaxed = any(math.hypot(uu - a, vv - b) < RELAX_DIST for a, b in relax)
                if not relaxed:
                    n_loose += 1
                    if n_loose > MAX_LOOSE:
                        continue
                # the colour does not centre such an icon (ring, glow and portrait alike):
                # the dark line inside the ring does (best contrast over +-2 px)
                if Lw is None:
                    Lw = cv2.cvtColor(work, cv2.COLOR_BGR2LAB)[:, :, 0].astype(np.float32)
                best = _best_contrast(Lw, x, y, Rw, 4 if relaxed else 2, not relaxed)
                if best[0] < RING_LINE_DL and not relaxed:
                    continue
                if any((x + best[1] - a) ** 2 + (y + best[2] - b) ** 2 < rr * rr
                       for a, b, rr in taken):
                    continue
            taken.append((x, y, 1.8 * rad if loose else rad))
            sx, sy = float(x), float(y)
            if loose:
                sx, sy = float(x + best[1]), float(y + best[2])
            elif 0 < x < w - 1:
                a_, b_, c_ = fm[y, x - 1], fm[y, x], fm[y, x + 1]
                den = a_ - 2 * b_ + c_
                if den < 0:
                    sx += float(np.clip(0.5 * (a_ - c_) / den, -0.5, 0.5))
            if not loose and 0 < y < h - 1:
                a_, b_, c_ = fm[y - 1, x], fm[y, x], fm[y + 1, x]
                den = a_ - 2 * b_ + c_
                if den < 0:
                    sy += float(np.clip(0.5 * (a_ - c_) / den, -0.5, 0.5))
            u, v = (sx + 0.5) / w, (sy + 0.5) / h
            f_self = float(fs[y, x]) if side == "ally" else 0.0
            # structure glyphs have team-coloured parts, never my teal outline
            if f_self < 0.3 and any(math.hypot(u - eu, v - ev) < STRUCT_DIST
                                    for eu, ev in exclude):
                continue
            if fountains and any(math.hypot(u - eu, v - ev) < FOUNTAIN_DIST
                                 for eu, ev in _FOUNTAINS):
                continue
            out.append(RingCand(u, v, side, float(fm[y, x]), f_self))
    return out


def _structure_points() -> list[tuple[float, float]]:
    try:
        from treeaicoach.render import iter_structures

        # round glyphs only (inhibitors, nexus): turrets do not look like a ring, and
        # champions often stand next to them
        return [(float(u), float(v)) for _, u, v, k, _t in iter_structures() if k != "turret"]
    except Exception:
        return []


# ======================================================================================
# Persistence
# ======================================================================================


def _default_cache_dir() -> Path | None:
    try:
        from treeaicoach.paths import cache_dir

        return Path(cache_dir()) / "learned_icons"
    except Exception:
        return None


# ======================================================================================
# Learner
# ======================================================================================


class IconLearner:
    """Bootstrap / capture / refresh of the minimap icons of unmatched roster champions.

    Driven by :class:`~treeaicoach.roster_matcher.RosterMatcher` (see the module doc).
    ``cache_dir``: folder of the persisted icon of the local player (True: the default
    ``<cache>/learned_icons``; None / False: no persistence).
    """

    def __init__(self, cache_dir: Path | str | bool | None = None) -> None:
        self._cache_arg = cache_dir
        self._lock = threading.RLock()
        self._structs: list[tuple[float, float]] | None = None
        self.skin_guesser: SkinGuesser | None = None
        self.reset()

    # ------------------------------------------------------------------ state
    def reset(self) -> None:
        with getattr(self, "_lock", threading.RLock()):
            self._tracks: list[_UTrack] = []
            self._next_tid = 1
            self.learned: dict[int, _Learned] = {}
            self._last_ok: dict[int, float] = {}
            self._first_t: float | None = None
            self._self_seen: tuple[float, float, float] | None = None    # (u, v, t)
            self._dead: set[str] = set()
            self._me_dead: bool | None = None
            self._hud_portrait: np.ndarray | None = None
            self._roster_key: tuple | None = None
            self._frames = 0
            self._gt: float | None = None
            self._lane_obs: Any = None
            self._lane_t: float | None = None
            self._skin_tried: set[int] = set()
            self._off_centre = 0
            self.last_ms = 0.0
            self.events: list[str] = []          # diagnostics (last ones)

    def _cache_dir(self) -> Path | None:
        if isinstance(self._cache_arg, (str, Path)):
            return Path(self._cache_arg)
        return _default_cache_dir() if self._cache_arg is True else None

    def _event(self, msg: str) -> None:
        log.info("Icon learner: %s", msg)
        self.events.append(msg)
        del self.events[:-20]

    # ------------------------------------------------------------------ inputs
    def on_roster(self, entries: Sequence[Any]) -> None:
        """New roster (called by the matcher): a new game forgets everything."""
        key = tuple((getattr(e, "alias", ""), getattr(e, "relation", "")) for e in entries)
        with self._lock:
            if key != self._roster_key:
                self.reset()
                self._roster_key = key

    def set_status(self, dead_aliases: Sequence[str] = (), me_dead: bool | None = None,
                   game_time: float | None = None) -> None:
        """Live API / HUD state: dead champions (no icon on the map), my HUD dead flag."""
        with self._lock:
            self._dead = {str(a) for a in dead_aliases if a}
            self._me_dead = me_dead
            if game_time is not None and math.isfinite(float(game_time)):
                self._gt = float(game_time)

    def feed_hud(self, portrait_bgr: np.ndarray | None, dead: bool | None = None) -> None:
        """HUD portrait crop (``hud_reader.HudRead.portrait``) and its dead flag."""
        with self._lock:
            if isinstance(portrait_bgr, np.ndarray) and portrait_bgr.ndim == 3:
                self._hud_portrait = portrait_bgr
            if dead is not None:
                self._me_dead = bool(dead)

    def load_cached(self, alias: str, skin_id: int) -> np.ndarray | None:
        """Persisted learned icon of ``alias`` / ``skin_id`` (BGR uint8) or None."""
        try:
            d = self._cache_dir()
            if d is None or not alias:
                return None
            p = d / f"{alias}_{max(0, int(skin_id))}.png"
            if not p.is_file():
                return None
            img = cv2.imread(str(p), cv2.IMREAD_COLOR)
            if img is None or img.shape[:2] != (LEARN_PX, LEARN_PX):
                return None
            return img
        except Exception:
            log.debug("Cannot read a learned icon", exc_info=True)
            return None

    def _save(self, alias: str, skin_id: int, icon: np.ndarray) -> None:
        try:
            d = self._cache_dir()
            if d is None:
                return
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"{alias}_{max(0, int(skin_id))}.png"
            tmp = p.with_suffix(".tmp.png")
            if cv2.imwrite(str(tmp), np.clip(icon, 0, 255).astype(np.uint8)):
                tmp.replace(p)
        except Exception:
            log.debug("Cannot save a learned icon", exc_info=True)

    def adopt(self, i: int, alias: str, icon: np.ndarray, now: float, source: str) -> None:
        """Entry ``i`` uses ``icon`` (cached / guessed) from now on: track it like a learned one."""
        with self._lock:
            ic = icon.astype(np.float32)
            if ic.shape[:2] != (LEARN_PX, LEARN_PX):
                ic = cv2.resize(ic, (LEARN_PX, LEARN_PX), interpolation=cv2.INTER_AREA)
            self.learned[i] = _Learned(alias, ic[:, :, :3].copy(), now, now, source, last_reg=now)

    # ------------------------------------------------------------------ outputs
    def self_position(self, now: float, max_age: float = 1.0) -> tuple[float, float] | None:
        """Last known position of my icon (matched or bootstrapped), or None."""
        with self._lock:
            s = self._self_seen
            if s is None or now - s[2] > max_age or now < s[2] - 1.0:
                return None
            return s[0], s[1]

    def observe_lane(self, now: float, game_time: float | None) -> None:
        """Accumulate my lane occupancy (call at a few Hz with the game time)."""
        try:
            from treeaicoach.roles import _Obs, _zone_class
        except Exception:
            return
        with self._lock:
            dt = 0.0 if self._lane_t is None else min(max(0.0, now - self._lane_t), 1.0)
            self._lane_t = now
            if game_time is None or not LANE_START_GT <= game_time <= LANE_END_GT or dt <= 0:
                return
            pos = self.self_position(now)
            if pos is None:
                return
            cls = _zone_class(pos[0], pos[1])
            if cls is None:
                return
            ob = self._lane_obs
            if ob is None:
                ob = self._lane_obs = _Obs()
            decay = 0.5 ** (dt / 150.0)
            for k in ob.dec:
                ob.dec[k] *= decay
            ob.raw[cls] = ob.raw.get(cls, 0.0) + dt
            ob.dec[cls] = ob.dec.get(cls, 0.0) + dt
            cand = ob.candidate(game_time)
            if cand is not None and cand != ob.lane:
                if ob.cand != cand or ob.cand_since is None:
                    ob.cand, ob.cand_since = cand, game_time
                elif game_time - ob.cand_since >= 8.0:
                    ob.lane, ob.cand, ob.cand_since = cand, None, None
            elif cand is None or cand == ob.lane:
                ob.cand, ob.cand_since = None, None

    def observed_lane(self) -> str | None:
        """Lane ("top" / "mid" / "bot") where my icon was seen laning, or None."""
        with self._lock:
            ob = self._lane_obs
            return ob.lane if ob is not None else None

    # ------------------------------------------------------------------ per frame
    def step(self, bgr: np.ndarray, now: float, R_px: float, entries: Sequence[Any],
             accepted: dict[int, tuple[float, float, float]], rings: Any = None,
             cam: tuple[float, float] | None = None, db: Any = None) -> StepOut:
        """One frame. ``accepted``: entry index -> (u, v, margin over the threshold) of this
        frame's matches; ``cam``: camera-locked point (where I would be). Never raises."""
        t0 = time.perf_counter()
        out = StepOut()
        try:
            with self._lock:
                self._step(bgr, now, R_px, entries, accepted, rings, cam, db, out)
        except Exception:
            log.exception("Icon learner step failed")
        finally:
            self.last_ms = 1000 * (time.perf_counter() - t0)
        return out

    def _step(self, bgr: np.ndarray, now: float, R_px: float, entries: Sequence[Any],
              accepted: dict, rings: Any, cam: tuple[float, float] | None, db: Any,
              out: StepOut) -> None:
        self._frames += 1
        if self._first_t is None or now < self._first_t:
            self._first_t = now
        H, W = bgr.shape[:2]
        D = 2.0 * R_px / W                                   # icon diameter (normalized)
        me = next((i for i, e in enumerate(entries) if getattr(e, "relation", "") == "self"), None)
        # --- my position (strong match) -----------------------------------------------
        me_ok = me is not None and me in accepted and accepted[me][2] >= SELF_WEAK_MARGIN
        fix: RingCand | None = None
        if me is not None and me in accepted:
            # (a confident match is only re-checked every CENTRED_EVERY frames)
            if accepted[me][2] < CENTRED_SKIP_MARGIN or self._frames % CENTRED_EVERY == 0 or \
                    self._off_centre:
                ok, near = self._centred(bgr, accepted[me], R_px, rings)
                self._off_centre = 0 if ok else self._off_centre + 1
            else:
                ok, near = True, None
            lr = self.learned.get(me)
            if not ok and lr is not None and self._off_centre >= OFF_CENTRE_UNLEARN:
                # my learned icon keeps matching beside my ring: it was learned badly
                out.revert.append(me)
                del self.learned[me]
                self._event(f"{lr.source} icon of {lr.alias} unlearned (off-centre)")
            if not ok:
                # my portrait matched NEXT to a ring (lookalike custom skin, occlusion): my
                # icon is not explained (learn it); a teal ring right there is me
                me_ok = False
                accepted = {i: a for i, a in accepted.items() if i != me}
                if near is not None and near.frac_self >= 0.3:
                    fix = near
        for i, (u, v, m) in accepted.items():
            if i < len(entries) and m >= (SELF_WEAK_MARGIN if i == me else OK_MARGIN_OTHER):
                self._last_ok[i] = now
                lr = self.learned.get(i)
                if lr is not None:
                    lr.last_ok = now
        if me is not None and me in accepted and not me_ok:
            # a weak match of my official portrait explains nothing (and is not learned from)
            accepted = {i: a for i, a in accepted.items() if i != me}
        if me_ok:
            u, v, _m = accepted[me]
            self._self_seen = (u, v, now)
        me_alive = me is not None and self._me_dead is not True and \
            getattr(entries[me], "alias", None) not in self._dead
        # --- refresh / unlearning of learned templates -------------------------------
        self._refresh(bgr, now, R_px, entries, accepted, me, out)
        # --- bootstrap: only when somebody needs it ----------------------------------
        want_self = me is not None and me_alive and not me_ok and \
            now - self._last_ok.get(me, self._first_t) >= min(WANT_SELF_S, now - self._first_t)
        others = [i for i, e in enumerate(entries) if i != me
                  and (i not in accepted or accepted[i][2] < OK_MARGIN_OTHER)
                  and getattr(e, "alias", None) not in self._dead
                  and now - self._last_ok.get(i, -1e9) >= ELIGIBLE_AFTER_S]
        if fix is not None:
            out.self_pos = (fix.u, fix.v, 0.6)
            self._self_seen = (fix.u, fix.v, now)
        # a track close to an elimination binding is followed more often
        pending = any(not tr.dead and tr.inter and len(tr.inter) == 1 and me not in tr.inter
                      and tr.hits >= 3 for tr in self._tracks)
        period = OTHERS_PENDING_EVERY if pending else OTHERS_EVERY
        if not want_self and not (others and self._frames % period == 0):
            self._expire(now)
            return
        if self._structs is None:
            self._structs = _structure_points()
        # (only the camera point: relaxing around my last position would feed on itself)
        relax = [cam] if want_self and cam is not None else []
        cands = ring_candidates(bgr, R_px, rings, self._structs, relax=relax)
        acc_pts = [(u, v) for (u, v, _m) in accepted.values()]
        # an icon is explained by an accepted match of its side (an ally stacked under an
        # enemy is not explained by the enemy)
        side_pts = {"ally": [], "enemy": []}
        for i, (u, v, _m) in accepted.items():
            if i < len(entries):
                rel = getattr(entries[i], "relation", "")
                side_pts["enemy" if rel == "enemy" else "ally"].append((u, v))

        def explained(u: float, v: float, side: str) -> bool:
            return any(math.hypot(u - a, v - b) < EXPLAINED_FRAC * D for a, b in side_pts[side])

        cands = [c for c in cands if not explained(c.u, c.v, c.side)]
        # every alive champion not matched (strongly, for me) in this frame may be an
        # unexplained icon; who may be LEARNED is decided at the binding (eligibility)
        unmatched: dict[str, set] = {"ally": set(), "enemy": set()}
        for i, e in enumerate(entries):
            rel = getattr(e, "relation", "")
            if getattr(e, "alias", None) in self._dead:
                continue
            if i == me:
                if me_alive and not me_ok:
                    unmatched["ally"].add(i)
            elif i not in accepted or accepted[i][2] < OK_MARGIN_OTHER:
                # (a weak match may be the official portrait on the wrong spot)
                unmatched["enemy" if rel == "enemy" else "ally"].add(i)
        # a track now explained by an accepted match was that champion: forget it
        self._tracks = [tr for tr in self._tracks if not explained(tr.u, tr.v, tr.side)]
        self._associate(cands, now, D, unmatched, cam, bgr, R_px, acc_pts)
        self._expire(now)
        # --- decisions ---------------------------------------------------------------
        # me: the best-supported track only (camera lock, teal outline, elimination)
        best_self: tuple[float, _UTrack] | None = None
        mine = [tr for tr in self._tracks if not tr.dead and tr.t == now and tr.inter
                and me is not None and me in tr.inter and tr.side == "ally"]
        for tr in mine:
            strong = tr.cam_frac() >= CAM_FRAC or tr.self_frac() >= SELF_RING_FRAC
            single = len(tr.inter) == 1 and len(mine) == 1 and tr.moved >= MOVED_MIN_ALONE
            prev = self._self_seen
            cont = prev is not None and now - prev[2] <= 1.0 and \
                math.hypot(tr.u - prev[0], tr.v - prev[1]) <= JUMP_SLACK + MAX_SPEED * (now - prev[2])
            cue = 1.5 * (tr.cam_frac() >= CAM_FRAC) + (tr.self_frac() >= SELF_RING_FRAC) + \
                0.5 * single + 0.7 * cont
            if (strong or single) and tr.hits >= SELF_HINT_HITS * (1 if strong else 2):
                sc = cue + 0.02 * min(tr.hits, 50)
                if best_self is None or sc > best_self[0]:
                    best_self = (sc, tr)
        if best_self is not None:
            self._maybe_bind(best_self[1], now, me, entries, out, for_me=True)
        for tr in self._tracks:
            if not tr.dead and tr.t == now and tr.inter and me not in tr.inter:
                self._maybe_bind(tr, now, me, entries, out)
        if fix is None and best_self is not None and me is not None and want_self:
            tr = best_self[1]
            out.self_pos = (tr.u, tr.v, float(min(0.9, 0.45 + 0.1 * best_self[0])))
            self._self_seen = (tr.u, tr.v, now)
        # --- official skin guess from the HUD portrait -------------------------------
        if me is not None and want_self and self.skin_guesser is not None and \
                now - self._last_ok.get(me, self._first_t) >= SKIN_GUESS_AFTER_S and \
                self._hud_portrait is not None and me not in self.learned:
            e = entries[me]
            n = self.skin_guesser.guess(getattr(e, "alias", ""), self._hud_portrait)
            if n is not None and n != getattr(e, "skin_id", 0) and n not in self._skin_tried \
                    and db is not None:
                self._skin_tried.add(n)
                icon = db.load_icon(getattr(e, "alias", ""), n)
                if icon is not None:
                    out.register[me] = ("skin", n, icon)
                    self._event(f"official skin {n} guessed from the HUD portrait")

    @staticmethod
    def _centred(bgr: np.ndarray, acc: tuple[float, float, float], R_px: float,
                 rings: Any = None) -> tuple[bool, RingCand | None]:
        """``(False, nearest ring)`` when an ally-side ring is right next to the match but
        not centred on it (normalized map coordinates), else ``(True, None)``."""
        H, W = bgr.shape[:2]
        cx, cy = acc[0] * W, acc[1] * H
        h = 2.2 * R_px
        x0, y0 = max(0, int(cx - h)), max(0, int(cy - h))
        x1, y1 = min(W, int(math.ceil(cx + h))), min(H, int(math.ceil(cy + h)))
        if x1 - x0 < 2 * R_px or y1 - y0 < 2 * R_px:
            return True, None
        win = bgr[y0:y1, x0:x1]
        best: tuple[float, RingCand] | None = None
        rel = [((cx - x0) / (x1 - x0), (cy - y0) / (y1 - y0))]
        for c in ring_candidates(win, R_px, rings, fountains=False, relax=rel):
            if c.side != "ally":
                continue
            px, py = c.u * (x1 - x0) + x0, c.v * (y1 - y0) + y0
            d = math.hypot(px - cx, py - cy)
            if best is None or d < best[0]:
                best = (d, RingCand(px / W, py / H, c.side, c.frac, c.frac_self))
        if best is None or best[0] <= 0.3 * R_px:
            return True, None
        return False, best[1] if best[0] <= 2.0 * R_px else None

    def _associate(self, cands: list[RingCand], now: float, D: float, unmatched: dict,
                   cam: tuple[float, float] | None, bgr: np.ndarray, R_px: float,
                   acc_pts: list[tuple[float, float]]) -> None:
        H, W = bgr.shape[:2]
        free = list(self._tracks)
        pairs = []
        for k, c in enumerate(cands):
            for tr in free:
                if tr.side != c.side:
                    continue
                dt = max(0.0, now - tr.t)
                d = math.hypot(c.u - tr.u, c.v - tr.v)
                if d <= JUMP_SLACK + MAX_SPEED * dt:
                    pairs.append((d, k, tr))
        pairs.sort(key=lambda p: p[0])
        used_c: set[int] = set()
        used_t: set[int] = set()
        matched: list[tuple[RingCand, _UTrack]] = []
        for d, k, tr in pairs:
            if k in used_c or tr.tid in used_t:
                continue
            used_c.add(k)
            used_t.add(tr.tid)
            matched.append((cands[k], tr))
        for k, c in enumerate(cands):
            if k in used_c:
                continue
            tr = _UTrack(self._next_tid, c.u, c.v, c.side, now, now, c.u, c.v)
            self._next_tid += 1
            self._tracks.append(tr)
            matched.append((c, tr))
        others_pts = [(c.u, c.v) for c in cands] + acc_pts
        for c, tr in matched:
            if tr.t != now or tr.hits == 0:
                tr.hits += 1
            tr.u, tr.v, tr.t = c.u, c.v, now
            tr.moved = max(tr.moved, math.hypot(c.u - tr.u0, c.v - tr.v0))
            if cam is not None and math.hypot(c.u - cam[0], c.v - cam[1]) < CAM_DIST:
                tr.cam_hits += 1
            if c.frac_self >= 0.5 * c.frac and c.frac_self > 0.2:
                tr.self_ring += 1
            if tr.dead:
                continue
            um = unmatched.get(c.side, set())
            tr.inter = set(um) if tr.inter is None else (tr.inter & um)
            if not tr.inter:
                tr.dead = True
                tr.crops.clear()
                continue
            # crop only an isolated icon (nothing drawn over it)
            if all(math.hypot(c.u - a, c.v - b) >= ISOLATED_FRAC * D or (a, b) == (c.u, c.v)
                   for a, b in others_pts):
                cr = crop_icon(bgr, c.u * W, c.v * H, R_px)
                if cr is not None:
                    if tr.crops:
                        cr, _s = align_to(cr, tr.crops[0])
                    tr.crops.append(cr)
                    del tr.crops[:-MAX_CROPS]

    def _expire(self, now: float) -> None:
        self._tracks = [t for t in self._tracks if now - t.t <= TRACK_LOST_S and now >= t.t - 1.0]

    def _maybe_bind(self, tr: _UTrack, now: float, me: int | None, entries: Sequence[Any],
                    out: StepOut, for_me: bool = False) -> None:
        """Learn the icon of the track's champion when the evidence is enough. ``for_me``: the
        track is my best-supported one (see :meth:`_step`); otherwise elimination only."""
        if for_me:
            i = me
        elif tr.inter and len(tr.inter) == 1:
            i = next(iter(tr.inter))
            if now - self._last_ok.get(i, -1e9) < ELIGIBLE_AFTER_S or tr.moved < MOVED_MIN_ALONE:
                return                  # matched by its portrait recently / a still "icon"
        else:
            return
        is_me = i == me
        hits = BIND_HITS_SELF if is_me else BIND_HITS_OTHER
        dur = BIND_MIN_S_SELF if is_me else BIND_MIN_S_OTHER
        if i is None or tr.hits < hits or now - tr.t0 < dur or len(tr.crops) < MIN_CROPS:
            return
        icon, consist = median_icon(tr.crops)
        if consist < CONSIST_MIN:
            tr.crops = tr.crops[-MIN_CROPS // 2:]          # moving occluders: collect again
            return
        e = entries[i]
        alias = str(getattr(e, "alias", ""))
        out.register[i] = ("learned", None, np.clip(icon, 0, 255).astype(np.uint8))
        self.learned[i] = _Learned(alias, icon, now, now, "learned", last_reg=now)
        self._last_ok[i] = now
        if is_me:
            self._self_seen = (tr.u, tr.v, now)
            self._save(alias, int(getattr(e, "skin_id", 0) or 0), icon)
            self.learned[i].last_save = now
        self._event(f"icon of {alias} learned ({len(tr.crops)} crops, consistency {consist:.2f})")
        tr.dead = True
        tr.crops.clear()

    def _refresh(self, bgr: np.ndarray, now: float, R_px: float, entries: Sequence[Any],
                 accepted: dict, me: int | None, out: StepOut) -> None:
        H, W = bgr.shape[:2]
        D = 2.0 * R_px / W
        for i, lr in list(self.learned.items()):
            if i >= len(entries):
                del self.learned[i]
                continue
            alias = getattr(entries[i], "alias", "")
            acc = accepted.get(i)
            if acc is None:
                alive = alias not in self._dead and (i != me or self._me_dead is not True)
                limit = REVERT_S if i == me else REVERT_OTHER_S
                if alive and now - lr.last_ok > limit:
                    out.revert.append(i)
                    del self.learned[i]
                    self._event(f"{lr.source} icon of {alias} unlearned (unmatched {limit:.0f} s)")
                continue
            u, v, margin = acc
            if margin < EMA_MARGIN or lr.source == "skin":
                continue
            if any(j != i and math.hypot(u - a, v - b) < ISOLATED_FRAC * D
                   for j, (a, b, _m) in accepted.items()):
                continue
            cr = crop_icon(bgr, u * W, v * H, R_px)
            if cr is None:
                continue
            cr, s = align_to(cr, lr.icon)
            if s < 0.5:
                continue
            lr.icon = (1 - EMA_RATE) * lr.icon + EMA_RATE * cr
            lr.updates += 1
            if lr.updates % REREGISTER_EVERY == 0 and now - lr.last_reg >= REREGISTER_MIN_S:
                lr.last_reg = now
                out.register[i] = ("refresh", None, np.clip(lr.icon, 0, 255).astype(np.uint8))
                if i == me and now - lr.last_save >= SAVE_MIN_S:
                    lr.last_save = now
                    self._save(alias, int(getattr(entries[i], "skin_id", 0) or 0), lr.icon)


# ======================================================================================
# Official skin guess from the HUD portrait
# ======================================================================================

#: CommunityDragon champion data (skin list), by numeric champion key.
CDRAGON_CHAMPION_JSON = ("https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/"
                         "global/default/v1/champions/{key}.json")
GUESS_PX = 40
GUESS_MIN = 0.62
GUESS_MARGIN = 0.08
MAX_SKINS = 60


def _circle_feats(bgr: np.ndarray, frac: float) -> np.ndarray | None:
    """Lab features of the centre disc (``frac`` of the half side) at ``GUESS_PX``."""
    h, w = bgr.shape[:2]
    s = min(h, w)
    half = max(2.0, frac * s / 2.0)
    c = ((w - 1) / 2.0, (h - 1) / 2.0)
    k = GUESS_PX / (2.0 * half)
    M = np.float32([[k, 0, (GUESS_PX - 1) / 2.0 - k * c[0]], [0, k, (GUESS_PX - 1) / 2.0 - k * c[1]]])
    img = cv2.warpAffine(bgr, M, (GUESS_PX, GUESS_PX), flags=cv2.INTER_AREA,
                         borderMode=cv2.BORDER_REPLICATE)
    return cv2.GaussianBlur(_lab(img.astype(np.float32)), (0, 0), 1.0)


_GMASK = _disc(GUESS_PX, GUESS_PX / 2.0 - 1.0)


def _feat_ncc(a: np.ndarray, b: np.ndarray) -> float:
    m = _GMASK[:, :, None]
    n = float(_GMASK.sum())
    za = (a - (a * m).sum(axis=(0, 1)) / n) * m
    zb = (b - (b * m).sum(axis=(0, 1)) / n) * m
    num = (za * zb).sum(axis=(0, 1))
    sa, sb = (za * za).sum(axis=(0, 1)), (zb * zb).sum(axis=(0, 1))
    nl = num[0] / math.sqrt(sa[0] * sb[0] + 1e-6)
    nc = (num[1] + num[2]) / math.sqrt((sa[1] + sa[2]) * (sb[1] + sb[2]) + 1e-6 + 25.0 * n)
    return float(0.5 * nl + 0.5 * nc)


class SkinGuesser:
    """Which official skin of my champion the HUD portrait shows (lazy downloads).

    ``skin_list_fn(alias) -> list[int] | None`` overrides the CommunityDragon skin list
    (tests); ``allow_network`` False never downloads anything. Never raises.
    """

    def __init__(self, db: Any, allow_network: bool = True,
                 skin_list_fn: Callable[[str], Sequence[int] | None] | None = None) -> None:
        self.db = db
        self.allow_network = bool(allow_network)
        self.skin_list_fn = skin_list_fn
        self._lock = threading.Lock()
        self._skins: dict[str, list[int] | None] = {}
        self._pending: set[str] = set()
        self._scores: dict[tuple[str, int], float] = {}
        self._n: dict[str, int] = {}
        self._last: dict[str, float] = {}
        self.min_period_s = 2.0

    def _skin_numbers(self, alias: str) -> list[int] | None:
        with self._lock:
            if alias in self._skins:
                return self._skins[alias]
        if self.skin_list_fn is not None:
            try:
                nums = [int(n) for n in (self.skin_list_fn(alias) or [])][:MAX_SKINS]
            except Exception:
                nums = []
            with self._lock:
                self._skins[alias] = nums
            self._prefetch(alias, nums)
            return nums
        cached = self._read_list(alias)
        if cached is not None:
            with self._lock:
                self._skins[alias] = cached
            self._prefetch(alias, cached)
            return cached
        if not self.allow_network:
            with self._lock:
                self._skins[alias] = []
            return []
        with self._lock:
            if alias in self._pending:
                return None
            self._pending.add(alias)
        threading.Thread(target=self._fetch_list, args=(alias,), name="skin-list",
                         daemon=True).start()
        return None

    def _list_path(self, alias: str) -> Path | None:
        try:
            cdir = getattr(self.db, "cache_dir", None)
            return Path(cdir) / f"skins_{alias}.json" if cdir is not None else None
        except Exception:
            return None

    def _read_list(self, alias: str) -> list[int] | None:
        p = self._list_path(alias)
        try:
            if p is not None and p.is_file():
                return [int(n) for n in json.loads(p.read_text("utf-8"))][:MAX_SKINS]
        except Exception:
            pass
        return None

    def _fetch_list(self, alias: str) -> None:
        nums: list[int] = []
        try:
            entry = self.db.get(alias)
            key = int(getattr(entry, "key", 0) or 0)
            if key > 0:
                from treeaicoach.champions import USER_AGENT

                url = CDRAGON_CHAMPION_JSON.format(key=key)
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=8.0) as resp:  # noqa: S310 (https)
                    data = json.loads(resp.read(4 * 1024 * 1024).decode("utf-8"))
                for s in data.get("skins", []) or []:
                    n = int(s.get("id", 0)) % 1000
                    if n > 0:
                        nums.append(n)
                nums = sorted(set(nums))[:MAX_SKINS]
                p = self._list_path(alias)
                if p is not None:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(json.dumps(nums), "utf-8")
        except Exception as exc:
            log.info("Skin list of %s unavailable: %s", alias, exc)
        with self._lock:
            self._skins[alias] = nums
            self._pending.discard(alias)
        self._prefetch(alias, nums)

    def _prefetch(self, alias: str, nums: Sequence[int]) -> None:
        try:
            if nums and self.allow_network:
                self.db.prefetch_skin_icons([{"champion_alias": alias, "skin_id": n} for n in nums])
        except Exception:
            log.debug("Skin icon prefetch failed", exc_info=True)

    def _available(self, alias: str, nums: Sequence[int]) -> list[int]:
        out = [0]
        for n in nums:
            try:
                p = self.db.cached_icon_path(alias, n)
                if p is not None and Path(p).is_file():
                    out.append(int(n))
            except Exception:
                continue
        return out

    def guess(self, alias: str, portrait_bgr: np.ndarray, now: float | None = None) -> int | None:
        """Skin number shown by the HUD portrait when one official skin clearly wins (scores
        averaged over the calls), else None (not downloaded yet, custom skin...)."""
        try:
            if not alias or not isinstance(portrait_bgr, np.ndarray):
                return None
            now = time.monotonic() if now is None else now
            if now - self._last.get(alias, -1e9) < self.min_period_s:
                return self._best(alias)
            self._last[alias] = now
            nums = self._skin_numbers(alias)
            if nums is None:
                return None
            q = _circle_feats(portrait_bgr.astype(np.float32), 0.92)
            for n in self._available(alias, nums):
                icon = self.db.load_icon(alias, n)
                if icon is None:
                    continue
                if icon.shape[2] == 4:
                    a = icon[:, :, 3:4].astype(np.float32) / 255.0
                    bgr = icon[:, :, 2::-1].astype(np.float32) * a + 40.0 * (1 - a)
                else:
                    bgr = icon[:, :, :3].astype(np.float32)
                s = max(_feat_ncc(q, _circle_feats(bgr, f)) for f in (0.8, 0.9, 1.0))
                k = (alias, n)
                old = self._scores.get(k)
                self._scores[k] = s if old is None else 0.7 * old + 0.3 * s
            self._n[alias] = self._n.get(alias, 0) + 1
            return self._best(alias)
        except Exception:
            log.debug("Skin guess failed", exc_info=True)
            return None

    def _best(self, alias: str) -> int | None:
        sc = sorted(((s, n) for (a, n), s in self._scores.items() if a == alias), reverse=True)
        if not sc or self._n.get(alias, 0) < 2:
            return None
        best, n = sc[0]
        second = sc[1][0] if len(sc) > 1 else -1.0
        if best >= GUESS_MIN and best - second >= GUESS_MARGIN:
            return n
        return None


__all__ = ["IconLearner", "SkinGuesser", "RingCand", "StepOut", "ring_candidates", "crop_icon",
           "icon_ncc", "align_to", "median_icon", "LEARN_PX", "CROP_HALF"]
