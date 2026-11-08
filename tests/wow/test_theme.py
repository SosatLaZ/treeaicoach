"""Modules/Theme.lua (common look for TreeAI frames): TOC order, adoption, taint rules, harness scenario."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
SRC = (ADDON / "Modules" / "Theme.lua").read_text(encoding="utf-8")


def test_toc_loads_theme_right_after_ui():
    lines = [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert lines.index("Modules\\Theme.lua") == lines.index("UI.lua") + 1


def test_api_surface():
    for fn in ("Skin", "Color", "Hex", "ClassColor", "PrioColor", "Font", "Text", "Button", "IconButton",
               "ProgressBar", "Header", "Separator", "Checkbox", "Slider", "Tab", "Badge", "Tooltip", "Refresh"):
        assert re.search(r"function Th:%s\(" % fn, SRC), fn
    for name in ("Sombre", "Clair", "Blizzard"):
        assert '"%s"' % name in SRC
    assert 'section = "Apparence"' in SRC and 'type = "choice"' in SRC


def test_never_touches_blizzard_frames():
    assert "SetParent" not in SRC
    assert 'name:find("^TreeAI")' in SRC and "IsProtected" in SRC and "IsForbidden" in SRC
    for bad in ("ShowUIPanel", "SelectQuestLogEntry", "SetSelectedQuest", "SendChatMessage"):
        assert bad not in SRC


def test_modules_adopt_theme_guarded():
    for mod in ("Compass", "Tracker", "Auction", "Macros", "Squad", "Highlight", "Hunter"):   # 4.0: HunterPro removed, Fight has no frame
        src = (ADDON / "Modules" / (mod + ".lua")).read_text(encoding="utf-8")
        assert re.search(r"if (?:T|_G\.TreeAI and _G\.TreeAI|TreeAI and TreeAI|TreeAI|Th)(?:\.Theme)? then", src), mod
        assert "Theme" in src and ":Skin(" in src, mod


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_syntax_no_globals():
    path = ADDON / "Modules" / "Theme.lua"
    assert subprocess.run(["luac5.1", "-p", str(path)], capture_output=True).returncode == 0
    listing = subprocess.run(["luac5.1", "-l", "-p", str(path)], capture_output=True, text=True).stdout
    assert "SETGLOBAL" not in listing


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor,cls", [("modern", "HUNTER"), ("legacy", "HUNTER"), ("modern", "MAGE")])
def test_theme_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", cls,
                        "--scenario", str(ROOT / "tests/wow/theme_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
