"""Modules/Team.lua: advice on every party member, even without TreeAI (Blizzard APIs only). Fake names only."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
TEAM = ADDON / "Modules" / "Team.lua"


def test_toc_lists_team_after_missions():
    lines = [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert lines.index("Modules\\Missions.lua") + 1 == lines.index("Modules\\Team.lua")


def test_no_chat_no_automation():
    src = TEAM.read_text(encoding="utf-8")
    for bad in ("SendChatMessage", "QuestLogPushQuest", "CastSpell", "TargetUnit", "SetSelectedQuest",
                "SelectQuestLogEntry", "InspectUnit", ":Print("):
        assert bad not in src, bad


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_syntax_no_globals():
    assert subprocess.run(["luac5.1", "-p", str(TEAM)], capture_output=True).returncode == 0
    listing = subprocess.run(["luac5.1", "-l", "-p", str(TEAM)], capture_output=True, text=True).stdout
    assert "SETGLOBAL" not in listing


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("cls,flavor", [("HUNTER", "modern"), ("HUNTER", "legacy"), ("PRIEST", "forever"),
                                        ("DRUID", "modern")])
def test_team_scenario(cls, flavor):
    r = subprocess.run(["lua5.1", "tests/wow/harness.lua", "-q", "--flavor", flavor, "--class", cls,
                        "--scenario", "tests/wow/team_scenario.lua", str(ADDON)],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
