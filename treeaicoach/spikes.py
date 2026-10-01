"""Power spikes in lane: who reaches the key levels / finishes a big item FIRST (Live Client data).

A lane is decided by short windows: the first to reach level 2 / 3 (more spells), level 6
(ultimate), 11 / 16 (ultimate ranks), or to complete a legendary item, can attack for a
little while; the other must step back until he catches up. Everything here is public Tab data
of the official Live Client API (levels and items of the 10 players): no cooldown, no hidden info.

:class:`SpikeTracker` (fed once per coaching tick) keeps, for me and my lane opponent, when each
key level / legendary item was reached, and exposes the ACTIVE spike window:

* :class:`Spike` ``who="me"`` (I am ahead: ATTAQUE) or ``"opp"`` (he is: recule), ``what`` =
  ``"level"`` or ``"item"``, the level / item name, since when; a level window ends when the
  other one catches up (or after :data:`LEVEL_WINDOW_S`), an item window after
  :data:`ITEM_WINDOW_S` or when the other one also completes a legendary;
* :meth:`SpikeTracker.factors` - weighted reasons for the play gauge (``coach.stance_factors``):
  ``(+1.5, "tu es 6 avant Darius")`` / ``(-1.5, "Darius est 6, pas toi")``;
* :meth:`SpikeTracker.tip_fields` - fields for :class:`treeaicoach.tips.TipContext`.

Pure functions :func:`level_spike` and :func:`item_spike` are reusable by other modules (play
ratings...). Pure Python, thread-safe, never raises from its public methods.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Any, Iterable

log = logging.getLogger(__name__)

SPIKE_LEVELS: tuple[int, ...] = (2, 3, 6, 11, 16)
#: how long a level lead stays a "window" at most (the other one usually catches up sooner)
LEVEL_WINDOW_S: dict[int, float] = {2: 35.0, 3: 35.0, 6: 75.0, 11: 60.0, 16: 60.0}
ITEM_WINDOW_S = 90.0
#: gauge weights (+ = play stronger), the plain level / gold gaps are already counted elsewhere
LEVEL_WEIGHT: dict[int, float] = {2: 1.0, 3: 0.75, 6: 1.5, 11: 1.0, 16: 0.75}
ITEM_WEIGHT = 1.0
LEGENDARY_MIN_GOLD = 2200        # a completed item at least this expensive counts as a spike


@dataclass(frozen=True)
class Spike:
    who: str                     # "me" | "opp"
    what: str                    # "level" | "item"
    level: int = 0
    item: str = ""
    opp: str = ""                # lane opponent display name
    since: float = 0.0           # game time

    @property
    def mine(self) -> bool:
        return self.who == "me"

    @property
    def weight(self) -> float:
        w = LEVEL_WEIGHT.get(self.level, 0.75) if self.what == "level" else ITEM_WEIGHT
        return w if self.mine else -w

    @property
    def reason(self) -> str:
        """Short French reason for the gauge ("tu es 6 avant Darius")."""
        opp = self.opp or "ton adversaire"
        if self.what == "level":
            return f"tu es {self.level} avant {opp}" if self.mine else f"{opp} est {self.level}, pas toi"
        return f"{self.item} fini, pas {opp}" if self.mine else f"{opp} vient de finir {self.item}"


def level_spike(my_level: int, opp_level: int) -> tuple[str, int] | None:
    """``("me" | "opp", level)`` when exactly one of the two reached a key level the other has not
    (the highest such level), else None. Pure."""
    try:
        a, b = int(my_level), int(opp_level)
    except (TypeError, ValueError):
        return None
    best = None
    for lvl in SPIKE_LEVELS:
        if a >= lvl > b:
            best = ("me", lvl)
        elif b >= lvl > a:
            best = ("opp", lvl)
    return best


def _legendaries(items: Iterable[Any]) -> list[tuple[int, str]]:
    """``(id, name)`` of the completed big items in an inventory (bundled item table)."""
    out = []
    try:
        from treeaicoach.scoreboard import item_info
    except Exception:
        return out
    for i in items or ():
        if isinstance(i, bool) or not isinstance(i, int):
            continue
        info = item_info(i)
        if info and info[2] == "legendary" and int(info[1]) >= LEGENDARY_MIN_GOLD:
            out.append((int(i), str(info[0])))
    return out


def item_spike(my_items: Iterable[Any], opp_items: Iterable[Any]) -> tuple[str, int] | None:
    """``("me" | "opp", legendary count difference)`` when one side has more completed legendary
    items than the other, else None. Pure."""
    a, b = len(_legendaries(my_items)), len(_legendaries(opp_items))
    if a > b:
        return "me", a - b
    if b > a:
        return "opp", b - a
    return None


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


class SpikeTracker:
    """Who spiked first in my lane (see the module docstring). Thread-safe, never raises."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._opp_alias: str | None = None
            self._lvl_win: Spike | None = None
            self._item_win: Spike | None = None
            self._items: dict[str, set[int]] = {"me": set(), "opp": set()}
            self._primed = False
            self._last_gt: float | None = None

    # ------------------------------------------------------------------ input
    def update(self, gt: float, game: Any, opp_alias: str | None) -> Spike | None:
        """One tick; returns the active spike (item spikes win over level spikes). Never raises."""
        try:
            with self._lock:
                return self._update(float(gt), game, opp_alias)
        except Exception:
            log.debug("SpikeTracker.update failed", exc_info=True)
            return None

    def _update(self, gt: float, game: Any, opp_alias: str | None) -> Spike | None:
        if self._last_gt is not None and gt < self._last_gt - 5.0:
            self._lvl_win = self._item_win = None
            self._items = {"me": set(), "opp": set()}
            self._primed = False
        self._last_gt = gt
        me = getattr(game, "me", None)
        opp = None
        if opp_alias:
            for p in getattr(game, "enemies", None) or []:
                if str(getattr(p, "champion_alias", "") or "").lower() == str(opp_alias).lower():
                    opp = p
                    break
        if me is None or opp is None:
            self._lvl_win = self._item_win = None
            return None
        if opp_alias != self._opp_alias:                    # lane swap: start over
            self._opp_alias = opp_alias
            self._lvl_win = self._item_win = None
            self._items = {"me": set(), "opp": set()}
            self._primed = False
        name = str(getattr(opp, "champion_name", "") or opp_alias)
        my_lvl, opp_lvl = int(_f(getattr(me, "level", 1)) or 1), int(_f(getattr(opp, "level", 1)) or 1)
        # -- levels: a window opens when one side reaches a key level first
        ls = level_spike(my_lvl, opp_lvl)
        cur = self._lvl_win
        if ls is None:
            self._lvl_win = None
        elif cur is None or (cur.who, cur.level) != ls:
            self._lvl_win = Spike(ls[0], "level", ls[1], opp=name, since=gt)
        # -- items: a NEW legendary completed by one side, the other not on par
        mine = dict(_legendaries(getattr(me, "items", None) or ()))
        theirs = dict(_legendaries(getattr(opp, "items", None) or ()))
        new_me = [n for i, n in mine.items() if i not in self._items["me"]]
        new_opp = [n for i, n in theirs.items() if i not in self._items["opp"]]
        self._items = {"me": set(mine), "opp": set(theirs)}
        if self._primed:
            if new_me and len(mine) > len(theirs):
                self._item_win = Spike("me", "item", item=new_me[0], opp=name, since=gt)
            elif new_opp and len(theirs) > len(mine):
                self._item_win = Spike("opp", "item", item=new_opp[0], opp=name, since=gt)
            elif new_me or new_opp:
                self._item_win = None                       # the other caught up
        self._primed = True
        return self._active(gt)

    def _active(self, gt: float) -> Spike | None:
        iw = self._item_win
        if iw is not None and not (0.0 <= gt - iw.since <= ITEM_WINDOW_S):
            iw = None
        lw = self._lvl_win
        if lw is not None and not (0.0 <= gt - lw.since <= LEVEL_WINDOW_S.get(lw.level, 60.0)):
            lw = None
        if iw is not None and lw is not None:
            return lw if lw.level >= 6 and abs(lw.weight) > abs(iw.weight) else iw
        return iw or lw

    # ------------------------------------------------------------------ output
    def current(self) -> Spike | None:
        with self._lock:
            return self._active(self._last_gt or 0.0) if self._last_gt is not None else None

    def factors(self) -> list[tuple[float, str]]:
        """Gauge reasons of the active windows (level and item, both may count)."""
        try:
            with self._lock:
                gt = self._last_gt or 0.0
                out = []
                for w in (self._lvl_win, self._item_win):
                    if w is None:
                        continue
                    limit = LEVEL_WINDOW_S.get(w.level, 60.0) if w.what == "level" else ITEM_WINDOW_S
                    if 0.0 <= gt - w.since <= limit:
                        out.append((w.weight, w.reason))
                return out
        except Exception:
            return []

    def tip_fields(self) -> dict[str, Any]:
        """Fields of :class:`treeaicoach.tips.TipContext` (``spike_*``)."""
        s = self.current()
        if s is None:
            return {}
        return {"spike_who": s.who, "spike_what": s.what, "spike_level": s.level, "spike_item": s.item}


__all__ = ["Spike", "SpikeTracker", "level_spike", "item_spike", "SPIKE_LEVELS"]
