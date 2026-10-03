"""Post-game HTML report (ARCHITECTURE.md §6.5) and the list of recorded games.

* :func:`render_report_html` -> self-contained French HTML page (inline CSS, images embedded
  as base64 PNG, no external request) with the dark "hextech" theme of the app.
* :func:`render_map_png` -> the minimap texture with my position heat map, my deaths (red
  crosses with the game time) and the enemy jungler sightings (dots coloured by minute).
* :func:`write_report` -> reads a record JSON and writes ``<same name>.html`` next to it.
* :func:`list_games` -> quick summary of the recorded games (History tab), newest first.

Nothing here raises: failures are logged and give ``None`` / an empty list / a degraded page.
"""

from __future__ import annotations

import base64
import datetime as _dt
import html
import io
import json
import logging
import math
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from treeaicoach import APP_NAME, __version__
from treeaicoach.fmtutil import clock, finite as _f

log = logging.getLogger(__name__)

# TreeAI palette (docs/DESIGN.md). Legacy names kept: GOLD = the accent, TEAL = my team's colour.
BG = "#0C0E0D"
PANEL = "#121513"
BORDER = "#222725"
LINE_STRONG = "#2F3532"
ACCENT = "#9BD84A"
GOLD = ACCENT
TEXT = "#E4E8E5"
TEAL = "#4A90D9"
RED = "#E5484D"
GREEN = ACCENT
ORANGE = "#E8A23A"
MUTED = "#8B948F"
DIM = "#59615C"

MAP_SIZE = 512                 # rendered map (px)
MINI_MAP_SIZE = 220            # jungler phase maps (px)
SUPERSAMPLE = 2                # antialiasing of the PIL drawings
HEAD_BYTES = 16384             # list_games(): bytes read to find the summary

_TERRAIN_TEXTURE = {"infernal": "infernal", "ocean": "ocean", "mountain": "mountain", "cloud": "cloud",
                    "hextech": "hextech", "chemtech": "base"}
_ZONE_FR_FALLBACK = "zone inconnue"
_MONTHS_FR = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")
_KIND_FR = {"jungler_approach": "Jungler", "roam_approach": "Roam", "collapse": "Plusieurs ennemis",
            "jungler_spotted": "Jungler vu", "laner_mia": "Adversaire disparu"}


# ======================================================================================
# small helpers
# ======================================================================================
def _e(x: Any) -> str:
    """HTML-escaped text."""
    return html.escape("" if x is None else str(x), quote=True)


def _num(x: Any, decimals: int = 1) -> str:
    v = _f(x)
    if v is None:
        return "-"
    s = f"{v:,.{decimals}f}".replace(",", "\u202f").replace(".", ",")
    return s


def _fmt_time(gt: Any) -> str:
    return clock(gt, "-", rounded=True)


def _hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lerp_color(stops: list[tuple[float, str]], x: float) -> tuple[int, int, int]:
    """Colour at ``x`` in [0, 1] along gradient ``stops`` [(pos, "#rrggbb")]."""
    x = min(1.0, max(0.0, x))
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        if x <= p1:
            a = 0.0 if p1 <= p0 else (x - p0) / (p1 - p0)
            r0, r1 = _hex_rgb(c0), _hex_rgb(c1)
            return tuple(int(round(r0[i] + a * (r1[i] - r0[i]))) for i in range(3))  # type: ignore[return-value]
    return _hex_rgb(stops[-1][1])


MINUTE_STOPS: list[tuple[float, str]] = [(0.0, "#C9D1CC"), (0.5, ORANGE), (1.0, RED)]
HEAT_STOPS: list[tuple[float, str]] = [(0.0, "#1C3312"), (0.35, "#5E8F2C"), (0.7, "#9BD84A"), (1.0, "#F4FAE8")]


def _date_fr(iso: Any) -> str:
    """``"12 sept. 2026, 20:31"`` from an ISO timestamp ('' if invalid)."""
    try:
        d = _dt.datetime.fromisoformat(str(iso))
        return f"{d.day} {_MONTHS_FR[d.month - 1]} {d.year}, {d:%H:%M}"
    except Exception:
        return ""


def _data_uri_png(png: bytes | None) -> str | None:
    if not png:
        return None
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


# ======================================================================================
# assets
# ======================================================================================
def _asset(*parts: str) -> Path:
    try:
        from treeaicoach.paths import asset_path

        return asset_path(*parts)
    except Exception:
        return Path(__file__).resolve().parent / "assets" / Path(*parts)


@lru_cache(maxsize=64)
def _icon_png_bytes(alias: str) -> bytes | None:
    """Embedded 64x64 champion icon (PNG bytes) or None."""
    try:
        if not alias:
            return None
        folder = _asset("icons", "champions")
        p = folder / f"{alias}.png"
        if not p.is_file():
            low = alias.lower()
            p = next((q for q in folder.glob("*.png") if q.stem.lower() == low), p)
        if p.is_file():
            return p.read_bytes()
    except Exception:
        log.debug("icon %s unavailable", alias, exc_info=True)
    return None


def icon_data_uri(alias: Any) -> str | None:
    """``data:`` URI of a champion icon (None if unknown)."""
    try:
        return _data_uri_png(_icon_png_bytes(str(alias or "")))
    except Exception:
        return None


def _texture_name(terrain: Any) -> str:
    key = _TERRAIN_TEXTURE.get(str(terrain or "").strip().lower(), "base")
    return f"2dlevelminimap_{key}_baron1.png"


@lru_cache(maxsize=8)
def _load_texture(name: str) -> Any:
    """Minimap texture as a PIL RGBA image (None if missing)."""
    try:
        from PIL import Image

        folder = _asset("minimap")
        for n in (name, "2dlevelminimap_base_baron1.png"):
            p = folder / n
            if p.is_file():
                with Image.open(p) as im:
                    return im.convert("RGBA")
        cands = sorted(folder.glob("2dlevelminimap_*.png"))
        if cands:
            with Image.open(cands[0]) as im:
                return im.convert("RGBA")
    except Exception:
        log.warning("Minimap texture unavailable", exc_info=True)
    return None


@lru_cache(maxsize=16)
def _font(size: int, bold: bool = False) -> Any:
    from PIL import ImageFont

    names = (["segoeuib.ttf", "seguisb.ttf", "DejaVuSans-Bold.ttf", "arialbd.ttf"] if bold
             else ["segoeui.ttf", "DejaVuSans.ttf", "arial.ttf"])
    dirs = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts", Path("/usr/share/fonts/truetype/dejavu"),
            Path("/usr/share/fonts/TTF"), Path("/Library/Fonts")]
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except Exception:
            pass
        for d in dirs:
            try:
                if (d / n).is_file():
                    return ImageFont.truetype(str(d / n), size)
            except Exception:
                pass
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


# ======================================================================================
# map rendering
# ======================================================================================
def _series(raw: Any) -> list[tuple[float, float, float]]:
    out = []
    if not isinstance(raw, list):
        return out
    for p in raw:
        try:
            t, u, v = _f(p[0]), _f(p[1]), _f(p[2])
        except (TypeError, IndexError, KeyError):
            continue
        if t is None or u is None or v is None:
            continue
        out.append((t, min(1.0, max(0.0, u)), min(1.0, max(0.0, v))))
    out.sort(key=lambda x: x[0])
    return out


def _jungler_points(record: dict, analysis: dict) -> list[tuple[float, float, float]]:
    j = analysis.get("jungler") or {}
    alias = str(j.get("alias") or "")
    name = str(j.get("name") or "")
    sight = record.get("sightings") if isinstance(record.get("sightings"), dict) else {}
    keys = {"".join(c for c in s.lower() if c.isalnum()) for s in (alias, name) if s}
    for k, v in sight.items():
        if "".join(c for c in str(k).lower() if c.isalnum()) in keys:
            return _series(v)
    return []


def _base_image(record: dict, size: int, darken: float = 0.5) -> Any:
    from PIL import Image

    tex = _load_texture(_texture_name((record.get("meta") or {}).get("map_terrain")))
    bg = Image.new("RGBA", (size, size), _hex_rgb(BG) + (255,))
    if tex is None:
        return bg
    t = tex.resize((size, size), Image.LANCZOS)
    arr = np.asarray(t).astype(np.float32)
    arr[..., :3] *= darken
    # slight cold tint so that the overlays pop
    arr[..., 2] = np.minimum(255.0, arr[..., 2] * 1.06 + 4)
    t = Image.fromarray(arr.clip(0, 255).astype(np.uint8), "RGBA")
    bg.alpha_composite(t)
    return bg


def _heat_layer(points: list[tuple[float, float, float]], size: int) -> Any:
    """RGBA heat map of my positions (time-weighted, blurred)."""
    from PIL import Image

    grid = 128
    h = np.zeros((grid, grid), np.float32)
    for i, (t, u, v) in enumerate(points):
        dt = (points[i + 1][0] - t) if i + 1 < len(points) else 1.0
        dt = min(2.0, max(0.0, dt))
        x = min(grid - 1, int(u * grid))
        y = min(grid - 1, int(v * grid))
        h[y, x] += dt
    if h.max() <= 0:
        return None
    try:
        import cv2

        h = cv2.GaussianBlur(h, (0, 0), 2.2)
    except Exception:   # numpy separable blur fallback
        k = np.exp(-0.5 * (np.arange(-6, 7) / 2.2) ** 2)
        k /= k.sum()
        h = np.apply_along_axis(lambda r: np.convolve(r, k, mode="same"), 0, h)
        h = np.apply_along_axis(lambda r: np.convolve(r, k, mode="same"), 1, h)
    ref = float(np.percentile(h[h > 0], 99)) if np.any(h > 0) else 1.0
    n = np.clip(h / max(ref, 1e-6), 0.0, 1.0) ** 0.6
    lut = np.array([_lerp_color(HEAT_STOPS, i / 255.0) for i in range(256)], np.uint8)
    idx = (n * 255).astype(np.uint8)
    rgb = lut[idx]
    alpha = np.where(n < 0.04, 0.0, np.clip(0.18 + 0.72 * n, 0.0, 0.9)) * 255
    rgba = np.dstack([rgb, alpha.astype(np.uint8)])
    img = Image.fromarray(rgba, "RGBA").resize((size, size), Image.BICUBIC)
    return img


def _draw_cross(draw: Any, x: float, y: float, r: float, w: int) -> None:
    for dx, dy in ((1, 1), (1, -1)):
        draw.line([(x - dx * r, y - dy * r), (x + dx * r, y + dy * r)], fill=(12, 14, 13, 255), width=w + 4)
    for dx, dy in ((1, 1), (1, -1)):
        draw.line([(x - dx * r, y - dy * r), (x + dx * r, y + dy * r)], fill=_hex_rgb(RED) + (255,), width=w)


def _label(draw: Any, x: float, y: float, text: str, font: Any, color: tuple[int, int, int], s: int,
           size: int, placed: list[tuple[float, float, float, float]] | None = None, gap: float = 0.0) -> None:
    """Small dark rounded label near the point (x, y): below, above, right or left, avoiding the
    boxes already ``placed``; kept inside the image."""
    try:
        l, t, r, b = draw.textbbox((0, 0), text, font=font)
    except Exception:
        l, t, r, b = 0, 0, 7 * len(text) * s, 12 * s
    tw, th = r - l, b - t
    pad = 3 * s
    w, h = tw + 2 * pad, th + 2 * pad
    cands = [(x - w / 2, y + gap), (x - w / 2, y - gap - h), (x + gap, y - h / 2), (x - gap - w, y - h / 2),
             (x + gap * 0.7, y + gap * 0.7), (x - gap * 0.7 - w, y - gap * 0.7 - h)]
    placed = placed if placed is not None else []

    def clamp(bx: float, by: float) -> tuple[float, float]:
        return min(max(2 * s, bx), size - w - 2 * s), min(max(2 * s, by), size - h - 2 * s)

    def overlap(bx: float, by: float) -> float:
        return sum(max(0.0, min(bx + w, q[2]) - max(bx, q[0])) * max(0.0, min(by + h, q[3]) - max(by, q[1]))
                   for q in placed)

    best = min((clamp(*c) for c in cands), key=lambda c: overlap(*c))
    bx, by = best
    placed.append((bx - s, by - s, bx + w + s, by + h + s))
    draw.rounded_rectangle([bx, by, bx + tw + 2 * pad, by + th + 2 * pad], radius=4 * s,
                           fill=(12, 14, 13, 225), outline=color + (255,), width=max(1, s))
    draw.text((bx + pad - l, by + pad - t), text, font=font, fill=color + (255,))


def _draw_jungler(draw: Any, pts: list[tuple[float, float, float]], size: int, s: int, duration: float,
                  dot: float) -> None:
    """Jungler sightings: faint path inside each appearance, dots coloured by minute."""
    span = max(duration, 60.0)
    prev = None
    for t, u, v in pts:
        x, y = u * size, v * size
        if prev is not None and t - prev[0] <= 5.0:
            c = _lerp_color(MINUTE_STOPS, t / span)
            draw.line([(prev[1] * size, prev[2] * size), (x, y)], fill=c + (150,), width=max(1, int(1.2 * s)))
        prev = (t, u, v)
    last_t = -1e9
    for t, u, v in pts:
        x, y = u * size, v * size
        c = _lerp_color(MINUTE_STOPS, t / span)
        first = t - last_t > 5.0
        r = dot * (1.6 if first else 1.0)
        draw.ellipse([x - r - s, y - r - s, x + r + s, y + r + s], fill=(12, 14, 13, 230))
        draw.ellipse([x - r, y - r, x + r, y + r], fill=c + (255,))
        last_t = t


def render_map_png(record: dict, analysis: dict, size: int = MAP_SIZE) -> bytes | None:
    """Map of the game: heat map of my positions, my deaths, enemy jungler sightings. PNG bytes."""
    try:
        from PIL import Image, ImageDraw

        s = SUPERSAMPLE
        big = size * s
        img = _base_image(record, big)
        mine = _series(record.get("my_positions"))
        heat = _heat_layer(mine, big)
        if heat is not None:
            img.alpha_composite(heat)
        layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        duration = _f((analysis.get("summary") or {}).get("duration"), 0.0) or _f(record.get("duration"), 0.0) or 0.0
        _draw_jungler(draw, _jungler_points(record, analysis), big, s, duration, dot=3.2 * s * size / 512)
        font = _font(int(12 * s * max(0.8, size / 512)), bold=True)
        crosses = [(float(d["uv"][0]) * big, float(d["uv"][1]) * big, str(d.get("time") or ""))
                   for d in analysis.get("deaths") or [] if isinstance(d, dict) and d.get("uv")]
        cr = 7 * s * size / 512
        for x, y, _ in crosses:
            _draw_cross(draw, x, y, cr, int(3 * s))
        placed = [(x - cr, y - cr, x + cr, y + cr) for x, y, _ in crosses]
        for x, y, text in crosses:
            _label(draw, x, y, text, font, _hex_rgb(TEXT), s, big, placed, gap=cr + 4 * s)
        img.alpha_composite(layer)
        out = img.resize((size, size), Image.LANCZOS).convert("RGB")
        buf = io.BytesIO()
        out.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception:
        log.exception("render_map_png failed")
        return None


def render_phase_map_png(record: dict, analysis: dict, t0: float, t1: float,
                         size: int = MINI_MAP_SIZE) -> bytes | None:
    """Small map with the enemy jungler sightings of one phase only. PNG bytes."""
    try:
        from PIL import Image, ImageDraw

        s = SUPERSAMPLE
        big = size * s
        img = _base_image(record, big, darken=0.55)
        layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        duration = _f((analysis.get("summary") or {}).get("duration"), 0.0) or 0.0
        pts = [p for p in _jungler_points(record, analysis) if t0 <= p[0] < t1]
        _draw_jungler(draw, pts, big, s, duration, dot=2.6 * s)
        img.alpha_composite(layer)
        out = img.resize((size, size), Image.LANCZOS).convert("RGB")
        buf = io.BytesIO()
        out.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception:
        log.exception("render_phase_map_png failed")
        return None


# ======================================================================================
# HTML
# ======================================================================================
CSS = f"""
*{{box-sizing:border-box}}
html,body{{margin:0;padding:0;background:{BG};color:{TEXT}}}
body{{font-family:"Segoe UI",system-ui,-apple-system,"Helvetica Neue","DejaVu Sans","Liberation Sans",Arial,sans-serif;
  font-size:14px;line-height:1.45;font-variant-numeric:tabular-nums}}
.title .name,.hstat .v,.card .v,.lane .v,.champ.ph{{font-family:"Bahnschrift SemiBold",Bahnschrift,"Segoe UI Semibold",
  "Segoe UI","DejaVu Sans Condensed","Liberation Sans Narrow","Arial Narrow",sans-serif}}
.wrap{{max-width:1080px;margin:0 auto;padding:20px 20px 36px}}
h2{{font-size:11.5px;letter-spacing:.12em;text-transform:uppercase;color:{MUTED};margin:0 0 12px;font-weight:700;
  padding-bottom:6px;border-bottom:1px solid {LINE_STRONG}}}
.panel{{background:{PANEL};border:1px solid {BORDER};border-radius:4px;padding:16px 18px;margin:0 0 14px}}
.hdr{{display:flex;gap:18px;align-items:center;flex-wrap:wrap}}
.champ{{width:72px;height:72px;border-radius:4px;border:1px solid {LINE_STRONG};background:{BG};object-fit:cover}}
.champ.ph{{display:flex;align-items:center;justify-content:center;font-size:28px;color:{MUTED};font-weight:700}}
.title{{flex:1;min-width:220px}}
.title .name{{font-size:26px;font-weight:700;margin:0;color:{TEXT}}}
.title .sub{{color:{MUTED};font-size:13px;margin-top:2px}}
.badge{{display:inline-block;padding:0;font-weight:700;font-size:12px;letter-spacing:.1em;text-transform:uppercase;
  margin-bottom:4px}}
.win{{color:{GREEN}}}
.lose{{color:{RED}}}
.unk{{color:{MUTED}}}
.hstats{{display:flex;gap:0;flex-wrap:wrap}}
.hstat{{padding:0 18px;border-left:1px solid {BORDER}}}
.hstat .v{{font-size:24px;font-weight:700;color:{TEXT};white-space:nowrap}}
.hstat .l{{font-size:11px;color:{MUTED};text-transform:uppercase;letter-spacing:.1em}}
.kda b{{color:{TEXT}}} .kda .d{{color:{RED}}} .kda .sl{{color:{MUTED};font-weight:400;padding:0 3px}}
.tldr{{margin:14px 0 0;padding:12px 0 0;border-top:1px solid {BORDER};display:flex;flex-direction:column;gap:4px}}
.tldr p{{margin:0;font-size:14.5px}} .tldr p b{{color:{ACCENT}}}
.cards{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:0;margin:0 0 14px;background:{PANEL};
  border:1px solid {BORDER};border-radius:4px}}
@media (max-width:760px){{.cards{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
.card{{padding:12px 16px;border-right:1px solid {BORDER};border-bottom:1px solid {BORDER}}}
.card:nth-child(4n){{border-right:none}} .card:nth-last-child(-n+4){{border-bottom:none}}
.card.r .v{{color:{RED}}} .card.g .v{{color:{GREEN}}}
.card .v{{font-size:22px;font-weight:700}} .card .l{{font-size:11px;color:{MUTED};text-transform:uppercase;letter-spacing:.08em}}
.card .x{{font-size:12px;color:{MUTED};margin-top:2px}}
.grid2{{display:grid;grid-template-columns:minmax(0,520px) minmax(0,1fr);gap:20px;align-items:start}}
@media (max-width:900px){{.grid2{{grid-template-columns:1fr}}}}
.map{{width:100%;max-width:512px;border-radius:4px;border:1px solid {LINE_STRONG};display:block}}
.legend{{display:flex;flex-direction:column;gap:8px;font-size:13px;color:{TEXT};margin-top:12px}}
.lg{{display:flex;align-items:center;gap:10px}}
.grad{{width:120px;height:8px;border-radius:2px}}
.lgx{{color:{RED};font-weight:900;font-size:16px;width:18px;text-align:center}}
.small{{font-size:12px;color:{MUTED}}}
.bars{{display:flex;flex-direction:column;gap:6px}}
.bar{{display:grid;grid-template-columns:140px 1fr 58px;gap:10px;align-items:center;font-size:13px}}
.track{{height:8px;background:{BORDER};border-radius:2px;overflow:hidden}}
.fill{{height:100%;background:{ACCENT};border-radius:2px}}
.fill.red{{background:{RED}}}
.pct{{text-align:right;color:{MUTED}}}
table{{width:100%;border-collapse:collapse;font-size:13.5px}}
th{{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:{MUTED};font-weight:600;
  border-bottom:1px solid {LINE_STRONG};padding:6px 10px}}
td{{padding:8px 10px;border-bottom:1px solid {BORDER};vertical-align:middle}}
tr:last-child td{{border-bottom:none}}
td.t{{font-weight:700;color:{TEXT};white-space:nowrap}}
.who{{display:flex;flex-wrap:wrap;gap:6px}}
.chip{{display:inline-flex;align-items:center;gap:6px;background:{BG};border:1px solid {BORDER};border-radius:4px;
  padding:2px 8px 2px 2px;font-size:12.5px;white-space:nowrap}}
.chip img{{width:20px;height:20px;border-radius:3px;border:1px solid {RED}}}
.chip.nj{{padding-left:8px}}
.chip.jgl img{{border-color:{ORANGE}}}
.tag{{display:inline-block;padding:1px 6px;border-radius:3px;font-size:12px;font-weight:700;white-space:nowrap}}
.tag.ok{{color:{ORANGE};border:1px solid {ORANGE}}}
.tag.no{{color:{RED};border:1px solid {RED}}}
.tag.sv{{color:{GREEN};border:1px solid {GREEN}}}
.recap{{color:{MUTED};font-size:12.5px;margin-top:4px}}
.tl{{position:relative;height:58px;margin:6px 8px 4px}}
.tl .axis{{position:absolute;left:0;right:0;top:28px;height:2px;background:{LINE_STRONG}}}
.tl .tick{{position:absolute;top:38px;font-size:11px;color:{MUTED};transform:translateX(-50%)}}
.tl .m{{position:absolute;top:22px;width:14px;height:14px;border-radius:2px;transform:translateX(-50%)}}
.tl .m.s{{background:{GREEN}}} .tl .m.d{{background:{RED}}} .tl .m.w{{background:{ORANGE}}}
.tl .m.k{{background:{ACCENT};width:10px;height:10px;top:24px}}
.tl .m.o{{background:{MUTED};width:3px;height:14px;border-radius:0}}
.tl .m.x{{top:22px;width:14px;height:14px;background:{BG};border:2px solid {RED}}}
.tl .lbl{{position:absolute;top:0;font-size:11px;transform:translateX(-50%);white-space:nowrap;color:{TEXT}}}
.moments{{display:grid;grid-template-columns:52px 74px 1fr;gap:0;font-size:13.5px}}
.moments div{{padding:5px 0;border-bottom:1px solid {BORDER}}}
.moments .t{{font-weight:700}} .moments .k{{font-size:11px;text-transform:uppercase;letter-spacing:.08em;padding-top:7px}}
.k.death{{color:{RED}}} .k.gank{{color:{ORANGE}}} .k.kill{{color:{GREEN}}} .k.objective{{color:{MUTED}}}
.jg{{display:flex;gap:16px;align-items:center;flex-wrap:wrap;margin-bottom:14px}}
.jg img.ic{{width:48px;height:48px;border-radius:4px;border:1px solid {RED}}}
.phases{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}}
@media (max-width:760px){{.phases{{grid-template-columns:1fr}}}}
.phase{{border-top:1px solid {BORDER};padding:10px 0 0}}
.phase img{{width:100%;max-width:220px;border-radius:4px;display:block;margin:0 0 8px;border:1px solid {BORDER}}}
.phase h3{{margin:0 0 6px;font-size:13.5px;color:{TEXT};display:flex;justify-content:space-between}}
.phase h3 span{{color:{MUTED};font-weight:400;font-size:12px}}
.zl{{display:flex;justify-content:space-between;font-size:12.5px;padding:3px 0;border-bottom:1px solid {BORDER}}}
.zl:last-child{{border-bottom:none}}
.lanes{{display:grid;grid-template-columns:repeat(3,1fr);gap:0;margin-top:14px;border:1px solid {BORDER};border-radius:4px}}
.lane{{padding:8px 14px;text-align:center;border-right:1px solid {BORDER}}} .lane:last-child{{border-right:none}}
.lane .v{{font-size:24px;font-weight:700}} .lane .l{{font-size:11px;color:{MUTED};text-transform:uppercase;letter-spacing:.08em}}
.lane.hot .v{{color:{RED}}}
.tips{{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:0}}
.tips li{{display:flex;gap:12px;align-items:flex-start;padding:9px 0;border-bottom:1px solid {BORDER}}}
.tips li:last-child{{border-bottom:none}}
.tips .i{{flex:0 0 22px;height:22px;border-radius:3px;display:flex;align-items:center;justify-content:center;
  font-weight:800;font-size:12px}}
.tips .warn .i{{color:{BG};background:{ORANGE}}}
.tips .good .i{{color:{BG};background:{GREEN}}}
.tips .info .i{{color:{TEXT};background:{LINE_STRONG}}}
.obj{{display:grid;max-width:520px;grid-template-columns:1fr 110px 110px;gap:4px 18px;font-size:13.5px;align-items:center}}
.obj .h{{font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:{MUTED}}}
.obj .me{{color:{TEAL};font-weight:700;text-align:center}} .obj .en{{color:{RED};font-weight:700;text-align:center}}
.otl{{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}}
.otl span{{font-size:12px;padding:2px 8px;border-radius:3px;border:1px solid {BORDER}}}
.otl .a{{border-color:{TEAL};color:{TEAL}}} .otl .b{{border-color:{RED};color:{RED}}}
.empty{{color:{MUTED};font-style:italic}}
.tw{{overflow-x:auto}}
@media (max-width:600px){{.wrap{{padding:14px 12px 28px}} .panel{{padding:14px}} .bar{{grid-template-columns:100px 1fr 44px}}}}
.footer{{color:{DIM};font-size:12px;margin-top:22px;padding-top:12px;border-top:1px solid {BORDER}}}
.footer b{{color:{MUTED};font-weight:600}}
.warnbox{{border-left:3px solid {ORANGE};background:{PANEL};color:{TEXT};border-radius:0;padding:10px 14px;margin-bottom:14px;
  font-size:13.5px}}
"""


def _chip(rec_roster: dict[str, dict], alias: str | None, label: str, jungler: bool = False) -> str:
    uri = icon_data_uri(alias) if alias else None
    cls = "chip" + (" jgl" if jungler else "") + ("" if uri else " nj")
    img = f'<img src="{uri}" alt="">' if uri else ""
    return f'<span class="{cls}">{img}{_e(label)}</span>'


def _roster_by_alias(record: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for p in record.get("roster") or []:
        if isinstance(p, dict) and p.get("alias"):
            out[str(p["alias"]).lower()] = p
    return out


def _name_for(roster: dict[str, dict], key: Any) -> str:
    k = str(key or "")
    if not k or "?" in k:
        return "ennemi inconnu"
    p = roster.get(k.lower())
    return str((p or {}).get("name") or k)


def _header(record: dict, a: dict) -> str:
    s = a.get("summary") or {}
    alias = s.get("champion") or (record.get("meta") or {}).get("champion") or ""
    name = s.get("champion_name") or alias or "Champion"
    uri = icon_data_uri(alias)
    icon = (f'<img class="champ" src="{uri}" alt="{_e(name)}">' if uri
            else f'<div class="champ ph">{_e((name or "?")[:1])}</div>')
    res = s.get("result")
    badge = ('<span class="badge win">Victoire</span>' if res == "Win" else
             '<span class="badge lose">Défaite</span>' if res == "Lose" else
             '<span class="badge unk">Partie non terminée</span>')
    sub = [x for x in (s.get("position_label"), _date_fr(s.get("start")),
                       "Faille de l'invocateur" if s.get("game_mode") in ("CLASSIC", "") else s.get("game_mode"))
           if x]
    kda = (f'<span class="kda"><b>{_e(s.get("kills", 0))}</b><span class="sl">/</span>'
           f'<b class="d">{_e(s.get("deaths", 0))}</b><span class="sl">/</span><b>{_e(s.get("assists", 0))}</b></span>')
    stats = [
        (kda, "K / D / A"),
        (_e(s.get("duration_text") or _fmt_time(s.get("duration"))), "Durée"),
        (_e(_num(s.get("cs_per_min"))), "CS / min"),
        (_e(_num(s.get("vision_per_min"), 2)), "Vision / min"),
    ]
    hs = "".join(f'<div class="hstat"><div class="v">{v}</div><div class="l">{_e(l)}</div></div>' for v, l in stats)
    return (f'<div class="panel"><div class="hdr">{icon}<div class="title">{badge}'
            f'<h1 class="name">{_e(name)}</h1><div class="sub">{_e(" · ".join(str(x) for x in sub))}</div></div>'
            f'<div class="hstats">{hs}</div></div>{_tldr(a)}</div>')


def _cards(a: dict) -> str:
    s = a.get("summary") or {}
    obj = a.get("objectives") or {}
    mine, theirs = obj.get("mine") or {}, obj.get("theirs") or {}
    kp = s.get("kill_participation")
    ganks = a.get("ganks_faced") or 0
    surv = a.get("ganks_survived") or 0
    cards = [
        ("", _num(s.get("kda_ratio"), 2), "Ratio KDA", f"{s.get('kills', 0)} + {s.get('assists', 0)} / "
                                                     f"{max(1, s.get('deaths') or 0)}"),
        ("t", str(s.get("cs") if s.get("cs") is not None else "-"), "Sbires tués",
         f"{_num(s.get('cs_per_min'))} par minute"),
        ("t", _num(s.get("vision_score"), 0), "Score de vision", f"{_num(s.get('vision_per_min'), 2)} par minute"),
        ("", f"{_num(kp * 100, 0)} %" if kp is not None else "-", "Participation",
         f"{s.get('team_kills')} kills d'équipe" if s.get("team_kills") is not None else "kills d'équipe inconnus"),
        ("g" if ganks and surv / ganks >= 0.5 else "r" if ganks else "g", f"{surv} / {ganks}", "Ganks survécus",
         "alertes DANGER de l'app"),
        ("r", str(a.get("deaths_warned", 0)), "Morts après alerte", _death_card_note(a)),
        ("", str(s.get("level") or "-"), "Niveau final", f"{_num(s.get('gold'), 0)} PO en poche" if s.get("gold")
         is not None else ""),
        ("t", f"{mine.get('dragons', 0)} – {theirs.get('dragons', 0)}" if mine and theirs else "-", "Dragons",
         f"Barons {mine.get('barons', 0)} – {theirs.get('barons', 0)}" if mine and theirs else ""),
    ]
    out = "".join(f'<div class="card {c}"><div class="v">{_e(v)}</div><div class="l">{_e(l)}</div>'
                  f'<div class="x">{_e(x)}</div></div>' for c, v, l, x in cards)
    return f'<div class="cards">{out}</div>'


def _death_card_note(a: dict) -> str:
    """Under "Morts après alerte": who was responsible for the other deaths (app vs duel)."""
    v = a.get("death_verdicts") if isinstance(a.get("death_verdicts"), dict) else None
    if not v:
        return f"{a.get('deaths_unwarned', 0)} mort(s) sans alerte"
    parts = [f"{int(v.get('missed') or 0)} sans alerte de l'app"]
    if v.get("late"):
        parts.append(f"{int(v['late'])} alerte(s) trop tardive(s)")
    if v.get("duel"):
        parts.append(f"{int(v['duel'])} en 1v1")
    if v.get("unseen"):
        parts.append(f"{int(v['unseen'])} ennemis invisibles")
    return " · ".join(parts)


def _gradient_css(stops: list[tuple[float, str]]) -> str:
    return "linear-gradient(90deg," + ",".join(f"{c} {int(p * 100)}%" for p, c in stops) + ")"


def _map_section(record: dict, a: dict) -> str:
    png = render_map_png(record, a)
    uri = _data_uri_png(png)
    s = a.get("summary") or {}
    dur_min = int(round((_f(s.get("duration"), 0.0) or 0.0) / 60.0))
    j = a.get("jungler") or {}
    jname = j.get("name") or "jungler ennemi"
    img = (f'<img class="map" src="{uri}" alt="Carte de la partie">' if uri
           else '<p class="empty">Carte indisponible.</p>')
    legend = (
        '<div class="legend">'
        f'<div class="lg"><span class="grad" style="background:{_gradient_css(HEAT_STOPS)}"></span>'
        '<span>Ma présence (plus clair = plus de temps)</span></div>'
        f'<div class="lg"><span class="grad" style="background:{_gradient_css(MINUTE_STOPS)}"></span>'
        f'<span>{_e(jname)} aperçu · de 0 à {dur_min} min</span></div>'
        '<div class="lg"><span class="lgx">✕</span><span>Mes morts (heure de jeu)</span></div>'
        '</div>')
    zones = a.get("zones") or {}
    groups = [g for g in zones.get("groups") or [] if (g.get("seconds") or 0) > 0]
    groups.sort(key=lambda g: -(g.get("seconds") or 0))
    if groups:
        mx = max(g.get("percent") or 0 for g in groups) or 1
        bars = "".join(
            f'<div class="bar"><span>{_e(g.get("label"))}</span><div class="track"><div class="fill" '
            f'style="width:{max(2.0, 100.0 * (g.get("percent") or 0) / mx):.1f}%"></div></div>'
            f'<span class="pct">{_num(g.get("percent"), 0)} %</span></div>' for g in groups)
        zone_html = (f'<h2 style="margin-top:22px">Mon temps par zone</h2><div class="bars">{bars}</div>'
                     f'<p class="small">Sur {_fmt_time(zones.get("total_s"))} de positions connues '
                     f'(icône visible sur la minimap).</p>')
    else:
        zone_html = '<p class="empty">Aucune position enregistrée.</p>'
    return (f'<div class="panel"><h2>Carte de la partie</h2><div class="grid2"><div>{img}</div>'
            f'<div>{legend}{zone_html}</div></div></div>')


def _deaths_section(record: dict, a: dict) -> str:
    deaths = a.get("deaths") or []
    roster = _roster_by_alias(record)
    if not deaths:
        return ('<div class="panel"><h2>Mes morts</h2><p class="empty">Aucune mort : partie parfaite de ce côté-là !'
                '</p></div>')
    rows = []
    for d in deaths:
        chips = []
        seen = set()
        killer = d.get("killer") or {}
        actors = ([killer] if killer else []) + list(d.get("assisters") or [])
        for ac in actors:
            if not isinstance(ac, dict) or ac.get("is_me"):
                continue
            key = ac.get("alias") or ac.get("label")
            if key in seen:
                continue
            seen.add(key)
            if ac.get("kind") == "champion" and ac.get("team") and ac.get("team") == (a.get("summary") or {}).get("team"):
                continue
            chips.append(_chip(roster, ac.get("alias"), ac.get("label") or "?", bool(ac.get("is_jungler"))))
        for k in d.get("involved") or []:
            if k in seen or "?" in str(k):
                continue
            seen.add(k)
            chips.append(_chip(roster, k, _name_for(roster, k)))
        who = f'<div class="who">{"".join(chips)}</div>' if chips else '<span class="small">inconnu</span>'
        if d.get("warned"):
            lead = d.get("alert_lead_s") if d.get("alert_lead_s") is not None else d.get("alert_before_s")
            tag = f'<span class="tag ok">Oui, {_num(lead, 0)} s avant</span>'
            verdict = d.get("verdict") or "alerte ignorée"
        else:
            tag = '<span class="tag no">Non</span>'
            verdict = d.get("verdict") or "mort sans alerte"
        if d.get("app_fault"):
            verdict += " (faute de l'app)"
        near = d.get("nearby") or []
        near_txt = (", ".join(_name_for(roster, k) for k in near) if near else "personne")
        rows.append(
            f'<tr><td class="t">{_e(d.get("time"))}</td><td>{_e(d.get("zone_label") or _ZONE_FR_FALLBACK)}</td>'
            f'<td>{who}<div class="recap">Visibles à proximité : {_e(near_txt)}</div></td>'
            f'<td>{"<b style=color:" + ORANGE + ">Oui</b>" if d.get("jungler_involved") else "Non"}'
            f'{_unseen_note(d)}</td>'
            f'<td>{tag}<div class="recap">{_e(verdict)}</div></td></tr>'
            f'<tr><td></td><td colspan="4" class="recap" style="padding-top:0">« {_e(d.get("recap"))} »</td></tr>')
    lead = a.get("alert_lead") if isinstance(a.get("alert_lead"), dict) else {}
    note = ""
    if lead.get("n"):
        note = (f'<p class="small">Avance des alertes avant tes morts : {_num(lead.get("mean"), 1)} s en moyenne '
                f'(objectif : 5 s ou plus pour avoir le temps de reculer)'
                + (f' · {lead.get("late")} trop tardive(s) (moins de 3 s)' if lead.get("late") else "") + '.</p>')
    return ('<div class="panel"><h2>Mes morts</h2>' + note + '<div class="tw"><table><thead><tr><th>Heure</th><th>Zone</th>'
            '<th>Tué par</th><th>Jungler ?</th><th>Alerte donnée ?</th></tr></thead><tbody>' + "".join(rows)
            + '</tbody></table></div></div>')


def _unseen_note(d: dict) -> str:
    u = _f(d.get("jungler_unseen_s"))
    if u is None:
        return ""
    if d.get("jungler_unseen"):
        return f'<div class="recap" style="color:{RED}">invisible depuis {_e(_fmt_time(u))}</div>'
    return f'<div class="recap">vu {_e(int(u))} s avant</div>'


def _ganks_section(record: dict, a: dict) -> str:
    ganks = a.get("ganks") or []
    s = a.get("summary") or {}
    dur = max(60.0, _f(s.get("duration"), 0.0) or 0.0)
    roster = _roster_by_alias(record)
    ticks = "".join(f'<span class="tick" style="left:{100.0 * m * 60 / dur:.2f}%">{m} min</span>'
                    for m in range(0, int(dur // 60) + 1, 5))
    marks = []
    for g in ganks:
        x = 100.0 * (_f(g.get("game_time"), 0.0) or 0.0) / dur
        cls = "d" if g.get("outcome") == "death" else "s"
        title = f'{g.get("time")} · {g.get("text")} ({g.get("outcome_label")})'
        marks.append(f'<span class="m {cls}" style="left:{x:.2f}%" title="{_e(title)}"></span>'
                     f'<span class="lbl" style="left:{x:.2f}%">{_e(g.get("time"))}</span>')
    death_marks = "".join(
        f'<span class="m x" style="left:{100.0 * (_f(d.get("game_time"), 0.0) or 0.0) / dur:.2f}%" '
        f'title="Mort à {_e(d.get("time"))}"></span>'
        for d in a.get("deaths") or [] if not any(
            g.get("death_time") == d.get("game_time") for g in ganks))
    timeline = (f'<div class="tl"><div class="axis"></div>{ticks}{death_marks}{"".join(marks)}</div>'
                f'<p class="small"><span style="color:{GREEN}">■</span> gank survécu · '
                f'<span style="color:{RED}">■</span> mort dans les 15 s · '
                f'<span style="color:{RED}">□</span> mort hors gank annoncé</p>')
    if not ganks:
        body = '<p class="empty">Aucune alerte DANGER de gank pendant cette partie.</p>'
    else:
        rows = []
        for g in ganks:
            names = g.get("aliases") or []
            chips = "".join(_chip(roster, al, _name_for(roster, al), False) for al in names) or \
                '<span class="small">plusieurs ennemis</span>'
            kinds = ", ".join(_KIND_FR.get(k, k) for k in g.get("kinds") or [])
            lead = (f'prévenu {_num(g.get("warning_lead_s"), 0)} s avant (ATTENTION)'
                    if g.get("warning_lead_s") is not None else "")
            out = ('<span class="tag no">Mort</span>' if g.get("outcome") == "death"
                   else '<span class="tag sv">Survécu</span>')
            rows.append(f'<tr><td class="t">{_e(g.get("time"))}</td><td>{_e(kinds)}</td>'
                        f'<td><div class="who">{chips}</div><div class="recap">« {_e(g.get("text"))} » {_e(lead)}</div></td>'
                        f'<td>{out}</td></tr>')
        body = ('<div class="tw"><table><thead><tr><th>Heure</th><th>Type</th><th>Ennemis</th><th>Issue</th></tr></thead><tbody>'
                + "".join(rows) + '</tbody></table></div>')
    return f'<div class="panel"><h2>Ganks subis</h2>{timeline}{body}</div>'


def _jungler_section(record: dict, a: dict) -> str:
    j = a.get("jungler") or {}
    if not j.get("known"):
        return ('<div class="panel"><h2>Jungler ennemi</h2><p class="empty">Jungler ennemi non identifié '
                '(aucun ennemi avec Châtiment).</p></div>')
    from treeaicoach.analysis import PHASES, PHASE_LABELS_FR

    my_team = (a.get("summary") or {}).get("team")
    uri = icon_data_uri(j.get("alias"))
    icon = f'<img class="ic" src="{uri}" alt="">' if uri else ""
    if j.get("first_seen") is not None:
        first = (f'Première apparition à <b>{_e(j.get("first_seen_time"))}</b> '
                 f'({_e(j.get("first_zone_label"))})')
    else:
        first = "Jamais aperçu sur la minimap"
    head = (f'<div class="jg">{icon}<div><div style="font-size:20px;font-weight:700">{_e(j.get("name"))}</div>'
            f'<div class="small">{first} · {j.get("appearances", 0)} apparitions · '
            f'{_num(j.get("visible_s"), 0)} s visible au total · a participé à {j.get("kills_involved", 0)} '
            f'kill(s) sur ton équipe, dont {j.get("my_deaths", 0)} sur toi</div></div></div>')
    phases = []
    for name, t0, t1 in PHASES:
        zones = (j.get("by_phase") or {}).get(name) or {}
        png = render_phase_map_png(record, a, t0, t1)
        img = f'<img src="{_data_uri_png(png)}" alt="">' if png else ""
        total = sum(zones.values())
        lines = "".join(f'<div class="zl"><span>{_e(_zone_fr(z, my_team))}</span><b>{n}</b></div>'
                        for z, n in sorted(zones.items(), key=lambda kv: -kv[1]))
        phases.append(f'<div class="phase">{img}<h3>{_e(PHASE_LABELS_FR.get(name, name))}'
                      f'<span>{total} apparition{"s" if total > 1 else ""}</span></h3>'
                      f'{lines or "<div class=empty>jamais vu</div>"}</div>')
    lanes = j.get("ganks_by_lane") or {}
    top = max((lanes.get(k, 0) for k in ("top", "mid", "bot")), default=0)
    lane_html = "".join(
        f'<div class="lane{" hot" if top and lanes.get(k, 0) == top else ""}"><div class="v">{lanes.get(k, 0)}</div>'
        f'<div class="l">{lbl}</div></div>'
        for k, lbl in (("top", "Kills en haut"), ("mid", "Kills au milieu"), ("bot", "Kills en bas")))
    try:
        path_html = _pathing_html(record, a)
    except Exception:
        log.exception("pathing section failed")
        path_html = ""
    return (f'<div class="panel"><h2>Parcours du jungler ennemi</h2>{head}<div class="phases">{"".join(phases)}</div>'
            f'<div class="lanes">{lane_html}</div><p class="small">Voies où il a participé à un kill sur ton équipe '
            f'(zone du kill, sinon rôle de la victime).</p>{path_html}</div>')


def _zone_fr(zone: str, my_team: Any) -> str:
    try:
        from treeaicoach import geometry

        return geometry.zone_name_fr(zone, my_team) or zone
    except Exception:
        return str(zone)


def _objectives_section(a: dict) -> str:
    obj = a.get("objectives") or {}
    mine, theirs = obj.get("mine"), obj.get("theirs")
    if not mine or not theirs:
        teams = obj.get("teams") or {}
        mine, theirs = teams.get("ORDER"), teams.get("CHAOS")
        la, lb = "Bleus", "Rouges"
    else:
        la, lb = "Ton équipe", "Ennemis"
    if not mine or not theirs:
        return ""
    rows = [("dragons", "Dragons"), ("elder", "Dragons ancestraux"), ("grubs", "Larves du Néant"),
            ("heralds", "Hérauts"), ("atakhan", "Atakhan"), ("barons", "Barons"), ("turrets", "Tourelles"),
            ("inhibitors", "Inhibiteurs")]
    grid = (f'<div class="obj"><span class="h">Objectif</span><span class="h">{la}</span><span class="h">{lb}</span>'
            + "".join(f'<span>{_e(lbl)}</span><span class="me">{mine.get(k, 0)}</span>'
                      f'<span class="en">{theirs.get(k, 0)}</span>' for k, lbl in rows
                      if mine.get(k, 0) or theirs.get(k, 0) or k in ("dragons", "barons", "turrets")) + '</div>')
    tl = "".join(f'<span class="{"a" if o.get("mine") else "b"}">{_e(o.get("time"))} {_e(o.get("label"))}</span>'
                 for o in obj.get("timeline") or [] if o.get("kind") not in ("turrets",))
    try:
        pres = _objective_presence_html(a)
    except Exception:
        log.exception("objective presence failed")
        pres = ""
    return (f'<div class="panel"><h2>Objectifs</h2>{grid}'
            f'{"<div class=otl>" + tl + "</div>" if tl else ""}{pres}</div>')


def _signed_cls(v: Any) -> str:
    x = _f(v, 0.0) or 0.0
    return "me" if x > 0 else "en" if x < 0 else ""


def _scoreboard_section(a: dict) -> str:
    """Tab scoreboard at the end of the game (lane matchups by item gold) + praise received."""
    sb = a.get("scoreboard") or {}
    if not isinstance(sb, dict):
        return ""
    praise = [p for p in sb.get("praise") or [] if isinstance(p, dict)]
    if not sb.get("available") and not praise:
        return ""
    body = ""
    if sb.get("available"):
        rows = "".join(
            f'<span>{_e(m.get("role_short"))}{" ★" if m.get("involves_me") else ""}</span>'
            f'<span>{_e(m.get("ally"))} vs {_e(m.get("enemy"))}</span>'
            f'<span class="{_signed_cls(m.get("gold_diff"))}">{_e(m.get("gold_label"))}</span>'
            f'<span class="{_signed_cls(m.get("cs_diff"))}">{int(_f(m.get("cs_diff"), 0) or 0):+d} CS</span>'
            for m in sb.get("matchups") or [] if isinstance(m, dict))
        head = (f'<p>Équipe : <b>{_e(sb.get("team_gold_label"))}</b> (valeur des objets, écran Tab) · kills '
                f'{int(_f(sb.get("ally_kills"), 0) or 0)}–{int(_f(sb.get("enemy_kills"), 0) or 0)}</p>')
        grid = ('<div class="obj" style="grid-template-columns:auto 1fr auto auto">'
                '<span class="h">Rôle</span><span class="h">Duel</span><span class="h">Or</span>'
                f'<span class="h">CS</span>{rows}</div>') if rows else ""
        notes = []
        if sb.get("fed"):
            notes.append("Ennemis très avancés : " + _e(", ".join(sb["fed"])))
        if sb.get("struggling"):
            notes.append("Alliés en difficulté : " + _e(", ".join(sb["struggling"])))
        if sb.get("spikes"):
            notes.append("Pics de puissance : " + _e(" · ".join(sb["spikes"][-5:])))
        body = head + grid + "".join(f'<p class="empty" style="font-style:normal">{n}</p>' for n in notes)
    if praise:
        lis = "".join(f'<li class="good"><span class="i">✓</span><span>{_e(p.get("time"))} · {_e(p.get("text"))}'
                      f'</span></li>' for p in praise[:12])
        body += f'<h2 style="margin-top:20px">Bien joué !</h2><ul class="tips">{lis}</ul>'
    return f'<div class="panel"><h2>Tableau des scores (Tab)</h2>{body}</div>'


def _selfcheck_section(record: dict) -> str:
    """"Santé de TreeAI" of the game (selfcheck.py, ``record["selfcheck"]``): what went wrong in our
    own pipeline (capture, minimap, detection, voice, API...) and what was fixed automatically."""
    sc = record.get("selfcheck") if isinstance(record, dict) else None
    if not isinstance(sc, dict):
        return ""
    probs = [p for p in sc.get("problems") or [] if isinstance(p, dict)]
    head = '<div class="panel"><h2>Santé de TreeAI pendant la partie</h2>'
    if not probs:
        return (head + '<p class="small">Rien à signaler : capture, minimap, détection, voix et API du jeu '
                'ont fonctionné normalement.</p></div>')
    sym = {"good": "✓", "warn": "!", "info": "i"}
    lis = []
    for p in probs:
        kind = "good" if p.get("outcome") == "fixed" else ("warn" if p.get("outcome") == "open" else "info")
        if p.get("rule") == "adapt":            # automatic adaptations of this PC: shown, not a fault
            kind, p = "info", dict(p, outcome_fr="adaptation automatique", actions=[])
        when = f" à {_fmt_time(p.get('first_gt'))}" if p.get("first_gt") is not None else ""
        dur = p.get("active_s")
        dur_txt = f", {int(round(float(dur)))} s" if isinstance(dur, (int, float)) and dur >= 1 else ""
        count = int(p.get("count") or 1)
        times = f" ({count} fois)" if count > 1 else ""
        acts = [str(x) for x in p.get("actions") or []]
        fix = f" Actions : {_e('; '.join(acts))}." if acts else ""
        lis.append(f'<li class="{kind}"><span class="i">{sym[kind]}</span><span><b>{_e(p.get("label"))}</b>'
                   f'{_e(when)}{times}{_e(dur_txt)} : {_e(p.get("status"))}.{fix} '
                   f'<i>{_e(p.get("outcome_fr") or "")}</i></span></li>')
    extra = ""
    if sc.get("profile_max") not in (None, "", "normal"):
        extra = (f'<p class="small">Analyse allégée automatiquement pendant la partie (niveau le plus bas : '
                 f'{_e(sc.get("profile_max"))}) pour garder la détection fluide.</p>')
    if sc.get("diagnostic"):
        extra += ('<p class="small">Un diagnostic automatique de 60 s a été enregistré (dossier diagnostics) : '
                  'joins-le à ton signalement.</p>')
    return head + f'<ul class="tips">{"".join(lis)}</ul>{extra}</div>'


def _tips_section(a: dict) -> str:
    items = a.get("tip_items") or [{"text": t, "kind": "info"} for t in a.get("tips") or []]
    if not items:
        return ""
    sym = {"warn": "!", "good": "✓", "info": "i"}
    lis = "".join(f'<li class="{_e(t.get("kind", "info"))}"><span class="i">{sym.get(t.get("kind"), "i")}</span>'
                  f'<span>{_e(t.get("text"))}</span></li>' for t in items)
    return f'<div class="panel"><h2>Conseils pour la prochaine partie</h2><ul class="tips">{lis}</ul></div>'


# ======================================================================================
# rated plays (plays.py, chess.com style) : précision + counts + best / worst badges
# ======================================================================================
def play_badge_image(cls: str, title: str, reason: str = "") -> Any:
    """The in-game badge of a rated play (``fx_render.render_frame(cls, title, reason, 1.2)``) as a
    cropped RGBA PIL image, None if unavailable. Never raises."""
    try:
        from PIL import Image

        from treeaicoach import fx_render

        bgra = fx_render.render_frame(str(cls), str(title or ""), str(reason or ""), 1.2)
        if bgra is None:
            return None
        a = bgra[..., 3]
        ys, xs = np.nonzero(a > 8)
        if not len(xs):
            return None
        crop = bgra[ys.min():ys.max() + 1, xs.min():xs.max() + 1].astype(np.float32)
        alpha = crop[..., 3:4] / 255.0
        rgb = np.where(alpha > 0, crop[..., :3] / np.maximum(alpha, 1e-6), 0.0)[..., ::-1]
        out = np.dstack([np.clip(rgb, 0, 255), crop[..., 3]]).astype(np.uint8)
        return Image.fromarray(np.ascontiguousarray(out), "RGBA")
    except Exception:
        log.debug("play badge unavailable", exc_info=True)
        return None


def _class_hex(cls: str) -> str:
    try:
        from treeaicoach import fx_render

        r, g, b = fx_render.CLASS_RGB.get(cls, (139, 148, 143))
        return f"#{r:02X}{g:02X}{b:02X}"
    except Exception:
        return MUTED


def play_summary(record: dict) -> dict | None:
    try:
        from treeaicoach import plays

        return plays.summary_from_record(record)
    except Exception:
        return None


def _plays_section(record: dict) -> str:
    summ = play_summary(record)
    if not summ or not summ.get("total"):
        return ""
    from treeaicoach import plays

    counts = summ.get("counts") or {}
    prec = int(summ.get("precision") or 0)
    pcol = GREEN if prec >= 75 else ORANGE if prec >= 50 else RED
    cells = "".join(
        f'<div class="pc"><b style="color:{_class_hex(c)}">{int(counts.get(c, 0) or 0)}</b>'
        f'<span>{_e(plays.LABEL_FR.get(c, c))}</span></div>' for c in plays.CLASSES)

    def rows(items: list) -> str:
        out = []
        for d in items[:3]:
            cls = str(d.get("cls") or "")
            title = str(d.get("title") or plays.TITLE_FR.get(cls, cls))
            img = play_badge_image(cls, title, str(d.get("reason") or ""))
            uri = None
            if img is not None:
                buf = io.BytesIO()
                img.save(buf, "PNG", optimize=True)
                uri = _data_uri_png(buf.getvalue())
            body = (f'<img class="badge-img" src="{uri}" alt="{_e(title)}">' if uri else
                    f'<b style="color:{_class_hex(cls)}">{_e(title)}</b> {_e(d.get("reason") or "")}')
            out.append(f'<div class="prow"><span class="t">{_e(_fmt_time(d.get("gt")))}</span>{body}</div>')
        return "".join(out) or '<p class="empty">Rien à signaler.</p>'

    return (f'<div class="panel"><h2>Coups notés</h2><div class="plays-top">'
            f'<div class="prec"><div class="v" style="color:{pcol}">{prec}</div><div class="l">Précision</div></div>'
            f'<div class="pcounts">{cells}</div></div>'
            f'<div class="grid2 pgrid"><div><h3 class="ph3">Tes meilleurs coups</h3>{rows(summ.get("best") or [])}</div>'
            f'<div><h3 class="ph3">À corriger</h3>{rows(summ.get("worst") or [])}</div></div></div>')


# ======================================================================================
# game understanding (mastermind.py): comps, power windows, plan vs what happened
# ======================================================================================
def _understanding_section(record: dict) -> str:
    """"Compréhension de la partie": both compositions, the plan at the start (windows, win
    conditions, my role), who was stronger when (bar), what the team did in each window, the
    threats. Empty for a record without a roster. Never raises (the caller catches)."""
    from treeaicoach import mastermind

    u = mastermind.understanding(record)
    if not u:
        return ""
    dur = max(1.0, float(record.get("duration") or 0.0) or max([b for _a, b, _l in u.get("fenetres") or []] or [1.0]))
    col = {"us": GREEN, "them": RED, "even": MUTED}
    bar = "".join(f'<span title="{_e(mastermind.clock(a))}-{_e(mastermind.clock(b))}" style="flex:{max(0.5, b - a):.0f};'
                  f'background:{col.get(lv, MUTED)}"></span>' for a, b, lv in u.get("fenetres") or [] if b > a)
    ticks = "".join(f'<span style="left:{100.0 * m * 60 / dur:.1f}%">{m}</span>' for m in range(0, int(dur // 60) + 1, 5))

    def comp(title: str, d: dict) -> str:
        return (f'<div><h3 class="ph3">{_e(title)}</h3><p>{_e(d.get("resume"))}</p>'
                + (f'<p class="small">Carry : {_e(d.get("carry"))}</p>' if d.get("carry") else "") + "</div>")

    plan = u.get("plan") or {}
    lis = [f'<li class="info"><span class="i">i</span><span><b>Fenêtre :</b> {_e(plan.get("fenetre"))}</span></li>']
    if plan.get("voie"):
        lis.append(f'<li class="info"><span class="i">i</span><span><b>Ta voie :</b> {_e(plan.get("voie"))}</span></li>')
    if plan.get("jungle"):
        lis.append(f'<li class="info"><span class="i">i</span><span><b>Jungle :</b> {_e(plan.get("jungle"))}</span></li>')
    lis += [f'<li class="good"><span class="i">✓</span><span>{_e(x)}</span></li>' for x in plan.get("nous") or []]
    lis += [f'<li class="warn"><span class="i">!</span><span>Leur plan : {_e(x)}</span></li>' for x in plan.get("eux") or []]
    did = "".join(f'<li class="{"good" if "bien joué" in x or "limité" in x else "warn"}"><span class="i">'
                  f'{"✓" if "bien joué" in x or "limité" in x else "!"}</span><span>{_e(x)}</span></li>'
                  for x in u.get("fait") or [])
    role = f'<p><b>Ton rôle :</b> {_e(u.get("role"))}</p>' if u.get("role") else ""
    thr = "".join(f'<li class="warn"><span class="i">!</span><span><b>{_e(t.get("c"))}</b> '
                  f'{_e(", ".join(t.get("pourquoi") or []))}{" (" + _e(", ".join(t.get("tags"))) + ")" if t.get("tags") else ""}'
                  f'</span></li>' for t in u.get("menaces") or [])
    return (f'<div class="panel"><h2>Compréhension de la partie</h2><div class="grid2">{comp("Ton équipe", u.get("nous") or {})}'
            f'{comp("Équipe adverse", u.get("eux") or {})}</div>'
            f'<h3 class="ph3">Le plan au début</h3><ul class="tips">{"".join(lis)}</ul>'
            + (f'<p class="small">{_e(plan.get("mon_role"))}</p>' if plan.get("mon_role") else "")
            + f'<h3 class="ph3">Qui était le plus fort</h3><div class="mmbar">{bar}</div><div class="mmticks">{ticks}</div>'
            f'<p class="small"><span style="color:{GREEN}">■</span> ton équipe · <span style="color:{RED}">■</span> '
            f'adversaires · <span style="color:{MUTED}">■</span> égal (courbes de puissance + or du tableau Tab)</p>'
            + (f'<h3 class="ph3">Ce que l\'équipe a fait</h3><ul class="tips">{did}</ul>' if did else "")
            + role + (f'<h3 class="ph3">Menaces en fin de partie</h3><ul class="tips">{thr}</ul>' if thr else "")
            + "</div>")


CSS_MM = f"""
.mmbar{{display:flex;height:14px;border:1px solid {BORDER};margin:6px 0 2px}} .mmbar span{{display:block;height:100%}}
.mmticks{{position:relative;height:14px;font-size:10px;color:{MUTED}}} .mmticks span{{position:absolute;transform:translateX(-50%)}}
"""


CSS_PLAYS = f"""
.plays-top{{display:flex;gap:24px;align-items:center;flex-wrap:wrap;margin-bottom:14px}}
.prec .v{{font-size:40px;font-weight:700;line-height:1}} .prec .l{{font-size:11px;color:{MUTED};text-transform:uppercase;
  letter-spacing:.1em}}
.pcounts{{display:grid;grid-template-columns:repeat(4,minmax(96px,1fr));gap:0;flex:1;border:1px solid {BORDER};
  border-radius:4px}}
.pc{{padding:6px 10px;border-right:1px solid {BORDER};border-bottom:1px solid {BORDER}}}
.pc b{{font-size:18px;display:block}} .pc span{{font-size:11.5px;color:{MUTED}}}
.ph3{{font-size:12px;color:{MUTED};margin:0 0 6px;font-weight:600}}
.prow{{display:flex;gap:10px;align-items:center;padding:4px 0;border-bottom:1px solid {BORDER}}}
.prow .t{{font-weight:700;min-width:42px}}
.badge-img{{height:44px;width:auto;max-width:100%;display:block}}
"""


def summary_lines(a: dict) -> list[str]:
    """The 3-line summary at the top of the report, in plain French (HTML-free text)."""
    s = a.get("summary") or {}
    lines: list[str] = []
    res = {"Win": "Victoire", "Lose": "Défaite"}.get(str(s.get("result") or ""), "Partie")
    champ = s.get("champion_name") or s.get("champion") or ""
    dur = s.get("duration_text") or _fmt_time(s.get("duration"))
    kda = f'{s.get("kills", 0)}/{s.get("deaths", 0)}/{s.get("assists", 0)}'
    first = f"{res} en {dur}" + (f" avec {champ}" if champ else "") + f" : {kda}"
    if s.get("cs_per_min") is not None:
        first += f", {_num(s.get('cs_per_min'))} CS/min"
    lines.append(first + ".")
    deaths = int(s.get("deaths") or 0)
    warned = int(a.get("deaths_warned") or 0)
    ganks, surv = int(a.get("ganks_faced") or 0), int(a.get("ganks_survived") or 0)
    second = (f"{deaths} mort{'s' if deaths > 1 else ''}" if deaths else "Aucune mort")
    if warned:
        second += f", dont {warned} juste après une alerte"
    if ganks:
        second += f". Ganks : {surv} évité{'s' if surv > 1 else ''} sur {ganks}"
    prec = (a.get("plays") or {}).get("precision") if isinstance(a.get("plays"), dict) else None
    if isinstance(prec, (int, float)):
        second += f". Précision des coups : {int(prec)}/100"
    lines.append(second + ".")
    items = a.get("tip_items") or [{"text": t, "kind": "warn"} for t in a.get("tips") or []]
    tip = next((t.get("text") for t in items if t.get("kind") == "warn" and t.get("text")), None) or \
        next((t.get("text") for t in items if t.get("text")), None)
    lines.append(f"À travailler : {tip}" if tip else "Rien de grave à corriger : continue comme ça.")
    return [ln.replace(chr(0x2014), "-") for ln in lines]


def _tldr(a: dict) -> str:
    lines = summary_lines(a)
    if not lines:
        return ""
    body = "".join(f"<p>{_e(ln)}</p>" for ln in lines[:2])
    if len(lines) > 2:
        head, _sep, rest = lines[2].partition(" : ")
        body += (f"<p><b>{_e(head)} :</b> {_e(rest)}</p>" if rest else f"<p>{_e(lines[2])}</p>")
    return f'<div class="tldr">{body}</div>'


_MOMENT_FR = {"death": "Mort", "gank": "Gank", "kill": "Kill", "objective": "Objectif"}
_OBJECTIVE_EVENTS = {"DragonKill": "Dragon", "BaronKill": "Baron", "HeraldKill": "Héraut", "HordeKill": "Larves",
                     "AtakhanKill": "Atakhan"}


def key_moments(record: dict, a: dict, limit: int = 24) -> list[dict[str, Any]]:
    """Key moments of the game, chronological: my deaths, ganks (with outcome), my kills, objectives.

    Each item: ``{"t", "time", "kind", "label"}``. Pure, never raises.
    """
    out: list[dict[str, Any]] = []
    try:
        for d in a.get("deaths") or []:
            t = _f(d.get("game_time"))
            if t is None:
                continue
            who = ", ".join(d.get("involved_names") or []) or "ennemi inconnu"
            verdict = d.get("verdict")
            out.append({"t": t, "kind": "death", "label": f"Mort face à {who}" + (f" ({verdict})" if verdict else "")})
        for g in a.get("ganks") or []:
            t = _f(g.get("game_time"))
            if t is None:
                continue
            names = ", ".join(g.get("names") or []) or "plusieurs ennemis"
            outcome = "évité" if g.get("outcome") != "death" else "mort"
            out.append({"t": t, "kind": "gank", "label": f"Gank de {names} : {outcome}"})
        roster = [p for p in record.get("roster") or [] if isinstance(p, dict)]
        me = next((p for p in roster if p.get("is_me")), {})
        my_names = {str(me.get(k) or "").lower() for k in ("summoner_name", "riot_id", "name", "alias")} - {""}
        my_names |= {n.split("#")[0] for n in my_names}
        names: dict[str, str] = {}
        for p in roster:
            for k in ("summoner_name", "riot_id"):
                if p.get(k):
                    names[str(p[k]).lower()] = str(p.get("name") or p.get("alias") or p[k])
                    names[str(p[k]).split("#")[0].lower()] = str(p.get("name") or p.get("alias") or p[k])
        my_team = str(me.get("team") or "")
        for ev in record.get("events") or []:
            if not isinstance(ev, dict):
                continue
            t = _f(ev.get("EventTime"))
            if t is None:
                continue
            n = ev.get("EventName")
            if n == "ChampionKill" and str(ev.get("KillerName") or "").lower() in my_names:
                v = str(ev.get("VictimName") or "?")
                out.append({"t": t, "kind": "kill", "label": f"Tu tues {names.get(v.lower(), v)}"})
            elif n in _OBJECTIVE_EVENTS:
                killer = str(ev.get("KillerName") or "").lower()
                team = next((str(p.get("team") or "") for p in roster
                             if killer and killer in (str(p.get("summoner_name") or "").lower(),
                                                      str(p.get("riot_id") or "").lower())), "")
                side = "" if not team or not my_team else (" pour ton équipe" if team == my_team else " pour l'ennemi")
                out.append({"t": t, "kind": "objective", "label": f"{_OBJECTIVE_EVENTS[n]}{side}"})
    except Exception:
        log.exception("key_moments failed")
    out.sort(key=lambda m: m["t"])
    # keep every death and gank; trim kills / objectives first if too many
    if len(out) > limit:
        prio = {"death": 0, "gank": 1, "kill": 2, "objective": 3}
        keep = sorted(out, key=lambda m: (prio.get(m["kind"], 9), m["t"]))[:limit]
        out = sorted(keep, key=lambda m: m["t"])
    for m in out:
        m["time"] = _fmt_time(m["t"])
    return out


def _moments_section(record: dict, a: dict) -> str:
    moments = key_moments(record, a)
    if not moments:
        return ""
    s = a.get("summary") or {}
    dur = max(60.0, _f(s.get("duration"), 0.0) or 0.0, max(m["t"] for m in moments))
    cls = {"death": "x", "gank": "w", "kill": "k", "objective": "o"}
    ticks = "".join(f'<span class="tick" style="left:{100.0 * m * 60 / dur:.2f}%">{m}</span>'
                    for m in range(0, int(dur // 60) + 1, 5))
    marks = "".join(f'<span class="m {cls.get(m["kind"], "o")}" style="left:{100.0 * m["t"] / dur:.2f}%" '
                    f'title="{_e(m["time"])} · {_e(m["label"])}"></span>' for m in moments)
    rows = "".join(f'<div class="t">{_e(m["time"])}</div><div class="k {m["kind"]}">'
                   f'{_e(_MOMENT_FR.get(m["kind"], m["kind"]))}</div><div>{_e(m["label"])}</div>' for m in moments)
    legend = (f'<p class="small"><span style="color:{RED}">□</span> mort · <span style="color:{ORANGE}">■</span> gank · '
              f'<span style="color:{GREEN}">■</span> kill · <span style="color:{MUTED}">|</span> objectif · '
              f'minutes sous l\'axe</p>')
    return (f'<div class="panel"><h2>Moments clés</h2><div class="tl"><div class="axis"></div>{ticks}{marks}</div>'
            f'{legend}<div class="moments">{rows}</div></div>')


def render_report_html(record: dict, analysis: dict | None = None, *, lcu_pending: bool = False) -> str:
    """Self-contained French HTML report. Never raises (degraded page on error).

    ``lcu_pending``: the League Client data is still being fetched (banner + bounded auto-reload)."""
    try:
        rec = record if isinstance(record, dict) else {}
        if analysis is None:
            from treeaicoach.analysis import analyze_game

            analysis = analyze_game(rec)
        a = analysis if isinstance(analysis, dict) else {}
        if "plays" not in a:
            ps = play_summary(rec)
            if ps:
                a = dict(a, plays=ps)
        s = a.get("summary") or {}
        title = f'{s.get("champion_name") or "Partie"} · {s.get("result_label") or ""}'.strip(" ·")
        parts = []
        sections = (lambda: _header(rec, a), lambda: _cards(a), lambda: _tips_section(a),
                   lambda: _plays_section(rec), lambda: _understanding_section(rec),
                   lambda: _moments_section(rec, a), lambda: _voice_box(a),
                   lambda: _phases_section(a), lambda: _map_section(rec, a), lambda: _truth_section(rec, a),
                   lambda: _presence_section(rec, a),
                   lambda: _positioning_section(a),
                   lambda: _deaths_section(rec, a), lambda: _ganks_section(rec, a),
                   lambda: _jungler_section(rec, a), lambda: _objectives_section(a),
                   lambda: _scoreboard_section(a), lambda: _selfcheck_section(rec), lambda: _trends_section(a))
        if a.get("limited_mode"):        # ARAM / Arena: no minimap analysis -> header + honest tips only
            sections = (lambda: _header(rec, a), lambda: _tips_section(a))
        for fn in sections:
            try:
                parts.append(fn())
            except Exception:
                log.exception("report section failed")
        warn = ""
        if rec.get("incomplete") or s.get("result") is None:
            warn = ('<div class="warnbox">Enregistrement incomplet (partie interrompue ou application fermée '
                    'avant la fin) : les chiffres couvrent uniquement la partie enregistrée.</div>')
        if a.get("limited_mode"):
            warn += (f'<div class="warnbox">Mode {_e(a["limited_mode"])} : TreeAI analyse en direct seulement la '
                     "Faille de l'invocateur. Ce rapport se limite à tes statistiques.</div>")
        refresh = ""
        if lcu_pending:
            warn += _pending_html()
            refresh = f'<meta http-equiv="refresh" content="{PENDING_RELOAD_S}">'
        version = (rec.get("meta") or {}).get("app_version") or __version__
        footer = (f'<div class="footer"><b>{_e(APP_NAME)}</b> v{_e(version)} · rapport généré localement le '
                  f'{_e(_date_fr(_dt.datetime.now().astimezone().isoformat()))} · aucune donnée envoyée · '
                  f'sources : capture de la minimap et API Live Client de Riot</div>')
        page = ("<!DOCTYPE html>\n<html lang=\"fr\"><head><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">" + refresh +
                f"<title>{_e(APP_NAME)} · {_e(title)}</title><style>{CSS}{CSS_V2}{CSS_TRUTH}{CSS_PLAYS}{CSS_MM}</style></head>"
                f"<body><div class=\"wrap\">{warn}{''.join(parts)}{footer}</div></body></html>")
        # design rule (docs/DESIGN.md): no em dash, even in texts coming from other modules
        em = chr(0x2014)
        return page.replace(f" {em} ", " · ").replace(em, "-")
    except Exception:
        log.exception("render_report_html failed")
        return ("<!DOCTYPE html><html lang=\"fr\"><head><meta charset=\"utf-8\"><title>Rapport</title></head>"
                f"<body style=\"background:{BG};color:{TEXT};font-family:sans-serif\">"
                "<p>Le rapport n'a pas pu être généré.</p></body></html>")


# ======================================================================================
# v2 coaching visuals: presence vs ideal, exposure, jungler pathing, trends
# ======================================================================================
SMALL_MAP = 240
PATH_MAP = 400


def _png(img: Any) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_ideal_png(record: dict, role: str, phase: str = "laning", size: int = SMALL_MAP) -> bytes | None:
    """Minimap where every zone group is lit by the ideal share of time for ``role`` in ``phase``."""
    try:
        from PIL import Image

        from treeaicoach import geometry
        from treeaicoach.analysis import _ideal_presence, _zone_group

        team = str((record.get("meta") or {}).get("team") or "ORDER").upper()
        ideal = _ideal_presence(str(role or "").upper(), phase)
        codes = geometry.zone_map(size)
        frac = np.zeros(len(geometry.ZONES), np.float32)
        for i, z in enumerate(geometry.ZONES):
            frac[i] = ideal.get(_zone_group(z.value, team), 0.0)
        mx = float(frac.max()) or 1.0
        n = (frac / mx)[codes]
        lut = np.array([_lerp_color(HEAT_STOPS, i / 255.0) for i in range(256)], np.uint8)
        rgb = lut[(n * 0.72 * 255).astype(np.uint8)]            # teal -> gold, never white
        alpha = (np.where(n < 0.02, 0.0, 0.10 + 0.42 * n) * 255).astype(np.uint8)
        img = _base_image(record, size, darken=0.55)
        img.alpha_composite(Image.fromarray(np.dstack([rgb, alpha]), "RGBA"))
        return _png(img.convert("RGB"))
    except Exception:
        log.exception("render_ideal_png failed")
        return None


def render_phase_heat_png(record: dict, t0: float, t1: float, size: int = SMALL_MAP) -> bytes | None:
    """My position heat map during one phase."""
    try:
        pts = [p for p in _series(record.get("my_positions")) if t0 <= p[0] < t1]
        img = _base_image(record, size, darken=0.55)
        heat = _heat_layer(pts, size)
        if heat is not None:
            img.alpha_composite(heat)
        return _png(img.convert("RGB"))
    except Exception:
        log.exception("render_phase_heat_png failed")
        return None


def render_exposure_png(record: dict, analysis: dict, size: int = SMALL_MAP) -> bytes | None:
    """Where I was exposed to ganks (enemy jungler unseen >= 30 s): red dots; deaths in that state: crosses."""
    try:
        from PIL import Image, ImageDraw

        s = SUPERSAMPLE
        big = size * s
        img = _base_image(record, big, darken=0.5)
        layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        r = 2.2 * s
        for u, v in ((analysis.get("exposure") or {}).get("spots") or [])[:400]:
            x, y = float(u) * big, float(v) * big
            draw.ellipse([x - r, y - r, x + r, y + r], fill=_hex_rgb(RED) + (150,))
        for d in analysis.get("deaths") or []:
            if isinstance(d, dict) and d.get("uv") and d.get("jungler_unseen"):
                _draw_cross(draw, float(d["uv"][0]) * big, float(d["uv"][1]) * big, 6 * s, int(3 * s))
        img.alpha_composite(layer)
        return _png(img.resize((size, size), Image.LANCZOS).convert("RGB"))
    except Exception:
        log.exception("render_exposure_png failed")
        return None


def _arrow(draw: Any, a: tuple[float, float], b: tuple[float, float], color: tuple[int, int, int], w: int,
           head: float) -> None:
    ax, ay = a
    bx, by = b
    d = math.hypot(bx - ax, by - ay)
    if d < 1e-6:
        return
    ux, uy = (bx - ax) / d, (by - ay) / d
    # stop short of the numbered discs
    sx, sy = ax + ux * head * 1.2, ay + uy * head * 1.2
    ex, ey = bx - ux * head * 1.3, by - uy * head * 1.3
    if math.hypot(ex - sx, ey - sy) < head:
        return
    draw.line([(sx, sy), (ex, ey)], fill=(12, 14, 13, 220), width=w + 4)
    draw.line([(sx, sy), (ex, ey)], fill=color + (235,), width=w)
    px, py = -uy, ux
    tip = (ex + ux * head * 0.2, ey + uy * head * 0.2)
    left = (ex - ux * head + px * head * 0.55, ey - uy * head + py * head * 0.55)
    right = (ex - ux * head - px * head * 0.55, ey - uy * head - py * head * 0.55)
    draw.polygon([tip, left, right], fill=color + (255,))


def render_pathing_png(record: dict, analysis: dict, size: int = PATH_MAP) -> bytes | None:
    """Enemy jungler path: numbered appearances (first 15 min) joined by arrows, coloured by time."""
    try:
        from PIL import Image, ImageDraw

        path = (analysis.get("pathing") or {}).get("path") or []
        s = SUPERSAMPLE
        big = size * s
        img = _base_image(record, big, darken=0.5)
        layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        span = max(60.0, max((float(p.get("game_time") or 0) for p in path), default=60.0))
        pts = [(float(p["uv"][0]) * big, float(p["uv"][1]) * big, float(p.get("game_time") or 0.0), p)
               for p in path if isinstance(p, dict) and p.get("uv")]
        rad = 10 * s * size / 400
        spread: list[tuple[float, float, float, Any]] = []
        for x, y, t, p in pts:                     # nudge markers that would hide an earlier one
            k = 0
            while k < 8 and any(math.hypot(x - q[0], y - q[1]) < 2.1 * rad for q in spread):
                ang = math.pi / 4 + k * math.pi / 2.5
                x, y = x + math.cos(ang) * 2.2 * rad, y + math.sin(ang) * 2.2 * rad
                k += 1
            spread.append((min(max(x, rad), big - rad), min(max(y, rad), big - rad), t, p))
        pts = spread
        for (x0, y0, t0, _), (x1, y1, t1, _) in zip(pts, pts[1:]):
            _arrow(draw, (x0, y0), (x1, y1), _lerp_color(MINUTE_STOPS, t1 / span), int(2.4 * s), rad)
        font = _font(int(11 * s * max(0.8, size / 400)), bold=True)
        small = _font(int(10 * s * max(0.8, size / 400)), bold=True)
        placed: list[tuple[float, float, float, float]] = []
        for i, (x, y, t, p) in enumerate(pts):
            c = _lerp_color(MINUTE_STOPS, t / span)
            draw.ellipse([x - rad - s, y - rad - s, x + rad + s, y + rad + s], fill=(12, 14, 13, 240))
            draw.ellipse([x - rad, y - rad, x + rad, y + rad], fill=c + (255,))
            num = str(i + 1)
            l, tp, r, b = draw.textbbox((0, 0), num, font=font)
            draw.text((x - (r - l) / 2 - l, y - (b - tp) / 2 - tp), num, font=font, fill=(12, 14, 13, 255))
            placed.append((x - rad, y - rad, x + rad, y + rad))
        for i, (x, y, t, p) in enumerate(pts):
            _label(draw, x, y, str(p.get("time") or ""), small, _hex_rgb(TEXT), s, big, placed, gap=rad + 3 * s)
        img.alpha_composite(layer)
        return _png(img.resize((size, size), Image.LANCZOS).convert("RGB"))
    except Exception:
        log.exception("render_pathing_png failed")
        return None


def _spark_svg(series: list[dict], key: str, color: str, fmt: Any, target: float | None = None,
               w: int = 300, h: int = 86) -> str:
    """Small inline SVG line chart (x = minute)."""
    pts = [(float(p["minute"]), float(p[key])) for p in series if _f(p.get(key)) is not None]
    if len(pts) < 2:
        return '<p class="empty">Pas assez de données.</p>'
    xs = [x for x, _ in pts]
    ys = [y for _, y in pts] + ([target] if target is not None else [])
    x0, x1 = min(xs), max(xs)
    y0, y1 = 0.0, max(ys) * 1.12 or 1.0
    pl, pr, pt, pb = 6, 44, 8, 18

    def X(x: float) -> float:
        return pl + (x - x0) / max(1e-6, x1 - x0) * (w - pl - pr)

    def Y(y: float) -> float:
        return pt + (1 - (y - y0) / max(1e-6, y1 - y0)) * (h - pt - pb)
    line = " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in pts)
    area = f"{X(pts[0][0]):.1f},{Y(0):.1f} " + line + f" {X(pts[-1][0]):.1f},{Y(0):.1f}"
    tgt = ""
    if target is not None:
        ty = Y(target)
        tgt = (f'<line x1="{pl}" x2="{w - pr}" y1="{ty:.1f}" y2="{ty:.1f}" stroke="{GOLD}" stroke-dasharray="4 4" '
               f'stroke-width="1" opacity=".75"/><text x="{pl + 2}" y="{ty - 4:.1f}" fill="{GOLD}" '
               f'font-size="10">objectif {_e(fmt(target))}</text>')
    lx, ly = X(pts[-1][0]), Y(pts[-1][1])
    ticks = "".join(f'<text x="{X(m):.1f}" y="{h - 4}" fill="{MUTED}" font-size="10" text-anchor="middle">{int(m)}'
                    f'</text>' for m in range(int(x0), int(x1) + 1) if m % 5 == 0)
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" role="img" preserveAspectRatio="none">'
            f'<polygon points="{area}" fill="{color}" opacity=".13"/>'
            f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round"/>'
            f'{tgt}<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3.2" fill="{color}"/>'
            f'<text x="{lx + 6:.1f}" y="{ly + 4:.1f}" fill="{TEXT}" font-size="11" font-weight="700">'
            f'{_e(fmt(pts[-1][1]))}</text>{ticks}</svg>')


CSS_V2 = f"""
.voice{{display:flex;gap:12px;align-items:flex-start;background:{PANEL};border-left:3px solid {ACCENT};border-radius:0;
  padding:12px 16px;margin:0 0 18px;font-size:14.5px}}
.voice .ic{{flex:0 0 28px;height:28px;border-radius:3px;background:{BG};border:1px solid {LINE_STRONG};color:{ACCENT};
  display:flex;align-items:center;justify-content:center;font-size:14px}}
.voice .q{{color:{TEXT}}} .voice .k{{font-size:11px;color:{MUTED};text-transform:uppercase;letter-spacing:.1em}}
.phgrid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}}
@media (max-width:860px){{.phgrid{{grid-template-columns:1fr}}}}
.ph{{border-top:1px solid {LINE_STRONG};padding:10px 0 0}}
.ph h3{{margin:0 0 2px;font-size:15px;color:{TEXT}}} .ph .rg{{font-size:12px;color:{MUTED};margin-bottom:10px}}
.kv{{display:grid;grid-template-columns:1fr auto;gap:4px 10px;font-size:13.5px}}
.kv span{{color:{MUTED}}} .kv b{{text-align:right;font-variant-numeric:tabular-nums}}
.kv b.bad{{color:{RED}}} .kv b.ok{{color:{GREEN}}} .kv b.mid{{color:{ORANGE}}}
.trip{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}}
@media (max-width:860px){{.trip{{grid-template-columns:1fr}}}}
.trip figure{{margin:0;padding:0}}
.trip img{{width:100%;max-width:240px;display:block;margin:0 auto;border-radius:4px;border:1px solid {BORDER}}}
.trip figcaption{{font-size:13px;text-align:center;margin-top:8px;color:{TEXT}}}
.trip figcaption span{{display:block;color:{MUTED};font-size:12px}}
.cmp{{display:grid;grid-template-columns:118px 1fr 70px;gap:6px 10px;align-items:center;font-size:13px;margin-top:6px}}
.cmp .tw2{{display:flex;flex-direction:column;gap:3px}}
.cmp .b1,.cmp .b2{{height:6px;border-radius:1px}}
.cmp .b1{{background:{ACCENT}}} .cmp .b2{{background:{LINE_STRONG}}}
.cmp .n{{text-align:right;color:{MUTED};font-variant-numeric:tabular-nums;font-size:12.5px}}
.gauge{{display:flex;align-items:center;gap:14px;margin:4px 0 12px}}
.gauge .g{{flex:1;height:8px;border-radius:2px;position:relative;
  background:linear-gradient(90deg,{GREEN} 0 33%,{ORANGE} 33% 66%,{RED} 66% 100%)}}
.gauge .g i{{position:absolute;top:-5px;width:3px;height:18px;background:{TEXT};border-radius:0}}
.gauge .v{{font-size:24px;font-weight:700;min-width:70px;text-align:right}}
.opres{{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}}
.opres span{{font-size:12px;padding:2px 8px;border-radius:3px;border:1px solid {BORDER}}}
.opres .y{{border-color:{GREEN};color:{GREEN}}} .opres .n{{border-color:{RED};color:{RED}}}
.opres .e{{opacity:.6}}
.sparks{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}}
@media (max-width:860px){{.sparks{{grid-template-columns:1fr}}}}
.sparks .sp{{border-top:1px solid {LINE_STRONG};padding:8px 0 0}}
.sparks h3{{margin:0 0 4px;font-size:13px;color:{TEXT}}} .sparks h3 span{{color:{MUTED};font-weight:400}}
svg.spark{{width:100%;height:96px;display:block}}
.pathbox{{display:grid;grid-template-columns:minmax(0,400px) minmax(0,1fr);gap:22px;align-items:start;margin-top:18px}}
@media (max-width:860px){{.pathbox{{grid-template-columns:1fr}}}}
.pathbox img{{width:100%;max-width:400px;border-radius:4px;border:1px solid {LINE_STRONG};display:block}}
ol.steps{{margin:0;padding-left:22px;font-size:13.5px}} ol.steps li{{padding:2px 0}}
ol.steps b{{font-variant-numeric:tabular-nums}}
"""


def _voice_box(a: dict) -> str:
    text = a.get("spoken_summary")
    if not text:
        try:
            from treeaicoach.analysis import spoken_summary

            text = spoken_summary(a)
        except Exception:
            text = ""
    if not text:
        return ""
    return (f'<div class="voice"><span class="ic">♪</span><div><div class="k">Résumé vocal de fin de partie</div>'
            f'<div class="q">« {_e(text)} »</div></div></div>')


def _cls(value: Any, good: float, bad: float, higher_is_better: bool = True) -> str:
    v = _f(value)
    if v is None:
        return ""
    if higher_is_better:
        return "ok" if v >= good else "bad" if v <= bad else "mid"
    return "ok" if v <= good else "bad" if v >= bad else "mid"


def _phases_section(a: dict) -> str:
    phases = a.get("phases") or []
    if not phases:
        return ""
    cards = []
    for p in phases:
        rows = [
            ("Morts", f'<b class="{_cls(p.get("deaths"), 1, 3, False)}">{_e(p.get("deaths", 0))}</b>'),
            ("Kills / assists", f'<b>{_e(p.get("kills", 0))} / {_e(p.get("assists", 0))}</b>'),
            ("CS / min", f'<b class="{_cls(p.get("cs_per_min"), 7, 5)}">{_e(_num(p.get("cs_per_min")))}</b>'),
            ("Vision / min", f'<b class="{_cls(p.get("vision_per_min"), 0.8, 0.4)}">'
                             f'{_e(_num(p.get("vision_per_min"), 2))}</b>'),
            ("Temps en voie", f'<b>{_e(_num(p.get("lane_percent"), 0))} %</b>' if p.get("lane_percent") is not None
             else "<b>-</b>"),
            ("Exposition aux ganks", f'<b class="{_cls(p.get("exposure"), 15, 25, False)}">'
                                     f'{_e(_num(p.get("exposure"), 0))} %</b>' if p.get("exposure") is not None
             else "<b>-</b>"),
            ("Ganks survécus", f'<b>{_e(p.get("ganks_survived", 0))} / {_e(p.get("ganks", 0))}</b>'),
            ("Objectifs épiques", f'<b>{_e(p.get("objectives_ours", 0))} – {_e(p.get("objectives_theirs", 0))}</b>'),
        ]
        kv = "".join(f"<span>{_e(k)}</span>{v}" for k, v in rows)
        cards.append(f'<div class="ph"><h3>{_e(p.get("label"))}</h3><div class="rg">{_e(p.get("range"))}</div>'
                     f'<div class="kv">{kv}</div></div>')
    return (f'<div class="panel"><h2>Partie phase par phase</h2><div class="phgrid">{"".join(cards)}</div>'
            f'<p class="small">Phase de voie 0–14 min · milieu de partie 14–25 min · fin de partie 25 min et +. '
            f'Objectifs épiques : ton équipe – ennemis.</p></div>')


def _presence_section(record: dict, a: dict) -> str:
    pres = a.get("presence") or {}
    phases = pres.get("phases") or []
    exp = a.get("exposure") or {}
    if not phases and exp.get("score") is None:
        return ""
    role = pres.get("role") or ""
    lan = next((p for p in phases if p.get("phase") == "laning"), phases[0] if phases else None)
    figs = []
    if lan is not None:
        from treeaicoach.analysis import GAME_PHASES

        t0, t1 = next(((x[2], x[3]) for x in GAME_PHASES if x[0] == lan["phase"]), (0.0, 840.0))
        mine = _data_uri_png(render_phase_heat_png(record, t0, t1))
        ideal = _data_uri_png(render_ideal_png(record, role, lan["phase"]))
        if mine:
            figs.append(f'<figure><img src="{mine}" alt=""><figcaption>Ta présence<span>{_e(lan["label"])}'
                        f'</span></figcaption></figure>')
        if ideal:
            figs.append(f'<figure><img src="{ideal}" alt=""><figcaption>Idéal pour ton rôle<span>'
                        f'{_e(_role_fr(role))} · {_e(lan["label"].lower())}</span></figcaption></figure>')
    expo = _data_uri_png(render_exposure_png(record, a))
    if expo:
        figs.append(f'<figure><img src="{expo}" alt=""><figcaption>Exposition aux ganks<span>points rouges : '
                    f'avancé alors que le jungler ennemi était invisible</span></figcaption></figure>')
    score = exp.get("score")
    gauge = ""
    if score is not None:
        pos = min(100.0, max(0.0, float(score) * 2.0))      # 50 % exposure = end of the scale
        col = GREEN if score < 15 else ORANGE if score < 25 else RED
        gauge = (f'<h2 style="margin-top:20px">Score d\'exposition aux ganks</h2><div class="gauge">'
                 f'<div class="g"><i style="left:calc({pos:.1f}% - 2px)"></i></div>'
                 f'<div class="v" style="color:{col}">{_num(score, 0)} %</div></div>'
                 f'<p class="small">{_num(exp.get("exposed_s"), 0)} s sur {_num(exp.get("total_s"), 0)} s hors base passées '
                 f'au-delà du milieu de ta voie ou dans la jungle ennemie alors que le jungler ennemi n\'avait pas été vu '
                 f'depuis 30 s. Objectif : moins de 15 %.</p>')
    blocks = []
    for p in phases:
        prow = (p.get("rows") or [])[:6]
        scale = max([max(r["mine"], r["ideal"]) for r in prow] + [1.0])
        rows = "".join(
            f'<span>{_e(r["label"])}</span><div class="tw2"><div class="b1" style="width:'
            f'{max(1.5, 100.0 * r["mine"] / scale):.1f}%"></div><div class="b2" style="width:'
            f'{max(1.5, 100.0 * r["ideal"] / scale):.1f}%"></div></div>'
            f'<span class="n">{_num(r["mine"], 0)} / {_num(r["ideal"], 0)} %</span>'
            for r in prow)
        match = _f(p.get("match"), 0.0) or 0.0
        mcol = GREEN if match >= 75 else ORANGE if match >= 55 else RED
        blocks.append(f'<div class="ph"><h3>{_e(p["label"])}</h3><div class="rg">Correspondance avec l\'idéal : '
                      f'<b style="color:{mcol}">{_num(match, 0)} / 100</b></div><div class="cmp">{rows}</div></div>')
    legend = (f'<p class="small"><span style="color:{TEAL}">▬</span> toi · <span style="color:{GOLD}">▬</span> idéal '
              f'pour ton rôle (part du temps où ton icône est visible).</p>')
    return (f'<div class="panel"><h2>Présence sur la carte : toi vs idéal</h2><div class="trip">{"".join(figs)}</div>'
            f'{gauge}<div class="phgrid" style="margin-top:16px">{"".join(blocks)}</div>{legend}</div>')


def _positioning_section(a: dict) -> str:
    """"Positionnement": was I at the right place at the right time, phase by phase."""
    pos = a.get("positioning") or {}
    phases = pos.get("phases") or []
    if not phases:
        return ""
    cards = []
    for p in phases:
        score = p.get("score")
        col = GREEN if (score or 0) >= 75 else ORANGE if (score or 0) >= 50 else RED
        pct = p.get("percent")
        rows = [
            ("Moments clés", f'<b>{_e(p.get("present", 0))} / {_e(p.get("moments", 0))}</b>'
                             + (f' <span class="small">({_num(pct, 0)} %)</span>' if pct is not None else "")),
            ("Objectifs manqués (absent)", f'<b class="{"bad" if p.get("missed") else "ok"}">'
                                           f'{len(p.get("missed") or [])}</b>'),
        ]
        if p.get("phase") != "laning":
            risk = p.get("side_risk_s", 0) or 0
            rows.append(("Seul en side lane (3+ disparus)",
                         f'<b class="{_cls(risk, 20, 60, False)}">{_e(_num(risk, 0))} s</b>'))
        kv = "".join(f"<span>{_e(k)}</span>{v}" for k, v in rows)
        missed = "".join(f'<li>{_e(m.get("time"))} {_e(m.get("label"))}</li>' for m in (p.get("missed") or [])[:4])
        head = (f'<div class="rg">{_e(p.get("range", ""))} · score <b style="color:{col}">'
                f'{_e(_num(score, 0)) if score is not None else "-"} / 100</b></div>')
        cards.append(f'<div class="ph"><h3>{_e(p.get("label"))}</h3>{head}<div class="kv">{kv}</div>'
                     + (f'<ul class="small" style="margin:8px 0 0 16px">{missed}</ul>' if missed else "") + "</div>")
    tips = "".join(f"<li>{_e(t)}</li>" for t in (pos.get("tips") or [])[:5])
    total = pos.get("score")
    title = "Positionnement : au bon endroit au bon moment"
    if total is not None:
        title += f" ({_num(total, 0)} / 100)"
    return (f'<div class="panel"><h2>{_e(title)}</h2><div class="phgrid">{"".join(cards)}</div>'
            + (f'<ul class="tips" style="margin-top:14px">{tips}</ul>' if tips else "")
            + '<p class="small">Moment clé : un objectif épique pris (par une équipe) alors que ton rôle devait y '
              'être et que tu étais en vie ; présent = à moins de ~3 700 unités de la fosse ou participant.</p></div>')


def _role_fr(role: str) -> str:
    return {"TOP": "Haut", "JUNGLE": "Jungle", "MIDDLE": "Milieu", "BOTTOM": "Tireur", "UTILITY": "Support"}.get(
        str(role or "").upper(), "rôle inconnu")


def _objective_presence_html(a: dict) -> str:
    op = a.get("objective_presence") or {}
    items = op.get("items") or []
    if not items:
        return ""
    chips = "".join(
        f'<span class="{"y" if i.get("near") else "n"}{"" if i.get("mine") else " e"}" '
        f'title="{_e("pris par ton équipe" if i.get("mine") else "pris par l’ennemi")}">'
        f'{"✓" if i.get("near") else "✗"} {_e(i.get("time"))} {_e(i.get("label"))}</span>' for i in items)
    pct = op.get("percent")
    head = (f'<p style="margin:16px 0 0">Présent sur <b>{op.get("ours_near", 0)}</b> des <b>{op.get("ours", 0)}</b> '
            f'objectifs pris par ton équipe{f" ({_num(pct, 0)} %)" if pct is not None else ""} · présent sur '
            f'{op.get("theirs_near", 0)} des {op.get("theirs", 0)} objectifs ennemis.</p>')
    return head + f'<div class="opres">{chips}</div><p class="small">✓ à moins de ~3 300 unités de la fosse (ou ' \
                  f'participant au kill) · objectifs ennemis en grisé.</p>'


def _trends_section(a: dict) -> str:
    tr = a.get("trends") or {}
    series = tr.get("series") or []
    if len(series) < 2:
        return ""
    pos = str((a.get("summary") or {}).get("position") or "").upper()
    try:
        from treeaicoach.analysis import CS_TARGET

        target = CS_TARGET.get(pos) if pos != "UTILITY" else None
    except Exception:
        target = None
    charts = [
        ("CS par minute", "cumulé", _spark_svg(series, "cs_per_min", TEAL, lambda v: _num(v), target)),
        ("Score de vision", "cumulé", _spark_svg(series, "vision", GOLD, lambda v: _num(v, 0))),
        ("Participation aux kills", "cumulée", _spark_svg(
            [dict(p, kp=(p["kp"] * 100 if p.get("kp") is not None else None)) for p in series], "kp", GREEN,
            lambda v: f"{_num(v, 0)} %")),
    ]
    body = "".join(f'<div class="sp"><h3>{_e(t)} <span>({_e(s)})</span></h3>{svg}</div>'
                   for t, s, svg in charts)
    notes = []
    if tr.get("cs_per_min_10") is not None:
        notes.append(f"CS/min à 10:00 : {_num(tr['cs_per_min_10'])}")
    if tr.get("cs_per_min_after_10") is not None:
        notes.append(f"après 10:00 : {_num(tr['cs_per_min_after_10'])}")
    if tr.get("vision_per_min_laning") is not None:
        notes.append(f"vision/min en phase de voie : {_num(tr['vision_per_min_laning'], 2)}")
    if tr.get("vision_per_min_after") is not None:
        notes.append(f"ensuite : {_num(tr['vision_per_min_after'], 2)}")
    return (f'<div class="panel"><h2>Tendances (minute par minute)</h2><div class="sparks">{body}</div>'
            f'<p class="small">{_e(" · ".join(notes))}</p></div>')


def _pathing_html(record: dict, a: dict) -> str:
    pa = a.get("pathing") or {}
    path = pa.get("path") or []
    if not pa.get("known") or not path:
        return ""
    uri = _data_uri_png(render_pathing_png(record, a))
    img = f'<img src="{uri}" alt="Parcours du jungler ennemi">' if uri else ""
    steps = "".join(f'<li><b>{_e(p.get("time"))}</b> · {_e(p.get("zone_label"))}</li>' for p in path)
    summary = pa.get("summary") or ""
    return (f'<div class="pathbox"><div>{img}</div><div><h3 style="margin:0 0 8px;font-size:15px">Parcours en début '
            f'de partie (apparitions sur la minimap, 0–15 min)</h3><p style="margin:0 0 10px">{_e(summary)}</p>'
            f'<ol class="steps">{steps}</ol></div></div>')


# ======================================================================================
# files
# ======================================================================================
def _games_dir(games_dir: Path | None) -> Path:
    if games_dir is not None:
        return Path(games_dir)
    from treeaicoach.recorder import default_games_dir

    return default_games_dir()


def report_path_for(record_path: Path) -> Path:
    """``X.json`` -> ``X.html`` ; ``X.partial.json`` -> ``X.html``."""
    p = Path(record_path)
    name = p.name
    for suf in (".partial.json", ".json"):
        if name.endswith(suf):
            return p.with_name(name[: -len(suf)] + ".html")
    return p.with_suffix(".html")


def write_report(record_path: Path, lcu_pending: bool = False) -> Path | None:
    """Read a record JSON, write its HTML report next to it and return its path (None on error).

    The League Client ground truth of the game (``ground_truth.load_truth``) is used when present."""
    try:
        from treeaicoach.analysis import analyze_game
        from treeaicoach.recorder import atomic_write_text

        p = Path(record_path)
        record = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            log.warning("write_report: %s is not a game record", p)
            return None
        if p.name.endswith(".partial.json"):
            record.setdefault("incomplete", True)
        truth = None
        try:
            from treeaicoach import ground_truth

            truth = ground_truth.load_truth(p)
        except Exception:
            log.exception("Cannot load the client ground truth of %s", p)
        analysis = analyze_game(record, truth=truth) if truth is not None else analyze_game(record)
        if truth is not None and isinstance(analysis.get("truth"), dict) and analysis["truth"].get("available"):
            try:
                analysis["truth"]["history"] = ground_truth.aggregate_scores(
                    ground_truth.truth_path_for(p).parent)
            except Exception:
                log.exception("Cannot aggregate the alert scores")
        text = render_report_html(record, analysis, lcu_pending=bool(lcu_pending) and truth is None)
        out = report_path_for(p)
        return out if atomic_write_text(out, text) else None
    except Exception:
        log.exception("write_report failed for %s", record_path)
        return None


def _read_summary(path: Path) -> dict | None:
    """The record's ``summary`` object (fast path: parse only the head of the file)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(HEAD_BYTES).decode("utf-8", errors="ignore")
        i = head.find('"summary"')
        if i >= 0:
            j = head.find("{", i)
            if j >= 0:
                try:
                    obj, _ = json.JSONDecoder().raw_decode(head, j)
                    if isinstance(obj, dict):
                        return obj
                except ValueError:
                    pass
        data = json.loads(Path(path).read_text(encoding="utf-8"))     # slow path (old / reordered files)
        if not isinstance(data, dict) or not any(k in data for k in ("summary", "meta", "snapshots")):
            return None                                                 # not a game record (cache, settings...)
        s = data.get("summary")
        if isinstance(s, dict):
            return s
        meta = data.get("meta") or {}
        snaps = data.get("snapshots") or [{}]
        last = snaps[-1] if isinstance(snaps[-1], dict) else {}
        return {"start": meta.get("start"), "champion": meta.get("champion"),
                "champion_name": meta.get("champion_name"), "result": data.get("result"),
                "duration": data.get("duration"), "kills": last.get("kills"), "deaths": last.get("deaths"),
                "assists": last.get("assists"), "cs": last.get("cs"), "incomplete": data.get("incomplete")}
    except Exception as exc:
        log.debug("Cannot read game summary %s: %s", path, exc)
        return None


#: JSON files of the games folder that are not game records (caches written next to them)
NOT_RECORDS = frozenset({"progress_cache.json"})
TAIL_BYTES = 65536             # list_games(): bytes read at the end of a record to find the play summary
_PLAYS_RE = re.compile(r'"plays"\s*:\s*\{\s*"schema"')
_precision_cache: dict[str, tuple[tuple[int, int], dict[str, Any] | None]] = {}


def _iter_record_files(d: Path) -> Iterable[Path]:
    try:
        for p in d.iterdir():
            n = p.name
            if (p.is_file() and n.endswith(".json") and not n.startswith(".") and n not in NOT_RECORDS
                    and not n.endswith(".truth.json") and not n.endswith("_cache.json")):
                yield p
    except FileNotFoundError:
        return
    except OSError as exc:
        log.warning("Cannot list %s: %s", d, exc)


def read_plays_brief(path: Path) -> dict[str, Any] | None:
    """``{"precision": 0-100, "best": play | None, "worst": play | None}`` of a rated game record
    (play = ``{"cls", "title", "reason", "gt"}``), None when the game was not rated.

    Cheap: the play summary (:mod:`treeaicoach.plays`) is the record's last key, so only the file's
    tail is parsed; cached by (mtime, size). Never raises."""
    try:
        p = Path(path)
        st = p.stat()
        sig = (int(st.st_mtime_ns), int(st.st_size))
        hit = _precision_cache.get(str(p))
        if hit is not None and hit[0] == sig:
            return hit[1]
        with open(p, "rb") as fh:
            if st.st_size > TAIL_BYTES:
                fh.seek(st.st_size - TAIL_BYTES)
            tail = fh.read().decode("utf-8", errors="ignore")
        block: Any = None
        matches = list(_PLAYS_RE.finditer(tail))
        if matches:
            try:
                block, _ = json.JSONDecoder().raw_decode(tail, tail.index("{", matches[-1].start() + 7))
            except ValueError:
                block = None
        if block is None and '"plays"' in tail and st.st_size <= 8 * 1024 * 1024:
            data = json.loads(p.read_text(encoding="utf-8"))           # summary longer than the tail
            block = data.get("plays") if isinstance(data, dict) else None
        val: dict[str, Any] | None = None
        if isinstance(block, dict) and int(block.get("total") or 0) > 0:
            prec = block.get("precision")
            if not isinstance(prec, (int, float)) and isinstance(block.get("counts"), dict):
                from treeaicoach import plays as _plays

                prec = _plays.precision(block["counts"])
            if isinstance(prec, (int, float)) and math.isfinite(prec):
                def brief(items: Any) -> dict[str, Any] | None:
                    d = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else None
                    if d is None:
                        return None
                    return {k: d.get(k) for k in ("cls", "title", "reason", "gt")}

                val = {"precision": int(round(min(max(float(prec), 0.0), 100.0))),
                       "best": brief(block.get("best")), "worst": brief(block.get("worst"))}
        if len(_precision_cache) > 512:
            _precision_cache.clear()
        _precision_cache[str(p)] = (sig, val)
        return val
    except Exception as exc:
        log.debug("Cannot read the play summary of %s: %s", path, exc)
        return None


def read_precision(path: Path) -> int | None:
    """Rated-play precision (0-100) of a game record, None when the game was not rated (cheap, cached)."""
    b = read_plays_brief(path)
    return int(b["precision"]) if b else None


def list_games(limit: int = 50, games_dir: Path | None = None) -> list[dict]:
    """Recorded games, newest first (reads only the summaries). Never raises.

    Each entry: ``path``, ``report_path`` (or None), ``start``, ``date_label``, ``champion``,
    ``champion_name``, ``result`` ("Win"/"Lose"/None), ``result_label``, ``kills``, ``deaths``,
    ``assists``, ``kda`` ("3/4/5"), ``cs``, ``duration``, ``duration_text``, ``ganks``,
    ``ganks_survived``, ``incomplete`` (True for a ``.partial.json`` left by a crash), ``precision``
    (rated plays 0-100, None when not rated), ``plays_brief`` (:func:`read_plays_brief`).
    """
    try:
        d = _games_dir(games_dir)
        files = list(_iter_record_files(d))
        finals = {p.name[: -len(".json")] for p in files if not p.name.endswith(".partial.json")}
        entries: list[dict] = []
        for p in files:
            partial = p.name.endswith(".partial.json")
            stem = p.name[: -len(".partial.json")] if partial else p.name[: -len(".json")]
            if partial and stem in finals:
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                mtime = 0.0
            entries.append({"_p": p, "_partial": partial, "_stem": stem, "_mtime": mtime})
        entries.sort(key=lambda e: (e["_stem"], e["_mtime"]), reverse=True)   # stems start with the date
        out: list[dict] = []
        for e in entries:
            if len(out) >= max(0, int(limit)):
                break
            s = _read_summary(e["_p"])
            if s is None:
                continue
            k, dd, a = s.get("kills") or 0, s.get("deaths") or 0, s.get("assists") or 0
            rp = report_path_for(e["_p"])
            brief = read_plays_brief(e["_p"])
            res = s.get("result") if s.get("result") in ("Win", "Lose") else None
            out.append({
                "path": e["_p"],
                "report_path": rp if rp.is_file() else None,
                "start": s.get("start"),
                "date_label": _date_fr(s.get("start")) or _dt.datetime.fromtimestamp(e["_mtime"]).strftime(
                    "%d/%m/%Y %H:%M"),
                "champion": s.get("champion") or "",
                "champion_name": s.get("champion_name") or s.get("champion") or "",
                "result": res,
                "result_label": {"Win": "Victoire", "Lose": "Défaite"}.get(res or "", "Inachevée"),
                "kills": k, "deaths": dd, "assists": a, "kda": f"{k}/{dd}/{a}",
                "cs": s.get("cs"),
                "duration": s.get("duration"),
                "duration_text": _fmt_time(s.get("duration")),
                "ganks": s.get("ganks"),
                "ganks_survived": s.get("ganks_survived"),
                "incomplete": bool(e["_partial"] or s.get("incomplete")),
                "precision": (brief or {}).get("precision"),
                "plays_brief": brief,
                "mtime": e["_mtime"],
            })
        out.sort(key=lambda g: (str(g.get("start") or ""), g["mtime"]), reverse=True)
        return out
    except Exception:
        log.exception("list_games failed")
        return []


# ======================================================================================
# League Client ground truth (lcu.py + ground_truth.py): verified maps, lane, reliability
# ======================================================================================
TRUTH_MAP = 360
CSS_TRUTH = f".pos{{color:{GREEN};font-weight:700}} .neg{{color:{RED};font-weight:700}}"
_VERDICT_FR = {"confirmed": ("juste", "sv"), "false": ("fausse", "no"), "probable_false": ("probablement fausse", "ok"),
               "unknown": ("invérifiable", "")}
PENDING_RELOAD_S = 20          # the pending page reloads itself (meta refresh) until the final page replaces it


def render_truth_deaths_png(record: dict, truth_a: dict, size: int = TRUTH_MAP) -> bytes | None:
    """My exact death positions (client timeline): red crosses, orange label when the enemy jungler took part."""
    try:
        from PIL import Image, ImageDraw

        s = SUPERSAMPLE
        big = size * s
        img = _base_image(record, big, darken=0.5)
        layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        deaths = [d for d in truth_a.get("deaths") or [] if isinstance(d, dict) and d.get("uv")]
        cr = 7 * s * size / 400
        pts = [(float(d["uv"][0]) * big, float(d["uv"][1]) * big, d) for d in deaths]
        for x, y, _ in pts:
            _draw_cross(draw, x, y, cr, int(3 * s))
        placed = [(x - cr, y - cr, x + cr, y + cr) for x, y, _ in pts]
        font = _font(int(11 * s * max(0.8, size / 400)), bold=True)
        for x, y, d in pts:
            col = _hex_rgb(ORANGE) if d.get("jungler_involved") else _hex_rgb(TEXT)
            _label(draw, x, y, str(d.get("time") or ""), font, col, s, big, placed, gap=cr + 4 * s)
        img.alpha_composite(layer)
        return _png(img.resize((size, size), Image.LANCZOS).convert("RGB"))
    except Exception:
        log.exception("render_truth_deaths_png failed")
        return None


def render_truth_path_png(record: dict, truth_a: dict, size: int = TRUTH_MAP) -> bytes | None:
    """True enemy-jungler path (one numbered disc per game minute, arrows between them)."""
    path = (truth_a.get("jungler") or {}).get("path") or []
    if not path:
        return None
    return render_pathing_png(record, {"pathing": {"path": path}}, size=size)


def _pct(x: Any) -> str:
    v = _f(x)
    return "-" if v is None else f"{_num(v * 100, 0)} %"


def _reliability_html(t: dict, history: dict | None) -> str:
    rel = t.get("reliability") or {}
    if not rel:
        return ""
    grade, label = rel.get("grade"), rel.get("grade_label") or ""
    head = (f'<p style="margin:0 0 12px;font-size:16px"><b>{_e(grade)}/100</b> · fiabilité {_e(label)}</p>'
            if grade is not None else f'<p class="empty">{_e(label or "Pas assez de données.")}</p>')
    scored = (rel.get("confirmed") or 0) + (rel.get("false") or 0) + (rel.get("probable_false") or 0)
    jd = rel.get("jungler_deaths") or 0
    cards = [
        ("g" if (rel.get("precision") or 0) >= 0.6 else "r" if scored else "", _pct(rel.get("precision")),
         "Alertes de gank justes",
         f"{rel.get('confirmed', 0)} justes sur {scored} vérifiables ({rel.get('alerts', 0)} au total)"),
        ("r" if rel.get("missed") else "g", f"{rel.get('missed', 0)} / {jd}", "Ganks manqués",
         ("sans alerte : " + ", ".join(rel.get("missed_times") or [])) if rel.get("missed") else
         "morts avec le jungler ennemi impliqué"),
        ("g" if (rel.get("lead_mean") or 0) >= 5 else "r" if rel.get("lead_n") else "",
         f"{_num(rel.get('lead_mean'), 1)} s" if rel.get("lead_mean") is not None else "-",
         "Avance des alertes",
         (f"avant {rel.get('lead_n')} de tes morts, {rel.get('lead_late', 0)} trop tardive(s) (< 3 s)"
          if rel.get("lead_n") else "aucune alerte avant tes morts")),
        ("t", _pct(rel.get("fog_coverage")), "Cercle du brouillard",
         f"contenait le vrai jungler ({rel.get('fog_inside', 0)}/{rel.get('fog_checks', 0)} vérifs)"),
        ("t", f"{rel.get('sightings_ok', 0)} / {rel.get('sightings_checked', 0)}", "Identifications minimap",
         "positions vues = vraies positions" if rel.get("sightings_checked") else "aucune vérification possible"),
    ]
    cards_html = "".join(f'<div class="card {c}"><div class="v">{_e(v)}</div><div class="l">{_e(l)}</div>'
                         f'<div class="x">{_e(x)}</div></div>' for c, v, l, x in cards)
    sug = rel.get("suggestion") or {}
    sug_txt = str(sug.get("text") or "")
    if sug.get("suggested") is not None:
        cur = sug.get("current")
        sug_txt += (f" Sensibilité suggérée : {_num(sug['suggested'], 1)}"
                    + (f" (actuelle {_num(cur, 1)})." if cur is not None else "."))
    hist = ""
    if isinstance(history, dict) and (history.get("games") or 0) >= 2:
        hs = history.get("suggestion") or {}
        hist = (f'<p class="small">Sur tes {history["games"]} dernières parties vérifiées : alertes justes '
                f'{_e(_pct(history.get("precision")))}, ganks manqués {history.get("missed", 0)} / '
                f'{history.get("jungler_deaths", 0)}, cercle {_e(_pct(history.get("fog_coverage")))}. '
                f'{_e(hs.get("text") or "")}'
                + (f' Réglage conseillé : {_num(hs["suggested"], 1)}.' if hs.get("suggested") is not None else "")
                + '</p>')
    eps = rel.get("episodes") or []
    rows = "".join(
        f'<tr><td class="t">{_e(e.get("time"))}</td><td>{_e(e.get("target_name") or e.get("target"))}</td>'
        f'<td><span class="tag {_VERDICT_FR.get(e.get("verdict"), ("", ""))[1]}">'
        f'{_e(_VERDICT_FR.get(e.get("verdict"), (e.get("verdict"), ""))[0])}</span></td></tr>' for e in eps)
    table = (f'<details style="margin-top:10px"><summary class="small" style="cursor:pointer">Détail des '
             f'{len(eps)} alertes</summary><div class="tw"><table><thead><tr><th>Heure</th><th>Ennemi annoncé</th>'
             f'<th>Verdict</th></tr></thead><tbody>{rows}</tbody></table></div></details>') if eps else ""
    return (f'<h2 style="margin-top:6px">Fiabilité de TreeAI cette partie</h2>{head}<div class="cards">{cards_html}</div>'
            f'<p style="margin:0 0 6px">{_e(sug_txt)}</p>{hist}{table}')


def _lane_html(t: dict) -> str:
    lane = t.get("lane") or {}
    rows = lane.get("rows") or []
    if not rows:
        return ""
    opp = lane.get("opponent_name") or lane.get("opponent")

    def signed(v: Any) -> str:
        f = _f(v)
        if f is None:
            return "-"
        return f'<span class="{"pos" if f > 0 else "neg" if f < 0 else ""}">{"+" if f > 0 else ""}{_num(f, 0)}</span>'

    body = "".join(
        f'<tr><td class="t">{r["minute"]} min</td><td>{_num(r.get("gold"), 0)}</td><td>{signed(r.get("gold_diff"))}</td>'
        f'<td>{signed(r.get("xp_diff"))}</td><td>{_e(r.get("cs"))}</td><td>{_e(r.get("opp_cs", "-"))}</td></tr>'
        for r in rows)
    vs = f" face à {_e(opp)}" if opp else ""
    return (f'<h3 style="margin:18px 0 8px;font-size:15px">Ma voie{vs} (chiffres exacts du client)</h3>'
            '<div class="tw"><table><thead><tr><th>Minute</th><th>Or total</th><th>Écart d\'or</th><th>Écart d\'XP</th>'
            f'<th>Sbires</th><th>Sbires adverses</th></tr></thead><tbody>{body}</tbody></table></div>')


def _truth_deaths_html(t: dict) -> str:
    deaths = t.get("deaths") or []
    if not deaths:
        return '<p class="small">Aucune mort d\'après le client.</p>'
    yes_j, no_j = f'<b style="color:{ORANGE}">Oui</b>', "Non"
    yes_a, no_a = '<span class="tag ok">Oui</span>', '<span class="tag no">Non</span>'
    rows = "".join(
        f'<tr><td class="t">{_e(d.get("time"))}</td><td>{_e(d.get("zone_label"))}</td><td>{_e(d.get("killer"))}'
        + (f'<div class="recap">+ {_e(", ".join(d.get("assisters") or []))}</div>' if d.get("assisters") else "")
        + f'</td><td>{yes_j if d.get("jungler_involved") else no_j}</td>'
        f'<td>{yes_a if d.get("warned") else no_a}</td></tr>'
        for d in deaths)
    return ('<div class="tw"><table><thead><tr><th>Heure</th><th>Position exacte</th><th>Tué par</th><th>Jungler ?</th>'
            f'<th>Alerte ?</th></tr></thead><tbody>{rows}</tbody></table></div>')


def _truth_section(record: dict, a: dict) -> str:
    t = a.get("truth")
    if not isinstance(t, dict) or not t.get("available"):
        return ""
    j = t.get("jungler") or {}
    deaths_uri = _data_uri_png(render_truth_deaths_png(record, t))
    path_uri = _data_uri_png(render_truth_path_png(record, t))
    jname = j.get("name") or "Jungler ennemi"
    first = (f"{_e(jname)} a commencé {_e(j.get('first_side_label'))} (vu à {_e(j.get('first_time'))} en "
             f"{_e(next((p.get('zone_label') for p in j.get('path') or [] if not p.get('in_base')), ''))}). "
             if j.get("first_side_label") else "")
    ganks = j.get("early_ganks") or []
    gank_txt = ("Kills avec lui avant 15 min : " + ", ".join(f"{g['time']} ({g['victim']})" for g in ganks) + "."
                if ganks else "Aucun kill avec lui avant 15 min.")
    maps = (
        '<div class="phases" style="grid-template-columns:repeat(2,minmax(0,1fr))">'
        '<div class="phase"><h3>Mes morts (positions exactes)</h3>'
        + (f'<img src="{deaths_uri}" alt="Morts exactes" style="max-width:360px">' if deaths_uri else "")
        + '<p class="small">Libellé orange : le jungler ennemi a participé.</p></div>'
        f'<div class="phase"><h3>Vrai parcours de {_e(jname)} <span>1 point = 1 minute</span></h3>'
        + (f'<img src="{path_uri}" alt="Parcours réel du jungler" style="max-width:360px">' if path_uri else
           '<p class="empty">Parcours indisponible.</p>')
        + f'<p class="small">{first}{_e(gank_txt)}</p></div></div>')
    return (f'<div class="panel"><h2>Vérité terrain (client LoL)</h2>'
            f'<p class="small" style="margin-top:-6px">Positions réelles minute par minute et kills exacts lus après la '
            f'partie dans l\'historique du client League of Legends (API locale officielle, lecture seule).</p>'
            f'{maps}{_lane_html(t)}<h3 style="margin:18px 0 8px;font-size:15px">Mes morts d\'après le client</h3>'
            f'{_truth_deaths_html(t)}{_reliability_html(t, t.get("history"))}</div>')


def _pending_html() -> str:
    """Banner shown while the client data is being fetched (the page reloads itself, see render_report_html)."""
    return ('<div class="warnbox">Récupération des données du client LoL en cours (positions exactes, écarts d\'or, '
            'fiabilité des alertes)… cette page se met à jour toute seule d\'ici 1 à 2 minutes.</div>')
