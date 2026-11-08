"""TAINT: nothing TreeAI does may be blocked by the game (« Échec des actions d'interface » in the AddOns list).

The strict mock (tests/wow/wowmock.lua) marks Blizzard protected frames / nameplates / protected functions and
records every frame op the real client would block in combat (M.blocked). Harness scenario on both API flavours
+ a combat-heavy 60-minute session (target switches, plates, loot in combat) for HUNTER / MAGE / WARRIOR ×
modern / forever with a budget of 0 blocked actions. Fake names only.
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
HL = (ADDON / "Modules" / "Highlight.lua").read_text(encoding="utf-8")
CORE = (ADDON / "Core.lua").read_text(encoding="utf-8")
SIMS = [(c, f) for c in ("HUNTER", "MAGE", "WARRIOR") for f in ("modern", "forever")]

lua = pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 not installed")


def test_highlight_never_anchors_on_protected_blizzard_frames():
    assert not re.search(r"SetPoint\([^)]*\bTF\b", HL), "badge must not be anchored on TargetFrame"
    assert "FrameLocked" in HL and "softHide" in HL
    assert "function TreeAI:FrameLocked" in CORE and "blockedTop" in CORE


@lua
@pytest.mark.parametrize("flavor", ["forever", "modern"])
def test_taint_scenario(flavor):
    r = subprocess.run(["lua5.1", str(HERE / "harness.lua"), "-q", "--flavor", flavor, "--class", "HUNTER",
                        "--scenario", str(HERE / "taint_scenario.lua"), str(ADDON)],
                       cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-4000:] + r.stderr[-2000:]


def _sim(combo):
    return subprocess.Popen(["lua5.1", str(HERE / "session_sim.lua"), "--class", combo[0], "--flavor", combo[1],
                             "--profile", "tout", "--addon", str(ADDON)], cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)


@pytest.fixture(scope="module")
def sims():
    # under pytest-xdist this module fixture exists once per worker: each test runs only its own session
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return {}
    procs = {combo: _sim(combo) for combo in SIMS}
    return {combo: p.communicate(timeout=900)[0] for combo, p in procs.items()}


@lua
@pytest.mark.parametrize("cls,flavor", SIMS)
def test_session_zero_blocked(sims, cls, flavor):
    if (cls, flavor) not in sims:
        sims[(cls, flavor)] = _sim((cls, flavor)).communicate(timeout=900)[0]
    out = sims[(cls, flavor)]
    m = re.search(r"blocked actions: (\d+) \(budget 0\)", out)
    assert m and int(m.group(1)) == 0, out[-4000:]
    res = re.search(r"RESULT \w+ session .* errors=(\d+) swallowed=(\d+) violations=(\d+)", out)
    assert res and res.groups() == ("0", "0", "0"), out[-4000:]
