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
    """Reachable region (translucent fill + contour) and the dashed straight-line bound circle."""
    uv = _uv_ok(fog.last_uv)
    if uv is None:
        return
    conf = _clamp01(fog.confidence)
    vis = 0.35 + 0.65 * math.sqrt(conf) if conf > 0 else 0.0
    # the jungler's zone is the headline; other hidden enemies get a discreet version
    main = bool(getattr(fog, "is_jungler", False))
    x, y = uv[0] * S, uv[1] * S
    region = fog.region
    if vis > 0 and isinstance(region, np.ndarray) and region.ndim == 2 and region.shape[0] >= 4:
        layers = _region_layers(region, int(S))
        if layers is not None:
            fill, edge = layers
            cv_.paint(0, 0, fill, DANGER, (0.24 if main else 0.10) * vis)
            cv_.paint(0, 0, edge, _mix(DANGER, WHITE, 0.15), (0.85 if main else 0.45) * vis)
    if main and vis > 0 and _finite(fog.radius) and fog.radius > 0:
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


# ======================================================================================
# HUD panel
# ======================================================================================
def _objective_rows(state: OverlayState) -> list[tuple[str, np.ndarray | None, str, tuple]]:
    """(label, icon, text, colour) for the objectives worth showing, soonest first."""
    gt = state.game_time if state.game_time is not None and _finite(state.game_time) else None
    items: list[tuple[float, str, np.ndarray | None, str, tuple]] = []
    for ob in state.objectives or []:
        try:
            name = str(getattr(ob, "name", "") or "")
            alive = bool(getattr(ob, "alive", False))
            nxt = getattr(ob, "next_spawn", None)
        except Exception:
            continue
        if not name:
            continue
        icon = objective_icon(name)
        label = OBJECTIVE_SHORT.get(name.lower(), name)
        if alive:
            items.append((-1.0, label, icon, "dispo", SAFE))
            continue
        if nxt is None or not _finite(nxt) or gt is None:
            continue
        remaining = float(nxt) - gt
        if remaining < -1:
            continue
        colour = WARNING if remaining <= 60 else GOLD_LIGHT
        items.append((remaining, label, icon, fmt_clock(max(0.0, remaining)), colour))
    items.sort(key=lambda it: it[0])
    return [(label, icon, text, colour) for _, label, icon, text, colour in items]


def _threat_glyph(cv_: Canvas, cx: float, cy: float, r: float, level: int, base_rgb: Sequence[int]) -> None:
    """Round badge with a check (safe), "!" (warning) or double "!" (danger)."""
    cv_.disc(cx, cy, r, _mix(base_rgb, BLACK, 0.35), 0.9)
    cv_.ring(cx, cy, r - 0.6, 1.2, WHITE, 0.55)
    w = max(1.6, r * 0.2)
    if level <= 0:
        cv_.capsule(cx - r * 0.42, cy + r * 0.02, cx - r * 0.1, cy + r * 0.34, w, WHITE)
        cv_.capsule(cx - r * 0.1, cy + r * 0.34, cx + r * 0.45, cy - r * 0.32, w, WHITE)
    else:
        xs = (cx,) if level == 1 else (cx - r * 0.24, cx + r * 0.24)
        for x in xs:
            cv_.capsule(x, cy - r * 0.45, x, cy + r * 0.12, w, WHITE)
            cv_.disc(x, cy + r * 0.42, w * 0.62, WHITE)


def _coin(cv_: Canvas, cx: float, cy: float, r: float) -> None:
    cv_.disc(cx, cy, r, (164, 124, 48))
    cv_.disc(cx, cy - r * 0.08, r * 0.8, (236, 200, 110))
    cv_.ring(cx, cy - r * 0.08, r * 0.52, max(1.0, r * 0.16), (190, 145, 60), 0.9)


def _hud_layout(state: OverlayState, width: int, k: float) -> dict[str, Any]:
    pad = 12 * k
    inner = width - 2 * pad
    fonts = {
        "title": get_font(round(10.5 * k), "bold"),
        "clock": get_font(round(12 * k), "semibold"),
        "threat": get_font(round(15.5 * k), "bold"),
        "body": get_font(round(12.5 * k), "regular"),
        "body_bold": get_font(round(12.5 * k), "bold"),
        "small": get_font(round(10.5 * k), "semibold"),
        "obj": get_font(round(11.5 * k), "semibold"),
    }
    rows: list[tuple[str, float]] = [("header", 20 * k), ("threat", 38 * k)]
    jl = (state.jungler_line or "").strip()
    jl_lines: list[str] = []
    if jl:
        jl_lines = _jungler_lines(jl, fonts, inner - 36 * k)
        rows.append(("jungler", max(30 * k, len(jl_lines) * 16 * k + 4 * k)))
    slot_w = inner / 5.0
    icon_d = min(40 * k, slot_w - 14 * k)
    rows.append(("enemies", icon_d + 22 * k))
    objs = _objective_rows(state)
    if objs:
        rows.append(("objectives", 26 * k))
    rows.append(("alert", 22 * k))
    hint = (state.hint or "").strip()
    if hint:
        rows.append(("hint", 20 * k))
    gap = 7 * k
    height = pad + sum(h for _, h in rows) + gap * (len(rows) - 1) + pad
    return {"pad": pad, "inner": inner, "fonts": fonts, "rows": rows, "gap": gap, "height": int(math.ceil(height)),
            "jl_lines": jl_lines, "slot_w": slot_w, "icon_d": icon_d, "objs": objs, "hint": hint}


def _jungler_lines(text: str, fonts: dict[str, Any], max_w: float) -> list[str]:
    """Split "Jungler : X — vu il y a 23 s, zone" into a title line and a detail line."""
    head, sep, tail = text.partition(" — ")
    if sep and head.strip() and tail.strip():
        return [fit_text(head.strip(), fonts["body_bold"], max_w), fit_text(tail.strip(), fonts["body"], max_w)]
    return wrap_text(text, fonts["body"], max_w, 2)


def hud_size(state: OverlayState, width: int = 340) -> tuple[int, int]:
    """(width, height) that :func:`render_hud` will produce for ``state``."""
    width = int(min(max(int(width) if _finite(width) else 340, 200), 1200))
    return width, _hud_layout(state, width, width / 340.0)["height"]


def render_hud(state: OverlayState, width: int = 340, now: float | None = None) -> np.ndarray:
    """HUD panel (threat bar, jungler, 5 enemies, objectives, last alert, hint) - premultiplied BGRA.

    Never raises (an empty 1-row image on internal error, logged).
    """
    width = int(min(max(int(width) if _finite(width) else 340, 200), 1200))
    try:
        return _render_hud(state, width, time.monotonic() if now is None else float(now))
    except Exception:
        log.exception("render_hud failed")
        return np.zeros((1, width, 4), np.uint8)


def _render_hud(state: OverlayState, width: int, now: float) -> np.ndarray:
    k = width / 340.0
    lay = _hud_layout(state, width, k)
    W, H = width, lay["height"]
    pad, inner, fonts, gap = lay["pad"], lay["inner"], lay["fonts"], lay["gap"]
    cv_ = Canvas(W, H)
    # panel: subtle vertical gradient + gold hairline border
    grad = np.linspace(0, 1, 32, dtype=np.float32)[:, None, None]
    top, bot = _rgb((16, 30, 54)), _rgb(PANEL)
    cv_.rrect(0, 0, W, H, 10 * k, top * (1 - grad) + bot * grad, PANEL_ALPHA, border=GOLD,
              border_alpha=BORDER_ALPHA, border_w=1.0)
    phase = (now % HALO_PERIOD_S) / HALO_PERIOD_S
    enemies = list(state.enemies or [])
    y = pad
    for name, h in lay["rows"]:
        cy = y + h / 2
        if name == "header":
            cv_.polygon([(pad + 4 * k, cy - 5 * k), (pad + 9 * k, cy), (pad + 4 * k, cy + 5 * k),
                         (pad - 1 * k, cy)], GOLD)
            cv_.text(pad + 14 * k, cy, "TREEAI COACH", fonts["title"], GOLD, shadow=0.4)
            cv_.text(W - pad, cy, fmt_clock(state.game_time), fonts["clock"], GOLD_LIGHT, anchor="r")
            ly = y + h + gap / 2
            cv_.capsule(pad, ly, W - pad, ly, 1.0, GOLD, 0.35)
        elif name == "threat":
            lvl = int(min(max(int(state.threat_level or 0), 0), 2)) if _finite(state.threat_level or 0) else 0
            base = THREAT_COLORS[lvl]
            if lvl == 2:
                pulse = 0.5 + 0.5 * math.sin(phase * 2 * math.pi)
                cv_.rrect(pad - 3 * k, y - 3 * k, inner + 6 * k, h + 6 * k, 10 * k, None, border=DANGER,
                          border_alpha=0.25 + 0.45 * pulse, border_w=2.5 * k)
            g = np.linspace(0, 1, 24, dtype=np.float32)[:, None, None]
            light, dark = _rgb(_mix(base, WHITE, 0.12)), _rgb(_mix(base, BLACK, 0.28))
            cv_.rrect(pad, y, inner, h, 7 * k, light * (1 - g) + dark * g, 0.95,
                      border=_mix(base, WHITE, 0.35), border_alpha=0.6)
            gr = h * 0.32
            _threat_glyph(cv_, pad + 8 * k + gr, cy, gr, lvl, base)
            text = (state.threat_text or "").strip() or THREAT_DEFAULT_TEXT[lvl]
            tx = pad + 16 * k + 2 * gr
            avail = inner - (tx - pad) - 8 * k
            tfont = fonts["threat"]
            size = 15.5 * k
            while text_width(text, tfont) > avail and size > 11.5 * k:
                size -= 1.0 * k
                tfont = get_font(round(size), "bold")
            cv_.text(tx, cy, fit_text(text, tfont, avail), tfont, WHITE, shadow=0.45)
        elif name == "jungler":
            jg = next((e for e in enemies if getattr(e, "is_jungler", False)), None)
            d = 28 * k
            icx = pad + d / 2
            if jg is not None:
                cv_.image(icx, cy, round_icon_patch(jg.icon, d, DANGER, max(1.6, 2 * k), grey=not jg.visible,
                                                    letter=jg.name or jg.alias or "J"))
                if jg.visible:
                    cv_.disc(icx + d * 0.36, cy + d * 0.36, 4.2 * k, PANEL_DEEP)
                    cv_.disc(icx + d * 0.36, cy + d * 0.36, 3.0 * k, SAFE)
            else:
                cv_.image(icx, cy, round_icon_patch(None, d, GOLD_DARK, max(1.6, 2 * k), letter="J"))
            lines = lay["jl_lines"]
            lh = 16 * k
            ty = cy - (len(lines) - 1) * lh / 2
            two_part = len(lines) == 2 and " — " in (state.jungler_line or "")
            for i, line in enumerate(lines):
                font = fonts["body_bold"] if (two_part and i == 0) else fonts["body"]
                colour = GOLD_LIGHT if i == 0 else (_mix(GOLD_LIGHT, MUTED, 0.45) if two_part else MUTED)
                cv_.text(pad + d + 8 * k, ty + i * lh, line, font, colour)
        elif name == "enemies":
            _draw_enemy_slots(cv_, enemies, pad, y, lay["slot_w"], lay["icon_d"], k, fonts["small"], phase)
        elif name == "objectives":
            x = pad
            isz = 20 * k
            for label, icon, text, colour in lay["objs"]:
                tw = text_width(text, fonts["obj"])
                need = isz + 5 * k + tw
                if x + need > W - pad + 0.5:
                    break
                if icon is not None:
                    cv_.image(x + isz / 2, cy, sprite_patch(icon, isz))
                else:
                    cv_.text(x + isz / 2, cy, label[:1], fonts["obj"], GOLD, anchor="m")
                cv_.text(x + isz + 5 * k, cy, text, fonts["obj"], colour)
                x += need + 13 * k
        elif name == "alert":
            _draw_alert_line(cv_, state, pad, cy, inner, fonts["body"], k)
        elif name == "hint":
            _coin(cv_, pad + 6 * k, cy, 6 * k)
            cv_.text(pad + 17 * k, cy, fit_text(lay["hint"], fonts["obj"], inner - 17 * k), fonts["obj"],
                     GOLD_LIGHT)
        y += h + gap
    return cv_.to_bgra()


def _draw_enemy_slots(cv_: Canvas, enemies: list[EnemyView], x0: float, y0: float, slot_w: float,
                      icon_d: float, k: float, font: Any, phase: float) -> None:
    """Five enemy slots: portrait (grey when not visible) + status line."""
    ring_w = max(1.8, 2.2 * k)
    for i in range(5):
        cx = x0 + slot_w * (i + 0.5)
        cy = y0 + icon_d / 2 + 1
        ly = y0 + icon_d + 12 * k
        if i >= len(enemies) or enemies[i] is None:
            cv_.ring(cx, cy, icon_d / 2 - 1, 1.2, GREY, 0.8, dash=(4, 3))
            cv_.text(cx, ly, "—", font, GREY, anchor="m", shadow=0)
            continue
        e = enemies[i]
        ago = e.last_seen_ago if e.last_seen_ago is not None and _finite(e.last_seen_ago) else None
        if e.visible and e.approaching:
            cv_.glow(cx, cy, icon_d * 0.45, icon_d * 0.72, DANGER, 0.35 + 0.3 * math.sin(phase * 2 * math.pi))
        cv_.image(cx, cy, round_icon_patch(e.icon, icon_d, DANGER, ring_w, grey=not e.visible,
                                           letter=e.name or e.alias or "?"))
        if e.is_jungler:
            bx, by, br = cx - icon_d * 0.38, cy - icon_d * 0.38, 6.5 * k
            cv_.disc(bx, by, br + 1.2, PANEL_DEEP)
            cv_.disc(bx, by, br, GOLD)
            cv_.text(bx, by, "J", get_font(round(9 * k), "bold"), PANEL_DEEP, anchor="m", shadow=0)
        if e.visible:
            bx, by = cx + icon_d * 0.36, cy + icon_d * 0.36
            cv_.disc(bx, by, 5.0 * k, PANEL_DEEP)
            cv_.disc(bx, by, 3.6 * k, SAFE)
            text, colour = ("approche", DANGER) if e.approaching else ("visible", SAFE)
        elif ago is None:
            text, colour = "non vu", GREY
        elif ago < LAST_SEEN_MAX_S:
            text, colour = f"MIA {fmt_seconds(ago)}", WARNING
        else:
            text, colour = f"vu {fmt_clock(ago)}", MUTED
        cv_.text(cx, ly, fit_text(text, font, slot_w - 2), font, colour, anchor="m", shadow=0.4)


def _draw_alert_line(cv_: Canvas, state: OverlayState, x: float, cy: float, inner: float, font: Any,
                     k: float) -> None:
    la = state.last_alert
    text, level, age = "", 0, None
    if la:
        try:
            text, level, age = str(la[0] or ""), int(la[1]), float(la[2])
        except (TypeError, ValueError, IndexError):
            text = ""
    if text and age is not None and _finite(age) and age < ALERT_FADE_S:
        a = 1.0 - max(0.0, age) / ALERT_FADE_S
        a = a ** 0.6
        colour = THREAT_COLORS.get(min(max(level, 0), 2), GOLD)
        cv_.disc(x + 5 * k, cy, 4.5 * k, colour, a)
        cv_.glow(x + 5 * k, cy, 3 * k, 9 * k, colour, 0.35 * a)
        fg = GOLD_LIGHT if level < 2 else _mix(DANGER, WHITE, 0.35)
        cv_.text(x + 16 * k, cy, fit_text(text, font, inner - 16 * k), font, fg, a)
    else:
        cv_.disc(x + 5 * k, cy, 3.5 * k, GREY, 0.8)
        cv_.text(x + 16 * k, cy, "Aucune alerte récente", font, GREY, 0.9, shadow=0)


# ======================================================================================
# Danger flash (screen edges)
# ======================================================================================
def _rect_tuple(r: Any) -> tuple[int, int, int, int] | None:
    if r is None:
        return None
    try:
        if hasattr(r, "x"):
            vals = (r.x, r.y, r.w, r.h)
        else:
            vals = tuple(r)[:4]
        x, y, w, h = (int(round(float(v))) for v in vals)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


def flash_profile(thickness: int) -> np.ndarray:
    """Alpha (0..1) as a function of the distance to the screen edge, length ``3 * thickness``."""
    th = max(1, int(thickness))
    d = np.arange(3 * th, dtype=np.float32)
    core = 0.92 - 0.22 * (d / th)
    glow = 0.70 * np.clip(1.0 - (d - th) / (2.0 * th), 0.0, 1.0) ** 2
    return np.where(d < th, core, glow).astype(np.float32)


def render_flash(w: int, h: int, intensity: float, exclude: "Rect | None", thickness: int = 10) -> np.ndarray:
    """Red frame on the screen edges (premultiplied BGRA ``h x w``), never covering ``exclude``.

    ``exclude`` is the minimap rectangle relative to the flash image (x, y, w, h). Never raises.
    """
    try:
        w = int(min(max(int(w), 1), _MAX_SIDE))
        h = int(min(max(int(h), 1), _MAX_SIDE))
    except (TypeError, ValueError):
        return np.zeros((1, 1, 4), np.uint8)
    out = np.zeros((h, w, 4), np.uint8)
    try:
        a = _clamp01(intensity)
        if a <= 0:
            return out
        prof = flash_profile(thickness) * np.float32(a)
        D = int(min(len(prof), max(1, w // 2), max(1, h // 2)))
        prof = prof[:D]
        dx = np.minimum(np.arange(w), np.arange(w)[::-1])
        dy = np.minimum(np.arange(h), np.arange(h)[::-1])
        col = np.array([DANGER[2], DANGER[1], DANGER[0]], np.float32)

        def fill(ys: slice, xs: slice) -> None:
            d = np.minimum(dy[ys][:, None], dx[xs][None, :])
            alpha = np.where(d < D, prof[np.minimum(d, D - 1)], np.float32(0.0))
            out[ys, xs, :3] = (alpha[..., None] * col + 0.5).astype(np.uint8)
            out[ys, xs, 3] = (alpha * 255.0 + 0.5).astype(np.uint8)

        fill(slice(0, D), slice(0, w))
        fill(slice(max(D, h - D), h), slice(0, w))
        if h - 2 * D > 0:
            fill(slice(D, h - D), slice(0, D))
            fill(slice(D, h - D), slice(max(D, w - D), w))
        ex = _rect_tuple(exclude)
        if ex is not None:
            m = 2
            x0, y0 = max(0, ex[0] - m), max(0, ex[1] - m)
            x1, y1 = min(w, ex[0] + ex[2] + m), min(h, ex[1] + ex[3] + m)
            if x0 < x1 and y0 < y1:
                out[y0:y1, x0:x1] = 0
        return out
    except Exception:
        log.exception("render_flash failed")
        return np.zeros((h, w, 4), np.uint8)


# ======================================================================================
# Pixel format helpers
# ======================================================================================
def to_premultiplied_bgra(rgba: np.ndarray) -> np.ndarray:
    """Straight RGBA uint8 -> premultiplied BGRA uint8 (``UpdateLayeredWindow`` format)."""
    arr = np.asarray(rgba)
    if arr.ndim != 3 or arr.shape[2] not in (3, 4):
        raise ValueError(f"expected an RGBA image, got shape {arr.shape}")
    if arr.shape[2] == 3:
        arr = np.dstack([arr, np.full(arr.shape[:2], 255, np.uint8)])
    a = arr[..., 3:4].astype(np.uint16)
    rgb = (arr[..., :3].astype(np.uint16) * a + 127) // 255
    out = np.empty(arr.shape[:2] + (4,), np.uint8)
    out[..., 0] = rgb[..., 2]
    out[..., 1] = rgb[..., 1]
    out[..., 2] = rgb[..., 0]
    out[..., 3] = arr[..., 3]
    return out


def premultiplied_to_rgba(bgra: np.ndarray) -> np.ndarray:
    """Premultiplied BGRA uint8 -> straight RGBA uint8 (for PIL / Tk previews)."""
    arr = np.asarray(bgra)
    a = arr[..., 3:4].astype(np.float32)
    safe = np.where(a > 0, a, 1.0)
    rgb = np.clip(arr[..., :3].astype(np.float32) * 255.0 / safe + 0.5, 0, 255).astype(np.uint8)
    out = np.empty(arr.shape[:2] + (4,), np.uint8)
    out[..., 0] = rgb[..., 2]
    out[..., 1] = rgb[..., 1]
    out[..., 2] = rgb[..., 0]
    out[..., 3] = arr[..., 3]
    out[..., :3][arr[..., 3] == 0] = 0
    return out


def composite_over(dst_rgb: np.ndarray, bgra_premul: np.ndarray, x: int, y: int) -> np.ndarray:
    """Blend a premultiplied BGRA image onto an RGB uint8 image at (x, y) (in place, clipped)."""
    H, W = dst_rgb.shape[:2]
    h, w = bgra_premul.shape[:2]
    X0, Y0, X1, Y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if X0 >= X1 or Y0 >= Y1:
        return dst_rgb
    src = bgra_premul[Y0 - y:Y1 - y, X0 - x:X1 - x].astype(np.float32)
    inv = 1.0 - src[..., 3:4] / 255.0
    dst = dst_rgb[Y0:Y1, X0:X1].astype(np.float32)
    dst = dst * inv + src[..., 2::-1]
    dst_rgb[Y0:Y1, X0:X1] = np.clip(dst + 0.5, 0, 255).astype(np.uint8)
    return dst_rgb


# ======================================================================================
# Previews (UI / tests / demo)
# ======================================================================================
def default_minimap_rect(screen_w: int, screen_h: int) -> tuple[int, int, int, int]:
    """Typical in-game minimap rectangle (bottom-right square, ~0.236 x screen height)."""
    side = int(round(screen_h * 0.236))
    margin = max(4, int(round(screen_h * 0.009)))
    return screen_w - side - margin, screen_h - side - margin, side, side


def game_background(w: int, h: int, minimap: tuple[int, int, int, int] | None = None,
                    seed: int = 7) -> np.ndarray:
    """A game-like RGB backdrop (terrain + bottom HUD + fogged minimap) for previews."""
    rng = np.random.default_rng(seed)
    small = rng.random((max(2, h // 40), max(2, w // 40), 3)).astype(np.float32)
    noise = cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)
    base = np.array([46, 58, 38], np.float32) / 255.0
    img = base * (0.75 + 0.5 * noise)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    lane = np.exp(-(((xx / w) - (yy / h) * 0.9 - 0.05) ** 2) / 0.004)
    img = img * (1 - 0.35 * lane[..., None]) + np.array([0.42, 0.37, 0.26], np.float32) * 0.35 * lane[..., None]
    vign = 1.0 - 0.45 * (((xx / w) - 0.5) ** 2 + ((yy / h) - 0.5) ** 2)
    rgb = np.clip(img * vign[..., None] * 255, 0, 255).astype(np.uint8)
    # bottom HUD (ability bar)
    cv_ = Canvas(w, h)
    bw, bh = int(w * 0.36), int(h * 0.13)
    cv_.rrect((w - bw) / 2, h - bh - 4, bw, bh, 8, (8, 16, 26), 0.95, border=GOLD_DARK, border_alpha=0.9,
              border_w=2)
    for i in range(6):
        sx = (w - bw) / 2 + bw * 0.12 + i * bw * 0.13
        cv_.rrect(sx, h - bh + 8, bw * 0.105, bh * 0.5, 4, (22, 40, 58), 1.0, border=GOLD, border_alpha=0.6)
    composite_over(rgb, cv_.to_bgra(), 0, 0)
    if minimap is not None:
        mx, my, mw, mh = minimap
        tex = default_radar_texture()
        mm = cv2.resize(tex, (mw, mh), interpolation=cv2.INTER_AREA)[..., ::-1].astype(np.float32)
        fog = np.full((mh, mw), 0.36, np.float32)
        for (u, v, r) in ((0.12, 0.88, 0.25), (0.1, 0.35, 0.12), (0.5, 0.5, 0.1)):
            cv2.circle(fog, (int(u * mw), int(v * mh)), int(r * mw), 1.0, -1, cv2.LINE_AA)
        fog = cv2.GaussianBlur(fog, (0, 0), 2)
        mm = (mm * fog[..., None]).astype(np.uint8)
        x0, y0 = max(0, mx), max(0, my)
        x1, y1 = min(w, mx + mw), min(h, my + mh)
        if x0 < x1 and y0 < y1:
            rgb[y0:y1, x0:x1] = mm[y0 - my:y1 - my, x0 - mx:x1 - mx]
            fr = Canvas(w, h)
            fr.rrect(mx - 5, my - 5, mw + 10, mh + 10, 3, None, border=(30, 50, 55), border_alpha=1, border_w=4)
            fr.rrect(mx - 6, my - 6, mw + 12, mh + 12, 3, None, border=(160, 130, 70), border_alpha=1)
            composite_over(rgb, fr.to_bgra(), 0, 0)
    return rgb


def render_preview(state: OverlayState, width: int = 1280, texture_bgr: np.ndarray | None = None,
                   now: float | None = None, cfg: Any = None) -> np.ndarray:
    """Straight RGBA preview of the whole overlay (radar, HUD, flash) over a game-like screen.

    The overlay is laid out at the state's screen resolution (1920 x 1080 by default) with the
    same placement rules as ``overlay.py`` then scaled to ``width``. Never raises.
    """
    try:
        return _render_preview(state, width, texture_bgr, now, cfg)
    except Exception:
        log.exception("render_preview failed")
        return np.zeros((max(1, int(width) * 9 // 16), max(1, int(width)), 4), np.uint8)


def _render_preview(state: OverlayState, width: int, texture_bgr: np.ndarray | None, now: float | None,
                    cfg: Any) -> np.ndarray:
    from treeaicoach import overlay as _ov   # lazy: overlay imports this module

    scr = _rect_tuple(state.screen_rect) or (0, 0, 1920, 1080)
    sx, sy, sw, sh = scr
    mm = _rect_tuple(state.minimap_rect) or tuple(
        a + b for a, b in zip(default_minimap_rect(sw, sh), (sx, sy, 0, 0)))
    bg = game_background(sw, sh, (mm[0] - sx, mm[1] - sy, mm[2], mm[3]))
    scale = float(getattr(cfg, "radar_scale", 1.0) or 1.0) if cfg is not None else 1.0
    rsize = _ov.radar_size(mm, scale)
    radar = render_radar(state, rsize, texture_bgr, now)
    rx, ry = _ov.radar_placement(mm, scr, rsize, getattr(cfg, "radar_position", "above_minimap"),
                                 getattr(cfg, "radar_xy", None))
    composite_over(bg, radar, rx - sx, ry - sy)
    hud_w = _ov.hud_width(scr)
    hud = render_hud(state, hud_w, now)
    hx, hy = _ov.hud_placement(scr, hud.shape[1], hud.shape[0], getattr(cfg, "hud_position", "top_left"),
                               getattr(cfg, "hud_xy", None), avoid=[mm, (rx, ry, rsize, rsize)])
    composite_over(bg, hud, hx - sx, hy - sy)
    if _clamp01(state.flash) > 0:
        fl = render_flash(sw, sh, state.flash, (mm[0] - sx, mm[1] - sy, mm[2], mm[3]),
                          thickness=_ov.flash_thickness(scr))
        composite_over(bg, fl, 0, 0)
    width = int(min(max(int(width), 64), 4096))
    out = cv2.resize(bg, (width, max(1, int(round(sh * width / sw)))), interpolation=cv2.INTER_AREA)
    return np.dstack([out, np.full(out.shape[:2], 255, np.uint8)])


def _encode_png(rgba: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))
    if not ok:
        raise ValueError("PNG encoding failed")
    return buf.tobytes()


def render_preview_png(state: OverlayState, path: str | os.PathLike[str] | None = None,
                       width: int = 1280, texture_bgr: np.ndarray | None = None,
                       now: float | None = None, cfg: Any = None) -> bytes:
    """PNG bytes of :func:`render_preview` (also written to ``path`` when given)."""
    data = _encode_png(render_preview(state, width, texture_bgr, now, cfg))
    if path is not None:
        Path(path).write_bytes(data)
    return data


def radar_preview_rgba(state: OverlayState, size: int = 256, texture_bgr: np.ndarray | None = None,
                       now: float | None = None) -> np.ndarray:
    """Straight RGBA radar image (for Tk / CustomTkinter previews)."""
    return premultiplied_to_rgba(render_radar(state, size, texture_bgr, now))


def hud_preview_rgba(state: OverlayState, width: int = 340, now: float | None = None) -> np.ndarray:
    """Straight RGBA HUD image (for Tk / CustomTkinter previews)."""
    return premultiplied_to_rgba(render_hud(state, width, now))


# ======================================================================================
# Sample states (demo, UI static preview, tests)
# ======================================================================================
@dataclass
class _DemoObjective:
    name: str
    next_spawn: float | None
    alive: bool = False
    source: str = "schedule"


def _demo_icon(db: Any, alias: str) -> np.ndarray | None:
    try:
        return db.load_icon(alias) if db is not None else None
    except Exception:
        return None


def sample_states(db: Any = None) -> dict[str, OverlayState]:
    """A few representative states: "safe", "warning", "danger" (jungler in fog), "late"."""
    from treeaicoach.fog_tracker import FogTracker

    if db is None:
        try:
            from treeaicoach.champions import ChampionDB

            db = ChampionDB()
        except Exception:
            db = None
    ic = {a: _demo_icon(db, a) for a in ("LeeSin", "Darius", "Ahri", "Jinx", "Thresh", "Garen")}
    fog = FogTracker()
    scr = (0, 0, 1920, 1080)
    mm = default_minimap_rect(1920, 1080)

    def enemies(spec: dict[str, tuple]) -> list[EnemyView]:
        names = {"LeeSin": "Lee Sin", "Darius": "Darius", "Ahri": "Ahri", "Jinx": "Jinx", "Thresh": "Thresh"}
        out = []
        for alias in ("Darius", "LeeSin", "Ahri", "Jinx", "Thresh"):
            vis, uv, ago, appr, vel = spec.get(alias, (False, None, None, False, None))
            out.append(EnemyView(key=alias, alias=alias, name=names[alias], visible=vis, uv=uv,
                                 last_seen_ago=ago, is_jungler=alias == "LeeSin", approaching=appr,
                                 icon=ic[alias], velocity=vel))
        return out

    obj_early = [_DemoObjective("Dragon", 300.0), _DemoObjective("Larves", 360.0),
                 _DemoObjective("Héraut", 900.0), _DemoObjective("Baron", 1500.0)]
    states: dict[str, OverlayState] = {}
    states["safe"] = OverlayState(
        minimap_rect=mm, screen_rect=scr, me_uv=(0.085, 0.36), my_team="ORDER",
        enemies=enemies({"Darius": (True, (0.09, 0.24), 0.0, False, None),
                         "LeeSin": (False, (0.74, 0.66), 65.0, False, None),
                         "Ahri": (True, (0.52, 0.47), 0.0, False, None),
                         "Jinx": (True, (0.86, 0.9), 0.0, False, None),
                         "Thresh": (False, (0.88, 0.84), 12.0, False, None)}),
        threat_level=0, threat_text="SÛR", last_alert=None, objectives=obj_early, game_time=192.0,
        jungler_line="Jungler : Lee Sin — vu il y a 1:05, jungle ennemie du bas",
        hint=None, me_icon=ic["Garen"],
    )
    states["warning"] = OverlayState(
        minimap_rect=mm, screen_rect=scr, me_uv=(0.085, 0.36), my_team="ORDER",
        enemies=enemies({"Darius": (True, (0.09, 0.25), 0.0, False, None),
                         "LeeSin": (True, (0.24, 0.3), 0.0, True, (-0.03, 0.012)),
                         "Ahri": (True, (0.5, 0.5), 0.0, False, None),
                         "Jinx": (True, (0.86, 0.9), 0.0, False, None),
                         "Thresh": (True, (0.84, 0.92), 0.0, False, None)}),
        threat_level=1, threat_text="ATTENTION — Lee Sin approche",
        last_alert=("Attention, Lee Sin approche.", 1, 1.3), objectives=obj_early, game_time=251.0,
        jungler_line="Jungler : Lee Sin — visible, rivière du haut", hint=None, me_icon=ic["Garen"],
    )
    lee_fog = fog.simulate("LeeSin", "LeeSin", "Lee Sin", (0.27, 0.23), 14.0, is_jungler=True, game_time=426)
    states["danger"] = OverlayState(
        minimap_rect=mm, screen_rect=scr, me_uv=(0.1, 0.3), my_team="ORDER",
        enemies=enemies({"Darius": (True, (0.1, 0.19), 0.0, True, (0.0, 0.03)),
                         "LeeSin": (False, (0.27, 0.23), 14.0, False, None),
                         "Ahri": (True, (0.2, 0.33), 0.0, True, (-0.035, -0.01)),
                         "Jinx": (False, (0.88, 0.86), 31.0, False, None),
                         "Thresh": (True, (0.86, 0.9), 0.0, False, None)}),
        fogs=[lee_fog], threat_level=2, threat_text="DANGER — 2 ennemis arrivent !",
        last_alert=("Danger, 2 ennemis arrivent, recule !", 2, 0.4),
        objectives=[_DemoObjective("Dragon", 540.0), _DemoObjective("Héraut", 900.0),
                    _DemoObjective("Larves", None, alive=True)],
        game_time=440.0, flash=0.85,
        jungler_line="Jungler : Lee Sin — vu il y a 14 s, rivière du haut",
        hint="1 450 PO — pense à rentrer", me_icon=ic["Garen"],
    )
    f1 = fog.simulate("LeeSin", "LeeSin", "Lee Sin", (0.62, 0.72), 21.0, is_jungler=True, game_time=1660)
    f2 = fog.simulate("Thresh", "Thresh", "Thresh", (0.8, 0.8), 38.0, game_time=1660)
    states["late"] = OverlayState(
        minimap_rect=mm, screen_rect=scr, me_uv=(0.47, 0.53), my_team="ORDER",
        enemies=enemies({"Darius": (True, (0.3, 0.2), 0.0, False, None),
                         "LeeSin": (False, (0.62, 0.72), 21.0, False, None),
                         "Ahri": (False, (0.6, 0.38), 6.0, False, None),
                         "Jinx": (True, (0.7, 0.4), 0.0, True, (-0.02, 0.012)),
                         "Thresh": (False, (0.8, 0.8), 38.0, False, None)}),
        fogs=[f1, f2], threat_level=1, threat_text="ATTENTION — Jinx approche",
        last_alert=("Le Baron est disponible.", 0, 2.6),
        objectives=[_DemoObjective("Baron", None, alive=True), _DemoObjective("Dragon ancestral", 1790.0),
                    _DemoObjective("Atakhan", None)],
        game_time=1660.0, jungler_line="Jungler : Lee Sin — vu il y a 21 s, rivière du bas",
        hint="Balise de contrôle : aucune dans l'inventaire", me_icon=ic["Garen"],
    )
    return states


def write_demo(outdir: str | os.PathLike[str], now: float = 0.3) -> list[Path]:
    """Write radar / HUD / full-screen previews of :func:`sample_states` into ``outdir``."""
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, st in sample_states().items():
        radar = radar_preview_rgba(st, 300, now=now)
        hud = hud_preview_rgba(st, 340, now=now)
        for label, img in (("radar", radar), ("hud", hud)):
            p = out / f"{label}_{name}.png"
            p.write_bytes(_encode_png(img))
            written.append(p)
        p = out / f"preview_{name}.png"
        render_preview_png(st, p, width=1600, now=now)
        written.append(p)
    return written


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m treeaicoach.overlay_render --demo OUTDIR``."""
    parser = argparse.ArgumentParser(prog="python -m treeaicoach.overlay_render",
                                     description="Rendus d'exemple de l'overlay TreeAI Coach.")
    parser.add_argument("--demo", metavar="OUTDIR", required=True, help="dossier de sortie des PNG")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        files = write_demo(args.demo)
    except Exception:
        log.exception("Demo rendering failed")
        return 1
    if sys.stdout is not None:
        for f in files:
            sys.stdout.write(f"{f}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
