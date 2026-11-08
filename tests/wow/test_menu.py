"""MENU: panel look (line types → icon + colour, right number column, no overlap / truncation, click affordance,
tab badges, grouped « Plus »), quest auto-tracking by region (Modules/Region.lua), minimap button + hub menu
(Modules/Hub.lua), clean dungeon card — harness scenario on the three API flavours + static checks."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["modern", "legacy", "forever"])
@pytest.mark.parametrize("cls", ["HUNTER", "WARRIOR"])
def test_menu_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", cls,
                        "--scenario", str(ROOT / "tests/wow/menu_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout


def test_toc_order():
    lines = [l.strip() for l in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert lines.index("Modules\\Todo.lua") < lines.index("Modules\\Region.lua") < lines.index("Modules\\Hub.lua")


def test_region_never_watches_automatically():
    src = (ADDON / "Modules" / "Region.lua").read_text(encoding="utf-8")
    # the game's quest watch is only called from WatchZone, which refuses outside a click (InUserAction)
    calls = [m.start() for m in re.finditer(r"pcall\(add(Modern|Old)", src)]
    assert calls, "AddQuestWatch call expected in WatchZone"
    watch = src.index("function R.WatchZone")
    guard = src.index("InUserAction", watch)
    assert all(c > guard for c in calls)
    for bad in ("SetSuperTracked", "SelectQuestLogEntry", "SetSelectedQuest", "Questie", "ShortestPath"):
        assert bad + "(" not in src


def test_hub_taint_rules():
    src = (ADDON / "Modules" / "Hub.lua").read_text(encoding="utf-8")
    for bad in ("SetParent(", "hooksecurefunc", "Minimap:SetScript", "Minimap:SetSize", "SetCVar", "Secure"):
        assert bad not in src, bad
    assert 'CreateFrame")("Button", "TreeAIMinimapButton", mm)' in src


def test_dungeon_texts_clean():
    data = (ADDON / "Data" / "Dungeons.lua").read_text(encoding="utf-8")
    for bad in ("Rare. Facile.", "Entraînez-vous", "pas de conseil Classic", "Simple, tapez fort", "pas de panique"):
        assert bad not in data, bad
    assert "cards = {" in data and "danger =" in data
