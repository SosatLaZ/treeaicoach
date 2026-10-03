"""Toolkit-free parts of the launcher: tokens kept for the in-game drawings and the reports,
choice lists, the settings inventory (HIDDEN_SETTINGS), icons drawn with PIL, the worker
dispatcher and the pure formatting helpers. No GUI toolkit is imported here (tests run headless);
the Qt widgets live in :mod:`treeaicoach.ui_widgets`.
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

PREBUILD_ORDER = ("alerts", "overlay", "settings", "analysis", "about")   # built after the first paint
GAMES_PAGE = 15                  # rows of the Analyses table drawn at once ("Afficher plus")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "TreeAICoach"

#: Sidebar navigation: 4 pages. Everything the player can set lives in ONE page (Réglages), in tabs
#: named after what they change (docs/DESIGN.md, "Navigation").
PAGES: tuple[tuple[str, str, str], ...] = (
    ("home", "Accueil", "home"),
    ("overlay", "Overlay", "overlay"),
    ("alerts", "Alertes et voix", "alerts"),
    ("analysis", "Analyse", "analysis"),
    ("settings", "Réglages", "settings"),
    ("about", "À propos", "about"),
)
#: Old page / tab names (tools, fixes, saved links) -> new page.
PAGE_ALIASES: dict[str, str] = {"dashboard": "home", "help": "about", "updates": "about", "Mises à jour": "about",
                                "Affichage": "overlay", "Voix": "alerts", "Général": "settings",
                                "Détection": "settings", "IA": "settings", "Avancé": "settings"}
#: Status of the launcher.
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
    "skill_level": "Accueil > Ton niveau",
    "safe_mode": "Accueil > Mode sûr (Ctrl+Maj+S)",
    "ui_geometry": "position de la fenêtre, mémorisée",
    "ui_scale": "ancien réglage, remplacé par « Taille de l'interface »",
    "ui_onboarding_done": "suivi du mode guidé",
    "ui_seen_changelog": "suivi des nouveautés",
    "update_channel_url": "réglage de développeur",
    "diag_duration_s": "réglage de développeur",
    "diag_interval_s": "réglage de développeur",
    "selfcheck_enabled": "auto-diagnostic (selfcheck.py) : réglage de développeur",
}



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
