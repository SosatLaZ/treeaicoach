"""Data-driven coaching extras, one object for the engine (visual first, never spoken).

:class:`CoachPlus` runs at the coaching rate and ties together:

* :mod:`treeaicoach.spikes` - level 2/3/6/11/16 and legendary-item spikes vs my lane opponent:
  play-gauge reasons (:meth:`factors`, fed to :func:`treeaicoach.coach.stance_factors`) and
  HUD tips ("Tu es 6 avant Darius : ATTAQUE");
* :mod:`treeaicoach.game_plan` - the lane matchup card at game start (one toast) + lines for the
  HUD tips, soul / Baron / Elder power-play facts;
* :mod:`treeaicoach.goals` - one personal goal per game (toast at start, HUD tip when at risk,
  praise toast when met);
* :mod:`treeaicoach.death_cause` - one written line on the likely cause of each death.

Output: :class:`Note` toasts (each with a skill priority: the engine drops those under the
player's level, see :mod:`treeaicoach.skill`), gauge factors and :class:`treeaicoach.tips.TipContext`
fields. Notes are held while a gank threat / fight is on and dropped when stale. Every toast of
this module is limited to one per :data:`NOTE_GAP_S`. Thread-safe, never raises.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

from treeaicoach.death_cause import DeathCoach
from treeaicoach.game_plan import map_fields, matchup_card
from treeaicoach.goals import GoalTracker
from treeaicoach.spikes import SpikeTracker

log = logging.getLogger(__name__)

NOTE_GAP_S = 20.0               # at most one toast of this module every 20 s (engine time)
NOTE_TTL_S = 30.0               # a note held back (fight / gank) is dropped after this
CARD_GT = (30.0, 150.0)         # the matchup card is shown in this game-time window


@dataclass(frozen=True)
class Note:
    kind: str                   # toast kind: "insight" | "praise" | "warning" | "danger"
    title: str
    text: str
    key: str
    prio: int = 3               # skill filter (tips.Tip.prio scale: 1 basic .. 4 urgent)
    hud: bool = False           # also the written HUD line for a few seconds
    t: float = 0.0


class CoachPlus:
    """See the module docstring."""

    def __init__(self, history_loader: Callable[[], list[dict]] | None = None) -> None:
        self._lock = threading.Lock()
        self._loader = history_loader
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._reset()

    def _reset(self) -> None:
        self.spikes = SpikeTracker()
        self.goals = GoalTracker(self._loader)
        self.deaths = DeathCoach()
        self.card: Any = None
        self._card_done = False
        self._queue: list[Note] = []
        self._last_note = -1e9
        self._map: dict[str, Any] = {}
        self._plan: dict[str, Any] = {}
        self._last_gt: float | None = None

    # ------------------------------------------------------------------ tick
    def update(self, t: float, gt: float, game: Any, facts: dict | None, map_state: Any = None,
               busy: bool = False, min_prio: int = 1) -> list[Note]:
        """One coaching tick; returns the toasts to show now (``busy``: gank / fight / dead in a
        fight -> held). Never raises."""
        try:
            with self._lock:
                return self._update(float(t), float(gt), game, facts or {}, map_state, bool(busy), int(min_prio))
        except Exception:
            log.exception("CoachPlus.update failed")
            return []

    def _update(self, t: float, gt: float, game: Any, facts: dict, map_state: Any, busy: bool,
                min_prio: int) -> list[Note]:
        if self._last_gt is not None and gt < self._last_gt - 5.0:
            self._reset()                          # new game
        self._last_gt = gt
        if getattr(game, "me", None) is None:
            return []
        opp = self._opp_alias(facts, game)
        self.spikes.update(gt, game, opp)
        self._map = map_fields(map_state)
        role = facts.get("my_role") or str(getattr(game.me, "position", "") or "").upper() or None
        new: list[Note] = []
        # -- matchup card (once, at game start)
        if not self._card_done and CARD_GT[0] <= gt <= CARD_GT[1]:
            card = matchup_card(game, role, opp)
            if card is not None or gt >= 60.0:
                self._card_done = True
                self.card = card
            if card is not None:
                self._plan = {"plan1": card.lines[0] if card.lines else None,
                              "plan2": card.lines[1] if len(card.lines) > 1 else None, "plan_jg": card.jungle}
                new.append(Note("insight", card.title, card.subtitle, "plan:card", 2, t=t))
        elif gt > CARD_GT[1] + 60.0:
            self._plan = {}
        # -- session goal
        for g in self.goals.update(gt, game, role):
            new.append(Note(g.kind, g.title, g.text, g.key, 3, t=t))
        # -- death cause
        res = self.deaths.update(gt, facts, game)
        if res is not None:
            new.append(Note("warning", "POURQUOI CETTE MORT ?", res[1], f"death:{res[0]}:{int(gt)}", 3,
                            hud=True, t=t))
        self._queue += [n for n in new if n.prio >= min_prio]
        self._queue = [n for n in self._queue if t - n.t <= NOTE_TTL_S]
        dead = bool(getattr(getattr(game, "me", None), "is_dead", False))
        lesson = [n for n in self._queue if n.hud and dead]
        if lesson:                       # dead: the death lesson at once (the fight / gank is over for me)
            self._queue.remove(lesson[0])
            self._last_note = t
            return [lesson[0]]
        if busy or not self._queue or t - self._last_note < NOTE_GAP_S:
            return []
        self._queue.sort(key=lambda n: (-n.prio, n.t))
        note = self._queue.pop(0)
        self._last_note = t
        return [note]

    @staticmethod
    def _opp_alias(facts: dict, game: Any) -> str | None:
        opps = [o for o in facts.get("opponents") or [] if o.get("alias")]
        if not opps:
            return None
        me_pos = str(getattr(getattr(game, "me", None), "position", "") or "").upper()
        by_alias = {str(p.champion_alias).lower(): p for p in getattr(game, "enemies", None) or []}
        for o in opps:                       # the facing laner first (ADC vs ADC, not vs support)
            p = by_alias.get(str(o["alias"]).lower())
            if p is not None and me_pos and str(getattr(p, "position", "")).upper() == me_pos:
                return str(o["alias"])
        return str(opps[0]["alias"])

    # ------------------------------------------------------------------ outputs
    def factors(self) -> list[tuple[float, str]]:
        """Play-gauge reasons (power spikes)."""
        return self.spikes.factors()

    def tip_fields(self) -> dict[str, Any]:
        """Extra :class:`treeaicoach.tips.TipContext` fields."""
        try:
            with self._lock:
                out: dict[str, Any] = {}
                out.update(self.spikes.tip_fields())
                out.update(self._map)
                out.update(self.goals.tip_fields())
                out.update({k: v for k, v in self._plan.items() if v})
                return out
        except Exception:
            return {}

    def death_causes(self) -> list[str]:
        return list(self.deaths.causes)


__all__ = ["CoachPlus", "Note", "NOTE_GAP_S", "buy_fields"]


def buy_fields(rec: Any, in_base: bool = False) -> dict[str, Any]:
    """TipContext ``buy_names`` / ``buy_value`` from an :class:`treeaicoach.itemization.Recommendation`
    (what I can afford now towards my next item). Never raises."""
    try:
        ids = tuple(getattr(rec, "buy_now", ()) or ())
        if not ids or in_base:
            return {}
        from treeaicoach.itemization import load_items
        items = load_items()
        value = sum(int(items[i].gold) for i in ids if i in items)
        if getattr(rec, "completes", False):
            names = str(getattr(rec, "item_name", "") or "")
        else:
            names = " + ".join(list(getattr(rec, "buy_now_names", ()) or ())[:2])
        return {"buy_names": names or None, "buy_value": value}
    except Exception:
        return {}
