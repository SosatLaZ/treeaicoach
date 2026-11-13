"""60-minute simulated play session, 4 TreeAI clients in a group (tests/wow/session_sim.lua).

Zero Lua errors (also those swallowed by the addon's pcall), zero rule violations, CPU per module,
« Maintenant » churn, corner alerts, duplicates by meaning, SavedVariables / memory growth, comms rate.
HUNTER, MAGE, WARRIOR × modern / legacy API in the default « essentiel » module profile (what every player
gets: ≤ 1.5 M Lua instructions / min for the whole addon), + HUNTER and MAGE with every module on (« tout »:
≤ 4 M / min), + HUNTER on the faithful WoW: Forever mock (essentiel and tout, same budgets), + PERF4 (4.0.1): HUNTER /
Forever in « mode sûr » (what the player runs: 4.0.0 measured 796 kinstr/min with ~30 modules; 4.3.6: 893 with Groupe,
Conseils pro, Pilote, Courses… in the safe profile; 5.0: ≤ 860, no frame over 4 ms) and 10 minutes of pure idle in
« mode sûr » (nothing happens: event-driven modules ≈ 0, whole addon ≤ 320, 4.0.0 measured 600). The sessions run in
parallel. Fake names only.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
ADDON = ROOT / "wow_addon" / "TreeAI"
COMBOS = [(c, f, "essentiel") for c in ("HUNTER", "MAGE", "WARRIOR") for f in ("modern", "legacy")]
COMBOS += [("HUNTER", "modern", "tout"), ("MAGE", "legacy", "tout")]
# 4.0: the real client too — WoW: Forever on the faithful mock (FIDELITY), every budget judged as on modern
COMBOS += [("HUNTER", "forever", "essentiel"), ("HUNTER", "forever", "tout")]
# PERF4 (4.0.1): « mode sûr » (the default profile) and pure idle (« sur-idle »: --idle --minutes 10)
COMBOS += [("HUNTER", "forever", "sur"), ("HUNTER", "forever", "sur-idle")]
EXTRA = {"sur-idle": ["--profile", "sur", "--idle", "--minutes", "10"]}
# kilo-instructions / simulated minute, whole addon (main client); 3.9.9: +50 for 10 new light modules (session_sim.lua)
# 5.0 STABLE: « sur » re-based on what the safe profile contains today (see session_sim.lua: 4.3.6 measured 893, 5.0
# measures 812), « sur-idle » tightened (4.3.6: 366, 5.0: 295)
BUDGET = {"essentiel": 2450, "tout": 4600, "sur": 860, "sur-idle": 320}

pytestmark = pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 not installed")


def _start(combo):
    prof = EXTRA.get(combo[2], ["--profile", combo[2]])
    return subprocess.Popen(["lua5.1", str(HERE / "session_sim.lua"), "--class", combo[0], "--flavor", combo[1],
                             *prof, "--addon", str(ADDON)], cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)


@pytest.fixture(scope="module")
def sessions():
    """All sessions at once (one process each) — except under pytest-xdist, where the module fixture exists once per
    worker: each test then runs only its own session (the workers are the parallelism, no session runs twice)."""
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return {}
    procs = {combo: _start(combo) for combo in COMBOS}
    out = {}
    for combo, p in procs.items():
        so, _ = p.communicate(timeout=900)
        out[combo] = (p.returncode, so)
    return out


@pytest.mark.parametrize("cls,flavor,profile", COMBOS)
def test_session(sessions, cls, flavor, profile):
    combo = (cls, flavor, profile)
    if combo not in sessions:
        p = _start(combo)
        so, _ = p.communicate(timeout=900)
        sessions[combo] = (p.returncode, so)
    rc, out = sessions[combo]
    assert rc == 0 and "RESULT ok" in out, out[-6000:]
    m = re.search(r"total addon\s+(\d+) kinstr/min", out)
    assert m and int(m.group(1)) <= BUDGET[profile], out[:1500]
