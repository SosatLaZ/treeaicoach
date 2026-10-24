"""PERSONAL WIN CONDITIONS on the recorded games: which conditions separate my wins from my losses.

    python tools/win_conditions_truth.py <games dir>        (records *.json + optional truth/*.truth.json)

Per game: ``progress.game_metrics`` (same numbers the app caches) -> ``win_conditions`` features.
Prints each condition's best threshold with the win rate when met / not met (counts), the
champion / role win rates, and the "focus du soir" the app would pick for each role.
"""

from __future__ import annotations

import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treeaicoach import progress  # noqa: E402
from treeaicoach import win_conditions as WC  # noqa: E402


def load(games: str) -> list[dict]:
    rows = []
    for f in sorted(glob.glob(os.path.join(games, "*.json"))):
        b = os.path.basename(f)
        if b.endswith((".truth.json", ".partial.json")) or b == progress.CACHE_NAME:
            continue
        try:
            rec = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        tf = os.path.join(games, "truth", b.replace(".json", ".truth.json"))
        truth = json.load(open(tf, encoding="utf-8")) if os.path.exists(tf) else None
        m = progress.game_metrics(rec, truth)
        if m:
            m["_file"] = b
            rows.append(m)
    return rows


def main() -> None:
    games = sys.argv[1] if len(sys.argv) > 1 else "."
    rows = load(games)
    use = WC.usable(rows)
    wins = sum(1 for r in use if str(r["result"]).lower() == "win")
    print(f"{len(use)} usable games ({wins} wins) of {len(rows)} records")
    keys = [c[0] for c in WC.CONDITIONS.values()]
    for r in use:
        print(f"  {r['_file'][:28]:28s} {r.get('position', ''):8s} {r['result']:4s} "
              + " ".join(f"{k[3:]}={r.get(k)}" for k in keys))
    print("\nCondition (best threshold)            met: wins/games   not met: wins/games   gap   fail%")
    for e in WC.effects(rows):
        print(f"  {e.label:36s} {e.w_met:3d}/{e.n_met:<3d}            {e.w_not:3d}/{e.n_not:<3d}"
              f"              {e.gap:+.2f}  {100 * e.fail_rate:3.0f}%{'  live' if e.live else ''}")
    print("\nChampion / role:", ", ".join(f"{k} {w}/{n}" for k, w, n in WC.champion_rates(rows)))
    for role in ("TOP", "UTILITY", "BOTTOM", ""):
        f = WC.pick_focus(rows, role)
        print(f"Focus {role or 'any':8s}: {f.text + ' (' + f.why + ')' if f else 'none'}")
    # honest check: leave-one-out - does the focus picked WITHOUT a game predict that game?
    hit = n = 0
    for i, r in enumerate(use):
        f = WC.pick_focus(use[:i] + use[i + 1:], r.get("position"))
        ev = WC.evaluate(f, r)
        if ev is None:
            continue
        n += 1
        hit += int(ev[0] == (str(r["result"]).lower() == "win"))
    print(f"Leave-one-out: focus held/failed matches the result in {hit}/{n} games")


if __name__ == "__main__":
    main()
