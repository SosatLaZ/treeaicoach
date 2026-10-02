"""Camera rectangle on the minimap -> projection from the map (u, v) to game-view pixels.

1. :func:`find_camera_rect` finds the white camera rectangle drawn by League on the minimap
   (``docs/MINIMAP_FACTS.md``: white 235-255, 1-2 px, ``0.272-0.279 x 0.151-0.158`` of the map
   width). Thresholded white pixels -> long horizontal / vertical segments (morphological
   opening) -> candidate rows / columns. A pair of rows ~0.155 W apart (or ONE row when the
   rectangle leaves the map: the other one is put at the known height, on the side where the
   vertical segments go) and likewise for the columns; every candidate rectangle is scored by
   how much of its *visible* sides is really drawn (icons drawn over the lines are tolerated;
   one side may even be fully hidden when the three others are drawn). 0.3-1 ms on a 300 px
   crop (larger crops are searched on a 320 px mask).
2. :class:`CameraTracker` smooths it over time (EMA, snap on a big jump: Space / recall / click
   on the minimap) and holds the last rectangle ~1.5 s when it is not found (icons, fights).
3. :class:`CameraProjection` maps a map point to a screen pixel of the game window.

Projection model (documented approximation)
-------------------------------------------
League's camera is a fixed-angle perspective camera (pitch ~56 deg, fixed FOV). Its ground
footprint is a trapezoid (the far / top edge is wider than the near / bottom one); the minimap
draws it as a rectangle. We model the true footprint as the drawn rectangle with its top edge
widened and its bottom edge narrowed by :data:`PERSPECTIVE_K` (same height, same centre line),
and fit the homography trapezoid -> game-window corners. ``PERSPECTIVE_K = 0`` is the plain
rectangle -> screen mapping.

Value: with a pitch of 56 deg and a vertical FOV of ~40 deg the far / near footprint widths are
in a ratio ~1.65 -> ``k = (1.65 - 1) / (1.65 + 1) ~ 0.245``, and the screen centre then sees the
ground at ~62 % of the rectangle height (not 50 %: the near half of the ground is magnified).
Checked on a real 2000x1125 screenshot (``scratchpad/v2/user_ingame_screenshot2.png``): the
enemy top outer turret (map 0.29, 0.067) projects at (1076, 547) px, its base is at ~(1120, 520);
with k = 0 it would land at (1071, 686), 15 % of the screen height too low. Expected error: a few
% of the screen, enough for a ground marker of ~70 px. Re-calibrate the constant if needed.

Pure numpy / OpenCV, never raises from the public API.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Any, Sequence

import cv2
import numpy as np

log = logging.getLogger(__name__)

#: Camera rectangle size, fraction of the minimap width (MINIMAP_FACTS: 0.272-0.279 x 0.151-0.158).
CAM_W = 0.275
CAM_H = 0.155
#: Accepted measured sizes (pairs of lines).
CAM_W_RANGE = (0.22, 0.33)
CAM_H_RANGE = (0.11, 0.20)
#: Top edge widened / bottom edge narrowed by this fraction (perspective, see module doc).
PERSPECTIVE_K = 0.24
#: Map width in game units (Summoner's Rift ~14 870) and a typical move speed (units / s).
MAP_UNITS = 14870.0
MOVE_SPEED = 345.0
#: White mask thresholds (min channel, max - min).
WHITE_MIN, WHITE_SAT = 185, 45
#: Local-contrast fallback (white top-hat of the min channel) for blurred captures.
TOPHAT_MIN = 55
_TOPHAT_K = np.ones((5, 5), np.uint8)
#: A visible side must be drawn on at least this fraction of its length.
SIDE_MIN = 0.35
#: ... except ONE side hidden by icons (the three others drawn), scored this much lower.
WEAK_PENALTY = 0.5
#: Downscale the mask above this width (keeps the search ~ constant time).
MAX_W = 420
WORK_W = 320


@dataclass(frozen=True)
class CameraRect:
    """Camera rectangle in normalized minimap coordinates (may extend outside [0, 1])."""

    u0: float
    v0: float
    u1: float
    v1: float
    score: float = 1.0

    @property
    def center(self) -> tuple[float, float]:
        return 0.5 * (self.u0 + self.u1), 0.5 * (self.v0 + self.v1)

    @property
    def size(self) -> tuple[float, float]:
        return self.u1 - self.u0, self.v1 - self.v0

    def as_tuple(self) -> tuple[float, float, float, float]:
        return self.u0, self.v0, self.u1, self.v1


# ======================================================================================
# Finder
# ======================================================================================
def _runs(profile: np.ndarray, min_count: float, max_w: int = 4) -> list[tuple[float, float]]:
    """(centre, strength) of thin runs where ``profile >= min_count`` (wide runs = white areas)."""
    on = profile >= min_count
    if not on.any():
        return []
    d = np.diff(np.concatenate(([0], on.view(np.int8), [0])))
    starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1
    out = [(0.5 * (a + b), float(profile[a:b + 1].max())) for a, b in zip(starts, ends) if b - a <= max_w]
    out.sort(key=lambda r: -r[1])
    return out[:6]


def _cover(line: np.ndarray) -> float:
    return float(line.mean()) if line.size else 0.0


def _side_support(horiz: np.ndarray, vert: np.ndarray, x0: float, y0: float, x1: float, y1: float
                  ) -> tuple[float, int, int]:
    """Mean coverage of the visible sides of the rectangle, (#visible horizontal, #visible vertical)
    sides with enough coverage; -1 when the rectangle is not supported.

    One visible side may be hidden (champion icons drawn over it) when the three others are well
    drawn: it then counts for :data:`WEAK_PENALTY` less."""
    h_, w_ = horiz.shape
    xa, xb = max(0, int(round(x0))), min(w_, int(round(x1)) + 1)
    ya, yb = max(0, int(round(y0))), min(h_, int(round(y1)) + 1)
    if xb - xa < 3 or yb - ya < 3:
        return -1.0, 0, 0
    covs: list[float] = []
    nh = nv = weak = 0
    for y in (y0, y1):
        r = int(round(y))
        if 0 <= r < h_:
            band = horiz[max(0, r - 1):r + 2, xa:xb].max(axis=0)
            c = _cover(band)
            if c < SIDE_MIN:
                if min(r, h_ - 1 - r) <= 2:      # at the very border: treated as clipped
                    continue
                weak += 1
                covs.append(c - WEAK_PENALTY)
                continue
            covs.append(c)
            nh += 1
    for x in (x0, x1):
        c_ = int(round(x))
        if 0 <= c_ < w_:
            band = vert[ya:yb, max(0, c_ - 1):c_ + 2].max(axis=1)
            c = _cover(band)
            if c < SIDE_MIN:
                if min(c_, w_ - 1 - c_) <= 2:
                    continue
                weak += 1
                covs.append(c - WEAK_PENALTY)
                continue
            covs.append(c)
            nv += 1
    if not covs or weak > 1 or (weak and nh + nv < 3):
        return -1.0, 0, 0
    return float(np.mean(covs)), nh, nv


def _options(runs: list[tuple[float, float]], seg_lo: np.ndarray, size: float, rng: tuple[float, float],
             limit: int) -> list[tuple[float, float, float]]:
    """Candidate (start, end, bonus) intervals from line runs: pairs at a plausible distance, or a single
    line + the known size on the side where the perpendicular segments extend (or both sides)."""
    out: list[tuple[float, float, float]] = []
    for i in range(len(runs)):
        for j in range(i + 1, len(runs)):
            a, b = sorted((runs[i][0], runs[j][0]))
            if rng[0] * limit <= b - a <= rng[1] * limit:
                out.append((a, b, 0.15))
    for c, _s in runs:
        p = int(round(c))
        lo = seg_lo[max(0, p - 3):p].sum() if p > 0 else 0
        hi = seg_lo[p + 1:p + 4].sum()
        sides = [1] if hi > lo * 1.5 else [-1] if lo > hi * 1.5 else [1, -1]
        for s in sides:
            out.append((c, c + size, 0.0) if s > 0 else (c - size, c, 0.0))
    return out


def white_mask(bgr: np.ndarray) -> np.ndarray:
    """uint8 0/1 mask of white, unsaturated pixels (fast: OpenCV per-channel min / max)."""
    b, g, r = cv2.split(np.ascontiguousarray(bgr[:, :, :3]))
    mn = cv2.min(cv2.min(b, g), r)
    mx = cv2.max(cv2.max(b, g), r)
    grey = cv2.compare(cv2.subtract(mx, mn), WHITE_SAT, cv2.CMP_LE)
    ok = cv2.compare(mn, WHITE_MIN, cv2.CMP_GE) & grey
    # blurred / compressed captures: thin lines only ~130 bright but much brighter than around
    th = cv2.morphologyEx(mn, cv2.MORPH_TOPHAT, _TOPHAT_K)
    ok |= cv2.compare(th, TOPHAT_MIN, cv2.CMP_GE) & cv2.compare(mn, 95, cv2.CMP_GE) & grey
    return (ok > 0).astype(np.uint8)


def find_camera_rect(minimap_bgr: Any) -> CameraRect | None:
    """White camera rectangle of a minimap crop (BGR), or None. Handles a rectangle partly outside
    the map (one row / column missing) and icons drawn over its lines. Never raises."""
    try:
        if not isinstance(minimap_bgr, np.ndarray) or minimap_bgr.ndim != 3 or minimap_bgr.shape[2] < 3:
            return None
        H, W = minimap_bgr.shape[:2]
        if min(H, W) < 48:
            return None
        mask = white_mask(minimap_bgr)
        if W > MAX_W:
            sh = max(1, int(round(H * WORK_W / W)))
            mask = (cv2.resize(mask, (WORK_W, sh), interpolation=cv2.INTER_AREA) > 0).astype(np.uint8)
        h_, w_ = mask.shape
        if int(mask.sum()) < 0.2 * w_:
            return None
        Lh = max(4, int(0.07 * w_))
        Lv = max(3, int(0.05 * w_))
        horiz = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((1, Lh), np.uint8))
        vert = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((Lv, 1), np.uint8))
        rows = _runs(horiz.sum(axis=1).astype(np.float32), Lh)
        cols = _runs(vert.sum(axis=0).astype(np.float32), Lv)
        if not rows and not cols:
            return None
        vprof = vert.sum(axis=1).astype(np.float32)    # vertical segments per row (where do they go?)
        hprof = horiz.sum(axis=0).astype(np.float32)
        ew, eh = CAM_W * w_, CAM_H * w_
        row_opts = _options(rows, vprof, eh, CAM_H_RANGE, w_)
        col_opts = _options(cols, hprof, ew, CAM_W_RANGE, w_)
        if not row_opts:             # both horizontal sides hidden: impossible unless occluded
            return None
        if not col_opts:
            return None
        best: tuple[float, tuple[float, float, float, float]] | None = None
        for (y0, y1, by) in row_opts[:24]:
            for (x0, x1, bx) in col_opts[:24]:
                aspect = (x1 - x0) / max(1e-6, (y1 - y0))
                if not 1.35 <= aspect <= 2.3:
                    continue
                s, nh, nv = _side_support(horiz, vert, x0, y0, x1, y1)
                if s < 0 or nh < 1 or nv < 1 or nh + nv < 2:
                    continue
                # inferred sides must really be outside the map (else they would be drawn)
                s = s + by + bx + 0.05 * (nh + nv)
                if best is None or s > best[0]:
                    best = (s, (x0, y0, x1, y1))
        if best is None:
            return None
        x0, y0, x1, y1 = best[1]
        sx, sy = 1.0 / w_, 1.0 / h_
        return CameraRect(float((x0 + 0.5) * sx), float((y0 + 0.5) * sy), float((x1 + 0.5) * sx),
                          float((y1 + 0.5) * sy), round(min(1.0, float(best[0])), 3))
    except Exception:
        log.debug("find_camera_rect failed", exc_info=True)
        return None


def rect_still_there(minimap_bgr: Any, rect: CameraRect | None, keep: float = 0.6) -> bool:
    """True when the white lines of ``rect`` (found in a previous frame) are still drawn at the
    same pixels: each side inside the map covered by white on >= ``keep`` of its length (one
    side may be hidden by icons). A 1-px camera move empties the old rows / columns, so a moved
    rectangle is never kept. ~0.1 ms instead of a ~4 ms search. Never raises."""
    try:
        if rect is None or not isinstance(minimap_bgr, np.ndarray) or minimap_bgr.ndim != 3:
            return False
        H, W = minimap_bgr.shape[:2]
        ok = weak = 0
        for kind, c, a0, a1 in (("h", rect.v0, rect.u0, rect.u1), ("h", rect.v1, rect.u0, rect.u1),
                                ("v", rect.u0, rect.v0, rect.v1), ("v", rect.u1, rect.v0, rect.v1)):
            n_c, n_a = (H, W) if kind == "h" else (W, H)
            pc = int(round(c * n_c - 0.5))
            if pc < 1 or pc > n_c - 2:
                continue                                   # clipped by the map border
            lo, hi = max(0, int(math.ceil(a0 * n_a)) + 1), min(n_a, int(math.floor(a1 * n_a)) - 1)
            if hi - lo < 6:
                continue
            strip = minimap_bgr[pc - 1:pc + 2, lo:hi] if kind == "h" else \
                minimap_bgr[lo:hi, pc - 1:pc + 2].transpose(1, 0, 2)
            m = white_mask(np.ascontiguousarray(strip)).max(axis=0)
            if float(m.mean()) >= keep:
                ok += 1
            else:
                weak += 1
        return ok >= 2 and weak <= 1
    except Exception:
        return False


class CameraTracker:
    """Temporal smoothing of :func:`find_camera_rect` (thread-safe)."""

    def __init__(self, alpha: float = 0.6, snap: float = 0.06, hold_s: float = 1.5) -> None:
        self.alpha, self.snap, self.hold_s = float(alpha), float(snap), float(hold_s)
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._rect: CameraRect | None = None
            self._t = -math.inf
            self._size: tuple[float, float] = (CAM_W, CAM_H)

    def update(self, minimap_bgr: Any, t: float) -> CameraRect | None:
        """Find the rectangle in this frame and return the smoothed one (None when lost)."""
        return self.feed(find_camera_rect(minimap_bgr), t)

    def feed(self, r: CameraRect | None, t: float) -> CameraRect | None:
        try:
            with self._lock:
                if r is not None:
                    cu, cv_ = r.center
                    w, h = r.size
                    # sizes measured on a full pair of lines feed the size estimate
                    if CAM_W_RANGE[0] <= w <= CAM_W_RANGE[1] and CAM_H_RANGE[0] <= h <= CAM_H_RANGE[1]:
                        self._size = (0.8 * self._size[0] + 0.2 * w, 0.8 * self._size[1] + 0.2 * h)
                    old = self._rect
                    if old is not None and t - self._t <= self.hold_s:
                        ou, ov = old.center
                        if math.hypot(cu - ou, cv_ - ov) < self.snap:
                            a = self.alpha
                            cu, cv_ = ou + a * (cu - ou), ov + a * (cv_ - ov)
                    sw, sh = self._size
                    self._rect = CameraRect(cu - sw / 2, cv_ - sh / 2, cu + sw / 2, cv_ + sh / 2, r.score)
                    self._t = t
                elif self._rect is not None and t - self._t > self.hold_s:
                    self._rect = None
                return self._rect
        except Exception:
            log.debug("CameraTracker.feed failed", exc_info=True)
            return None

    def current(self, t: float) -> CameraRect | None:
        with self._lock:
            return self._rect if self._rect is not None and t - self._t <= self.hold_s else None


# ======================================================================================
# Projection
# ======================================================================================
class CameraProjection:
    """Map (u, v) -> game-window pixels, from the camera rectangle (see the module doc).

    ``screen`` = (x, y, w, h) of the game window (physical px). ``persp`` = :data:`PERSPECTIVE_K`.
    """

    def __init__(self, cam: CameraRect | Sequence[float], screen: Sequence[float], persp: float = PERSPECTIVE_K
                 ) -> None:
        if isinstance(cam, CameraRect):
            u0, v0, u1, v1 = cam.as_tuple()
        else:
            u0, v0, u1, v1 = (float(c) for c in cam[:4])
        sx, sy, sw, sh = (float(c) for c in screen[:4])
        if not (u1 > u0 and v1 > v0 and sw > 0 and sh > 0):
            raise ValueError("degenerate camera / screen rectangle")
        self.cam = (u0, v0, u1, v1)
        self.screen = (sx, sy, sw, sh)
        self.persp = float(persp)
        hw = 0.5 * (u1 - u0)
        cu = 0.5 * (u0 + u1)
        k = self.persp
        src = np.float32([[cu - hw * (1 + k), v0], [cu + hw * (1 + k), v0],
                          [cu + hw * (1 - k), v1], [cu - hw * (1 - k), v1]])
        dst = np.float32([[sx, sy], [sx + sw, sy], [sx + sw, sy + sh], [sx, sy + sh]])
        self.H = cv2.getPerspectiveTransform(src, dst).astype(np.float64)
        self.Hinv = np.linalg.inv(self.H)

    @property
    def cam_center(self) -> tuple[float, float]:
        """Centre of the camera rectangle (map u, v)."""
        return 0.5 * (self.cam[0] + self.cam[2]), 0.5 * (self.cam[1] + self.cam[3])

    def map_to_screen(self, u: float, v: float) -> tuple[float, float] | None:
        """Screen pixel of the map point (None if behind the camera / not finite)."""
        p = self.H @ np.array([float(u), float(v), 1.0])
        if not np.all(np.isfinite(p)) or p[2] <= 1e-9:
            return None
        return float(p[0] / p[2]), float(p[1] / p[2])

    def screen_to_map(self, x: float, y: float) -> tuple[float, float] | None:
        p = self.Hinv @ np.array([float(x), float(y), 1.0])
        if not np.all(np.isfinite(p)) or abs(p[2]) <= 1e-12:
            return None
        return float(p[0] / p[2]), float(p[1] / p[2])

    def is_visible(self, u: float, v: float, margin: float = 0.0,
                   exclude: Sequence[Sequence[float]] = ()) -> bool:
        """The map point projects inside the game window (shrunk by ``margin`` px) and outside every
        ``exclude`` rectangle (x, y, w, h: minimap, HUD bar...)."""
        p = self.map_to_screen(u, v)
        if p is None:
            return False
        x, y = p
        sx, sy, sw, sh = self.screen
        if not (sx + margin <= x <= sx + sw - margin and sy + margin <= y <= sy + sh - margin):
            return False
        for r in exclude or ():
            try:
                if r[0] <= x <= r[0] + r[2] and r[1] <= y <= r[1] + r[3]:
                    return False
            except (TypeError, IndexError):
                continue
        return True

    def direction(self, u: float, v: float) -> tuple[float, float]:
        """Unit screen direction from the screen centre towards the map point (map u right / v down =
        screen right / down: the camera never rotates)."""
        cu = 0.5 * (self.cam[0] + self.cam[2])
        cv_ = 0.5 * (self.cam[1] + self.cam[3])
        ppu = self.screen[2] / (self.cam[2] - self.cam[0])
        ppv = self.screen[3] / (self.cam[3] - self.cam[1])
        dx, dy = (u - cu) * ppu, (v - cv_) * ppv
        n = math.hypot(dx, dy)
        return (dx / n, dy / n) if n > 1e-9 else (0.0, -1.0)


def make_projection(cam: CameraRect | None, screen: Any, persp: float = PERSPECTIVE_K) -> CameraProjection | None:
    """:class:`CameraProjection` or None (no camera / no screen / degenerate). Never raises."""
    if cam is None or screen is None:
        return None
    try:
        return CameraProjection(cam, screen, persp)
    except Exception:
        return None


def map_to_screen(cam: CameraRect | None, screen: Any, u: float, v: float,
                  persp: float = PERSPECTIVE_K) -> tuple[float, float] | None:
    """One-shot :meth:`CameraProjection.map_to_screen` (None without a camera / screen). Never raises."""
    proj = make_projection(cam, screen, persp)
    try:
        return proj.map_to_screen(u, v) if proj is not None else None
    except Exception:
        return None


def is_visible(cam: CameraRect | None, screen: Any, u: float, v: float, margin: float = 0.0,
               exclude: Sequence[Sequence[float]] = (), persp: float = PERSPECTIVE_K) -> bool:
    """One-shot :meth:`CameraProjection.is_visible` (False without a camera / screen). Never raises."""
    proj = make_projection(cam, screen, persp)
    try:
        return bool(proj is not None and proj.is_visible(u, v, margin, exclude))
    except Exception:
        return False


def walk_seconds(a: Sequence[float], b: Sequence[float]) -> float:
    """Rough walking time (s) between two map points."""
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1])) * MAP_UNITS / MOVE_SPEED


def hud_bar_rect(screen: Sequence[float]) -> tuple[int, int, int, int]:
    """League's bottom-centre HUD bar (spells / items / stats), screen px: the bottom ~11 % between
    ~27 % and ~65 % of the width (measured on 16:9 screenshots)."""
    sx, sy, sw, sh = (float(c) for c in screen[:4])
    return (int(sx + 0.27 * sw), int(sy + 0.885 * sh), int(0.38 * sw), int(0.115 * sh) + 1)


__all__ = ["CameraRect", "find_camera_rect", "CameraTracker", "CameraProjection", "make_projection",
           "map_to_screen", "is_visible",
           "walk_seconds", "hud_bar_rect", "PERSPECTIVE_K", "CAM_W", "CAM_H"]
