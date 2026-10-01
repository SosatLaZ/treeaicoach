"""Hard-case mining for the detection gym: random search over scenario parameters for the
cases the CURRENT system fails worst; the worst ones become fixed seeds of the "hard" suite
(``tools/det_gym_hard.json``, run with ``det_gym.py --suite hard``).

Searched parameters: game kind (laning / bot fight / base siege), minimap size (200-330 px),
JPEG quality, blur, detection rate (3 / 6 / 12 img/s), ring darkness, how often the support
stands ON his ADC, overlay labels and pings rates, pasted glyph count, camera mode and line
width, the side, the custom-skin "me" and the seed (rosters, paths, stack positions).

The main suite stays untouched (stable comparisons); ``--merge`` keeps the previous hard
cases and adds the new worst ones (``--max`` cases in all, the worst first).

    python tools/det_mine.py [--trials 24] [--keep 6] [--seconds 6] [--merge] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import det_gym as G  # noqa: E402
import numpy as np  # noqa: E402

KINDS = {
    "laning": lambda rng: [(float(rng.uniform(2, 5)), "flash", {"who": [int(rng.integers(5, 10))]})],
    "botfight": lambda rng: [
        (0.0, "goto", {"who": [3, 4, 8, 9], "uv": (0.86, 0.86), "spread": float(rng.uniform(0.01, 0.03))}),
        (0.0, "goto", {"who": [6], "uv": (0.8, 0.8), "spread": 0.01, "speed_k": 1.1}),
        (float(rng.uniform(2.5, 4.0)), "mill", {"who": [3, 4, 6, 8, 9], "n": 10,
                                               "spread": float(rng.uniform(0.015, 0.04))}),
        (float(rng.uniform(4.0, 5.5)), "die", {"who": [int(rng.choice([4, 8, 9]))], "respawn": 4.0})],
    "siege": lambda rng: [
        (0.0, "goto", {"who": [5, 6, 7, 8], "uv": (0.80, 0.22), "spread": float(rng.uniform(0.015, 0.035))}),
        (0.0, "goto", {"who": [0, 1, 2], "uv": (0.85, 0.17), "spread": 0.02}),
        (float(rng.uniform(2.0, 4.0)), "mill", {"who": [0, 1, 2, 5, 6, 7, 8], "n": 10, "spread": 0.025})],
}


def sample(rng: np.random.Generator, i: int, seconds: float) -> G.Scenario:
    kind = str(rng.choice(list(KINDS)))
    team = str(rng.choice(["ORDER", "CHAOS"]))
    if kind == "siege":
        team = "CHAOS"                      # (the siege target is the CHAOS base)
    return G.Scenario(
        name=f"hard_{kind}_{i}", seed=int(rng.integers(1, 10 ** 6)), fps=float(rng.choice([3.0, 6.0, 12.0])),
        seconds=seconds, size=int(rng.integers(200, 331)), my_team=team,
        camera=str(rng.choice(["locked", "pan", "jump", "free"])), custom_me=bool(rng.random() < 0.25),
        self_outline=bool(rng.random() < 0.6), jpeg=int(rng.integers(50, 91)),
        blur=float(np.round(rng.uniform(0.3, 1.1), 2)), events=KINDS[kind](rng),
        pings=float(np.round(rng.uniform(0.0, 1.0), 2)), labels=float(np.round(rng.uniform(0.0, 1.5), 2)),
        distractors=int(rng.integers(0, 13)), ring_dark=float(np.round(rng.uniform(0.0, 0.6), 2)),
        duo_close=float(np.round(rng.uniform(0.0, 0.9), 2)), cam_px=int(rng.integers(1, 4)), suite="hard")


def badness(s: dict) -> float:
    """Failure rate of one game (what the user sees, weighted like det_gym.score)."""
    n = max(1, s["n_vis"])
    return ((1.0 - s["rec"]) + (1.0 - s["prec"]) + 3.0 * s["g_live"] / n
            + 2.0 * (s["id_wrong"] + s["team"]) / n + 5.0 * s["idsw"] / n + 10.0 * s["g_dead"] / n
            + s["me_bad"])


def jsonable(sc: G.Scenario) -> dict:
    d = asdict(sc)
    d["events"] = [list(e) for e in sc.events]
    return d


def mine(trials: int, keep: int, seconds: float, seed: int = 0) -> list[tuple[float, dict, dict]]:
    import logging

    from training.real_art import RealArt
    from treeaicoach.champions import get_default_db

    logging.disable(logging.WARNING)
    db, art = get_default_db(), RealArt()
    rng = np.random.default_rng(seed)
    found = []
    G._patch_clock()
    try:
        for i in range(trials):
            sc = sample(rng, i, seconds)
            t0 = time.perf_counter()
            s = G.run_game(sc, db, art).summary()
            b = badness(s)
            found.append((b, jsonable(sc), {k: v for k, v in s.items() if not isinstance(v, dict)}))
            print(f"{i:3d} {sc.name:18s} size {sc.size:3d} fps {sc.fps:4.0f} jpeg {sc.jpeg:2d} dark {sc.ring_dark:.2f} "
                  f"duo {sc.duo_close:.2f} -> bad {b:.3f} (rec {s['rec']:.3f} prec {s['prec']:.3f} "
                  f"g_live {s['g_live']}) {time.perf_counter() - t0:.1f}s", flush=True)
    finally:
        G._unpatch_clock()
        logging.disable(logging.NOTSET)
    found.sort(key=lambda z: -z[0])
    return found[:keep]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=24)
    ap.add_argument("--keep", type=int, default=6)
    ap.add_argument("--max", type=int, default=10)
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    worst = mine(a.trials, a.keep, a.seconds, a.seed)
    cases = [dict(d, mined_badness=round(b, 4)) for b, d, _s in worst]
    if a.merge and G.HARD_FILE.is_file():
        old = json.loads(G.HARD_FILE.read_text(encoding="utf-8")).get("cases", [])
        names = {c["name"] for c in cases}
        cases = sorted(cases + [c for c in old if c["name"] not in names],
                       key=lambda c: -c.get("mined_badness", 0.0))[:a.max]
    print("worst:", [(c["name"], c["mined_badness"]) for c in cases])
    if not a.dry_run:
        G.HARD_FILE.write_text(json.dumps({"about": "hard cases mined by tools/det_mine.py; run with "
                                           "det_gym.py --suite hard", "cases": cases}, indent=1),
                               encoding="utf-8")
        print("written", G.HARD_FILE)


if __name__ == "__main__":
    main()
