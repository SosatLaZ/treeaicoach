"""OBJECTIVE PLANNER: the next 90 s before an epic objective (dragon, grubs, Herald, Baron, Elder).

Planner rules of :mod:`treeaicoach.macro` (same card / banner path, no second channel), from the
Live Client data only: the spawn timers of :mod:`treeaicoach.objectives` (``ctx.objectives``: the
season 2026 set, ``ai_advisor.SEASON_RULES``), my gold and my build's next purchase
(:func:`treeaicoach.itemization.recommend`, the gold it spends as in :mod:`treeaicoach.spike_planner`),
the walk base -> pit (:func:`treeaicoach.fog_intel.fountain_eta`, geodesic, at my speed), alive /
dead with the respawn timers and the fight calculator (:func:`treeaicoach.fog_intel.fight_odds`):

* back timing (kind ``wave_recall``: one back message per trip, shared cooldown with the other recall
  calls): "Rentre maintenant : Phage et retour pour le Dragon (1:30)" when a recall now (channel +
  shop + walk) lands me at the pit before the spawn with a real purchase; ``gc_obj_stay`` "Ne rentre
  pas : Dragon dans 0:40" when it would not (the other recall calls wait meanwhile:
  :func:`recall_blocked`, used by :func:`treeaicoach.macro.evaluate`);
* ``gc_obj_safe`` (soft) "Ne prends aucun risque : mourir = Dragon perdu", 40 s before a spawn the fight
  calculator does not call lost.

Measured on the user's 20 games with LCU truth (``python tools/objective_truth.py <games dir>``,
:data:`VALIDATION`; 72 spawns my role plays): at the pit at the spawn we took it 9 / 14 (64 %),
otherwise 18 / 51 (35 %); a fresh item at the pit did not separate (n = 3 without one). Backs that
land (last back 50-150 s before the spawn) put me at the pit 4 / 14, late backs (< 50 s) 2 / 12
and the late ones lost the objective 7 / 12 (58 %, early 5 / 14). Dying in the 40 s before a spawn
when the fight was not lost (even or ahead at S-40): dragon / Baron lost 4 / 6, nobody of us died 6 / 17
(grubs / Herald: no death in that window, so no call).
NOT built, the data said no: a "balise la rivière maintenant" set-up call (ward score up in the 75 s
before: ours 42 % vs 40 % without; a control ward placed: 1 case) - coach.MapCoach's
``objective_setup`` line stays as it was.

Riot policy: Live Client data only; no automation, no memory reading. Pure (no I/O), never raises.
"""

from __future__ import annotations

import logging
import threading

from treeaicoach import geometry
from treeaicoach import macro as M

log = logging.getLogger(__name__)

KIND_STAY = "gc_obj_stay"
KIND_SAFE = "gc_obj_safe"
#: roles that play each objective (the others are told nothing: a top laner is not sent to dragon)
ROLE_PLAYS: dict[str, tuple[str, ...]] = {
    "dragon": ("BOTTOM", "UTILITY", "JUNGLE", "MIDDLE"),
    "elder": ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"),
    "grubs": ("TOP", "JUNGLE", "MIDDLE"),
    "herald": ("TOP", "JUNGLE", "MIDDLE"),
    "baron": ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"),
}
NAME = {"dragon": "Dragon", "elder": "Dragon ancestral", "grubs": "Larves", "herald": "Héraut", "baron": "Baron"}
POUR = {"dragon": "pour le Dragon", "elder": "pour l'ancestral", "grubs": "pour les larves",
        "herald": "pour le Héraut", "baron": "pour le Baron"}
LOST = {"dragon": "Dragon perdu", "elder": "ancestral perdu", "grubs": "larves perdues",
        "herald": "Héraut perdu", "baron": "Baron perdu"}
#: recall channel (s; mid role quest: 4 s) + shopping
RECALL_S = 8.0
RECALL_MID_S = 4.0
SHOP_S = 4.0
#: my walking speed (units / s: boots + the fountain speed boost); fog_intel.fountain_eta uses an upper bound
WALK_UNITS = 390.0
#: a back must land this much before the spawn (set-up time)
LAND_MARGIN_S = 10.0
#: "Rentre maintenant" needs this much more than the trip (it never runs into "Ne rentre pas")
BACK_LEAD_S = 15.0
#: "Rentre maintenant" looks this far ahead (s)
BACK_AHEAD_S = 120.0
#: a purchase worth a back before an objective (gold the recommended buy spends)
MIN_BUY = 700
#: "Ne rentre pas" from this gold (the player is tempted) ...
STAY_GOLD = M.RECALL_GOLD
#: ... and down to this many seconds before the spawn (closer: the countdown line says it)
STAY_MIN_S = 20.0
STAY_HP = 0.5
#: don't-die window before the spawn
SAFE_S = 40.0
SAFE_MIN_S = 10.0
#: the objectives the don't-die rule speaks for (the measured ones: grubs / Herald had no case)
SAFE_KEYS = frozenset({"dragon", "elder", "baron"})
VALIDATION = {
    "games": 20, "spawns": 72,
    "at_pit": {"n": 14, "ours": 9}, "not_at_pit": {"n": 51, "ours": 18},
    "fresh_item_at_pit": {"with": [7, 11], "without": [2, 3]},
    "back_early": {"n": 14, "at_pit": 4, "theirs": 5}, "back_late": {"n": 12, "at_pit": 2, "theirs": 7},
    "died40_not_lost": {"n": 6, "theirs": 4}, "nobody_died_not_lost": {"n": 17, "theirs": 6},
    "ward": {"with": [10, 24], "without": [19, 48]}, "control_ward": 1,
}

_CACHE: dict[tuple, tuple[int, str | None] | None] = {}
_CACHE_LOCK = threading.Lock()


def _clock(s: float) -> str:
    s = max(0, int(round(s)))
    return f"{s // 60}:{s % 60:02d}"


def next_objective(ctx: M.MacroCtx, lo: float, hi: float) -> tuple[str, float] | None:
    """``(key, s before the spawn)`` of the first objective my role plays spawning in [lo, hi] s
    (not up yet). The Baron only from 20:00, grubs / Herald only if not gone."""
    best = None
    for key, (alive, rem) in (ctx.objectives or {}).items():
        if alive or rem is None or key not in M.PIT_UV or not (lo <= rem <= hi):
            continue
        if ctx.role not in ROLE_PLAYS.get(key, ()):
            continue
        if best is None or rem < best[1]:
            best = (key, float(rem))
    return best


def back_trip_s(ctx: M.MacroCtx, key: str) -> float | None:
    """Recall now -> standing at ``key``'s pit: channel + shop + walk (geodesic, my speed)."""
    try:
        from treeaicoach.fog_intel import BASE_SPEED_UNITS, fountain_eta

        eta = fountain_eta(ctx.my_team, M.PIT_UV.get(key))
        if eta is None:
            return None
        rc = RECALL_MID_S if ctx.role == "MIDDLE" else RECALL_S
        return rc + SHOP_S + eta * BASE_SPEED_UNITS / WALK_UNITS
    except Exception:
        log.debug("back trip failed", exc_info=True)
        return None


def purchase(ctx: M.MacroCtx) -> tuple[int, str | None] | None:
    """``(gold spent, name)`` of what my build buys with my gold now (cached)."""
    try:
        me = getattr(ctx.game, "me", None)
        if me is None:
            return None
        from treeaicoach.live_edge import _items

        own = _items(me)
        g = int(ctx.gold // 50) * 50
        key = (tuple(sorted(own)), g, str(ctx.role or ""), str(getattr(me, "champion_alias", "") or ""))
        with _CACHE_LOCK:
            if key in _CACHE:
                return _CACHE[key]
        from treeaicoach import itemization as IT
        from treeaicoach.spike_planner import _spent

        rec = IT.recommend(ctx.game, ctx.role, gold=float(g))
        out = _spent(rec, own, g) if rec is not None else None
        with _CACHE_LOCK:
            if len(_CACHE) > 128:
                _CACHE.clear()
            _CACHE[key] = out
        return out
    except Exception:
        log.debug("objective purchase failed", exc_info=True)
        return None


def _out_of_base(ctx: M.MacroCtx) -> bool:
    if ctx.in_base:
        return False
    if ctx.me_uv is not None and ctx.my_team and geometry.in_fountain(ctx.me_uv[0], ctx.me_uv[1], ctx.my_team):
        return False
    return True


def _stay_window(ctx: M.MacroCtx, lo: float | None = None) -> tuple[str, float] | None:
    """The objective a back now would miss, spawning in [``lo``, 75] s: ``(key, s)``, else None."""
    if not ctx.me_alive or not _out_of_base(ctx) or ctx.gt < 240.0:
        return None
    if ctx.hp is not None and ctx.hp < STAY_HP:
        return None                                      # low: the back is right, objective or not
    nxt = next_objective(ctx, STAY_MIN_S * (0.7 if ctx.keep else 1.0) if lo is None else lo, 75.0)
    if nxt is None:
        return None
    trip = back_trip_s(ctx, nxt[0])
    if trip is None or nxt[1] >= trip + LAND_MARGIN_S + (5.0 if ctx.keep else 0.0):
        return None                                      # a back still lands: rule_back_now says it
    return nxt


def recall_blocked(ctx: M.MacroCtx) -> bool:
    """A recall now would miss the objective my role plays (the other recall calls wait)."""
    try:
        return _stay_window(ctx, lo=0.0) is not None    # down to the spawn (the line stops at STAY_MIN_S)
    except Exception:
        return False


def rule_back_now(ctx: M.MacroCtx) -> M.GeniusCall | None:
    """"Rentre maintenant : Phage et retour pour le Dragon (1:30)"."""
    try:
        if not ctx.me_alive or not _out_of_base(ctx) or ctx.gt < 240.0:
            return None
        nxt = next_objective(ctx, 30.0, BACK_AHEAD_S + (10.0 if ctx.keep else 0.0))
        if nxt is None:
            return None
        key, rem = nxt
        trip = back_trip_s(ctx, key)
        if trip is None or rem < trip + LAND_MARGIN_S + (0.0 if ctx.keep else BACK_LEAD_S):
            return None                                  # closer: rule_stay's window (never back to back)
        if not ctx.keep and rem > trip + 75.0:
            return None                                  # too early: farming one more wave lands too
        buy = purchase(ctx)
        if buy is None or buy[0] < MIN_BUY:
            return None
        spent, name = buy
        from treeaicoach.game_changers import _fit

        when = _clock(rem)
        text = _fit(f"Rentre maintenant : {name} et retour {POUR[key]} ({when})" if name else "",
                    f"Rentre : {name} et retour {POUR[key]} ({when})" if name else "",
                    f"Rentre maintenant : achète et retour {POUR[key]} ({when})")
        v = VALIDATION
        why = (f"Rappel + achat + trajet : {int(trip)} s. Au puits à l'apparition, tes parties : pris "
               f"{v['at_pit']['ours']}/{v['at_pit']['n']} ({v['not_at_pit']['ours']}/{v['not_at_pit']['n']} sinon).")
        return M._call("wave_recall", f"wave_recall:obj:{key}:{int((ctx.gt + rem) // 10)}", "RENTRE ACHETER", text,
                       why, M.FOUNTAIN.get(ctx.my_team or ""), tier="basic", score=0.58, priority=61, color="gold",
                       life=15.0, label="BASE",
                       factors=(f"{key} dans {int(rem)} s", f"trajet {int(trip)} s", f"achat {spent} or"))
    except Exception:
        log.debug("back-now rule failed", exc_info=True)
        return None


def rule_stay(ctx: M.MacroCtx) -> M.GeniusCall | None:
    """"Ne rentre pas : Dragon dans 0:40" (a back now misses the spawn)."""
    try:
        nxt = _stay_window(ctx)
        if nxt is None:
            return None
        key, rem = nxt
        if ctx.gold < STAY_GOLD * (0.9 if ctx.keep else 1.0):
            return None                                  # nothing to buy: nobody is tempted
        trip = back_trip_s(ctx, key) or 0.0
        text = f"Ne rentre pas : {NAME[key]} dans {_clock(rem)}"
        v = VALIDATION["back_late"]
        why = (f"Rappel + trajet : {int(trip)} s, tu arriverais trop tard. Rentre après. Tes parties : retour "
               f"tardif = objectif perdu {v['theirs']}/{v['n']}.")
        return M._call(KIND_STAY, f"{KIND_STAY}:{key}:{int((ctx.gt + rem) // 10)}", "NE RENTRE PAS", text, why,
                       None, tier="basic", score=0.52, priority=60, color="gold", life=11.0, label="RESTE",
                       factors=(f"{key} dans {int(rem)} s", f"trajet {int(trip)} s", f"{int(ctx.gold)} or"))
    except Exception:
        log.debug("stay rule failed", exc_info=True)
        return None


def rule_dont_die(ctx: M.MacroCtx) -> M.GeniusCall | None:
    """"Ne prends aucun risque : mourir = Dragon perdu" (40 s before a spawn the fight calculator does not call lost)."""
    try:
        if not ctx.me_alive or not _out_of_base(ctx) or ctx.gt < 240.0:
            return None
        nxt = next_objective(ctx, SAFE_MIN_S * (0.5 if ctx.keep else 1.0), SAFE_S + (5.0 if ctx.keep else 0.0))
        if nxt is None:
            return None
        key, rem = nxt
        if key not in SAFE_KEYS:
            return None
        from treeaicoach.fog_intel import fight_odds

        odds = fight_odds(ctx)
        if odds is None or odds["verdict"] == "lose":
            return None                                  # lost: fog_intel says "laisse"
        text = f"Ne prends aucun risque : mourir = {LOST[key]}"
        v, w = VALIDATION["died40_not_lost"], VALIDATION["nobody_died_not_lost"]
        why = (f"{NAME[key]} dans {int(rem)} s, {odds['us']}v{odds['them']}. Tes parties : une mort juste avant = "
               f"perdu {v['theirs']}/{v['n']} ({w['theirs']}/{w['n']} sinon).")
        return M._call(KIND_SAFE, f"{KIND_SAFE}:{key}:{int((ctx.gt + rem) // 10)}", "NE MEURS PAS", text, why,
                       None, tier="basic", score=0.44, priority=52, color="gold", life=20.0, label="SAFE",
                       factors=(f"{key} dans {int(rem)} s", f"{odds['us']}v{odds['them']}", f"or {odds['gold']:+d}"))
    except Exception:
        log.debug("dont-die rule failed", exc_info=True)
        return None


RULES = (rule_back_now, rule_stay, rule_dont_die)

__all__ = ["KIND_STAY", "KIND_SAFE", "ROLE_PLAYS", "VALIDATION", "next_objective", "back_trip_s", "purchase",
           "recall_blocked", "rule_back_now", "rule_stay", "rule_dont_die", "RULES"]
