"""Build ``treeaicoach/assets/meta_builds.json``: the live meta builds (items, runes, spells, skill
order with win rates) of the popular champions x roles, pre-bundled so the exe works offline, plus
Riot's own in-client rune / spell recommendations for every champion and the French names.

Sources (see :mod:`treeaicoach.meta_builds`): U.GG per-champion overview JSON (one request per
champion, cached in ``training/cache/gamedata``, 0.5 s between requests), CommunityDragon
``champion-rune-recommendations.json`` (official Riot data), Data Dragon ``runesReforged.json`` /
``summoner.json`` (fr_FR). The patch is the bundled item table's version (``assets/items.json``).

Usage:  python tools/fetch_meta_builds.py [--out path] [--champions Garen,Swain,...] [--limit 60] [--offline]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import datalib  # noqa: E402
from treeaicoach import meta_builds as mb  # noqa: E402

OUT = ROOT / "treeaicoach" / "assets" / "meta_builds.json"
#: the user's champions first, then the most played champions of 2026 solo queue (roles are kept
#: when U.GG has >= MIN_MATCHES games for them, so a champion may give 1-3 roles)
DEFAULT_CHAMPIONS = (
    "Garen Swain TahmKench Blitzcrank Seraphine Lux MissFortune Jinx "
    "Darius Sett Mordekaiser Nasus Malphite Yone Yasuo Jax Aatrox Camille Fiora Riven Ornn Shen Teemo Tryndamere "
    "LeeSin Vi Viego Kayn Warwick MasterYi Amumu Nocturne JarvanIV Hecarim XinZhao Ekko "
    "Ahri Zed Syndra Orianna Katarina Viktor Sylas Akali Annie Veigar Yone Galio "
    "Caitlyn Ezreal Kaisa Ashe Vayne Jhin Xayah Varus Samira Lucian Draven "
    "Thresh Leona Nautilus Morgana Soraka Lulu Yuumi Janna Karma Rakan Pyke Milio Nami Senna Braum"
).split()
SLEEP_S = 0.5


def key_of(alias: str) -> int:
    from treeaicoach.champions import get_default_db

    e = get_default_db().get(alias)
    return int(e.key) if e is not None else 0


def fetch_champion(alias: str, patch: str, items: dict, offline: bool) -> dict[str, Any] | None:
    key = key_of(alias)
    if key <= 0:
        print(f"  ! unknown champion {alias}")
        return None
    url = mb.UGG_URL.format(patch=patch, key=key)
    try:
        data = datalib.get_json(url, offline=offline, max_age_s=7 * 86400, timeout=20.0)
    except Exception as exc:
        print(f"  ! {alias}: {exc}")
        return None
    roles = {}
    for role in mb.ROLES:
        row = mb.parse_ugg(data, role, items)
        if row:
            roles[role] = row
    return mb.keep_roles(roles) or None


def build(champions: list[str], offline: bool = False, limit: int = 60) -> dict[str, Any]:
    items_table = datalib.read_asset("items.json")
    version = str(items_table.get("version") or "")
    patch = mb.patch_key(version)
    from treeaicoach.itemization import load_items

    items = load_items(items_table)
    champs: dict[str, Any] = {}
    seen: list[str] = []
    for alias in champions:
        if alias in seen:
            continue
        seen.append(alias)
        if len(champs) >= limit:
            break
        roles = fetch_champion(alias, patch, items, offline)
        if roles:
            champs[alias] = {"roles": roles, "fetched": max(r.get("fetched", "") for r in roles.values())}
            summary = ", ".join("%s %dg %.1f%%" % (r, v["matches"], 100.0 * v["wins"] / max(1, v["matches"]))
                                for r, v in roles.items())
            print(f"  {alias}: {summary}")
        if not offline:
            time.sleep(SLEEP_S)
    riot = {}
    try:
        riot = mb.compact_riot(datalib.get_json(mb.RIOT_RUNES_URL, offline=offline, max_age_s=86400, timeout=30.0))
    except Exception as exc:
        print(f"  ! Riot recommendations: {exc}")
    names = {}
    try:
        names = mb.names_from_ddragon(
            datalib.get_json(f"{datalib.DDRAGON}/cdn/{version}/data/fr_FR/runesReforged.json", offline=offline),
            datalib.get_json(f"{datalib.DDRAGON}/cdn/{version}/data/fr_FR/summoner.json", offline=offline))
    except Exception as exc:
        print(f"  ! names: {exc}")
    return {"schema": mb.SCHEMA, "version": version, "patch": patch, "generated": datalib.today(),
            "source": mb.SOURCE_UGG,
            "sources": {"ugg": "U.GG champion overview JSON (ranked solo, world, Emerald+ / Platinum+ / all), "
                               "undocumented endpoint: " + mb.UGG_URL,
                        "riot": "Riot client rune / spell recommendations via CommunityDragon: " + mb.RIOT_RUNES_URL,
                        "names": "Riot Data Dragon runesReforged.json / summoner.json (fr_FR)"},
            "champions": champs, "riot": riot, "names": names}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--champions", default="", help="comma-separated aliases (default: the popular list)")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args(argv)
    champs = [c.strip() for c in args.champions.split(",") if c.strip()] or list(DEFAULT_CHAMPIONS)
    data = build(champs, offline=args.offline, limit=args.limit)
    out = Path(args.out)
    datalib.write_json(out, data, indent=None)
    n_roles = sum(len(c["roles"]) for c in data["champions"].values())
    print(f"meta builds: {len(data['champions'])} champions / {n_roles} roles (patch {data['patch']}), "
          f"Riot recommendations for {len(data['riot'])} champions, {len(data['names'].get('perks', {}))} rune names "
          f"-> {out} ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
