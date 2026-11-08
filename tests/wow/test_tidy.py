"""TIDY: the options window is laid out by one engine (category tree + scroll page) and can never overlap —
every options page walked, rectangles checked; compass hidden while dead; SPF route removed on death."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["forever", "modern", "legacy"])
@pytest.mark.parametrize("cls", ["HUNTER", "MAGE"])
def test_tidy_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", cls,
                        "--allow", "ShortestPathForever",
                        "--scenario", str(ROOT / "tests/wow/tidy_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "RESULT ok" in r.stdout


def test_options_single_blizzard_entry():
    src = (ADDON / "Options.lua").read_text(encoding="utf-8")
    assert "RegisterCanvasLayoutSubcategory" not in src      # one entry in Blizzard's list, our own tree inside
    assert "OptionsSliderTemplate" in src and "ScrollFrame" in src
    assert "SetPoint(\"LEFT\", cb, \"LEFT\", 330" not in src  # no hand-placed widgets


def test_spf_route_cleared_on_death_only_ours():
    src = (ADDON / "Modules" / "Compass.lua").read_text(encoding="utf-8")
    assert "ClearRouteOnDeath" in src and '"TreeAI"' in src and "PLAYER_DEAD" in src
