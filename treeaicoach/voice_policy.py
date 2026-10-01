"""What is SPOKEN and what is only WRITTEN (HUD tip line + toasts), plus the anti-spam gate.

The user wants a quiet coach: by default (``cfg.voice_level == "minimal"``) the voice only says

* gank alerts (jungler / roam approach, collapse: WARNING and DANGER) and the F9 answer - only
  when they are RELEVANT (:func:`triage_gank`: not during a fight, not when I am grouped, not
  when the "ganker" is behind my allies; a lone weaker enemy is a written opportunity instead);
* the fight calls ("Engage !" / "Repli !", ``call:`` keys) and the urgent macro / positioning
  calls (``urgent:`` keys: "Ils sont 4 morts : Baron maintenant !", "Tu es seul en haut...");
* the objective warnings 60 s before a spawn (``objective_soon:<kind>:<lead>`` with lead >= 45);
* the "prudent" stance change only (``stance:prudent``; the other stances are written);
* praise for the big moments only (multikill, shutdown, solo kill, steal).

On top of that, :class:`SpeechBudget` caps what is said outside gank / fight calls: at most one
message every :data:`BUDGET_GAP_S` (20 s) and :data:`BUDGET_PER_MIN` (3) per minute, nothing for a
few seconds after a gank / fight call, nothing during a fight; a message over budget waits in a
small priority queue until it expires (big praise waits for the end of the fight).

Everything else (macro tips, Tab insights, buy / recall / ward reminders, small praise, death
recap, jungler sightings...) is written in the HUD and as a toast. ``"normal"`` also speaks the
objective timers, macro tips, death recap, sightings, Tab insights and every praise (reminders
stay written); ``"bavard"`` speaks everything.

:class:`MessageGate` is the per-game anti-spam memory applied to every non-gank message,
spoken or written: a minimum interval per kind (e.g. one recall reminder every 150 s) and no
identical key / text again within a per-kind window. Gank alerts are never gated here (the
:class:`treeaicoach.alerts.AlertThrottler` handles them).

Pure Python, thread-safe, never raises from its public methods.
"""

from __future__ import annotations

import logging
import math
import threading
from typing import Any

from treeaicoach.alerts import AlertKind

log = logging.getLogger(__name__)

VOICE_LEVELS: tuple[str, ...] = ("minimal", "normal", "bavard")
DEFAULT_VOICE_LEVEL = "minimal"
GANK_KINDS = frozenset({AlertKind.JUNGLER_APPROACH, AlertKind.ROAM_APPROACH, AlertKind.COLLAPSE})
#: Always spoken (answers to a hotkey the player pressed).
ALWAYS_VOICE = frozenset({AlertKind.JUNGLER_WHERE})
#: Praise keys (praise.PraiseCoach) of the big moments, spoken even in "minimal".
BIG_PRAISE_PREFIXES: tuple[str, ...] = ("multi:", "shutdown:", "solo:", "steal:")
STANCE_PREFIX = "stance:"
#: Keys always spoken (subject to the budget): fight calls and urgent macro / positioning calls.
URGENT_PREFIXES: tuple[str, ...] = ("call:", "urgent:")
#: Stances spoken in "normal" (safety only; never in "minimal").
MINIMAL_STANCES = ("stance:prudent",)
#: Objective warnings at least this many seconds before the spawn are spoken in "minimal".
OBJECTIVE_VOICE_MIN_LEAD_S = 45
#: Spoken in "normal" (besides the "minimal" set).
NORMAL_VOICE = frozenset({AlertKind.OBJECTIVE_SOON, AlertKind.MACRO_TIP, AlertKind.DEATH_RECAP,
                          AlertKind.JUNGLER_SPOTTED, AlertKind.LANER_MIA, AlertKind.SCOREBOARD,
                          AlertKind.PRAISE})

#: Minimum interval between two messages of one kind (spoken or written), seconds.
KIND_GAP_S: dict[str, float] = {
    "macro_tip": 20.0, "praise": 12.0, "scoreboard": 30.0, "recall_gold": 150.0, "control_ward": 300.0,
    "objective_soon": 6.0, "jungler_spotted": 30.0, "laner_mia": 30.0, "death_recap": 0.0, "stance": 120.0,
}
#: The same key or the same text is not repeated within this window (per game), seconds.
DEDUPE_S: dict[str, float] = {
    "macro_tip": 240.0, "praise": 600.0, "scoreboard": 600.0, "recall_gold": 180.0, "control_ward": 600.0,
    "objective_soon": 200.0, "jungler_spotted": 60.0, "laner_mia": 90.0, "death_recap": 30.0, "stance": 120.0,
}
DEFAULT_GAP_S = 15.0
DEFAULT_DEDUPE_S = 180.0

#: Toast (kind, title) of the written-only messages, by alert kind.
TEXT_TOAST: dict[str, tuple[str, str]] = {
    "macro_tip": ("insight", "CONSEIL"), "recall_gold": ("insight", "RETOUR EN BASE"),
    "control_ward": ("insight", "BALISE"), "objective_soon": ("warning", "OBJECTIF"),
    "death_recap": ("danger", "TA MORT"), "jungler_spotted": ("warning", "JUNGLER"),
    "laner_mia": ("warning", "MIA"),
}


def normalize_level(value: Any) -> str:
    v = str(value or "").strip().lower()
    return v if v in VOICE_LEVELS else DEFAULT_VOICE_LEVEL


def kind_name(alert: Any) -> str:
    """Stable kind id of an alert (``"stance"`` for stance announcements)."""
    key = str(getattr(alert, "key", "") or "")
    if key.startswith(STANCE_PREFIX):
        return "stance"
    k = getattr(alert, "kind", "")
    return str(getattr(k, "value", k) or "")


def _objective_lead(key: str) -> float | None:
    try:
        return float(key.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


def is_big_praise(alert: Any) -> bool:
    return str(getattr(alert, "key", "") or "").startswith(BIG_PRAISE_PREFIXES)


def route(alert: Any, voice_level: Any = DEFAULT_VOICE_LEVEL) -> str:
    """``"voice"`` (candidate for speech, see :class:`VoiceGate`) or ``"text"`` (visual only:
    HUD line + toast). VISUAL FIRST: in ``"minimal"`` (default) only gank alerts, the fight call
    (``call:``), the F9 answer and ONE objective warning (>= 45 s before the spawn) may be
    spoken. Never raises."""
    try:
        kind = getattr(alert, "kind", None)
        key = str(getattr(alert, "key", "") or "")
        if kind in GANK_KINDS or kind in ALWAYS_VOICE or key.startswith("call:"):
            return "voice"
        level = normalize_level(voice_level)
        if level == "bavard":
            return "voice"
        if kind == AlertKind.OBJECTIVE_SOON:
            lead = _objective_lead(key)
            if lead is not None and lead >= OBJECTIVE_VOICE_MIN_LEAD_S:
                return "voice"
            return "voice" if level == "normal" else "text"
        if level == "minimal":
            return "text"
        # "normal": urgent macro calls, the prudent stance, big praise and the NORMAL_VOICE kinds
        if key.startswith("urgent:"):
            return "voice"
        if key.startswith(STANCE_PREFIX):
            return "voice" if key.startswith(MINIMAL_STANCES) else "text"
        if kind == AlertKind.PRAISE:
            return "voice" if is_big_praise(alert) else "text"
        if kind in NORMAL_VOICE:
            return "voice"
        return "text"
    except Exception:
        log.debug("voice_policy.route failed", exc_info=True)
        return "text"


class MessageGate:
    """Per-game anti-spam memory (see the module docstring). Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._kind_t: dict[str, float] = {}
            self._seen: dict[str, float] = {}          # "k:<key>" / "x:<text>" -> time

    @staticmethod
    def _exempt(alert: Any) -> bool:
        return getattr(alert, "kind", None) in GANK_KINDS or getattr(alert, "kind", None) in ALWAYS_VOICE

    def check(self, alert: Any, t: float) -> bool:
        """True when ``alert`` may be said / shown now (nothing is remembered). Never raises."""
        try:
            if self._exempt(alert):
                return True
            with self._lock:
                return self._ok(alert, float(t))
        except Exception:
            log.debug("MessageGate.check failed", exc_info=True)
            return True

    def record(self, alert: Any, t: float) -> None:
        """Remember that ``alert`` was said / shown at ``t``. Never raises."""
        try:
            if self._exempt(alert):
                return
            kind = kind_name(alert)
            with self._lock:
                now = float(t)
                self._kind_t[kind] = now
                self._seen["k:" + str(getattr(alert, "key", "") or "")] = now
                self._seen["x:" + _norm(getattr(alert, "text", ""))] = now
                if len(self._seen) > 400:
                    for k in sorted(self._seen, key=self._seen.get)[:200]:
                        self._seen.pop(k, None)
        except Exception:
            log.debug("MessageGate.record failed", exc_info=True)

    def allow(self, alert: Any, t: float) -> bool:
        """:meth:`check` + :meth:`record` in one step (written messages)."""
        if self._exempt(alert):
            return True
        with self._lock:
            ok = self._ok(alert, float(t))
        if ok:
            self.record(alert, t)
        return ok

    def _ok(self, alert: Any, now: float) -> bool:
        if not math.isfinite(now):
            return False
        kind = kind_name(alert)
        last = self._kind_t.get(kind)
        if last is not None and 0.0 <= now - last < KIND_GAP_S.get(kind, DEFAULT_GAP_S):
            return False
        window = DEDUPE_S.get(kind, DEFAULT_DEDUPE_S)
        for k in ("k:" + str(getattr(alert, "key", "") or ""), "x:" + _norm(getattr(alert, "text", ""))):
            seen = self._seen.get(k)
            if seen is not None and 0.0 <= now - seen < window:
                return False
        return True


def _norm(text: Any) -> str:
    return " ".join(str(text or "").casefold().split())


# ======================================================================================
# Speech budget
# ======================================================================================
BUDGET_GAP_S = 20.0            # min time between two budgeted (non gank / fight) spoken messages
BUDGET_PER_MIN = 3             # ... and at most this many per rolling minute
AFTER_CRITICAL_S = 6.0         # nothing budgeted this long after a gank / fight call
QUEUE_MAX = 4
QUEUE_TTL_S = 12.0             # a waiting message is dropped after this (stale)
PRAISE_TTL_S = 45.0            # big praise can wait for the end of a fight
URGENT_TTL_S = 8.0


def is_critical(alert: Any) -> bool:
    """Gank alerts, F9 answers and fight calls: never budgeted (they ARE the point)."""
    kind = getattr(alert, "kind", None)
    return kind in GANK_KINDS or kind in ALWAYS_VOICE or str(getattr(alert, "key", "") or "").startswith("call:")


def speech_priority(alert: Any) -> int:
    key = str(getattr(alert, "key", "") or "")
    kind = getattr(alert, "kind", None)
    if is_critical(alert):
        return 100
    if key.startswith("urgent:"):
        return 80
    if kind == AlertKind.OBJECTIVE_SOON:
        return 60
    if key.startswith(STANCE_PREFIX):
        return 50
    if kind == AlertKind.PRAISE:
        return 40
    return 20


class SpeechBudget:
    """Global speech budget + priority queue with expiry (see the module docstring). Thread-safe."""

    def __init__(self, gap_s: float = BUDGET_GAP_S, per_min: int = BUDGET_PER_MIN) -> None:
        self._lock = threading.Lock()
        self.gap_s = float(gap_s)
        self.per_min = int(per_min)
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._spoken: list[float] = []            # budgeted messages said (t)
            self._critical_t = -math.inf
            self._queue: list[tuple[int, float, float, Any]] = []   # (prio, expiry, t, alert)
            self._quiet_until = -math.inf

    def note_critical(self, t: float) -> None:
        with self._lock:
            self._critical_t = max(self._critical_t, float(t))

    def quiet(self, until: float) -> None:
        """No budgeted message before ``until`` (a fight is going on)."""
        with self._lock:
            self._quiet_until = max(self._quiet_until, float(until))

    def _can(self, t: float) -> bool:
        if t < self._quiet_until or 0.0 <= t - self._critical_t < AFTER_CRITICAL_S:
            return False
        self._spoken = [x for x in self._spoken if 0.0 <= t - x < 60.0]
        if self._spoken and t - self._spoken[-1] < self.gap_s:
            return False
        return len(self._spoken) < self.per_min

    def filter(self, alerts: list[Any], t: float) -> list[Any]:
        """Alerts allowed to be SPOKEN now (critical ones always; the others within the budget,
        at most one; the rest is queued with an expiry). Never raises."""
        try:
            now = float(t)
            out: list[Any] = []
            rest: list[Any] = []
            for a in alerts or []:
                (out if is_critical(a) else rest).append(a)
            with self._lock:
                if out:
                    self._critical_t = now
                rest.sort(key=lambda a: -speech_priority(a))
                for a in rest:
                    if not out and self._can(now):
                        self._spoken.append(now)
                        out.append(a)
                    else:
                        self._enqueue(a, now)
            return out
        except Exception:
            log.debug("SpeechBudget.filter failed", exc_info=True)
            return list(alerts or [])

    def _enqueue(self, a: Any, now: float) -> None:
        prio = speech_priority(a)
        ttl = PRAISE_TTL_S if getattr(a, "kind", None) == AlertKind.PRAISE else (
            URGENT_TTL_S if prio >= 80 else QUEUE_TTL_S)
        key = str(getattr(a, "key", "") or "")
        self._queue = [q for q in self._queue if str(getattr(q[3], "key", "")) != key]
        self._queue.append((prio, now + ttl, now, a))
        self._queue.sort(key=lambda q: (-q[0], q[2]))
        del self._queue[QUEUE_MAX:]

    def pop_ready(self, t: float) -> Any | None:
        """The best queued message when the budget allows it now (else None). Never raises."""
        try:
            now = float(t)
            with self._lock:
                self._queue = [q for q in self._queue if q[1] > now]
                if not self._queue or not self._can(now):
                    return None
                q = self._queue.pop(0)
                self._spoken.append(now)
                return q[3]
        except Exception:
            return None

    def allow_text(self, t: float) -> bool:
        """For free-text speech outside the alert pipeline (caster lines...): budget check + record."""
        try:
            with self._lock:
                if self._can(float(t)):
                    self._spoken.append(float(t))
                    return True
                return False
        except Exception:
            return False

    def queued(self) -> list[Any]:
        with self._lock:
            return [q[3] for q in self._queue]


# ======================================================================================
# Gank relevance
# ======================================================================================
GROUPED_R = 0.15               # allies this close to me: I am grouped
GROUPED_MIN = 2
SCREEN_CONE_COS = math.cos(math.radians(35))
STRONGER_RATIO = 1.35          # me (+ close allies) / ganker(s) power -> opportunity
OPPORTUNITY_MIN_HP = 0.6
ALONE_R = 0.25                 # no other enemy this close to the ganker = alone


def triage_gank(alert: Any, *, me_pos: Any, allies: list[Any], enemies: list[Any], game: Any = None,
                in_fight: bool = False, in_base: bool = False, scoreboard: Any = None,
                name: str | None = None) -> tuple[str, str | None]:
    """Is this gank alert worth SPEAKING? Returns ``(decision, text)``:

    * ``("speak", None)`` - say it (the word "Gank" is kept);
    * ``("drop", None)``  - fight going on / I am dead / in my base: nothing (the fight call speaks);
    * ``("text", reason)`` - grouped with >= 2 allies, or the ganker is behind my allies: written only;
    * ``("opportunity", text)`` - the "ganker" is alone and clearly weaker than me (+ close
      allies): written "Lee Sin seul et plus faible : tu peux le punir".

    ``allies`` / ``enemies``: :class:`treeaicoach.fight.Seen` (visible allies, all enemy tracks).
    Never raises (``"speak"`` on error: a gank is never lost by a bug).
    """
    try:
        me = getattr(game, "me", None)
        if in_fight:
            return "drop", None
        if (me is not None and bool(getattr(me, "is_dead", False))) or in_base:
            return "drop", None
        if me_pos is None:
            return "speak", None
        from treeaicoach import geometry

        near = [a for a in allies if getattr(a, "uv", None) is not None and geometry.dist(a.uv, me_pos) < GROUPED_R]
        members = [str(m).lower() for m in (getattr(alert, "members", ()) or ())]
        alias = str(getattr(alert, "alias", "") or "").lower()
        if alias and alias not in members:
            members.append(alias)
        gankers = [e for e in enemies if getattr(e, "visible", False) and getattr(e, "uv", None) is not None
                   and str(getattr(e, "alias", "") or "").lower() in members]
        lvl = int(getattr(alert, "level", 0) or 0)
        if len(near) >= GROUPED_MIN and not (lvl >= 2 and len(gankers) >= 3):
            return "text", "groupé"
        # ganker behind my allies (an ally stands between us, much closer to him)
        for g in gankers:
            d_me = geometry.dist(g.uv, me_pos)
            for a in allies:
                if getattr(a, "uv", None) is None:
                    continue
                d_al = geometry.dist(g.uv, a.uv)
                if d_al >= 0.6 * d_me or d_me < 1e-6:
                    continue
                vx, vy = g.uv[0] - me_pos[0], g.uv[1] - me_pos[1]
                wx, wy = a.uv[0] - me_pos[0], a.uv[1] - me_pos[1]
                nw = math.hypot(wx, wy)
                if nw > 1e-6 and (vx * wx + vy * wy) / (d_me * nw) >= SCREEN_CONE_COS:
                    return "text", "derrière tes alliés"
        # a lone, weaker "ganker": an opportunity, not a danger
        if len(gankers) == 1 and game is not None:
            g = gankers[0]
            others = [e for e in enemies if e is not g and getattr(e, "visible", False) and getattr(e, "uv", None)
                      is not None and geometry.dist(e.uv, g.uv) < ALONE_R]
            stats = getattr(game, "champion_stats", None) or {}
            try:
                hp = float(stats.get("currentHealth")) / float(stats.get("maxHealth"))
            except (TypeError, ValueError, ZeroDivisionError):
                hp = None
            if not others and hp is not None and hp >= OPPORTUNITY_MIN_HP:
                from treeaicoach.fight import champion_power
                from treeaicoach.scoreboard import items_gold

                p = game.player_by_alias(g.alias) if g.alias and hasattr(game, "player_by_alias") else None
                if p is not None and me is not None:
                    gt = float(getattr(game, "game_time", 0.0) or 0.0)
                    mine = champion_power(me.level, items_gold(me.items), me.champion_alias, gt, hp)
                    mine += sum(0.8 * champion_power(getattr(game.player_by_alias(a.alias), "level", 1) if a.alias and
                                                     game.player_by_alias(a.alias) else 1, 0, None, gt) for a in near)
                    his = champion_power(p.level, items_gold(p.items), p.champion_alias, gt)
                    if his > 0 and mine / his >= STRONGER_RATIO:
                        who = name or p.champion_name or p.champion_alias
                        return "opportunity", f"{who} seul et plus faible : tu peux le punir."
        return "speak", None
    except Exception:
        log.debug("triage_gank failed", exc_info=True)
        return "speak", None


__all__ = ["VOICE_LEVELS", "DEFAULT_VOICE_LEVEL", "route", "MessageGate", "TEXT_TOAST", "is_big_praise",
           "kind_name", "normalize_level", "SpeechBudget", "triage_gank", "is_critical", "speech_priority"]
