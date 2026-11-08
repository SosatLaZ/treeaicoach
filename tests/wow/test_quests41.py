"""QUESTS (4.1) — the quest brain (Modules/QuestBrain.lua + ZonePlan / Planner / Route / Export).

tests/wow/quests41_scenario.lua replays a realistic level-29 Horde hunter log (group of 4, frFR) and checks every item of
research/quests41.md: one quest at one place = one stop (« La revanche de Gann »), never a dungeon name as the route zone /
card title, the four modes (« Rendre mes quêtes » with flights / hearth across zones, « Quêtes à prendre » ranked with the
why, « Tout faire ici », « Avec le groupe » common quests first), filters (grey, elite solo, dungeon unless 4 and wanted,
faction, class, done, prerequisite, too high), stable order, French names only, SPF re-sent after a rebuild, nothing in the
chat — with QuestieDB and WITHOUT it (game waypoints + NPCs the player met)."""
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
SRC = ADDON / "Modules" / "QuestBrain.lua"
LUA = shutil.which("lua5.1")
ALLOW = ["LibQuestieDB", "QuestDB", "NpcDB", "ObjectDB", "ItemDB", "TreeAIPlan", "IsQuestFlaggedCompleted", "QuestieLoader"]


def _run(flavor: str, variant: str) -> subprocess.CompletedProcess:
    cmd = [LUA, str(HERE / "harness.lua"), "-q", "--flavor", flavor, "--class", "HUNTER", "--profile", "sur"]
    for a in ALLOW:
        cmd += ["--allow", a]
    cmd += ["--scenario", str(HERE / "quests41_scenario.lua"), str(ADDON)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=ROOT, check=False,
                          env={**os.environ, "Q41_VARIANT": variant})


@pytest.mark.skipif(not LUA, reason="lua5.1 not installed")
@pytest.mark.parametrize("flavor,variant", [("forever", "full"), ("modern", "full"), ("legacy", "full"),
                                            ("forever", "noquestie"), ("modern", "noquestie")])
def test_quest_brain_scenario(flavor, variant):
    r = _run(flavor, variant)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-6000:] + r.stderr[-2000:]


def _code(path: Path) -> str:
    return "\n".join(re.sub(r"--.*$", "", ln) for ln in path.read_text(encoding="utf-8").splitlines())


def test_toc_and_safe_module():
    lines = [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    qb = lines.index("Modules\\QuestBrain.lua")
    for dep in ("ZonePlan", "Planner", "Route", "Export"):
        assert lines.index("Modules\\%s.lua" % dep) < qb, dep
    code = _code(SRC)
    assert "T.SAFE_MODULES.QuestBrain = true" in code
    core = (ADDON / "Core.lua").read_text(encoding="utf-8")
    assert re.search(r'QuestBrain = \{ lite = true[^\n]*desc = "', core)


def test_public_api_and_event():
    code = _code(SRC)
    for fn in ("function QB.Modes()", "function QB.SetMode(", "function QB.Mode()", "function QB.PickupList(",
               "function QB.TurnInList()", "function QB.Skip()", "function QB.Unskip(", "function QB.Prev()", "function QB.Cycle()"):
        assert fn in code, fn
    assert '"QUEST_MODE_CHANGED"' in code
    assert "T.QuestBrain = QB" in code


def test_nothing_protected_no_chat_no_timer():
    code = _code(SRC)
    for bad in ("AcceptQuest(", "CompleteQuest(", "GetQuestReward(", "AbandonQuest", "SelectQuestLogEntry", "QuestLogPushQuest",
                "SendChatMessage", ":Print(", "print(", "C_Timer", "NewTicker", 'SetScript("OnUpdate"', "CastSpell", "UseItem",
                "TakeTaxiNode", "hooksecurefunc", "SetBinding"):
        assert bad not in code, bad


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_syntax_no_globals():
    assert subprocess.run(["luac5.1", "-p", str(SRC)], capture_output=True).returncode == 0
    listing = subprocess.run(["luac5.1", "-l", "-p", str(SRC)], capture_output=True, text=True).stdout
    assert "SETGLOBAL" not in listing
    used = set(re.findall(r"GETGLOBAL\s.*;\s*(\w+)", listing))
    assert used <= {"_G", "ipairs", "pairs", "math", "pcall", "rawget", "string", "table", "tonumber", "tostring", "type",
                    "select", "next", "unpack"}, used


def test_research_doc():
    doc = (ROOT / "research" / "quests41.md").read_text(encoding="utf-8")
    for s in ("La revanche de Gann", "Kraal de Tranchebauge", "Rendre mes quêtes", "Quêtes à prendre", "Questie"):
        assert s in doc, s
