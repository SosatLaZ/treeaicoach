"""Automatic localisation of the League of Legends minimap on a screen capture.

The minimap is a square glued to the bottom-right corner of the game (bottom-left with the
"flip minimap" option), 0.14-0.50 x the screen height, whose content is the *whole* official
``2dlevelminimap`` texture (black margins included) plus fog of war, icons, pings and the
camera rectangle (see ``docs/MINIMAP_FACTS.md``).

Method (:meth:`MinimapLocator.locate`):

1. **Features** (2 channels, lightly blurred): ``log(gray + c)`` and the chroma
   ``log(B + c) - log(R + c)``. The fog of war multiplies the map by ~0.36, which is an
   additive offset in the log domain (and cancels in the chroma), so the texture structure
   (khaki lane ring, dark walls, blue river, blue-grey bases) keeps its contrast in the fog.
   The chroma channel (blue river / khaki lanes) makes random scenes score very low.
2. **Templates**: the textures rendered by :mod:`treeaicoach.render` with structures, camps
   and shop but no champion, clean and fogged (lit around each team's structures), each
   averaged over the dragon-soul texture variants.
3. **Coarse search**: the capture is downscaled to :data:`WORK_HEIGHT` px; templates are
   matched (``TM_CCOEFF_NORMED``, mean of the two channels) at ~2 % size steps covering
   ``[0.14, 0.50] x H``, only at positions whose gaps to the bottom and side edges are
   <= 4 % of the height (one or both bottom corners, depending on ``side``).
4. **Refinement** of the best candidates at up to full resolution (the minimap resampled
   to <= :data:`REFINE_SIZE` px): +/-2.5 % size, a few pixels of position.
5. **Score** = :meth:`MinimapLocator.verify` of the refined crop (canonical 128 px NCC with
   a small shift / scale tolerance). ``None`` below :data:`LOCATE_MIN_SCORE`.

Thread-safe: templates are built once under a lock (first call, ~0.2-0.4 s), then only read.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .capture import Rect

log = logging.getLogger(__name__)

# ======================================================================================
# Tunable constants
# ======================================================================================

#: Minimum :meth:`MinimapLocator.verify` score of a located minimap (else ``locate`` -> None).
#: Measured: true minimaps 0.74-0.95 (real 2024-2026 captures, synthetic), best false
#: candidate on screenshots without a minimap <= 0.35.
LOCATE_MIN_SCORE: float = 0.55
#: Suggested threshold for periodic re-checks with :meth:`MinimapLocator.verify` on the crop
#: of the known rectangle (a covered minimap - shop, scoreboard, death screen - drops below).
VERIFY_MIN_SCORE: float = 0.45
#: Searched minimap sizes, as fractions of the capture height.
SIZE_RANGE: tuple[float, float] = (0.14, 0.50)
#: Maximum gap between the minimap and the bottom / side edges (fraction of the height).
MAX_MARGIN: float = 0.04
#: :func:`fallback_rect` square size (fraction of the height), anchored in the corner.
FALLBACK_SIZE: float = 0.265

#: Coarse search: working height (px) and relative size step.
WORK_HEIGHT: int = 300
SIZE_STEP: float = 1.02
#: Master templates used by the coarse search (0 clean, 1 / 2 fogged lit around the
#: ORDER / CHAOS structures); all of them are used for the refinement and :meth:`verify`.
COARSE_TEMPLATES: tuple[int, ...] = (0, 1, 2)
#: Coarse candidates refined per side, and score gap below the best one to skip refining.
REFINE_CANDIDATES: int = 2
REFINE_SCORE_GAP: float = 0.15
#: Refinement: the minimap is resampled to at most this size (px); size tolerance.
REFINE_SIZE: int = 256
REFINE_SIZE_TOL: float = 0.025
#: Canonical size (px) and tolerances of :meth:`MinimapLocator.verify`.
VERIFY_SIZE: int = 128
VERIFY_SHIFT: int = 4
VERIFY_SCALES: tuple[float, ...] = (0.97, 1.0, 1.03)
#: Features: ``log(x + LOG_OFFSET)``; Gaussian blur sigma in px at every working scale.
LOG_OFFSET: float = 12.0
BLUR_SIGMA: float = 1.0
#: Fogged templates: darkening (real game: x0.36) and lit radius around allied structures.
TEMPLATE_FOG: float = 0.64
TEMPLATE_VISION_R: float = 0.09
#: Master template resolution (px) and its resampling pyramid.
MASTER_SIZE: int = 512
_PYRAMID: tuple[int, ...] = (512, 384, 288, 216, 162, 122, 92, 69, 52, 39, 29, 22)

_MIN_CAPTURE = 120
_LOG_LUT = np.log(np.arange(256, dtype=np.float32) + LOG_OFFSET).astype(np.float32)


@dataclass
class MinimapLocation:
    """A located minimap: ``rect`` in screen pixels, ``score`` 0..1, ``method``
    ``"auto" | "manual" | "fallback"``, ``side`` ``"right" | "left"`` (screen corner)."""

    rect: Rect
    score: float
    method: str
    side: str = "right"


def fallback_rect(window: Rect, side: str = "right") -> Rect:
    """Heuristic minimap rectangle when localisation fails.

    A square of side ~:data:`FALLBACK_SIZE` x height anchored in the bottom-right corner of
    ``window`` (bottom-left when ``side == "left"``): it contains a default-size minimap
    and its frame. Never raises.
    """
    try:
        w, h = max(1, int(window.w)), max(1, int(window.h))
        s = max(1, min(int(round(FALLBACK_SIZE * h)), w, h))
        x = int(window.x) if str(side).lower() == "left" else int(window.x) + w - s
        return Rect(x, int(window.y) + h - s, s, s)
    except Exception:
        log.exception("fallback_rect failed for %r", window)
        return Rect(0, 0, 1, 1)


# ======================================================================================
# Features
# ======================================================================================

Feat = tuple[np.ndarray, np.ndarray]   # (log-gray, chroma), float32, same shape


def _features(img: np.ndarray, sigma: float = BLUR_SIGMA) -> Feat:
    """Blurred ``log(gray + c)`` and ``log(B + c) - log(R + c)`` of a BGR ``uint8`` image."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    lg = _LOG_LUT[g]
    ch = _LOG_LUT[img[:, :, 0]]
    ch -= _LOG_LUT[img[:, :, 2]]
    if sigma > 0:
        lg = cv2.GaussianBlur(lg, (0, 0), sigma)
        ch = cv2.GaussianBlur(ch, (0, 0), sigma)
    return lg, ch


def _ncc(feat: Feat, tpl: Feat) -> np.ndarray:
    """Mean over the two channels of the normalized cross-correlation maps."""
    r = cv2.matchTemplate(feat[0], tpl[0], cv2.TM_CCOEFF_NORMED)
    r += cv2.matchTemplate(feat[1], tpl[1], cv2.TM_CCOEFF_NORMED)
    r *= 0.5
    # flat regions give NaN / inf (zero variance): count them as "no match"
    np.nan_to_num(r, copy=False, nan=-1.0, posinf=-1.0, neginf=-1.0)
    return r


def _to_bgr_u8(img: Any) -> np.ndarray | None:
    """Validate / convert an image to contiguous BGR ``uint8``; None if unusable."""
    if not isinstance(img, np.ndarray) or img.ndim not in (2, 3) or img.size == 0:
        return None
    if img.ndim == 3 and img.shape[2] not in (1, 3, 4):
        return None
    if img.dtype != np.uint8:
        if not (np.issubdtype(img.dtype, np.integer) or np.issubdtype(img.dtype, np.floating)):
            return None
        img = np.clip(np.nan_to_num(img.astype(np.float32)), 0, 255).astype(np.uint8)
    if img.ndim == 2 or img.shape[2] == 1:
        return cv2.cvtColor(np.ascontiguousarray(img.reshape(img.shape[:2])), cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    return np.ascontiguousarray(img)


def _resize_from(pyramid: list[np.ndarray], size: int) -> np.ndarray:
    """Square resize from the smallest pyramid level >= size (ratio <= 1.33)."""
    src = pyramid[0]
    for lv in pyramid[1:]:
        if lv.shape[0] >= size:
            src = lv
    if src.shape[0] == size:
        return src
    return cv2.resize(src, (size, size), interpolation=cv2.INTER_AREA)


# ======================================================================================
# Locator
# ======================================================================================


@dataclass
class _Candidate:
    score: float
    x: float         # capture pixels (full resolution)
    y: float
    s: float
    side: str
    tidx: int


class _Templates:
    """Immutable template set (built once, then shared read-only between threads)."""

    def __init__(self, masters: list[np.ndarray]) -> None:
        self.pyramids = []
        for m in masters:
            self.pyramids.append([m] + [cv2.resize(m, (lv, lv), interpolation=cv2.INTER_AREA)
                                        for lv in _PYRAMID[1:] if lv < m.shape[0]])
        # coarse templates for every working size (fixed set: WORK_HEIGHT is constant)
        self.coarse_sizes = _coarse_sizes(WORK_HEIGHT)
        self.coarse_idx = [i for i in COARSE_TEMPLATES if i < len(self.pyramids)] or [0]
        self.coarse: dict[int, list[Feat]] = {
            s: [_features(_resize_from(self.pyramids[i], s)) for i in self.coarse_idx]
            for s in self.coarse_sizes}
        # verify templates: centre crops (shift tolerance) at a few scales
        n = VERIFY_SIZE - 2 * VERIFY_SHIFT
        self.verify: list[Feat] = []
        for p in self.pyramids:
            for z in VERIFY_SCALES:
                sz = int(round(VERIFY_SIZE * z))
                c = (sz - n) // 2
                lg, ch = _features(_resize_from(p, sz))
                self.verify.append((np.ascontiguousarray(lg[c:c + n, c:c + n]),
                                    np.ascontiguousarray(ch[c:c + n, c:c + n])))


def _coarse_sizes(work_h: int) -> list[int]:
    lo = max(8, int(round(SIZE_RANGE[0] * work_h)))
    hi = int(round(SIZE_RANGE[1] * work_h))
    sizes: list[int] = []
    s = float(lo)
    while int(round(s)) <= hi:
        if not sizes or int(round(s)) != sizes[-1]:
            sizes.append(int(round(s)))
        s *= SIZE_STEP
    return sizes


class MinimapLocator:
    """Finds the minimap in screen captures (see module doc). Thread-safe."""

    def __init__(self, assets_dir: Path | None = None) -> None:
        self.assets_dir = Path(assets_dir) if assets_dir is not None else None
        self._lock = threading.Lock()
        self._tpl: _Templates | None = None
        self._failed = False
        #: Timings (s) of the last :meth:`locate` call: templates / coarse / refine / total.
        self.last_timing: dict[str, float] = {}

    # ---------------------------------------------------------------- templates
    def _render_masters(self) -> list[np.ndarray]:
        """Template images (BGR, MASTER_SIZE): clean, and fogged but lit around each team's
        structures; each averaged over the texture variants."""
        from .render import STRUCTURES, MinimapRenderer, Scene  # lazy import

        renderer = MinimapRenderer(self.assets_dir)
        textures = renderer.textures()
        if not textures:
            raise FileNotFoundError(f"no minimap texture in {renderer.assets_dir / 'minimap'}")
        variants: dict[str, str] = {}
        for t in textures:  # one texture per dragon-soul variant (baron pit shape is minor)
            parts = t.split("_")
            variants.setdefault(parts[1] if len(parts) > 2 else t, t)
        scenes: list[dict[str, Any]] = [{"fog_alpha": 0.0, "my_team": "ORDER"}]
        for team in ("ORDER", "CHAOS"):
            vision = [(u, v, TEMPLATE_VISION_R) for u, v, _k, t in STRUCTURES if t == team]
            scenes.append({"fog_alpha": TEMPLATE_FOG, "vision": vision, "my_team": team})
        masters = []
        for kw in scenes:
            acc = np.zeros((MASTER_SIZE, MASTER_SIZE, 3), np.float32)
            for tex in variants.values():
                acc += renderer.render(Scene(texture=tex, size=MASTER_SIZE, structures=True,
                                             camps=True, **kw))
            masters.append(np.clip(acc / len(variants) + 0.5, 0, 255).astype(np.uint8))
        return masters

    def _templates(self) -> _Templates | None:
        tpl = self._tpl
        if tpl is not None or self._failed:
            return tpl
        with self._lock:
            if self._tpl is None and not self._failed:
                t0 = time.perf_counter()
                try:
                    self._tpl = _Templates(self._render_masters())
                    log.debug("Minimap templates ready in %.0f ms",
                              1000 * (time.perf_counter() - t0))
                except Exception:
                    self._failed = True
                    log.exception("Cannot build the minimap templates: automatic "
                                  "minimap localisation disabled")
            return self._tpl

    # ---------------------------------------------------------------- public API
    def verify(self, minimap_bgr: np.ndarray) -> float:
        """Similarity 0..1 between a minimap crop and the minimap texture.

        Robust to fog, icons, JPEG and a few % of misalignment: ~0.75-0.95 on a real
        minimap, < 0.35 on unrelated images, 0.0 on invalid input. Never raises.
        """
        try:
            img = _to_bgr_u8(minimap_bgr)
            if img is None or min(img.shape[:2]) < 16:
                return 0.0
            tpl = self._templates()
            if tpl is None:
                return 0.0
            small = cv2.resize(img, (VERIFY_SIZE, VERIFY_SIZE), interpolation=cv2.INTER_AREA)
            feat = _features(small)
            if float(feat[0].std()) < 1e-3 or float(feat[1].std()) < 1e-3:
                return 0.0
            best = max(float(_ncc(feat, t).max()) for t in tpl.verify)
            return float(min(1.0, max(0.0, best)))
        except Exception:
            log.exception("Minimap verify failed")
            return 0.0

    def locate(self, screen_bgr: np.ndarray, origin: Rect | None = None,
               side: str = "auto") -> MinimapLocation | None:
        """Find the minimap in ``screen_bgr`` (a capture whose top-left pixel is at
        ``origin.x, origin.y`` on the screen).

        ``side``: ``"right"`` / ``"left"`` searches one bottom corner, ``"auto"`` both.
        Returns the square in screen coordinates with its score, or None if nothing
        reaches :data:`LOCATE_MIN_SCORE`. Never raises.
        """
        t_start = time.perf_counter()
        try:
            img = _to_bgr_u8(screen_bgr)
            if img is None or min(img.shape[:2]) < _MIN_CAPTURE:
                return None
            tpl = self._templates()
            if tpl is None:
                return None
            t_tpl = time.perf_counter()
            sd = str(side or "auto").lower()
            sides = (sd,) if sd in ("right", "left") else ("right", "left")
            H, W = img.shape[:2]
            f = WORK_HEIGHT / float(H)
            small = cv2.resize(img, (max(1, int(round(W * f))), WORK_HEIGHT),
                               interpolation=cv2.INTER_AREA)
            feat = _features(small)
            cands: list[_Candidate] = []
            for s in sides:
                cands.extend(self._coarse(tpl, feat, f, s))
            t_coarse = time.perf_counter()
            best: _Candidate | None = None
            top = max((c.score for c in cands), default=-1.0)
            for c in sorted(cands, key=lambda c: -c.score):
                if c.score < top - REFINE_SCORE_GAP:
                    break
                r = self._refine(tpl, img, c)
                if r is None:
                    continue
                x, y, s = r
                sc = self.verify(img[y:y + s, x:x + s])
                if best is None or sc > best.score:
                    best = _Candidate(sc, x, y, s, c.side, c.tidx)
            t_end = time.perf_counter()
            self.last_timing = {"templates": t_tpl - t_start, "coarse": t_coarse - t_tpl,
                                "refine": t_end - t_coarse, "total": t_end - t_start}
            if best is None or best.score < LOCATE_MIN_SCORE:
                log.debug("Minimap not found (best candidate %s)", best)
                return None
            ox = int(origin.x) if origin is not None else 0
            oy = int(origin.y) if origin is not None else 0
            rect = Rect(ox + int(best.x), oy + int(best.y), int(best.s), int(best.s))
            return MinimapLocation(rect=rect, score=round(best.score, 4), method="auto",
                                   side=best.side)
        except Exception:
            log.exception("Minimap localisation failed")
            return None

    # ---------------------------------------------------------------- internals
    def _coarse(self, tpl: _Templates, feat: Feat, f: float, side: str) -> list[_Candidate]:
        """Best coarse candidates (capture coordinates) near one bottom corner."""
        Hs, Ws = feat[0].shape[:2]
        m = int(math.ceil(MAX_MARGIN * Hs))
        found: list[_Candidate] = []
        for s in tpl.coarse_sizes:
            if s > Hs or s > Ws:
                break
            y0 = max(0, Hs - s - m)
            x0 = max(0, Ws - s - m) if side == "right" else 0
            x1 = Ws if side == "right" else min(Ws, s + m)
            reg = (feat[0][y0:, x0:x1], feat[1][y0:, x0:x1])
            for ti, t in zip(tpl.coarse_idx, tpl.coarse[s]):
                _mn, mv, _l, ml = cv2.minMaxLoc(_ncc(reg, t))
                found.append(_Candidate(float(mv), (x0 + ml[0]) / f, (y0 + ml[1]) / f,
                                        s / f, side, ti))
        found.sort(key=lambda c: -c.score)
        out: list[_Candidate] = []
        for c in found:  # keep candidates of distinct sizes (> 6 % apart)
            if all(abs(c.s - o.s) > 0.06 * o.s for o in out):
                out.append(c)
                if len(out) >= REFINE_CANDIDATES:
                    break
        return out

    def _refine(self, tpl: _Templates, img: np.ndarray, c: _Candidate
                ) -> tuple[int, int, int] | None:
        """Refined (x, y, size) of a coarse candidate, in capture pixels."""
        H, W = img.shape[:2]
        work_px = H / float(WORK_HEIGHT)             # one coarse pixel in capture pixels
        pad = int(math.ceil(1.5 * work_px + REFINE_SIZE_TOL * c.s + 2))
        x0 = max(0, int(math.floor(c.x)) - pad)
        y0 = max(0, int(math.floor(c.y)) - pad)
        x1 = min(W, int(math.ceil(c.x + c.s)) + pad)
        y1 = min(H, int(math.ceil(c.y + c.s)) + pad)
        if x1 - x0 < 16 or y1 - y0 < 16:
            return None
        k = min(1.0, REFINE_SIZE / max(1.0, c.s))    # resampling factor
        crop = img[y0:y1, x0:x1]
        if k < 1.0:
            cw, ch = max(1, int(round((x1 - x0) * k))), max(1, int(round((y1 - y0) * k)))
            crop = cv2.resize(crop, (cw, ch), interpolation=cv2.INTER_AREA)
            fx, fy = (x1 - x0) / cw, (y1 - y0) / ch
        else:
            fx = fy = 1.0
        feat = _features(crop)
        pyr = tpl.pyramids[c.tidx]
        s_lo = max(8, int(math.floor(c.s * k * (1 - REFINE_SIZE_TOL))))
        s_hi = int(math.ceil(c.s * k * (1 + REFINE_SIZE_TOL)))
        best: tuple[float, int, int, int] | None = None
        for s2 in range(s_lo, s_hi + 1):
            if s2 > feat[0].shape[0] or s2 > feat[0].shape[1]:
                break
            _mn, mv, _l, ml = cv2.minMaxLoc(_ncc(feat, _features(_resize_from(pyr, s2))))
            if best is None or mv > best[0]:
                best = (float(mv), ml[0], ml[1], s2)
        if best is None:
            return None
        _sc, bx, by, s2 = best
        x = int(round(x0 + bx * fx))
        y = int(round(y0 + by * fy))
        s = int(round(s2 * (fx + fy) / 2.0))
        s = max(1, min(s, W - x, H - y))
        return x, y, s
