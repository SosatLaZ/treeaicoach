"""Download the Data Dragon item data (fr_FR + en_US) into ``treeaicoach/assets/items.json``.

The conversion is :func:`treeaicoach.game_data.build_items_table` (the same code refreshes the
table at runtime); ``n`` / ``g`` / ``k`` are read by :mod:`treeaicoach.scoreboard`, the other
fields by :mod:`treeaicoach.itemization`::

    {"version": "16.19.1", "lang": "fr_FR",
     "items": {"3157": {"n": "Sablier de Zhonya", "en": "Zhonya's Hourglass", "g": 3250, "b": 450,
                        "k": "legendary", "t": ["Armor", "SpellDamage", "Active"],
                        "s": {"FlatArmorMod": 50, "FlatMagicDamageMod": 105},
                        "f": [1058, 2420], "i": [], "p": 1}, ...}}

``g`` total gold, ``b`` combine cost (gold.base), ``t`` Data Dragon tags, ``s`` stats, ``f`` / ``i``
built from / builds into, ``p`` 1 when the item can be bought on Summoner's Rift (map 11) by any
champion (purchasable, in store, not champion-specific, not hidden).

Usage:  python tools/fetch_items.py [--version 16.19.1] [--out path]
Only the standard library is used. Network access happens only when this tool is run by hand.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treeaicoach.game_data import (  # noqa: E402 - after the sys.path fix (run as a script)
    DDRAGON,
    build_items_table as build_table,
    classify,
    clean_name,
    purchasable_on_rift,
)

OUT = ROOT / "treeaicoach" / "assets" / "items.json"
UA = {"User-Agent": "TreeAICoach-assets/1.0"}

__all__ = ["build_table", "classify", "clean_name", "purchasable_on_rift", "fetch", "main"]


def _get(url: str) -> Any:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:   # noqa: S310 - fixed https host
        return json.loads(resp.read().decode("utf-8"))


def fetch(version: str | None) -> dict:
    if not version:
        version = _get(f"{DDRAGON}/api/versions.json")[0]
    fr = _get(f"{DDRAGON}/cdn/{version}/data/fr_FR/item.json")["data"]
    en = _get(f"{DDRAGON}/cdn/{version}/data/en_US/item.json")["data"]
    return build_table(fr, en, str(version))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--version", default=None)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    table = fetch(args.version)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(table, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                   encoding="utf-8")
    tmp.replace(out)
    n_sr = sum(1 for v in table["items"].values() if v["p"])
    print(f"{len(table['items'])} items ({n_sr} on the Rift, Data Dragon {table['version']}) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
