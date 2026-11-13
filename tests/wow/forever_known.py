"""Known REAL bugs the faithful Forever mock finds (research/fidelity.md « Bugs found »): one id, one output signature.

A harness / session_sim run on the « forever » flavour is classified by its problem lines (ERROR / VIOLATION / LEAK /
SAVEDVARS / FAIL): every line explained by a finding below = the run is a known failure (xfail, with the ids); any
other line = a NEW failure (the test fails). When the integrator fixes a finding, its signature stops appearing:
delete its entry here and its xfail in research/fidelity.md.

  python tests/wow/forever_known.py < harness_output.txt
      exit 0: clean · exit 3: only known findings (prints « XFAIL F1,F4 ») · exit 1: unexplained problems (printed)
"""
from __future__ import annotations

import re
import sys

# id -> (regex on one problem line, what the player sees in game)
KNOWN: dict[str, tuple[str, str]] = {
    # 4.0 integration: every finding of research/fidelity.md § 3 is fixed or its module is gone —
    #   F1 Missions TargetInfo (module merged into Group: names through pub/clean, no target read in combat),
    #   F2 Edge double spé (Edge removed by CLEAN), F3 Edge2 drinkSmart (removed by CLEAN), F4 Stats damage per fight
    #   (player health followed out of combat, Stats.lua SnapHealth), F5 / F6 PvPMark (C_PvP.GetZonePVPInfo, _ns on
    #   UnitIsPVP), F7 Fight pet aggro (removed by CLEAN), F8 Fight killer name (read on the target before the pull),
    #   F9 Binds / PvP macro index (Macros.MacroIndex: one GetMacroInfo scan, cached until UPDATE_MACROS),
    #   F10 UnitIsAFK in chat lockdown (Stats / Group / Detect / QuickTravel guard secrets), F11 Compass (our own
    #   Blizzard waypoint removed when Suivi Forever guides). A new real bug found by the faithful mock goes here
    #   with its signature until it is fixed.
}


# « E   » = pytest failure text; the first line of an assertion message carries « AssertionError: » / « Failed: »
_PROBLEM = re.compile(r"^(?:E\s+)?(?:(?:AssertionError|Failed):\s+)?((?:ERROR|VIOLATION|LEAK|SAVEDVARS|GLYPH)\b.*|FAIL .*)$")
# session_sim instruction / GC budgets (« FAIL hitch: », « FAIL gc: », « FAIL cpu: ») — 4.0: judged on forever too
# (the real client; test_session_sim.py runs HUNTER on forever with the modern budgets) : never exempted here.
_PERF = re.compile(r"^FAIL (?:hitch|gc|cpu): ")
JUDGE_PERF_ON_FOREVER = True
_RESULT = re.compile(r"RESULT \w+ addon=\S+ flavor=forever\b.*?errors=(\d+) violations=(\d+) leaks=(\d+) savedvars=(\d+)(?: glyphs=(\d+))?")
_RESULT_SIM = re.compile(r"RESULT \w+ session class=\S+ flavor=forever errors=(\d+) swallowed=(\d+) violations=(\d+) "
                         r"fails=(\d+)")


def problems(output: str) -> list[str]:
    """Problem lines of a harness / session_sim output (also inside a pytest failure text: « E   VIOLATION … »)."""
    out = []
    for ln in output.splitlines():
        m = _PROBLEM.match(ln.strip())
        if m:
            out.append(m.group(1).split("stack traceback:")[0].strip())
    return out


def classify(output: str, session: bool = False):
    """(sorted known ids, unexplained problem lines); session=True: a session_sim output (perf budgets not judged)"""
    ids, rest = set(), []
    for p in problems(output):
        if session and _PERF.match(p) and not JUDGE_PERF_ON_FOREVER:
            continue
        hit = [i for i, (rx, _) in KNOWN.items() if re.search(rx, p)]
        if hit:
            ids.update(hit)
        else:
            rest.append(p)
    return sorted(ids, key=lambda s: int(s[1:])), rest


def explained_forever_failure(text: str):
    """For a FAILED test whose text holds a forever harness output: the finding ids when EVERY problem of the run is a
    known one and the output is complete (as many problem lines as the RESULT counts) — else None."""
    m = _RESULT.search(text)
    session = m is None
    if m:
        probs = [p for p in problems(text) if not p.startswith("FAIL ")]
    else:
        m = _RESULT_SIM.search(text)   # session_sim: ERROR / ERROR swallowed / VIOLATION / FAIL lines (LEAKs too)
        if not m:
            return None
        probs = [p for p in problems(text) if not p.startswith("LEAK")]
    total = sum(int(x) for x in m.groups() if x is not None)
    if total == 0 or len(probs) < total:   # truncated output: unknown lines could hide in the cut part
        return None
    ids, rest = classify(text, session=session)
    if rest or not ids:
        return None
    return ids


def main() -> int:
    ids, rest = classify(sys.stdin.read())
    if rest:
        print("NEW (not in research/fidelity.md):")
        for p in rest[:40]:
            print("  " + p[:300])
        return 1
    if ids:
        print("XFAIL " + ",".join(ids) + " (research/fidelity.md)")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
