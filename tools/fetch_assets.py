"""Download the official game assets used by TreeAICoach.

Sources (public Riot/community CDNs, no login):
  * Data Dragon  (ddragon.leagueoflegends.com)  -> champion list + localized names
  * CommunityDragon (raw.communitydragon.org)  -> minimap textures, fog, minimap
    icons, pings and the round "circle" champion icons (with skins) that the
    game draws on the minimap.

Two destinations:
  * ``treeaicoach/assets/``   runtime assets bundled in the .exe (small)
  * ``training/cache/``       training-only assets (all skins, ~50 MB, gitignored)

Usage:
    python tools/fetch_assets.py            # runtime + training assets
    python tools/fetch_assets.py --runtime  # only what the .exe needs
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "treeaicoach" / "assets"
TRAIN_DIR = ROOT / "training" / "cache"

DDRAGON = "https://ddragon.leagueoflegends.com"
CDRAGON = "https://raw.communitydragon.org/latest"
UA = {"User-Agent": "TreeAICoach-assets/1.0 (+https://github.com/sosatlaz/treeaicoach)"}

# Minimap icons (ux/minimap/icons) used by the renderer / demo / training.
MINIMAP_ICONS = [
    "icon_ui_tower_minimap.png", "tower.png", "tower_low.png", "tower_medium.png",
    "turret_1plate.png", "turret_3plate.png", "turret_5plate.png",
    "inhibitor.png", "icon_ui_inhibitor_minimap_v2.png", "nexus.png", "icon_ui_nexus_minimap_v2.png",
    "minimap_ward_green_full.png", "minimap_ward_green_enemy_new.png", "minimap_ward_pink_friendly.png",
    "minimap_ward_pink_enemy.png", "minimap_ward_blue_full.png", "minimap_jammer_enemy.png",
    "minimap_jammer_friendly.png", "camp.png", "smallcamp.png", "jungle_camp_1.png", "jungle_camp_2.png",
    "jungle_camp_3.png", "jungle_camp_4.png", "jungle_camp_5.png", "jungle_camp_6.png", "jungle_camp_7.png",
    "jungle_camp_current.png", "jungle_camp_next.png", "blue.png", "red.png", "dragon.png",
    "dragon_infernal.png", "dragon_ocean.png", "dragon_mountain.png", "dragon_cloud.png",
    "dragon_hextech.png", "dragon_chemtech.png", "dragon_elder.png", "baron.png", "riftherald.png",
    "grub.png", "atakhan_r.png", "atakhan_v.png", "shop.png", "healthpack.png", "junglelight.png",
    "jungleplant.png", "plant_icon_yellow.png", "recalloutline.png", "recallhostileoutline.png",
    "teleporthighlight_enemy.png", "teleporthighlight_friendly.png", "champion_dead.png",
    "minionmapcircle.png", "mapcircle.png", "dummy_enemy_circle.png", "dummy_friendly_circle.png",
    "timergrey.png", "timeryellow.png", "tunnelicon.png", "zzrotblue.png", "zzrotred.png",
    "respawnicon01.png", "questicon.png", "crystalicon.png",
]
PINGS = [
    "ping.png", "caution.png", "mia_new.png", "on_my_way_new.png", "assist.png", "enemychampsighted.png",
    "need_ward.png", "retreat.png", "push.png", "all_in.png", "hold.png", "bait.png", "target.png",
    "area_is_warded_small_red_new.png", "ring_red.png", "ring_green.png", "ring2_red.png",
    "ring2_yellow.png", "pingmarker_red.png", "pingmarker_green.png", "ring_danger.png",
]
MAP_TEXTURE_RE = re.compile(r"^2dlevelminimap_(base|cloud|hextech|infernal|mountain|ocean)_baron\d\.png$")
FOG_RE = re.compile(r"^fogofwaroverlay(_srx_[a-z]+)?\.png$")

RUNTIME_ICON_SIZE = 64  # champion circle icons bundled in the exe are downscaled to 64x64


def http_get(url: str, retries: int = 4, timeout: float = 30.0) -> bytes:
    delay = 1.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            if attempt == retries - 1:
                raise
        except Exception:
            if attempt == retries - 1:
                raise
        time.sleep(delay)
        delay *= 2
    raise RuntimeError("unreachable")


def http_json(url: str):
    return json.loads(http_get(url).decode("utf-8"))


def list_dir(url: str) -> list[str]:
    """List file names of a CommunityDragon directory listing."""
    html = http_get(url.rstrip("/") + "/").decode("utf-8", "replace")
    names = re.findall(r'href="([^"?#/][^"]*)"', html)
    return [n for n in names if not n.startswith("http") and not n.endswith("/")]


def save_png(data: bytes, dest: Path, size: int | None = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    im = Image.open(io.BytesIO(data)).convert("RGBA")
    if size and im.size != (size, size):
        im = im.resize((size, size), Image.LANCZOS)
    im.save(dest, optimize=True)


def circle_mask_square_icon(data: bytes) -> Image.Image:
    """Fallback: turn a Data Dragon square portrait into a round icon."""
    im = Image.open(io.BytesIO(data)).convert("RGBA").resize((128, 128), Image.LANCZOS)
    yy, xx = np.mgrid[0:128, 0:128]
    d = np.sqrt((xx - 63.5) ** 2 + (yy - 63.5) ** 2)
    alpha = np.clip(64.0 - d, 0.0, 1.0) * 255
    arr = np.array(im)
    arr[..., 3] = alpha.astype(np.uint8)
    return Image.fromarray(arr)


def champion_catalog() -> tuple[str, list[dict]]:
    version = http_json(f"{DDRAGON}/api/versions.json")[0]
    en = http_json(f"{DDRAGON}/cdn/{version}/data/en_US/champion.json")["data"]
    fr = http_json(f"{DDRAGON}/cdn/{version}/data/fr_FR/champion.json")["data"]
    champs = []
    for alias, c in sorted(en.items()):
        champs.append({
            "alias": alias,                    # e.g. "MonkeyKing" (matches Live Client rawChampionName)
            "key": int(c["key"]),              # numeric champion id
            "name_en": c["name"],
            "name_fr": fr.get(alias, c)["name"],
            "tags": c.get("tags", []),
        })
    return version, champs


def hud_circle_files(alias: str) -> list[str]:
    try:
        names = list_dir(f"{CDRAGON}/game/assets/characters/{alias.lower()}/hud")
    except Exception:
        return []
    return [n for n in names if n.lower().endswith(".png") and "circle" in n.lower()
            and "semicircle" not in n.lower()]


def pick_base_and_skins(alias: str, files: list[str]) -> tuple[str | None, dict[int, str]]:
    a = alias.lower()
    skins: dict[int, str] = {}
    base = None
    for f in files:
        m = re.fullmatch(rf"{re.escape(a)}_circle_(\d+)\.png", f.lower())
        if m:
            n = int(m.group(1))
            if n == 0:
                base = f
            else:
                skins[n] = f
    if base is None:
        for f in files:
            if f.lower() == f"{a}_circle.png":
                base = f
                break
    if base is None:  # legacy names, e.g. chronokeeper_circle.png (Zilean)
        for f in files:
            if re.fullmatch(r"[a-z]+_circle\.png", f.lower()):
                base = f
                break
    return base, skins


def fetch_champion(champ: dict, version: str, with_skins: bool) -> dict:
    alias = champ["alias"]
    files = hud_circle_files(alias)
    base, skins = pick_base_and_skins(alias, files)
    out = {"alias": alias, "base_source": None, "skins": []}
    hud = f"{CDRAGON}/game/assets/characters/{alias.lower()}/hud"
    base_bytes = None
    if base:
        try:
            base_bytes = http_get(f"{hud}/{base}")
            out["base_source"] = "cdragon:" + base
        except Exception:
            base_bytes = None
    if base_bytes is None:
        sq = http_get(f"{DDRAGON}/cdn/{version}/img/champion/{alias}.png")
        buf = io.BytesIO()
        circle_mask_square_icon(sq).save(buf, format="PNG")
        base_bytes = buf.getvalue()
        out["base_source"] = "ddragon-square"
    save_png(base_bytes, RUNTIME_DIR / "icons" / "champions" / f"{alias}.png", RUNTIME_ICON_SIZE)
    if with_skins:
        save_png(base_bytes, TRAIN_DIR / "champions" / alias / "0.png")
        for n, f in sorted(skins.items()):
            dest = TRAIN_DIR / "champions" / alias / f"{n}.png"
            if dest.exists():
                out["skins"].append(n)
                continue
            try:
                save_png(http_get(f"{hud}/{f}"), dest)
                out["skins"].append(n)
            except Exception:
                pass
    return out


def fetch_simple(names: list[str], base_url: str, dest_dir: Path, required: bool = False) -> list[str]:
    got = []
    for n in names:
        dest = dest_dir / n
        if dest.exists():
            got.append(n)
            continue
        try:
            save_png(http_get(f"{base_url}/{n}"), dest)
            got.append(n)
        except urllib.error.HTTPError as e:
            if required:
                raise
            print(f"  skip {n}: HTTP {e.code}")
    return got


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime", action="store_true", help="only runtime assets (no skins)")
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()
    with_skins = not args.runtime

    print("Champion catalog ...")
    version, champs = champion_catalog()
    print(f"  patch {version}: {len(champs)} champions")

    print("Minimap textures ...")
    map_dir = f"{CDRAGON}/game/assets/maps/info/map11"
    names = list_dir(map_dir)
    textures = sorted(n for n in names if MAP_TEXTURE_RE.match(n))
    fogs = sorted(n for n in names if FOG_RE.match(n))
    fetch_simple(textures, map_dir, RUNTIME_DIR / "minimap", required=True)
    fetch_simple(fogs, map_dir, RUNTIME_DIR / "minimap", required=True)
    print(f"  {len(textures)} textures, {len(fogs)} fog overlays")

    print("Minimap icons & pings ...")
    icons = fetch_simple(MINIMAP_ICONS, f"{CDRAGON}/game/assets/ux/minimap/icons", RUNTIME_DIR / "icons" / "minimap")
    pings = fetch_simple(PINGS, f"{CDRAGON}/game/assets/ux/minimap/pings", RUNTIME_DIR / "icons" / "pings")
    print(f"  {len(icons)} icons, {len(pings)} pings")

    print(f"Champion circle icons ({'with' if with_skins else 'without'} skins) ...")
    results = []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_champion, c, version, with_skins): c for c in champs}
        for i, fut in enumerate(cf.as_completed(futs), 1):
            c = futs[fut]
            try:
                results.append(fut.result())
            except Exception as e:  # keep going, report at the end
                print(f"  FAILED {c['alias']}: {e}")
            if i % 25 == 0:
                print(f"  {i}/{len(champs)}")
    by_alias = {r["alias"]: r for r in results}
    index = []
    for c in champs:
        r = by_alias.get(c["alias"])
        if r is None:
            continue
        index.append({**c, "icon": f"{c['alias']}.png", "icon_source": r["base_source"]})
    idx_path = RUNTIME_DIR / "icons" / "champions" / "index.json"
    idx_path.write_text(json.dumps({"patch": version, "champions": index}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    manifest = {
        "patch": version,
        "textures": textures,
        "fogs": fogs,
        "minimap_icons": icons,
        "pings": pings,
        "champions": len(index),
        "fallback_square_icons": [r["alias"] for r in results if r["base_source"] == "ddragon-square"],
    }
    (RUNTIME_DIR / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    if with_skins:
        n_skins = sum(len(r["skins"]) for r in results)
        (TRAIN_DIR / "skins.json").write_text(json.dumps({r["alias"]: r["skins"] for r in results}, indent=0))
        print(f"  training icons: {len(results)} champions, {n_skins} extra skins")
    print(f"Done. {len(index)}/{len(champs)} champions indexed; "
          f"{len(manifest['fallback_square_icons'])} used square-portrait fallback.")
    return 0 if len(index) == len(champs) else 1


if __name__ == "__main__":
    sys.exit(main())
