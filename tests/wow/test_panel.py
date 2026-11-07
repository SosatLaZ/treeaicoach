"""Panel contract (Core/UI/Options): tabs, « Maintenant » top 5, scrolling, mini mode, corner alert box,
no advice in chat, one options page per module — harness scenario on both API flavours + static checks."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["modern", "legacy"])
@pytest.mark.parametrize("cls", ["HUNTER", "MAGE"])
def test_panel_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", cls,
                        "--scenario", str(ROOT / "tests/wow/panel_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout


def test_ui_is_taint_safe():
    src = (ADDON / "UI.lua").read_text(encoding="utf-8")
    assert "Secure" not in src                       # no secure template
    assert ":SetParent(" not in src                  # never reparent (onto Blizzard frames)
    for parent in re.findall(r"CreateFrame\([^,]+,[^,]+,\s*([^,\)]+)", src):
        assert parent.strip() in {"_G.UIParent", "panel", "nil", "menu"}, parent   # menu = notre menu « Plus »


def test_modules_use_tabs_guarded():
    for mod in ("Squad.lua", "Quests.lua", "Auction.lua"):
        assert "T.SetTabLines" in (ADDON / "Modules" / mod).read_text(encoding="utf-8"), mod
