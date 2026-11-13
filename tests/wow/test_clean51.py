"""5.1 CLEAN — what the independent audit (AUDIT5) called botched, dangerous or dead, each with its reproduction.

B15 / B13 commands (tests/wow/slash51_scenario.lua): a bare switch command shows the state and never toggles, a command
whose module is off says what to enable (never « ACTIVÉ »), no documented command flips a switch when run bare, one name
per switch (« Profil : Simple / Complet », « Conseils »), duplicates out of the help, one shell.
Every scenario here runs in the DEFAULT module profile (« sur ») on the faithful Forever mock."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
CORE = (ADDON / "Core.lua").read_text(encoding="utf-8")
HARNESS = ROOT / "tests" / "wow" / "harness.lua"

needs_lua = pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")


def run_scenario(name, *extra, flavor="forever", cls="HUNTER", level="40", timeout=300):
    r = subprocess.run(["lua5.1", str(HARNESS), "-q", "--no-storm", "--profile", "sur", "--flavor", flavor,
                        "--class", cls, "--level", level, *extra,
                        "--scenario", str(ROOT / "tests" / "wow" / name), str(ADDON)],
                       capture_output=True, text=True, timeout=timeout, cwd=ROOT)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-5000:] + r.stderr[-2000:]
    return r.stdout


# ------------------------------------------------------------------------------------------------ B15 / B13 commands
def test_bare_toggle_pattern_is_gone_from_the_owned_files():
    """« rest == "" and not X » (a bare command switching something on) no longer exists in Core / QoL / PvP."""
    for name in ("Core.lua", "Modules/QoL.lua", "Modules/PvP.lua"):
        src = re.sub(r"--[^\n]*", "", (ADDON / name).read_text(encoding="utf-8"))
        assert not re.search(r'rest == ""\s+and not ', src), name
    qol = re.sub(r"--[^\n]*", "", (ADDON / "Modules" / "QoL.lua").read_text(encoding="utf-8"))
    assert "T:SlashSwitch(" in qol and "ACTIVÉ" not in qol
    # the switches of the core go through the one contract
    for cmd in ("pvp", "verrou", "debug", "guide"):
        assert re.search(r'RegisterSlash\("%s", TreeAI:SlashSwitch\(' % cmd, CORE), cmd
    # the old « test » command of the core (overwritten at load since 3.9) is gone, « profil » is registered once
    assert CORE.count('RegisterSlash("profil"') == 1 and 'RegisterSlash("test"' not in CORE
    assert '"Mode sûr", essentiel' not in CORE


@needs_lua
@pytest.mark.parametrize("flavor", ["forever", "modern", "legacy"])
def test_slash51_scenario(flavor):
    run_scenario("slash51_scenario.lua", flavor=flavor)
