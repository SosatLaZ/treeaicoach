"""Auto-tuning of the detection thresholds with the detection gym.

Coordinate search over :data:`treeaicoach.det_params.TUNABLE`: for each parameter (random
order), try values around the current one (x / 1.35, x * 1.35, and the range ends when
``--wide``); a value is kept when the gym score (``det_gym.score``: recall + precision minus
the rates of ghost-on-live, wrong identity / team, identity switches, dead drawn, my position
off) on the TUNE suite (main, quick) improves by more than ``--min-gain``. The final set is
then VALIDATED on the holdout suite (other seeds / rosters / sizes / sides) and the real
crops: it is written to ``treeaicoach/assets/model/det_params.json`` (read by the matcher /
tracker / detector at import) only with ``--write`` AND when the holdout score improves and
no real-crop metric drops. Every evaluation is logged to ``tools/det_tune_log.jsonl``.

    python tools/det_tune.py [--minutes 10] [--params roster_matcher.TRACK_RELAX ...] [--write]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import det_gym as G  # noqa: E402
import numpy as np  # noqa: E402

from treeaicoach import det_params as DP  # noqa: E402

LOG = ROOT / "tools" / "det_tune_log.jsonl"


def evaluate(params: dict, suite: str = "main", quick: bool = True, real: bool = False) -> dict:
    old = {k: DP.get_value(k) for k in params}
    try:
        for k, v in params.items():
            DP.set_value(k, v)
        out = G.run(quick=quick, real=real, suite=suite)
    finally:
        for k, v in old.items():
            DP.set_value(k, v)
    e = G.history_entry(out, note=f"tune {suite}")
    e["params"] = dict(params)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(e, default=float) + "\n")
    return e


def candidates(key: str, cur: float, wide: bool) -> list[float]:
    lo, hi, _k = DP.TUNABLE[key]
    vals = [cur / 1.35 if cur > 0 else lo + 0.25 * (hi - lo), cur * 1.35 if cur > 0 else lo + 0.5 * (hi - lo)]
    if wide:
        vals += [lo, hi]
    out = []
    for v in vals:
        v = float(np.clip(v, lo, hi))
        if abs(v - cur) > 1e-6 and all(abs(v - o) > 1e-6 for o in out):
            out.append(round(v, 4))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=10.0)
    ap.add_argument("--params", nargs="*", help="subset of det_params.TUNABLE keys")
    ap.add_argument("--min-gain", type=float, default=0.002)
    ap.add_argument("--wide", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--write", action="store_true", help="write det_params.json when validated")
    a = ap.parse_args()
    logging.disable(logging.WARNING)
    keys = a.params or list(DP.TUNABLE)
    rng = np.random.default_rng(a.seed)
    keys = [keys[i] for i in rng.permutation(len(keys))]
    t_end = time.time() + 60.0 * a.minutes
    best: dict = {}
    base = evaluate({})
    best_score = base["score"]
    print(f"tune baseline score {best_score:.4f} (rec {base['total']['rec']:.3f} prec {base['total']['prec']:.3f} "
          f"g_live {base['total']['g_live']})", flush=True)
    for key in keys:
        cur = float(best.get(key, DP.get_value(key)))
        for v in candidates(key, cur, a.wide):
            if time.time() > t_end:
                break
            e = evaluate({**best, key: v})
            gain = e["score"] - best_score
            print(f"  {key} = {v:<8g} score {e['score']:.4f} ({gain:+.4f})", flush=True)
            if gain > a.min_gain:
                best[key], best_score = v, e["score"]
        if time.time() > t_end:
            print("time budget reached")
            break
    print("best on tune:", best, f"score {best_score:.4f} vs {base['score']:.4f}")
    if not best:
        print("nothing to validate")
        return
    # ---- validation: holdout suite (full) + real crops, defaults vs best
    h0 = evaluate({}, suite="holdout", quick=False, real=True)
    h1 = evaluate(best, suite="holdout", quick=False, real=True)
    real_ok = all(h1["real"][k] >= h0["real"][k] - 1e-9 for k in ("rec", "prec", "team", "id"))
    ok = h1["score"] > h0["score"] + 1e-4 and real_ok
    print(f"holdout score {h0['score']:.4f} -> {h1['score']:.4f}; real ok {real_ok}; "
          f"regressions {G.regressions(h1, h0)}")
    print("VALIDATED" if ok else "REJECTED (holdout / real)")
    if ok and a.write:
        cur = {}
        try:
            cur = json.loads(DP.PARAMS_FILE.read_text(encoding="utf-8")).get("params", {})
        except (OSError, ValueError):
            pass
        cur.update(best)
        DP.PARAMS_FILE.write_text(json.dumps({
            "params": cur, "validated": {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "rev": G._git_rev(),
                                         "tune_score": best_score, "holdout_before": h0["score"],
                                         "holdout_after": h1["score"]}}, indent=1), encoding="utf-8")
        print("written", DP.PARAMS_FILE)


if __name__ == "__main__":
    main()
