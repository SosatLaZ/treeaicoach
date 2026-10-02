"""Building blocks of the interface (ui.py): drawings, status texts, diagnostics, help texts.

Everything here is pure / Tk-free except :class:`Tooltip` (Tk imported lazily), so it is unit-tested
without a display. Nothing in this module may raise into the UI: every public helper is defensive.

* PIL drawings: :func:`extra_icon` (line icons), :func:`role_glyph` / :func:`decorate_portrait`
  (role badge + MIA arc on a round champion portrait), :func:`hero_background` (status strip).
* Dashboard texts: :func:`status_line` (the ONE status line + its fix), :func:`subsystem_rows`
  ("Système" rows), :func:`objectives_text`, :func:`lane_opponent`, :func:`window_mode_status`.
* :func:`diagnostic_text` (clipboard report), :func:`game_keys`, :data:`CHANGELOG`, :data:`SHORTCUTS`,
  :data:`ABOUT_TEXT`, :func:`onboarding_steps`.
"""

from __future__ import annotations

import datetime as _dt
import logging
import math
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw

log = logging.getLogger(__name__)

# ======================================================================================
# Colours shared with ui.py (kept in sync with its palette)
# ======================================================================================
# TreeAI tokens (docs/DESIGN.md); legacy names kept for callers.
GOLD = "#9BD84A"            # the accent (TreeAI sap green)
GOLD_DARK = "#3E5A1E"
ALLY = "#4A90D9"            # allied team ring (team colour)
ENEMY = "#E5484D"           # enemy team ring
BADGE_BG = "#0C0E0D"

ROLE_ORDER: tuple[str, ...] = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
ROLE_FR: dict[str, str] = {"TOP": "Haut", "JUNGLE": "Jungle", "MIDDLE": "Milieu", "BOTTOM": "Tireur",
                           "UTILITY": "Support"}
ROLE_SHORT: dict[str, str] = {"TOP": "TOP", "JUNGLE": "JGL", "MIDDLE": "MID", "BOTTOM": "ADC",
                              "UTILITY": "SUP"}
#: Role as players say it, for the status line ("En jeu : Garen top").
ROLE_GAMER: dict[str, str] = {"TOP": "top", "JUNGLE": "jungle", "MIDDLE": "mid", "BOTTOM": "ADC",
                              "UTILITY": "support"}


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


def extra_icon(kind: str, size: int = 18, color: str = "#8B948F") -> Image.Image:
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
                      arc_color: str = "#E8A23A", badge_bg: str = BADGE_BG, badge_ring: str = "#2F3532",
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
            g = role_glyph(role, int(b * .62), "#D5DBD7")
            layer.alpha_composite(g, (x0 + (b - g.size[0]) // 2, y0 + (b - g.size[1]) // 2))
        if star:
            st = extra_icon("star", int(S * .34), "#E8A23A")
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


def hero_background(w: int, h: int, glow: str, bg: str = "#0C0E0D", panel: str = "#121513",
                    border: str = "#222725", gold: str = GOLD, radius: int = 4) -> Image.Image:
    """Status strip background: flat graphite panel, 1 px border, 3 px state bar on the left.

    No gradient, no glow (docs/DESIGN.md): the state colour ``glow`` only tints the left bar.
    ``gold`` is accepted for compatibility and unused.
    """
    w, h = max(8, int(w)), max(8, int(h))
    out = Image.new("RGB", (w, h), hex_rgb(bg))
    d = ImageDraw.Draw(out)
    r = max(0, min(int(radius), 6))
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=hex_rgb(panel), outline=hex_rgb(border), width=1)
    d.rectangle((0, r, 2, h - 1 - r), fill=hex_rgb(glow))
    return out


# ======================================================================================
# Dashboard helpers
# ======================================================================================
# ======================================================================================
# Launcher: status of each subsystem with a one-click fix
# ======================================================================================
#: level: 0 = ok, 1 = to check, 2 = broken, -1 = idle / not applicable
SubsystemRow = tuple[str, str, int, str, str, str]      # (key, label, level, text, fix label, fix action)


#: short provider names (dashboard row "IA conseil")
AI_SHORT: dict[str, str] = {"gemini": "Gemini", "groq": "Groq", "openrouter": "OpenRouter", "ollama": "Ollama",
                            "anthropic": "Claude"}
#: ai_advisor.AIError code -> short French status (dashboard row, "Tester la clé")
AI_ERR_SHORT: dict[str, str] = {"key": "clé refusée", "nokey": "clé manquante", "quota": "quota atteint",
                                "offline": "service injoignable", "model": "modèle inconnu",
                                "server": "erreur du service", "bad": "réponse illisible",
                                "rate": "limite / minute", "empty": "réponse vide"}


def ai_row(provider: str = "off", key_set: bool = False, budget: str = "",
           test: tuple[bool, str] | None = None) -> SubsystemRow:
    """Dashboard row "IA conseil": provider, key set or not, budget left ("IA 3/5"), last key test."""
    prov = str(provider or "off").lower()
    if prov in ("", "off"):
        return ("ai", "IA conseil", -1, "désactivée", "Activer", "settings_ai")
    name = AI_SHORT.get(prov, prov.title())
    needs_key = prov != "ollama"
    if needs_key and not key_set:
        return ("ai", "IA conseil", 1, f"{name} · clé manquante", "Ajouter", "settings_ai")
    if test is not None:
        ok, short = test
        if not ok:
            return ("ai", "IA conseil", 2, f"{name} · {short}", "Tester la clé", "test_ai")
        return ("ai", "IA conseil", 0, f"{name} · {budget or 'clé OK'}", "Tester la clé", "test_ai")
    extra = budget or ("local" if not needs_key else "clé enregistrée")
    return ("ai", "IA conseil", 0, f"{name} · {extra}", "Tester la clé" if needs_key else "Tester", "test_ai")


def test_ai_key(cfg: Any, caller: Callable[..., str] | None = None) -> tuple[bool, str, str]:
    """Blocking tiny request to the chosen AI provider: ``(ok, short status, French message)``.

    Run it on a worker thread. An empty answer still proves the key works. Never raises."""
    try:
        from treeaicoach import ai_advisor  # noqa: PLC0415

        prov = str(getattr(cfg, "ai_provider", "off") or "off").lower()
        spec = ai_advisor.provider_spec(prov)
        if spec is None:
            return False, "désactivée", "Choisis d'abord un fournisseur d'IA dans Réglages > IA."
        name = AI_SHORT.get(prov, spec.label)
        key = str(getattr(cfg, "ai_api_key", "") or "").strip()
        if spec.needs_key and not key:
            return False, AI_ERR_SHORT["nokey"], f"{name} : colle d'abord ta clé dans Réglages > IA."
        call = caller or ai_advisor.call_llm
        try:
            call(prov, key, str(getattr(cfg, "ai_model", "") or ""), "Réponds en un mot.", "Réponds : OK",
                 timeout=8.0, max_tokens=16)
        except ai_advisor.AIError as exc:
            if exc.code == "rate":            # 429 per minute: the key was accepted (it is metered)
                return True, "clé OK", f"{name} : la clé fonctionne (limite par minute atteinte pour l'instant)."
            if exc.code != "empty":           # an empty answer = the key was accepted
                return (False, AI_ERR_SHORT.get(exc.code, AI_ERR_SHORT["server"]),
                        ai_advisor.error_text(exc.code, prov).replace("Conseil IA", name))
        return True, "clé OK", f"{name} : la clé fonctionne."
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("AI key test failed", exc_info=True)
        return False, "erreur", f"Test impossible ({type(exc).__name__})."


def window_mode_status(window_mode: Any) -> tuple[int, str]:
    """Game display mode (game_settings.GameSettings.window_mode) -> (level, French text).

    level 0 = fine (borderless / windowed), 2 = exclusive fullscreen, -1 = unknown."""
    if window_mode == 2:
        return 0, "Sans bordure : parfait"
    if window_mode == 1:
        return 0, "Fenêtré : ça marche"
    if window_mode == 0:
        return 2, "Plein écran : passe en Sans bordure"
    return -1, "réglage du jeu introuvable : vérifie à la main"


def subsystem_rows(*, state: str = "", message: str = "", running: bool = False, demo: bool = False,
                   minimap_found: bool = False, minimap_method: str | None = None, detector: str = "",
                   voice_backend: str = "", muted: bool = False, lcu_text: str = "",
                   lcu_enabled: bool = True, engine_ok: bool = True, ai_provider: str = "off",
                   ai_key_set: bool = False, ai_budget: str = "",
                   ai_test: tuple[bool, str] | None = None) -> list[SubsystemRow]:
    """Plain-French status of "Jeu / Minimap / Client LoL / Détection / IA conseil / Voix" for the dashboard.

    Each row carries a short fix hint and an action key the UI maps to a button:
    "start", "calibrate", "help_borderless", "settings_ia", "settings_ai", "test_ai", "voice", "unmute",
    "lcu_help", "". Pure: no I/O, never raises.
    """
    rows: list[SubsystemRow] = []
    st = str(state or "").upper()
    # game
    if not engine_ok:
        rows.append(("game", "Jeu", 2, "moteur indisponible", "Réessayer", "start"))
    elif not running:
        rows.append(("game", "Jeu", -1, "analyse arrêtée", "Démarrer", "start"))
    elif demo:
        rows.append(("game", "Jeu", 0, "partie simulée", "", ""))
    elif st == "WAITING_GAME":
        rows.append(("game", "Jeu", -1, "pas de partie en cours", "", ""))
    elif st == "UNSUPPORTED_MODE":
        rows.append(("game", "Jeu", 1, "mode non géré (Faille seulement)", "", ""))
    elif st == "CAPTURE_BLACK":
        rows.append(("game", "Jeu", 2, "capture noire", "Passer en Sans bordure", "help_borderless"))
    elif st == "ERROR":
        rows.append(("game", "Jeu", 2, (message or "erreur")[:48], "Diagnostic", "diagnostic"))
    else:
        rows.append(("game", "Jeu", 0, "partie détectée", "", ""))
    # minimap
    if running and st in ("RUNNING",) and (minimap_found or demo):
        how = {"manual": "calibrée", "auto": "trouvée", "fallback": "position par défaut"}.get(
            str(minimap_method or ""), "trouvée")
        rows.append(("minimap", "Minimap", 1 if minimap_method == "fallback" else 0, how,
                     "Calibrer" if minimap_method == "fallback" else "", "calibrate" if minimap_method == "fallback"
                     else ""))
    elif running and st == "LOCATING":
        rows.append(("minimap", "Minimap", 1, "recherche en cours", "Calibrer", "calibrate"))
    elif running and st == "CAPTURE_BLACK":
        rows.append(("minimap", "Minimap", 2, "invisible (plein écran)", "Passer en Sans bordure", "help_borderless"))
    else:
        rows.append(("minimap", "Minimap", -1, "en attente de partie", "", ""))
    # League client (post-game truth)
    if not lcu_enabled:
        rows.append(("lcu", "Client LoL", -1, "désactivé", "", ""))
    elif lcu_text.endswith("connecté"):
        rows.append(("lcu", "Client LoL", 0, "connecté", "", ""))
    elif lcu_text:
        rows.append(("lcu", "Client LoL", 1, "non trouvé", "Aide", "lcu_help"))
    else:
        rows.append(("lcu", "Client LoL", -1, "vérification…", "", ""))
    # champion detection model (key "ia" kept for compatibility)
    d = str(detector or "").lower()
    if "onnx" in d or "roster" in d:
        rows.append(("ia", "Détection", 0, "réseau de neurones", "", ""))
    elif "classic" in d:
        rows.append(("ia", "Détection", 1, "mode secours", "Réglages", "settings_ia"))
    elif d in ("", "-", "none", "aucun"):
        rows.append(("ia", "Détection", 2 if engine_ok else -1, "non chargée", "Réglages", "settings_ia"))
    else:
        rows.append(("ia", "Détection", 0, detector[:24], "", ""))
    # optional LLM advice (ai_advisor.py)
    rows.append(ai_row(ai_provider, ai_key_set, ai_budget, ai_test))
    # voice
    vb = str(voice_backend or "").lower()
    if muted:
        rows.append(("voice", "Voix", 1, "coupée", "Rétablir", "unmute"))
    elif vb in ("sapi", "onecore", "neural"):
        rows.append(("voice", "Voix", 0, {"sapi": "Windows (SAPI)", "onecore": "Windows",
                                         "neural": "neurale"}[vb], "Tester", "voice"))
    elif vb in ("print", ""):
        rows.append(("voice", "Voix", 2 if vb == "print" else -1,
                     "aucune voix Windows" if vb == "print" else "chargement…", "Réglages", "voice_settings"))
    else:
        rows.append(("voice", "Voix", 0, vb, "Tester", "voice"))
    return rows


#: Default engine messages that only repeat the state title (replaced by something useful).
_WAITING_PREFIX = "En attente d'une partie"
_RUNNING_GENERIC = ("Analyse de la minimap en cours", "Analyse en cours")


def status_line(state: str, message: str = "", *, champion: str = "", role: Any = None) -> tuple[str, str, str, str]:
    """The dashboard's ONE status line: ``(title, message, fix label, fix action)``.

    "En attente d'une partie" / "En jeu : Garen top" / a problem with its one-click fix (the action
    keys are the ones of :func:`subsystem_rows`, run by the UI). Pure, never raises."""
    try:
        st = str(state or "").upper()
        msg = str(message or "").strip()
        if st == "RUNNING":
            r = ROLE_GAMER.get(norm_role(role) or "", "")
            title = f"En jeu : {champion} {r}".rstrip() if champion else "En jeu"
            if msg.startswith("Minimap non trouvée"):
                return title, msg, "Calibrer", "calibrate"
            if not msg or msg.rstrip(".…").startswith(_RUNNING_GENERIC):
                msg = "Alertes et overlay actifs."
            return title, msg, "", ""
        if st == "WAITING_GAME":
            if not msg or msg.startswith(_WAITING_PREFIX):
                msg = "Lance une partie : l'analyse démarre toute seule."
            return "En attente d'une partie", msg, "", ""
        if st == "LOCATING":
            if not msg or msg.startswith("Recherche de la minimap"):
                return "Recherche de la minimap", "Le coach cherche la minimap sur ton écran.", "Calibrer", "calibrate"
            return "En pause", msg, "", ""
        if st == "CAPTURE_BLACK":
            return "Capture noire", msg or "Passe le jeu en Sans bordure (Options > Vidéo).", "Aide", "help_borderless"
        if st == "UNSUPPORTED_MODE":
            return "Mode de jeu non pris en charge", msg or "Seulement la Faille de l'invocateur.", "", ""
        if st == "ERROR":
            return "Erreur", msg or "Erreur d'analyse.", "Diagnostic", "diagnostic"
        if st == "NO_ENGINE":
            return "Moteur indisponible", msg or "Le moteur d'analyse n'a pas démarré.", "Diagnostic", "diagnostic"
        if st == "STARTING":
            return "Démarrage…", msg or "Chargement du détecteur et de la voix…", "", ""
        return ("Analyse arrêtée", msg if msg and not msg.startswith("Analyse arrêtée") else
                "Clique sur « Démarrer l'analyse » pour suivre ta prochaine partie.", "", "")
    except Exception:
        return "TreeAI Coach", str(message or ""), "", ""


def game_keys(cfg: Any) -> list[tuple[str, str]]:
    """In-game keys with their current binding ("F9", "Où est le jungler ?"), for the Aide page."""
    rows = (("hotkey_details", "Maintenir : overlay détaillé"), ("hotkey_ward", "Où poser une balise ?"),
            ("hotkey_ai", "Demander à l'IA"), ("hotkey_diag", "Diagnostic complet (60 s)"),
            ("hotkey_jungler", "Où est le jungler ?"), ("hotkey_mute", "Couper / rétablir la voix"),
            ("hotkey_overlay", "Afficher / masquer l'overlay"))
    out = []
    for field_name, what in rows:
        key = str(getattr(cfg, field_name, "") or "").strip()
        if key:
            out.append((key.replace("+", " + "), what))
    return out


OBJECTIVE_SHORT_FR: dict[str, str] = {"dragon": "Drake", "dragon ancestral": "Ancien", "baron": "Baron",
                                       "héraut": "Héraut", "larves": "Larves"}


def objectives_text(ov: Any, max_items: int = 3) -> str:
    """Compact objective timers for the status strip: "Drake 1:24 · Baron 4:10" (soonest first).

    Spawned objectives read "Drake là". Pure, never raises; "" when nothing is known.
    """
    try:
        gt = getattr(ov, "game_time", None)
        items: list[tuple[float, str]] = []
        for ob in list(getattr(ov, "objectives", None) or []):
            name = str(getattr(ob, "name", "") or "")
            if not name:
                continue
            short = OBJECTIVE_SHORT_FR.get(name.lower(), name)
            if getattr(ob, "alive", False):
                items.append((-1.0, f"{short} là"))
                continue
            nxt = getattr(ob, "next_spawn", None)
            if not isinstance(nxt, (int, float)) or not isinstance(gt, (int, float)):
                continue
            rem = float(nxt) - float(gt)
            if not math.isfinite(rem) or rem < -1:
                continue
            rem = max(0.0, rem)
            items.append((rem, f"{short} {int(rem) // 60}:{int(rem) % 60:02d}"))
        items.sort(key=lambda it: it[0])
        return " · ".join(t for _r, t in items[:max(0, int(max_items))])
    except Exception:
        return ""


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
    def g(obj: Any, name: str, default: Any = "-") -> Any:
        try:
            v = getattr(obj, name, default)
            return v() if callable(v) else v
        except Exception:
            return default

    lines = [f"TreeAI Coach {version} : diagnostic du {_dt.datetime.now():%d/%m/%Y %H:%M:%S}"]
    lines.append(f"Système : {platform.system()} {platform.release()} ({platform.machine()}), "
                 f"Python {platform.python_version()}, exe={'oui' if getattr(sys, 'frozen', False) else 'non'}")
    st = status
    if st is not None:
        state = g(st, "state")
        lines.append(f"Moteur : {getattr(state, 'name', state)} : {g(st, 'message', '')}")
        lines.append(f"FPS : {g(st, 'fps')} · tick {g(st, 'tick_ms')} ms · erreurs {g(st, 'errors')} · "
                     f"minimap {g(st, 'minimap_rect')} ({g(st, 'locate_method')})")
    else:
        lines.append("Moteur : indisponible" if engine is None else "Moteur : aucun statut")
    lines.append(f"Mode démo : {'oui' if demo else 'non'} · CPU appli : "
                 f"{'-' if cpu is None else f'{cpu:.0f} %'}")
    lines.append(f"Détecteur : {g(detector, 'name', None) or getattr(cfg, 'detector_backend', '?')} "
                 f"(réglage {getattr(cfg, 'detector_backend', '?')}, seuil {getattr(cfg, 'detection_threshold', '?')})")
    lines.append(f"Voix : {g(voice, 'backend', None) or '-'} (moteur {getattr(cfg, 'voice_engine', '-')}, "
                 f"volume {getattr(cfg, 'voice_volume', '?')})")
    ov_mode = g(overlay, "effective_mode", None) if overlay is not None else None
    lines.append(f"Overlay : réglage {getattr(cfg, 'overlay_mode', '?')}, effectif {ov_mode or 'indisponible'}, "
                 f"activé={getattr(cfg, 'overlay_enabled', '?')}")
    lines.append(f"Mode sûr : {'oui' if getattr(cfg, 'safe_mode', False) else 'non'} · minimap "
                 f"{getattr(cfg, 'minimap_mode', '?')}/{getattr(cfg, 'minimap_side', '?')}")
    lines.append(f"Journal : {log_file or '-'}")
    if data_dir is not None:
        lines.append(f"Données : {data_dir}")
    errs = last_log_errors(log_file)
    lines.append("Dernières erreurs :" if errs else "Dernières erreurs : aucune")
    lines.extend(f"  {e}" for e in errs)
    return "\n".join(lines)


# ======================================================================================
# Texts
# ======================================================================================
CHANGELOG_VERSION = "2.4"
CHANGELOG: tuple[tuple[str, str], ...] = (
    ("Compréhension de la partie", "Compos, qui est plus fort et jusqu'à quand, conditions de victoire et "
                                   "ton rôle : « Tu es plus fort que Vladimir jusqu'à ~21:00 : punis-le »."),
    ("Ce que tu oublies", "Point de compétence, potion, balises, or non dépensé, et la leçon quand tu "
                          "meurs avec un sort prêt."),
    ("IA corrigée", "22 bugs corrigés : plus de plan en plein combat, limite Groq gérée, plans "
                    "cohérents avec le panneau, analyse de fin de partie en 3 axes."),
    ("Rapport", "Nouvelle section « Compréhension de la partie »."),
)
SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("Ctrl + 1 … 4", "Aller à une page (En jeu, Analyses, Réglages, Aide)"),
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
    """The 3 steps of the guided first run ("Mode guidé"): level, borderless check, overlay test."""
    return [
        ("Ton niveau",
         "Plus tu es débutant, plus le coach explique. Tu pourras le changer à tout moment dans la barre "
         "de gauche."),
        ("Jeu en « Sans bordure »",
         "Options du jeu > Vidéo > Mode d'affichage : Sans bordure. En plein écran exclusif, la capture est "
         "noire et rien ne s'affiche sur ta minimap."),
        ("Teste l'overlay et la voix",
         "Un exemple de gank s'affiche 10 s sur ton écran et le coach parle. Si tu ne vois ou n'entends "
         "rien, ouvre la page Aide."),
    ]


class Tooltip:
    """Delayed hover tooltip for any Tk / CustomTkinter widget. Never raises."""

    DELAY_MS = 450

    def __init__(self, widget: Any, text: str | Callable[[], str], bg: str = "#191D1B", fg: str = "#E4E8E5",
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


__all__ = [
    "extra_icon", "role_glyph", "decorate_portrait", "hero_background", "lane_opponent", "objectives_text",
    "subsystem_rows", "status_line", "game_keys", "ai_row", "test_ai_key", "window_mode_status", "CpuMeter",
    "diagnostic_text", "CHANGELOG", "SHORTCUTS", "ABOUT_TEXT", "norm_role", "onboarding_steps",
]
