"""Build ``treeaicoach/assets/champion_meta.json``: versioned champion profiles with provenance.

One profile per champion of the live Data Dragon version (every champion, new releases included)::

    {"schema": 2, "version": "16.19.1", "patch": "26.19", "generated": "2026-10-01",
     "sources": {...}, "fields": {...}, "license": "...",
     "champions": {"Garen": {"name": "Garen", "key": 86, "tags": ["Fighter", "Tank"],
                             "cls": ["FIGHTER", "JUGGERNAUT", "TANK"], "class": "juggernaut",
                             "lane": "juggernaut", "dmg": "P", "rng": "M", "ar": 175, "hp": 690,
                             "hpl": 98, "ms": 340, "pos": ["TOP"], "r": [2, 3, 1, 1, 1], "mob": 1,
                             "cc": 1, "diff": 1, "curve": "mid", "spike6": 3, "wave": 2, "split": 2,
                             "sus": 3, "style": ["sustain"],
                             "src": {"r": "rc", "pos": "rr", "curve": "cu", ...}}}}

Field sources (``src`` per champion and field), best first:

* ``dd`` Riot Data Dragon ``champion.json`` (en_US / fr_FR): key, names, tags, base stats;
* ``rc`` Riot client champion data (CommunityDragon ``rcp-be-lol-game-data``): official playstyle
  ratings (damage, durability, crowd control, mobility, utility), damage type, attack type,
  difficulty;
* ``rr`` Riot client rune recommendations (``champion-rune-recommendations.json``): the positions
  Riot recommends on Summoner's Rift, the default one first;
* ``mk`` Meraki Analytics ``champions.json`` (code MIT, data from the League of Legends Wiki under
  CC BY-SA 3.0): class roles (JUGGERNAUT, CATCHER...), fallback ratings / positions;
* ``wk`` League of Legends Wiki (CC BY-SA 3.0), transcribed in ``tools/data/champion_curation.json``
  for champions Meraki does not know yet;
* ``cu`` TreeAI curation (``tools/data/champion_curation.json``): power curve, level-6 spike,
  waveclear, splitpush, sustain, mixed damage, play styles, lane class overrides;
* ``ru`` rule derived from the other fields (see :func:`derive`).

Usage:  python tools/fetch_meta.py [--out path] [--offline] [--meraki-file path]
Network access only when this tool is run by hand (downloads are cached in training/cache/gamedata).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import datalib  # noqa: E402

OUT = ROOT / "treeaicoach" / "assets" / "champion_meta.json"
CURATION = ROOT / "tools" / "data" / "champion_curation.json"
RUNE_RECS = f"{datalib.CDRAGON}/plugins/rcp-be-lol-game-data/global/default/v1/champion-rune-recommendations.json"
SCHEMA = 2

SUBCLASSES = ("JUGGERNAUT", "DIVER", "SKIRMISHER", "ASSASSIN", "BURST", "BATTLEMAGE", "ARTILLERY", "MARKSMAN",
              "ENCHANTER", "CATCHER", "VANGUARD", "WARDEN", "SPECIALIST")
#: primary subclass preference by Data Dragon primary tag
PREF = {
    "Fighter": ("JUGGERNAUT", "DIVER", "SKIRMISHER", "ASSASSIN", "VANGUARD", "WARDEN", "SPECIALIST", "BATTLEMAGE"),
    "Tank": ("VANGUARD", "WARDEN", "JUGGERNAUT", "CATCHER", "DIVER", "SPECIALIST"),
    "Mage": ("BURST", "BATTLEMAGE", "ARTILLERY", "SPECIALIST", "ASSASSIN", "CATCHER", "ENCHANTER", "WARDEN"),
    "Assassin": ("ASSASSIN", "SKIRMISHER", "DIVER", "BURST", "SPECIALIST", "CATCHER"),
    "Marksman": ("MARKSMAN", "SPECIALIST", "ARTILLERY", "ASSASSIN"),
    "Support": ("ENCHANTER", "CATCHER", "WARDEN", "VANGUARD", "BURST", "ARTILLERY", "SPECIALIST", "MARKSMAN"),
}
TAG_ROLES = {"Tank": ["TANK", "VANGUARD"], "Fighter": ["FIGHTER", "JUGGERNAUT"], "Mage": ["MAGE", "BURST"],
             "Assassin": ["ASSASSIN"], "Marksman": ["MARKSMAN"], "Support": ["SUPPORT", "ENCHANTER"]}
TAG_POS = {"Marksman": "BOTTOM", "Support": "UTILITY", "Mage": "MIDDLE", "Assassin": "MIDDLE", "Fighter": "TOP",
           "Tank": "TOP"}
LANE_CLASSES = ("juggernaut", "diver", "skirmisher", "tank", "assassin", "mage", "marksman", "enchanter", "engage")
POSITIONS = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
SOURCES = {
    "dd": "Riot Data Dragon champion.json (en_US, fr_FR)",
    "rc": "Riot client champion data via CommunityDragon (rcp-be-lol-game-data champions/{id}.json)",
    "rr": "Riot client rune recommendations via CommunityDragon (champion-rune-recommendations.json, map 11)",
    "mk": "Meraki Analytics champions.json (MIT; data from the League of Legends Wiki, CC BY-SA 3.0)",
    "wk": "League of Legends Wiki champion pages (CC BY-SA 3.0), transcribed in tools/data/champion_curation.json",
    "cu": "TreeAI curation, tools/data/champion_curation.json",
    "ru": "rule derived from the other fields (tools/fetch_meta.py)",
}
FIELDS = {
    "name": "French name", "key": "numeric champion id", "tags": "Data Dragon class tags",
    "cls": "class roles (Meraki / wiki: JUGGERNAUT, CATCHER...)", "class": "primary subclass (lowercase)",
    "lane": "lane class for the matchup tips (" + " / ".join(LANE_CLASSES) + ")",
    "dmg": "P physical / M magic / X mixed", "rng": "M melee / R ranged", "ar": "attack range",
    "hp": "base health", "hpl": "health per level", "ms": "move speed",
    "pos": "positions (Live Client names), the usual one first",
    "r": "ratings 0..3: damage, toughness, crowd control, mobility, utility", "mob": "mobility 1..3",
    "cc": "crowd control 0..3", "diff": "difficulty 1..3", "curve": "power curve early / mid / late",
    "spike6": "level-6 power spike 1..3", "wave": "waveclear 1..3", "split": "splitpush 1..3",
    "sus": "sustain 1..3", "g": "grammatical gender of the French name (m / f), for the tips",
    "style": "play styles: engage / pick / poke / burst / dive / sustain / peel / splitpush",
}
LICENSE = ("Derived data. Riot Games data (Data Dragon, client data via CommunityDragon) used under Riot's "
           "\"Legal Jibber Jabber\" policy; class roles via Meraki Analytics (MIT) from the League of Legends "
           "Wiki, CC BY-SA 3.0 (https://wiki.leagueoflegends.com) - this file is shared under CC BY-SA 3.0; "
           "curated fields: TreeAI Coach.")


def load_curation(path: Path = CURATION) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _set(cur: dict, *keys: str) -> set[str]:
    d: Any = cur
    for k in keys:
        d = (d or {}).get(k)
    return set(d or ())


def primary_subclass(roles: list[str], tags: list[str]) -> str:
    rs = set(roles)
    for sub in PREF.get(tags[0] if tags else "", ()) + SUBCLASSES:
        if sub in rs:
            return sub.lower()
    return (tags[0] if tags else "fighter").lower()


def lane_class(roles: set[str], tags: list[str], ranged: bool, dmg: str, main_pos: str) -> str:
    tag0 = tags[0] if tags else ""
    if main_pos == "UTILITY":
        if "ENCHANTER" in roles:
            return "enchanter"
        if roles & {"VANGUARD", "WARDEN", "CATCHER"}:
            return "engage"
        if "MARKSMAN" in roles:
            return "marksman"
        return "mage"
    if "MARKSMAN" in roles and ranged:
        return "marksman"
    for sub, cls in (("JUGGERNAUT", "juggernaut"), ("DIVER", "diver"), ("SKIRMISHER", "skirmisher")):
        if sub in roles:
            return cls
    if "ASSASSIN" in roles and not ranged:
        return "assassin"
    if roles & {"VANGUARD", "WARDEN"}:
        return "tank"
    if roles & {"BURST", "BATTLEMAGE", "ARTILLERY"} or tag0 == "Mage":
        return "mage"
    if "ASSASSIN" in roles:
        return "assassin"
    if "CATCHER" in roles:
        return "engage"
    if "ENCHANTER" in roles:
        return "enchanter"
    if ranged:
        return "mage" if dmg == "M" else "marksman"
    return {"Tank": "tank", "Fighter": "skirmisher", "Assassin": "assassin"}.get(tag0, "skirmisher")


def styles(cls: set[str], r: list[int], alias: str, ranged: bool, sus: int, split: int, cur: dict) -> list[str]:
    s: list[str] = []
    _dmg, tough, ctrl, mob, _util = r
    if cls & {"VANGUARD", "DIVER"} or (ctrl >= 3 and tough >= 2) or alias in _set(cur, "styles", "engage_extra"):
        s.append("engage")
    if cls & {"CATCHER"} or alias in _set(cur, "styles", "pick_extra"):
        s.append("pick")
    if cls & {"ARTILLERY"} or alias in _set(cur, "styles", "poke"):
        s.append("poke")
    if cls & {"BURST", "ASSASSIN"}:
        s.append("burst")
    if cls & {"DIVER", "ASSASSIN", "SKIRMISHER"} and mob >= 2:
        s.append("dive")
    if sus >= 3:
        s.append("sustain")
    if cls & {"ENCHANTER", "WARDEN"} or alias in _set(cur, "styles", "peel_extra"):
        s.append("peel")
    if split >= 3:
        s.append("splitpush")
    return s


def _positions(cid: int, recs: dict[int, tuple[list[str], list[str]]], mer_pos: list[str], tags: list[str]
               ) -> tuple[list[str], str]:
    norm = ["UTILITY" if p == "SUPPORT" else p for p in mer_pos]
    if cid in recs and recs[cid][1]:
        default, allp = recs[cid]
        rest = sorted((p for p in allp if p not in default), key=lambda p: (p not in norm, POSITIONS.index(p)
                                                                                if p in POSITIONS else 9))
        return [p for p in default if p in POSITIONS] + [p for p in rest if p in POSITIONS], "rr"
    if norm:
        return [p for p in norm if p in POSITIONS], "mk"
    return [TAG_POS.get(tags[0], "TOP")] if tags else ["TOP"], "ru"


def rune_positions(data: Any) -> dict[int, tuple[list[str], list[str]]]:
    """championId -> (default positions, all positions) on Summoner's Rift (map 11)."""
    out: dict[int, tuple[list[str], list[str]]] = {}
    for e in data if isinstance(data, list) else []:
        try:
            cid = int(e.get("championId"))
        except (TypeError, ValueError, AttributeError):
            continue
        dflt: list[str] = []
        allp: list[str] = []
        for rec in e.get("runeRecommendations") or []:
            if rec.get("mapId") != 11:
                continue
            p = str(rec.get("position") or "")
            if p not in POSITIONS:
                continue
            if p not in allp:
                allp.append(p)
            if rec.get("isDefaultPosition") and p not in dflt:
                dflt.append(p)
        out[cid] = (dflt or allp[:1], allp)
    return out


def derive(alias: str, en: dict, fr: dict, client: dict | None, mer: dict | None, recs: dict, cur: dict) -> dict:
    """One profile (see the module doc); pure."""
    src: dict[str, str] = {}
    tags = [str(t) for t in en.get("tags") or []]
    st, info = en.get("stats") or {}, en.get("info") or {}
    cid = int(en.get("key") or 0)
    out: dict[str, Any] = {"name": str(fr.get("name") or en.get("name") or alias), "key": cid, "tags": tags,
                           "ar": int(float(st.get("attackrange") or 0)), "hp": int(float(st.get("hp") or 0)),
                           "hpl": int(float(st.get("hpperlevel") or 0)), "ms": int(float(st.get("movespeed") or 0))}
    for k in ("name", "key", "tags", "ar", "hp", "hpl", "ms"):
        src[k] = "dd"
    # class roles
    new = (cur.get("new_champions") or {}).get(alias) or {}
    if mer and mer.get("roles"):
        cls, src["cls"] = [str(x) for x in mer["roles"]], "mk"
    elif new.get("cls"):
        cls, src["cls"] = [str(x) for x in new["cls"]], "wk"
    else:
        cls, src["cls"] = [x for t in tags for x in TAG_ROLES.get(t, [])], "ru"
    out["cls"] = sorted(set(cls))
    # ratings: Riot client first (all-ones = not filled in by Riot -> Meraki)
    ps = (client or {}).get("playstyleInfo") or {}
    r = [ps.get(k) for k in ("damage", "durability", "crowdControl", "mobility", "utility")]
    if all(isinstance(x, int) for x in r) and r != [1, 1, 1, 1, 1]:
        src["r"] = "rc"
    else:
        ar = (mer or {}).get("attributeRatings") or {}
        r = [ar.get(k) for k in ("damage", "toughness", "control", "mobility", "utility")]
        if all(isinstance(x, int) for x in r):
            src["r"] = "mk"
        else:
            a, d, m = (float(info.get(k) or 5) for k in ("attack", "defense", "magic"))
            r = [max(1, min(3, round(max(a, m) / 3.4))), max(1, min(3, round(d / 3.4))), 2, 2, 1]
            src["r"] = "ru"
    out["r"] = [max(0, min(3, int(x))) for x in r]
    out["mob"], out["cc"] = max(1, out["r"][3]), out["r"][2]
    src["mob"] = src["cc"] = src["r"]
    ti = (client or {}).get("tacticalInfo") or {}
    out["diff"] = max(1, min(3, int(ti.get("difficulty") or round(float(info.get("difficulty") or 5) / 3.4) or 2)))
    src["diff"] = "rc" if ti.get("difficulty") else "dd"
    # damage type / range
    dt = str(ti.get("damageType") or "")
    if alias in _set(cur, "mixed_damage"):
        out["dmg"], src["dmg"] = "X", "cu"
    elif dt in ("kPhysical", "kMagic", "kMixed"):
        out["dmg"], src["dmg"] = {"kPhysical": "P", "kMagic": "M", "kMixed": "X"}[dt], "rc"
    elif mer and mer.get("adaptiveType"):
        out["dmg"], src["dmg"] = ("M" if "MAGIC" in str(mer.get("adaptiveType")) else "P"), "mk"
    else:
        out["dmg"] = "M" if float(info.get("magic") or 0) > float(info.get("attack") or 0) else "P"
        src["dmg"] = "dd"
    at = str(ti.get("attackType") or "").lower()
    if at in ("melee", "ranged"):
        ranged, src["rng"] = at == "ranged", "rc"
    else:
        ranged, src["rng"] = out["ar"] >= 300, "dd"
    out["rng"] = "R" if ranged else "M"
    # positions
    out["pos"], src["pos"] = _positions(cid, recs, list((mer or {}).get("positions") or []), tags)
    # classes
    out["class"], src["class"] = primary_subclass(out["cls"], tags), "ru"
    lane_over = (cur.get("lane_class") or {}).get(alias)
    if lane_over in LANE_CLASSES:
        out["lane"], src["lane"] = lane_over, "cu"
    else:
        out["lane"] = lane_class(set(out["cls"]), tags, ranged, out["dmg"], out["pos"][0] if out["pos"] else "")
        src["lane"] = "ru"
    # curated knowledge
    if alias in _set(cur, "curve", "early"):
        out["curve"], src["curve"] = "early", "cu"
    elif alias in _set(cur, "curve", "late"):
        out["curve"], src["curve"] = "late", "cu"
    else:
        out["curve"], src["curve"] = "mid", "ru"

    def level(field: str, key: str, default: int) -> None:
        for v in ("3", "2", "1"):
            if alias in _set(cur, key, v):
                out[field], src[field] = int(v), "cu"
                return
        out[field], src[field] = default, "ru"

    level("spike6", "spike6", 2)
    level("wave", "waveclear", 1 if out["lane"] in ("enchanter", "engage") else 2)
    level("split", "splitpush", 1)
    level("sus", "sustain", 1)
    out["style"] = styles(set(out["cls"]), out["r"], alias, ranged, out["sus"], out["split"], cur)
    src["style"] = "ru"
    out["g"], src["g"] = ("f" if alias in _set(cur, "female") else "m"), "cu"
    out["src"] = dict(sorted(src.items()))
    return out


def build(version: str, en: dict, fr: dict, clients: dict[int, dict], meraki: dict, recs: dict, cur: dict) -> dict:
    champs = {}
    for alias in sorted(en):
        c = en[alias]
        try:
            cid = int(c.get("key") or 0)
        except (TypeError, ValueError):
            continue
        champs[alias] = derive(alias, c, fr.get(alias) or {}, clients.get(cid), meraki.get(alias), recs, cur)
    return {"schema": SCHEMA, "version": str(version), "patch": datalib.patch_of(version),
            "generated": datalib.today(), "license": LICENSE, "sources": SOURCES, "fields": FIELDS,
            "champions": champs}


def fetch(offline: bool = False, meraki_file: str | None = None, log=print) -> dict:
    version = datalib.latest_version(offline)
    en = datalib.ddragon(version, "champion", "en_US", offline)
    fr = datalib.ddragon(version, "champion", "fr_FR", offline)
    log(f"  Data Dragon {version}: {len(en)} champions")
    meraki = json.loads(Path(meraki_file).read_text(encoding="utf-8")) if meraki_file else \
        datalib.meraki_champions(offline)
    log(f"  Meraki: {len(meraki)} champions")
    clients = datalib.client_champions((int(c["key"]) for c in en.values()), offline=offline, log=log)
    log(f"  Riot client data: {len(clients)} champions")
    try:
        recs = rune_positions(datalib.get_json(RUNE_RECS, offline=offline, max_age_s=3 * 86400.0))
    except Exception as exc:   # noqa: BLE001 - Meraki positions as the fallback
        log(f"  rune recommendations unavailable ({type(exc).__name__}): Meraki positions")
        recs = {}
    log(f"  Riot client positions: {len(recs)} champions")
    return build(version, en, fr, clients, meraki, recs, load_curation())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--offline", action="store_true", help="use only the download cache")
    ap.add_argument("--meraki-file", default=None, help="use a local copy of the Meraki champions.json")
    args = ap.parse_args(argv)
    data = fetch(args.offline, args.meraki_file)
    out = Path(args.out)
    datalib.write_json(out, data)
    srcs: dict[str, int] = {}
    for c in data["champions"].values():
        for s in c["src"].values():
            srcs[s] = srcs.get(s, 0) + 1
    print(f"{len(data['champions'])} champion profiles (patch {data['patch']}, fields by source {srcs}) -> {out} "
          f"({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
