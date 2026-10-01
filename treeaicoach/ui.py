"""CustomTkinter user interface of TreeAI Coach (ARCHITECTURE.md §8.2) - dark "hextech" theme, French.

Public entry point: :func:`run_app`. It creates the voice, the detector, the analysis engine
(:mod:`treeaicoach.engine`, imported lazily) and the overlay manager, shows the main window and
returns 0 when the window is closed (everything stopped, configuration saved).

Threading rules
    * Widgets are only touched from the Tk main thread. Other threads (engine start/stop,
      report generation, radar rendering, hotkeys...) hand their results over through
      :class:`_Dispatcher` (a queue polled by ``root.after``).
    * The dashboard reads **thread-safe snapshots** only (``engine.get_status()``,
      ``engine.get_overlay_state()``) every 250 ms; the radar preview is rendered at 5 Hz by a
      background thread and pasted into a ``PhotoImage`` by the main thread (200 ms loop).
    * Every user callback is wrapped (:func:`_guarded` / :meth:`CoachApp.cb`): an exception is
      logged and shown as a French toast; the app never crashes.

Every setting change -> ``cfg`` updated + validated, saved after 500 ms (debounced) and applied
live (``engine.apply_config`` / ``overlay.apply_config`` / ``voice.set_params``, hotkeys rebound).
"""

from __future__ import annotations

import dataclasses
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
import webbrowser
from collections import deque
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np
from PIL import Image, ImageDraw

from treeaicoach import APP_NAME, __version__, paths, ui_kit
from treeaicoach.config import Config, save_config

log = logging.getLogger(__name__)

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
MUTED = "#8B948F"
DIM = "#59615C"
TEAL = ACCENT               # "active" = the accent too (one accent only)
TEAL_DARK = ACCENT_DIM
DANGER = "#E5484D"
DANGER_DARK = "#3A1618"
DANGER_HOVER = "#4E1D20"
ON_DANGER = "#FFD7D8"
WARNING = "#E8A23A"
WARNING_BG = "#2A2010"
SAFE = ACCENT               # semantic "ok" = the TreeAI green
ON_GOLD = ON_ACCENT
HOVER = "#212624"           # hover of raised elements
SWITCH_OFF = "#2A302D"      # switch / slider track
ALLY_RING = ui_kit.ALLY     # allied team ring (team colour, not an accent)
ENEMY_RING = ui_kit.ENEMY   # enemy team ring
TRACK = "#1C211F"           # empty gauge segment / slider track
RADIUS = 4                  # controls
EM_DASH = chr(0x2014)       # never shown (docs/DESIGN.md); used to parse / clean texts of other modules
RADIUS_DIALOG = 6

THREAT_COLORS = {0: SAFE, 1: WARNING, 2: DANGER}
#: "jouer plus fort ou non" gauge step -> colour; tip tone -> colour (dashboard coach strip)
GAUGE_UI_COLORS = {2: SAFE, 1: "#C3E79A", 0: MUTED, -1: WARNING, -2: DANGER}
TIP_UI_COLORS = {"danger": "#F2888B", "warning": "#F0BE6E", "go": "#C3E79A", "info": TEXT}
THREAT_LABELS = {0: "SÛR", 1: "ATTENTION", 2: "DANGER"}
LEVEL_COLORS = {0: TEXT, 1: WARNING, 2: DANGER}

SIDEBAR_W = 192
MIN_W, MIN_H = 980, 640
DEFAULT_W, DEFAULT_H = 1100, 720
RADAR_PX = 260
STATUS_MS = 250
PREVIEW_MS = 200
PULSE_MS = 70
DISPATCH_MS = 40
SAVE_DEBOUNCE_MS = 500
TOAST_MS = 4500
UPDATE_CHECK_DELAY_MS = 8000     # silent update check after launch (frozen exe only)
JOURNAL_MAX = 12

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "TreeAICoach"

PAGES: tuple[tuple[str, str, str], ...] = (
    ("dashboard", "En jeu", "dashboard"),
    ("alerts", "Alertes", "voice"),
    ("overlay", "Overlay", "overlay"),
    ("analysis", "Analyses", "analysis"),
    ("settings", "Réglages", "settings"),
    ("help", "Aide", "help"),
)

#: Engine state name -> (short French title, colour).
STATE_INFO: dict[str, tuple[str, str]] = {
    "STOPPED": ("Analyse arrêtée", DIM),
    "WAITING_GAME": ("En attente d'une partie", GOLD),
    "LOCATING": ("Recherche de la minimap", TEAL),
    "RUNNING": ("Analyse en cours", SAFE),
    "UNSUPPORTED_MODE": ("Mode de jeu non pris en charge", WARNING),
    "CAPTURE_BLACK": ("Capture noire", WARNING),
    "ERROR": ("Erreur", DANGER),
    "NO_ENGINE": ("Moteur indisponible", DANGER),
    "STARTING": ("Démarrage…", GOLD),
}
PILL_TEXT: dict[str, str] = {
    "STOPPED": "Arrêté", "WAITING_GAME": "En attente", "LOCATING": "Localisation",
    "RUNNING": "En partie", "UNSUPPORTED_MODE": "Mode non géré", "CAPTURE_BLACK": "Capture noire",
    "ERROR": "Erreur", "NO_ENGINE": "Indisponible", "STARTING": "Démarrage",
}

RADAR_POSITIONS: tuple[tuple[str, str], ...] = (
    ("above_minimap", "Au-dessus de la minimap"),
    ("left_of_minimap", "À gauche de la minimap"),
    ("top_left", "En haut à gauche"),
    ("custom", "Personnalisée"),
)
HUD_POSITIONS: tuple[tuple[str, str], ...] = (
    ("above_minimap", "Au-dessus de la minimap"),
    ("top_left", "En haut à gauche"),
    ("top_right", "En haut à droite"),
    ("left_middle", "Au milieu à gauche"),
    ("custom", "Personnalisée"),
)
FOG_MODES: tuple[tuple[str, str], ...] = (("jungler", "Jungler"), ("all", "Tous"), ("off", "Off"))
OVERLAY_MODES: tuple[tuple[str, str], ...] = (("minimap", "Sur la minimap"), ("radar", "Radar à côté"),
                                              ("off", "Aucun"))
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
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return "--:--"
    if not math.isfinite(s) or s < 0:
        return "--:--"
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


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
    """The app's CTkFont set (created once the root exists).

    Three sizes only (docs/DESIGN.md): display 17 (titles, Bahnschrift), body 11, caption 9;
    the big numbers (clock, stats) use the display face at 22. ``ui_scale`` scales them all.
    """

    def __init__(self, ctk: Any, family: str, display: str | None = None) -> None:
        f = family
        d = display or family
        dw = display_weight(d)
        self.family = f
        self.display = d
        self.brand = ctk.CTkFont(family=d, size=15, weight=dw)
        self.title = ctk.CTkFont(family=d, size=17, weight=dw)
        self.h2 = ctk.CTkFont(family=f, size=11, weight="bold")
        self.h3 = ctk.CTkFont(family=f, size=11, weight="bold")
        self.body = ctk.CTkFont(family=f, size=11)
        self.small = ctk.CTkFont(family=f, size=11)
        self.tiny = ctk.CTkFont(family=f, size=10)
        self.tiny_bold = ctk.CTkFont(family=f, size=10, weight="bold")
        self.caps = ctk.CTkFont(family=f, size=9, weight="bold")
        self.nav = ctk.CTkFont(family=f, size=11)
        self.nav_active = ctk.CTkFont(family=f, size=11, weight="bold")
        self.button = ctk.CTkFont(family=f, size=11, weight="bold")
        self.big_button = ctk.CTkFont(family=d, size=13, weight=dw)
        self.state = ctk.CTkFont(family=d, size=17, weight=dw)
        self.clock = ctk.CTkFont(family=d, size=22, weight=dw)
        self.stat = ctk.CTkFont(family=d, size=22, weight=dw)
        self.num = ctk.CTkFont(family=d, size=13, weight=dw)
        self.threat = ctk.CTkFont(family=d, size=13, weight=dw)


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
            changed = "text" in opts and self.canvas.itemcget(self.item, "text") != opts["text"]
            self.canvas.itemconfigure(self.item, **opts)
            if changed and self._on_change is not None:
                self._on_change()
        except Exception:
            pass

    def cget(self, name: str) -> Any:
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
        self.h = s(112)
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
                                        font=(disp, px(17), dw))
        self.msg_item = c.create_text(0, 0, anchor="nw", text="", fill=MUTED, font=(fam, px(11)))
        self.badge_bg = c.create_rectangle(0, 0, 0, 0, fill="", outline=WARNING, state="hidden")
        self.badge_txt = c.create_text(0, 0, text="DÉMO", fill=WARNING, font=(fam, px(8), "bold"),
                                       state="hidden")
        self.vsep = c.create_line(0, 0, 0, 0, fill=LINE)
        self.clock_cap = c.create_text(0, 0, text="CHRONO", fill=DIM, font=(fam, px(9), "bold"))
        self.clock_item = c.create_text(0, 0, text="--:--", fill=DIM, font=(disp, px(24), dw))
        self.timers_item = c.create_text(0, 0, text="", fill=MUTED, font=(fam, px(10)), anchor="e")
        self.rule = c.create_line(0, 0, 0, 0, fill=LINE)
        self.threat_cap = c.create_text(0, 0, anchor="w", text="MENACE", fill=DIM, font=(fam, px(9), "bold"))
        self.threat_item = c.create_text(0, 0, anchor="w", text="-", fill=DIM, font=(disp, px(13), dw))
        self.detail_item = c.create_text(0, 0, anchor="w", text="Hors partie", fill=MUTED, font=(fam, px(11)))
        self.segs = [c.create_rectangle(0, 0, 0, 0, fill=TRACK, outline="") for _ in range(self.SEGMENTS)]
        self._button_win: int | None = None
        self._mu_win: int | None = None
        self._mu_widget: Any = None
        self._badge = False
        self._detail_full = "Hors partie"
        self.title = _CanvasText(c, self.title_item, self.layout)
        self.msg = _CanvasText(c, self.msg_item)
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
            top = s(34)                 # centre line of the upper row
            btn_w = 0
            if self._button_win is not None:
                btn_w = int(self.app.btn_start.winfo_reqwidth())
                c.coords(self._button_win, w - pad, top)
            clock_x = w - pad - btn_w - s(16)          # right edge of the clock block
            c.itemconfigure(self.clock_cap, anchor="e")
            c.itemconfigure(self.clock_item, anchor="e")
            c.coords(self.clock_cap, clock_x, top - s(17))
            c.coords(self.clock_item, clock_x, top + s(4))
            cb = c.bbox(self.clock_item)
            clock_left = (cb[0] if cb else clock_x - s(80)) - s(16)
            c.coords(self.vsep, clock_left, top - s(20), clock_left, top + s(20))
            right_limit = clock_left - s(16)
            if self._mu_win is not None:
                c.coords(self._mu_win, right_limit, top)
                mw = int(self._mu_widget.winfo_reqwidth()) if self._mu_widget is not None else 0
                right_limit -= mw + s(16)
            dx, dy = pad + s(10), top - s(7)
            self._dot = (dx, dy)
            c.coords(self.core, dx - s(4), dy - s(4), dx + s(4), dy + s(4))
            tx = pad + s(24)
            c.coords(self.title_item, tx, top - s(7))
            bb = c.bbox(self.title_item)
            if bb and self._badge:
                bx = bb[2] + s(10)
                c.coords(self.badge_bg, bx, top - s(14), bx + s(38), top)
                c.coords(self.badge_txt, bx + s(19), top - s(7))
            c.coords(self.msg_item, tx, top + s(5))
            c.itemconfigure(self.msg_item, width=max(s(120), right_limit - tx))
            # lower row: threat + gauge (left), objective timers (right)
            ty = h - s(20)
            c.coords(self.rule, s(3), ty - s(18), w - 1, ty - s(18))
            c.coords(self.threat_cap, pad, ty)
            c.coords(self.threat_item, pad + s(56), ty)
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

    def _fit_detail(self) -> None:
        """Ellipsize the threat detail so that it never runs into the gauge."""
        c = self.canvas
        try:
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
                    try:
                        self.app.btn_start.configure(bg_color=rb)
                    except Exception:
                        pass
            self.layout()
        except Exception:
            log.debug("hero background failed", exc_info=True)


# ======================================================================================
# The application
# ======================================================================================
EngineFactory = Callable[[Config, Any, Any, Any], Any]


class CoachApp:
    """Main window + orchestration of engine / overlay / voice. Build on the Tk thread."""

    def __init__(self, cfg: Config, *, demo: bool = False,
                 engine_factory: EngineFactory | None = None,
                 overlay_factory: Callable[[Config, Callable[[], Any]], Any] | None = None,
                 voice: Any = None, detector_factory: Callable[[Config], Any] | None = None,
                 demo_source_factory: Callable[[], Any] | None = None,
                 hotkeys: bool = True, save_path: Path | None = None) -> None:
        ctk = _import_ctk()
        self.ctk = ctk
        self.cfg: Config = (cfg if isinstance(cfg, Config) else Config()).validated()
        self._save_path = save_path
        self._engine_factory = engine_factory or _default_engine_factory
        self._overlay_factory = overlay_factory or _default_overlay_factory
        self._detector_factory = detector_factory or _default_detector_factory
        self._demo_source_factory = demo_source_factory or _default_demo_source
        self._want_hotkeys = hotkeys
        self.voice: Any = voice
        self._own_voice = voice is None
        self.engine: Any = None
        self.overlay: Any = None
        self._detector: Any = None
        self._hotkeys: Any = None
        self.demo = bool(demo)
        self.engine_error: str | None = None
        self._busy = False              # engine start/stop/rebuild in progress
        self._closing = False
        self._closed = threading.Event()
        self._muted = False
        self._move_mode = False
        self._save_job: str | None = None
        self._overlay_preview_job: str | None = None
        self._journal: deque[tuple[float | None, int, str]] = deque(maxlen=JOURNAL_MAX)
        self._journal_sig: tuple = ()
        self._journal_hidden: set = set()
        self._last_alert_seen: tuple[str, float] | None = None
        self._ai_test: tuple[bool, str] | None = None     # last "Tester la clé" result (ok, short text)
        self._ai_test_busy = False
        self._last_status_alert: str | None = None
        self._last_state_key = ""
        self._games: list[dict] = []
        self._voices: list[str] = []
        self._current_page = ""
        self._pulse_phase = 0.0
        self._enemy_cache: dict[tuple, Any] = {}
        self._images: dict[str, Any] = {}     # keep CTkImage references alive
        self._widgets_by_field: dict[str, Callable[[], None]] = {}   # field -> refresh function
        self._row_slots: list[Any] = []       # setting rows (their description wraps with the window)
        self._wrap_width = 0
        self._dispatcher = _Dispatcher()
        self._eng_lock = threading.Lock()
        self._created_engines: list[Any] = []   # every engine built (stopped again at close)
        self._radar_worker = _RadarWorker(self._radar_source, RADAR_PX)
        self._radar_seq = -1
        self._radar_live = False

        _apply_theme(ctk)
        try:  # compact by default (Windows display scaling already enlarges everything)
            ctk.set_widget_scaling(min(1.4, max(0.7, float(getattr(cfg, "ui_scale", 0.88)))))
        except Exception:
            log.debug("Cannot set the UI scaling", exc_info=True)
        self.root = ctk.CTk()
        # NB: never withdraw() the CTk root before mainloop: on Windows CTk re-applies the
        # saved "withdrawn" state after colouring the title bar and the window never shows.
        self.root.title(APP_NAME)
        self.root.report_callback_exception = self._tk_exception
        _body = _pick_family(self.root)
        self.fonts = _Fonts(ctk, _body, _pick_display(self.root, _body))
        self._set_window_icon(self.root)
        self._apply_geometry()
        self.root.minsize(MIN_W, MIN_H)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self.content = ctk.CTkFrame(self.root, fg_color=BG, corner_radius=0)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        self.pages: dict[str, Any] = {}
        builders = {"dashboard": self._build_dashboard, "alerts": self._build_alerts_page,
                    "overlay": self._build_overlay_page, "analysis": self._build_analysis_page,
                    "settings": self._build_settings_page, "help": self._build_help_page}
        for key, _label, _icon in PAGES:
            try:
                self.pages[key] = builders[key]()
            except Exception:
                log.exception("Cannot build page %s", key)
                self.pages[key] = self._error_page(key)
            self.pages[key].grid(row=0, column=0, sticky="nsew")
            self.pages[key].grid_remove()
        self._toast_frame: Any = None
        self._toast_job: str | None = None
        self._compact: bool | None = None
        self._layout_job: str | None = None
        self.root.bind("<Configure>", self._on_root_configure, add="+")
        self._bind_shortcuts()
        first = "dashboard"
        if getattr(self.cfg, "ui_remember_page", False) and getattr(self.cfg, "ui_last_page", "") in self.pages:
            first = self.cfg.ui_last_page
        self.show_page(first)
        self.root.protocol("WM_DELETE_WINDOW", self.request_close)
        self.root.after(1200, self._first_run_dialogs)
        self.root.after(350, self._ensure_visible)
        self.root.after(1500, self._ensure_visible)

        self._radar_worker.start()
        self.root.after(DISPATCH_MS, self._dispatch_loop)
        self.root.after(STATUS_MS, self._status_loop)
        self.root.after(PREVIEW_MS, self._preview_loop)
        self.root.after(PULSE_MS, self._pulse_loop)
        self._start_backend()
        if paths.is_frozen() and self.cfg.check_updates_on_start:   # silent update check (updater.py)
            self.root.after(UPDATE_CHECK_DELAY_MS, self._startup_update_check)
        self.root.after(2500, self.cb(self._check_last_update))      # did the last update apply?
        self.root.after(1800, self.cb(self.refresh_games))           # dashboard "avant la partie" + table
        self.root.after(3000, lambda: self._dispatcher.run(_prewarm_preview, None, None, name="TreeAI-ui-prewarm"))

    # ------------------------------------------------------------------ infrastructure
    def cb(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Wrap any widget callback (lambda...) like :func:`_guarded`."""
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                log.exception("UI callback failed")
                try:
                    self.show_error(f"Une erreur est survenue : {exc}")
                except Exception:
                    pass
                return None
        return wrapper

    def _tk_exception(self, exc_type: Any, exc: Any, tb: Any) -> None:
        log.error("Unhandled Tk callback exception", exc_info=(exc_type, exc, tb))
        try:
            self.show_error(f"Une erreur inattendue est survenue : {exc}")
        except Exception:
            pass

    def _ensure_visible(self) -> None:
        """Make sure the main window is shown and in front (CTk/Windows title-bar quirk)."""
        try:
            if self._closing or not self.root.winfo_exists():
                return
            if self.root.state() in ("withdrawn", "iconic"):
                self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(200, lambda: self._safe_untop())
            self.root.focus_force()
        except Exception:
            log.debug("ensure_visible failed", exc_info=True)

    def _safe_untop(self) -> None:
        try:
            self.root.attributes("-topmost", False)
        except Exception:
            pass

    def _dispatch_loop(self) -> None:
        if self._closing:
            return
        self._dispatcher.drain()
        self.root.after(DISPATCH_MS, self._dispatch_loop)

    def _set_window_icon(self, win: Any) -> None:
        """Window / taskbar icon (``.ico`` on Windows, PNG photo elsewhere)."""
        try:
            if sys.platform == "win32":
                try:  # own taskbar group + icon when run from python.exe
                    import ctypes  # noqa: PLC0415

                    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TreeAI.Coach")
                except Exception:
                    pass
                ico = app_icon_path("ico")
                if ico is not None:
                    win.iconbitmap(str(ico))
                    win.after(260, lambda: self._safe_iconbitmap(win, ico))   # CTk resets it after 200 ms
                    return
            from PIL import ImageTk  # noqa: PLC0415

            photo = ImageTk.PhotoImage(load_logo(64), master=win)
            self._images[f"icon-{id(win)}"] = photo
            win.iconphoto(True, photo)
        except Exception:
            log.debug("Cannot set the window icon", exc_info=True)

    @staticmethod
    def _safe_iconbitmap(win: Any, ico: Path) -> None:
        try:
            if win.winfo_exists():
                win.iconbitmap(str(ico))
        except Exception:
            pass

    def _apply_geometry(self) -> None:
        """Restore the saved window geometry, kept on screen; default 1100x720 centred."""
        sw = max(800, int(self.root.winfo_screenwidth()))
        sh = max(600, int(self.root.winfo_screenheight()))
        geo = self.cfg.ui_geometry or ""
        w, h, x, y = DEFAULT_W, DEFAULT_H, None, None
        try:
            import re  # noqa: PLC0415

            m = re.fullmatch(r"(\d+)x(\d+)(?:([+-]-?\d+)([+-]-?\d+))?", geo.strip())
            if m:
                w, h = int(m.group(1)), int(m.group(2))
                if m.group(3) is not None:
                    x, y = int(m.group(3)), int(m.group(4))
        except Exception:
            pass
        w = min(max(w, MIN_W), max(MIN_W, sw))
        h = min(max(h, MIN_H), max(MIN_H, sh))
        if x is None or y is None or x < -w + 120 or y < 0 or x > sw - 120 or y > sh - 80:
            x, y = max(0, (sw - w) // 2), max(0, (sh - h) // 3)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _on_root_configure(self, event: Any) -> None:
        if event.widget is not self.root or self._closing:
            return
        if self._layout_job is not None:
            try:
                self.root.after_cancel(self._layout_job)
            except Exception:
                pass
        self._layout_job = self.root.after(60, self._apply_layout)

    def _apply_layout(self) -> None:
        """Responsive tweaks: short toolbar labels and text wrapping at small window sizes."""
        self._layout_job = None
        try:
            scale = max(0.5, self._scaled(100) / 100)
            width = self.root.winfo_width() / scale
            compact = width < 1200
            if compact != self._compact:
                self._compact = compact
                self.btn_test_voice.configure(text="Voix" if compact else "Tester la voix")
                self.btn_calib.configure(text="Calibrer" if compact else "Calibrer la minimap")
                self._demo_button_text()
            en_w = self.enemies_card.winfo_width() / scale
            if en_w > 50:
                self.jungler_lbl.configure(wraplength=int(max(160, en_w - 40)))
            self._wrap_rows(int(self.content.winfo_width() / scale))
        except Exception:
            log.debug("Layout update failed", exc_info=True)

    def _wrap_rows(self, content_w: int) -> None:
        """Setting descriptions wrap before the control on the right (any window width)."""
        if content_w < 200 or content_w == self._wrap_width:
            return
        self._wrap_width = content_w
        scale = max(0.5, self._scaled(100) / 100)
        row_w = content_w - 96               # page / card paddings + scrollbar
        for slot in list(self._row_slots):
            lbl = getattr(slot, "desc_label", None)
            if lbl is None:
                continue
            try:
                sw = slot.winfo_reqwidth() / scale
                lbl.configure(wraplength=int(max(180, min(560, row_w - sw - 36))))
            except Exception:
                pass

    def _demo_button_text(self) -> None:
        if self.demo:
            text = "Fin démo" if self._compact else "Quitter la démo"
        else:
            text = "Démo" if self._compact else "Mode démo"
        self._set_text(self.btn_demo, text)

    # ------------------------------------------------------------------ small widget factories
    def _card(self, parent: Any, **kw: Any) -> Any:
        opts = dict(fg_color=PANEL, corner_radius=RADIUS, border_width=1, border_color=BORDER)
        opts.update(kw)
        return self.ctk.CTkFrame(parent, **opts)

    def _hline(self, parent: Any, color: str = LINE) -> Any:
        """1 px separator (the design uses lines, not nested boxes)."""
        return self.ctk.CTkFrame(parent, height=1, fg_color=color, corner_radius=0)

    def _caption(self, parent: Any, text: str, color: str = DIM, **kw: Any) -> Any:
        """Caption style: small spaced capitals (column headers, section titles)."""
        return self._label(parent, str(text).upper(), self.fonts.caps, color, **kw)

    def _label(self, parent: Any, text: str = "", font: Any = None, color: str = TEXT, **kw: Any) -> Any:
        kw.setdefault("height", 1)      # size to the text (CTkLabel's default minimum is 28 px)
        return self.ctk.CTkLabel(parent, text=text, font=font or self.fonts.body, text_color=color,
                                 fg_color="transparent", **kw)

    def _icon(self, kind: str, size: int = 18, color: str = MUTED) -> Any:
        key = f"{kind}-{size}-{color}"
        img = self._images.get(key)
        if img is None:
            if kind in ui_kit.EXTRA_ICONS:     # drawn at 2x for HiDPI, shown at ``size``
                pil = ui_kit.extra_icon(kind, size * 2, color)
            else:
                pil = nav_icon(kind, size * 2, color)
            img = self.ctk.CTkImage(light_image=pil, dark_image=pil, size=(size, size))
            self._images[key] = img
        return img

    def _tip(self, widget: Any, text: str | Callable[[], str]) -> None:
        """Hover tooltip (delayed, never raises)."""
        try:
            ui_kit.Tooltip(widget, text, bg=PANEL_HI, fg=TEXT, border=GOLD_DARK,
                           font=(self.fonts.family, self._font_px(12)))
        except Exception:
            log.debug("Tooltip failed", exc_info=True)

    def _hoverable(self, frame: Any, normal: str, hover: str, state: dict | None = None) -> None:
        """Border highlight while the mouse is over ``frame`` (or any of its children).

        ``state`` (optional dict) may hold a ``"border"`` key overriding ``normal`` (live colour).
        """
        def inside() -> bool:
            try:
                x, y = frame.winfo_pointerxy()
                w = frame.winfo_containing(x, y)
                while w is not None:
                    if w is frame:
                        return True
                    w = w.master
            except Exception:
                pass
            return False

        def enter(_e: Any = None) -> None:
            try:
                frame.configure(border_color=hover)
            except Exception:
                pass

        def leave(_e: Any = None) -> None:
            if inside():
                return
            try:
                frame.configure(border_color=(state or {}).get("border", normal))
            except Exception:
                pass

        def bind_all(w: Any) -> None:
            try:
                w.bind("<Enter>", enter, add="+")
                w.bind("<Leave>", leave, add="+")
            except Exception:
                pass
            try:
                for ch in w.winfo_children():
                    bind_all(ch)
            except Exception:
                pass

        frame.after(50, lambda: bind_all(frame))

    def _button(self, parent: Any, text: str, command: Callable[[], Any], kind: str = "secondary",
                icon: str | None = None, **kw: Any) -> Any:
        styles = {
            "primary": dict(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ON_ACCENT, border_width=0),
            "secondary": dict(fg_color=PANEL_HI, hover_color=HOVER, text_color=TEXT, border_width=1,
                              border_color=LINE_STRONG),
            "ghost": dict(fg_color="transparent", hover_color=PANEL_HI, text_color=TEXT, border_width=0),
            "danger": dict(fg_color=DANGER_DARK, hover_color=DANGER_HOVER, text_color=ON_DANGER, border_width=1,
                           border_color=DANGER),
        }
        opts: dict[str, Any] = dict(height=30, corner_radius=RADIUS, font=self.fonts.button,
                                    text_color_disabled=DIM)
        opts.update(styles.get(kind, styles["secondary"]))
        if icon:
            col = ON_ACCENT if kind == "primary" else MUTED
            opts["image"] = self._icon(icon, 14, col)
            opts["compound"] = "left"
        opts.update(kw)
        if kind == "primary" and opts.get("state") == "disabled":     # a disabled primary must not look active
            opts.update(fg_color=PANEL_HI, image=self._icon(icon, 14, DIM) if icon else None)
        btn = self.ctk.CTkButton(parent, text=text, command=self.cb(command), **opts)
        btn._tree_kind = kind  # type: ignore[attr-defined]
        btn._tree_icon = icon  # type: ignore[attr-defined]
        return btn

    def _btn_state(self, btn: Any, enabled: bool) -> None:
        """Enable / disable a button made by :meth:`_button` (a disabled primary turns grey)."""
        try:
            btn.configure(state="normal" if enabled else "disabled")
            if getattr(btn, "_tree_kind", "") == "primary":
                icon = getattr(btn, "_tree_icon", None)
                btn.configure(fg_color=ACCENT if enabled else PANEL_HI)
                if icon:
                    btn.configure(image=self._icon(icon, 14, ON_ACCENT if enabled else DIM))
        except Exception:
            log.debug("button state failed", exc_info=True)

    def _rule_image(self, width: int = 220) -> Any:
        """Gold rule fading out to the right (page titles)."""
        key = f"rule-{width}"
        img = self._images.get(key)
        if img is None:
            w, h = width * 2, 4
            arr = np.zeros((h, w, 4), np.uint8)
            arr[..., :3] = _hex_rgb(GOLD)
            fade = (np.clip(1.0 - np.linspace(0.0, 1.0, w), 0, 1) ** 1.6 * 230).astype(np.uint8)
            arr[1:3, :, 3] = fade
            pil = Image.fromarray(arr, "RGBA")
            img = self.ctk.CTkImage(light_image=pil, dark_image=pil, size=(width, 2))
            self._images[key] = img
        return img

    def _page(self, title: str, subtitle: str, scroll: bool = True,
              icon: str | None = None) -> tuple[Any, Any, Any]:
        """(page frame, header right slot, body frame)."""
        ctk = self.ctk
        page = ctk.CTkFrame(self.content, fg_color=BG, corner_radius=0)
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(page, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=24, pady=(16, 0))
        head.grid_columnconfigure(0, weight=1)
        tl = ctk.CTkFrame(head, fg_color="transparent")
        tl.grid(row=0, column=0, sticky="w")
        self._label(tl, title, self.fonts.title, TEXT, anchor="w").grid(row=0, column=0, sticky="w")
        sub = self._label(tl, subtitle, self.fonts.small, MUTED, anchor="w")
        sub.grid(row=0, column=1, sticky="w", padx=(12, 0), pady=(4, 0))
        page.subtitle = sub  # type: ignore[attr-defined]
        right = ctk.CTkFrame(head, fg_color="transparent", width=1, height=1)
        right.grid(row=0, column=1, sticky="e")
        page.head = head  # type: ignore[attr-defined]
        self._hline(page).grid(row=0, column=0, sticky="sew", padx=24)
        head.grid_configure(pady=(16, 12))
        if scroll:
            body = ctk.CTkScrollableFrame(page, fg_color=BG, corner_radius=0,
                                          scrollbar_button_color=SWITCH_OFF,
                                          scrollbar_button_hover_color=LINE_STRONG)
            body.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=(12, 8))
            body.grid_columnconfigure(0, weight=1)
            page.scroll_frame = body  # type: ignore[attr-defined]
            inner = ctk.CTkFrame(body, fg_color="transparent")
            inner.grid(row=0, column=0, sticky="nsew", padx=(12, 12))
            inner.grid_columnconfigure(0, weight=1)
            return page, right, inner
        body = ctk.CTkFrame(page, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=24, pady=(12, 16))
        return page, right, body

    def _section(self, parent: Any, row: int, title: str, subtitle: str | None = None,
                 icon: str | None = None) -> Any:
        """A titled section (caption title + 1 px rule, no box); returns its content frame."""
        card = self.ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 20))
        card.title = title  # type: ignore[attr-defined]
        try:
            parent._sections.append(card)
        except AttributeError:
            parent._sections = [card]
        card.grid_columnconfigure(0, weight=1)
        th = self.ctk.CTkFrame(card, fg_color="transparent")
        th.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 0))
        th.grid_columnconfigure(1, weight=1)
        self._caption(th, title, MUTED, anchor="w").grid(row=0, column=1, sticky="w")
        card.head = th  # type: ignore[attr-defined]
        self._hline(card, LINE_STRONG).grid(row=1, column=0, sticky="ew", pady=(4, 0))
        if subtitle:
            self._label(card, subtitle, self.fonts.tiny, DIM, anchor="w", justify="left", wraplength=640).grid(
                row=2, column=0, sticky="w", pady=(6, 0))
        body = self.ctk.CTkFrame(card, fg_color="transparent")
        body.grid(row=3, column=0, sticky="ew", padx=0, pady=(0, 0))
        body.grid_columnconfigure(0, weight=1)
        body._rows = 0  # type: ignore[attr-defined]
        body.card = card  # type: ignore[attr-defined]
        return body

    def _tabs(self, page: Any, body: Any, groups: Sequence[tuple[str, Sequence[str]]],
              default: str | None = None) -> dict[str, Any]:
        """Underlined tabs in the page header showing one group of sections at a time.

        ``groups`` = (tab label, section titles); sections not listed go to the last tab
        (usually "Avancé", i.e. collapsed by default). Returns {label: button}.
        """
        ctk = self.ctk
        sections = list(getattr(body, "_sections", []))
        bar = ctk.CTkFrame(page.head, fg_color="transparent")
        bar.grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))
        labels = [g for g, _t in groups]
        owner: dict[int, str] = {}
        for g, titles in groups:
            for card in sections:
                if getattr(card, "title", None) in titles:
                    owner[id(card)] = g
        for card in sections:
            owner.setdefault(id(card), labels[-1])
        btns: dict[str, Any] = {}
        unders: dict[str, Any] = {}
        state = {"cur": None}

        def select(label: str) -> None:
            if state["cur"] == label:
                return
            state["cur"] = label
            for card in sections:
                if owner[id(card)] == label:
                    card.grid()
                else:
                    card.grid_remove()
            for g, b in btns.items():
                on = g == label
                b.configure(text_color=TEXT if on else MUTED, font=self.fonts.nav_active if on else self.fonts.nav)
                unders[g].configure(fg_color=ACCENT if on else "transparent")
            try:
                page.scroll_frame._parent_canvas.yview_moveto(0)
            except Exception:
                pass

        for i, g in enumerate(labels):
            if not any(owner[id(c)] == g for c in sections):
                continue
            try:
                tw = int(self.fonts.nav_active.measure(g)) + 6
            except Exception:
                tw = 8 * len(g)
            b = ctk.CTkButton(bar, text=g, width=tw, height=24, corner_radius=0, fg_color="transparent",
                              hover_color=BG, text_color=MUTED, font=self.fonts.nav,
                              command=self.cb(lambda gg=g: select(gg)))
            b.grid(row=0, column=i, padx=(0, 14), sticky="w")
            u = ctk.CTkFrame(bar, width=1, height=2, corner_radius=0, fg_color="transparent")
            u.grid(row=1, column=i, padx=(0, 14), sticky="ew")
            btns[g], unders[g] = b, u
        page.select_tab = select  # type: ignore[attr-defined]
        first = default if default in btns else next(iter(btns), None)
        if first is not None:
            select(first)
        return btns

    def _row(self, body: Any, title: str, desc: str | None = None) -> tuple[Any, Any]:
        """A setting row (title + description on the left, control slot on the right)."""
        ctk = self.ctk
        r = body._rows
        if r:
            ctk.CTkFrame(body, height=1, fg_color=BORDER, corner_radius=0).grid(
                row=2 * r - 1, column=0, sticky="ew", pady=0)
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.grid(row=2 * r, column=0, sticky="ew", pady=8)
        row.grid_columnconfigure(0, weight=1)
        body._rows = r + 1
        left = ctk.CTkFrame(row, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w")
        self._label(left, title, self.fonts.body, TEXT, anchor="w").grid(row=0, column=0, sticky="w")
        desc_lbl = None
        if desc:
            desc_lbl = self._label(left, desc, self.fonts.tiny, DIM, anchor="w", justify="left",
                                   wraplength=430)
            desc_lbl.grid(row=1, column=0, sticky="w", pady=(1, 0))
        slot = ctk.CTkFrame(row, fg_color="transparent")
        slot.grid(row=0, column=1, sticky="e", padx=(16, 0))
        slot.desc_label = desc_lbl  # type: ignore[attr-defined]
        self._last_slot = slot
        self._last_row = row
        self._row_slots.append(slot)
        return row, slot

    def _switch_row(self, body: Any, field: str, title: str, desc: str | None = None,
                    on_change: Callable[[bool], None] | None = None) -> Any:
        _row, slot = self._row(body, title, desc)
        var = self.ctk.BooleanVar(value=bool(getattr(self.cfg, field)))

        def changed() -> None:
            self.set_option(field, bool(var.get()))
            if on_change is not None:
                on_change(bool(var.get()))

        sw = self.ctk.CTkSwitch(slot, text="", variable=var, command=self.cb(changed), width=46,
                                switch_width=34, switch_height=16, fg_color=SWITCH_OFF, progress_color=TEAL,
                                button_color=TEXT, button_hover_color="#FFFFFF")
        sw.grid(row=0, column=0)
        self._widgets_by_field[field] = lambda: var.set(bool(getattr(self.cfg, field)))
        return sw

    def _slider_row(self, body: Any, field: str, title: str, desc: str | None, lo: float, hi: float,
                    step: float, fmt: Callable[[float], str], cast: Callable[[float], Any] = float,
                    on_change: Callable[[Any], None] | None = None,
                    to_float: Callable[[Any], float] = float) -> Any:
        _row, slot = self._row(body, title, desc)
        value_lbl = self._label(slot, fmt(getattr(self.cfg, field)), self.fonts.h3, GOLD, width=78,
                                anchor="e")
        steps = max(1, int(round((hi - lo) / step)))

        def moved(v: float) -> None:
            val = cast(round(float(v) / step) * step)
            value_lbl.configure(text=fmt(val))
            self.set_option(field, val)
            if on_change is not None:
                on_change(val)

        sl = self.ctk.CTkSlider(slot, from_=lo, to=hi, number_of_steps=steps, width=210, height=18,
                                command=self.cb(moved), fg_color=SWITCH_OFF, progress_color=GOLD_DARK,
                                button_color=GOLD, button_hover_color=GOLD_HOVER)
        sl.set(to_float(getattr(self.cfg, field)))
        sl.grid(row=0, column=0, padx=(0, 8))
        value_lbl.grid(row=0, column=1)

        def refresh() -> None:
            sl.set(to_float(getattr(self.cfg, field)))
            value_lbl.configure(text=fmt(getattr(self.cfg, field)))
        self._widgets_by_field[field] = refresh
        return sl

    def _choice_row(self, body: Any, field: str, title: str, desc: str | None,
                    choices: Sequence[tuple[str, str]], segmented: bool = False, width: int = 220,
                    on_change: Callable[[str], None] | None = None) -> Any:
        _row, slot = self._row(body, title, desc)
        labels = [lbl for _v, lbl in choices]
        to_value = {lbl: v for v, lbl in choices}
        to_label = {v: lbl for v, lbl in choices}

        def changed(label: str) -> None:
            value = to_value.get(label)
            if value is None:
                return
            self.set_option(field, value)
            if on_change is not None:
                on_change(value)
            refresh()

        if segmented:
            w = self.ctk.CTkSegmentedButton(slot, values=labels, command=self.cb(changed), height=28,
                                            font=self.fonts.small, fg_color=PANEL_LO,
                                            selected_color=GOLD_DARK, selected_hover_color=ACCENT_DIM,
                                            unselected_color=PANEL_LO, unselected_hover_color=PANEL_HI,
                                            text_color=TEXT, corner_radius=RADIUS)
        else:
            w = self.ctk.CTkOptionMenu(slot, values=labels, command=self.cb(changed), width=width, height=28,
                                       font=self.fonts.small, dropdown_font=self.fonts.small,
                                       fg_color=PANEL_HI, button_color=HOVER, button_hover_color=HOVER,
                                       text_color=TEXT, dropdown_fg_color=PANEL_HI,
                                       dropdown_hover_color=HOVER, dropdown_text_color=TEXT,
                                       corner_radius=RADIUS, dynamic_resizing=False)
        w.grid(row=0, column=0)

        def refresh() -> None:
            w.set(to_label.get(getattr(self.cfg, field), labels[0]))
        refresh()
        self._widgets_by_field[field] = refresh
        return w

    # ------------------------------------------------------------------ sidebar
    def _build_sidebar(self) -> None:
        ctk = self.ctk
        sb = ctk.CTkFrame(self.root, width=SIDEBAR_W, fg_color=SURFACE, corner_radius=0)
        sb.grid(row=0, column=0, sticky="nsw")
        sb.grid_propagate(False)
        sb.grid_columnconfigure(0, weight=1)
        sb.grid_rowconfigure(3, weight=1)
        # right edge line (gold, subtle)
        ctk.CTkFrame(self.root, width=1, fg_color=LINE, corner_radius=0).grid(
            row=0, column=0, sticky="nse")

        brand = ctk.CTkFrame(sb, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 14))
        logo = load_logo(64)
        self._images["logo"] = ctk.CTkImage(light_image=logo, dark_image=logo, size=(28, 28))
        ctk.CTkLabel(brand, text="", image=self._images["logo"], fg_color="transparent").grid(
            row=0, column=0, rowspan=2, padx=(0, 8))
        self._label(brand, "TreeAI Coach", self.fonts.brand, TEXT, anchor="w").grid(
            row=0, column=1, sticky="sw")
        self._label(brand, f"v{__version__}", self.fonts.tiny, DIM, anchor="w").grid(
            row=1, column=1, sticky="nw")

        self._hline(sb).grid(row=1, column=0, sticky="new")
        nav = ctk.CTkFrame(sb, fg_color="transparent")
        nav.grid(row=1, column=0, sticky="new", pady=(8, 0))
        nav.grid_columnconfigure(1, weight=1)
        self._nav: dict[str, tuple[Any, Any, str]] = {}
        for i, (key, label, icon) in enumerate(PAGES, start=1):
            ind = ctk.CTkFrame(nav, width=3, height=30, fg_color="transparent", corner_radius=0)
            ind.grid(row=i, column=0, sticky="nsw")
            btn = ctk.CTkButton(nav, text=" " + label, anchor="w", height=30, corner_radius=0,
                                fg_color="transparent", hover_color=PANEL_HI, text_color=MUTED,
                                font=self.fonts.nav, image=self._icon(icon, 16, MUTED), compound="left",
                                command=self.cb(lambda k=key: self.show_page(k)))
            btn.grid(row=i, column=1, sticky="ew", pady=0, padx=(10, 0))
            self._nav[key] = (btn, ind, icon)
            self._tip(btn, f"{label}   (Ctrl+{i})")
        try:
            self._build_quick_toggles(sb)
        except Exception:
            log.exception("Cannot build the quick toggles")

        foot = ctk.CTkFrame(sb, fg_color="transparent")
        foot.grid(row=4, column=0, sticky="sew", padx=12, pady=(8, 10))
        foot.grid_columnconfigure(0, weight=1)
        self._hline(foot).grid(row=0, column=0, sticky="new")
        pill = ctk.CTkFrame(foot, fg_color="transparent", corner_radius=0, height=30)
        pill.grid(row=0, column=0, sticky="ew", pady=(8, 0))
        pill.grid_columnconfigure(1, weight=1)
        self.pill_dot = ctk.CTkCanvas(pill, width=14, height=14, bg=SURFACE, highlightthickness=0, bd=0)
        self.pill_dot.grid(row=0, column=0, padx=(2, 6), pady=4)
        self._pill_halo = self.pill_dot.create_oval(0, 0, 14, 14, fill=SURFACE, outline="")
        self._pill_dot_item = self.pill_dot.create_rectangle(3, 3, 11, 11, fill=DIM, outline="")
        self.pill_text = self._label(pill, "Démarrage…", self.fonts.small, TEXT, anchor="w")
        self.pill_text.grid(row=0, column=1, sticky="w", padx=(0, 8))
        meta = ctk.CTkFrame(foot, fg_color="transparent")
        meta.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        meta.grid_columnconfigure(0, weight=1)
        ver = ctk.CTkButton(meta, text="Nouveautés", anchor="w", width=0, height=24, corner_radius=RADIUS,
                            font=self.fonts.tiny, fg_color="transparent", hover_color=PANEL_HI, text_color=DIM,
                            command=self.cb(self.show_changelog))
        ver.grid(row=0, column=0, sticky="w")
        self._tip(ver, f"Nouveautés de la version {ui_kit.CHANGELOG_VERSION}")
        for col, (icon, tip, fn) in enumerate((("info", "À propos et mentions légales", lambda: self.show_about()),
                                               ("minimize", "Réduire la fenêtre (l'analyse continue)",
                                                lambda: self.minimize()))):
            b = ctk.CTkButton(meta, text="", width=26, height=24, corner_radius=RADIUS, fg_color="transparent",
                              hover_color=PANEL_HI, image=self._icon(icon, 14, DIM), command=self.cb(fn))
            b.grid(row=0, column=col + 1, padx=(2, 0))
            self._tip(b, tip)

    def _build_quick_toggles(self, sb: Any) -> None:
        """Sidebar "ACCÈS RAPIDE": safe mode, voice and overlay switches (always visible)."""
        ctk = self.ctk
        box = ctk.CTkFrame(sb, fg_color="transparent")
        box.grid(row=2, column=0, sticky="new", padx=12, pady=(16, 0))
        box.grid_columnconfigure(1, weight=1)
        self._hline(box).grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        self._label(box, "ACCÈS RAPIDE", self.fonts.caps, DIM, anchor="w").grid(
            row=1, column=0, columnspan=3, sticky="w", padx=2, pady=(0, 4))
        self._quick: dict[str, tuple[Any, Any]] = {}
        specs = (("safe", "shield", "Mode sûr", "Mode sûr : aucune alerte de gank ni suivi du jungler, aucune zone "
                                                  "dans le brouillard (Ctrl+Maj+S)."),
                 ("voice", "voice", "Voix", "Couper / rétablir les annonces vocales (Ctrl+M)."),
                 ("overlay", "overlay", "Overlay", "Afficher / masquer les indications sur la minimap."))
        for i, (key, icon, text, tip) in enumerate(specs, start=2):
            ctk.CTkLabel(box, text="", image=self._icon(icon, 14, MUTED), fg_color="transparent", width=16).grid(
                row=i, column=0, padx=(2, 8), pady=3)
            lbl = self._label(box, text, self.fonts.small, MUTED, anchor="w")
            lbl.grid(row=i, column=1, sticky="w")
            var = ctk.BooleanVar(value=False)
            sw = ctk.CTkSwitch(box, text="", variable=var, width=40, switch_width=30, switch_height=14,
                               fg_color=SWITCH_OFF, progress_color=WARNING if key == "safe" else TEAL,
                               button_color=TEXT, button_hover_color="#FFFFFF",
                               command=self.cb(lambda k=key: self._quick_toggled(k)))
            sw.grid(row=i, column=2, sticky="e")
            self._tip(lbl, tip)
            self._tip(sw, tip)
            self._quick[key] = (var, sw)
        # one-click player level (skill.py): the higher the level, the fewer basic indications
        try:
            from treeaicoach import skill as _skill
            row = 2 + len(specs)
            self._label(box, "TON NIVEAU", self.fonts.caps, DIM, anchor="w").grid(
                row=row, column=0, columnspan=3, sticky="w", padx=2, pady=(12, 4))
            short = {"debutant": "Déb.", "intermediaire": "Inter.", "avance": "Avancé", "expert": "Expert"}
            to_key = {short[k]: k for k, _l in _skill.SKILL_LEVELS}
            self.skill_seg = ctk.CTkSegmentedButton(
                box, values=[short[k] for k, _l in _skill.SKILL_LEVELS], height=26, font=self.fonts.tiny_bold,
                fg_color=PANEL_LO, selected_color=GOLD_DARK, selected_hover_color=ACCENT_DIM,
                unselected_color=PANEL_LO, unselected_hover_color=PANEL_HI, text_color=TEXT, corner_radius=RADIUS,
                command=self.cb(lambda lbl: self.apply_skill_level(to_key.get(lbl, "intermediaire"))))
            self.skill_seg.grid(row=row + 1, column=0, columnspan=3, sticky="ew", padx=0)
            self.skill_seg.set(short[_skill.normalize(getattr(self.cfg, "skill_level", "intermediaire"))])
            self._tip(self.skill_seg, "\n".join(f"{l} : {_skill.SKILL_HELP[k]}" for k, l in _skill.SKILL_LEVELS))
        except Exception:
            log.exception("Cannot build the skill level selector")
        self._sync_quick()

    @_guarded
    def apply_skill_level(self, level: str) -> None:
        """Débutant / Intermédiaire / Avancé / Expert: adapts tips, voice and overlay in one click."""
        from treeaicoach import skill as _skill
        changes = _skill.preset_changes(self.cfg, level)
        new = dataclasses.replace(self.cfg, **changes).validated()
        self._replace_config(new, changed=set(changes))
        self._refresh_all_widgets()
        self.show_toast(f"Niveau « {_skill.label(level)} » : {_skill.SKILL_HELP[_skill.normalize(level)]}")

    def _sync_quick(self, muted: bool | None = None) -> None:
        """Quick toggles <- configuration / engine state (never fires their callbacks)."""
        q = getattr(self, "_quick", None)
        if not q:
            return
        try:
            if muted is None:
                muted = self._is_muted()
            want = {"safe": bool(getattr(self.cfg, "safe_mode", False)), "voice": not muted,
                    "overlay": bool(self.cfg.overlay_enabled)}
            for key, (var, _sw) in q.items():
                if bool(var.get()) != want[key]:
                    var.set(want[key])
            dash = getattr(self, "dash_safe_var", None)
            if dash is not None and bool(dash.get()) != want["safe"]:
                dash.set(want["safe"])
            pill = getattr(self, "pill_text", None)
            if pill is not None:
                pill.configure(text_color=WARNING if want["safe"] else TEXT)
        except Exception:
            log.debug("quick toggles sync failed", exc_info=True)

    def _is_muted(self) -> bool:
        eng = self.engine
        try:
            if eng is not None and hasattr(eng, "muted"):
                return bool(eng.muted)
        except Exception:
            pass
        return bool(self._muted)

    def _quick_toggled(self, key: str) -> None:
        var = self._quick[key][0]
        on = bool(var.get())
        if key == "safe":
            self.set_safe_mode(on)
        elif key == "voice":
            if on == self._is_muted():
                self.toggle_mute()
        elif key == "overlay":
            self.set_option("overlay_enabled", on)
            self.show_toast("Overlay affiché." if on else "Overlay masqué.")

    @_guarded
    def set_safe_mode(self, on: bool) -> None:
        """One-click "mode sûr" (dashboard, sidebar, Ctrl+Maj+S)."""
        if not hasattr(self.cfg, "safe_mode"):
            return
        self.set_option("safe_mode", bool(on))
        self._sync_quick()
        self.show_toast("Mode sûr activé : aucune alerte de gank ni suivi du jungler." if on else
                        "Mode sûr désactivé : toutes les alertes choisies sont actives.",
                        "warning" if on else "info")

    @_guarded
    def toggle_mute(self) -> None:
        """Mute / unmute the voice (Tk thread)."""
        self._hk_mute()
        self.root.after(60, self._sync_quick)

    @_guarded
    def minimize(self) -> None:
        """Reduce the window to the taskbar (the analysis keeps running)."""
        self.root.iconify()

    def show_page(self, key: str) -> None:
        """Switch the visible page."""
        if key not in self.pages or key == self._current_page:
            return
        for k, page in self.pages.items():
            if k != key:
                page.grid_remove()
        self.pages[key].grid()
        self._current_page = key
        if getattr(self.cfg, "ui_remember_page", False) and getattr(self.cfg, "ui_last_page", key) != key:
            try:
                self.cfg = dataclasses.replace(self.cfg, ui_last_page=key).validated()
                self._schedule_save()
            except Exception:
                log.debug("Cannot remember the page", exc_info=True)
        for k, (btn, ind, icon) in self._nav.items():
            active = k == key
            btn.configure(fg_color=PANEL_HI if active else "transparent",
                          text_color=TEXT if active else MUTED,
                          font=self.fonts.nav_active if active else self.fonts.nav,
                          image=self._icon(icon, 16, ACCENT if active else MUTED))
            ind.configure(fg_color=ACCENT if active else "transparent")
        if key == "dashboard":
            self._radar_worker.active.set()
        else:
            self._radar_worker.active.clear()
        if key == "analysis":
            self.refresh_games()
        if key == "overlay":
            self._schedule_overlay_preview()

    def _bind_shortcuts(self) -> None:
        """Window shortcuts: Ctrl+1..6 pages, Ctrl+M mute, Ctrl+Shift+S safe mode, Ctrl+D diagnostic, F1."""
        r = self.root

        def page(k: str) -> str:
            self.cb(self.show_page)(k)
            return "break"

        for i, (key, _l, _i) in enumerate(PAGES, start=1):
            r.bind(f"<Control-Key-{i}>", lambda _e, k=key: page(k), add="+")
            r.bind(f"<Control-KP_{i}>", lambda _e, k=key: page(k), add="+")
        r.bind("<Control-m>", lambda _e: self.toggle_mute(), add="+")
        r.bind("<Control-S>", lambda _e: self.set_safe_mode(not getattr(self.cfg, "safe_mode", False)), add="+")
        r.bind("<Control-d>", lambda _e: self.copy_diagnostic(), add="+")
        r.bind("<F1>", lambda _e: page("help"), add="+")

    # ------------------------------------------------------------------ dashboard
    def _build_dashboard(self) -> Any:
        ctk = self.ctk
        page, right, body = self._page("En jeu", "Minimap et alertes en direct", scroll=False)
        self.dash_safe_var = ctk.BooleanVar(value=bool(getattr(self.cfg, "safe_mode", False)))
        self.dash_safe = ctk.CTkSwitch(right, text="Mode sûr", variable=self.dash_safe_var, font=self.fonts.small,
                                       text_color=MUTED, width=40, switch_width=28, switch_height=14,
                                       fg_color=SWITCH_OFF, progress_color=WARNING, button_color=TEXT,
                                       button_hover_color="#FFFFFF",
                                       command=self.cb(lambda: self.set_safe_mode(bool(self.dash_safe_var.get()))))
        self.dash_safe.grid(row=0, column=0, padx=(0, 16))
        self._tip(self.dash_safe, "Mode sûr : aucune alerte de gank ni suivi du jungler, aucune zone dans le "
                                  "brouillard. Minuteurs et rappels restent actifs. (Ctrl+Maj+S)")
        self.btn_test_voice = self._button(right, "Tester la voix", self.test_voice, "ghost", icon="voice", width=0,
                                           height=26)
        self.btn_test_voice.grid(row=0, column=1, padx=(0, 8))
        self._tip(self.btn_test_voice, "Fait dire une alerte d'exemple au coach.")
        self.btn_demo = self._button(right, "Mode démo", self.toggle_demo, "ghost", icon="demo", width=0, height=26)
        self.btn_demo.grid(row=0, column=2, padx=(0, 8))
        self._tip(self.btn_demo, "Partie simulée : le jungler ennemi vient te ganker vers 40 s.")
        self.btn_calib = self._button(right, "Calibrer la minimap", self.calibrate, "ghost", icon="target", width=0,
                                      height=26)
        self.btn_calib.grid(row=0, column=3)
        self._tip(self.btn_calib, "Trace un carré autour de la minimap si elle n'est pas trouvée toute seule.")

        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0)
        body.grid_rowconfigure(2, weight=1)

        # --- banner (break reminder...) -------------------------------------------------
        self.banner = ctk.CTkFrame(body, fg_color=WARNING_BG, corner_radius=RADIUS, border_width=0)
        self.banner.grid_columnconfigure(1, weight=1)
        ctk.CTkFrame(self.banner, width=3, height=20, corner_radius=0, fg_color=WARNING).grid(
            row=0, column=0, sticky="ns", padx=(0, 10))
        self.banner_lbl = self._label(self.banner, "", self.fonts.small, TEXT, anchor="w", justify="left")
        self.banner_lbl.grid(row=0, column=1, sticky="w", pady=8)
        self._button(self.banner, "Compris", self._dismiss_banner, "ghost", width=90, height=28).grid(
            row=0, column=2, padx=10)
        self._banner_dismissed: str | None = None

        # --- hero: state + pulse, clock, threat gauge, start button ----------------------
        self._gauge_frac = 0.0
        self._gauge_target = 0.0
        self._gauge_color = DIM
        hero = HeroBanner(self, body)
        self.hero = hero
        hero.canvas.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        self.status_card = hero.canvas
        self.state_title = hero.title
        self.state_msg = hero.msg
        self.clock_lbl = hero.clock
        self.threat_lbl = hero.threat
        self.threat_detail = hero.detail
        self.demo_badge = hero.badge
        self.btn_start = ctk.CTkButton(hero.canvas, text="Démarrer l'analyse", width=150, height=34,
                                       corner_radius=RADIUS, font=self.fonts.button, fg_color=ACCENT,
                                       hover_color=ACCENT_HOVER, text_color=ON_ACCENT, text_color_disabled=DIM,
                                       bg_color=hero.right_bg, image=self._icon("play", 12, ON_ACCENT),
                                       compound="left", command=self.cb(self.toggle_engine))
        hero.attach_button(self.btn_start)

        # --- left column: teams + journal --------------------------------------------
        left = ctk.CTkFrame(body, fg_color="transparent")
        left.grid(row=2, column=0, sticky="nsew", padx=(0, 16))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(2, weight=1)

        # --- live coach strip: my role (+ lane swap), "jouer plus fort ou non", top tip, AI counter
        co = ctk.CTkFrame(left, fg_color="transparent", corner_radius=0)
        self.coach_card = co
        co.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        co.grid_columnconfigure(1, weight=1)
        self.coach_gauge_lbl = self._label(co, "-", self.fonts.num, DIM, anchor="w")
        self.coach_gauge_lbl.grid(row=0, column=0, sticky="w", padx=(0, 12), pady=(0, 0))
        self._tip(self.coach_gauge_lbl, "Jouer plus fort ou non : ATTAQUE ▲▲, PLUS FORT ▲, NORMAL, "
                                        "PRUDENT ▼, SAFE ▼▼ (selon ton avance, les combats et ton face-à-face).")
        self.coach_role_lbl = self._label(co, "Rôle : -", self.fonts.small, MUTED, anchor="w")
        self.coach_role_lbl.grid(row=0, column=1, sticky="w")
        self._tip(self.coach_role_lbl, "Rôle détecté d'après la partie (et les échanges de voie).")
        self.coach_ai_lbl = self._label(co, "", self.fonts.tiny_bold, TEAL, anchor="e")
        self.coach_ai_lbl.grid(row=0, column=2, sticky="e", padx=(8, 0))
        self._tip(self.coach_ai_lbl, "Conseils IA utilisés dans cette partie : 5 automatiques max "
                                     "+ 1 en urgence (F8 à part).")
        self.coach_tip_lbl = self._label(co, "Le conseil du moment s'affichera ici pendant la partie.",
                                         self.fonts.small, DIM, anchor="w", justify="left", wraplength=520)
        self.coach_tip_lbl.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(2, 8))
        self._hline(co).grid(row=2, column=0, columnspan=3, sticky="ew")
        self._coach_sig: tuple = ()

        en = ctk.CTkFrame(left, fg_color="transparent", corner_radius=0)
        self.enemies_card = en
        en.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        en.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(en, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew")
        head.grid_columnconfigure(1, weight=1)
        self._caption(head, "Ennemis", ENEMY_RING, anchor="w").grid(row=0, column=0, sticky="w")
        self.visible_lbl = self._label(head, "", self.fonts.tiny, MUTED, anchor="e")
        self.visible_lbl.grid(row=0, column=2, sticky="e")
        self.jungler_lbl = self._label(en, "Jungler : en attente d'une partie", self.fonts.small, MUTED,
                                       anchor="w", justify="left", wraplength=480)
        self.jungler_lbl.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        slots = ctk.CTkFrame(en, fg_color="transparent")
        slots.grid(row=2, column=0, sticky="ew", pady=(6, 8))
        self.enemy_slots: list[dict[str, Any]] = []
        for i in range(5):
            slots.grid_columnconfigure(i, weight=1, uniform="enemy")
            box = ctk.CTkFrame(slots, fg_color=PANEL, corner_radius=RADIUS, border_width=1, border_color=PANEL)
            box.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 2, 0))
            box.grid_columnconfigure(0, weight=1)
            icon = ctk.CTkLabel(box, text="", image=self._enemy_image(None, None, "empty"), fg_color="transparent")
            icon.grid(row=0, column=0, pady=(9, 0))
            name = self._label(box, "-", self.fonts.tiny_bold, MUTED)
            name.grid(row=1, column=0, padx=4, pady=(3, 0))
            status = self._label(box, " ", self.fonts.tiny, DIM)
            status.grid(row=2, column=0, pady=(1, 8), padx=4)
            slot = {"box": box, "icon": icon, "name": name, "status": status, "sig": None, "tip": ""}
            self._hoverable(box, PANEL, LINE_STRONG, slot)
            self._tip(box, lambda sl=slot: sl.get("tip") or "")
            self.enemy_slots.append(slot)
        # allies + lane match-up
        self._hline(en).grid(row=3, column=0, sticky="ew")
        team = ctk.CTkFrame(en, fg_color="transparent")
        team.grid(row=4, column=0, sticky="ew", pady=(8, 8))
        team.grid_columnconfigure(1, weight=1)
        al = ctk.CTkFrame(team, fg_color="transparent")
        al.grid(row=0, column=0, sticky="w")
        self._label(al, "ALLIÉS", self.fonts.caps, DIM, anchor="w").grid(row=0, column=0, columnspan=4, sticky="w")
        self.ally_slots: list[dict[str, Any]] = []
        for i in range(4):
            cell = ctk.CTkFrame(al, fg_color="transparent")
            cell.grid(row=1, column=i, padx=(0, 8), pady=(4, 0))
            ic = ctk.CTkLabel(cell, text="", image=self._ally_image(None, None, None), fg_color="transparent")
            ic.grid(row=0, column=0)
            nm = self._label(cell, "-", self.fonts.tiny, DIM)
            nm.grid(row=1, column=0, pady=(2, 0))
            slot = {"icon": ic, "name": nm, "sig": None, "tip": ""}
            self._tip(ic, lambda sl=slot: sl.get("tip") or "")
            self.ally_slots.append(slot)
        # lane match-up lives in the status strip (the "hero"): me VS my lane opponent
        mu = ctk.CTkFrame(hero.canvas, fg_color=PANEL, corner_radius=0)
        self.mu_me = ctk.CTkLabel(mu, text="", image=self._ally_image(None, None, None), fg_color="transparent")
        self.mu_me.grid(row=0, column=0, rowspan=2)
        self._label(mu, "VS", self.fonts.tiny_bold, DIM).grid(row=0, column=1, rowspan=2, padx=6)
        self.mu_opp = ctk.CTkLabel(mu, text="", image=self._ally_image(None, None, None, ring=ENEMY_RING),
                                   fg_color="transparent")
        self.mu_opp.grid(row=0, column=2, rowspan=2)
        self._caption(mu, "Face-à-face", DIM, anchor="w").grid(row=0, column=3, sticky="sw", padx=(10, 0))
        self.matchup_lbl = self._label(mu, "En attente", self.fonts.tiny, DIM, anchor="w")
        self.matchup_lbl.grid(row=1, column=3, sticky="nw", padx=(10, 0))
        self._matchup_sig: tuple = ()
        hero.attach_matchup(mu)

        jr = ctk.CTkFrame(left, fg_color="transparent", corner_radius=0)
        jr.grid(row=2, column=0, sticky="nsew")
        jr.grid_columnconfigure(0, weight=1)
        jr.grid_rowconfigure(1, weight=1)
        jh = ctk.CTkFrame(jr, fg_color="transparent")
        jh.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        jh.grid_columnconfigure(1, weight=1)
        self.journal_cap = self._caption(jh, "Journal", MUTED, anchor="w")
        self.journal_cap.grid(row=0, column=0, sticky="w")
        clr = ctk.CTkButton(jh, text="", width=26, height=24, corner_radius=RADIUS, fg_color="transparent",
                            hover_color=PANEL_HI, image=self._icon("close", 12, DIM),
                            command=self.cb(self.clear_journal))
        clr.grid(row=0, column=2)
        self._tip(clr, "Effacer le journal")
        self.journal_clear_btn = clr
        # empty journal -> "avant la partie": last game + précision, goal, point to work on (or a checklist)
        self.pregame = ctk.CTkFrame(jr, fg_color="transparent", corner_radius=0)
        self.pregame.grid_columnconfigure(0, weight=1)
        self._pregame_data: dict[str, Any] | None = None
        self._pregame_sig: Any = None
        self._pregame_busy = False
        self.journal = ctk.CTkTextbox(jr, fg_color=SUNKEN, text_color=TEXT, font=self.fonts.small,
                                      wrap="word", activate_scrollbars=True, border_width=0,
                                      scrollbar_button_color=SWITCH_OFF,
                                      scrollbar_button_hover_color=LINE_STRONG, height=60)
        self.journal.grid(row=1, column=0, sticky="nsew")
        for lvl, col in LEVEL_COLORS.items():
            self.journal.tag_config(f"lvl{lvl}", foreground=col)
        self.journal.tag_config("time", foreground=DIM)
        self.journal.tag_config("line", spacing1=3, spacing3=3)
        self.journal.tag_config("empty", foreground=DIM)
        self._render_journal()

        # --- right column: radar + tech -------------------------------------------------
        rc = ctk.CTkFrame(body, fg_color="transparent", corner_radius=0, width=RADAR_PX + 8)
        rc.grid(row=2, column=1, sticky="n")
        rc.grid_columnconfigure(0, weight=1)
        rh = ctk.CTkFrame(rc, fg_color="transparent")
        rh.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        rh.grid_columnconfigure(1, weight=1)
        self._caption(rh, "Radar", MUTED, anchor="w").grid(row=0, column=0, sticky="w")
        for i, (icon, tip, fn) in enumerate((
                ("refresh", "Rechercher la minimap maintenant", lambda: self.relocate()),
                ("report", "Ouvrir le dernier rapport", lambda: self.open_last_report()),
                ("folder", "Ouvrir le dossier des rapports", lambda: self.open_games_dir()),
                ("copy", "Copier le diagnostic (Ctrl+D)", lambda: self.copy_diagnostic()))):
            b = ctk.CTkButton(rh, text="", width=28, height=26, corner_radius=RADIUS, fg_color="transparent",
                              hover_color=PANEL_HI, image=self._icon(icon, 15, MUTED), command=self.cb(fn))
            b.grid(row=0, column=i + 2, padx=(2, 0))
            self._tip(b, tip)
        import tkinter as tk  # noqa: PLC0415
        from PIL import ImageTk  # noqa: PLC0415

        self._radar_size = self._scaled(RADAR_PX)
        self._radar_placeholder = radar_placeholder(self._radar_size)
        self._radar_photo = ImageTk.PhotoImage(self._radar_placeholder, master=self.root)
        holder = tk.Frame(rc, bg=BG, width=self._radar_size, height=self._radar_size)
        holder.grid(row=1, column=0)
        holder.grid_propagate(False)
        self.radar_lbl = tk.Label(holder, image=self._radar_photo, bg=BG, bd=0, highlightthickness=0)
        self.radar_lbl.place(x=0, y=0, relwidth=1, relheight=1)
        self.radar_msg = tk.Label(holder, text="En attente d'une partie…", bg=PANEL_LO, fg=MUTED,
                                  font=(self.fonts.family, self._font_px(12)), padx=12, pady=6)
        self.radar_msg.place(relx=0.5, rely=0.5, anchor="center")
        self.radar_badge = ctk.CTkLabel(holder, text=" HORS LIGNE ", font=self.fonts.caps, text_color=MUTED,
                                        fg_color=PANEL_HI, corner_radius=RADIUS, height=18, bg_color=PANEL_LO)
        self.radar_badge.place(x=self._scaled(8), y=self._scaled(8))
        # --- launcher: status of each subsystem with a one-click fix ----------------------
        sysf = ctk.CTkFrame(rc, fg_color="transparent")
        sysf.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        sysf.grid_columnconfigure(2, weight=1)
        self._caption(sysf, "Système", MUTED, anchor="w").grid(row=0, column=0, columnspan=4, sticky="w")
        self._hline(sysf, LINE_STRONG).grid(row=1, column=0, columnspan=4, sticky="ew", pady=(4, 2))
        self.sys_rows: dict[str, dict[str, Any]] = {}
        sys_tips = {"ia": "Modèle qui reconnaît les champions sur la minimap.",
                    "ai": "Conseils écrits par une IA en ligne (facultatif) : fournisseur, clé, conseils utilisés "
                          "dans la partie (5 + 1 en urgence)."}
        for i, (key, label) in enumerate((("game", "Jeu"), ("minimap", "Minimap"), ("lcu", "Client LoL"),
                                          ("ia", "Détection"), ("ai", "IA conseil"), ("voice", "Voix"))):
            r = 2 + i
            dot = ctk.CTkFrame(sysf, width=6, height=6, corner_radius=0, fg_color=DIM)
            dot.grid(row=r, column=0, padx=(0, 8))
            name = self._label(sysf, label, self.fonts.small, TEXT, anchor="w")
            name.grid(row=r, column=1, sticky="w", padx=(0, 8), pady=1)
            val = self._label(sysf, "-", self.fonts.tiny, MUTED, anchor="w")
            val.grid(row=r, column=2, sticky="w")
            if key in sys_tips:
                self._tip(name, sys_tips[key])
            self._tip(val, lambda v=val: v.cget("text"))
            btn = ctk.CTkButton(sysf, text="", width=10, height=18, corner_radius=RADIUS, fg_color="transparent",
                                hover_color=PANEL_HI, text_color=ACCENT, font=self.fonts.tiny_bold,
                                command=self.cb(lambda k=key: self._system_fix(k)))
            btn.grid(row=r, column=3, sticky="e")
            btn.grid_remove()
            self.sys_rows[key] = {"dot": dot, "val": val, "btn": btn, "sig": None, "action": ""}
        self._lcu_text = ""
        self._lcu_polled = 0.0
        self.btn_test_overlay = self._button(rc, "Tester l'overlay", self.test_overlay, "secondary", icon="overlay",
                                             height=26)
        self.btn_test_overlay.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self._tip(self.btn_test_overlay, "Affiche l'overlay sur une partie d'exemple pendant 10 s "
                                         "(sûr, attention, danger), hors partie.")
        self._overlay_test: tuple[float, list] | None = None

        tech = ctk.CTkFrame(rc, fg_color="transparent")
        tech.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        self.tech: dict[str, Any] = {}
        for i, (key, label, tip) in enumerate((
                ("fps", "FPS", "Images de minimap analysées par seconde"),
                ("cpu", "CPU", "Processeur utilisé par TreeAI Coach (en % de la machine)"),
                ("detector", "MODÈLE", "Détecteur de champions utilisé"),
                ("voice", "VOIX", "Moteur de synthèse vocale utilisé"))):
            tech.grid_columnconfigure(i, weight=1, uniform="tech")
            tile = ctk.CTkFrame(tech, fg_color="transparent", corner_radius=0)
            tile.grid(row=0, column=i, sticky="ew")
            tile.grid_columnconfigure(0, weight=1)
            self._label(tile, label, self.fonts.caps, DIM, anchor="w").grid(row=0, column=0, sticky="w")
            val = self._label(tile, "-", self.fonts.tiny_bold, TEXT, anchor="w")
            val.grid(row=1, column=0, sticky="w")
            self.tech[key] = val
            self._tip(tile, tip)
        self._cpu = ui_kit.CpuMeter()
        return page

    def _scaled(self, px: int) -> int:
        try:
            return int(round(px * float(self.ctk.ScalingTracker.get_widget_scaling(self.root))))
        except Exception:
            return px

    def _font_px(self, size: int) -> int:
        return -abs(self._scaled(size))

    def _enemy_image(self, icon: np.ndarray | None, alias: str | None, mode: str, role: str | None = None,
                     mia: float | None = None, jungler: bool = False) -> Any:
        """CTkImage of an enemy card portrait: team ring, role badge, MIA arc, jungler star (cached)."""
        bucket = None if mia is None else min(20, int(mia // 3))
        key = (alias, mode, icon is not None, role, bucket, jungler)
        img = self._enemy_cache.get(key)
        if img is None:
            ring = {"visible": ENEMY_RING, "approach": WARNING, "mia": DIM, "empty": BORDER}.get(mode, DIM)
            pil = circle_icon(icon, 104, ring, grey=(mode == "mia"), bg=PANEL)
            if mode != "empty":
                frac = None if bucket is None else min(1.0, bucket * 3 / 60.0)
                pil = ui_kit.decorate_portrait(pil, role, frac, arc_color=WARNING, badge_bg=PANEL, star=jungler)
            img = self.ctk.CTkImage(light_image=pil, dark_image=pil, size=(52, 52))
            if len(self._enemy_cache) > 160:
                self._enemy_cache.clear()
            self._enemy_cache[key] = img
        return img

    def _ally_image(self, icon: np.ndarray | None, alias: str | None, role: str | None,
                    ring: str = ALLY_RING) -> Any:
        """Small round portrait of an ally (or of me / my lane opponent) with its role badge."""
        key = ("ally", alias, icon is not None, role, ring)
        img = self._enemy_cache.get(key)
        if img is None:
            pil = circle_icon(icon, 72, ring if alias else BORDER, bg=PANEL)
            if alias:
                pil = ui_kit.decorate_portrait(pil, role, badge_bg=PANEL)
            img = self.ctk.CTkImage(light_image=pil, dark_image=pil, size=(36, 36))
            self._enemy_cache[key] = img
        return img

    def _draw_gauge(self) -> None:
        hero = getattr(self, "hero", None)
        if hero is not None:
            hero.draw_gauge(self._gauge_frac, self._gauge_color)

    # ------------------------------------------------------------------ alerts & voice page
    def _build_alerts_page(self) -> Any:
        ctk = self.ctk
        page, right, body = self._page("Alertes", "Ce que le coach annonce et comment il parle")
        self._button(right, "Tester la voix", self.test_voice, "secondary", icon="voice", height=26).grid(
            row=0, column=0)
        ex = _example_phrases()
        self._examples = _example_speech()

        # --- presets -------------------------------------------------------------------
        s = self._section(body, 0, "Préréglage", "Un clic pour tout régler (alertes et overlay). Tu peux ensuite "
                                                 "ajuster chaque option.", icon="sliders")
        _row, slot = self._row(s, "Style du coach", None)
        labels = [lbl for _k, lbl in ui_kit.PRESET_LABELS]
        to_key = {lbl: k for k, lbl in ui_kit.PRESET_LABELS}
        self.preset_seg = ctk.CTkSegmentedButton(slot, values=labels, height=28, font=self.fonts.small,
                                                 fg_color=PANEL_LO, selected_color=GOLD_DARK,
                                                 selected_hover_color=ACCENT_DIM, unselected_color=PANEL_LO,
                                                 unselected_hover_color=PANEL_HI, text_color=TEXT, corner_radius=RADIUS,
                                                 command=self.cb(lambda lbl: self.apply_preset(to_key.get(lbl, ""))))
        self.preset_seg.grid(row=0, column=0)
        self.preset_lbl = self._row_desc(slot)
        self._refresh_preset_label()

        s = self._section(body, 1, "Alertes de gank", "Annonces vocales quand un ennemi menace ta position. "
                                                      "▶ fait entendre un exemple.", icon="swords")
        for field, title, key in (("alert_jungler_approach", "Jungler ennemi qui approche", "jungler_approach"),
                                  ("alert_roam", "Roam d'un autre ennemi", "roam_approach"),
                                  ("alert_collapse", "Plusieurs ennemis convergent", "collapse"),
                                  ("alert_jungler_spotted", "Jungler ennemi aperçu", "jungler_spotted"),
                                  ("alert_laner_mia", "Adversaire de voie disparu", "laner_mia")):
            self._switch_row(s, field, title, ex[key])
            self._example_button(key)

        s = self._section(body, 2, "Sensibilité",
                          "Plus la sensibilité est haute, plus les alertes arrivent tôt (et plus souvent).",
                          icon="target")
        self.radius_lbl: Any = None
        self._slider_row(s, "sensitivity", "Sensibilité des alertes", self._radius_text(), 0.6, 1.6, 0.05,
                         lambda v: f"× {fmt_decimal_fr(v, 2)}", float,
                         on_change=lambda _v: (self._refresh_radius_text(), self._refresh_preset_label()))
        self.radius_lbl = self._last_slot.desc_label

        s = self._section(body, 3, "Aides de jeu", "Rappels basés uniquement sur l'API officielle de Riot.",
                          icon="clock")
        self._switch_row(s, "objective_timers", "Minuteurs des objectifs", ex["objective_soon"])
        self._example_button("objective_soon")
        self._switch_row(s, "recall_reminder", "Rappel pour dépenser ton or", ex["recall_gold"])
        self._example_button("recall_gold")
        self._slider_row(s, "recall_gold_threshold", "Seuil d'or du rappel", "Or à partir duquel le coach "
                         "te conseille de rentrer.", 300, 5000, 50, lambda v: f"{fmt_int_fr(v)} PO", int)
        self._switch_row(s, "control_ward_reminder", "Balise de contrôle", ex["control_ward"])
        self._example_button("control_ward")
        self._switch_row(s, "death_recap", "Récap de mort", ex["death_recap"])
        self._example_button("death_recap")
        self._switch_row(s, "break_reminder", "Conseil de pause",
                         "Après 3 défaites d'affilée : « une pause de 10 minutes aide à rester concentré ».")

        s = self._section(body, 4, "Voix", "La voix neurale (en ligne) est la plus naturelle ; les voix Windows "
                                           "servent de secours hors ligne.", icon="voice")
        self._choice_row(s, "voice_engine", "Moteur de voix", "« Automatique » utilise la voix neurale si "
                         "Internet répond, sinon une voix Windows.", self._engine_choices(), width=260,
                         on_change=lambda _v: self._refresh_voice_rows())
        self._choice_row(s, "neural_voice", "Voix neurale", "Voix Microsoft en ligne (française).",
                         self._neural_choices(), width=260)
        self._neural_row = self._last_row
        if hasattr(self.cfg, "neural_rate"):
            self._slider_row(s, "neural_rate", "Vitesse de la voix neurale", "Défaut : +15 %.", -50, 100, 5,
                             lambda v: str(v).replace("%", " %") if isinstance(v, str) else f"{int(v):+d} %",
                             lambda v: f"{int(round(v)):+d}%", to_float=_pct_value)
            self._neural_rate_row = self._last_row
        _row, slot = self._row(s, "Voix Windows", "« Automatique » choisit la meilleure voix française installée.")
        self._windows_voice_row = _row
        self.voice_menu = ctk.CTkOptionMenu(
            slot, values=[AUTO_VOICE], command=self.cb(self._on_voice_choice), width=300, height=28,
            font=self.fonts.small, dropdown_font=self.fonts.small, fg_color=PANEL_HI, button_color=HOVER,
            button_hover_color=HOVER, text_color=TEXT, dropdown_fg_color=PANEL_HI,
            dropdown_hover_color=HOVER, dropdown_text_color=TEXT, corner_radius=RADIUS, dynamic_resizing=False)
        self.voice_menu.grid(row=0, column=0)
        self.voice_menu.set(self.cfg.voice_name or AUTO_VOICE)
        self._widgets_by_field["voice_name"] = lambda: self.voice_menu.set(self.cfg.voice_name or AUTO_VOICE)
        self._slider_row(s, "voice_rate", "Vitesse", "De -10 (lent) à 10 (rapide). Défaut : 2.", -10, 10, 1,
                         lambda v: f"{int(v):+d}" if int(v) else "0", int)
        self._slider_row(s, "voice_volume", "Volume", None, 0, 100, 1, lambda v: f"{int(v)} %", int)
        self._switch_row(s, "beep_on_danger", "Bip avant un danger", "Deux bips courts avant « Gank ! ».")
        self._refresh_voice_rows()

        s = self._section(body, 5, "Raccourcis clavier",
                          "Touches globales (RegisterHotKey, comme Discord ou OBS) : rien n'est envoyé au jeu.",
                          icon="keyboard")
        for field, title, desc in (
                ("hotkey_jungler", "Où est le jungler ?", "Annonce la dernière position connue du jungler ennemi."),
                ("hotkey_mute", "Couper / rétablir la voix", None),
                ("hotkey_overlay", "Afficher / masquer l'overlay", None),
                ("hotkey_ai", "Demander à l'IA", "Conseil d'achat et de macro immédiat (si un fournisseur "
                 "d'IA est configuré dans Réglages > IA).")):
            if not hasattr(self.cfg, field):
                continue
            cur = getattr(self.cfg, field) or "Désactivé"
            values = list(HOTKEY_CHOICES) + ([cur] if cur not in HOTKEY_CHOICES else [])
            self._choice_row(s, field, title, desc, [("" if v == "Désactivé" else v, v) for v in values],
                             width=150)
        try:
            self._build_coach_extras(body, 6)
        except Exception:
            log.exception("Cannot build the build-advice / caster sections")
        self._tabs(page, body, (("Alertes", ("Préréglage", "Alertes de gank", "Sensibilité")),
                                ("Voix", ("Voix", "Mode annonceur")),
                                ("Aides", ("Aides de jeu", "Conseils d'achat")),
                                ("Touches", ("Raccourcis clavier",))))
        return page

    def _build_coach_extras(self, body: Any, row: int) -> None:
        """Alertes page: build advice switches + "mode annonceur" (hype.py)."""
        if hasattr(self.cfg, "item_advice"):
            s = self._section(body, row, "Conseils d'achat", "Le prochain objet adapté à la partie (soins "
                              "adverses, ennemi très fort, dégâts magiques…), d'après l'API officielle.", icon="star")
            self._switch_row(s, "item_advice", "Conseils d'achat", "Ligne « Prochain objet » dans le HUD.")
            self._switch_row(s, "item_advice_toasts", "Bandeau à l'écran",
                             "Affiche le conseil en bandeau au retour en base, à la mort, aux niveaux 6/11/16.")
            self._switch_row(s, "item_advice_speak", "Lire les conseils d'achat à voix haute",
                             "Désactivé par défaut : le conseil reste écrit.")
        if hasattr(self.cfg, "caster_style"):
            from treeaicoach.hype import STYLE_LABELS  # noqa: PLC0415

            s = self._section(body, row + 1, "Mode annonceur",
                              "Probabilité de victoire en direct et, en style « Caster esport », des annonces "
                              "enflammées pour les grands moments (multikill, shutdown, ace, vol de Baron).",
                              icon="star")
            self._choice_row(s, "caster_style", "Style", "Sobre : rien n'est lu · Coach : la probabilité de "
                             "victoire est lue sur les gros retournements (+/-15 points, 3 min max) · Caster : "
                             "en plus, des annonces de commentateur.", STYLE_LABELS, segmented=True)
            if hasattr(self.cfg, "win_prob_hud"):
                self._switch_row(s, "win_prob_hud", "Afficher la probabilité de victoire",
                                 "Dans le HUD et le tableau de bord (modèle sur l'or, kills, tours, dragons, "
                                 "Baron et Elder).")

    def _row_desc(self, slot: Any) -> Any:
        """The description label of the row owning ``slot`` (created empty if the row had none)."""
        lbl = getattr(slot, "desc_label", None)
        if lbl is None:
            left = slot.master.grid_slaves(row=0, column=0)[0]
            lbl = self._label(left, " ", self.fonts.tiny, MUTED, anchor="w", justify="left", wraplength=430)
            lbl.grid(row=1, column=0, sticky="w", pady=(3, 0))
            slot.desc_label = lbl
        return lbl

    def _example_button(self, key: str) -> None:
        """A small "▶" button in the last row: speaks an example of this alert."""
        slot = self._last_slot
        b = self.ctk.CTkButton(slot, text="", width=30, height=28, corner_radius=RADIUS, fg_color="transparent",
                               hover_color=PANEL_HI, border_width=1, border_color=BORDER_GOLD,
                               image=self._icon("play", 11, GOLD), command=self.cb(lambda: self.play_example(key)))
        for w in slot.grid_slaves(row=0):
            w.grid_configure(column=int(w.grid_info().get("column", 0)) + 1)
        b.grid(row=0, column=0, padx=(0, 14))
        self._tip(b, "Entendre un exemple")

    @_guarded
    def play_example(self, key: str) -> None:
        """Speak the example sentence of an alert type (always audible, even when muted by settings)."""
        if self.voice is None:
            self.show_error("La synthèse vocale n'est pas disponible.")
            return
        text, level = self._examples.get(key, ("Attention, Lee Sin approche !", 1))
        self.voice.say(text, level)
        if getattr(self.voice, "backend", "") == "print":
            self.show_toast("Voix indisponible sur ce système : le message est écrit dans le journal.", "warning")

    def _voice_api(self) -> Any:
        """The voice object (or the VoiceEngine class before it exists) for the list_* selectors."""
        if self.voice is not None:
            return self.voice
        try:
            from treeaicoach.voice import VoiceEngine  # noqa: PLC0415

            return VoiceEngine
        except Exception:
            return None

    def _engine_choices(self) -> list[tuple[str, str]]:
        labels = dict(ENGINE_LABELS)
        values: list[str] = []
        fn = getattr(self._voice_api(), "list_engines", None)
        try:
            got = fn() if callable(fn) else None
            for item in got or []:
                v = item[0] if isinstance(item, (tuple, list)) else item
                if isinstance(item, (tuple, list)) and len(item) > 1 and isinstance(item[1], str):
                    labels.setdefault(str(v), item[1])
                values.append(str(v))
        except Exception:
            log.debug("list_engines failed", exc_info=True)
        if not values:
            values = [v for v, _l in ENGINE_LABELS]
        cur = getattr(self.cfg, "voice_engine", "auto")
        if cur not in values:
            values.append(cur)
        return [(v, labels.get(v, v)) for v in values]

    def _neural_choices(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        fn = getattr(self._voice_api(), "list_neural_voices", None)
        try:
            got = fn() if callable(fn) else None
            for item in got or []:
                if isinstance(item, (tuple, list)) and item:
                    out.append((str(item[0]), str(item[1]) if len(item) > 1 else str(item[0])))
                elif isinstance(item, str):
                    out.append((item, item))
        except Exception:
            log.debug("list_neural_voices failed", exc_info=True)
        if not out:
            try:
                from treeaicoach.tts_neural import NEURAL_VOICES  # noqa: PLC0415

                out = [(v, lbl) for v, lbl in NEURAL_VOICES]
            except Exception:
                out = [("fr-FR-DeniseNeural", "Denise (femme, France)")]
        cur = getattr(self.cfg, "neural_voice", "")
        if cur and cur not in dict(out):
            out.append((cur, cur))
        return out

    def _refresh_voice_rows(self) -> None:
        """Neural voice row only for auto / neural; Windows voice row only for auto / onecore / sapi."""
        eng = getattr(self.cfg, "voice_engine", "auto")
        for row, show in ((getattr(self, "_neural_row", None), eng in ("auto", "neural")),
                          (getattr(self, "_neural_rate_row", None), eng in ("auto", "neural")),
                          (getattr(self, "_windows_voice_row", None), eng != "neural")):
            if row is None:
                continue
            try:
                lbl = row.grid_slaves(row=0, column=0)[0].grid_slaves(row=0, column=0)[0]
                lbl.configure(text_color=TEXT if show else DIM)
                for w in row.grid_slaves(row=0, column=1)[0].winfo_children():
                    try:
                        w.configure(state="normal" if show else "disabled")
                    except Exception:
                        pass
            except Exception:
                log.debug("voice rows refresh failed", exc_info=True)

    def _radius_text(self) -> str:
        try:
            from treeaicoach.geometry import to_game_units  # noqa: PLC0415

            warn = to_game_units(self.cfg.effective_warn_radius())
            danger = to_game_units(self.cfg.effective_danger_radius())
        except Exception:
            warn = self.cfg.effective_warn_radius() * 14870.0
            danger = self.cfg.effective_danger_radius() * 14870.0
        return (f"Rayon d'alerte ≈ {fmt_int_fr(round(warn, -2))} unités · "
                f"danger ≈ {fmt_int_fr(round(danger, -2))} unités")

    def _refresh_radius_text(self) -> None:
        if self.radius_lbl is not None:
            try:
                self.radius_lbl.configure(text=self._radius_text())
            except Exception:
                pass

    def _on_voice_choice(self, label: str) -> None:
        self.set_option("voice_name", "" if label == AUTO_VOICE else label)

    def _load_voices(self) -> None:
        voice = self.voice
        if voice is None:
            return

        def job() -> list[str]:
            return list(voice.list_voices() or [])

        def done(voices: list[str]) -> None:
            self._voices = [v for v in voices if isinstance(v, str) and v][:60]
            values = [AUTO_VOICE] + self._voices
            if self.cfg.voice_name and self.cfg.voice_name not in values:
                values.append(self.cfg.voice_name)
            self.voice_menu.configure(values=values)
            self.voice_menu.set(self.cfg.voice_name or AUTO_VOICE)

        self._dispatcher.run(job, done, name="TreeAI-ui-voices")

    # ------------------------------------------------------------------ overlay page
    def _build_overlay_page(self) -> Any:
        page, right, body = self._page("Overlay", "Ce qui est dessiné sur ta minimap")
        self.btn_move = self._button(right, "Déplacer les fenêtres", self.toggle_move_mode, "secondary", icon="move",
                                     height=26)
        self.btn_move.grid(row=0, column=0)
        self._tip(self.btn_move, "Fais glisser le radar et le HUD à la souris (Windows).")
        prev = lambda _v=None: self._schedule_overlay_preview()  # noqa: E731
        self._build_overlay_preview(body, 0)
        s = self._section(body, 1, "Affichage sur la minimap",
                          "Fenêtres transparentes traversées par la souris (jeu en Sans bordure ou Fenêtré). "
                          "Rien n'est injecté dans le jeu.", icon="map")
        self._switch_row(s, "overlay_enabled", "Activer l'overlay", "Interrupteur général (F11 en jeu).",
                         on_change=lambda v: (prev(), self._sync_quick()))
        if hasattr(self.cfg, "overlay_mode"):
            self._choice_row(s, "overlay_mode", "Où dessiner", "Sur la minimap : marques posées sur la vraie "
                             "minimap. Radar : copie agrandie à côté. Aucun : seulement le HUD et le flash.",
                             OVERLAY_MODES, segmented=True, on_change=lambda _v: (prev(), self._refresh_radar_rows()))
        if hasattr(self.cfg, "overlay_show_frame"):
            self._switch_row(s, "overlay_show_frame", "Cadre discret « TreeAI »",
                             "Fin liseré autour de la minimap pour voir que le coach est actif.",
                             on_change=prev)
        if hasattr(self.cfg, "overlay_hide_from_capture"):
            self._switch_row(s, "overlay_hide_from_capture", "Masquer des captures et du stream",
                             "Les marques sur la minimap n'apparaissent pas sur tes captures d'écran ni sur OBS "
                             "(Windows 10 2004 ou plus récent).")
        if self._overlay_supports("overlay_opacity"):
            self._slider_row(s, "overlay_opacity", "Opacité", "Transparence des marques et du HUD.", 0.3, 1.0, 0.05,
                             lambda v: f"{int(round(float(v) * 100))} %", float, on_change=prev)
        if self._overlay_supports("overlay_scale"):
            self._slider_row(s, "overlay_scale", "Taille des marques", None, 0.6, 1.6, 0.1,
                             lambda v: f"× {fmt_decimal_fr(v, 1)}", float, on_change=prev)

        s = self._section(body, 2, "HUD et flash", "Panneau compact (jauge de menace, jungler, objectifs) et "
                                                    "alerte visuelle en cas de gank.", icon="eye")
        self._switch_row(s, "hud_enabled", "Panneau HUD", "Jauge de menace, jungler, 5 ennemis, objectifs.",
                         on_change=prev)
        self._position_menus: dict[str, Any] = {}
        self._position_menus["hud"] = self._choice_row(
            s, "hud_position", "Position du HUD", None, HUD_POSITIONS, width=230, on_change=prev)
        self._switch_row(s, "danger_flash", "Flash de danger", "Cadre rouge sur les bords de l'écran en cas de gank.",
                         on_change=prev)
        if hasattr(self.cfg, "ward_guide"):
            hk_ward = getattr(self.cfg, "hotkey_ward", "") or "sans raccourci"
            self._switch_row(s, "ward_guide", "Guide de balise",
                             f"Où poser ta balise : repère sur la minimap (après un retour, avant un objectif, "
                             f"ou {hk_ward}). Disparaît dès que la balise est posée.")
        if hasattr(self.cfg, "ward_world"):
            self._switch_row(s, "ward_world", "Repère dans le jeu",
                             "« Ward ici » au sol sur le buisson conseillé, ou flèche au bord de l'écran.")

        s = self._section(body, 3, "Position possible dans le brouillard",
                          "Zone qui grandit là où un ennemi caché peut se trouver (dernière position vue "
                          "+ vitesse de déplacement). Aucune prédiction.", icon="clock")
        self._choice_row(s, "fog_mode", "Cercle de position", "Pour le jungler seulement, tous les ennemis, "
                         "ou désactivé.", FOG_MODES, segmented=True, on_change=prev)
        self._slider_row(s, "fog_max_s", "Durée maximale", "Au-delà, la zone est trop grande : elle s'efface.",
                         10, 180, 5, lambda v: f"{int(v)} s", float)

        s = self._section(body, 4, "Radar à côté de la minimap", "Utilisé seulement en mode « Radar ».",
                          icon="target")
        self._radar_section = s
        self._position_menus["radar"] = self._choice_row(
            s, "radar_position", "Position du radar", None, RADAR_POSITIONS, width=230, on_change=prev)
        self._slider_row(s, "radar_scale", "Taille du radar", "1,0 = même taille que la minimap.", 0.5, 2.0, 0.1,
                         lambda v: f"× {fmt_decimal_fr(v, 1)}", float, on_change=prev)
        self._refresh_position_menus()
        self._refresh_radar_rows()
        try:
            self._build_plays_section(body, 10)
        except Exception:
            log.exception("Cannot build the rated plays section")
        return page

    def _build_plays_section(self, body: Any, row: int) -> None:
        """Overlay page: rated plays (plays.py / fx_overlay.py, chess.com style badges)."""
        if not hasattr(self.cfg, "plays_enabled"):
            return
        s = self._section(body, row, "Coups notés", "Après un moment clé, un badge note ton coup : coup de "
                          "maître, excellent, erreur, gaffe… Jamais pendant un combat.")
        prev = lambda _v=None: self._schedule_overlay_preview()  # noqa: E731
        self._switch_row(s, "plays_enabled", "Afficher les coups notés", "Badge à l'écran et précision dans le "
                         "rapport d'après-partie.", on_change=prev)
        if hasattr(self.cfg, "plays_position"):
            self._choice_row(s, "plays_position", "Position du badge", "En haut au centre de l'écran, ou près de "
                             "la minimap.", (("top_center", "Haut, au centre"), ("minimap", "Près de la minimap")),
                             segmented=True, on_change=prev)
        if hasattr(self.cfg, "plays_sound"):
            self._switch_row(s, "plays_sound", "Son pour les bons coups", "Petit son court.")
        if hasattr(self.cfg, "plays_sound_negative"):
            self._switch_row(s, "plays_sound_negative", "Son aussi pour les erreurs", "Désactivé par défaut.")

    def _overlay_supports(self, field: str) -> bool:
        """Whether the overlay module reads an optional look setting (``SUPPORTED_SETTINGS``)."""
        if not hasattr(self.cfg, field):
            return False
        try:
            import importlib  # noqa: PLC0415

            mod = importlib.import_module("treeaicoach.overlay")
            return field in tuple(getattr(mod, "SUPPORTED_SETTINGS", ()) or ())
        except Exception:
            return False

    def _refresh_radar_rows(self) -> None:
        """Dim the radar section when the map mode is not "radar"."""
        s = getattr(self, "_radar_section", None)
        if s is None:
            return
        on = getattr(self.cfg, "overlay_mode", "radar") == "radar"
        try:
            s.card.configure(border_color=BORDER if on else _blend(BORDER, BG, 0.5))
            for w in s.winfo_children():
                for lbl in w.winfo_children():
                    for ch in lbl.winfo_children():
                        if isinstance(ch, self.ctk.CTkLabel) and ch.cget("text_color") in (TEXT, DIM):
                            ch.configure(text_color=TEXT if on else DIM)
        except Exception:
            log.debug("radar rows refresh failed", exc_info=True)

    def _position_choices(self, which: str) -> list[tuple[str, str]]:
        base = RADAR_POSITIONS if which == "radar" else HUD_POSITIONS
        xy = self.cfg.radar_xy if which == "radar" else self.cfg.hud_xy
        return [(v, lbl) for v, lbl in base if v != "custom" or xy is not None]

    def _schedule_overlay_preview(self) -> None:
        if self._overlay_preview_job is not None:
            try:
                self.root.after_cancel(self._overlay_preview_job)
            except Exception:
                pass
        self._overlay_preview_job = self.root.after(300, self._render_overlay_preview)

    def _build_overlay_preview(self, body: Any, row: int) -> None:
        """Top of the Overlay page: the real overlay layers for the current settings (ui_preview.py)."""
        ctk = self.ctk
        card = ctk.CTkFrame(body, fg_color="transparent", corner_radius=0)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 20))
        card.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew")
        head.grid_columnconfigure(0, weight=1)
        self._caption(head, "Aperçu", MUTED, anchor="w").grid(row=0, column=0, sticky="w")
        self.overlay_preview_tag = self._label(head, "", self.fonts.caps, DIM, anchor="e")
        self.overlay_preview_tag.grid(row=0, column=1, sticky="e")
        self._hline(card, LINE_STRONG).grid(row=1, column=0, sticky="ew", pady=(4, 10))
        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.grid(row=2, column=0, sticky="w")
        self._overlay_tiles: dict[str, tuple[Any, Any]] = {}

        def tile(parent: Any, key: str, caption: str, r: int, c: int, **gkw: Any) -> None:
            f = ctk.CTkFrame(parent, fg_color="transparent")
            f.grid(row=r, column=c, sticky="nw", **gkw)
            img = ctk.CTkLabel(f, text="", fg_color="transparent")
            img.grid(row=0, column=0, sticky="nw")
            cap = self._label(f, caption, self.fonts.tiny, DIM, anchor="w")
            cap.grid(row=1, column=0, sticky="w", pady=(3, 0))
            self._overlay_tiles[key] = (f, img)
            if key != "screen":
                f.grid_remove()          # shown once rendered

        left = ctk.CTkFrame(grid, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nw", padx=(0, 16))
        tile(left, "screen", "Écran entier : où chaque élément s'affiche", 0, 0)
        tile(left, "badge", "Coup noté (exemple)", 1, 0, pady=(10, 0))
        right = ctk.CTkFrame(grid, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nw")
        tile(right, "hud", "HUD", 0, 0)
        tile(right, "minimap", "Marques sur la minimap", 1, 0, pady=(10, 0))
        tile(right, "radar", "Radar", 2, 0, pady=(10, 0))
        self.overlay_preview = self._overlay_tiles["screen"][1]
        self.overlay_preview.configure(text="Génération de l'aperçu…", text_color=DIM, font=self.fonts.small,
                                       width=400, height=225, fg_color=SUNKEN, corner_radius=0)
        self._overlay_preview_busy = False
        self._overlay_preview_live = False

    @_guarded
    def _render_overlay_preview(self) -> None:
        self._overlay_preview_job = None
        if self._current_page != "overlay" or self._closing or getattr(self, "_overlay_preview_busy", False):
            return
        cfg = self.cfg
        scale = max(0.5, self._scaled(100) / 100)
        live_state = self._overlay_state() if self._in_game() else None

        def job() -> tuple[dict[str, Image.Image], bool]:
            from treeaicoach import ui_preview  # noqa: PLC0415

            comp = ui_preview.compose(cfg, live_state)
            return ui_preview.preview_images(comp, screen_w=int(400 * scale), zoom=0.75 * scale), comp.live

        def done(res: tuple[dict[str, Image.Image], bool]) -> None:
            self._overlay_preview_busy = False
            imgs, live = res
            tiles = getattr(self, "_overlay_tiles", {})
            for key, (frame, lbl) in tiles.items():
                im = imgs.get(key)
                if im is None:
                    frame.grid_remove()
                    continue
                ci = self.ctk.CTkImage(light_image=im, dark_image=im,
                                       size=(int(im.width / scale), int(im.height / scale)))
                self._images[f"overlay-preview-{key}"] = ci
                lbl.configure(image=ci, text="", fg_color="transparent", width=0, height=0)
                frame.grid()
            tag = getattr(self, "overlay_preview_tag", None)
            if tag is not None:
                off = not bool(getattr(cfg, "overlay_enabled", True))
                tag.configure(text="OVERLAY DÉSACTIVÉ" if off else ("EN DIRECT · TA PARTIE" if live else
                                                                    "EXEMPLE · GANK EN COURS · 1920 × 1080"),
                              text_color=WARNING if off else (ACCENT if live else DIM))
            self._overlay_preview_live = live
            if live and self._current_page == "overlay" and self._overlay_preview_job is None:
                self._overlay_preview_job = self.root.after(1500, self._render_overlay_preview)

        def failed(exc: BaseException) -> None:
            self._overlay_preview_busy = False
            log.debug("overlay preview failed: %s", exc)

        self._overlay_preview_busy = True
        self._dispatcher.run(job, done, self.cb(failed), name="TreeAI-ui-overlay-preview")

    # ------------------------------------------------------------------ analysis page
    def _build_analysis_page(self) -> Any:
        ctk = self.ctk
        page, right, body = self._page("Analyses", "Parties, progrès et replay")
        b = self._button(right, "Dernier rapport", self.open_last_report, "primary", icon="report", height=26)
        b.grid(row=0, column=0, padx=(0, 6))
        self._tip(b, "Ouvre le rapport de ta dernière partie dans le navigateur.")
        b = self._button(right, "", self.refresh_games, "ghost", icon="refresh", width=30, height=26)
        b.grid(row=0, column=1, padx=(0, 2))
        self._tip(b, "Actualiser la liste")
        b = self._button(right, "", self.open_games_dir, "ghost", icon="folder", width=30, height=26)
        b.grid(row=0, column=2, padx=(0, 2))
        self._tip(b, "Ouvrir le dossier des parties et des rapports")
        b = self._button(right, "", self.copy_share_summary, "ghost", icon="copy", width=30, height=26)
        b.grid(row=0, column=3)
        self._tip(b, "Copier un résumé de ta dernière partie à partager (Discord, réseaux).")
        body._sections = []  # type: ignore[attr-defined]

        # ---------------------------------------------------------------- tab "Parties"
        games = ctk.CTkFrame(body, fg_color="transparent", corner_radius=0)
        games.grid(row=0, column=0, sticky="ew")
        games.grid_columnconfigure(0, weight=1)
        games.title = "Parties"  # type: ignore[attr-defined]
        body._sections.append(games)
        top = ctk.CTkFrame(games, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        top.grid_columnconfigure(0, weight=1)
        self.session_scope = self._label(top, "SESSION", self.fonts.caps, DIM, anchor="w")
        self.session_scope.grid(row=0, column=0, sticky="w")
        self.lcu_status = self._label(top, "Client LoL : …", self.fonts.tiny, DIM, anchor="e")
        self.lcu_status.grid(row=0, column=1, sticky="e")
        self._tip(self.lcu_status, "Après chaque partie, TreeAI lit l'historique du client League of Legends "
                                   "(API locale officielle, lecture seule) : positions exactes, écarts d'or et "
                                   "fiabilité de ses alertes dans le rapport.")
        strip = ctk.CTkFrame(games, fg_color=PANEL, corner_radius=RADIUS, border_width=1, border_color=LINE)
        strip.grid(row=1, column=0, sticky="ew", pady=(0, 16))
        self.stat_labels: dict[str, tuple[Any, Any]] = {}
        for i, (key, label, col) in enumerate((("games", "Parties", TEXT), ("wins", "Victoires", SAFE),
                                               ("deaths", "Morts / partie", DANGER),
                                               ("avoided", "Ganks évités", TEXT))):
            strip.grid_columnconfigure(2 * i, weight=1, uniform="stat")
            if i:
                ctk.CTkFrame(strip, width=1, height=1, fg_color=LINE, corner_radius=0).grid(
                    row=0, column=2 * i - 1, sticky="ns", pady=10)
            cell = ctk.CTkFrame(strip, fg_color="transparent")
            cell.grid(row=0, column=2 * i, sticky="ew", padx=16, pady=10)
            self._caption(cell, label, DIM, anchor="w").grid(row=0, column=0, sticky="w")
            v = self._label(cell, "-", self.fonts.stat, col, anchor="w")
            v.grid(row=1, column=0, sticky="w")
            sub = self._label(cell, " ", self.fonts.tiny, MUTED, anchor="w")
            sub.grid(row=2, column=0, sticky="w")
            self.stat_labels[key] = (v, sub)
        self.games_box = ctk.CTkFrame(games, fg_color="transparent")
        self.games_box.grid(row=2, column=0, sticky="ew")
        self.games_box.grid_columnconfigure(1, weight=1)
        self._games_empty(text="Chargement…")

        # ---------------------------------------------------------------- tab "Progrès"
        prog = ctk.CTkFrame(body, fg_color="transparent", corner_radius=0)
        prog.grid(row=1, column=0, sticky="ew")
        prog.grid_columnconfigure(0, weight=1)
        prog.title = "Progrès"  # type: ignore[attr-defined]
        body._sections.append(prog)
        self.progress_box = prog
        self._progress_sig: Any = None
        self._label(prog, "Chargement…", self.fonts.small, DIM, anchor="w").grid(row=0, column=0, sticky="w")

        # ---------------------------------------------------------------- tab "Replay"
        rp = ctk.CTkFrame(body, fg_color="transparent", corner_radius=0)
        rp.grid(row=2, column=0, sticky="ew")
        rp.grid_columnconfigure(1, weight=1)
        rp.title = "Replay"  # type: ignore[attr-defined]
        body._sections.append(rp)
        self._build_replay(rp)

        def on_tab(label: str) -> None:
            if label == "Progrès":
                self.refresh_progress()
            elif label == "Replay":
                self._replay_ensure_loaded()
        self._analysis_tabs = self._tabs(page, body, (("Parties", ("Parties",)), ("Progrès", ("Progrès",)),
                                                      ("Replay", ("Replay",))))
        self._analysis_page = page
        for lbl, btn in self._analysis_tabs.items():
            btn.configure(command=self.cb(lambda ll=lbl: (page.select_tab(ll), on_tab(ll))))
        return page

    def _games_empty(self, text: str | None = None) -> None:
        for w in self.games_box.winfo_children():
            w.destroy()
        msg = text or "Aucune partie enregistrée pour l'instant."
        self._label(self.games_box, msg, self.fonts.body, TEXT, anchor="w").grid(
            row=0, column=0, columnspan=8, sticky="w", pady=(8, 2))
        if text is None:
            self._label(self.games_box, "Joue une partie avec l'analyse active : le rapport (morts, ganks, jungler "
                                        "adverse, conseils) apparaît ici.", self.fonts.small, MUTED, anchor="w",
                        wraplength=560, justify="left").grid(row=1, column=0, columnspan=8, sticky="w")

    @_guarded
    def refresh_games(self) -> None:
        """Reload the game history (background thread)."""
        def job() -> list[dict]:
            fn = _report_function("list_games")
            if fn is None:
                return []
            games = fn(50) or []
            return [g for g in games if isinstance(g, dict)]

        self._dispatcher.run(job, self._show_games, self.cb(lambda e: self._games_empty(
            text="Impossible de lire l'historique des parties")), name="TreeAI-ui-games")
        self._refresh_lcu_status()
        self._progress_sig = None

    @_guarded
    def _refresh_lcu_status(self) -> None:
        """"Client LoL : connecté / non trouvé" on the Analyses page (probed on a worker thread)."""
        lbl = getattr(self, "lcu_status", None)
        if lbl is None:
            return

        def job() -> str:
            if not getattr(self.cfg, "lcu_enabled", True):
                return "Client LoL : désactivé"
            from treeaicoach.lcu import get_default_client  # noqa: PLC0415

            return get_default_client().status_text()

        def done(text: str) -> None:
            self._lcu_text = str(text or "")
            try:
                lbl.configure(text=ui_text(text), text_color=SAFE if text.endswith("connecté") else DIM)
            except Exception:
                pass

        self._dispatcher.run(job, done, None, name="TreeAI-ui-lcu")

    @_guarded
    def _show_games(self, games: list[dict]) -> None:
        self._games = games
        st = session_stats(games)
        self.session_scope.configure(text=f"SESSION · {st['scope'].upper()}")
        wr = st["winrate"]
        dpg = st["deaths_per_game"]
        vals = {
            "games": (str(st["games"]), "parties analysées"),
            "wins": (str(st["wins"]), f"{round(100 * wr)} % de victoires" if wr is not None else " "),
            "deaths": (fmt_decimal_fr(dpg, 1) if dpg is not None else "-", "en moyenne"),
            "avoided": (str(st["ganks_avoided"]), f"sur {st['ganks']} ganks subis" if st["ganks"] else "aucun gank"),
        }
        for key, (v, sub) in vals.items():
            self.stat_labels[key][0].configure(text=v)
            self.stat_labels[key][1].configure(text=sub)
        for w in self.games_box.winfo_children():
            w.destroy()
        self._replay_games_menu(games)
        self.refresh_pregame()
        if not games:
            self._games_empty()
            return
        box = self.games_box
        for c, (txt, anchor) in enumerate((("", "w"), ("Champion", "w"), ("Résultat", "w"), ("K / D / A", "e"),
                                           ("Ganks", "e"), ("Précision", "e"), ("Durée", "e"), ("", "e"))):
            if txt:
                cap = self._caption(box, txt, DIM, anchor=anchor)
                cap.grid(row=0, column=c, sticky=anchor, padx=(0, 16), pady=(0, 4))
                if txt == "Précision":
                    self._tip(cap, "Précision des coups notés (sur 100) : coups de maître, erreurs, gaffes… "
                                   "« - » : partie non notée.")
        for c, w in ((0, 36), (2, 70), (3, 70), (4, 48), (5, 64), (6, 48)):
            box.grid_columnconfigure(c, minsize=w)
        self._hline(box, LINE_STRONG).grid(row=1, column=0, columnspan=8, sticky="ew")
        for i, g in enumerate(games[:50]):
            self._game_row(i, g)

    def _game_row(self, i: int, g: dict) -> None:
        ctk = self.ctk
        box = self.games_box
        r = 2 + 2 * i
        alias = str(game_field(g, "champion", "alias", default="") or "")
        name = str(game_field(g, "champion_name", "name", default="") or alias or "Champion inconnu")
        icon = None
        try:
            from treeaicoach.champions import get_default_db  # noqa: PLC0415

            icon = get_default_db().load_icon(alias) if alias else None
        except Exception:
            icon = None
        res = game_result(g)
        pil = square_icon(icon, 56, bg=BG)
        img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(28, 28))
        self._images[f"game-{i}"] = img
        ctk.CTkLabel(box, text="", image=img, fg_color="transparent").grid(row=r, column=0, sticky="w", pady=6)
        cell = ctk.CTkFrame(box, fg_color="transparent")
        cell.grid(row=r, column=1, sticky="w", padx=(0, 16))
        self._label(cell, name, self.fonts.h3, TEXT, anchor="w").grid(row=0, column=0, sticky="w")
        when = fmt_game_date(game_datetime(g))
        pos = game_field(g, "position")
        sub = when + (f" · {_POSITION_FR.get(pos.upper(), pos.title())}" if isinstance(pos, str) and pos else "")
        self._label(cell, sub, self.fonts.tiny, DIM, anchor="w").grid(row=1, column=0, sticky="w")
        rtxt, rcol = {"win": ("Victoire", SAFE), "lose": ("Défaite", DANGER)}.get(
            res or "", ("Inachevée" if game_field(g, "incomplete") else "-", MUTED))
        self._label(box, rtxt, self.fonts.h3, rcol, anchor="w").grid(row=r, column=2, sticky="w", padx=(0, 16))
        k, d, a = (_int_or_none(game_field(g, x)) for x in ("kills", "deaths", "assists"))
        kda = f"{k if k is not None else '?'} / {d if d is not None else '?'} / {a if a is not None else '?'}"
        self._label(box, kda, self.fonts.num, TEXT, anchor="e").grid(row=r, column=3, sticky="e", padx=(0, 16))
        ganks = _int_or_none(game_field(g, "ganks"))
        surv = _int_or_none(game_field(g, "ganks_survived"))
        gtxt = "-" if ganks is None else (f"{surv}/{ganks}" if surv is not None and ganks else str(ganks))
        gl = self._label(box, gtxt, self.fonts.num, TEXT, anchor="e")
        gl.grid(row=r, column=4, sticky="e", padx=(0, 16))
        self._tip(gl, "Ganks évités / ganks subis")
        prec = _int_or_none(game_field(g, "precision"))
        pl = self._label(box, "-" if prec is None else str(prec), self.fonts.num, precision_color(prec), anchor="e")
        pl.grid(row=r, column=5, sticky="e", padx=(0, 16))
        self._tip(pl, "Précision des coups notés (sur 100)" if prec is not None else "Partie non notée")
        dur = game_field(g, "duration")
        self._label(box, fmt_clock(dur) if isinstance(dur, (int, float)) and dur > 0 else "-", self.fonts.small,
                    MUTED, anchor="e").grid(row=r, column=6, sticky="e", padx=(0, 16))
        btns = ctk.CTkFrame(box, fg_color="transparent")
        btns.grid(row=r, column=7, sticky="e")
        self._button(btns, "Rapport", lambda gg=g: self.open_report(gg), "secondary", width=70,
                     height=24).grid(row=0, column=0, padx=(0, 4))
        self._button(btns, "Replay", lambda gg=g: self.open_replay(gg), "secondary", width=62,
                     height=24).grid(row=0, column=1, padx=(0, 4))
        fb = self._button(btns, "", lambda gg=g: self.open_game_folder(gg), "ghost", icon="folder", width=26,
                          height=24)
        fb.grid(row=0, column=2)
        self._tip(fb, "Afficher le fichier")
        self._hline(box).grid(row=r + 1, column=0, columnspan=8, sticky="ew")

    # ------------------------------------------------------------------ progress tab (progress.py)
    @_guarded
    def refresh_progress(self) -> None:
        """Compute the trends of the last games on a worker thread, then draw the tab."""
        if self._progress_sig == "loading":
            return
        if self._progress_sig is not None:
            return
        self._progress_sig = "loading"

        def job() -> list[dict]:
            from treeaicoach import progress  # noqa: PLC0415

            return progress.collect(paths.user_data_dir() / "games", last=20)

        def failed(exc: BaseException) -> None:
            self._progress_sig = None
            self.show_error(f"Progrès indisponibles : {exc}")

        self._dispatcher.run(job, self._show_progress, self.cb(failed), name="TreeAI-ui-progress")

    @_guarded
    def _show_progress(self, rows: list[dict]) -> None:
        from treeaicoach import progress  # noqa: PLC0415

        self._progress_sig = len(rows)
        box = self.progress_box
        for w in box.winfo_children():
            w.destroy()
        ctk = self.ctk
        if len(rows) < 2:
            self._label(box, "Pas encore assez de parties.", self.fonts.body, TEXT, anchor="w").grid(
                row=0, column=0, sticky="w")
            self._label(box, "Les courbes apparaissent après 2 parties enregistrées (5 min minimum).",
                        self.fonts.small, MUTED, anchor="w").grid(row=1, column=0, sticky="w")
            return
        tr = progress.trends(rows)
        pts = progress.focus_points(rows, 3)
        self._caption(box, f"Tes 3 points à travailler · {len(rows)} dernières parties", MUTED, anchor="w").grid(
            row=0, column=0, sticky="w")
        self._hline(box, LINE_STRONG).grid(row=1, column=0, sticky="ew", pady=(4, 6))
        fp = ctk.CTkFrame(box, fg_color="transparent")
        fp.grid(row=2, column=0, sticky="ew", pady=(0, 18))
        fp.grid_columnconfigure(1, weight=1)
        for i, (title, text) in enumerate(pts):
            self._label(fp, str(i + 1), self.fonts.stat, ACCENT, anchor="n", width=28).grid(
                row=i, column=0, sticky="n", pady=(0, 8))
            cell = ctk.CTkFrame(fp, fg_color="transparent")
            cell.grid(row=i, column=1, sticky="ew", pady=(2, 8))
            self._label(cell, title, self.fonts.h3, TEXT, anchor="w").grid(row=0, column=0, sticky="w")
            self._label(cell, ui_text(text), self.fonts.small, MUTED, anchor="w", justify="left",
                        wraplength=600).grid(row=1, column=0, sticky="w")
        self._caption(box, "Tendances", MUTED, anchor="w").grid(row=3, column=0, sticky="w")
        self._hline(box, LINE_STRONG).grid(row=4, column=0, sticky="ew", pady=(4, 0))
        tab = ctk.CTkFrame(box, fg_color="transparent")
        tab.grid(row=5, column=0, sticky="ew")
        tab.grid_columnconfigure(1, weight=1)
        for c, (txt, anchor) in enumerate((("Mesure", "w"), ("Parties", "w"), ("Dernière", "e"),
                                           ("Moyenne", "e"), ("Tendance", "e"))):
            self._caption(tab, txt, DIM, anchor=anchor).grid(row=0, column=c, sticky=anchor, padx=(0, 16),
                                                            pady=(6, 2))
        r = 1
        for key, (label, unit, _higher, dec) in progress.METRICS.items():
            t = tr.get(key) or {}
            if t.get("avg") is None:
                continue
            self._label(tab, label, self.fonts.body, TEXT, anchor="w").grid(row=r, column=0, sticky="w",
                                                                            padx=(0, 16), pady=4)
            base = 0.0 if key.startswith("gold_diff") else None
            pil = progress.sparkline(t["values"], 360, 30, progress.metric_color(t), baseline=base,
                                     bg=_hex_rgb(BG))
            img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(180, 30))
            self._images[f"spark-{key}"] = img
            ctk.CTkLabel(tab, text="", image=img, fg_color="transparent").grid(row=r, column=1, sticky="w",
                                                                               padx=(0, 16))
            signed = key.startswith("gold_diff")
            suffix = f" {unit}" if unit else ""
            self._label(tab, progress.fmt_num(t["last"], dec, signed) + suffix, self.fonts.num, TEXT,
                        anchor="e").grid(row=r, column=2, sticky="e", padx=(0, 16))
            self._label(tab, progress.fmt_num(t["avg"], dec, signed) + suffix, self.fonts.small, MUTED,
                        anchor="e").grid(row=r, column=3, sticky="e", padx=(0, 16))
            arrow = {"up": "▲ en hausse", "down": "▼ en baisse"}.get(t["direction"], "stable")
            col = SAFE if t.get("better") is True else DANGER if t.get("better") is False else DIM
            self._label(tab, arrow, self.fonts.small, col, anchor="e").grid(row=r, column=4, sticky="e")
            self._hline(tab).grid(row=r + 1, column=0, columnspan=5, sticky="ew")
            r += 2
        if tr.get("gold_diff10", {}).get("avg") is None:
            self._label(box, "Or à 10/15 min et fiabilité TreeAI : disponibles quand le client LoL est ouvert après "
                             "la partie.", self.fonts.tiny, DIM, anchor="w").grid(row=6, column=0, sticky="w",
                                                                                 pady=(8, 0))

    # ------------------------------------------------------------------ replay tab (replay.py)
    def _build_replay(self, rp: Any) -> None:
        import tkinter as tk  # noqa: PLC0415

        ctk = self.ctk
        self._replay: dict[str, Any] = {"model": None, "t": 0.0, "playing": False, "speed": 30.0, "job": None,
                                        "path": None, "loading": False, "icons": {}}
        bar = ctk.CTkFrame(rp, fg_color="transparent")
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        bar.grid_columnconfigure(1, weight=1)
        self._caption(bar, "Partie", DIM, anchor="w").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.replay_menu = ctk.CTkOptionMenu(bar, values=["Aucune partie"], width=300, height=26,
                                             font=self.fonts.small, dropdown_font=self.fonts.small,
                                             command=self.cb(self._replay_menu_pick), dynamic_resizing=False)
        self.replay_menu.grid(row=0, column=1, sticky="w")
        self._replay_choices: dict[str, dict] = {}
        size = self._scaled(300)
        self._replay_size = size
        holder = tk.Frame(rp, bg=BG, width=size, height=size)
        holder.grid(row=1, column=0, sticky="nw", padx=(0, 16))
        holder.grid_propagate(False)
        from PIL import ImageTk  # noqa: PLC0415

        self._replay_photo = ImageTk.PhotoImage(radar_placeholder(size), master=self.root)
        self.replay_img = tk.Label(holder, image=self._replay_photo, bg=BG, bd=0, highlightthickness=0)
        self.replay_img.place(x=0, y=0, relwidth=1, relheight=1)
        side = ctk.CTkFrame(rp, fg_color="transparent")
        side.grid(row=1, column=1, sticky="nsew")
        side.grid_columnconfigure(0, weight=1)
        self.replay_clock = self._label(side, "--:--", self.fonts.clock, TEXT, anchor="w")
        self.replay_clock.grid(row=0, column=0, sticky="w")
        self.replay_caption = self._label(side, "Choisis une partie.", self.fonts.small, MUTED, anchor="w",
                                          justify="left", wraplength=380)
        self.replay_caption.grid(row=1, column=0, sticky="w", pady=(0, 2))
        self.replay_plays_lbl = self._label(side, "", self.fonts.tiny_bold, ACCENT, anchor="w")
        self.replay_plays_lbl.grid(row=6, column=0, sticky="w", pady=(6, 0))
        self._tip(self.replay_plays_lbl, "Coups notés de la partie (style échecs) : précision sur 100.")
        ctl = ctk.CTkFrame(side, fg_color="transparent")
        ctl.grid(row=2, column=0, sticky="w", pady=(0, 8))
        self.replay_play = self._button(ctl, "Lecture", self.replay_toggle, "primary", icon="play", width=86,
                                        height=26)
        self.replay_play.grid(row=0, column=0, padx=(0, 6))
        b = self._button(ctl, "<", lambda: self.replay_jump(-1), "secondary", width=28, height=26)
        b.grid(row=0, column=1, padx=(0, 2))
        self._tip(b, "Moment clé précédent (mort, gank)")
        b = self._button(ctl, ">", lambda: self.replay_jump(1), "secondary", width=28, height=26)
        b.grid(row=0, column=2, padx=(0, 8))
        self._tip(b, "Moment clé suivant (mort, gank)")
        self.replay_speed = ctk.CTkSegmentedButton(ctl, values=["x10", "x30", "x60", "x120"], height=26,
                                                   font=self.fonts.tiny_bold,
                                                   command=self.cb(self._replay_speed))
        self.replay_speed.grid(row=0, column=3)
        self.replay_speed.set("x30")
        self._tip(self.replay_speed, "Vitesse : secondes de jeu par seconde")
        self._caption(side, "Moments clés", DIM, anchor="w").grid(row=3, column=0, sticky="w", pady=(4, 0))
        self._hline(side, LINE_STRONG).grid(row=4, column=0, sticky="ew", pady=(4, 0))
        self.replay_moments = ctk.CTkScrollableFrame(side, fg_color="transparent", height=150, corner_radius=0)
        self.replay_moments.grid(row=5, column=0, sticky="ew")
        self.replay_moments.grid_columnconfigure(1, weight=1)
        tl_w = self._scaled(640)
        self._replay_tl_w = tl_w
        self.replay_tl = tk.Label(rp, bg=BG, bd=0, highlightthickness=0, cursor="hand2")
        self.replay_tl.grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.replay_tl.bind("<Button-1>", lambda e: self._replay_click(e.x), add="+")
        self.replay_tl.bind("<B1-Motion>", lambda e: self._replay_click(e.x), add="+")
        self._replay_tl_photo: Any = None
        self._replay_legend = self._label(rp, "x mort · ▲ gank · ■ kill · | objectif · ◆ coup noté", self.fonts.tiny,
                                          DIM, anchor="w")
        self._replay_legend.grid(row=3, column=0, columnspan=2, sticky="w")

    def _replay_games_menu(self, games: list[dict]) -> None:
        menu = getattr(self, "replay_menu", None)
        if menu is None:
            return
        self._replay_choices = {}
        for g in games[:50]:
            p = _game_json_path(g)
            if p is None:
                continue
            name = str(game_field(g, "champion_name", "champion", default="") or "?")
            res = {"win": "V", "lose": "D"}.get(game_result(g) or "", "-")
            label = f"{fmt_game_date(game_datetime(g))} · {name} · {res}"
            while label in self._replay_choices:
                label += " "
            self._replay_choices[label] = g
        values = list(self._replay_choices) or ["Aucune partie"]
        try:
            menu.configure(values=values)
            if self._replay.get("path") is None:
                menu.set(values[0])
        except Exception:
            pass

    def _replay_ensure_loaded(self) -> None:
        if self._replay.get("model") is None and not self._replay.get("loading") and self._replay_choices:
            self._replay_menu_pick(next(iter(self._replay_choices)))

    def _replay_menu_pick(self, label: str) -> None:
        g = self._replay_choices.get(label)
        if g is not None:
            self._replay_load(g)

    @_guarded
    def open_replay(self, game: dict) -> None:
        """Replay button of a game row: switch to the Replay tab and load that game."""
        page = getattr(self, "_analysis_page", None)
        if page is not None and hasattr(page, "select_tab"):
            page.select_tab("Replay")
        for label, g in self._replay_choices.items():
            if g is game:
                self.replay_menu.set(label)
        self._replay_load(game)

    def _replay_load(self, game: dict) -> None:
        p = _game_json_path(game)
        if p is None or self._replay.get("loading"):
            return
        self._replay_stop()
        self._replay["loading"] = True
        self.replay_caption.configure(text="Chargement de la partie…")

        def job() -> Any:
            import json  # noqa: PLC0415

            from treeaicoach import replay  # noqa: PLC0415

            rec = json.loads(Path(p).read_text(encoding="utf-8"))
            return replay.ReplayModel(rec)

        def done(model: Any) -> None:
            self._replay["loading"] = False
            self._replay["model"] = model
            self._replay["path"] = p
            self._replay["t"] = model.start
            self._replay["icons"] = {}
            self._replay_fill_moments(model)
            m = model.next_marker(model.start - 1)
            self._replay_seek(m.t - 8 if m is not None else model.start)

        def failed(exc: BaseException) -> None:
            self._replay["loading"] = False
            self.replay_caption.configure(text=f"Impossible de lire cette partie : {exc}")

        self._dispatcher.run(job, self.cb(done), self.cb(failed), name="TreeAI-ui-replay")

    def _replay_fill_moments(self, model: Any) -> None:
        box = self.replay_moments
        for w in box.winfo_children():
            w.destroy()
        from treeaicoach import replay  # noqa: PLC0415

        cols = {"death": DANGER, "gank": WARNING, "kill": SAFE, "objective": MUTED}
        rows = [m for m in model.markers if m.kind in ("death", "gank", "kill", "play")][:80]
        summ = None
        try:
            from treeaicoach import plays as _plays  # noqa: PLC0415

            summ = _plays.summary_from_record(model.record)
            self.replay_plays_lbl.configure(text=ui_text(_plays.summary_line(summ)) if summ else "")
        except Exception:
            log.debug("no play summary", exc_info=True)
        if not rows:
            self._label(box, "Aucun moment clé enregistré.", self.fonts.small, DIM, anchor="w").grid(
                row=0, column=0, columnspan=2, sticky="w")
        for i, m in enumerate(rows):
            col = cols.get(m.kind, MUTED)
            if m.kind == "play":
                col = "#%02X%02X%02X" % replay.play_rgb(m.cls)
            b = self.ctk.CTkButton(box, text=replay.fmt_clock(m.t), width=44, height=20, corner_radius=RADIUS,
                                   font=self.fonts.tiny_bold, fg_color="transparent", hover_color=PANEL_HI,
                                   text_color=col, anchor="w",
                                   command=self.cb(lambda tt=m.t: self._replay_seek(tt - 6)))
            b.grid(row=i, column=0, sticky="w")
            self._label(box, ui_text(m.label), self.fonts.small, TEXT, anchor="w").grid(row=i, column=1, sticky="w")

    def _replay_icon(self, alias: str) -> Any:
        cache = self._replay["icons"]
        if alias not in cache:
            try:
                from treeaicoach.champions import get_default_db  # noqa: PLC0415

                cache[alias] = get_default_db().load_icon(alias) if alias and "?" not in alias else None
            except Exception:
                cache[alias] = None
        return cache[alias]

    def _replay_seek(self, t: float) -> None:
        model = self._replay.get("model")
        if model is None:
            return
        t = min(max(float(t), model.start), model.end)
        self._replay["t"] = t
        self._replay_draw()

    def _replay_draw(self) -> None:
        model = self._replay.get("model")
        if model is None:
            return
        from PIL import ImageTk  # noqa: PLC0415

        from treeaicoach import replay  # noqa: PLC0415

        t = self._replay["t"]
        try:
            im = replay.render_frame(model, t, self._replay_size, icon_loader=self._replay_icon)
            self._replay_photo = ImageTk.PhotoImage(im, master=self.root)
            self.replay_img.configure(image=self._replay_photo)
            tl = replay.render_timeline(model, self._replay_tl_w, self._scaled(34), t)
            self._replay_tl_photo = ImageTk.PhotoImage(tl, master=self.root)
            self.replay_tl.configure(image=self._replay_tl_photo)
        except Exception:
            log.exception("Replay rendering failed")
        self.replay_clock.configure(text=f"{replay.fmt_clock(t)} / {replay.fmt_clock(model.end)}")
        self.replay_caption.configure(text=ui_text(replay.frame_caption(model, t)))

    def _replay_click(self, x: int) -> None:
        model = self._replay.get("model")
        if model is None:
            return
        from treeaicoach import replay  # noqa: PLC0415

        self._replay_seek(replay.time_at_x(model, x, self._replay_tl_w))

    def _replay_speed(self, label: str) -> None:
        try:
            self._replay["speed"] = float(str(label).lstrip("x"))
        except ValueError:
            pass

    @_guarded
    def replay_toggle(self) -> None:
        if self._replay.get("model") is None:
            self._replay_ensure_loaded()
            return
        if self._replay["playing"]:
            self._replay_stop()
        else:
            if self._replay["t"] >= self._replay["model"].end - 0.5:
                self._replay["t"] = self._replay["model"].start
            self._replay["playing"] = True
            self.replay_play.configure(text="Pause", image=self._icon("stop", 12, ON_ACCENT))
            self._replay_tick()

    def _replay_stop(self) -> None:
        self._replay["playing"] = False
        job = self._replay.get("job")
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self._replay["job"] = None
        try:
            self.replay_play.configure(text="Lecture", image=self._icon("play", 12, ON_ACCENT))
        except Exception:
            pass

    def _replay_tick(self) -> None:
        self._replay["job"] = None
        if not self._replay["playing"] or self._closing:
            return
        model = self._replay["model"]
        step_ms = 200
        self._replay["t"] = min(model.end, self._replay["t"] + self._replay["speed"] * step_ms / 1000)
        self._replay_draw()
        if self._replay["t"] >= model.end or self._current_page != "analysis":
            self._replay_stop()
            return
        self._replay["job"] = self.root.after(step_ms, self._replay_tick)

    @_guarded
    def replay_jump(self, direction: int) -> None:
        model = self._replay.get("model")
        if model is None:
            return
        t = self._replay["t"] + 6
        m = model.next_marker(t) if direction > 0 else model.prev_marker(t - 1)
        if m is not None:
            self._replay_seek(m.t - 6)


    @_guarded
    def open_report(self, game: dict) -> None:
        """Open the HTML report of a game (generated first when missing)."""
        src = _game_json_path(game)
        html = _game_html_path(game, src)

        def job() -> Path | None:
            if html is not None and html.is_file():
                return html
            if src is None:
                return None
            fn = _report_function("write_report")
            if fn is None:
                return None
            out = fn(src)
            return Path(out) if out else None

        def done(p: Path | None) -> None:
            if p is None or not Path(p).is_file():
                self.show_error("Impossible de générer le rapport de cette partie.")
                return
            webbrowser.open(Path(p).resolve().as_uri())
            self.show_toast("Rapport ouvert dans le navigateur.")

        self.show_toast("Préparation du rapport…")
        self._dispatcher.run(job, done, self.cb(lambda e: self.show_error(f"Rapport impossible : {e}")),
                             name="TreeAI-ui-report")

    @_guarded
    def open_game_folder(self, game: dict) -> None:
        p = _game_json_path(game)
        if p is not None and p.exists():
            open_path(p, select=True)
        else:
            self.open_games_dir()

    @_guarded
    def open_games_dir(self) -> None:
        d = paths.user_data_dir() / "games"
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        if not open_path(d):
            self.show_error("Impossible d'ouvrir le dossier des parties.")

    # ------------------------------------------------------------------ settings page
    def _build_settings_page(self) -> Any:
        page, right, body = self._page("Réglages", "Démarrage, minimap, IA, maintenance")
        b = self._button(right, "Diagnostic", self.copy_diagnostic, "ghost", icon="copy", height=26)
        b.grid(row=0, column=0)
        self._tip(b, "Copie un rapport technique (sans donnée personnelle) à coller dans ton message. (Ctrl+D)")
        s = self._section(body, 0, "Minimap", "Par défaut, la minimap est trouvée automatiquement. "
                                              "Calibre-la à la main si la détection échoue.", icon="map")
        self._choice_row(s, "minimap_mode", "Localisation", self._manual_rect_text(), MINIMAP_MODES, segmented=True,
                         on_change=self._on_minimap_mode)
        self._rect_desc = self._last_slot.desc_label
        _row, slot = self._row(s, "Calibration manuelle", "Trace un carré autour de la minimap sur une capture.")
        self._button(slot, "Calibrer la minimap", self.calibrate, "ghost", icon="target").grid(row=0, column=0)
        self._choice_row(s, "minimap_side", "Côté de la minimap", "Position de la minimap dans les options du jeu.",
                         MINIMAP_SIDES, segmented=True)
        _row, slot = self._row(s, "Relocaliser", "Recherche à nouveau la minimap (après un changement de "
                                                 "résolution ou d'échelle de l'interface).")
        self._button(slot, "Relocaliser maintenant", self.relocate, "secondary", icon="refresh").grid(row=0, column=0)

        s = self._section(body, 1, "Détection", icon="cpu")
        self._slider_row(s, "target_fps", "Images par seconde", "Plus c'est haut, plus c'est réactif (et plus "
                         "le processeur travaille). Défaut : 8.", 2, 20, 1, lambda v: f"{int(v)} i/s", float)
        self._choice_row(s, "detector_backend", "Détecteur", "Le réseau de neurones est plus précis ; le "
                         "détecteur classique sert de secours.", DETECTORS, width=240)
        self._switch_row(s, "download_skin_icons", "Télécharger les icônes de skins",
                         "Améliore la reconnaissance des champions avec un skin (CommunityDragon).")
        self._switch_row(s, "collect_samples", "Collecter des captures de minimap",
                         "Enregistre des minimaps pour améliorer le modèle (dossier « collect »).")
        self._slider_row(s, "collect_interval_s", "Intervalle de collecte", None, 0.5, 30, 0.5,
                         lambda v: f"{fmt_decimal_fr(v, 1)} s", float)

        s = self._section(body, 2, "Démarrage & rapports", icon="play")
        self._switch_row(s, "autostart", "Démarrer l'analyse au lancement",
                         "L'analyse attend une partie en arrière-plan (processeur ≈ 0 hors partie).")
        ok, reason = autostart_support()
        _row, slot = self._row(s, "Lancer avec Windows",
                               "Ouvre TreeAI Coach à l'ouverture de ta session." if ok else reason)
        self.win_autostart_var = self.ctk.BooleanVar(value=get_windows_autostart() if ok else False)
        sw = self.ctk.CTkSwitch(slot, text="", variable=self.win_autostart_var, width=46, switch_width=34,
                                switch_height=16, command=self.cb(self._on_windows_autostart),
                                fg_color=SWITCH_OFF, progress_color=TEAL, button_color=TEXT)
        sw.grid(row=0, column=0)
        if not ok:
            sw.configure(state="disabled", button_color=DIM)
        self._switch_row(s, "post_game_report", "Rapport d'après-partie",
                         "Analyse chaque partie (morts, ganks subis, jungler ennemi) et écrit un rapport HTML.")
        self._switch_row(s, "open_report_automatically", "Ouvrir le rapport automatiquement",
                         "À la fin de la partie, dans ton navigateur.")

        s = self._section(body, 3, "Interface", icon="sliders")
        if hasattr(self.cfg, "safe_mode"):
            self._switch_row(s, "safe_mode", "Mode sûr", "Aucune alerte de gank ni suivi du jungler, aucune zone dans "
                             "le brouillard. Minuteurs et rappels restent actifs.",
                             on_change=lambda _v: self._sync_quick())
        if hasattr(self.cfg, "ui_remember_page"):
            self._switch_row(s, "ui_remember_page", "Rouvrir la dernière page",
                             "Au lancement, revient sur la page que tu regardais.")
        if hasattr(self.cfg, "ui_confirm_quit"):
            self._switch_row(s, "ui_confirm_quit", "Confirmer avant de quitter en partie",
                             "Évite de fermer le coach par erreur pendant une partie.")
        _row, slot = self._row(s, "Mode guidé", "3 étapes : ton niveau, jeu en Sans bordure, test de l'overlay.")
        self._button(slot, "Relancer", lambda: self.show_onboarding(0), "secondary").grid(row=0, column=0)

        s = self._section(body, 4, "Maintenance et support", icon="info")
        _row, slot = self._row(s, "Diagnostic", "Version, moteur, détecteur, voix et dernières erreurs, à joindre "
                                                "à un signalement.")
        self._button(slot, "Copier le diagnostic", self.copy_diagnostic, "secondary", icon="copy").grid(
            row=0, column=0)
        _row, slot = self._row(s, "Journaux", "Utile pour signaler un problème.")
        self._button(slot, "Ouvrir les journaux", self.open_logs, "secondary",
                     icon="folder").grid(row=0, column=0)
        _row, slot = self._row(s, "Rapports de parties", "Rapports HTML d'après-partie.")
        self._button(slot, "Dernier rapport", self.open_last_report, "secondary", icon="report").grid(
            row=0, column=0, padx=(0, 8))
        self._button(slot, "Dossier", self.open_games_dir, "secondary", icon="folder").grid(row=0, column=1)
        _row, slot = self._row(s, "Données", str(paths.user_data_dir()))
        self._button(slot, "Ouvrir le dossier", lambda: open_path(paths.user_data_dir()), "secondary",
                     icon="folder").grid(row=0, column=0)
        _row, slot = self._row(s, "Réinitialiser", "Remet tous les réglages par défaut.")
        self._button(slot, "Réinitialiser", self.ask_reset, "danger").grid(row=0, column=0)
        try:
            self._build_ai_section(body, 5)
        except Exception:
            log.exception("Cannot build the AI section")
        try:
            self._build_updates_section(body, 6)
        except Exception:
            log.exception("Cannot build the updates section")
        self._tabs(page, body, (("Général", ("Démarrage & rapports", "Interface", "Mises à jour")),
                                ("Minimap", ("Minimap", "Détection")),
                                ("IA", ("IA (facultatif)",)),
                                ("Avancé", ("Maintenance et support",))))
        return page

    # ------------------------------------------------------------------ optional AI advice (ai_advisor.py)
    def _build_ai_section(self, body: Any, row: int) -> None:
        if not hasattr(self.cfg, "ai_provider"):
            return
        from treeaicoach import ai_advisor  # noqa: PLC0415

        s = self._section(body, row, "IA (facultatif)", "Un conseil d'achat et de macro écrit par une IA aux "
                          "moments clés (retour en base, mort, niveaux 6/11/16, 60 s avant dragon / Baron). "
                          "Désactivé par défaut ; ta clé reste sur ce PC et aucun pseudo n'est envoyé.",
                          icon="star")
        self._choice_row(s, "ai_provider", "Fournisseur", "Gemini, Groq et OpenRouter ont une offre gratuite ; "
                         "Ollama tourne sur ton PC.", ai_advisor.PROVIDER_CHOICES, width=260)
        _row, slot = self._row(s, "Clé API", "Collée ici, enregistrée localement (jamais exportée).")
        key_entry = self.ctk.CTkEntry(slot, width=260, show="•", placeholder_text="Clé du fournisseur")
        if self.cfg.ai_api_key:
            key_entry.insert(0, self.cfg.ai_api_key)
        key_entry.grid(row=0, column=0)
        save_key = self.cb(lambda _e=None: self.set_option("ai_api_key", key_entry.get().strip()))
        key_entry.bind("<FocusOut>", save_key, add="+")
        key_entry.bind("<Return>", save_key, add="+")
        self._ai_key_entry = key_entry
        defaults = ", ".join(f"{ui_kit.AI_SHORT.get(k, k)} : {p.default_model}"
                             for k, p in getattr(ai_advisor, "PROVIDERS", {}).items())
        _row, slot = self._row(s, "Modèle", f"Vide = modèle par défaut ({defaults}).")
        model_entry = self.ctk.CTkEntry(slot, width=260, placeholder_text="par défaut")
        if self.cfg.ai_model:
            model_entry.insert(0, self.cfg.ai_model)
        model_entry.grid(row=0, column=0)
        save_model = self.cb(lambda _e=None: self.set_option("ai_model", model_entry.get().strip()))
        model_entry.bind("<FocusOut>", save_model, add="+")
        model_entry.bind("<Return>", save_model, add="+")
        self._ai_model_entry = model_entry
        hk = getattr(self.cfg, "hotkey_ai", "") or "sans raccourci"
        _row, slot = self._row(s, "Tester la connexion", "Envoie une petite question de test au fournisseur. "
                               f"En partie : « Demander à l'IA » ({hk}) donne un conseil immédiat ; une revue "
                               "IA est ajoutée au rapport d'après-partie.")
        self._ai_test_btn = self._button(slot, "Tester", self.test_ai, "secondary", icon="check")
        self._ai_test_btn.grid(row=0, column=0, padx=(0, 8))
        self._button(slot, "Demander à l'IA", self.ask_ai, "ghost", icon="star").grid(row=0, column=1)
        box = self.ctk.CTkFrame(s, fg_color="transparent")
        box.grid(row=2 * s._rows, column=0, sticky="ew", pady=(0, 6))
        box.grid_columnconfigure(0, weight=1)
        s._rows += 1
        self._ai_status = self._label(box, "", self.fonts.small, MUTED, anchor="w", justify="left",
                                      wraplength=620)
        self._ai_status.grid(row=0, column=0, sticky="w")
        self._ai_status_box = box
        box.grid_remove()               # shown with the first test result
        self._switch_row(s, "ai_speak", "Lire le conseil IA à voix haute", "Désactivé par défaut : le conseil "
                         "s'affiche en bandeau et dans le HUD.")
        links = self.ctk.CTkFrame(s, fg_color="transparent")
        links.grid(row=2 * s._rows, column=0, sticky="ew", pady=(4, 6))
        s._rows += 1
        self._label(links, "Obtenir une clé gratuite :", self.fonts.tiny, MUTED, anchor="w").grid(
            row=0, column=0, sticky="w", padx=(0, 8))
        for i, (label, url) in enumerate((("Gemini", "https://aistudio.google.com/apikey"),
                                          ("Groq", "https://console.groq.com/keys"),
                                          ("OpenRouter", "https://openrouter.ai/keys"),
                                          ("Ollama (local)", "https://ollama.com"))):
            lnk = self._label(links, label, self.fonts.tiny, TEAL, anchor="w", cursor="hand2")
            lnk.grid(row=0, column=i + 1, sticky="w", padx=(0, 10))
            lnk.bind("<Button-1>", self.cb(lambda _e=None, u=url: webbrowser.open(u)), add="+")
            self._tip(lnk, url)

    def _set_ai_status(self, text: str, color: str = MUTED) -> None:
        lbl = getattr(self, "_ai_status", None)
        if lbl is not None:
            try:
                lbl.configure(text=text, text_color=color)
                box = getattr(self, "_ai_status_box", None)
                if box is not None:
                    (box.grid if text else box.grid_remove)()
            except Exception:
                pass

    @_guarded
    def test_ai(self) -> None:
        """"Tester" button: one request to the chosen provider (background thread)."""
        for field, entry in (("ai_api_key", getattr(self, "_ai_key_entry", None)),
                             ("ai_model", getattr(self, "_ai_model_entry", None))):
            if entry is not None and entry.get().strip() != getattr(self.cfg, field, ""):
                self.set_option(field, entry.get().strip())
        from treeaicoach import ai_advisor  # noqa: PLC0415

        cfg = self.cfg
        self._set_ai_status("Test en cours…")

        def done(res: Any) -> None:
            ok, msg = res
            self._ai_test = (bool(ok), "clé OK" if ok else "erreur")
            self._set_ai_status(msg, SAFE if ok else DANGER)

        self._dispatcher.run(lambda: ai_advisor.check_connection(cfg), done,
                             self.cb(lambda e: self._set_ai_status(f"Test impossible : {e}", DANGER)),
                             name="TreeAI-ui-ai-test")

    def _ai_budget(self) -> str:
        """"IA 3/5" while a game runs (engine.ai_budget_text), "" otherwise."""
        eng = self.engine
        fn = getattr(eng, "ai_budget_text", None) if eng is not None else None
        if not callable(fn) or not self._in_game():
            return ""
        try:
            return str(fn() or "")
        except Exception:
            return ""

    @_guarded
    def test_ai_key(self) -> None:
        """Dashboard "Tester la clé": one tiny request on a worker thread, result in French (row + toast)."""
        if self._ai_test_busy:
            return
        self._ai_test_busy = True
        cfg = self.cfg

        def done(res: Any) -> None:
            self._ai_test_busy = False
            ok, short, msg = res
            self._ai_test = (bool(ok), str(short))
            self._set_ai_status(msg, SAFE if ok else DANGER)
            self.show_toast(msg, "info" if ok else "error")

        def failed(exc: BaseException) -> None:
            self._ai_test_busy = False
            self._ai_test = (False, "erreur")
            self.show_error(f"Test de la clé impossible : {exc}")

        self._dispatcher.run(lambda: ui_kit.test_ai_key(cfg), done, self.cb(failed), name="TreeAI-ui-ai-key")

    # ------------------------------------------------------------------ updates (updater.py)
    def _build_updates_section(self, body: Any, row: int) -> None:
        s = self._section(body, row, "Mises à jour", "Les nouvelles versions sont publiées sur GitHub ; "
                                                     "le fichier est vérifié (SHA-256) avant d'être installé.",
                          icon="download")
        self._update_info: Any = None
        self._update_busy = False
        _row, slot = self._row(s, f"Version installée : {__version__}", None)
        self._update_check_btn = self._button(slot, "Vérifier les mises à jour", self.check_updates,
                                              "secondary", icon="refresh")
        self._update_check_btn.grid(row=0, column=0)
        box = self.ctk.CTkFrame(s, fg_color="transparent")
        box.grid(row=2 * s._rows, column=0, sticky="ew", pady=(0, 6))
        box.grid_columnconfigure(0, weight=1)
        s._rows += 1
        self._update_status = self._label(box, "", self.fonts.small, MUTED, anchor="w", justify="left",
                                          wraplength=620)
        self._update_status.grid(row=0, column=0, sticky="w")
        self._update_status_box = box
        box.grid_remove()               # shown with the first message
        self._update_bar = self.ctk.CTkProgressBar(box, height=8)
        self._update_bar.set(0)
        self._update_bar.grid(row=1, column=0, sticky="ew", pady=(6, 2))
        self._update_bar.grid_remove()
        _row, slot = self._row(s, "Installer", "Télécharge la nouvelle version, la vérifie puis redémarre "
                                               "TreeAI Coach.")
        self._update_btn = self._button(slot, "Mettre à jour", self.install_update, "primary",
                                        state="disabled")
        self._update_btn.grid(row=0, column=0)
        self._update_manual_btn = self._button(slot, "Télécharger manuellement", self.open_manual_download,
                                               "secondary", icon="download")
        self._update_manual_btn.grid(row=0, column=1, padx=(6, 0))
        self._tip(self._update_manual_btn, "Ouvre le lien direct du dernier TreeAICoach.exe dans ton navigateur. "
                                           "Ferme TreeAI Coach, puis remplace l'ancien fichier par le nouveau.")
        _row, slot = self._row(s, "Jeton GitHub (dépôt privé)", "Facultatif : jeton d'accès personnel avec "
                               "lecture du dépôt, nécessaire tant que le dépôt est privé.")
        entry = self.ctk.CTkEntry(slot, width=240, show="•", placeholder_text="ghp_… ou github_pat_…")
        if self.cfg.github_token:
            entry.insert(0, self.cfg.github_token)
        entry.grid(row=0, column=0)
        save_token = self.cb(lambda _e=None: self.set_option("github_token", entry.get().strip()))
        entry.bind("<FocusOut>", save_token, add="+")
        entry.bind("<Return>", save_token, add="+")
        self._update_token_entry = entry
        self._switch_row(s, "check_updates_on_start", "Vérifier au démarrage",
                         "Cherche une nouvelle version en arrière-plan à chaque lancement.")

    @_guarded
    def open_manual_download(self) -> None:
        """Fallback when the in-app update fails: the direct link to the published exe."""
        from treeaicoach import updater  # noqa: PLC0415

        try:
            webbrowser.open(updater.MANUAL_DOWNLOAD_URL)
            self.show_toast("Lien de téléchargement ouvert dans le navigateur.")
        except Exception:
            self._copy_text(updater.MANUAL_DOWNLOAD_URL)
            self.show_toast("Lien copié : colle-le dans ton navigateur.")

    def _copy_text(self, text: str) -> None:
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except Exception:
            log.debug("clipboard failed", exc_info=True)

    def _check_last_update(self) -> None:
        """At launch: did the last in-app update really apply? (updater.startup_report)."""
        from treeaicoach import updater  # noqa: PLC0415

        def done(rep: Any) -> None:
            if rep is None or self._closing:
                return
            if rep.ok:
                self.show_toast(rep.message)
                return
            self._set_update_status(rep.message, DANGER)
            top, body, bar, close = self._dialog("Mise à jour non appliquée", None, width=520)
            self._label(body, ui_text(rep.message.split(" Tu peux aussi")[0]), self.fonts.small, TEXT, anchor="w",
                        justify="left", wraplength=470).grid(row=0, column=0, sticky="w")
            self._label(body, updater.MANUAL_DOWNLOAD_URL, self.fonts.tiny, MUTED, anchor="w", justify="left",
                        wraplength=470).grid(row=1, column=0, sticky="w", pady=(8, 0))
            self._button(bar, "Fermer", close, "ghost").grid(row=0, column=0, padx=(0, 6))
            self._button(bar, "Réessayer", lambda: (close(), self.show_page("settings"), self.check_updates()),
                         "secondary").grid(row=0, column=1, padx=(0, 6))
            self._button(bar, "Télécharger manuellement", lambda: (close(), self.open_manual_download()),
                         "primary", icon="download").grid(row=0, column=2)
            self._place_dialog(top, grab=False)

        self._dispatcher.run(updater.startup_report, done, None, name="TreeAI-update-report")

    def _set_update_status(self, text: str, color: str = MUTED) -> None:
        lbl = getattr(self, "_update_status", None)
        if lbl is not None:
            try:
                lbl.configure(text=text, text_color=color)
                box = getattr(self, "_update_status_box", None)
                if box is not None:
                    (box.grid if text else box.grid_remove)()
            except Exception:
                pass

    def _startup_update_check(self) -> None:
        """Silent background check at launch (frozen exe only): toast if a new version exists."""
        if self._closing or not self.cfg.check_updates_on_start:
            return
        self.check_updates(quiet=True)

    def check_updates(self, quiet: bool = False) -> None:
        """Check GitHub for a new version (background thread); ``quiet`` = toast only if available."""
        if getattr(self, "_update_busy", False):
            return
        self._update_busy = True
        from treeaicoach import updater
        entry = getattr(self, "_update_token_entry", None)
        if entry is not None and entry.get().strip() != self.cfg.github_token:
            self.set_option("github_token", entry.get().strip())
        cfg = self.cfg
        if not quiet:
            self._set_update_status("Recherche d'une nouvelle version…")

        def done(res: Any) -> None:
            self._update_busy = False
            self._update_info = res.info if res.available else None
            color = GOLD if res.available else (DANGER if res.status == updater.ERROR else TEAL)
            self._set_update_status(updater.manual_hint(res.message) if res.status == updater.ERROR
                                    else res.message, color)
            btn = getattr(self, "_update_btn", None)
            if btn is not None:
                self._btn_state(btn, bool(res.available and res.can_install))
            if res.available and quiet:
                self.show_toast(f"Nouvelle version {res.info.version} disponible : Réglages → Mises à jour.")
            elif not quiet:
                self.show_toast(res.message, "error" if res.status == updater.ERROR else "info")

        def failed(exc: BaseException) -> None:
            self._update_busy = False
            if not quiet:
                self._set_update_status(f"Vérification impossible : {exc}", DANGER)

        self._dispatcher.run(lambda: updater.check_for_update(cfg), done, failed, name="TreeAI-update-check")

    def install_update(self) -> None:
        """Download + verify + swap the exe, then close the app (the batch relaunches it)."""
        info = getattr(self, "_update_info", None)
        if info is None or self._update_busy:
            return
        from treeaicoach import updater
        self._update_busy = True
        self._btn_state(self._update_btn, False)
        self._update_check_btn.configure(state="disabled")
        self._update_bar.set(0)
        self._update_bar.grid()
        self._set_update_status(f"Téléchargement de la version {info.version}…")
        cfg = self.cfg
        last = [0.0]

        def progress(done_b: int, total: int) -> None:     # worker thread: throttled post
            now = time.monotonic()
            if now - last[0] < 0.15 and done_b < total:
                return
            last[0] = now
            frac = done_b / total if total else 0.0
            text = (f"Téléchargement de la version {info.version}… "
                    f"{fmt_decimal_fr(done_b / 1e6, 1)} / {fmt_decimal_fr(total / 1e6, 1)} Mo")
            self._dispatcher.post(lambda: (self._update_bar.set(frac), self._set_update_status(text)))

        def job() -> Any:
            dl = updater.download_update(info, cfg, progress=progress)
            if not dl.ok:
                return dl
            return updater.apply_update(dl.path, info)

        def done(res: Any) -> None:
            self._update_busy = False
            self._update_check_btn.configure(state="normal")
            if res.ok and isinstance(res, updater.ApplyResult):
                self._set_update_status(res.message, TEAL)
                self.show_toast(res.message)
                self.root.after(800, self.close)
                return
            self._update_bar.grid_remove()
            self._btn_state(self._update_btn, True)
            msg = updater.manual_hint(res.message)
            self._set_update_status(msg, DANGER)
            self.show_error(res.message + " Lien direct : Réglages > Mises à jour > Télécharger manuellement.")

        def failed(exc: BaseException) -> None:
            done(updater.ApplyResult(False, f"Mise à jour impossible : {exc}"))

        self._dispatcher.run(job, done, failed, name="TreeAI-update-install")

    def _manual_rect_text(self) -> str:
        r = self.cfg.manual_minimap_rect
        if not r:
            return "Aucune calibration manuelle enregistrée."
        return (f"Calibration : {r['w']} × {r['h']} px en ({r['x']}, {r['y']}) "
                f"pour un écran {r['screen_w']} × {r['screen_h']}.")

    def _on_minimap_mode(self, value: str) -> None:
        if value == "manual" and not self.cfg.manual_minimap_rect:
            self.show_toast("Calibre d'abord la minimap pour utiliser le mode manuel.")
            self.calibrate()
        else:
            self.relocate(quiet=True)

    @_guarded
    def _on_windows_autostart(self) -> None:
        want = bool(self.win_autostart_var.get())
        if set_windows_autostart(want):
            self.show_toast("TreeAI Coach se lancera avec Windows." if want else
                            "Lancement avec Windows désactivé.")
        else:
            self.win_autostart_var.set(get_windows_autostart())
            self.show_error("Impossible de modifier le lancement avec Windows.")

    @_guarded
    def ask_reset(self) -> None:
        self._confirm("Réinitialiser les réglages ?",
                      "Tous les réglages reviennent à leur valeur par défaut (la calibration de la minimap "
                      "et la position de la fenêtre sont conservées).", "Réinitialiser", self.reset_settings)

    @_guarded
    def reset_settings(self) -> None:
        keep = {"manual_minimap_rect": self.cfg.manual_minimap_rect, "ui_geometry": self.cfg.ui_geometry}
        for k in ("ui_onboarding_done", "ui_seen_changelog", "ui_last_page", "github_token", "ai_api_key",
                  "icon_scale_by_res"):
            if hasattr(self.cfg, k):
                keep[k] = getattr(self.cfg, k)
        new = dataclasses.replace(Config(), **keep).validated()
        self._replace_config(new, changed=set(f.name for f in dataclasses.fields(Config)))
        self._refresh_all_widgets()
        self.show_toast("Réglages réinitialisés.")

    # ------------------------------------------------------------------ help page
    def _build_help_page(self) -> Any:
        ctk = self.ctk
        page, right, body = self._page("Aide", "Bien démarrer, sécurité et dépannage", icon="help")
        b = self._button(right, "Mode guidé", lambda: self.show_onboarding(0), "secondary", icon="star")
        b.grid(row=0, column=0, padx=(0, 8))
        self._tip(b, "Relancer le mode guidé (niveau, Sans bordure, test de l'overlay)")
        self._button(right, f"Nouveautés v{ui_kit.CHANGELOG_VERSION}", self.show_changelog, "ghost",
                     icon="star").grid(row=0, column=1)
        steps = (
            ("Passe le jeu en mode « Sans bordure »",
             "Options du jeu → Vidéo → Mode d'affichage : Sans bordure (ou Fenêtré). En plein écran exclusif, "
             "la capture est noire et l'overlay invisible."),
            ("Lance TreeAI Coach",
             "Double-clique sur TreeAICoach.exe. L'analyse démarre toute seule et attend une partie : rien à "
             "installer, rien à configurer."),
            ("Joue ta partie",
             "Dès le chargement terminé, le coach trouve la minimap et suit les ennemis. Écoute les annonces et "
             "regarde les marques posées sur ta minimap."),
            ("Réagis aux alertes",
             "« Attention » : un ennemi se rapproche, reste prudent. « Gank ! … recule ! » : recule tout de suite "
             "vers ta tour. F9 : où est le jungler ?"),
            ("Consulte ton analyse",
             "À la fin de la partie, un rapport s'ouvre : morts, ganks subis, habitudes du jungler ennemi et "
             "conseils. Retrouve-les dans l'onglet Analyses."),
        )
        s = self._section(body, 0, "Mode d'emploi en 5 étapes", icon="play")
        for i, (title, text) in enumerate(steps):
            r = ctk.CTkFrame(s, fg_color="transparent")
            r.grid(row=i, column=0, sticky="ew", pady=7)
            r.grid_columnconfigure(1, weight=1)
            badge = self._number_badge(r, i + 1)
            badge.grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 14), pady=(2, 0))
            self._label(r, title, self.fonts.h3, TEXT, anchor="w").grid(row=0, column=1, sticky="w")
            self._label(r, text, self.fonts.small, MUTED, anchor="w", justify="left", wraplength=560).grid(
                row=1, column=1, sticky="w")
        s = self._section(body, 1, "Sécurité et règles de Riot",
                          "TreeAI Coach fonctionne comme un logiciel de streaming (OBS, Discord) :", icon="shield")
        for i, text in enumerate((
                "Il lit uniquement l'écran (la minimap déjà visible) et l'API officielle « Live Client Data » "
                "fournie par le jeu.",
                "Aucune lecture ni écriture de la mémoire du jeu, aucune injection, aucune touche ni clic simulé.",
                "Aucun suivi des sorts ni des ultimes ennemis, aucune prédiction cachée : seulement ce que tu "
                "pourrais voir toi-même.",
                "L'overlay est une fenêtre séparée et transparente posée au-dessus du jeu, jamais dessinée dans "
                "le jeu.",
                "Besoin d'encore plus de prudence ? Active le « Mode sûr » (tableau de bord ou Ctrl+Maj+S).")):
            r = ctk.CTkFrame(s, fg_color="transparent")
            r.grid(row=i, column=0, sticky="ew", pady=3)
            r.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(r, text="", image=self._icon("check", 14, TEAL), fg_color="transparent", width=16).grid(
                row=0, column=0, sticky="n", padx=(2, 12), pady=(3, 0))
            self._label(r, text, self.fonts.small, TEXT, anchor="w", justify="left", wraplength=580).grid(
                row=0, column=1, sticky="w")
        s = self._section(body, 2, "Raccourcis clavier", "Dans la fenêtre de TreeAI Coach (les touches F9 à F11 "
                                                         "marchent aussi en jeu).", icon="keyboard")
        for i, (keys, what) in enumerate(ui_kit.SHORTCUTS):
            if keys.startswith("Échap"):
                what = "Fermer une fenêtre de dialogue"
            r = ctk.CTkFrame(s, fg_color="transparent")
            r.grid(row=i, column=0, sticky="ew", pady=3)
            r.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(r, text=keys, font=self.fonts.tiny_bold, text_color=GOLD, fg_color=PANEL_LO,
                         corner_radius=RADIUS, width=130, height=24).grid(row=0, column=0, sticky="w", padx=(0, 14))
            self._label(r, what, self.fonts.small, TEXT, anchor="w").grid(row=0, column=1, sticky="w")
        s = self._section(body, 3, "Dépannage", icon="target")
        for i, (q, a) in enumerate((
                ("« Capture noire »", "Le jeu est en plein écran exclusif : passe en « Sans bordure »."),
                ("La minimap n'est pas trouvée", "Vérifie l'échelle de la minimap dans le jeu, puis utilise "
                 "« Calibrer la minimap » (Réglages ou tableau de bord)."),
                ("Aucune voix", "Clique sur « Tester la voix ». Vérifie le volume de Windows ; sans Internet, "
                 "choisis une voix Windows dans « Alertes & voix »."),
                ("L'overlay n'apparaît pas", "Mode Sans bordure obligatoire ; vérifie l'interrupteur de l'onglet "
                 "Overlay (ou appuie sur F11)."),
                ("Alertes trop fréquentes ou trop tardives", "Choisis le préréglage « Discret » ou ajuste la "
                 "sensibilité dans « Alertes & voix »."),
                ("Un autre problème", "Réglages → « Copier le diagnostic » et colle-le dans ton message, avec le "
                 "dernier fichier du dossier des journaux."))):
            r = ctk.CTkFrame(s, fg_color="transparent")
            r.grid(row=i, column=0, sticky="ew", pady=5)
            r.grid_columnconfigure(0, weight=1)
            self._label(r, q, self.fonts.h3, GOLD_HOVER, anchor="w").grid(row=0, column=0, sticky="w")
            self._label(r, a, self.fonts.small, MUTED, anchor="w", justify="left", wraplength=600).grid(
                row=1, column=0, sticky="w")
        bar = ctk.CTkFrame(s, fg_color="transparent")
        bar.grid(row=10, column=0, sticky="w", pady=(10, 4))
        self._button(bar, "Copier le diagnostic", self.copy_diagnostic, "secondary", icon="copy").grid(
            row=0, column=0, padx=(0, 8))
        self._button(bar, "Ouvrir les journaux", self.open_logs, "secondary", icon="folder").grid(row=0, column=1)
        s = self._section(body, 4, "À propos et mentions légales", f"{APP_NAME} {__version__}", icon="info")
        self._label(s, ui_kit.ABOUT_TEXT, self.fonts.small, MUTED, anchor="w", justify="left",
                    wraplength=620).grid(row=0, column=0, sticky="w", pady=(4, 8))
        return page

    def _number_badge(self, parent: Any, n: int) -> Any:
        """Step number: the display face in the accent colour (no badge, no circle)."""
        return self._label(parent, str(n), self.fonts.stat, ACCENT, anchor="n", width=26)

    def _error_page(self, key: str) -> Any:
        page = self.ctk.CTkFrame(self.content, fg_color=BG, corner_radius=0)
        self._label(page, "Cette page n'a pas pu être affichée (voir les journaux).", self.fonts.h3,
                    DANGER).pack(pady=60)
        return page

    # ------------------------------------------------------------------ toasts & dialogs
    def show_toast(self, text: str, level: str = "info") -> None:
        """Small message at the bottom-right of the window for a few seconds (Tk thread)."""
        if self._closing:
            return
        try:
            if self._toast_frame is not None:
                self._toast_frame.destroy()
            if self._toast_job is not None:
                self.root.after_cancel(self._toast_job)
        except Exception:
            pass
        col = {"error": DANGER, "info": TEAL, "warning": WARNING}.get(level, TEAL)
        fr = self.ctk.CTkFrame(self.root, fg_color=PANEL_HI, corner_radius=0, border_width=1, border_color=LINE_STRONG)
        bar = self.ctk.CTkFrame(fr, width=3, height=22, corner_radius=0, fg_color=col)
        bar.grid(row=0, column=0, sticky="ns", padx=(1, 10), pady=1)
        self._label(fr, ui_text(text), self.fonts.small, TEXT, justify="left", wraplength=380).grid(
            row=0, column=1, padx=(0, 14), pady=9)
        fr.place(relx=1.0, rely=1.0, anchor="se", x=-16, y=-16)
        fr.lift()
        self._toast_frame = fr
        self._toast_job = self.root.after(TOAST_MS + (2000 if level == "error" else 0), self._hide_toast)

    def show_error(self, text: str) -> None:
        """French error toast (thread-safe: re-posted to the Tk thread if needed)."""
        if threading.current_thread() is not threading.main_thread():
            self._dispatcher.post(lambda: self.show_toast(text, "error"))
            return
        self.show_toast(text, "error")

    def _hide_toast(self) -> None:
        self._toast_job = None
        try:
            if self._toast_frame is not None:
                self._toast_frame.destroy()
        except Exception:
            pass
        self._toast_frame = None

    def _dialog(self, title: str, subtitle: str | None = None, icon: str | None = None,
                width: int = 460) -> tuple[Any, Any, Any, Callable[[], None]]:
        """Themed modal-less dialog: (toplevel, body frame, button bar, close function)."""
        ctk = self.ctk
        top = ctk.CTkToplevel(self.root)
        top.title(title)
        top.resizable(False, False)
        top.transient(self.root)
        top.configure(fg_color=SURFACE)
        self._set_window_icon(top)
        top.grid_columnconfigure(0, weight=1)
        card = ctk.CTkFrame(top, fg_color="transparent", corner_radius=0, border_width=0)
        card.grid(row=0, column=0, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 0))
        head.grid_columnconfigure(1, weight=1)
        self._label(head, ui_text(title), self.fonts.title, TEXT, anchor="w").grid(row=0, column=1, sticky="w")
        self._hline(card, LINE_STRONG).grid(row=1, column=0, sticky="ew", padx=20, pady=(8, 0))
        if subtitle:
            self._label(card, ui_text(subtitle), self.fonts.small, MUTED, anchor="w", justify="left",
                        wraplength=width - 60).grid(row=2, column=0, sticky="w", padx=20, pady=(8, 0))
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.grid(row=3, column=0, sticky="nsew", padx=20, pady=(10, 0))
        body.grid_columnconfigure(0, weight=1)
        bar = ctk.CTkFrame(card, fg_color="transparent")
        bar.grid(row=4, column=0, sticky="e", padx=20, pady=(14, 16))

        def close() -> None:
            try:
                top.grab_release()
            except Exception:
                pass
            try:
                top.destroy()
            except Exception:
                pass
            if getattr(self, "_open_dialog", None) is top:
                self._open_dialog = None

        top.protocol("WM_DELETE_WINDOW", close)
        top.bind("<Escape>", lambda _e: close(), add="+")
        old = getattr(self, "_open_dialog", None)
        if old is not None:
            try:
                old.destroy()
            except Exception:
                pass
        self._open_dialog = top
        top._close = close  # type: ignore[attr-defined]
        return top, body, bar, close

    def _place_dialog(self, top: Any, grab: bool = True) -> None:
        try:
            top.update_idletasks()
            x = self.root.winfo_rootx() + (self.root.winfo_width() - top.winfo_width()) // 2
            y = self.root.winfo_rooty() + (self.root.winfo_height() - top.winfo_height()) // 3
            top.geometry(f"+{max(0, x)}+{max(0, y)}")
            top.lift()
            top.focus_force()
            if grab:
                top.grab_set()
        except Exception:
            pass

    def _confirm(self, title: str, text: str, yes: str, on_yes: Callable[[], None],
                 kind: str = "danger") -> None:
        top, body, bar, close = self._dialog(title, icon="info")
        self._label(body, text, self.fonts.small, TEXT, anchor="w", justify="left", wraplength=400).grid(
            row=0, column=0, sticky="w")

        def ok() -> None:
            close()
            on_yes()

        self._button(bar, "Annuler", close, "secondary", width=110).grid(row=0, column=0, padx=(0, 8))
        self._button(bar, yes, ok, kind, width=130).grid(row=0, column=1)
        self._place_dialog(top)

    # ------------------------------------------------------------------ dialogs: changelog, about, onboarding
    def _first_run_dialogs(self) -> None:
        """Onboarding on the very first launch, else "Nouveautés" once per version."""
        if self._closing:
            return
        try:
            if not getattr(self.cfg, "ui_onboarding_done", True):
                self.show_onboarding()
            elif getattr(self.cfg, "ui_seen_changelog", ui_kit.CHANGELOG_VERSION) != ui_kit.CHANGELOG_VERSION:
                self.show_changelog()
        except Exception:
            log.exception("First-run dialog failed")

    def _mark(self, **fields: Any) -> None:
        """Store UI bookkeeping fields (onboarding / changelog seen) without applying anything live."""
        upd = {k: v for k, v in fields.items() if hasattr(self.cfg, k)}
        if upd:
            self.cfg = dataclasses.replace(self.cfg, **upd).validated()
            self._schedule_save()

    @_guarded
    def show_changelog(self) -> None:
        top, body, bar, close = self._dialog(f"Nouveautés v{ui_kit.CHANGELOG_VERSION}",
                                             "Ce qui change dans cette version de TreeAI Coach.", "sparkle", 500)
        for i, (title, text) in enumerate(ui_kit.CHANGELOG):
            r = self.ctk.CTkFrame(body, fg_color="transparent")
            r.grid(row=i, column=0, sticky="ew", pady=5)
            r.grid_columnconfigure(1, weight=1)
            self.ctk.CTkLabel(r, text="", image=self._icon("check", 14, TEAL), fg_color="transparent",
                              width=16).grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 10), pady=(3, 0))
            self._label(r, title, self.fonts.h3, TEXT, anchor="w").grid(row=0, column=1, sticky="w")
            self._label(r, text, self.fonts.small, MUTED, anchor="w", justify="left", wraplength=400).grid(
                row=1, column=1, sticky="w", pady=(2, 0))

        def ok() -> None:
            close()
            self._mark(ui_seen_changelog=ui_kit.CHANGELOG_VERSION)

        top.protocol("WM_DELETE_WINDOW", ok)
        self._button(bar, "OK", ok, "primary", width=80).grid(row=0, column=0)
        self._place_dialog(top, grab=False)

    @_guarded
    def show_about(self) -> None:
        top, body, bar, close = self._dialog("À propos de TreeAI Coach", f"Version {__version__}", "info", 520)
        self._label(body, ui_kit.ABOUT_TEXT, self.fonts.small, TEXT, anchor="w", justify="left",
                    wraplength=440).grid(row=0, column=0, sticky="w")
        self._button(bar, "Nouveautés", lambda: (close(), self.show_changelog()), "ghost", icon="star",
                     width=130).grid(row=0, column=0, padx=(0, 8))
        self._button(bar, "Fermer", close, "primary", width=110).grid(row=0, column=1)
        self._place_dialog(top, grab=False)

    @_guarded
    def show_onboarding(self, step: int = 0) -> None:
        """Guided first run ("Mode guidé", 3 steps): player level -> borderless check -> overlay + voice test."""
        steps = ui_kit.onboarding_steps()
        step = min(max(int(step), 0), len(steps) - 1)
        title, text = steps[step]
        top, body, bar, close = self._dialog("Mode guidé", f"Étape {step + 1} sur {len(steps)}", None, 520)
        self._onboarding_step = step
        dots = self.ctk.CTkFrame(body, fg_color="transparent")
        dots.grid(row=0, column=0, sticky="w", pady=(0, 12))
        import tkinter as tk  # noqa: PLC0415

        for i in range(len(steps)):
            tk.Frame(dots, width=self._scaled(40), height=max(2, self._scaled(3)), bd=0, highlightthickness=0,
                     bg=ACCENT if i <= step else LINE_STRONG).grid(row=0, column=i, padx=(0, self._scaled(4)))
        r = self.ctk.CTkFrame(body, fg_color="transparent")
        r.grid(row=1, column=0, sticky="ew")
        r.grid_columnconfigure(1, weight=1)
        self._number_badge(r, step + 1).grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 14))
        self._label(r, title, self.fonts.h3, TEXT, anchor="w").grid(row=0, column=1, sticky="w")
        self._label(r, text, self.fonts.small, MUTED, anchor="w", justify="left", wraplength=400).grid(
            row=1, column=1, sticky="w", pady=(3, 0))
        extra = self.ctk.CTkFrame(body, fg_color="transparent")
        extra.grid(row=2, column=0, sticky="ew", pady=(14, 0), padx=(40, 0))
        if step == 0:
            from treeaicoach import skill as _skill  # noqa: PLC0415

            cur = _skill.normalize(getattr(self.cfg, "skill_level", "intermediaire"))
            for i, (key, label) in enumerate(_skill.SKILL_LEVELS):
                b = self._button(extra, label, lambda k=key: (self.apply_skill_level(k), self._sync_skill_seg(),
                                                              close(), self.show_onboarding(0)),
                                 "primary" if key == cur else "secondary", width=100, height=28)
                b.grid(row=0, column=i, padx=(0, 6))
            self._label(extra, _skill.SKILL_HELP.get(cur, ""), self.fonts.tiny, MUTED, anchor="w", justify="left",
                        wraplength=420).grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))
        elif step == 1:
            status = self._label(extra, "Lecture des réglages du jeu…", self.fonts.small, MUTED, anchor="w")
            status.grid(row=0, column=0, sticky="w", padx=(0, 12))

            def check() -> None:
                status.configure(text="Lecture des réglages du jeu…", text_color=MUTED)

                def job() -> tuple[int, str]:
                    from treeaicoach import game_settings  # noqa: PLC0415

                    gs = game_settings.load_game_settings()
                    return ui_kit.window_mode_status(getattr(gs, "window_mode", None))

                def done(res: tuple[int, str]) -> None:
                    level, msg = res
                    try:
                        status.configure(text=msg, text_color={0: SAFE, 2: DANGER}.get(level, WARNING))
                    except Exception:
                        pass       # dialog closed meanwhile

                self._dispatcher.run(job, done, None, name="TreeAI-ui-window-mode")

            self._button(extra, "Vérifier", check, "secondary", icon="refresh", width=100, height=28).grid(
                row=0, column=1)
            self._label(extra, "Lu dans les fichiers de réglages du jeu (lecture seule). Change-le en jeu si besoin, "
                        "puis clique sur Vérifier.", self.fonts.tiny, DIM, anchor="w", justify="left",
                        wraplength=420).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
            check()
        else:
            self._button(extra, "Tester l'overlay", self.test_overlay, "secondary", icon="overlay", width=150,
                         height=28).grid(row=0, column=0, padx=(0, 8))
            self._button(extra, "Écouter", self.test_voice, "secondary", icon="voice", width=110, height=28).grid(
                row=0, column=1)
            self._label(extra, "Le test de l'overlay ne marche qu'en dehors d'une partie.", self.fonts.tiny, DIM,
                        anchor="w").grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

        def finish() -> None:
            close()
            self._mark(ui_onboarding_done=True, ui_seen_changelog=ui_kit.CHANGELOG_VERSION)
            self.show_toast("C'est prêt : lance une partie, l'analyse démarre toute seule.")

        top.protocol("WM_DELETE_WINDOW", finish)
        self._button(bar, "Passer", finish, "ghost", width=90).grid(row=0, column=0, padx=(0, 8))
        if step > 0:
            self._button(bar, "Précédent", lambda: (close(), self.show_onboarding(step - 1)), "secondary",
                         width=110).grid(row=0, column=1, padx=(0, 8))
        if step < len(steps) - 1:
            self._button(bar, "Suivant", lambda: (close(), self.show_onboarding(step + 1)), "primary",
                         width=110).grid(row=0, column=2)
        else:
            self._button(bar, "Terminer", finish, "primary", width=110).grid(row=0, column=2)
        self._place_dialog(top, grab=False)

    def _sync_skill_seg(self) -> None:
        """Sidebar level selector <- configuration."""
        seg = getattr(self, "skill_seg", None)
        if seg is None:
            return
        try:
            from treeaicoach import skill as _skill  # noqa: PLC0415

            short = {"debutant": "Déb.", "intermediaire": "Inter.", "avance": "Avancé", "expert": "Expert"}
            seg.set(short[_skill.normalize(getattr(self.cfg, "skill_level", "intermediaire"))])
        except Exception:
            log.debug("skill selector sync failed", exc_info=True)

    # ------------------------------------------------------------------ presets, diagnostics, shortcuts
    @_guarded
    def apply_preset(self, name: str) -> None:
        """Apply the Discret / Équilibré / Complet preset (alerts + overlay)."""
        changes = ui_kit.preset_changes(self.cfg, name)
        if not changes:
            return
        new = dataclasses.replace(self.cfg, **changes).validated()
        self._replace_config(new, changed=set(changes))
        self._refresh_all_widgets()
        label = dict(ui_kit.PRESET_LABELS).get(name, name)
        self.show_toast(f"Préréglage « {label} » appliqué.")

    def _refresh_all_widgets(self) -> None:
        for refresh in list(self._widgets_by_field.values()):
            try:
                refresh()
            except Exception:
                log.debug("Widget refresh failed", exc_info=True)
        self._refresh_radius_text()
        self._refresh_position_menus()
        self._sync_quick()
        self._refresh_preset_label()

    def _refresh_preset_label(self) -> None:
        seg = getattr(self, "preset_seg", None)
        if seg is None:
            return
        try:
            cur = ui_kit.preset_of(self.cfg)
            labels = dict(ui_kit.PRESET_LABELS)
            seg.set(labels.get(cur, "") if cur else "")
            self._set_text(self.preset_lbl, ui_kit.PRESET_HELP.get(cur, "") if cur else
                           "Personnalisé : tes réglages ne correspondent à aucun préréglage.")
        except Exception:
            log.debug("preset label refresh failed", exc_info=True)

    def diagnostic(self) -> str:
        """Plain-text diagnostic report (no secret)."""
        log_file = None
        try:
            logs = paths.logs_dir()
            files = sorted(Path(logs).glob("*.log"), key=lambda f: f.stat().st_mtime)
            log_file = files[-1] if files else None
        except Exception:
            pass
        cpu = getattr(getattr(self, "_cpu", None), "value", None)
        return ui_kit.diagnostic_text(version=__version__, cfg=self.cfg, status=self._get_status(),
                                      engine=self.engine, overlay=self.overlay, detector=self._detector,
                                      voice=self.voice, log_file=log_file, data_dir=paths.user_data_dir(),
                                      cpu=cpu, demo=self.demo)

    @_guarded
    def copy_diagnostic(self) -> None:
        text = self.diagnostic()
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.show_toast("Diagnostic copié : colle-le (Ctrl+V) dans ton message.")

    @_guarded
    def open_logs(self) -> None:
        if not open_path(paths.logs_dir()):
            self.show_error("Impossible d'ouvrir le dossier des journaux.")

    @_guarded
    def open_last_report(self) -> None:
        """Open the most recent game report (generated first when missing)."""
        def job() -> list[dict]:
            fn = _report_function("list_games")
            return [g for g in (fn(5) or []) if isinstance(g, dict)] if fn is not None else []

        def done(games: list[dict]) -> None:
            if not games:
                self.show_toast("Aucun rapport pour l'instant : joue une partie avec l'analyse active.")
                return
            best = max(games, key=lambda g: game_datetime(g) or _dt.datetime.min)
            self.open_report(best)

        self._dispatcher.run(job, done, self.cb(lambda e: self.show_error(f"Rapport impossible : {e}")),
                             name="TreeAI-ui-last-report")

    @_guarded
    def copy_share_summary(self) -> None:
        """Copy a shareable summary of the most recent game (hype.share_summary) to the clipboard."""
        eng = self.engine
        stats = {}
        try:
            fn = getattr(eng, "hype_stats", None)
            stats = fn() if callable(fn) else {}
        except Exception:
            stats = {}

        def job() -> str | None:
            fn = _report_function("list_games")
            games = [g for g in (fn(5) or []) if isinstance(g, dict)] if fn is not None else []
            if not games:
                return None
            best = max(games, key=lambda g: game_datetime(g) or _dt.datetime.min)
            src = _game_json_path(best)
            if src is None or not Path(src).is_file():
                return None
            import json  # noqa: PLC0415

            from treeaicoach.analysis import analyze_game  # noqa: PLC0415
            from treeaicoach.hype import share_summary  # noqa: PLC0415

            return share_summary(analyze_game(json.loads(Path(src).read_text(encoding="utf-8"))), stats)

        def done(text: str | None) -> None:
            if not text:
                self.show_toast("Aucune partie enregistrée pour l'instant.")
                return
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.show_toast("Résumé copié : colle-le (Ctrl+V) où tu veux.")

        self._dispatcher.run(job, done, self.cb(lambda e: self.show_error(f"Résumé impossible : {e}")),
                             name="TreeAI-ui-share")

    @_guarded
    def clear_journal(self) -> None:
        self._journal_hidden.update(self._journal)
        self._journal.clear()
        self._render_journal()

    def _in_game(self) -> bool:
        st = self._get_status()
        return (st is not None and self._engine_running() and state_key(getattr(st, "state", None)) == "RUNNING"
                and getattr(st, "game_time", None) is not None)

    def request_close(self) -> None:
        """Window close button: confirm first while a game is being analysed."""
        if self._closing:
            return
        try:
            if getattr(self.cfg, "ui_confirm_quit", True) and self._in_game():
                self._confirm("Quitter pendant la partie ?",
                              "Une partie est en cours d'analyse : en quittant, tu n'auras plus d'alertes ni de "
                              "rapport pour cette partie. Tu peux plutôt réduire la fenêtre.", "Quitter",
                              self.close)
                return
        except Exception:
            log.exception("Close confirmation failed")
        self.close()

    # ------------------------------------------------------------------ settings plumbing
    def set_option(self, field: str, value: Any) -> None:
        """Change one setting: validate, apply live, save (debounced)."""
        if not hasattr(self.cfg, field):
            log.warning("Unknown setting %s", field)
            return
        if getattr(self.cfg, field) == value:
            return
        new = dataclasses.replace(self.cfg, **{field: value}).validated()
        self._replace_config(new, changed={field})

    def _replace_config(self, new: Config, changed: set[str]) -> None:
        old = self.cfg
        self.cfg = new
        diff = {f for f in changed if getattr(old, f, None) != getattr(new, f, None)} | (
            {f.name for f in dataclasses.fields(Config) if getattr(old, f.name) != getattr(new, f.name)})
        if not diff:
            return
        self._apply_live(diff)
        self._schedule_save()

    def _apply_live(self, diff: set[str]) -> None:
        cfg = self.cfg
        if self.engine is not None:
            try:
                self.engine.apply_config(cfg)
            except Exception:
                log.exception("engine.apply_config failed")
        if self.overlay is not None:
            try:
                self.overlay.apply_config(cfg)
            except Exception:
                log.exception("overlay.apply_config failed")
        if diff & VOICE_FIELDS and self.voice is not None:
            base = dict(voice_name=cfg.voice_name, rate=cfg.voice_rate, volume=cfg.voice_volume,
                        beep_on_danger=cfg.beep_on_danger)
            try:
                extra = {k: getattr(cfg, f) for k, f in (("engine", "voice_engine"), ("neural_voice", "neural_voice"),
                                                         ("neural_rate", "neural_rate")) if hasattr(cfg, f)}
                try:
                    self.voice.set_params(**base, **extra)
                except TypeError:          # older voice module without engine selection
                    self.voice.set_params(**base)
            except Exception:
                log.exception("voice.set_params failed")
        if diff & HOTKEY_FIELDS:
            self._rebind_hotkeys()
        if diff & DETECTOR_FIELDS and self.engine is not None:
            self._rebuild_engine(self.demo, start=None, new_detector=True)
        if diff & {"ai_provider", "ai_api_key", "ai_model"}:
            self._ai_test = None           # the last key test no longer applies
        if diff & {"sensitivity", "warn_radius", "danger_radius"}:
            self._refresh_radius_text()
        if "manual_minimap_rect" in diff or "minimap_mode" in diff:
            try:
                if getattr(self, "_rect_desc", None) is not None:
                    self._rect_desc.configure(text=self._manual_rect_text())
            except Exception:
                pass

    def _schedule_save(self) -> None:
        if self._save_job is not None:
            try:
                self.root.after_cancel(self._save_job)
            except Exception:
                pass
        self._save_job = self.root.after(SAVE_DEBOUNCE_MS, self.save_now)

    def save_now(self) -> None:
        """Write the configuration now (Tk thread)."""
        self._save_job = None
        save_config(self.cfg, self._save_path)

    # ------------------------------------------------------------------ backend lifecycle
    def _start_backend(self) -> None:
        cfg = self.cfg
        demo = self.demo

        def job() -> dict[str, Any]:
            out: dict[str, Any] = {}
            voice = self.voice
            if voice is None:
                try:
                    from treeaicoach.voice import VoiceEngine  # noqa: PLC0415

                    extra = {k: getattr(cfg, f) for k, f in (("engine", "voice_engine"),
                                                             ("neural_voice", "neural_voice"),
                                                             ("neural_rate", "neural_rate")) if hasattr(cfg, f)}
                    try:
                        voice = VoiceEngine(cfg.voice_name, cfg.voice_rate, cfg.voice_volume, cfg.beep_on_danger,
                                            **extra)
                    except TypeError:
                        voice = VoiceEngine(cfg.voice_name, cfg.voice_rate, cfg.voice_volume, cfg.beep_on_danger)
                    voice.start()
                except Exception:
                    log.exception("Voice unavailable")
                    voice = None
            out["voice"] = voice
            try:
                out["detector"] = self._detector_factory(cfg)
            except Exception:
                log.exception("Detector unavailable")
                out["detector"] = None
            try:
                src = self._demo_source_factory() if demo else None
                eng = self._engine_factory(cfg, voice, out["detector"], src)
                out["engine"] = eng
            except Exception as exc:
                log.exception("Cannot create the analysis engine")
                out["engine"] = None
                out["error"] = f"Le moteur d'analyse n'a pas pu démarrer : {exc}"
            try:
                ov = self._overlay_factory(cfg, self._overlay_provider)
                out["overlay"] = ov
                if ov is not None:
                    set_cb = getattr(ov, "set_on_moved", None)
                    if callable(set_cb):
                        set_cb(self._on_overlay_moved)
                    ov.start()
            except Exception:
                log.exception("Overlay unavailable")
                out["overlay"] = None
            eng = out.get("engine")
            if eng is not None and (cfg.autostart or demo):
                try:
                    eng.start()
                except Exception as exc:
                    log.exception("Engine start failed")
                    out["error"] = f"Impossible de démarrer l'analyse : {exc}"
            self._track_engine(eng)
            if self._closing:     # window closed during start-up: _backend_ready will never run
                self._shutdown_components(None, out.get("overlay"), voice if self._own_voice else None)
            return out

        def failed(exc: BaseException) -> None:
            # never leave the launcher stuck on "Démarrage…" (start button disabled forever)
            self._busy = False
            self.engine_error = f"Le démarrage a échoué : {exc}. Clique sur « Démarrer l'analyse » pour réessayer."
            self.show_error(self.engine_error)
            self._refresh_status()

        self._busy = True
        self._backend_t0 = time.monotonic()
        self._dispatcher.run(job, self._backend_ready, self.cb(failed), name="TreeAI-ui-init")

    @_guarded
    def _backend_ready(self, out: dict[str, Any]) -> None:
        self._busy = False
        if self._closing:
            # the window was closed during start-up: stop what was created
            self._shutdown_components(out.get("engine"), out.get("overlay"),
                                      out.get("voice") if self._own_voice else None)
            return
        self.voice = out.get("voice")
        self._detector = out.get("detector")
        self.engine = out.get("engine")
        self.overlay = out.get("overlay")
        self.engine_error = out.get("error") if self.engine is None else None
        if out.get("error"):
            self.show_error(out["error"])
        self._load_voices()
        if self._want_hotkeys:
            self._rebind_hotkeys()
        self._refresh_status()

    def _on_overlay_moved(self, name: str, x: int, y: int) -> None:
        """Overlay thread callback (move mode): save the new window position (Tk thread)."""
        def apply() -> None:
            field = {"radar": "radar", "hud": "hud"}.get(str(name))
            if field is None:
                return
            upd = {f"{field}_xy": [int(x), int(y)], f"{field}_position": "custom"}
            new = dataclasses.replace(self.cfg, **upd).validated()
            self._replace_config(new, changed=set(upd))
            self._refresh_position_menus()
        self._dispatcher.post(self.cb(apply))

    def _refresh_position_menus(self) -> None:
        for field, which in (("radar_position", "radar"), ("hud_position", "hud")):
            menu = self._position_menus.get(which) if hasattr(self, "_position_menus") else None
            if menu is None:
                continue
            choices = self._position_choices(which)
            try:
                menu.configure(values=[lbl for _v, lbl in choices])
                menu.set(dict(choices).get(getattr(self.cfg, field), choices[0][1]))
            except Exception:
                log.debug("Position menu refresh failed", exc_info=True)

    def _track_engine(self, eng: Any) -> None:
        """Remember an engine built on a worker thread so that close() always stops it."""
        if eng is None:
            return
        with self._eng_lock:
            self._created_engines.append(eng)
            del self._created_engines[:-4]
            closing = self._closing
        if closing:
            try:
                eng.stop()
            except Exception:
                log.exception("Cannot stop an engine created during shutdown")

    def _update_system(self, st: Any, key: str, running: bool) -> None:
        """Dashboard "Système" rows (ui_kit.subsystem_rows) + a LoL client probe every 30 s."""
        rows = getattr(self, "sys_rows", None)
        if not rows:
            return
        now = time.monotonic()
        if now - self._lcu_polled > 30.0 and self._current_page == "dashboard":
            self._lcu_polled = now
            self._refresh_lcu_status()
        det = str(getattr(st, "detector", "") or getattr(self._detector, "name", "") or "")
        vb = str(getattr(self.voice, "backend", "") or getattr(st, "voice", "") or "")
        data = ui_kit.subsystem_rows(
            state=key, message=str(getattr(st, "message", "") or ""), running=running, demo=self.demo,
            minimap_found=getattr(st, "minimap_rect", None) is not None,
            minimap_method=getattr(st, "locate_method", None), detector=det, voice_backend=vb,
            muted=self._is_muted(), lcu_text=self._lcu_text, lcu_enabled=bool(getattr(self.cfg, "lcu_enabled", True)),
            engine_ok=self.engine is not None or self._busy, ai_provider=str(getattr(self.cfg, "ai_provider", "off")),
            ai_key_set=bool(str(getattr(self.cfg, "ai_api_key", "") or "").strip()), ai_budget=self._ai_budget(),
            ai_test=None if self._ai_test_busy else self._ai_test)
        if self._ai_test_busy:     # "test en cours" replaces the AI row while the request runs
            data = [r if r[0] != "ai" else (r[0], r[1], -1, "test en cours…", "", "") for r in data]
        cols = {0: SAFE, 1: WARNING, 2: DANGER, -1: DIM}
        for k, label, level, text, fix, action in data:
            row = rows.get(k)
            if row is None:
                continue
            sig = (level, text, fix)
            if row["sig"] == sig:
                continue
            row["sig"] = sig
            row["action"] = action
            row["dot"].configure(fg_color=cols.get(level, DIM))
            row["val"].configure(text=ui_text(text), text_color=TEXT if level in (1, 2) else MUTED)
            if fix and action:
                row["btn"].configure(text=fix, text_color=DANGER if level == 2 else ACCENT)
                row["btn"].grid()
            else:
                row["btn"].grid_remove()

    @_guarded
    def _system_fix(self, key: str) -> None:
        row = (getattr(self, "sys_rows", {}) or {}).get(key) or {}
        self._run_fix(str(row.get("action") or ""))

    @_guarded
    def _run_fix(self, action: str) -> None:
        """One-click fix of a "Système" row / of the first-game checklist, by action name."""
        if action == "start":
            self.toggle_engine()
        elif action == "calibrate":
            self.calibrate()
        elif action == "help_borderless":
            self.show_page("help")
            self.show_toast("Dans le jeu : Options > Vidéo > Mode d'affichage : Sans bordure.", "warning")
        elif action == "diagnostic":
            self.copy_diagnostic()
        elif action == "settings_ia":
            self.show_page("settings")
            sel = getattr(self.pages.get("settings"), "select_tab", None)
            if callable(sel):
                sel("Minimap")
        elif action == "settings_ai":
            self.show_page("settings")
            sel = getattr(self.pages.get("settings"), "select_tab", None)
            if callable(sel):
                sel("IA")
        elif action == "test_ai":
            self.test_ai_key()
        elif action == "voice":
            self.test_voice()
        elif action == "unmute":
            self.toggle_mute()
        elif action == "voice_settings":
            self.show_page("alerts")
            sel = getattr(self.pages.get("alerts"), "select_tab", None)
            if callable(sel):
                sel("Voix")
            self.show_toast("Aucune voix Windows trouvée : choisis la voix neurale (Internet) ou installe une "
                            "voix française (Paramètres Windows > Heure et langue > Voix).", "warning")
        elif action == "lcu_help":
            self.show_toast("Laisse le client League of Legends ouvert : après la partie, TreeAI y lit tes "
                            "vraies stats (lecture seule). Rien à régler.")

    @_guarded
    def test_overlay(self) -> None:
        """Show the overlay on sample states (sûr / attention / danger) for 10 s, out of game."""
        ov = self.overlay
        if self._in_game():
            self.show_toast("Une partie est en cours : l'overlay affiche déjà la vraie partie.")
            return
        if ov is None or not getattr(ov, "ok", False):
            self.show_page("overlay")
            self.show_toast("L'overlay ne s'affiche que sous Windows, jeu en Sans bordure. Voici l'aperçu.",
                            "warning")
            return
        if not self.cfg.overlay_enabled:
            self.set_option("overlay_enabled", True)
            self._sync_quick()

        def job() -> list:
            from treeaicoach import overlay_render as orr  # noqa: PLC0415

            states = orr.sample_states()
            sw, sh = 1920, 1080
            try:
                from treeaicoach.capture import monitor_rects  # noqa: PLC0415

                mons = monitor_rects()
                if mons:
                    sw, sh = int(mons[0].w), int(mons[0].h)
            except Exception:
                log.debug("monitor size unknown", exc_info=True)
            out = []
            for name in ("safe", "warning", "danger"):
                st = states.get(name)
                if st is None:
                    continue
                mm = orr.default_minimap_rect(sw, sh)
                try:
                    rect_t = type(st.minimap_rect) if st.minimap_rect is not None else tuple
                    st = dataclasses.replace(st, minimap_rect=rect_t(*mm), screen_rect=rect_t(0, 0, sw, sh))
                except Exception:
                    log.debug("sample state not resized", exc_info=True)
                out.append(st)
            return out

        def done(states: list) -> None:
            if not states:
                self.show_error("Aperçu de l'overlay indisponible.")
                return
            self._overlay_test = (time.monotonic(), states)
            self.show_toast("Overlay de test affiché 10 s : sûr, attention, puis danger.")

        self._dispatcher.run(job, done, self.cb(lambda e: self.show_error(f"Test de l'overlay impossible : {e}")),
                             name="TreeAI-ui-overlay-test")

    def _overlay_provider(self) -> Any:
        """State provider given to the overlay thread: the "Tester l'overlay" samples for 10 s,
        else the engine snapshot."""
        test = getattr(self, "_overlay_test", None)
        if test is not None:
            t0, states = test
            el = time.monotonic() - t0
            if el < 10.0 and states:
                return states[min(len(states) - 1, int(el / (10.0 / len(states))))]
            self._overlay_test = None
        return self._overlay_state()

    def _overlay_state(self) -> Any:
        """Thread-safe engine snapshot of the overlay state (None out of game)."""
        eng = self.engine
        if eng is None:
            return None
        try:
            return eng.get_overlay_state()
        except Exception:
            log.debug("get_overlay_state failed", exc_info=True)
            return None

    def _radar_source(self) -> tuple[Any, Any]:
        """(overlay state, raw preview) for the radar worker thread."""
        eng = self.engine
        if eng is None:
            return None, None
        state = None
        try:
            state = eng.get_overlay_state()
        except Exception:
            state = None
        if state is not None:
            return state, None
        try:
            return None, eng.get_preview()
        except Exception:
            return None, None

    def _engine_running(self) -> bool:
        try:
            return bool(self.engine is not None and self.engine.is_running())
        except Exception:
            return False

    @_guarded
    def toggle_engine(self) -> None:
        """Start / stop the analysis (worker thread: stop may take up to 3 s)."""
        if self._busy:
            return
        if self.engine is None:
            if self.engine_error:
                self.show_error(self.engine_error)
            self._rebuild_engine(self.demo, start=True, new_detector=self._detector is None)
            return
        eng = self.engine
        running = self._engine_running()
        self._busy = True
        self.btn_start.configure(state="disabled", text="Arrêt…" if running else "Démarrage…")

        def job() -> None:
            if running:
                eng.stop()
            else:
                eng.start()

        def done(_r: Any = None) -> None:
            self._busy = False
            self._refresh_status()

        def failed(exc: BaseException) -> None:
            self._busy = False
            self.show_error(f"Impossible de {'arrêter' if running else 'démarrer'} l'analyse : {exc}")
            self._refresh_status()

        self._dispatcher.run(job, done, failed, name="TreeAI-ui-engine")

    @_guarded
    def toggle_demo(self) -> None:
        """Switch between the simulated game (DemoSource) and the real screen analysis."""
        if self._busy:
            return
        self._rebuild_engine(not self.demo, start=True)
        self.show_toast("Mode démo : partie simulée, le jungler ennemi va venir te ganker vers 40 s."
                        if not self.demo else "Retour à l'analyse réelle.")

    def _rebuild_engine(self, demo: bool, start: bool | None, new_detector: bool = False) -> None:
        """Replace the engine (demo toggle, detector change). ``start`` None = keep the running state."""
        if self._busy:
            # try again once the current operation is over
            self.root.after(300, lambda: self._rebuild_engine(demo, start, new_detector))
            return
        old = self.engine
        was_running = self._engine_running()
        want_start = was_running if start is None else start
        cfg, voice = self.cfg, self.voice
        self._busy = True
        self.demo = demo
        try:
            self.btn_start.configure(state="disabled")
        except Exception:
            pass

        def job() -> tuple[Any, Any, str | None]:
            if old is not None:
                try:
                    old.stop()
                except Exception:
                    log.exception("Old engine stop failed")
            det = self._detector
            if new_detector or det is None:
                try:
                    det = self._detector_factory(cfg)
                except Exception:
                    log.exception("Detector unavailable")
            try:
                src = self._demo_source_factory() if demo else None
                eng = self._engine_factory(cfg, voice, det, src)
                try:
                    if want_start:
                        eng.start()
                finally:
                    self._track_engine(eng)
                return eng, det, None
            except Exception as exc:
                log.exception("Cannot rebuild the engine")
                return None, det, f"Le moteur d'analyse n'a pas pu démarrer : {exc}"

        def done(res: tuple[Any, Any, str | None]) -> None:
            self._busy = False
            eng, det, err = res
            if self._closing:
                self._shutdown_components(eng, None, None)
                return
            self.engine, self._detector, self.engine_error = eng, det, err
            self._journal.clear()
            self._last_alert_seen = None
            self._render_journal()
            if err:
                self.show_error(err)
            self._refresh_status()

        def failed(exc: BaseException) -> None:
            self._busy = False
            self.engine_error = f"Le moteur d'analyse n'a pas pu redémarrer : {exc}"
            self.show_error(self.engine_error)
            self._refresh_status()

        self._dispatcher.run(job, done, self.cb(failed), name="TreeAI-ui-rebuild")

    @_guarded
    def test_voice(self) -> None:
        if self.voice is None:
            self.show_error("La synthèse vocale n'est pas disponible.")
            return
        self.voice.say("Test de la voix. Attention, Lee Sin approche !", 1)
        backend = getattr(self.voice, "backend", "")
        if backend == "print":
            self.show_toast("Voix indisponible sur ce système : le message est écrit dans le journal.", "warning")
        else:
            self.show_toast("Test de la voix en cours…")

    @_guarded
    def relocate(self, quiet: bool = False) -> None:
        if self.engine is not None:
            self.engine.request_relocate()
            if not quiet:
                self.show_toast("Recherche de la minimap relancée.")
        elif not quiet:
            self.show_error("Le moteur d'analyse n'est pas disponible.")

    @_guarded
    def calibrate(self) -> None:
        """Manual minimap calibration (modal)."""
        from treeaicoach.calibration import run_calibration  # noqa: PLC0415

        rect = run_calibration(self.root, self.cfg)
        if rect:
            new = dataclasses.replace(self.cfg, manual_minimap_rect=dict(rect), minimap_mode="manual").validated()
            self._replace_config(new, changed={"manual_minimap_rect", "minimap_mode"})
            refresh = self._widgets_by_field.get("minimap_mode")
            if refresh:
                refresh()
            self.relocate(quiet=True)
            self.show_toast(f"Minimap calibrée : {rect['w']} × {rect['h']} px.")

    @_guarded
    def toggle_move_mode(self) -> None:
        """Overlay "move" mode: the windows become draggable; each drop is saved (on_moved)."""
        ov = self.overlay
        setter = getattr(ov, "set_move_mode", None) if ov is not None else None
        if not callable(setter) or not bool(getattr(ov, "ok", True)):
            self.show_error("Le déplacement des fenêtres de l'overlay n'est disponible que sous Windows.")
            return
        self._move_mode = not self._move_mode
        setter(self._move_mode)
        if self._move_mode:
            self.btn_move.configure(text="Terminer le déplacement", fg_color=GOLD, text_color=ON_GOLD,
                                    hover_color=GOLD_HOVER, image=self._icon("move", 16, ON_GOLD))
            self.show_toast("Fais glisser le radar et le HUD à la souris, puis clique sur « Terminer ».")
        else:
            self.btn_move.configure(text="Déplacer les fenêtres", fg_color=PANEL_HI, text_color=TEXT,
                                    hover_color=HOVER, image=self._icon("move", 16, MUTED))
            self.show_toast("Positions de l'overlay enregistrées.")

    # ------------------------------------------------------------------ hotkeys
    def _rebind_hotkeys(self) -> None:
        cfg = self.cfg
        bindings: dict[str, Callable[[], None]] = {}
        for key, fn in ((cfg.hotkey_jungler, self._hk_jungler), (cfg.hotkey_mute, self._hk_mute),
                        (cfg.hotkey_overlay, self._hk_overlay), (getattr(cfg, "hotkey_ai", ""), self._hk_ai),
                        (getattr(cfg, "hotkey_ward", ""), self._hk_ward)):
            if key:
                bindings[key] = fn
        try:
            if self._hotkeys is None:
                from treeaicoach.hotkeys import HotkeyListener  # noqa: PLC0415

                self._hotkeys = HotkeyListener(bindings)
                hk = self._hotkeys
                self._dispatcher.run(hk.start, self._hotkeys_started, name="TreeAI-ui-hotkeys")
            else:
                hk = self._hotkeys
                self._dispatcher.run(lambda: hk.set_bindings(bindings), self._hotkeys_started,
                                     name="TreeAI-ui-hotkeys")
        except Exception:
            log.exception("Hotkeys unavailable")

    def _hotkeys_started(self, _r: Any = None) -> None:
        hk = self._hotkeys
        failed = list(getattr(hk, "failed", []) or [])
        if failed:
            self.show_toast(f"Raccourci déjà utilisé par une autre application : {', '.join(failed)}", "warning")

    def _hk_jungler(self) -> None:          # hotkey thread
        eng, voice = self.engine, self.voice
        try:
            speak = getattr(eng, "speak_jungler_status", None)
            if callable(speak):
                speak()
                return
            text = eng.jungler_status_text() if eng is not None else ""
            if text and voice is not None:
                voice.say(text, 1)
        except Exception:
            log.exception("F9 hotkey failed")

    def _hk_ai(self) -> None:               # hotkey thread
        self._dispatcher.post(self.ask_ai)

    def _hk_ward(self) -> None:             # hotkey thread: "where to ward?" (visual only, cheap)
        fn = getattr(self.engine, "request_ward_guide", None)
        if callable(fn):
            try:
                fn()
            except Exception:
                log.debug("ward guide hotkey failed", exc_info=True)

    @_guarded
    def ask_ai(self) -> None:
        """"Demander à l'IA": manual request through the engine (the answer comes back as a toast)."""
        fn = getattr(self.engine, "ask_ai", None)
        if not callable(fn):
            self.show_toast("Conseil IA indisponible : le moteur n'est pas démarré.")
            return
        self._dispatcher.run(fn, lambda msg: self.show_toast(str(msg or "")),
                             self.cb(lambda e: self.show_error(f"Conseil IA impossible : {e}")),
                             name="TreeAI-ui-ask-ai")

    def _hk_mute(self) -> None:             # hotkey thread
        eng = self.engine
        self._muted = not bool(getattr(eng, "muted", self._muted))
        try:
            if eng is not None and hasattr(eng, "mute"):
                eng.mute(self._muted)
            elif self.voice is not None and hasattr(self.voice, "set_muted"):
                self.voice.set_muted(self._muted)
        except Exception:
            log.exception("Mute hotkey failed")
        muted = self._muted
        self._dispatcher.post(lambda: self.show_toast("Voix coupée." if muted else "Voix rétablie."))

    def _hk_overlay(self) -> None:          # hotkey thread
        try:
            if self.engine is not None and hasattr(self.engine, "toggle_overlay"):
                self.engine.toggle_overlay()
        except Exception:
            log.exception("Overlay hotkey failed")

    # ------------------------------------------------------------------ periodic refresh
    def _status_loop(self) -> None:
        if self._closing:
            return
        try:
            self._refresh_status()
        except Exception:
            log.exception("Status refresh failed")
        self.root.after(STATUS_MS, self._status_loop)

    def _get_status(self) -> Any:
        if self.engine is None:
            return None
        try:
            return self.engine.get_status()
        except Exception:
            log.debug("get_status failed", exc_info=True)
            return None

    def _refresh_status(self) -> None:
        st = self._get_status()
        ov = self._overlay_state() if st is not None else None
        running = self._engine_running()
        if self.engine is None:
            key = "STARTING" if self._busy else "NO_ENGINE"
            slow = self._busy and time.monotonic() - getattr(self, "_backend_t0", time.monotonic()) > 30.0
            msg = (("Chargement plus long que prévu (premier lancement ou analyse antivirus)…" if slow else
                    "Chargement du détecteur et de la voix…") if self._busy else
                   (self.engine_error or "Le moteur d'analyse n'est pas disponible."))
        else:
            key = state_key(getattr(st, "state", None)) if st is not None else "STOPPED"
            if not running and key not in ("ERROR",):
                key = "STOPPED"
            msg = str(getattr(st, "message", "") or "") if st is not None else ""
            if key == "STOPPED" and not msg:
                msg = "Clique sur « Démarrer l'analyse » pour suivre ta prochaine partie."
        title, color = STATE_INFO.get(key, (key.title(), GOLD))
        msg = self._with_extras(msg, key)
        self._set_text(self.state_title, title)
        self._set_text(self.state_msg, msg or " ")
        self._state_color = color
        if key != self._last_state_key:
            if self._last_state_key == "RUNNING" and key != "RUNNING":
                self.root.after(4000, self.cb(self.refresh_games))     # a game just ended: new record
            self._last_state_key = key
            self.pill_dot.itemconfigure(self._pill_dot_item, fill=color)
            self.pill_text.configure(text=PILL_TEXT.get(key, title) + (" · démo" if self.demo else ""))
        if self.demo:
            self.demo_badge.grid(row=0, column=1, padx=(10, 0))
        else:
            self.demo_badge.grid_remove()
        pill = PILL_TEXT.get(key, title) + (" · démo" if self.demo else "")
        self._set_text(self.pill_text, pill)

        # start / stop button
        if self._busy:
            self.btn_start.configure(state="disabled")
        elif running:
            self._style_start(False)
        else:
            self._style_start(True)
        self._demo_button_text()

        gt = getattr(st, "game_time", None) if st is not None else None
        self._set_text(self.clock_lbl, fmt_clock(gt))
        self.clock_lbl.configure(text_color=TEXT if gt is not None else DIM)

        # tech tiles
        fps = getattr(st, "fps", None) if st is not None else None
        self._set_text(self.tech["fps"], fmt_decimal_fr(fps, 1) if isinstance(fps, (int, float)) and running
                       and key == "RUNNING" else "-")
        det = str(getattr(st, "detector", "") or getattr(self._detector, "name", "") or "-")
        self._set_text(self.tech["detector"], detector_short(det))
        vname = str(getattr(st, "voice", "") or getattr(self.voice, "backend", "") or "-")
        if st is not None and getattr(st, "muted", False):
            self._set_text(self.tech["voice"], "Coupée")
        else:
            self._set_text(self.tech["voice"], _VOICE_FR.get(vname.lower(), vname)[:10])
        banner = getattr(st, "banner", None) if st is not None else None
        if banner and banner != self._banner_dismissed:
            self._set_text(self.banner_lbl, str(banner))
            if not self.banner.grid_info():
                self.banner.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        elif self.banner.grid_info():
            self.banner.grid_remove()

        cpu = self._cpu.sample() if getattr(self, "_cpu", None) is not None else None
        self._set_text(self.tech["cpu"], "-" if cpu is None else f"{cpu:.0f} %")
        try:
            self._update_system(st, key, running)
        except Exception:
            log.debug("system rows update failed", exc_info=True)
        self._update_threat(ov)
        try:
            self._set_text(self.hero.timers, ui_kit.objectives_text(ov) if ov is not None else "")
        except Exception:
            log.debug("objective timers failed", exc_info=True)
        self._update_enemies(ov, st)
        try:
            self._update_team(ov)
        except Exception:
            log.debug("team update failed", exc_info=True)
        self._collect_alerts(st, ov)
        self._journal_caption()
        self._pregame_tick = (getattr(self, "_pregame_tick", 0) + 1) % 8
        if self._pregame_tick == 0 and self._current_page == "dashboard" and not self._journal:
            self._render_pregame()          # the game's own goal / its status can change
        try:
            self._update_coach_strip(ov, running and key == "RUNNING")
        except Exception:
            log.debug("coach strip update failed", exc_info=True)
        lvl = min(max(int(getattr(ov, "threat_level", 0) or 0), 0), 2) if ov is not None else 0
        self.hero.set_glow(THREAT_COLORS[lvl] if ov is not None and lvl > 0 else color)
        muted = self._is_muted()
        if muted != getattr(self, "_quick_muted", None):
            self._quick_muted = muted
            self._sync_quick(muted)
        if self._current_page == "dashboard":
            self._draw_gauge_step()

    def _update_coach_strip(self, ov: Any, live: bool) -> None:
        """Dashboard coach strip: gauge, detected role (+ swap notice), top tip, AI counter."""
        if getattr(self, "coach_gauge_lbl", None) is None:
            return
        eng = self.engine
        gauge = tip = None
        role = notice = None
        ai = ""
        if eng is not None and live:
            fn = getattr(eng, "play_gauge", None)
            gauge = fn() if callable(fn) else None
            fn = getattr(eng, "top_tip", None)
            tip = fn() if callable(fn) else None
            fn = getattr(eng, "detected_role", None)
            role, notice = fn() if callable(fn) else (None, None)
            fn = getattr(eng, "ai_budget_text", None)
            ai = (fn() if callable(fn) else "") or ""
        step = getattr(gauge, "step", None)
        sig = (step, getattr(gauge, "reason", None), tip, role, notice, ai)
        if sig == self._coach_sig:
            return
        self._coach_sig = sig
        if step is None:
            self.coach_gauge_lbl.configure(text="-" if live else "", text_color=DIM)
        else:
            col = GAUGE_UI_COLORS.get(int(step), MUTED)
            self.coach_gauge_lbl.configure(text=str(getattr(gauge, "label", "") or "-"), text_color=col)
        role_txt = f"Rôle : {role}" if role else ("Rôle : -" if live else "")
        if notice:
            role_txt += " · échange de voie"
        reason = str(getattr(gauge, "reason", "") or "")
        if reason:
            role_txt += f" · {reason}"
        self.coach_role_lbl.configure(text=role_txt, text_color=WARNING if notice else MUTED)
        self.coach_ai_lbl.configure(text=ai)
        if tip:
            text, tone = tip
            self.coach_tip_lbl.configure(text=str(text), text_color=TIP_UI_COLORS.get(str(tone), TEXT))
        else:
            self.coach_tip_lbl.configure(
                text="Le conseil du moment s'affichera ici pendant la partie." if not live else "Rien à signaler.",
                text_color=DIM)

    def _with_extras(self, msg: str, key: str) -> str:
        """Win probability (hype.py) appended to the dashboard message; new AI error shown once."""
        eng = self.engine
        if eng is None:
            return msg
        try:
            status = getattr(eng, "ai_status", None)
            if callable(status):
                seq, text = status()
                if text and seq != getattr(self, "_ai_status_seq", 0):
                    self._ai_status_seq = seq
                    self._set_ai_status(text, DANGER)
                    self.show_error(text)
            aseq = getattr(eng, "ai_answer_seq", 0)
            if isinstance(aseq, int) and aseq != getattr(self, "_ai_answer_seq", 0):
                self._ai_answer_seq = aseq
                answer = getattr(eng, "last_ai_advice", None)
                if answer:
                    self.show_toast(f"IA : {answer}")
            wp = getattr(eng, "win_probability", None)
            p = wp() if callable(wp) and key == "RUNNING" and getattr(self.cfg, "win_prob_hud", True) else None
            if isinstance(p, (int, float)):
                return f"{msg} · Probabilité de victoire : {int(round(100 * p))} %" if msg else \
                    f"Probabilité de victoire : {int(round(100 * p))} %"
        except Exception:
            log.debug("win probability / AI status unavailable", exc_info=True)
        return msg

    def _dismiss_banner(self) -> None:
        self._banner_dismissed = self.banner_lbl.cget("text")
        self.banner.grid_remove()

    def _style_start(self, start: bool) -> None:
        want = "start" if start else "stop"
        if getattr(self, "_start_style", None) == want and str(self.btn_start.cget("state")) == "normal":
            return
        self._start_style = want
        if start:
            self.btn_start.configure(state="normal" if self.engine is not None or not self._busy else "disabled",
                                     text="Démarrer l'analyse", fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                     text_color=ON_ACCENT, border_width=0, image=self._icon("play", 12, ON_ACCENT))
        else:
            self.btn_start.configure(state="normal", text="Arrêter l'analyse", fg_color=PANEL_HI,
                                     hover_color=DANGER_DARK, text_color=TEXT, border_width=1,
                                     border_color=LINE_STRONG, image=self._icon("stop", 10, DANGER))

    @staticmethod
    def _set_text(widget: Any, text: str) -> None:
        text = ui_text(text)
        try:
            if widget.cget("text") != text:
                widget.configure(text=text)
        except Exception:
            pass

    def _update_threat(self, ov: Any) -> None:
        if ov is None:
            self._set_text(self.threat_lbl, "-")
            self.threat_lbl.configure(text_color=DIM)
            self._set_text(self.threat_detail, "Hors partie")
            self._gauge_target, self._gauge_color = 0.0, DIM
            return
        lvl = int(getattr(ov, "threat_level", 0) or 0)
        lvl = min(max(lvl, 0), 2)
        text = str(getattr(ov, "threat_text", "") or THREAT_LABELS[lvl])
        head = THREAT_LABELS[lvl]
        detail = text.split(EM_DASH, 1)[1].strip() if EM_DASH in text else (
            "Aucun ennemi menaçant" if lvl == 0 else text)
        self._set_text(self.threat_lbl, head)
        self.threat_lbl.configure(text_color=THREAT_COLORS[lvl])
        self._set_text(self.threat_detail, detail)
        self._gauge_target, self._gauge_color = threat_fraction(lvl), THREAT_COLORS[lvl]

    def _draw_gauge_step(self) -> None:
        diff = self._gauge_target - self._gauge_frac
        if abs(diff) < 0.004 and getattr(self, "_gauge_drawn_color", None) == self._gauge_color:
            return
        self._gauge_frac += diff * 0.55 if abs(diff) > 0.01 else diff
        self._gauge_drawn_color = self._gauge_color
        self._draw_gauge()

    def _update_enemies(self, ov: Any, st: Any) -> None:
        enemies = list(getattr(ov, "enemies", []) or [])[:5] if ov is not None else []
        roles = dict(getattr(ov, "roles", {}) or {}) if ov is not None else {}
        if enemies:     # order the cards like the scoreboard: top, jungle, mid, adc, support
            order = {r: i for i, r in enumerate(ui_kit.ROLE_ORDER)}

            def rank(e: Any) -> int:
                r = ui_kit.norm_role(getattr(e, "role", None) or roles.get(getattr(e, "alias", "") or "")
                                     or roles.get(getattr(e, "key", "") or ""))
                return order.get(r, 9) if r else 9
            if all(rank(e) < 9 for e in enemies):
                enemies.sort(key=rank)
        jl = getattr(ov, "jungler_line", None) if ov is not None else None
        if ov is None:
            jl = "Jungler : en attente d'une partie"
        self._set_text(self.jungler_lbl, jl or "Jungler : inconnu")
        nvis = sum(1 for e in enemies if getattr(e, "visible", False))
        if ov is not None:
            self._set_text(self.visible_lbl, f"{nvis} visible{'s' if nvis > 1 else ''} sur la minimap")
        else:
            self._set_text(self.visible_lbl, "")
        for i, slot in enumerate(self.enemy_slots):
            e = enemies[i] if i < len(enemies) else None
            if e is None:
                sig: tuple = ("empty",)
                if slot["sig"] != sig:
                    slot["icon"].configure(image=self._enemy_image(None, None, "empty"))
                    slot["name"].configure(text="-", text_color=DIM)
                    slot["status"].configure(text=" ", text_color=DIM)
                    slot["box"].configure(border_color=BORDER)
                    slot["border"] = BORDER
                    slot["tip"] = ""
                    slot["sig"] = sig
                continue
            visible = bool(getattr(e, "visible", False))
            appr = bool(getattr(e, "approaching", False))
            jungler = bool(getattr(e, "is_jungler", False))
            ago = getattr(e, "last_seen_ago", None)
            alias = getattr(e, "alias", None)
            role = ui_kit.norm_role(getattr(e, "role", None) or roles.get(alias or "")
                                    or roles.get(getattr(e, "key", "") or ""))
            mode = "approach" if visible and appr else "visible" if visible else "mia"
            name = str(getattr(e, "name", "") or alias or "?")
            mia = None
            if visible:
                status, scol = ("Approche !", WARNING) if appr else ("Visible", SAFE)
            elif isinstance(ago, (int, float)) and math.isfinite(ago):
                status, scol = f"caché {_fmt_ago(ago)}", (WARNING if jungler and ago < 45 else MUTED)
                mia = float(ago)
            else:
                status, scol = "Jamais vu", DIM
            sig = (alias, mode, name, status, jungler, role)
            if slot["sig"] == sig:
                continue
            slot["sig"] = sig
            role_fr = ui_kit.ROLE_FR.get(role or "", "rôle inconnu")
            slot["tip"] = f"{name} · {role_fr}" + (" · jungler ennemi" if jungler and role != "JUNGLE" else "") + \
                f" · {status}"
            slot["icon"].configure(image=self._enemy_image(getattr(e, "icon", None), alias, mode, role, mia, jungler))
            slot["name"].configure(text=_ellipsize(name, 11), text_color=GOLD if jungler else TEXT)
            slot["status"].configure(text=status, text_color=scol)
            border = WARNING if appr else (GOLD_DARK if jungler else BORDER)
            slot["border"] = border
            slot["box"].configure(border_color=border)

    def _update_team(self, ov: Any) -> None:
        """Allies row + lane match-up (me vs the enemy of my role)."""
        allies = list(getattr(ov, "allies", []) or [])[:4] if ov is not None else []
        roles = dict(getattr(ov, "roles", {}) or {}) if ov is not None else {}
        order = {r: i for i, r in enumerate(ui_kit.ROLE_ORDER)}
        allies.sort(key=lambda a: order.get(ui_kit.norm_role(getattr(a, "role", None)
                                                             or roles.get(getattr(a, "alias", "") or "")) or "", 9))
        for i, slot in enumerate(self.ally_slots):
            a = allies[i] if i < len(allies) else None
            if a is None:
                sig: tuple = ("empty",)
                if slot["sig"] != sig:
                    slot["sig"] = sig
                    slot["icon"].configure(image=self._ally_image(None, None, None))
                    slot["name"].configure(text="-", text_color=DIM)
                    slot["tip"] = ""
                continue
            alias = getattr(a, "alias", None)
            role = ui_kit.norm_role(getattr(a, "role", None) or roles.get(alias or ""))
            visible = bool(getattr(a, "visible", False))
            name = str(getattr(a, "name", "") or alias or "?")
            sig = (alias, role, visible, name)
            if slot["sig"] == sig:
                continue
            slot["sig"] = sig
            slot["tip"] = f"{name} · {ui_kit.ROLE_FR.get(role or '', 'rôle inconnu')}" + \
                ("" if visible else " · hors de vue")
            slot["icon"].configure(image=self._ally_image(getattr(a, "icon", None), alias, role))
            slot["name"].configure(text=_ellipsize(name, 9), text_color=TEXT if visible else MUTED)
        me, my_role, opp = ui_kit.lane_opponent(ov) if ov is not None else (None, None, None)
        if ov is None:
            text, col = "En attente", DIM
        elif my_role is None:
            text, col = "Rôle inconnu", DIM
        elif opp is None:
            text, col = f"{ui_kit.ROLE_FR.get(my_role, my_role)} · adversaire inconnu", MUTED
        else:
            ago = getattr(opp, "last_seen_ago", None)
            oname = _ellipsize(str(getattr(opp, "name", "") or getattr(opp, "alias", "") or "?"), 12)
            if getattr(opp, "visible", False):
                text, col = f"{oname} · visible", SAFE
            elif isinstance(ago, (int, float)) and math.isfinite(ago):
                text, col = f"{oname} · caché {_fmt_ago(ago)}", WARNING if ago > 20 else MUTED
            else:
                text, col = f"{oname} · jamais vu", MUTED
        self._set_text(self.matchup_lbl, text)
        try:
            self.matchup_lbl.configure(text_color=col)
        except Exception:
            pass
        sig2 = (me, my_role, getattr(opp, "alias", None),
                getattr(ov, "me_icon", None) is not None if ov is not None else False)
        if sig2 != self._matchup_sig:
            self._matchup_sig = sig2
            self.mu_me.configure(image=self._ally_image(getattr(ov, "me_icon", None) if ov is not None else None,
                                                        me or ("me" if my_role else None), my_role))
            self.mu_opp.configure(image=self._ally_image(getattr(opp, "icon", None) if opp is not None else None,
                                                         getattr(opp, "alias", None), my_role if opp else None,
                                                         ring=ENEMY_RING))

    # ------------------------------------------------------------------ alerts journal
    def _collect_alerts(self, st: Any, ov: Any) -> None:
        gt = getattr(st, "game_time", None) if st is not None else None
        eng = self.engine
        recent = getattr(eng, "recent_alerts", None) if eng is not None else None
        if callable(recent):
            try:
                items = list(recent() or [])[-JOURNAL_MAX:]
                entries = [_alert_entry(a) for a in items]
                entries = [e for e in entries if e is not None and e not in self._journal_hidden]
                sig = tuple(entries)
                if sig != self._journal_sig:
                    self._journal_sig = sig
                    self._journal.clear()
                    self._journal.extend(entries)
                    self._render_journal()
                return
            except Exception:
                log.debug("recent_alerts failed", exc_info=True)
        la = getattr(ov, "last_alert", None) if ov is not None else None
        now = time.monotonic()
        if isinstance(la, (tuple, list)) and len(la) >= 3 and la[0]:
            text, lvl, age = str(la[0]), int(la[1] or 0), float(la[2] or 0.0)
            born = now - age
            prev = self._last_alert_seen
            if prev is None or prev[0] != text or abs(prev[1] - born) > 6.0:
                self._last_alert_seen = (text, born)
                self._journal.append(((gt - age) if isinstance(gt, (int, float)) else None, lvl, text))
                self._render_journal()
            return
        last = getattr(st, "last_alert", None) if st is not None else None
        if last and last != self._last_status_alert:
            self._last_status_alert = str(last)
            self._journal.append((gt, 1, str(last)))
            self._render_journal()

    def _render_journal(self) -> None:
        tb = self.journal
        try:
            empty = not self._journal
            pg = getattr(self, "pregame", None)
            if pg is not None:
                shown = pg.winfo_manager() == "grid"
                if empty and not shown:
                    tb.grid_remove()
                    pg.grid(row=1, column=0, sticky="nsew")
                    self.journal_clear_btn.grid_remove()
                    self._render_pregame()
                elif not empty and shown:
                    pg.grid_remove()
                    tb.grid()
                    self.journal_clear_btn.grid()
                self._journal_caption()
            tb.configure(state="normal")
            tb.delete("1.0", "end")
            if empty:
                tb.insert("end", "Aucune alerte pour l'instant. Les annonces vocales apparaîtront ici.", "empty")
            for gt, lvl, text in reversed(self._journal):
                lvl = min(max(int(lvl), 0), 2)
                tb.insert("end", f"{fmt_clock(gt) if gt is not None else '  -  '}   ", ("time", "line"))
                tb.insert("end", "● ", (f"lvl{lvl}", "line"))
                tb.insert("end", text + "\n", (f"lvl{lvl}", "line"))
            tb.configure(state="disabled")
        except Exception:
            log.debug("Journal render failed", exc_info=True)

    # ------------------------------------------------------------------ "avant la partie" (empty journal)
    def refresh_pregame(self) -> None:
        """Recompute the empty-journal panel on a worker thread: goal, point to work on, game display mode."""
        if self._pregame_busy or self._closing:
            return
        self._pregame_busy = True
        games = list(self._games)

        def job() -> dict[str, Any]:
            out: dict[str, Any] = {"games": games}
            try:
                from treeaicoach import goals  # noqa: PLC0415

                role = next((str(game_field(g, "position") or "") for g in games if game_field(g, "position")), "")
                out["goal"] = goals.pick_goal(games, role) if games else None
            except Exception:
                log.debug("goal unavailable", exc_info=True)
            try:
                from treeaicoach import progress  # noqa: PLC0415

                rows = progress.collect(paths.user_data_dir() / "games", last=20) if len(games) >= 2 else []
                out["focus"] = (progress.focus_points(rows, 1) or [None])[0]
            except Exception:
                log.debug("focus points unavailable", exc_info=True)
            try:
                from treeaicoach import game_settings  # noqa: PLC0415

                gs = game_settings.load_game_settings()
                out["window"] = ui_kit.window_mode_status(getattr(gs, "window_mode", None))
            except Exception:
                out["window"] = ui_kit.window_mode_status(None)
            return out

        def done(data: dict[str, Any]) -> None:
            self._pregame_busy = False
            self._pregame_data = data
            self._render_pregame()

        def failed(_exc: BaseException) -> None:
            self._pregame_busy = False

        self._dispatcher.run(job, done, self.cb(failed), name="TreeAI-ui-pregame")

    def _render_pregame(self) -> None:
        """Fill the "avant la partie" panel (rebuilt only when its data changed)."""
        pg = getattr(self, "pregame", None)
        data = self._pregame_data
        if pg is None or data is None or pg.winfo_manager() != "grid":
            return
        games = data.get("games") or []
        goal = data.get("goal")
        focus = data.get("focus")
        goal_label, goal_why, goal_status = getattr(goal, "label", "") or "", getattr(goal, "why", "") or "", ""
        if self._last_state_key == "RUNNING" and self.engine is not None:   # this game's own goal
            try:
                ex = self.engine.coach_extras() if callable(getattr(self.engine, "coach_extras", None)) else {}
                if isinstance(ex, dict) and ex.get("goal"):
                    goal_label, goal_why = str(ex["goal"]), ""
                    goal_status = str(ex.get("goal_status") or "")
            except Exception:
                log.debug("coach extras unavailable", exc_info=True)
        window = data.get("window") or (-1, "")
        sig = (tuple(str(g.get("path", "")) + str(g.get("precision")) for g in games[:10]),
               goal_label, goal_status, focus, window)
        if sig == self._pregame_sig:
            return
        self._pregame_sig = sig
        for w in pg.winfo_children():
            w.destroy()
        ctk = self.ctk
        if not games:
            self._pregame_checklist(pg, window)
            return
        g = games[0]
        # last game: champion, result, K/D/A, duration | précision | report
        row = ctk.CTkFrame(pg, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew", pady=(2, 8))
        row.grid_columnconfigure(1, weight=1)
        alias = str(game_field(g, "champion", "alias", default="") or "")
        icon = None
        try:
            from treeaicoach.champions import get_default_db  # noqa: PLC0415

            icon = get_default_db().load_icon(alias) if alias else None
        except Exception:
            icon = None
        pil = square_icon(icon, 72, bg=BG)
        img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(36, 36))
        self._images["pregame-last"] = img
        ctk.CTkLabel(row, text="", image=img, fg_color="transparent").grid(row=0, column=0, rowspan=3,
                                                                         padx=(0, 12), sticky="n")
        name = str(game_field(g, "champion_name", "name", default="") or alias or "Champion inconnu")
        res = game_result(g)
        rtxt, rcol = {"win": ("Victoire", SAFE), "lose": ("Défaite", DANGER)}.get(res or "", ("Inachevée", MUTED))
        head = ctk.CTkFrame(row, fg_color="transparent")
        head.grid(row=0, column=1, sticky="w")
        self._label(head, name, self.fonts.h3, TEXT, anchor="w").grid(row=0, column=0, sticky="w")
        self._label(head, rtxt, self.fonts.h3, rcol, anchor="w").grid(row=0, column=1, sticky="w", padx=(10, 0))
        k, d, a = (_int_or_none(game_field(g, x)) for x in ("kills", "deaths", "assists"))
        dur = game_field(g, "duration")
        bits = ["Dernière partie · " + fmt_game_date(game_datetime(g))]
        if k is not None and d is not None and a is not None:
            bits.append(f"{k} / {d} / {a}")
        if isinstance(dur, (int, float)) and dur > 0:
            bits.append(fmt_clock(dur))
        self._label(row, " · ".join(bits), self.fonts.tiny, MUTED, anchor="w").grid(row=1, column=1, sticky="w")
        brief = g.get("plays_brief") if isinstance(g.get("plays_brief"), dict) else {}
        moments = [(brief.get(k), col) for k, col in (("best", SAFE), ("worst", DANGER)) if brief.get(k)]
        if moments:
            mf = ctk.CTkFrame(row, fg_color="transparent")
            mf.grid(row=2, column=1, sticky="w", pady=(2, 0))
            for i, (pl, col) in enumerate(moments):
                gt = pl.get("gt")
                self._label(mf, str(pl.get("title") or "").upper(), self.fonts.tiny_bold, col, anchor="w").grid(
                    row=i, column=0, sticky="w", padx=(0, 6))
                txt = _ellipsize(ui_text(str(pl.get("reason") or "")), 48)
                if isinstance(gt, (int, float)):
                    txt += f" ({fmt_clock(gt)})"
                self._label(mf, txt, self.fonts.tiny, MUTED, anchor="w").grid(row=i, column=1, sticky="w")
        prec = _int_or_none(game_field(g, "precision"))
        pc = ctk.CTkFrame(row, fg_color="transparent")
        pc.grid(row=0, column=2, rowspan=3, sticky="e", padx=(12, 12))
        self._caption(pc, "Précision", DIM, anchor="e").grid(row=0, column=0, sticky="e")
        self._label(pc, "-" if prec is None else str(prec), self.fonts.stat, precision_color(prec),
                    anchor="e").grid(row=1, column=0, sticky="e")
        self._tip(pc, "Précision des coups notés de ta dernière partie (sur 100)." if prec is not None
                  else "Cette partie n'a pas de coups notés.")
        self._button(row, "Rapport", lambda gg=g: self.open_report(gg), "secondary", width=72, height=26).grid(
            row=0, column=3, rowspan=3, sticky="e")
        self._hline(pg).grid(row=1, column=0, sticky="ew")
        # goal of the next game | point to work on
        cols = ctk.CTkFrame(pg, fg_color="transparent")
        cols.grid(row=2, column=0, sticky="ew", pady=(8, 8))
        cols.grid_columnconfigure(0, weight=2, uniform="pg")
        cols.grid_columnconfigure(1, weight=3, uniform="pg")
        gl = ctk.CTkFrame(cols, fg_color="transparent")
        gl.grid(row=0, column=0, sticky="nw", padx=(0, 16))
        self._caption(gl, "Objectif de la partie", DIM, anchor="w").grid(row=0, column=0, sticky="w")
        self._label(gl, goal_label or "-", self.fonts.num,
                    {"raté": DANGER, "réussi": SAFE}.get(goal_status, ACCENT), anchor="w").grid(
            row=1, column=0, sticky="w", pady=(2, 0))
        why = goal_status or goal_why
        if why:
            self._label(gl, why, self.fonts.tiny, DIM, anchor="w").grid(row=2, column=0, sticky="w")
        fl = ctk.CTkFrame(cols, fg_color="transparent")
        fl.grid(row=0, column=1, sticky="nwe")
        fl.grid_columnconfigure(0, weight=1)
        self._caption(fl, "À travailler", DIM, anchor="w").grid(row=0, column=0, sticky="w")
        if focus:
            title, text = focus
            self._label(fl, str(title), self.fonts.h3, TEXT, anchor="w").grid(row=1, column=0, sticky="w",
                                                                             pady=(2, 0))
            ft = self._label(fl, ui_text(text), self.fonts.small, MUTED, anchor="w", justify="left",
                             wraplength=340)
            ft.grid(row=2, column=0, sticky="w")
            fl.bind("<Configure>", lambda e, lbl=ft: lbl.configure(
                wraplength=max(160, int(e.width / max(0.5, self._scaled(100) / 100)) - 8)), add="+")
        else:
            self._label(fl, "Rien d'urgent : continue comme ça." if len(games) >= 2 else
                        "Joue encore une partie pour voir tes points à travailler.", self.fonts.small, MUTED,
                        anchor="w").grid(row=1, column=0, sticky="w", pady=(2, 0))
        self._hline(pg).grid(row=3, column=0, sticky="ew")
        st = session_stats(games)
        parts = [f"{st['games']} partie{'s' if st['games'] > 1 else ''}",
                 f"{st['wins']} victoire{'s' if st['wins'] > 1 else ''}"]
        if st.get("deaths_per_game") is not None:
            parts.append(f"{fmt_decimal_fr(st['deaths_per_game'], 1)} morts par partie")
        if st.get("precision") is not None:
            parts.append(f"précision moyenne {int(round(st['precision']))}")
        line = ctk.CTkFrame(pg, fg_color="transparent")
        line.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        self._caption(line, "Session · " + st["scope"], DIM, anchor="w").grid(row=0, column=0, sticky="w",
                                                                               padx=(0, 10))
        self._label(line, " · ".join(parts), self.fonts.tiny, MUTED, anchor="w").grid(row=0, column=1, sticky="w")
        if window[0] == 2:      # the game is in exclusive fullscreen: say it before the next game
            self._label(pg, "Ton jeu est en plein écran exclusif : passe en Sans bordure (Options > Vidéo).",
                        self.fonts.small, WARNING, anchor="w").grid(row=5, column=0, sticky="w", pady=(6, 0))

    def _journal_caption(self) -> None:
        cap = getattr(self, "journal_cap", None)
        if cap is not None:
            self._set_text(cap, "JOURNAL" if self._journal else
                           ("JOURNAL · AUCUNE ALERTE POUR L'INSTANT" if self._last_state_key == "RUNNING"
                            else "AVANT LA PARTIE"))

    def _pregame_checklist(self, pg: Any, window: tuple[int, str]) -> None:
        """No game recorded yet: 3 checks before the first game."""
        cols = {0: SAFE, 1: WARNING, 2: DANGER, -1: DIM}
        self._label(pg, "Trois vérifications avant ta première partie.", self.fonts.small, MUTED, anchor="w").grid(
            row=0, column=0, sticky="w", pady=(2, 6))
        voice_ok = str(getattr(self.voice, "backend", "") or "").lower() in ("sapi", "onecore", "neural")
        items = (
            ("Jeu en Sans bordure", window[0], window[1], "Aide", lambda: self._run_fix("help_borderless")),
            ("Overlay", 0 if getattr(self.cfg, "overlay_enabled", True) else 1,
             "affiche un exemple de gank 10 s" if getattr(self.cfg, "overlay_enabled", True) else "désactivé",
             "Tester", self.test_overlay),
            ("Voix", 0 if voice_ok else 1, "écoute une alerte d'exemple" if voice_ok else "aucune voix Windows",
             "Écouter" if voice_ok else "Réglages",
             self.test_voice if voice_ok else (lambda: self._run_fix("voice_settings"))),
        )
        for i, (title, level, text, btn, fn) in enumerate(items):
            r = 1 + 2 * i
            if i:
                self._hline(pg).grid(row=r - 1, column=0, sticky="ew")
            row = self.ctk.CTkFrame(pg, fg_color="transparent")
            row.grid(row=r, column=0, sticky="ew", pady=6)
            row.grid_columnconfigure(2, weight=1)
            self._label(row, str(i + 1), self.fonts.num, ACCENT, width=18, anchor="w").grid(row=0, column=0,
                                                                                            padx=(0, 8))
            self._label(row, title, self.fonts.body, TEXT, anchor="w").grid(row=0, column=1, sticky="w",
                                                                            padx=(0, 12))
            self._label(row, ui_text(text), self.fonts.small, cols.get(level, MUTED) if level == 2 else MUTED,
                        anchor="w").grid(row=0, column=2, sticky="w")
            self._button(row, btn, fn, "secondary", width=80, height=26).grid(row=0, column=3, sticky="e")

    # ------------------------------------------------------------------ radar preview & pulse
    def _preview_loop(self) -> None:
        if self._closing:
            return
        try:
            visible = self._current_page == "dashboard" and self.root.state() != "iconic"
            if visible != self._radar_worker.active.is_set():
                (self._radar_worker.active.set if visible else self._radar_worker.active.clear)()
            if visible:
                seq, img = self._radar_worker.latest()
                if seq != self._radar_seq:
                    self._radar_seq = seq
                    live = img is not None
                    if live:
                        if img.size != (self._radar_size, self._radar_size):
                            img = img.resize((self._radar_size, self._radar_size), Image.LANCZOS)
                        self._radar_photo.paste(img)
                    elif self._radar_live:
                        self._radar_photo.paste(self._radar_placeholder)
                    if live != self._radar_live:
                        self._radar_live = live
                        if live:
                            self.radar_msg.place_forget()
                            self.radar_badge.configure(text=" EN DIRECT ", text_color=ON_GOLD, fg_color=TEAL)
                        else:
                            self.radar_msg.place(relx=0.5, rely=0.5, anchor="center")
                            self.radar_badge.configure(text=" HORS LIGNE ", text_color=MUTED, fg_color=PANEL_HI)
        except Exception:
            log.exception("Radar preview update failed")
        self.root.after(PREVIEW_MS, self._preview_loop)

    def _pulse_loop(self) -> None:
        if self._closing:
            return
        try:
            if self.root.state() != "iconic":
                self._pulse_phase = (self._pulse_phase + PULSE_MS / 1000 / 1.6) % 1.0
                color = getattr(self, "_state_color", DIM)
                active = self._last_state_key == "RUNNING"     # pulse = "live", nothing else moves
                k = 0.5 - 0.5 * math.cos(2 * math.pi * self._pulse_phase) if active else 0.0
                if self._current_page == "dashboard":
                    self.hero.pulse(color, k, active)
                    self._draw_gauge_step()
                # sidebar status square: steady colour (motion only on the dashboard's live dot)
                self.pill_dot.itemconfigure(self._pill_dot_item, fill=color)
        except Exception:
            log.debug("Pulse failed", exc_info=True)
        self.root.after(PULSE_MS, self._pulse_loop)

    # ------------------------------------------------------------------ run / close
    def run(self, smoke_seconds: float | None = None) -> int:
        """Main loop; returns 0 once the window is closed."""
        if smoke_seconds is not None:
            try:
                delay = max(0, int(float(smoke_seconds) * 1000))
            except (TypeError, ValueError):
                delay = 0
            self.root.after(delay, self.close)
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self.close()
        except Exception as exc:
            if self._closing:
                log.debug("Tk main loop ended during shutdown: %s", exc)
            else:
                log.exception("Tk main loop failed")
                self.close()
        return 0

    def close(self) -> None:
        """Stop everything, save the configuration and destroy the window. Idempotent."""
        if self._closing:
            return
        self._closing = True
        try:
            geo = self.root.geometry()
            if self.root.state() == "normal":
                self.cfg = dataclasses.replace(self.cfg, ui_geometry=geo).validated()
        except Exception:
            pass
        if self._save_job is not None:
            try:
                self.root.after_cancel(self._save_job)
            except Exception:
                pass
            self._save_job = None
        save_config(self.cfg, self._save_path)
        self._radar_worker.stop()
        self._dispatcher.closed = True
        with self._eng_lock:
            engines = list(self._created_engines)
        if self.engine is not None and self.engine not in engines:
            engines.append(self.engine)
        ov, hk = self.overlay, self._hotkeys
        voice = self.voice if self._own_voice else None

        def shutdown() -> None:
            for e in engines[:-1]:
                self._shutdown_components(e, None, None)
            self._shutdown_components(engines[-1] if engines else None, ov, voice, hk)

        t = threading.Thread(target=shutdown, name="TreeAI-ui-shutdown", daemon=True)
        t.start()
        t.join(8.0)
        if t.is_alive():
            log.warning("Shutdown did not finish in 8 s; closing the window anyway")
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass
        self._closed.set()

    @staticmethod
    def _shutdown_components(engine: Any, overlay: Any, voice: Any, hotkeys: Any = None) -> None:
        for name, obj, call in (("hotkeys", hotkeys, "stop"), ("overlay", overlay, "stop"),
                                ("engine", engine, "stop"), ("voice", voice, "stop")):
            if obj is None:
                continue
            try:
                getattr(obj, call)()
            except Exception:
                log.exception("Cannot stop %s", name)


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
    """``list_games`` / ``write_report`` from report.py (or analysis.py), None if unavailable."""
    for mod in ("treeaicoach.report", "treeaicoach.analysis"):
        try:
            import importlib  # noqa: PLC0415

            m = importlib.import_module(mod)
        except Exception:
            continue
        fn = getattr(m, name, None)
        if callable(fn):
            return fn
    log.info("%s() is not available (report module missing)", name)
    return None


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


def _prewarm_preview() -> None:
    """Build the Overlay page preview once in the background (sample state, backdrop) so the page opens fast."""
    try:
        from treeaicoach import ui_preview  # noqa: PLC0415

        ui_preview.compose(Config())
    except Exception:
        log.debug("overlay preview prewarm failed", exc_info=True)


def _default_engine_factory(cfg: Config, voice: Any, detector: Any, frame_source: Any) -> Any:
    from treeaicoach.engine import CoachEngine  # noqa: PLC0415 - written in parallel, imported lazily

    try:   # the UI owns the overlay windows and the global hotkeys (they survive engine rebuilds)
        return CoachEngine(cfg, voice, detector=detector, frame_source=frame_source,
                           manage_overlay=False, enable_hotkeys=False)
    except TypeError:
        return CoachEngine(cfg, voice, detector=detector, frame_source=frame_source)


def _default_overlay_factory(cfg: Config, provider: Callable[[], Any]) -> Any:
    try:
        from treeaicoach.overlay import OverlayManager  # noqa: PLC0415
    except Exception:
        log.info("Overlay module unavailable: overlay disabled", exc_info=log.isEnabledFor(logging.DEBUG))
        return None
    return OverlayManager(cfg, provider)


def _default_detector_factory(cfg: Config) -> Any:
    from treeaicoach.detector import create_detector  # noqa: PLC0415

    return create_detector(cfg.detector_backend, cfg.detection_threshold)


def _default_demo_source() -> Any:
    from treeaicoach.demo import DemoSource  # noqa: PLC0415

    return DemoSource()


# ======================================================================================
# Entry point
# ======================================================================================
def run_app(cfg: Config, *, demo: bool = False, smoke_seconds: float | None = None,
            _engine_factory: EngineFactory | None = None,
            _overlay_factory: Callable[[Config, Callable[[], Any]], Any] | None = None,
            _voice: Any = None, _detector_factory: Callable[[Config], Any] | None = None,
            _demo_source_factory: Callable[[], Any] | None = None, _hotkeys: bool = True,
            _save_path: Path | None = None) -> int:
    """Open the main window (blocking) and return 0 when it is closed.

    ``demo`` starts the engine on the simulated game; ``smoke_seconds`` closes the window
    automatically after that delay (CI). The underscore parameters inject stand-ins (tests).
    Returns 1 only if the window itself cannot be created (no display...).
    """
    try:
        app = CoachApp(cfg, demo=demo, engine_factory=_engine_factory, overlay_factory=_overlay_factory,
                       voice=_voice, detector_factory=_detector_factory,
                       demo_source_factory=_demo_source_factory, hotkeys=_hotkeys, save_path=_save_path)
    except Exception:
        log.exception("Cannot create the main window")
        try:
            save_config(cfg if isinstance(cfg, Config) else Config(), _save_path)
        except Exception:
            pass
        return 1
    return app.run(smoke_seconds)


__all__ = ["run_app", "CoachApp", "fmt_clock", "fmt_int_fr", "state_key", "session_stats",
           "autostart_support", "get_windows_autostart", "set_windows_autostart", "app_icon_path"]
