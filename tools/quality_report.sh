#!/usr/bin/env bash
# One run of every test and every measurement, unattended: full logs in $OUT/logs, and a SHORT summary
# ($OUT/summary.txt, the last lines of each tool) - the only file to read afterwards.
# Usage: tools/quality_report.sh <games dir with *.json + truth/> [out dir]
set -u
GAMES="${1:?games dir}"
OUT="${2:-/tmp/treeai_quality}"
cd "$(dirname "$0")/.." || exit 1
rm -rf "$OUT"; mkdir -p "$OUT/logs"
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}" QT_QPA_PLATFORM=offscreen TREEAICOACH_NO_SPLASH=1

run() {  # name, lines kept in the summary, command...
  local name="$1" keep="$2"; shift 2
  local t0=$SECONDS
  timeout 1800 "$@" >"$OUT/logs/$name.log" 2>&1
  local rc=$?
  { echo "=== $name (rc=$rc, $((SECONDS - t0)) s)"; tail -n "$keep" "$OUT/logs/$name.log"; } >>"$OUT/summary.txt"
}

run pytest           4  python -m pytest -q -p no:cacheprovider -n 4 --no-header -q -rf
run ux_replay        6  python tools/ux_replay.py --level all
run real_minimap     8  python tools/real_minimap_bench.py
run real_minimap_tk  8  python tools/real_minimap_bench.py --dir tests/fixtures/real_cases/tk_botlane_20261005
for s in main botlane mirror hard; do run det_gym_$s 6 python tools/det_gym.py --suite $s --no-record; done
run winprob          10 python tools/winprob_fit.py "$GAMES" --truth "$GAMES/truth"
run jungler_map      10 python tools/jungler_map_truth.py "$GAMES"
run fog_intel        10 python tools/fog_intel_truth.py "$GAMES"
run objective        10 python tools/objective_truth.py "$GAMES"
run spike            10 python tools/spike_truth.py "$GAMES"
run win_conditions   10 python tools/win_conditions_truth.py "$GAMES"
run call_eval        10 python tools/call_eval_truth.py "$GAMES"
run team_brain       10 python tools/team_brain_truth.py "$GAMES"
echo "DONE" >>"$OUT/summary.txt"
