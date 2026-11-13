"""4.0 QA (FIX-SYSTEMS): regressions of research/qa_systems.md — death recap never blames a previous fight's enemy,
« content » pet = ready, dungeon loot by armor proficiency AND level (nothing before the item data), no invented talent
« Ensuite » on Forever, too-strong rares = warn without sound, no invented boss timers, one owner for the quest
countdown, « Fais N× X » only for learned recipes — on the faithful Forever mock and the modern flavour."""
# (5.1 CLEAN) harness default = the player's profile (« sur ») ; this file tests modules that are off there: « tout »
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["forever", "modern"])
def test_qasys_scenario(flavor):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--profile", "tout", "--flavor", flavor, "--class", "HUNTER",
                        "--level", "28", "--scenario", str(ROOT / "tests/wow/qasys_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=180, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-5000:] + r.stderr[-2000:]
    assert "RESULT ok" in r.stdout


def test_no_invented_boss_timer_or_quest_timer_duplicate_left():
    dungeon = (ADDON / "Modules" / "Dungeon.lua").read_text(encoding="utf-8")
    assert "TickTimers" not in dungeon and "Prochain (≈)" not in dungeon and 'key = "timers"' not in dungeon
    edge2 = (ADDON / "Modules" / "Edge2.lua").read_text(encoding="utf-8")
    assert "questTimer" not in edge2 and "CheckTimers" not in edge2


def test_pet_content_is_ready_everywhere():
    assert not (ADDON / "Modules" / "QuickFight.lua").exists()   # (5.1 CLEAN) removed
    dungeon = (ADDON / "Modules" / "Dungeon.lua").read_text(encoding="utf-8")
    assert "pas content" not in dungeon


def test_mail_list_follows_level_proficiency():
    data = (ADDON / "Data" / "Dungeons.lua").read_text(encoding="utf-8")
    assert 'MAIL = "MAGE WARLOCK PRIEST", "HUNTER ROGUE DRUID", "WARRIOR PALADIN HUNTER SHAMAN"' in data
