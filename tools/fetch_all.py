"""Regenerate every game data table of TreeAI Coach for the live patch, then audit them.

One command after a League patch::

    python tools/fetch_all.py              # download (cached), rebuild, validate
    python tools/fetch_all.py --offline    # rebuild from the download cache only
    python tools/fetch_all.py --icons      # also refresh the champion icons / index (fetch_assets --runtime)

Steps (each one is also a standalone tool):

1. ``fetch_items.py``  - ``assets/items.json``: Data Dragon fr_FR + en_US items, CLASSIC shop
   filter (game files), semantic flags;
2. ``fetch_assets.py --runtime`` - champion icons + ``icons/champions/index.json``, only with
   ``--icons`` or when the live champion list has champions the bundled index does not know;
3. ``fetch_meta.py``   - ``assets/champion_meta.json``: champion profiles with provenance;
4. ``fetch_map.py``    - ``assets/ward_spots.json``: Faelight / camp positions from the game files;
5. ``fetch_builds.py`` - ``assets/item_builds.json``: build paths / counters / boots / starts;
6. ``validate_data.py`` - the audit (exit code 1 when it finds an ERROR).

The curated inputs stay in the repository: ``tools/data/champion_curation.json`` (profiles),
``assets/matchups.json`` (lane tips), the classic ward spots of ``assets/ward_spots.json`` and
``assets/objectives.json`` (timers, checked against the 2026 rules by the audit). The app itself
also refreshes the item / champion tables at runtime (``treeaicoach.game_data``) and works offline
with the bundled tables.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import datalib, fetch_builds, fetch_items, fetch_map, fetch_meta, validate_data  # noqa: E402


def _step(name: str) -> float:
    print(f"\n== {name}")
    return time.monotonic()


def _need_icons(offline: bool) -> list[str]:
    """Live champions missing from the bundled index (they need an icon and an index entry)."""
    try:
        version = datalib.latest_version(offline)
        live = set(datalib.ddragon(version, "champion", "en_US", offline))
        idx = json.loads((datalib.ASSETS / "icons" / "champions" / "index.json").read_text(encoding="utf-8"))
        return sorted(live - {str(c.get("alias")) for c in idx.get("champions") or []})
    except Exception as exc:   # noqa: BLE001
        print(f"  champion list unavailable ({type(exc).__name__})")
        return []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--offline", action="store_true", help="use only the download cache (training/cache/gamedata)")
    ap.add_argument("--icons", action="store_true", help="refresh the champion icons / index (fetch_assets --runtime)")
    ap.add_argument("--no-icons", action="store_true", help="never run fetch_assets, even for a new champion")
    ap.add_argument("--skip-validate", action="store_true")
    args = ap.parse_args(argv)
    t0 = time.monotonic()
    off = ["--offline"] if args.offline else []

    _step("1/6 items (Data Dragon + CLASSIC shop)")
    if fetch_items.main(off) != 0:
        return 2
    _step("2/6 champion icons / index")
    new = _need_icons(args.offline)
    if (args.icons or new) and not args.no_icons and not args.offline:
        if new:
            print(f"  new champions: {', '.join(new)}")
        rc = subprocess.call([sys.executable, str(ROOT / "tools" / "fetch_assets.py"), "--runtime"], cwd=str(ROOT))
        if rc != 0:
            print(f"  fetch_assets exited with {rc}")
    else:
        print("  index up to date" if not new else f"  skipped (new champions {new}: run with --icons online)")
    _step("3/6 champion profiles")
    if fetch_meta.main(off) != 0:
        return 2
    _step("4/6 ward spots / Faelights (game files)")
    rc = fetch_map.main(off)
    if rc != 0:
        print("  ! some ward spots are not on walkable pixels (see above)")
    _step("5/6 builds (from the item data)")
    if fetch_builds.main([]) != 0:
        return 2
    if args.skip_validate:
        print(f"\nDone in {time.monotonic() - t0:.0f} s (audit skipped).")
        return 0
    _step("6/6 audit")
    rc = validate_data.main(["--offline"] if args.offline else [])
    print(f"\nDone in {time.monotonic() - t0:.0f} s.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
