"""Build the gear-planner data (agent GEAR) from the WoW Forever client tables.

Inputs: ``treeaicoach/wow/data/items.json.gz`` (already built by ``tools/wow_build_data.py``) + three client
DB2 tables (ItemSparse: buy price / binding; SkillLineAbility + SpellEffect: crafted items and the skill
needed), build 1.60.1.70862, frFR, via wago.tools (same source as ``wow_build_data.py``).

Outputs:
  wow_addon/TreeAI/Data/Gear.lua        equippable candidates (quality ≥ 2, weapons ≥ 1) + crafted map
  treeaicoach/wow/data/gear.json.gz     same crafted map + buy price + binding for the Python side

The client has **no** JournalEncounter/JournalEncounterItem tables in this build (wago: « Table not found »),
so boss / vendor / quest sources are resolved at runtime from QuestieDB (read-only) by Modules/Gear.lua.
Usage: python tools/wow_build_gear.py [--cache DIR] [--offline]
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wow_build_data as base  # noqa: E402  (fetch/i helpers, BUILD, paths)

TABLES = ("ItemSparse", "SkillLineAbility", "SpellEffect")
PROFS = {164: "Forge", 165: "Travail du cuir", 197: "Couture", 202: "Ingénierie", 171: "Alchimie",
         333: "Enchantement", 185: "Cuisine", 129: "Secourisme"}
CREATE_ITEM = 24
SKIP_INV = (0, 4, 18, 19, 24)      # non-equipped, shirt, bag, tabard, ammo


def crafted_map(T: dict) -> dict[int, list[int]]:
    """item id -> [profession skill line, min skill] (lowest skill when several recipes)."""
    spell_line: dict[int, tuple[int, int]] = {}
    for r in T["SkillLineAbility"]:
        line = base.i(r["SkillLine"])
        if line in PROFS:
            sp = base.i(r["Spell"])
            # MinSkillLineRank is 1 for almost every recipe in this build: approximate the skill needed as
            # « yellow threshold − 15 » (TrivialSkillLineRankLow), never below the stated minimum.
            rank = max(base.i(r["MinSkillLineRank"]), base.i(r["TrivialSkillLineRankLow"]) - 15, 1)
            spell_line[sp] = (line, rank)
    out: dict[int, list[int]] = {}
    for r in T["SpellEffect"]:
        if base.i(r["Effect"]) != CREATE_ITEM:
            continue
        sp = spell_line.get(base.i(r["SpellID"]))
        item = base.i(r["EffectItemType"])
        if sp and item:
            if item not in out or sp[1] < out[item][1]:
                out[item] = [sp[0], sp[1]]
    return out


def candidates(items: dict) -> dict[int, list]:
    F = items["fields"]
    out = {}
    for k, row in items["items"].items():
        x = dict(zip(F, row))
        if x["class"] not in (2, 4) or x["inv_type"] in SKIP_INV or x["req_level"] > 60:
            continue
        minq = 1 if x["class"] == 2 else 2
        if x["quality"] < minq or x["quality"] > 4:
            continue
        if not (x["stats"] or x["dps"] or x["armor"]):
            continue
        out[int(k)] = x
    return out


def lua_s(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def write(build: str, cand: dict, crafted: dict, sparse: dict, lua_path: str, gz_path: str) -> dict:
    rows = []
    for iid in sorted(cand):
        x = cand[iid]
        sp = sparse.get(iid, {})
        stats = " ".join(f"{k}:{v}" for k, v in sorted(x["stats"].items(), key=lambda kv: int(kv[0])))
        mask = x["allowable_class"] if x["allowable_class"] not in (-1, 0) else 0
        dps = ("%g" % x["dps"]) if x["dps"] else "0"
        rows.append(f"[{iid}]={{{x['inv_type']},{x['req_level']},{x['quality']},{x['class']},{x['subclass']},"
                    f"{lua_s(stats)},{dps},{'%g' % (x['speed'] or 0)},{x['armor'] or 0},{sp.get('bind', 0)},"
                    f"{sp.get('buy', 0)},{lua_s(x['name'])},{mask}}},")
    cr = [f"[{iid}]={{{v[0]},{v[1]}}}," for iid, v in sorted(crafted.items()) if iid in cand]
    profs = ",".join(f"[{k}]={lua_s(v)}" for k, v in sorted(PROFS.items()))
    text = (
        "-- TreeAI — données du planificateur d'équipement (GÉNÉRÉ par tools/wow_build_gear.py, ne pas éditer).\n"
        f"-- Source : tables client Blizzard (WoW Forever, build {build}) via wago.tools.\n"
        "-- items[id] = { inventoryType, niveau requis, qualité, classID, subclassID, \"stat:val …\", DPS, vitesse,\n"
        "--               armure, liaison (1 lié quand ramassé, 2 quand équipé), prix d'achat (pc), nom FR, masque classes }\n"
        "-- crafted[id] = { métier (SkillLine), compétence minimale }\n"
        "local T = TreeAI\nif type(T) ~= \"table\" then return end\nT.Data = T.Data or {}\n"
        f"T.Data.Gear = {{ build = {lua_s(build)}, profs = {{{profs}}},\n"
        "items = {\n" + "\n".join(rows) + "\n},\ncrafted = {\n" + "\n".join(cr) + "\n} }\n")
    with open(lua_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    gz = {"build": build, "profs": {str(k): v for k, v in PROFS.items()},
          "crafted": {str(k): v for k, v in sorted(crafted.items())},
          "buy": {str(k): sparse[k]["buy"] for k in sorted(cand) if sparse.get(k, {}).get("buy")},
          "bind": {str(k): sparse[k]["bind"] for k in sorted(cand) if sparse.get(k, {}).get("bind")}}
    raw = json.dumps(gz, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with gzip.GzipFile(gz_path, "wb", mtime=0) as fh:
        fh.write(raw)
    return {"candidates": len(rows), "crafted": len(cr), "lua_bytes": len(text.encode("utf-8")),
            "gz_bytes": os.path.getsize(gz_path)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache", default=os.path.join(tempfile.gettempdir(), "treeai_wowf_db2"))
    ap.add_argument("--build", default=base.BUILD)
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args(argv)
    os.makedirs(a.cache, exist_ok=True)
    T = {t: base.fetch(a.cache, t, a.build, a.offline) for t in TABLES}
    with gzip.open(os.path.join(base.OUT_DIR, "items.json.gz"), "rt", encoding="utf-8") as fh:
        items = json.load(fh)
    sparse = {base.i(r["ID"]): {"buy": base.i(r["BuyPrice"]), "bind": base.i(r["Bonding"])}
              for r in T["ItemSparse"]}
    res = write(a.build, candidates(items), crafted_map(T), sparse,
                os.path.join(base.ADDON_DATA, "Gear.lua"), os.path.join(base.OUT_DIR, "gear.json.gz"))
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
