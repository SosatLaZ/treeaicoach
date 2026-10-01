"""Epic objective timers (dragon, elder, voidgrubs, herald, baron) and voice announcements.

Sources (official only): the match clock and the kill events of the Live Client Data API
(``DragonKill`` with ``DragonType``, ``BaronKill``, ``HeraldKill``, ``HordeKill``).
Respawns after a kill are exact (dragon 5:00, elder / baron 6:00); first spawns come from a
schedule that changes with patches, so it can be overridden without rebuilding the app.

Season 2026 (patch 26.1+, checked against patch 26.19): Atakhan was removed from the game
(an ``AtakhanKill`` event or an ``"atakhan"`` key in an old override file is silently ignored),
Baron spawns at 20:00 again, the single Voidgrub camp (3 grubs) spawns at 8:00 and leaves at 14:45,
the Rift Herald spawns at 15:00 (gone at 19:45), dragons at 5:00 (+5:00 respawn), soul at 4 dragons,
Elder 6:00 after the soul / an Elder kill.  Schedule chain:

``OBJECTIVE_SCHEDULE`` (code) <- ``assets/objectives.json`` (bundled) <- ``user_data_dir()/objectives.json``.

Design:

* The state is **rebuilt from the whole set of kill events** each time it changes (a handful of
  events: cheap). Events are deduplicated by ``EventID`` and kept across polls (a reconnect
  that shortens the event list does not forget earlier kills); a list whose ids now describe
  different events (renumbering) replaces the known set.
* A new game (clock going back by more than a few seconds, or another roster) resets everything.
* The match clock is extrapolated between the 1 Hz API polls with ``t - game.fetched_at`` so
  that announcements are on time at the analysis frame rate.
* Each announcement (``cfg.objective_lead_s``, default 60 s and 20 s before a spawn) is made at
  most once per spawn. After a clock jump (reconnect, spectating) a missed window is skipped, or
  announced with the real remaining time if it is only a little late ("Dragon dans 45 secondes.").
  Two announcements due at the same moment are spaced by :data:`ANNOUNCE_SPACING_S`.
* Soul: after the 4th elemental dragon taken by one team, the next dragon is the Elder dragon.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import threading
import time
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from treeaicoach import paths
from treeaicoach.alerts import Alert, AlertKind, Level

log = logging.getLogger(__name__)

#: Default schedule, game seconds. ``first`` = first spawn, ``respawn`` = delay after a kill
#: (None = never respawns), ``despawn`` = leaves the map at that game time,
#: ``count`` (grubs) = kills that clear a wave, ``soul`` (dragon) = elemental dragons for the soul.
OBJECTIVE_SCHEDULE: dict[str, dict[str, float | None]] = {
    "dragon": {"first": 300, "respawn": 300, "soul": 4},
    "elder": {"respawn": 360},
    "grubs": {"first": 480, "respawn": None, "despawn": 885, "count": 3},
    "herald": {"first": 900, "despawn": 1185},
    "baron": {"first": 1200, "respawn": 360},
}
SCHEDULE_FILE_NAME = "objectives.json"
SCHEDULE_KEYS: dict[str, tuple[str, ...]] = {
    "dragon": ("first", "respawn", "soul"),
    "elder": ("respawn",),
    "grubs": ("first", "respawn", "despawn", "count"),
    "herald": ("first", "respawn", "despawn"),
    "baron": ("first", "respawn", "despawn"),
}
#: Objectives removed from the game: silently ignored in override files (Atakhan: removed in 26.1).
LEGACY_KEYS: frozenset[str] = frozenset({"atakhan"})
MAX_SCHEDULE_TIME_S = 4 * 3600.0
MAX_SCHEDULE_FILE_BYTES = 64 * 1024

#: French display names (HUD) per objective kind.
NAMES_FR: dict[str, str] = {
    "dragon": "Dragon",
    "elder": "Dragon ancestral",
    "grubs": "Larves",
    "herald": "Héraut",
    "baron": "Baron",
}
#: Order of the objective slots (the dragon slot turns into "elder" after the soul).
SLOT_ORDER: tuple[str, ...] = ("dragon", "grubs", "herald", "baron")
KILL_EVENTS: dict[str, str] = {
    "DragonKill": "dragon",
    "BaronKill": "baron",
    "HeraldKill": "herald",
    "HordeKill": "grubs",
}

DEFAULT_LEADS_S: tuple[int, ...] = (60, 20)
ANNOUNCE_SPACING_S = 2.5          # min gap between two of our announcements (monotonic s)
NOMINAL_TOLERANCE_S = 2.0         # late by less than this -> say the nominal lead ("une minute")
MIN_ANNOUNCE_REMAINING_S = 2.0    # never announce a spawn closer than this
GAME_TIME_BACK_RESET_S = 3.0      # clock going back more than this -> new game / rewind: reset
MAX_EXTRAPOLATION_S = 2.5         # max extrapolation of the API clock between polls
MAX_EVENTS = 2000                 # bound on the remembered kill events

_OBJECTIVE_SOON: Any = getattr(AlertKind, "OBJECTIVE_SOON", "objective_soon")


@dataclass
class ObjectiveState:
    """Snapshot of one objective for the HUD.

    ``next_spawn`` is the game time of the (next) spawn: in the future while ``alive`` is False;
    once spawned, ``alive`` is True and ``next_spawn`` keeps the (past) spawn time.
    ``remaining`` = seconds until the spawn at the last update (0 when alive).
    """

    name: str                           # "Dragon", "Baron", "Héraut", "Larves", "Dragon ancestral"
    next_spawn: float | None
    alive: bool
    source: str                         # "schedule" | "event"
    key: str = ""                       # "dragon" | "elder" | "grubs" | "herald" | "baron"
    remaining: float | None = None


# --------------------------------------------------------------------------- schedule


def _clean_value(key: str, value: Any) -> tuple[bool, float | None]:
    """(ok, value) for one schedule entry; None (= never) allowed for first / respawn / despawn."""
    if value is None:
        return (key in ("first", "respawn", "despawn")), None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False, None
    f = float(value)
    if not math.isfinite(f) or f < 0 or f > MAX_SCHEDULE_TIME_S:
        return False, None
    if key in ("count", "soul"):
        return (1 <= f <= 20), float(int(round(f)))
    return True, f


def merge_schedule(base: Mapping[str, Any], override: Any, source: str = "override") -> dict[str, dict[str, float | None]]:
    """Copy of ``base`` updated with the valid entries of ``override`` (invalid ones logged)."""
    out = copy.deepcopy(dict(base))
    if not isinstance(override, Mapping):
        if override is not None:
            log.warning("Objective schedule %s is not an object; ignored", source)
        return out
    for obj, entry in override.items():
        if not isinstance(obj, str) or obj.startswith("_") or obj in LEGACY_KEYS:
            continue
        allowed = SCHEDULE_KEYS.get(obj)
        if allowed is None or not isinstance(entry, Mapping):
            log.warning("Objective schedule %s: unknown or invalid entry %r ignored", source, obj)
            continue
        target = out.setdefault(obj, {})
        for k, v in entry.items():
            if not isinstance(k, str) or k.startswith("_"):
                continue
            if k not in allowed:
                log.warning("Objective schedule %s: unknown key %s.%s ignored", source, obj, k)
                continue
            ok, clean = _clean_value(k, v)
            if not ok:
                log.warning("Objective schedule %s: invalid value %s.%s=%r ignored", source, obj, k, v)
                continue
            target[k] = clean
    return out


def _read_json(path: Path) -> Any:
    try:
        if not path.is_file():
            return None
        if path.stat().st_size > MAX_SCHEDULE_FILE_BYTES:
            log.warning("Objective schedule %s is too large; ignored", path)
            return None
        return json.loads(path.read_bytes().decode("utf-8-sig"))
    except Exception as exc:
        log.warning("Cannot read objective schedule %s (%s); ignored", path, exc)
        return None


def schedule_paths() -> list[Path]:
    """Override files, lowest priority first: bundled asset, then the user's file."""
    out: list[Path] = []
    try:
        out.append(paths.asset_path(SCHEDULE_FILE_NAME))
    except Exception:
        log.debug("No asset path for the objective schedule", exc_info=True)
    try:
        out.append(paths.user_data_dir() / SCHEDULE_FILE_NAME)
    except Exception:
        log.debug("No user path for the objective schedule", exc_info=True)
    return out


def load_schedule(files: Iterable[Path | str] | None = None) -> dict[str, dict[str, float | None]]:
    """``OBJECTIVE_SCHEDULE`` overridden by the JSON files (default :func:`schedule_paths`). Never raises."""
    sched = copy.deepcopy(OBJECTIVE_SCHEDULE)
    try:
        for p in (schedule_paths() if files is None else [Path(f) for f in files]):
            data = _read_json(Path(p))
            if data is not None:
                sched = merge_schedule(sched, data, str(p))
                log.info("Objective schedule overrides loaded from %s", p)
    except Exception:
        log.exception("Cannot load the objective schedule overrides; using defaults")
        return copy.deepcopy(OBJECTIVE_SCHEDULE)
    return sched


# --------------------------------------------------------------------------- phrases


def _delay_fr(n: int) -> str:
    """``"une minute"`` / ``"20 secondes"`` / ``"1 minute 30"`` / ``"2 minutes"``."""
    if n < 60:
        return "une seconde" if n == 1 else f"{n} secondes"
    m, s = divmod(n, 60)
    head = "une minute" if m == 1 else f"{m} minutes"
    if s == 0:
        return head
    return f"{m} minute {s}" if m == 1 else f"{m} minutes {s}"


def announcement_text(kind: str, seconds: int) -> str:
    """Short French announcement, e.g. ``"Dragon dans une minute."``."""
    delay = _delay_fr(max(1, int(seconds)))
    if kind == "grubs":
        return f"Les larves apparaissent dans {delay}."
    return f"{NAMES_FR.get(kind, 'Objectif')} dans {delay}."


def _spoken_seconds(lead: int, remaining: float) -> int:
    """Nominal lead if on time, else the real remaining time (1 s steps < 30 s, 5 s steps above)."""
    if lead - remaining <= NOMINAL_TOLERANCE_S:
        return int(lead)
    if remaining < 30:
        return max(1, int(round(remaining)))
    return max(5, int(5 * round(remaining / 5.0)))


def _grace(lead: int) -> float:
    """How late an announcement may still be made (s)."""
    return min(15.0, max(3.0, 0.25 * lead))


# --------------------------------------------------------------------------- events


@dataclass(frozen=True)
class _KillEvent:
    uid: tuple                     # dedupe key
    content: tuple                 # (name, time) used to detect renumbered lists
    slot: str                      # "dragon" | "baron" | ...
    time: float
    elder: bool = False
    team: str | None = None        # team of the killer (dragons), if known


def _norm_player(name: Any) -> str:
    if not isinstance(name, str):
        return ""
    s = unicodedata.normalize("NFC", name).replace(" ", " ")
    s = s.split("#", 1)[0]
    return " ".join(s.split()).casefold()


def _team_lookup(game: Any) -> dict[str, str]:
    """Normalized player names (game name, summoner name, Riot ID) -> team."""
    out: dict[str, str] = {}
    try:
        players = game.all_players()
    except Exception:
        return out
    for p in players:
        team = getattr(p, "team", "")
        if team not in ("ORDER", "CHAOS"):
            continue
        for attr in ("riot_id", "summoner_name"):
            key = _norm_player(getattr(p, attr, ""))
            if key:
                out.setdefault(key, team)
    return out


def _killer_team(ev: Mapping[str, Any], lookup: Mapping[str, str]) -> str | None:
    team = lookup.get(_norm_player(ev.get("KillerName")))
    if team:
        return team
    assisters = ev.get("Assisters")
    if isinstance(assisters, list):
        teams = {lookup.get(_norm_player(a)) for a in assisters[:10]} - {None}
        if len(teams) == 1:
            return teams.pop()
    return None


def _event_time(ev: Mapping[str, Any]) -> float | None:
    v = ev.get("EventTime")
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) and 0.0 <= f <= MAX_SCHEDULE_TIME_S else None


def _parse_kill(ev: Any, lookup: Mapping[str, str]) -> _KillEvent | None:
    if not isinstance(ev, Mapping):
        return None
    name = ev.get("EventName")
    slot = KILL_EVENTS.get(name) if isinstance(name, str) else None
    if slot is None:
        return None
    t = _event_time(ev)
    if t is None:
        return None
    eid = ev.get("EventID")
    content = (name, round(t, 2))
    if isinstance(eid, int) and not isinstance(eid, bool):
        uid: tuple = ("id", eid)
    else:
        uid = ("content", name, round(t, 2), _norm_player(ev.get("KillerName")))
    elder = False
    team = None
    if slot == "dragon":
        dtype = ev.get("DragonType")
        elder = isinstance(dtype, str) and dtype.strip().casefold() == "elder"
        team = _killer_team(ev, lookup)
    return _KillEvent(uid=uid, content=content, slot=slot, time=t, elder=elder, team=team)


# --------------------------------------------------------------------------- state machine


@dataclass
class _Slot:
    key: str                       # slot name (SLOT_ORDER)
    kind: str                      # "dragon" / "elder" for the dragon slot, else == key
    next_spawn: float | None       # None = no more spawns
    source: str = "schedule"
    wave_kills: int = 0            # grubs


@dataclass
class _Announcement:
    kind: str
    lead: int
    remaining: float
    done_key: tuple


def _num(entry: Mapping[str, Any] | None, key: str) -> float | None:
    if not entry:
        return None
    v = entry.get(key)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def build_slots(schedule: Mapping[str, Mapping[str, Any]], events: Iterable[_KillEvent]) -> dict[str, _Slot]:
    """Objective slots after applying the kill events (in time order) to the schedule."""
    slots: dict[str, _Slot] = {}
    for key in SLOT_ORDER:
        entry = schedule.get(key)
        if entry is None:
            continue
        slots[key] = _Slot(key=key, kind=key, next_spawn=_num(entry, "first"))
    dragon_sched = schedule.get("dragon") or {}
    elder_sched = schedule.get("elder") or {}
    soul = int(_num(dragon_sched, "soul") or 4)
    team_dragons: dict[str | None, int] = {}
    total_dragons = 0
    for ev in sorted(events, key=lambda e: (e.time, e.uid)):
        slot = slots.get(ev.slot)
        if slot is None:
            continue
        if ev.slot == "dragon":
            if ev.elder:
                slot.kind = "elder"
                respawn = _num(elder_sched, "respawn")
            else:
                team_dragons[ev.team] = team_dragons.get(ev.team, 0) + 1
                total_dragons += 1
                known = max((n for team, n in team_dragons.items() if team is not None), default=0)
                if known >= soul or total_dragons >= 2 * soul - 1:
                    slot.kind = "elder"
                    respawn = _num(elder_sched, "respawn")
                else:
                    slot.kind = "dragon"
                    respawn = _num(dragon_sched, "respawn")
            slot.next_spawn = ev.time + respawn if respawn is not None else None
            slot.source = "event"
            continue
        entry = schedule.get(ev.slot) or {}
        if ev.slot == "grubs":
            slot.wave_kills += 1
            if slot.wave_kills < int(_num(entry, "count") or 1):
                continue                   # some grubs are still alive
            slot.wave_kills = 0
        respawn = _num(entry, "respawn")
        slot.next_spawn = ev.time + respawn if respawn is not None else None
        slot.source = "event"
    return slots


def _is_rift(game: Any) -> bool:
    try:
        return bool(game.is_summoners_rift)
    except Exception:
        return False


def _roster_signature(game: Any) -> tuple:
    try:
        return tuple(sorted((str(p.team), str(p.champion_alias), str(p.riot_id)) for p in game.all_players()))
    except Exception:
        return ()


def _leads_from(cfg: Any) -> tuple[int, ...]:
    raw = getattr(cfg, "objective_lead_s", DEFAULT_LEADS_S)
    if not isinstance(raw, (list, tuple)):
        return DEFAULT_LEADS_S
    leads: set[int] = set()
    for v in raw:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            continue
        leads.add(int(min(max(round(float(v)), 5), 300)))
    return tuple(sorted(leads, reverse=True))


class ObjectiveTimers:
    """Tracks epic objective spawns and produces ``OBJECTIVE_SOON`` alerts. Thread-safe.

    ``schedule`` defaults to :func:`load_schedule` (code defaults + JSON overrides); an explicit
    schedule is merged over ``OBJECTIVE_SCHEDULE`` only (no file is read).
    """

    def __init__(self, cfg: Any, schedule: Mapping[str, Any] | None = None) -> None:
        self._lock = threading.Lock()
        if schedule is None:
            self.schedule = load_schedule()
        else:
            self.schedule = merge_schedule(OBJECTIVE_SCHEDULE, schedule, "argument")
        self._enabled = True
        self._leads: tuple[int, ...] = DEFAULT_LEADS_S
        self._error_logged_at = -math.inf
        self.apply_config(cfg)
        self._clear()

    # -- public -------------------------------------------------------------------------

    def apply_config(self, cfg: Any) -> None:
        """Take new settings (``objective_timers``, ``objective_lead_s``) into account."""
        try:
            enabled = getattr(cfg, "objective_timers", True)
            with self._lock:
                self._enabled = enabled if isinstance(enabled, bool) else True
                self._leads = _leads_from(cfg)
        except Exception:
            log.exception("ObjectiveTimers.apply_config failed")

    def update(self, game: Any, t: float) -> list[Alert]:
        """Process a Live Client snapshot (``None`` outside a game) at monotonic time ``t``.

        Returns the announcements to make now (at most one). Never raises.
        """
        try:
            with self._lock:
                return self._update_locked(game, t)
        except Exception:
            now = time.monotonic()
            if now - self._error_logged_at > 60.0:
                self._error_logged_at = now
                log.exception("ObjectiveTimers.update failed")
            return []

    def states(self) -> list[ObjectiveState]:
        """Current objectives for the HUD (spawned or upcoming; gone ones omitted), fixed order."""
        with self._lock:
            return [copy.copy(s) for s in self._states]

    def reset(self) -> None:
        """Forget everything (new game)."""
        with self._lock:
            self._clear()

    @property
    def game_time(self) -> float | None:
        """Extrapolated match clock at the last update (None before any game)."""
        return self._game_time

    # -- internals ----------------------------------------------------------------------

    def _clear(self) -> None:
        self._events: dict[tuple, _KillEvent] = {}
        self._slots: dict[str, _Slot] = build_slots(self.schedule, [])
        self._states: list[ObjectiveState] = []
        self._done: set[tuple] = set()
        self._last_emit_t: float | None = None
        self._last_raw_time: float | None = None
        self._roster: tuple = ()
        self._game_time: float | None = None
        self._events_sig: tuple | None = None

    def _clock(self, game: Any, t: float) -> float | None:
        try:
            raw = float(game.game_time)
        except (TypeError, ValueError, AttributeError):
            return None
        if not math.isfinite(raw) or raw < 0:
            return None
        g = raw
        try:
            dt = float(t) - float(game.fetched_at)
            if math.isfinite(dt) and 0.0 <= dt <= MAX_EXTRAPOLATION_S:
                g = raw + dt
        except (TypeError, ValueError, AttributeError):
            pass
        return g

    def _new_game_check(self, game: Any) -> None:
        raw = float(game.game_time)
        roster = _roster_signature(game)
        last = self._last_raw_time
        if last is not None and raw < last - GAME_TIME_BACK_RESET_S:
            log.info("Objective timers: game clock went back (%.0f -> %.0f s), reset", last, raw)
            self._clear()
        elif self._roster and roster and roster != self._roster:
            log.info("Objective timers: new roster, reset")
            self._clear()
        prev = self._last_raw_time            # None after a reset
        self._last_raw_time = raw if prev is None else max(raw, prev)
        if roster:
            self._roster = roster

    def _ingest_events(self, game: Any) -> None:
        events = getattr(game, "events", None)
        if not isinstance(events, list):
            return
        sig = (id(events), len(events))
        if sig == self._events_sig:
            return                              # same snapshot as the last tick
        self._events_sig = sig
        lookup: dict[str, str] | None = None
        fresh: dict[tuple, _KillEvent] = {}
        for raw in events[-MAX_EVENTS:]:
            if not isinstance(raw, Mapping) or raw.get("EventName") not in KILL_EVENTS:
                continue
            if lookup is None:
                lookup = _team_lookup(game)
            ev = _parse_kill(raw, lookup)
            if ev is not None and ev.uid not in fresh:
                fresh[ev.uid] = ev
        if not fresh:
            return
        renumbered = any(uid in self._events and self._events[uid].content != ev.content
                         for uid, ev in fresh.items())
        if renumbered:
            log.info("Objective timers: event ids reused for other events, using the new list")
            merged = fresh
        else:
            merged = dict(self._events)
            for uid, ev in fresh.items():
                old = merged.get(uid)
                if old is None or (old.team is None and ev.team is not None):
                    merged[uid] = ev
        if merged.keys() != self._events.keys() or renumbered:
            if len(merged) > MAX_EVENTS:
                keep = sorted(merged.values(), key=lambda e: e.time)[-MAX_EVENTS:]
                merged = {e.uid: e for e in keep}
            self._events = merged
            self._slots = build_slots(self.schedule, self._events.values())

    def _update_locked(self, game: Any, t: float) -> list[Alert]:
        if game is None:
            return []
        g = self._clock(game, t)
        if g is None:
            return []
        self._new_game_check(game)
        self._ingest_events(game)
        self._game_time = g
        rift = _is_rift(game)
        self._states = self._build_states(g) if rift else []
        due = self._due_announcements(g)
        announce = self._enabled and bool(self._leads) and rift
        if not due:
            return []
        if not announce:
            self._done.update(a.done_key for a in due)   # no backlog when re-enabled
            return []
        now = float(t) if isinstance(t, (int, float)) and math.isfinite(float(t)) else 0.0
        last = self._last_emit_t
        if last is not None and last <= now < last + ANNOUNCE_SPACING_S:
            return []
        best = min(due, key=lambda a: a.remaining)
        self._done.add(best.done_key)
        self._last_emit_t = now
        seconds = _spoken_seconds(best.lead, best.remaining)
        text = announcement_text(best.kind, seconds)
        log.info("Objective announcement: %s (game time %.0f s)", text, g)
        return [Alert(kind=_OBJECTIVE_SOON, level=Level.INFO, text=text,
                      key=f"objective_soon:{best.kind}:{best.lead}", t=now, alias=None)]

    def _slot_alive(self, slot: _Slot, g: float) -> tuple[bool, bool]:
        """(present, alive) for a slot at game time ``g``."""
        if slot.next_spawn is None:
            return False, False
        despawn = _num(self.schedule.get(slot.key), "despawn")
        if despawn is not None and (g >= despawn or slot.next_spawn >= despawn):
            return False, False
        return True, g >= slot.next_spawn

    def _build_states(self, g: float) -> list[ObjectiveState]:
        out: list[ObjectiveState] = []
        for key in SLOT_ORDER:
            slot = self._slots.get(key)
            if slot is None:
                continue
            present, alive = self._slot_alive(slot, g)
            if not present or slot.next_spawn is None:
                continue
            out.append(ObjectiveState(
                name=NAMES_FR.get(slot.kind, slot.kind), next_spawn=slot.next_spawn, alive=alive,
                source=slot.source, key=slot.kind, remaining=0.0 if alive else slot.next_spawn - g))
        return out

    def _due_announcements(self, g: float) -> list[_Announcement]:
        due: list[_Announcement] = []
        leads = self._leads
        for key in SLOT_ORDER:
            slot = self._slots.get(key)
            if slot is None:
                continue
            present, alive = self._slot_alive(slot, g)
            if not present or alive or slot.next_spawn is None:
                continue
            remaining = slot.next_spawn - g
            if remaining < MIN_ANNOUNCE_REMAINING_S:
                continue
            spawn_id = round(slot.next_spawn, 1)
            for i, lead in enumerate(leads):
                done_key = (slot.key, slot.kind, spawn_id, lead)
                if done_key in self._done or remaining > lead:
                    continue
                smaller = leads[i + 1] if i + 1 < len(leads) else None
                if lead - remaining > _grace(lead) or (smaller is not None and remaining <= smaller):
                    self._done.add(done_key)       # missed (clock jump) or superseded
                    continue
                due.append(_Announcement(kind=slot.kind, lead=lead, remaining=remaining, done_key=done_key))
        self._prune_done(g)
        return due

    def _prune_done(self, g: float) -> None:
        if len(self._done) > 64:
            self._done = {k for k in self._done if k[2] >= g - 60.0}
