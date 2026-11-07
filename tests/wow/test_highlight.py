"""Modules/Highlight.lua (quest item / mob highlighting): TOC, taint guards, syntax, harness scenario."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
SRC = (ADDON / "Modules" / "Highlight.lua").read_text(encoding="utf-8")


def test_toc_lists_highlight_after_quests_modules():
    lines = [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert "Modules\\Highlight.lua" in lines
    assert lines.index("Modules\\Highlight.lua") > lines.index("Modules\\Quests.lua")
    assert lines.index("Modules\\Highlight.lua") > lines.index("Modules\\Predict.lua")


def test_taint_rules():
    # never re-parent onto / write fields of Blizzard frames, no protected calls
    assert ":SetParent(" not in SRC and ".SetParent" not in SRC
    for bad in ("SelectQuestLogEntry", "SetSelectedQuest", "ShowUIPanel", "UseContainerItem", "PickupContainerItem",
                "SendChatMessage", "Minimap:"):
        assert bad not in SRC, bad
    assert not re.search(r"\b(LootButton\d|GroupLootFrame\d|TargetFrame|ContainerFrame\w*)\.\w+\s*=[^=]", SRC)
    assert "EnableMouse(false)" in SRC  # overlays never eat clicks
    assert "section =" in SRC


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_syntax_no_globals():
    path = ADDON / "Modules" / "Highlight.lua"
    assert subprocess.run(["luac5.1", "-p", str(path)], capture_output=True).returncode == 0
    listing = subprocess.run(["luac5.1", "-l", "-p", str(path)], capture_output=True, text=True).stdout
    assert "SETGLOBAL" not in listing


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["modern", "legacy"])
def test_highlight_scenario(flavor):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", "HUNTER",
                        "--scenario", str(ROOT / "tests/wow/highlight_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
