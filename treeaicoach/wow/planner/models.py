"""Player model for the route planner: XP, feasibility, « galère », time per objective, zone switch.

The formulas are the Classic ones (cmangos / vmangos) collected in the planner research, report 4 §1–6.
The constants are priors. :class:`Calib` replaces them with what the addon measured (learned stats, see
SCHEMA.md §2). Every function is pure and O(1). Nothing here raises on odd input.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# ------------------------------------------------------------------ XP curve (WF = Classic)
# XP from level L to L+1 (Classic table, sums to 4,084,700 = the WF total).
XP_NEEDED: dict[int, int] = {
    1: 400, 2: 900, 3: 1400, 4: 2100, 5: 2800, 6: 3600, 7: 4500, 8: 5400, 9: 6500, 10: 7600,
    11: 8800, 12: 10100, 13: 11400, 14: 12900, 15: 14400, 16: 16000, 17: 17700, 18: 19400, 19: 21300, 20: 23200,
    21: 25200, 22: 27300, 23: 29400, 24: 31700, 25: 34000, 26: 36400, 27: 38900, 28: 41400, 29: 44300, 30: 47400,
    31: 50800, 32: 54500, 33: 58600, 34: 62800, 35: 67100, 36: 71600, 37: 76100, 38: 80800, 39: 85700, 40: 90700,
    41: 95800, 42: 101000, 43: 106300, 44: 111800, 45: 117500, 46: 123200, 47: 129100, 48: 135100, 49: 141200, 50: 147500,
    51: 153900, 52: 160400, 53: 167100, 54: 173900, 55: 180800, 56: 187900, 57: 195000, 58: 202300, 59: 209800,
}
MAX_LEVEL = 60
GROUP_RATE = (1.0, 1.0, 1.0, 1.166, 1.3, 1.4)       # index = group size (0 unused)


def xp_needed(level: int) -> int:
    return XP_NEEDED.get(int(level), 0) if level < MAX_LEVEL else 0


def rnd(x: float) -> int:
    """Round half up (the client's rounding of XP)."""
    return int(math.floor(x + 0.5))


def zero_diff(pl: int) -> int:
    """ZD of the mob-XP formula, by player level."""
    for top, zd in ((7, 5), (9, 6), (11, 7), (15, 8), (19, 9), (29, 11), (39, 12), (44, 13), (49, 14),
                    (54, 15), (59, 16)):
        if pl <= top:
            return zd
    return 17


def grey_level(pl: int) -> int:
    """Highest mob / quest level that is grey (no XP) for a player of level ``pl``."""
    if pl <= 5:
        return 0
    if pl <= 39:
        return pl - 5 - pl // 10
    if pl <= 59:
        return pl - 1 - pl // 5
    return 51


def kill_xp(pl: int, ml: int, elite: bool = False, rested: bool = False) -> int:
    """Solo XP of one kill (Formulas.h)."""
    if ml <= grey_level(pl):
        return 0
    base = 5 * pl + 45
    if ml >= pl:
        xp = base * (1 + 0.05 * min(ml - pl, 4))
    else:
        xp = base * (1 - (pl - ml) / zero_diff(pl))
    if elite:
        xp *= 2.5
    if rested:
        xp *= 2
    return max(0, rnd(xp))


def group_kill_xp(levels: list[int], ml: int, me: int = 0, elite: bool = False) -> int:
    """XP that member ``me`` gets for one group kill (Group.cpp): computed with the highest member for whom
    the mob is not grey, × rate(n) × lvl_i / Σlvl; halved (+1) when the top member greys the mob."""
    if not levels:
        return 0
    n = min(len(levels), 5)
    if n == 1:
        return kill_xp(levels[0], ml, elite)
    ok = [lv for lv in levels if ml > grey_level(lv)]
    if not ok:
        return 0
    m = max(ok)
    if levels[me] > m:
        return 0
    xp = kill_xp(m, ml, elite) * GROUP_RATE[n] * levels[me] / float(sum(levels))
    if max(levels) > m:
        return rnd(xp * 0.5 + 1)
    return rnd(xp)


def quest_xp_factor(pl: int, ql: int) -> float:
    d = pl - ql
    if d <= 5:
        return 1.0
    return {6: 0.8, 7: 0.6, 8: 0.4, 9: 0.2}.get(d, 0.1)


def quest_xp(base: int, pl: int, ql: int) -> int:
    """XP of a turn-in at player level ``pl`` (QuestDef.cpp decay), rounded to 5. 0 at the cap."""
    if pl >= MAX_LEVEL or base <= 0:
        return 0
    f = quest_xp_factor(pl, ql)
    return base if f >= 1 else int(rnd(base * f / 5.0) * 5)


def quest_xp_estimate(ql: int) -> int:
    """Full XP of an unseen quest: 55·ql + ql² (≈ 2100 at level 26)."""
    return 55 * ql + ql * ql if ql > 0 else 0


def green_range(pl: int) -> int:
    for top, g in ((9, 4), (19, 5), (29, 6), (39, 7), (44, 8), (49, 9), (54, 10), (59, 11)):
        if pl <= top:
            return g
    return 12


def quest_color(pl: int, ql: int) -> str:
    d = ql - pl
    if d >= 5:
        return "red"
    if d >= 3:
        return "orange"
    if d >= -2:
        return "yellow"
    if ql > grey_level(pl):
        return "green"
    return "grey"


def mob_color(pl: int, ml: int) -> str:
    return quest_color(pl, ml)


# ------------------------------------------------------------------ combat power
def lf(delta: int) -> float:
    """Relative power vs a mob, Δ = player − mob level (1.10^Δ, cliff at −3)."""
    d = int(delta)
    if d >= -2:
        return 1.10 ** d
    return {-3: 0.62, -4: 0.50, -5: 0.38}.get(d, 0.25)


@dataclass(frozen=True)
class ClassModel:
    ttk: float          # seconds, same-level normal mob, reference gear
    downtime: float     # share of time eating / drinking
    cap: float          # solo capacity (normal-mob units)
    dps_ref: float      # DPS_ref = dps_ref × level
    tank: bool = False
    healer: bool = False


CLASSES: dict[str, ClassModel] = {
    "HUNTER": ClassModel(15.0, 0.15, 2.6, 2.1, tank=True),
    "WARLOCK": ClassModel(20.0, 0.125, 2.5, 1.6),
    "PALADIN": ClassModel(27.0, 0.125, 2.2, 1.6, tank=True, healer=True),
    "DRUID": ClassModel(20.0, 0.20, 2.2, 1.6, tank=True, healer=True),
    "SHAMAN": ClassModel(20.0, 0.25, 2.1, 1.6, healer=True),
    "WARRIOR": ClassModel(21.0, 0.375, 2.0, 1.6, tank=True),
    "PRIEST": ClassModel(22.0, 0.275, 1.9, 1.6, healer=True),
    "ROGUE": ClassModel(17.0, 0.30, 1.8, 1.6),
    "MAGE": ClassModel(12.0, 0.325, 1.8, 1.6),
}
DEFAULT_CLASS = ClassModel(20.0, 0.20, 2.1, 1.6)
RANK_UNITS = {0: 1.0, 1: 4.0, 2: 6.0, 3: 10.0, 4: 1.5}


def class_model(cls: str | None) -> ClassModel:
    return CLASSES.get(str(cls or "").upper(), DEFAULT_CLASS)


def gear_factor(cls: str | None, level: int, dps: float | None) -> float:
    """g = (DPS / DPS_ref(L))^0.5, 1.0 when DPS is unknown."""
    if not dps or dps <= 0 or level <= 0:
        return 1.0
    return max(0.5, min(1.6, (dps / (class_model(cls).dps_ref * level)) ** 0.5))


@dataclass
class Member:
    key: str
    cls: str = "HUNTER"
    level: int = 1
    dps: float = 0.0
    speed: float = 7.0


def capacity(m: Member, mob_level: int) -> float:
    return class_model(m.cls).cap * gear_factor(m.cls, m.level, m.dps) * lf(m.level - mob_level)


def synergy(members: list[Member], elites: bool) -> float:
    cms = [class_model(m.cls) for m in members]
    tank = any(c.tank for c in cms)
    heal = any(c.healer for c in cms)
    if len(members) > 1 and tank and heal:
        return 1.25
    if elites and not heal and len(members) > 1:
        return 0.85
    return 1.0


def feasibility(members: list[Member], mob_level: int, rank: int = 0, pull: int = 1) -> float:
    """F = C / Req (capacity of the group vs the worst pull, elite = 4 units)."""
    if not members:
        return 0.0
    req = RANK_UNITS.get(int(rank), 1.0) * max(1, int(pull))
    c = sum(capacity(m, mob_level) for m in members) * synergy(members, rank in (1, 2, 3))
    return c / req


def feas_label(f: float) -> str:
    if f >= 1.3:
        return "facile"
    if f >= 1.0:
        return "faisable"
    if f >= 0.8:
        return "galere"
    if f >= 0.6:
        return "risque"
    return "impossible"


def p_death(r: float) -> float:
    """Death chance of a fight from the danger ratio R (logistic, centred on 0.85)."""
    try:
        return 1.0 / (1.0 + math.exp(-(r - 0.85) / 0.08))
    except OverflowError:
        return 0.0 if r < 0.85 else 1.0


# ------------------------------------------------------------------ calibration (learned stats)
@dataclass
class Calib:
    """Priors, overwritten by the addon's learned stats (SCHEMA.md §2)."""
    ttk: dict[int, float] = field(default_factory=dict)       # Δ(mob−me) → measured seconds
    search: float = 10.0                                      # s between kills
    downtime: float | None = None
    eff: float = 1.6                                          # E_player
    kappa: dict[int, float] = field(default_factory=dict)      # uiMapID → competition
    drops: dict[int, tuple[float, float]] = field(default_factory=dict)  # item → (k, n)
    respawn: dict[int, float] = field(default_factory=dict)    # npc → s
    flights: dict[tuple[int, int], float] = field(default_factory=dict)
    detour: dict[int, float] = field(default_factory=dict)
    quest_secs: dict[int, float] = field(default_factory=dict)
    xph: float = 0.0

    def k(self, zone: int) -> float:
        return max(1.0, min(5.0, self.kappa.get(int(zone or 0), 1.0)))

    def drop_p(self, item: int, prior: float | None) -> float:
        """Bayesian p̂ = (k + α·p_prior) / (n + α), α = 10, prior 0.45 when unknown."""
        p0 = prior if prior and 0 < prior <= 1 else 0.45
        k, n = self.drops.get(int(item or 0), (0.0, 0.0))
        return max(0.01, min(1.0, (k + 10 * p0) / (n + 10)))


def kill_cycle(cls: str, level: int, mob_level: int, calib: Calib | None = None, dps: float = 0.0,
               group: int = 1, zone: int = 0, rank: int = 0) -> float:
    """Seconds per kill: TTK·k_lvl·k_group + T_search·κ + downtime (report 4 §5)."""
    calib = calib or Calib()
    cm = class_model(cls)
    d_mob = int(mob_level) - int(level)
    if d_mob in calib.ttk:
        ttk = calib.ttk[d_mob]
    else:
        g = gear_factor(cls, level, dps)
        ttk = cm.ttk / (g * g) / lf(-d_mob)
    if rank in (1, 2, 3):
        ttk *= 3.0
    k_group = 1.0 / (1.0 + 0.8 * (max(1, group) - 1))
    share = calib.downtime if calib.downtime is not None else cm.downtime
    share = max(0.0, min(0.7, share))
    down = share * ttk / (1 - share)
    return ttk * k_group + calib.search * calib.k(zone) + down * k_group


@dataclass
class ObjTime:
    median: float     # seconds
    p80: float
    kills: float      # expected kills (for kill XP)
    corpses: float = 0.0


def negbin_quantile(n: int, p: float, q: float) -> float:
    """q-quantile of the number of trials to get n successes at chance p (normal approximation)."""
    if p >= 1:
        return float(n)
    mu = n / p
    sd = math.sqrt(n * (1 - p)) / p
    z = {0.5: 0.0, 0.8: 0.8416, 0.9: 1.2816}.get(q, 0.8416)
    return max(float(n), mu + z * sd)


def objective_time(o: dict, cls: str, level: int, calib: Calib | None = None, dps: float = 0.0,
                   group: int = 1, zone: int = 0) -> ObjTime:
    """Service time of one objective (seconds, before E_player), report 4 §5 + report 5 A5."""
    calib = calib or Calib()
    kind = str(o.get("k") or "other")
    need = max(0, int(_num(o.get("n"), 1)) - int(_num(o.get("have"), 0)))
    if need <= 0:
        return ObjTime(0.0, 0.0, 0.0)
    ml = int(_num(o.get("ml"), level)) or level
    rank = int(_num(o.get("rank"), 0))
    k = max(1, int(group))
    if kind == "kill":
        c = kill_cycle(cls, level, ml, calib, dps, k, zone, rank)
        t = need * c
        return ObjTime(t, t * 1.25, float(need), float(need))
    if kind == "item":
        p = calib.drop_p(int(_num(o.get("id"), 0)), _num(o.get("p"), 0) or None)
        mult = 1 if int(_num(o.get("plf"), 0)) else k
        c = kill_cycle(cls, level, ml, calib, dps, k, zone, rank)
        corpses = mult * need / p
        corpses80 = negbin_quantile(mult * need, p, 0.8)
        sp = int(_num(o.get("sp"), 0))
        if sp > 0:                                      # respawn starvation: ≤ sp mobs per respawn period
            resp = calib.respawn.get(int(_num((o.get("npc") or [0])[0] if o.get("npc") else 0, 0)), 300.0)
            c = max(c, resp * calib.k(zone) / sp)
        return ObjTime(corpses * c, corpses80 * c, corpses, corpses)
    if kind == "obj":
        per = 33.0 * calib.k(zone)
        t = need * per                                  # each member clicks their own objects, in parallel
        return ObjTime(t, t * 1.3, 0.0)
    if kind in ("explore", "talk", "spell"):
        return ObjTime(30.0, 45.0, 0.0)
    return ObjTime(60.0, 120.0, 0.0)


def _num(v, default=0):
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ galère / verdicts
@dataclass
class Galere:
    score: float
    reason: str
    label: str


def galere(q: dict, members: list[Member], calib: Calib | None = None, zone: int = 0) -> Galere:
    """G ∈ [0,100] with its main reason (report 4 §6.2; group drop rule of report 5 C3)."""
    calib = calib or Calib()
    if not members:
        return Galere(0.0, "", "fluide")
    lvl = max(m.level for m in members)
    k = len(members)
    terms: list[tuple[float, str]] = []
    worst_f, worst_txt = 9.0, ""
    for o in q.get("o") or []:
        if o.get("k") in ("kill", "item"):
            ml = int(_num(o.get("mx"), 0) or _num(o.get("ml"), lvl))
            f = feasibility(members, ml, int(_num(o.get("rank"), 0)))
            if f < worst_f:
                worst_f = f
                worst_txt = "élites" if int(_num(o.get("rank"), 0)) in (1, 2, 3) else "mobs"
                worst_txt += f" niv {ml}"
    if q.get("elite") and worst_f > 0.99 and k < max(2, int(_num(q.get("grp"), 0))):
        worst_f, worst_txt = min(worst_f, 0.65 * k / max(2, int(_num(q.get("grp"), 3)))), "quête élite"
    if worst_f < 9:
        terms.append((30 * max(0.0, 1 - worst_f), f"galère : {worst_txt}"))
    col = quest_color(lvl, int(_num(q.get("lv"), lvl)))
    if col == "orange":
        terms.append((15.0, "quête orange"))
    elif col == "red":
        terms.append((25.0, "quête rouge"))
    for o in q.get("o") or []:
        if o.get("k") == "item":
            need = max(1, int(_num(o.get("n"), 1)) - int(_num(o.get("have"), 0)))
            p = calib.drop_p(int(_num(o.get("id"), 0)), _num(o.get("p"), 0) or None)
            cv = min(1.0, 4.0 * math.sqrt((1 - p) / need))
            terms.append((12 * cv, f"taux de drop {int(round(p * 100))} %"))
            mult = 1 if int(_num(o.get("plf"), 0)) else k
            corpses = mult * need / p
            if k > 1 and corpses > 60 and p < 0.3:            # report 5 C3: always « galère »
                terms.append((45.0, f"{int(corpses)} corps à {int(round(p * 100))} % pour {k}"))
    kap = calib.k(zone)
    if kap > 1:
        terms.append((10 * (kap - 1), "camp disputé"))
    if q.get("esc") or q.get("timed"):
        terms.append((10.0, "escorte / chrono"))
    if worst_f < 9:
        pd = p_death(0.85 / max(0.05, worst_f))
        terms.append((15 * min(1.0, pd * 10) * (1 if worst_f < 1 else 0.2), "risque de mort"))
    score = max(0.0, min(100.0, sum(t for t, _ in terms)))
    reason = max(terms, key=lambda t: t[0])[1] if terms and score > 0 else ""
    label = "fluide" if score < 20 else "ok" if score < 45 else "galere" if score < 70 else "eviter"
    return Galere(score, reason, label)


def should_switch_zone(r_here: float, r_alt: float, travel_min: float, horizon_min: float,
                       non_grey_left: int = 99, level: int = 0, zone_median: int = 0) -> tuple[bool, str]:
    """Report 4 §6.5: leave when r_here < r_alt·(1 − T_tr/H)/1.1, or < 3 non-grey quests, or 5+ levels above
    the zone's median quest level."""
    if non_grey_left < 3:
        return True, f"plus que {non_grey_left} quête(s) utile(s) ici"
    if zone_median and level >= zone_median + 5:
        return True, f"tu as {level - zone_median} niveaux de plus que la zone"
    h = max(1.0, horizon_min)
    if r_alt > 0 and r_here < r_alt * max(0.0, 1 - travel_min / h) / 1.1:
        gain = int(round((r_alt * (1 - travel_min / h) / max(1.0, r_here) - 1) * 100))
        return True, f"ailleurs : +{gain} % d'XP/h après {int(round(travel_min))} min de trajet"
    return False, ""


def calib_from_learn(learn: dict | None, char_learn: dict | None) -> Calib:
    """Build :class:`Calib` from TreeAIDB.learn / chars[key].learn (SCHEMA.md §2). Never raises."""
    c = Calib()

    def mean(e, lo=None, hi=None, nmin=1):
        try:
            s, n = float(e.get("s", 0)), float(e.get("n", 0))
            if n < nmin:
                return None
            v = s / n
            if lo is not None:
                v = max(lo, v)
            if hi is not None:
                v = min(hi, v)
            return v
        except Exception:
            return None

    def items(t):
        if isinstance(t, list):
            return [(i + 1, v) for i, v in enumerate(t)]
        return list(t.items()) if isinstance(t, dict) else []

    try:
        cl = char_learn if isinstance(char_learn, dict) else {}
        for k, e in items(cl.get("ttk")):
            v = mean(e, 2, 300, 3) if isinstance(e, dict) else None
            if v is not None:
                c.ttk[int(float(k))] = v
        v = mean(cl.get("search") or {}, 1, 120, 5)
        if v is not None:
            c.search = v
        v = mean(cl.get("downtime") or {}, 0, 0.7, 5)
        if v is not None:
            c.downtime = v
        v = mean(cl.get("eff") or {}, 0.6, 4.0, 3)
        if v is not None:
            c.eff = v
        v = mean(cl.get("xph") or {}, 0, 1e6, 1)
        if v is not None:
            c.xph = v
        for k, e in items(cl.get("quests")):
            v = mean(e, 10, 10800) if isinstance(e, dict) else None
            if v is not None:
                c.quest_secs[int(float(k))] = v
        lr = learn if isinstance(learn, dict) else {}
        for k, e in items(lr.get("drops")):
            if isinstance(e, dict):
                c.drops[int(float(k))] = (float(e.get("k", 0) or 0), float(e.get("n", 0) or 0))
        for k, e in items(lr.get("respawn")):
            v = mean(e, 10, 3600) if isinstance(e, dict) else None
            if v is not None:
                c.respawn[int(float(k))] = v
        for k, e in items(lr.get("comp")):
            v = mean(e, 1, 5) if isinstance(e, dict) else None
            if v is not None:
                c.kappa[int(float(k))] = v
        for k, e in items(lr.get("detour")):
            v = mean(e, 1, 3) if isinstance(e, dict) else None
            if v is not None:
                c.detour[int(float(k))] = v
        for k, e in items(lr.get("flights")):
            v = mean(e, 5, 1200) if isinstance(e, dict) else None
            try:
                a, b = str(k).split("-", 1)
                if v is not None:
                    c.flights[(int(a), int(b))] = v
            except ValueError:
                pass
    except Exception:
        pass
    return c
