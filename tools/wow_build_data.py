#!/usr/bin/env python3
"""Build TreeAI's WoW Forever game data from the official client tables (offline, deterministic).

Source: Blizzard client DB2 tables as served by wago.tools (product ``wowf``), build ``BUILD`` below,
French locale. Output:
  treeaicoach/wow/data/{spells,items,zones,meta}.json.gz   (read by treeaicoach/wow/gamedata.py)
  treeaicoach/wow/data/taxi.json.gz   (flight graph + map bounds, read by treeaicoach/wow/planner/data.py)
  wow_addon/TreeAI/Data/PetFood.lua, Data/Consumables.lua  (small Lua tables for the addon)

Usage:  python tools/wow_build_data.py [--cache DIR] [--build 1.60.1.70862] [--offline] [--only taxi]
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
          "ItemDamageTwoHand", "ItemDamageRanged", "ItemDamageThrown", "ItemDamageWand", "AreaTable")
TAXI_TABLES = ("TaxiNodes", "TaxiPath", "TaxiPathNode", "UiMapAssignment", "UiMap")

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
    ap.add_argument("--only", choices=("taxi",), help="build only this file")
    a = ap.parse_args(argv)
    os.makedirs(a.cache, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)
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
        "taxi": tsize,
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
    print(json.dumps({"sizes": sizes, **meta["counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
