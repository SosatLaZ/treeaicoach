"""Generate the TreeAI Coach application icon.

Design: a stylised "AI tree" whose branches are circuit traces (45 degree bends, round pads)
in hextech gold on a dark-blue medallion with a thin gold ring. Every size is drawn
separately at 8x supersampling and reduced with a high-quality filter; sizes <= 32 px use a
simplified drawing (fewer, thicker branches) so the icon stays readable in the taskbar.

Outputs (next to this script by default):
  * ``icon.png`` - 512 x 512 RGBA (README, window icon)
  * ``icon.ico`` - 16, 24, 32, 48, 64, 128, 256 px (BMP entries + PNG for 256, like Windows' own icons)

Usage::

    python packaging/make_icon.py [--out-dir DIR] [--preview preview.png]
"""

from __future__ import annotations

import argparse
import io
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ICO_SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)
PNG_SIZE = 512
SUPERSAMPLE = 8

NAVY = (10, 20, 40)              # #0A1428
NAVY_LIGHT = (22, 44, 78)        # centre of the medallion gradient
NAVY_DARK = (5, 11, 24)          # edge of the medallion gradient
GOLD = (200, 170, 110)           # #C8AA6E
GOLD_LIGHT = (240, 230, 210)     # #F0E6D2
GOLD_DARK = (120, 90, 40)        # #785A28
TEAL = (10, 200, 185)            # #0AC8B9 (hextech core)

Point = tuple[float, float]


@dataclass(frozen=True)
class Trace:
    """A circuit trace: polyline in unit coordinates (y down) with a stroke width."""
    points: tuple[Point, ...]
    width: float


@dataclass(frozen=True)
class Pad:
    """A round pad (node) at the end of a trace; ``hole`` > 0 draws a dark via in its centre."""
    center: Point
    radius: float
    hole: float = 0.0
    core: bool = False           # teal glowing centre (the "AI" spark)


@dataclass(frozen=True)
class Design:
    traces: tuple[Trace, ...]
    pads: tuple[Pad, ...]
    ring_width: float            # unit width of the gold ring
    glow: bool                   # soft teal glow behind the crown
    shadow: bool                 # drop shadow under the tree


def _mirror(points: tuple[Point, ...]) -> tuple[Point, ...]:
    return tuple((1.0 - x, y) for x, y in points)


def _symmetric(traces: list[Trace], pads: list[Pad]) -> tuple[tuple[Trace, ...], tuple[Pad, ...]]:
    """Add the mirror image (x -> 1 - x) of every left-side element (x < 0.5)."""
    out_t = list(traces)
    out_p = list(pads)
    for t in traces:
        if any(x < 0.499 for x, _ in t.points):
            out_t.append(Trace(_mirror(t.points), t.width))
    for p in pads:
        if p.center[0] < 0.499:
            out_p.append(Pad((1.0 - p.center[0], p.center[1]), p.radius, p.hole, p.core))
    return tuple(out_t), tuple(out_p)


def full_design() -> Design:
    """Detailed tree for sizes >= 48 px."""
    w_trunk, w_main, w_twig = 0.052, 0.034, 0.022
    traces = [
        Trace(((0.5, 0.80), (0.5, 0.25)), w_trunk),                                  # trunk
        # upper branches -> crown shoulders
        Trace(((0.5, 0.47), (0.36, 0.33), (0.36, 0.25)), w_main),
        # middle branches -> wide nodes
        Trace(((0.5, 0.58), (0.35, 0.43), (0.22, 0.43)), w_main),
        # lower branches
        Trace(((0.5, 0.68), (0.38, 0.56), (0.27, 0.56)), w_main * 0.9),
        # twigs
        Trace(((0.36, 0.33), (0.27, 0.33), (0.23, 0.29)), w_twig),
        # roots (PCB ground traces)
        Trace(((0.5, 0.74), (0.40, 0.84), (0.32, 0.84)), w_main * 0.85),
    ]
    pads = [
        Pad((0.5, 0.20), 0.058, hole=0.030, core=True),
        Pad((0.36, 0.215), 0.040, hole=0.018),
        Pad((0.18, 0.43), 0.040, hole=0.018),
        Pad((0.24, 0.56), 0.032, hole=0.014),
        Pad((0.215, 0.275), 0.026, hole=0.0),
        Pad((0.29, 0.84), 0.030, hole=0.013),
        Pad((0.5, 0.84), 0.036, hole=0.015),
    ]
    t, p = _symmetric(traces, pads)
    return Design(t, p, ring_width=0.022, glow=True, shadow=True)


def small_design() -> Design:
    """Simplified tree for sizes <= 32 px: trunk, two branches per side, teal core on top."""
    w_trunk, w_branch = 0.105, 0.080
    traces = [
        Trace(((0.5, 0.80), (0.5, 0.28)), w_trunk),
        Trace(((0.5, 0.52), (0.27, 0.29)), w_branch),
        Trace(((0.5, 0.70), (0.34, 0.54), (0.22, 0.54)), w_branch),
        Trace(((0.5, 0.73), (0.39, 0.84), (0.33, 0.84)), w_branch * 0.85),   # roots
    ]
    pads = [
        Pad((0.5, 0.215), 0.095, hole=0.048, core=True),
        Pad((0.255, 0.275), 0.072),
        Pad((0.195, 0.54), 0.072),
    ]
    t, p = _symmetric(traces, pads)
    return Design(t, p, ring_width=0.0, glow=False, shadow=False)


def design_for(size: int) -> Design:
    return small_design() if size <= 32 else full_design()


def _vertical_gradient(h: int, w: int, stops: list[tuple[float, tuple[int, int, int]]]) -> np.ndarray:
    """float32 [h, w, 3] vertical gradient through (position 0..1, rgb) stops."""
    ys = np.linspace(0.0, 1.0, h, dtype=np.float32)
    pos = np.array([s[0] for s in stops], dtype=np.float32)
    out = np.empty((h, 3), dtype=np.float32)
    for c in range(3):
        out[:, c] = np.interp(ys, pos, np.array([s[1][c] for s in stops], dtype=np.float32))
    return np.repeat(out[:, None, :], w, axis=1)


def _gold_fill(s: int) -> np.ndarray:
    """Metallic hextech gold: light at the top, #C8AA6E in the middle, bronze at the bottom."""
    return _vertical_gradient(s, s, [(0.0, GOLD_LIGHT), (0.30, (226, 204, 150)),
                                     (0.60, GOLD), (1.0, (150, 118, 62))])


def _disc_mask(s: int, radius: float, cx: float = 0.5, cy: float = 0.5) -> Image.Image:
    m = Image.new("L", (s, s), 0)
    d = ImageDraw.Draw(m)
    r = radius * s
    d.ellipse((cx * s - r, cy * s - r, cx * s + r, cy * s + r), fill=255)
    return m


def _tree_mask(design: Design, s: int) -> Image.Image:
    """Anti-aliasing is provided by the supersampling: draw hard shapes at s x s."""
    m = Image.new("L", (s, s), 0)
    d = ImageDraw.Draw(m)
    for tr in design.traces:
        pts = [(x * s, y * s) for x, y in tr.points]
        w = max(1, round(tr.width * s))
        d.line(pts, fill=255, width=w, joint="curve")
        r = w / 2.0
        for x, y in (pts[0], pts[-1]):                  # round caps
            d.ellipse((x - r, y - r, x + r, y + r), fill=255)
    for p in design.pads:
        x, y, r = p.center[0] * s, p.center[1] * s, p.radius * s
        d.ellipse((x - r, y - r, x + r, y + r), fill=255)
    return m


def _alpha(img: Image.Image) -> np.ndarray:
    return np.asarray(img, dtype=np.float32) / 255.0


def _over(dst: np.ndarray, color: np.ndarray, alpha: np.ndarray) -> None:
    """In-place 'over' compositing of ``color`` (HxWx3 or 3,) with coverage ``alpha`` (HxW)."""
    a = alpha[..., None]
    dst[..., :3] = dst[..., :3] * (1.0 - a) + color * a


def render_icon(size: int, supersample: int = SUPERSAMPLE) -> Image.Image:
    """Render the icon at ``size`` x ``size`` px (RGBA)."""
    size = int(size)
    if size < 8:
        raise ValueError("icon size must be >= 8 px")
    design = design_for(size)
    ss = max(2, int(supersample))
    if size > 64:                       # keep the canvas <= ~2048 px: plenty for large sizes
        ss = max(2, min(ss, 2048 // size))
    s = size * ss
    yy, xx = (np.mgrid[0:s, 0:s].astype(np.float32) + 0.5) / s     # unit coordinates
    dist = np.hypot(xx - 0.5, yy - 0.5)

    # medallion background: radial navy gradient, light source slightly below the centre
    t = np.clip(np.hypot(xx - 0.5, yy - 0.54) / 0.5, 0.0, 1.0)[..., None]
    bg = np.array(NAVY_LIGHT, np.float32) * (1 - t) + np.array(NAVY_DARK, np.float32) * t
    rgb = bg.copy()
    # thin hextech circle pattern inside the medallion (large sizes only)
    if size >= 128:
        for rad, strength in ((0.40, 0.10), (0.30, 0.06)):
            ring = np.clip(1.0 - np.abs(dist - rad) * s / (0.004 * s + 0.75), 0.0, 1.0)
            _over(rgb, np.array(GOLD, np.float32), ring * strength)
    if design.glow:
        glow = np.exp(-((xx - 0.5) ** 2 + (yy - 0.36) ** 2) / (2 * 0.16 ** 2))
        _over(rgb, np.array(TEAL, np.float32), (glow * 0.16).astype(np.float32))

    tree = _tree_mask(design, s)
    if design.shadow:
        off = round(0.012 * s)
        sh = tree.filter(ImageFilter.GaussianBlur(0.014 * s))
        sh_a = np.roll(_alpha(sh), off, axis=0) * 0.65
        _over(rgb, np.zeros(3, np.float32), sh_a)
    _over(rgb, _gold_fill(s), _alpha(tree))

    # pads: dark vias / teal core
    holes = Image.new("L", (s, s), 0)
    cores = Image.new("L", (s, s), 0)
    dh, dc = ImageDraw.Draw(holes), ImageDraw.Draw(cores)
    for p in design.pads:
        if p.hole <= 0:
            continue
        x, y, r = p.center[0] * s, p.center[1] * s, p.hole * s
        (dc if p.core else dh).ellipse((x - r, y - r, x + r, y + r), fill=255)
    _over(rgb, np.array(NAVY, np.float32), _alpha(holes))
    core_a = _alpha(cores)
    if core_a.any():
        cy_core = next(p.center for p in design.pads if p.core)
        cd = np.hypot(xx - cy_core[0], yy - cy_core[1])
        hole_r = next(p.hole for p in design.pads if p.core)
        tcore = np.clip(cd / max(hole_r, 1e-6), 0.0, 1.0)[..., None]
        core_col = np.array((190, 255, 245), np.float32) * (1 - tcore) + np.array(TEAL, np.float32) * tcore
        _over(rgb, core_col, core_a)

    # medallion shape + gold ring
    outer = 0.5
    shape = _alpha(_disc_mask(s, outer, 0.5, 0.5))
    ring_w = max(design.ring_width, 1.0 / size)           # thin, but at least one pixel
    inner = _alpha(_disc_mask(s, outer - ring_w, 0.5, 0.5))
    ring_a = np.clip(shape - inner, 0.0, 1.0)
    ring_fill = _vertical_gradient(s, s, [(0.0, GOLD_LIGHT), (0.45, GOLD), (1.0, GOLD_DARK)])
    _over(rgb, ring_fill, ring_a)
    if size >= 64:                                        # fine dark line inside the ring (depth)
        in2 = _alpha(_disc_mask(s, outer - ring_w - 0.010, 0.5, 0.5))
        _over(rgb, np.array((3, 7, 15), np.float32), np.clip(inner - in2, 0, 1) * 0.8)

    rgba = np.dstack([np.clip(rgb, 0, 255), shape * 255.0]).round().astype(np.uint8)
    big = Image.fromarray(rgba, "RGBA")
    return big.resize((size, size), Image.Resampling.LANCZOS)


def _bmp_entry(img: Image.Image) -> bytes:
    """32-bit BGRA DIB (+ AND mask) as stored in .ico files, bottom-up."""
    w, h = img.size
    arr = np.asarray(img.convert("RGBA"), dtype=np.uint8)
    bgra = arr[::-1, :, [2, 1, 0, 3]].tobytes()
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, len(bgra), 0, 0, 0, 0)
    row_bytes = ((w + 31) // 32) * 4                       # 1 bpp rows padded to 32 bits
    bits = np.packbits(arr[::-1, :, 3] == 0, axis=1)       # transparent pixels -> 1 (MSB first)
    and_mask = np.zeros((h, row_bytes), dtype=np.uint8)
    and_mask[:, : bits.shape[1]] = bits
    return header + bgra + and_mask.tobytes()


def write_ico(images: list[Image.Image], path: Path) -> None:
    """Write a multi-resolution .ico: BMP entries below 256 px, PNG for 256 px."""
    images = sorted(images, key=lambda im: im.size[0])
    blobs: list[bytes] = []
    for im in images:
        if im.size[0] >= 256:
            buf = io.BytesIO()
            im.save(buf, format="PNG", optimize=True)
            blobs.append(buf.getvalue())
        else:
            blobs.append(_bmp_entry(im))
    out = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    offset = 6 + 16 * len(images)
    for im, blob in zip(images, blobs):
        w, h = im.size
        out += struct.pack("<BBBBHHII", w % 256, h % 256, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    for blob in blobs:
        out += blob
    path.write_bytes(bytes(out))


def preview_sheet(images: dict[int, Image.Image], zoom: int = 6) -> Image.Image:
    """Contact sheet: every size at 1:1 on a light and a dark strip, then sizes < 64 enlarged."""
    sizes = sorted(images)
    pad = 16
    small = [n for n in sizes if n < 64]
    row_h = max(sizes) + 2 * pad
    zoom_h = max((n * zoom for n in small), default=0) + 2 * pad
    width = max(sum(sizes) + pad * (len(sizes) + 1), sum(n * zoom for n in small) + pad * (len(small) + 1))
    sheet = Image.new("RGBA", (width, 2 * row_h + zoom_h), (242, 242, 242, 255))
    ImageDraw.Draw(sheet).rectangle((0, row_h, width, sheet.height), fill=(32, 34, 37, 255))
    for top in (pad, row_h + pad):
        x = pad
        for n in sizes:
            sheet.alpha_composite(images[n], (x, top + (max(sizes) - n) // 2))
            x += n + pad
    x = pad
    for n in small:
        sheet.alpha_composite(images[n].resize((n * zoom, n * zoom), Image.Resampling.NEAREST), (x, 2 * row_h + pad))
        x += n * zoom + pad
    return sheet


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Génère icon.png et icon.ico de TreeAI Coach")
    ap.add_argument("--out-dir", type=Path, default=Path(__file__).resolve().parent)
    ap.add_argument("--preview", type=Path, default=None, help="planche d'aperçu PNG (facultatif)")
    args = ap.parse_args(argv)
    out: Path = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    images = {s: render_icon(s) for s in ICO_SIZES}
    render_icon(PNG_SIZE).save(out / "icon.png", format="PNG", optimize=True)
    write_ico([images[s] for s in ICO_SIZES], out / "icon.ico")
    if args.preview is not None:
        preview_sheet(images).save(args.preview)
    sys.stdout.write(f"icon.png + icon.ico écrits dans {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
