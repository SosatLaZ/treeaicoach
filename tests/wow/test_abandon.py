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
    assert lines.index("Modules\\Abandon.lua") > lines.index("Modules\\Economy.lua") > lines.index("Modules\\Quests.lua")


def test_never_abandons_nor_selects():
    src = SRC.read_text(encoding="utf-8")
    code = "\n".join(re.sub(r"--.*$", "", ln) for ln in src.splitlines())
    for bad in ("AbandonQuest", "SetAbandonQuest", "SelectQuestLogEntry", "SetSelectedQuest", "QuestLogPushQuest",
                "ShowUIPanel", "QuestMapFrame", "SendChatMessage"):
        assert bad not in code, bad


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
@pytest.mark.parametrize("flavor", ["modern", "legacy"])
def test_abandon_scenario(flavor):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", "HUNTER",
                        "--scenario", str(ROOT / "tests/wow/abandon_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
