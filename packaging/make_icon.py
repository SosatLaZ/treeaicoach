"""Generate the TreeAI Coach application icon.

Design (v2.7, « l'arbre-balise », recoloured in v2.17) : a flat amber rounded tile (#F5A25A, the app's only
accent) carrying a near-black (#0A080E) broad-leaf tree silhouette with a ward's open eye cut into
its crown. Tree = TreeAI, ward eye = vision, the coach that watches the minimap.
Two colours, no gradient, no glow, no shadow, no text (docs/DESIGN.md). The silhouette is
bold enough for the 16 px taskbar entry; every size is drawn separately at supersampling and
reduced with a high-quality filter.

Outputs (next to this script by default):
  * ``icon.png`` - 512 x 512 RGBA (README, window icon, About page)
  * ``icon.ico`` - 16, 24, 32, 48, 64, 128, 256 px (BMP entries + PNG for 256, like Windows' own icons)

Usage::

    python packaging/make_icon.py [--out-dir DIR] [--preview preview.png]
"""

from __future__ import annotations

import argparse
import io
import math
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ICO_SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)
PNG_SIZE = 512
SUPERSAMPLE = 8

SAP = (245, 162, 90, 255)        # #F5A25A  ACCENT (tile): the accretion disk's amber (v2.17, « trou noir »)
INK = (10, 8, 14, 255)           # #0A080E  deep space (tree)

# geometry in unit coordinates (y down)
TILE_RADIUS = 0.22               # rounded-square tile, like the minimap frame
# crown: five overlapping discs (cx, cy, r) - a broad-leaf tree, not a playing-card club
CROWN_LOBES: tuple[tuple[float, float, float], ...] = (
    (0.50, 0.30, 0.22), (0.30, 0.42, 0.18), (0.70, 0.42, 0.18), (0.40, 0.55, 0.15), (0.60, 0.55, 0.15))
TRUNK_TOP = 0.50
TRUNK_TOP_HALF = 0.055           # trunk half-width under the crown ...
TRUNK_BASE_HALF = 0.075          # ... and at the foot (slight taper, no flare)
TRUNK_BOTTOM = 0.87
EYE_C = (0.5, 0.42)              # the ward eye cut into the crown
EYE_HALF_W = 0.22                # eye opening: two circular arcs (pointed corners)
EYE_HALF_H = 0.12
PUPIL_R = 0.07


def _eye_outline(cx: float, cy: float, hw: float, hh: float, n: int = 96) -> list[tuple[float, float]]:
    """Vesica (lens) of half-width ``hw`` and half-height ``hh`` in unit coordinates."""
    r = (hw * hw + hh * hh) / (2.0 * hh)
    a0 = math.asin(hw / r)
    pts: list[tuple[float, float]] = []
    for i in range(n + 1):                                   # upper lid, left -> right
        a = -a0 + 2.0 * a0 * i / n
        pts.append((cx + r * math.sin(a), cy + (r - hh) - r * math.cos(a)))
    for i in range(n + 1):                                   # lower lid, right -> left
        a = -a0 + 2.0 * a0 * i / n
        pts.append((cx - r * math.sin(a), cy - (r - hh) + r * math.cos(a)))
    return pts


def render_icon(size: int, supersample: int = SUPERSAMPLE) -> Image.Image:
    """Render the icon at ``size`` x ``size`` px (RGBA, transparent outside the tile)."""
    size = int(size)
    if size < 8:
        raise ValueError("icon size must be >= 8 px")
    ss = max(2, int(supersample))
    if size > 64:                       # keep the canvas <= ~2048 px: plenty for large sizes
        ss = max(2, min(ss, 2048 // size))
    s = size * ss

    def px(pts):
        return [(x * s, y * s) for x, y in pts]

    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=round(TILE_RADIUS * s), fill=SAP)
    d.polygon(px([(0.5 - TRUNK_TOP_HALF, TRUNK_TOP), (0.5 + TRUNK_TOP_HALF, TRUNK_TOP),
                  (0.5 + TRUNK_BASE_HALF, TRUNK_BOTTOM), (0.5 - TRUNK_BASE_HALF, TRUNK_BOTTOM)]), fill=INK)
    for lx, ly, r in CROWN_LOBES:
        d.ellipse(px([(lx - r, ly - r), (lx + r, ly + r)]), fill=INK)
    ex, ey = EYE_C
    d.polygon(px(_eye_outline(ex, ey, EYE_HALF_W, EYE_HALF_H)), fill=SAP)
    d.ellipse(px([(ex - PUPIL_R, ey - PUPIL_R), (ex + PUPIL_R, ey + PUPIL_R)]), fill=INK)
    return im.resize((size, size), Image.Resampling.LANCZOS)


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
