"""Compass / Tracker / Predict (agent QUEST-PRO): TOC order, 3.8.2 taint rules (static), Lua scenario on both flavours."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = Path(os.environ.get("TREEAI_WOW_ADDON") or ROOT / "wow_addon" / "TreeAI")
MODULES = ["Compass", "Tracker", "Predict"]
LUA = shutil.which("lua5.1")
needs_lua = pytest.mark.skipif(not LUA, reason="lua5.1 not installed")


def test_toc_order():
    lines = [l.strip() for l in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    q = lines.index("Modules\\Quests.lua")
    for m in MODULES:
        assert lines.index(f"Modules\\{m}.lua") > q, m


@pytest.mark.parametrize("name", MODULES)
def test_no_taint_or_automation(name):
    src = (ADDON / "Modules" / f"{name}.lua").read_text(encoding="utf-8")
    src = "\n".join(l.split("--", 1)[0] for l in src.splitlines())  # code only, not comments
    for bad in ("SelectQuestLogEntry(", "SetSelectedQuest(", "QuestMapFrame", "ShowUIPanel", "SetParent(",
                "WorldMapFrame", "hooksecurefunc", "ImportModule(", "SetSuperTrackedQuestID", "AcceptQuest(",
                "CompleteQuest(", "GetQuestReward(", "AbandonQuest(", "QuestLogPushQuest", "SendChatMessage",
                "ObjectiveTrackerFrame", "QuestWatchFrame", "NamePlate"):
        assert bad not in src, f"{name}: {bad}"


def test_user_waypoint_only_from_pin():
    src = (ADDON / "Modules" / "Compass.lua").read_text(encoding="utf-8")
    # SetUserWaypoint / Navigate only inside M.Pin (called from clicks / slash), never from tickers
    assert src.count("SetUserWaypoint, pt") == 1
    pin = src[src.index("function M.Pin"):src.index("function M.GoTo")]
    assert "SetUserWaypoint" in pin and '"Navigate"' in pin and "AddWaypoint" in pin


@needs_lua
def test_syntax():
    luac = LUA.replace("lua5.1", "luac5.1")
    for m in MODULES:
        assert subprocess.run([luac, "-p", str(ADDON / "Modules" / f"{m}.lua")]).returncode == 0, m


@needs_lua
@pytest.mark.parametrize("flavor", ["modern", "legacy"])
def test_scenario(flavor):
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--flavor", flavor, "--class", "HUNTER", "--scenario",
                        "tests/wow/questpro_scenario.lua", str(ADDON)],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
