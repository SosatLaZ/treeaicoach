"""FOG INTEL: what the public scoreboard (Tab) data says about enemies we do NOT see.

The Live Client Data API (``live_client.py``: ``playerlist`` items, level, creep score, ``isDead`` /
``respawnTimer``) updates for every enemy, seen or not: exactly what the player can read on Tab at
any time. :mod:`treeaicoach.jungle_intel` turns it into facts (a NEW purchase = he stood in his
fountain at that moment; creep score up = he was farming) and re-anchors the fog regions of
:mod:`treeaicoach.fog_tracker` on them, which already feeds the jungler-fog gank danger of
:mod:`treeaicoach.danger` (``fog_mass_near``). This module adds the CALLS those facts deserve, as
planner rules of :mod:`treeaicoach.macro` (same card / banner / voice path, no second channel):

* ``gc_jg_based`` - the enemy jungler bought something while unseen: he is at his base, and the
  geodesic walk from his fountain to me at an UPPER speed bound (:data:`BASE_SPEED_UNITS`, boots +
  margin) is a hard floor of his next gank: "Joue fort : Lee Sin est à la base, 25 s sans gank".
  :func:`jungler_base` also feeds :func:`treeaicoach.macro.jungler_location` (source ``"base"``), so
  "jungler invisible : recule", the first-gank forecast and the plates guard agree with it.
* lane opponent based (:func:`opp_base_window`): his purchase while unseen = he recalled; the
  macro ``plates`` rule then says "Pousse et plaque la tour : Darius est en base." (plate gold in the
  WHY), or, when I am low / rich, :func:`rule_sync_recall`: "Rentre aussi : Darius est en base".
* ``gc_obj_fight`` - fight calculator before an epic objective: alive players (death timers), item
  gold (Data Dragon value of the public inventories) and levels of each team: "Force le dragon :
  combat 5v4, +3,2k or" / "Laisse le dragon, prends les larves : combat perdu (4v5)".

Measured on the user's 20 games with LCU truth (``python tools/fog_intel_truth.py <games dir>``,
numbers in :data:`VALIDATION`, minute frames = a sample): 6 laning minutes with the true enemy
jungler in his fountain, window median 26 s, 0 kills with him inside a window; the fight calculator
said "laisse" on 11 objective minutes (enemy take 5, ours 2, fights near the pit lost 6 / won 1) and
"force" on 2 (our take 1, fights won 2 / lost 0). NOT built, the data said no: "full clear fini :
gank probable dans 20 s" (the jungler took part in a kill within 75 s of his level 4 in 4 / 18
games, on his level-4 half in 1) and a plates countdown before 14:00 (2026: plates no longer fall
at 14:00, see ai_advisor.SEASON_RULES).

Riot policy: Tab data only; no enemy summoner spell / ultimate cooldown, no memory reading, no
automation. Pure (no I/O), never raises.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import replace
from typing import Any

from treeaicoach import geometry
from treeaicoach import macro as M
from treeaicoach import objectives as _objectives
from treeaicoach.fmtutil import finite as _f

log = logging.getLogger(__name__)

KIND_BASED = "gc_jg_based"
KIND_FIGHT = "gc_obj_fight"
#: upper bound of a champion's speed walking out of his fountain (game units / s): tier-2 boots
#: (~390) x the fog tracker's speed margin, rounded up: the window is a FLOOR of his travel time
BASE_SPEED_UNITS = 560.0
BASE_SPEED = BASE_SPEED_UNITS / geometry.MAP_GAME_UNITS
#: a purchase older than this is no news any more
BASE_NEWS_S = 60.0
#: the call starts within this after the purchase was seen, with at least this window
BASE_CALL_FRESH_S = 8.0
BASE_MIN_WINDOW_S = 15.0
BASE_FROM_GT = 150.0
#: lane opponent: purchase this recent while unseen = "en base"
OPP_NEWS_S = 20.0
OPP_MIN_WINDOW_S = 10.0
#: "rentre aussi" when an opponent is based and I am this low / this rich
SYNC_HP = 0.5
SYNC_GOLD = 1300.0
#: fight calculator
FIGHT_DEAD_MIN_S = 10.0          # a player respawning sooner still counts as alive
FIGHT_WIN_GOLD = -1500           # more of us alive: force unless this far behind in item gold
FIGHT_EVEN_GOLD = 3000           # even numbers: this item gold gap decides
FIGHT_SOON_S = 30.0              # objective spawning within this (or up)
FIGHT_NEAR_R = 0.2               # me / players this close to the pit = "the objective is the topic"
FIGHT_KEYS = ("baron", "elder", "dragon", "herald", "grubs")
#: the objective to take instead when the fight is lost (other side of the map)
#: (never the Baron / the Elder: a man down, those are the fight itself)
ALTERNATIVE = {"dragon": ("grubs", "herald"), "elder": (), "baron": (), "herald": ("dragon",), "grubs": ("dragon",)}
#: truth replay of the fight verdicts (tools/fog_intel_truth.py, 20 games, minute frames)
VALIDATION = {"games": 20, "base": {"moments": 6, "window_median_s": 26, "kills_in_window": 0},
              "lose": {"minutes": 11, "enemy_take": 5, "our_take": 2, "fights_lost": 6, "fights_won": 1},
              "win": {"minutes": 2, "our_take": 1, "fights_won": 2, "fights_lost": 0},
              "clear_gank": {"games": 18, "kill_75s": 4, "same_half": 1}}

_FIELDS: dict[str, Any] = {}
_FIELDS_LOCK = threading.Lock()


# ----------------------------------------------------------------------------- travel
def _fountain_field(team: str) -> Any:
    with _FIELDS_LOCK:
        fld = _FIELDS.get(team)
        if fld is None:
            from treeaicoach.fog_tracker import shared_reachability

            src = geometry.RED_FOUNTAIN if team == "CHAOS" else geometry.BLUE_FOUNTAIN
            fld = _FIELDS[team] = shared_reachability().distance_field(src)
        return fld


def fountain_eta(team: Any, uv: Any) -> float | None:
    """Fastest walk (s) from ``team``'s fountain to ``uv`` (geodesic on the walkable mask, upper
    speed bound :data:`BASE_SPEED_UNITS`: nobody can be faster). None when unknown."""
    try:
        t = geometry.normalize_team(team)
        p = M._uv(uv)
        if t is None or p is None:
            return None
        src = geometry.RED_FOUNTAIN if t == "CHAOS" else geometry.BLUE_FOUNTAIN
        straight = math.hypot(p[0] - src[0], p[1] - src[1])
        d = straight
        try:
            from treeaicoach.jungle_intel import _lookup

            d = max(straight, _lookup(_fountain_field(t), p[0], p[1], straight) - 0.02)
        except Exception:
            log.debug("fountain field unavailable", exc_info=True)
        return max(0.0, d) / BASE_SPEED
    except Exception:
        return None


# ----------------------------------------------------------------------------- facts
def _seen_after(ctx: M.MacroCtx, alias: str, t_fact: float) -> bool | None:
    """True when ``alias`` is visible / was seen after ``t_fact`` (the fact is stale), None when visible."""
    tr = M._track(ctx, alias)
    if tr is None or M._uv(getattr(tr, "uv", None)) is None:
        return False
    if bool(getattr(tr, "visible", False)):
        return None
    hid = _f(getattr(tr, "hidden_s", None), None)
    if hid is None:
        return False
    return ctx.t - hid > t_fact + 0.5


def purchase_t(ctx: M.MacroCtx, alias: Any) -> float | None:
    """Engine time of the last purchase of the enemy ``alias`` seen on Tab (jungle_intel), when
    newer than :data:`BASE_NEWS_S`, the champion alive, unseen since. None otherwise."""
    try:
        a = str(alias or "").lower()
        ji = ctx.jint
        if not a or ji is None:
            return None
        rec = None
        for al, tt in getattr(ji, "recalls", ()) or ():
            if str(al).lower() == a:
                rec = _f(tt, None)
        if rec is None and str(getattr(ji, "alias", "") or "").lower() == a:
            rec = _f(getattr(ji, "recalled_t", None), None)
        if rec is None or not (0.0 <= ctx.t - rec <= BASE_NEWS_S):
            return None
        p = M._player(ctx, a)
        if p is None or bool(getattr(p, "is_dead", False)):
            return None
        if _seen_after(ctx, a, rec) is not False:
            return None                                   # visible / seen since: the minimap knows better
        return rec
    except Exception:
        return None


def jungler_base(ctx: M.MacroCtx, uv: Any = None) -> tuple[float, float] | None:
    """``(purchase engine time, seconds left before he can stand on uv)`` (``uv``: me by default)
    when the enemy jungler bought something while unseen. None otherwise."""
    try:
        a = ctx.jungler_alias
        rec = purchase_t(ctx, a)
        if rec is None:
            return None
        eta = fountain_eta(ctx.enemy_team, uv if uv is not None else ctx.me_uv)
        if eta is None:
            eta = 20.0                                    # fountain -> nearest outer tower, fast
        return rec, eta - (ctx.t - rec)
    except Exception:
        return None


def base_confidence(ctx: M.MacroCtx) -> tuple[float, float] | None:
    """``(confidence, age s)`` of "the enemy jungler is at / just left his base" for
    :func:`treeaicoach.macro.jungler_location`: 0.9 while he cannot have reached me, then fading
    out over 10 s. None without a fact."""
    jb = jungler_base(ctx)
    if jb is None:
        return None
    rec, left = jb
    conf = 0.9 if left > 0.0 else 0.9 * max(0.0, 1.0 + left / 10.0)
    return (conf, max(0.0, ctx.t - rec)) if conf > 0.0 else None


def opp_base_window(ctx: M.MacroCtx, alias: Any, target: Any) -> float | None:
    """Seconds before the lane opponent ``alias`` can be back on ``target`` (my lane's tower) after
    a purchase seen while he is unseen (he recalled), when at least :data:`OPP_MIN_WINDOW_S`."""
    rec = purchase_t(ctx, alias)
    if rec is None or ctx.t - rec > OPP_NEWS_S:
        return None
    eta = fountain_eta(ctx.enemy_team, target)
    if eta is None:
        return None
    left = eta - (ctx.t - rec)
    return left if left >= (2.0 if ctx.keep else OPP_MIN_WINDOW_S) else None


def sync_wanted(ctx: M.MacroCtx) -> bool:
    """I am low or rich: an opponent's recall is MY recall window, not a plates window."""
    return (ctx.hp is not None and ctx.hp < SYNC_HP) or ctx.gold >= SYNC_GOLD


# ----------------------------------------------------------------------------- rules
def _lane_ok(ctx: M.MacroCtx) -> str | None:
    lane = M._my_lane(ctx)
    if not ctx.me_alive or ctx.in_base or lane is None or ctx.role == "JUNGLE":
        return None
    if ctx.me_uv is None or M._zone_lane(ctx.me_uv) != lane:
        return None
    return lane


def rule_jungler_based(ctx: M.MacroCtx) -> M.GeniusCall | None:
    """"Joue fort : Lee Sin est à la base, 25 s sans gank" (once per purchase, laning phase)."""
    try:
        lane = _lane_ok(ctx)
        if lane is None or not (BASE_FROM_GT <= ctx.gt < M.LANING_END_GT) or not ctx.jungler_alias:
            return None
        jb = jungler_base(ctx)
        if jb is None:
            return None
        rec, left = jb
        if not ctx.keep and (ctx.t - rec > BASE_CALL_FRESH_S or left < BASE_MIN_WINDOW_S):
            return None
        if ctx.keep and left < 2.0:
            return None
        opps = [M._player(ctx, a) for a in ctx.lane_opps]
        if not any(p is not None and not bool(getattr(p, "is_dead", False)) for p in opps):
            return None                                  # opponent dead: the plates call says it
        if ctx.hp is not None and ctx.hp < 0.4:
            return None
        soon = M._obj_soon(ctx, 60.0)
        if not ctx.keep and soon is not None and M._role_plays(ctx, soon):
            return None                                  # the objective coming is my topic, not my lane
        p = M._player(ctx, ctx.jungler_alias)
        name = str(getattr(p, "champion_name", "") or "") or "Leur jungler"
        w = int(left // 5 * 5) if left >= 10 else int(left)
        from treeaicoach.game_changers import _draft_says_no, _fit, _stance_ok

        careful = _draft_says_no(ctx) or not _stance_ok(ctx)
        head = "Farme devant" if careful else "Joue fort"
        text = _fit(f"{head} : {name} est à la base, {w} s sans gank",
                    f"{head} : leur jungler est à la base, {w} s sans gank")
        why = ("Il vient d'acheter : il était dans sa fontaine. Même au plus vite, il lui faut "
               f"{w} s pour arriver jusqu'à toi.")
        if careful:
            why += " Ton adversaire reste plus fort : prends les sbires, pas l'échange."
        return replace(M._call(KIND_BASED, f"{KIND_BASED}:{int(rec)}", "JUNGLER À LA BASE", text, why,
                               None, tier="basic", score=0.6, priority=76, color="safe",
                               life=max(6.0, min(left, 30.0)),
                               factors=(f"achat vu il y a {int(ctx.t - rec)} s", f"fenêtre {int(left)} s")),
                       voice="Jungler à la base : farme !" if careful else "Jungler à la base : joue fort !")
    except Exception:
        log.debug("jungler based rule failed", exc_info=True)
        return None


def rule_sync_recall(ctx: M.MacroCtx) -> M.GeniusCall | None:
    """My lane opponent(s) recalled (purchase while unseen) and I am low / rich: "Rentre aussi"."""
    try:
        lane = _lane_ok(ctx)
        if lane is None or not ctx.lane_opps or ctx.gt < BASE_FROM_GT:
            return None
        if not (ctx.keep or sync_wanted(ctx)):
            return None
        stand = M._standing(ctx, ctx.enemy_team, lane)
        target = stand[0][1] if stand else M.lane_uv(lane, 0.6, ctx.my_team)
        names = []
        for a in ctx.lane_opps:
            p = M._player(ctx, a)
            if p is None:
                continue
            if bool(getattr(p, "is_dead", False)):
                continue
            if opp_base_window(ctx, a, target) is None:
                return None                              # one of them is still in lane
            names.append(ctx.enemy_names.get(a, a))
        if not names:
            return None
        who = " et ".join(names[:2])
        verb = "sont en base" if len(names) > 1 else "est en base"
        low = ctx.hp is not None and ctx.hp < SYNC_HP
        return M._call("wave_recall", f"wave_recall:sync:{'-'.join(sorted(ctx.lane_opps))}:{int(ctx.gt // 60)}",
                       "RENTRE AUSSI", f"Rentre aussi : {who} {verb}",
                       ("Tu ne perds ni sbires ni plaques pendant son absence : vous revenez en même temps"
                        + (", toi avec la vie pleine." if low else ", toi avec tes objets.")),
                       M.FOUNTAIN.get(ctx.my_team or ""), tier="basic", score=0.7, priority=83, color="gold",
                       life=12.0, label="BASE", factors=(f"{who} en base", "PV bas" if low else "or à dépenser"))
    except Exception:
        log.debug("sync recall rule failed", exc_info=True)
        return None


def _team(ctx: M.MacroCtx, side: str) -> tuple[list[Any], list[tuple[str, float]]]:
    """``(alive players, [(name, respawn s)] dead)`` of ``side``; me counted with the allies."""
    alive, dead = [], []
    players = list(getattr(ctx.game, side, None) or [])
    if side == "allies":
        me = getattr(ctx.game, "me", None)
        if me is not None and all(p is not me for p in players):
            players.append(me)
    for p in players:
        r = M._respawn(p)
        if bool(getattr(p, "is_dead", False)) and r >= FIGHT_DEAD_MIN_S:
            dead.append((str(getattr(p, "champion_name", "") or getattr(p, "champion_alias", "")), r))
        else:
            alive.append(p)
    return alive, dead


def fight_odds(ctx: M.MacroCtx, gold_of: Any = None) -> dict[str, Any] | None:
    """Numbers of an objective fight now: alive counts, item gold and level gaps (alive players),
    the dead with their timers, and the verdict ``"win"`` / ``"lose"`` / ``None`` (even).
    ``gold_of(player)``: gold of a player (default: Data Dragon value of his items)."""
    try:
        us, us_dead = _team(ctx, "allies")
        them, them_dead = _team(ctx, "enemies")
        if not us or not them or len(us) > 5 or len(them) > 5:
            return None
        from treeaicoach.live_edge import item_gold

        def items(p: Any) -> list:
            return list(getattr(p, "items", None) or [])

        gold = gold_of or (lambda p: item_gold(items(p)))
        gd = sum(gold(p) for p in us) - sum(gold(p) for p in them)
        lv = (sum(int(_f(getattr(p, "level", 0), 0) or 0) for p in us) / len(us)
              - sum(int(_f(getattr(p, "level", 0), 0) or 0) for p in them) / len(them))
        n_us, n_them = len(us), len(them)
        verdict = None
        if n_us > n_them and gd >= FIGHT_WIN_GOLD:
            verdict = "win"
        elif n_us < n_them and gd <= -FIGHT_WIN_GOLD:
            verdict = "lose"
        elif n_us == n_them and gd >= FIGHT_EVEN_GOLD and lv >= 0.0:
            verdict = "win"
        elif n_us == n_them and gd <= -FIGHT_EVEN_GOLD and lv <= 0.0:
            verdict = "lose"
        try:
            from treeaicoach.freshness import level_exact

            fresh = all(level_exact(p) for p in them)     # Tab lines (items) seen in the last 30 s
        except Exception:
            fresh = True
        return {"us": n_us, "them": n_them, "gold": int(gd), "level": round(lv, 2), "verdict": verdict,
                "us_dead": us_dead, "them_dead": them_dead, "fresh": fresh}
    except Exception:
        log.debug("fight odds failed", exc_info=True)
        return None


def _k(gold: float) -> str:
    """+3 240 -> "+3,2k"."""
    v = abs(gold) / 1000.0
    s = f"{v:.1f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{'+' if gold >= 0 else '-'}{s}k"


def _topic_objective(ctx: M.MacroCtx) -> str | None:
    """The epic objective that is the topic now: up (or spawning within :data:`FIGHT_SOON_S`),
    my role plays it, and I (or 2+ players) stand near its pit."""
    for key in FIGHT_KEYS:
        o = ctx.objectives.get(key)
        if o is None:
            continue
        alive, rem = o
        if not (alive or (rem is not None and 0.0 <= rem <= FIGHT_SOON_S)):
            continue
        if key == "baron" and ctx.gt < _objectives.baron_first_s():
            continue
        pit = M.PIT_UV.get(key)
        if pit is None or not M._role_plays(ctx, key):
            continue
        near_me = ctx.me_uv is not None and geometry.dist(ctx.me_uv, pit) < FIGHT_NEAR_R
        crowd = sum(1 for e in list(ctx.allies) + M._fresh_enemies(ctx)
                    if M._uv(getattr(e, "uv", None)) is not None and geometry.dist(M._uv(e.uv), pit) < FIGHT_NEAR_R)
        if near_me or crowd >= 2:
            return key
    return None


def rule_objective_fight(ctx: M.MacroCtx, gold_of: Any = None) -> M.GeniusCall | None:
    """Fight calculator before an epic objective (see the module docstring)."""
    try:
        if not ctx.me_alive or ctx.in_base or ctx.gt < 300.0:
            return None
        key = _topic_objective(ctx)
        if key is None:
            return None
        if M.jungler_location(ctx).dead and not ctx.keep:
            return None                                  # jungler_dead says it (smite window)
        odds = fight_odds(ctx, gold_of)
        if odds is None or odds["verdict"] is None:
            return None
        n_us, n_them = odds["us"], odds["them"]
        if not ctx.keep and abs(n_us - n_them) >= 2:
            return None                                  # fight_won / fight_lost / number_advantage say it
        pit = M.PIT_UV[key]
        what = M.OBJ_LE.get(key, key)
        gold = odds["gold"]
        numbers = f"{n_us}v{n_them}"
        dead = odds["them_dead"] if odds["verdict"] == "win" else odds["us_dead"]
        dead_txt = ", ".join(f"{n} mort {int(r)} s" for n, r in sorted(dead, key=lambda x: -x[1])[:2])
        if odds["verdict"] == "win":
            # their inventories are the ones seen last: an exact "+12,3k or" only on fresh Tab lines
            gtxt = f", {_k(gold)} or" if abs(gold) >= 1000 and odds.get("fresh", True) else ""
            text = f"Force {what} : combat {numbers}{gtxt}"
            why = (f"Vous êtes {n_us} contre {n_them}" + (f" ({dead_txt})" if dead_txt else "")
                   + ((f" avec {_k(gold)} d'or d'objets" if odds.get("fresh", True) else " avec plus d'objets")
                      if gold >= 1000 else "") + " : le combat est pour vous.")
            call = M._call(KIND_FIGHT, f"{KIND_FIGHT}:{key}:win:{numbers}:{int(ctx.gt // 90)}",
                           M.OBJ_TITLE.get(key, "OBJECTIF"), text, why, pit, tier="mid", score=0.66, priority=86,
                           color="safe", life=20.0, label=M.OBJ_TITLE.get(key, "OBJECTIF").rstrip(" !"),
                           factors=(numbers, f"or d'objets {gold:+d}", f"niveaux {odds['level']:+.1f}"))
            word = {"dragon": "le dragon", "baron": "le Baron", "elder": "l'ancestral", "herald": "le Héraut",
                    "grubs": "les larves"}[key]
            return replace(call, voice=f"Combat gagné : force {word} !")
        alt = None
        for k2 in ALTERNATIVE.get(key, ()):
            if M._obj_up(ctx, k2, within=10.0):
                alt = k2
                break
        lane = M._my_lane(ctx)
        if alt is not None:
            tail, target, label = f"prends {M.OBJ_LE.get(alt, alt)}", M.PIT_UV.get(alt), M.OBJ_TITLE[alt].rstrip(" !")
        elif ctx.gt < M.LANING_END_GT and lane is not None and M._standing(ctx, ctx.enemy_team, lane):
            tail, target, label = "prends des plaques", M._standing(ctx, ctx.enemy_team, lane)[0][1], "TOUR"
        else:
            tail, target, label = "farme loin de la rivière", M._safe_uv(ctx), "REPLI"
        text = f"Laisse {what}, {tail} : combat perdu ({numbers})"
        if len(text) > 60:
            text = f"Laisse {what}, {tail} : combat perdu"
        why = (f"Vous êtes {n_us} contre {n_them}" + (f" ({dead_txt})" if dead_txt else "")
               + (f", {_k(gold)} d'or d'objets" if gold <= -1000 else "")
               + f" : ils gagnent ce combat. Prends ailleurs ce qu'ils te laissent.")
        word = {"dragon": "le dragon", "baron": "le Baron", "elder": "l'ancestral", "herald": "le Héraut",
                "grubs": "les larves"}[key]
        call = M._call(KIND_FIGHT, f"{KIND_FIGHT}:{key}:lose:{numbers}:{int(ctx.gt // 90)}", "COMBAT PERDU",
                       text, why, target, tier="mid", score=0.62, priority=85, color="danger", life=20.0,
                       label=label, factors=(numbers, f"or d'objets {gold:+d}", f"niveaux {odds['level']:+.1f}"))
        return replace(call, voice=f"Combat perdu : laisse {word}.")
    except Exception:
        log.debug("objective fight rule failed", exc_info=True)
        return None


RULES = (rule_jungler_based, rule_sync_recall, rule_objective_fight)

VOICE_PHRASES = ("Jungler à la base : joue fort !", "Jungler à la base : farme !",
                 *(f"Combat gagné : force {w} !" for w in ("le dragon", "le Baron", "l'ancestral", "le Héraut",
                                                           "les larves")),
                 *(f"Combat perdu : laisse {w}." for w in ("le dragon", "le Baron", "l'ancestral", "le Héraut",
                                                           "les larves")))

__all__ = ["RULES", "KIND_BASED", "KIND_FIGHT", "fountain_eta", "purchase_t", "jungler_base", "base_confidence",
           "opp_base_window", "sync_wanted", "fight_odds", "rule_jungler_based", "rule_sync_recall",
           "rule_objective_fight", "VOICE_PHRASES", "VALIDATION"]
