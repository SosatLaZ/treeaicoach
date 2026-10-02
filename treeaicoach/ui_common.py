"""Shared building blocks of the CustomTkinter UI: design tokens (docs/DESIGN.md), fonts, small
widgets (toggle, dropdown, segmented control, hero banner), icons, threading helpers and the
pure formatting helpers used by the pages.

Split out of ``ui.py`` (zero behaviour change); :mod:`treeaicoach.ui` re-exports every public
name, so ``ui.BG`` / ``ui.fmt_clock`` ... keep working.
"""

from __future__ import annotations

import datetime as _dt
import functools
import logging
import math
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Sequence

import numpy as np
from PIL import Image, ImageDraw

from treeaicoach import paths, ui_kit
from treeaicoach.fmtutil import clock

if TYPE_CHECKING:  # pragma: no cover - typing only
    from treeaicoach.ui import CoachApp

log = logging.getLogger("treeaicoach.ui")   # same logger as before the split


# ======================================================================================
# Palette & layout constants
# ======================================================================================
# Design tokens: docs/DESIGN.md ("régie esport": green-black graphite, ONE accent = TreeAI sap
# green, semantic red / amber only for meaning, 1 px separators, 4 px radius). The historical
# names (GOLD, TEAL...) are kept as aliases so that the rest of the module and the tests do not move.
BG = "#0C0E0D"              # window / page background
SURFACE = "#121513"         # sidebar, status strip, dialogs
RAISED = "#191D1B"          # hover, inputs, secondary buttons
SUNKEN = "#090B0A"          # wells (journal, gauge tracks)
LINE = "#222725"            # 1 px separators
LINE_STRONG = "#2F3532"     # control borders
ROW_LINE = "#252B28"        # separators between the rows of a card (on SURFACE)
ACCENT = "#9BD84A"          # the only accent (TreeAI sap green)
ACCENT_HOVER = "#B0E46C"
ACCENT_DIM = "#3E5A1E"      # selected background
ON_ACCENT = "#0C0E0D"
PANEL = SURFACE             # legacy names -> tokens
PANEL_HI = RAISED
PANEL_LO = SUNKEN
BORDER = LINE
BORDER_GOLD = LINE_STRONG
GOLD = ACCENT
GOLD_HOVER = ACCENT_HOVER
GOLD_DARK = ACCENT_DIM
TEXT = "#E4E8E5"
MUTED = "#A4ADA8"          # secondary text: 8.4:1 on BG (WCAG AA)
DIM = "#7E8782"            # captions / absent values: 5.2:1 on BG, 4.6:1 on RAISED
TEAL = ACCENT               # "active" = the accent too (one accent only)
DANGER = "#E5484D"
DANGER_DARK = "#3A1618"
DANGER_HOVER = "#4E1D20"
ON_DANGER = "#FFD7D8"
WARNING = "#E8A23A"
WARNING_BG = "#2A2010"
SAFE = ACCENT               # semantic "ok" = the TreeAI green
ON_GOLD = ON_ACCENT
HOVER = "#212624"           # hover of raised elements
SWITCH_OFF = "#2F3532"      # switch / slider track (off)
ALLY_RING = ui_kit.ALLY     # allied team ring (team colour, not an accent)
ENEMY_RING = ui_kit.ENEMY   # enemy team ring
TRACK = "#1C211F"           # empty gauge segment / slider track
RADIUS = 4                  # controls
EM_DASH = chr(0x2014)       # never shown (docs/DESIGN.md); used to parse / clean texts of other modules
RADIUS_DIALOG = 6
BTN_H, BTN_H_SMALL = 34, 30       # the two button heights
CTL_H = 34                        # inputs, menus, segmented controls
CONTENT_MAX = 860                 # max width of the centred content column (settings-like pages)
WIDE_MAX = 1300                   # dashboard / analyses
PAGE_PAD = 32                     # minimum side gutter of a page
CARD_PAD = 20                     # inner padding of a section card
SECTION_GAP = 28                  # space between two sections (title + card)
ROW_PAD_Y = 12                    # vertical padding of a setting row (rows are separated by 1 px lines)
CTL_GAP = 8                       # gap between two controls side by side
ROW_CTL_GAP = 24                  # gap between the text of a setting row and its control
TAB_GAP = 24                      # gap between two tabs of a page header
LINK_H = 22                       # inline text buttons (fix links of the "Système" rows, moments)
ICON_BTN = 30                     # square icon-only buttons (toolbars)

THREAT_COLORS = {0: SAFE, 1: WARNING, 2: DANGER}
#: "jouer plus fort ou non" gauge step -> colour; tip tone -> colour (dashboard coach strip)
GAUGE_UI_COLORS = {2: SAFE, 1: "#C3E79A", 0: MUTED, -1: WARNING, -2: DANGER}
TIP_UI_COLORS = {"danger": "#F2888B", "warning": "#F0BE6E", "go": "#C3E79A", "info": TEXT}
THREAT_LABELS = {0: "SÛR", 1: "ATTENTION", 2: "DANGER"}
LEVEL_COLORS = {0: TEXT, 1: WARNING, 2: DANGER}

SIDEBAR_W = 236
ROW_MIN_H = 40
SLIDER_KNOB_R = 9
MIN_W, MIN_H = 980, 640
DEFAULT_W, DEFAULT_H = 1100, 720
RADAR_PX = 260
STATUS_MS = 250                  # dashboard on screen during a game
STATUS_IDLE_MS = 1000            # any other page / no game
STATUS_ICONIC_MS = 2000          # window minimised
PREVIEW_MS = 200
IDLE_LOOP_MS = 1000              # radar preview / pulse loops while they have nothing to draw
PULSE_MS = 80
DISPATCH_MS = 50
DISPATCH_ICONIC_MS = 200
SAVE_DEBOUNCE_MS = 500
TOAST_MS = 4500
UPDATE_CHECK_DELAY_MS = 8000     # silent update check after launch (frozen exe only)
JOURNAL_MAX = 12
PREBUILD_GAP_MS = 150             # idle gap between two prebuild slots (each slot: one page or one section, < 100 ms)
PREBUILD_ORDER = ("settings", "analysis", "help", "dashboard")
GAMES_PAGE = 15                  # rows of the Analyses table drawn at once ("Afficher plus")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "TreeAICoach"

#: Sidebar navigation: 4 pages. Everything the player can set lives in ONE page (Réglages), in tabs
#: named after what they change (docs/DESIGN.md, "Navigation").
PAGES: tuple[tuple[str, str, str], ...] = (
    ("dashboard", "En jeu", "dashboard"),
    ("analysis", "Analyses", "analysis"),
    ("settings", "Réglages", "settings"),
    ("help", "Aide", "help"),
)
#: Tabs of the Réglages page, in order (label shown in the tab bar).
SETTINGS_TABS: tuple[str, ...] = ("Général", "Affichage", "Voix", "Détection", "IA", "Mises à jour", "Avancé")
#: Pages of older versions -> (page, tab) of the Réglages page that replaced them.
PAGE_ALIASES: dict[str, tuple[str, str]] = {"alerts": ("settings", "Voix"), "overlay": ("settings", "Affichage")}

#: Engine state name -> (short French title, colour).
STATE_INFO: dict[str, tuple[str, str]] = {
    "STOPPED": ("Analyse arrêtée", DIM),
    "WAITING_GAME": ("En attente d'une partie", GOLD),
    "LOCATING": ("Recherche de la minimap", TEAL),
    "RUNNING": ("En jeu", SAFE),
    "UNSUPPORTED_MODE": ("Mode de jeu non pris en charge", WARNING),
    "CAPTURE_BLACK": ("Capture noire", WARNING),
    "ERROR": ("Erreur", DANGER),
    "NO_ENGINE": ("Moteur indisponible", DANGER),
    "STARTING": ("Démarrage…", GOLD),
}
PILL_TEXT: dict[str, str] = {
    "STOPPED": "Arrêté", "WAITING_GAME": "En attente", "LOCATING": "Localisation",
    "RUNNING": "En jeu", "UNSUPPORTED_MODE": "Mode non géré", "CAPTURE_BLACK": "Capture noire",
    "ERROR": "Erreur", "NO_ENGINE": "Indisponible", "STARTING": "Démarrage",
}

RADAR_POSITIONS: tuple[tuple[str, str], ...] = (
    ("above_minimap", "Au-dessus de la minimap"),
    ("left_of_minimap", "À gauche de la minimap"),
    ("top_left", "En haut à gauche"),
    ("custom", "Personnalisée"),
)
HUD_POSITIONS: tuple[tuple[str, str], ...] = (
    ("left_of_minimap", "À gauche de la minimap"),
    ("above_minimap", "Au-dessus de la minimap"),
    ("top_left", "En haut à gauche"),
    ("top_right", "En haut à droite"),
    ("left_middle", "Au milieu à gauche"),
    ("custom", "Personnalisée"),
)
FOG_MODES: tuple[tuple[str, str], ...] = (("jungler", "Jungler"), ("all", "Tous"), ("off", "Aucun"))
OVERLAY_MODES: tuple[tuple[str, str], ...] = (("minimap", "Sur la minimap"), ("radar", "Radar à côté"),
                                              ("off", "Aucun"))
#: cfg.voice_level (voice_policy.py): how much the coach says out loud (the rest is written).
VOICE_LEVELS: tuple[tuple[str, str], ...] = (("minimal", "Minimal"), ("normal", "Normal"), ("bavard", "Bavard"))
#: One control for (cfg.beep_on_danger, cfg.danger_voice): "voix" = no beep, the sentence only.
DANGER_MODES: tuple[tuple[str, str], ...] = (("bip_voix", "Bip + voix"), ("bip", "Bip seul"), ("voix", "Voix seule"))
PERF_MODES: tuple[tuple[str, str], ...] = (("auto", "Auto"), ("normal", "Normal"), ("low_end", "PC modeste"))
CAPTURE_BACKENDS: tuple[tuple[str, str], ...] = (("auto", "Auto"), ("dxgi", "DXGI"), ("mss", "Compatible"))
UI_SCALINGS: tuple[tuple[str, str], ...] = (("auto", "Auto"), ("90", "90 %"), ("100", "100 %"), ("110", "110 %"),
                                            ("125", "125 %"), ("150", "150 %"))
PLAYS_POSITIONS: tuple[tuple[str, str], ...] = (("top_center", "En haut"), ("minimap", "Près de la minimap"))
DETECTORS: tuple[tuple[str, str], ...] = (
    ("auto", "Automatique"),
    ("onnx", "Réseau de neurones (ONNX)"),
    ("classic", "Classique (secours)"),
)
MINIMAP_MODES: tuple[tuple[str, str], ...] = (("auto", "Automatique"), ("manual", "Manuelle"))
MINIMAP_SIDES: tuple[tuple[str, str], ...] = (("auto", "Auto"), ("right", "Droite"), ("left", "Gauche"))
HOTKEY_CHOICES: tuple[str, ...] = ("Désactivé",) + tuple(f"F{i}" for i in range(1, 13))
AUTO_VOICE = "Automatique (meilleure voix française)"

#: Fields whose change needs a new detector (the engine is rebuilt).
DETECTOR_FIELDS = frozenset({"detector_backend", "detection_threshold"})
VOICE_FIELDS = frozenset({"voice_name", "voice_rate", "voice_volume", "beep_on_danger", "voice_engine",
                          "neural_voice", "neural_rate"})
#: voice_engine -> label (voice.VoiceEngine.list_engines() may add / rename some).
ENGINE_LABELS: tuple[tuple[str, str], ...] = (
    ("auto", "Automatique (recommandé)"),
    ("neural", "Neurale en ligne (naturelle)"),
    ("onecore", "Windows moderne (OneCore)"),
    ("sapi", "Windows classique (SAPI)"),
)
HOTKEY_FIELDS = frozenset({"hotkey_jungler", "hotkey_mute", "hotkey_overlay", "hotkey_ai", "hotkey_ward"})
#: Fields read once when the engine starts (the engine is rebuilt, the detector kept).
ENGINE_RESTART_FIELDS = frozenset({"hotkey_diag"})

#: Settings deliberately NOT shown in Réglages, with the reason (tests/test_ui_settings.py checks that
#: every other Config field has a control: no setting is dead or unreachable).
HIDDEN_SETTINGS: dict[str, str] = {
    "manual_minimap_rect": "écrit par « Calibrer la minimap »",
    "icon_scale_by_res": "appris pendant les parties",
    "objective_lead_s": "annonces des objectifs : réglées par le niveau de voix",
    "warn_radius": "réglé par la sensibilité",
    "danger_radius": "réglé par la sensibilité",
    "detection_threshold": "0 = valeur du modèle (réglage de développeur)",
    "radar_enabled": "remplacé par « Où dessiner » (overlay_mode)",
    "radar_xy": "écrit par « Déplacer les fenêtres »",
    "hud_xy": "écrit par « Déplacer les fenêtres »",
    "overlay_show_allies": "suivi du niveau du joueur (skill.py) et du mode détaillé",
    "overlay_show_roles": "suivi du niveau du joueur (skill.py) et du mode détaillé",
    "overlay_show_ghosts": "suivi du niveau du joueur (skill.py) et du mode détaillé",
    "overlay_show_last_seen": "suivi du niveau du joueur (skill.py) et du mode détaillé",
    "skill_level": "barre latérale « Ton niveau »",
    "safe_mode": "barre latérale « Mode sûr » (Ctrl+Maj+S)",
    "ui_geometry": "position de la fenêtre, mémorisée",
    "ui_scale": "ancien réglage, remplacé par « Taille de l'interface »",
    "ui_onboarding_done": "suivi du mode guidé",
    "ui_seen_changelog": "suivi des nouveautés",
    "update_channel_url": "réglage de développeur",
    "diag_duration_s": "réglage de développeur",
    "diag_interval_s": "réglage de développeur",
    "selfcheck_enabled": "auto-diagnostic (selfcheck.py) : réglage de développeur",
}

_ctk: Any = None      # customtkinter module (imported lazily by _import_ctk)


def _import_ctk() -> Any:
    """Import customtkinter once (lazy: importing this module never needs it)."""
    global _ctk
    if _ctk is None:
        import customtkinter  # noqa: PLC0415 - lazy by design

        _ctk = customtkinter
    return _ctk


# ======================================================================================
# Small pure helpers (tested without a display)
# ======================================================================================
def fmt_clock(seconds: Any) -> str:
    """``"12:34"`` (``"1:02:03"`` after an hour); ``"--:--"`` when unknown."""
    return clock(seconds, "--:--", hours=True)


def fmt_int_fr(n: Any) -> str:
    """French thousands separator (narrow no-break space): ``3300 -> "3 300"``."""
    try:
        v = int(round(float(n)))
    except (TypeError, ValueError, OverflowError):
        return "?"
    return f"{v:,}".replace(",", " ")


def fmt_decimal_fr(x: Any, decimals: int = 1) -> str:
    """``1.5 -> "1,5"``."""
    try:
        return f"{float(x):.{decimals}f}".replace(".", ",")
    except (TypeError, ValueError):
        return "?"


def state_key(state: Any) -> str:
    """Normalize an ``EngineState`` (enum, name or value) to its upper-case name."""
    if state is None:
        return "STOPPED"
    name = getattr(state, "name", None)
    if not isinstance(name, str):
        name = str(getattr(state, "value", state))
    name = name.strip().upper()
    if name.startswith("ENGINESTATE."):
        name = name.split(".", 1)[1]
    return name or "STOPPED"


def threat_fraction(level: int) -> float:
    """Fill ratio of the threat gauge for a threat level."""
    return {0: 0.18, 1: 0.6, 2: 1.0}.get(int(level), 0.0)


def app_icon_path(kind: str = "ico") -> Path | None:
    """The application icon (``packaging/icon.<kind>`` next to the package, also in the .exe)."""
    candidates = [paths.package_dir().parent / "packaging" / f"icon.{kind}",
                  paths.asset_path(f"app_icon.{kind}")]
    for p in candidates:
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def autostart_support() -> tuple[bool, str]:
    """(supported, French reason when not) for "Lancer avec Windows"."""
    if sys.platform != "win32":
        return False, "Disponible uniquement sous Windows."
    if not paths.is_frozen():
        return False, "Disponible uniquement avec TreeAICoach.exe (pas depuis les sources Python)."
    return True, ""


def autostart_command() -> str:
    """Value written in the Run key: the quoted executable path."""
    return f'"{sys.executable}"'


def get_windows_autostart() -> bool:
    """True if the HKCU Run key launches this executable. Never raises."""
    if sys.platform != "win32":
        return False
    try:
        import winreg  # noqa: PLC0415

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _typ = winreg.QueryValueEx(key, RUN_VALUE)
        return bool(value)
    except OSError:
        return False
    except Exception:
        log.exception("Cannot read the Windows autostart key")
        return False


def set_windows_autostart(enabled: bool) -> bool:
    """Create / delete ``HKCU\\...\\Run\\TreeAICoach``. Returns True on success. Never raises."""
    ok, _reason = autostart_support()
    if not ok:
        return False
    try:
        import winreg  # noqa: PLC0415

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE | winreg.KEY_READ) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, autostart_command())
            else:
                try:
                    winreg.DeleteValue(key, RUN_VALUE)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        log.exception("Cannot change the Windows autostart key")
        return False


def open_path(path: Path | str, select: bool = False) -> bool:
    """Open a folder / file with the system file manager (``select`` highlights a file). Never raises."""
    try:
        p = Path(path)
        if sys.platform == "win32":
            if select and p.exists():
                subprocess.Popen(["explorer", "/select,", str(p)])  # noqa: S603,S607
            else:
                os.startfile(str(p if p.is_dir() or not select else p.parent))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R" if select else "", str(p)])  # noqa: S603,S607
        else:
            target = p.parent if select and p.is_file() else p
            subprocess.Popen(["xdg-open", str(target)], stdout=subprocess.DEVNULL,  # noqa: S603,S607
                             stderr=subprocess.DEVNULL)
        return True
    except Exception:
        log.exception("Cannot open %s", path)
        return False


def game_field(game: dict, *names: str, default: Any = None) -> Any:
    """First non-empty value among ``names`` in a ``list_games()`` entry (or its ``summary``)."""
    for src in (game, game.get("summary") if isinstance(game.get("summary"), dict) else None,
                game.get("meta") if isinstance(game.get("meta"), dict) else None):
        if not isinstance(src, dict):
            continue
        for n in names:
            v = src.get(n)
            if v not in (None, ""):
                return v
    return default


def game_result(game: dict) -> str | None:
    """"win" | "lose" | None from a game summary."""
    r = game_field(game, "result", "outcome")
    if isinstance(r, bool):
        return "win" if r else "lose"
    if isinstance(r, str):
        s = r.strip().lower()
        if s in ("win", "victory", "victoire", "gagné", "won"):
            return "win"
        if s in ("lose", "loss", "defeat", "défaite", "lost", "perdu"):
            return "lose"
    return None


def game_datetime(game: dict) -> _dt.datetime | None:
    """Start date of a recorded game (ISO string or epoch)."""
    v = game_field(game, "start", "date", "started_at", "recorded_at")
    try:
        if isinstance(v, (int, float)) and math.isfinite(v):
            return _dt.datetime.fromtimestamp(float(v))
        if isinstance(v, str) and v:
            return _dt.datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
    except (ValueError, OverflowError, OSError):
        pass
    return None


def fmt_game_date(d: _dt.datetime | None, today: _dt.date | None = None) -> str:
    """``"Aujourd'hui 21:14"`` / ``"Hier 18:02"`` / ``"12/09 20:31"``."""
    if d is None:
        return "Date inconnue"
    today = today or _dt.date.today()
    hm = d.strftime("%H:%M")
    if d.date() == today:
        return f"Aujourd'hui {hm}"
    if d.date() == today - _dt.timedelta(days=1):
        return f"Hier {hm}"
    return f"{d.day:02d}/{d.month:02d} {hm}"


def _int_or_none(v: Any) -> int | None:
    try:
        if v is None or isinstance(v, bool):
            return None
        f = float(v)
        return int(round(f)) if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def precision_color(prec: Any) -> str:
    """Colour of a rated-play precision (0-100): green from 80, plain text from 60, amber below, dim if None."""
    p = _int_or_none(prec)
    if p is None:
        return DIM
    return SAFE if p >= 80 else TEXT if p >= 60 else WARNING


def session_stats(games: Sequence[dict], today: _dt.date | None = None) -> dict[str, Any]:
    """Stats of the session cards: today's games, or the 10 most recent ones when none today."""
    today = today or _dt.date.today()
    todays = [g for g in games if (game_datetime(g) or _dt.datetime(1970, 1, 1)).date() == today]
    scope = "Aujourd'hui" if todays else (f"{min(10, len(games))} dernières parties" if games else "Aucune partie")
    sel = todays if todays else list(games)[:10]
    n = len(sel)
    wins = sum(1 for g in sel if game_result(g) == "win")
    deaths = [d for d in (_int_or_none(game_field(g, "deaths")) for g in sel) if d is not None]
    ganks = [_int_or_none(game_field(g, "ganks")) for g in sel]
    survived = [_int_or_none(game_field(g, "ganks_survived")) for g in sel]
    total_ganks = sum(x for x in ganks if x is not None)
    avoided = sum(x for x in survived if x is not None)
    precs = [p for p in (_int_or_none(game_field(g, "precision")) for g in sel) if p is not None]
    return {
        "scope": scope,
        "games": n,
        "wins": wins,
        "winrate": (wins / n) if n else None,
        "deaths_per_game": (sum(deaths) / len(deaths)) if deaths else None,
        "ganks": total_ganks,
        "ganks_avoided": avoided,
        "precision": (sum(precs) / len(precs)) if precs else None,
    }


# ======================================================================================
# Images
# ======================================================================================
def _hex_rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _rgba_array_to_pil(rgba: np.ndarray) -> Image.Image | None:
    try:
        a = np.asarray(rgba)
        if a.ndim != 3 or a.shape[2] not in (3, 4) or a.dtype != np.uint8:
            return None
        return Image.fromarray(np.ascontiguousarray(a), "RGBA" if a.shape[2] == 4 else "RGB")
    except Exception:
        return None


def circle_icon(icon_rgba: np.ndarray | None, size: int, ring: str | None, grey: bool = False,
                bg: str = PANEL, ring_w: float = 0.075) -> Image.Image:
    """Round champion portrait with a coloured ring, on ``bg`` (RGB), 4x supersampled."""
    ss = 4
    big = size * ss
    out = Image.new("RGBA", (big, big), _hex_rgb(bg) + (255,))
    pil = _rgba_array_to_pil(icon_rgba) if icon_rgba is not None else None
    ring_px = max(ss * 2, int(round(big * ring_w)))
    inner = big - 2 * ring_px
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse((ring_px, ring_px, big - ring_px - 1, big - ring_px - 1), fill=255)
    if pil is not None:
        p = pil.convert("RGBA")
        w, h = p.size
        m = min(w, h)
        crop = int(m * 0.08)            # portraits have a thin dark border: zoom in a bit
        p = p.crop(((w - m) // 2 + crop, (h - m) // 2 + crop, (w + m) // 2 - crop, (h + m) // 2 - crop))
        p = p.resize((inner, inner), Image.LANCZOS)
        if grey:
            g = p.convert("L").point(lambda v: int(v * 0.55))
            p = Image.merge("RGBA", (g, g, g, p.getchannel("A")))
        layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        layer.paste(p, (ring_px, ring_px))
        out.paste(layer, (0, 0), Image.composite(layer.getchannel("A"), Image.new("L", (big, big), 0), mask))
    else:
        d = ImageDraw.Draw(out)
        d.ellipse((ring_px, ring_px, big - ring_px - 1, big - ring_px - 1), fill=_hex_rgb(PANEL_HI))
        # a discreet question mark
        cx, cy, r = big / 2, big / 2, big * 0.16
        d.arc((cx - r, cy - r * 1.6, cx + r, cy + r * 0.4), 200, 90, fill=_hex_rgb(DIM), width=ss * 3)
        d.line((cx, cy + r * 0.4, cx, cy + r * 0.9), fill=_hex_rgb(DIM), width=ss * 3)
        d.ellipse((cx - ss * 2, cy + r * 1.35, cx + ss * 2, cy + r * 1.35 + ss * 4), fill=_hex_rgb(DIM))
    if ring:
        d = ImageDraw.Draw(out)
        d.ellipse((ss, ss, big - ss - 1, big - ss - 1), outline=_hex_rgb(ring), width=ring_px - ss)
    # round the outer corners onto the background (anti-aliased)
    return out.resize((size, size), Image.LANCZOS).convert("RGB")


def square_icon(icon_rgba: np.ndarray | None, size: int, bg: str = PANEL, radius: int = 3) -> Image.Image:
    """Champion portrait as a square with slightly rounded corners (tables), RGB on ``bg``."""
    pil = _rgba_array_to_pil(icon_rgba) if icon_rgba is not None else None
    if pil is None:
        img = Image.new("RGBA", (size, size), _hex_rgb(PANEL_HI) + (255,))
    else:
        p = pil.convert("RGBA")
        w, h = p.size
        m = min(w, h)
        c = int(m * 0.08)
        img = p.crop(((w - m) // 2 + c, (h - m) // 2 + c, (w + m) // 2 - c, (h + m) // 2 - c)).resize(
            (size, size), Image.LANCZOS)
    return rounded_on_bg(img, radius, bg)


def nav_icon(kind: str, size: int = 18, color: str = MUTED) -> Image.Image:
    """Small line icons of the sidebar (drawn with PIL, 4x supersampled)."""
    ss = 4
    S = size * ss
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    col = _hex_rgb(color) + (255,)
    w = int(1.7 * ss)
    if kind == "dashboard":
        g = S * 0.08
        c = (S - 3 * g) / 2
        for i in range(2):
            for j in range(2):
                x = g + i * (c + g)
                y = g + j * (c + g)
                h = c * (0.75 if (i, j) == (0, 1) else 1.0)
                d.rounded_rectangle((x, y + (c - h), x + c, y + c), radius=ss * 1.5, outline=col, width=w)
    elif kind == "voice":
        d.polygon([(S * 0.1, S * 0.38), (S * 0.28, S * 0.38), (S * 0.5, S * 0.16), (S * 0.5, S * 0.84),
                   (S * 0.28, S * 0.62), (S * 0.1, S * 0.62)], outline=col, fill=None, width=w)
        for r in (0.17, 0.32):
            d.arc((S * (0.5 - r), S * (0.5 - r) - 0, S * (0.5 + r) + S * 0.12, S * (0.5 + r)),
                  -50, 50, fill=col, width=w)
    elif kind == "overlay":
        for k, y in enumerate((0.3, 0.5, 0.7)):
            pts = [(S * 0.5, S * (y - 0.2)), (S * 0.92, S * y), (S * 0.5, S * (y + 0.2)), (S * 0.08, S * y)]
            if k == 0:
                d.polygon(pts, outline=col, width=w)
            else:
                d.line([pts[1], pts[2], pts[3]], fill=col, width=w, joint="curve")
    elif kind == "analysis":
        base = S * 0.88
        for i, hgt in enumerate((0.35, 0.62, 0.48, 0.8)):
            x = S * (0.1 + i * 0.22)
            d.rounded_rectangle((x, base - S * hgt, x + S * 0.13, base), radius=ss, fill=col)
    elif kind == "settings":
        cx = cy = S / 2
        for k in range(8):
            a = k * math.pi / 4
            x = cx + math.cos(a) * S * 0.36
            y = cy + math.sin(a) * S * 0.36
            d.ellipse((x - S * 0.09, y - S * 0.09, x + S * 0.09, y + S * 0.09), fill=col)
        d.ellipse((cx - S * 0.34, cy - S * 0.34, cx + S * 0.34, cy + S * 0.34), fill=col)
        d.ellipse((cx - S * 0.14, cy - S * 0.14, cx + S * 0.14, cy + S * 0.14), fill=(0, 0, 0, 0))
    elif kind == "help":
        d.ellipse((S * 0.07, S * 0.07, S * 0.93, S * 0.93), outline=col, width=w)
        d.arc((S * 0.34, S * 0.24, S * 0.66, S * 0.54), 180, 80, fill=col, width=w)
        d.line((S * 0.5, S * 0.53, S * 0.5, S * 0.62), fill=col, width=w)
        d.ellipse((S * 0.455, S * 0.7, S * 0.545, S * 0.79), fill=col)
    elif kind == "play":
        d.polygon([(S * 0.22, S * 0.12), (S * 0.88, S * 0.5), (S * 0.22, S * 0.88)], fill=col)
    elif kind == "stop":
        d.rounded_rectangle((S * 0.18, S * 0.18, S * 0.82, S * 0.82), radius=ss * 2, fill=col)
    elif kind == "target":
        d.rectangle((S * 0.12, S * 0.12, S * 0.88, S * 0.88), outline=col, width=w)
        d.line((S * 0.5, S * 0.02, S * 0.5, S * 0.3), fill=col, width=w)
        d.line((S * 0.5, S * 0.7, S * 0.5, S * 0.98), fill=col, width=w)
        d.line((S * 0.02, S * 0.5, S * 0.3, S * 0.5), fill=col, width=w)
        d.line((S * 0.7, S * 0.5, S * 0.98, S * 0.5), fill=col, width=w)
    elif kind == "demo":
        d.ellipse((S * 0.08, S * 0.08, S * 0.92, S * 0.92), outline=col, width=w)
        d.polygon([(S * 0.4, S * 0.3), (S * 0.72, S * 0.5), (S * 0.4, S * 0.7)], fill=col)
    elif kind == "folder":
        d.rounded_rectangle((S * 0.08, S * 0.25, S * 0.92, S * 0.85), radius=ss * 2, outline=col, width=w)
        d.line((S * 0.08, S * 0.28, S * 0.08, S * 0.18, S * 0.4, S * 0.18, S * 0.48, S * 0.28), fill=col,
               width=w)
    elif kind == "report":
        d.rounded_rectangle((S * 0.18, S * 0.06, S * 0.82, S * 0.94), radius=ss * 2, outline=col, width=w)
        for y in (0.32, 0.5, 0.68):
            d.line((S * 0.32, S * y, S * 0.68, S * y), fill=col, width=w)
    elif kind == "refresh":
        d.arc((S * 0.12, S * 0.12, S * 0.88, S * 0.88), 30, 320, fill=col, width=w)
        d.polygon([(S * 0.94, S * 0.2), (S * 0.9, S * 0.52), (S * 0.62, S * 0.36)], fill=col)
    elif kind == "move":
        c = S / 2
        d.line((c, S * 0.08, c, S * 0.92), fill=col, width=w)
        d.line((S * 0.08, c, S * 0.92, c), fill=col, width=w)
        a = S * 0.14
        d.polygon([(c, S * 0.02), (c - a, S * 0.02 + a), (c + a, S * 0.02 + a)], fill=col)
        d.polygon([(c, S * 0.98), (c - a, S * 0.98 - a), (c + a, S * 0.98 - a)], fill=col)
        d.polygon([(S * 0.02, c), (S * 0.02 + a, c - a), (S * 0.02 + a, c + a)], fill=col)
        d.polygon([(S * 0.98, c), (S * 0.98 - a, c - a), (S * 0.98 - a, c + a)], fill=col)
    return im.resize((size, size), Image.LANCZOS)


def _fallback_logo(size: int) -> Image.Image:
    """Gold medallion used when the packaged icon is missing."""
    ss = 4
    S = size * ss
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((ss, ss, S - ss, S - ss), fill=_hex_rgb(PANEL) + (255,), outline=_hex_rgb(GOLD) + (255,),
              width=ss * 2)
    c = S / 2
    d.line((c, S * 0.78, c, S * 0.3), fill=_hex_rgb(GOLD) + (255,), width=ss * 2)
    for dx in (-1, 1):
        d.line((c, S * 0.55, c + dx * S * 0.2, S * 0.4, c + dx * S * 0.2, S * 0.3),
               fill=_hex_rgb(GOLD) + (255,), width=ss * 2)
        d.ellipse((c + dx * S * 0.2 - ss * 3, S * 0.27 - ss * 3, c + dx * S * 0.2 + ss * 3, S * 0.27 + ss * 3),
                  fill=_hex_rgb(TEAL) + (255,))
    d.ellipse((c - ss * 3, S * 0.27 - ss * 3, c + ss * 3, S * 0.27 + ss * 3), fill=_hex_rgb(TEAL) + (255,))
    return im.resize((size, size), Image.LANCZOS)


def load_logo(size: int) -> Image.Image:
    """App logo (``packaging/icon.png``), or a drawn fallback."""
    p = app_icon_path("png")
    if p is not None:
        try:
            with Image.open(p) as im:
                return im.convert("RGBA").resize((size, size), Image.LANCZOS)
        except Exception:
            log.debug("Cannot read the app icon %s", p, exc_info=True)
    return _fallback_logo(size)


def rounded_on_bg(img: Image.Image, radius: int, bg: str = PANEL) -> Image.Image:
    """Composite an RGBA image on ``bg`` with rounded corners (RGB result)."""
    w, h = img.size
    base = Image.new("RGB", (w, h), _hex_rgb(bg))
    mask = Image.new("L", (w * 3, h * 3), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * 3 - 1, h * 3 - 1), radius=radius * 3, fill=255)
    mask = mask.resize((w, h), Image.LANCZOS)
    rgba = img.convert("RGBA")
    alpha = Image.fromarray((np.asarray(rgba.getchannel("A"), np.float32) *
                             np.asarray(mask, np.float32) / 255.0).astype(np.uint8))
    base.paste(rgba.convert("RGB"), (0, 0), alpha)
    return base


def flat_placeholder(size: int) -> Image.Image:
    """Plain rounded square (instant) shown until :func:`radar_placeholder` is ready."""
    return rounded_on_bg(Image.new("RGBA", (size, size), _hex_rgb(PANEL_LO) + (255,)), 14, PANEL)


def radar_placeholder(size: int) -> Image.Image:
    """Dimmed minimap texture shown while no game is analysed."""
    try:
        from treeaicoach.overlay_render import default_radar_texture  # noqa: PLC0415

        tex = default_radar_texture()
        import cv2  # noqa: PLC0415

        small = cv2.resize(tex, (size, size), interpolation=cv2.INTER_AREA)[..., ::-1]
        dim = (small.astype(np.float32) * 0.22 + np.array(_hex_rgb(PANEL_LO), np.float32) * 0.5)
        img = Image.fromarray(np.clip(dim, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    except Exception:
        img = Image.new("RGBA", (size, size), _hex_rgb(PANEL_LO) + (255,))
    return rounded_on_bg(img, 14, PANEL)


# ======================================================================================
# Threading helpers
# ======================================================================================
class _Dispatcher:
    """Runs jobs on worker threads and hands callbacks back to the Tk thread (queue + after)."""

    def __init__(self) -> None:
        self._q: queue.SimpleQueue[Callable[[], None]] = queue.SimpleQueue()
        self.closed = False

    def post(self, fn: Callable[[], None]) -> None:
        """Queue ``fn`` to run on the Tk thread (callable from any thread)."""
        if not self.closed:
            self._q.put(fn)

    def run(self, job: Callable[[], Any], on_done: Callable[[Any], None] | None = None,
            on_error: Callable[[BaseException], None] | None = None, name: str = "TreeAI-ui-job") -> None:
        """Run ``job()`` on a daemon thread; ``on_done(result)`` / ``on_error(exc)`` on the Tk thread."""
        def worker() -> None:
            try:
                res = job()
            except BaseException as exc:  # noqa: BLE001 - reported to the UI
                log.exception("Background job %s failed", name)
                if on_error is not None:
                    self.post(lambda e=exc: on_error(e))
                return
            if on_done is not None:
                self.post(lambda r=res: on_done(r))

        threading.Thread(target=worker, name=name, daemon=True).start()

    def drain(self, max_items: int = 50) -> None:
        """Run pending callbacks (Tk thread)."""
        for _ in range(max_items):
            try:
                fn = self._q.get_nowait()
            except queue.Empty:
                return
            try:
                fn()
            except Exception:
                log.exception("UI callback failed")


class _RadarWorker:
    """Renders the radar preview at 5 Hz on a background thread (only while ``active``)."""

    def __init__(self, provider: Callable[[], Any], size: int) -> None:
        self._provider = provider
        self.size = size
        self.active = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._frame: tuple[int, Image.Image | None] = (0, None)
        self._seq = 0
        self._thread = threading.Thread(target=self._run, name="TreeAI-ui-radar", daemon=True)
        self.render_ms = 0.0

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.active.set()

    def latest(self) -> tuple[int, Image.Image | None]:
        with self._lock:
            return self._frame

    def _render_once(self) -> Image.Image | None:
        state, preview = self._provider()
        if state is not None:
            from treeaicoach.overlay_render import radar_preview_rgba  # noqa: PLC0415

            rgba = radar_preview_rgba(state, self.size)
            return rounded_on_bg(Image.fromarray(rgba, "RGBA"), 14, PANEL)
        if isinstance(preview, np.ndarray) and preview.ndim == 3 and preview.shape[2] >= 3:
            import cv2  # noqa: PLC0415

            small = cv2.resize(preview[..., :3], (self.size, self.size), interpolation=cv2.INTER_AREA)
            return rounded_on_bg(Image.fromarray(np.ascontiguousarray(small[..., ::-1]), "RGB"), 14, PANEL)
        return None

    def _run(self) -> None:
        errors = 0
        while not self._stop.is_set():
            if not self.active.wait(0.5):
                continue
            if self._stop.is_set():
                break
            t0 = time.perf_counter()
            try:
                img = self._render_once()
                errors = 0
            except Exception:
                errors += 1
                if errors in (1, 50):
                    log.exception("Radar preview rendering failed")
                img = None
            self.render_ms = 1000 * (time.perf_counter() - t0)
            with self._lock:
                self._seq += 1
                self._frame = (self._seq, img)
            self._stop.wait(max(0.02, PREVIEW_MS / 1000 - (time.perf_counter() - t0)))


def _guarded(method: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator for :class:`CoachApp` callbacks: log + French toast instead of an exception."""
    @functools.wraps(method)
    def wrapper(self: "CoachApp", *args: Any, **kwargs: Any) -> Any:
        if getattr(self, "_closing", False):
            return None
        try:
            return method(self, *args, **kwargs)
        except Exception as exc:
            if getattr(self, "_closing", False):
                log.debug("UI action %s interrupted by shutdown: %s", method.__name__, exc)
                return None
            log.exception("UI action %s failed", method.__name__)
            try:
                self.show_error(f"Une erreur est survenue : {exc}")
            except Exception:
                pass
            return None
    return wrapper


# ======================================================================================
# Theme & widgets
# ======================================================================================
def _apply_theme(ctk: Any) -> None:
    """Point every CustomTkinter default at the TreeAI tokens (docs/DESIGN.md): flat, 4 px radius."""
    ctk.set_appearance_mode("dark")
    try:   # rounded shapes drawn as 1 polygon instead of antialiased font glyphs (8+ text items per
        # frame, the slowest thing to draw on Windows; radii are 4-6 px, the difference is invisible)
        ctk.DrawEngine.preferred_drawing_method = "polygon_shapes"
    except Exception:
        pass
    try:
        ctk.set_default_color_theme("dark-blue")
    except Exception:
        pass
    th = ctk.ThemeManager.theme

    def put(widget: str, **kw: Any) -> None:
        d = th.setdefault(widget, {})
        for k, v in kw.items():
            d[k] = [v, v] if isinstance(v, str) and v != "transparent" and k.endswith("color") else v

    put("CTk", fg_color=BG)
    put("CTkToplevel", fg_color=BG)
    put("CTkFrame", fg_color=PANEL, top_fg_color=PANEL_HI, border_color=BORDER, corner_radius=RADIUS)
    put("CTkButton", fg_color=PANEL_HI, hover_color=HOVER, border_color=BORDER_GOLD,
        text_color=TEXT, text_color_disabled=DIM, corner_radius=RADIUS)
    put("CTkLabel", text_color=TEXT, corner_radius=0)
    put("CTkEntry", fg_color=PANEL_LO, border_color=BORDER_GOLD, text_color=TEXT,
        placeholder_text_color=DIM, corner_radius=RADIUS, border_width=1)
    put("CTkCheckBox", fg_color=GOLD, border_color=LINE_STRONG, hover_color=GOLD_HOVER, checkmark_color=ON_GOLD,
        text_color=TEXT, text_color_disabled=DIM, corner_radius=3, border_width=2)
    put("CTkSwitch", fg_color=SWITCH_OFF, progress_color=ACCENT, button_color=TEXT, button_hover_color="#FFFFFF",
        text_color=TEXT, text_color_disabled=DIM, corner_radius=3, border_width=2, button_length=0)
    put("CTkRadioButton", fg_color=GOLD, border_color=LINE_STRONG, hover_color=GOLD_HOVER, text_color=TEXT)
    put("CTkProgressBar", fg_color=SWITCH_OFF, progress_color=ACCENT, border_color=BORDER, corner_radius=2)
    put("CTkSlider", fg_color=SWITCH_OFF, progress_color=ACCENT_DIM, button_color=ACCENT,
        button_hover_color=ACCENT_HOVER, corner_radius=2, button_corner_radius=3, button_length=0)
    put("CTkOptionMenu", fg_color=PANEL_HI, button_color=PANEL_HI, button_hover_color=HOVER,
        text_color=TEXT, text_color_disabled=DIM, corner_radius=RADIUS)
    put("CTkComboBox", fg_color=PANEL_HI, border_color=BORDER_GOLD, button_color=HOVER,
        button_hover_color=HOVER, text_color=TEXT, corner_radius=RADIUS, border_width=1)
    put("CTkScrollbar", fg_color="transparent", button_color=SWITCH_OFF, button_hover_color=LINE_STRONG,
        corner_radius=2)
    put("CTkSegmentedButton", fg_color=PANEL_LO, selected_color=ACCENT_DIM, selected_hover_color=ACCENT_DIM,
        unselected_color=PANEL_LO, unselected_hover_color=PANEL_HI, text_color=TEXT,
        text_color_disabled=DIM, corner_radius=RADIUS, border_width=2)
    put("CTkTextbox", fg_color=PANEL, border_color=BORDER, text_color=TEXT, scrollbar_button_color=SWITCH_OFF,
        scrollbar_button_hover_color=LINE_STRONG, corner_radius=RADIUS)
    put("CTkScrollableFrame", label_fg_color=PANEL)
    put("DropdownMenu", fg_color=PANEL_HI, hover_color=HOVER, text_color=TEXT)


#: Body font candidates (Segoe UI on Windows). Inter / Poppins / Space Grotesk / Geist are
#: deliberately absent: they are the "generated template" look (docs/DESIGN.md).
BODY_FONTS: tuple[str, ...] = ("Segoe UI", "Segoe UI Variable Text", "Noto Sans", "DejaVu Sans",
                               "Liberation Sans", "Helvetica", "Arial")
#: Display font (numbers, titles): condensed, "broadcast" feel. Tk on Windows lists the named
#: instances of the Bahnschrift variable font as separate GDI families ("Bahnschrift",
#: "Bahnschrift SemiBold", "Bahnschrift SemiBold Condensed"...; Windows 10 1709+). Older Windows:
#: Segoe UI Semibold (Vista+, GDI names are cut at 31 characters, hence "...Display Semib").
#: Elsewhere: a condensed / semi-bold sans, else the body family (never Tk's default font).
DISPLAY_FONTS: tuple[str, ...] = ("Bahnschrift SemiBold", "Bahnschrift", "Segoe UI Variable Display Semib",
                                  "Segoe UI Semibold", "Roboto Condensed", "DejaVu Sans Condensed",
                                  "Liberation Sans Narrow", "Arial Narrow")


def _families(root: Any) -> set[str]:
    try:
        import tkinter.font as tkfont  # noqa: PLC0415

        return {str(f) for f in tkfont.families(root)}
    except Exception:
        return set()


def pick_font(families: Any, candidates: Sequence[str], default: str) -> str:
    """First candidate installed (case-insensitive; vertical "@" GDI families ignored), spelled as
    Tk lists it; ``default`` when none is. Pure."""
    by_lower: dict[str, str] = {}
    for f in families or ():
        name = str(f)
        if name and not name.startswith("@"):
            by_lower.setdefault(name.strip().lower(), name)
    for cand in candidates:
        hit = by_lower.get(cand.lower())
        if hit is not None:
            return hit
    return default


def _pick_family(root: Any) -> str:
    """Segoe UI on Windows, else the best available sans-serif font."""
    return pick_font(_families(root), BODY_FONTS, "TkDefaultFont")


def _pick_display(root: Any, body: str) -> str:
    """Bahnschrift SemiBold (Windows 10+) for numbers and titles, else the best fallback / the body family."""
    return pick_font(_families(root), DISPLAY_FONTS, body)


def display_weight(family: str) -> str:
    """Tk weight for the display face: the named semi-bold / bold instances are drawn "normal" (asking
    "bold" on them would synthesise an ugly double-bold), the others "bold"."""
    f = str(family or "").lower()
    return "normal" if any(w in f for w in ("semibold", "semib", "bold", "black", "heavy")) else "bold"


class _Fonts:
    """The app's CTkFont set (created once the root exists). Sizes in px at 100 % (docs/DESIGN.md).

    Readability first: body 14, secondary 13, captions 12 (never smaller), section titles 16 and
    page titles 26 in the display face (Bahnschrift), big numbers 28. ``ui_scale`` and the Windows
    display scaling (CustomTkinter's per-monitor DPI factor) multiply them all.
    """

    def __init__(self, ctk: Any, family: str, display: str | None = None) -> None:
        f = family
        d = display or family
        dw = display_weight(d)
        self.family = f
        self.display = d
        self.brand = ctk.CTkFont(family=d, size=18, weight=dw)
        self.title = ctk.CTkFont(family=d, size=26, weight=dw)
        self.h2 = ctk.CTkFont(family=d, size=17, weight=dw)
        self.h3 = ctk.CTkFont(family=f, size=14, weight="bold")
        self.body = ctk.CTkFont(family=f, size=14)
        self.small = ctk.CTkFont(family=f, size=13)
        self.tiny = ctk.CTkFont(family=f, size=12)
        self.tiny_bold = ctk.CTkFont(family=f, size=12, weight="bold")
        self.caps = ctk.CTkFont(family=f, size=12, weight="bold")
        self.nav = ctk.CTkFont(family=f, size=14)
        self.nav_active = ctk.CTkFont(family=f, size=14, weight="bold")
        self.button = ctk.CTkFont(family=f, size=13, weight="bold")
        self.state = ctk.CTkFont(family=d, size=21, weight=dw)
        self.clock = ctk.CTkFont(family=d, size=28, weight=dw)
        self.stat = ctk.CTkFont(family=d, size=28, weight=dw)
        self.num = ctk.CTkFont(family=d, size=16, weight=dw)


# ======================================================================================
# Dashboard hero (canvas: hextech background, pulse, clock, threat gauge)
# ======================================================================================
class _CanvasText:
    """Label-like shim over a canvas text item: ``configure(text=, text_color=)`` / ``cget("text")``."""

    def __init__(self, canvas: Any, item: int, on_change: Callable[[], None] | None = None) -> None:
        self.canvas, self.item, self._on_change = canvas, item, on_change

    def configure(self, **kw: Any) -> None:
        opts: dict[str, Any] = {}
        if "text" in kw:
            opts["text"] = kw["text"]
        if "text_color" in kw:
            opts["fill"] = kw["text_color"]
        if not opts:
            return
        try:
            changed = "text" in opts and getattr(self, "_req", None) != opts["text"]
            if "text" in opts:
                if not changed:
                    opts.pop("text")
                self._req = kw["text"]
            if not opts:
                return
            self.canvas.itemconfigure(self.item, **opts)
            if changed and self._on_change is not None:
                self._on_change()
        except Exception:
            pass

    def cget(self, name: str) -> Any:
        if name == "text" and getattr(self, "_req", None) is not None:
            return self._req          # the full text (the item may show it ellipsized)
        try:
            return self.canvas.itemcget(self.item, "text" if name == "text" else "fill")
        except Exception:
            return ""


class _CanvasBadge:
    """``grid()`` / ``grid_remove()`` shim showing the "DÉMO" badge drawn on the hero canvas."""

    def __init__(self, hero: "HeroBanner") -> None:
        self.hero = hero

    def grid(self, **_kw: Any) -> None:
        self.hero.set_badge(True)

    def grid_remove(self) -> None:
        self.hero.set_badge(False)


class HeroBanner:
    """Dashboard status strip: live state, lane match-up, threat gauge, objective timers, clock, button.

    Everything is drawn on one ``tk.Canvas`` over a flat PIL background (graphite panel, 1 px
    border, 3 px state bar on the left: docs/DESIGN.md). The match-up block is a small frame put on
    the canvas with ``create_window``. The background ``PhotoImage`` is created with the canvas as
    master and kept on ``self`` (no "pyimage doesn't exist").
    """

    SEGMENTS = 20

    def __init__(self, app: "CoachApp", parent: Any) -> None:
        import tkinter as tk  # noqa: PLC0415

        self.app = app
        s = app._scaled
        self.s = s
        self.h = s(128)
        fam, px = app.fonts.family, app._font_px
        disp = getattr(app.fonts, "display", fam)
        dw = "normal" if ("Semi" in disp or "Bold" in disp) else "bold"
        c = tk.Canvas(parent, height=self.h, bg=BG, highlightthickness=0, bd=0)
        self.canvas = c
        self._photo: Any = None
        self._glow = DIM
        self._rendered: tuple = ()
        self._bg_job: str | None = None
        self.dot_bg = PANEL
        self.right_bg = PANEL
        self._dot = (s(30), s(34))
        self._gauge_box: tuple[float, float, float, float] | None = None
        self._bg_item = c.create_image(0, 0, anchor="nw")
        self.halo = c.create_oval(0, 0, 0, 0, fill="", outline="")
        self.ring = c.create_oval(0, 0, 0, 0, fill="", outline="")
        self.core = c.create_oval(0, 0, 0, 0, fill=DIM, outline="")
        self.title_item = c.create_text(0, 0, anchor="w", text="Démarrage…", fill=TEXT,
                                        font=(disp, px(21), dw))
        self.msg_item = c.create_text(0, 0, anchor="nw", text="", fill=MUTED, font=(fam, px(13)))
        self.badge_bg = c.create_rectangle(0, 0, 0, 0, fill="", outline=WARNING, state="hidden")
        self.badge_txt = c.create_text(0, 0, text="DÉMO", fill=WARNING, font=(fam, px(11), "bold"),
                                       state="hidden")
        self.vsep = c.create_line(0, 0, 0, 0, fill=LINE)
        self.clock_cap = c.create_text(0, 0, text="CHRONO", fill=DIM, font=(fam, px(12), "bold"))
        self.clock_item = c.create_text(0, 0, text="--:--", fill=DIM, font=(disp, px(30), dw))
        self.timers_item = c.create_text(0, 0, text="", fill=MUTED, font=(fam, px(13)), anchor="e")
        self.rule = c.create_line(0, 0, 0, 0, fill=LINE)
        self.threat_cap = c.create_text(0, 0, anchor="w", text="MENACE", fill=DIM, font=(fam, px(12), "bold"))
        self.threat_item = c.create_text(0, 0, anchor="w", text="-", fill=DIM, font=(disp, px(16), dw))
        self.detail_item = c.create_text(0, 0, anchor="w", text="Hors partie", fill=MUTED, font=(fam, px(13)))
        self.segs = [c.create_rectangle(0, 0, 0, 0, fill=TRACK, outline="") for _ in range(self.SEGMENTS)]
        self._button_win: int | None = None
        self._mu_win: int | None = None
        self._mu_widget: Any = None
        self._fix_win: int | None = None          # one-click fix of a problem state ("Calibrer", "Aide"...)
        self._fix_widget: Any = None
        self._fix_on = False
        self._mu_on = False                       # the match-up block (in game only)
        self._badge = False
        self._detail_full = "Hors partie"
        self.title = _CanvasText(c, self.title_item, self.layout)
        self.msg = _CanvasText(c, self.msg_item, self._fit_msg)
        self.clock = _CanvasText(c, self.clock_item)
        self.timers = _CanvasText(c, self.timers_item, self.layout)
        self.threat = _CanvasText(c, self.threat_item, self.layout)
        self.detail = _CanvasText(c, self.detail_item, self._fit_detail)
        self.badge = _CanvasBadge(self)
        c.bind("<Configure>", lambda _e: self._schedule_bg(), add="+")

    # ---------------------------------------------------------------- geometry
    def attach_button(self, btn: Any) -> None:
        self._button_win = self.canvas.create_window(0, 0, window=btn, anchor="e")
        self.layout()

    def attach_matchup(self, widget: Any) -> None:
        """Lane match-up block (my portrait VS my lane opponent) placed in the strip."""
        self._mu_widget = widget
        self._mu_win = self.canvas.create_window(0, 0, window=widget, anchor="e")
        self.layout()

    def attach_fix(self, widget: Any) -> None:
        """The fix button of a problem state: shown in place of the match-up (no game data then)."""
        self._fix_widget = widget
        self._fix_win = self.canvas.create_window(0, 0, window=widget, anchor="e", state="hidden")
        self.layout()

    def set_fix(self, on: bool) -> None:
        if bool(on) != self._fix_on:
            self._fix_on = bool(on)
            self.layout()

    def set_matchup(self, on: bool) -> None:
        """Show the match-up block (me VS my lane opponent) only when there is a game to show."""
        if bool(on) != self._mu_on:
            self._mu_on = bool(on)
            self.layout()

    def set_badge(self, on: bool) -> None:
        if on != self._badge:
            self._badge = on
            state = "normal" if on else "hidden"
            self.canvas.itemconfigure(self.badge_bg, state=state)
            self.canvas.itemconfigure(self.badge_txt, state=state)
            self.layout()

    def layout(self) -> None:
        c, s = self.canvas, self.s
        try:
            w = max(s(420), int(c.winfo_width()))
            h = self.h
            pad = s(16)
            top = s(42)                 # centre line of the upper row
            btn_w = 0
            if self._button_win is not None:
                btn_w = int(self.app.btn_start.winfo_reqwidth())
                c.coords(self._button_win, w - pad, top)
            clock_x = w - pad - btn_w - s(16)          # right edge of the clock block
            c.itemconfigure(self.clock_cap, anchor="e")
            c.itemconfigure(self.clock_item, anchor="e")
            c.coords(self.clock_cap, clock_x, top - s(20))
            c.coords(self.clock_item, clock_x, top + s(6))
            cb = c.bbox(self.clock_item)
            clock_left = (cb[0] if cb else clock_x - s(80)) - s(16)
            c.coords(self.vsep, clock_left, top - s(24), clock_left, top + s(24))
            right_limit = clock_left - s(16)
            if self._fix_win is not None:
                c.itemconfigure(self._fix_win, state="normal" if self._fix_on else "hidden")
                if self._fix_on:
                    c.coords(self._fix_win, right_limit, top)
                    right_limit -= int(self._fix_widget.winfo_reqwidth()) + s(16)
            if self._mu_win is not None:
                mw = int(self._mu_widget.winfo_reqwidth()) if self._mu_widget is not None else 0
                fits = self._mu_on and not self._fix_on and right_limit - mw - s(16) - (pad + s(24)) >= s(300)
                c.itemconfigure(self._mu_win, state="normal" if fits else "hidden")
                if fits:
                    c.coords(self._mu_win, right_limit, top)
                    right_limit -= mw + s(16)
            dx, dy = pad + s(10), top - s(10)
            self._dot = (dx, dy)
            c.coords(self.core, dx - s(4), dy - s(4), dx + s(4), dy + s(4))
            tx = pad + s(24)
            c.coords(self.title_item, tx, top - s(10))
            bb = c.bbox(self.title_item)
            if bb and self._badge:
                bx = bb[2] + s(10)
                fits = bx + s(50) <= right_limit           # narrow window: the sidebar pill says "démo"
                for item in (self.badge_bg, self.badge_txt):
                    c.itemconfigure(item, state="normal" if fits else "hidden")
                c.coords(self.badge_bg, bx, top - s(20), bx + s(50), top)
                c.coords(self.badge_txt, bx + s(25), top - s(10))
            c.coords(self.msg_item, tx, top + s(6))
            c.itemconfigure(self.msg_item, width=max(s(120), right_limit - tx))
            self._msg_bottom = h - s(22) - s(21) - s(4)
            self._fit_msg()
            # lower row: threat + gauge (left), objective timers (right)
            ty = h - s(22)
            c.coords(self.rule, s(3), ty - s(21), w - 1, ty - s(21))
            c.coords(self.threat_cap, pad, ty)
            c.coords(self.threat_item, pad + s(74), ty)
            tb = c.bbox(self.threat_item)
            dx0 = (tb[2] if tb else pad + s(140)) + s(10)
            c.coords(self.detail_item, dx0, ty)
            c.coords(self.timers_item, w - pad, ty)
            tib = c.bbox(self.timers_item)
            t_left = (tib[0] if tib and c.itemcget(self.timers_item, "text") else w - pad)
            gx1 = t_left - s(20)
            gx0 = max(int(w * 0.40), dx0 + s(120))
            if gx1 - gx0 < s(80):
                gx0 = gx1 - s(80)
            self._gauge_box = (gx0, ty - s(4), gx1, ty + s(4))
            self._fit_detail()
            self.draw_gauge(self.app._gauge_frac, self.app._gauge_color)
        except Exception:
            log.debug("hero layout failed", exc_info=True)

    def _fit_msg(self) -> None:
        """Ellipsize the state message so that it never runs into the threat row (narrow windows)."""
        c = self.canvas
        try:
            msg = getattr(self, "msg", None)
            text = getattr(msg, "_req", None)
            if text is None:
                text = c.itemcget(self.msg_item, "text")
            c.itemconfigure(self.msg_item, text=text)
            limit = getattr(self, "_msg_bottom", 10 ** 6)
            n = 0
            while len(text) > 1 and (c.bbox(self.msg_item) or (0, 0, 0, 0))[3] > limit and n < 400:
                text = text[:-3]
                n += 1
                c.itemconfigure(self.msg_item, text=text.rstrip(" ·,") + "…")
        except Exception:
            pass

    def _fit_detail(self) -> None:
        """Ellipsize the threat detail so that it never runs into the gauge."""
        c = self.canvas
        try:
            req = getattr(getattr(self, "detail", None), "_req", None)
            if req is not None:
                self._detail_full = req
            else:
                full = c.itemcget(self.detail_item, "text")
                if not full.endswith("…"):
                    self._detail_full = full
            box = self._gauge_box
            if box is None:
                return
            limit = box[0] - self.s(14)
            text = self._detail_full
            c.itemconfigure(self.detail_item, text=text)
            while len(text) > 1 and (c.bbox(self.detail_item) or (0, 0, 0, 0))[2] > limit:
                text = text[:-2]
                c.itemconfigure(self.detail_item, text=text + "…")
        except Exception:
            pass

    def draw_gauge(self, frac: float, color: str) -> None:
        box = self._gauge_box
        if box is None:
            return
        c, n = self.canvas, self.SEGMENTS
        x0, y0, x1, y1 = box
        gap = max(1, self.s(2))
        sw = max(2.0, (x1 - x0 - (n - 1) * gap) / n)
        lit = max(0.0, min(1.0, frac)) * n
        for i, item in enumerate(self.segs):
            a = x0 + i * (sw + gap)
            c.coords(item, a, y0, a + sw, y1)
            k = min(1.0, max(0.0, lit - i))
            fill = TRACK if k <= 0 else _blend(color, TRACK, 1 - k)
            c.itemconfigure(item, fill=fill)

    def pulse(self, color: str, k: float, active: bool) -> None:
        """Live dot: a square that breathes only while a game is analysed."""
        x, y = self._dot
        s = self.s
        c = self.canvas
        r = s(4) + (s(2) * k if active else 0)
        c.coords(self.core, x - r, y - r, x + r, y + r)
        c.itemconfigure(self.core, fill=_blend(color, PANEL, 0.25 * k) if active else color)

    # ---------------------------------------------------------------- background
    def set_glow(self, color: str) -> None:
        if color != self._glow:
            self._glow = color
            self._schedule_bg()

    def _schedule_bg(self) -> None:
        if self._bg_job is not None:
            try:
                self.canvas.after_cancel(self._bg_job)
            except Exception:
                pass
        try:
            self._bg_job = self.canvas.after(40, self._render_bg)
        except Exception:
            self._bg_job = None

    def _render_bg(self) -> None:
        self._bg_job = None
        c = self.canvas
        try:
            from PIL import ImageTk  # noqa: PLC0415

            w, h = int(c.winfo_width()), self.h
            if w < 20:
                return
            sig = (w, h, self._glow)
            if sig != self._rendered:
                self._rendered = sig
                img = ui_kit.hero_background(w, h, self._glow, bg=BG, panel=PANEL, border=LINE,
                                             gold=GOLD, radius=RADIUS)
                photo = ImageTk.PhotoImage(img, master=c)
                c.itemconfigure(self._bg_item, image=photo)
                self._photo = photo           # keep the reference (Tk does not)
                x, y = self._dot
                self.dot_bg = "#%02X%02X%02X" % img.getpixel((min(w - 1, int(x)), min(h - 1, int(y))))[:3]
                rb = "#%02X%02X%02X" % img.getpixel((max(0, w - self.s(30)), min(h - 1, self.s(50))))[:3]
                if rb != self.right_bg:
                    self.right_bg = rb
                    for btn in (self.app.btn_start, self._fix_widget):
                        try:
                            if btn is not None:
                                btn.configure(bg_color=rb)
                        except Exception:
                            pass
            self.layout()
        except Exception:
            log.debug("hero background failed", exc_info=True)


# ======================================================================================
# Toggle switch (anti-aliased pill, reads as on / off at a glance)
# ======================================================================================
TOGGLE_W, TOGGLE_H = 46, 26          # logical px (x ui_scale x Windows DPI)
TOGGLE_SMALL = (38, 22)
TOGGLE_RADIUS = TOGGLE_H // 2        # the one pill shape of the UI (docs/DESIGN.md: toggles only)
_toggle_cache: dict[tuple, Image.Image] = {}


def toggle_image(w: int, h: int, on: bool, color: str = ACCENT, bg: str = SURFACE, hover: bool = False,
                 disabled: bool = False) -> Image.Image:
    """Pill switch drawn 4x then reduced (crisp edges at any scaling). Off: outlined dark track and a
    grey knob on the left; on: filled ``color`` track and a dark knob on the right. Pure, cached."""
    key = (w, h, on, color, bg, hover, disabled)
    img = _toggle_cache.get(key)
    if img is not None:
        return img
    k = 4
    W, H = max(8, w) * k, max(6, h) * k
    big = Image.new("RGB", (W, H), _hex_rgb(bg))
    d = ImageDraw.Draw(big)
    r = H // 2
    if on:
        track = _blend(color, bg, 0.45) if disabled else (_blend(color, "#FFFFFF", 0.12) if hover else color)
        d.rounded_rectangle((0, 0, W - 1, H - 1), radius=r, fill=_hex_rgb(track))
        knob = _blend(ON_ACCENT, track, 0.25) if disabled else ON_ACCENT
        kr = r - 4 * k
        cx = W - r
    else:
        fill = RAISED if not hover else HOVER
        edge = _blend(MUTED, bg, 0.55) if disabled else (MUTED if hover else "#5E6762")
        d.rounded_rectangle((0, 0, W - 1, H - 1), radius=r, fill=_hex_rgb(fill), outline=_hex_rgb(edge),
                            width=max(2, int(1.6 * k)))
        knob = _blend(MUTED, bg, 0.5) if disabled else ("#C9CFCB" if hover else MUTED)
        kr = r - 6 * k
        cx = r
    d.ellipse((cx - kr, r - kr, cx + kr, r + kr), fill=_hex_rgb(knob))
    img = big.resize((max(8, w), max(6, h)), Image.LANCZOS)
    if len(_toggle_cache) > 256:
        _toggle_cache.clear()
    _toggle_cache[key] = img
    return img


def _widget_bg(w: Any, default: str = BG) -> str:
    """Effective background colour behind a widget (resolves CustomTkinter "transparent")."""
    for _ in range(30):
        if w is None:
            break
        try:
            fg = w.cget("fg_color")
            if isinstance(fg, (tuple, list)):
                fg = fg[-1]
            if fg and fg != "transparent":
                return str(fg)
        except Exception:
            try:
                return str(w.cget("bg"))
            except Exception:
                pass
        w = getattr(w, "master", None)
    return default


class Toggle:
    """On / off switch bound to a ``BooleanVar`` (CTkSwitch-like API: ``grid`` / ``configure(state=)``).

    Two cached anti-aliased images on a plain ``tk.Label`` (no per-frame redraw, no canvas items),
    hover highlight, keyboard (Space / Return when focused), optional text label on the right.
    """

    def __init__(self, app: "CoachApp", parent: Any, variable: Any, command: Callable[[], Any] | None = None,
                 color: str = ACCENT, small: bool = False, text: str = "", text_color: str = MUTED) -> None:
        import tkinter as tk  # noqa: PLC0415

        self.app, self.var, self.command, self.color = app, variable, command, color
        self.size = TOGGLE_SMALL if small else (TOGGLE_W, TOGGLE_H)
        self.state = "normal"
        self._hover = False
        self._sig: tuple = ()
        self._photo: Any = None
        self.bg = _widget_bg(parent)
        self.frame = tk.Frame(parent, bg=self.bg, bd=0, highlightthickness=0)
        self.frame._toggle = self  # type: ignore[attr-defined]
        self.lbl = tk.Label(self.frame, bd=0, highlightthickness=0, bg=self.bg, cursor="hand2", takefocus=1)
        self.lbl.grid(row=0, column=0)
        self.text_lbl = None
        widgets = [self.lbl]
        if text:
            self.text_lbl = tk.Label(self.frame, text=text, bd=0, bg=self.bg, fg=text_color, cursor="hand2",
                                     font=(app.fonts.family, app._font_px(13)))
            self.text_lbl.grid(row=0, column=1, padx=(app._scaled(8), 0))
            widgets.append(self.text_lbl)
        for wdg in widgets:
            wdg.bind("<Button-1>", self._click, add="+")
            wdg.bind("<Enter>", lambda _e: self._set_hover(True), add="+")
            wdg.bind("<Leave>", lambda _e: self._set_hover(False), add="+")
        self.lbl.bind("<space>", self._click, add="+")
        self.lbl.bind("<Return>", self._click, add="+")
        try:
            self._trace = variable.trace_add("write", lambda *_a: self.draw())
        except Exception:
            self._trace = None
        self.draw()

    # CTk-like geometry API (padding in logical px, scaled like CustomTkinter does)
    def _scale_kw(self, kw: dict) -> dict:
        s = self.app._scaled
        for k in ("padx", "pady"):
            v = kw.get(k)
            if isinstance(v, (tuple, list)):
                kw[k] = tuple(s(int(x)) for x in v)
            elif isinstance(v, (int, float)):
                kw[k] = s(int(v))
        return kw

    def grid(self, **kw: Any) -> None:
        self.frame.grid(**self._scale_kw(kw))

    def grid_remove(self) -> None:
        self.frame.grid_remove()

    def pack(self, **kw: Any) -> None:
        self.frame.pack(**self._scale_kw(kw))

    def winfo_reqwidth(self) -> int:
        return int(self.frame.winfo_reqwidth())

    def winfo_children(self) -> list:
        return []

    def bind(self, *a: Any, **kw: Any) -> None:
        self.lbl.bind(*a, **kw)

    def get(self) -> bool:
        return bool(self.var.get())

    def configure(self, **kw: Any) -> None:
        if "state" in kw:
            self.state = str(kw["state"])
            cur = "arrow" if self.state == "disabled" else "hand2"
            self.lbl.configure(cursor=cur)
            if self.text_lbl is not None:
                self.text_lbl.configure(cursor=cur, fg=DIM if self.state == "disabled" else MUTED)
            self.draw()

    def cget(self, name: str) -> Any:
        return self.state if name == "state" else None

    def _set_hover(self, on: bool) -> None:
        if on != self._hover:
            self._hover = on
            self.draw()

    def _click(self, _e: Any = None) -> str:
        if self.state == "disabled":
            return "break"
        try:
            self.lbl.focus_set()
        except Exception:
            pass
        self.var.set(not bool(self.var.get()))
        if self.command is not None:
            self.command()
        return "break"

    def draw(self) -> None:
        try:
            on = bool(self.var.get())
        except Exception:
            on = False
        w, h = self.app._scaled(self.size[0]), self.app._scaled(self.size[1])
        sig = (w, h, on, self._hover, self.state, self.color)
        if sig == self._sig:
            return
        self._sig = sig
        try:
            from PIL import ImageTk  # noqa: PLC0415

            img = toggle_image(w, h, on, self.color, self.bg, self._hover and self.state != "disabled",
                               self.state == "disabled")
            self._photo = ImageTk.PhotoImage(img, master=self.lbl)
            self.lbl.configure(image=self._photo)
        except Exception:
            log.debug("toggle draw failed", exc_info=True)


# ======================================================================================
# Plain Tk widgets for static layout (8x cheaper to create than CTkFrame / CTkLabel)
# ======================================================================================
#: Current widget scaling (ui_scale x Windows DPI factor) applied to the plain widgets below.
_PLAIN_SCALE = [1.0]
_PLAIN: list[Any] = [None, None]           # (PFrame, PLabel) classes, created with the first window
_font_tuples: dict[tuple, Any] = {}


def _sc(v: Any) -> Any:
    """Scale a logical length (int or (a, b) padding) to physical pixels."""
    k = _PLAIN_SCALE[0]
    if isinstance(v, (tuple, list)):
        return tuple(int(round(float(x) * k)) for x in v)
    if isinstance(v, (int, float)):
        return int(round(float(v) * k))
    return v


def _font_tuple(font: Any) -> Any:
    """A CTkFont at the current scaling, as a Tk font tuple (CTkFont objects are unscaled)."""
    fn = getattr(font, "create_scaled_tuple", None)
    if fn is None:
        return font
    key = (id(font), _PLAIN_SCALE[0])
    t = _font_tuples.get(key)
    if t is None:
        t = fn(_PLAIN_SCALE[0])
        _font_tuples[key] = t
    return t


def _plain_classes() -> tuple[Any, Any]:
    """(PFrame, PLabel): tk.Frame / tk.Label with CustomTkinter-like options (``fg_color``,
    ``text_color``, logical ``wraplength`` / padding scaled like CTk widgets, rescaled on a DPI change)."""
    if _PLAIN[0] is not None:
        return _PLAIN[0], _PLAIN[1]
    import tkinter as tk  # noqa: PLC0415
    import weakref  # noqa: PLC0415

    live: "weakref.WeakSet[Any]" = weakref.WeakSet()

    class _Scaled:
        _grid_kw: dict | None = None

        def grid(self, **kw: Any) -> None:  # type: ignore[override]
            self._grid_kw = {**(self._grid_kw or {}), **kw}
            out = dict(kw)
            for k in ("padx", "pady", "ipadx", "ipady"):
                if k in out:
                    out[k] = _sc(out[k])
            tk.Grid.grid_configure(self, **out)  # type: ignore[arg-type]

        grid_configure = grid

        def _rescale(self) -> None:
            if self._grid_kw and self.winfo_manager() == "grid":
                self.grid(**self._grid_kw)

    class PFrame(_Scaled, tk.Frame):
        def __init__(self, master: Any, fg_color: str | None = None, width: int = 0, height: int = 0,
                     **kw: Any) -> None:
            bg = fg_color if fg_color and fg_color != "transparent" else _widget_bg(master)
            tk.Frame.__init__(self, master, bg=bg, bd=0, highlightthickness=0, width=_sc(width),
                              height=_sc(height), **kw)
            live.add(self)

        def configure(self, **kw: Any) -> Any:  # type: ignore[override]
            fg = kw.pop("fg_color", None)
            if fg:
                kw["bg"] = _widget_bg(self.master) if fg == "transparent" else fg
            for k in ("width", "height"):
                if k in kw:
                    kw[k] = _sc(kw[k])
            kw.pop("border_color", None)
            return tk.Frame.configure(self, **kw) if kw else None

        def cget(self, key: str) -> Any:  # type: ignore[override]
            return tk.Frame.cget(self, "bg" if key == "fg_color" else key)

    class PLabel(_Scaled, tk.Label):
        def __init__(self, master: Any, text: str = "", font: Any = None, text_color: str = TEXT,
                     wraplength: int = 0, fg_color: str | None = None, **kw: Any) -> None:
            self._font_src = font
            self._wrap = int(wraplength or 0)
            bg = fg_color if fg_color and fg_color != "transparent" else _widget_bg(master)
            kw.pop("height", None)
            kw.setdefault("anchor", "w")
            kw.setdefault("justify", "left")
            tk.Label.__init__(self, master, text=text, fg=text_color, bg=bg, bd=0, padx=0, pady=0,
                              highlightthickness=0, font=_font_tuple(font), wraplength=_sc(self._wrap), **kw)
            live.add(self)

        def configure(self, **kw: Any) -> Any:  # type: ignore[override]
            if "text_color" in kw:
                kw["fg"] = kw.pop("text_color")
            fg = kw.pop("fg_color", None)
            if fg and fg != "transparent":
                kw["bg"] = fg
            if "wraplength" in kw:
                self._wrap = int(kw["wraplength"] or 0)
                kw["wraplength"] = _sc(self._wrap)
            if "font" in kw:
                self._font_src = kw["font"]
                kw["font"] = _font_tuple(kw["font"])
            kw.pop("height", None)
            return tk.Label.configure(self, **kw) if kw else None

        config = configure

        def cget(self, key: str) -> Any:  # type: ignore[override]
            if key == "text_color":
                key = "fg"
            elif key == "fg_color":
                key = "bg"
            elif key == "wraplength":
                return self._wrap
            return tk.Label.cget(self, key)

        def _rescale(self) -> None:
            tk.Label.configure(self, font=_font_tuple(self._font_src), wraplength=_sc(self._wrap))
            _Scaled._rescale(self)

    def rescale_all() -> None:
        for w in list(live):
            try:
                if w.winfo_exists():
                    w._rescale()
            except Exception:
                pass

    PFrame.rescale_all = staticmethod(rescale_all)  # type: ignore[attr-defined]
    _PLAIN[0], _PLAIN[1] = PFrame, PLabel
    return PFrame, PLabel


class Dropdown:
    """Light option menu (CTkOptionMenu API subset: ``set`` / ``get`` / ``configure(values=, state=)``).

    A rounded CTkFrame with two plain labels (value + chevron); the Tk menu is created on the first
    click. About 5x cheaper to build than a CTkOptionMenu (which builds its menu and redraws eagerly).
    """

    def __init__(self, app: "CoachApp", parent: Any, values: Sequence[str], command: Callable[[str], Any] | None = None,
                 width: int = 240, height: int = CTL_H, font: Any = None) -> None:
        ctk = app.ctk
        self.app, self.values, self.command = app, [str(v) for v in values], command
        self._value = self.values[0] if self.values else ""
        self.state = "normal"
        self._menu: Any = None
        self._font = font or app.fonts.small
        self.frame = ctk.CTkFrame(parent, width=width, height=height, fg_color=RAISED, border_width=1,
                                  border_color=LINE_STRONG, corner_radius=RADIUS)
        self.frame.grid_propagate(False)
        self.frame.grid_columnconfigure(0, weight=1)
        self.frame.grid_rowconfigure(0, weight=1)
        self.frame._dropdown = self  # type: ignore[attr-defined]
        self.lbl = app._PLabel(self.frame, text=self._value, font=self._font, text_color=TEXT)
        self.lbl.grid(row=0, column=0, sticky="w", padx=(12, 4))
        self.chev = app._PLabel(self.frame, text="▾", font=self._font, text_color=MUTED)
        self.chev.grid(row=0, column=1, sticky="e", padx=(0, 12))
        for w in (self.frame, self.lbl, self.chev):
            w.bind("<Button-1>", self._open, add="+")
            w.bind("<Enter>", lambda _e: self._hover(True), add="+")
            w.bind("<Leave>", lambda _e: self._hover(False), add="+")
        for w in (self.lbl, self.chev):
            w.configure(cursor="hand2")

    def grid(self, **kw: Any) -> None:
        self.frame.grid(**kw)

    def grid_remove(self) -> None:
        self.frame.grid_remove()

    def winfo_reqwidth(self) -> int:
        return int(self.frame.winfo_reqwidth())

    def _hover(self, on: bool) -> None:
        if self.state == "disabled":
            return
        col = HOVER if on else RAISED
        try:
            self.frame.configure(fg_color=col)
            self.lbl.configure(fg_color=col)
            self.chev.configure(fg_color=col)
        except Exception:
            pass

    def set(self, value: str) -> None:
        self._value = str(value)
        self.lbl.configure(text=self._value)

    def get(self) -> str:
        return self._value

    def cget(self, name: str) -> Any:
        return {"values": list(self.values), "state": self.state}.get(name)

    def configure(self, **kw: Any) -> None:
        if "values" in kw:
            self.values = [str(v) for v in kw["values"]]
            self._menu = None
        if "command" in kw:
            self.command = kw["command"]
        if "state" in kw:
            self.state = str(kw["state"])
            dis = self.state == "disabled"
            self.lbl.configure(text_color=DIM if dis else TEXT)
            self.chev.configure(text_color=DIM if dis else MUTED)
            for w in (self.lbl, self.chev):
                w.configure(cursor="arrow" if dis else "hand2")

    def _pick(self, value: str) -> None:
        self.set(value)
        if self.command is not None:
            self.command(value)

    def _open(self, _e: Any = None) -> str:
        if self.state == "disabled" or not self.values:
            return "break"
        try:
            import tkinter as tk  # noqa: PLC0415

            if self._menu is None:
                m = tk.Menu(self.frame, tearoff=0, bg=RAISED, fg=TEXT, activebackground=ACCENT_DIM,
                            activeforeground=TEXT, bd=1, relief="flat", font=_font_tuple(self._font))
                for v in self.values:
                    m.add_command(label="  " + v + "    ", command=lambda vv=v: self._pick(vv))
                self._menu = m
            x = self.frame.winfo_rootx()
            y = self.frame.winfo_rooty() + self.frame.winfo_height() + 2
            self._menu.tk_popup(x, y)
        except Exception:
            log.debug("dropdown failed", exc_info=True)
        finally:
            try:
                if self._menu is not None:
                    self._menu.grab_release()
            except Exception:
                pass
        return "break"


class Segmented:
    """Light segmented choice (CTkSegmentedButton API subset: ``set`` / ``get`` / ``configure(state=)``).

    One rounded CTkFrame holding plain labels; the selected segment is filled with the accent tint.
    """

    def __init__(self, app: "CoachApp", parent: Any, values: Sequence[str], command: Callable[[str], Any] | None = None,
                 height: int = CTL_H, font: Any = None) -> None:
        ctk = app.ctk
        self.app, self.values, self.command = app, [str(v) for v in values], command
        self._value = ""
        self.state = "normal"
        self.frame = ctk.CTkFrame(parent, height=height, fg_color=SUNKEN, border_width=1, border_color=LINE_STRONG,
                                  corner_radius=RADIUS)
        self.frame._dropdown = self  # type: ignore[attr-defined]
        self.frame.grid_rowconfigure(0, weight=1)
        self._cells: dict[str, Any] = {}
        font = font or app.fonts.small
        for i, v in enumerate(self.values):
            lbl = app._PLabel(self.frame, text=v.strip(), font=font, text_color=MUTED, anchor="center",
                              fg_color=SUNKEN)
            lbl.configure(padx=_sc(14), pady=_sc(6), cursor="hand2")
            lbl.grid(row=0, column=i, sticky="nsew", padx=(3 if i == 0 else 1, 3 if i == len(self.values) - 1 else 0),
                     pady=3)
            lbl.bind("<Button-1>", lambda _e, vv=v: self._click(vv), add="+")
            lbl.bind("<Enter>", lambda _e, vv=v: self._hover(vv, True), add="+")
            lbl.bind("<Leave>", lambda _e, vv=v: self._hover(vv, False), add="+")
            self._cells[v] = lbl

    def grid(self, **kw: Any) -> None:
        self.frame.grid(**kw)

    def grid_remove(self) -> None:
        self.frame.grid_remove()

    def winfo_reqwidth(self) -> int:
        return int(self.frame.winfo_reqwidth())

    def _paint(self, v: str, hover: bool = False) -> None:
        lbl = self._cells.get(v)
        if lbl is None:
            return
        on = v == self._value
        dis = self.state == "disabled"
        bg = ACCENT_DIM if on else (RAISED if hover and not dis else SUNKEN)
        fg = DIM if dis else (TEXT if on else MUTED)
        lbl.configure(fg_color=bg, text_color=fg)

    def _hover(self, v: str, on: bool) -> None:
        self._paint(v, on)

    def _click(self, v: str) -> None:
        if self.state == "disabled":
            return
        self.set(v)
        if self.command is not None:
            self.command(v)

    def set(self, value: str) -> None:
        old, self._value = self._value, str(value)
        for v in (old, self._value):
            self._paint(v)

    def get(self) -> str:
        return self._value

    def cget(self, name: str) -> Any:
        return {"values": list(self.values), "state": self.state}.get(name)

    def configure(self, **kw: Any) -> None:
        if "command" in kw:
            self.command = kw["command"]
        if "state" in kw:
            self.state = str(kw["state"])
            for v in self.values:
                self._paint(v)


_SCROLL_CLS: list[Any] = [None]


def scroll_frame_class() -> Any:
    """:class:`ScrollFrame`: a light vertical scrolling container (replaces CTkScrollableFrame).

    One canvas + one thin canvas scrollbar: no CTk drawing, and above all no ``update_idletasks``
    inside the scrollbar (CTkScrollbar flushes every pending layout each time the content height
    changes: the page was drawn half laid out, then again). The gutter is always reserved, so
    the content never shifts when the scrollbar appears or disappears.

    The object IS the inner frame (children go in it, like CTkScrollableFrame); ``grid`` /
    ``grid_remove`` / ``destroy`` act on the outer frame; ``_parent_canvas`` is the canvas.
    """
    if _SCROLL_CLS[0] is not None:
        return _SCROLL_CLS[0]
    import tkinter as tk  # noqa: PLC0415

    class ScrollFrame(tk.Frame):
        BAR_W = 10                      # logical px (gutter); the thumb is 6 px wide in it
        WHEEL_PX = 60                   # logical px per wheel notch

        def __init__(self, master: Any, fg_color: str | None = None, height: int = 0, width: int = 0,
                     thumb: str = SWITCH_OFF, thumb_hover: str = LINE_STRONG, **_kw: Any) -> None:
            bg = fg_color if fg_color and fg_color != "transparent" else _widget_bg(master)
            self._bg, self._thumb, self._thumb_hover = bg, thumb, thumb_hover
            self._dead = False
            outer = tk.Frame(master, bg=bg, bd=0, highlightthickness=0)
            outer.grid_columnconfigure(0, weight=1)
            outer.grid_rowconfigure(0, weight=1)
            self._outer = outer
            cv = tk.Canvas(outer, bg=bg, bd=0, highlightthickness=0, width=_sc(width) or 1,
                           height=_sc(height) or 1, yscrollincrement=1)
            cv.grid(row=0, column=0, sticky="nsew")
            bar = tk.Canvas(outer, bg=bg, bd=0, highlightthickness=0, width=_sc(self.BAR_W), height=1)
            bar.grid(row=0, column=1, sticky="ns")
            self._parent_canvas, self._bar = cv, bar
            tk.Frame.__init__(self, cv, bg=bg, bd=0, highlightthickness=0)
            self._win = cv.create_window(0, 0, window=self, anchor="nw")
            self._thumb_id = bar.create_line(0, 0, 0, 0, fill=thumb, width=_sc(6), capstyle="round",
                                             state="hidden")
            self._span = (0.0, 1.0)
            self._drag: tuple[int, float] | None = None
            cv.configure(yscrollcommand=self._on_scroll)
            cv._tree_scroll = self  # type: ignore[attr-defined]
            outer._tree_scroll = self  # type: ignore[attr-defined]
            self._tree_scroll = self
            self.bind("<Configure>", self._on_inner, add="+")
            cv.bind("<Configure>", self._on_canvas, add="+")
            bar.bind("<Configure>", lambda _e: self._draw_bar(), add="+")
            bar.bind("<Button-1>", self._bar_press, add="+")
            bar.bind("<B1-Motion>", self._bar_drag, add="+")
            bar.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_drag", None), add="+")
            bar.bind("<Enter>", lambda _e: bar.itemconfigure(self._thumb_id, fill=self._thumb_hover), add="+")
            bar.bind("<Leave>", lambda _e: bar.itemconfigure(self._thumb_id, fill=self._thumb), add="+")
            _install_wheel(self)

        # ---- geometry: the outer frame is the one laid out in the parent
        def grid(self, **kw: Any) -> None:  # type: ignore[override]
            self._outer.grid(**kw)

        grid_configure = grid

        def grid_remove(self) -> None:  # type: ignore[override]
            self._outer.grid_remove()

        def grid_forget(self) -> None:  # type: ignore[override]
            self._outer.grid_forget()

        def grid_info(self) -> Any:  # type: ignore[override]
            return self._outer.grid_info()

        def pack(self, **kw: Any) -> None:  # type: ignore[override]
            self._outer.pack(**kw)

        def place(self, **kw: Any) -> None:  # type: ignore[override]
            self._outer.place(**kw)

        def winfo_manager(self) -> str:  # type: ignore[override]
            return self._outer.winfo_manager()

        def destroy(self) -> None:  # type: ignore[override]
            if self._dead:
                return
            self._dead = True
            try:
                self._outer.destroy()
            except Exception:
                pass

        def cget(self, key: str) -> Any:  # type: ignore[override]
            return tk.Frame.cget(self, "bg" if key == "fg_color" else key)

        def configure(self, **kw: Any) -> Any:  # type: ignore[override]
            fg = kw.pop("fg_color", None)
            if fg and fg != "transparent":
                kw["bg"] = fg
                for w in (self._outer, self._parent_canvas, self._bar):
                    w.configure(bg=fg)
            for k in ("corner_radius", "border_width", "border_color", "scrollbar_button_color",
                      "scrollbar_button_hover_color", "label_fg_color"):
                kw.pop(k, None)
            if "height" in kw:
                self._parent_canvas.configure(height=_sc(kw.pop("height")))
            return tk.Frame.configure(self, **kw) if kw else None

        config = configure

        # ---- scrolling
        def _on_inner(self, _e: Any = None) -> None:
            cv = self._parent_canvas
            h = max(1, self.winfo_reqheight(), self.winfo_height())
            cv.configure(scrollregion=(0, 0, max(1, cv.winfo_width()), h))

        def _on_canvas(self, e: Any) -> None:
            self._parent_canvas.itemconfigure(self._win, width=e.width)
            self._on_inner()

        def _on_scroll(self, lo: str, hi: str) -> None:
            self._span = (float(lo), float(hi))
            self._draw_bar()

        def _draw_bar(self) -> None:
            lo, hi = self._span
            bar = self._bar
            try:
                if hi - lo >= 0.999:
                    bar.itemconfigure(self._thumb_id, state="hidden")
                    return
                h = max(1, bar.winfo_height())
                r = _sc(3)
                x = _sc(self.BAR_W) / 2
                y0 = lo * h + r + 1
                y1 = max(y0 + _sc(12), hi * h - r - 1)
                bar.coords(self._thumb_id, x, y0, x, y1)
                bar.itemconfigure(self._thumb_id, state="normal")
            except Exception:
                pass

        def scrollable(self) -> bool:
            return self._span[1] - self._span[0] < 0.999

        def scroll_px(self, px: int) -> None:
            if self.scrollable():
                self._parent_canvas.yview_scroll(int(px), "units")

        def _bar_press(self, e: Any) -> None:
            lo, hi = self._span
            h = max(1, self._bar.winfo_height())
            f = e.y / h
            if lo <= f <= hi:
                self._drag = (e.y, lo)
            else:                       # click in the trough: one page up / down
                self._drag = None
                self._parent_canvas.yview_scroll(-1 if f < lo else 1, "pages")

        def _bar_drag(self, e: Any) -> None:
            if self._drag is None:
                return
            y0, lo0 = self._drag
            h = max(1, self._bar.winfo_height())
            self._parent_canvas.yview_moveto(max(0.0, lo0 + (e.y - y0) / h))

    _SCROLL_CLS[0] = ScrollFrame
    return ScrollFrame


def _install_wheel(sf: Any) -> None:
    """One global mouse-wheel binding: the innermost :class:`ScrollFrame` under the pointer scrolls
    (a text box scrolls itself)."""
    root = sf.winfo_toplevel()
    if getattr(root, "_tree_wheel", False):
        return
    root._tree_wheel = True

    def wheel(e: Any) -> None:
        w = e.widget
        if isinstance(w, str):
            try:
                w = root.nametowidget(w)
            except Exception:
                return
        if w.winfo_class() in ("Text", "Listbox", "Menu"):
            return
        if getattr(e, "num", None) == 4:
            notches = 1.0
        elif getattr(e, "num", None) == 5:
            notches = -1.0
        else:
            notches = float(getattr(e, "delta", 0) or 0) / (120.0 if sys.platform.startswith("win") else 1.0)
        while w is not None:
            owner = getattr(w, "_tree_scroll", None)
            if owner is not None and owner.scrollable():
                owner.scroll_px(-notches * _sc(owner.WHEEL_PX))
                return
            w = getattr(w, "master", None)

    try:
        root.bind_all("<MouseWheel>", wheel, add="+")
        if not sys.platform.startswith("win"):
            root.bind_all("<Button-4>", wheel, add="+")
            root.bind_all("<Button-5>", wheel, add="+")
    except Exception:
        log.debug("wheel binding failed", exc_info=True)


class _LazyPages(dict):
    """``{page key: frame}`` whose missing pages are built on first access (``pages["help"]``,
    ``pages.get("help")``); ``key in pages`` is true for every known page."""

    def __init__(self, build: Callable[[str], Any], builders: dict[str, Any]) -> None:
        super().__init__()
        self._build, self._builders = build, builders

    def __missing__(self, key: str) -> Any:
        if key not in self._builders:
            raise KeyError(key)
        return self._build(key)

    def get(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key: object) -> bool:
        return key in self._builders


def _p(d: Any, key: str) -> Any:
    v = d.get(key) if isinstance(d, dict) else None
    return v if isinstance(v, (int, float)) else None


def health_text(h: Any) -> tuple[str, int]:
    """One line for the dashboard from ``engine.health()``: (text, level 0 ok / 1 warning / 2 problem).
    Pure, never raises; "" outside a game."""
    if not isinstance(h, dict) or not h:
        return "", 0
    try:
        bits: list[str] = []
        level = 0
        fps = _p(h, "capture_fps")
        backend = str(h.get("capture_backend") or "")
        if backend or fps is not None:
            bits.append(f"Capture {backend} {fmt_decimal_fr(fps, 0) + ' i/s' if fps is not None else ''}".strip())
        det = h.get("detect_ms") if isinstance(h.get("detect_ms"), dict) else {}
        p50, p95 = _p(det, "p50"), _p(det, "p95")
        if p50 is not None:
            bits.append(f"détection {p50:.0f}/{p95:.0f} ms" if p95 is not None else f"détection {p50:.0f} ms")
            if p95 is not None and p95 > 120:
                level = max(level, 1)
        ov = h.get("overlay") if isinstance(h.get("overlay"), dict) else {}
        ofps = _p(ov, "fps")
        if ofps is not None:
            bits.append(f"overlay {ofps:.0f} i/s")
        seen, exp = h.get("champions_seen"), h.get("champions_expected")
        if isinstance(seen, int) and isinstance(exp, int) and exp:
            bits.append(f"champions {seen}/{exp}")
        cpu = _p(h, "cpu_percent")
        if cpu is not None:
            bits.append(f"CPU {cpu:.0f} %")
            if cpu > 60:
                level = max(level, 1)
        warn = h.get("warnings") or h.get("capture_note")
        if isinstance(warn, (list, tuple)):
            warn = " · ".join(str(w) for w in warn if w)
        if warn:
            bits.append(str(warn))
            level = max(level, 1)
        if h.get("capture_status") in ("black", "error", "failed"):
            level = 2
        return ui_text(" · ".join(b for b in bits if b)), level
    except Exception:
        return "", 0


def ui_scale_of(cfg: Any) -> float:
    """Widget scaling factor from the configuration (Windows display scaling comes on top).

    ``ui_scaling`` ("auto" or a percentage) wins; "auto" uses ``ui_scale`` where the old compact
    default (0.88, never chosen in the UI) now means 1.0: text was too small."""
    pct = str(getattr(cfg, "ui_scaling", "auto") or "auto")
    if pct != "auto":
        try:
            return min(1.5, max(0.8, int(pct) / 100))
        except ValueError:
            pass
    try:
        v = float(getattr(cfg, "ui_scale", 1.0))
    except (TypeError, ValueError):
        v = 1.0
    if abs(v - 0.88) < 1e-6:
        v = 1.0
    return min(1.4, max(0.8, v))


# ======================================================================================
# Module-level helpers used by the app
# ======================================================================================
_DETECTOR_FR = {"onnx": "ONNX", "classic": "Classique", "none": "Aucun", "auto": "Auto"}


def detector_short(name: Any) -> str:
    """Short label of the detector backend for the dashboard tile ("roster+onnx" -> "ONNX")."""
    d = str(name or "").strip().lower()
    if not d or d == "-":
        return "-"
    if "onnx" in d:
        return "ONNX"
    if "classic" in d:
        return "Classique"
    return _DETECTOR_FR.get(d, str(name))[:9]
_VOICE_FR = {"sapi": "SAPI", "onecore": "Windows", "neural": "Neurale", "print": "Journal", "": "-"}
ROLE_GAMER = ui_kit.ROLE_GAMER
_champ_names: dict[str, str] = {}


def champion_name(alias: Any) -> str:
    """Display name of a champion alias ("MonkeyKing" -> "Wukong"), cached; the alias when unknown."""
    a = str(alias or "").strip()
    if not a:
        return ""
    name = _champ_names.get(a)
    if name is None:
        name = a
        try:
            from treeaicoach.champions import get_default_db  # noqa: PLC0415

            entry = get_default_db().get(a)
            name = str(getattr(entry, "name_fr", "") or getattr(entry, "name_en", "") or a)
        except Exception:
            log.debug("champion name unavailable for %s", a, exc_info=True)
        _champ_names[a] = name
    return name
_ALERT_KINDS = frozenset({"jungler_approach", "roam_approach", "collapse", "jungler_spotted", "laner_mia",
                          "objective_soon", "recall_gold", "control_ward", "jungler_where", "death_recap"})
_POSITION_FR = {"TOP": "Haut", "JUNGLE": "Jungle", "MIDDLE": "Milieu", "BOTTOM": "Bas", "UTILITY": "Support"}


def ui_text(text: Any) -> str:
    """User-facing text cleaned for the design rules: no em dash (docs/DESIGN.md).

    " — " becomes " · " and a lone "-" (missing value) becomes "-". Strings coming from other
    modules (engine, coach...) go through this before being shown.
    """
    t = str(text if text is not None else "")
    if EM_DASH in t:
        t = (t.replace(f" {EM_DASH} ", " · ").replace(f"{EM_DASH} ", "· ").replace(f" {EM_DASH}", " ·")
             .replace(EM_DASH, "-"))
    return t


def _fmt_ago(s: float) -> str:
    s = max(0, int(s))
    return f"{s} s" if s < 60 else f"{s // 60}:{s % 60:02d}"


def _ellipsize(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _blend(c1: str, c2: str, t: float) -> str:
    a, b = _hex_rgb(c1), _hex_rgb(c2)
    t = min(max(t, 0.0), 1.0)
    return "#%02X%02X%02X" % tuple(int(round(x * (1 - t) + y * t)) for x, y in zip(a, b))


def _alert_entry(a: Any) -> tuple[float | None, int, str] | None:
    """(game_time, level, text) from an Alert-like object / tuple / dict."""
    try:
        if isinstance(a, dict):
            return (a.get("game_time"), int(a.get("level", 1) or 0), str(a.get("text", "")))
        if isinstance(a, (tuple, list)) and len(a) >= 3:
            gt = a[0] if isinstance(a[0], (int, float)) and not isinstance(a[0], bool) else None
            if len(a) >= 4:
                # engine: (game_time, text, level, kind) ; recorder: [game_time, kind, level, text]
                text = a[3] if str(a[1]).lower() in _ALERT_KINDS and str(a[3]).lower() not in _ALERT_KINDS \
                    else a[1]
                return (gt, int(a[2] or 0), str(text))
            return (gt, int(a[1] or 0), str(a[2]))
        text = getattr(a, "text", None)
        if text:
            return (getattr(a, "game_time", None), int(getattr(a, "level", 1) or 0), str(text))
    except Exception:
        return None
    return None


def _example_phrases() -> dict[str, str]:
    """Example sentences of each alert kind (from alerts.phrase, so they match the voice)."""
    out = {
        "jungler_approach": "« Attention, Lee Sin approche. »",
        "roam_approach": "« Attention, Ahri arrive. »",
        "collapse": "« Danger, 3 ennemis arrivent, recule ! »",
        "jungler_spotted": "« Jungler ennemi vu en haut. »",
        "laner_mia": "« Darius a disparu. »",
        "objective_soon": "« Dragon dans une minute. »",
        "recall_gold": "« 1 400 pièces d'or, pense à rentrer. »",
        "control_ward": "« Pense à acheter une balise de contrôle. »",
        "death_recap": "Après ta mort : « Mort face à 2 ennemis, dont le jungler. »",
    }
    try:
        from treeaicoach.alerts import AlertKind, Level, phrase  # noqa: PLC0415

        spec = {
            "jungler_approach": (AlertKind.JUNGLER_APPROACH, Level.WARNING, "Lee Sin", None, 0),
            "roam_approach": (AlertKind.ROAM_APPROACH, Level.WARNING, "Ahri", None, 0),
            "collapse": (AlertKind.COLLAPSE, Level.DANGER, None, None, 3),
            "jungler_spotted": (AlertKind.JUNGLER_SPOTTED, Level.INFO, "Lee Sin", "en haut", 0),
            "laner_mia": (AlertKind.LANER_MIA, Level.INFO, "Darius", None, 0),
            "objective_soon": (AlertKind.OBJECTIVE_SOON, Level.INFO, "Dragon", None, 60),
            "recall_gold": (AlertKind.RECALL_GOLD, Level.INFO, None, None, 1400),
            "control_ward": (AlertKind.CONTROL_WARD, Level.INFO, None, None, 0),
        }
        for key, (kind, lvl, champ, zone, count) in spec.items():
            txt = phrase(kind, lvl, champ, zone, count)
            if txt:
                out[key] = f"« {txt} »"
    except Exception:
        log.debug("Example phrases unavailable", exc_info=True)
    return out


def _pct_value(v: Any) -> float:
    """``"+15%"`` -> 15.0 (0.0 if unreadable)."""
    try:
        return float(str(v).strip().rstrip("%").strip())
    except (TypeError, ValueError):
        return 0.0


def _example_speech() -> dict[str, tuple[str, int]]:
    """(sentence, level) spoken by "Entendre un exemple" for each alert kind."""
    out: dict[str, tuple[str, int]] = {}
    for key, txt in _example_phrases().items():
        t = txt.split(":", 1)[1] if key == "death_recap" and ":" in txt else txt
        out[key] = (t.replace("«", "").replace("»", "").strip(), 2 if key == "collapse" else
                    (1 if key in ("jungler_approach", "roam_approach") else 0))
    return out


def _report_function(name: str) -> Callable[..., Any] | None:
    """``list_games`` / ``write_report``, resolved through :mod:`treeaicoach.ui` at call time (tests patch
    ``ui._report_function``; imported lazily: ui imports this module)."""
    from treeaicoach import ui  # noqa: PLC0415 - circular at import time

    return ui._report_function(name)


def _game_json_path(game: dict) -> Path | None:
    for k in ("path", "json_path", "record_path", "file"):
        v = game.get(k)
        if isinstance(v, (str, Path)) and str(v):
            return Path(v)
    return None


def _game_html_path(game: dict, src: Path | None) -> Path | None:
    for k in ("html_path", "report_path", "html", "report"):
        v = game.get(k)
        if isinstance(v, (str, Path)) and str(v).lower().endswith(".html"):
            return Path(v)
    if src is not None:
        return src.with_suffix(".html")
    return None
