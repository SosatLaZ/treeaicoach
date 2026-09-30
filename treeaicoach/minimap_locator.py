"""Automatic localisation of the League of Legends minimap on a screen capture.

The minimap is a square glued to the bottom-right corner of the game (bottom-left with the
"flip minimap" option), 0.14-0.50 x the screen height, whose content is the *whole* official
``2dlevelminimap`` texture (black margins included) plus fog of war, icons, pings and the
camera rectangle (see ``docs/MINIMAP_FACTS.md``).

Method (:meth:`MinimapLocator.locate`):

1. **Features**: grayscale -> ``log(I + c)`` -> light Gaussian blur. The fog of war is a
   uniform multiplication (~0.36) of the map, i.e. an additive offset in the log domain, so
   the texture structure (khaki lane ring, walls, river) keeps the same contrast in the fog.
2. **Templates**: the textures rendered by :mod:`treeaicoach.render` with their structures
   and camps (no champion), plus fog-of-war variants lit around each team's structures,
   averaged over the dragon-soul texture variants.
3. **Coarse search**: the screen is downscaled to a fixed working height; the templates are
   matched (``TM_CCOEFF_NORMED``) at ~2 % size steps covering ``[0.14, 0.50] x H``, only at
   positions whose margins to the bottom / side edges are <= 4 % of the height.
4. **Refinement** of the best candidates (each side) at (up to) full resolution: +/-2.5 %
   size, a few pixels of position.
5. **Score** = :meth:`MinimapLocator.verify` of the refined crop (canonical 128 px NCC with
   a small shift / scale tolerance). ``None`` below :data:`LOCATE_MIN_SCORE`.

Typical cost: ~40-80 ms for a 1920x1080 capture with ``side="auto"`` (first call + ~0.1 s
to build the templates). Thread-safe: templates are built once under a lock, then only read.
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
LOCATE_MIN_SCORE: float = 0.50
#: Suggested threshold for periodic re-checks with :meth:`MinimapLocator.verify`
#: (lower than :data:`LOCATE_MIN_SCORE`: crowded / pinged minimaps score a bit lower).
VERIFY_MIN_SCORE: float = 0.40
#: Searched minimap sizes, as fractions of the screen height.
SIZE_RANGE: tuple[float, float] = (0.14, 0.50)
#: Maximum gap between the minimap and the bottom / side edges (fraction of the height).
MAX_MARGIN: float = 0.04
#: :func:`fallback_rect` square size (fraction of the height), anchored in the corner.
FALLBACK_SIZE: float = 0.265

#: Coarse search: working height (px) and relative size step.
WORK_HEIGHT: int = 300
SIZE_STEP: float = 1.02
#: Refinement: the minimap is resampled to at most this size (px) for precise matching.
REFINE_SIZE: int = 256
REFINE_SIZE_TOL: float = 0.025
#: Number of coarse candidates refined per side.
REFINE_CANDIDATES: int = 2
#: Canonical size (px) and tolerances of :meth:`MinimapLocator.verify`.
VERIFY_SIZE: int = 128
VERIFY_SHIFT: int = 4
VERIFY_SCALES: tuple[float, ...] = (0.97, 1.0, 1.03)
#: Feature parameters: ``log(gray + LOG_OFFSET)``, Gaussian blur sigma (px at each scale).
LOG_OFFSET: float = 12.0
BLUR_SIGMA: float = 1.0
#: Fog darkening used for the fogged templates (real game: x0.36) and lit radius around
#: allied structures (normalized).
TEMPLATE_FOG: float = 0.64
TEMPLATE_VISION_R: float = 0.09
#: Master template resolution (px).
MASTER_SIZE: int = 512

_MIN_SCREEN_H = 120
_LOG_LUT = np.log(np.arange(256, dtype=np.float32) + LOG_OFFSET).astype(np.float32)


@dataclass
class MinimapLocation:
    """A located minimap: ``rect`` in screen pixels, ``score`` 0..1, ``method``
    ``"auto" | "manual" | "fallback"``, ``side`` ``"right" | "left"``."""

    rect: Rect
    score: float
    method: str
    side: str = "right"


def fallback_rect(window: Rect, side: str = "right") -> Rect:
    """Heuristic minimap rectangle when localisation fails.

    A square of side ~:data:`FALLBACK_SIZE` x height anchored in the bottom-right corner
    of ``window`` (bottom-left when ``side == "left"``); it contains the default-size
    minimap and its frame.
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


def _gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _feature(img: np.ndarray, sigma: float = BLUR_SIGMA) -> np.ndarray:
    """Matching feature of a BGR / gray ``uint8`` image: blurred ``log(gray + c)``."""
    g = _gray(img)
    if g.dtype != np.uint8:
        g = np.clip(g, 0, 255).astype(np.uint8)
    f = _LOG_LUT[g]
    if sigma > 0:
        f = cv2.GaussianBlur(f, (0, 0), sigma)
    return f


def _as_bgr_u8(img: Any) -> np.ndarray | None:
    """Validate / convert an input image to BGR (or gray) uint8; None if unusable."""
    if not isinstance(img, np.ndarray) or img.ndim not in (2, 3) or img.size == 0:
        return None
    if img.ndim == 3 and img.shape[2] not in (1, 3, 4):
        return None
    if img.ndim == 3 and img.shape[2] == 1:
        img = img[:, :, 0]
    if img.dtype != np.uint8:
        if not np.issubdtype(img.dtype, np.number):
            return None
        img = np.clip(np.nan_to_num(img.astype(np.float32)), 0, 255).astype(np.uint8)
    return img


# ======================================================================================
# Locator
# ======================================================================================


@dataclass
class _Candidate:
    score: float
    x: float         # full-resolution image coordinates (relative to the capture)
    y: float
    s: float
    side: str
    tidx: int


class MinimapLocator:
    """Finds the minimap in screen captures (see module doc). Thread-safe."""

    def __init__(self, assets_dir: Path | None = None) -> None:
        self.assets_dir = Path(assets_dir) if assets_dir is not None else None
        self._lock = threading.Lock()
        self._masters: list[np.ndarray] | None = None     # BGR MASTER_SIZE templates
        self._failed = False
        self._coarse_cache: dict[int, list[np.ndarray]] = {}
        self._verify_tpl: list[np.ndarray] | None = None
        self.last_timing: dict[str, float] = {}

    # ---------------------------------------------------------------- templates
    def _render_masters(self) -> list[np.ndarray]:
        """Template images (BGR, MASTER_SIZE): clean / fogged around each team's structures,
        each averaged over the available texture variants."""
        from .render import STRUCTURES, MinimapRenderer, Scene  # lazy: heavy module

        renderer = MinimapRenderer(self.assets_dir)
        textures = renderer.textures()
        if not textures:
            raise FileNotFoundError(f"no minimap texture in {renderer.assets_dir / 'minimap'}")
        variants: dict[str, str] = {}
        for t in textures:  # one texture per dragon-soul variant (baron pit shape is minor)
            key = t.split("_")[1] if t.count("_") >= 2 else t
            variants.setdefault(key, t)
        scenes: list[dict[str, Any]] = [{"fog_alpha": 0.0, "my_team": "ORDER"}]
        for team in ("ORDER", "CHAOS"):
            vision = [(u, v, TEMPLATE_VISION_R) for u, v, _k, t in STRUCTURES if t == team]
            scenes.append({"fog_alpha": TEMPLATE_FOG, "vision": vision, "my_team": team})
        masters = []
        for kw in scenes:
            acc = np.zeros((MASTER_SIZE, MASTER_SIZE, 3), np.float32)
            for tex in variants.values():
                img = renderer.render(Scene(texture=tex, size=MASTER_SIZE, structures=True,
                                            camps=True, **kw))
                acc += img.astype(np.float32)
            masters.append(np.clip(acc / len(variants) + 0.5, 0, 255).astype(np.uint8))
        return masters

    def _ensure_templates(self) -> list[np.ndarray] | None:
        if self._masters is not None or self._failed:
            return self._masters
        with self._lock:
            if self._masters is None and not self._failed:
                t0 = time.perf_counter()
                try:
                    masters = self._render_masters()
                    vt: list[np.ndarray] = []
                    k = VERIFY_SHIFT
                    for m in masters:
                        for z in VERIFY_SCALES:
                            sz = int(round(VERIFY_SIZE * z))
                            f = _feature(cv2.resize(m, (sz, sz), interpolation=cv2.INTER_AREA))
                            c = (sz - (VERIFY_SIZE - 2 * k)) // 2
                            n = VERIFY_SIZE - 2 * k
                            vt.append(np.ascontiguousarray(f[c:c + n, c:c + n]))
                    self._verify_tpl = vt
                    self._masters = masters
                    log.debug("Minimap templates ready (%d) in %.0f ms", len(masters),
                              1000 * (time.perf_counter() - t0))
                except Exception:
                    self._failed = True
                    log.exception("Cannot build the minimap templates: automatic "
                                  "localisation disabled")
        return self._masters

    def _coarse_templates(self, s: int) -> list[np.ndarray]:
        tpl = self._coarse_cache.get(s)
        if tpl is None:
            tpl = [_feature(cv2.resize(m, (s, s), interpolation=cv2.INTER_AREA))
                   for m in self._masters or ()]
            if len(self._coarse_cache) < 512:
                self._coarse_cache[s] = tpl
        return tpl

    # ---------------------------------------------------------------- public API
    def verify(self, minimap_bgr: np.ndarray) -> float:
        """Similarity 0..1 between a minimap crop and the minimap texture.

        Robust to fog, icons and a few % of misalignment. ~0.6-0.9 on a real minimap,
        < 0.3 on unrelated images. 0.0 on invalid input. Never raises.
        """
        try:
            img = _as_bgr_u8(minimap_bgr)
            if img is None or min(img.shape[:2]) < 16 or self._ensure_templates() is None:
                return 0.0
            small = cv2.resize(img, (VERIFY_SIZE, VERIFY_SIZE), interpolation=cv2.INTER_AREA)
            f = _feature(small)
            if float(f.std()) < 1e-3:
                return 0.0
            best = -1.0
            for t in self._verify_tpl or ():
                r = cv2.matchTemplate(f, t, cv2.TM_CCOEFF_NORMED)
                best = max(best, float(np.nanmax(r)) if r.size else -1.0)
            return float(min(1.0, max(0.0, best)))
        except Exception:
            log.exception("verify failed")
            return 0.0

    def locate(self, screen_bgr: np.ndarray, origin: Rect | None = None,
               side: str = "auto") -> MinimapLocation | None:
        """Find the minimap in ``screen_bgr`` (capture whose top-left is ``origin``).

        Returns the rectangle in screen coordinates (origin offset added) with its score,
        or None if nothing scores at least :data:`LOCATE_MIN_SCORE`. Never raises.
        """
        t_start = time.perf_counter()
        try:
            img = _as_bgr_u8(screen_bgr)
            if img is None or img.shape[0] < _MIN_SCREEN_H or img.shape[1] < _MIN_SCREEN_H:
                return None
            if self._ensure_templates() is None:
                return None
            t_tpl = time.perf_counter()
            side = str(side or "auto").lower()
            sides = ("right", "left") if side not in ("right", "left") else (side,)
            H, W = img.shape[:2]
            f = WORK_HEIGHT / float(H)
            ws = max(1, int(round(W * f)))
            small = cv2.resize(img, (ws, WORK_HEIGHT), interpolation=cv2.INTER_AREA)
            feat = _feature(small)
            cands: list[_Candidate] = []
            for sd in sides:
                cands.extend(self._coarse(feat, f, sd))
            t_coarse = time.perf_counter()
            best: _Candidate | None = None
            for c in cands:
                r = self._refine(img, c)
                if r is None:
                    continue
                x, y, s = r
                crop = img[y:y + s, x:x + s]
                sc = self.verify(crop)
                if best is None or sc > best.score:
                    best = _Candidate(sc, x, y, s, c.side, c.tidx)
            t_end = time.perf_counter()
            self.last_timing = {"templates": t_tpl - t_start, "coarse": t_coarse - t_tpl,
                                "refine": t_end - t_coarse, "total": t_end - t_start}
            if best is None or best.score < LOCATE_MIN_SCORE:
                log.debug("Minimap not found (best %s)", best)
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
    def _coarse(self, feat: np.ndarray, f: float, side: str) -> list[_Candidate]:
        """Best coarse candidates (full-resolution coordinates) near one bottom corner."""
        Hs, Ws = feat.shape[:2]
        m = int(math.ceil(MAX_MARGIN * Hs))
        s_min = max(8, int(round(SIZE_RANGE[0] * Hs)))
        s_max = min(int(round(SIZE_RANGE[1] * Hs)), Hs, Ws)
        found: list[_Candidate] = []
        s_f = float(s_min)
        seen: set[int] = set()
        while True:
            s = int(round(s_f))
            s_f *= SIZE_STEP
            if s > s_max:
                break
            if s in seen:
                continue
            seen.add(s)
            y0 = max(0, Hs - s - m)
            if side == "right":
                x0 = max(0, Ws - s - m)
                reg = feat[y0:, x0:]
            else:
                x0 = 0
                reg = feat[y0:, :min(Ws, s + m)]
            if reg.shape[0] < s or reg.shape[1] < s:
                continue
            for ti, t in enumerate(self._coarse_templates(s)):
                r = cv2.matchTemplate(reg, t, cv2.TM_CCOEFF_NORMED)
                _mn, mv, _l, ml = cv2.minMaxLoc(r)
                if np.isfinite(mv):
                    found.append(_Candidate(float(mv), (x0 + ml[0]) / f, (y0 + ml[1]) / f,
                                            s / f, side, ti))
        found.sort(key=lambda c: -c.score)
        out: list[_Candidate] = []
        for c in found:  # distinct sizes (> 6 %)
            if all(abs(c.s - o.s) > 0.06 * o.s for o in out):
                out.append(c)
            if len(out) >= REFINE_CANDIDATES:
                break
        return out

    def _refine(self, img: np.ndarray, c: _Candidate) -> tuple[int, int, int] | None:
        """Refine a coarse candidate: (x, y, size) in full-resolution image pixels."""
        H, W = img.shape[:2]
        coarse_px = H / float(WORK_HEIGHT)            # one working pixel in full-res px
        pad = int(math.ceil(1.5 * coarse_px + REFINE_SIZE_TOL * c.s + 3))
        x0 = max(0, int(math.floor(c.x)) - pad)
        y0 = max(0, int(math.floor(c.y)) - pad)
        x1 = min(W, int(math.ceil(c.x + c.s)) + pad)
        y1 = min(H, int(math.ceil(c.y + c.s)) + pad)
        if x1 - x0 < 16 or y1 - y0 < 16:
            return None
        f2 = min(1.0, REFINE_SIZE / max(1.0, c.s))
        crop = img[y0:y1, x0:x1]
        if f2 < 1.0:
            cw = max(1, int(round((x1 - x0) * f2)))
            ch = max(1, int(round((y1 - y0) * f2)))
            crop = cv2.resize(crop, (cw, ch), interpolation=cv2.INTER_AREA)
            fx, fy = (x1 - x0) / cw, (y1 - y0) / ch
        else:
            fx = fy = 1.0
        feat = _feature(crop)
        master = (self._masters or [])[c.tidx]
        s_lo = int(math.floor(c.s * f2 * (1 - REFINE_SIZE_TOL)))
        s_hi = int(math.ceil(c.s * f2 * (1 + REFINE_SIZE_TOL)))
        best: tuple[float, int, int, int] | None = None
        for s2 in range(max(8, s_lo), s_hi + 1):
            if s2 > feat.shape[0] or s2 > feat.shape[1]:
                break
            t = _feature(cv2.resize(master, (s2, s2), interpolation=cv2.INTER_AREA))
            r = cv2.matchTemplate(feat, t, cv2.TM_CCOEFF_NORMED)
            _mn, mv, _l, ml = cv2.minMaxLoc(r)
            if np.isfinite(mv) and (best is None or mv > best[0]):
                best = (float(mv), ml[0], ml[1], s2)
        if best is None:
            return None
        _sc, bx, by, s2 = best
        x = int(round(x0 + bx * fx))
        y = int(round(y0 + by * fy))
        s = int(round(s2 * (fx + fy) / 2.0))
        s = max(1, min(s, W - x, H - y))
        return x, y, s
