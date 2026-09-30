"""Pure renderers of the visual overlay: radar, HUD panel and danger screen-edge flash.

Everything here is numpy + PIL (fonts only) + OpenCV (resizing): no window, no Win32, so it is
testable on Linux and reused by the UI for its live / static previews. ``overlay.py`` only
puts the returned images on screen with ``UpdateLayeredWindow``.

Rendering model
    A small :class:`Canvas` holds a *premultiplied* RGBA float32 image. Shapes are drawn with
    analytic anti-aliasing (signed distance fields: discs, rings, dashed rings, rounded
    rectangles, capsules, convex polygons), text with FreeType (PIL) and icons with area
    resampling, then the canvas is converted to premultiplied **BGRA uint8** - the pixel format
    ``UpdateLayeredWindow`` expects (``AC_SRC_ALPHA``).

Palette ("hextech"): gold ``#C8AA6E``, light gold ``#F0E6D2``, teal ``#0AC8B9``, danger
``#E84057``, warning ``#F0A030``, safe ``#2DC66B``, dark panels ``#0A1428`` (~85 % opacity,
1 px gold border, rounded corners).

Fonts: Segoe UI / Segoe UI Bold (Windows), DejaVu Sans (Linux), PIL's default font as the last
resort; cached. French text with accents is rendered as is (UTF-8).

``python -m treeaicoach.overlay_render --demo OUTDIR`` writes sample renders (safe, warning,
danger with the jungler's fog region, late game).
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Sequence

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from treeaicoach.fog_tracker import FogEstimate

if TYPE_CHECKING:  # pragma: no cover - typing only
    from treeaicoach.capture import Rect
    from treeaicoach.objectives import ObjectiveState

log = logging.getLogger(__name__)

# ======================================================================================
# Palette (RGB 0..255) and tunable constants
# ======================================================================================
GOLD = (200, 170, 110)          # #C8AA6E
GOLD_LIGHT = (240, 230, 210)    # #F0E6D2
GOLD_DARK = (120, 90, 40)       # #785A28
TEAL = (10, 200, 185)           # #0AC8B9
DANGER = (232, 64, 87)          # #E84057
WARNING = (240, 160, 48)        # #F0A030
SAFE = (45, 198, 107)           # #2DC66B
PANEL = (10, 20, 40)            # #0A1428
PANEL_DEEP = (1, 10, 19)        # #010A13
MUTED = (160, 155, 140)         # #A09B8C
GREY = (91, 90, 86)             # #5B5A56
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
ALLY_BLUE = (78, 152, 218)

PANEL_ALPHA = 0.86
BORDER_ALPHA = 0.85
THREAT_COLORS = {0: SAFE, 1: WARNING, 2: DANGER}
THREAT_DEFAULT_TEXT = {0: "SÛR", 1: "ATTENTION", 2: "DANGER"}
LAST_SEEN_MAX_S = 60.0          # invisible enemies are drawn at their last position this long
ALERT_FADE_S = 4.0              # the "last alert" line fades out over this duration
HALO_PERIOD_S = 1.1             # jungler halo pulse period

#: Objective display name (FR) -> minimap icon file (assets/icons/minimap).
OBJECTIVE_ICONS: dict[str, str] = {
    "dragon": "dragon.png",
    "dragon ancestral": "dragon_elder.png",
    "baron": "baron.png",
    "héraut": "riftherald.png",
    "heraut": "riftherald.png",
    "larves": "grub.png",
    "atakhan": "atakhan_r.png",
}
#: Short labels for the HUD objectives row.
OBJECTIVE_SHORT: dict[str, str] = {"dragon ancestral": "Ancestral"}

_MAX_SIDE = 8192


# ======================================================================================
# Public data (ARCHITECTURE.md §7.2)
# ======================================================================================
@dataclass
class EnemyView:
    """One enemy champion as shown by the overlay."""

    key: str
    alias: str | None = None
    name: str = ""
    visible: bool = False
    uv: tuple[float, float] | None = None       # current (visible) or last seen position
    last_seen_ago: float | None = None          # seconds since last seen (None = never seen)
    is_jungler: bool = False
    approaching: bool = False
    icon: np.ndarray | None = None              # RGBA uint8 (ChampionDB.load_icon)
    velocity: tuple[float, float] | None = None  # normalized / s (optional, for the arrow)


@dataclass
class OverlayState:
    """Immutable-by-convention snapshot of everything the overlay shows."""

    minimap_rect: "Rect | None" = None
    screen_rect: "Rect | None" = None
    me_uv: tuple[float, float] | None = None
    my_team: str | None = None
    enemies: list[EnemyView] = field(default_factory=list)
    fogs: list[FogEstimate] = field(default_factory=list)
    threat_level: int = 0                     # 0 safe, 1 warning, 2 danger
    threat_text: str = "SÛR"
    last_alert: tuple[str, int, float] | None = None   # (text, level, age s)
    objectives: list["ObjectiveState"] = field(default_factory=list)
    game_time: float | None = None
    warn_radius: float = 0.22
    danger_radius: float = 0.12
    flash: float = 0.0                        # 0..1 danger flash intensity
    jungler_line: str | None = None
    hint: str | None = None
    me_icon: np.ndarray | None = None         # my champion icon (RGBA), optional


# ======================================================================================
# Small helpers
# ======================================================================================
def _rgb(c: Sequence[float]) -> np.ndarray:
    return np.asarray(c[:3], dtype=np.float32) / 255.0


def _mix(a: Sequence[float], b: Sequence[float], t: float) -> tuple[float, float, float]:
    return tuple(float(a[i]) * (1.0 - t) + float(b[i]) * t for i in range(3))  # type: ignore[return-value]


def _finite(*vals: Any) -> bool:
    try:
        return all(math.isfinite(float(v)) for v in vals)
    except (TypeError, ValueError):
        return False


def _clamp01(x: Any) -> float:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(f):
        return 0.0
    return 0.0 if f < 0 else 1.0 if f > 1 else f


def _uv_ok(uv: Any) -> tuple[float, float] | None:
    try:
        u, v = float(uv[0]), float(uv[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    if not (math.isfinite(u) and math.isfinite(v)):
        return None
    return min(max(u, 0.0), 1.0), min(max(v, 0.0), 1.0)


def fmt_clock(seconds: float | None) -> str:
    """``m:ss`` (``h:mm:ss`` over an hour); ``--:--`` when unknown."""
    if seconds is None or not _finite(seconds):
        return "--:--"
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def fmt_seconds(seconds: float | None) -> str:
    """``23 s`` below a minute, ``1:05`` above."""
    if seconds is None or not _finite(seconds):
        return "?"
    s = max(0, int(seconds))
    return f"{s} s" if s < 60 else fmt_clock(s)


class _LRU:
    """Tiny thread-safe LRU cache."""

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._d: OrderedDict[Any, Any] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Any) -> Any:
        with self._lock:
            val = self._d.get(key)
            if val is not None:
                self._d.move_to_end(key)
            return val

    def put(self, key: Any, val: Any) -> None:
        with self._lock:
            self._d[key] = val
            self._d.move_to_end(key)
            while len(self._d) > self.capacity:
                self._d.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


# ======================================================================================
# Fonts
# ======================================================================================
def _windows_font_dir() -> Path:
    return Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


_FONT_FILES: dict[str, tuple[str, ...]] = {
    "regular": ("segoeui.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf", "arial.ttf"),
    "semibold": ("seguisb.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf",
                 "arialbd.ttf"),
    "bold": ("segoeuib.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "arialbd.ttf"),
}
_FONT_DIRS: tuple[Path, ...] = (
    _windows_font_dir(),
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/dejavu"),
    Path("/usr/share/fonts/TTF"),
    Path("/usr/share/fonts/truetype/liberation"),
    Path("/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
)
_font_cache: dict[tuple[str, int], Any] = {}
_font_lock = threading.Lock()
_font_warned = False


def get_font(size: int, weight: str = "regular") -> Any:
    """Cached PIL font (``weight`` = "regular" | "semibold" | "bold"). Never raises."""
    global _font_warned
    size = int(min(max(int(size), 6), 200))
    weight = weight if weight in _FONT_FILES else "regular"
    key = (weight, size)
    with _font_lock:
        cached = _font_cache.get(key)
        if cached is not None:
            return cached
        font = None
        for name in _FONT_FILES[weight]:
            candidates = [d / name for d in _FONT_DIRS] + [Path(name)]
            for cand in candidates:
                try:
                    if cand.is_absolute() and not cand.is_file():
                        continue
                    font = ImageFont.truetype(str(cand), size)
                    break
                except (OSError, ValueError):
                    continue
            if font is not None:
                break
        if font is None:
            if not _font_warned:
                _font_warned = True
                log.warning("No TrueType font found; using PIL's default font")
            try:
                font = ImageFont.load_default(size=size)
            except TypeError:  # Pillow < 10.1
                font = ImageFont.load_default()
        _font_cache[key] = font
        return font


def _cap_height(font: Any) -> float:
    try:
        bb = font.getbbox("H", anchor="ls")
        return float(-bb[1])
    except Exception:
        return float(getattr(font, "size", 10)) * 0.7


def text_width(text: str, font: Any) -> float:
    """Advance width of ``text`` in pixels."""
    try:
        return float(font.getlength(text))
    except Exception:
        try:
            bb = font.getbbox(text)
            return float(bb[2] - bb[0])
        except Exception:
            return 7.0 * len(text)


def fit_text(text: str, font: Any, max_w: float) -> str:
    """``text`` shortened with an ellipsis so that it fits in ``max_w`` pixels."""
    if text_width(text, font) <= max_w:
        return text
    ell = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if text_width(text[:mid].rstrip() + ell, font) <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return (text[:lo].rstrip(" ,;:—-") + ell) if lo > 0 else ell


def wrap_text(text: str, font: Any, max_w: float, max_lines: int = 2) -> list[str]:
    """Greedy word wrap into at most ``max_lines`` lines (the last one ellipsized)."""
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    cur = ""
    for i, w in enumerate(words):
        cand = f"{cur} {w}".strip()
        if text_width(cand, font) <= max_w or not cur:
            cur = cand
            continue
        lines.append(cur)
        cur = w
        if len(lines) == max_lines - 1:
            cur = " ".join(words[i:])
            break
    lines.append(cur)
    return [fit_text(line, font, max_w) for line in lines[:max_lines]]


_text_cache = _LRU(768)


def _text_mask(text: str, font: Any, stroke: int = 0) -> tuple[np.ndarray, int, int]:
    """Coverage mask (float32 0..1) of ``text`` + offset of its top-left from the baseline origin."""
    key = (text, id(font), stroke)
    hit = _text_cache.get(key)
    if hit is not None:
        return hit
    try:
        l, t, r, b = font.getbbox(text, anchor="ls", stroke_width=stroke)
    except TypeError:
        l, t, r, b = font.getbbox(text)
    w, h = max(1, int(r - l) + 2), max(1, int(b - t) + 2)
    img = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(img)
    try:
        draw.text((-l + 1, -t + 1), text, font=font, fill=255, anchor="ls",
                  stroke_width=stroke, stroke_fill=255)
    except TypeError:
        draw.text((-l + 1, -t + 1), text, font=font, fill=255)
    mask = np.asarray(img, dtype=np.float32) * (1.0 / 255.0)
    mask.setflags(write=False)
    res = (mask, int(l) - 1, int(t) - 1)
    _text_cache.put(key, res)
    return res


# ======================================================================================
# Canvas with analytic anti-aliasing (premultiplied RGBA float32)
# ======================================================================================
def _span(c0: float, c1: float, limit: int) -> tuple[int, int]:
    return max(0, int(math.floor(c0)) - 1), min(limit, int(math.ceil(c1)) + 1)


class Canvas:
    """Premultiplied RGBA float32 drawing surface (origin top-left, pixel centres at +0.5)."""

    def __init__(self, width: int, height: int) -> None:
        self.w = int(min(max(1, width), _MAX_SIDE))
        self.h = int(min(max(1, height), _MAX_SIDE))
        self.px = np.zeros((self.h, self.w, 4), np.float32)

    # ------------------------------------------------------------------ compositing
    def paint(self, x0: int, y0: int, cov: np.ndarray, rgb: Any, alpha: float = 1.0) -> None:
        """Composite colour ``rgb`` (0..255 tuple or float array 0..1) with coverage ``cov`` at (x0, y0)."""
        if alpha <= 0.0 or cov.size == 0:
            return
        h, w = cov.shape[:2]
        X0, Y0, X1, Y1 = max(x0, 0), max(y0, 0), min(x0 + w, self.w), min(y0 + h, self.h)
        if X0 >= X1 or Y0 >= Y1:
            return
        sy, sx = slice(Y0 - y0, Y1 - y0), slice(X0 - x0, X1 - x0)
        a = cov[sy, sx] * np.float32(alpha)
        if isinstance(rgb, np.ndarray) and rgb.ndim == 3:
            col = rgb[sy, sx if rgb.shape[1] > 1 else slice(None)]
        else:
            col = rgb if isinstance(rgb, np.ndarray) else _rgb(rgb)
        dst = self.px[Y0:Y1, X0:X1]
        inv = 1.0 - a
        dst[..., :3] *= inv[..., None]
        dst[..., :3] += col * a[..., None]
        dst[..., 3] *= inv
        dst[..., 3] += a

    def paint_premul(self, x0: int, y0: int, patch: np.ndarray, opacity: float = 1.0) -> None:
        """Composite a premultiplied RGBA float32 patch with its top-left at (x0, y0)."""
        if opacity <= 0.0:
            return
        h, w = patch.shape[:2]
        X0, Y0, X1, Y1 = max(x0, 0), max(y0, 0), min(x0 + w, self.w), min(y0 + h, self.h)
        if X0 >= X1 or Y0 >= Y1:
            return
        src = patch[Y0 - y0:Y1 - y0, X0 - x0:X1 - x0]
        if opacity < 1.0:
            src = src * np.float32(opacity)
        dst = self.px[Y0:Y1, X0:X1]
        dst *= (1.0 - src[..., 3:4])
        dst += src

    # ------------------------------------------------------------------ primitives
    def _grid(self, x0: float, y0: float, x1: float, y1: float
              ) -> tuple[int, int, np.ndarray, np.ndarray] | None:
        X0, X1 = _span(x0, x1, self.w)
        Y0, Y1 = _span(y0, y1, self.h)
        if X0 >= X1 or Y0 >= Y1:
            return None
        xs = np.arange(X0, X1, dtype=np.float32) + np.float32(0.5)
        ys = np.arange(Y0, Y1, dtype=np.float32) + np.float32(0.5)
        return X0, Y0, xs[None, :], ys[:, None]

    def disc(self, cx: float, cy: float, r: float, rgb: Any, alpha: float = 1.0) -> None:
        """Filled anti-aliased disc."""
        if not _finite(cx, cy, r) or r <= 0:
            return
        g = self._grid(cx - r, cy - r, cx + r, cy + r)
        if g is None:
            return
        X0, Y0, xs, ys = g
        d = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
        self.paint(X0, Y0, np.clip(r - d + 0.5, 0.0, 1.0), rgb, alpha)

    def glow(self, cx: float, cy: float, r0: float, r1: float, rgb: Any, alpha: float = 1.0) -> None:
        """Soft radial glow: full ``alpha`` inside ``r0`` fading (quadratically) to 0 at ``r1``."""
        if not _finite(cx, cy, r0, r1) or r1 <= 0:
            return
        g = self._grid(cx - r1, cy - r1, cx + r1, cy + r1)
        if g is None:
            return
        X0, Y0, xs, ys = g
        d = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
        t = np.clip((r1 - d) / max(1e-3, r1 - r0), 0.0, 1.0)
        self.paint(X0, Y0, t * t, rgb, alpha)

    def ring(self, cx: float, cy: float, r: float, width: float, rgb: Any, alpha: float = 1.0,
             dash: tuple[float, float] | None = None, phase: float = 0.0) -> None:
        """Anti-aliased circle outline (optionally dashed: ``dash = (on_px, off_px)``)."""
        if not _finite(cx, cy, r, width) or r <= 0 or width <= 0:
            return
        ext = r + width / 2 + 1
        g = self._grid(cx - ext, cy - ext, cx + ext, cy + ext)
        if g is None:
            return
        X0, Y0, xs, ys = g
        dx, dy = xs - cx, ys - cy
        d = np.sqrt(dx * dx + dy * dy)
        cov = np.clip(width * 0.5 - np.abs(d - r) + 0.5, 0.0, 1.0)
        if width < 1.0:
            cov *= width
        if dash is not None:
            on, off = float(dash[0]), float(dash[1])
            circ = 2.0 * math.pi * r
            n = max(3, int(round(circ / max(1.0, on + off))))
            period = circ / n
            on = period * on / (on + off)
            s = (np.arctan2(dy, dx) + math.pi) * r + phase * period
            m = np.mod(s, period)
            cov *= np.clip(np.minimum(m, on - m) + 0.5, 0.0, 1.0)
        self.paint(X0, Y0, cov, rgb, alpha)

    def rrect(self, x: float, y: float, w: float, h: float, radius: float, rgb: Any,
              alpha: float = 1.0, border: Any = None, border_alpha: float = 1.0,
              border_w: float = 1.0) -> None:
        """Rounded rectangle, filled with ``rgb`` (tuple or vertical gradient array) + optional inner border."""
        if w <= 0 or h <= 0 or not _finite(x, y, w, h, radius):
            return
        g = self._grid(x, y, x + w, y + h)
        if g is None:
            return
        X0, Y0, xs, ys = g
        rad = float(min(max(radius, 0.0), w / 2, h / 2))
        qx = np.abs(xs - (x + w / 2)) - (w / 2 - rad)
        qy = np.abs(ys - (y + h / 2)) - (h / 2 - rad)
        sdf = (np.sqrt(np.maximum(qx, 0) ** 2 + np.maximum(qy, 0) ** 2)
               + np.minimum(np.maximum(qx, qy), 0) - rad)
        fill = np.clip(0.5 - sdf, 0.0, 1.0)
        if rgb is not None:
            col = rgb
            if isinstance(rgb, np.ndarray) and rgb.ndim == 3 and rgb.shape[0] != fill.shape[0]:
                # gradient given for [y, y+h): resample to the grid rows
                rows = np.clip(((ys[:, 0] - y) / max(h, 1e-3) * rgb.shape[0]).astype(int), 0, rgb.shape[0] - 1)
                col = rgb[rows]
            self.paint(X0, Y0, fill, col, alpha)
        if border is not None and border_alpha > 0 and border_w > 0:
            inner = np.clip(0.5 - (sdf + border_w), 0.0, 1.0)
            self.paint(X0, Y0, fill - inner, border, border_alpha)

    def capsule(self, x0: float, y0: float, x1: float, y1: float, width: float, rgb: Any,
                alpha: float = 1.0) -> None:
        """Thick anti-aliased segment with round caps."""
        if not _finite(x0, y0, x1, y1, width) or width <= 0:
            return
        ext = width / 2 + 1
        g = self._grid(min(x0, x1) - ext, min(y0, y1) - ext, max(x0, x1) + ext, max(y0, y1) + ext)
        if g is None:
            return
        X0, Y0, xs, ys = g
        vx, vy = x1 - x0, y1 - y0
        ll = vx * vx + vy * vy
        px, py = xs - x0, ys - y0
        if ll < 1e-9:
            d = np.sqrt(px * px + py * py)
        else:
            tt = np.clip((px * vx + py * vy) / ll, 0.0, 1.0)
            d = np.sqrt((px - tt * vx) ** 2 + (py - tt * vy) ** 2)
        cov = np.clip(width * 0.5 - d + 0.5, 0.0, 1.0)
        self.paint(X0, Y0, cov, rgb, alpha)

    def polygon(self, pts: Sequence[tuple[float, float]], rgb: Any, alpha: float = 1.0,
                grow: float = 0.0) -> None:
        """Convex polygon (any winding), optionally grown by ``grow`` px (outline halo)."""
        if len(pts) < 3:
            return
        p = np.asarray(pts, dtype=np.float32)
        if not np.all(np.isfinite(p)):
            return
        area = 0.0
        for i in range(len(p)):
            x_a, y_a = p[i]
            x_b, y_b = p[(i + 1) % len(p)]
            area += float(x_a * y_b - x_b * y_a)
        if abs(area) < 1e-6:
            return
        sign = 1.0 if area > 0 else -1.0
        ext = grow + 1
        g = self._grid(p[:, 0].min() - ext, p[:, 1].min() - ext, p[:, 0].max() + ext, p[:, 1].max() + ext)
        if g is None:
            return
        X0, Y0, xs, ys = g
        sdf = None
        for i in range(len(p)):
            ax, ay = p[i]
            bx, by = p[(i + 1) % len(p)]
            ex, ey = bx - ax, by - ay
            ln = math.hypot(ex, ey)
            if ln < 1e-6:
                continue
            # outward normal for this winding
            nx, ny = sign * ey / ln, -sign * ex / ln
            dist = (xs - ax) * nx + (ys - ay) * ny
            sdf = dist if sdf is None else np.maximum(sdf, dist)
        if sdf is None:
            return
        self.paint(X0, Y0, np.clip(0.5 - (sdf - grow), 0.0, 1.0), rgb, alpha)

    def text(self, x: float, y: float, text: str, font: Any, rgb: Any, alpha: float = 1.0,
             anchor: str = "l", shadow: float = 0.55, outline: int = 0,
             outline_rgb: Any = BLACK, outline_alpha: float = 0.75) -> float:
        """Draw one line of text vertically centred (cap height) on ``y``; returns its width.

        ``anchor``: "l" (x = left), "m" (x = centre) or "r" (x = right).
        """
        if not text or alpha <= 0:
            return 0.0
        tw = text_width(text, font)
        if anchor == "m":
            ox = x - tw / 2
        elif anchor == "r":
            ox = x - tw
        else:
            ox = x
        base = y + _cap_height(font) / 2
        bx, by = int(round(ox)), int(round(base))
        if outline > 0:
            m, dx, dy = _text_mask(text, font, outline)
            self.paint(bx + dx, by + dy, m, outline_rgb, alpha * outline_alpha)
        m, dx, dy = _text_mask(text, font, 0)
        if shadow > 0 and outline <= 0:
            self.paint(bx + dx, by + dy + 1, m, BLACK, alpha * shadow)
        self.paint(bx + dx, by + dy, m, rgb, alpha)
        return tw

    def image(self, cx: float, cy: float, patch: np.ndarray, opacity: float = 1.0) -> None:
        """Composite a premultiplied patch centred on (cx, cy)."""
        h, w = patch.shape[:2]
        self.paint_premul(int(round(cx - w / 2)), int(round(cy - h / 2)), patch, opacity)

    # ------------------------------------------------------------------ output
    def to_bgra(self) -> np.ndarray:
        """Premultiplied BGRA uint8."""
        out = np.empty((self.h, self.w, 4), np.uint8)
        px = np.clip(self.px, 0.0, 1.0) * np.float32(255.0)
        px += np.float32(0.5)
        out[..., 0] = px[..., 2]
        out[..., 1] = px[..., 1]
        out[..., 2] = px[..., 0]
        out[..., 3] = px[..., 3]
        return out


# ======================================================================================
# Icons
# ======================================================================================
_icon_cache = _LRU(160)
_asset_icon_cache: dict[str, np.ndarray | None] = {}
_asset_lock = threading.Lock()


def _icon_fingerprint(icon: np.ndarray) -> tuple:
    try:
        return (icon.shape, hash(np.ascontiguousarray(icon).tobytes()))
    except Exception:
        return (id(icon),)


def _as_rgba_icon(icon: Any) -> np.ndarray | None:
    try:
        arr = np.asarray(icon)
    except Exception:
        return None
    if arr.ndim != 3 or arr.shape[0] < 4 or arr.shape[1] < 4 or arr.dtype != np.uint8:
        return None
    if arr.shape[2] == 4:
        return arr
    if arr.shape[2] == 3:
        return np.dstack([arr, np.full(arr.shape[:2], 255, np.uint8)])
    return None


def round_icon_patch(icon: np.ndarray | None, diameter: float, ring_rgb: Sequence[int] | None,
                     ring_w: float = 2.0, grey: bool = False, fallback_rgb: Sequence[int] = GREY,
                     letter: str = "") -> np.ndarray:
    """Round champion portrait with a coloured ring as a premultiplied RGBA float32 patch (cached).

    ``icon`` is RGBA uint8 (None -> a plain disc with ``letter``). ``grey`` desaturates and
    darkens the portrait (champion not visible).
    """
    diameter = float(min(max(diameter, 6.0), 256.0))
    rgba = _as_rgba_icon(icon) if icon is not None else None
    fp = _icon_fingerprint(rgba) if rgba is not None else ("none", letter)
    key = (fp, round(diameter * 4), tuple(ring_rgb) if ring_rgb else None, round(ring_w * 4), grey,
           tuple(fallback_rgb))
    hit = _icon_cache.get(key)
    if hit is not None:
        return hit
    size = int(math.ceil(diameter)) + 2
    c = size / 2.0
    r = diameter / 2.0
    cv_ = Canvas(size, size)
    rw = ring_w if ring_rgb is not None else 0.0
    r_in = r - rw - (0.6 if rw > 0 else 0.0)
    # dark base (shows through the separator line)
    cv_.disc(c, c, r, PANEL_DEEP, 1.0)
    if rgba is not None and r_in > 1:
        d_in = max(2, int(round(2 * r_in)))
        h0, w0 = rgba.shape[:2]
        crop = 0.06   # drop the icon's own outer border
        y0, y1 = int(h0 * crop), int(round(h0 * (1 - crop)))
        x0, x1 = int(w0 * crop), int(round(w0 * (1 - crop)))
        src = rgba[y0:y1, x0:x1]
        interp = cv2.INTER_AREA if src.shape[0] > d_in else cv2.INTER_CUBIC
        img = cv2.resize(src, (d_in, d_in), interpolation=interp).astype(np.float32) / 255.0
        col = img[..., :3]
        a = img[..., 3]
        if grey:
            lum = col @ np.array([0.299, 0.587, 0.114], np.float32)
            col = np.repeat((lum * 0.62 + 0.04)[..., None], 3, axis=2)
            col = col * np.array([0.95, 0.98, 1.08], np.float32)
        # bake alpha over the dark base colour
        base = _rgb(PANEL_DEEP)
        col = col * a[..., None] + base * (1 - a[..., None])
        off = (size - d_in) / 2.0
        xs = np.arange(d_in, dtype=np.float32) + 0.5 + off - c
        dmap = np.sqrt(xs[None, :] ** 2 + xs[:, None] ** 2)
        cov = np.clip(r_in - dmap + 0.5, 0.0, 1.0)
        io = int(round(off))
        cv_.paint(io, io, cov, np.clip(col, 0, 1), 1.0)
    elif r_in > 1:
        cv_.disc(c, c, r_in, fallback_rgb, 1.0)
        if letter:
            f = get_font(max(7, int(r_in * 1.1)), "bold")
            cv_.text(c, c, letter[:1].upper(), f, GOLD_LIGHT, 1.0, anchor="m", shadow=0)
    if rw > 0 and ring_rgb is not None:
        ring_col = _mix(ring_rgb, GREY, 0.55) if grey else ring_rgb
        cv_.ring(c, c, r - rw / 2, rw, ring_col, 1.0)
    patch = cv_.px
    patch.setflags(write=False)
    _icon_cache.put(key, patch)
    return patch


def load_asset_icon(name: str) -> np.ndarray | None:
    """RGBA uint8 icon from ``assets/icons/minimap`` (cached, None if missing)."""
    with _asset_lock:
        if name in _asset_icon_cache:
            return _asset_icon_cache[name]
    img: np.ndarray | None = None
    try:
        from treeaicoach import paths

        p = paths.asset_path("icons", "minimap", name)
        data = np.fromfile(str(p), dtype=np.uint8)
        raw = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
        if raw is not None and raw.ndim == 3 and raw.shape[2] == 4:
            img = cv2.cvtColor(raw, cv2.COLOR_BGRA2RGBA)
        elif raw is not None and raw.ndim == 3:
            img = cv2.cvtColor(raw, cv2.COLOR_BGR2RGBA)
    except Exception as exc:
        log.debug("Cannot load icon %s: %s", name, exc)
    if img is not None:
        img.setflags(write=False)
    with _asset_lock:
        _asset_icon_cache[name] = img
    return img


def sprite_patch(rgba: np.ndarray, size: float, opacity: float = 1.0, grey: bool = False) -> np.ndarray:
    """Plain RGBA sprite resized to ``size`` px as a premultiplied float32 patch (cached)."""
    key = ("sprite", _icon_fingerprint(rgba), round(size * 2), round(opacity * 100), grey)
    hit = _icon_cache.get(key)
    if hit is not None:
        return hit
    s = max(2, int(round(size)))
    img = cv2.resize(rgba, (s, s), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    col, a = img[..., :3], img[..., 3:4] * np.float32(opacity)
    if grey:
        lum = col @ np.array([0.299, 0.587, 0.114], np.float32)
        col = np.repeat(lum[..., None] * 0.7, 3, axis=2)
    patch = np.concatenate([col * a, a], axis=2).astype(np.float32)
    patch.setflags(write=False)
    _icon_cache.put(key, patch)
    return patch


def objective_icon(name: str) -> np.ndarray | None:
    """Minimap icon for an objective display name ("Dragon", "Baron", "Héraut"...)."""
    fname = OBJECTIVE_ICONS.get(str(name or "").strip().lower())
    return load_asset_icon(fname) if fname else None


# ======================================================================================
# Radar
# ======================================================================================
_radar_bg_cache = _LRU(6)
_region_cache = _LRU(24)
_default_texture: np.ndarray | None = None
_default_texture_lock = threading.Lock()


def default_radar_texture() -> np.ndarray:
    """The official minimap texture as BGR (transparent parts = near-black walls). Cached."""
    global _default_texture
    with _default_texture_lock:
        if _default_texture is not None:
            return _default_texture
        img = None
        try:
            from treeaicoach.fog_tracker import load_default_texture

            bgra = load_default_texture()
            if bgra is not None:
                a = bgra[..., 3:4].astype(np.float32) / 255.0
                void = np.array([10, 7, 3], np.float32)
                img = (bgra[..., :3].astype(np.float32) * a + void * (1 - a)).astype(np.uint8)
        except Exception as exc:
            log.warning("Cannot load the radar texture: %s", exc)
        if img is None:
            img = np.full((512, 512, 3), (40, 34, 22), np.uint8)
        img.setflags(write=False)
        _default_texture = img
        return img


def _radar_background(texture_bgr: np.ndarray, size: int) -> np.ndarray:
    """Darkened, slightly desaturated map inside a rounded panel (premultiplied RGBA, cached)."""
    key = (_icon_fingerprint(texture_bgr[::8, ::8]), texture_bgr.shape, size)
    hit = _radar_bg_cache.get(key)
    if hit is not None:
        return hit
    tex = np.asarray(texture_bgr)
    if tex.ndim == 2:
        tex = cv2.cvtColor(tex, cv2.COLOR_GRAY2BGR)
    elif tex.shape[2] == 4:
        a = tex[..., 3:4].astype(np.float32) / 255.0
        tex = (tex[..., :3].astype(np.float32) * a + np.array([10, 7, 3], np.float32) * (1 - a)).astype(np.uint8)
    border = max(2, int(round(size * 0.012)))
    inner = size - 2 * border
    img = cv2.resize(tex[..., :3], (inner, inner), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    rgb = img[..., ::-1]
    lum = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
    rgb = rgb * 0.62 + lum[..., None] * 0.38          # desaturate
    rgb = rgb * 0.60                                   # darken so overlays pop
    rgb = rgb * np.array([0.92, 0.98, 1.10], np.float32) + np.array([0.0, 0.01, 0.025], np.float32)
    # soft vignette
    yy, xx = np.mgrid[0:inner, 0:inner].astype(np.float32)
    r = np.sqrt((xx / inner - 0.5) ** 2 + (yy / inner - 0.5) ** 2) / 0.7071
    rgb = rgb * (1.0 - 0.35 * r[..., None] ** 2)
    cv_ = Canvas(size, size)
    radius = size * 0.045
    cv_.rrect(0, 0, size, size, radius, PANEL, 0.94)
    # map, clipped to an inner rounded rect
    gx = np.arange(inner, dtype=np.float32) + 0.5
    qx = np.abs(gx[None, :] - inner / 2) - (inner / 2 - radius * 0.8)
    qy = np.abs(gx[:, None] - inner / 2) - (inner / 2 - radius * 0.8)
    sdf = (np.sqrt(np.maximum(qx, 0) ** 2 + np.maximum(qy, 0) ** 2)
           + np.minimum(np.maximum(qx, qy), 0) - radius * 0.8)
    cov = np.clip(0.5 - sdf, 0.0, 1.0)
    cv_.paint(border, border, cov, np.clip(rgb, 0, 1), 1.0)
    patch = cv_.px
    patch.setflags(write=False)
    _radar_bg_cache.put(key, patch)
    return patch


def _region_layers(region: np.ndarray, size: int) -> tuple[np.ndarray, np.ndarray] | None:
    """(fill coverage, contour coverage) of a fog region upsampled to ``size`` (cached)."""
    key = (id(region), region.shape, size)
    hit = _region_cache.get(key)
    if hit is not None and hit[0] is region:
        return hit[1], hit[2]
    m = region.astype(np.float32)
    if not m.any():
        return None
    m = cv2.GaussianBlur(m, (0, 0), 0.8)
    up = cv2.resize(m, (size, size), interpolation=cv2.INTER_LINEAR)
    cell = size / float(region.shape[0])
    k = max(1.0, cell * 0.9)
    fill = np.clip((up - 0.42) * k + 0.5, 0.0, 1.0)
    # contour: distance to the iso-line in pixels ~ |up - 0.42| / |grad|
    gx = cv2.Sobel(up, cv2.CV_32F, 1, 0, ksize=3) * 0.125
    gy = cv2.Sobel(up, cv2.CV_32F, 0, 1, ksize=3) * 0.125
    grad = np.sqrt(gx * gx + gy * gy) + 1e-4
    dist = np.abs(up - 0.42) / grad
    edge = np.clip(1.6 * 0.5 - dist + 0.5, 0.0, 1.0)
    edge[grad < 0.004] = 0.0
    _region_cache.put(key, (region, fill, edge))
    return fill, edge


def _label_pill(cv_: Canvas, cx: float, cy: float, text: str, font: Any, fg: Any, border: Any,
                alpha: float = 1.0, bg_alpha: float = 0.88) -> tuple[float, float]:
    """Small rounded label centred on (cx, cy); returns its (w, h)."""
    tw = text_width(text, font)
    h = _cap_height(font) + 8
    w = tw + 10
    x = min(max(cx - w / 2, 1), cv_.w - w - 1)
    y = min(max(cy - h / 2, 1), cv_.h - h - 1)
    cv_.rrect(x, y, w, h, h / 2, PANEL_DEEP, bg_alpha * alpha, border=border, border_alpha=0.9 * alpha)
    cv_.text(x + w / 2, y + h / 2, text, font, fg, alpha, anchor="m", shadow=0)
    return w, h


def _arrow(cv_: Canvas, x0: float, y0: float, x1: float, y1: float, width: float, rgb: Any,
           alpha: float = 1.0) -> None:
    """Arrow from (x0, y0) to (x1, y1) with a dark outline."""
    L = math.hypot(x1 - x0, y1 - y0)
    if L < 3:
        return
    ux, uy = (x1 - x0) / L, (y1 - y0) / L
    head = min(L * 0.55, width * 3.2 + 3)
    hw = width * 1.9 + 1.5
    bx, by = x1 - ux * head, y1 - uy * head
    tri = [(x1, y1), (bx - uy * hw, by + ux * hw), (bx + uy * hw, by - ux * hw)]
    cv_.capsule(x0, y0, bx + ux * 1.0, by + uy * 1.0, width + 2.4, BLACK, 0.55 * alpha)
    cv_.polygon(tri, BLACK, 0.55 * alpha, grow=1.2)
    cv_.capsule(x0, y0, bx + ux * 1.0, by + uy * 1.0, width, rgb, alpha)
    cv_.polygon(tri, rgb, alpha)


def _enemy_by_key(state: OverlayState) -> dict[str, EnemyView]:
    out: dict[str, EnemyView] = {}
    for e in state.enemies or []:
        if e is not None and getattr(e, "key", None):
            out[e.key] = e
    return out


def render_radar(state: OverlayState, size: int, texture_bgr: np.ndarray | None = None,
                 now: float | None = None) -> np.ndarray:
    """Radar (enlarged minimap copy with threats) as premultiplied BGRA ``size x size``.

    Never raises: on an internal error an empty (transparent) image is returned and logged.
    ``now`` (s) drives the pulse animations (default ``time.monotonic()``).
    """
    size = int(min(max(int(size) if _finite(size) else 256, 64), 2048))
    try:
        return _render_radar(state, size, texture_bgr, time.monotonic() if now is None else float(now))
    except Exception:
        log.exception("render_radar failed")
        return np.zeros((size, size, 4), np.uint8)


def _render_radar(state: OverlayState, size: int, texture_bgr: np.ndarray | None, now: float) -> np.ndarray:
    tex = texture_bgr if isinstance(texture_bgr, np.ndarray) and texture_bgr.ndim >= 2 \
        and min(texture_bgr.shape[:2]) >= 8 else default_radar_texture()
    cv_ = Canvas(size, size)
    cv_.paint_premul(0, 0, _radar_background(tex, size))
    S = float(size)
    icon_d = max(18.0, S * 0.082)
    ring_w = max(1.6, icon_d * 0.09)
    f_tiny = get_font(max(8, int(S * 0.032)), "bold")
    phase = (now % HALO_PERIOD_S) / HALO_PERIOD_S
    enemies = _enemy_by_key(state)
    me = _uv_ok(state.me_uv) if state.me_uv is not None else None

    # ---- fog regions (reachable zones of hidden enemies), most confident on top
    fogs = [f for f in (state.fogs or []) if f is not None]
    fog_keys = {f.key for f in fogs}
    for fog in sorted(fogs, key=lambda f: f.confidence):
        _draw_fog(cv_, fog, S, phase)

    # ---- warn / danger radius around me
    if me is not None:
        mx, my = me[0] * S, me[1] * S
        warn_r = max(0.0, float(state.warn_radius)) * S if _finite(state.warn_radius) else 0.0
        dang_r = max(0.0, float(state.danger_radius)) * S if _finite(state.danger_radius) else 0.0
        if warn_r > 2:
            cv_.glow(mx, my, warn_r * 0.0, warn_r, WARNING, 0.06)
            cv_.ring(mx, my, warn_r, max(1.2, S * 0.005), WARNING, 0.75,
                     dash=(max(4.0, S * 0.025), max(3.0, S * 0.015)), phase=now * 0.15)
        if dang_r > 2:
            lvl = int(state.threat_level or 0)
            cv_.disc(mx, my, dang_r, DANGER, 0.10 if lvl < 2 else 0.16 + 0.06 * math.sin(phase * 2 * math.pi))
            cv_.ring(mx, my, dang_r, max(1.5, S * 0.006), DANGER, 0.85)

    # ---- last known positions of invisible enemies (without a fog estimate)
    for e in enemies.values():
        if e.visible or e.key in fog_keys:
            continue
        uv = _uv_ok(e.uv) if e.uv is not None else None
        ago = e.last_seen_ago
        if uv is None or ago is None or not _finite(ago) or ago > LAST_SEEN_MAX_S:
            continue
        fade = 1.0 - 0.55 * _clamp01(ago / LAST_SEEN_MAX_S)
        x, y = uv[0] * S, uv[1] * S
        patch = round_icon_patch(e.icon, icon_d * 0.9, DANGER, ring_w, grey=True, letter=e.name or e.alias or "?")
        cv_.image(x, y, patch, 0.9 * fade)
        br = icon_d * 0.2
        bx, by = x + icon_d * 0.36, y - icon_d * 0.36
        cv_.disc(bx, by, br + 1, PANEL_DEEP, 0.9 * fade)
        cv_.disc(bx, by, br, WARNING, fade)
        cv_.text(bx, by, "?", get_font(max(7, int(br * 1.6)), "bold"), PANEL_DEEP, fade, anchor="m", shadow=0)
        _label_pill(cv_, x, y + icon_d * 0.45 + _cap_height(f_tiny) / 2 + 5, fmt_seconds(ago), f_tiny,
                    GOLD_LIGHT, WARNING, alpha=fade)

    # ---- last seen point of each fog estimate: greyed icon + elapsed time
    for fog in sorted(fogs, key=lambda f: f.confidence):
        ev = enemies.get(fog.key)
        _draw_fog_label(cv_, fog, ev.icon if ev is not None else None, S)

    # ---- me
    if me is not None:
        mx, my = me[0] * S, me[1] * S
        cv_.glow(mx, my, icon_d * 0.5, icon_d * 0.95, TEAL, 0.35)
        if state.me_icon is not None:
            cv_.image(mx, my, round_icon_patch(state.me_icon, icon_d, TEAL, ring_w))
        else:
            cv_.disc(mx, my, icon_d * 0.28, WHITE, 1.0)
            cv_.disc(mx, my, icon_d * 0.22, TEAL, 1.0)

    # ---- visible enemies (jungler halo, approach arrows)
    visible = [e for e in enemies.values() if e.visible and e.uv is not None and _uv_ok(e.uv) is not None]
    visible.sort(key=lambda e: (e.is_jungler, e.approaching))
    for e in visible:
        uv = _uv_ok(e.uv)
        assert uv is not None
        x, y = uv[0] * S, uv[1] * S
        if e.is_jungler:
            pr = icon_d * (0.55 + 0.45 * phase)
            cv_.glow(x, y, icon_d * 0.45, icon_d * 0.85, DANGER, 0.45)
            cv_.ring(x, y, pr, max(1.5, icon_d * 0.1), DANGER, 0.9 * (1 - phase) ** 1.4)
        if e.approaching:
            vx, vy = (e.velocity if e.velocity is not None and _finite(*e.velocity) else (0.0, 0.0))
            if math.hypot(vx, vy) < 1e-4 and me is not None:
                vx, vy = me[0] - uv[0], me[1] - uv[1]
            n = math.hypot(vx, vy)
            if n > 1e-6:
                ux, uy = vx / n, vy / n
                speed_len = (n * S * 1.6) if e.velocity is not None else 0.0
                L = min(max(speed_len, icon_d * 0.8), icon_d * 1.6)
                sx, sy = x + ux * icon_d * 0.58, y + uy * icon_d * 0.58
                _arrow(cv_, sx, sy, sx + ux * L, sy + uy * L, max(2.0, icon_d * 0.11), DANGER, 0.95)
        cv_.image(x, y, round_icon_patch(e.icon, icon_d, DANGER, ring_w, letter=e.name or e.alias or "?"))

    # ---- frame (colour follows the threat level)
    lvl = int(state.threat_level or 0) if _finite(state.threat_level or 0) else 0
    radius = S * 0.045
    if lvl >= 2:
        pulse = 0.55 + 0.45 * math.sin(phase * 2 * math.pi)
        cv_.rrect(0.5, 0.5, S - 1, S - 1, radius, None, border=DANGER, border_alpha=0.9, border_w=2.5)
        cv_.rrect(2.5, 2.5, S - 5, S - 5, radius - 2, None, border=DANGER, border_alpha=0.35 * pulse,
                  border_w=3.0)
    elif lvl == 1:
        cv_.rrect(0.5, 0.5, S - 1, S - 1, radius, None, border=WARNING, border_alpha=0.85, border_w=1.8)
    else:
        cv_.rrect(0, 0, S, S, radius, None, border=GOLD, border_alpha=BORDER_ALPHA, border_w=1.2)
    return cv_.to_bgra()


def _draw_fog(cv_: Canvas, fog: FogEstimate, S: float, phase: float) -> None:
    """Reachable region + dashed bound circle + greyed icon and timer at the last seen point."""
    uv = _uv_ok(fog.last_uv)
    if uv is None:
        return
    conf = _clamp01(fog.confidence)
    vis = 0.35 + 0.65 * math.sqrt(conf) if conf > 0 else 0.0
    x, y = uv[0] * S, uv[1] * S
    region = fog.region
    if vis > 0 and isinstance(region, np.ndarray) and region.ndim == 2 and region.shape[0] >= 4:
        layers = _region_layers(region, int(S))
        if layers is not None:
            fill, edge = layers
            cv_.paint(0, 0, fill, DANGER, 0.24 * vis)
            cv_.paint(0, 0, edge, _mix(DANGER, WHITE, 0.15), 0.85 * vis)
    if vis > 0 and _finite(fog.radius) and fog.radius > 0:
        rr = float(fog.radius) * S
        cv_.ring(x, y, rr, max(1.2, S * 0.0045), _mix(DANGER, WHITE, 0.35), 0.7 * vis,
                 dash=(max(5.0, S * 0.022), max(4.0, S * 0.018)))


def _draw_fog_label(cv_: Canvas, fog: FogEstimate, icon: np.ndarray | None, S: float) -> None:
    """Greyed champion icon + elapsed-time pill at the last seen point of a fog estimate."""
    uv = _uv_ok(fog.last_uv)
    if uv is None:
        return
    conf = _clamp01(fog.confidence)
    alpha = 0.6 + 0.4 * conf
    x, y = uv[0] * S, uv[1] * S
    icon_d = max(18.0, S * 0.082)
    f_tiny = get_font(max(8, int(S * 0.034)), "bold")
    cv_.image(x, y, round_icon_patch(icon, icon_d * 0.92, DANGER, max(1.6, icon_d * 0.09), grey=True,
                                     letter=fog.name or fog.alias or "?"), alpha)
    _label_pill(cv_, x, y + icon_d * 0.46 + _cap_height(f_tiny) / 2 + 5, fmt_seconds(fog.elapsed), f_tiny,
                WHITE, DANGER, alpha=max(alpha, 0.8))
