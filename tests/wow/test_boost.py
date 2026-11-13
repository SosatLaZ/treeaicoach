"""Modules/Boost.lua (module « Rares »): syntax, no global, and the harness scenario (three API flavours, hunter)."""
# (5.1 CLEAN) harness default = the player's profile (« sur ») ; this file tests modules that are off there: « tout »
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
BOOST = ADDON / "Modules" / "Boost.lua"


def test_toc_lists_boost_after_inventory():
    lines = [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert lines.index("Modules\\Boost.lua") == lines.index("Modules\\Inventory.lua") + 1


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_boost_syntax_no_globals():
    assert subprocess.run(["luac5.1", "-p", str(BOOST)], capture_output=True).returncode == 0
    listing = subprocess.run(["luac5.1", "-l", "-p", str(BOOST)], capture_output=True, text=True).stdout
    assert "SETGLOBAL" not in listing


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["modern", "legacy", "forever"])
def test_boost_scenario(flavor):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--profile", "tout", "--flavor", flavor, "--class", "HUNTER",
                        "--scenario", str(ROOT / "tests/wow/boost_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
