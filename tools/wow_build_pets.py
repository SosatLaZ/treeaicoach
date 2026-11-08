#!/usr/bin/env python3
"""Build TreeAI's hunter pet table (« Meilleur familier maintenant », Modules/Finder.lua) — offline, deterministic.

Two sources, kept apart on purpose:

1. **Client facts** (reliable) — WoW Forever client DB2 tables via wago.tools (product ``wowf``, build ``BUILD``,
   frFR), the same fetch/cache as tools/wow_build_data.py:
     * ``CreatureFamily``   : tameable families (``SkillLine_1 == 270`` = hunter pet line, food mask > 0), FR name,
                              diet mask (1 viande, 2 poisson, 4 fromage, 8 pain, 16 champignon, 32 fruit), icon.
     * ``SkillLineAbility`` + ``SpellName`` + ``SpellLevels`` : what each family can learn (Morsure, Griffe,
                              Hurlement furieux…) and the level of every rank.
     * ``SpellEffect``      : the family passive (« Tamed Pet Passive ») = the era's family modifiers — aura 79
                              (dégâts %), 142 (armure %), 133 (points de vie %).
2. **Hand-curated beasts** (``BEASTS`` below) — well-known Classic tameable beasts: NPC id, FR name, family, level
   range, zone. The client tables do not list world creatures, so these are Classic knowledge: levels, the rank a
   beast teaches and the attack speed are APPROXIMATE (flag ``approx``) and the addon re-checks every NPC against
   QuestieDB at run time (levels + spawns, read-only); beasts the player targets in game are learned on top.

Output: ``treeaicoach/wow/data/pets.json.gz`` (facts + beasts) and ``wow_addon/TreeAI/Data/Pets.lua``.
Usage:  python tools/wow_build_pets.py [--cache DIR] [--offline]   (re-render only: ``--render``)
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_JSON = os.path.join(ROOT, "treeaicoach", "wow", "data", "pets.json.gz")
OUT_LUA = os.path.join(ROOT, "wow_addon", "TreeAI", "Data", "Pets.lua")
BUILD = "1.60.1.70862"
TABLES = ("CreatureFamily", "SkillLineAbility", "SpellName", "SpellLevels", "SpellEffect")
PET_LINE = "270"
AURA = {"79": "dmg", "142": "armor", "133": "hp"}

# --------------------------------------------------------------------------------------------- curated (APPROX)
# zones : uiMapID, FR name, faction ("A" / "H" = enemy guards for the other side, None = contested)
ZONES = {
    1429: ("Forêt d'Elwynn", "A"), 1436: ("Marche de l'Ouest", "A"), 1433: ("Les Carmines", "A"),
    1431: ("Bois de la Pénombre", "A"), 1432: ("Loch Modan", "A"), 1439: ("Sombrivage", "A"),
    1437: ("Les Paluns", None), 1424: ("Contreforts de Hautebrande", None), 1416: ("Montagnes d'Alterac", None),
    1417: ("Hautes-terres d'Arathi", None), 1434: ("Vallée de Strangleronce", None), 1418: ("Terres ingrates", None),
    1440: ("Orneval", None), 1441: ("Mille pointes", None), 1442: ("Les Serres-Rocheuses", None),
    1443: ("Désolace", None), 1421: ("Forêt des Pins-Argentés", "H"), 1413: ("Les Tarides", "H"),
}
# npc, FR name, family id, lo, hi, uiMapID, x, y (zone %, rough centre used only without QuestieDB),
# teach (ability FR, rank) or None = probable rank from the level, attack speed s (None = 2.0 assumed), flags
BEASTS = [
    (1766, "Worg tacheté", 1, 13, 14, 1421, 50, 40, None, None, ""),
    (2071, "Rôdeur sélénien", 2, 14, 15, 1439, 45, 30, ("Griffe", 2), None, ""),
    (1109, "Écorcheur", 7, 15, 16, 1436, 50, 50, None, None, ""),
    (442, "Tarentule", 3, 16, 17, 1433, 30, 50, None, None, ""),
    (547, "Grand sanglier", 5, 18, 19, 1433, 55, 60, ("Charge", 2), None, ""),
    (428, "Condor redoutable", 7, 18, 19, 1433, 40, 40, None, None, ""),
    (213, "Loup redoutable affamé", 1, 19, 20, 1431, 55, 60, None, None, ""),
    (3824, "Coureur Pattes-fantômes", 1, 19, 20, 1440, 60, 40, None, None, ""),
    (2350, "Rampant des mousses forestier", 3, 20, 21, 1424, 40, 30, None, None, ""),
    (2351, "Ours gris", 4, 21, 22, 1424, 45, 35, None, None, ""),
    (3809, "Ours d'Orneval", 4, 21, 22, 1440, 40, 50, None, None, ""),
    (1015, "Raptor tacheté", 11, 21, 22, 1437, 40, 60, None, None, ""),
    (1417, "Jeune crocilisque des Paluns", 6, 21, 22, 1437, 30, 45, None, None, ""),
    (521, "Lupos", 1, 23, 23, 1431, 70, 50, ("Morsure", 3), None, "rare"),
    (5828, "Humar le Fier", 2, 23, 23, 1413, 45, 70, ("Griffe", 3), None, "rare"),
    (2384, "Lion des montagnes affamé", 2, 23, 24, 1424, 50, 30, None, None, ""),
    (628, "Ravageur noir", 1, 24, 25, 1431, 65, 70, ("Morsure", 4), None, ""),
    (2354, "Ours gris vicieux", 4, 24, 25, 1424, 40, 25, None, None, ""),
    (2349, "Rampant des mousses géant", 3, 24, 25, 1424, 35, 20, None, None, ""),
    (1021, "Griffefaux tacheté", 11, 24, 25, 1437, 45, 55, ("Griffe", 4), None, ""),
    (1258, "Molosse ravageur noir", 1, 25, 26, 1431, 70, 70, ("Hurlement furieux", 2), None, ""),
    (2406, "Lion des montagnes", 2, 25, 26, 1416, 45, 55, ("Griffe", 4), None, ""),
    (4140, "Saccageur scorpide", 20, 26, 27, 1441, 50, 50, None, None, ""),
    (2385, "Lion des montagnes sauvage", 2, 27, 28, 1424, 55, 25, ("Griffe", 4), None, ""),
    (2356, "Ours gris ancien", 4, 27, 28, 1416, 50, 60, None, None, ""),
    (3825, "Alpha Pattes-fantômes", 1, 27, 28, 1440, 65, 40, ("Hurlement furieux", 2), None, ""),
    (4118, "Serpent des nuages venimeux", 27, 27, 28, 1441, 30, 35, ("Souffle de foudre", 3), None, ""),
    (2408, "Mâchoire-claquante", 21, 30, 31, 1424, 30, 50, None, None, ""),
    (681, "Jeune tigre de Strangleronce", 2, 30, 31, 1434, 40, 15, None, None, ""),
    (2559, "Fouette-queue des hautes-terres", 11, 30, 31, 1417, 40, 60, None, None, ""),
    (682, "Tigre de Strangleronce", 2, 32, 33, 1434, 40, 20, ("Griffe", 5), None, ""),
    (2407, "Lion des montagnes massif", 2, 32, 33, 1416, 55, 55, ("Griffe", 5), None, ""),
    (2560, "Ravageur des hautes-terres", 11, 33, 34, 1417, 50, 70, None, None, ""),
    (1085, "Tigre de Strangleronce ancien", 2, 34, 35, 1434, 35, 25, None, None, ""),
    (2850, "Brisedent", 2, 37, 37, 1418, 60, 40, ("Griffe", 5), 1.0, "rare"),
]


# --------------------------------------------------------------------------------------------- client facts
def _i(v, d: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return d


def extract(T: dict) -> dict:
    """Client tables (lists of CSV row dicts) → {families: {id: {...}}, abilities: {name: {...}}}. Pure."""
    fams, by_line = {}, {}
    for r in T["CreatureFamily"]:
        if r.get("SkillLine_1") != PET_LINE or _i(r.get("PetFoodMask")) <= 0:
            continue
        fid = _i(r["ID"])
        fams[fid] = {"n": r["Name_lang"], "diet": _i(r["PetFoodMask"]), "icon": _i(r.get("IconFileID")),
                     "dmg": 0, "armor": 0, "hp": 0, "learn": []}
        by_line[r["SkillLine_0"]] = fid
    names = {r["ID"]: r["Name_lang"] for r in T["SpellName"]}
    levels = {}
    for r in T["SpellLevels"]:
        lv = max(_i(r.get("SpellLevel")), _i(r.get("BaseLevel")))
        levels[r["SpellID"]] = max(levels.get(r["SpellID"], 0), lv)
    owners: dict[str, list[int]] = {}
    for r in T["SkillLineAbility"]:
        fid = by_line.get(r["SkillLine"])
        if fid is not None:
            owners.setdefault(r["Spell"], []).append(fid)
    abil: dict[str, dict] = {}
    passive: dict[str, int] = {}
    for sid, fids in owners.items():
        nm = names.get(sid) or ""
        if "Passive" in nm:
            if len(set(fids)) == 1:
                passive[sid] = fids[0]
            continue
        if not nm or "(DND)" in nm or nm.startswith("Évolution"):
            continue
        a = abil.setdefault(nm, {"ranks": {}, "fam": set()})
        a["ranks"][_i(sid)] = levels.get(sid, 0)
        a["fam"].update(fids)
    for r in T["SpellEffect"]:
        fid = passive.get(r.get("SpellID"))
        key = AURA.get(r.get("EffectAura"))
        if fid is not None and key and r.get("Effect") == "6":
            fams[fid][key] = _i(r.get("EffectBasePointsF"))
    out_ab = {}
    for nm in sorted(abil):
        a = abil[nm]
        ranks = sorted(a["ranks"].items(), key=lambda kv: (kv[1], kv[0]))
        out_ab[nm] = {"ids": [s for s, _ in ranks], "lv": [lv for _, lv in ranks], "fam": sorted(a["fam"])}
        for fid in a["fam"]:
            fams[fid]["learn"].append(nm)
    for f in fams.values():
        f["learn"].sort()
    return {"families": fams, "abilities": out_ab}


def probable_rank(abilities: dict, ability: str, level: int) -> int:
    lv = abilities.get(ability, {}).get("lv", [])
    return sum(1 for x in lv if 0 < x <= level) or (1 if lv and lv[0] <= level else 0)


def beasts(facts: dict) -> list[dict]:
    """Curated beasts, checked against the client facts (family exists, taught ability belongs to it)."""
    fams, ab = facts["families"], facts["abilities"]
    out = []
    for npc, name, fam, lo, hi, mp, x, y, teach, atk, flags in BEASTS:
        if fam not in fams or mp not in ZONES:
            raise ValueError(f"beast {npc}: unknown family {fam} or zone {mp}")
        if teach and fam not in ab.get(teach[0], {}).get("fam", []):
            raise ValueError(f"beast {npc}: {fams[fam]['n']} cannot learn {teach[0]}")
        out.append({"npc": npc, "n": name, "fam": fam, "lo": lo, "hi": hi, "map": mp, "x": x, "y": y,
                    "zone": ZONES[mp][0], "fz": ZONES[mp][1] or "", "teach": list(teach) if teach else None,
                    "atk": atk, "rare": "rare" in flags})
    return out


def build(T: dict) -> dict:
    facts = extract(T)
    facts["beasts"] = beasts(facts)
    facts["build"] = BUILD
    return facts


# --------------------------------------------------------------------------------------------- outputs
def _s(v: str) -> str:
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_lua(d: dict) -> str:
    fams = {int(k): v for k, v in d["families"].items()}
    L = ["-- GÉNÉRÉ par tools/wow_build_pets.py — ne pas éditer à la main.",
         f"-- Familles / techniques / modificateurs : tables client WoW Forever (build {d['build']}, frFR, wago.tools) —",
         "-- fiables. Bêtes : liste Classic écrite à la main — niveaux, rang enseigné et vitesse d'attaque APPROXIMATIFS",
         "-- (Finder les revérifie dans QuestieDB en jeu : niveaux + apparitions ; les bêtes ciblées en jeu s'ajoutent).",
         "-- families[id] = { n, diet (1 viande 2 poisson 4 fromage 8 pain 16 champignon 32 fruit), dmg, armor, hp (%), icon,",
         "--   learn = { techniques } } · abilities[nom] = { ids = { sort par rang }, lv = { niveau par rang } }",
         "-- beasts = { { npc, n, fam, lo, hi, map, x, y, zone, fz (\"A\"/\"H\"/\"\" disputée), teach = { nom, rang }|nil,",
         "--   atk (s, nil = 2,0 supposé), rare } }",
         "local T = TreeAI", 'if type(T) ~= "table" then return end', "T.Data = T.Data or {}",
         "T.Data.Pets = {", f"  build = {_s(d['build'])},", "  families = {"]
    for fid in sorted(fams):
        f = fams[fid]
        learn = ", ".join(_s(x) for x in f["learn"])
        L.append(f"    [{fid}] = {{ n = {_s(f['n'])}, diet = {f['diet']}, dmg = {f['dmg']}, armor = {f['armor']},"
                 f" hp = {f['hp']}, icon = {f['icon']}, learn = {{ {learn} }} }},")
    L.append("  },")
    L.append("  abilities = {")
    for nm in sorted(d["abilities"]):
        a = d["abilities"][nm]
        L.append(f"    [{_s(nm)}] = {{ ids = {{ {', '.join(str(i) for i in a['ids'])} }},"
                 f" lv = {{ {', '.join(str(i) for i in a['lv'])} }} }},")
    L.append("  },")
    L.append("  beasts = {")
    for b in d["beasts"]:
        teach = f"{{ {_s(b['teach'][0])}, {b['teach'][1]} }}" if b.get("teach") else "nil"
        atk = "nil" if b.get("atk") is None else repr(float(b["atk"]))
        L.append(f"    {{ npc = {b['npc']}, n = {_s(b['n'])}, fam = {b['fam']}, lo = {b['lo']}, hi = {b['hi']},"
                 f" map = {b['map']}, x = {b['x']}, y = {b['y']}, zone = {_s(b['zone'])}, fz = {_s(b['fz'])},"
                 f" teach = {teach}, atk = {atk}, rare = {'true' if b.get('rare') else 'false'} }},")
    L.append("  },")
    L.append("}")
    return "\n".join(L) + "\n"


def write_json(path: str, d: dict) -> None:
    data = json.dumps(d, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with open(path, "wb") as fh:
        with gzip.GzipFile(filename="", mode="wb", fileobj=fh, mtime=0, compresslevel=9) as g:
            g.write(data)


def load_json(path: str = OUT_JSON) -> dict:
    with gzip.open(path, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache", default=os.path.join(tempfile.gettempdir(), "treeai_wago_cache"))
    ap.add_argument("--build", default=BUILD)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--render", action="store_true", help="re-render Data/Pets.lua from pets.json.gz + BEASTS")
    a = ap.parse_args(argv)
    if a.render:
        d = load_json()
        d["beasts"] = beasts(d | {"families": {int(k): v for k, v in d["families"].items()}})
    else:
        sys.path.insert(0, HERE)
        from wow_build_data import fetch  # noqa: PLC0415 - same downloader / cache as the other tables
        import csv  # noqa: PLC0415,F401
        os.makedirs(a.cache, exist_ok=True)
        d = build({t: fetch(a.cache, t, a.build, a.offline) for t in TABLES})
        d["build"] = a.build
    write_json(OUT_JSON, d)
    with open(OUT_LUA, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_lua(json.loads(json.dumps(d))))
    print(f"pets: {len(d['families'])} families, {len(d['abilities'])} abilities, {len(d['beasts'])} beasts",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
