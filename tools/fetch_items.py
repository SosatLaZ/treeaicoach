"""Download the Data Dragon item data (fr_FR + en_US) into ``treeaicoach/assets/items.json``.

Superset of the format written by ``tools/build_items.py`` (``n`` / ``g`` / ``k`` are kept for
:mod:`treeaicoach.scoreboard`), with the extra fields used by :mod:`treeaicoach.itemization`::

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
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "treeaicoach" / "assets" / "items.json"
DDRAGON = "https://ddragon.leagueoflegends.com"
UA = {"User-Agent": "TreeAICoach-assets/1.0"}
SR_MAP = "11"


def _get(url: str) -> Any:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:   # noqa: S310 - fixed https host
        return json.loads(resp.read().decode("utf-8"))


def classify(item: dict) -> str:
    """Same rules as ``tools/build_items.py`` (kept in sync: scoreboard reads ``k``)."""
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


def clean_name(name: Any) -> str:
    s = re.sub(r"<br>.*$", "", str(name or ""))
    return re.sub(r"<[^>]+>", "", s).strip()


def purchasable_on_rift(item: dict) -> bool:
    gold = item.get("gold") or {}
    return bool((item.get("maps") or {}).get(SR_MAP)) and bool(gold.get("purchasable")) \
        and item.get("inStore", True) is not False and not item.get("requiredChampion") \
        and not item.get("requiredAlly") and not item.get("hideFromAll")


def _ids(xs: Any) -> list[int]:
    out = []
    for x in xs or []:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return out


def build_table(fr: dict, en: dict, version: str) -> dict:
    """Compact table from the two Data Dragon ``data`` dicts (pure, tested)."""
    items: dict[str, dict] = {}
    for key, it in sorted(fr.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
        if not key.isdigit() or int(key) >= 10000:   # >= 10000: Arena / special-mode variants
            continue
        gold = it.get("gold") or {}
        e = en.get(key) or {}
        stats = {k: v for k, v in (it.get("stats") or {}).items()
                 if isinstance(v, (int, float)) and not isinstance(v, bool) and v}
        items[key] = {
            "n": clean_name(it.get("name")),
            "en": clean_name(e.get("name")) or clean_name(it.get("name")),
            "g": int(gold.get("total") or 0),
            "b": int(gold.get("base") or 0),
            "k": classify(it),
            "t": sorted(str(t) for t in (it.get("tags") or [])),
            "s": stats,
            "f": _ids(it.get("from")),
            "i": _ids(it.get("into")),
            "p": 1 if purchasable_on_rift(it) else 0,
        }
    return {"version": version, "lang": "fr_FR", "items": items}


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
