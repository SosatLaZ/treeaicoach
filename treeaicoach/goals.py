"""Session goal: ONE personal goal per game, picked from my past games, checked live.

:func:`pick_goal` looks at my last recorded games (``report.list_games``: deaths, CS, duration)
and picks the most useful single goal for this game:

* a carry (top / mid / ADC) whose CS per minute is clearly under the role target gets a CS goal
  slightly above his average ("6,5 sbires par minute");
* else a player who dies a lot gets a deaths goal one under his average ("4 morts maximum");
* without history: a role default.

:class:`GoalTracker` shows it once at game start (toast "OBJECTIF DE LA PARTIE"), warns ONCE when
it is at risk (last death allowed, CS clearly behind at 10:00 / 15:00 / 20:00 - written HUD tip),
and praises it ONCE when it is met (CS at 20:00, deaths at 25:00). Never nags after a failed goal.
Only my own data (Live Client ``activePlayer`` / my scores). Pure Python, never raises.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Callable

from treeaicoach.fmtutil import finite_loose as _f

log = logging.getLogger(__name__)

CS_TARGET = {"TOP": 7.0, "MIDDLE": 7.0, "BOTTOM": 7.5}
MIN_GAME_S = 900.0              # past games shorter than 15 min are ignored
HISTORY_GAMES = 10
SHOW_GT = (30.0, 120.0)         # the goal toast is shown in this game-time window
CS_CHECKS = (600.0, 900.0, 1200.0)
CS_PRAISE_GT = 1200.0
DEATHS_PRAISE_GT = 1500.0


@dataclass(frozen=True)
class Goal:
    kind: str                    # "cs" | "deaths"
    target: float                # sbires/min, or max deaths
    label: str                   # "6,5 sbires par minute" / "4 morts maximum"
    why: str                     # "ta moyenne : 5,4" (shown with the goal)

    @property
    def subtitle(self) -> str:
        return f"{self.label} ({self.why})" if self.why else self.label


def _dec(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


def pick_goal(history: list[dict] | None, role: str | None) -> Goal:
    """The goal of this game from past game summaries (``deaths``, ``cs``, ``duration``). Pure."""
    role = str(role or "").upper()
    games = []
    for g in history or []:
        d, cs, dur = _f(g.get("deaths")), _f(g.get("cs")), _f(g.get("duration"))
        if dur is None or dur < MIN_GAME_S or g.get("incomplete"):
            continue
        games.append((d, cs / (dur / 60.0) if cs is not None else None))
        if len(games) >= HISTORY_GAMES:
            break
    deaths = [d for d, _c in games if d is not None]
    cspm = [c for _d, c in games if c is not None]
    target = CS_TARGET.get(role)
    if len(games) >= 2:
        avg_cs = sum(cspm) / len(cspm) if cspm else None
        avg_d = sum(deaths) / len(deaths) if deaths else None
        if target is not None and avg_cs is not None and avg_cs < target - 0.5:
            goal = round(min(target, avg_cs + 0.7) * 2) / 2
            return Goal("cs", goal, f"{_dec(goal)} sbires par minute", f"ta moyenne : {_dec(avg_cs)}")
        if avg_d is not None and avg_d >= 4.5:
            lim = max(3, int(round(avg_d)) - 1)
            return Goal("deaths", lim, f"{lim} morts maximum", f"ta moyenne : {_dec(avg_d)}")
        if avg_d is not None:
            lim = max(2, int(math.floor(avg_d)))
            return Goal("deaths", lim, f"{lim} morts maximum", f"ta moyenne : {_dec(avg_d)}")
    if target is not None:
        return Goal("cs", 6.0, "6 sbires par minute", "")
    return Goal("deaths", 5, "5 morts maximum", "")


def load_history(limit: int = HISTORY_GAMES + 5) -> list[dict]:
    """My recorded games, newest first (``report.list_games``), [] on any problem."""
    try:
        from treeaicoach.report import list_games
        return list(list_games(limit=limit) or [])
    except Exception:
        log.debug("goal history unavailable", exc_info=True)
        return []


@dataclass(frozen=True)
class GoalNote:
    kind: str                    # toast kind: "insight" | "praise"
    title: str
    text: str
    key: str


class GoalTracker:
    """Per-game goal (see the module docstring). Not thread-safe on its own (owned by CoachPlus)."""

    def __init__(self, history_loader: Callable[[], list[dict]] | None = None) -> None:
        self._loader = history_loader or load_history
        self.reset()

    def reset(self) -> None:
        self.goal: Goal | None = None
        self._shown = False
        self._done: set[str] = set()
        self.status: str = "en cours"           # "en cours" | "réussi" | "raté"
        self.risk: str | None = None             # tip key when the goal is at risk now

    def _ensure(self, role: str | None) -> Goal:
        if self.goal is None:
            self.goal = pick_goal(self._loader(), role)
        return self.goal

    def update(self, gt: float, game: Any, role: str | None) -> list[GoalNote]:
        me = getattr(game, "me", None)
        if me is None:
            return []
        g = self._ensure(role)
        out: list[GoalNote] = []
        scores = getattr(me, "scores", None) or {}
        deaths = int(_f(scores.get("deaths")) or 0)
        cs = _f(scores.get("creepScore")) or 0.0
        cspm = cs / (gt / 60.0) if gt >= 60 else 0.0
        if not self._shown and SHOW_GT[0] <= gt <= SHOW_GT[1]:
            self._shown = True
            out.append(GoalNote("insight", "OBJECTIF DE LA PARTIE", g.subtitle, "goal:show"))
        self.risk = None
        if g.kind == "deaths":
            if deaths > g.target:
                self.status = "raté"
            elif deaths == int(g.target) and self.status == "en cours":
                self.risk = "last_death"
            if gt >= DEATHS_PRAISE_GT and deaths <= g.target and "praise" not in self._done:
                self._done.add("praise")
                self.status = "réussi"
                out.append(GoalNote("praise", "OBJECTIF TENU",
                                    f"{deaths} mort{'s' if deaths > 1 else ''} à 25:00 (max {int(g.target)})", "goal:ok"))
        else:
            # behind at a checkpoint: a written warning for one minute (the tip rotator shows it once)
            if any(0.0 <= gt - c < 60.0 for c in CS_CHECKS) and cspm < g.target - 0.3:
                self.risk = "cs_behind"
            if gt >= CS_PRAISE_GT and "praise" not in self._done:
                self._done.add("praise")
                if cspm >= g.target:
                    self.status = "réussi"
                    out.append(GoalNote("praise", "OBJECTIF ATTEINT",
                                        f"{_dec(cspm)} sbires par minute (objectif {_dec(g.target)})", "goal:ok"))
                else:
                    self.status = "raté"
        return out

    def tip_fields(self) -> dict[str, Any]:
        g = self.goal
        if g is None:
            return {}
        return {"goal_kind": g.kind, "goal_target": g.target, "goal_risk": self.risk}


__all__ = ["Goal", "GoalNote", "GoalTracker", "pick_goal", "load_history"]
