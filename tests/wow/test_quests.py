"""Quests module (agent Q): Lua harness scenario on both API flavours + static checks (fake names only)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = Path(os.environ.get("TREEAI_WOW_ADDON") or ROOT / "wow_addon" / "TreeAI")
MODULE = ADDON / "Modules" / "Quests.lua"
LUA = shutil.which("lua5.1")

needs_lua = pytest.mark.skipif(not LUA, reason="lua5.1 not installed")


def test_module_listed_in_toc_after_group():
    lines = [l.strip() for l in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert "Modules\\Quests.lua" in lines
    assert lines.index("Modules\\Quests.lua") > lines.index("Modules\\Group.lua")


def test_no_automation_calls():
    src = MODULE.read_text(encoding="utf-8")
    for bad in ("AbandonQuest(", "SetAbandonQuest", "AcceptQuest(", "CompleteQuest(", "GetQuestReward(",
                "ImportModule(", "hooksecurefunc", "SelectGossip"):
        assert bad not in src, bad
    assert "SecureActionButtonTemplate" in src and "RunOutOfCombat" in src


@needs_lua
def test_syntax():
    assert subprocess.run([LUA.replace("lua5.1", "luac5.1"), "-p", str(MODULE)]).returncode == 0


@needs_lua
@pytest.mark.parametrize("flavor", ["modern", "legacy"])
def test_scenario(flavor):
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--flavor", flavor, "--scenario",
                        "tests/wow/quests_scenario.lua", str(ADDON)],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
