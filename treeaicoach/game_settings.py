"""League of Legends display settings read from the game's own config files (read only).

The game keeps its options in ``<install>/Config/game.cfg`` (INI) and
``<install>/Config/PersistedSettings.json`` (the copy synchronised with the account, which
the game re-applies at launch, so it wins when both exist). Reading these files is the same
as reading any user file on disk: no memory access, no hook, nothing sent to the game.

Used as **priors** for the detection (never as the truth: the screen decides):

* ``FlipMiniMap`` -> the minimap is in the bottom-LEFT corner: the locator searches only
  that side (no left / right confusion, half the search);
* the settings fingerprint (resolution, window mode, ``MinimapScale``, ``GlobalScale``,
  flip) keys a small on-disk cache of the last located minimap rectangle
  (:class:`RectCache`): next game with the same settings, that rectangle is checked first
  (one ``verify`` ~8 ms instead of a full search) and a changed minimap scale invalidates it;
* ``WindowMode == 0`` (exclusive fullscreen) explains black captures;
* the colour-blind flag is exposed (ring colours are learned live by the roster matcher
  anyway, see roster_matcher.RingColorModel).

Key names and value meanings come from public game.cfg dumps; the colour-blind key is NOT
verified (several spellings are accepted). Every function is pure or never raises; on
non-Windows machines nothing is found and :func:`load_game_settings` returns None.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

GAME_CFG = "game.cfg"
PERSISTED = "PersistedSettings.json"
ENV_DIR = "TREEAI_LOL_DIR"
MAX_FILE_BYTES = 2 * 1024 * 1024
#: Seconds between two checks of the files' modification times (cheap re-read).
RELOAD_S = 10.0
#: Accepted spellings of the colour-blind option (section-less, case-insensitive).
_COLORBLIND_KEYS = ("colorblindmode", "colourblindmode", "enablecolorblindmode",
                    "colorblind", "colorpalette")


Sections = dict[str, dict[str, str]]


@dataclass(frozen=True)
class GameSettings:
    """Display-related game options (None = unknown)."""

    width: int | None = None
    height: int | None = None
    window_mode: int | None = None        # 0 fullscreen, 1 windowed, 2 borderless
    minimap_scale: float | None = None
    global_scale: float | None = None
    flip_minimap: bool | None = None
    colorblind: bool | None = None
    relative_team_colors: bool | None = None
    source: str = ""

    @property
    def exclusive_fullscreen(self) -> bool:
        return self.window_mode == 0

    def minimap_side(self) -> str | None:
        """"left" / "right" from ``FlipMiniMap``, None if unknown."""
        if self.flip_minimap is None:
            return None
        return "left" if self.flip_minimap else "right"

    def fingerprint(self) -> str:
        """Key of the settings that move / resize the minimap ("" if none is known)."""
        if self.minimap_scale is None and self.global_scale is None and self.flip_minimap is None:
            return ""

        def f(x: float | None) -> str:
            return "?" if x is None else f"{x:.3f}"

        return (f"{self.width or '?'}x{self.height or '?'}|w{self.window_mode if self.window_mode is not None else '?'}"
                f"|m{f(self.minimap_scale)}|g{f(self.global_scale)}"
                f"|f{'?' if self.flip_minimap is None else int(self.flip_minimap)}")


# ---------------------------------------------------------------------------------- parsing
def parse_game_cfg(text: Any) -> Sections:
    """``game.cfg`` INI text -> ``{section: {key: value}}`` (lower-case names; duplicate
    keys: last wins; comments / junk lines ignored). Never raises."""
    out: Sections = {}
    try:
        sec = ""
        for raw in str(text or "").splitlines():
            line = raw.strip().lstrip("﻿")
            if not line or line[0] in ";#":
                continue
            if line.startswith("[") and "]" in line:
                sec = line[1:line.index("]")].strip().lower()
                out.setdefault(sec, {})
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip().lower()
            if k:
                out.setdefault(sec, {})[k] = v.strip().strip('"')
    except Exception:
        log.debug("game.cfg parse failed", exc_info=True)
    return out


def parse_persisted(text: Any) -> Sections:
    """``PersistedSettings.json`` -> the sections of its ``game.cfg`` file entry (same
    shape as :func:`parse_game_cfg`). Never raises."""
    out: Sections = {}
    try:
        data = json.loads(text) if isinstance(text, (str, bytes)) else text
        files = data.get("files") if isinstance(data, dict) else None
        for fe in files if isinstance(files, list) else []:
            if not isinstance(fe, dict) or str(fe.get("name", "")).lower() != GAME_CFG:
                continue
            for sec in fe.get("sections") or []:
                if not isinstance(sec, dict):
                    continue
                name = str(sec.get("name", "") or "").strip().lower()
                d = out.setdefault(name, {})
                for st in sec.get("settings") or []:
                    if isinstance(st, dict) and st.get("name"):
                        d[str(st["name"]).strip().lower()] = str(st.get("value", "")).strip()
    except Exception:
        log.debug("PersistedSettings.json parse failed", exc_info=True)
    return out


def _get(sections: Sections, key: str, prefer: Iterable[str] = ()) -> str | None:
    for s in list(prefer) + [s for s in sections if s not in prefer]:
        v = sections.get(s, {}).get(key)
        if v not in (None, ""):
            return v
    return None


def _num(v: str | None) -> float | None:
    try:
        f = float(str(v).strip())
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _int(v: str | None, lo: int, hi: int) -> int | None:
    f = _num(v)
    if f is None or not lo <= f <= hi:
        return None
    return int(round(f))


def _bool(v: str | None) -> bool | None:
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    f = _num(s)
    return None if f is None else f != 0


def settings_from_sections(sections: Sections, source: str = "") -> GameSettings:
    """Pure: :class:`GameSettings` from parsed sections. Out-of-range values -> None."""
    g = ("general",)
    h = ("hud",)
    w = _int(_get(sections, "width", g), 320, 16384)
    hh = _int(_get(sections, "height", g), 200, 16384)
    mode = _int(_get(sections, "windowmode", g), 0, 2)
    ms = _num(_get(sections, "minimapscale", h))
    gs = _num(_get(sections, "globalscale", h))
    cb = None
    for k in _COLORBLIND_KEYS:
        cb = _bool(_get(sections, k))
        if cb is not None:
            break
    return GameSettings(
        width=w, height=hh, window_mode=mode,
        minimap_scale=ms if ms is not None and 0.0 <= ms <= 10.0 else None,
        global_scale=gs if gs is not None and 0.0 <= gs <= 10.0 else None,
        flip_minimap=_bool(_get(sections, "flipminimap", h)),
        colorblind=cb,
        relative_team_colors=_bool(_get(sections, "relativeteamcolors", g)),
        source=source)


def merge_sections(primary: Sections, secondary: Sections) -> Sections:
    """``primary`` values win; ``secondary`` fills the gaps."""
    out: Sections = {s: dict(d) for s, d in secondary.items()}
    for s, d in primary.items():
        out.setdefault(s, {}).update(d)
    return out


# ---------------------------------------------------------------------------------- files
def config_dirs() -> list[Path]:
    """Candidate ``<install>/Config`` folders (``TREEAI_LOL_DIR``, then the LCU discovery's
    install folders). Never raises."""
    dirs: list[Path] = []
    try:
        env = os.environ.get(ENV_DIR)
        if env:
            dirs.append(Path(env))
        from treeaicoach.lcu import candidate_dirs

        dirs += candidate_dirs()
    except Exception:
        log.debug("League install folders unknown", exc_info=True)
    out: list[Path] = []
    for d in dirs:
        c = d if d.name.lower() == "config" else d / "Config"
        if c not in out:
            out.append(c)
    return out


def _read(p: Path) -> str | None:
    try:
        if not p.is_file() or p.stat().st_size > MAX_FILE_BYTES:
            return None
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def load_game_settings(dirs: Iterable[Path] | None = None) -> GameSettings | None:
    """Settings of the first folder holding ``PersistedSettings.json`` or ``game.cfg``
    (the JSON wins, game.cfg fills the gaps); None when nothing is found. Never raises."""
    try:
        for d in (config_dirs() if dirs is None else [Path(x) for x in dirs]):
            pj, cfg = _read(d / PERSISTED), _read(d / GAME_CFG)
            if pj is None and cfg is None:
                continue
            secs = merge_sections(parse_persisted(pj) if pj else {},
                                  parse_game_cfg(cfg) if cfg else {})
            if not secs:
                continue
            gs = settings_from_sections(secs, source=str(d))
            log.info("Game settings (%s): %dx%s mode %s, minimap scale %s, HUD scale %s, "
                     "flip %s, colour-blind %s", d, gs.width or 0, gs.height, gs.window_mode,
                     gs.minimap_scale, gs.global_scale, gs.flip_minimap, gs.colorblind)
            return gs
    except Exception:
        log.debug("Game settings unreadable", exc_info=True)
    return None


class SettingsWatcher:
    """Cached :func:`load_game_settings`, re-read when a file changed (checked at most
    every ``RELOAD_S``). Thread-safe, never raises."""

    def __init__(self, dirs: Iterable[Path] | None = None, clock: Any = time.monotonic) -> None:
        self._dirs = list(dirs) if dirs is not None else None
        self._clock = clock
        self._lock = threading.Lock()
        self._next = -math.inf
        self._sig: tuple | None = None
        self._value: GameSettings | None = None

    def _signature(self) -> tuple:
        sig = []
        for d in (self._dirs if self._dirs is not None else config_dirs()):
            for name in (PERSISTED, GAME_CFG):
                try:
                    st = (Path(d) / name).stat()
                    sig.append((str(d), name, st.st_mtime_ns, st.st_size))
                except OSError:
                    continue
        return tuple(sig)

    def get(self) -> GameSettings | None:
        try:
            with self._lock:
                now = float(self._clock())
                if now < self._next:
                    return self._value
                self._next = now + RELOAD_S
                sig = self._signature()
                if sig != self._sig:
                    self._sig = sig
                    self._value = load_game_settings(self._dirs) if sig else None
                return self._value
        except Exception:
            log.debug("SettingsWatcher failed", exc_info=True)
            return None


# ---------------------------------------------------------------------------------- rect cache
class RectCache:
    """Last located minimap rectangle per (window size, settings fingerprint), on disk.

    ``get`` -> ``(x, y, w, h, score)`` relative to the window, or None. Never raises."""

    MAX_ENTRIES = 16

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, list] | None = None

    @staticmethod
    def key(win_w: int, win_h: int, settings: GameSettings | None) -> str:
        fp = settings.fingerprint() if settings is not None else ""
        return f"{int(win_w)}x{int(win_h)}" + (f"|{fp}" if fp else "")

    def _file(self) -> Path | None:
        if self._path is not None:
            return self._path
        try:
            from treeaicoach.paths import user_data_dir

            self._path = user_data_dir() / "minimap_cache.json"
        except Exception:
            return None
        return self._path

    def _load(self) -> dict[str, list]:
        if self._data is None:
            self._data = {}
            p = self._file()
            try:
                if p is not None and p.is_file():
                    raw = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        self._data = {str(k): v for k, v in raw.items()
                                      if isinstance(v, list) and len(v) == 5}
            except Exception:
                log.debug("Minimap cache unreadable", exc_info=True)
        return self._data

    def get(self, key: str) -> tuple[int, int, int, int, float] | None:
        try:
            with self._lock:
                v = self._load().get(key)
                if v is None:
                    return None
                x, y, w, h = (int(a) for a in v[:4])
                sc = float(v[4])
                if w < 16 or h < 16 or not math.isfinite(sc):
                    return None
                return x, y, w, h, sc
        except Exception:
            return None

    def put(self, key: str, x: int, y: int, w: int, h: int, score: float) -> None:
        try:
            with self._lock:
                d = self._load()
                old = d.pop(key, None)
                sc = float(score)
                if old is not None and [int(a) for a in old[:4]] == [int(x), int(y), int(w), int(h)]:
                    sc = max(sc, float(old[4]))     # same rectangle: keep its best score
                d[key] = [int(x), int(y), int(w), int(h), round(sc, 3)]
                while len(d) > self.MAX_ENTRIES:
                    d.pop(next(iter(d)))
                p = self._file()
                if p is None:
                    return
                tmp = p.with_suffix(".tmp")
                tmp.write_text(json.dumps(d), encoding="utf-8")
                os.replace(tmp, p)
        except Exception:
            log.debug("Minimap cache not saved", exc_info=True)


__all__ = ["GameSettings", "parse_game_cfg", "parse_persisted", "settings_from_sections",
           "merge_sections", "config_dirs", "load_game_settings", "SettingsWatcher", "RectCache"]
