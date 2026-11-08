"""4.1 BUGHUNT — less dependence on other addons (research/deps.md). tests/wow/deps_scenario.lua, without Questie /
SPF: the NPC book learns the NPCs the player talks to (quest giver, turn-in, vendor, trainer…), the quest tracker
points a finished quest at its learned turn-in NPC, the highlight marks the monsters that really dropped an objective
item (Stats' loot record) — on both API flavours."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = os.environ.get("TREEAI_WOW_ADDON") or str(ROOT / "wow_addon" / "TreeAI")
HARNESS = ROOT / "tests" / "wow" / "harness.lua"
SCENARIO = ROOT / "tests" / "wow" / "deps_scenario.lua"


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["forever", "modern"])
def test_without_questie_or_spf(flavor):
    env = dict(os.environ)
    env.pop("TREEAI_WOW_FORCE_FLAVOR", None)
    r = subprocess.run(["lua5.1", str(HARNESS), "-q", "--no-storm", "--flavor", flavor, "--scenario", str(SCENARIO), ADDON],
                       capture_output=True, text=True, timeout=240, cwd=ROOT, env=env)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-5000:] + r.stderr[-2000:]
