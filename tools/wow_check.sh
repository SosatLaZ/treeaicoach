#!/usr/bin/env bash
# tools/wow_check.sh — every WoW-side check in one go (run from anywhere).
#   1. luac5.1 -p syntax + global-leak / ToS static checks on every .lua (tests/wow/luacheck.py)
#   2. Lua harness: the addon TOC loaded on the WoW API mock, event storm, three API flavours
#      (modern C_Container/C_ChatInfo…, legacy globals, forever = Forever 1.60.1 rules), Hunter + Mage; fixtures too
#   3. pytest tests/wow (python modules + the above)
# Needs lua5.1 (apt-get install -y lua5.1); without it the Lua parts are skipped with a note.
# Override the addon folder with TREEAI_WOW_ADDON=/path/to/TreeAI. Exit != 0 if anything fails.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 2
PY="${PYTHON:-python}"
ADDON="${TREEAI_WOW_ADDON:-$ROOT/wow_addon/TreeAI}"
fail=0

echo "== 1/3 luacheck (syntax, globals, ToS)"
roots=("$ROOT/tests/wow")
[ -d "$ROOT/wow_addon" ] && roots+=("$ROOT/wow_addon")
[ "$ADDON" != "$ROOT/wow_addon/TreeAI" ] && [ -d "$ADDON" ] && roots+=("$ADDON")
"$PY" tests/wow/luacheck.py "${roots[@]}" || fail=1

echo "== 2/3 Lua harness"
if command -v lua5.1 >/dev/null 2>&1; then
  run_h() {  # label, args…
    local label="$1"; shift
    local out rc
    out="$(lua5.1 tests/wow/harness.lua -q "$@" 2>&1)"; rc=$?
    echo "$out" | grep -E '^(RESULT|ERROR|VIOLATION|LEAK|SAVEDVARS)' | sed "s|^|  [$label] |"
    # FIDELITY: on the faithful Forever mock, a run failing ONLY on known real bugs (tests/wow/forever_known.py,
    # research/fidelity.md) is an expected failure until the addon is fixed; anything else still fails
    if [ $rc -ne 0 ] && [[ "$label" == *forever* ]]; then
      local known
      known="$(echo "$out" | "$PY" tests/wow/forever_known.py)"
      if [ $? -eq 3 ]; then echo "  [$label] $known"; return 0; fi
    fi
    return $rc
  }
  for fl in modern legacy; do
    run_h "sample/$fl" --flavor "$fl" --scenario tests/wow/fixtures/SampleAddon/scenario.lua \
      tests/wow/fixtures/SampleAddon || fail=1
  done
  if run_h "leaky(expected fail)" tests/wow/fixtures/bad/LeakyAddon >/dev/null; then
    echo "  harness did NOT catch the bad fixture"; fail=1
  else
    echo "  [leaky] bad fixture correctly rejected"
  fi
  if [ -d "$ADDON" ] && ls "$ADDON"/*.toc >/dev/null 2>&1; then
    for fl in modern legacy forever; do
      for cls in HUNTER MAGE WARRIOR; do
        run_h "TreeAI/$fl/$cls" --flavor "$fl" --class "$cls" "$ADDON" || fail=1
      done
    done
    # forever = the real WoW Forever 1.60.1 rules: secret values in combat, Era globals removed,
    # COMBAT_LOG_EVENT_UNFILTERED forbidden, macro writes only inside a click / command (+ /tai test)
    # LITE: what a new player gets (profile « essentiel »: most modules asleep, events / tickers unregistered)
    run_h "TreeAI/forever/essentiel/HUNTER" --flavor forever --class HUNTER --profile essentiel "$ADDON" || fail=1
    # REVIEW: « mode sûr » = what every player gets by default since 3.9.9 (bêta modules off)
    run_h "TreeAI/forever/sur/HUNTER" --flavor forever --class HUNTER --profile sur "$ADDON" || fail=1
    for cls in HUNTER WARRIOR; do
      run_h "TreeAI/forever/selftest/$cls" --flavor forever --class "$cls" \
        --scenario tests/wow/selftest_scenario.lua "$ADDON" || fail=1
    done
    echo "  (details: lua5.1 tests/wow/harness.lua $ADDON)"
  else
    echo "  note: $ADDON not present yet: real addon skipped"
  fi
else
  echo "  SKIP: lua5.1 not installed (apt-get install -y lua5.1)"
fi

echo "== fidelity: API of the real Forever client vs the addon and the mock (tests/wow/forever_api_check.py)"
"$PY" tests/wow/forever_api_check.py --check --print >/dev/null || fail=1

echo "== 3/3 pytest tests/wow"
QT_QPA_PLATFORM=offscreen PYTHONPATH=. TREEAI_WOW_ADDON="$ADDON" \
  "$PY" -m pytest -q -p no:cacheprovider tests/wow || fail=1

if [ "$fail" = 0 ]; then echo "WOW CHECK: OK"; else echo "WOW CHECK: FAILED"; fi
exit "$fail"
