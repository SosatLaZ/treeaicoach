"""60-minute simulated play session, 4 TreeAI clients in a group (tests/wow/session_sim.lua).

Zero Lua errors (also those swallowed by the addon's pcall), zero rule violations, CPU per module,
« Maintenant » churn, corner alerts, duplicates by meaning, SavedVariables / memory growth, comms rate.
HUNTER, MAGE, WARRIOR × modern / legacy API; the 6 sessions run in parallel. Fake names only.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
ADDON = ROOT / "wow_addon" / "TreeAI"
COMBOS = [(c, f) for c in ("HUNTER", "MAGE", "WARRIOR") for f in ("modern", "legacy")]

pytestmark = pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 not installed")


@pytest.fixture(scope="module")
def sessions():
    procs = {
        combo: subprocess.Popen(["lua5.1", str(HERE / "session_sim.lua"), "--class", combo[0], "--flavor", combo[1],
                                 "--addon", str(ADDON)], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True)
        for combo in COMBOS
    }
    out = {}
    for combo, p in procs.items():
        so, _ = p.communicate(timeout=900)
        out[combo] = (p.returncode, so)
    return out


@pytest.mark.parametrize("cls,flavor", COMBOS)
def test_session(sessions, cls, flavor):
    rc, out = sessions[(cls, flavor)]
    assert rc == 0 and "RESULT ok" in out, out[-6000:]
