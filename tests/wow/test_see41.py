"""4.1 INTEG — « what the player sees » (tests/wow/see41_scenario.lua): the SCREEN405 moment (WoW: Forever, level-29
hunter, group of 4, Les Tarides, quests finished in five zones) on the real-client geometry. On screen by default only
the button strip, the compass and the minimap button (nothing over the party / player / target frames, the tracker or
the minimap); the strip labels and counts come from T.QuestBrain; the compass box, the Quêtes page « Prochaine étape »
and stop 1 of the route say the same thing at the same distance (one stop per place); the Quêtes page starts with the
4 modes; the Groupe page has « Équipement du groupe »; nothing in the chat. SEE41=1 prints the dump (research/see41.md)."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
ADDON = ROOT / "wow_addon" / "TreeAI"
ALLOW = ["LibQuestieDB", "QuestDB", "NpcDB", "ObjectDB", "ItemDB", "TreeAIPlan", "IsQuestFlaggedCompleted", "QuestieLoader",
         "ShortestPathForever", "ShortestPathForeverDB"]


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["forever", "modern"])
def test_see41_scenario(flavor: str) -> None:
    cmd = ["lua5.1", str(HERE / "harness.lua"), "-q", "--no-storm", "--geometry", "ready=1", "--profile", "sur",
           "--flavor", flavor, "--class", "HUNTER", "--level", "29"]
    for a in ALLOW:
        cmd += ["--allow", a]
    cmd += ["--scenario", str(HERE / "see41_scenario.lua"), str(ADDON)]
    env = dict(os.environ)
    env.pop("TREEAI_WOW_FORCE_FLAVOR", None)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=240, cwd=ROOT, env=env, check=False)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-6000:] + r.stderr[-2000:]
