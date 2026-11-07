"""Modules/Pro.lua (quest features 1-15) + Modules/Economy.lua (16-25): TOC, syntax, no global, scenario."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
FILES = [ADDON / "Modules" / "Pro.lua", ADDON / "Modules" / "Economy.lua"]


def test_toc_lists_pro_economy_after_boost():
    lines = [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    i = lines.index("Modules\\Boost.lua")
    assert lines[i + 1] == "Modules\\Pro.lua" and lines[i + 2] == "Modules\\Economy.lua"


def test_no_chat_and_options_have_sections():
    for f in FILES:
        src = f.read_text(encoding="utf-8")
        assert "SendChatMessage" not in src and "SelectQuestLogEntry" not in src and "SetSelectedQuest" not in src
        assert src.count("section = ") >= 10


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_syntax_no_globals(path):
    assert subprocess.run(["luac5.1", "-p", str(path)], capture_output=True).returncode == 0
    listing = subprocess.run(["luac5.1", "-l", "-p", str(path)], capture_output=True, text=True).stdout
    assert "SETGLOBAL" not in listing


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["modern", "legacy"])
def test_pro_economy_scenario(flavor):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", "HUNTER",
                        "--scenario", str(ROOT / "tests/wow/pro_economy_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
