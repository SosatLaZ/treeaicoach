"""Refresh the map data of ``treeaicoach/assets/ward_spots.json`` from the game files.

The Summoner's Rift placeables of the live patch (CommunityDragon
``game/data/maps/mapgeometry/map11/base_srx.materials.bin.json``) give the exact position of the
12 Faelight pads (``MapGroup`` ``FaerieLightsGroup_<name>``) and of the jungle camps / epic pits.
This tool:

* stores them in the ``map`` section (``faelights``: name -> game x / y + uv; ``camps``);
* moves every Faelight ward spot (``"game": "<name>"``) to the exact position of its pad and
  updates ``after_rift`` (the ``SplitPush`` pads appear after the Elemental Rift transformation);
* checks every spot against the official minimap texture (walkable pixel after a 5x5 erosion,
  the rule of ``tests/test_voice_gate_wards.py``) and reports the ones that are not.

The classic (non-Faelight) spots are curated in the JSON file itself. Usage:
``python tools/fetch_map.py [--offline]``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import datalib  # noqa: E402

OUT = datalib.ASSETS / "ward_spots.json"
TEXTURE = datalib.ASSETS / "minimap" / "2dlevelminimap_base_baron1.png"


def walkable_mask():
    """Boolean walkable mask (512 x 512) of the official minimap texture, eroded 5x5; None without cv2."""
    try:
        import cv2
        import numpy as np
    except Exception:   # noqa: BLE001 - tools run without the runtime deps in some setups
        return None
    tex = cv2.imread(str(TEXTURE))
    if tex is None:
        return None
    return cv2.erode((tex.max(axis=2) > 40).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0


def update(data: dict, geo: dict, version: str, log=print) -> dict:
    fae = datalib.faelights(geo)
    camps = datalib.camps(geo)
    if len(fae) != 12:
        log(f"  ! {len(fae)} Faelight groups in the game files (12 expected)")
    for spot in data.get("spots") or []:
        name = spot.get("game")
        if not name:
            continue
        f = fae.get(name)
        if f is None:
            log(f"  ! spot {spot.get('id')}: Faelight {name} not in the game files")
            continue
        spot["uv"] = [round(x, 4) for x in f["uv"]]
        spot["game_xy"] = [f["x"], f["y"]]
        spot["after_rift"] = bool(f["after_rift"])
        spot["faelight"] = True
    data["map"] = {"source": "CommunityDragon base_srx.materials.bin (map placeables of the live patch), "
                             "tools/fetch_map.py", "faelights": fae, "camps": camps}
    data["version"] = version
    data["patch"] = datalib.patch_of(version)
    data["generated"] = datalib.today()
    return data


def check(data: dict) -> list[str]:
    mask = walkable_mask()
    if mask is None:
        return ["walkability not checked (cv2 / texture unavailable)"]
    out = []
    for s in data.get("spots") or []:
        u, v = s["uv"]
        if not mask[int(v * 512), int(u * 512)]:
            out.append(f"{s['id']} at ({u:.3f}, {v:.3f}) is not on a walkable pixel")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--offline", action="store_true", help="use only the download cache")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    data = datalib.read_asset("ward_spots.json")
    geo = datalib.get_json(datalib.MAP11_PLACEABLES, offline=args.offline, max_age_s=86400.0)
    version = datalib.latest_version(args.offline)
    data = update(data, geo, version)
    problems = check(data)
    datalib.write_json(Path(args.out), data, indent=1)
    n_fae = sum(1 for s in data["spots"] if s.get("faelight"))
    print(f"{len(data['spots'])} ward spots ({n_fae} Faelights at their game-file position), "
          f"{len(data['map']['camps'])} camps -> {args.out}")
    for p in problems:
        print("  !", p)
    return 1 if any("not on a walkable" in p for p in problems) else 0


if __name__ == "__main__":
    sys.exit(main())
