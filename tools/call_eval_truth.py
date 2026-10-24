"""CALL EVALUATION on the recorded games: was each call followed, what followed, which kinds get quiet.

    python tools/call_eval_truth.py <games dir>

Per game (``call_eval.evaluate_record``): the checkable calls (danger / gank alerts, written tips
with an intent, logged macro calls), followed or not, death / objective / gold swing after.
Then replays the games in order through ``call_eval.update_stats`` (temporary folder) and prints
the cooldown scales the app would hold after the last game.

``gold|f`` / ``gold|i`` are the app's own measure (Tab scoreboard team gold difference, 60 s
later): an item-price ESTIMATE, off by 1.5-3 k gold on these games, so its 60 s swing is mostly
noise. When the game has its LCU truth (``truth/<record>.truth.json``), ``tg|f`` / ``tg|i`` give
the TRUE team gold difference swing over :data:`TRUTH_GOLD_S` (per-minute frames) and ``cs|f`` /
``cs|i`` my creep score over the same time (the farm a call makes me give up).
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treeaicoach import call_eval as CE  # noqa: E402
from treeaicoach import ground_truth as GT  # noqa: E402

TRUTH_GOLD_S = 120.0


def _truth_gold(truth: dict | None):
    """``f(t) -> true team gold difference (mine - theirs) at the frame nearest t`` or None."""
    if not truth:
        return None
    tr = GT._Truth(truth)
    if not tr.frames:
        return None
    sign = {pid: (1 if p.get("team") == tr.my_team else -1) for pid, p in tr.parts.items()}

    def at(t: float) -> float | None:
        ft, row = min(tr.frames, key=lambda fr: abs(fr[0] - t))
        if abs(ft - t) > 40.0:
            return None
        return sum(float(r[2]) * sign.get(int(k), 0) for k, r in row.items() if isinstance(r, list) and len(r) > 2)
    return at


def _cs_fn(rec: dict):
    snaps = sorted((float(s["game_time"]), float(s.get("cs") or 0)) for s in rec.get("snapshots") or []
                   if isinstance(s, dict) and s.get("game_time") is not None)

    def at(t: float) -> float | None:
        if not snaps or t > snaps[-1][0] + 5.0:
            return None
        return min(snaps, key=lambda x: abs(x[0] - t))[1]
    return at


def _enrich(ev: list, rec: dict, truth: dict | None) -> None:
    tg, cs = _truth_gold(truth), _cs_fn(rec)
    for e in ev:
        t = float(e["t"])
        e["tg"] = None
        if tg is not None:
            a, b = tg(t), tg(t + TRUTH_GOLD_S)
            e["tg"] = None if a is None or b is None else b - a
        a, b = cs(t), cs(t + TRUTH_GOLD_S)
        e["cs"] = None if a is None or b is None else b - a


def main() -> None:
    games = sys.argv[1] if len(sys.argv) > 1 else "."
    tmp = tempfile.mkdtemp(prefix="calleval_")
    allev = []
    try:
        for i, f in enumerate(sorted(glob.glob(os.path.join(games, "2*.json")))):
            if f.endswith((".truth.json", ".partial.json")):
                continue
            rec = json.load(open(f, encoding="utf-8"))
            ev = CE.evaluate_record(rec)
            if not ev:
                continue
            tp = os.path.join(games, "truth", os.path.basename(f)[:-5] + ".truth.json")
            _enrich(ev, rec, json.load(open(tp, encoding="utf-8")) if os.path.isfile(tp) else None)
            allev += ev
            sl = CE.summary_line(ev)
            print(f"  {os.path.basename(f)[:30]:30s} {sl[0]:24s} {sl[1]}")
            CE.update_stats(tmp, os.path.basename(f), ev, order=i)
        print(f"\n{len(allev)} checkable calls, {sum(e['useful'] for e in allev)} useful")
        print("kind                  n  followed  died|followed  died|ignored  obj|f  gold|f  gold|i   "
              "tg|f   tg|i  cs|f  cs|i  quiet?")
        tot = CE.totals({"games": {"all": {"kinds": CE._agg_game(allev)}}})
        for k, s in sorted(tot.items(), key=lambda x: -x[1]["n"]):
            xs = [e for e in allev if e["kind"] == k]
            fo, ig = [e for e in xs if e["followed"]], [e for e in xs if not e["followed"]]

            def mean(v):
                v = [x for x in v if x is not None]
                return f"{sum(v) / len(v):+5.0f}" if v else "    -"
            print(f"  {k:20s}{s['n']:4d}  {s['followed']:4d}      {s['died_f']:3d}/{len(fo):<4d}     "
                  f"{s['died_i']:3d}/{len(ig):<4d}   {sum(e['objective'] for e in fo):3d}  "
                  f"{mean([e['gold'] for e in fo])}  {mean([e['gold'] for e in ig])}  "
                  f"{mean([e['tg'] for e in fo])}  {mean([e['tg'] for e in ig])}  "
                  f"{mean([e['cs'] for e in fo])[2:]}  {mean([e['cs'] for e in ig])[2:]}  {CE.quiet_reason(s) or ''}")
        d = CE.load_stats(tmp)
        print("Scales after the last game:", d.get("scale") or "none")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
