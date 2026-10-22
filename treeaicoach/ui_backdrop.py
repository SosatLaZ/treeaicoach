"""The launcher's background: a black hole in deep space (docs/DESIGN.md, "Lanceur").

The slow-varying light is rendered ONCE, offline (``python tools/make_backdrop.py`` writes
``assets/ui/backdrop.png``, 1600 x 1000, ~120 KB; a worker thread decodes it in a few ms; if the
file is missing, :func:`render_rgb` draws a smaller one, ~0.2 s, on that worker): the
light: accretion disk seen almost edge-on, its lensed far side arching over the shadow,
the thin photon ring, a faint warm glow, a dark violet sky. On each window size the image is scaled
("cover", the hole stays round) and a seeded star field is drawn at device resolution, crisp; the
result is ONE cached pixmap that ``paintEvent`` only blits. No timer, no animation: zero idle CPU.
"""

from __future__ import annotations

import math
import random
from typing import Any

import numpy as np

#: reference frame of the glow image (aspect 1.6; drawn "cover" on any window)
REF_W, REF_H = 960, 600
#: black hole centre and shadow radius in reference-frame fractions (upper right, partly behind
#: the content column so the cards float over it; the page titles stay on dark sky)
HOLE_X, HOLE_Y, HOLE_R = 0.79, 0.33, 0.115
#: disk tilt (ellipse height / width as seen from the camera)
TILT = 0.17
STAR_SEED = 7
STARS_PER_MPX = 150
SKY_TOP = np.array([5, 5, 10], dtype=np.float32) / 255.0
SKY_BOTTOM = np.array([9, 7, 15], dtype=np.float32) / 255.0


def _smooth(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def render_rgb(w: int = REF_W, h: int = REF_H) -> np.ndarray:
    """``h x w x 3`` uint8 image of the black hole (no stars). Pure numpy, thread-safe."""
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    R = HOLE_R * h
    dx = (xs - HOLE_X * w) / R
    dy = (ys - HOLE_Y * h) / R
    rr = np.sqrt(dx * dx + dy * dy) + 1e-6
    # Doppler beaming: the side of the disk that comes towards us (left) is brighter
    beam = 1.0 + 0.55 * np.clip(-dx / np.maximum(rr, 1.0), -1.0, 1.0)

    # near side of the disk: a flat ellipse in front of the hole
    de = np.sqrt(dx * dx + (dy / TILT) ** 2)
    disk = np.exp(-(de - 1.45) * 0.95) * _smooth(1.25, 1.55, de) * (1.0 - _smooth(2.8, 4.4, de))
    disk *= np.exp(-((dy / (0.42 + 0.06 * de)) ** 2))        # thin: fades fast off the plane
    behind = (dy < 0) & (rr < 1.0)                              # the far half is hidden by the shadow
    disk = np.where(behind, 0.0, disk) * beam

    # far side of the disk, lensed into an arch over the shadow (and a fainter one under it)
    arch_w = np.where(dy < 0, 0.17, 0.10)
    arch = np.exp(-(((rr - 1.30) / arch_w) ** 2)) * np.where(dy < 0, 0.62, 0.22)
    arch *= 0.65 + 0.35 * beam

    ring = np.exp(-(((rr - 1.04) / 0.022) ** 2)) * 0.85 * beam   # photon ring (sharp, thin)
    r2 = rr * rr
    glow = np.exp(r2 * (-1.0 / 3.4 ** 2)) * 0.10 + np.exp(r2 * (-1.0 / 1.6 ** 2)) * 0.06

    light = disk * 1.25 + arch + ring + glow
    shadow = _smooth(0.96, 1.02, rr)                            # the event horizon's shadow: black
    inside = 1.0 - _smooth(0.975, 1.005, rr)                    # soft edge: no stair steps when scaled
    light = light * (1.0 - inside) + disk * 1.25 * inside

    # tone map + colour ramp: deep ember (faint) -> amber -> warm cream (hottest)
    v = 1.0 - np.exp(-light * 1.15)
    ember = np.array([0.42, 0.11, 0.03], dtype=np.float32)
    amber = np.array([0.96, 0.58, 0.24], dtype=np.float32)
    cream = np.array([1.00, 0.92, 0.80], dtype=np.float32)
    a = _smooth(0.0, 0.55, v)[..., None]
    b = _smooth(0.55, 0.95, v)[..., None]
    col = ember + (amber - ember) * a
    col = col + (cream - col) * b
    col = col * v[..., None] * 0.92

    sky = SKY_TOP + (SKY_BOTTOM - SKY_TOP) * (ys / h)[..., None]
    # a very faint cold haze, low left: depth, not a colour halo
    hx = np.exp(-(((np.arange(w, dtype=np.float32) / w - 0.12) / 0.35) ** 2))
    hy = np.exp(-(((np.arange(h, dtype=np.float32) / h - 0.92) / 0.35) ** 2))
    haze = np.outer(hy, hx) * 0.035
    sky = sky + haze[..., None] * np.array([0.35, 0.30, 0.60], dtype=np.float32)
    sky = sky * shadow[..., None]
    img = sky + col
    # vignette: edges fall off so the sidebar and the corners stay quiet
    vx = (xs / w - 0.5) * 2.0
    vy = (ys / h - 0.5) * 2.0
    img *= (1.0 - 0.28 * np.clip(vx * vx * 0.6 + vy * vy * 0.7, 0.0, 1.0))[..., None]
    return np.clip(img * 255.0 + 0.5, 0, 255).astype(np.uint8)


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


def star_field(w: int, h: int, seed: int = STAR_SEED) -> list[tuple[float, float, float, float, int]]:
    """Stars (x, y, radius, alpha, tint) for a ``w x h`` window: seeded, density per pixel, none
    inside the shadow. Tint 0 = cool white, 1 = warm."""
    rnd = random.Random(seed)
    sx, sy, sw, sh = cover_rect(REF_W, REF_H, w, h)
    scale = w / sw
    hx, hy = (HOLE_X * REF_W - sx) * scale, (HOLE_Y * REF_H - sy) * scale
    hr = HOLE_R * REF_H * scale * 1.15
    out = []
    for _ in range(int(STARS_PER_MPX * w * h / 1e6)):
        x, y = rnd.random() * w, rnd.random() * h
        m = rnd.random() ** 3                        # most stars faint, a few bright
        if math.hypot(x - hx, y - hy) < hr:
            continue
        out.append((x, y, 0.45 + 1.0 * m, 0.25 + 0.65 * m, 1 if rnd.random() < 0.25 else 0))
    return out


#: the bundled image (tools/make_backdrop.py)
ASSET = ("ui", "backdrop.png")
ASSET_SIZE = (1600, 1000)


def load_glow() -> Any:
    """Worker thread: the glow as a QImage (the bundled PNG, else rendered now, smaller)."""
    try:
        from PySide6 import QtGui  # noqa: PLC0415

        from treeaicoach.paths import asset_path  # noqa: PLC0415

        img = QtGui.QImage(str(asset_path(*ASSET)))
        if not img.isNull():
            return img
    except Exception:
        pass
    return render_rgb(800, 500)


class BackdropCache:
    """Glow image (rendered once) + the pixmap for the current size (scaled + stars)."""

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
        img = img.convertToFormat(QtGui.QImage.Format_RGB32)
        if smooth:
            qp = QtGui.QPainter(img)
            qp.setRenderHint(QtGui.QPainter.Antialiasing)
            qp.setPen(Qt.NoPen)
            cool, warm = QtGui.QColor(225, 230, 255), QtGui.QColor(255, 222, 190)
            for x, y, r, a, tint in star_field(pw, ph):
                c = QtGui.QColor(warm if tint else cool)
                c.setAlphaF(a)
                qp.setBrush(c)
                qp.drawEllipse(QtCore.QPointF(x, y), r * dpr, r * dpr)
            qp.end()
        pm = QtGui.QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        self.pixmap, self.size = pm, ((w, h, dpr) if smooth else (0, 0, 0.0))
        return pm
