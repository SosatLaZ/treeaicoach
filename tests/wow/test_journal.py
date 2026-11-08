"""JOURNAL 4.0.1 — « Nettoyer mon journal » (Modules/Abandon.lua + the window page Modules/JournalPage.lua) and « les
donjons déjà faits » (Modules/Dungeon.lua + Data/Dungeons.lua `quests`): TOC, rendering-only page, data sanity,
documented contracts, and the harness scenario on every API flavour (Forever first)."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
PAGE = ADDON / "Modules" / "JournalPage.lua"
DUNGEON = ADDON / "Modules" / "Dungeon.lua"
DATA = ADDON / "Data" / "Dungeons.lua"


def _code(path):
    return "\n".join(re.sub(r"--.*$", "", ln) for ln in path.read_text(encoding="utf-8").splitlines())


def test_toc_page_after_window_pages():
    lines = [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert lines.index("Modules\\JournalPage.lua") == lines.index("Modules\\WindowPages.lua") + 1
    assert lines.index("Modules\\Abandon.lua") < lines.index("Modules\\JournalPage.lua")


def test_page_renders_only():
    """The page never touches the quest log itself: the click goes through Abandon.Request / BatchRequest."""
    code = _code(PAGE)
    for bad in ("AbandonQuest(", "SetAbandonQuest", "SetSelectedQuest", "SelectQuestLogEntry", "StaticPopup",
                "ShowUIPanel", "QuestMapFrame", "C_Timer", ":Print(", "SendChatMessage"):
        assert bad not in code, bad
    assert "Ab.Request(" in code and 'Ab.BatchRequest("grey")' in code
    assert "T:UserAction(" in code and "T:CombatRefused(" in code


def test_dungeon_contracts_and_readme():
    code = _code(DUNGEON)
    for fn in ("function M.IsDone(", "function M.QuestDungeon(", "function M.DoneInfo(", "function M.QuestIDs(",
               "function M.RecordKill("):
        assert fn in code, fn
    # read-only: the instance locks and the completed quests are read, never changed
    assert "GetSavedInstanceInfo" in code and "IsQuestFlaggedCompleted" in code
    for bad in ("SetSelectedQuest", "AbandonQuest", "ResetInstances"):
        assert bad not in code, bad
    readme = (ADDON.parent / "README.md").read_text(encoding="utf-8")
    for s in ("Dungeon.IsDone(", "Dungeon.QuestDungeon(", "Abandon.Request(", "JournalPage"):
        assert s in readme, s


def test_dungeon_quest_data():
    src = DATA.read_text(encoding="utf-8")
    block = src.split("  quests = {", 1)[1].split("\n  },\n", 1)[0]
    keys = re.findall(r'key = "(\w+)"', src)
    seen = {}
    for key, body in re.findall(r"^    (\w+) = \{(.*?)\} \},?$", block, re.M | re.S):
        assert key in keys, key
        for qid in re.findall(r"\d+", re.sub(r"\[(\d+)\] = \d+", r"\1", body)):
            assert seen.get(qid, key) == key, (qid, seen.get(qid), key)   # a quest belongs to one dungeon
            seen[qid] = key
    assert len(seen) > 250
    assert "5722" in seen and seen["5722"] == "RFC" and seen["1102"] == "RFK"
    assert 'qsort = { RFK = { 1717 }' in src


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
@pytest.mark.parametrize("path", [PAGE, DUNGEON, DATA])
def test_syntax_no_globals(path):
    assert subprocess.run(["luac5.1", "-p", str(path)], capture_output=True).returncode == 0
    listing = subprocess.run(["luac5.1", "-l", "-p", str(path)], capture_output=True, text=True).stdout
    assert "SETGLOBAL" not in listing


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["forever", "modern", "legacy"])
def test_journal_scenario(flavor):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--no-storm", "--flavor", flavor, "--class",
                        "HUNTER", "--scenario", str(ROOT / "tests/wow/journal_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
