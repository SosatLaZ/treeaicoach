"""CALL EVALUATION on the recorded games: was each call followed, what followed, which kinds get quiet.

    python tools/call_eval_truth.py <games dir>

Per game (``call_eval.evaluate_record``): the checkable calls (danger / gank alerts, written tips
with an intent, logged macro calls), followed or not, death / objective / gold swing after.
Then replays the games in order through ``call_eval.update_stats`` (temporary folder) and prints
the cooldown scales the app would hold after the last game.
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
            allev += ev
            sl = CE.summary_line(ev)
            print(f"  {os.path.basename(f)[:30]:30s} {sl[0]:24s} {sl[1]}")
            CE.update_stats(tmp, os.path.basename(f), ev, order=i)
        print(f"\n{len(allev)} checkable calls, {sum(e['useful'] for e in allev)} useful")
        print("kind                  n  followed  died|followed  died|ignored  obj|f  gold|f  gold|i  quiet?")
        tot = CE.totals({"games": {"all": {"kinds": CE._agg_game(allev)}}})
        for k, s in sorted(tot.items(), key=lambda x: -x[1]["n"]):
            xs = [e for e in allev if e["kind"] == k]
            fo, ig = [e for e in xs if e["followed"]], [e for e in xs if not e["followed"]]

            def mean(v):
                v = [x for x in v if x is not None]
                return f"{sum(v) / len(v):+5.0f}" if v else "    -"
            print(f"  {k:20s}{s['n']:4d}  {s['followed']:4d}      {s['died_f']:3d}/{len(fo):<4d}     "
                  f"{s['died_i']:3d}/{len(ig):<4d}   {sum(e['objective'] for e in fo):3d}  "
                  f"{mean([e['gold'] for e in fo])}  {mean([e['gold'] for e in ig])}  {CE.quiet_reason(s) or ''}")
        d = CE.load_stats(tmp)
        print("Scales after the last game:", d.get("scale") or "none")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
