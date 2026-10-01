"""Build ``treeaicoach/assets/item_builds.json``: core build paths, situational counters, boots,
starting items and support item upgrades per class, derived from the item data itself.

Input: the bundled item table ``treeaicoach/assets/items.json`` (``tools/fetch_items.py``: Data
Dragon + the CLASSIC shop filter, with the semantic flags ``x`` read from the descriptions). No
network access. The rules are simple and explicit:

* a **preference list** per class / need (expert rules, below) is kept only where the data agrees:
  the item exists, is sold on the Rift (``p``), is of the expected kind (legendary / boots /
  starter / component) and has the stats or the flag the rule is about (an anti-heal item must
  apply Wounds, an armor counter must give Armor...);
* when a list ends up empty (an item removed by a patch), it is **filled from the data**: the
  sold items with the needed flag / stats, scored by how well their tags fit the class;
* counter lists hold the full item then its cheap flagged component (the advice may only name
  the component before the first legendary - see ``itemization.recommend``).

Every list carries its provenance in ``src`` ("rule" = the preference list survived the data
checks, "data" = filled from the item data). ``tools/validate_data.py`` re-checks the output.

Usage:  python tools/fetch_builds.py [--out path]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import datalib  # noqa: E402

OUT = ROOT / "treeaicoach" / "assets" / "item_builds.json"
SCHEMA = 1
CLASSES = ("marksman", "mage", "assassin_ad", "assassin_ap", "fighter", "fighter_ap", "tank", "support_tank",
           "enchanter")
AD = {"marksman", "assassin_ad", "fighter"}
AP = {"mage", "assassin_ap", "fighter_ap"}
TANKS = {"tank", "support_tank"}

#: Tags that make an item fit a class (score) / a class must find at least one of ``need`` tags.
CLASS_TAGS: dict[str, tuple[set[str], set[str]]] = {
    "marksman": ({"Damage", "AttackSpeed", "CriticalStrike", "LifeSteal", "ArmorPenetration", "OnHit"},
                 {"Damage", "AttackSpeed", "CriticalStrike"}),
    "assassin_ad": ({"Damage", "ArmorPenetration", "NonbootsMovement", "AbilityHaste", "Active"}, {"Damage"}),
    "fighter": ({"Damage", "Health", "AbilityHaste", "Armor", "SpellBlock", "LifeSteal", "Tenacity"}, {"Damage"}),
    "mage": ({"SpellDamage", "Mana", "MagicPenetration", "AbilityHaste", "CooldownReduction"}, {"SpellDamage"}),
    "assassin_ap": ({"SpellDamage", "MagicPenetration", "NonbootsMovement", "AbilityHaste"}, {"SpellDamage"}),
    "fighter_ap": ({"SpellDamage", "Health", "SpellVamp", "AbilityHaste", "AttackSpeed"}, {"SpellDamage"}),
    "tank": ({"Health", "Armor", "SpellBlock", "MagicResist", "AbilityHaste", "Aura", "HealthRegen"},
             {"Health", "Armor", "SpellBlock"}),
    "support_tank": ({"Health", "Armor", "SpellBlock", "MagicResist", "AbilityHaste", "Aura", "Active"},
                     {"Health", "Armor", "SpellBlock"}),
    "enchanter": ({"SpellDamage", "ManaRegen", "AbilityHaste", "Health", "CooldownReduction"},
                  {"ManaRegen", "SpellDamage"}),
}

# ----------------------------------------------------------------------------- expert rules (26.19)
#: core build path per class, in buy order (first legendary first)
CORE: dict[str, list[int]] = {
    "marksman": [3032, 3031, 3046, 3036, 3072, 6672],     # Yun Tal, IE, Phantom Dancer, LDR, BT, Kraken
    "mage": [6655, 4645, 3089, 3135, 3157],                # Luden, Shadowflame, Rabadon, Void Staff, Zhonya
    "assassin_ad": [3142, 6697, 6694, 6696, 3814],         # Youmuu, Hubris, Serylda, Axiom Arc, Edge of Night
    "assassin_ap": [4646, 4645, 3089, 3135, 3157],         # Stormsurge, Shadowflame, Rabadon, Void Staff, Zhonya
    "fighter": [3071, 6610, 3053, 6333],                   # Black Cleaver, Sundered Sky, Sterak, Death's Dance
    "fighter_ap": [4633, 6653, 3157, 3089],                # Riftmaker, Liandry, Zhonya, Rabadon
    "tank": [3068, 3084, 6665, 2502, 4401],                # Sunfire, Heartsteel, Jak'Sho, Unending Despair, FoN
    "support_tank": [3190, 2524, 3109, 3050],              # Locket, Bandlepipes, Knight's Vow, Zeke
    "enchanter": [6617, 6616, 3107, 3222, 3504],           # Moonstone, Staff, Redemption, Mikael, Ardent
}
#: champions whose build differs a lot from their class (core path; optional class change)
CHAMPIONS: dict[str, dict[str, Any]] = {
    "Kayle": {"class": "mage", "core": [3115, 3124, 3089, 4645, 3135]},       # Nashor, Guinsoo, Rabadon...
    "KogMaw": {"core": [3153, 3124, 3302, 3085, 3091]},                       # BotRK, Guinsoo, Terminus...
    "Vayne": {"core": [3153, 3124, 3302, 3036]},
    "Kalista": {"core": [3153, 3124, 3085, 3302]},
    "Ezreal": {"core": [3004, 3078, 6694, 3161]},                             # Manamune, Trinity, Serylda, Shojin
    "Gwen": {"class": "fighter_ap", "core": [3115, 4633, 3089, 3157]},
    "Teemo": {"class": "mage", "core": [3115, 6653, 3089, 4645]},
    "Katarina": {"class": "assassin_ap", "core": [4646, 4645, 3089, 3135, 3157]},
    "Shyvana": {"class": "fighter_ap", "core": [4633, 6653, 3089, 3157]},
    "Senna": {"class": "marksman", "core": [3142, 3179, 6694, 3036]},        # AD support: Youmuu, Umbral...
}
#: counter items per need and class (preference order: full item, then its component)
COUNTERS: dict[str, dict[str, list[int]]] = {
    "antiheal": {"marksman": [3033, 3123], "assassin_ad": [6609, 3123], "fighter": [6609, 3123],
                 "mage": [3165, 3916], "assassin_ap": [3165, 3916], "fighter_ap": [3165, 3916],
                 "tank": [3075, 3076], "support_tank": [3075, 3076], "enchanter": [3916]},
    "magic": {"marksman": [3156, 3139], "assassin_ad": [3156, 3814], "fighter": [3156, 2504],
              "mage": [3102], "assassin_ap": [3102], "fighter_ap": [3102, 2504],
              "tank": [4401, 2504, 3065], "support_tank": [3190, 8020], "enchanter": [3190]},
    "physical": {"marksman": [3026, 6673], "assassin_ad": [6333, 3026], "fighter": [6333, 3053],
                 "mage": [3157], "assassin_ap": [3157], "fighter_ap": [3157],
                 "tank": [3143, 3075], "support_tank": [3109, 3190], "enchanter": [3190]},
    "cc": {"marksman": [3139], "assassin_ad": [3814], "fighter": [3053], "mage": [3102],
           "assassin_ap": [3102], "fighter_ap": [3102], "tank": [3111], "support_tank": [3222],
           "enchanter": [3222]},
    "crit": {"tank": [3143, 3110], "support_tank": [3110, 3143], "fighter": [3143, 6333],
             "fighter_ap": [3157], "mage": [3157], "assassin_ap": [3157]},
    "armor": {"marksman": [3036, 3153], "assassin_ad": [6694], "fighter": [3071, 6694, 3153],
              "mage": [3135, 6653], "assassin_ap": [3135], "fighter_ap": [6653, 3135]},
    "shields": {"assassin_ad": [6695], "fighter": [6695]},
}
#: what the data must confirm for each need: a flag of the item (any of), else a stat tag (any of)
NEED_CHECK: dict[str, tuple[set[str], set[str]]] = {
    "antiheal": ({"antiheal"}, set()),
    "magic": ({"lifeline", "spellshield", "cleanse"}, {"SpellBlock", "MagicResist"}),
    "physical": ({"stasis", "revive", "lifeline"}, {"Armor"}),
    "cc": ({"cleanse", "spellshield"}, {"Tenacity"}),
    "crit": ({"anticrit", "antiattack", "stasis"}, {"Armor"}),
    "armor": ({"armorpen", "magicpen", "armorshred", "pcthp"}, set()),
    "shields": ({"shieldbreak"}, set()),
}
BOOTS_CLASS = {"marksman": 3006, "mage": 3020, "assassin_ap": 3020, "enchanter": 3158, "assassin_ad": 3158,
               "support_tank": 3047, "tank": 3047, "fighter": 3047, "fighter_ap": 3020}
BOOTS_VS = {"physical": 3047, "magic": 3111, "cc": 3111}
#: starting items per start kind (champ_select.start_kind)
START = {
    "support": [3865, 2003, 2003],           # Atlas (support quest item) + 2 potions
    "jungle_tank": [1103, 2003],              # Bébé Ixamandre (Mosstomper)
    "jungle_mobile": [1102, 2003],            # Bébé Saute-nuages (Gustwalker)
    "jungle": [1101, 2003],                   # Bébé Chardent (Scorchclaw)
    "marksman": [1055, 2003],                 # Lame de Doran
    "mage": [1056, 2003, 2003],               # Anneau de Doran
    "tank": [1054, 2003],                     # Bouclier de Doran
    "fighter": [1055, 2003],                  # Lame de Doran
}
START_WHY = {
    "support": "l'objet de support : or et balises",
    "jungle_tank": "familier résistant pour ta jungle",
    "jungle_mobile": "familier rapide pour ganker tôt",
    "jungle": "familier offensif pour nettoyer vite",
    "marksman": "dégâts et vol de vie en voie",
    "mage": "mana et puissance pour farmer de loin",
    "tank": "tenir la voie face aux échanges",
    "fighter": "dégâts et vol de vie en voie",
}
#: support quest: Bounty of Worlds (3867) upgrades for free into one of these (by class / subclass)
SUPPORT_UPGRADE = {"support_tank": 3869, "catcher": 3876, "enchanter": 3870, "mage": 3871, "marksman": 3877,
                   "assassin_ad": 3877, "fighter": 3877}
SUPPORT_ITEM_CHAIN = [3865, 3866, 3867]       # Atlas -> Boussole runique -> Trésor des mondes


# ----------------------------------------------------------------------------- data checks
def _fits(item: dict, cls: str) -> float:
    ok, need = CLASS_TAGS[cls]
    tags = set(item.get("t") or ())
    if not tags & need:
        return 0.0
    if cls in TANKS and tags & {"Damage", "SpellDamage", "CriticalStrike"}:
        return 0.0
    if cls in AD and "SpellDamage" in tags and "Damage" not in tags:
        return 0.0
    if cls in AP and "Damage" in tags and "SpellDamage" not in tags:
        return 0.0
    return len(tags & ok) / max(1, len(tags))


def _sold(items: dict, iid: int) -> dict | None:
    it = items.get(str(iid))
    return it if isinstance(it, dict) and it.get("p") else None


def _confirms(item: dict, need: str) -> bool:
    flags, tags = NEED_CHECK[need]
    return bool(set(item.get("x") or ()) & flags) or bool(set(item.get("t") or ()) & tags)


def check_core(items: dict, cls: str, ids: list[int]) -> list[int]:
    return [i for i in ids if (it := _sold(items, i)) and it.get("k") == "legendary"]


def check_counter(items: dict, need: str, cls: str, ids: list[int]) -> list[int]:
    out = []
    for i in ids:
        it = _sold(items, i)
        if it and it.get("k") in ("legendary", "component", "boots") and _confirms(it, need):
            out.append(i)
    return out


def fill_counter(items: dict, need: str, cls: str) -> list[int]:
    """Best sold legendary with the need's flag / stats for the class, then its flagged component."""
    best = sorted(((_fits(it, cls), -int(it.get("g") or 0), int(k)) for k, it in items.items()
                   if it.get("p") and it.get("k") == "legendary" and _confirms(it, need) and _fits(it, cls) > 0),
                  reverse=True)
    if not best:
        return []
    top = best[0][2]
    comps = [p for p in items[str(top)].get("f") or () if (c := _sold(items, p)) and _confirms(c, need)]
    return [top] + comps[:1]


def fill_core(items: dict, cls: str, n: int = 4) -> list[int]:
    scored = sorted(((_fits(it, cls), int(it.get("g") or 0), int(k)) for k, it in items.items()
                     if it.get("p") and it.get("k") == "legendary" and _fits(it, cls) >= 0.5), reverse=True)
    return [i for _f, _g, i in scored[:n]]


def build(items_table: dict) -> dict:
    items = items_table.get("items") or {}
    src: dict[str, str] = {}
    problems: list[str] = []
    core: dict[str, list[int]] = {}
    for cls in CLASSES:
        ids = check_core(items, cls, CORE[cls])
        dropped = [i for i in CORE[cls] if i not in ids]
        if dropped:
            problems.append(f"core.{cls}: {dropped} not sold / not legendary")
        if len(ids) < 3:
            ids = ids + [i for i in fill_core(items, cls) if i not in ids]
            src[f"core.{cls}"] = "data"
        else:
            src[f"core.{cls}"] = "rule"
        core[cls] = ids
    counters: dict[str, dict[str, list[int]]] = {}
    for need, by_cls in COUNTERS.items():
        counters[need] = {}
        for cls, ids in by_cls.items():
            ok = check_counter(items, need, cls, ids)
            if len(ok) < len(ids):
                problems.append(f"counters.{need}.{cls}: {[i for i in ids if i not in ok]} rejected by the data")
            if not ok:
                ok = fill_counter(items, need, cls)
                src[f"counters.{need}.{cls}"] = "data"
            else:
                src[f"counters.{need}.{cls}"] = "rule"
            if ok:
                counters[need][cls] = ok
    champs: dict[str, dict[str, Any]] = {}
    for alias, d in CHAMPIONS.items():
        row: dict[str, Any] = {}
        if d.get("class") in CLASSES:
            row["class"] = d["class"]
        c = check_core(items, row.get("class", ""), list(d.get("core") or ()))
        if c:
            row["core"] = c
        if row:
            champs[alias] = row
            src[f"champions.{alias}"] = "rule"
    boots_class = {c: b for c, b in BOOTS_CLASS.items() if (it := _sold(items, b)) and it.get("k") == "boots"}
    boots_vs = {n: b for n, b in BOOTS_VS.items() if (it := _sold(items, b)) and it.get("k") == "boots"}
    start = {}
    for kind, ids in START.items():
        ok = [i for i in ids if (it := _sold(items, i)) and it.get("k") in ("starter", "consumable")]
        if ok:
            start[kind] = ok
        if len(ok) < len(ids):
            problems.append(f"start.{kind}: {[i for i in ids if i not in ok]} not sold")
    upgrade = {k: i for k, i in SUPPORT_UPGRADE.items() if str(i) in items}
    lasting = ("legendary", "boots", "component")          # not consumables (Elixir of Iron)
    same_need = {
        "antiheal": sorted(int(k) for k, it in items.items() if it.get("p") and it.get("k") in lasting
                           and "antiheal" in (it.get("x") or ())),
        "cc": sorted(int(k) for k, it in items.items() if it.get("p") and it.get("k") in lasting and (
            {"cleanse", "spellshield"} & set(it.get("x") or ()) or "Tenacity" in (it.get("t") or ()))),
    }
    version = str(items_table.get("version") or "")
    return {"schema": SCHEMA, "version": version, "patch": datalib.patch_of(version), "generated": datalib.today(),
            "rules": "tools/fetch_builds.py: expert preference lists checked against the item data (sold on the Rift, "
                     "kind, stats / semantic flags), empty lists filled from the data",
            "classes": list(CLASSES), "core": core, "counters": counters, "champions": champs,
            "boots": {"class": boots_class, "vs": boots_vs}, "start": start, "start_why": START_WHY,
            "support_upgrade": upgrade, "support_item_chain": SUPPORT_ITEM_CHAIN, "same_need": same_need,
            "src": dict(sorted(src.items())), "problems": problems}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    data = build(datalib.read_asset("items.json"))
    out = Path(args.out)
    datalib.write_json(out, data, indent=1)
    n_data = sum(1 for v in data["src"].values() if v == "data")
    print(f"builds for {len(data['core'])} classes, {sum(len(v) for v in data['counters'].values())} counter lists, "
          f"{len(data['champions'])} champion overrides (patch {data['patch']}; {n_data} lists filled from the data, "
          f"{len(data['problems'])} rule items rejected) -> {out}")
    for p in data["problems"]:
        print("  !", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
