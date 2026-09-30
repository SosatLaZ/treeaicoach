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
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from treeaicoach import APP_NAME, __version__

log = logging.getLogger(__name__)

# hextech palette (ARCHITECTURE.md §8.2)
BG = "#010A13"
PANEL = "#0A1428"
BORDER = "#1E2328"
GOLD = "#C8AA6E"
TEXT = "#F0E6D2"
TEAL = "#0AC8B9"
RED = "#E84057"
GREEN = "#2DC66B"
ORANGE = "#F0A030"
MUTED = "#A09B8C"

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


def _f(x: Any, default: float | None = None) -> float | None:
    if x is None or isinstance(x, bool):
        return default
    try:
        v = float(x)
    except (TypeError, ValueError, OverflowError):
        return default
    return v if math.isfinite(v) else default


def _num(x: Any, decimals: int = 1) -> str:
    v = _f(x)
    if v is None:
        return "—"
    s = f"{v:,.{decimals}f}".replace(",", "\u202f").replace(".", ",")
    return s


def _fmt_time(gt: Any) -> str:
    v = _f(gt)
    if v is None or v < 0:
        return "—"
    s = int(round(v))
    return f"{s // 60}:{s % 60:02d}"


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


MINUTE_STOPS: list[tuple[float, str]] = [(0.0, TEAL), (0.5, GOLD), (1.0, RED)]
HEAT_STOPS: list[tuple[float, str]] = [(0.0, "#0A3A5A"), (0.35, "#0AC8B9"), (0.7, "#C8AA6E"), (1.0, "#FFF4DC")]


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
        draw.line([(x - dx * r, y - dy * r), (x + dx * r, y + dy * r)], fill=(10, 4, 8, 255), width=w + 4)
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
                           fill=(1, 10, 19, 225), outline=color + (255,), width=max(1, s))
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
        draw.ellipse([x - r - s, y - r - s, x + r + s, y + r + s], fill=(1, 10, 19, 230))
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
body{{font-family:"Segoe UI",system-ui,-apple-system,"Helvetica Neue",Arial,sans-serif;font-size:15px;line-height:1.45;
  background:radial-gradient(1200px 500px at 50% -120px,#0B2A3A 0%,{BG} 60%) no-repeat,{BG}}}
.wrap{{max-width:1120px;margin:0 auto;padding:28px 20px 40px}}
h2{{font-size:13px;letter-spacing:.14em;text-transform:uppercase;color:{GOLD};margin:0 0 14px;font-weight:700;
  display:flex;align-items:center;gap:10px}}
h2:after{{content:"";flex:1;height:1px;background:linear-gradient(90deg,{GOLD}66,transparent)}}
.panel{{background:linear-gradient(180deg,#0C1830,{PANEL});border:1px solid {BORDER};border-top:1px solid #C8AA6E55;
  border-radius:10px;padding:20px 22px;margin:0 0 18px;box-shadow:0 8px 24px #0008}}
.hdr{{display:flex;gap:22px;align-items:center;flex-wrap:wrap}}
.champ{{width:92px;height:92px;border-radius:50%;border:3px solid {GOLD};box-shadow:0 0 0 3px {BG},0 0 22px #C8AA6E55;
  background:#111;object-fit:cover}}
.champ.ph{{display:flex;align-items:center;justify-content:center;font-size:34px;color:{GOLD};font-weight:700}}
.title{{flex:1;min-width:240px}}
.title .name{{font-size:30px;font-weight:700;letter-spacing:.02em;margin:0;color:{TEXT}}}
.title .sub{{color:{MUTED};font-size:14px;margin-top:2px}}
.badge{{display:inline-block;padding:4px 14px;border-radius:999px;font-weight:700;font-size:13px;letter-spacing:.12em;
  text-transform:uppercase;margin-bottom:8px;border:1px solid}}
.win{{color:{GREEN};border-color:{GREEN};background:#2DC66B1A}}
.lose{{color:{RED};border-color:{RED};background:#E840571A}}
.unk{{color:{MUTED};border-color:{MUTED};background:#A09B8C14}}
.hstats{{display:flex;gap:26px;flex-wrap:wrap}}
.hstat .v{{font-size:26px;font-weight:700;color:{TEXT};white-space:nowrap}}
.hstat .l{{font-size:12px;color:{MUTED};text-transform:uppercase;letter-spacing:.1em}}
.kda b{{color:{TEXT}}} .kda .d{{color:{RED}}} .kda .sl{{color:{MUTED};font-weight:400;padding:0 3px}}
.cards{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:0 0 18px}}
@media (max-width:760px){{.cards{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
.card{{background:{PANEL};border:1px solid {BORDER};border-radius:10px;padding:14px 16px;position:relative;overflow:hidden}}
.card:before{{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:{GOLD}}}
.card.t:before{{background:{TEAL}}} .card.r:before{{background:{RED}}} .card.g:before{{background:{GREEN}}}
.card .v{{font-size:24px;font-weight:700}} .card .l{{font-size:12px;color:{MUTED};text-transform:uppercase;letter-spacing:.08em}}
.card .x{{font-size:12.5px;color:{MUTED};margin-top:2px}}
.grid2{{display:grid;grid-template-columns:minmax(0,560px) minmax(0,1fr);gap:22px;align-items:start}}
@media (max-width:900px){{.grid2{{grid-template-columns:1fr}}}}
.map{{width:100%;max-width:512px;border-radius:8px;border:1px solid #C8AA6E66;display:block;box-shadow:0 0 0 4px {BG},0 0 0 5px #C8AA6E33}}
.legend{{display:flex;flex-direction:column;gap:10px;font-size:13.5px;color:{TEXT};margin-top:14px}}
.lg{{display:flex;align-items:center;gap:10px}}
.grad{{width:130px;height:10px;border-radius:5px}}
.lgx{{color:{RED};font-weight:900;font-size:18px;width:18px;text-align:center}}
.small{{font-size:12.5px;color:{MUTED}}}
.bars{{display:flex;flex-direction:column;gap:8px}}
.bar{{display:grid;grid-template-columns:140px 1fr 58px;gap:10px;align-items:center;font-size:13.5px}}
.track{{height:10px;background:#1E2328;border-radius:5px;overflow:hidden}}
.fill{{height:100%;background:linear-gradient(90deg,{TEAL},{GOLD});border-radius:5px}}
.fill.red{{background:linear-gradient(90deg,#8A2335,{RED})}}
.pct{{text-align:right;color:{MUTED};font-variant-numeric:tabular-nums}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
th{{text-align:left;font-size:11.5px;text-transform:uppercase;letter-spacing:.1em;color:{GOLD};font-weight:600;
  border-bottom:1px solid #C8AA6E44;padding:8px 10px}}
td{{padding:10px;border-bottom:1px solid {BORDER};vertical-align:middle}}
tr:last-child td{{border-bottom:none}}
td.t{{font-weight:700;font-variant-numeric:tabular-nums;color:{TEXT};white-space:nowrap}}
.who{{display:flex;flex-wrap:wrap;gap:6px}}
.chip{{display:inline-flex;align-items:center;gap:6px;background:#1E2328;border:1px solid #2C3440;border-radius:999px;
  padding:2px 10px 2px 2px;font-size:13px;white-space:nowrap}}
.chip img{{width:22px;height:22px;border-radius:50%;border:1px solid {RED}}}
.chip.nj{{padding-left:10px}}
.chip.jgl img{{border-color:{ORANGE}}}
.tag{{display:inline-block;padding:3px 10px;border-radius:6px;font-size:12.5px;font-weight:700;white-space:nowrap}}
.tag.ok{{color:{ORANGE};background:#F0A0301F;border:1px solid #F0A03066}}
.tag.no{{color:{RED};background:#E840571A;border:1px solid #E8405766}}
.tag.sv{{color:{GREEN};background:#2DC66B1A;border:1px solid #2DC66B66}}
.recap{{color:{MUTED};font-size:13px;margin-top:4px}}
.tl{{position:relative;height:64px;margin:6px 8px 4px}}
.tl .axis{{position:absolute;left:0;right:0;top:30px;height:4px;border-radius:2px;
  background:linear-gradient(90deg,#0AC8B955,#C8AA6E55,#E8405755)}}
.tl .tick{{position:absolute;top:40px;font-size:11px;color:{MUTED};transform:translateX(-50%)}}
.tl .m{{position:absolute;top:21px;width:22px;height:22px;border-radius:50%;transform:translateX(-50%);
  border:3px solid {BG};box-shadow:0 0 0 2px currentColor}}
.tl .m.s{{background:{GREEN};color:{GREEN}}} .tl .m.d{{background:{RED};color:{RED}}}
.tl .m.x{{top:24px;width:14px;height:14px;background:{BG};color:{RED};border:2px solid {BG};box-shadow:0 0 0 2px {RED}}}
.tl .lbl{{position:absolute;top:0;font-size:11.5px;transform:translateX(-50%);white-space:nowrap;color:{TEXT}}}
.jg{{display:flex;gap:18px;align-items:center;flex-wrap:wrap;margin-bottom:16px}}
.jg img.ic{{width:56px;height:56px;border-radius:50%;border:2px solid {RED};box-shadow:0 0 14px #E8405755}}
.phases{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}}
@media (max-width:760px){{.phases{{grid-template-columns:1fr}}}}
.phase{{background:#07101F;border:1px solid {BORDER};border-radius:10px;padding:12px}}
.phase img{{width:100%;max-width:220px;border-radius:6px;display:block;margin:0 auto 10px;border:1px solid #C8AA6E44}}
.phase h3{{margin:0 0 8px;font-size:14px;color:{TEXT};display:flex;justify-content:space-between}}
.phase h3 span{{color:{MUTED};font-weight:400;font-size:12.5px}}
.zl{{display:flex;justify-content:space-between;font-size:13px;padding:3px 0;border-bottom:1px dashed #1E2328}}
.zl:last-child{{border-bottom:none}}
.lanes{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:16px}}
.lane{{background:#07101F;border:1px solid {BORDER};border-radius:10px;padding:10px 14px;text-align:center}}
.lane .v{{font-size:26px;font-weight:700}} .lane .l{{font-size:12px;color:{MUTED};text-transform:uppercase;letter-spacing:.08em}}
.lane.hot{{border-color:{RED}}} .lane.hot .v{{color:{RED}}}
.tips{{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:10px}}
.tips li{{display:flex;gap:12px;align-items:flex-start;background:#07101F;border:1px solid {BORDER};border-radius:10px;
  padding:12px 14px}}
.tips .i{{flex:0 0 26px;height:26px;border-radius:50%;display:flex;align-items:center;justify-content:center;
  font-weight:800;font-size:14px}}
.tips .warn .i{{background:#F0A03022;color:{ORANGE};border:1px solid #F0A03088}}
.tips .good .i{{background:#2DC66B22;color:{GREEN};border:1px solid #2DC66B88}}
.tips .info .i{{background:#0AC8B922;color:{TEAL};border:1px solid #0AC8B988}}
.obj{{display:grid;max-width:520px;grid-template-columns:1fr 110px 110px;gap:4px 18px;font-size:14px;align-items:center}}
.obj .h{{font-size:11.5px;text-transform:uppercase;letter-spacing:.1em;color:{GOLD}}}
.obj .me{{color:{TEAL};font-weight:700;text-align:center}} .obj .en{{color:{RED};font-weight:700;text-align:center}}
.otl{{display:flex;flex-wrap:wrap;gap:6px;margin-top:14px}}
.otl span{{font-size:12.5px;padding:3px 9px;border-radius:6px;border:1px solid #2C3440;background:#07101F}}
.otl .a{{border-color:#0AC8B966;color:{TEAL}}} .otl .b{{border-color:#E8405766;color:{RED}}}
.empty{{color:{MUTED};font-style:italic}}
.tw{{overflow-x:auto}}
@media (max-width:600px){{.wrap{{padding:16px 16px 30px}} .panel{{padding:16px}} .bar{{grid-template-columns:100px 1fr 44px}}}}
.footer{{text-align:center;color:{MUTED};font-size:12.5px;margin-top:26px;padding-top:16px;border-top:1px solid {BORDER}}}
.footer b{{color:{GOLD};font-weight:600}}
.warnbox{{border:1px solid #F0A03066;background:#F0A03014;color:{ORANGE};border-radius:8px;padding:10px 14px;margin-bottom:18px;font-size:14px}}
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
            f'<div class="hstats">{hs}</div></div></div>')


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
        ("t", str(s.get("cs") if s.get("cs") is not None else "—"), "Sbires tués",
         f"{_num(s.get('cs_per_min'))} par minute"),
        ("t", _num(s.get("vision_score"), 0), "Score de vision", f"{_num(s.get('vision_per_min'), 2)} par minute"),
        ("", f"{_num(kp * 100, 0)} %" if kp is not None else "—", "Participation",
         f"{s.get('team_kills')} kills d'équipe" if s.get("team_kills") is not None else "kills d'équipe inconnus"),
        ("g" if ganks and surv / ganks >= 0.5 else "r" if ganks else "g", f"{surv} / {ganks}", "Ganks survécus",
         "alertes DANGER de l'app"),
        ("r", str(a.get("deaths_warned", 0)), "Morts après alerte",
         f"{a.get('deaths_unwarned', 0)} mort(s) sans alerte"),
        ("", str(s.get("level") or "—"), "Niveau final", f"{_num(s.get('gold'), 0)} PO en poche" if s.get("gold")
         is not None else ""),
        ("t", f"{mine.get('dragons', 0)} – {theirs.get('dragons', 0)}" if mine and theirs else "—", "Dragons",
         f"Barons {mine.get('barons', 0)} – {theirs.get('barons', 0)}" if mine and theirs else ""),
    ]
    out = "".join(f'<div class="card {c}"><div class="v">{_e(v)}</div><div class="l">{_e(l)}</div>'
                  f'<div class="x">{_e(x)}</div></div>' for c, v, l, x in cards)
    return f'<div class="cards">{out}</div>'


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
        f'<span>{_e(jname)} aperçu — de 0 à {dur_min} min</span></div>'
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
            tag = f'<span class="tag ok">Oui, {_num(d.get("alert_before_s"), 0)} s avant</span>'
            verdict = "alerte ignorée"
        else:
            tag = '<span class="tag no">Non</span>'
            verdict = "mort sans alerte"
        near = d.get("nearby") or []
        near_txt = (", ".join(_name_for(roster, k) for k in near) if near else "personne")
        rows.append(
            f'<tr><td class="t">{_e(d.get("time"))}</td><td>{_e(d.get("zone_label") or _ZONE_FR_FALLBACK)}</td>'
            f'<td>{who}<div class="recap">Visibles à proximité : {_e(near_txt)}</div></td>'
            f'<td>{"<b style=color:" + ORANGE + ">Oui</b>" if d.get("jungler_involved") else "Non"}</td>'
            f'<td>{tag}<div class="recap">{_e(verdict)}</div></td></tr>'
            f'<tr><td></td><td colspan="4" class="recap" style="padding-top:0">« {_e(d.get("recap"))} »</td></tr>')
    return ('<div class="panel"><h2>Mes morts</h2><div class="tw"><table><thead><tr><th>Heure</th><th>Zone</th><th>Tué par</th>'
            '<th>Jungler ?</th><th>Alerte donnée ?</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table></div></div>')


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
        title = f'{g.get("time")} — {g.get("text")} ({g.get("outcome_label")})'
        marks.append(f'<span class="m {cls}" style="left:{x:.2f}%" title="{_e(title)}"></span>'
                     f'<span class="lbl" style="left:{x:.2f}%">{_e(g.get("time"))}</span>')
    death_marks = "".join(
        f'<span class="m x" style="left:{100.0 * (_f(d.get("game_time"), 0.0) or 0.0) / dur:.2f}%" '
        f'title="Mort à {_e(d.get("time"))}"></span>'
        for d in a.get("deaths") or [] if not any(
            g.get("death_time") == d.get("game_time") for g in ganks))
    timeline = (f'<div class="tl"><div class="axis"></div>{ticks}{death_marks}{"".join(marks)}</div>'
                f'<p class="small"><span style="color:{GREEN}">●</span> gank survécu · '
                f'<span style="color:{RED}">●</span> mort dans les 15 s · '
                f'<span style="color:{RED}">○</span> mort hors gank annoncé</p>')
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
    return (f'<div class="panel"><h2>Parcours du jungler ennemi</h2>{head}<div class="phases">{"".join(phases)}</div>'
            f'<div class="lanes">{lane_html}</div><p class="small">Voies où il a participé à un kill sur ton équipe '
            f'(zone du kill, sinon rôle de la victime).</p></div>')


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
    return (f'<div class="panel"><h2>Objectifs</h2>{grid}'
            f'{"<div class=otl>" + tl + "</div>" if tl else ""}</div>')


def _tips_section(a: dict) -> str:
    items = a.get("tip_items") or [{"text": t, "kind": "info"} for t in a.get("tips") or []]
    if not items:
        return ""
    sym = {"warn": "!", "good": "✓", "info": "i"}
    lis = "".join(f'<li class="{_e(t.get("kind", "info"))}"><span class="i">{sym.get(t.get("kind"), "i")}</span>'
                  f'<span>{_e(t.get("text"))}</span></li>' for t in items)
    return f'<div class="panel"><h2>Conseils pour la prochaine partie</h2><ul class="tips">{lis}</ul></div>'


def render_report_html(record: dict, analysis: dict | None = None) -> str:
    """Self-contained French HTML report. Never raises (degraded page on error)."""
    try:
        rec = record if isinstance(record, dict) else {}
        if analysis is None:
            from treeaicoach.analysis import analyze_game

            analysis = analyze_game(rec)
        a = analysis if isinstance(analysis, dict) else {}
        s = a.get("summary") or {}
        title = f'{s.get("champion_name") or "Partie"} — {s.get("result_label") or ""}'.strip(" —")
        parts = []
        for fn in (lambda: _header(rec, a), lambda: _cards(a), lambda: _tips_section(a),
                   lambda: _map_section(rec, a), lambda: _deaths_section(rec, a), lambda: _ganks_section(rec, a),
                   lambda: _jungler_section(rec, a), lambda: _objectives_section(a)):
            try:
                parts.append(fn())
            except Exception:
                log.exception("report section failed")
        warn = ""
        if rec.get("incomplete") or s.get("result") is None:
            warn = ('<div class="warnbox">Enregistrement incomplet (partie interrompue ou application fermée '
                    'avant la fin) : les chiffres couvrent uniquement la partie enregistrée.</div>')
        version = (rec.get("meta") or {}).get("app_version") or __version__
        footer = (f'<div class="footer"><b>{_e(APP_NAME)}</b> v{_e(version)} · rapport généré localement le '
                  f'{_e(_date_fr(_dt.datetime.now().astimezone().isoformat()))} · aucune donnée envoyée · '
                  f'sources : capture de la minimap et API Live Client de Riot</div>')
        return ("<!DOCTYPE html>\n<html lang=\"fr\"><head><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
                f"<title>{_e(APP_NAME)} — {_e(title)}</title><style>{CSS}</style></head>"
                f"<body><div class=\"wrap\">{warn}{''.join(parts)}{footer}</div></body></html>")
    except Exception:
        log.exception("render_report_html failed")
        return ("<!DOCTYPE html><html lang=\"fr\"><head><meta charset=\"utf-8\"><title>Rapport</title></head>"
                f"<body style=\"background:{BG};color:{TEXT};font-family:sans-serif\">"
                "<p>Le rapport n'a pas pu être généré.</p></body></html>")


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


def write_report(record_path: Path) -> Path | None:
    """Read a record JSON, write its HTML report next to it and return its path (None on error)."""
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
        text = render_report_html(record, analyze_game(record))
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
        if not isinstance(data, dict):
            return None
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


def _iter_record_files(d: Path) -> Iterable[Path]:
    try:
        for p in d.iterdir():
            if p.is_file() and p.name.endswith(".json") and not p.name.startswith("."):
                yield p
    except FileNotFoundError:
        return
    except OSError as exc:
        log.warning("Cannot list %s: %s", d, exc)


def list_games(limit: int = 50, games_dir: Path | None = None) -> list[dict]:
    """Recorded games, newest first (reads only the summaries). Never raises.

    Each entry: ``path``, ``report_path`` (or None), ``start``, ``date_label``, ``champion``,
    ``champion_name``, ``result`` ("Win"/"Lose"/None), ``result_label``, ``kills``, ``deaths``,
    ``assists``, ``kda`` ("3/4/5"), ``cs``, ``duration``, ``duration_text``, ``ganks``,
    ``ganks_survived``, ``incomplete`` (True for a ``.partial.json`` left by a crash).
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
                "mtime": e["_mtime"],
            })
        out.sort(key=lambda g: (str(g.get("start") or ""), g["mtime"]), reverse=True)
        return out
    except Exception:
        log.exception("list_games failed")
        return []
