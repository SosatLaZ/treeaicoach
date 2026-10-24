"""SPIKE PLANNER: the purchase after which I stop losing my lane trades, and the gold to back with.

One planner rule of :mod:`treeaicoach.macro` (same card / banner path, no second channel), from the
public Tab data of the Live Client API only (items, levels):

* **Lane lead** (:func:`lead_score`): item gold gap with my role's lane opponent in thousands +
  :data:`LEVEL_K` per level of gap. Measured on the user's 18 games with LCU truth
  (``python tools/spike_truth.py <games dir>``, :data:`VALIDATION`, my 10 games in a solo lane / ADC):
  from :data:`FLIP` (+0.4) I win the next 2 min of the lane (7 kills on him, 2 deaths), below 0 I
  lose them (3 kills, 37 deaths in 59 minutes); after a back that flips the lead I die 2 times in
  10, after the other backs 14 in 26.
  The draft's phase scores (:func:`treeaicoach.draft_edge.phase_scores`) did NOT separate the
  outcomes on these games: the live gap decides, not the champion curves.
* ``gc_spike`` "Après Phage tu gagnes l'échange : back à 1 100 or" (my gold a little short of the
  flip, :data:`PLAN_AHEAD`; a soft call, :data:`treeaicoach.macro.SOFT_KINDS`: any other call
  replaces it), then, once I hold it, a ``wave_recall`` (same kind = same cooldown as
  the planner's own recall call, which it replaces with the WHY): "Rentre acheter Phage : après, tu
  gagnes l'échange". The item is the next step of my build (:func:`treeaicoach.itemization.recommend`
  with that gold), the gold the least amount whose purchase closes the gap (gold spent = item
  value added).

NOT built, the data said no (:data:`VALIDATION` ``"back"``): predicting the opponent's next back
("Darius rentre vers 1 300 or : il en a ~1 150"). Even with his TRUE earned gold, a threshold
learned from his previous backs in the same game (or a fixed 1 250) puts the predicted back within
30 s of the truth in 20-33 % of 40 backs (60 % was the bar): players back on HP and waves, not on
a gold amount. His hidden gold estimated from income (passive + creep score + kills) is good
(median error 72 PO for a top laner) but item value minus consumables drifts, and subtracting it
from the lead destroyed the signal above.

Riot policy: Tab data and my own gold only. Pure (no I/O), never raises.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from treeaicoach import macro as M
from treeaicoach.fmtutil import finite as _f

log = logging.getLogger(__name__)

KIND = "gc_spike"
#: lead = item gold gap / 1000 + LEVEL_K x level gap (fit on the truth games, tools/spike_truth.py)
LEVEL_K = 0.35
#: lead from which the next 2 minutes of the lane are even
FLIP = 0.4
#: a single back cannot close a bigger gap (item gold)
MAX_NEED = 1500.0
#: the plan call shows when my gold is this short of the flip gold (about one wave + passive gold)
PLAN_AHEAD = 300.0
#: never a back for less than this (a back costs a wave and the opponent buys too)
MIN_BACK_GOLD = 800
#: roles that manage their own recall (the support follows his ADC, as in macro wave_recall)
ROLES = ("TOP", "MIDDLE", "BOTTOM")
FROM_GT = 150.0
GOLD_STEP = 50
#: truth replay (tools/spike_truth.py, 18 games with LCU truth, laning minutes 2:30-14:00)
VALIDATION = {
    "games": 10,                    # my role TOP / MIDDLE / BOTTOM (18 with the support games: same direction)
    "lead": {"ahead": {"minutes": 31, "won": 7, "lost": 2}, "close": {"minutes": 20, "won": 1, "lost": 8},
             "behind": {"minutes": 59, "won": 3, "lost": 37}},
    "draft": {"mine": {"minutes": 29, "won": 2, "lost": 20}, "theirs": {"minutes": 14, "won": 0, "lost": 8}},
    "backs": {"flipped": {"n": 10, "lost_2min": 2}, "no_flip": {"n": 26, "lost_2min": 14}},
    "back": {"backs": 40, "within_30s_learned": 0.33, "within_30s_fixed": 0.20, "threshold_err_median": 403,
             "income_err_median_top": 73},
}

_CACHE: dict[tuple, tuple[int, str | None] | None] = {}
_CACHE_LOCK = threading.Lock()


def lead_score(my_items_gold: Any, opp_items_gold: Any, my_level: Any, opp_level: Any) -> float:
    """My lane lead (> 0 mine): item gold gap in thousands + :data:`LEVEL_K` per level."""
    gap = (_f(my_items_gold, 0.0) or 0.0) - (_f(opp_items_gold, 0.0) or 0.0)
    lv = (_f(my_level, 0.0) or 0.0) - (_f(opp_level, 0.0) or 0.0)
    return gap / 1000.0 + LEVEL_K * lv


def need_gold(lead: float) -> float:
    """Item gold to add before the lead reaches :data:`FLIP` (0 when already there)."""
    return max(0.0, (FLIP - float(lead)) * 1000.0)


def _spent(rec: Any, owned: list[int], gold: int) -> tuple[int, str | None]:
    """``(gold spent, name)`` of the purchase the recommendation makes with ``gold``."""
    from treeaicoach import itemization as IT

    items = IT.load_items()
    ids, done = IT.plan_purchase(int(rec.item_id), owned, gold, items)
    pool = list(owned)
    spent = 0
    names: list[str] = []
    for iid in ids:
        spent += IT._remaining(int(iid), pool, items)
        it = items.get(int(iid))
        if it is not None and it.name:
            names.append(it.name)
    if done:
        name = str(getattr(rec, "item_name", "") or "") or None
    elif len(set(names)) == 1 and len(names) == 1:
        name = names[0]
    elif len(names) == 2:
        name = " + ".join(names)
    else:
        name = None                                   # three components: the gold alone says it better
    return spent, name


def flip_purchase(game: Any, role: Any, need: float, owned: Any) -> tuple[int, str | None] | None:
    """``(gold, item name)``: the least gold (steps of :data:`GOLD_STEP`) whose purchase on my build
    adds at least ``need`` item gold, with the name of what it buys. None when unknown / out of reach
    of :data:`MAX_NEED` + one step. Cached per (inventory, need)."""
    try:
        own = [int(x) for x in owned or () if int(x) > 0]
        target = int(max(0.0, float(need)))
        key = (tuple(sorted(own)), target // GOLD_STEP, str(role or ""),
               str(getattr(getattr(game, "me", None), "champion_alias", "") or ""))
        with _CACHE_LOCK:
            if key in _CACHE:
                return _CACHE[key]
        from treeaicoach import itemization as IT

        out = None
        g = max(GOLD_STEP, -(-target // GOLD_STEP) * GOLD_STEP)
        while g <= MAX_NEED + 4 * GOLD_STEP:
            rec = IT.recommend(game, role, gold=float(g))
            if rec is None:
                break
            spent, name = _spent(rec, own, g)
            if spent >= target:
                out = (g, name)
                break
            g += GOLD_STEP
        if out is None and target <= MAX_NEED:
            out = (-(-target // GOLD_STEP) * GOLD_STEP, None)     # no build data: the gold alone
        with _CACHE_LOCK:
            if len(_CACHE) > 128:
                _CACHE.clear()
            _CACHE[key] = out
        return out
    except Exception:
        log.debug("flip purchase failed", exc_info=True)
        return None


def _fmt(g: int) -> str:
    """1100 -> "1 100" (French thousands, narrow no-break space)."""
    return f"{int(g):,}".replace(",", " ")


def rule_spike(ctx: M.MacroCtx) -> M.GeniusCall | None:
    """"Après Phage tu gagnes l'échange : back à 1 100 or", then "Rentre acheter Phage : après, tu
    gagnes l'échange"."""
    try:
        if not ctx.me_alive or ctx.in_base or ctx.role not in ROLES:
            return None
        if not (FROM_GT <= ctx.gt < M.LANING_END_GT):
            return None
        from treeaicoach.game_changers import _fit, _in_my_lane
        from treeaicoach.live_edge import _items, item_gold, lane_opponent

        if not _in_my_lane(ctx):
            return None
        if ctx.hp is not None and ctx.hp < M.LOW_HP:
            return None                                  # low HP: wave_recall says "rentre" for that
        me = getattr(ctx.game, "me", None)
        opp = lane_opponent(ctx.game, role=ctx.role)
        if me is None or opp is None or bool(getattr(opp, "is_dead", False)):
            return None
        try:
            from treeaicoach.fog_intel import purchase_t

            if purchase_t(ctx, str(getattr(opp, "champion_alias", "") or "")) is not None:
                return None                              # he is in base: plates / sync recall say it
        except Exception:
            log.debug("fog intel unavailable", exc_info=True)
        mine = _items(me)
        my_ig, op_ig = item_gold(mine), item_gold(_items(opp))
        lead = lead_score(my_ig, op_ig, getattr(me, "level", 0), getattr(opp, "level", 0))
        if lead >= FLIP + (0.05 if ctx.keep else 0.0):
            return None
        need = need_gold(lead)
        if need > MAX_NEED:
            return None
        fp = flip_purchase(ctx.game, ctx.role, need, mine)
        if fp is None:
            return None
        gold_needed, name = fp
        if gold_needed < MIN_BACK_GOLD:
            return None
        from treeaicoach.game_changers import _draft_says_no

        win = "tu passes devant" if _draft_says_no(ctx) else "tu gagnes l'échange"   # never against the draft card
        oname = str(getattr(opp, "champion_name", "") or "") or "ton adversaire"
        gap = int(round(my_ig - op_ig))
        lv_gap = int(_f(getattr(me, "level", 0), 0) or 0) - int(_f(getattr(opp, "level", 0), 0) or 0)
        factors = (f"or d'objets {gap:+d}", f"niveaux {lv_gap:+d}", f"il manque {int(need)} or d'objets")
        v = VALIDATION["lead"]
        stat = (f"Sur tes parties, avec cette avance tu gagnes ta voie ({v['ahead']['won']} kills pour "
                f"{v['ahead']['lost']} morts) ; derrière, {v['behind']['won']} pour {v['behind']['lost']}.")
        if ctx.gold >= gold_needed:
            text = _fit(f"Rentre acheter {name} : après, {win}" if name else "",
                        f"Rentre : avec {_fmt(gold_needed)} or {win}",
                        f"Rentre acheter : après, {win}")
            why = (f"Ton or ({int(ctx.gold)}) te fait passer devant {oname}. Tes parties, avec cette avance : "
                   f"{v['ahead']['won']} kills pour {v['ahead']['lost']} morts (derrière : {v['behind']['won']}/"
                   f"{v['behind']['lost']}).")
            return M._call("wave_recall", f"wave_recall:spike:{my_ig}", "RENTRE ACHETER", text, why,
                           M.FOUNTAIN.get(ctx.my_team or ""), tier="basic", score=0.56, priority=59,
                           color="gold", life=15.0, label="BASE", factors=factors)
        if gold_needed - ctx.gold > PLAN_AHEAD and not ctx.keep:
            return None
        g = _fmt(gold_needed)
        text = _fit(f"Après {name} {win} : back à {g} or" if name else "",
                    f"Back à {g} or : après, {win} contre {oname}",
                    f"Back à {g} or : après, {win}")
        why = (f"Il te manque {int(gold_needed - ctx.gold)} or. "
               + (f"Avec {name}, " if name else "Avec cet achat, ")
               + f"tu as plus d'or d'objets que {oname}. Ne rentre pas avant, ne meurs pas d'ici là. " + stat)
        return M._call(KIND, f"{KIND}:{my_ig}:{gold_needed // 100}", "TON PROCHAIN ACHAT", text, why, None,
                       tier="basic", score=0.42, priority=50, color="gold", life=25.0, label="ACHAT",
                       factors=factors)
    except Exception:
        log.debug("spike rule failed", exc_info=True)
        return None


RULES = (rule_spike,)

__all__ = ["KIND", "LEVEL_K", "FLIP", "VALIDATION", "lead_score", "need_gold", "flip_purchase", "rule_spike",
           "RULES"]
