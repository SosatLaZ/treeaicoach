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
    "F1": (r"Missions\.lua:105\b|Missions: .*Missions\.lua:105",
           "Missions:TargetInfo reads UnitName('target') in combat (secret) and cleans it with :gsub -> Lua error in "
           "every fight with a target: the Missions / Groupe tab stops refreshing (Missions.lua:1351 -> :105)"),
    "F2": (r"\bdualSpec : nil|au moins \d+ avantages vus en action",
           "Edge « double spé » never shown: GetNumTalentGroups does not exist on Forever (Edge.lua:1041)"),
    "F3": (r"\bdrinkSmart : nil",
           "Edge2 « boire ou enchaîner » silent: player HP at the pull is secret in combat (Edge2.lua:466/473)"),
    "F4": (r"dégâts subis .* PV / combat",
           "Stats « dégâts subis par combat » never measured: player HP is secret in combat (Stats.lua:231/241)"),
    "F5": (r"sanctuary: nothing marked",
           "PvPMark marks enemies in sanctuaries: GetZonePVPInfo is C_PvP.GetZonePVPInfo on Forever (PvPMark.lua:95)"),
    "F7": (r"pet aggro: nil",
           "Fight « ton familier a perdu l'aggro » never fires: threat values are secret in combat (Fight CheckPetAggro)"),
    "F8": (r"death recap: Mort — ",
           "Fight death recap cannot name the killer: NPC names / plates are secret at PLAYER_DEAD (Fight.lua:258-273)"),
    "F9": (r"binds_scenario\.lua",
           "Binds never finds the TAI macros: macroIndex() uses only GetMacroIndexByName, absent on Forever "
           "(Binds.lua:186) -> « Touches » plans raw spells, flags spells already covered"),
    "F11": (r"opt-in \+ SPF guiding",
            "Compass: TreeAI's own Blizzard waypoint (« Aller là ») stays when the player switches to Suivi Forever "
            "-> two arrows (Compass.lua:263-273)"),
}


# « E   » = pytest failure text; the first line of an assertion message carries « AssertionError: » / « Failed: »
_PROBLEM = re.compile(r"^(?:E\s+)?(?:(?:AssertionError|Failed):\s+)?((?:ERROR|VIOLATION|LEAK|SAVEDVARS)\b.*|FAIL .*)$")
# session_sim instruction / GC budgets (« FAIL hitch: », « FAIL gc: », « FAIL cpu: ») are calibrated on the modern
# flavour and enforced there by test_session_sim.py; on forever they are printed, not judged (research/fidelity.md § 5)
_PERF = re.compile(r"^FAIL (?:hitch|gc|cpu): ")
_RESULT = re.compile(r"RESULT \w+ addon=\S+ flavor=forever\b.*?errors=(\d+) violations=(\d+) leaks=(\d+) savedvars=(\d+)")
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
        if session and _PERF.match(p):
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
    total = sum(int(x) for x in m.groups())
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
