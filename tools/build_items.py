"""Build ``treeaicoach/assets/items.json`` (compact item price table) from Data Dragon.

The Live Client Data API gives every player's itemIDs (public on the Tab scoreboard) but not
always a reliable total price; :mod:`treeaicoach.scoreboard` uses this bundled table to value
inventories (estimated gold) and to spot completed major items (power spikes).

Output format::

    {"version": "16.19.1", "items": {"3071": {"n": "Couperet noir", "g": 3000, "k": "legendary"}, ...}}

``k`` (kind): "legendary" (completed major item), "boots" (tier-2 boots), "starter",
"component", "consumable", "trinket", "other".

Usage:  python tools/build_items.py [--version 16.19.1] [--lang fr_FR]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "treeaicoach" / "assets" / "items.json"
DDRAGON = "https://ddragon.leagueoflegends.com"
UA = {"User-Agent": "TreeAICoach-assets/1.0"}


def _get(url: str) -> object:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def classify(item: dict) -> str:
    tags = set(item.get("tags") or [])
    gold = item.get("gold") or {}
    total = int(gold.get("total") or 0)
    into = item.get("into") or []
    if "Trinket" in tags:
        return "trinket"
    if "Consumable" in tags:
        return "consumable"
    if "Boots" in tags:
        return "boots" if total > 300 else "component"
    if into:
        return "component"
    if ("Lane" in tags or "Jungle" in tags) and total <= 500:
        return "starter"
    if total >= 2200:
        return "legendary"
    return "other"


def build(version: str | None, lang: str) -> dict:
    if not version:
        version = _get(f"{DDRAGON}/api/versions.json")[0]   # type: ignore[index]
    data = _get(f"{DDRAGON}/cdn/{version}/data/{lang}/item.json")["data"]   # type: ignore[index]
    items: dict[str, dict] = {}
    for key, it in sorted(data.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
        if not key.isdigit() or int(key) >= 10000:   # >= 10000: Arena / special-mode variants
            continue
        gold = it.get("gold") or {}
        name = re.sub(r"<br>.*$", "", str(it.get("name") or ""))
        name = re.sub(r"<[^>]+>", "", name).strip()
        items[key] = {"n": name, "g": int(gold.get("total") or 0),
                      "k": classify(it)}
    return {"version": version, "lang": lang, "items": items}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--version", default=None)
    ap.add_argument("--lang", default="fr_FR")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    table = build(args.version, args.lang)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(table, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                   encoding="utf-8")
    print(f"{len(table['items'])} items (Data Dragon {table['version']}) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
