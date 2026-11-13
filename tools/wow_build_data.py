#!/usr/bin/env python3
"""Build TreeAI's WoW Forever game data from the official client tables (offline, deterministic).

Source: Blizzard client DB2 tables as served by wago.tools (product ``wowf``), build ``BUILD`` below,
French locale. Output:
  treeaicoach/wow/data/{spells,items,zones,meta}.json.gz   (read by treeaicoach/wow/gamedata.py)
  treeaicoach/wow/data/taxi.json.gz   (flight graph + map bounds, read by treeaicoach/wow/planner/data.py)
  wow_addon/TreeAI/Data/PetFood.lua, Data/Consumables.lua  (small Lua tables for the addon)
  treeaicoach/wow/data/talents.json.gz + wow_addon/TreeAI/Data/Talents.lua  (talent trees + validated
      levelling builds from treeaicoach/wow/talent_builds.py)

  wow_addon/TreeAI/Data/Recipes.lua + treeaicoach/wow/data/recipes.json.gz  (tradeskill recipes: SkillLineAbility
  colours, SpellReagents, SpellEffect 24 crafted item, recipe items + their required skill, vendor unit prices
  (ItemSparse BuyPrice / VendorStackCount), leather farming bands; ``--only recipes``)

Usage:  python tools/wow_build_data.py [--cache DIR] [--build 1.60.1.70862] [--offline] [--only taxi|talents|recipes]
CSV files are downloaded once into --cache (default: a temp dir) and reused. Same CSVs -> same bytes out.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import sys
import tempfile
import urllib.request

BUILD = "1.60.1.70862"
URL = "https://wago.tools/db2/{table}/csv?build={build}&locale=frFR"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "treeaicoach", "wow", "data")
ADDON_DATA = os.path.join(ROOT, "wow_addon", "TreeAI", "Data")

TABLES = ("SpellName", "SpellLevels", "SkillLineAbility", "SkillLine", "ChrClasses", "ItemSparse", "Item",
          "ItemXItemEffect", "ItemEffect", "SpellEffect", "SpellMisc", "SpellDuration", "RandPropPoints",
          "ItemArmorTotal", "ItemArmorQuality", "ItemArmorShield", "ArmorLocation", "ItemDamageOneHand",
          "ItemDamageTwoHand", "ItemDamageRanged", "ItemDamageThrown", "ItemDamageWand", "AreaTable",
          "SpellReagents")
TAXI_TABLES = ("TaxiNodes", "TaxiPath", "TaxiPathNode", "UiMapAssignment", "UiMap")
TALENT_TABLES = ("Talent", "TalentTab", "SpellName", "Spell", "SpellEffect", "SpellMisc", "SpellDuration")

# Class skill lines (talent trees) -> class token. ChrClasses ids: 1 war, 2 pal, 3 hunt, 4 rog, 5 pri,
# 7 sham, 8 mage, 9 lock, 11 druid.
CLASS_LINES = {
    "WARRIOR": (26, 256, 257), "PALADIN": (594, 267, 184), "HUNTER": (50, 163, 51),
    "ROGUE": (253, 38, 39), "PRIEST": (613, 56, 78), "SHAMAN": (375, 373, 374),
    "MAGE": (237, 8, 6), "WARLOCK": (355, 354, 593), "DRUID": (574, 134, 573),
}
PETFOOD = {1: "meat", 2: "fish", 3: "cheese", 4: "bread", 5: "fungus", 6: "fruit", 7: "meat", 8: "fish"}
JUNK_NAME = ("OLD", "Deprecated", "DEP", "Monster -", "TEST", "Test ", "[PH]", "zz", "DEBUG", "QA", "Unused",
             "NPC ", "PVP ")

# InventoryType -> RandPropPoints column (0: head/chest/legs/2H, 1: shoulder/hands/waist/feet/trinket,
# 2: wrist/neck/back/finger/off-hand/shield, 3: one-hand weapon, 4: ranged/thrown/wand/relic).
SLOT_COL = {1: 0, 5: 0, 20: 0, 7: 0, 17: 0, 3: 1, 10: 1, 6: 1, 8: 1, 12: 1, 9: 2, 2: 2, 16: 2, 11: 2,
            14: 2, 23: 2, 13: 3, 21: 3, 22: 3, 15: 4, 25: 4, 26: 4, 28: 4}
ARMOR_COL = {1: ("Cloth", "Clothmodifier"), 2: ("Leather", "Leathermodifier"), 3: ("Mail", "Chainmodifier"),
             4: ("Plate", "Platemodifier")}
WEAPON_TABLE = {0: "ItemDamageOneHand", 4: "ItemDamageOneHand", 7: "ItemDamageOneHand",
                13: "ItemDamageOneHand", 15: "ItemDamageOneHand", 11: "ItemDamageOneHand",
                1: "ItemDamageTwoHand", 5: "ItemDamageTwoHand", 6: "ItemDamageTwoHand", 8: "ItemDamageTwoHand",
                10: "ItemDamageTwoHand", 12: "ItemDamageTwoHand", 17: "ItemDamageTwoHand",
                2: "ItemDamageRanged", 3: "ItemDamageRanged", 18: "ItemDamageRanged",
                16: "ItemDamageThrown", 19: "ItemDamageWand"}
FLAG_CONJURED = 0x2


def fetch(cache: str, table: str, build: str, offline: bool) -> list[dict[str, str]]:
    path = os.path.join(cache, f"{table}.csv")
    if not os.path.isfile(path):
        if offline:
            raise SystemExit(f"missing {path} (offline)")
        url = URL.format(table=table, build=build)
        print("download", table, file=sys.stderr)
        with urllib.request.urlopen(url, timeout=120) as r:  # noqa: S310 - fixed https host
            data = r.read()
        with open(path, "wb") as f:
            f.write(data)
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def i(v: str | None, default: int = 0) -> int:
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        try:
            return int(float(v))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default


def f(v: str | None) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def junk(name: str) -> bool:
    return not name or name.startswith(JUNK_NAME) or "(OLD)" in name or "UNUSED" in name.upper()


# ----------------------------------------------------------------------------------------------- spells
def build_spells(T: dict) -> dict:
    names = {r["ID"]: r["Name_lang"] for r in T["SpellName"]}
    level = {}
    for r in T["SpellLevels"]:
        if r["DifficultyID"] == "0":
            level[r["SpellID"]] = i(r["SpellLevel"]) or i(r["BaseLevel"])
    line_cls = {str(sl): c for c, lines in CLASS_LINES.items() for sl in lines}
    seen: dict[tuple, int] = {}
    per: dict[str, list] = {c: [] for c in CLASS_LINES}
    for r in T["SkillLineAbility"]:
        cls = line_cls.get(r["SkillLine"])
        sid = i(r["Spell"])
        name = names.get(r["Spell"])
        lvl = level.get(r["Spell"], 0)
        if not cls or not name or lvl <= 0 or lvl > 60 or junk(name):
            continue
        if r["AcquireMethod"] == "3" or 400000 <= sid < 1000000:  # Season of Discovery runes: not in Forever
            continue
        key = (cls, name, lvl)
        if key in seen and seen[key] <= sid:
            continue
        seen[key] = sid
    for (cls, name, lvl), sid in seen.items():
        per[cls].append([lvl, sid, name, 0, 0])
    talents = {i(r["Spell"]) for r in T["SkillLineAbility"] if r["ClassMask"] == "0"}
    for cls, rows in per.items():
        rows.sort(key=lambda r: (r[0], r[2], r[1]))
        count: dict[str, int] = {}
        for r in rows:
            count[r[2]] = count.get(r[2], 0) + 1
            r[3] = count[r[2]]
            r[4] = 1 if r[1] in talents else 0
        multi = {n for n, c in count.items() if c > 1}
        for r in rows:
            if r[2] not in multi:
                r[3] = 0
    return {"fields": ["level", "id", "name", "rank", "talent"], "classes": per}


# ------------------------------------------------------------------------------------------------ items
def build_items(T: dict) -> tuple[dict, dict]:
    by = lambda t: {r["ID"]: r for r in T[t]}  # noqa: E731
    item = by("Item")
    rpp = by("RandPropPoints")
    a_total, a_qual, a_shield, a_loc = by("ItemArmorTotal"), by("ItemArmorQuality"), by("ItemArmorShield"), \
        by("ArmorLocation")
    dmg = {t: by(t) for t in set(WEAPON_TABLE.values())}
    spells_of: dict[int, list[int]] = {}
    effects = by("ItemEffect")
    for r in T["ItemXItemEffect"]:
        e = effects.get(r["ItemEffectID"])
        if e:
            spells_of.setdefault(i(r["ItemID"]), []).append(i(e["SpellID"]))
    out = {}
    raw = {}
    for r in T["ItemSparse"]:
        iid = i(r["ID"])
        name = r["Display_lang"]
        if junk(name):
            continue
        it = item.get(r["ID"], {})
        q, ilvl, inv = i(r["OverallQualityID"]), i(r["ItemLevel"]), i(r["InventoryType"])
        cid, sub = i(it.get("ClassID"), -1), i(it.get("SubclassID"), -1)
        stats = {}
        col = SLOT_COL.get(inv)
        p = rpp.get(str(ilvl))
        if col is not None and p:
            tier = "Epic" if q >= 4 else ("Superior" if q == 3 else "Good")
            pts = f(p.get(f"{tier}_{col}"))
            for k in range(10):
                st, alloc = i(r[f"StatModifier_bonusStat_{k}"], -1), i(r[f"StatPercentEditor_{k}"])
                if st >= 0 and alloc:
                    amt = int(pts * alloc / 10000 + 0.5)
                    if amt:
                        stats[str(st)] = stats.get(str(st), 0) + amt
        armor = 0
        if cid == 4 and sub in ARMOR_COL and str(ilvl) in a_total and str(inv) in a_loc:
            tcol, lcol = ARMOR_COL[sub]
            armor = int(f(a_total[str(ilvl)][tcol]) * f(a_loc[str(inv)][lcol])
                        * f(a_qual.get(str(ilvl), {}).get(f"Qualitymod_{q}")) + 0.5)
        elif cid == 4 and sub == 6 and str(ilvl) in a_shield:
            armor = i(a_shield[str(ilvl)].get(f"Quality_{q}"))
        dps, speed = 0.0, round(i(r["ItemDelay"]) / 1000, 2)
        if cid == 2 and sub in WEAPON_TABLE and speed:
            row = dmg[WEAPON_TABLE[sub]].get(str(ilvl))
            if row:
                dps = round(f(row.get(f"Quality_{q}")), 1)
        flags0 = i(r["Flags_0"])
        out[str(iid)] = [name, q, ilvl, i(r["RequiredLevel"]), cid, sub, inv, stats, armor, dps,
                         speed if cid == 2 else 0, i(r["SellPrice"]), i(r["StartQuestID"]),
                         i(r["AllowableClass"], -1)]
        raw[iid] = {"name": name, "q": q, "ilvl": ilvl, "req": i(r["RequiredLevel"]), "cid": cid, "sub": sub,
                    "conj": bool(flags0 & FLAG_CONJURED), "pet": PETFOOD.get(i(it.get("ItemPetFoodID"))),
                    "skill": i(r["RequiredSkill"]), "skillrank": i(r["RequiredSkillRank"]),
                    "buy": i(r["BuyPrice"]), "spells": spells_of.get(iid, [])}
    fields = ["name", "quality", "ilvl", "req_level", "class", "subclass", "inv_type", "stats", "armor",
              "dps", "speed", "sell", "start_quest", "allowable_class"]
    return {"fields": fields, "items": out}, raw


# ------------------------------------------------------------------------------------------ consumables
def spell_values(T: dict) -> dict[int, dict]:
    """spell id -> {heal, mana, regen_hp, regen_mp} (totals) for consumable classification."""
    dur = {r["ID"]: i(r["Duration"]) for r in T["SpellDuration"]}
    sdur = {}
    for r in T["SpellMisc"]:
        if r["DifficultyID"] == "0":
            sdur[i(r["SpellID"])] = dur.get(r["DurationIndex"], 0)
    out: dict[int, dict] = {}
    for r in T["SpellEffect"]:
        if r["DifficultyID"] != "0":
            continue
        sid, eff, aura = i(r["SpellID"]), i(r["Effect"]), i(r["EffectAura"])
        pts = f(r["EffectBasePointsF"])
        d = out.setdefault(sid, {})
        if eff == 10:  # heal
            d["heal"] = d.get("heal", 0) + pts
        elif eff == 30 and i(r["EffectMiscValue_0"]) == 0:  # energize mana
            d["mana"] = d.get("mana", 0) + pts
        elif eff == 6 and aura in (8, 20, 21, 84, 85):  # food (84) / drink (85): pts per 5 s; 8/20/21: per tick
            total = sdur.get(sid, 0)
            period = 5000 if aura in (84, 85) else (i(r["EffectAuraPeriod"]) or 1000)
            ticks = total / period if total > 0 else 1
            k = "regen_mp" if aura in (21, 85) else "regen_hp"
            d[k] = d.get(k, 0) + pts * ticks
    return out


def build_consumables(raw: dict, T: dict) -> tuple[dict, list]:
    sv = spell_values(T)
    names = {i(r["ID"]): r["Name_lang"] for r in T["SpellName"]}
    cats: dict[str, list] = {k: [] for k in ("food", "water", "hpot", "mpot", "bandage", "ammo")}
    petfood = []
    for iid, it in sorted(raw.items()):
        if it["q"] > 3:
            continue
        vals: dict[str, float] = {}
        for s in it["spells"]:
            for k, v in sv.get(s, {}).items():
                vals[k] = vals.get(k, 0) + v
        req = max(1, it["req"])
        snames = {names.get(s, "") for s in it["spells"]}
        if it["pet"] and (it["buy"] > 0 or it["cid"] in (0, 7)):
            petfood.append([iid, it["pet"], max(1, it["ilvl"])])
        if it["cid"] == 0 and it["sub"] == 5:
            tag = "conj" if it["conj"] else (it["pet"] or "")
            if vals.get("regen_hp", 0) > 0 and ("Nourriture" in snames or "Boisson" not in snames):
                cats["food"].append([iid, req, int(vals["regen_hp"]), tag])
            if vals.get("regen_mp", 0) > 0:
                cats["water"].append([iid, req, int(vals["regen_mp"]), "conj" if it["conj"] else ""])
        elif it["cid"] == 0 and it["sub"] == 1 and it["buy"] >= 0:
            if vals.get("heal", 0) > 0 and not vals.get("mana"):
                cats["hpot"].append([iid, req, int(vals["heal"]), ""])
            elif vals.get("mana", 0) > 0 and not vals.get("heal"):
                cats["mpot"].append([iid, req, int(vals["mana"]), ""])
        elif it["cid"] == 0 and it["sub"] == 7 and vals.get("regen_hp", 0) + vals.get("heal", 0) > 0:
            rank = it["skillrank"] if it["skill"] == 129 else 1
            cats["bandage"].append([iid, max(1, rank), int(vals.get("regen_hp", 0) + vals.get("heal", 0)), ""])
        elif it["cid"] == 6 and it["sub"] in (2, 3) and it["buy"] > 0:
            cats["ammo"].append([iid, req, it["ilvl"], "arrow" if it["sub"] == 2 else "bullet"])
    for rows in cats.values():
        rows.sort(key=lambda r: (r[3], r[1], r[2], r[0]))
    petfood.sort(key=lambda r: (r[1], r[2], r[0]))
    return cats, petfood


# ------------------------------------------------------------------------------------------------ zones
def build_zones(T: dict) -> dict:
    z = {}
    for r in T["AreaTable"]:
        name = r["AreaName_lang"]
        if name and not junk(name):
            z[r["ID"]] = [name, i(r["ParentAreaID"]), i(r["ContinentID"])]
    return {"fields": ["name", "parent", "continent"], "zones": z}


def build_taxi(T: dict) -> dict:
    """Flight graph (nodes with world pos / faction, directed edges with spline length in yards and cost in
    copper) and uiMap world bounds (UiMapAssignment Region, verified conversion:
    mapX = (R4 − worldY)/(R4 − R1), mapY = (R3 − worldX)/(R3 − R0))."""
    nodes = {}
    for r in T["TaxiNodes"]:
        name = r["Name_lang"] or ""
        if not name or name.startswith(("zz", "Quest Path")) or "OLD" in name:
            continue
        fl = i(r["Flags"])
        fac = ("A" if fl & 1 else "") + ("H" if fl & 2 else "")
        nodes[r["ID"]] = [name, i(r["ContinentID"]), round(f(r["Pos_0"]), 1), round(f(r["Pos_1"]), 1), fac]
    pts: dict[str, list] = {}
    for r in T["TaxiPathNode"]:
        pts.setdefault(r["PathID"], []).append((i(r["NodeIndex"]), f(r["Loc_0"]), f(r["Loc_1"]), f(r["Loc_2"]),
                                                f(r["Delay"])))
    edges = []
    for r in T["TaxiPath"]:
        a, b = r["FromTaxiNode"], r["ToTaxiNode"]
        if a not in nodes or b not in nodes:
            continue
        p = sorted(pts.get(r["ID"], []))
        length = sum(((p[k + 1][1] - p[k][1]) ** 2 + (p[k + 1][2] - p[k][2]) ** 2 + (p[k + 1][3] - p[k][3]) ** 2)
                     ** 0.5 for k in range(len(p) - 1))
        if not p:
            na, nb = nodes[a], nodes[b]
            length = ((na[2] - nb[2]) ** 2 + (na[3] - nb[3]) ** 2) ** 0.5 * 1.15
        delay = sum(x[4] for x in p)
        edges.append([i(a), i(b), int(round(length)), i(r["Cost"]), int(round(delay))])
    edges.sort()
    names = {r["ID"]: r["Name_lang"] for r in T["UiMap"]}
    types = {r["ID"]: i(r["Type"]) for r in T["UiMap"]}
    maps = {}
    for r in T["UiMapAssignment"]:
        mid = r["UiMapID"]
        if types.get(mid) not in (3, 4, 5) or mid in maps or i(r["OrderIndex"]) != 0:
            continue
        if f(r["UiMin_0"]) != 0 or f(r["UiMin_1"]) != 0 or f(r["UiMax_0"]) != 1 or f(r["UiMax_1"]) != 1:
            continue
        r0, r1, r3, r4 = f(r["Region_0"]), f(r["Region_1"]), f(r["Region_3"]), f(r["Region_4"])
        # world pos at map (0,0) and (1,1): x0 = R3, y0 = R4, x1 = R0, y1 = R1
        maps[mid] = [names.get(mid, ""), i(r["MapID"]), round(r3, 1), round(r4, 1), round(r0, 1), round(r1, 1)]
    return {"nodes_fields": ["name", "continent", "x", "y", "faction"], "nodes": dict(sorted(nodes.items(), key=lambda kv: int(kv[0]))),
            "edges_fields": ["from", "to", "length_yd", "cost_copper", "delay_s"], "edges": edges,
            "maps_fields": ["name", "continent", "x0", "y0", "x1", "y1"], "maps": dict(sorted(maps.items(), key=lambda kv: int(kv[0])))}


# ---------------------------------------------------------------------------------------------- talents
# TalentTab.ClassMask -> class token.
CLASS_MASK = {1: "WARRIOR", 2: "PALADIN", 4: "HUNTER", 8: "ROGUE", 16: "PRIEST", 64: "SHAMAN", 128: "MAGE",
              256: "WARLOCK", 1024: "DRUID"}
# A few talent spells are absent from the 1.60.1 client SpellName/Spell tables (hotfix-only). Fallback FR names
# from the Classic client (same spell ids); flagged "approx" in the output.
TALENT_NAME_FALLBACK = {
    12165: "Spécialisation Arme d'hast", 12300: "Volonté de fer", 12303: "Défi", 13733: "Attaque sournoise améliorée",
    14911: "Prière de soins améliorée", 16041: "Appel de la foudre", 16952: "Fureur primitive",
    16857: "Lucioles (farouche)", 18213: "Drain d'âme amélioré", 17864: "Drain de vie amélioré",
    18220: "Pacte noir", 18748: "Endurance gangrenée", 20234: "Imposition des mains améliorée",
    20911: "Bénédiction du sanctuaire", 19386: "Piqûre de wyverne", 19491: "Piqûre de scorpide améliorée",
}


def _num(v: float) -> str:
    v = abs(v)
    return str(int(round(v))) if abs(v - round(v)) < 1e-6 else f"{v:.1f}".replace(".", ",")


def _dur(ms: int) -> str:
    if ms <= 0:
        return "X s"
    s = ms / 1000
    return f"{_num(s / 60)} min" if s >= 120 and s % 60 == 0 else f"{_num(s)} s"


def resolve_desc(text: str, sid: int, eff: dict, dur: dict, period: dict) -> str:
    """Fill the client tooltip tokens ($s1, $d, $12345s2, $lpoint:points;…) with values; unknown -> 'X'."""
    import re
    if not text:
        return ""

    def val(m):
        ref = int(m.group(1)) if m.group(1) else sid
        k = m.group(2).lower()
        idx = int(m.group(3) or 1) - 1
        if k in ("s", "m", "o"):
            v = eff.get((ref, idx))
            return _num(v) if v is not None else "X"
        if k == "d":
            return _dur(dur.get(ref, 0))
        if k == "t":
            p = period.get((ref, idx), 0)
            return _num(p / 1000) if p else "X"
        return "X"

    t = re.sub(r"\$\?[^\[]*\[([^\]]*)\]\[([^\]]*)\]", r"\2", text)
    t = re.sub(r"\$\{[^}]*\}", "X", t)
    t = re.sub(r"\$[lL]([^:;]*):([^;]*);", r"\2", t)
    t = re.sub(r"\$<[^>]*>", "X", t)
    t = re.sub(r"\$(\d+)?([sSmMoOdDtT])(\d)?", val, t)
    t = re.sub(r"\$\w+", "X", t)
    t = re.sub(r"\s+", " ", t.replace("\r", " ").replace("\n", " ")).strip()
    return t.replace("X X", "X")


def build_talents(T: dict) -> dict:
    """Talent trees (per class: 3 tabs in client order, talents with tier/column/ranks/prereq/FR name/
    max-rank description) + the hand-written levelling builds of treeaicoach/wow/talent_builds.py,
    validated against the trees (tier gating 5 points per row, prerequisites, ≤ 51 points)."""
    names = {i(r["ID"]): r["Name_lang"] for r in T["SpellName"]}
    descs = {i(r["ID"]): r["Description_lang"] for r in T["Spell"]}
    eff: dict = {}
    period: dict = {}
    for r in T["SpellEffect"]:
        if r["DifficultyID"] != "0":
            continue
        key = (i(r["SpellID"]), i(r["EffectIndex"]))
        eff[key] = f(r["EffectBasePointsF"])
        period[key] = i(r["EffectAuraPeriod"])
    dlen = {r["ID"]: i(r["Duration"]) for r in T["SpellDuration"]}
    dur = {i(r["SpellID"]): dlen.get(r["DurationIndex"], 0) for r in T["SpellMisc"] if r["DifficultyID"] == "0"}
    classes: dict = {}
    tabs = sorted(T["TalentTab"], key=lambda r: (i(r["ClassMask"]), i(r["OrderIndex"])))
    for tab in tabs:
        cls = CLASS_MASK.get(i(tab["ClassMask"]))
        if not cls:
            continue
        rows = []
        for r in T["Talent"]:
            if r["TabID"] != tab["ID"]:
                continue
            spells = [i(r[f"SpellRank_{k}"]) for k in range(9) if i(r[f"SpellRank_{k}"])]
            if not spells:
                continue
            name = names.get(spells[0]) or TALENT_NAME_FALLBACK.get(spells[0], "")
            last = spells[-1]
            desc = resolve_desc(descs.get(last) or descs.get(spells[0], ""), last if descs.get(last) else spells[0],
                                eff, dur, period)
            pre = None
            if i(r["PrereqTalent_0"]):
                pre = [i(r["PrereqTalent_0"]), max(1, i(r["PrereqRank_0"]) + 1)]
            t = {"id": i(r["ID"]), "tier": i(r["TierID"]), "col": i(r["ColumnIndex"]), "ranks": len(spells),
                 "name": name or f"Talent {r['ID']}", "spells": spells, "desc": desc[:400]}
            if pre:
                t["prereq"] = pre
            if spells[0] not in names:
                t["approx"] = 1
            rows.append(t)
        rows.sort(key=lambda t: (t["tier"], t["col"]))
        classes.setdefault(cls, {"tabs": []})["tabs"].append(
            {"id": i(tab["ID"]), "name": tab["Name_lang"], "order": i(tab["OrderIndex"]), "talents": rows})
    for c in classes.values():
        c["tabs"].sort(key=lambda t: t["order"])
    builds = [validate_build(b, classes) for b in load_builds()]
    return {"classes": dict(sorted(classes.items())), "builds": builds,
            "source": "Talent/TalentTab/Spell* (client DB2 via wago.tools)"}


def load_builds() -> list:
    sys.path.insert(0, ROOT)
    from treeaicoach.wow import talent_builds  # noqa: PLC0415 - hand-written knowledge, repo-local
    return talent_builds.BUILDS


def validate_build(b: dict, classes: dict) -> dict:
    """Expand a build's (talent id, target rank, why) steps into the point-by-point order and check it is
    legal: talent belongs to the class, rank ≤ max, row gating (5 × tier points already in that tree),
    prerequisite fully learned, ≤ 51 points (levels 10…60). Raises ValueError on a bad build."""
    cls = b["class"]
    where = {}
    for tab in classes[cls]["tabs"]:
        for t in tab["talents"]:
            where[t["id"]] = (tab, t)
    spent = {tab["order"]: 0 for tab in classes[cls]["tabs"]}
    rank: dict[int, int] = {}
    steps, order = [], []
    for tid, target, why in b["steps"]:
        if tid not in where:
            raise ValueError(f"{b['id']}: talent {tid} is not a {cls} talent")
        tab, t = where[tid]
        if target > t["ranks"] or target <= rank.get(tid, 0):
            raise ValueError(f"{b['id']}: bad target rank {target} for {t['name']} (max {t['ranks']})")
        if spent[tab["order"]] < 5 * t["tier"]:
            raise ValueError(f"{b['id']}: {t['name']} needs {5 * t['tier']} points in {tab['name']}")
        if "prereq" in t and rank.get(t["prereq"][0], 0) < t["prereq"][1]:
            raise ValueError(f"{b['id']}: {t['name']} needs its prerequisite first")
        first = len(order)
        for _ in range(target - rank.get(tid, 0)):
            rank[tid] = rank.get(tid, 0) + 1
            spent[tab["order"]] += 1
            order.append(tid)
        steps.append({"talent": tid, "tab": tab["order"] + 1, "tier": t["tier"] + 1, "col": t["col"] + 1,
                      "name": t["name"], "rank": target, "max": t["ranks"], "why": why,
                      "level_from": 10 + first, "level_to": 9 + len(order)})
    if len(order) > 51:
        raise ValueError(f"{b['id']}: {len(order)} points > 51")
    out = {k: v for k, v in b.items() if k != "steps"}
    out["steps"] = steps
    out["points"] = len(order)
    out["split"] = "/".join(str(spent[k]) for k in sorted(spent))
    return out


def write_talents(tal: dict, build: str) -> int:
    size = write_gz(os.path.join(OUT_DIR, "talents.json.gz"), tal)
    os.makedirs(ADDON_DATA, exist_ok=True)
    out = [HEADER.format(build=build),
           "-- Talents : arbres (onglet → { rang, colonne, rangs max, nom }) et parcours de montée validés\n"
           "-- (étapes { onglet, rang, colonne, rang visé, nom, pourquoi }). Source des parcours :\n"
           "-- treeaicoach/wow/talent_builds.py.\n",
           "local T = TreeAI\nif type(T) ~= \"table\" then return end\nT.Data = T.Data or {}\n",
           "T.Data.Talents = {\n  trees = {\n"]
    for cls, c in tal["classes"].items():
        out.append(f"    {cls} = {{\n")
        for tab in c["tabs"]:
            rows = ", ".join("{%d,%d,%d,%s}" % (t["tier"] + 1, t["col"] + 1, t["ranks"], lua_str(t["name"]))
                             for t in tab["talents"])
            out.append(f"      {{ name = {lua_str(tab['name'])}, t = {{ {rows} }} }},\n")
        out.append("    },\n")
    out.append("  },\n  builds = {\n")
    for b in tal["builds"]:
        out.append("    { id = %s, class = %s, role = %s, name = %s, spec = %s, split = %s, summary = %s,\n"
                   % tuple(lua_str(str(b.get(k, ""))) for k in ("id", "class", "role", "name", "spec", "split",
                                                                "summary")))
        out.append("      steps = {\n")
        for s in b["steps"]:
            out.append("        {%d,%d,%d,%d,%s,%s},\n" % (s["tab"], s["tier"], s["col"], s["rank"],
                                                          lua_str(s["name"]), lua_str(s["why"])))
        out.append("      } },\n")
    out.append("  },\n}\n")
    with open(os.path.join(ADDON_DATA, "Talents.lua"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(out))
    return size
# --------------------------------------------------------------------------------------------- recipes
# Tradeskill lines (SkillLine ids): kind c = primary crafting (Forever: Legacy points at 150/225/300),
# g = gathering (primary, no Legacy points), s = secondary.
PROF_LINES = {171: ("c", "Alchimie"), 164: ("c", "Forge"), 333: ("c", "Enchantement"), 202: ("c", "Ingénierie"),
              165: ("c", "Travail du cuir"), 197: ("c", "Couture"), 182: ("g", "Herboristerie"),
              186: ("g", "Minage"), 393: ("g", "Dépeçage"), 185: ("s", "Cuisine"), 129: ("s", "Secourisme"),
              356: ("s", "Pêche")}
PROF_TABLES = ("SkillLineAbility", "SpellName", "SpellEffect", "SpellReagents", "ItemSparse", "Item",
               "ItemEffect", "ItemXItemEffect")
# Reagents sold by ordinary vendors (Classic item ids; thread, vials, flux, dyes, spices, wood…). Unit price =
# ItemSparse BuyPrice / VendorStackCount (vials and spices come in bundles of 5). Ids missing from the build are dropped.
VENDOR_MATS = (159, 1179, 1205, 2320, 2321, 2324, 2325, 2596, 2604, 2605, 2665, 2678, 2692, 2880, 2894, 3371,
               3372, 3466, 3713, 3857, 4289, 4291, 4340, 4341, 4342, 4399, 4400, 4470, 4536, 6217, 6260, 6261,
               6530, 8343, 8925, 10290, 10647, 10648, 11291, 14341, 17194, 17196, 18256, 18567)
# Gathering nodes: item gathered, skill needed (Classic values; Forever keeps the 1-300 scale).
GATHER = {
    182: ((2447, 1), (765, 1), (2449, 15), (785, 50), (2450, 70), (3820, 85), (2453, 100), (3355, 115),
          (3369, 120), (3356, 125), (3357, 150), (3818, 160), (3821, 170), (3358, 185), (3819, 195), (4625, 205),
          (8831, 210), (8836, 220), (8838, 230), (8839, 235), (8845, 245), (8846, 250), (13464, 260),
          (13463, 270), (13465, 280), (13466, 285), (13467, 290), (13468, 300)),
    186: ((2770, 1), (2771, 65), (3340, 65), (2775, 75), (4278, 75), (2772, 125), (5833, 150), (2776, 155),
          (3858, 175), (7911, 230), (11370, 230), (10620, 245)),
}
# Best zones per skill band (FR zone names as the client shows them, accent/apostrophe-insensitive match).
GATHER_ZONES = {
    182: ((1, 70, ("Forêt d'Elwynn", "Dun Morogh", "Teldrassil", "Durotar", "Mulgore", "Clairières de Tirisfal")),
          (70, 125, ("Marche de l'Ouest", "Les Tarides", "Forêt des Pins argentés", "Loch Modan", "Sombrivage")),
          (125, 175, ("Contreforts de Hautebrande", "Les Paluns", "Orneval", "Bois de la Pénombre",
                      "Les Serres-Rocheuses")),
          (175, 230, ("Vallée de Strangleronce", "Hautes-terres Arathies", "Désolace", "Marécage d'Âprefange")),
          (230, 270, ("Feralas", "Les Hinterlands", "Marais des Chagrins", "Tanaris")),
          (270, 301, ("Cratère d'Un'Goro", "Gangrebois", "Maleterres de l'Ouest", "Maleterres de l'Est",
                      "Berceau-de-l'Hiver"))),
    186: ((1, 65, ("Forêt d'Elwynn", "Dun Morogh", "Durotar", "Mulgore", "Clairières de Tirisfal", "Teldrassil")),
          (65, 125, ("Les Carmines", "Marche de l'Ouest", "Les Tarides", "Forêt des Pins argentés", "Loch Modan",
                     "Sombrivage")),
          (125, 175, ("Mille pointes", "Les Paluns", "Hautes-terres Arathies", "Désolace",
                      "Contreforts de Hautebrande")),
          (175, 245, ("Terres ingrates", "Tanaris", "Les Hinterlands", "Vallée de Strangleronce",
                      "Gorge des Vents brûlants")),
          (245, 301, ("Cratère d'Un'Goro", "Gorge des Vents brûlants", "Steppes ardentes", "Berceau-de-l'Hiver",
                      "Silithus"))),
    393: ((1, 75, ("Forêt d'Elwynn", "Dun Morogh", "Durotar", "Mulgore", "Clairières de Tirisfal", "Teldrassil")),
          (75, 150, ("Marche de l'Ouest", "Les Tarides", "Forêt des Pins argentés", "Les Carmines", "Sombrivage")),
          (150, 200, ("Contreforts de Hautebrande", "Les Paluns", "Orneval", "Mille pointes",
                      "Hautes-terres Arathies")),
          (200, 250, ("Vallée de Strangleronce", "Terres ingrates", "Feralas", "Tanaris")),
          (250, 301, ("Cratère d'Un'Goro", "Les Hinterlands", "Gangrebois", "Berceau-de-l'Hiver", "Silithus"))),
}


# PROFLEVEL (4.0.2) — where to skin a leather / hide you lack: beasts of these levels (skinning ≈ 5 × level above
# 20), best zones (FR names as the client shows them; checked against AreaTable by tests/wow/test_prof.py).
FARM = {
    2318: (5, 20, 1, ("Les Tarides", "Forêt des Pins argentés", "Marche de l'Ouest", "Loch Modan", "Sombrivage")),
    783: (5, 20, 1, ("Les Tarides", "Forêt des Pins argentés", "Marche de l'Ouest", "Loch Modan", "Sombrivage")),
    2319: (20, 30, 100, ("Les Serres-Rocheuses", "Orneval", "Contreforts de Hautebrande", "Les Paluns",
                         "Mille pointes")),
    4232: (20, 30, 100, ("Les Serres-Rocheuses", "Orneval", "Contreforts de Hautebrande", "Les Paluns",
                         "Mille pointes")),
    4234: (30, 40, 150, ("Mille pointes", "Hautes-terres Arathies", "Désolace", "Vallée de Strangleronce",
                         "Marécage d'Âprefange")),
    4235: (30, 40, 150, ("Mille pointes", "Hautes-terres Arathies", "Désolace", "Vallée de Strangleronce",
                         "Marécage d'Âprefange")),
    4304: (40, 50, 200, ("Feralas", "Tanaris", "Les Hinterlands", "Marais des Chagrins")),
    8169: (40, 50, 200, ("Feralas", "Tanaris", "Les Hinterlands", "Marais des Chagrins")),
    8170: (50, 60, 250, ("Cratère d'Un'Goro", "Berceau-de-l'Hiver", "Gangrebois", "Silithus")),
    8171: (50, 60, 250, ("Cratère d'Un'Goro", "Berceau-de-l'Hiver", "Gangrebois", "Silithus")),
}
# recipe row flags (index 10): 1 = learned with the profession (AcquireMethod 1), 2 = learn skill ESTIMATED (trainer
# recipe: the client tables carry MinSkillLineRank = 1, the real requirement is trainer data on the server; TreeAI
# plans it from its yellow threshold — never too early — and learns the real value at the trainer)
FLAG_AUTO, FLAG_EST = 1, 2


def build_recipes(T: dict) -> dict:
    """Tradeskill recipes per profession line: [spell, item, count, learn, yellow, grey, skillups, recipeItem,
    name, [reagent, n, …], flags] + reagent table {id: [name, unitBuyPrice, vendor 0/1, sellPrice, vendorBundle]}
    + gathering nodes + leather farming bands.
    Colours (Classic): orange < yellow (TrivialSkillLineRankLow) ≤ yellow < (yellow+grey)/2 ≤ green < grey
    (TrivialSkillLineRankHigh). learn = skill to learn it: 1 for recipes learned with the profession, the recipe
    item's RequiredSkillRank for patterns, else MinSkillLineRank when the client has it, else the yellow threshold
    (estimate, flag 2: Forever's tables hold no trainer requirement)."""
    names = {r["ID"]: r["Name_lang"] for r in T["SpellName"]}
    create: dict[str, tuple[int, int]] = {}
    for r in T["SpellEffect"]:
        if r["Effect"] == "24" and r["DifficultyID"] == "0" and r["SpellID"] not in create:
            create[r["SpellID"]] = (i(r["EffectItemType"]), max(1, i(r["EffectBasePointsF"])))
    reag: dict[str, list[int]] = {}
    for r in T["SpellReagents"]:
        flat = []
        for k in range(8):
            rid, n = i(r[f"Reagent_{k}"]), i(r[f"ReagentCount_{k}"])
            if rid > 0 and n > 0:
                flat += [rid, n]
        if flat:
            reag[r["SpellID"]] = flat
    sparse = {r["ID"]: r for r in T["ItemSparse"]}
    iclass = {r["ID"]: i(r["ClassID"], -1) for r in T["Item"]}
    effects = {r["ID"]: r for r in T["ItemEffect"]}
    teach: dict[str, int] = {}
    for r in T["ItemXItemEffect"]:
        e = effects.get(r["ItemEffectID"])
        if not e or iclass.get(r["ItemID"]) != 9:
            continue
        sp = sparse.get(r["ItemID"])
        if not sp or junk(sp["Display_lang"]):
            continue
        cur = teach.get(e["SpellID"])
        if cur is None or i(r["ItemID"]) < cur:
            teach[e["SpellID"]] = i(r["ItemID"])
    per: dict[int, list] = {line: [] for line in PROF_LINES}
    seen: set[str] = set()
    mats: set[int] = set()
    for r in T["SkillLineAbility"]:
        line, s = i(r["SkillLine"]), r["Spell"]
        if line not in PROF_LINES or s in seen:
            continue
        sid = i(s)
        if r["AcquireMethod"] == "3" or 400000 <= sid < 1000000:  # Season of Discovery: not in Forever
            continue
        name = names.get(s) or ""
        if junk(name) or (s not in create and s not in reag):
            continue
        seen.add(s)
        item, count = create.get(s, (0, 1))
        flat = reag.get(s, [])
        mats.update(flat[0::2])  # crafted items are named by the recipe itself (only reagents are listed)
        yellow, grey = i(r["TrivialSkillLineRankLow"]), i(r["TrivialSkillLineRankHigh"])
        recipe_item = teach.get(s, 0)
        req = i(sparse[str(recipe_item)].get("RequiredSkillRank")) if recipe_item and str(recipe_item) in sparse else 0
        flags = 0
        if r["AcquireMethod"] == "1":
            learn, flags = 1, FLAG_AUTO
        elif i(r["MinSkillLineRank"]) > 1:
            learn = i(r["MinSkillLineRank"])
            # (5.0 PROF audit) a table requirement ABOVE the yellow threshold is a leftover (the recipe would be grey
            # or green the day it is learned: « Pierre à aiguiser grossière » 75 for a grey at 55): estimate instead
            if yellow > 0 and learn > yellow:
                learn, flags = max(1, yellow), FLAG_EST
        elif req > 0:
            learn = req
        else:
            learn, flags = max(1, yellow), FLAG_EST
        per[line].append([sid, item, count, learn, yellow, grey, max(1, i(r["NumSkillUps"])), recipe_item, name,
                          flat, flags])
    for rows in per.values():
        rows.sort(key=lambda x: (x[3], x[4], x[0]))
    vendor = set(VENDOR_MATS)
    gather = {}
    for line, nodes in GATHER.items():
        gather[line] = [[iid, skill] for iid, skill in nodes if str(iid) in sparse]
        mats.update(iid for iid, _ in nodes)
    mats.update(FARM)
    mat_tab = {}
    for m in sorted(mats):
        sp = sparse.get(str(m))
        if not sp:
            continue
        bundle = max(1, i(sp.get("VendorStackCount"), 1))
        unit = -(-i(sp["BuyPrice"]) // bundle)  # price of ONE item (rounded up)
        mat_tab[m] = [sp["Display_lang"], unit, 1 if m in vendor else 0, i(sp["SellPrice"]), bundle if m in vendor else 1]
    farm = {iid: [lo, hi, skill, list(z)] for iid, (lo, hi, skill, z) in FARM.items() if str(iid) in sparse}
    return {"lines": {line: [k, n] for line, (k, n) in PROF_LINES.items()}, "recipes": per, "mats": mat_tab,
            "gather": gather, "zones": {line: [[a, b, list(z)] for a, b, z in bands]
                                        for line, bands in GATHER_ZONES.items()}, "farm": farm}


def _lua_val(v) -> str:
    if isinstance(v, str):
        return lua_str(v)
    if isinstance(v, (list, tuple)):
        return "{" + ",".join(_lua_val(x) for x in v) + "}"
    return str(v)


def write_recipes_lua(build: str, rec: dict, path: str | None = None) -> int:
    """Compact Lua table T.Data.Recipes (≤ 300 KB). Returns the byte size."""
    out = [HEADER.format(build=build),
           "-- Métiers : recettes { sort, objet créé, quantité, compétence pour l'apprendre, jaune, gris, points,\n"
           "-- objet-recette (0 = entraîneur/auto), nom FR, { compo, n, … }, drapeaux } ; vert = (jaune + gris) / 2 ;\n"
           "-- drapeaux : 1 = appris avec le métier, 2 = compétence pour l'apprendre estimée (= jaune : le client n'a\n"
           "-- pas l'exigence de l'entraîneur ; la vraie est lue chez lui). mats[id] = { nom, prix d'achat À L'UNITÉ,\n"
           "-- vendu par un marchand (1/0), prix de revente, lot du marchand } ; gather = nœuds { objet, compétence } ;\n"
           "-- zones = { de, à, { zones conseillées } } ; farm[cuir] = { niv. des bêtes de, à, dépeçage, { zones } }.\n",
           "local T = TreeAI\nif type(T) ~= \"table\" then return end\nT.Data = T.Data or {}\n",
           f"T.Data.Recipes = {{\n  build = {lua_str(build)},\n  lines = {{\n"]
    for line, (k, n) in sorted(rec["lines"].items()):
        out.append(f"    [{line}] = {{{lua_str(k)},{lua_str(n)}}},\n")
    out.append("  },\n  r = {\n")
    for line, rows in sorted(rec["recipes"].items()):
        out.append(f"    [{line}] = {{\n")
        out.extend("{" + ",".join(_lua_val(v) for v in row) + "},\n" for row in rows)
        out.append("    },\n")
    out.append("  },\n  mats = {\n")
    out.extend(f"[{m}]={_lua_val(v)},\n" for m, v in sorted(rec["mats"].items()))
    out.append("  },\n  gather = {\n")
    for line, rows in sorted(rec["gather"].items()):
        out.append(f"    [{line}] = {_lua_val(rows)},\n")
    out.append("  },\n  zones = {\n")
    for line, bands in sorted(rec["zones"].items()):
        out.append(f"    [{line}] = {_lua_val(bands)},\n")
    out.append("  },\n  farm = {\n")
    for iid, v in sorted((rec.get("farm") or {}).items()):
        out.append(f"    [{iid}] = {_lua_val(v)},\n")
    out.append("  },\n}\n")
    data = "".join(out).encode("utf-8")
    path = path or os.path.join(ADDON_DATA, "Recipes.lua")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return len(data)


def write_recipes_files(build: str, rec: dict) -> dict:
    lua = write_recipes_lua(build, rec)
    gz = write_gz(os.path.join(OUT_DIR, "recipes.json.gz"), {"build": build, **rec})
    return {"recipes_lua": lua, "recipes_gz": gz, "recipes": sum(len(v) for v in rec["recipes"].values()),
            "mats": len(rec["mats"])}


# ----------------------------------------------------------------------------------------------- output
def write_gz(path: str, obj) -> int:
    data = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0, compresslevel=9) as g:
        g.write(data)
    with open(path, "wb") as fh:
        fh.write(buf.getvalue())
    return len(buf.getvalue())


def lua_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def lua_rows(rows: list) -> str:
    return "\n".join("    {" + ", ".join(lua_str(v) if isinstance(v, str) else str(v) for v in r) + "},"
                     for r in rows)


HEADER = ("-- GÉNÉRÉ par tools/wow_build_data.py — ne pas éditer à la main.\n"
          "-- Source : tables client Blizzard (WoW Forever, build {build}) via wago.tools.\n")


def write_lua(build: str, cats: dict, petfood: list) -> None:
    os.makedirs(ADDON_DATA, exist_ok=True)
    pf = (HEADER.format(build=build)
          + "-- Nourriture de familier : { itemID, régime, niveau d'objet } (régime : meat fish bread cheese"
            " fruit fungus).\n"
          + "local T = TreeAI\nif type(T) ~= \"table\" then return end\nT.Data = T.Data or {}\n"
          + "T.Data.PetFood = {\n" + lua_rows(petfood).replace("    {", "  {") + "\n}\n")
    with open(os.path.join(ADDON_DATA, "PetFood.lua"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(pf)
    body = []
    for k in ("food", "water", "hpot", "mpot", "bandage", "ammo"):
        body.append(f"  {k} = {{\n{lua_rows(cats[k])}\n  }},")
    cs = (HEADER.format(build=build)
          + "-- Consommables : { itemID, niveau requis (bandage : secourisme), valeur (PV/mana rendus ;"
            " munitions : niveau d'objet), étiquette }.\n"
          + "local T = TreeAI\nif type(T) ~= \"table\" then return end\nT.Data = T.Data or {}\n"
          + "T.Data.Consumables = {\n" + "\n".join(body) + "\n}\n")
    with open(os.path.join(ADDON_DATA, "Consumables.lua"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(cs)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache", default=os.path.join(tempfile.gettempdir(), "treeai_wowf_db2"))
    ap.add_argument("--build", default=BUILD)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--only", choices=("taxi", "talents", "recipes"), help="build only this file")
    a = ap.parse_args(argv)
    os.makedirs(a.cache, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    if a.only == "recipes":
        rec = build_recipes({t: fetch(a.cache, t, a.build, a.offline) for t in PROF_TABLES})
        print(json.dumps(write_recipes_files(a.build, rec)))
        return 0
    tal = build_talents({t: fetch(a.cache, t, a.build, a.offline) for t in TALENT_TABLES})
    tal["build"] = a.build
    talsize = write_talents(tal, a.build)
    if a.only == "talents":
        print(json.dumps({"talents": talsize, "classes": {c: sum(len(t["talents"]) for t in v["tabs"])
                                                          for c, v in tal["classes"].items()},
                          "builds": len(tal["builds"])}))
        return 0
    taxi = build_taxi({t: fetch(a.cache, t, a.build, a.offline) for t in TAXI_TABLES})
    taxi["build"] = a.build
    tsize = write_gz(os.path.join(OUT_DIR, "taxi.json.gz"), taxi)
    if a.only == "taxi":
        print(json.dumps({"taxi": tsize, "nodes": len(taxi["nodes"]), "edges": len(taxi["edges"]),
                          "maps": len(taxi["maps"])}))
        return 0
    T = {t: fetch(a.cache, t, a.build, a.offline) for t in TABLES}
    os.makedirs(OUT_DIR, exist_ok=True)
    spells = build_spells(T)
    items, raw = build_items(T)
    zones = build_zones(T)
    cats, petfood = build_consumables(raw, T)
    sizes = {
        "taxi": tsize, "talents": talsize,
        "spells": write_gz(os.path.join(OUT_DIR, "spells.json.gz"), spells),
        "items": write_gz(os.path.join(OUT_DIR, "items.json.gz"), items),
        "zones": write_gz(os.path.join(OUT_DIR, "zones.json.gz"), zones),
    }
    meta = {"source": "Blizzard client DB2 tables via wago.tools (product wowf)", "build": a.build,
            "locale": "frFR", "counts": {"items": len(items["items"]), "zones": len(zones["zones"]),
                                         "spells": {c: len(v) for c, v in spells["classes"].items()},
                                         "petfood": len(petfood),
                                         "consumables": {k: len(v) for k, v in cats.items()}}}
    sizes["meta"] = write_gz(os.path.join(OUT_DIR, "meta.json.gz"), meta)
    write_lua(a.build, cats, petfood)
    sizes.update(write_recipes_files(a.build, build_recipes(T)))
    print(json.dumps({"sizes": sizes, **meta["counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
