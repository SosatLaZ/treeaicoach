"""Alert types, short French voice phrases and anti-spam throttling.

* :class:`AlertKind` / :class:`Level` / :class:`Alert` are the data exchanged between the
  analysers (``gank.py``, ``objectives.py``, ``reminders.py``), the engine, the voice and the UI.
* :func:`phrase` builds the sentence spoken by the voice. Sentences are deliberately SHORT
  (about 1.8 s at SAPI rate +2 for gank alerts): the player is in a fight and must react fast.
  The v1.1 kinds (objectives, reminders, jungler position, death recap) may carry a free text.
* :class:`AlertThrottler` turns the raw alerts produced at every analysis tick (the analysers
  re-emit an alert for as long as its condition holds) into at most ONE message per tick:

  - cooldown per ``Alert.key`` depending on the level (INFO 30 s, WARNING 8 s, DANGER 6 s;
    ``JUNGLER_WHERE`` 3 s; gank kinds 12 s; ``JUNGLER_SPOTTED`` 45 s);
  - the same gank is not repeated for 12 s: a gank alert (``JUNGLER_APPROACH``,
    ``ROAM_APPROACH``, ``COLLAPSE``) whose ``members`` (the champions involved) were all already
    announced in a gank alert of at least the same level during the last 12 s is dropped, even
    under another key (e.g. "Gank bot : Lee Sin et Ahri !" then "Lee Sin arrive...");
  - escalation (a more severe alert than the last one said for the same key, or a DANGER
    about a champion whose last announcement was a WARNING) passes immediately;
  - highest level wins, then the most recent (``Alert.t``), then the kind priority;
  - global minimum gap between two messages, except for DANGER and gank WARNINGs (never delayed
    by another message: latency first) and for ``JUNGLER_WHERE`` / ``DEATH_RECAP``, answers the
    player explicitly waits for; at equal level a gank alert wins over any other kind;
  - a DANGER is not said within ``danger_gap_s`` (1.5 s) of the previous DANGER, so that two
    gank alerts raised on consecutive ticks do not cut each other off (the voice purges the
    current sentence for a DANGER);
  - an alert that was only held back by the global gap / the one-per-tick rule is kept for a
    few seconds (INFO 6 s, WARNING 1.5 s) and said as soon as possible, so that one-shot
    alerts (objective timer, jungler spotted...) are not lost;
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
    # v2: live macro coaching (coach.py), INFO only, free text
    MACRO_TIP = "macro_tip"
    # v2: praise (praise.py) and Tab scoreboard insights (scoreboard.py), INFO only, free text
    PRAISE = "praise"
    SCOREBOARD = "scoreboard"

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
    #: champions (aliases / anonymous track keys) involved in a gank alert, for the
    #: "same gank" repeat rule of the throttler
    members: tuple[str, ...] = ()

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
        try:
            self.members = tuple(str(m) for m in (self.members or ()) if m)
        except TypeError:
            self.members = ()

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
FREE_TEXT_MAX_LEN = 240    # free texts (death recap...) are longer but still bounded
# Kinds whose sentence may be given verbatim through ``phrase(..., text=...)``.
FREE_TEXT_KINDS: frozenset[AlertKind] = frozenset({
    AlertKind.OBJECTIVE_SOON,
    AlertKind.RECALL_GOLD,
    AlertKind.CONTROL_WARD,
    AlertKind.JUNGLER_WHERE,
    AlertKind.DEATH_RECAP,
    AlertKind.MACRO_TIP,
    AlertKind.PRAISE,
    AlertKind.SCOREBOARD,
})
_WS_RE = re.compile(r"\s+")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_EDGE_PUNCT = " \t\r\n.,;:!? "
_END_PUNCT = ".!?…"


def _to_str(value: Any) -> str | None:
    """``str(value)`` for text-like values (Enum -> its value), None for None/bool/NaN/errors."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        return value.value if isinstance(value, Enum) and isinstance(value.value, str) else str(value)
    except Exception:  # pathological __str__
        return None


def _clean(value: Any, max_len: int) -> str | None:
    """Single-line trimmed text without edge punctuation, or None if empty / not text-like."""
    s = _to_str(value)
    if s is None:
        return None
    s = _WS_RE.sub(" ", _CTRL_RE.sub(" ", s)).strip(_EDGE_PUNCT)
    if not s or s.lower() in ("none", "null", "nan"):
        return None
    if len(s) > max_len:
        s = s[:max_len].rstrip(_EDGE_PUNCT)
    return s or None


def _free_text(value: Any, max_len: int = FREE_TEXT_MAX_LEN) -> str | None:
    """Caller-provided sentence: one line, bounded, ending with a punctuation mark; None if empty."""
    s = _to_str(value)
    if s is None:
        return None
    s = _WS_RE.sub(" ", _CTRL_RE.sub(" ", s)).strip()
    if not s.strip(_EDGE_PUNCT) or s.lower() in ("none", "null", "nan"):
        return None
    if len(s) > max_len:
        cut = s[:max_len]
        space = cut.rfind(" ")
        s = (cut[:space] if space > max_len // 2 else cut).rstrip(_EDGE_PUNCT)
    if s[-1] not in _END_PUNCT:
        s = s.rstrip(",;: ") + "."
    return s


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


def _arrives(who: str, zone: str | None) -> str:
    """``"Lee Sin arrive par la rivière !"`` (``zone`` is a direction such as "par la rivière")."""
    return f"{_cap(who)} arrive {zone} !" if zone else f"{_cap(who)} arrive !"


def _jungler_approach(level: Level, champ: str | None, zone: str | None, n: int) -> str:
    if level >= Level.DANGER:
        return f"Gank ! {champ}, recule !" if champ else "Gank du jungler, recule !"
    if level == Level.WARNING:
        return _arrives(champ or "le jungler", zone)
    return f"{champ} rôde près de toi." if champ else "Le jungler rôde près de toi."


def _roam_approach(level: Level, champ: str | None, zone: str | None, n: int) -> str:
    who = champ or "un ennemi"
    if level >= Level.DANGER:
        return f"Gank ! {champ}, recule !" if champ else "Gank ! Un ennemi arrive, recule !"
    if level == Level.WARNING:
        return _arrives(who, zone)
    return f"{_cap(who)} rôde près de toi."


def _join_fr(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " et " + items[-1]


def _gank_group(level: Level, names: list[str], lane: str | None, n: int) -> str:
    """``"Gank bot : Lee Sin et Ahri !"`` (``n`` = total count, anonymous ones included)."""
    items = list(names[:5])
    extra = max(0, min(n, 5) - len(items))
    if extra == 1:
        items.append("un ennemi")
    elif extra > 1:
        items.append(f"{extra} ennemis")
    head = f"Gank {lane}" if lane else "Gank"
    tail = ", recule !" if level >= Level.DANGER else " !"
    return f"{head} : {_join_fr(items)}{tail}"


def _collapse(level: Level, champ: str | None, zone: str | None, n: int) -> str:
    if n >= 2:
        n = min(n, 5)                    # at most 5 enemies on the Rift
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


def _macro_tip(level: Level, champ: str | None, zone: str | None, n: int) -> str:
    return "Conseil : regarde la minimap."


def _praise(level: Level, champ: str | None, zone: str | None, n: int) -> str:
    return "Bien joué !"


def _scoreboard(level: Level, champ: str | None, zone: str | None, n: int) -> str:
    return f"Attention à {champ}." if champ else "Regarde le tableau des scores."


def _death_recap(level: Level, champ: str | None, zone: str | None, n: int) -> str:
    if n >= 2:
        return f"Mort face à {min(n, 5)} ennemis."
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
    AlertKind.MACRO_TIP: _macro_tip,
    AlertKind.PRAISE: _praise,
    AlertKind.SCOREBOARD: _scoreboard,
}
_GENERIC = {Level.INFO: "Attention.", Level.WARNING: "Attention !", Level.DANGER: "Danger, recule !"}


def phrase(kind: AlertKind, level: Level, champ: str | None, zone_label: str | None = None,
           count: int = 0, text: str | None = None, names: Any = None) -> str:
    """Short French sentence for an alert. Never raises.

    ``champ`` is the (localised) champion name, or ``None`` when unknown ("un ennemi");
    for ``OBJECTIVE_SOON`` it is the objective name ("Dragon"). ``zone_label`` is a
    ``geometry.zone_label_fr`` phrase ("en haut", "dans la rivière du bas").
    ``count`` is the number of enemies (COLLAPSE, DEATH_RECAP), seconds (OBJECTIVE_SOON,
    JUNGLER_WHERE) or gold (RECALL_GOLD).
    ``text`` is a ready-made sentence used verbatim (cleaned, bounded) for the v1.1 kinds
    (:data:`FREE_TEXT_KINDS`, e.g. the death recap of ``analysis.death_recap``); it is
    ignored for the gank kinds, whose sentences must stay short.
    For ``JUNGLER_APPROACH`` / ``ROAM_APPROACH`` at WARNING, ``zone_label`` is a direction
    ("par la rivière" -> "Lee Sin arrive par la rivière !").
    ``names`` (``COLLAPSE`` only): champion names of a merged gank alert; ``zone_label`` is then
    the lane word ("bot") and ``count`` the total number of enemies ->
    "Gank bot : Lee Sin et Ahri !" (", recule !" at DANGER).
    """
    lvl = Level.coerce(level)
    try:
        k = kind if isinstance(kind, AlertKind) else AlertKind(kind)
    except ValueError:
        log.warning("phrase(): unknown alert kind %r", kind)
        return _free_text(text) or _GENERIC[lvl]
    try:
        if text is not None and k in FREE_TEXT_KINDS:
            free = _free_text(text)
            if free:
                return free
        name = _clean(champ, NAME_MAX_LEN)
        zone = _clean(zone_label, ZONE_MAX_LEN)
        n = _as_count(count)
        if k == AlertKind.COLLAPSE and names:
            clean = [c for c in (_clean(x, NAME_MAX_LEN) for x in names) if c]
            if clean or n:
                out = _gank_group(lvl, clean, zone, max(n, len(clean)))
                return _WS_RE.sub(" ", out).strip() or _GENERIC[lvl]
        out = _BUILDERS[k](lvl, name, zone, n)
        return _WS_RE.sub(" ", out).strip() or _GENERIC[lvl]
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
               key: str | None = None, text: str | None = None,
               members: tuple[str, ...] = (), names: Any = None) -> Alert:
    """Build an :class:`Alert` with its phrase and the conventional key (see :func:`alert_key`)."""
    lvl = Level.coerce(level)
    return Alert(
        kind=kind,
        level=lvl,
        text=phrase(kind, lvl, champ, zone_label, count, text=text, names=names),
        key=key if key else alert_key(kind, alias or champ),
        t=t,
        alias=alias,
        members=tuple(members or ((alias,) if alias else ())),
    )


# --------------------------------------------------------------------------------------
# Throttler
# --------------------------------------------------------------------------------------

COOLDOWN_S: dict[Level, float] = {Level.INFO: 30.0, Level.WARNING: 8.0, Level.DANGER: 6.0}
GANK_REPEAT_S = 12.0        # the same gank is not announced again for this long
JUNGLER_SPOTTED_COOLDOWN_S = 45.0
GANK_ALERT_KINDS: frozenset[AlertKind] = frozenset(
    {AlertKind.JUNGLER_APPROACH, AlertKind.ROAM_APPROACH, AlertKind.COLLAPSE})
KIND_COOLDOWN_S: dict[AlertKind, float] = {
    AlertKind.JUNGLER_WHERE: 3.0,
    AlertKind.JUNGLER_APPROACH: GANK_REPEAT_S,
    AlertKind.ROAM_APPROACH: GANK_REPEAT_S,
    AlertKind.COLLAPSE: GANK_REPEAT_S,
    AlertKind.JUNGLER_SPOTTED: JUNGLER_SPOTTED_COOLDOWN_S,
}
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
    AlertKind.MACRO_TIP,
    AlertKind.SCOREBOARD,
    AlertKind.PRAISE,
)
DEFAULT_MIN_GAP_S = 1.2
DEFAULT_DANGER_GAP_S = 1.5  # min time between two DANGER messages (≈ one spoken sentence)
MAX_MIN_GAP_S = 30.0
# How long an alert held back by the gap / one-per-tick rule stays eligible (DANGER: never kept,
# the gank analyser re-emits it every tick while it is true).
PENDING_TTL_S: dict[Level, float] = {Level.INFO: 6.0, Level.WARNING: 1.5}
MAX_PENDING = 16
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


def _gap_value(value: Any, default: float) -> float:
    return min(max(_finite_or(value, default), 0.0), MAX_MIN_GAP_S)


class AlertThrottler:
    """Anti-spam filter: at most one alert per tick, per-key cooldowns, global gap. Thread-safe."""

    def __init__(self, min_gap_s: float = DEFAULT_MIN_GAP_S, *,
                 danger_gap_s: float = DEFAULT_DANGER_GAP_S) -> None:
        self.min_gap_s: float = _gap_value(min_gap_s, DEFAULT_MIN_GAP_S)
        self.danger_gap_s: float = _gap_value(danger_gap_s, DEFAULT_DANGER_GAP_S)
        self._lock = threading.Lock()
        self._by_key: dict[str, tuple[float, Level]] = {}
        self._by_alias: dict[str, tuple[float, Level]] = {}
        self._by_member: dict[str, tuple[float, Level]] = {}   # gank members announced
        # key -> (alert, time it was last raised) for alerts held back by the gap
        self._pending: dict[str, tuple[Alert, float]] = {}
        self._last_emit_t: float | None = None
        self._last_danger_t: float | None = None
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

    def pending_count(self) -> int:
        """Number of alerts currently held back and still eligible (diagnostics / tests)."""
        with self._lock:
            return len(self._pending)

    # -- internals ----------------------------------------------------------------------

    def _clear(self) -> None:
        self._by_key.clear()
        self._by_alias.clear()
        self._by_member.clear()
        self._pending.clear()
        self._last_emit_t = None
        self._last_danger_t = None
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
        for table in (self._by_key, self._by_alias, self._by_member):
            stale = [k for k, (ts, _lvl) in table.items() if now - ts > _KEEP_S]
            for k in stale:
                del table[k]

    def _same_gank(self, a: Alert, now: float) -> bool:
        """All the members of this gank alert were announced recently at >= its level."""
        if a.kind not in GANK_ALERT_KINDS or not a.members:
            return False
        for m in a.members:
            seen = self._by_member.get(m)
            if seen is None or now - seen[0] >= GANK_REPEAT_S or seen[1] < a.level:
                return False
        return True

    def _key_ok(self, a: Alert, now: float) -> bool:
        if self._same_gank(a, now):
            return False
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
        if a.kind in GANK_ALERT_KINDS and a.level < Level.DANGER:
            return True                         # a gank warning is never delayed by the gap
        if a.level >= Level.DANGER:
            last = self._last_danger_t
            return last is None or now - last >= self.danger_gap_s
        if a.kind in GAP_EXEMPT_KINDS or self._last_emit_t is None:
            return True
        return now - self._last_emit_t >= self.min_gap_s

    @staticmethod
    def _valid(a: Any) -> bool:
        return isinstance(a, Alert) and bool(a.text) and isinstance(a.key, str)

    def _keep_pending(self, a: Alert, raised_at: float) -> None:
        if a.level >= Level.DANGER:
            return
        self._pending[a.key] = (a, raised_at)
        if len(self._pending) > MAX_PENDING:
            # drop the least useful: lowest level, then oldest
            worst = min(self._pending.items(), key=lambda kv: (int(kv[1][0].level), kv[1][1]))
            del self._pending[worst[0]]

    def _filter_locked(self, alerts: Iterable[Alert] | None, t: float) -> list[Alert]:
        now = self._now(t)
        self._prune(now)
        fresh: list[Alert] = []
        for a in alerts or ():
            if self._valid(a):
                fresh.append(a)
            else:
                log.debug("AlertThrottler: ignoring invalid alert %r", a)
        fresh_keys = {a.key for a in fresh}
        # candidates = still-eligible held-back alerts (older first) + this tick's alerts
        candidates: list[tuple[Alert, float]] = []
        for key, (a, raised_at) in list(self._pending.items()):
            ttl = PENDING_TTL_S.get(a.level, 0.0)
            if key in fresh_keys or now - raised_at > ttl:
                del self._pending[key]           # superseded by a fresh one, or expired
            else:
                candidates.append((a, raised_at))
        candidates.extend((a, now) for a in fresh)
        if not candidates:
            return []

        best: tuple[Alert, float] | None = None
        best_rank: tuple[int, int, float, int, int] | None = None
        held: list[tuple[Alert, float]] = []
        for index, (a, raised_at) in enumerate(candidates):
            if not self._key_ok(a, now):
                self._pending.pop(a.key, None)   # already said recently: nothing to keep
                continue
            if not self._gap_ok(a, now):
                held.append((a, raised_at))
                continue
            rank = (
                int(a.level),
                int(a.kind in GANK_ALERT_KINDS),                    # gank first, at equal level
                _finite_or(a.t, -math.inf),
                -_PRIORITY_RANK.get(a.kind, len(_PRIORITY_RANK)),  # type: ignore[arg-type]
                index,                                              # later in the list = more recent
            )
            if best_rank is None or rank > best_rank:
                if best is not None:
                    held.append(best)
                best, best_rank = (a, raised_at), rank
            else:
                held.append((a, raised_at))

        for a, raised_at in held:
            if best is None or a.key != best[0].key:
                self._keep_pending(a, raised_at)
        if best is None:
            return []
        chosen = best[0]
        self._pending.pop(chosen.key, None)
        self._by_key[chosen.key] = (now, chosen.level)
        if chosen.alias:
            self._by_alias[chosen.alias] = (now, chosen.level)
        if chosen.kind in GANK_ALERT_KINDS:
            for m in chosen.members:
                self._by_member[m] = (now, chosen.level)
        self._last_emit_t = now
        if chosen.level >= Level.DANGER:
            self._last_danger_t = now
        return [chosen]
