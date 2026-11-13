"""ROUTE42 (4.2): study items 2-7 and 10-13 + InsertOptional / VendorFor wired, no hearth bridge, the player's log case
(chosen city / empty zone → auto, stable compass target). Two API flavours × four variants (Questie / SPF mocked or not).
Fake quests, fake names."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
LUA = shutil.which("lua5.1")
needs_lua = pytest.mark.skipif(not LUA, reason="lua5.1 not installed")


@needs_lua
@pytest.mark.parametrize("variant", ["full", "plain", "spf", "questie"])
@pytest.mark.parametrize("flavor", ["modern", "forever", "legacy"])
def test_route42_scenario(flavor, variant):
    env = dict(os.environ, R42_VARIANT=variant)
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--flavor", flavor, "--class", "HUNTER", "--level", "30",
                        "--allow", "ShortestPathForever", "--scenario", "tests/wow/route42_scenario.lua", str(ADDON)],
                       cwd=ROOT, capture_output=True, text=True, timeout=300, env=env)
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "RESULT ok" in out, out[-5000:]
    assert "violations=0" in out


def test_no_data_provider_on_the_map():
    for f in ("PathDraw.lua", "QuestPins.lua", "CityRun.lua"):
        src = re.sub(r"--[^\n]*", "", (ADDON / "Modules" / f).read_text(encoding="utf-8"))
        assert "AddDataProvider" not in src and "AcquirePin" not in src, f


def test_tracker_never_hidden():
    # (5.1 CLEAN) Watch.lua (the TreeAI tracker that could hide the game's) is removed
    assert not (ADDON / "Modules" / "Watch.lua").exists()
    for p in (ADDON / "Modules").glob("*.lua"):
        assert "hideTracker" not in re.sub(r"--[^\n]*", "", p.read_text(encoding="utf-8")), p.name


def test_lifts_not_invented():
    from treeaicoach.wow.planner import transport as TR
    assert TR.LIFTS == () and "TODO" in (ROOT / "treeaicoach" / "wow" / "planner" / "transport.py").read_text(encoding="utf-8")


def test_watch_off_by_default():
    assert "Modules\\Watch.lua" not in (ADDON / "TreeAI.toc").read_text(encoding="utf-8")   # (5.1 CLEAN) removed
