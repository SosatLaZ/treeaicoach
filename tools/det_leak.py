"""Order-independence check of the detection pipeline (cross-game state leaks).

Runs every game of a suite (or every real-bench image) three ways, each in a fresh forked
process with the detector re-run (no tape replay): the whole sequence in order, the reversed
sequence, and every unit alone. Any unit whose metrics differ between the three is printed:
some state survived from one game / image to the next (module-level cache, class attribute).

    python tools/det_leak.py holdout            # a det_gym suite name, or "real"
    python tools/det_leak.py main laning,siege  # only the units whose name contains one of these
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import multiprocessing as mp
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import det_fast as F  # noqa: E402
import det_gym as G  # noqa: E402


def _seq(args: tuple) -> list:
    units, mode = args
    F._worker_init()
    F._ctx()
    out = []
    for k, pl in units:
        r = F._unit(k, pl, mode)
        if k == "game":
            out.append((pl[1].name, json.dumps(F.metrics_only([r["M"].summary(), r["M"].fails]),
                                               sort_keys=True, default=str)))
        else:
            out += [(n, json.dumps(F.metrics_only(dataclasses.asdict(v) if dataclasses.is_dataclass(v) else v),
                                   sort_keys=True, default=str)) for n, v in r["res"].items()]
    return out


def main(argv: list[str]) -> int:
    if os.environ.get("PYTHONHASHSEED") != "0":
        os.environ["PYTHONHASHSEED"] = "0"
        os.execv(sys.executable, [sys.executable, *sys.argv])
    what = argv[0] if argv else "holdout"
    F._ctx()
    if what == "real":
        import real_minimap_bench as RB

        units = [("real", (str(fix), [n])) for fix in F.real_fixtures() for n in RB.load_truth(fix)["images"]]
    else:
        units = [("game", (what, sc, None)) for sc in G.suite_scenarios(what, False)]
    if len(argv) > 1:
        units = [u for u in units if any(s in str(u[1][1]) for s in argv[1].split(","))]
    jobs = [(units, "record"), (units[::-1], "record")] + [([u], "record") for u in units]
    with mp.get_context("fork").Pool(3, maxtasksperchild=1) as p:
        res = p.map(_seq, jobs, chunksize=1)
    ref, rev = dict(res[0]), dict(res[1])
    alone: dict = {}
    for r in res[2:]:
        alone.update(dict(r))
    bad = 0
    for n, a in ref.items():
        if not (a == rev[n] == alone[n]):
            bad += 1
            print("DIFF", n, *(hashlib.sha1(x.encode()).hexdigest()[:8] for x in (a, rev[n], alone[n])))
    print(f"{what}: {len(ref)} units, {bad} order-dependent")
    return int(bad > 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
