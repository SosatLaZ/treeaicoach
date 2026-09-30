"""Alert types, short French voice phrases and anti-spam throttling.

* :class:`AlertKind` / :class:`Level` / :class:`Alert` are the data exchanged between the
  analysers (``gank.py``, ``objectives.py``, ``reminders.py``), the engine, the voice and the UI.
* :func:`phrase` builds the sentence spoken by the voice. Sentences are deliberately SHORT
  (about 1.8 s at SAPI rate +2 for gank alerts): the player is in a fight and must react fast.
* :class:`AlertThrottler` turns the raw alerts produced at every analysis tick (the analysers
  re-emit an alert for as long as its condition holds) into at most ONE message per tick:

  - cooldown per ``Alert.key`` depending on the level (INFO 30 s, WARNING 8 s, DANGER 6 s;
    ``JUNGLER_WHERE`` 3 s);
  - escalation (a more severe alert than the last one said for the same key, or a DANGER
    about a champion whose last announcement was a WARNING) passes immediately;
  - highest level wins, then the most recent (``Alert.t``), then the kind priority;
  - global minimum gap between two messages, except for DANGER (and for ``JUNGLER_WHERE`` /
    ``DEATH_RECAP``, answers the player explicitly waits for);
  - robust to time going backwards (state reset when the clock jumps back) and huge jumps.

Pure module (stdlib only), importable everywhere.
"""

from __future__ import annotations

import logging
import math
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Any

log = logging.getLogger(__name__)


class AlertKind(str, Enum):
    """Kind of alert (the value is a stable lower-case identifier, used in JSON records)."""

    JUNGLER_APPROACH = "jungler_approach"
    ROAM_APPROACH = "roam_approach"
    COLLAPSE = "collapse"
    JUNGLER_SPOTTED = "jungler_spotted"
    LANER_MIA = "laner_mia"
    # v1.1 (ARCHITECTURE.md §6.1)
    OBJECTIVE_SOON = "objective_soon"
    RECALL_GOLD = "recall_gold"
    CONTROL_WARD = "control_ward"
    JUNGLER_WHERE = "jungler_where"
    DEATH_RECAP = "death_recap"

    @classmethod
    def _missing_(cls, value: object) -> AlertKind | None:
        """Accept names and values case-insensitively (``"COLLAPSE"``, ``"Collapse"``...)."""
        if isinstance(value, str):
            v = value.strip().lower()
            for member in cls:
                if member.value == v or member.name.lower() == v:
                    return member
        return None


class Level(IntEnum):
    """Severity of an alert (also the ``level`` passed to ``VoiceEngine.say``)."""

    INFO = 0
    WARNING = 1
    DANGER = 2

    @classmethod
    def coerce(cls, value: Any, default: Level | None = None) -> Level:
        """Best-effort conversion (int, float, name such as ``"danger"``) clamped to INFO..DANGER."""
        fallback = cls.WARNING if default is None else default
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            name = value.strip().upper()
            if name in cls.__members__:
                return cls[name]
            try:
                value = float(name)
            except ValueError:
                return fallback
        try:
            f = float(value)
        except (TypeError, ValueError, OverflowError):
            return fallback
        if math.isnan(f):
            return fallback
        if math.isinf(f):
            return cls.DANGER if f > 0 else cls.INFO
        return cls(int(min(max(round(f), cls.INFO), cls.DANGER)))


@dataclass
class Alert:
    """One alert. ``key`` identifies "the same alert" for cooldowns (e.g. ``"jungler_approach:LeeSin"``)."""

    kind: AlertKind
    level: Level
    text: str
    key: str
    t: float
    alias: str | None = None

    def __post_init__(self) -> None:
        # Lenient normalisation (never raises): analysers may pass plain str / int.
        if not isinstance(self.kind, AlertKind):
            try:
                self.kind = AlertKind(self.kind)
            except ValueError:
                pass
        self.level = Level.coerce(self.level)
        if not isinstance(self.text, str):
            self.text = "" if self.text is None else str(self.text)
        if not isinstance(self.key, str):
            self.key = "" if self.key is None else str(self.key)

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly representation (used by the game recorder / UI log)."""
        kind = self.kind.value if isinstance(self.kind, AlertKind) else str(self.kind)
        return {
            "kind": kind,
            "level": int(self.level),
            "text": self.text,
            "key": self.key,
            "t": self.t,
            "alias": self.alias,
        }


# --------------------------------------------------------------------------------------
# Phrases
# --------------------------------------------------------------------------------------

NAME_MAX_LEN = 32          # longest champion name is ~16 chars; guard against garbage
ZONE_MAX_LEN = 48
_WS_RE = re.compile(r"\s+")
_EDGE_PUNCT = " \t\r\n.,;:!? "


def _clean(value: Any, max_len: int) -> str | None:
    """Single-line trimmed text without edge punctuation, or None if empty / not text-like."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        s = value.value if isinstance(value, Enum) and isinstance(value.value, str) else str(value)
    except Exception:  # pathological __str__
        return None
    s = _WS_RE.sub(" ", s).strip(_EDGE_PUNCT)
    if not s or s.lower() in ("none", "null", "nan"):
        return None
    if len(s) > max_len:
        s = s[:max_len].rstrip(_EDGE_PUNCT)
    return s or None


def _as_count(value: Any, upper: int = 100_000) -> int:
    """Non-negative int (None / NaN / garbage -> 0), clamped to ``upper``."""
    if value is None or isinstance(value, bool):
        return 0
    try:
        f = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    if math.isnan(f) or f <= 0:
        return 0
    return int(min(f, upper))


def _cap(s: str) -> str:
    """Upper-case the first character (the voice does not care, the UI log does)."""
    return s[:1].upper() + s[1:] if s else s


def _seconds_fr(n: int) -> str:
    """``"une seconde"`` / ``"23 secondes"`` / ``"une minute"`` / ``"1 minute 30"``."""
    if n < 60:
        return "une seconde" if n == 1 else f"{n} secondes"
    m, s = divmod(n, 60)
    unit = "minute" if m == 1 else "minutes"
    head = "une minute" if m == 1 else f"{m} {unit}"
    return head if s == 0 else f"{m} {unit} {s}"


def _jungler_approach(level: Level, champ: str | None, zone: str | None, n: int) -> str:
    if level >= Level.DANGER:
        return f"Gank ! {champ}, recule !" if champ else "Gank du jungler, recule !"
    if level == Level.WARNING:
        return f"Attention, {champ} approche." if champ else "Attention, le jungler ennemi approche."
    return f"{champ} rôde près de toi." if champ else "Le jungler ennemi rôde près de toi."


def _roam_approach(level: Level, champ: str | None, zone: str | None, n: int) -> str:
    who = champ or "un ennemi"
    if level >= Level.DANGER:
        return f"Gank ! {_cap(who)} arrive, recule !"
    if level == Level.WARNING:
        return f"{_cap(who)} arrive vers toi."
    return f"{_cap(who)} rôde vers toi."


def _collapse(level: Level, champ: str | None, zone: str | None, n: int) -> str:
    if n >= 2:
        if level >= Level.DANGER:
            return f"Danger, {n} ennemis arrivent, recule !"
        if level == Level.WARNING:
            return f"Attention, {n} ennemis approchent."
        return f"{n} ennemis près de toi."
    if n == 1:
        who = champ or "un ennemi"
        if level >= Level.DANGER:
            return f"Danger, {who} arrive, recule !"
        if level == Level.WARNING:
            return f"Attention, {who} approche."
        return f"{_cap(who)} près de toi."
    # count unknown
    if level >= Level.DANGER:
        return "Danger, des ennemis arrivent, recule !"
    if level == Level.WARNING:
        return "Attention, des ennemis approchent."
    return "Des ennemis près de toi."


def _jungler_spotted(level: Level, champ: str | None, zone: str | None, n: int) -> str:
    where = f"vu {zone}" if zone else "repéré"
    if level >= Level.DANGER:
        return f"Danger, jungler ennemi {where} !"
    if level == Level.WARNING:
        return f"Attention, jungler ennemi {where}."
    return f"Jungler ennemi {where}."


def _laner_mia(level: Level, champ: str | None, zone: str | None, n: int) -> str:
    if level >= Level.DANGER:
        return f"{champ} a disparu, recule !" if champ else "Ton adversaire a disparu, recule !"
    if level == Level.WARNING:
        return f"Attention, {champ} a disparu." if champ else "Attention, ton adversaire a disparu."
    return f"{champ} a disparu, prudence." if champ else "Ton adversaire a disparu, prudence."


def _objective_soon(level: Level, champ: str | None, zone: str | None, n: int) -> str:
    name = _cap(champ) if champ else "Objectif"
    if n > 0:
        return f"{name} dans {_seconds_fr(n)}."
    return f"{name} bientôt."


def _recall_gold(level: Level, champ: str | None, zone: str | None, n: int) -> str:
    gold = (n // 50) * 50            # "mille quatre cent cinquante" is shorter than the exact value
    if gold > 0:
        return f"Tu as {gold} pièces d'or, pense à rentrer."
    return "Pense à rentrer acheter."


def _control_ward(level: Level, champ: str | None, zone: str | None, n: int) -> str:
    return "Pense à acheter une balise de contrôle."


def _jungler_where(level: Level, champ: str | None, zone: str | None, n: int) -> str:
    who = champ or "Le jungler ennemi"
    if not zone:
        return f"Position de {champ} inconnue." if champ else "Position du jungler ennemi inconnue."
    if n <= 0:
        return f"{_cap(who)} est visible {zone}."
    if n >= 60:
        return f"{_cap(who)} vu {zone} il y a plus d'une minute."
    return f"{_cap(who)} vu {zone} il y a {_seconds_fr(n)}."


def _death_recap(level: Level, champ: str | None, zone: str | None, n: int) -> str:
    if n >= 2:
        return f"Mort face à {n} ennemis."
    if n == 1:
        return f"Mort face à {champ}." if champ else "Mort face à un ennemi."
    return "Tu es mort."


_BUILDERS = {
    AlertKind.JUNGLER_APPROACH: _jungler_approach,
    AlertKind.ROAM_APPROACH: _roam_approach,
    AlertKind.COLLAPSE: _collapse,
    AlertKind.JUNGLER_SPOTTED: _jungler_spotted,
    AlertKind.LANER_MIA: _laner_mia,
    AlertKind.OBJECTIVE_SOON: _objective_soon,
    AlertKind.RECALL_GOLD: _recall_gold,
    AlertKind.CONTROL_WARD: _control_ward,
    AlertKind.JUNGLER_WHERE: _jungler_where,
    AlertKind.DEATH_RECAP: _death_recap,
}
_GENERIC = {Level.INFO: "Attention.", Level.WARNING: "Attention !", Level.DANGER: "Danger, recule !"}


def phrase(kind: AlertKind, level: Level, champ: str | None, zone_label: str | None = None,
           count: int = 0) -> str:
    """Short French sentence for an alert. Never raises.

    ``champ`` is the (localised) champion name, or ``None`` when unknown ("un ennemi");
    for ``OBJECTIVE_SOON`` it is the objective name ("Dragon"). ``zone_label`` is a
    ``geometry.zone_label_fr`` phrase ("en haut", "dans la rivière du bas").
    ``count`` is the number of enemies (COLLAPSE, DEATH_RECAP), seconds (OBJECTIVE_SOON,
    JUNGLER_WHERE) or gold (RECALL_GOLD).
    """
    lvl = Level.coerce(level)
    try:
        k = kind if isinstance(kind, AlertKind) else AlertKind(kind)
    except ValueError:
        log.warning("phrase(): unknown alert kind %r", kind)
        return _GENERIC[lvl]
    try:
        name = _clean(champ, NAME_MAX_LEN)
        zone = _clean(zone_label, ZONE_MAX_LEN)
        n = _as_count(count)
        text = _BUILDERS[k](lvl, name, zone, n)
        return _WS_RE.sub(" ", text).strip() or _GENERIC[lvl]
    except Exception:  # defensive: the voice must always get something
        log.exception("phrase(%r, %r) failed", kind, level)
        return _GENERIC[lvl]


def alert_key(kind: AlertKind | str, who: str | None = None) -> str:
    """Conventional throttling key: ``"<kind>:<alias or anonymous id>"`` (``"<kind>"`` if no one)."""
    try:
        k = kind.value if isinstance(kind, AlertKind) else AlertKind(kind).value
    except ValueError:
        k = str(kind)
    w = _clean(who, 64)
    return f"{k}:{w}" if w else k


def make_alert(kind: AlertKind, level: Level | int, t: float, champ: str | None = None,
               alias: str | None = None, zone_label: str | None = None, count: int = 0,
               key: str | None = None) -> Alert:
    """Build an :class:`Alert` with its phrase and the conventional key (see :func:`alert_key`)."""
    lvl = Level.coerce(level)
    return Alert(
        kind=kind,
        level=lvl,
        text=phrase(kind, lvl, champ, zone_label, count),
        key=key if key else alert_key(kind, alias or champ),
        t=t,
        alias=alias,
    )


# --------------------------------------------------------------------------------------
# Throttler
# --------------------------------------------------------------------------------------

COOLDOWN_S: dict[Level, float] = {Level.INFO: 30.0, Level.WARNING: 8.0, Level.DANGER: 6.0}
KIND_COOLDOWN_S: dict[AlertKind, float] = {AlertKind.JUNGLER_WHERE: 3.0}
# Answers the player waits for: never delayed by the global gap (they keep their key cooldown).
GAP_EXEMPT_KINDS: frozenset[AlertKind] = frozenset({AlertKind.JUNGLER_WHERE, AlertKind.DEATH_RECAP})
# Tie-break between alerts of the same level and time (first = preferred).
KIND_PRIORITY: tuple[AlertKind, ...] = (
    AlertKind.COLLAPSE,
    AlertKind.JUNGLER_APPROACH,
    AlertKind.ROAM_APPROACH,
    AlertKind.JUNGLER_WHERE,
    AlertKind.DEATH_RECAP,
    AlertKind.JUNGLER_SPOTTED,
    AlertKind.LANER_MIA,
    AlertKind.OBJECTIVE_SOON,
    AlertKind.RECALL_GOLD,
    AlertKind.CONTROL_WARD,
)
DEFAULT_MIN_GAP_S = 1.2
MAX_MIN_GAP_S = 30.0
BACKWARD_RESET_S = 1.0     # clock going back more than this -> new timeline, state reset
PRUNE_EVERY_S = 10.0       # housekeeping period of the per-key memory

_PRIORITY_RANK = {k: i for i, k in enumerate(KIND_PRIORITY)}
_KEEP_S = max(max(COOLDOWN_S.values()), max(KIND_COOLDOWN_S.values(), default=0.0)) + 1.0


def cooldown_for(alert: Alert) -> float:
    """Per-key cooldown (s) applying to ``alert``."""
    kind = alert.kind
    if isinstance(kind, AlertKind) and kind in KIND_COOLDOWN_S:
        return KIND_COOLDOWN_S[kind]
    return COOLDOWN_S[Level.coerce(alert.level)]


def _finite_or(value: Any, default: float) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return f if math.isfinite(f) else default


class AlertThrottler:
    """Anti-spam filter: at most one alert per tick, per-key cooldowns, global gap. Thread-safe."""

    def __init__(self, min_gap_s: float = DEFAULT_MIN_GAP_S) -> None:
        gap = _finite_or(min_gap_s, DEFAULT_MIN_GAP_S)
        self.min_gap_s: float = min(max(gap, 0.0), MAX_MIN_GAP_S)
        self._lock = threading.Lock()
        self._by_key: dict[str, tuple[float, Level]] = {}
        self._by_alias: dict[str, tuple[float, Level]] = {}
        self._last_emit_t: float | None = None
        self._last_tick: float | None = None
        self._last_prune: float = -math.inf

    # -- public -------------------------------------------------------------------------

    def filter(self, alerts: Iterable[Alert] | None, t: float) -> list[Alert]:
        """Return ``[]`` or ``[the one alert to say now]`` and remember it. Never raises."""
        try:
            with self._lock:
                return self._filter_locked(alerts, t)
        except Exception:  # defensive: the analysis loop must go on
            log.exception("AlertThrottler.filter failed")
            return []

    def reset(self) -> None:
        """Forget everything (new game)."""
        with self._lock:
            self._clear()
            self._last_tick = None

    # -- internals ----------------------------------------------------------------------

    def _clear(self) -> None:
        self._by_key.clear()
        self._by_alias.clear()
        self._last_emit_t = None
        self._last_prune = -math.inf

    def _now(self, t: Any) -> float:
        """Sanitised tick time; resets the state when the clock goes back significantly."""
        last = self._last_tick
        now = _finite_or(t, math.nan)
        if math.isnan(now):
            log.debug("AlertThrottler: invalid time %r, using the previous tick", t)
            now = last if last is not None else 0.0
        if last is not None and now < last:
            if last - now > BACKWARD_RESET_S:
                log.info("AlertThrottler: clock went back by %.1f s, state reset", last - now)
                self._clear()
            else:
                now = last          # small jitter: keep time monotonic
        self._last_tick = now
        return now

    def _prune(self, now: float) -> None:
        if now - self._last_prune < PRUNE_EVERY_S:
            return
        self._last_prune = now
        for table in (self._by_key, self._by_alias):
            stale = [k for k, (ts, _lvl) in table.items() if now - ts > _KEEP_S]
            for k in stale:
                del table[k]

    def _key_ok(self, a: Alert, now: float) -> bool:
        last = self._by_key.get(a.key)
        if last is None:
            return True
        last_t, last_level = last
        if now - last_t >= cooldown_for(a):
            return True
        if a.level > last_level:
            return True                         # escalation on the same key
        if a.level >= Level.DANGER and a.alias:
            seen = self._by_alias.get(a.alias)
            # the champion's latest announcement was a WARNING said after this key's DANGER
            if seen is not None and seen[1] < Level.DANGER and seen[0] >= last_t:
                return True
        return False

    def _gap_ok(self, a: Alert, now: float) -> bool:
        if a.level >= Level.DANGER or a.kind in GAP_EXEMPT_KINDS:
            return True
        if self._last_emit_t is None:
            return True
        return now - self._last_emit_t >= self.min_gap_s

    @staticmethod
    def _valid(a: Any) -> bool:
        return isinstance(a, Alert) and bool(a.text) and isinstance(a.key, str)

    def _filter_locked(self, alerts: Iterable[Alert] | None, t: float) -> list[Alert]:
        now = self._now(t)
        self._prune(now)
        if not alerts:
            return []
        best: Alert | None = None
        best_rank: tuple[int, float, int, int] | None = None
        for index, a in enumerate(alerts):
            if not self._valid(a):
                log.debug("AlertThrottler: ignoring invalid alert %r", a)
                continue
            if not (self._key_ok(a, now) and self._gap_ok(a, now)):
                continue
            rank = (
                int(a.level),
                _finite_or(a.t, -math.inf),
                -_PRIORITY_RANK.get(a.kind, len(_PRIORITY_RANK)),  # type: ignore[arg-type]
                index,                                              # later in the list = more recent
            )
            if best_rank is None or rank > best_rank:
                best, best_rank = a, rank
        if best is None:
            return []
        self._by_key[best.key] = (now, best.level)
        if best.alias:
            self._by_alias[best.alias] = (now, best.level)
        self._last_emit_t = now
        return [best]
