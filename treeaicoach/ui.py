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

from treeaicoach import APP_NAME, __version__, paths
from treeaicoach.config import Config, save_config

log = logging.getLogger(__name__)

# ======================================================================================
# Palette & layout constants
# ======================================================================================
BG = "#010A13"              # window background
PANEL = "#0A1428"           # cards / panels
PANEL_HI = "#0F1D36"        # raised elements, hover
PANEL_LO = "#06101F"        # sunken areas (sidebar, inputs)
BORDER = "#1E2328"          # neutral borders
BORDER_GOLD = "#3C3222"     # subtle gold borders
GOLD = "#C8AA6E"            # accent
GOLD_HOVER = "#DCC28E"
GOLD_DARK = "#785A28"
TEXT = "#F0E6D2"            # light gold text
MUTED = "#A09B8C"
DIM = "#5B5A56"
TEAL = "#0AC8B9"            # active
TEAL_DARK = "#0A5E63"
DANGER = "#E84057"
DANGER_DARK = "#4A1520"
WARNING = "#F0A030"
SAFE = "#2DC66B"
ON_GOLD = "#1A1408"         # text on gold buttons

THREAT_COLORS = {0: SAFE, 1: WARNING, 2: DANGER}
THREAT_LABELS = {0: "SÛR", 1: "ATTENTION", 2: "DANGER"}
LEVEL_COLORS = {0: TEXT, 1: WARNING, 2: DANGER}

SIDEBAR_W = 226
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
    ("dashboard", "Tableau de bord", "dashboard"),
    ("alerts", "Alertes & voix", "voice"),
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
VOICE_FIELDS = frozenset({"voice_name", "voice_rate", "voice_volume", "beep_on_danger"})
HOTKEY_FIELDS = frozenset({"hotkey_jungler", "hotkey_mute", "hotkey_overlay"})

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
    return {
        "scope": scope,
        "games": n,
        "wins": wins,
        "winrate": (wins / n) if n else None,
        "deaths_per_game": (sum(deaths) / len(deaths)) if deaths else None,
        "ganks": total_ganks,
        "ganks_avoided": avoided,
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
        try:
            return method(self, *args, **kwargs)
        except Exception as exc:
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
    """Point every CustomTkinter default colour at the hextech palette (dark mode)."""
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
    put("CTkFrame", fg_color=PANEL, top_fg_color=PANEL_HI, border_color=BORDER, corner_radius=12)
    put("CTkButton", fg_color=PANEL_HI, hover_color="#16284A", border_color=BORDER_GOLD,
        text_color=TEXT, text_color_disabled=DIM, corner_radius=8)
    put("CTkLabel", text_color=TEXT)
    put("CTkEntry", fg_color=PANEL_LO, border_color=BORDER_GOLD, text_color=TEXT,
        placeholder_text_color=DIM)
    put("CTkCheckBox", fg_color=GOLD, border_color=GOLD_DARK, hover_color=GOLD_HOVER, checkmark_color=ON_GOLD,
        text_color=TEXT, text_color_disabled=DIM)
    put("CTkSwitch", fg_color="#1B2638", progress_color=TEAL, button_color=TEXT, button_hover_color="#FFFFFF",
        text_color=TEXT, text_color_disabled=DIM)
    put("CTkRadioButton", fg_color=GOLD, border_color=GOLD_DARK, hover_color=GOLD_HOVER, text_color=TEXT)
    put("CTkProgressBar", fg_color="#1B2638", progress_color=TEAL, border_color=BORDER)
    put("CTkSlider", fg_color="#1B2638", progress_color=GOLD_DARK, button_color=GOLD,
        button_hover_color=GOLD_HOVER)
    put("CTkOptionMenu", fg_color=PANEL_HI, button_color="#16284A", button_hover_color="#1D3560",
        text_color=TEXT, text_color_disabled=DIM)
    put("CTkComboBox", fg_color=PANEL_HI, border_color=BORDER_GOLD, button_color="#16284A",
        button_hover_color="#1D3560", text_color=TEXT)
    put("CTkScrollbar", fg_color="transparent", button_color="#1B2638", button_hover_color=GOLD_DARK)
    put("CTkSegmentedButton", fg_color=PANEL_LO, selected_color=GOLD_DARK, selected_hover_color="#8C6A32",
        unselected_color=PANEL_LO, unselected_hover_color=PANEL_HI, text_color=TEXT,
        text_color_disabled=DIM)
    put("CTkTextbox", fg_color=PANEL, border_color=BORDER, text_color=TEXT, scrollbar_button_color="#1B2638",
        scrollbar_button_hover_color=GOLD_DARK)
    put("CTkScrollableFrame", label_fg_color=PANEL)
    put("DropdownMenu", fg_color=PANEL_HI, hover_color="#1D3560", text_color=TEXT)


def _pick_family(root: Any) -> str:
    """Segoe UI on Windows, else the best available sans-serif font."""
    try:
        import tkinter.font as tkfont  # noqa: PLC0415

        fams = set(tkfont.families(root))
    except Exception:
        fams = set()
    for fam in ("Segoe UI", "Segoe UI Variable Text", "Inter", "Noto Sans", "DejaVu Sans",
                "Liberation Sans", "Helvetica", "Arial"):
        if fam in fams:
            return fam
    return "TkDefaultFont"


class _Fonts:
    """The app's CTkFont set (created once the root exists)."""

    def __init__(self, ctk: Any, family: str) -> None:
        f = family
        self.family = f
        self.brand = ctk.CTkFont(family=f, size=17, weight="bold")
        self.title = ctk.CTkFont(family=f, size=22, weight="bold")
        self.h2 = ctk.CTkFont(family=f, size=15, weight="bold")
        self.h3 = ctk.CTkFont(family=f, size=13, weight="bold")
        self.body = ctk.CTkFont(family=f, size=13)
        self.small = ctk.CTkFont(family=f, size=12)
        self.tiny = ctk.CTkFont(family=f, size=11)
        self.tiny_bold = ctk.CTkFont(family=f, size=11, weight="bold")
        self.caps = ctk.CTkFont(family=f, size=10, weight="bold")
        self.nav = ctk.CTkFont(family=f, size=14)
        self.nav_active = ctk.CTkFont(family=f, size=14, weight="bold")
        self.button = ctk.CTkFont(family=f, size=13, weight="bold")
        self.big_button = ctk.CTkFont(family=f, size=15, weight="bold")
        self.state = ctk.CTkFont(family=f, size=18, weight="bold")
        self.clock = ctk.CTkFont(family=f, size=26, weight="bold")
        self.stat = ctk.CTkFont(family=f, size=24, weight="bold")
        self.threat = ctk.CTkFont(family=f, size=17, weight="bold")


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
        self._last_alert_seen: tuple[str, float] | None = None
        self._last_status_alert: str | None = None
        self._last_state_key = ""
        self._games: list[dict] = []
        self._voices: list[str] = []
        self._current_page = ""
        self._pulse_phase = 0.0
        self._enemy_cache: dict[tuple, Any] = {}
        self._images: dict[str, Any] = {}     # keep CTkImage references alive
        self._widgets_by_field: dict[str, Callable[[], None]] = {}   # field -> refresh function
        self._dispatcher = _Dispatcher()
        self._eng_lock = threading.Lock()
        self._created_engines: list[Any] = []   # every engine built (stopped again at close)
        self._radar_worker = _RadarWorker(self._radar_source, RADAR_PX)
        self._radar_seq = -1
        self._radar_live = False

        _apply_theme(ctk)
        self.root = ctk.CTk()
        # NB: never withdraw() the CTk root before mainloop: on Windows CTk re-applies the
        # saved "withdrawn" state after colouring the title bar and the window never shows.
        self.root.title(APP_NAME)
        self.root.report_callback_exception = self._tk_exception
        self.fonts = _Fonts(ctk, _pick_family(self.root))
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
        self.show_page("dashboard")
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
            compact = width < 1080
            if compact != self._compact:
                self._compact = compact
                self.btn_test_voice.configure(text="Voix" if compact else "Tester la voix")
                self.btn_calib.configure(text="Calibrer" if compact else "Calibrer la minimap")
                self._demo_button_text()
            st_w = self.status_card.winfo_width() / scale
            if st_w > 50:
                self.state_msg.configure(wraplength=int(max(180, st_w - 480)))
            en_w = self.enemies_card.winfo_width() / scale
            if en_w > 50:
                self.jungler_lbl.configure(wraplength=int(max(160, en_w - 44)))
        except Exception:
            log.debug("Layout update failed", exc_info=True)

    def _demo_button_text(self) -> None:
        if self.demo:
            text = "Quitter" if self._compact else "Quitter la démo"
        else:
            text = "Démo" if self._compact else "Mode démo"
        self._set_text(self.btn_demo, text)

    # ------------------------------------------------------------------ small widget factories
    def _card(self, parent: Any, **kw: Any) -> Any:
        opts = dict(fg_color=PANEL, corner_radius=14, border_width=1, border_color=BORDER)
        opts.update(kw)
        return self.ctk.CTkFrame(parent, **opts)

    def _label(self, parent: Any, text: str = "", font: Any = None, color: str = TEXT, **kw: Any) -> Any:
        kw.setdefault("height", 1)      # size to the text (CTkLabel's default minimum is 28 px)
        return self.ctk.CTkLabel(parent, text=text, font=font or self.fonts.body, text_color=color,
                                 fg_color="transparent", **kw)

    def _icon(self, kind: str, size: int = 18, color: str = MUTED) -> Any:
        key = f"{kind}-{size}-{color}"
        img = self._images.get(key)
        if img is None:
            pil = nav_icon(kind, size, color)
            img = self.ctk.CTkImage(light_image=pil, dark_image=pil, size=(size, size))
            self._images[key] = img
        return img

    def _button(self, parent: Any, text: str, command: Callable[[], Any], kind: str = "secondary",
                icon: str | None = None, **kw: Any) -> Any:
        styles = {
            "primary": dict(fg_color=GOLD, hover_color=GOLD_HOVER, text_color=ON_GOLD, border_width=0),
            "secondary": dict(fg_color=PANEL_HI, hover_color="#16284A", text_color=TEXT, border_width=1,
                              border_color=BORDER_GOLD),
            "ghost": dict(fg_color="transparent", hover_color=PANEL_HI, text_color=GOLD, border_width=1,
                          border_color=GOLD_DARK),
            "danger": dict(fg_color=DANGER_DARK, hover_color="#6A1D2C", text_color="#FFD9DF", border_width=1,
                           border_color=DANGER),
        }
        opts: dict[str, Any] = dict(height=34, corner_radius=8, font=self.fonts.button,
                                    text_color_disabled=DIM)
        opts.update(styles.get(kind, styles["secondary"]))
        if icon:
            col = ON_GOLD if kind == "primary" else (GOLD if kind == "ghost" else MUTED)
            opts["image"] = self._icon(icon, 16, col)
            opts["compound"] = "left"
        opts.update(kw)
        return self.ctk.CTkButton(parent, text=text, command=self.cb(command), **opts)

    def _page(self, title: str, subtitle: str, scroll: bool = True) -> tuple[Any, Any, Any]:
        """(page frame, header right slot, body frame)."""
        ctk = self.ctk
        page = ctk.CTkFrame(self.content, fg_color=BG, corner_radius=0)
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(page, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=28, pady=(22, 12))
        head.grid_columnconfigure(0, weight=1)
        self._label(head, title, self.fonts.title, TEXT, anchor="w").grid(row=0, column=0, sticky="w")
        sub = self._label(head, subtitle, self.fonts.small, MUTED, anchor="w")
        sub.grid(row=1, column=0, sticky="w", pady=(3, 0))
        page.subtitle = sub  # type: ignore[attr-defined]
        right = ctk.CTkFrame(head, fg_color="transparent", width=1, height=1)
        right.grid(row=0, column=1, rowspan=2, sticky="e")
        if scroll:
            body = ctk.CTkScrollableFrame(page, fg_color=BG, corner_radius=0,
                                          scrollbar_button_color="#1B2638",
                                          scrollbar_button_hover_color=GOLD_DARK)
            body.grid(row=1, column=0, sticky="nsew", padx=(16, 6), pady=(0, 12))
            body.grid_columnconfigure(0, weight=1)
            page.scroll_frame = body  # type: ignore[attr-defined]
            inner = ctk.CTkFrame(body, fg_color="transparent")
            inner.grid(row=0, column=0, sticky="nsew", padx=(12, 14))
            inner.grid_columnconfigure(0, weight=1)
            return page, right, inner
        body = ctk.CTkFrame(page, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=28, pady=(0, 22))
        return page, right, body

    def _section(self, parent: Any, row: int, title: str, subtitle: str | None = None) -> Any:
        """A titled card; returns its content frame (1 column, rows added by the caller)."""
        card = self._card(parent)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)
        self._label(card, title, self.fonts.h2, GOLD, anchor="w").grid(
            row=0, column=0, sticky="w", padx=20, pady=(18, 0 if subtitle else 4))
        if subtitle:
            self._label(card, subtitle, self.fonts.tiny, MUTED, anchor="w", justify="left", wraplength=640).grid(
                row=1, column=0, sticky="w", padx=20, pady=(4, 4))
        body = self.ctk.CTkFrame(card, fg_color="transparent")
        body.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 12))
        body.grid_columnconfigure(0, weight=1)
        body._rows = 0  # type: ignore[attr-defined]
        return body

    def _row(self, body: Any, title: str, desc: str | None = None) -> tuple[Any, Any]:
        """A setting row (title + description on the left, control slot on the right)."""
        ctk = self.ctk
        r = body._rows
        if r:
            ctk.CTkFrame(body, height=1, fg_color=BORDER, corner_radius=0).grid(
                row=2 * r - 1, column=0, sticky="ew", pady=0)
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.grid(row=2 * r, column=0, sticky="ew", pady=12)
        row.grid_columnconfigure(0, weight=1)
        body._rows = r + 1
        left = ctk.CTkFrame(row, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w")
        self._label(left, title, self.fonts.h3, TEXT, anchor="w").grid(row=0, column=0, sticky="w")
        desc_lbl = None
        if desc:
            desc_lbl = self._label(left, desc, self.fonts.tiny, MUTED, anchor="w", justify="left",
                                   wraplength=430)
            desc_lbl.grid(row=1, column=0, sticky="w", pady=(3, 0))
        slot = ctk.CTkFrame(row, fg_color="transparent")
        slot.grid(row=0, column=1, sticky="e", padx=(16, 0))
        slot.desc_label = desc_lbl  # type: ignore[attr-defined]
        self._last_slot = slot
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
                                switch_width=42, switch_height=22, fg_color="#1B2638", progress_color=TEAL,
                                button_color=TEXT, button_hover_color="#FFFFFF")
        sw.grid(row=0, column=0)
        self._widgets_by_field[field] = lambda: var.set(bool(getattr(self.cfg, field)))
        return sw

    def _slider_row(self, body: Any, field: str, title: str, desc: str | None, lo: float, hi: float,
                    step: float, fmt: Callable[[float], str], cast: Callable[[float], Any] = float,
                    on_change: Callable[[Any], None] | None = None) -> Any:
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
                                command=self.cb(moved), fg_color="#1B2638", progress_color=GOLD_DARK,
                                button_color=GOLD, button_hover_color=GOLD_HOVER)
        sl.set(float(getattr(self.cfg, field)))
        sl.grid(row=0, column=0, padx=(0, 8))
        value_lbl.grid(row=0, column=1)

        def refresh() -> None:
            sl.set(float(getattr(self.cfg, field)))
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
            w = self.ctk.CTkSegmentedButton(slot, values=labels, command=self.cb(changed), height=32,
                                            font=self.fonts.small, fg_color=PANEL_LO,
                                            selected_color=GOLD_DARK, selected_hover_color="#8C6A32",
                                            unselected_color=PANEL_LO, unselected_hover_color=PANEL_HI,
                                            text_color=TEXT, corner_radius=8)
        else:
            w = self.ctk.CTkOptionMenu(slot, values=labels, command=self.cb(changed), width=width, height=32,
                                       font=self.fonts.small, dropdown_font=self.fonts.small,
                                       fg_color=PANEL_HI, button_color="#16284A", button_hover_color="#1D3560",
                                       text_color=TEXT, dropdown_fg_color=PANEL_HI,
                                       dropdown_hover_color="#1D3560", dropdown_text_color=TEXT,
                                       corner_radius=8, dynamic_resizing=False)
        w.grid(row=0, column=0)

        def refresh() -> None:
            w.set(to_label.get(getattr(self.cfg, field), labels[0]))
        refresh()
        self._widgets_by_field[field] = refresh
        return w

    # ------------------------------------------------------------------ sidebar
    def _build_sidebar(self) -> None:
        ctk = self.ctk
        sb = ctk.CTkFrame(self.root, width=SIDEBAR_W, fg_color=PANEL_LO, corner_radius=0)
        sb.grid(row=0, column=0, sticky="nsw")
        sb.grid_propagate(False)
        sb.grid_columnconfigure(0, weight=1)
        sb.grid_rowconfigure(2, weight=1)
        # right edge line (gold, subtle)
        ctk.CTkFrame(self.root, width=1, fg_color=BORDER_GOLD, corner_radius=0).grid(
            row=0, column=0, sticky="nse")

        brand = ctk.CTkFrame(sb, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=18, pady=(22, 18))
        logo = load_logo(84)
        self._images["logo"] = ctk.CTkImage(light_image=logo, dark_image=logo, size=(42, 42))
        ctk.CTkLabel(brand, text="", image=self._images["logo"], fg_color="transparent").grid(
            row=0, column=0, rowspan=2, padx=(0, 10))
        self._label(brand, "TreeAI Coach", self.fonts.brand, GOLD, anchor="w").grid(
            row=0, column=1, sticky="sw")
        self._label(brand, "Coach anti-gank", self.fonts.tiny, MUTED, anchor="w").grid(
            row=1, column=1, sticky="nw", pady=(2, 0))

        nav = ctk.CTkFrame(sb, fg_color="transparent")
        nav.grid(row=1, column=0, sticky="new", padx=10)
        nav.grid_columnconfigure(1, weight=1)
        self._label(nav, "NAVIGATION", self.fonts.caps, DIM, anchor="w").grid(
            row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 6))
        self._nav: dict[str, tuple[Any, Any, str]] = {}
        for i, (key, label, icon) in enumerate(PAGES, start=1):
            ind = ctk.CTkFrame(nav, width=3, height=26, fg_color="transparent", corner_radius=2)
            ind.grid(row=i, column=0, sticky="w", padx=(0, 4))
            btn = ctk.CTkButton(nav, text="  " + label, anchor="w", height=40, corner_radius=8,
                                fg_color="transparent", hover_color=PANEL_HI, text_color=MUTED,
                                font=self.fonts.nav, image=self._icon(icon, 18, MUTED), compound="left",
                                command=self.cb(lambda k=key: self.show_page(k)))
            btn.grid(row=i, column=1, sticky="ew", pady=2)
            self._nav[key] = (btn, ind, icon)

        foot = ctk.CTkFrame(sb, fg_color="transparent")
        foot.grid(row=3, column=0, sticky="sew", padx=14, pady=(8, 18))
        foot.grid_columnconfigure(0, weight=1)
        pill = ctk.CTkFrame(foot, fg_color=PANEL, corner_radius=18, border_width=1, border_color=BORDER, height=36)
        pill.grid(row=0, column=0, sticky="ew")
        pill.grid_columnconfigure(1, weight=1)
        self.pill_dot = ctk.CTkCanvas(pill, width=12, height=12, bg=PANEL, highlightthickness=0, bd=0)
        self.pill_dot.grid(row=0, column=0, padx=(14, 8), pady=10)
        self._pill_dot_item = self.pill_dot.create_oval(1, 1, 11, 11, fill=DIM, outline="")
        self.pill_text = self._label(pill, "Démarrage…", self.fonts.small, TEXT, anchor="w")
        self.pill_text.grid(row=0, column=1, sticky="w", padx=(0, 12))
        self._label(foot, f"Version {__version__}", self.fonts.tiny, DIM, anchor="w").grid(
            row=1, column=0, sticky="w", padx=6, pady=(10, 0))

    def show_page(self, key: str) -> None:
        """Switch the visible page."""
        if key not in self.pages or key == self._current_page:
            return
        for k, page in self.pages.items():
            if k != key:
                page.grid_remove()
        self.pages[key].grid()
        self._current_page = key
        for k, (btn, ind, icon) in self._nav.items():
            active = k == key
            btn.configure(fg_color=PANEL_HI if active else "transparent",
                          text_color=TEXT if active else MUTED,
                          font=self.fonts.nav_active if active else self.fonts.nav,
                          image=self._icon(icon, 18, GOLD if active else MUTED))
            ind.configure(fg_color=GOLD if active else "transparent")
        if key == "dashboard":
            self._radar_worker.active.set()
        else:
            self._radar_worker.active.clear()
        if key == "analysis":
            self.refresh_games()
        if key == "overlay":
            self._schedule_overlay_preview()

    # ------------------------------------------------------------------ dashboard
    def _build_dashboard(self) -> Any:
        ctk = self.ctk
        page, right, body = self._page("Tableau de bord", "Suivi en direct de la minimap et des alertes",
                                       scroll=False)
        self.btn_test_voice = self._button(right, "Tester la voix", self.test_voice, "secondary", icon="voice", width=0)
        self.btn_test_voice.grid(row=0, column=0, padx=(0, 8))
        self.btn_demo = self._button(right, "Mode démo", self.toggle_demo, "secondary", icon="demo", width=0)
        self.btn_demo.grid(row=0, column=1, padx=(0, 8))
        self.btn_calib = self._button(right, "Calibrer la minimap", self.calibrate, "secondary", icon="target", width=0)
        self.btn_calib.grid(row=0, column=2)

        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0)
        body.grid_rowconfigure(2, weight=1)

        # --- banner (break reminder...) -------------------------------------------------
        self.banner = ctk.CTkFrame(body, fg_color="#2A1F0C", corner_radius=10, border_width=1,
                                   border_color=WARNING)
        self.banner.grid_columnconfigure(1, weight=1)
        ctk.CTkFrame(self.banner, width=4, height=20, corner_radius=2, fg_color=WARNING).grid(
            row=0, column=0, padx=(14, 10), pady=10)
        self.banner_lbl = self._label(self.banner, "", self.fonts.small, TEXT, anchor="w", justify="left")
        self.banner_lbl.grid(row=0, column=1, sticky="w", pady=10)
        self._button(self.banner, "Compris", self._dismiss_banner, "ghost", width=90, height=28).grid(
            row=0, column=2, padx=10)
        self._banner_dismissed: str | None = None

        # --- status card -----------------------------------------------------------
        st = self._card(body)
        self.status_card = st
        st.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        st.grid_columnconfigure(1, weight=1)
        self.dot_canvas = ctk.CTkCanvas(st, width=34, height=34, bg=PANEL, highlightthickness=0, bd=0)
        self.dot_canvas.grid(row=0, column=0, rowspan=2, padx=(20, 12), pady=18)
        self._dot_halo = self.dot_canvas.create_oval(2, 2, 32, 32, fill=PANEL, outline="")
        self._dot_core = self.dot_canvas.create_oval(11, 11, 23, 23, fill=DIM, outline="")
        tl = ctk.CTkFrame(st, fg_color="transparent")
        tl.grid(row=0, column=1, rowspan=2, sticky="w")
        title_row = ctk.CTkFrame(tl, fg_color="transparent")
        title_row.grid(row=0, column=0, sticky="w")
        self.state_title = self._label(title_row, "Démarrage…", self.fonts.state, TEXT, anchor="w")
        self.state_title.grid(row=0, column=0, sticky="w")
        self.demo_badge = ctk.CTkLabel(title_row, text=" DÉMO ", font=self.fonts.caps, text_color=ON_GOLD,
                                       fg_color=GOLD, corner_radius=6, height=20)
        self.state_msg = self._label(tl, "Initialisation du moteur d'analyse…", self.fonts.small, MUTED,
                                     anchor="w", justify="left", wraplength=360)
        self.state_msg.grid(row=1, column=0, sticky="w", pady=(4, 0))
        clock = ctk.CTkFrame(st, fg_color="transparent")
        clock.grid(row=0, column=2, rowspan=2, padx=(12, 22))
        self._label(clock, "TEMPS DE JEU", self.fonts.caps, DIM).grid(row=0, column=0)
        self.clock_lbl = self._label(clock, "--:--", self.fonts.clock, TEXT)
        self.clock_lbl.grid(row=1, column=0)
        self.btn_start = ctk.CTkButton(st, text="Démarrer l'analyse", width=210, height=48, corner_radius=10,
                                       font=self.fonts.big_button, fg_color=GOLD, hover_color=GOLD_HOVER,
                                       text_color=ON_GOLD, text_color_disabled="#4A4232",
                                       image=self._icon("play", 16, ON_GOLD), compound="left",
                                       command=self.cb(self.toggle_engine))
        self.btn_start.grid(row=0, column=3, rowspan=2, padx=(0, 20), pady=18)

        # --- left column -------------------------------------------------------------
        left = ctk.CTkFrame(body, fg_color="transparent")
        left.grid(row=2, column=0, sticky="nsew", padx=(0, 14))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(2, weight=1)

        th = self._card(left)
        th.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        th.grid_columnconfigure(1, weight=1)
        th.grid_columnconfigure(0, minsize=self._scaled(150))
        self._label(th, "MENACE", self.fonts.caps, DIM, anchor="w").grid(row=0, column=0, sticky="w",
                                                                           padx=(20, 0), pady=(14, 0))
        self.threat_lbl = self._label(th, "—", self.fonts.threat, DIM, anchor="w")
        self.threat_lbl.grid(row=1, column=0, sticky="w", padx=(20, 14), pady=(3, 16))
        gauge_box = ctk.CTkFrame(th, fg_color="transparent")
        gauge_box.grid(row=0, column=1, rowspan=2, sticky="ew", padx=(0, 20), pady=12)
        gauge_box.grid_columnconfigure(0, weight=1)
        self.threat_detail = self._label(gauge_box, "Hors partie", self.fonts.small, MUTED, anchor="w")
        self.threat_detail.grid(row=0, column=0, sticky="w")
        self.gauge = ctk.CTkCanvas(gauge_box, height=14, bg=PANEL, highlightthickness=0, bd=0)
        self.gauge.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self._gauge_frac = 0.0
        self._gauge_target = 0.0
        self._gauge_color = DIM
        self.gauge.bind("<Configure>", lambda _e: self._draw_gauge())

        en = self._card(left)
        self.enemies_card = en
        en.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        en.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(en, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=20, pady=(14, 4))
        head.grid_columnconfigure(1, weight=1)
        self._label(head, "Ennemis", self.fonts.h2, GOLD, anchor="w").grid(row=0, column=0, sticky="w")
        self.visible_lbl = self._label(head, "", self.fonts.tiny, MUTED, anchor="e")
        self.visible_lbl.grid(row=0, column=1, sticky="e")
        self.jungler_lbl = self._label(en, "Jungler : en attente d'une partie", self.fonts.small, TEXT,
                                       anchor="w", justify="left", wraplength=480)
        self.jungler_lbl.grid(row=1, column=0, sticky="ew", padx=20)
        slots = ctk.CTkFrame(en, fg_color="transparent")
        slots.grid(row=2, column=0, sticky="ew", padx=14, pady=(8, 14))
        self.enemy_slots: list[dict[str, Any]] = []
        for i in range(5):
            slots.grid_columnconfigure(i, weight=1, uniform="enemy")
            box = ctk.CTkFrame(slots, fg_color=PANEL_LO, corner_radius=10, border_width=1, border_color=BORDER)
            box.grid(row=0, column=i, sticky="ew", padx=4)
            box.grid_columnconfigure(0, weight=1)
            img = self._enemy_image(None, None, "empty")
            icon = ctk.CTkLabel(box, text="", image=img, fg_color="transparent")
            icon.grid(row=0, column=0, pady=(10, 2))
            name = self._label(box, "—", self.fonts.tiny_bold, MUTED)
            name.grid(row=1, column=0, padx=4, pady=(4, 0))
            status = self._label(box, " ", self.fonts.tiny, DIM)
            status.grid(row=2, column=0, pady=(2, 10), padx=4)
            self.enemy_slots.append({"box": box, "icon": icon, "name": name, "status": status, "sig": None})

        jr = self._card(left)
        jr.grid(row=2, column=0, sticky="nsew")
        jr.grid_columnconfigure(0, weight=1)
        jr.grid_rowconfigure(1, weight=1)
        self._label(jr, "Journal des alertes", self.fonts.h2, GOLD, anchor="w").grid(
            row=0, column=0, sticky="w", padx=20, pady=(14, 4))
        self.journal = ctk.CTkTextbox(jr, fg_color=PANEL, text_color=TEXT, font=self.fonts.small,
                                      wrap="word", activate_scrollbars=True, border_width=0,
                                      scrollbar_button_color="#1B2638",
                                      scrollbar_button_hover_color=GOLD_DARK, height=80)
        self.journal.grid(row=1, column=0, sticky="nsew", padx=(14, 8), pady=(0, 12))
        for lvl, col in LEVEL_COLORS.items():
            self.journal.tag_config(f"lvl{lvl}", foreground=col)
        self.journal.tag_config("time", foreground=DIM)
        self.journal.tag_config("line", spacing1=3, spacing3=3)
        self.journal.tag_config("empty", foreground=DIM)
        self._render_journal()

        # --- right column (radar + tech) ----------------------------------------------
        rc = self._card(body, width=RADAR_PX + 40)
        rc.grid(row=2, column=1, sticky="n")
        rc.grid_columnconfigure(0, weight=1)
        rh = ctk.CTkFrame(rc, fg_color="transparent")
        rh.grid(row=0, column=0, sticky="ew", padx=20, pady=(14, 8))
        rh.grid_columnconfigure(0, weight=1)
        self._label(rh, "Radar en direct", self.fonts.h2, GOLD, anchor="w").grid(row=0, column=0, sticky="w")
        self.radar_badge = ctk.CTkLabel(rh, text=" HORS LIGNE ", font=self.fonts.caps, text_color=MUTED,
                                        fg_color=PANEL_HI, corner_radius=6, height=20)
        self.radar_badge.grid(row=0, column=1, sticky="e")
        import tkinter as tk  # noqa: PLC0415
        from PIL import ImageTk  # noqa: PLC0415

        self._radar_size = self._scaled(RADAR_PX)
        self._radar_placeholder = radar_placeholder(self._radar_size)
        self._radar_photo = ImageTk.PhotoImage(self._radar_placeholder, master=self.root)
        holder = tk.Frame(rc, bg=PANEL, width=self._radar_size, height=self._radar_size)
        holder.grid(row=1, column=0, padx=20)
        holder.grid_propagate(False)
        self.radar_lbl = tk.Label(holder, image=self._radar_photo, bg=PANEL, bd=0, highlightthickness=0)
        self.radar_lbl.place(x=0, y=0, relwidth=1, relheight=1)
        self.radar_msg = tk.Label(holder, text="En attente d'une partie…", bg=PANEL_LO, fg=MUTED,
                                  font=(self.fonts.family, self._font_px(12)), padx=12, pady=6)
        self.radar_msg.place(relx=0.5, rely=0.5, anchor="center")
        tech = ctk.CTkFrame(rc, fg_color="transparent")
        tech.grid(row=2, column=0, sticky="ew", padx=16, pady=(12, 16))
        self.tech: dict[str, Any] = {}
        for i, (key, label) in enumerate((("fps", "FPS"), ("detector", "DÉTECTEUR"), ("voice", "VOIX"))):
            tech.grid_columnconfigure(i, weight=1, uniform="tech")
            tile = ctk.CTkFrame(tech, fg_color=PANEL_LO, corner_radius=8)
            tile.grid(row=0, column=i, sticky="ew", padx=4)
            self._label(tile, label, self.fonts.caps, DIM).grid(row=0, column=0, padx=8, pady=(7, 0))
            val = self._label(tile, "—", self.fonts.tiny_bold, TEXT)
            val.grid(row=1, column=0, padx=8, pady=(0, 7))
            tile.grid_columnconfigure(0, weight=1)
            self.tech[key] = val
        return page

    def _scaled(self, px: int) -> int:
        try:
            return int(round(px * float(self.ctk.ScalingTracker.get_widget_scaling(self.root))))
        except Exception:
            return px

    def _font_px(self, size: int) -> int:
        return -abs(self._scaled(size))

    def _enemy_image(self, icon: np.ndarray | None, alias: str | None, mode: str) -> Any:
        """CTkImage of an enemy slot portrait (cached per champion and visibility)."""
        key = (alias, mode, icon is not None)
        img = self._enemy_cache.get(key)
        if img is None:
            ring = {"visible": DANGER, "approach": WARNING, "mia": DIM, "empty": BORDER}.get(mode, DIM)
            pil = circle_icon(icon, 88, ring, grey=(mode == "mia"), bg=PANEL_LO)
            img = self.ctk.CTkImage(light_image=pil, dark_image=pil, size=(44, 44))
            if len(self._enemy_cache) > 80:
                self._enemy_cache.clear()
            self._enemy_cache[key] = img
        return img

    def _draw_gauge(self) -> None:
        c = self.gauge
        try:
            w = max(10, int(c.winfo_width()))
            h = max(6, int(c.winfo_height()))
        except Exception:
            return
        c.delete("all")
        r = h / 2
        self._round_bar(c, 0, 0, w, h, r, "#1B2638")
        # segment ticks
        for f in (1 / 3, 2 / 3):
            x = w * f
            c.create_line(x, 2, x, h - 2, fill=PANEL, width=2)
        fw = max(0.0, min(1.0, self._gauge_frac)) * w
        if fw >= h:
            self._round_bar(c, 0, 0, fw, h, r, self._gauge_color)
        elif fw > 1:
            c.create_oval(0, 0, h, h, fill=self._gauge_color, outline="")

    @staticmethod
    def _round_bar(c: Any, x0: float, y0: float, x1: float, y1: float, r: float, color: str) -> None:
        c.create_oval(x0, y0, x0 + 2 * r, y1, fill=color, outline="")
        c.create_oval(x1 - 2 * r, y0, x1, y1, fill=color, outline="")
        c.create_rectangle(x0 + r, y0, x1 - r, y1, fill=color, outline="")

    # ------------------------------------------------------------------ alerts & voice page
    def _build_alerts_page(self) -> Any:
        page, right, body = self._page("Alertes & voix", "Choisis ce que le coach annonce et comment il parle")
        self._button(right, "Tester la voix", self.test_voice, "primary", icon="voice").grid(row=0, column=0)
        ex = _example_phrases()
        s = self._section(body, 0, "Alertes de gank", "Annonces vocales quand un ennemi menace ta position.")
        self._switch_row(s, "alert_jungler_approach", "Jungler ennemi qui approche", ex["jungler_approach"])
        self._switch_row(s, "alert_roam", "Roam d'un autre ennemi", ex["roam_approach"])
        self._switch_row(s, "alert_collapse", "Plusieurs ennemis convergent", ex["collapse"])
        self._switch_row(s, "alert_jungler_spotted", "Jungler ennemi aperçu", ex["jungler_spotted"])
        self._switch_row(s, "alert_laner_mia", "Adversaire de voie disparu (MIA)", ex["laner_mia"])

        s = self._section(body, 1, "Sensibilité",
                          "Plus la sensibilité est haute, plus les alertes arrivent tôt (et plus souvent).")
        self.radius_lbl: Any = None
        self._slider_row(s, "sensitivity", "Sensibilité des alertes", self._radius_text(), 0.6, 1.6, 0.05,
                         lambda v: f"× {fmt_decimal_fr(v, 2)}", float,
                         on_change=lambda _v: self._refresh_radius_text())
        self.radius_lbl = self._last_slot.desc_label

        s = self._section(body, 2, "Aides de jeu", "Rappels basés uniquement sur l'API officielle de Riot.")
        self._switch_row(s, "objective_timers", "Minuteurs des objectifs", ex["objective_soon"])
        self._switch_row(s, "recall_reminder", "Rappel pour dépenser ton or", ex["recall_gold"])
        self._slider_row(s, "recall_gold_threshold", "Seuil d'or du rappel", "Or à partir duquel le coach "
                         "te conseille de rentrer.", 300, 5000, 50, lambda v: f"{fmt_int_fr(v)} PO", int)
        self._switch_row(s, "control_ward_reminder", "Balise de contrôle", ex["control_ward"])
        self._switch_row(s, "death_recap", "Récap de mort", ex["death_recap"])
        self._switch_row(s, "break_reminder", "Conseil de pause",
                         "Après 3 défaites d'affilée : « une pause de 10 minutes aide à rester concentré ».")

        s = self._section(body, 3, "Voix", "Synthèse vocale Windows (SAPI), voix française de préférence.")
        _row, slot = self._row(s, "Voix", "« Automatique » choisit la meilleure voix française installée.")
        self.voice_menu = self.ctk.CTkOptionMenu(
            slot, values=[AUTO_VOICE], command=self.cb(self._on_voice_choice), width=300, height=32,
            font=self.fonts.small, dropdown_font=self.fonts.small, fg_color=PANEL_HI, button_color="#16284A",
            button_hover_color="#1D3560", text_color=TEXT, dropdown_fg_color=PANEL_HI,
            dropdown_hover_color="#1D3560", dropdown_text_color=TEXT, corner_radius=8, dynamic_resizing=False)
        self.voice_menu.grid(row=0, column=0)
        self.voice_menu.set(self.cfg.voice_name or AUTO_VOICE)
        self._widgets_by_field["voice_name"] = lambda: self.voice_menu.set(self.cfg.voice_name or AUTO_VOICE)
        self._slider_row(s, "voice_rate", "Vitesse", "De -10 (lent) à 10 (rapide). Défaut : 2.", -10, 10, 1,
                         lambda v: f"{int(v):+d}" if int(v) else "0", int)
        self._slider_row(s, "voice_volume", "Volume", None, 0, 100, 1, lambda v: f"{int(v)} %", int)
        self._switch_row(s, "beep_on_danger", "Bip avant un danger", "Deux bips courts avant « Gank ! ».")

        s = self._section(body, 4, "Raccourcis clavier",
                          "Touches globales (RegisterHotKey, comme Discord ou OBS) : rien n'est envoyé au jeu.")
        for field, title, desc in (
                ("hotkey_jungler", "Où est le jungler ?", "Annonce la dernière position connue du jungler ennemi."),
                ("hotkey_mute", "Couper / rétablir la voix", None),
                ("hotkey_overlay", "Afficher / masquer l'overlay", None)):
            cur = getattr(self.cfg, field) or "Désactivé"
            values = list(HOTKEY_CHOICES) + ([cur] if cur not in HOTKEY_CHOICES else [])
            self._choice_row(s, field, title, desc, [("" if v == "Désactivé" else v, v) for v in values],
                             width=150)
        return page

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
        page, right, body = self._page("Overlay", "Radar agrandi, panneau HUD et flash de danger par-dessus le jeu")
        self.btn_move = self._button(right, "Déplacer les fenêtres", self.toggle_move_mode, "secondary", icon="move")
        self.btn_move.grid(row=0, column=0)
        s = self._section(body, 0, "Affichage",
                          "Fenêtres transparentes traversées par la souris (mode Sans bordure ou Fenêtré). "
                          "Rien n'est dessiné dans le jeu ni sur la minimap.")
        self._switch_row(s, "overlay_enabled", "Activer l'overlay", "Interrupteur général (F11 en jeu).",
                         on_change=lambda _v: self._schedule_overlay_preview())
        self._switch_row(s, "radar_enabled", "Radar", "Copie agrandie de la minimap avec les menaces, "
                         "placée à côté de la vraie minimap.", on_change=lambda _v: self._schedule_overlay_preview())
        self._switch_row(s, "hud_enabled", "Panneau HUD", "Jauge de menace, jungler, 5 ennemis, objectifs.",
                         on_change=lambda _v: self._schedule_overlay_preview())
        self._switch_row(s, "danger_flash", "Flash de danger", "Cadre rouge sur les bords de l'écran en cas de gank.",
                         on_change=lambda _v: self._schedule_overlay_preview())
        s = self._section(body, 1, "Position possible dans le brouillard",
                          "Zone qui grandit là où un ennemi caché peut se trouver (dernière position vue "
                          "+ vitesse de déplacement). Aucune prédiction.")
        self._choice_row(s, "fog_mode", "Cercle de position", "Pour le jungler seulement, tous les ennemis, "
                         "ou désactivé.", FOG_MODES, segmented=True,
                         on_change=lambda _v: self._schedule_overlay_preview())
        self._slider_row(s, "fog_max_s", "Durée maximale", "Au-delà, la zone est trop grande : elle s'efface.",
                         10, 180, 5, lambda v: f"{int(v)} s", float)
        s = self._section(body, 2, "Placement")
        self._position_menus: dict[str, Any] = {}
        self._position_menus["radar"] = self._choice_row(
            s, "radar_position", "Position du radar", None, RADAR_POSITIONS, width=230,
            on_change=lambda _v: self._schedule_overlay_preview())
        self._slider_row(s, "radar_scale", "Taille du radar", "1,0 = même taille que la minimap.", 0.5, 2.0, 0.1,
                         lambda v: f"× {fmt_decimal_fr(v, 1)}", float,
                         on_change=lambda _v: self._schedule_overlay_preview())
        self._position_menus["hud"] = self._choice_row(
            s, "hud_position", "Position du HUD", None, HUD_POSITIONS, width=230,
            on_change=lambda _v: self._schedule_overlay_preview())
        self._refresh_position_menus()
        prev = self._card(body)
        prev.grid(row=3, column=0, sticky="ew", pady=(0, 14))
        prev.grid_columnconfigure(0, weight=1)
        self._label(prev, "Aperçu", self.fonts.h2, GOLD, anchor="w").grid(row=0, column=0, sticky="w",
                                                                         padx=20, pady=(16, 2))
        self._label(prev, "Exemple d'écran en jeu (gank en cours, 1920 × 1080).", self.fonts.tiny, MUTED,
                    anchor="w").grid(row=1, column=0, sticky="w", padx=20)
        self.overlay_preview = self.ctk.CTkLabel(prev, text="Génération de l'aperçu…", text_color=DIM,
                                                 font=self.fonts.small, fg_color=PANEL_LO, corner_radius=10,
                                                 width=560, height=315)
        self.overlay_preview.grid(row=2, column=0, padx=20, pady=(10, 18))
        return page

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

    @_guarded
    def _render_overlay_preview(self) -> None:
        self._overlay_preview_job = None
        if self._current_page != "overlay" or self._closing:
            return
        cfg = self.cfg
        width = self._scaled(560)

        def job() -> Image.Image:
            return _overlay_preview_image(cfg, width)

        def done(img: Image.Image) -> None:
            ci = self.ctk.CTkImage(light_image=img, dark_image=img, size=(560, int(560 * img.height / img.width)))
            self._images["overlay_preview"] = ci
            self.overlay_preview.configure(image=ci, text="")

        self._dispatcher.run(job, done, name="TreeAI-ui-overlay-preview")

    # ------------------------------------------------------------------ analysis page
    def _build_analysis_page(self) -> Any:
        ctk = self.ctk
        page, right, body = self._page("Analyses", "Tes parties enregistrées et les conseils d'après-partie")
        self._button(right, "Actualiser", self.refresh_games, "secondary", icon="refresh").grid(row=0, column=0,
                                                                                               padx=(0, 8))
        self._button(right, "Dossier des parties", self.open_games_dir, "secondary", icon="folder").grid(
            row=0, column=1)
        self.session_scope = self._label(body, "SESSION", self.fonts.caps, DIM, anchor="w")
        self.session_scope.grid(row=0, column=0, sticky="w", pady=(0, 6))
        cards = ctk.CTkFrame(body, fg_color="transparent")
        cards.grid(row=1, column=0, sticky="ew", pady=(0, 16))
        self.stat_labels: dict[str, tuple[Any, Any]] = {}
        for i, (key, label) in enumerate((("games", "Parties"), ("wins", "Victoires"),
                                          ("deaths", "Morts / partie"), ("avoided", "Ganks évités"))):
            cards.grid_columnconfigure(i, weight=1, uniform="stat")
            c = self._card(cards)
            c.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 6, 0 if i == 3 else 6))
            self._label(c, label.upper(), self.fonts.caps, DIM, anchor="w").grid(row=0, column=0, sticky="w",
                                                                                   padx=18, pady=(14, 0))
            v = self._label(c, "—", self.fonts.stat, TEXT, anchor="w")
            v.grid(row=1, column=0, sticky="w", padx=18)
            sub = self._label(c, " ", self.fonts.tiny, MUTED, anchor="w")
            sub.grid(row=2, column=0, sticky="w", padx=18, pady=(0, 14))
            self.stat_labels[key] = (v, sub)
        self._label(body, "Parties récentes", self.fonts.h2, GOLD, anchor="w").grid(row=2, column=0, sticky="w",
                                                                                    pady=(0, 8))
        self.games_box = ctk.CTkFrame(body, fg_color="transparent")
        self.games_box.grid(row=3, column=0, sticky="ew")
        self.games_box.grid_columnconfigure(0, weight=1)
        self._games_empty(text="Chargement…")
        return page

    def _games_empty(self, text: str | None = None) -> None:
        for w in self.games_box.winfo_children():
            w.destroy()
        c = self._card(self.games_box)
        c.grid(row=0, column=0, sticky="ew")
        c.grid_columnconfigure(0, weight=1)
        self._label(c, text or "Aucune partie enregistrée pour l'instant", self.fonts.h3, TEXT).grid(
            row=0, column=0, pady=(26, 4))
        if text is None:
            self._label(c, "Joue une partie avec l'analyse active : un rapport détaillé (morts, ganks subis, "
                           "jungler ennemi, conseils) apparaîtra ici.", self.fonts.small, MUTED,
                        wraplength=520, justify="center").grid(row=1, column=0, padx=20, pady=(0, 26))

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
            "deaths": (fmt_decimal_fr(dpg, 1) if dpg is not None else "—", "en moyenne"),
            "avoided": (str(st["ganks_avoided"]), f"sur {st['ganks']} ganks subis" if st["ganks"] else "aucun gank"),
        }
        for key, (v, sub) in vals.items():
            self.stat_labels[key][0].configure(text=v)
            self.stat_labels[key][1].configure(text=sub)
        for w in self.games_box.winfo_children():
            w.destroy()
        if not games:
            self._games_empty()
            return
        for i, g in enumerate(games[:50]):
            self._game_row(i, g)

    def _game_row(self, i: int, g: dict) -> None:
        ctk = self.ctk
        row = self._card(self.games_box, corner_radius=12)
        row.grid(row=i, column=0, sticky="ew", pady=(0, 8))
        row.grid_columnconfigure(1, weight=1)
        alias = str(game_field(g, "champion", "alias", default="") or "")
        name = str(game_field(g, "champion_name", "name", default="") or alias or "Champion inconnu")
        icon = None
        try:
            from treeaicoach.champions import get_default_db  # noqa: PLC0415

            icon = get_default_db().load_icon(alias) if alias else None
        except Exception:
            icon = None
        res = game_result(g)
        ring = SAFE if res == "win" else DANGER if res == "lose" else DIM
        pil = circle_icon(icon, 80, ring, bg=PANEL)
        img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(40, 40))
        self._images[f"game-{i}"] = img
        ctk.CTkLabel(row, text="", image=img, fg_color="transparent").grid(row=0, column=0, rowspan=2,
                                                                           padx=(16, 12), pady=12)
        self._label(row, name, self.fonts.h3, TEXT, anchor="w").grid(row=0, column=1, sticky="sw", pady=(0, 2))
        dur = game_field(g, "duration")
        when = fmt_game_date(game_datetime(g))
        sub = when + (f" · {fmt_clock(dur)}" if isinstance(dur, (int, float)) and dur > 0 else "")
        pos = game_field(g, "position")
        if isinstance(pos, str) and pos:
            sub += f" · {_POSITION_FR.get(pos.upper(), pos.title())}"
        self._label(row, sub, self.fonts.tiny, MUTED, anchor="w").grid(row=1, column=1, sticky="nw")
        badge_txt, badge_bg, badge_fg = {"win": ("VICTOIRE", "#113A26", SAFE),
                                         "lose": ("DÉFAITE", DANGER_DARK, "#FF8A9B")}.get(
            res or "", ("INCOMPLÈTE" if game_field(g, "incomplete") else "—", PANEL_HI, MUTED))
        row.grid_columnconfigure(3, minsize=76)
        row.grid_columnconfigure(4, minsize=64)
        ctk.CTkLabel(row, text=badge_txt, font=self.fonts.caps, fg_color=badge_bg, text_color=badge_fg,
                     corner_radius=6, height=22, width=88).grid(row=0, column=2, rowspan=2, padx=(8, 12))
        k, d, a = (_int_or_none(game_field(g, x)) for x in ("kills", "deaths", "assists"))
        kda = f"{k if k is not None else '?'} / {d if d is not None else '?'} / {a if a is not None else '?'}"
        self._label(row, kda, self.fonts.h3, TEXT).grid(row=0, column=3, sticky="s", padx=4)
        self._label(row, "K / D / A", self.fonts.caps, DIM).grid(row=1, column=3, sticky="n", padx=4)
        ganks = _int_or_none(game_field(g, "ganks"))
        surv = _int_or_none(game_field(g, "ganks_survived"))
        ratio = surv is not None and bool(ganks)
        gtxt = "—" if ganks is None else (f"{surv}/{ganks}" if ratio else str(ganks))
        self._label(row, gtxt, self.fonts.h3, TEXT).grid(row=0, column=4, sticky="s", padx=4)
        self._label(row, "ÉVITÉS" if ratio else "GANKS", self.fonts.caps, DIM).grid(row=1, column=4, sticky="n",
                                                                                 padx=4)
        btns = ctk.CTkFrame(row, fg_color="transparent")
        btns.grid(row=0, column=5, rowspan=2, padx=(10, 14))
        self._button(btns, "Rapport", lambda gg=g: self.open_report(gg), "ghost", icon="report", width=96,
                     height=32).grid(row=0, column=0, padx=(0, 6))
        self._button(btns, "", lambda gg=g: self.open_game_folder(gg), "secondary", icon="folder",
                     width=34, height=32).grid(row=0, column=1)

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
        page, _right, body = self._page("Réglages", "Minimap, détection, démarrage et maintenance")
        s = self._section(body, 0, "Minimap", "Par défaut, la minimap est trouvée automatiquement. "
                                              "Calibre-la à la main si la détection échoue.")
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

        s = self._section(body, 1, "Détection")
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

        s = self._section(body, 2, "Démarrage & rapports")
        self._switch_row(s, "autostart", "Démarrer l'analyse au lancement",
                         "L'analyse attend une partie en arrière-plan (processeur ≈ 0 hors partie).")
        ok, reason = autostart_support()
        _row, slot = self._row(s, "Lancer avec Windows",
                               "Ouvre TreeAI Coach à l'ouverture de ta session." if ok else reason)
        self.win_autostart_var = self.ctk.BooleanVar(value=get_windows_autostart() if ok else False)
        sw = self.ctk.CTkSwitch(slot, text="", variable=self.win_autostart_var, width=46, switch_width=42,
                                switch_height=22, command=self.cb(self._on_windows_autostart),
                                fg_color="#1B2638", progress_color=TEAL, button_color=TEXT)
        sw.grid(row=0, column=0)
        if not ok:
            sw.configure(state="disabled", button_color=DIM)
        self._switch_row(s, "post_game_report", "Rapport d'après-partie",
                         "Analyse chaque partie (morts, ganks subis, jungler ennemi) et écrit un rapport HTML.")
        self._switch_row(s, "open_report_automatically", "Ouvrir le rapport automatiquement",
                         "À la fin de la partie, dans ton navigateur.")

        s = self._section(body, 3, "Maintenance")
        _row, slot = self._row(s, "Journaux", "Utile pour signaler un problème.")
        self._button(slot, "Ouvrir les journaux", lambda: open_path(paths.logs_dir()), "secondary",
                     icon="folder").grid(row=0, column=0)
        _row, slot = self._row(s, "Données", str(paths.user_data_dir()))
        self._button(slot, "Ouvrir le dossier", lambda: open_path(paths.user_data_dir()), "secondary",
                     icon="folder").grid(row=0, column=0)
        _row, slot = self._row(s, "Réinitialiser", "Remet tous les réglages par défaut.")
        self._button(slot, "Réinitialiser", self.ask_reset, "danger").grid(row=0, column=0)
        try:
            self._build_updates_section(body, 4)
        except Exception:
            log.exception("Cannot build the updates section")
        return page

    # ------------------------------------------------------------------ updates (updater.py)
    def _build_updates_section(self, body: Any, row: int) -> None:
        s = self._section(body, row, "Mises à jour", "Les nouvelles versions sont publiées sur GitHub ; "
                                                     "le fichier est vérifié (SHA-256) avant d'être installé.")
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
        self._update_bar = self.ctk.CTkProgressBar(box, height=8)
        self._update_bar.set(0)
        self._update_bar.grid(row=1, column=0, sticky="ew", pady=(6, 2))
        self._update_bar.grid_remove()
        _row, slot = self._row(s, "Installer", "Télécharge la nouvelle version, la vérifie puis redémarre "
                                               "TreeAI Coach.")
        self._update_btn = self._button(slot, "Mettre à jour", self.install_update, "primary",
                                        state="disabled")
        self._update_btn.grid(row=0, column=0)
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

    def _set_update_status(self, text: str, color: str = MUTED) -> None:
        lbl = getattr(self, "_update_status", None)
        if lbl is not None:
            try:
                lbl.configure(text=text, text_color=color)
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
            self._set_update_status(res.message, color)
            btn = getattr(self, "_update_btn", None)
            if btn is not None:
                btn.configure(state="normal" if res.available and res.can_install else "disabled")
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
        self._update_btn.configure(state="disabled")
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
            self._update_btn.configure(state="normal")
            self._set_update_status(res.message, DANGER)
            self.show_error(res.message)

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
        new = dataclasses.replace(Config(), **keep).validated()
        self._replace_config(new, changed=set(f.name for f in dataclasses.fields(Config)))
        for refresh in list(self._widgets_by_field.values()):
            try:
                refresh()
            except Exception:
                log.debug("Widget refresh failed", exc_info=True)
        self._refresh_radius_text()
        self._refresh_position_menus()
        self.show_toast("Réglages réinitialisés.")

    # ------------------------------------------------------------------ help page
    def _build_help_page(self) -> Any:
        ctk = self.ctk
        page, _right, body = self._page("Aide", "Bien démarrer, sécurité et dépannage")
        steps = (
            ("Passe le jeu en mode « Sans bordure »",
             "Options du jeu → Vidéo → Mode d'affichage : Sans bordure (ou Fenêtré). En plein écran exclusif, "
             "la capture est noire et l'overlay invisible."),
            ("Lance TreeAI Coach",
             "Double-clique sur TreeAICoach.exe. L'analyse démarre toute seule et attend une partie : rien à "
             "installer, rien à configurer."),
            ("Joue ta partie",
             "Dès le chargement terminé, le coach trouve la minimap et suit les ennemis. Écoute les annonces et "
             "garde un œil sur le radar et le HUD."),
            ("Réagis aux alertes",
             "« Attention » : un ennemi se rapproche, reste prudent. « Gank ! … recule ! » : recule tout de suite "
             "vers ta tour. F9 : où est le jungler ?"),
            ("Consulte ton analyse",
             "À la fin de la partie, un rapport s'ouvre : morts, ganks subis, habitudes du jungler ennemi et "
             "conseils. Retrouve-les dans l'onglet Analyses."),
        )
        s = self._section(body, 0, "Mode d'emploi en 5 étapes")
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
                          "TreeAI Coach fonctionne comme un logiciel de streaming (OBS, Discord) :")
        for i, text in enumerate((
                "Il lit uniquement l'écran (la minimap déjà visible) et l'API officielle « Live Client Data » "
                "fournie par le jeu.",
                "Aucune lecture ni écriture de la mémoire du jeu, aucune injection, aucune touche ni clic simulé.",
                "Aucun suivi des sorts ni des ultimes ennemis, aucune prédiction cachée : seulement ce que tu "
                "pourrais voir toi-même.",
                "L'overlay est une fenêtre séparée, transparente, jamais dessinée dans le jeu.")):
            r = ctk.CTkFrame(s, fg_color="transparent")
            r.grid(row=i, column=0, sticky="ew", pady=3)
            r.grid_columnconfigure(1, weight=1)
            dot = ctk.CTkFrame(r, width=8, height=8, corner_radius=4, fg_color=TEAL)
            dot.grid(row=0, column=0, sticky="n", padx=(4, 12), pady=(7, 0))
            self._label(r, text, self.fonts.small, TEXT, anchor="w", justify="left", wraplength=580).grid(
                row=0, column=1, sticky="w")
        s = self._section(body, 2, "Dépannage")
        for i, (q, a) in enumerate((
                ("« Capture noire »", "Le jeu est en plein écran exclusif : passe en « Sans bordure »."),
                ("La minimap n'est pas trouvée", "Vérifie l'échelle de la minimap dans le jeu, puis utilise "
                 "« Calibrer la minimap » (Réglages ou tableau de bord)."),
                ("Aucune voix", "Clique sur « Tester la voix ». Vérifie le volume de Windows et installe une voix "
                 "française (Paramètres → Heure et langue → Voix)."),
                ("L'overlay n'apparaît pas", "Mode Sans bordure obligatoire ; vérifie l'interrupteur de l'onglet "
                 "Overlay (ou appuie sur F11)."),
                ("Alertes trop fréquentes ou trop tardives", "Ajuste la sensibilité dans « Alertes & voix »."),
                ("Un autre problème", "Réglages → Ouvrir les journaux, et joins le dernier fichier à ton message."))):
            r = ctk.CTkFrame(s, fg_color="transparent")
            r.grid(row=i, column=0, sticky="ew", pady=5)
            r.grid_columnconfigure(0, weight=1)
            self._label(r, q, self.fonts.h3, GOLD_HOVER, anchor="w").grid(row=0, column=0, sticky="w")
            self._label(r, a, self.fonts.small, MUTED, anchor="w", justify="left", wraplength=600).grid(
                row=1, column=0, sticky="w")
        self._label(body, f"{APP_NAME} {__version__} — projet indépendant, non affilié à Riot Games.",
                    self.fonts.tiny, DIM).grid(row=3, column=0, pady=(4, 10))
        return page

    def _number_badge(self, parent: Any, n: int) -> Any:
        """Round numbered badge (drawn with PIL so it stays a perfect circle with any font)."""
        key = f"badge-{n}"
        img = self._images.get(key)
        if img is None:
            ss, size = 4, 34
            S = size * ss
            im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            d.ellipse((0, 0, S - 1, S - 1), fill=_hex_rgb(GOLD_DARK) + (255,))
            d.ellipse((ss, ss, S - 1 - ss, S - 1 - ss), outline=_hex_rgb(GOLD) + (255,), width=ss)
            pil = im.resize((size * 2, size * 2), Image.LANCZOS)
            img = self.ctk.CTkImage(light_image=pil, dark_image=pil, size=(size, size))
            self._images[key] = img
        return self.ctk.CTkLabel(parent, text=str(n), image=img, compound="center", font=self.fonts.h3,
                                 text_color=TEXT, fg_color="transparent", width=34, height=34)

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
        fr = self.ctk.CTkFrame(self.root, fg_color=PANEL_HI, corner_radius=10, border_width=1, border_color=col)
        bar = self.ctk.CTkFrame(fr, width=4, height=22, corner_radius=2, fg_color=col)
        bar.grid(row=0, column=0, padx=(12, 10), pady=12)
        self._label(fr, text, self.fonts.small, TEXT, justify="left", wraplength=380).grid(
            row=0, column=1, padx=(0, 16), pady=10)
        fr.place(relx=1.0, rely=1.0, anchor="se", x=-22, y=-22)
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

    def _confirm(self, title: str, text: str, yes: str, on_yes: Callable[[], None]) -> None:
        ctk = self.ctk
        top = ctk.CTkToplevel(self.root)
        top.title(title)
        top.resizable(False, False)
        top.transient(self.root)
        self._set_window_icon(top)
        top.grid_columnconfigure(0, weight=1)
        self._label(top, title, self.fonts.h2, GOLD, anchor="w").grid(row=0, column=0, sticky="w", padx=24,
                                                                     pady=(22, 6))
        self._label(top, text, self.fonts.small, TEXT, anchor="w", justify="left", wraplength=380).grid(
            row=1, column=0, sticky="w", padx=24)
        bar = ctk.CTkFrame(top, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="e", padx=24, pady=20)

        def close() -> None:
            try:
                top.grab_release()
            except Exception:
                pass
            top.destroy()

        def ok() -> None:
            close()
            on_yes()

        self._button(bar, "Annuler", close, "secondary", width=110).grid(row=0, column=0, padx=(0, 8))
        self._button(bar, yes, ok, "danger", width=130).grid(row=0, column=1)
        top.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - top.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - top.winfo_height()) // 3
        top.geometry(f"+{max(0, x)}+{max(0, y)}")
        try:
            top.grab_set()
        except Exception:
            pass

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
            try:
                self.voice.set_params(voice_name=cfg.voice_name, rate=cfg.voice_rate, volume=cfg.voice_volume,
                                      beep_on_danger=cfg.beep_on_danger)
            except Exception:
                log.exception("voice.set_params failed")
        if diff & HOTKEY_FIELDS:
            self._rebind_hotkeys()
        if diff & DETECTOR_FIELDS and self.engine is not None:
            self._rebuild_engine(self.demo, start=None, new_detector=True)
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
                ov = self._overlay_factory(cfg, self._overlay_state)
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

        self._busy = True
        self._dispatcher.run(job, self._backend_ready, name="TreeAI-ui-init")

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

    def _overlay_state(self) -> Any:
        """State provider of the overlay thread (thread-safe engine snapshot)."""
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

        self._dispatcher.run(job, done, name="TreeAI-ui-rebuild")

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
                                    hover_color="#16284A", image=self._icon("move", 16, MUTED))
            self.show_toast("Positions de l'overlay enregistrées.")

    # ------------------------------------------------------------------ hotkeys
    def _rebind_hotkeys(self) -> None:
        cfg = self.cfg
        bindings: dict[str, Callable[[], None]] = {}
        for key, fn in ((cfg.hotkey_jungler, self._hk_jungler), (cfg.hotkey_mute, self._hk_mute),
                        (cfg.hotkey_overlay, self._hk_overlay)):
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
            msg = ("Chargement du détecteur et de la voix…" if self._busy else
                   (self.engine_error or "Le moteur d'analyse n'est pas disponible."))
        else:
            key = state_key(getattr(st, "state", None)) if st is not None else "STOPPED"
            if not running and key not in ("ERROR",):
                key = "STOPPED"
            msg = str(getattr(st, "message", "") or "") if st is not None else ""
            if key == "STOPPED" and not msg:
                msg = "Clique sur « Démarrer l'analyse » pour suivre ta prochaine partie."
        title, color = STATE_INFO.get(key, (key.title(), GOLD))
        self._set_text(self.state_title, title)
        self._set_text(self.state_msg, msg or " ")
        self._state_color = color
        if key != self._last_state_key:
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
                       and key == "RUNNING" else "—")
        det = str(getattr(st, "detector", "") or getattr(self._detector, "name", "") or "—")
        self._set_text(self.tech["detector"], _DETECTOR_FR.get(det.lower(), det)[:14])
        vname = str(getattr(st, "voice", "") or getattr(self.voice, "backend", "") or "—")
        if st is not None and getattr(st, "muted", False):
            self._set_text(self.tech["voice"], "Coupée")
        else:
            self._set_text(self.tech["voice"], _VOICE_FR.get(vname.lower(), vname)[:14])
        banner = getattr(st, "banner", None) if st is not None else None
        if banner and banner != self._banner_dismissed:
            self._set_text(self.banner_lbl, str(banner))
            if not self.banner.grid_info():
                self.banner.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        elif self.banner.grid_info():
            self.banner.grid_remove()

        self._update_threat(ov)
        self._update_enemies(ov, st)
        self._collect_alerts(st, ov)
        if self._current_page == "dashboard":
            self._draw_gauge_step()

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
                                     text="Démarrer l'analyse", fg_color=GOLD, hover_color=GOLD_HOVER,
                                     text_color=ON_GOLD, border_width=0, image=self._icon("play", 16, ON_GOLD))
        else:
            self.btn_start.configure(state="normal", text="Arrêter l'analyse", fg_color=DANGER_DARK,
                                     hover_color="#6A1D2C", text_color="#FFD9DF", border_width=1,
                                     border_color=DANGER, image=self._icon("stop", 14, "#FFD9DF"))

    @staticmethod
    def _set_text(widget: Any, text: str) -> None:
        try:
            if widget.cget("text") != text:
                widget.configure(text=text)
        except Exception:
            pass

    def _update_threat(self, ov: Any) -> None:
        if ov is None:
            self._set_text(self.threat_lbl, "—")
            self.threat_lbl.configure(text_color=DIM)
            self._set_text(self.threat_detail, "Hors partie")
            self._gauge_target, self._gauge_color = 0.0, DIM
            return
        lvl = int(getattr(ov, "threat_level", 0) or 0)
        lvl = min(max(lvl, 0), 2)
        text = str(getattr(ov, "threat_text", "") or THREAT_LABELS[lvl])
        head = THREAT_LABELS[lvl]
        detail = text.split("—", 1)[1].strip() if "—" in text else (
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
                    slot["name"].configure(text="—", text_color=DIM)
                    slot["status"].configure(text=" ", text_color=DIM)
                    slot["box"].configure(border_color=BORDER)
                    slot["sig"] = sig
                continue
            visible = bool(getattr(e, "visible", False))
            appr = bool(getattr(e, "approaching", False))
            jungler = bool(getattr(e, "is_jungler", False))
            ago = getattr(e, "last_seen_ago", None)
            mode = "approach" if visible and appr else "visible" if visible else "mia"
            name = str(getattr(e, "name", "") or getattr(e, "alias", "") or "?")
            if visible:
                status, scol = ("Approche", WARNING) if appr else ("Visible", SAFE)
            elif isinstance(ago, (int, float)) and math.isfinite(ago):
                status, scol = f"MIA {_fmt_ago(ago)}", (WARNING if jungler and ago < 45 else MUTED)
            else:
                status, scol = "Jamais vu", DIM
            sig = (getattr(e, "alias", None), mode, name, status, jungler)
            if slot["sig"] == sig:
                continue
            slot["sig"] = sig
            slot["icon"].configure(image=self._enemy_image(getattr(e, "icon", None), getattr(e, "alias", None), mode))
            slot["name"].configure(text=_ellipsize(name, 11) + (" ★" if jungler else ""),
                                   text_color=GOLD if jungler else TEXT)
            slot["status"].configure(text=status, text_color=scol)
            slot["box"].configure(border_color=WARNING if appr else (GOLD_DARK if jungler else BORDER))

    # ------------------------------------------------------------------ alerts journal
    def _collect_alerts(self, st: Any, ov: Any) -> None:
        gt = getattr(st, "game_time", None) if st is not None else None
        eng = self.engine
        recent = getattr(eng, "recent_alerts", None) if eng is not None else None
        if callable(recent):
            try:
                items = list(recent() or [])[-JOURNAL_MAX:]
                entries = [_alert_entry(a) for a in items]
                entries = [e for e in entries if e is not None]
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
            tb.configure(state="normal")
            tb.delete("1.0", "end")
            if not self._journal:
                tb.insert("end", "Aucune alerte pour l'instant. Les annonces vocales apparaîtront ici.", "empty")
            for gt, lvl, text in reversed(self._journal):
                lvl = min(max(int(lvl), 0), 2)
                tb.insert("end", f"{fmt_clock(gt) if gt is not None else '  —  '}   ", ("time", "line"))
                tb.insert("end", "● ", (f"lvl{lvl}", "line"))
                tb.insert("end", text + "\n", (f"lvl{lvl}", "line"))
            tb.configure(state="disabled")
        except Exception:
            log.debug("Journal render failed", exc_info=True)

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
            if self._current_page == "dashboard" and self.root.state() != "iconic":
                self._pulse_phase = (self._pulse_phase + PULSE_MS / 1000 / 1.6) % 1.0
                color = getattr(self, "_state_color", DIM)
                active = self._last_state_key in ("RUNNING", "WAITING_GAME", "LOCATING", "STARTING")
                k = 0.5 - 0.5 * math.cos(2 * math.pi * self._pulse_phase) if active else 0.0
                r = 6 + 10 * k
                self.dot_canvas.coords(self._dot_halo, 17 - r, 17 - r, 17 + r, 17 + r)
                self.dot_canvas.itemconfigure(self._dot_halo, fill=_blend(color, PANEL, 0.55 * (1 - k) + 0.1))
                self.dot_canvas.itemconfigure(self._dot_core, fill=color)
                self._draw_gauge_step()
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
        except Exception:
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
_VOICE_FR = {"sapi": "Windows", "print": "Journal", "": "—"}
_ALERT_KINDS = frozenset({"jungler_approach", "roam_approach", "collapse", "jungler_spotted", "laner_mia",
                          "objective_soon", "recall_gold", "control_ward", "jungler_where", "death_recap"})
_POSITION_FR = {"TOP": "Haut", "JUNGLE": "Jungle", "MIDDLE": "Milieu", "BOTTOM": "Bas", "UTILITY": "Support"}


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


def _overlay_preview_image(cfg: Config, width: int) -> Image.Image:
    """Static overlay preview (sample "danger" state over a game-like screen)."""
    from treeaicoach import overlay_render as orr  # noqa: PLC0415

    states = orr.sample_states()
    st = states.get("danger") or next(iter(states.values()))
    st = dataclasses.replace(st)
    if not cfg.danger_flash or not cfg.overlay_enabled:
        st.flash = 0.0
    if cfg.fog_mode == "off":
        st.fogs = []
    rgba = None
    try:
        import importlib  # noqa: PLC0415

        importlib.import_module("treeaicoach.overlay")
        view_cfg = dataclasses.replace(cfg)
        rgba = orr.render_preview(st, width=width, cfg=view_cfg, now=0.3)
        if not rgba.any():
            rgba = None
    except Exception:
        rgba = None
    if rgba is None:     # overlay.py unavailable: simple layout (HUD top-left, radar above the minimap)
        sw, sh = 1920, 1080
        mm = orr.default_minimap_rect(sw, sh)
        bg = orr.game_background(sw, sh, mm)
        if cfg.overlay_enabled and cfg.radar_enabled:
            size = int(mm[2] * cfg.radar_scale)
            radar = orr.render_radar(st, size, None, 0.3)
            orr.composite_over(bg, radar, mm[0] + mm[2] - size, max(0, mm[1] - size - 12))
        if cfg.overlay_enabled and cfg.hud_enabled:
            hud = orr.render_hud(st, 360, 0.3)
            x = 24 if cfg.hud_position != "top_right" else sw - hud.shape[1] - 24
            y = 24 if cfg.hud_position != "left_middle" else (sh - hud.shape[0]) // 2
            orr.composite_over(bg, hud, x, y)
        if cfg.overlay_enabled and cfg.danger_flash and st.flash > 0:
            orr.composite_over(bg, orr.render_flash(sw, sh, st.flash, mm, 12), 0, 0)
        import cv2  # noqa: PLC0415

        rgb = cv2.resize(bg, (width, int(round(sh * width / sw))), interpolation=cv2.INTER_AREA)
        return rounded_on_bg(Image.fromarray(rgb, "RGB").convert("RGBA"), 10, PANEL)
    return rounded_on_bg(Image.fromarray(rgba, "RGBA"), 10, PANEL)


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
