"""Build ``treeaicoach/assets/meta_builds.json``: the live meta builds (items, runes, spells, skill
order with win rates) of the popular champions x roles, pre-bundled so the exe works offline, plus
Riot's own in-client rune / spell recommendations for every champion and the French names.

Sources (see :mod:`treeaicoach.meta_builds`): U.GG per-champion overview JSON (one request per
champion, cached in ``training/cache/gamedata``, 0.5 s between requests), CommunityDragon
``champion-rune-recommendations.json`` (official Riot data), Data Dragon ``runesReforged.json`` /
``summoner.json`` (fr_FR). The patch is the bundled item table's version (``assets/items.json``).

``--icons``: also bundle the icons the build highlight shows offline (``assets/icons/{item,spell,perk}``,
48 px PNG): every item of the bundled builds (start, core, best options), the summoner spells and the
keystones / rune trees (:mod:`treeaicoach.item_icons`).

Usage:  python tools/fetch_meta_builds.py [--out path] [--champions Garen,Swain,...] [--limit 60]
        [--patch 16_20] [--icons] [--offline]
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


def build(champions: list[str], offline: bool = False, limit: int = 60, patch: str = "") -> dict[str, Any]:
    items_table = datalib.read_asset("items.json")
    version = str(items_table.get("version") or "")
    patch = patch or mb.patch_key(version)
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


def bundle_icons(data: dict[str, Any], version: str) -> int:
    """Download and bundle (48 px PNG) the icons of the bundled builds. Returns the icon count."""
    import urllib.request

    import cv2
    import numpy as np

    want: dict[str, set] = {"item": set(), "spell": set(), "perk": set()}
    for c in data["champions"].values():
        for r in c["roles"].values():
            b = mb.build_from_row("x", "TOP", r)
            if b is None:
                continue
            want["item"].update(b.start)
            want["item"].update(b.core)
            want["item"].update(i for i, _w, _n in b.options[:4])
            want["spell"].update(b.spells[:2])
            want["perk"].update([*b.runes[:1], b.primary_style, b.secondary_style])
    for rows in data["riot"].values():
        for r in rows.values():
            want["spell"].update(r.get("spells") or ())
            want["perk"].update([*(r.get("runes") or ())[:1], *(r.get("styles") or ())])
    want["item"].update((1001, 2055, 3340, 3363, 3364, 2003, 2031))   # boots, control ward, trinkets, potions
    names = data["names"]
    n = 0
    for kind, keys in want.items():
        folder = ROOT / "treeaicoach" / "assets" / "icons" / kind
        folder.mkdir(parents=True, exist_ok=True)
        for key in sorted(str(k) for k in keys if k):
            if kind == "item":
                url, fname = f"{datalib.DDRAGON}/cdn/{version}/img/item/{key}.png", f"{key}.png"
            elif kind == "spell":
                img = names.get("spell_images", {}).get(key)
                if not img:
                    continue
                url, fname = f"{datalib.DDRAGON}/cdn/{version}/img/spell/{img}", img
            else:
                path = names.get("perk_icons", {}).get(key)
                if not path:
                    continue
                url, fname = f"{datalib.DDRAGON}/cdn/img/{path}", f"{key}.png"
            dest = folder / fname
            if dest.is_file():
                n += 1
                continue
            try:
                req = urllib.request.Request(url, headers={"User-Agent": mb.USER_AGENT})
                with urllib.request.urlopen(req, timeout=20) as resp:   # noqa: S310
                    raw = resp.read()
                img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
                if img is None:
                    raise ValueError("not an image")
                img = cv2.resize(img, (48, 48), interpolation=cv2.INTER_AREA)
                ok, png = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 9])
                if ok:
                    dest.write_bytes(png.tobytes())
                    n += 1
            except Exception as exc:
                print(f"  ! icon {kind}/{key}: {exc}")
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--champions", default="", help="comma-separated aliases (default: the popular list)")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--patch", default="", help="U.GG patch key (default: the bundled items' version)")
    ap.add_argument("--icons", action="store_true", help="also bundle the build icons (48 px)")
    args = ap.parse_args(argv)
    champs = [c.strip() for c in args.champions.split(",") if c.strip()] or list(DEFAULT_CHAMPIONS)
    data = build(champs, offline=args.offline, limit=args.limit, patch=args.patch)
    if args.icons:
        print(f"icons: {bundle_icons(data, data['version'])} bundled")
    out = Path(args.out)
    datalib.write_json(out, data, indent=None)
    n_roles = sum(len(c["roles"]) for c in data["champions"].values())
    print(f"meta builds: {len(data['champions'])} champions / {n_roles} roles (patch {data['patch']}), "
          f"Riot recommendations for {len(data['riot'])} champions, {len(data['names'].get('perks', {}))} rune names "
          f"-> {out} ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
