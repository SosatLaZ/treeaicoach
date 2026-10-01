"""Building blocks of the v1.5 interface (ui.py): drawings, presets, diagnostics, voice gate.

Everything here is pure / Tk-free except :class:`Tooltip` (Tk imported lazily), so it is unit-tested
without a display. Nothing in this module may raise into the UI: every public helper is defensive.

* PIL drawings: :func:`extra_icon` (line icons), :func:`role_glyph` / :func:`decorate_portrait`
  (role badge + MIA arc on a round champion portrait), :func:`hero_background` (hextech banner),
  :func:`gradient_rule` (gold separator).
* Settings helpers: :data:`PRESETS` / :func:`preset_of` / :func:`preset_changes`,
  :func:`export_settings` / :func:`import_settings`, :func:`in_quiet_hours`.
* :class:`VoiceGate`: wraps the voice handed to the engine and applies the "quiet" settings
  (first seconds of a game, quiet hours, information announcements) without touching voice.py.
* :func:`diagnostic_text` (clipboard report), :data:`CHANGELOG`, :data:`SHORTCUTS`, :data:`ABOUT_TEXT`.
* :func:`lane_opponent`: my role + the enemy of the same role, from an ``OverlayState``.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import logging
import math
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFilter

log = logging.getLogger(__name__)

# ======================================================================================
# Colours shared with ui.py (kept in sync with its palette)
# ======================================================================================
GOLD = "#C8AA6E"
GOLD_DARK = "#785A28"
ALLY = "#3A8DDE"            # allied team ring (LoL blue)
ENEMY = "#E84057"           # enemy team ring
BADGE_BG = "#010A13"

ROLE_ORDER: tuple[str, ...] = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
ROLE_FR: dict[str, str] = {"TOP": "Haut", "JUNGLE": "Jungle", "MIDDLE": "Milieu", "BOTTOM": "Tireur",
                           "UTILITY": "Support"}
ROLE_SHORT: dict[str, str] = {"TOP": "TOP", "JUNGLE": "JGL", "MIDDLE": "MID", "BOTTOM": "ADC",
                              "UTILITY": "SUP"}

#: Colour-blind friendly threat palette (Okabe-Ito): safe / warning / danger.
COLORBLIND_THREAT = {0: "#56B4E9", 1: "#F0E442", 2: "#D55E00"}


def hex_rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    try:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except (ValueError, IndexError):
        return (128, 128, 128)


def blend(c1: str, c2: str, t: float) -> str:
    a, b = hex_rgb(c1), hex_rgb(c2)
    t = min(max(float(t), 0.0), 1.0)
    return "#%02X%02X%02X" % tuple(int(round(x * (1 - t) + y * t)) for x, y in zip(a, b))


def norm_role(role: Any) -> str | None:
    """API position / role name -> one of :data:`ROLE_ORDER` (None if unknown)."""
    if not isinstance(role, str):
        role = getattr(role, "value", None) or getattr(role, "name", None)
        if not isinstance(role, str):
            return None
    r = role.strip().upper()
    alias = {"MID": "MIDDLE", "ADC": "BOTTOM", "BOT": "BOTTOM", "SUPPORT": "UTILITY", "SUP": "UTILITY",
             "JUNGLER": "JUNGLE", "JGL": "JUNGLE", "CARRY": "BOTTOM"}
    r = alias.get(r, r)
    return r if r in ROLE_ORDER else None


# ======================================================================================
# Icons
# ======================================================================================
EXTRA_ICONS: frozenset[str] = frozenset({
    "shield", "cpu", "copy", "keyboard", "info", "download", "upload", "star", "bell", "moon", "sliders",
    "eye", "mute", "minimize", "sparkle", "reset", "check", "close", "map", "users", "swords", "clock",
})


def extra_icon(kind: str, size: int = 18, color: str = "#A09B8C") -> Image.Image:
    """Additional line icons (same style as ``ui.nav_icon``), RGBA, 4x supersampled."""
    ss = 4
    S = size * ss
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    col = hex_rgb(color) + (255,)
    w = int(1.7 * ss)
    try:
        if kind == "shield":
            d.polygon([(S * .5, S * .06), (S * .88, S * .2), (S * .82, S * .58), (S * .5, S * .94),
                       (S * .18, S * .58), (S * .12, S * .2)], outline=col, width=w)
            d.line((S * .34, S * .5, S * .46, S * .62, S * .68, S * .38), fill=col, width=w, joint="curve")
        elif kind == "cpu":
            d.rounded_rectangle((S * .22, S * .22, S * .78, S * .78), radius=ss * 2, outline=col, width=w)
            d.rectangle((S * .4, S * .4, S * .6, S * .6), fill=col)
            for t in (.36, .5, .64):
                for a, b in (((t, .04), (t, .2)), ((t, .8), (t, .96)), ((.04, t), (.2, t)), ((.8, t), (.96, t))):
                    d.line((S * a[0], S * a[1], S * b[0], S * b[1]), fill=col, width=w)
        elif kind == "copy":
            d.rounded_rectangle((S * .3, S * .3, S * .9, S * .9), radius=ss * 2, outline=col, width=w)
            d.line((S * .1, S * .66, S * .1, S * .1, S * .66, S * .1), fill=col, width=w)
        elif kind == "keyboard":
            d.rounded_rectangle((S * .04, S * .22, S * .96, S * .78), radius=ss * 2, outline=col, width=w)
            for j, y in enumerate((.38, .52)):
                for i in range(5 - j):
                    x = .18 + i * .16 + j * .08
                    d.rectangle((S * (x - .03), S * (y - .03), S * (x + .03), S * (y + .03)), fill=col)
            d.line((S * .3, S * .66, S * .7, S * .66), fill=col, width=w)
        elif kind == "info":
            d.ellipse((S * .07, S * .07, S * .93, S * .93), outline=col, width=w)
            d.line((S * .5, S * .44, S * .5, S * .74), fill=col, width=w)
            d.ellipse((S * .45, S * .24, S * .55, S * .34), fill=col)
        elif kind in ("download", "upload"):
            d.line((S * .12, S * .7, S * .12, S * .9, S * .88, S * .9, S * .88, S * .7), fill=col, width=w)
            d.line((S * .5, S * .08, S * .5, S * .66), fill=col, width=w)
            if kind == "download":
                d.line((S * .3, S * .46, S * .5, S * .66, S * .7, S * .46), fill=col, width=w)
            else:
                d.line((S * .3, S * .28, S * .5, S * .08, S * .7, S * .28), fill=col, width=w)
        elif kind == "star":
            pts = []
            for k in range(10):
                r = S * (.46 if k % 2 == 0 else .2)
                a = -math.pi / 2 + k * math.pi / 5
                pts.append((S / 2 + r * math.cos(a), S / 2 + r * math.sin(a) + S * .04))
            d.polygon(pts, fill=col)
        elif kind == "bell":
            d.chord((S * .2, S * .12, S * .8, S * .8), 180, 360, outline=col, width=w)
            d.line((S * .2, S * .46, S * .2, S * .72), fill=col, width=w)
            d.line((S * .8, S * .46, S * .8, S * .72), fill=col, width=w)
            d.line((S * .1, S * .74, S * .9, S * .74), fill=col, width=w)
            d.ellipse((S * .42, S * .8, S * .58, S * .94), fill=col)
        elif kind == "moon":
            d.ellipse((S * .1, S * .1, S * .86, S * .86), fill=col)
            d.ellipse((S * .32, S * .02, S * 1.02, S * .7), fill=(0, 0, 0, 0))
        elif kind == "sliders":
            for i, (y, x) in enumerate(((.22, .3), (.5, .66), (.78, .42))):
                d.line((S * .06, S * y, S * .94, S * y), fill=col, width=w)
                d.ellipse((S * (x - .1), S * (y - .1), S * (x + .1), S * (y + .1)), fill=col)
        elif kind == "eye":
            d.chord((S * .04, S * .2, S * .96, S * 1.0), 200, 340, outline=col, width=w)
            d.chord((S * .04, S * 0, S * .96, S * .8), 20, 160, outline=col, width=w)
            d.ellipse((S * .36, S * .36, S * .64, S * .64), fill=col)
        elif kind == "mute":
            d.polygon([(S * .06, S * .38), (S * .24, S * .38), (S * .46, S * .16), (S * .46, S * .84),
                       (S * .24, S * .62), (S * .06, S * .62)], outline=col, width=w)
            d.line((S * .6, S * .36, S * .9, S * .66), fill=col, width=w)
            d.line((S * .9, S * .36, S * .6, S * .66), fill=col, width=w)
        elif kind == "minimize":
            d.line((S * .16, S * .78, S * .84, S * .78), fill=col, width=w)
        elif kind == "sparkle":
            for cx, cy, r in ((.42, .5, .36), (.8, .22, .14)):
                pts = [(S * cx, S * (cy - r)), (S * (cx + r * .25), S * (cy - r * .25)), (S * (cx + r), S * cy),
                       (S * (cx + r * .25), S * (cy + r * .25)), (S * cx, S * (cy + r)),
                       (S * (cx - r * .25), S * (cy + r * .25)), (S * (cx - r), S * cy),
                       (S * (cx - r * .25), S * (cy - r * .25))]
                d.polygon(pts, fill=col)
        elif kind == "reset":
            d.arc((S * .12, S * .12, S * .88, S * .88), 120, 420, fill=col, width=w)
            d.polygon([(S * .06, S * .18), (S * .1, S * .5), (S * .36, S * .32)], fill=col)
        elif kind == "check":
            d.line((S * .14, S * .52, S * .4, S * .78, S * .88, S * .24), fill=col, width=int(w * 1.3),
                   joint="curve")
        elif kind == "close":
            d.line((S * .2, S * .2, S * .8, S * .8), fill=col, width=w)
            d.line((S * .8, S * .2, S * .2, S * .8), fill=col, width=w)
        elif kind == "map":
            d.polygon([(S * .06, S * .2), (S * .36, S * .08), (S * .64, S * .2), (S * .94, S * .08),
                       (S * .94, S * .8), (S * .64, S * .92), (S * .36, S * .8), (S * .06, S * .92)],
                      outline=col, width=w)
            d.line((S * .36, S * .08, S * .36, S * .8), fill=col, width=w)
            d.line((S * .64, S * .2, S * .64, S * .92), fill=col, width=w)
        elif kind == "users":
            for cx, r, k in ((.36, .16, 1.0), (.7, .13, .8)):
                d.ellipse((S * (cx - r), S * (.32 - r), S * (cx + r), S * (.32 + r)), outline=col, width=w)
                d.arc((S * (cx - r * 2), S * .56, S * (cx + r * 2), S * (.56 + r * 4)), 180, 360, fill=col,
                      width=w)
        elif kind == "swords":
            for a, b in (((.1, .1), (.78, .78)), ((.9, .1), (.22, .78))):
                d.line((S * a[0], S * a[1], S * b[0], S * b[1]), fill=col, width=w)
            for x, y in ((.72, .84), (.28, .84)):
                d.line((S * (x - .14), S * (y - .14) + S * .02, S * (x + .14), S * (y + .14) - S * .02),
                       fill=col, width=w)
        elif kind == "clock":
            d.ellipse((S * .07, S * .07, S * .93, S * .93), outline=col, width=w)
            d.line((S * .5, S * .22, S * .5, S * .5, S * .7, S * .62), fill=col, width=w)
    except Exception:
        log.debug("extra_icon %s failed", kind, exc_info=True)
    return im.resize((size, size), Image.LANCZOS)


def role_glyph(role: str | None, size: int, color: str = GOLD) -> Image.Image:
    """Position icon in the spirit of the game's own ones (RGBA, ``size`` px)."""
    ss = 4
    S = size * ss
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    col = hex_rgb(color) + (255,)
    dim = hex_rgb(color) + (90,)
    r = norm_role(role)
    m, M = S * .14, S * .86
    w = max(ss, int(S * .1))
    if r in ("TOP", "BOTTOM", "MIDDLE"):
        q = S * .3          # size of the small corner square
        if r == "TOP":
            d.line((m + w / 2, M, m + w / 2, m + w / 2, M, m + w / 2), fill=col, width=w)
            d.rectangle((M - q, M - q, M, M), fill=dim)
            d.rectangle((S * .4, S * .4, S * .6, S * .6), fill=col)
        elif r == "BOTTOM":
            d.line((m, M - w / 2, M - w / 2, M - w / 2, M - w / 2, m), fill=col, width=w)
            d.rectangle((m, m, m + q, m + q), fill=dim)
            d.rectangle((S * .4, S * .4, S * .6, S * .6), fill=col)
        else:
            d.line((m + w / 2, M - w / 2, M - w / 2, m + w / 2), fill=col, width=int(w * 1.3))
            d.polygon([(m, m), (m + q, m), (m, m + q)], fill=dim)
            d.polygon([(M, M), (M - q, M), (M, M - q)], fill=dim)
    elif r == "JUNGLE":
        d.line((S * .5, S * .1, S * .5, S * .9), fill=col, width=w)
        d.line((S * .26, S * .2, S * .4, S * .62, S * .5, S * .9), fill=col, width=w, joint="curve")
        d.line((S * .74, S * .2, S * .6, S * .62, S * .5, S * .9), fill=col, width=w, joint="curve")
    elif r == "UTILITY":
        d.polygon([(S * .5, S * .14), (S * .84, S * .3), (S * .74, S * .7), (S * .5, S * .88),
                   (S * .26, S * .7), (S * .16, S * .3)], outline=col, width=w)
        d.line((S * .5, S * .34, S * .5, S * .68), fill=col, width=w)
        d.line((S * .34, S * .5, S * .66, S * .5), fill=col, width=w)
    else:
        d.ellipse((S * .3, S * .3, S * .7, S * .7), outline=dim, width=w)
    return im.resize((size, size), Image.LANCZOS)


def decorate_portrait(img: Image.Image, role: str | None = None, mia_frac: float | None = None,
                      arc_color: str = "#F0A030", badge_bg: str = BADGE_BG, badge_ring: str = GOLD_DARK,
                      star: bool = False) -> Image.Image:
    """Add a role badge (bottom-right), an MIA progress arc and a jungler star to a portrait (RGB)."""
    try:
        base = img.convert("RGBA")
        size = base.size[0]
        ss = 4
        S = size * ss
        layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        if mia_frac is not None and math.isfinite(mia_frac):
            f = min(max(float(mia_frac), 0.0), 1.0)
            pw = max(ss * 2, int(S * .075))
            d.arc((ss, ss, S - ss - 1, S - ss - 1), -90, -90 + 360 * f, fill=hex_rgb(arc_color) + (255,),
                  width=pw)
        if norm_role(role):
            b = int(S * .4)
            x0, y0 = S - b, S - b
            d.ellipse((x0, y0, S - 1, S - 1), fill=hex_rgb(badge_bg) + (255,),
                      outline=hex_rgb(badge_ring) + (255,), width=max(ss, int(b * .08)))
            g = role_glyph(role, int(b * .62), GOLD)
            layer.alpha_composite(g, (x0 + (b - g.size[0]) // 2, y0 + (b - g.size[1]) // 2))
        if star:
            st = extra_icon("star", int(S * .34), GOLD)
            b = int(S * .38)
            d.ellipse((0, S - b, b, S - 1), fill=hex_rgb(badge_bg) + (255,),
                      outline=hex_rgb(badge_ring) + (255,), width=max(ss, int(b * .08)))
            layer.alpha_composite(st, ((b - st.size[0]) // 2, S - b + (b - st.size[1]) // 2))
        small = layer.resize((size, size), Image.LANCZOS)
        base.alpha_composite(small)
        return base.convert("RGB")
    except Exception:
        log.debug("decorate_portrait failed", exc_info=True)
        return img


def hero_background(w: int, h: int, glow: str, bg: str = "#010A13", panel: str = "#0A1428",
                    border: str = "#1E2328", gold: str = GOLD, radius: int = 14) -> Image.Image:
    """Hextech banner: dark gradient card, coloured glow on the left, gold top rule and corner marks."""
    w, h = max(8, int(w)), max(8, int(h))
    try:
        import numpy as np  # noqa: PLC0415

        x = np.linspace(0.0, 1.0, w, dtype=np.float32)[None, :, None]
        y = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None, None]
        p0 = np.array(hex_rgb("#0E1D38"), np.float32)
        p1 = np.array(hex_rgb(panel), np.float32)
        img = p0 * (1 - np.clip(x * 1.6, 0, 1)) + p1 * np.clip(x * 1.6, 0, 1)
        img = img * (1.0 - 0.18 * y)                       # darker towards the bottom
        g = np.array(hex_rgb(glow), np.float32)
        cx, cy = 0.06, 0.38
        dx = (x - cx) * (w / max(h, 1)) * 0.55
        dy = (y - cy)
        k = np.exp(-(dx * dx + dy * dy) / 0.22)[..., :1] * 0.30
        img = img * (1 - k) + g * k
        arr = np.clip(img, 0, 255).astype(np.uint8)
        card = Image.fromarray(np.broadcast_to(arr, (h, w, 3)).copy(), "RGB").convert("RGBA")
    except Exception:
        card = Image.new("RGBA", (w, h), hex_rgb(panel) + (255,))
    d = ImageDraw.Draw(card)
    gc = hex_rgb(gold)
    # gold top rule fading out to the right
    for i in range(radius, w - radius):
        a = int(200 * max(0.0, 1.0 - (i - radius) / max(1, (w - 2 * radius) * 0.85)))
        if a <= 0:
            break
        d.point((i, 0), fill=gc + (a,))
    # corner ornaments (bottom-right)
    o = hex_rgb(gold) + (110,)
    d.line((w - 34, h - 7, w - 7, h - 7, w - 7, h - 34), fill=o, width=1)
    d.line((w - 22, h - 11, w - 11, h - 11, w - 11, h - 22), fill=o, width=1)
    # rounded corners + border on the window background
    ss = 3
    mask = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * ss - 1, h * ss - 1), radius=radius * ss, fill=255)
    mask = mask.resize((w, h), Image.LANCZOS)
    out = Image.new("RGBA", (w, h), hex_rgb(bg) + (255,))
    out.paste(card, (0, 0), mask)
    edge = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(edge).rounded_rectangle((0, 0, w * ss - 1, h * ss - 1), radius=radius * ss, outline=255,
                                           width=ss)
    edge = edge.resize((w, h), Image.LANCZOS)
    out.paste(Image.new("RGBA", (w, h), hex_rgb(border) + (255,)), (0, 0), edge)
    # re-draw the gold rule over the border
    d = ImageDraw.Draw(out)
    for i in range(radius, w - radius):
        a = max(0.0, 1.0 - (i - radius) / max(1, (w - 2 * radius) * 0.85))
        if a <= 0:
            break
        base = out.getpixel((i, 0))[:3]
        d.point((i, 0), fill=tuple(int(b * (1 - a * .8) + c * a * .8) for b, c in zip(base, gc)) + (255,))
    return out.convert("RGB")


def glow_dot(size: int, color: str, bg: str) -> Image.Image:
    """Soft glowing dot (status indicator), RGB on ``bg``."""
    ss = 4
    S = size * ss
    im = Image.new("RGBA", (S, S), hex_rgb(bg) + (255,))
    halo = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(halo).ellipse((S * .2, S * .2, S * .8, S * .8), fill=hex_rgb(color) + (160,))
    halo = halo.filter(ImageFilter.GaussianBlur(S * .12))
    im.alpha_composite(halo)
    ImageDraw.Draw(im).ellipse((S * .34, S * .34, S * .66, S * .66), fill=hex_rgb(color) + (255,))
    return im.resize((size, size), Image.LANCZOS).convert("RGB")


# ======================================================================================
# Presets
# ======================================================================================
PRESET_LABELS: tuple[tuple[str, str], ...] = (("discret", "Discret"), ("equilibre", "Équilibré"),
                                              ("complet", "Complet"))
PRESET_HELP: dict[str, str] = {
    "discret": "Seulement les vrais dangers, overlay minimal, aucune annonce d'information.",
    "equilibre": "Réglages recommandés : alertes de gank, minuteurs et rappels utiles.",
    "complet": "Tout est annoncé et affiché (MIA, zones, flèches, fantômes, conseils).",
}
PRESETS: dict[str, dict[str, Any]] = {
    "discret": {
        "alert_jungler_approach": True, "alert_roam": False, "alert_collapse": True,
        "alert_jungler_spotted": False, "alert_laner_mia": False, "objective_timers": False,
        "recall_reminder": False, "control_ward_reminder": False, "voice_info_alerts": False,
        "sensitivity": 0.85, "danger_flash": False, "hud_enabled": False, "fog_mode": "off",
        "layer_roles": False, "layer_arrows": False, "layer_zones": True, "layer_ghosts": False,
        "overlay_opacity": 0.8,
    },
    "equilibre": {
        "alert_jungler_approach": True, "alert_roam": True, "alert_collapse": True,
        "alert_jungler_spotted": True, "alert_laner_mia": False, "objective_timers": True,
        "recall_reminder": True, "control_ward_reminder": True, "voice_info_alerts": True,
        "sensitivity": 1.0, "danger_flash": True, "hud_enabled": True, "fog_mode": "jungler",
        "layer_roles": True, "layer_arrows": True, "layer_zones": True, "layer_ghosts": True,
        "overlay_opacity": 1.0,
    },
    "complet": {
        "alert_jungler_approach": True, "alert_roam": True, "alert_collapse": True,
        "alert_jungler_spotted": True, "alert_laner_mia": True, "objective_timers": True,
        "recall_reminder": True, "control_ward_reminder": True, "voice_info_alerts": True,
        "sensitivity": 1.2, "danger_flash": True, "hud_enabled": True, "fog_mode": "all",
        "layer_roles": True, "layer_arrows": True, "layer_zones": True, "layer_ghosts": True,
        "overlay_opacity": 1.0,
    },
}


def preset_changes(cfg: Any, name: str) -> dict[str, Any]:
    """Fields of preset ``name`` that exist on ``cfg`` (unknown preset -> {})."""
    spec = PRESETS.get(name, {})
    return {k: v for k, v in spec.items() if hasattr(cfg, k)}


def preset_of(cfg: Any) -> str | None:
    """Name of the preset ``cfg`` matches exactly, else None ("personnalisé")."""
    for name in PRESETS:
        ch = preset_changes(cfg, name)
        if ch and all(_close(getattr(cfg, k, None), v) for k, v in ch.items()):
            return name
    return None


def _close(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) < 1e-6
        except (TypeError, ValueError):
            return False
    return a == b


# ======================================================================================
# Settings export / import
# ======================================================================================
#: Never exported (secret / machine specific).
EXPORT_EXCLUDE = frozenset({"github_token", "ai_api_key", "ui_geometry", "manual_minimap_rect", "icon_scale_by_res",
                            "radar_xy", "hud_xy", "ui_last_page", "ui_onboarding_done", "ui_seen_changelog"})


def export_settings(cfg: Any, path: str | os.PathLike[str]) -> bool:
    """Write the shareable settings as JSON (UTF-8). Never raises."""
    try:
        data = cfg.to_dict() if hasattr(cfg, "to_dict") else dict(cfg)
        payload = {"treeaicoach_settings": 1, "exported_at": _dt.datetime.now().isoformat(timespec="seconds")}
        payload.update({k: v for k, v in data.items() if k not in EXPORT_EXCLUDE})
        Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return True
    except Exception:
        log.exception("Settings export failed")
        return False


def import_settings(cfg: Any, path: str | os.PathLike[str]) -> tuple[Any, int]:
    """(new validated config, number of imported fields). Machine-specific fields are kept.

    Raises ValueError (French message) when the file is not a settings file.
    """
    try:
        raw = Path(path).read_bytes()
        if len(raw) > 1_000_000:
            raise ValueError("fichier trop volumineux")
        data = json.loads(raw.decode("utf-8-sig"))
    except ValueError as exc:
        raise ValueError(f"Fichier de réglages illisible ({exc}).") from exc
    except OSError as exc:
        raise ValueError(f"Impossible de lire le fichier ({exc}).") from exc
    if not isinstance(data, dict):
        raise ValueError("Ce fichier ne contient pas de réglages TreeAI Coach.")
    names = {f.name for f in dataclasses.fields(cfg)}
    upd = {k: v for k, v in data.items() if k in names and k not in EXPORT_EXCLUDE}
    if not upd:
        raise ValueError("Ce fichier ne contient aucun réglage TreeAI Coach reconnu.")
    new = dataclasses.replace(cfg, **upd).validated()
    return new, len(upd)


# ======================================================================================
# Quiet settings & voice gate
# ======================================================================================
def in_quiet_hours(hour: int, start: int, end: int) -> bool:
    """True if ``hour`` (0-23) is in [start, end) — the range may wrap past midnight."""
    try:
        hour, start, end = int(hour) % 24, int(start) % 24, int(end) % 24
    except (TypeError, ValueError):
        return False
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def speech_allowed(cfg: Any, level: int, game_time: float | None, hour: int | None = None,
                   explicit: bool = False) -> bool:
    """Whether the coach may speak an announcement of ``level`` now. DANGER always passes."""
    try:
        level = int(level)
    except (TypeError, ValueError):
        level = 1
    if level >= 2 or explicit:
        return True
    if level <= 0 and not bool(getattr(cfg, "voice_info_alerts", True)):
        return False
    quiet_s = getattr(cfg, "voice_quiet_start_s", 0) or 0
    try:
        if quiet_s > 0 and game_time is not None and 0 <= float(game_time) < float(quiet_s):
            return False
    except (TypeError, ValueError):
        pass
    if bool(getattr(cfg, "quiet_hours", False)):
        h = _dt.datetime.now().hour if hour is None else hour
        if in_quiet_hours(h, getattr(cfg, "quiet_start_h", 23), getattr(cfg, "quiet_end_h", 8)):
            return False
    return True


class VoiceGate:
    """Voice proxy given to the engine: drops non-danger speech while the user wants calm.

    Everything except :meth:`say` is forwarded to the current real voice (``get_voice()``), so the
    UI can swap the voice object without rebuilding the engine.
    """

    def __init__(self, get_voice: Callable[[], Any], get_cfg: Callable[[], Any],
                 get_game_time: Callable[[], float | None]) -> None:
        self._get_voice = get_voice
        self._get_cfg = get_cfg
        self._get_game_time = get_game_time
        self.explicit_until = 0.0        # monotonic deadline: user-requested speech (F9...) passes
        self.dropped = 0

    def say(self, text: str, level: int = 1) -> None:
        voice = self._get_voice()
        if voice is None:
            return
        try:
            explicit = time.monotonic() < self.explicit_until
            if not speech_allowed(self._get_cfg(), level, self._get_game_time(), explicit=explicit):
                self.dropped += 1
                log.debug("Announcement muted by the quiet settings: %s", text)
                return
        except Exception:
            log.debug("VoiceGate check failed", exc_info=True)
        voice.say(text, level)

    def allow_next(self, seconds: float = 3.0) -> None:
        self.explicit_until = time.monotonic() + seconds

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        voice = self._get_voice()
        if voice is None:
            raise AttributeError(name)
        return getattr(voice, name)


# ======================================================================================
# Dashboard helpers
# ======================================================================================
def lane_opponent(ov: Any) -> tuple[str | None, str | None, Any]:
    """(my alias, my role, enemy view of the same role) from an OverlayState. Never raises."""
    try:
        roles = dict(getattr(ov, "roles", {}) or {})
        allies = {getattr(a, "alias", None) for a in (getattr(ov, "allies", []) or [])}
        enemies = list(getattr(ov, "enemies", []) or [])
        enemy_alias = {getattr(e, "alias", None) for e in enemies}
        me = getattr(ov, "me_alias", None)
        if not me:
            mine = [k for k in roles if k not in allies and k not in enemy_alias]
            me = mine[0] if len(mine) == 1 else None
        my_role = norm_role(getattr(ov, "my_role", None) or (roles.get(me) if me else None))
        if my_role is None:
            return me, None, None
        for e in enemies:
            r = norm_role(getattr(e, "role", None) or roles.get(getattr(e, "alias", "") or ""))
            if r == my_role:
                return me, my_role, e
        return me, my_role, None
    except Exception:
        log.debug("lane_opponent failed", exc_info=True)
        return None, None, None


class CpuMeter:
    """Process CPU usage (all threads, % of the whole machine), sampled on demand."""

    def __init__(self) -> None:
        self._t = time.monotonic()
        self._c = time.process_time()
        self.value: float | None = None
        self._n = max(1, os.cpu_count() or 1)

    def sample(self) -> float | None:
        t, c = time.monotonic(), time.process_time()
        dt = t - self._t
        if dt >= 0.9:
            self.value = max(0.0, min(100.0, 100.0 * (c - self._c) / dt / self._n))
            self._t, self._c = t, c
        return self.value


# ======================================================================================
# Diagnostics
# ======================================================================================
def last_log_errors(path: Path | None, n: int = 8, max_bytes: int = 400_000) -> list[str]:
    """Last ``n`` ERROR / CRITICAL lines of the log file (tail read). Never raises."""
    if path is None:
        return []
    try:
        p = Path(path)
        size = p.stat().st_size
        with p.open("rb") as fh:
            fh.seek(max(0, size - max_bytes))
            text = fh.read().decode("utf-8", "replace")
        lines = [ln.strip() for ln in text.splitlines() if " ERROR " in ln or " CRITICAL " in ln
                 or "| ERROR" in ln or "| CRITICAL" in ln]
        return [ln[:300] for ln in lines[-n:]]
    except Exception:
        return []


def diagnostic_text(*, version: str, cfg: Any, status: Any = None, engine: Any = None, overlay: Any = None,
                    detector: Any = None, voice: Any = None, log_file: Path | None = None,
                    data_dir: Path | None = None, cpu: float | None = None, demo: bool = False) -> str:
    """Plain-text report for bug reports ("Copier le diagnostic"). No secret (token) inside."""
    def g(obj: Any, name: str, default: Any = "—") -> Any:
        try:
            v = getattr(obj, name, default)
            return v() if callable(v) else v
        except Exception:
            return default

    lines = [f"TreeAI Coach {version} — diagnostic du {_dt.datetime.now():%d/%m/%Y %H:%M:%S}"]
    lines.append(f"Système : {platform.system()} {platform.release()} ({platform.machine()}), "
                 f"Python {platform.python_version()}, exe={'oui' if getattr(sys, 'frozen', False) else 'non'}")
    st = status
    if st is not None:
        state = g(st, "state")
        lines.append(f"Moteur : {getattr(state, 'name', state)} — {g(st, 'message', '')}")
        lines.append(f"FPS : {g(st, 'fps')} · tick {g(st, 'tick_ms')} ms · erreurs {g(st, 'errors')} · "
                     f"minimap {g(st, 'minimap_rect')} ({g(st, 'locate_method')})")
    else:
        lines.append("Moteur : indisponible" if engine is None else "Moteur : aucun statut")
    lines.append(f"Mode démo : {'oui' if demo else 'non'} · CPU appli : "
                 f"{'—' if cpu is None else f'{cpu:.0f} %'}")
    lines.append(f"Détecteur : {g(detector, 'name', None) or getattr(cfg, 'detector_backend', '?')} "
                 f"(réglage {getattr(cfg, 'detector_backend', '?')}, seuil {getattr(cfg, 'detection_threshold', '?')})")
    lines.append(f"Voix : {g(voice, 'backend', None) or '—'} (moteur {getattr(cfg, 'voice_engine', '—')}, "
                 f"volume {getattr(cfg, 'voice_volume', '?')})")
    ov_mode = g(overlay, "effective_mode", None) if overlay is not None else None
    lines.append(f"Overlay : réglage {getattr(cfg, 'overlay_mode', '?')}, effectif {ov_mode or 'indisponible'}, "
                 f"activé={getattr(cfg, 'overlay_enabled', '?')}")
    lines.append(f"Mode sûr : {'oui' if getattr(cfg, 'safe_mode', False) else 'non'} · minimap "
                 f"{getattr(cfg, 'minimap_mode', '?')}/{getattr(cfg, 'minimap_side', '?')}")
    lines.append(f"Journal : {log_file or '—'}")
    if data_dir is not None:
        lines.append(f"Données : {data_dir}")
    errs = last_log_errors(log_file)
    lines.append("Dernières erreurs :" if errs else "Dernières erreurs : aucune")
    lines.extend(f"  {e}" for e in errs)
    return "\n".join(lines)


# ======================================================================================
# Texts
# ======================================================================================
CHANGELOG_VERSION = "1.5"
CHANGELOG: tuple[tuple[str, str], ...] = (
    ("Nouvelle interface", "Bandeau de partie avec chrono et jauge de menace, cartes des champions avec rôle "
                           "et minuteur MIA, alliés et face-à-face de ta voie."),
    ("Préréglages", "Discret, Équilibré ou Complet en un clic (alertes et overlay)."),
    ("Calme quand tu veux", "Silence pendant les premières secondes de la partie, heures calmes et "
                            "annonces d'information désactivables (les dangers sont toujours annoncés)."),
    ("Overlay sur mesure", "Opacité, taille et couches (rôles, flèches, zones, fantômes), palette daltonien."),
    ("Mode sûr en un clic", "Depuis le tableau de bord, la barre latérale ou les réglages."),
    ("Outils", "Copier le diagnostic, export / import des réglages, historique filtrable des alertes, "
               "raccourcis Ctrl+1…6, indicateur CPU."),
)
SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("Ctrl + 1 … 6", "Aller à une page (Tableau de bord … Aide)"),
    ("Ctrl + M", "Couper / rétablir la voix"),
    ("Ctrl + Maj + S", "Activer / désactiver le mode sûr"),
    ("Ctrl + D", "Copier le diagnostic"),
    ("F1", "Afficher l'aide"),
    ("Échap", "Fermer une fenêtre de dialogue"),
)
ABOUT_TEXT = (
    "TreeAI Coach est un projet indépendant. Il n'est ni approuvé ni sponsorisé par Riot Games et ne reflète "
    "pas les opinions de Riot Games ni de quiconque officiellement impliqué dans la production ou la gestion "
    "de League of Legends. League of Legends et Riot Games sont des marques commerciales ou des marques "
    "déposées de Riot Games, Inc.\n\n"
    "Fonctionnement : lecture de l'écran (la minimap déjà visible) et de l'API officielle « Live Client Data » "
    "uniquement. Aucune lecture de la mémoire du jeu, aucune injection, aucune touche ni clic simulé.\n\n"
    "Icônes des champions : Data Dragon / CommunityDragon. Utilisation à tes propres risques."
)


def onboarding_steps() -> list[tuple[str, str]]:
    return [
        ("Passe le jeu en « Sans bordure »",
         "Options du jeu → Vidéo → Mode d'affichage : Sans bordure. En plein écran exclusif, la capture est "
         "noire et rien ne peut s'afficher sur ta minimap."),
        ("Teste la voix",
         "Clique sur « Écouter » : tu dois entendre une alerte d'exemple. Règle le volume ici ou dans "
         "Alertes & voix."),
        ("Choisis ton style",
         "Discret : seulement les dangers. Équilibré : recommandé. Complet : tout est annoncé et affiché. "
         "Tu pourras tout ajuster ensuite."),
    ]


class Tooltip:
    """Delayed hover tooltip for any Tk / CustomTkinter widget. Never raises."""

    DELAY_MS = 450

    def __init__(self, widget: Any, text: str | Callable[[], str], bg: str = "#0F1D36", fg: str = "#F0E6D2",
                 border: str = GOLD_DARK, font: Any = None, wrap: int = 320) -> None:
        self.widget = widget
        self.text = text
        self.bg, self.fg, self.border, self.font, self.wrap = bg, fg, border, font, wrap
        self._job: Any = None
        self._tip: Any = None
        for seq, fn in (("<Enter>", self._schedule), ("<Leave>", self._hide), ("<ButtonPress>", self._hide)):
            try:
                widget.bind(seq, fn, add="+")
            except Exception:
                pass

    def _schedule(self, _e: Any = None) -> None:
        self._cancel()
        try:
            self._job = self.widget.after(self.DELAY_MS, self._show)
        except Exception:
            self._job = None

    def _cancel(self) -> None:
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def _show(self) -> None:
        self._job = None
        try:
            import tkinter as tk  # noqa: PLC0415

            text = self.text() if callable(self.text) else self.text
            if not text or self._tip is not None:
                return
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            tip = tk.Toplevel(self.widget)
            tip.wm_overrideredirect(True)
            try:
                tip.attributes("-topmost", True)
            except Exception:
                pass
            frame = tk.Frame(tip, bg=self.border, padx=1, pady=1)
            frame.pack()
            tk.Label(frame, text=text, bg=self.bg, fg=self.fg, justify="left", wraplength=self.wrap,
                     padx=10, pady=6, font=self.font).pack()
            tip.wm_geometry(f"+{x}+{y}")
            self._tip = tip
        except Exception:
            log.debug("Tooltip failed", exc_info=True)

    def _hide(self, _e: Any = None) -> None:
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


def caps(text: str) -> str:
    """Spaced small-caps style label ("MENACE" -> "M E N A C E" with thin spaces)."""
    return " ".join(str(text).upper())


__all__ = [
    "extra_icon", "role_glyph", "decorate_portrait", "hero_background", "glow_dot", "PRESETS", "preset_of",
    "preset_changes", "export_settings", "import_settings", "in_quiet_hours", "speech_allowed", "VoiceGate",
    "lane_opponent", "CpuMeter", "diagnostic_text", "CHANGELOG", "SHORTCUTS", "ABOUT_TEXT", "norm_role",
]
