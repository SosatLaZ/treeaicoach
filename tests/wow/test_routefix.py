"""ROUTEFIX (4.1): exact stop order vs brute force, realistic Kalimdor cases on the shipped travel tables (flights known /
unknown, hearth ready / on cooldown, zeppelin), learned walking paths (ramp / L-shape), compass « par le chemin »,
one stop list, Shortest Path Forever re-send. Fake quests, fake names."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
LUA = shutil.which("lua5.1")
needs_lua = pytest.mark.skipif(not LUA, reason="lua5.1 not installed")


@needs_lua
@pytest.mark.parametrize("flavor", ["modern", "forever"])
def test_routefix_scenario(flavor):
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--flavor", flavor, "--class", "HUNTER", "--level", "30",
                        "--allow", "ShortestPathForever", "--scenario", "tests/wow/routefix_scenario.lua", str(ADDON)],
                       cwd=ROOT, capture_output=True, text=True, timeout=300)
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "RESULT ok" in out, out[-4000:]
    assert "violations=0" in out


def test_no_new_onupdate_in_pathlearn():
    src = (ADDON / "Modules" / "PathLearn.lua").read_text(encoding="utf-8")
    assert "SetScript" not in src and "C_Timer" not in src          # fed by NavTime's movement sampler only
