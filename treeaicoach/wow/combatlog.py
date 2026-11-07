"""WoW combat log: live tail of ``Logs/WoWCombatLog.txt`` + a tolerant line parser.

The game writes this file itself when the player types ``/combatlog`` (exactly what Warcraft Logs
uses): reading it is allowed by Blizzard. Nothing here touches the game process.

Supported line shapes (Classic Era / Forever and Retail):

* ``11/7 21:14:03.123  SPELL_DAMAGE,<base 8>,<prefix>,[advanced 16|17],<suffix>``
* ``11/7/2026 21:14:03.1234-5  SPELL_DAMAGE,...`` (newer timestamp with year and UTC offset)
* header ``COMBAT_LOG_VERSION,9,ADVANCED_LOG_ENABLED,1,BUILD_VERSION,1.15.4,PROJECT_ID,2``

Advanced logging (``/console advancedCombatLogging 1``) adds the HP of the unit described
(``info_guid``): used for "low HP" callouts. Without it everything else still works.

Public API: :func:`parse_line` -> :class:`CombatEvent` | None, :class:`CombatLogTail` (poll based,
handles truncation / rotation / partial lines), :class:`CombatLogWatcher` (thread calling a
callback). Never raises in the app path.
"""
from __future__ import annotations

import csv
import datetime as _dt
import logging
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# --- unit flags (COMBATLOG_OBJECT_*) -------------------------------------------------------------
AFFIL_MINE = 0x1
AFFIL_PARTY = 0x2
AFFIL_RAID = 0x4
AFFIL_OUTSIDER = 0x8
REACT_FRIENDLY = 0x10
REACT_NEUTRAL = 0x20
REACT_HOSTILE = 0x40
CONTROL_PLAYER = 0x100
TYPE_PLAYER = 0x400
TYPE_NPC = 0x800
TYPE_PET = 0x1000
TYPE_GUARDIAN = 0x2000

NO_GUID = "0000000000000000"

# prefix name -> number of prefix params
_PREFIXES: tuple[tuple[str, int], ...] = (
    ("SPELL_PERIODIC_", 3), ("SPELL_BUILDING_", 3), ("SPELL_", 3), ("RANGE_", 3),
    ("SWING_", 0), ("ENVIRONMENTAL_", 1),
)

_TS_RE = re.compile(
    r"^(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\s+(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d{1,4}))?(?:[-+]\d+)?$")

ENV_FR = {"DROWNING": "noyade", "FATIGUE": "fatigue", "FALLING": "chute", "LAVA": "lave",
          "FIRE": "feu", "SLIME": "vase"}


@dataclass
class CombatEvent:
    """One parsed line. ``amount`` is the damage/heal amount (0 otherwise)."""

    t: float                       # epoch seconds (local clock of the log)
    event: str                     # e.g. "SPELL_DAMAGE"
    src_guid: str = ""
    src_name: str = ""
    src_flags: int = 0
    dst_guid: str = ""
    dst_name: str = ""
    dst_flags: int = 0
    spell_id: int = 0
    spell_name: str = ""
    school: int = 0
    amount: int = 0
    overkill: int = 0
    critical: bool = False
    env_type: str = ""             # ENVIRONMENTAL_*: DROWNING, FATIGUE, FALLING, LAVA…
    aura_type: str = ""            # SPELL_AURA_*: BUFF / DEBUFF
    extra_spell_id: int = 0        # SPELL_INTERRUPT / SPELL_AURA_BROKEN_SPELL / DISPEL
    extra_spell_name: str = ""
    miss_type: str = ""            # *_MISSED: DODGE, PARRY, MISS, IMMUNE…
    info_guid: str = ""            # advanced block: unit whose HP is given
    hp: int = -1
    max_hp: int = -1
    raw: list[str] = field(default_factory=list, repr=False)

    # --- helpers --------------------------------------------------------------------------------
    @property
    def is_damage(self) -> bool:
        return self.event.endswith("_DAMAGE") or self.event == "DAMAGE_SPLIT"

    @property
    def is_heal(self) -> bool:
        return self.event.endswith("_HEAL")

    @property
    def hp_pct(self) -> float | None:
        if self.max_hp > 0 and self.hp >= 0:
            return 100.0 * self.hp / self.max_hp
        return None


def is_mine(flags: int) -> bool:
    return bool(flags & AFFIL_MINE)


def is_me(flags: int, guid: str = "") -> bool:
    return bool(flags & AFFIL_MINE) and bool(flags & TYPE_PLAYER) and not guid.startswith("Pet-")


def is_my_pet(flags: int, guid: str = "") -> bool:
    return bool(flags & AFFIL_MINE) and (bool(flags & (TYPE_PET | TYPE_GUARDIAN)) or guid.startswith("Pet-"))


def is_party(flags: int) -> bool:
    return bool(flags & (AFFIL_PARTY | AFFIL_RAID)) and bool(flags & TYPE_PLAYER)


def is_friendly_side(flags: int) -> bool:
    """Me, my pet, party/raid members and their pets."""
    return bool(flags & (AFFIL_MINE | AFFIL_PARTY | AFFIL_RAID))


def is_hostile(flags: int) -> bool:
    return bool(flags & REACT_HOSTILE) or (bool(flags & REACT_NEUTRAL) and bool(flags & TYPE_NPC)
                                           and not flags & (AFFIL_MINE | AFFIL_PARTY | AFFIL_RAID))


def short_name(name: str) -> str:
    """'Nom-Royaume' -> 'Nom' (players); NPC names unchanged."""
    if not name or name == "nil":
        return ""
    return name.split("-", 1)[0] if "-" in name and " " not in name else name


def _int(s: Any, default: int = 0) -> int:
    try:
        txt = str(s).strip()
        if txt.lower().startswith("0x"):
            return int(txt, 16)
        return int(float(txt))
    except (TypeError, ValueError, OverflowError):
        return default


def _unq(s: str) -> str:
    s = s.strip()
    if s == "nil":
        return ""
    return s


def _guid_like(s: str) -> bool:
    return s == NO_GUID or ("-" in s and s[:1].isalpha())


def parse_timestamp(ts: str, year: int | None = None) -> float | None:
    m = _TS_RE.match(ts.strip())
    if not m:
        return None
    mo, d, y, hh, mm, ss, frac = m.groups()
    try:
        yy = int(y) if y else (year or _dt.date.today().year)
        us = int((frac or "0").ljust(6, "0")[:6])
        return _dt.datetime(yy, int(mo), int(d), int(hh), int(mm), int(ss), us).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


@dataclass
class LogHeader:
    version: int = 0
    advanced: bool | None = None
    build: str = ""
    project_id: int = 0


def parse_header(fields: list[str]) -> LogHeader:
    h = LogHeader()
    it = iter(fields)
    for k in it:
        v = next(it, "")
        if k == "COMBAT_LOG_VERSION":
            h.version = _int(v)
        elif k == "ADVANCED_LOG_ENABLED":
            h.advanced = v.strip() == "1"
        elif k == "BUILD_VERSION":
            h.build = v.strip()
        elif k == "PROJECT_ID":
            h.project_id = _int(v)
    return h


def _split(line: str) -> tuple[str, list[str]] | None:
    line = line.rstrip("\r\n")
    i = line.find("  ")
    if i <= 0:
        return None
    ts, body = line[:i], line[i + 2:].strip()
    if not body:
        return None
    try:
        fields = next(csv.reader([body], quotechar='"', skipinitialspace=False))
    except (csv.Error, StopIteration):
        return None
    return ts, fields


def _fill_damage(ev: CombatEvent, f: list[str], start: int, swing: bool) -> None:
    """Damage suffix, located from the END of the line (robust to the advanced block length and to
    the optional ``baseAmount`` column of newer builds)."""
    n = len(f)
    tail = 0
    while tail < 4 and n - 1 - tail >= start and f[n - 1 - tail].strip() in ("nil", "1"):
        tail += 1
    tail = max(tail, 3)
    overkill_i = n - tail - 5
    if overkill_i - 1 < start:
        return
    remaining = n - start - tail          # amount,[base],overkill,school,resisted,blocked,absorbed
    has_base = False
    adv = start < n and _guid_like(f[start].strip())
    if adv:
        for base in (True, False):
            a = remaining - (7 if base else 6)
            if a in (16, 17):
                has_base = base
                _fill_advanced(ev, f, start, a)
                break
    else:
        has_base = remaining == 7
    amount_i = overkill_i - (2 if has_base else 1)
    if amount_i < start:
        amount_i = overkill_i - 1
    ev.amount = max(0, _int(f[amount_i]))
    ev.overkill = max(0, _int(f[overkill_i], -1))
    crit_i = n - tail
    ev.critical = crit_i < n and f[crit_i].strip() == "1"


def _fill_advanced(ev: CombatEvent, f: list[str], start: int, length: int) -> None:
    if start + 3 >= len(f):
        return
    ev.info_guid = f[start].strip()
    hp, mx = _int(f[start + 2], -1), _int(f[start + 3], -1)
    if mx > 0 and 0 <= hp <= mx * 2:
        ev.hp, ev.max_hp = hp, mx


def parse_fields(ts: float, f: list[str]) -> CombatEvent | None:
    """Parse the CSV fields of one event line (``f[0]`` is the event name)."""
    if not f:
        return None
    name = f[0].strip()
    ev = CombatEvent(t=ts, event=name, raw=f)
    if len(f) < 9:
        return ev  # e.g. ZONE_CHANGE, ENCOUNTER_START: keep the name only
    ev.src_guid, ev.src_name, ev.src_flags = f[1].strip(), _unq(f[2]), _int(f[3])
    ev.dst_guid, ev.dst_name, ev.dst_flags = f[5].strip(), _unq(f[6]), _int(f[7])
    i = 9
    if name in ("UNIT_DIED", "UNIT_DESTROYED", "UNIT_DISSIPATES", "PARTY_KILL"):
        return ev
    if name == "DAMAGE_SPLIT" or name == "DAMAGE_SHIELD":
        pre = 3
    else:
        pre = next((n for p, n in _PREFIXES if name.startswith(p)), 0)
    if pre == 3 and len(f) >= i + 3:
        ev.spell_id, ev.spell_name, ev.school = _int(f[i]), _unq(f[i + 1]), _int(f[i + 2])
    elif pre == 1 and len(f) > i:
        ev.env_type = f[i].strip().upper()
    i += pre
    if name.endswith("_DAMAGE") or name in ("DAMAGE_SPLIT", "DAMAGE_SHIELD"):
        _fill_damage(ev, f, i, name.startswith("SWING_"))
    elif name.endswith("_HEAL"):
        # amount, [baseAmount], overheal, absorbed, critical
        n = len(f)
        adv = i < n and _guid_like(f[i].strip())
        rem = n - i
        if adv:
            for a in (17, 16):
                if rem - a in (4, 5):
                    _fill_advanced(ev, f, i, a)
                    ev.amount = max(0, _int(f[i + a]))
                    break
        elif i < n:
            ev.amount = max(0, _int(f[i]))
        ev.critical = f[-1].strip() == "1"
    elif name.endswith("_CAST_SUCCESS"):
        if i < len(f) and _guid_like(f[i].strip()) and len(f) - i in (16, 17):
            _fill_advanced(ev, f, i, len(f) - i)
    elif name.endswith("_MISSED"):
        if i < len(f):
            ev.miss_type = f[i].strip().upper()
    elif name.startswith("SPELL_AURA_") or name.endswith("_AURA_APPLIED") or name.endswith("_AURA_REMOVED"):
        if name in ("SPELL_AURA_BROKEN_SPELL",) and len(f) >= i + 2:
            ev.extra_spell_id, ev.extra_spell_name = _int(f[i]), _unq(f[i + 1])
            ev.aura_type = f[i + 3].strip().upper() if len(f) > i + 3 else ""
        elif i < len(f):
            ev.aura_type = f[i].strip().upper()
    elif name.endswith(("_INTERRUPT", "_DISPEL", "_STOLEN", "_DISPEL_FAILED")):
        if len(f) >= i + 2:
            ev.extra_spell_id, ev.extra_spell_name = _int(f[i]), _unq(f[i + 1])
    return ev


def parse_line(line: str, year: int | None = None) -> CombatEvent | LogHeader | None:
    """One raw log line -> :class:`CombatEvent`, :class:`LogHeader` (version line) or None."""
    try:
        sp = _split(line)
        if sp is None:
            return None
        ts_txt, f = sp
        if f and f[0].strip() == "COMBAT_LOG_VERSION":
            return parse_header(f)
        ts = parse_timestamp(ts_txt, year)
        if ts is None:
            return None
        return parse_fields(ts, f)
    except Exception as e:  # never raise on a weird line
        log.debug("combat log line skipped (%s): %.120r", e, line)
        return None


def parse_lines(lines: Any, year: int | None = None) -> list[CombatEvent]:
    out: list[CombatEvent] = []
    for ln in lines:
        ev = parse_line(ln, year)
        if isinstance(ev, CombatEvent):
            out.append(ev)
    return out


def default_log_path(wow_dir: str | os.PathLike | None) -> Path | None:
    """``<install>/_classic_era_/Logs/WoWCombatLog.txt`` style path from a game folder."""
    if not wow_dir:
        return None
    try:
        p = Path(wow_dir)
        if p.name.lower() == "logs":
            return p / "WoWCombatLog.txt"
        return p / "Logs" / "WoWCombatLog.txt"
    except (TypeError, ValueError):
        return None


class CombatLogTail:
    """Poll-based tail. ``poll()`` returns the events appended since the previous call.

    Starts at the END of an existing file (old fights are not replayed) unless ``from_start``.
    Handles: file created later, truncation / new file (size shrinks or inode changes), partial last
    line (kept until its newline arrives), invalid UTF-8 (replaced), huge bursts (``max_bytes``)."""

    def __init__(self, path: str | os.PathLike, from_start: bool = False, max_bytes: int = 4 << 20) -> None:
        self.path = Path(path)
        self.from_start = from_start
        self.max_bytes = max(4096, int(max_bytes))
        self._pos: int | None = None
        self._ident: tuple[int, int] | None = None
        self._partial = b""
        self.header: LogHeader | None = None
        self.lines_read = 0

    def _stat(self) -> os.stat_result | None:
        try:
            return self.path.stat()
        except OSError:
            return None

    def read_lines(self) -> list[str]:
        st = self._stat()
        if st is None:
            self._pos, self._ident, self._partial = None, None, b""
            self.from_start = True  # it will be a NEW file (/combatlog typed later): read it whole
            return []
        ident = (st.st_ino, st.st_dev)
        if self._pos is None:
            self._pos = 0 if self.from_start else st.st_size
            self._ident = ident
            self.from_start = True  # a file created later is read from its start
        elif st.st_size < self._pos or (self._ident != ident and st.st_ino):
            self._pos, self._partial, self._ident = 0, b"", ident
        if st.st_size == self._pos:
            return []
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self._pos)
                data = fh.read(self.max_bytes)
        except OSError as e:
            log.debug("combat log read failed: %s", e)
            return []
        self._pos += len(data)
        data = self._partial + data
        cut = data.rfind(b"\n")
        if cut < 0:
            self._partial = data[-65536:]
            return []
        self._partial = data[cut + 1:]
        text = data[:cut].decode("utf-8", errors="replace")
        lines = [ln for ln in text.split("\n") if ln.strip()]
        self.lines_read += len(lines)
        return lines

    def poll(self) -> list[CombatEvent]:
        out: list[CombatEvent] = []
        for ln in self.read_lines():
            ev = parse_line(ln)
            if isinstance(ev, LogHeader):
                self.header = ev
            elif ev is not None:
                out.append(ev)
        return out


class CombatLogWatcher:
    """Background thread: tails the log every ``interval`` s and calls ``on_events(list)``."""

    def __init__(self, path: str | os.PathLike, on_events: Callable[[list[CombatEvent]], None],
                 interval: float = 0.25, from_start: bool = False, idle_calls: bool = False) -> None:
        self.idle_calls = idle_calls  # also call on_events([]) when nothing new (lets callers flush)
        self.tail = CombatLogTail(path, from_start=from_start)
        self.on_events = on_events
        self.interval = max(0.05, float(interval))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="wow-combatlog", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                evs = self.tail.poll()
                if evs or self.idle_calls:
                    self.on_events(evs)
            except Exception as e:
                log.debug("combat log watcher: %s", e)
            self._stop.wait(self.interval)
