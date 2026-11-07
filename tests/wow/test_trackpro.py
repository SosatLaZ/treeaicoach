"""Quest tracker (agent TRACK-PRO): cost model, filters, group overlay, plan steps, standalone frame, sound —
Lua scenario on both API flavours + static taint / noise rules (fake names only)."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = Path(os.environ.get("TREEAI_WOW_ADDON") or ROOT / "wow_addon" / "TreeAI")
LUA = shutil.which("lua5.1")
needs_lua = pytest.mark.skipif(not LUA, reason="lua5.1 not installed")


def code(name):
    src = (ADDON / "Modules" / f"{name}.lua").read_text(encoding="utf-8")
    return "\n".join(l.split("--", 1)[0] for l in src.splitlines())


@pytest.mark.parametrize("name", ["Tracker", "Quests"])
def test_never_touches_blizzard_quest_ui(name):
    src = code(name)
    for bad in ("SelectQuestLogEntry(", "SetSelectedQuest(", "QuestMapFrame", "ShowUIPanel", "SetParent(",
                "ObjectiveTrackerFrame", "QuestWatchFrame", "hooksecurefunc", "SetSuperTrackedQuestID",
                "AbandonQuest(", "QuestLogPushQuest", "AddQuestWatch", "RemoveQuestWatch", "SendChatMessage"):
        assert bad not in src, f"{name}: {bad}"


def test_sound_only_on_completion():
    src = code("Tracker")
    assert src.count("PlaySound") == 1
    start = src.index("function M.CheckCompletions")
    body = src[start:src.index("\nend\n", start)]
    assert "PlaySound" in body and 'cfg("sound")' in body


def test_story_never_uses_selection_api():
    src = code("Quests")
    story = src[src.index("function M.Story"):]
    story = story[:story.index("\nend\n")]
    # GetQuestLogQuestText only by index on the modern (Mainline) API, behind C_QuestLog.GetInfo
    assert "GetQuestLogQuestText, q.index" in story and "QL.GetInfo" in story


def test_options_and_contract_documented():
    src = (ADDON / "Modules" / "Tracker.lua").read_text(encoding="utf-8")
    for key in ("standalone", "fontSize", "width", "scale", "alpha", "lock", "sound", "group", "plan", "story", "chain"):
        assert re.search(rf'key = "{key}"', src), key
    readme = (ROOT / "wow_addon" / "README.md").read_text(encoding="utf-8")
    assert "GetActiveSteps" in readme and "TRACK-PRO" in readme


@needs_lua
def test_syntax():
    luac = LUA.replace("lua5.1", "luac5.1")
    for m in ("Tracker", "Quests"):
        assert subprocess.run([luac, "-p", str(ADDON / "Modules" / f"{m}.lua")]).returncode == 0, m


@needs_lua
@pytest.mark.parametrize("flavor", ["modern", "legacy"])
def test_scenario(flavor):
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--flavor", flavor, "--class", "HUNTER", "--scenario",
                        "tests/wow/trackpro_scenario.lua", str(ADDON)],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
