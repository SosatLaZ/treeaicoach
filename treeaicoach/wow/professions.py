"""WoW Forever professions: Classic skill-up formula + cheapest levelling path (mirror of Modules/Prof.lua).

Data: ``treeaicoach/wow/data/recipes.json.gz`` built by ``tools/wow_build_data.py --only recipes`` from the
client tables (SkillLineAbility colours, SpellReagents, SpellEffect 24 = crafted item). Pure, never raises.

Skill-up chance (Classic, as in the server cores): skill < yellow -> 1 (orange); skill >= grey -> 0 (grey);
otherwise (grey - skill) / (grey - yellow) (yellow, then green from (yellow + grey) / 2). Forever's Legacy perk
« Working Overtime » adds 4 % per rank (5 ranks) to the chance, capped at 1.
Forever Legacy points: the 6 primary crafting professions give one point at 150, 225 and 300.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
from typing import Any, Callable

log = logging.getLogger(__name__)

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "recipes.json.gz")
OVERTIME_PER_RANK = 0.04
LEGACY_STEPS = (150, 225, 300)
# rank name, min character level, skill needed to train it, max skill
RANKS = (("Apprenti", 5, 0, 75), ("Compagnon", 10, 50, 150), ("Expert", 20, 125, 225), ("Artisan", 35, 200, 300))
_CACHE: dict[str, Any] = {}

# recipe row indexes
SPELL, ITEM, COUNT, MIN, YELLOW, GREY, UPS, RECIPE, NAME, MATS = range(10)


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


def plan(line: int, skill: int, target: int, price: Callable[[int], float | None],
         stock: dict[int, int] | None = None, known: set[int] | None = None, overtime: int = 0,
         known_only: bool = False,
         rows: list[list] | None = None, max_steps: int = 400) -> dict:
    """Greedy cheapest path: at each skill point pick the recipe with the lowest expected cost per point.
    Returns {steps: [{spell, name, from, to, crafts, color, cost}], shopping: {item: n}, cost}."""
    rows = rows if rows is not None else recipes(line)
    stock = dict(stock or {})
    shopping: dict[int, int] = {}
    steps: list[dict] = []
    total = 0.0
    s = int(skill)
    guard = 0
    while s < target and guard < max_steps:
        guard += 1
        best, best_v = None, None
        for r in rows:
            if known_only and r[RECIPE] and (known is None or r[SPELL] not in known):
                continue
            c = chance(s, r[YELLOW], r[GREY], overtime, r[MIN])
            if c <= 0:
                continue
            v = (craft_cost(r, price, stock) + 1) / (c * r[UPS])
            if best_v is None or v < best_v:
                best, best_v = r, v
        if best is None:
            break
        c = chance(s, best[YELLOW], best[GREY], overtime, best[MIN])
        crafts = 1.0 / c
        m = best[MATS]
        for k in range(0, len(m), 2):
            need = m[k + 1] * crafts
            use = min(stock.get(m[k], 0), need)
            stock[m[k]] = stock.get(m[k], 0) - use
            if need - use > 0:
                shopping[m[k]] = shopping.get(m[k], 0) + (need - use)
                p = price(m[k])
                total += (need - use) * (p if p is not None else 10_000)
        last = steps[-1] if steps else None
        if last and last["spell"] == best[SPELL]:
            last["to"] = min(target, s + best[UPS])
            last["crafts"] += crafts
        else:
            steps.append({"spell": best[SPELL], "name": best[NAME], "from": s, "to": min(target, s + best[UPS]),
                          "crafts": crafts, "color": color(s, best)})
        s += best[UPS]
    for st in steps:
        st["crafts"] = int(st["crafts"] + 0.999)
    return {"steps": steps, "shopping": {k: int(v + 0.999) for k, v in shopping.items()}, "cost": int(total),
            "reached": s}


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
