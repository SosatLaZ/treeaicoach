"""Modules/PvP.lua: TOC order, syntax, no global, ToS (no combat log, no auto-target) and the harness scenario."""
# (5.1 CLEAN) harness default = the player's profile (« sur ») ; this file tests modules that are off there: « tout »
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
PVP = ADDON / "Modules" / "PvP.lua"


def test_toc_lists_pvp():
    lines = [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert "Modules\\PvP.lua" in lines
    assert lines.index("Modules\\PvP.lua") > lines.index("Core.lua")


def test_pvp_static_rules():
    src = PVP.read_text(encoding="utf-8")
    code = "\n".join(re.sub(r"--.*$", "", ln) for ln in src.splitlines())
    assert "COMBAT_LOG_EVENT_UNFILTERED" not in code
    assert "CombatLogGetCurrentEventInfo" not in code
    for bad in ("TargetUnit", "TargetByName", "RunMacroText", "CastSpellByName", "SetBinding", "SendChatMessage"):
        assert bad not in code, bad
    assert "InCombatLockdown" in code  # macro edit guarded
    assert 'RegisterTab(TAB, "JcJ"' in code


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_pvp_syntax_no_globals():
    assert subprocess.run(["luac5.1", "-p", str(PVP)], capture_output=True).returncode == 0
    listing = subprocess.run(["luac5.1", "-l", "-p", str(PVP)], capture_output=True, text=True).stdout
    assert "SETGLOBAL" not in listing


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["modern", "legacy"])
def test_pvp_scenario(flavor):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--profile", "tout", "--flavor", flavor, "--class", "HUNTER",
                        "--scenario", str(ROOT / "tests/wow/pvp_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
