"""« À faire » (agent TODO): one window + compact quests tab — Lua scenario on both API flavours (layout, filters,
pins, hide, order, buttons, data from mocks) + static taint rules."""
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
SRC = ADDON / "Modules" / "Todo.lua"


def code():
    return "\n".join(l.split("--", 1)[0] for l in SRC.read_text(encoding="utf-8").splitlines())


def test_never_touches_blizzard_quest_ui():
    src = code()
    for bad in ("SelectQuestLogEntry(", "SetSelectedQuest(", "QuestMapFrame", "ShowUIPanel", "SetParent(",
                "ObjectiveTrackerFrame", "QuestWatchFrame", "hooksecurefunc", "AbandonQuest(", "QuestLogPushQuest",
                "AddQuestWatch", "RemoveQuestWatch", "SendChatMessage", "Secure", "PlaySound"):
        assert bad not in src, bad


def test_toc_options_and_movable():
    toc = (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()
    assert toc.index("Modules\\Todo.lua") == toc.index("Modules\\Tracker.lua") + 1
    src = SRC.read_text(encoding="utf-8")
    assert "if T.RegisterMovable then pcall(T.RegisterMovable" in src
    for key in ("shown", "lock", "fontSize", "scale", "alpha", "tab", "maxTab", "steps", "errands", "group", "session",
                "verdict", "reward", "mates"):
        assert re.search(rf'key = "{key}".*section = ', src), key
    assert "function M:AutoRefresh()" in src


@needs_lua
def test_syntax():
    luac = LUA.replace("lua5.1", "luac5.1")
    assert subprocess.run([luac, "-p", str(SRC)]).returncode == 0


@needs_lua
@pytest.mark.parametrize("flavor", ["modern", "legacy"])
def test_scenario(flavor):
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--flavor", flavor, "--class", "HUNTER", "--scenario",
                        "tests/wow/todo_scenario.lua", str(ADDON)],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
