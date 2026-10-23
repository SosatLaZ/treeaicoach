"""The launcher's background: a tree of life in sapphire (docs/DESIGN.md, "Lanceur").

A fine line-art tree (trunk, branches that fill a circle, roots that mirror them) inside a thin ring,
on a deep sapphire gradient, low contrast so it stays behind the content. It is drawn ONCE, offline
(``python tools/make_backdrop.py`` writes ``assets/ui/backdrop.png``, 1600 x 1000); the window
decodes it on a worker thread, scales it once per window size ("cover": the circle stays round) and
``paintEvent`` only copies that cached pixmap. No timer, no animation: zero idle CPU. If the file is
missing, :func:`render_rgb` draws it on that worker (~0.3 s).
"""

from __future__ import annotations

import math
import random
from typing import Any

import numpy as np

#: reference frame (aspect 1.6; drawn "cover" on any window)
REF_W, REF_H = 1600, 1000
#: emblem centre and radius in reference-frame fractions (right of the content column's centre)
TREE_X, TREE_Y, TREE_R = 0.73, 0.52, 0.40
SEED = 11
BG_TOP = (6, 17, 44)          # #06112C
BG_BOTTOM = (10, 29, 64)      # #0A1D40
LIFT = (18, 58, 115)          # #123A73: a soft lift behind the tree (depth, not a glow)
LINE = (31, 95, 191)          # #1F5FBF
LEAF = (79, 142, 247)         # #4F8EF7
ASSET = ("ui", "backdrop.png")
ASSET_SIZE = (REF_W, REF_H)


def _background(w: int, h: int) -> np.ndarray:
    ys = (np.arange(h, dtype=np.float32) / max(1, h - 1))[:, None, None]
    top, bot = np.array(BG_TOP, np.float32), np.array(BG_BOTTOM, np.float32)
    img = np.broadcast_to(top + (bot - top) * ys, (h, w, 3)).copy()
    cx, cy, r = TREE_X * w, TREE_Y * h, TREE_R * h
    gx = np.exp(-(((np.arange(w, dtype=np.float32) - cx) / (1.25 * r)) ** 2))
    gy = np.exp(-(((np.arange(h, dtype=np.float32) - cy) / (1.25 * r)) ** 2))
    lift = np.outer(gy, gx)[..., None] * 0.30
    img = img * (1 - lift) + np.array(LIFT, np.float32) * lift
    return img


def _tree_layer(w: int, h: int, ss: int = 2) -> Any:
    """RGBA PIL image of the tree, ring, roots and leaves (supersampled ``ss`` times)."""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    W, H = w * ss, h * ss
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    rnd = random.Random(SEED)
    cx, cy, R = TREE_X * W, TREE_Y * H, TREE_R * H
    unit = H / 1000.0

    def stroke(pts: list[tuple[float, float]], width: float, alpha: int) -> None:
        d.line(pts, fill=LINE + (alpha,), width=max(1, int(round(width))), joint="curve")

    def inside(x: float, y: float, lim: float) -> tuple[float, float]:
        dx, dy = x - cx, y - cy
        dist = math.hypot(dx, dy)
        if dist <= lim:
            return x, y
        return cx + dx * lim / dist, cy + dy * lim / dist

    tips: list[tuple[float, float, float]] = []

    def grow(x: float, y: float, ang: float, length: float, width: float, depth: int, down: bool) -> None:
        ex, ey = x + length * math.sin(ang), y + (length if down else -length) * math.cos(ang)
        lim = R * (0.80 if down else 0.86)
        clipped = math.hypot(ex - cx, ey - cy) > lim
        ex, ey = inside(ex, ey, lim)
        # a gentle curve: quadratic through a bent midpoint
        bend = length * 0.10 * (rnd.random() - 0.5)
        mx, my = (x + ex) / 2 + bend * math.cos(ang), (y + ey) / 2 + bend * math.sin(ang)
        pts = [((1 - t) ** 2 * x + 2 * (1 - t) * t * mx + t * t * ex,
                (1 - t) ** 2 * y + 2 * (1 - t) * t * my + t * t * ey) for t in (i / 8 for i in range(9))]
        alpha = int((150 if not down else 120) * (0.55 + 0.45 * depth / 7))
        stroke(pts, width, alpha)
        if depth <= 0 or length < 6 * unit or clipped:
            if not clipped:
                tips.append((ex, ey, 0.0 if down else 1.0))
            return
        n = 3 if (depth >= 5 and rnd.random() < 0.35) else 2
        spread = (0.36 if not down else 0.46) + 0.10 * rnd.random()
        for i in range(n):
            f = -1 + 2 * i / (n - 1)
            child = ang + f * spread + 0.12 * (rnd.random() - 0.5)
            grow(ex, ey, child, length * (0.72 + 0.08 * rnd.random()), max(1.0 * ss, width * 0.68), depth - 1,
                 down)

    ground = cy + 0.30 * R
    fork = cy - 0.02 * R
    trunk_w = 9 * unit
    stroke([(cx - 2 * unit, ground), (cx + 1 * unit, (ground + fork) / 2), (cx, fork)], trunk_w, 150)
    for a in (-1.05, -0.45, 0.0, 0.42, 1.0):
        grow(cx, fork, a, 0.25 * R, trunk_w * 0.60, 6, False)
    for a in (-1.15, -0.45, 0.0, 0.5, 1.1):
        grow(cx, ground, a, 0.19 * R, trunk_w * 0.45, 4, True)
    # the ring that holds it, and a ground line
    rw = max(1, int(1.6 * unit))
    d.ellipse((cx - R, cy - R, cx + R, cy + R), outline=LINE + (110,), width=rw)
    d.line([(cx - 0.97 * R * math.cos(0.22), ground), (cx + 0.97 * R * math.cos(0.22), ground)],
           fill=LINE + (70,), width=rw)
    for x, y, up in tips:                      # leaves (and root ends): small sapphire seeds
        if up and rnd.random() < 0.8:
            r = (2.2 + 2.2 * rnd.random()) * unit
            d.ellipse((x - r, y - r, x + r, y + r), fill=LEAF + (int(70 + 70 * rnd.random()),))
    return layer.resize((w, h), Image.LANCZOS)


def render_rgb(w: int = REF_W, h: int = REF_H) -> np.ndarray:
    """``h x w x 3`` uint8 image of the backdrop. PIL + numpy, thread-safe."""
    bg = _background(w, h)
    tree = np.asarray(_tree_layer(w, h), dtype=np.float32)
    a = tree[..., 3:4] / 255.0
    img = bg * (1 - a) + tree[..., :3] * a
    return np.clip(img + 0.5, 0, 255).astype(np.uint8)


def peak_luminance(rgb: np.ndarray) -> float:
    """Highest relative luminance (WCAG) in an image, for the contrast check of the glass panels."""
    c = rgb.reshape(-1, 3).astype(np.float64) / 255.0
    c = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return float((c @ np.array([0.2126, 0.7152, 0.0722])).max())


def cover_rect(src_w: int, src_h: int, dst_w: int, dst_h: int) -> tuple[float, float, float, float]:
    """Source rectangle (x, y, w, h) that fills ``dst`` without stretching ("cover")."""
    s = max(dst_w / src_w, dst_h / src_h)
    cw, ch = dst_w / s, dst_h / s
    return ((src_w - cw) / 2.0, (src_h - ch) / 2.0, cw, ch)


def load_glow() -> Any:
    """Worker thread: the backdrop as a QImage (the bundled PNG, else drawn now)."""
    try:
        from PySide6 import QtGui  # noqa: PLC0415

        from treeaicoach.paths import asset_path  # noqa: PLC0415

        img = QtGui.QImage(str(asset_path(*ASSET)))
        if not img.isNull():
            return img
    except Exception:
        pass
    return render_rgb(960, 600)


class BackdropCache:
    """Backdrop image (decoded once) + the pixmap for the current size."""

    def __init__(self) -> None:
        self.glow: Any = None          # QImage, reference frame
        self.pixmap: Any = None        # QPixmap at the widget size (device pixels)
        self.size: tuple[int, int, float] = (0, 0, 0.0)

    def set_glow(self, rgb: Any) -> None:
        from PySide6 import QtGui  # noqa: PLC0415

        if isinstance(rgb, QtGui.QImage):
            self.glow, self.pixmap, self.size = rgb, None, (0, 0, 0.0)
            return
        h, w, _ = rgb.shape
        buf = np.ascontiguousarray(rgb)
        self.glow = QtGui.QImage(buf.data, w, h, 3 * w, QtGui.QImage.Format_RGB888).copy()
        self.pixmap = None
        self.size = (0, 0, 0.0)

    def compose(self, w: int, h: int, dpr: float, smooth: bool = True) -> Any:
        from PySide6 import QtCore, QtGui  # noqa: PLC0415

        Qt = QtCore.Qt
        if self.glow is None or w <= 0 or h <= 0:
            return None
        if self.pixmap is not None and self.size == (w, h, dpr):
            return self.pixmap
        pw, ph = max(1, int(round(w * dpr))), max(1, int(round(h * dpr)))
        sx, sy, sw, sh = cover_rect(self.glow.width(), self.glow.height(), pw, ph)
        part = self.glow.copy(QtCore.QRect(int(sx), int(sy), max(1, int(sw)), max(1, int(sh))))
        img = part.scaled(pw, ph, Qt.IgnoreAspectRatio, Qt.SmoothTransformation if smooth else Qt.FastTransformation)
        pm = QtGui.QPixmap.fromImage(img.convertToFormat(QtGui.QImage.Format_RGB32))
        pm.setDevicePixelRatio(dpr)
        self.pixmap, self.size = pm, ((w, h, dpr) if smooth else (0, 0, 0.0))
        return pm
