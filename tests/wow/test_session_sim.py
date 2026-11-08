"""60-minute simulated play session, 4 TreeAI clients in a group (tests/wow/session_sim.lua).

Zero Lua errors (also those swallowed by the addon's pcall), zero rule violations, CPU per module,
« Maintenant » churn, corner alerts, duplicates by meaning, SavedVariables / memory growth, comms rate.
HUNTER, MAGE, WARRIOR × modern / legacy API in the default « essentiel » module profile (what every player
gets: ≤ 1.5 M Lua instructions / min for the whole addon), + HUNTER and MAGE with every module on (« tout »:
≤ 4 M / min). The sessions run in parallel. Fake names only.
"""
from __future__ import annotations

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
BUDGET = {"essentiel": 2450, "tout": 4600}   # 3.9.9: +50 for 10 new light modules (see session_sim.lua)   # kilo-instructions / simulated minute, whole addon (main client)

pytestmark = pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 not installed")


@pytest.fixture(scope="module")
def sessions():
    procs = {
        combo: subprocess.Popen(["lua5.1", str(HERE / "session_sim.lua"), "--class", combo[0], "--flavor", combo[1],
                                 "--profile", combo[2], "--addon", str(ADDON)], cwd=ROOT, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        for combo in COMBOS
    }
    out = {}
    for combo, p in procs.items():
        so, _ = p.communicate(timeout=900)
        out[combo] = (p.returncode, so)
    return out


@pytest.mark.parametrize("cls,flavor,profile", COMBOS)
def test_session(sessions, cls, flavor, profile):
    rc, out = sessions[(cls, flavor, profile)]
    assert rc == 0 and "RESULT ok" in out, out[-6000:]
    m = re.search(r"total addon\s+(\d+) kinstr/min", out)
    assert m and int(m.group(1)) <= BUDGET[profile], out[:1500]
