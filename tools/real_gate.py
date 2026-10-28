"""Regression gate on ALL real minimap cases (CI / before every detection commit).

    python tools/real_gate.py                 # run every case, compare with the baseline, exit 1 on a regression
    python tools/real_gate.py --jobs 3        # cases in parallel processes
    python tools/real_gate.py --update        # (after an accepted change) store the new numbers as the baseline

Cases: ``tests/fixtures/real`` (hand-labelled crops) and every ``tests/fixtures/real_cases/*``
folder with a ``ground_truth.json`` (LCU-truth labelled frames, :mod:`tools.real_truth_case`).
Each one is scored by :mod:`tools.real_minimap_bench` (production detector, static replay) and
compared with ``tests/fixtures/real_cases/gate_baseline.json``: a case regresses when its
recall drops by more than :data:`RECALL_TOL` or its precision by more than :data:`PREC_TOL`
(the detection swarm's acceptance rule), or its team / identity accuracy by more than
:data:`ACC_TOL`. The frames whose hits / false positives changed are listed (what to look at).
A case missing from the baseline is reported, never a failure (new case: ``--update``).
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
CASES_DIR = ROOT / "tests" / "fixtures" / "real_cases"
BASELINE = CASES_DIR / "gate_baseline.json"
RECALL_TOL = 0.003
PREC_TOL = 0.005
ACC_TOL = 0.01


def case_dirs() -> dict[str, Path]:
    out = {"real": ROOT / "tests" / "fixtures" / "real"}
    if CASES_DIR.is_dir():
        for d in sorted(CASES_DIR.iterdir()):
            if (d / "ground_truth.json").is_file():
                out[d.name] = d
    return {k: v for k, v in out.items() if (v / "ground_truth.json").is_file()}


def summarize(results: dict) -> dict:
    """Totals + per-frame (tp, false positives) of a ``real_minimap_bench.run_all`` result."""
    T = {"gt": 0, "det": 0, "tp": 0, "team": 0, "idn": 0, "idok": 0}
    frames = {}
    for name, r in results.items():
        T["gt"] += r.n_gt
        T["det"] += r.n_det
        T["tp"] += r.tp
        T["team"] += r.team_ok
        T["idn"] += r.id_n
        T["idok"] += r.id_ok
        frames[name] = [r.tp, r.n_det - r.tp]
    return {"recall": round(T["tp"] / max(1, T["gt"]), 4),
            "precision": round(T["tp"] / max(1, T["det"]), 4),
            "team": round(T["team"] / max(1, T["tp"]), 4),
            "id": round(T["idok"] / max(1, T["idn"]), 4),
            "gt": T["gt"], "frames": frames}


def run_case(path: str) -> dict:
    import real_minimap_bench as RB
    return summarize(RB.run_all(fix=Path(path)))


def compare(cur: dict, base: dict) -> tuple[list[str], list[str]]:
    """(regressions, notes) of the current summaries against the baseline ones."""
    bad, notes = [], []
    for name, c in cur.items():
        b = base.get(name)
        if not b:
            notes.append(f"{name}: not in the baseline (new case)")
            continue
        drops = []
        for key, tol in (("recall", RECALL_TOL), ("precision", PREC_TOL), ("team", ACC_TOL), ("id", ACC_TOL)):
            d = round(float(c[key]) - float(b.get(key, 0.0)), 4)
            if d < -tol:
                drops.append(f"{key} {b[key]:.3f}->{c[key]:.3f}")
            elif d > tol:
                notes.append(f"{name}: {key} {b[key]:.3f}->{c[key]:.3f} (better)")
        if drops:
            worse = [f for f, (tp, fp) in c.get("frames", {}).items()
                     if f in b.get("frames", {}) and (tp < b["frames"][f][0] or fp > b["frames"][f][1])]
            bad.append(f"{name}: " + ", ".join(drops) + (f" (frames: {', '.join(worse[:8])})" if worse else ""))
    return bad, notes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--update", action="store_true", help="write the current numbers as the baseline")
    ap.add_argument("--baseline", default=str(BASELINE))
    ap.add_argument("cases", nargs="*", help="case names (default: all)")
    a = ap.parse_args(argv)
    dirs = case_dirs()
    names = [n for n in dirs if not a.cases or n in a.cases]
    if a.jobs > 1:
        with ProcessPoolExecutor(a.jobs) as ex:
            cur = dict(zip(names, ex.map(run_case, [str(dirs[n]) for n in names])))
    else:
        cur = {n: run_case(str(dirs[n])) for n in names}
    for n, c in cur.items():
        print(f"{n:30s} recall {c['recall']:.3f}  prec {c['precision']:.3f}  team {c['team']:.3f}  id {c['id']:.3f}")
    bpath = Path(a.baseline)
    base = json.loads(bpath.read_text(encoding="utf-8")) if bpath.is_file() else {}
    if a.update:
        base.update(cur)
        bpath.write_text(json.dumps(base, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"baseline written: {bpath}")
        return 0
    bad, notes = compare(cur, base)
    for n in notes:
        print("  note:", n)
    for b in bad:
        print("  REGRESSION:", b)
    print("GATE", "FAIL" if bad else "OK")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
