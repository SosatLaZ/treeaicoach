"""4.3.5 PICKFIX — a « Prendre » stop is done only by the quest log (never by arrival), phantom quests learned at the
giver (offered list / reached twice), and the « Monter vite » mode (QuestBrain fastlvl, /tai vite).
tests/wow/pickfix_scenario.lua, Forever flavour first."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
ADDON = ROOT / "wow_addon" / "TreeAI"
LUA = shutil.which("lua5.1")


@pytest.mark.skipif(not LUA, reason="lua5.1 not installed")
@pytest.mark.parametrize("flavor", ["forever", "modern"])
def test_pickfix_scenario(flavor):
    cmd = [LUA, str(HERE / "harness.lua"), "-q", "--flavor", flavor, "--class", "HUNTER", "--profile", "sur",
           "--scenario", str(HERE / "pickfix_scenario.lua"), str(ADDON)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=ROOT, check=False)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-6000:] + r.stderr[-2000:]
