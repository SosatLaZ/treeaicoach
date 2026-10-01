"""Download the Data Dragon item data (fr_FR + en_US) into ``treeaicoach/assets/items.json``.

The conversion is :func:`treeaicoach.game_data.build_items_table` (the same code refreshes the
table at runtime); ``n`` / ``g`` / ``k`` are read by :mod:`treeaicoach.scoreboard`, the other
fields by :mod:`treeaicoach.itemization`::

    {"version": "16.19.1", "lang": "fr_FR", "not_sr": [1105, 2051, ...],
     "items": {"3157": {"n": "Sablier de Zhonya", "en": "Zhonya's Hourglass", "g": 3250, "b": 450,
                        "k": "legendary", "t": ["Active", "Armor", "SpellDamage"],
                        "s": {"FlatArmorMod": 50, "FlatMagicDamageMod": 105},
                        "f": [1058, 2420], "i": [], "p": 1, "x": ["stasis"]}, ...}}

``g`` total gold, ``b`` combine cost (gold.base), ``t`` Data Dragon tags, ``s`` stats, ``f`` / ``i``
built from / builds into, ``x`` semantic flags from the English description, ``p`` 1 when the item
can be bought in a normal Summoner's Rift game: purchasable on map 11 for any champion (Data Dragon)
AND listed in the CLASSIC shop of the game files (CommunityDragon ``map11.bin``; Data Dragon also
flags Swiftplay / ARAM starters for map 11). ``not_sr`` = the ids that filter removed (kept by the
runtime refresh).

Usage:  python tools/fetch_items.py [--version 16.19.1] [--out path] [--no-shop-filter] [--offline]
Only the standard library is used. Network access happens only when this tool is run by hand.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import datalib  # noqa: E402 - after the sys.path fix (run as a script)
from treeaicoach.game_data import (  # noqa: E402
    DDRAGON,
    build_items_table as build_table,
    classify,
    clean_name,
    purchasable_on_rift,
)

OUT = ROOT / "treeaicoach" / "assets" / "items.json"

__all__ = ["build_table", "classify", "clean_name", "purchasable_on_rift", "fetch", "main", "DDRAGON"]


def fetch(version: str | None, shop_filter: bool = True, offline: bool = False,
          log=print) -> dict:
    version = version or datalib.latest_version(offline)
    fr = datalib.ddragon(version, "item", "fr_FR", offline)
    en = datalib.ddragon(version, "item", "en_US", offline)
    shop = None
    if shop_filter:
        try:
            shop = datalib.classic_shop(datalib.get_json(datalib.MAP11_BIN, offline=offline, max_age_s=86400.0))
            if len(shop) < 150:
                log(f"  CLASSIC shop list too small ({len(shop)} ids): filter skipped")
                shop = None
        except Exception as exc:   # noqa: BLE001 - the Data Dragon flags stay usable
            log(f"  CLASSIC shop list unavailable ({type(exc).__name__}): Data Dragon map flags only")
    return build_table(fr, en, str(version), shop=shop)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--version", default=None)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--no-shop-filter", action="store_true", help="Data Dragon map flags only")
    ap.add_argument("--offline", action="store_true", help="use only the download cache")
    args = ap.parse_args(argv)
    table = fetch(args.version, not args.no_shop_filter, args.offline)
    out = Path(args.out)
    datalib.write_json(out, table)
    n_sr = sum(1 for v in table["items"].values() if v["p"])
    print(f"{len(table['items'])} items ({n_sr} sold on the Rift, {len(table.get('not_sr') or [])} "
          f"filtered by the CLASSIC shop; Data Dragon {table['version']}) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
