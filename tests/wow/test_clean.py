"""CLEAN 4.0: after the big clean-up, what remains tells the game's truth, once, in the right place (Fight = why I
died + ready before the pull; Hunter = pet / ammo / aspects / traps owner; Stats = XP/h with its measuring time;
Gear = words not %; Inventory food; Errands real trainer price; Session mount; Leveling rested = info; Binds new
spells; core auto-slow) — harness scenario on three API flavours, hunter + a caster."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["modern", "legacy", "forever"])
@pytest.mark.parametrize("cls", ["HUNTER", "MAGE"])
def test_clean_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", cls,
                        "--scenario", str(ROOT / "tests/wow/clean_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
    assert "RESULT ok" in r.stdout


def test_removed_modules_gone_from_toc_and_disk():
    toc = (ADDON / "TreeAI.toc").read_text(encoding="utf-8")
    for n in ("SafePath", "HunterPro", "Comfort", "Economy", "Predict", "Edge"):
        assert f"Modules\\{n}.lua" not in toc.split(), n
        assert not (ADDON / "Modules" / f"{n}.lua").exists(), n


def test_no_invented_numbers_left_in_owners():
    gear = (ADDON / "Modules" / "Gear.lua").read_text(encoding="utf-8")
    assert '("+%d %%")' not in gear and "Amélioration +" not in gear
    hunter = (ADDON / "Modules" / "Hunter.lua").read_text(encoding="utf-8")
    for gone in ("CheckInteractDistance", "COMBAT_LOG_EVENT_UNFILTERED", "CreateMacro", "EditMacro"):
        assert gone not in hunter.split("-- Retiré")[0].split("\n", 30)[-1] or gone not in hunter, gone
    fight = (ADDON / "Modules" / "Fight.lua").read_text(encoding="utf-8")
    for gone in ("UnitDetailedThreatSituation", "UnitCastingInfo", "RegisterEvent(\"UNIT_COMBAT\"", "NAME_PLATE_UNIT_ADDED"):
        assert gone not in fight, gone
