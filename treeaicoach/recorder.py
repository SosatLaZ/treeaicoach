"""Game recorder: keeps a compact, bounded record of the current match for the post-game report.

The engine feeds it from its threads (ARCHITECTURE.md §6.5):

* :meth:`GameRecorder.on_game_info` (~1 Hz, Live Client poller): starts the record, keeps
  the roster, my scores / gold / items / level (a snapshot every 10 s of game time plus one
  at every change of level, K/D/A or death state) and the Live Client events (deduplicated
  by ``EventID``); saves a ``.partial.json`` every 60 s.
* :meth:`GameRecorder.on_tracks` (analysis loop, frame rate): my position at 1 Hz and the
  positions of the *visible* enemies at most 2 Hz per champion. It downsamples by itself.
* :meth:`GameRecorder.on_alert`: every alert that was actually announced.
* :meth:`GameRecorder.finish`: writes ``games/<YYYY-MM-DD_HHMM>_<Alias>.json`` atomically,
  removes the ``.partial.json`` and returns the path (idempotent).

Record format (JSON, ``"schema": 1``)::

    {"schema": 1,
     "summary": {...},            # small header read by report.list_games() without a full parse
     "meta": {"app", "app_version", "start", "start_game_time", "champion", "champion_name",
              "riot_id", "summoner_name", "team", "position", "skin_id", "game_mode",
              "map_number", "map_terrain"},
     "roster": [{"alias", "name", "team", "position", "has_smite", "riot_id", "summoner_name",
                 "skin_id", "is_bot", "is_me"}, ...],             # 10 players
     "result": "Win" | "Lose" | null, "duration": 1694.0, "incomplete": false,
     "snapshots": [{"game_time", "level", "gold", "cs", "kills", "deaths", "assists",
                    "ward_score", "items", "is_dead"}, ...],
     "my_positions": [[game_time, u, v], ...],                   # 1 Hz, only when visible
     "sightings": {"LeeSin": [[game_time, u, v], ...], ...},     # <= 2 Hz per enemy, visible only
     "alerts": [[game_time, kind, level, text, alias], ...],     # alias = 5th, optional element
     "events": [{Live Client event}, ...],
     "fog": [[game_time, alias, u, v, radius], ...],             # enemy-jungler fog circle, <= 1 Hz
     "settings": {"sensitivity", "fog_max_s", "fog_mode", "safe_mode"}}   # coaching settings (calibration)

Memory is bounded (hard caps + decimation of the oldest data): a 60-minute game stays far
below 5 MB of JSON. Every public method is thread-safe and never raises.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import math
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from treeaicoach import APP_NAME, __version__

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
GAMES_DIR_NAME = "games"
FINAL_SUFFIX = ".json"
PARTIAL_SUFFIX = ".partial.json"

SNAPSHOT_PERIOD_S = 10.0        # game seconds between two periodic snapshots
MY_POS_PERIOD_S = 1.0           # my position: 1 Hz (game time)
SIGHTING_PERIOD_S = 0.5         # enemy sightings: <= 2 Hz per champion (game time)
AUTOSAVE_PERIOD_S = 60.0        # .partial.json refresh period (monotonic time)
SCOREBOARD_EVERY_S = 60.0       # Tab scoreboard timeline: one sample per game minute
NEW_GAME_BACKJUMP_S = 60.0      # game_time going back more than this = another game
SAME_GAME_TOLERANCE_S = 30.0    # after finish(): same player/champion and game_time >= end - this -> same game
MAX_SNAPSHOTS = 2000
MAX_MY_POSITIONS = 4 * 3600     # 4 h at 1 Hz, then decimated
MAX_SIGHTINGS_PER_KEY = 8000    # ~66 min of continuous visibility at 2 Hz, then decimated
MAX_SIGHTING_KEYS = 16          # 5 enemies + anonymous tracks; overflow goes to "enemy?"
OVERFLOW_KEY = "enemy?"
MAX_ALERTS = 3000
MAX_EVENTS = 3000
FOG_PERIOD_S = 1.0              # fog circle samples: <= 1 Hz (game time)
MAX_FOG = 4 * 3600              # then decimated
SETTINGS_KEYS = ("sensitivity", "warn_radius", "danger_radius", "fog_max_s", "fog_mode", "safe_mode")
MAX_TEXT_LEN = 160
MAX_ITEMS = 8

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")


# ---------------------------------------------------------------------------------- helpers
def default_games_dir() -> Path:
    """``user_data_dir()/games`` (not created here)."""
    try:
        from treeaicoach.paths import user_data_dir

        return user_data_dir() / GAMES_DIR_NAME
    except Exception:  # defensive: never raise
        log.exception("Cannot resolve the games directory")
        return Path(tempfile.gettempdir()) / "TreeAICoach" / GAMES_DIR_NAME


def _finite(x: Any, default: float | None = None) -> float | None:
    """``float(x)`` if finite, else ``default``."""
    if x is None or isinstance(x, bool):
        return default
    try:
        f = float(x)
    except (TypeError, ValueError, OverflowError):
        return default
    return f if math.isfinite(f) else default


def _int(x: Any, default: int = 0) -> int:
    f = _finite(x)
    return int(f) if f is not None else default


def _text(x: Any, max_len: int = MAX_TEXT_LEN) -> str:
    if x is None:
        return ""
    try:
        s = str(x)
    except Exception:
        return ""
    s = " ".join(s.split())
    return s[:max_len]


def _uv(pos: Any) -> tuple[float, float] | None:
    """Validated, clamped (u, v) or None."""
    try:
        u = _finite(pos[0])
        v = _finite(pos[1])
    except (TypeError, IndexError, KeyError):
        return None
    if u is None or v is None or not (-0.1 <= u <= 1.1 and -0.1 <= v <= 1.1):
        return None
    return min(1.0, max(0.0, u)), min(1.0, max(0.0, v))


def safe_file_part(name: Any, default: str = "Partie") -> str:
    """File-name friendly champion alias (``"Kai'Sa"`` -> ``"KaiSa"``)."""
    s = _SAFE_NAME_RE.sub("", _text(name, 40))
    return s or default


def _decimate(lst: list) -> None:
    """Halve a time series in place (keeps every other sample, always the last one)."""
    if len(lst) < 4:
        return
    last = lst[-1]
    del lst[1::2]
    if lst[-1] is not last:
        lst.append(last)


def _json_scalar(x: Any) -> Any:
    if x is None or isinstance(x, (bool, int)):
        return x
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    return _text(x, 100)


def sanitize_event(ev: Any) -> dict | None:
    """JSON-safe copy of a Live Client event (scalars and short string lists only)."""
    if not isinstance(ev, dict):
        return None
    out: dict[str, Any] = {}
    for k, v in list(ev.items())[:24]:
        key = _text(k, 40)
        if not key:
            continue
        if isinstance(v, (list, tuple)):
            out[key] = [_json_scalar(x) for x in list(v)[:10]]
        elif isinstance(v, dict):
            continue
        else:
            out[key] = _json_scalar(v)
    return out if out.get("EventName") else None


def _event_key(ev: dict) -> Any:
    eid = ev.get("EventID")
    if isinstance(eid, int) and not isinstance(eid, bool):
        return eid
    t = _finite(ev.get("EventTime"), 0.0) or 0.0
    return (str(ev.get("EventName")), round(t, 1), str(ev.get("KillerName")), str(ev.get("VictimName")))


def atomic_write_text(path: Path, text: str) -> bool:
    """Write ``text`` (UTF-8) to ``path`` atomically (temp file + ``os.replace``). Never raises."""
    tmp: str | None = None
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".part", dir=str(path.parent))
        with os.fdopen(fd, "wb") as fh:
            fh.write(text.encode("utf-8"))
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        for attempt in range(6):   # Windows: the target may be briefly locked (antivirus, indexer)
            try:
                os.replace(tmp, path)
                tmp = None
                return True
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.05 * (attempt + 1))
        return False
    except Exception as exc:
        log.warning("Cannot write %s: %s", path, exc)
        return False
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.debug("Cannot remove %s: %s", path, exc)


def _player_dict(p: Any, is_me: bool) -> dict[str, Any]:
    return {
        "alias": _text(getattr(p, "champion_alias", ""), 40),
        "name": _text(getattr(p, "champion_name", ""), 40),
        "team": _text(getattr(p, "team", ""), 8),
        "position": _text(getattr(p, "position", ""), 10),
        "has_smite": bool(getattr(p, "has_smite", False)),
        "riot_id": _text(getattr(p, "riot_id", ""), 64),
        "summoner_name": _text(getattr(p, "summoner_name", ""), 64),
        "skin_id": _int(getattr(p, "skin_id", 0)),
        "is_bot": bool(getattr(p, "is_bot", False)),
        "is_me": bool(is_me),
    }


def _game_result(events: Any) -> str | None:
    if not isinstance(events, list):
        return None
    for e in reversed(events):
        if isinstance(e, dict) and e.get("EventName") == "GameEnd":
            res = _text(e.get("Result"), 10).lower()
            if res in ("win", "victory", "victoire"):
                return "Win"
            if res in ("lose", "loss", "defeat", "défaite", "defaite"):
                return "Lose"
    return None


# ---------------------------------------------------------------------------------- recorder
class GameRecorder:
    """Records one match at a time (see module docstring). Thread-safe, never raises."""

    def __init__(self, out_dir: Path | None = None,
                 wallclock: Callable[[], _dt.datetime] | None = None,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._out_dir_arg = Path(out_dir) if out_dir is not None else None
        self._wallclock = wallclock or (lambda: _dt.datetime.now().astimezone())
        self._monotonic = monotonic
        self._lock = threading.RLock()
        self._io_lock = threading.Lock()
        self.active: bool = False
        self._gen = 0
        self._last_path: Path | None = None
        self._finished_sig: tuple | None = None
        self._finished_gt: float = 0.0
        self._warned: set[str] = set()
        self._settings: dict[str, Any] = {}
        self._reset_game_state()

    # ------------------------------------------------------------------ state
    def _reset_game_state(self) -> None:
        self._meta: dict[str, Any] | None = None
        self._roster: list[dict[str, Any]] = []
        self._roster_sig: tuple = ()
        self._sig: tuple | None = None
        self._stem: str = ""
        self._snapshots: list[dict[str, Any]] = []
        self._snap_important: list[bool] = []
        self._last_snap_gt: float = -math.inf
        self._last_snap_key: tuple | None = None
        self._latest_snap: dict[str, Any] | None = None
        self._my_positions: list[list[float]] = []
        self._last_my_gt: float = -math.inf
        self._sightings: dict[str, list[list[float]]] = {}
        self._last_sight_gt: dict[str, float] = {}
        self._alerts: list[list[Any]] = []
        self._events: list[dict[str, Any]] = []
        self._event_keys: set[Any] = set()
        self._result: str | None = None
        self._max_gt: float = 0.0
        self._anchor: tuple[float, float] | None = None    # (game_time, monotonic t) of last game info
        self._dirty = False
        self._last_autosave: float | None = None
        self._scoreboard: dict[str, Any] | None = None          # latest scoreboard.ScoreboardSummary.to_dict()
        self._scoreboard_timeline: list[list[Any]] = []         # [gt, team gold diff, {role: [gold, cs, lvl]}]
        self._last_sb_gt: float = -math.inf
        self._fog: list[list[Any]] = []
        self._last_fog_gt: float = -math.inf

    def _warn_once(self, key: str, msg: str, *args: Any) -> None:
        if key not in self._warned:
            self._warned.add(key)
            log.warning(msg, *args)

    @property
    def out_dir(self) -> Path:
        """Folder of the game records."""
        return self._out_dir_arg if self._out_dir_arg is not None else default_games_dir()

    @property
    def last_path(self) -> Path | None:
        """Path of the last finished record (None if none yet)."""
        with self._lock:
            return self._last_path

    # ------------------------------------------------------------------ time helpers
    def _estimate_gt(self, t: Any) -> float | None:
        """Game time for monotonic ``t`` from the last game-info anchor (None if impossible)."""
        if self._anchor is None:
            return None
        gt0, t0 = self._anchor
        tt = _finite(t)
        if tt is None or not (-5.0 <= tt - t0 <= 30.0):
            return gt0
        return max(0.0, gt0 + (tt - t0))

    def _resolve_gt(self, game_time: Any, t: Any) -> float | None:
        gt = _finite(game_time)
        if gt is not None and gt >= 0.0:
            return gt
        return self._estimate_gt(t)

    # ------------------------------------------------------------------ game info
    @staticmethod
    def _signature(game: Any) -> tuple:
        me = game.me
        return (_text(getattr(me, "riot_id", "")) or _text(getattr(me, "summoner_name", "")),
                _text(getattr(me, "champion_alias", "")), _text(getattr(me, "team", "")),
                _text(getattr(game, "game_mode", "")), _int(getattr(game, "map_number", 0)))

    def on_game_info(self, game: Any, t: float) -> None:
        """Feed one Live Client poll (~1 Hz). Starts a record on the first call of a match."""
        try:
            if game is None or getattr(game, "me", None) is None:
                return   # spectator / replay / no game: nothing personal to record
            gt = _finite(getattr(game, "game_time", None))
            if gt is None or gt < 0.0:
                return
            sig = self._signature(game)
            with self._lock:
                other_game = self.active and (sig != self._sig or gt < self._max_gt - NEW_GAME_BACKJUMP_S)
            if other_game:
                log.info("New game detected: closing the previous record")
                self.finish()
            autosave_due = False
            with self._lock:
                if not self.active:
                    if (self._finished_sig == sig and gt >= self._finished_gt - SAME_GAME_TOLERANCE_S):
                        return   # post-game screen of the game we already saved
                    self._start(game, gt, sig)
                self._update(game, gt, t)
                now = self._monotonic()
                if self._last_autosave is None:
                    self._last_autosave = now
                elif now - self._last_autosave >= AUTOSAVE_PERIOD_S:
                    autosave_due = True
            if autosave_due:
                self.autosave()
        except Exception:
            log.exception("GameRecorder.on_game_info failed")

    def _start(self, game: Any, gt: float, sig: tuple) -> None:
        self._reset_game_state()
        self._gen += 1
        me = game.me
        try:
            now_wall = self._wallclock()
        except Exception:
            now_wall = _dt.datetime.now().astimezone()
        start_wall = now_wall - _dt.timedelta(seconds=gt)
        alias = _text(getattr(me, "champion_alias", ""), 40)
        self._meta = {
            "app": APP_NAME,
            "app_version": __version__,
            "start": start_wall.isoformat(timespec="seconds"),
            "recorded_at": now_wall.isoformat(timespec="seconds"),
            "start_game_time": round(gt, 1),
            "champion": alias,
            "champion_name": _text(getattr(me, "champion_name", ""), 40),
            "riot_id": _text(getattr(me, "riot_id", ""), 64),
            "summoner_name": _text(getattr(me, "summoner_name", ""), 64),
            "team": _text(getattr(me, "team", ""), 8),
            "position": _text(getattr(me, "position", ""), 10),
            "skin_id": _int(getattr(me, "skin_id", 0)),
            "game_mode": _text(getattr(game, "game_mode", ""), 32),
            "map_number": _int(getattr(game, "map_number", 0)),
            "map_terrain": _text(getattr(game, "map_terrain", ""), 32) or "Default",
        }
        self._sig = sig
        self._stem = self._unique_stem(f"{start_wall:%Y-%m-%d_%H%M}_{safe_file_part(alias)}")
        self.active = True
        self._dirty = True
        log.info("Game record started: %s", self._stem)

    def _unique_stem(self, base: str) -> str:
        try:
            d = self.out_dir
            stem = base
            n = 2
            while (d / (stem + FINAL_SUFFIX)).exists() or (d / (stem + PARTIAL_SUFFIX)).exists():
                stem = f"{base}_{n}"
                n += 1
                if n > 99:
                    break
            return stem
        except Exception:
            return base

    def _update(self, game: Any, gt: float, t: Any) -> None:
        tt = _finite(t)
        if tt is not None:
            self._anchor = (gt, tt)
        self._max_gt = max(self._max_gt, gt)
        # roster (refreshed when the set of players changes)
        players = []
        try:
            players = list(game.all_players())
        except Exception:
            players = [game.me] + list(getattr(game, "allies", []) or []) + list(getattr(game, "enemies", []) or [])
        roster_sig = tuple((getattr(p, "champion_alias", ""), getattr(p, "team", ""), bool(getattr(p, "has_smite", False)))
                           for p in players)
        if roster_sig != self._roster_sig:
            self._roster_sig = roster_sig
            self._roster = [_player_dict(p, p is game.me) for p in players[:12]]
            self._dirty = True
        # events (deduplicated by EventID)
        events = getattr(game, "events", None)
        if isinstance(events, list):
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                key = _event_key(ev)
                if key in self._event_keys:
                    continue
                if len(self._events) >= MAX_EVENTS:
                    self._warn_once("events", "Game record: event cap reached (%d)", MAX_EVENTS)
                    break
                clean = sanitize_event(ev)
                self._event_keys.add(key)
                if clean is not None:
                    self._events.append(clean)
                    self._dirty = True
            res = _game_result(events)
            if res is not None and res != self._result:
                self._result = res
                self._dirty = True
        # my snapshot
        snap = self._make_snapshot(game, gt)
        self._latest_snap = snap
        key = (snap["level"], snap["kills"], snap["deaths"], snap["assists"], snap["is_dead"])
        changed = self._last_snap_key is not None and key != self._last_snap_key
        if changed or gt - self._last_snap_gt >= SNAPSHOT_PERIOD_S or gt < self._last_snap_gt:
            self._append_snapshot(snap, important=changed or self._last_snap_key is None)
            self._last_snap_gt = gt
            self._last_snap_key = key

    @staticmethod
    def _make_snapshot(game: Any, gt: float) -> dict[str, Any]:
        me = game.me
        scores = getattr(me, "scores", None)
        scores = scores if isinstance(scores, dict) else {}
        items = getattr(me, "items", None)
        items = [_int(i) for i in list(items)[:MAX_ITEMS]] if isinstance(items, (list, tuple)) else []
        gold = _finite(getattr(game, "current_gold", None))
        if gold is None:
            gold = _finite(getattr(me, "current_gold", None), 0.0)
        return {
            "game_time": round(gt, 1),
            "level": _int(getattr(me, "level", 1), 1),
            "gold": int(gold or 0),
            "cs": _int(scores.get("creepScore")),
            "kills": _int(scores.get("kills")),
            "deaths": _int(scores.get("deaths")),
            "assists": _int(scores.get("assists")),
            "ward_score": round(_finite(scores.get("wardScore"), 0.0) or 0.0, 1),
            "items": items,
            "is_dead": bool(getattr(me, "is_dead", False)),
        }

    def _append_snapshot(self, snap: dict[str, Any], important: bool) -> None:
        self._snapshots.append(snap)
        self._snap_important.append(important)
        self._dirty = True
        if len(self._snapshots) > MAX_SNAPSHOTS:
            # drop every other periodic (non-change) snapshot of the oldest half
            half = len(self._snapshots) // 2
            keep_s: list[dict[str, Any]] = []
            keep_i: list[bool] = []
            toggle = False
            for i, (s, imp) in enumerate(zip(self._snapshots, self._snap_important)):
                if i < half and not imp:
                    toggle = not toggle
                    if toggle:
                        continue
                keep_s.append(s)
                keep_i.append(imp)
            if len(keep_s) > MAX_SNAPSHOTS:   # only important ones left: plain decimation
                idx = list(range(0, len(keep_s), 2))
                if idx[-1] != len(keep_s) - 1:
                    idx.append(len(keep_s) - 1)
                keep_s, keep_i = [keep_s[i] for i in idx], [keep_i[i] for i in idx]
            self._snapshots, self._snap_important = keep_s, keep_i

    # ------------------------------------------------------------------ tracks
    def on_tracks(self, tracker: Any, t: float, game_time: float | None) -> None:
        """Feed the tracker state (called at frame rate; downsampled here). Never raises."""
        try:
            if tracker is None:
                return
            with self._lock:
                if not self.active:
                    return
                gt = self._resolve_gt(game_time, t)
            if gt is None:
                return
            me_pos = None
            try:
                me = tracker.me()
                if me is not None and getattr(me, "visible", True):
                    me_pos = _uv(me.position())
            except Exception:
                log.debug("on_tracks: tracker.me() failed", exc_info=True)
            enemies: list[tuple[str, tuple[float, float]]] = []
            try:
                for tr in tracker.enemies(visible_only=True) or []:
                    if not getattr(tr, "visible", True):
                        continue
                    pos = _uv(tr.position())
                    if pos is None:
                        continue
                    key = _text(getattr(tr, "alias", None) or getattr(tr, "key", None) or "enemy?", 40)
                    enemies.append((key, pos))
            except Exception:
                log.debug("on_tracks: tracker.enemies() failed", exc_info=True)
            with self._lock:
                if not self.active:
                    return
                self._record_positions(gt, me_pos, enemies)
        except Exception:
            log.exception("GameRecorder.on_tracks failed")

    def _record_positions(self, gt: float, me_pos: tuple[float, float] | None,
                          enemies: list[tuple[str, tuple[float, float]]]) -> None:
        gtr = round(gt, 1)
        if me_pos is not None:
            if gt < self._last_my_gt - 5.0:
                self._last_my_gt = -math.inf        # game time went back (replay / new timeline)
            if gt - self._last_my_gt >= MY_POS_PERIOD_S * 0.95:
                self._my_positions.append([gtr, round(me_pos[0], 3), round(me_pos[1], 3)])
                self._last_my_gt = gt
                self._dirty = True
                if len(self._my_positions) > MAX_MY_POSITIONS:
                    _decimate(self._my_positions)
        for key, pos in enemies:
            if key not in self._sightings and len(self._sightings) >= MAX_SIGHTING_KEYS - 1:
                key = OVERFLOW_KEY          # the overflow list is the 16th (last) key
            last = self._last_sight_gt.get(key, -math.inf)
            if gt < last - 5.0:
                last = -math.inf
            if gt - last < SIGHTING_PERIOD_S * 0.95:
                continue
            lst = self._sightings.setdefault(key, [])
            lst.append([gtr, round(pos[0], 3), round(pos[1], 3)])
            self._last_sight_gt[key] = gt
            self._dirty = True
            if len(lst) > MAX_SIGHTINGS_PER_KEY:
                _decimate(lst)

    def on_fog(self, estimates: Any, game_time: float | None) -> None:
        """Enemy-jungler fog circle (``fog_tracker.FogEstimate`` list) at <= 1 Hz, for the post-game
        check against the client timeline (``ground_truth.py``). Never raises."""
        try:
            gt = _finite(game_time)
            if gt is None or not estimates:
                return
            rows = []
            for e in list(estimates)[:8]:
                if not getattr(e, "is_jungler", False):
                    continue
                uv = _uv(getattr(e, "last_uv", None))
                r = _finite(getattr(e, "radius", None))
                if uv is None or r is None:
                    continue
                alias = _text(getattr(e, "alias", None) or getattr(e, "key", None), 40)
                rows.append([round(gt, 1), alias, round(uv[0], 3), round(uv[1], 3), round(max(0.0, r), 3)])
            if not rows:
                return
            with self._lock:
                if not self.active:
                    return
                if gt < self._last_fog_gt - 5.0:
                    self._last_fog_gt = -math.inf
                if gt - self._last_fog_gt < FOG_PERIOD_S * 0.95:
                    return
                self._last_fog_gt = gt
                self._fog.extend(rows)
                self._dirty = True
                if len(self._fog) > MAX_FOG:
                    _decimate(self._fog)
        except Exception:
            log.exception("GameRecorder.on_fog failed")

    def note_settings(self, cfg: Any) -> None:
        """Remember the coaching settings of this game (sensitivity...) for the calibration."""
        try:
            out: dict[str, Any] = {}
            for k in SETTINGS_KEYS:
                v = getattr(cfg, k, None)
                if isinstance(v, (bool, int, float, str)):
                    out[k] = _json_scalar(v)
            with self._lock:
                self._settings = out
        except Exception:
            log.exception("GameRecorder.note_settings failed")

    # ------------------------------------------------------------------ alerts
    def on_scoreboard(self, summary: Any, game_time: float | None) -> None:
        """Tab scoreboard summary (``ScoreboardSummary.to_dict()``): kept as the latest value +
        a compact timeline every :data:`SCOREBOARD_EVERY_S` of game time (lane diffs by role)."""
        try:
            if not isinstance(summary, dict):
                return
            with self._lock:
                if not self.active:
                    return
                self._scoreboard = summary
                gt = _finite(game_time, None)
                if gt is not None and gt - self._last_sb_gt >= SCOREBOARD_EVERY_S:
                    self._last_sb_gt = gt
                    lanes = {str(m.get("role")): [m.get("gold_diff", 0), m.get("cs_diff", 0), m.get("level_diff", 0)]
                             for m in summary.get("matchups") or [] if isinstance(m, dict)}
                    self._scoreboard_timeline.append([round(gt, 1), summary.get("team_gold_diff", 0), lanes])
                    if len(self._scoreboard_timeline) > 400:
                        _decimate(self._scoreboard_timeline)
                self._dirty = True
        except Exception:
            log.exception("GameRecorder.on_scoreboard failed")

    def on_alert(self, alert: Any, game_time: float | None) -> None:
        """Record an announced alert. Never raises."""
        try:
            if alert is None:
                return
            with self._lock:
                if not self.active:
                    return
                gt = self._resolve_gt(game_time, getattr(alert, "t", None))
                if gt is None:
                    return
                if len(self._alerts) >= MAX_ALERTS:
                    self._warn_once("alerts", "Game record: alert cap reached (%d)", MAX_ALERTS)
                    return
                kind = getattr(alert, "kind", "")
                kind_s = _text(getattr(kind, "value", kind), 32)
                level = _int(getattr(alert, "level", 0))
                alias = getattr(alert, "alias", None)
                self._alerts.append([round(gt, 1), kind_s, level, _text(getattr(alert, "text", "")),
                                     _text(alias, 40) or None])
                self._dirty = True
        except Exception:
            log.exception("GameRecorder.on_alert failed")

    # ------------------------------------------------------------------ record building
    def _duration(self) -> float:
        d = self._max_gt
        for ev in reversed(self._events):
            if ev.get("EventName") == "GameEnd":
                d = max(d, _finite(ev.get("EventTime"), 0.0) or 0.0)
                break
        return round(d, 1)

    def _summary(self, duration: float, incomplete: bool) -> dict[str, Any]:
        meta = self._meta or {}
        snap = self._latest_snap or (self._snapshots[-1] if self._snapshots else {})
        return {
            "start": meta.get("start"),
            "champion": meta.get("champion", ""),
            "champion_name": meta.get("champion_name", ""),
            "team": meta.get("team", ""),
            "position": meta.get("position", ""),
            "game_mode": meta.get("game_mode", ""),
            "result": self._result,
            "duration": duration,
            "kills": snap.get("kills", 0),
            "deaths": snap.get("deaths", 0),
            "assists": snap.get("assists", 0),
            "cs": snap.get("cs", 0),
            "level": snap.get("level", 1),
            "ward_score": snap.get("ward_score", 0.0),
            "incomplete": incomplete,
            "ganks": None,
            "ganks_survived": None,
        }

    def _build_record(self, final: bool) -> dict[str, Any]:
        """JSON-ready copy of the current record (lists copied: safe to serialize unlocked)."""
        snaps = list(self._snapshots)
        if final and self._latest_snap is not None and (
                not snaps or self._latest_snap["game_time"] > snaps[-1]["game_time"]):
            snaps.append(self._latest_snap)
        duration = self._duration()
        incomplete = not final
        return {
            "schema": SCHEMA_VERSION,
            "summary": self._summary(duration, incomplete),
            "meta": dict(self._meta or {}),
            "roster": [dict(p) for p in self._roster],
            "result": self._result,
            "duration": duration,
            "incomplete": incomplete,
            "snapshots": snaps,
            "my_positions": list(self._my_positions),
            "sightings": {k: list(v) for k, v in self._sightings.items()},
            "alerts": list(self._alerts),
            "events": list(self._events),
            "scoreboard": {"final": self._scoreboard, "timeline": list(self._scoreboard_timeline)},
            "fog": list(self._fog),
            "settings": dict(self._settings),
        }

    def snapshot(self, recent_s: float | None = None) -> dict[str, Any] | None:
        """Copy of the record so far (e.g. for ``analysis.death_recap``); None if not recording.

        With ``recent_s``, the time series (positions, sightings, alerts) are limited to the last
        ``recent_s`` seconds of game time, which keeps the copy cheap during the game.
        """
        try:
            with self._lock:
                if not self.active:
                    return None
                rec = self._build_record(final=False)
                if recent_s is not None and self._max_gt > 0:
                    t0 = self._max_gt - max(0.0, float(recent_s))
                    rec["my_positions"] = [p for p in rec["my_positions"] if p[0] >= t0]
                    rec["sightings"] = {k: [p for p in v if p[0] >= t0] for k, v in rec["sightings"].items()}
                    rec["alerts"] = [a for a in rec["alerts"] if a[0] >= t0]
                return rec
        except Exception:
            log.exception("GameRecorder.snapshot failed")
            return None

    def death_recap(self, death_event: Any) -> str | None:
        """Short French recap of my death (``analysis.death_recap`` on the recent record)."""
        try:
            rec = self.snapshot(recent_s=60.0)
            if rec is None:
                return None
            from treeaicoach.analysis import death_recap

            return death_recap(rec, death_event)
        except Exception:
            log.exception("GameRecorder.death_recap failed")
            return None

    @staticmethod
    def _dumps(record: dict[str, Any]) -> str:
        return json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

    # ------------------------------------------------------------------ saving
    def autosave(self) -> None:
        """Write the ``.partial.json`` now if something changed (crash safety). Never raises."""
        try:
            with self._io_lock:
                with self._lock:
                    if not self.active or not self._dirty or self._meta is None:
                        return
                    record = self._build_record(final=False)
                    path = self.out_dir / (self._stem + PARTIAL_SUFFIX)
                    self._dirty = False
                    self._last_autosave = self._monotonic()
                try:
                    text = self._dumps(record)
                except (TypeError, ValueError):
                    log.exception("Game record not serializable")
                    return
                if not atomic_write_text(path, text):
                    with self._lock:
                        self._dirty = True
        except Exception:
            log.exception("GameRecorder.autosave failed")

    def finish(self) -> Path | None:
        """Write the final record, remove the partial file; returns the path. Idempotent, never raises."""
        try:
            with self._io_lock:
                with self._lock:
                    if not self.active or self._meta is None:
                        return self._last_path
                    record = self._build_record(final=True)
                    stem = self._stem
                    out_dir = self.out_dir
                    self._finished_sig = self._sig
                    self._finished_gt = self._max_gt
                    self.active = False
                    self._reset_game_state()
                self._add_analysis_summary(record)
                final = out_dir / (stem + FINAL_SUFFIX)
                partial = out_dir / (stem + PARTIAL_SUFFIX)
                try:
                    text = self._dumps(record)
                except (TypeError, ValueError):
                    log.exception("Game record not serializable")
                    return None
                if not atomic_write_text(final, text):
                    return None
                _remove(partial)
                with self._lock:
                    self._last_path = final
                log.info("Game record saved: %s (%d bytes)", final, len(text.encode("utf-8")))
                return final
        except Exception:
            log.exception("GameRecorder.finish failed")
            return None

    @staticmethod
    def _add_analysis_summary(record: dict[str, Any]) -> None:
        """Fill ``summary.ganks`` / ``ganks_survived`` (for the history list) from the analysis."""
        try:
            from treeaicoach.analysis import analyze_game

            a = analyze_game(record)
            s = record.get("summary") or {}
            s["ganks"] = a.get("ganks_faced")
            s["ganks_survived"] = a.get("ganks_survived")
            summ = a.get("summary") or {}
            for k in ("kills", "deaths", "assists", "cs"):
                if summ.get(k) is not None:
                    s[k] = summ[k]
            record["summary"] = s
        except Exception:
            log.exception("Cannot compute the analysis summary of the record")

    def reset(self) -> None:
        """Drop the current record without saving it (e.g. user action)."""
        with self._io_lock, self._lock:
            self.active = False
            self._reset_game_state()
