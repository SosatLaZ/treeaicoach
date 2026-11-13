"""WoW Forever professions: Classic skill-up formula + cheapest levelling path (mirror of Modules/Prof.lua).

Data: ``treeaicoach/wow/data/recipes.json.gz`` built by ``tools/wow_build_data.py --only recipes`` from the
client tables (SkillLineAbility colours, SpellReagents, SpellEffect 24 = crafted item). Pure, never raises.

Skill-up chance (Classic, as in the server cores): skill < yellow -> 1 (orange); skill >= grey -> 0 (grey);
otherwise (grey - skill) / (grey - yellow) (yellow, then green from (yellow + grey) / 2). Forever's Legacy perk
« Working Overtime » adds 4 % per rank (5 ranks) to the chance, capped at 1.
Forever Legacy points: the 6 primary crafting professions give one point at 150, 225 and 300.

PROFLEVEL (4.0.2): ``session_plan`` is the levelling planner of the addon (``Prof.Engine.Session`` in
wow_addon/TreeAI/Modules/Prof.lua), line for line: one craft at a time (whole counts), the USABLE recipe with the best
(cost or scarce reagents) per expected point; « full » mode reaches the target and buys what is missing (vendor at the
exact price, else AH / estimate, else UNKNOWN = farm / ask), « exact » mode uses only what you own + vendor reagents
(empty the bags). ``split`` = where the used reagents come from (bags, bank, mail; quest reserve taken from the bags
first) and what to buy where (vendor: margin + bundle rounding). Parity with the Lua engine: tests/wow/test_proflevel.py.
Row flags (index 10): 1 = learned with the profession, 2 = learn skill estimated (= yellow: Forever's client tables
hold no trainer requirement).
"""
from __future__ import annotations

import gzip
import json
import logging
import math
import os
from typing import Any, Callable

log = logging.getLogger(__name__)

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "recipes.json.gz")
OVERTIME_PER_RANK = 0.04
LEGACY_STEPS = (150, 225, 300)
# rank name, min character level, skill needed to train it, max skill
RANKS = (("Apprenti", 5, 0, 75), ("Compagnon", 10, 50, 150), ("Expert", 20, 125, 225), ("Artisan", 35, 200, 300))
_CACHE: dict[str, Any] = {}

# recipe row indexes (MIN = skill to learn it; FLAGS: 1 learned with the profession, 2 learn skill estimated)
SPELL, ITEM, COUNT, MIN, YELLOW, GREY, UPS, RECIPE, NAME, MATS, FLAGS = range(11)
FLAG_AUTO, FLAG_EST = 1, 2
UNKNOWN = 10_000
CRAFT_SEC = 3.5
STICKY, LEARN_COST = 0.85, 1.3
# (5.0 PROF) a recipe under 25 % (deep green: >= 4 crafts per point) is taken only when NO other usable recipe gives
# points; range of a step = expectation +/- 1.2816 sigma (8 times out of 10), sigma^2 = sum (1 - c) / c per craft
MIN_CHANCE = 0.25
Z80 = 1.2816
EPS = 1e-9


def load() -> dict:
    if "d" not in _CACHE:
        try:
            with gzip.open(DATA, "rt", encoding="utf-8") as fh:
                _CACHE["d"] = json.load(fh)
        except Exception:
            log.debug("recipes data unavailable", exc_info=True)
            _CACHE["d"] = {}
    return _CACHE["d"]


def recipes(line: int) -> list[list]:
    return list(load().get("recipes", {}).get(str(line), []))


def color(skill: int, row: list) -> str:
    y, g = row[YELLOW], row[GREY]
    if skill < row[MIN]:
        return "red"  # not learnable yet
    if g <= 0 or skill >= g:
        return "grey"
    if skill < y:
        return "orange"
    return "yellow" if skill < (y + g) / 2 else "green"


def chance(skill: int, yellow: int, grey: int, overtime: int = 0, min_rank: int = 1) -> float:
    """Classic skill-up probability for one craft (0..1)."""
    try:
        if skill < min_rank or grey <= 0 or skill >= grey:
            return 0.0
        if skill < yellow or grey <= yellow:
            return 1.0
        base = (grey - skill) / (grey - yellow)
        return max(0.0, min(1.0, base + OVERTIME_PER_RANK * max(0, min(5, int(overtime or 0)))))
    except Exception:
        return 0.0


def craft_cost(row: list, price: Callable[[int], float | None], stock: dict[int, int] | None = None) -> float:
    """Copper to buy the missing reagents for one craft (reagents in stock cost nothing)."""
    total = 0.0
    m = row[MATS]
    for k in range(0, len(m), 2):
        need = m[k + 1]
        have = (stock or {}).get(m[k], 0)
        missing = max(0, need - have)
        if missing:
            p = price(m[k])
            total += missing * (p if p is not None else 10_000)
    return total


def usable(row: list, sk: int, known: set | dict | None = None, learn_at: dict | None = None,
           patterns: bool = False) -> tuple[bool, str | None]:
    """Usable at skill ``sk``? -> (ok, to learn: "trainer" | "pattern" | None). Known (read in the profession window)
    or learned with the profession = usable; a trainer recipe once its requirement is met (read at the trainer, else
    the yellow threshold); an unknown pattern only with ``patterns``."""
    flags = row[FLAGS] if len(row) > FLAGS else 0
    if flags % 2 == 1 or (known is not None and row[SPELL] in known):
        return True, None
    item = row[RECIPE] or 0
    if item != 0 and not patterns:
        return False, None
    need = (learn_at or {}).get(row[SPELL])  # Lua: (learnAt and learnAt[r[1]]) or r[4] or 1 (0 is true in Lua)
    if need is None:
        need = row[MIN] if row[MIN] is not None else 1
    if sk < need:
        return False, None
    if item != 0:
        return True, "pattern"
    return True, ("trainer" if known is not None else None)


def chance2(row: list, sk: int, overtime: int = 0) -> float:
    """Skill-up chance of a usable recipe: orange 1, yellow / green (grey - sk) / (grey - yellow) + overtime."""
    y, g = row[YELLOW] or 0, row[GREY] or 0
    if g <= 0 or sk >= g:
        return 0.0
    if sk < y or g <= y:
        return 1.0
    c = (g - sk) / (g - y) + OVERTIME_PER_RANK * max(0, min(5, int(overtime or 0)))
    return max(0.0, min(1.0, c))


def color2(row: list, sk: int) -> str:
    y, g = row[YELLOW] or 0, row[GREY] or 0
    if g <= 0 or sk >= g:
        return "grey"
    if sk < y:
        return "orange"
    return "yellow" if sk < (y + g) / 2 else "green"


def session_plan(rows: list[list], skill: int, target: int, cap: int = 300, level: int = 60, mode: str = "full",
                 stock: dict[int, int] | None = None, known: set | dict | None = None,
                 learn_at: dict[int, int] | None = None, patterns: bool = False,
                 vendor: dict[int, int] | None = None, price: dict[int, float] | Callable[[int], float | None] | None = None,
                 value: dict[int, float] | None = None, overtime: int = 0, ranks=RANKS,
                 craft_sec: float = CRAFT_SEC, max_crafts: int = 3000) -> dict:
    """The addon's planner (Prof.Engine.Session), same decisions in the same order. See the module docstring."""
    skill, target, cap = int(skill), min(300, int(target)), int(cap)
    exact = mode == "exact"
    vendor, value = vendor or {}, value or {}
    pf = price if callable(price) else (lambda i: (price or {}).get(i))
    st = {k: v for k, v in (stock or {}).items() if (v or 0) > 0}
    res: dict = {"seq": [], "steps": [], "train": [], "learn": [], "use": {}, "buy": {}, "unknown": {}, "var": {},
                 "cost": 0.0, "crafts": 0}
    planned: set = set()
    last = None
    stick = None
    s: float = skill
    guard = 0
    while True:
        sk = int(s + EPS)
        if sk >= target:
            res["stop"] = "target"
            break
        if sk >= cap:
            nr = next((r for r in ranks if r[3] > cap), None)
            if nr is None:
                res["stop"] = "cap"
                break
            if level < nr[1]:
                res["stop"], res["next_rank"] = "level", nr
                break
            t = {"kind": "train", "at": sk, "rank": nr[0], "level": nr[1], "cap": nr[3]}
            res["train"].append(t)
            res["seq"].append(t)
            cap, last = nr[3], None
            continue
        guard += 1
        if guard > max_crafts:
            res["stop"] = "guard"
            break
        best = best_v = best_c = best_l = best_t = None
        for r in rows:
            ok, need_l = usable(r, sk, known, learn_at, patterns)
            if not ok:
                continue
            c = chance2(r, sk, overtime)
            if c <= 0:
                continue
            m = r[MATS] or []
            cst, scarce, feasible = 0.0, 0.0, True
            for k in range(0, len(m) - 1, 2):
                iid, n = m[k], m[k + 1]
                have = st.get(iid, 0)
                u = have if have < n else n
                miss = n - u
                if miss > 0:
                    vp = vendor.get(iid)
                    if vp is not None:
                        cst = cst + miss * vp
                    elif exact:
                        feasible = False
                        break
                    else:
                        p = pf(iid)
                        cst = cst + miss * (p if p is not None else UNKNOWN)
                w = value.get(iid)
                scarce = scarce + u * (w if w is not None else 1)
            if not feasible:
                continue
            ups = r[UPS] if r[UPS] is not None else 1
            v = (scarce + cst + 1) / (c * ups) if exact else (cst + scarce * 0.05 + 1) / (c * ups)
            # fewer back-and-forths: the recipe in progress keeps the lead within 15 %; one still to learn (trainer
            # visit) must be 30 % better
            if stick == r[SPELL]:
                v = v * STICKY
            if need_l and r[SPELL] not in planned:
                v = v * LEARN_COST
            tier = 0 if c >= MIN_CHANCE else 1
            if (best_v is None or tier < best_t or (tier == best_t and (
                    v < best_v - EPS
                    or (v <= best_v + EPS and (c > best_c + EPS or (c >= best_c - EPS and r[SPELL] < best[SPELL])))))):
                best, best_v, best_c, best_l, best_t = r, v, c, need_l, tier
        if best is None:
            res["stop"] = "mats" if exact else "none"
            break
        cost0 = res["cost"]
        m = best[MATS] or []
        for k in range(0, len(m) - 1, 2):
            iid, n = m[k], m[k + 1]
            have = st.get(iid, 0)
            u = have if have < n else n
            if u > 0:
                st[iid] = have - u
                res["use"][iid] = res["use"].get(iid, 0) + u
            miss = n - u
            if miss > 0:
                res["buy"][iid] = res["buy"].get(iid, 0) + miss
                p = vendor.get(iid)
                if p is None:
                    p = pf(iid)
                if p is not None:
                    res["cost"] = res["cost"] + miss * p
                else:
                    res["unknown"][iid] = res["unknown"].get(iid, 0) + miss
            if best_c < 1:
                res["var"][iid] = True
        if best_l and best[SPELL] not in planned:
            planned.add(best[SPELL])
            read = (learn_at or {}).get(best[SPELL])
            flags = best[FLAGS] if len(best) > FLAGS and best[FLAGS] else 0
            res["learn"].append({"spell": best[SPELL], "name": best[NAME], "at": sk, "kind": best_l,
                                 "item": best[RECIPE], "req": read or best[MIN] or 1,
                                 "est": bool(not read and flags & FLAG_EST)})
        s = s + best_c * (best[UPS] if best[UPS] is not None else 1)
        if s > cap:
            s = float(cap)  # the game never goes past the rank cap
        res["crafts"] += 1
        stick = best[SPELL]
        to = int(s + EPS)
        if last is not None and last["spell"] == best[SPELL]:
            last["crafts"] += 1
            last["to"] = to
            last["color_to"] = color2(best, sk)
            last["var"] += (1 - best_c) / best_c
            last["min_c"] = min(last["min_c"], best_c)
            last["cost1"] = res["cost"]
        else:
            last = {"kind": "craft", "spell": best[SPELL], "name": best[NAME], "from": sk, "to": to, "crafts": 1,
                    "color": color2(best, sk), "color_to": color2(best, sk), "learn": best_l,
                    "var": (1 - best_c) / best_c, "min_c": best_c, "row": best, "cost0": cost0, "cost1": res["cost"]}
            res["seq"].append(last)
            res["steps"].append(last)
    res["reached"], res["skill"], res["cap"] = int(s + EPS), s, cap
    res["time"] = res["crafts"] * craft_sec
    for x in res["steps"]:
        x["lo"], x["hi"] = craft_range(x["crafts"], x["var"], x["to"] - x["from"])
    return res


def craft_range(n: int, var: float, points: int = 0) -> tuple[int, int]:
    """Range (8 times out of 10) of the crafts of a step: expectation n, variance var (Prof.Engine.Range)."""
    if var <= EPS:
        return n, n
    sd = math.sqrt(var)
    lo = max(1, points, int(math.floor(n - Z80 * sd + 0.5)))
    return min(lo, n), int(math.ceil(n + Z80 * sd - EPS))


def audit(res: dict, skill: int, target: int, cap: int = 300, level: int = 60, stock: dict | None = None,
          known: set | dict | None = None, learn_at: dict | None = None, patterns: bool = False,
          vendor: dict | None = None, price: dict | Callable | None = None, overtime: int = 0, **_: Any) -> list[str]:
    """Sanity checks of a plan (Prof.Engine.Audit): the list of inconsistencies, empty = sound plan."""
    bad: list[str] = []
    skill, target, cap = int(skill), min(300, int(target)), int(cap)
    at, need, crafts, cur = skill, {}, 0, cap
    for x in res.get("seq", []):
        if x["kind"] == "train":
            if x["at"] != cur:
                bad.append(f"rank {x['rank']} taken at {x['at']} (cap {cur})")
            if level < x["level"]:
                bad.append(f"rank {x['rank']} needs level {x['level']}")
            if x["cap"] <= cur:
                bad.append("next rank without a higher cap")
            cur = x["cap"]
            continue
        r, nm = x["row"], x["name"]
        if x["from"] < skill:
            bad.append(f"{nm} starts at {x['from']} below skill {skill}")
        if x["from"] < at:
            bad.append(f"{nm} goes back ({x['from']} after {at})")
        if x["to"] < x["from"]:
            bad.append(f"{nm} goes down")
        if x["from"] >= target:
            bad.append(f"{nm} beyond the target {target}")
        if x["to"] > cur:
            bad.append(f"{nm} beyond the cap {cur}")
        if x["crafts"] < 1:
            bad.append(f"{nm}: 0 craft")
        if not usable(r, x["from"], known, learn_at, patterns)[0]:
            bad.append(f"{nm} not learnable at {x['from']}")
        if chance2(r, x["from"], overtime) <= 0:
            bad.append(f"{nm} grey at {x['from']}")
        ups = max(1, r[UPS] or 1)
        bound = 1.0
        for k in range(x["from"], max(x["from"], x["to"]) + 1):
            c = chance2(r, k, overtime)
            if c > 0:
                bound += 1 / (c * ups)
        if x["crafts"] > math.ceil(bound + EPS):
            bad.append(f"{nm}: {x['crafts']} crafts for {x['from']}->{x['to']} (at most {math.ceil(bound)})")
        if not (1 <= x["lo"] <= x["crafts"] <= x["hi"]):
            bad.append(f"{nm}: range {x['lo']}-{x['hi']} outside {x['crafts']}")
        m = r[MATS] or []
        for k in range(0, len(m) - 1, 2):
            need[m[k]] = need.get(m[k], 0) + m[k + 1] * x["crafts"]
        crafts += x["crafts"]
        at = x["to"]
    if crafts != res.get("crafts", 0):
        bad.append("craft total mismatch")
    if res.get("reached", 0) < skill or res.get("reached", 0) > max(cur, skill):
        bad.append(f"reached {res.get('reached')} outside [{skill}, {cur}]")
    stock, vendor = stock or {}, vendor or {}
    pf = price if callable(price) else (lambda i: (price or {}).get(i))
    cost = 0.0
    for iid in set(need) | set(res.get("use", {})) | set(res.get("buy", {})):
        u, b = res["use"].get(iid, 0), res["buy"].get(iid, 0)
        have = max(0, stock.get(iid, 0) or 0)
        if u + b != need.get(iid, 0):
            bad.append(f"item {iid}: {u + b} planned for {need.get(iid, 0)} consumed")
        if u > have:
            bad.append(f"item {iid}: {u} used for {have} owned")
        if b > 0 and u < have:
            bad.append(f"item {iid}: bought while {have - u} left")
        p = vendor.get(iid)
        if p is None:
            p = pf(iid)
        if b > 0:
            if p is not None:
                cost += b * p
            elif res["unknown"].get(iid, 0) != b:
                bad.append(f"item {iid}: no price and not flagged to farm")
    if abs(cost - res.get("cost", 0)) > 0.5:
        bad.append(f"cost {res.get('cost')} != {cost}")
    for le in res.get("learn", []):
        if le["at"] < skill:
            bad.append(f"learn {le['name']} at {le['at']} below skill {skill}")
        if le["req"] > le["at"]:
            bad.append(f"learn {le['name']} at {le['at']}: requirement {le['req']}")
        if le["kind"] == "pattern" and not patterns:
            bad.append(f"pattern {le['name']} not asked for")
    return bad


def split(res: dict, have: dict | None = None, reserve: dict[int, int] | None = None,
          vinfo: dict[int, tuple[int, int]] | None = None,
          price: dict[int, float] | Callable[[int], float | None] | None = None) -> dict:
    """Logistics of a plan (Prof.Engine.Split): reagents from bags, then bank, then mail (the quest reserve is taken
    from the bags first); vendor purchases with a margin (yellow/green crafts, unit price <= 1 silver) rounded to the
    vendor bundle; AH purchases at the known price; the rest = farm / ask."""
    have, reserve, vinfo = have or {}, reserve or {}, vinfo or {}
    bags, bank, mail = have.get("bags", {}), have.get("bank", {}), have.get("mail", {})
    pf = price if callable(price) else (lambda i: (price or {}).get(i))
    out: dict = {"bank": {}, "mail": {}, "vendor": [], "ah": [], "farm": [], "vendor_cost": 0, "ah_cost": 0.0}
    for iid in sorted(res.get("use", {})):
        n = res["use"][iid]
        rsv = reserve.get(iid, 0)
        in_bags = max(0, bags.get(iid, 0) - rsv)
        rsv = max(0, rsv - bags.get(iid, 0))
        in_bank = max(0, bank.get(iid, 0) - rsv)
        rsv = max(0, rsv - bank.get(iid, 0))
        in_mail = max(0, mail.get(iid, 0) - rsv)
        rest = max(0, n - in_bags)
        b = min(rest, in_bank)
        if b > 0:
            out["bank"][iid] = b
        rest -= b
        ml = min(rest, in_mail)
        if ml > 0:
            out["mail"][iid] = ml
    for iid in sorted(res.get("buy", {})):
        n = res["buy"][iid]
        vi = vinfo.get(iid)
        if vi:
            unit, lot = vi[0] or 0, max(1, vi[1] or 1)
            margin = max(1, -(-n // 10)) if (res.get("var", {}).get(iid) and unit <= 100) else 0
            purchase = -(-(n + margin) // lot) * lot
            out["vendor"].append({"id": iid, "n": n, "buy": purchase, "margin": purchase - n, "unit": unit,
                                  "cost": purchase * unit})
            out["vendor_cost"] += purchase * unit
        elif iid in res.get("unknown", {}):
            out["farm"].append({"id": iid, "n": n})
        else:
            unit = pf(iid)
            unit = unit if unit is not None else 0
            out["ah"].append({"id": iid, "n": n, "unit": unit, "cost": n * unit})
            out["ah_cost"] += n * unit
    return out


def plan(line: int, skill: int, target: int, price: Callable[[int], float | None],
         stock: dict[int, int] | None = None, known: set[int] | None = None, overtime: int = 0,
         known_only: bool = False,
         rows: list[list] | None = None, max_steps: int = 400) -> dict:
    """Former contract (still used by the app): {steps: [{spell, name, from, to, crafts, color}], shopping, used,
    cost, reached} — now computed by ``session_plan`` (full mode, no rank cap)."""
    rows = rows if rows is not None else recipes(line)
    res = session_plan(rows, skill, target, cap=300, mode="full", stock=stock, known=known, patterns=not known_only,
                       price=price, overtime=overtime, max_crafts=max(3000, max_steps))
    steps = [{"spell": x["spell"], "name": x["name"], "from": x["from"], "to": x["to"], "crafts": x["crafts"],
              "color": x["color"]} for x in res["steps"]]
    return {"steps": steps, "shopping": dict(res["buy"]), "used": dict(res["use"]), "cost": int(res["cost"]),
            "reached": res["reached"]}


def legacy_points(professions: dict[int, int]) -> dict:
    """{line: skill} -> earned/possible Legacy points (6 primary crafting professions only) + next step."""
    lines = {int(k): v for k, v in (load().get("lines") or {}).items()}
    earned, nxt = 0, None
    for line, sk in professions.items():
        kind = (lines.get(int(line)) or ["?"])[0]
        if kind != "c":
            continue
        for step in LEGACY_STEPS:
            if sk >= step:
                earned += 1
            elif nxt is None or step - sk < nxt[1] - professions.get(nxt[0], 0):
                nxt = (int(line), step)
                break
    return {"earned": earned, "next": nxt}


def next_rank(skill: int, max_skill: int) -> tuple[str, int, int] | None:
    """Next rank to train: (name, character level, skill needed) once skill nears the cap."""
    for name, lvl, need, cap in RANKS:
        if cap > max_skill:
            return name, lvl, need
    return None
