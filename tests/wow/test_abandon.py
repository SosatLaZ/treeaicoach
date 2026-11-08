"""Modules/Abandon.lua (« Quêtes à retirer », replaces Pro feature 2): TOC, safety, pure scoring spec, scenario."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
SRC = ADDON / "Modules" / "Abandon.lua"


def test_toc_lists_abandon_after_economy():
    lines = [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert lines.index("Modules\\Abandon.lua") > lines.index("Modules\\Quests.lua")   # Economy removed in 4.0 (CLEAN)
    assert "Modules\\Economy.lua" not in lines


def _code(path):
    return "\n".join(re.sub(r"--.*$", "", ln) for ln in path.read_text(encoding="utf-8").splitlines())


def test_abandon_only_inside_the_click():
    """(JOURNAL 4.0.1) the quest log selection and the abandon are touched in ONE place, M.AbandonNow, after the
    combat / click (T:InUserAction) guards; never Blizzard's Lua (popup, quest map, panels), never the chat."""
    code = _code(SRC)
    for bad in ("QuestLogPushQuest", "ShowUIPanel", "HideUIPanel", "QuestMapFrame", "StaticPopup", "QuestLog_",
                "ToggleQuestLog", "SendChatMessage", "OpenQuestLog"):
        assert bad not in code, bad
    body = code.split("function M.AbandonNow(", 1)[1].split("\nend\n", 1)[0]
    rest = code.replace(body, "")
    for api in ("SetSelectedQuest", "SetAbandonQuest", "AbandonQuest", "SelectQuestLogEntry"):
        assert api in body, api
    # outside AbandonNow the names only appear in api() (existence test: type(...) == "function") and comments
    calls = re.findall(r"try\((?:CQ\.)?(?:SetSelectedQuest|SetAbandonQuest|AbandonQuest|sel|setAb|ab)\b", rest)
    assert calls == [], calls
    guards = body.split("local here, idx = logIndex(id)", 1)[0]
    assert "inCombat()" in guards and "T:InUserAction()" in guards and "M.CanDirect()" in guards
    # the previous selection and the game's abandon target are put back right after
    assert "try(CQ.SetSelectedQuest, prevSel)" in body and "try(sel, prev)" in body
    assert "prevAb ~= 0 and prevAb ~= id" in body
    # the target is re-read before abandoning; a refusal by the game switches to the guided flow for good
    assert "target == id" in body and "M.db.refused = true" in body


def test_pro_feature_2_moved():
    src = (ADDON / "Modules" / "Pro.lua").read_text(encoding="utf-8")
    assert "AbandonList" not in src and 'key = "abandon"' not in src


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_syntax_no_globals():
    assert subprocess.run(["luac5.1", "-p", str(SRC)], capture_output=True).returncode == 0
    listing = subprocess.run(["luac5.1", "-l", "-p", str(SRC)], capture_output=True, text=True).stdout
    assert "SETGLOBAL" not in listing


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
def test_scoring_spec_exact_numbers():
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/abandon_spec.lua"), str(SRC)],
                       capture_output=True, text=True, timeout=30, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0 failures" in r.stdout


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["modern", "legacy", "forever"])
def test_abandon_scenario(flavor):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", "HUNTER",
                        "--scenario", str(ROOT / "tests/wow/abandon_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
