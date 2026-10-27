"""LIVE EDGE: gold-exact live numbers from the official Live Client Data API, turned into ONE call.

What no human tracks in real time while playing, computed every coaching tick from the public Tab
data (``allPlayers``: levels, items, scores, ``isDead`` / ``respawnTimer``), my own
``activePlayer`` (current gold, HP) and the event feed. Riot policy: public information only -
NO enemy summoner spell / ultimate cooldown, no enemy HP (the API does not expose it), no hidden
information. Everything else in the app already covers part of this; this module only adds the
exact numbers and the missing calls, through the SAME presenter path (one instruction at a time):

====================  ==============================================================  =========
feature               output                                                          channel
====================  ==============================================================  =========
lane gold diff        "+850 or sur Darius" (Data Dragon value of both inventories)     live line
kill window           "Attaque Darius : fenêtre de kill (niv 6 vs 5, +600 or)"          card
optimal recall        "Rentre acheter BF Glaive : tu as 1300 or" (meta build's next     card
                      item, its most expensive affordable component, exact prices)
adaptive defense      "Achète Sandales de Mercure + Force de la nature : ennemis 70 % AP"  card
                      / anti-heal when their healing is the problem (shop / dead only)
win probability       "victoire 62 % (+8 : Baron)" (hype.py logistic + level term, the   live line
                      term that moved the most in the last ~90 s)
objective + numbers   "Prends le Baron maintenant : 3 ennemis morts (25 s)" ONLY as a    card+voice
                      fallback when the macro planner (positions) has no call
farm rates            "6,8 CS/min (obj 7)" + "Farme mieux ta vague : 4,1 CS/min,        live line /
                      objectif 6,5" only when far below the target of my rank           card
====================  ==============================================================  =========

:class:`LiveEdge` keeps the anti-spam memory: one note per tick at most, :data:`GLOBAL_GAP_S`
between two notes, per-feature gaps (:data:`GAPS`), a call is re-emitted only when its identity
changes (a new component, a new damage profile bucket, the kill window re-opening...). The pure
functions are reusable and tested on their own. Never raises from its public functions.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass
from typing import Any, Iterable

from treeaicoach import objectives as _objectives

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------------- tunables
GLOBAL_GAP_S = 20.0             # min engine time between two notes of this module
GAPS = {"kill": 90.0, "recall": 120.0, "defense": 240.0, "objective": 30.0, "farm": 300.0, "bounty": 180.0}
LANING_END_GT = 14 * 60.0       # kill window: laning phase only
KILL_MIN_HP = 0.55              # my HP fraction needed to call a kill window
KILL_SCORE = 1.5                # level diff + gold diff / KILL_GOLD_UNIT
KILL_GOLD_UNIT = 600.0
RECALL_MIN_VALUE = 700          # a component worth a trip (BF Glaive 1300, Pioche 875...)
DEFENSE_SHARE = 0.68            # "ennemis 70 % AP": one damage type this dominant
ANTIHEAL_SEVERITY = 1.2         # itemization.enemy_profile need severity for anti-heal
FARM_MIN_GT = 8 * 60.0
FARM_FAR_BELOW = 0.60           # nudge under 60 % of the target only (a statistic is no instruction)
WIN_REF_S = 90.0                # the swing reason compares with the factors this old
WIN_SWING_POINTS = 5.0          # a reason is shown when the probability moved this much
WATCH_S = 30.0                  # no call before the module watched the game this long (app start)
#: game modes with the normal rift economy (the calls' numbers: CS targets, recall prices, objectives);
#: elsewhere (URF, One for All, practice tool...) only the live line is kept
NORMAL_MODES = frozenset({"", "CLASSIC", "SWIFTPLAY"})
NO_WIN_MODES = frozenset({"PRACTICETOOL"})      # (+ TUTORIAL*): no real opponent, no win probability

#: CS / min and estimated gold / min targets by elo bracket (meta_builds.get_bracket(): "low" =
#: Iron-Silver, "mid" = Gold-Platinum, "" = Emerald+ / unknown) and role. Public averages, rounded.
CS_TARGETS = {"low": {"TOP": 5.5, "MIDDLE": 5.5, "BOTTOM": 6.0, "JUNGLE": 4.5},
              "mid": {"TOP": 6.5, "MIDDLE": 6.5, "BOTTOM": 7.0, "JUNGLE": 5.0},
              "": {"TOP": 7.5, "MIDDLE": 7.5, "BOTTOM": 8.0, "JUNGLE": 5.5}}
GOLD_TARGETS = {"low": 330.0, "mid": 370.0, "": 410.0}
SUPPORT_GOLD_FACTOR = 0.68
START_GOLD = 500.0

TIER2_BOOTS_MR, TIER2_BOOTS_ARMOR = 3111, 3047      # Sandales de Mercure, Coques en acier
_NUM_FR = {2: "deux", 3: "trois", 4: "quatre", 5: "cinq"}


@dataclass(frozen=True)
class EdgeNote:
    """One call of this module: ``text`` is the card line (verb first), ``voice`` a short static
    phrase for the important ones (objective now), else ""."""

    feature: str             # "kill" | "recall" | "defense" | "objective" | "farm"
    key: str                 # presenter / topic key ("lvl_ahead:edge:...", "comp_ready:edge:...")
    title: str
    text: str
    voice: str = ""
    ident: str = ""


@dataclass(frozen=True)
class EdgeStats:
    """The live line's numbers, structured (the in-game essentials strip, essentials.py): lane
    gold diff vs my opponent, CS / min vs the target of my rank (gold / min for the support), win
    probability with its swing over ~90 s and the term that moved it. None = unknown."""

    gold_diff: int | None = None
    opponent: str = ""
    cs_min: float | None = None
    cs_target: float | None = None
    gold_min: int | None = None
    support: bool = False
    win: float | None = None             # 0..1
    win_delta: float | None = None       # points over WIN_REF_S (None: not enough history)
    win_reason: str | None = None        # REASON_LABEL of the term that moved it (swing >= WIN_SWING_POINTS)


# ----------------------------------------------------------------------------- helpers
def _f(x: Any, d: float = 0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return d
    return v if math.isfinite(v) else d


def _name(p: Any) -> str:
    return str(getattr(p, "champion_name", "") or getattr(p, "champion_alias", "") or "ton adversaire")


def _items(p: Any) -> list[int]:
    out = []
    for i in getattr(p, "items", None) or ():
        try:
            if int(i) > 0:
                out.append(int(i))
        except (TypeError, ValueError):
            continue
    return out


def fmt_num(x: float, nd: int = 1) -> str:
    """French decimal: 6.8 -> "6,8", 7.0 -> "7"."""
    s = f"{x:.{nd}f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def fmt_or(n: float) -> str:
    """Exact signed gold, French spacing: +850 or / -1 250 or (rounded to 10)."""
    v = int(round(_f(n) / 10.0) * 10)
    s = f"{abs(v):,}".replace(",", " ")
    return f"{'+' if v > 0 else '-' if v < 0 else '±'}{s} or"


def item_gold(items: Iterable[Any]) -> int:
    """Data Dragon value of an inventory (scoreboard's table: exact prices of the live patch)."""
    try:
        from treeaicoach.scoreboard import items_gold

        return int(items_gold(items))
    except Exception:
        return 0


# ----------------------------------------------------------------------------- 1. lane gold diff
def lane_opponent(game: Any, summary: Any = None, role: str | None = None) -> Any:
    """My lane opponent: the Tab matchup of the scoreboard (resolved roles), else the enemy with
    my position. None when unknown (jungle mirror is fine)."""
    try:
        m = getattr(summary, "my_matchup", None) if summary is not None else None
        alias = str(getattr(m, "enemy_alias", "") or "")
        enemies = list(getattr(game, "enemies", None) or [])
        if alias:
            for p in enemies:
                if str(getattr(p, "champion_alias", "")) == alias:
                    return p
        pos = str(role or getattr(getattr(game, "me", None), "position", "") or "").upper()
        if pos:
            same = [p for p in enemies if str(getattr(p, "position", "") or "").upper() == pos]
            if len(same) == 1:
                return same[0]
    except Exception:
        log.debug("lane opponent failed", exc_info=True)
    return None


def lane_gold_diff(me: Any, opp: Any) -> int:
    """My inventory value - his (items only: his unspent gold is hidden, so mine is left out too)."""
    return item_gold(_items(me)) - item_gold(_items(opp))


def gold_diff_text(diff: float, opp_name: str) -> str:
    """"+850 or sur Darius" / "-300 or sur Darius"."""
    return f"{fmt_or(diff)} sur {opp_name}"


# ----------------------------------------------------------------------------- 2. kill window
def kill_window(my_level: int, opp_level: int, gold_diff: float, hp_frac: float | None,
                extra: float = 0.0) -> tuple[bool, float]:
    """``(open, score)``: the lane kill window from what the API exposes (levels, item gold, MY
    HP; the enemy HP / cooldowns are not public). score = level diff + gold diff / 600, +0.5 when
    I have my ultimate (level 6) and he does not. Open when score >= :data:`KILL_SCORE` + ``extra``
    (draft_edge.lane_prior: stricter against an opponent the draft / his rank favours now), I am
    not behind in levels and my HP >= :data:`KILL_MIN_HP`."""
    lv = int(my_level) - int(opp_level)
    score = lv + _f(gold_diff) / KILL_GOLD_UNIT
    if int(my_level) >= 6 > int(opp_level):
        score += 0.5
    hp_ok = hp_frac is None or _f(hp_frac, 0.0) >= KILL_MIN_HP
    return (score >= KILL_SCORE + _f(extra) and lv >= 0 and _f(gold_diff) > -300 and hp_ok), round(score, 2)


def kill_window_text(opp_name: str, my_level: int, opp_level: int, gold_diff: float) -> str:
    """"Attaque Darius : fenêtre de kill (niv 6 vs 5, +600 or)" (shortened to fit the card)."""
    nums = f"niv {int(my_level)} vs {int(opp_level)}, {fmt_or(gold_diff)}"
    for s in (f"Attaque {opp_name} : fenêtre de kill ({nums})", f"Attaque {opp_name} : {nums}"):
        if len(s) <= 60:
            return s
    return f"Attaque : fenêtre de kill ({nums})"


# ----------------------------------------------------------------------------- 3. optimal recall
def next_build_item(build: Any, owned: Iterable[int], items: dict | None = None) -> int | None:
    """The first item of the meta build strip I do not own yet (build_strip order)."""
    try:
        from treeaicoach.build_strip import make_strip

        strip = make_strip(build, list(owned or ()), 0.0, items)
        if strip is None:
            return None
        for it in strip.items:
            if it.status == "next":
                return int(it.item_id)
    except Exception:
        log.debug("next build item failed", exc_info=True)
    return None


def recall_target(target: int | None, owned: Iterable[int], gold: float,
                  items: dict | None = None) -> tuple[str, int, tuple[int, ...]] | None:
    """``(name, value, ids)`` my gold buys NOW towards ``target`` (exact component prices,
    itemization.plan_purchase): the whole item, else its most expensive affordable component;
    None when that piece is worth less than :data:`RECALL_MIN_VALUE` (no trip for small change)."""
    if target is None:
        return None
    try:
        from treeaicoach.itemization import load_items, plan_purchase

        table = items if items is not None else load_items()
        buys, done = plan_purchase(int(target), list(owned or ()), _f(gold), table)
        if not buys:
            return None
        value = sum(int(table[i].gold) for i in buys if i in table)
        if done:
            from treeaicoach.itemization import remaining_cost

            value = max(value, int(remaining_cost(int(target), list(owned or ()), table)))
        biggest = max((int(table[i].gold) for i in buys if i in table), default=0)
        if value < RECALL_MIN_VALUE or (not done and biggest < RECALL_MIN_VALUE):
            return None                  # "Bottes + Dague" is no reason to walk back: one real piece
        if done and int(target) in table:
            names = table[int(target)].name
        else:
            big = max((i for i in buys if i in table), key=lambda i: int(table[i].gold))
            names = table[big].name
            value = int(table[big].gold)
        return (names, int(value), tuple(int(i) for i in buys)) if names else None
    except Exception:
        log.debug("recall target failed", exc_info=True)
        return None


def recall_text(names: str, gold: float) -> str:
    s = f"Rentre acheter {names} : tu as {int(_f(gold))} or"
    return s if len(s) <= 60 else f"Rentre acheter {names}"[:60]


# ----------------------------------------------------------------------------- 4. adaptive defense
def damage_profile(enemies: Iterable[Any]) -> tuple[float, float, float]:
    """``(physical, magic, true)`` shares of the enemy team's damage: each champion's damage split
    (itemization.damage_split) weighted by his threat (item gold, kills - deaths). Sums to 1."""
    try:
        from treeaicoach.itemization import damage_split

        tot = [0.0, 0.0, 0.0]
        wsum = 0.0
        for p in enemies or ():
            alias = str(getattr(p, "champion_alias", "") or "")
            if not alias:
                continue
            sc = getattr(p, "scores", None) or {}
            kd = _f(sc.get("kills")) - _f(sc.get("deaths"))
            w = max(0.4, min(3.0, 1.0 + item_gold(_items(p)) / 4000.0 + 0.12 * kd))
            ad, ap, tr = damage_split(alias)
            tot[0] += w * ad
            tot[1] += w * ap
            tot[2] += w * tr
            wsum += w
        if wsum <= 0:
            return 0.0, 0.0, 0.0
        s = sum(tot) or 1.0
        return tot[0] / s, tot[1] / s, tot[2] / s
    except Exception:
        log.debug("damage profile failed", exc_info=True)
        return 0.0, 0.0, 0.0


def _owns_tag(owned: Iterable[int], tag: str, table: dict, kinds: tuple[str, ...]) -> bool:
    return any(i in table and tag in table[i].tags and table[i].kind in kinds for i in owned)


def defense_call(enemies: list[Any], my_alias: str, role: str | None, owned: Iterable[int],
                 items: dict | None = None) -> tuple[str, str] | None:
    """``(card line, ident)``: the defensive buy their team composition asks for, or None.
    70 % of their damage of one type -> the tier-2 boots of that type + the class item for it
    (itemization.NEED_ITEMS); else strong healing -> the anti-heal item of my class."""
    try:
        from treeaicoach.itemization import NEED_ITEMS, SAME_NEED, champion_class, enemy_profile, load_items

        table = items if items is not None else load_items()
        own = [int(i) for i in owned or ()]
        cls = champion_class(str(my_alias or ""), role)
        phys, magic, _tr = damage_profile(enemies)
        if max(phys, magic) >= DEFENSE_SHARE:
            ap = magic >= phys
            tag, need = ("SpellBlock", "magic") if ap else ("Armor", "physical")
            if not _owns_tag(own, tag, table, ("legendary", "boots")):
                picks: list[str] = []
                has_t2_boots = any(i in table and table[i].kind == "boots" for i in own)
                boots = TIER2_BOOTS_MR if ap else TIER2_BOOTS_ARMOR
                if not has_t2_boots and boots in table:
                    picks.append(table[boots].name)
                cand = next((i for i in NEED_ITEMS.get(need, {}).get(cls, ()) if i in table and i not in own), None)
                if cand is not None:
                    picks.append(table[cand].name)
                if picks:
                    pct = int(round(100 * (magic if ap else phys) / 10.0) * 10)
                    why = f"ennemis {pct} % {'AP' if ap else 'AD'}"
                    for s in (f"Achète {' + '.join(picks)} : {why}", f"Achète {' + '.join(picks)} : {why[8:]}",
                              f"Achète {picks[-1]} : {why}"):
                        if len(s) <= 60:
                            return s, f"{need}:{pct}"
        prof = enemy_profile(enemies, table)
        sev = prof.needs.get("antiheal", 0.0)
        if sev >= ANTIHEAL_SEVERITY and not any(i in SAME_NEED.get("antiheal", frozenset()) for i in own):
            cand = next((i for i in NEED_ITEMS.get("antiheal", {}).get(cls, ()) if i in table), None)
            names = [n for n in prof.names.get("antiheal", []) if n][:2]
            if cand is not None and names:
                who = " et ".join(names)
                for s in (f"Achète {table[cand].name} : {who} se soignent", f"Achète {table[cand].name} : anti-soin"):
                    if len(s) <= 60:
                        return s, "antiheal"
    except Exception:
        log.debug("defense call failed", exc_info=True)
    return None


# ----------------------------------------------------------------------------- 5. win probability reason
REASON_LABEL = {"gold": "or", "kills": "kills", "towers": "tours", "dragons": "dragons", "baron": "Baron",
                "elder": "ancestral", "inhibs": "inhibiteurs", "levels": "niveaux"}


def win_terms(f: Any) -> dict[str, float]:
    """The logit of :func:`treeaicoach.hype.win_logit` split by reason (transparent model)."""
    try:
        from treeaicoach import hype as H

        g = _f(getattr(f, "gold_diff", 0.0)) / 1000.0
        return {"gold": H.W_GOLD * g * H._gold_scale(_f(getattr(f, "game_time", 0.0))),
                "kills": H.W_KILL * _f(f.kill_diff),
                "towers": H.W_TOWER * _f(f.tower_diff),
                "dragons": H.W_DRAKE * _f(f.dragon_diff) + H.W_SOUL * _f(f.soul),
                "baron": H.W_BARON * _f(f.baron_diff) + H.W_BUFF * _f(f.baron_buff),
                "elder": H.W_ELDER * _f(f.elder_buff),
                "inhibs": H.W_INHIB * _f(f.inhib_diff),
                "levels": getattr(H, "W_LEVEL", 0.0) * _f(getattr(f, "level_diff", 0.0))}
    except Exception:
        return {}


def swing_reason(prev: Any, cur: Any) -> str | None:
    """The term whose contribution moved the most between two factor sets (None if nothing)."""
    a, b = win_terms(prev), win_terms(cur)
    if not a or not b:
        return None
    k = max(b, key=lambda x: abs(b[x] - a.get(x, 0.0)))
    return REASON_LABEL.get(k) if abs(b[k] - a.get(k, 0.0)) >= 0.05 else None


def win_text(p: float, delta_points: float | None, reason: str | None) -> str:
    """"victoire 62 %" / "victoire 62 % (+8 : Baron)"."""
    s = f"victoire {int(round(100 * _f(p, 0.5)))} %"
    if delta_points is not None and abs(delta_points) >= WIN_SWING_POINTS and reason:
        s += f" ({'+' if delta_points > 0 else '-'}{int(round(abs(delta_points)))} : {reason})"
    elif reason and delta_points is None:
        s += f" ({reason})"
    return s


# ----------------------------------------------------------------------------- 6. objective + numbers
def dead_players(players: Iterable[Any], min_respawn: float = 0.0) -> list[tuple[str, float]]:
    """``(alias, respawn s)`` of the dead players (Live Client ``isDead`` / ``respawnTimer``)."""
    out = []
    for p in players or ():
        if bool(getattr(p, "is_dead", False)):
            r = max(0.0, _f(getattr(p, "respawn_timer", 0.0)))
            if r >= min_respawn:
                out.append((str(getattr(p, "champion_alias", "") or ""), r))
    return out


#: (objective key, min dead enemies, min respawn window s, label, voice word, min game time)
OBJ_RULES = (("baron", 3, 25.0, "le Baron", "Baron", 1200.0),
             ("elder", 3, 20.0, "l'ancestral", "ancestral", 0.0),
             ("dragon", 2, 20.0, "le dragon", "dragon", 0.0))


def number_advantage(game: Any, objectives: Iterable[Any], role: str | None = None
                     ) -> tuple[str, str, str] | None:
    """``(card line, voice, ident)``: an epic objective is up and enough enemies are dead long
    enough (respawn timers) while my team is not: "Prends le Baron maintenant : 3 ennemis morts
    (25 s)". Me alive; dragon not for the top laner. None otherwise."""
    try:
        me = getattr(game, "me", None)
        if me is None or bool(getattr(me, "is_dead", False)):
            return None
        gt = _f(getattr(game, "game_time", 0.0))
        dead = dead_players(getattr(game, "enemies", None) or [], 1.0)
        al = dead_players(getattr(game, "allies", None) or [], 3.0)
        n = len(dead)
        if n < 2 or n - len(al) < 2:
            return None
        window = min(r for _a, r in dead)
        up = {str(getattr(o, "key", "")) for o in objectives or () if bool(getattr(o, "alive", False))}
        for key, need, min_w, label, word, min_gt in OBJ_RULES:
            if key not in up or n < need or window < min_w or gt < min_gt * _objectives.baron_first_s() / 1200.0:
                continue
            if key == "dragon" and str(role or "").upper() == "TOP":
                continue
            w = int(window)
            voice = f"Ils sont {_NUM_FR[min(5, n)]} morts : {word} !"
            return (f"Prends {label} maintenant : {n} ennemis morts ({w} s)", voice,
                    f"{key}:{int((gt + window) // 15)}")
    except Exception:
        log.debug("number advantage failed", exc_info=True)
    return None


# ----------------------------------------------------------------------------- 7. farm rates
def farm_rates(game: Any) -> tuple[float, float] | None:
    """``(CS / min, gold / min)`` of me. Gold / min is estimated from what the API exposes
    (inventory value + current gold - the 500 start gold; sold / used items are not counted)."""
    try:
        me = getattr(game, "me", None)
        gt = _f(getattr(game, "game_time", 0.0))
        if me is None or gt < 180.0:
            return None
        mins = gt / 60.0
        cs = _f((getattr(me, "scores", None) or {}).get("creepScore")) / mins
        earned = item_gold(_items(me)) + _f(getattr(game, "current_gold", 0.0)) - START_GOLD
        return round(cs, 1), round(max(0.0, earned) / mins)
    except Exception:
        return None


def farm_targets(role: str | None, bracket: str = "") -> tuple[float | None, float]:
    """``(CS / min target or None for the support, gold / min target)`` of my role and elo."""
    b = bracket if bracket in CS_TARGETS else ""
    r = str(role or "").upper()
    gold = GOLD_TARGETS[b] * (SUPPORT_GOLD_FACTOR if r == "UTILITY" else 1.0)
    return CS_TARGETS[b].get(r) if r != "UTILITY" else None, gold


def farm_text(cs: float, cs_target: float | None) -> str:
    return f"{fmt_num(cs)} CS/min (obj {fmt_num(cs_target)})" if cs_target else f"{fmt_num(cs)} CS/min"


def farm_nudge(cs: float, cs_target: float | None) -> str | None:
    """"Farme mieux ta vague : 4,1 CS/min, objectif 6,5" when far below, else None."""
    if cs <= 0.0:                    # no CS read (missing scores) is not a farming fact
        return None
    if not cs_target or cs >= FARM_FAR_BELOW * cs_target:
        return None
    return f"Farme mieux ta vague : {fmt_num(cs)} CS/min, objectif {fmt_num(cs_target)}"


# ----------------------------------------------------------------------------- the tracker
class LiveEdge:
    """See the module docstring. :meth:`update` once per coaching tick, :meth:`line` for the
    small live line. Thread-compatible (engine thread), never raises."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._last_note_t = -math.inf
        self._feat_t: dict[str, float] = {}
        self._idents: dict[str, str] = {}
        self._kill_open = False
        self._win_hist: deque = deque(maxlen=120)        # (t, p, factors)
        self._line: str | None = None
        self._stats: EdgeStats | None = None
        self._win: tuple[float, float | None, str | None] | None = None
        self._gt = 0.0
        self._first_t: float | None = None

    def line(self) -> str | None:
        return self._line

    def stats(self) -> EdgeStats | None:
        """The numbers of :meth:`line` (immutable snapshot), None before the first update."""
        return self._stats

    # ------------------------------------------------------------------ helpers
    def _due(self, feature: str, ident: str, t: float) -> bool:
        if self._idents.get(feature) == ident:
            return False
        last = self._feat_t.get(feature)
        return last is None or not 0.0 <= t - last < GAPS.get(feature, 60.0)

    def _emit(self, note: EdgeNote, t: float, gt: float) -> EdgeNote:
        self._feat_t[note.feature] = t
        self._idents[note.feature] = note.ident
        self._last_note_t = t
        return note

    # ------------------------------------------------------------------ main
    def update(self, t: float, game: Any, summary: Any = None, objectives: Iterable[Any] = (),
               role: str | None = None, in_base: bool = False, busy: bool = False,
               recall_ok: bool = True, item_text: str | None = None, bracket: str = "") -> list[EdgeNote]:
        """The live line + at most ONE new note. ``busy``: a macro call / fight / gank holds the
        card (only the line updates); ``recall_ok``: no other recall message was shown recently;
        ``item_text``: the build advisor's current line (a defense call naming the same item is
        not repeated). Never raises."""
        try:
            return self._update(float(t), game, summary, list(objectives or ()), role, bool(in_base),
                                bool(busy), bool(recall_ok), item_text, str(bracket or ""))
        except Exception:
            log.debug("live edge update failed", exc_info=True)
            return []

    def _update(self, t: float, game: Any, summary: Any, objectives: list, role: str | None, in_base: bool,
                busy: bool, recall_ok: bool, item_text: str | None, bracket: str) -> list[EdgeNote]:
        me = getattr(game, "me", None) if game is not None else None
        if me is None:
            self._line = self._stats = None
            return []
        gt = _f(getattr(game, "game_time", 0.0))
        if gt + 5.0 < self._gt:                      # a new game
            self.reset()
        self._gt = gt
        if self._first_t is None:
            self._first_t = t
        role = str(role or getattr(me, "position", "") or "").upper() or None
        mode = str(getattr(game, "game_mode", "") or "").upper()
        calls_on = mode in NORMAL_MODES            # URF / One for All / practice tool: other economy
        win_on = not (mode in NO_WIN_MODES or mode.startswith("TUTORIAL")) and bool(getattr(game, "enemies", None))
        opp = lane_opponent(game, summary, role)
        # ---- live line: gold vs my opponent · farm · win probability (+ reason)
        parts: list[str] = []
        gd = None
        if opp is not None:
            gd = lane_gold_diff(me, opp)
            parts.append(gold_diff_text(gd, _name(opp)))
        rates = farm_rates(game)
        cs_t, _gold_t = farm_targets(role, bracket)
        if rates is not None:
            parts.append(farm_text(rates[0], cs_t) if role != "UTILITY" else f"{int(rates[1])} or/min")
        self._win = None
        wt = self._win_line(t, game, summary) if win_on else None
        if wt:
            parts.append(wt)
        self._line = " · ".join(parts) if parts else None
        w = self._win
        self._stats = EdgeStats(gold_diff=int(gd) if gd is not None else None,
                                opponent=_name(opp) if opp is not None else "",
                                cs_min=rates[0] if rates is not None and rates[0] > 0 else None,
                                cs_target=cs_t, gold_min=int(rates[1]) if rates is not None else None,
                                support=role == "UTILITY", win=w[0] if w else None,
                                win_delta=w[1] if w else None, win_reason=w[2] if w else None)
        # ---- one note at most
        if busy or not calls_on or 0.0 <= t - self._last_note_t < GLOBAL_GAP_S or t - self._first_t < WATCH_S:
            return []
        alive = not bool(getattr(me, "is_dead", False))
        # 6) objective with the number advantage (fallback of the macro planner: busy covers it)
        obj = number_advantage(game, objectives, role) if alive else None
        if obj is not None and self._due("objective", obj[2], t):
            return [self._emit(EdgeNote("objective", f"edge:obj:{obj[2]}", "OBJECTIF", obj[0], obj[1], obj[2]), t, gt)]
        # 2) kill window (laning, in lane, both alive)
        if alive and opp is not None and not in_base and gt < LANING_END_GT and role in ("TOP", "MIDDLE", "BOTTOM") \
                and not bool(getattr(opp, "is_dead", False)) and gd is not None:
            st = getattr(game, "champion_stats", None) or {}
            mx = _f(st.get("maxHealth"))
            hp = _f(st.get("currentHealth")) / mx if mx > 0 else None
            try:
                from treeaicoach.draft_edge import live_prior

                extra = live_prior(me, opp).kill_extra
            except Exception:
                extra = 0.0
            ok, _score = kill_window(int(getattr(me, "level", 1)), int(getattr(opp, "level", 1)), gd, hp, extra)
            opened = ok and not self._kill_open
            self._kill_open = ok
            ident = f"{getattr(me, 'level', 1)}:{getattr(opp, 'level', 1)}:{int(gd // 300)}"
            if opened and self._due("kill", ident, t):
                text = kill_window_text(_name(opp), int(me.level), int(opp.level), gd)
                return [self._emit(EdgeNote("kill", f"lvl_ahead:edge:{ident}", "FENÊTRE DE KILL", text, "", ident),
                                   t, gt)]
        else:
            self._kill_open = False
        # 2b) bounties (kill streaks, bounty.py): I am the shutdown target / focus their streaking carry
        if alive and not in_base and gt >= 300.0:
            from treeaicoach.bounty import bounty_call

            bc = bounty_call(game)
            if bc is not None and (recall_ok or "rentre" not in bc[0].lower()) and self._due("bounty", bc[1], t):
                return [self._emit(EdgeNote("bounty", f"edge:bounty:{bc[1]}", "PRIME", bc[0], "", bc[1]), t, gt)]
        # 3) optimal recall: my gold reaches a real piece of the meta build
        gold = _f(getattr(game, "current_gold", 0.0))
        if alive and not in_base and recall_ok and gold >= RECALL_MIN_VALUE:
            try:
                from treeaicoach.itemization import load_items, meta_build

                table = load_items()
                owned = _items(me)
                build = meta_build(str(getattr(me, "champion_alias", "") or ""), role, getattr(game, "game_mode", ""))
                rt = recall_target(next_build_item(build, owned, table), owned, gold, table)
            except Exception:
                rt = None
            if rt is not None and self._due("recall", ",".join(map(str, rt[2])), t):
                return [self._emit(EdgeNote("recall", f"comp_ready:edge:{rt[2][0]}", "RAPPEL",
                                            recall_text(rt[0], gold), "", ",".join(map(str, rt[2]))), t, gt)]
        # 4) adaptive defense: in the shop / while dead (the moment to buy)
        if (in_base or not alive) and gt >= 300.0:
            dc = defense_call(list(getattr(game, "enemies", None) or []), str(getattr(me, "champion_alias", "") or ""),
                              role, _items(me))
            if dc is not None and self._due("defense", dc[1], t):
                item = dc[0].split(" : ", 1)[0].replace("Achète ", "").split(" + ")[-1]
                if not (item_text and item and item.lower() in str(item_text).lower()):
                    return [self._emit(EdgeNote("defense", f"edge:def:{dc[1]}", "DÉFENSE", dc[0], "", dc[1]), t, gt)]
        # 7) farm nudge: far below the target of my rank, alive in lane
        if alive and not in_base and gt >= FARM_MIN_GT and rates is not None:
            nudge = farm_nudge(rates[0], cs_t)
            ident = f"farm:{int(gt // 300)}"
            if nudge is not None and self._due("farm", ident, t):
                return [self._emit(EdgeNote("farm", f"cs_low:edge:{int(gt // 300)}", "FARM", nudge, "", ident), t, gt)]
        return []

    def _win_line(self, t: float, game: Any, summary: Any) -> str | None:
        try:
            from treeaicoach import hype as H

            f = H.factors_from_game(game, summary)
            if f is None:
                return None
            p = H.win_probability(f)
            hist = self._win_hist
            hist.append((t, p, f))
            while hist and t - hist[0][0] > 4 * WIN_REF_S:
                hist.popleft()
            ref = next((h for h in hist if t - h[0] <= WIN_REF_S), None)
            if ref is not None and t - ref[0] >= 10.0:
                delta = 100.0 * (p - ref[1])
                reason = swing_reason(ref[2], f) if abs(delta) >= WIN_SWING_POINTS else None
                self._win = (float(p), float(delta), reason)
                return win_text(p, delta, reason)
            self._win = (float(p), None, None)
            return win_text(p, 0.0, None)
        except Exception:
            log.debug("win line failed", exc_info=True)
            return None


__all__ = ["EdgeNote", "EdgeStats", "LiveEdge", "lane_opponent", "lane_gold_diff", "gold_diff_text", "kill_window",
           "kill_window_text", "next_build_item", "recall_target", "recall_text", "damage_profile",
           "defense_call", "win_terms", "swing_reason", "win_text", "number_advantage",
           "farm_rates", "farm_targets", "farm_text", "farm_nudge", "fmt_or", "fmt_num"]
