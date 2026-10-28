"""Freshness guard: every coaching message is checked again right before it reaches the player.

Real bug (2.29): "the enemy has been level 10 for 5 minutes and the coach says he is level 9".
Two causes, both handled here:

1. **The Live Client level of an enemy is the level his team showed last time he was SEEN**
   (Tab data is fog-of-war'd). Measured on the 20 real games with an LCU truth file: every
   enemy level mismatch (21 / 595 role samples) is an enemy unseen for 21 s to 5 min, always
   LOWER than the truth (by 1 to 3 levels, mostly the jungler). :class:`LevelWatch` keeps, per
   enemy, when the API level last changed and when the minimap last saw him, and once he has been
   unseen :data:`UNSEEN_MIN_S` it raises ``PlayerInfo.level`` to a conservative estimate
   (``max(API level, the 10th-percentile level of his role at this game time, his API level + the
   XP a slow player of his role earns while unseen)``, at most :data:`MAX_RAISE` above the API).
   ``PlayerInfo.level_api`` keeps the raw number, ``level_estimated`` says it is an estimate.
   On the same 595 samples: 0 over-estimate, 2 of the 10 under-estimates fixed with the sighting
   anchor only (the API-change anchor used live is fresher).

2. **A message is built at tick t and said / shown later** (voice queue, voice gate queue,
   throttler, Discord bot, written HUD line). :func:`snapshot` takes the facts a line depends on
   (my death, levels, the named champion's death) when the message is created;
   :func:`stale_reason` is called again right before the speech / display with the current game:
   the line is dropped when it is older than its kind's :data:`MAX_AGE_S` or when one of its facts
   changed (a "niveau" line after a level change, any line after my death / respawn, a line about
   a champion who died / respawned since).

Pure Python, no I/O, never raises from the public API.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------------- 1. message age
#: max age (s, since the alert was created) of a message when it is said / shown, by kind
#: (voice_policy.kind_name). A danger is about THIS second; a macro call about the next ~10 s.
MAX_AGE_S: dict[str, float] = {
    "jungler_approach": 4.0, "roam_approach": 4.0, "collapse": 4.0, "personal_danger": 4.0,
    "jungler_spotted": 5.0, "laner_mia": 5.0, "jungler_where": 6.0,
    "objective_soon": 6.0,          # "Dragon dans 30 s" said 6 s late is wrong
    "gc": 8.0, "macro_call": 10.0, "stance": 10.0,
    "macro_tip": 12.0, "recall_gold": 12.0, "control_ward": 12.0,
    "scoreboard": 15.0, "death_recap": 30.0, "praise": 45.0,
}
DEFAULT_MAX_AGE_S = 12.0
#: kinds still true after my death / respawn ("Dragon dans 30 s", "Bien joué")
SURVIVES_MY_DEATH = frozenset({"objective_soon", "praise", "death_recap"})
#: kinds whose champion dying / respawning does not change the line (praise names the victim)
SURVIVES_ALIAS_DEATH = frozenset({"praise", "death_recap", "objective_soon", "scoreboard"})
#: a line that states levels ("niveau 9", "2 niveaux d'avance", "niv 6 vs 5")
LEVEL_RE = re.compile(r"(?i)\bniv(?:eaux?|\.)?\b|\blevel\b")


def max_age(kind: str) -> float:
    return MAX_AGE_S.get(str(kind or ""), DEFAULT_MAX_AGE_S)


def _kind(alert: Any) -> str:
    try:
        from treeaicoach import voice_policy as vp

        return vp.kind_name(alert)
    except Exception:
        k = getattr(alert, "kind", "")
        return str(getattr(k, "value", k) or "")


def _norm(x: Any) -> str:
    return "".join(ch for ch in str(x or "").lower() if ch.isalnum())


@dataclass(frozen=True)
class Facts:
    """What a message may depend on, at one moment."""

    me_dead: bool = False
    me_level: int = 0
    levels: tuple[tuple[str, int], ...] = ()      # (normalized alias, level) of every player
    dead: tuple[tuple[str, bool], ...] = ()       # (normalized alias, is_dead)

    def level_of(self, alias: Any) -> int | None:
        a = _norm(alias)
        return next((lv for k, lv in self.levels if k == a), None)

    def dead_of(self, alias: Any) -> bool | None:
        a = _norm(alias)
        return next((d for k, d in self.dead if k == a), None)


def snapshot(game: Any) -> Facts | None:
    """The facts of ``game`` (a live_client.GameInfo), None without a game. Never raises."""
    try:
        if game is None:
            return None
        me = getattr(game, "me", None)
        players = list(game.all_players()) if hasattr(game, "all_players") else []
        lv, dd = [], []
        for p in players:
            a = _norm(getattr(p, "champion_alias", ""))
            if a:
                lv.append((a, int(getattr(p, "level", 0) or 0)))
                dd.append((a, bool(getattr(p, "is_dead", False))))
        return Facts(me_dead=bool(getattr(me, "is_dead", False)) if me is not None else False,
                     me_level=int(getattr(me, "level", 0) or 0) if me is not None else 0,
                     levels=tuple(lv), dead=tuple(dd))
    except Exception:
        log.debug("freshness snapshot failed", exc_info=True)
        return None


def stamp(alert: Any, game: Any) -> None:
    """Attach the creation facts to an alert (once: a re-queued alert keeps its first stamp)."""
    try:
        if getattr(alert, "_fresh_facts", None) is None:
            setattr(alert, "_fresh_facts", snapshot(game) or Facts())
    except Exception:
        pass


def stale_reason(alert: Any, now: float, game: Any, text: str | None = None) -> str | None:
    """Why ``alert`` must NOT be said / shown at ``now`` with the current ``game`` (None = fresh).

    ``"age"``: older than its kind's :data:`MAX_AGE_S`; ``"me_dead"``: I died / respawned since;
    ``"target_dead"``: the named champion died / respawned since; ``"level"``: a line stating
    levels after a level change of me / the named champion (any enemy when none is named)."""
    try:
        kind = _kind(alert)
        t0 = float(getattr(alert, "t", now))
        if math.isfinite(t0) and now - t0 > max_age(kind):
            return "age"
        then = getattr(alert, "_fresh_facts", None)
        cur = snapshot(game)
        if not isinstance(then, Facts) or cur is None or not then.levels:
            return None
        if then.me_dead != cur.me_dead and kind not in SURVIVES_MY_DEATH:
            return "me_dead"
        alias = getattr(alert, "alias", None)
        if alias and kind not in SURVIVES_ALIAS_DEATH:
            d0, d1 = then.dead_of(alias), cur.dead_of(alias)
            if d0 is not None and d1 is not None and d0 != d1:
                return "target_dead"
        body = text if text is not None else str(getattr(alert, "text", "") or "")
        if LEVEL_RE.search(body):
            if then.me_level != cur.me_level:
                return "level"
            if alias:
                l0, l1 = then.level_of(alias), cur.level_of(alias)
                if l0 is not None and l1 is not None and l0 != l1:
                    return "level"
            elif dict(then.levels) != dict(cur.levels):
                return "level"
        return None
    except Exception:
        log.debug("stale_reason failed", exc_info=True)
        return None


def checker(alert: Any, clock: Callable[[], float], game_fn: Callable[[], Any]) -> Callable[[], bool]:
    """A ``() -> bool`` (True = still say it) for a queue that speaks later (voice, Discord)."""
    def ok() -> bool:
        try:
            return stale_reason(alert, float(clock()), game_fn()) is None
        except Exception:
            return True
    return ok


# ----------------------------------------------------------------------------- 2. enemy levels
#: enemy unseen this long (s, engine clock) before his API level is treated as possibly old
UNSEEN_MIN_S = 30.0
#: at most this many levels above the API level (a guess must stay a floor, not a fantasy)
MAX_RAISE = 3
#: 10th percentile of the true level per role, per game minute (20 real games, LCU truth frames)
LEVEL_FLOOR: dict[str, tuple[int, ...]] = {
    "TOP": (1, 1, 1, 3, 4, 5, 6, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 12, 12, 13, 14, 14, 14, 14, 14, 15),
    "JUNGLE": (1, 1, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 9, 10, 10, 10, 11, 11, 11, 12, 12, 13, 13, 13, 13),
    "MIDDLE": (1, 1, 2, 3, 4, 5, 5, 6, 7, 7, 7, 8, 8, 9, 9, 9, 10, 10, 11, 11, 11, 11, 11, 11, 12, 13, 13, 13, 13, 14),
    "BOTTOM": (1, 1, 1, 2, 3, 3, 4, 5, 5, 5, 6, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 10, 10, 11, 11, 12, 12, 12, 12, 13),
    "UTILITY": (1, 1, 1, 2, 3, 3, 4, 4, 5, 5, 5, 6, 6, 7, 7, 7, 8, 8, 8, 9, 9, 10, 10, 10, 10, 11, 11, 11, 12, 12),
}
#: XP / min of a slow player of the role (25th percentile, truth frames), per 5 game minutes
SLOW_XP_PER_MIN: dict[str, tuple[int, ...]] = {
    "TOP": (380, 293, 426, 426, 460, 526), "JUNGLE": (220, 260, 326, 393, 435, 510),
    "MIDDLE": (380, 301, 360, 393, 426, 493), "BOTTOM": (220, 226, 293, 360, 426, 460),
    "UTILITY": (220, 226, 260, 326, 393, 426),
}
#: total XP to reach a level (index = level)
XP_TOTAL = (0, 0, 280, 660, 1140, 1720, 2400, 3180, 4060, 5040, 6120, 7300, 8580, 9960, 11440, 13020,
            14700, 16480, 18360)


def _role(p: Any) -> str:
    if bool(getattr(p, "has_smite", False)):
        return "JUNGLE"
    pos = str(getattr(p, "position", "") or "").upper()
    return pos if pos in LEVEL_FLOOR else "UTILITY"     # unknown role: the slowest curve


def level_floor(role: str, gt: float) -> int:
    """The level almost every player of ``role`` has at game time ``gt`` (30 s margin)."""
    row = LEVEL_FLOOR.get(role, LEVEL_FLOOR["UTILITY"])
    m = max(0, int((float(gt) - 30.0) // 60.0))
    return int(row[min(m, len(row) - 1)])


def level_after(level: int, role: str, gt: float, unseen_s: float) -> int:
    """``level`` + the levels a slow player of ``role`` earns in ``unseen_s`` (from the start of
    ``level``: a floor)."""
    lv = max(1, min(18, int(level)))
    rates = SLOW_XP_PER_MIN.get(role, SLOW_XP_PER_MIN["UTILITY"])
    xp = XP_TOTAL[lv] + rates[min(len(rates) - 1, max(0, int(gt // 300.0)))] * max(0.0, unseen_s) / 60.0
    while lv < 18 and XP_TOTAL[lv + 1] <= xp:
        lv += 1
    return lv


def estimate_level(api_level: int, role: str, gt: float, unseen_s: float) -> int:
    """Conservative level of an enemy whose API level may be old (pure)."""
    api = max(1, min(18, int(api_level or 1)))
    if unseen_s < UNSEEN_MIN_S:
        return api
    est = max(api, level_floor(role, gt), level_after(api, role, gt, unseen_s))
    return int(min(18, api + MAX_RAISE, est))


@dataclass
class _Seen:
    api: int = 0
    changed_t: float = 0.0          # engine time the API level last changed (= seen by his enemies)
    est: int = 0                    # last estimate (never goes down while the API does not)


@dataclass
class LevelWatch:
    """Per game: raises the possibly-old API level of the enemies unseen for a while."""

    _st: dict[str, _Seen] = field(default_factory=dict)
    raised: int = 0                 # players raised on the last update (diagnostics)

    def reset(self) -> None:
        self._st.clear()
        self.raised = 0

    def update(self, game: Any, t: float, last_seen: Callable[[str], float | None] | None = None) -> None:
        """Annotate ``game.enemies`` in place (``level``, ``level_api``, ``level_estimated``).
        ``last_seen(alias)``: engine time the minimap last saw him (None: never). Never raises."""
        try:
            gt = float(getattr(game, "game_time", 0.0) or 0.0)
            raised = 0
            for p in _enemies(game):
                alias = _norm(getattr(p, "champion_alias", ""))
                if not alias:
                    continue
                api = int(getattr(p, "level_api", 0) or 0) or int(getattr(p, "level", 1) or 1)
                st = self._st.get(alias)
                if st is None or api != st.api:
                    st = self._st[alias] = _Seen(api=api, changed_t=t, est=api)
                seen = None
                if last_seen is not None:
                    try:
                        seen = last_seen(getattr(p, "champion_alias", ""))
                    except Exception:
                        seen = None
                anchor = max(st.changed_t, float(seen) if seen is not None else -math.inf)
                if gt < 90.0:
                    anchor = t                  # fountain / first camps: the API is right
                if bool(getattr(p, "is_dead", False)):
                    est = max(api, st.est)          # no XP while dead: keep the last estimate
                else:
                    est = estimate_level(api, _role(p), gt, t - anchor)
                    est = max(est, st.est) if est > api else est
                st.est = est
                _set(p, "level_api", api)
                _set(p, "level_estimated", est > api)
                _set(p, "level_age_s", max(0.0, t - anchor))
                if est > api:
                    _set(p, "level", est)
                    raised += 1
            self.raised = raised
        except Exception:
            log.debug("LevelWatch.update failed", exc_info=True)


def level_exact(p: Any) -> bool:
    """False when ``p.level`` may be OLD: an enemy raised by :class:`LevelWatch` or unseen (and his
    level unchanged) for more than :data:`UNSEEN_MIN_S`. Such a level is a FLOOR: "Darius est niveau
    6" stays true, "niveau 6 contre 5" / "tu as 2 niveaux d'avance sur lui" / "pas lui" may be false.
    Players never annotated (me, allies, demo / replay sources) are exact. Never raises."""
    try:
        if bool(getattr(p, "level_estimated", False)):
            return False
        age = getattr(p, "level_age_s", None)
        return not (isinstance(age, (int, float)) and math.isfinite(age) and age > UNSEEN_MIN_S)
    except Exception:
        return True


def enemy_level_exact(game: Any, alias: Any) -> bool:
    """:func:`level_exact` of the enemy ``alias`` of ``game`` (True when unknown). Never raises."""
    try:
        a = _norm(alias)
        p = next((e for e in _enemies(game) if _norm(getattr(e, "champion_alias", "")) == a), None)
        return True if p is None else level_exact(p)
    except Exception:
        return True


def _enemies(game: Any) -> Iterable[Any]:
    return list(getattr(game, "enemies", None) or [])


def _set(obj: Any, name: str, value: Any) -> None:
    try:
        setattr(obj, name, value)
    except Exception:
        pass


__all__ = ["MAX_AGE_S", "DEFAULT_MAX_AGE_S", "Facts", "snapshot", "stamp", "stale_reason", "checker",
           "max_age", "LevelWatch", "estimate_level", "level_exact", "enemy_level_exact", "level_floor",
           "level_after", "UNSEEN_MIN_S", "MAX_RAISE"]
