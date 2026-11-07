"""Modules/SelfTest.lua (/tai test, TreeAIDB.diag.selftest / diag.errors) + the Forever 1.60.1 API audit fixes."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
SELFTEST = ADDON / "Modules" / "SelfTest.lua"


def _lines():
    return [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]


def test_toc_loads_selftest_early():
    lines = _lines()
    assert lines[lines.index("Options.lua") + 1] == "Modules\\SelfTest.lua"


def test_selftest_is_read_only():
    src = SELFTEST.read_text(encoding="utf-8")
    # never a protected / throttled action from the self-test
    for bad in ("CreateMacro(", "EditMacro(", "DeleteMacro(", "ReplicateItems(", "SendBrowseQuery", "PostItem",
                "PlaceBid", "SetRaidTarget", "SendChatMessage", "CastSpell", "UseContainerItem"):
        assert bad not in src, bad
    assert "TreeAIDB.diag.selftest" in src and "diag.errors" in src


def test_no_combat_log_registration_without_guard():
    """COMBAT_LOG_EVENT_UNFILTERED = ADDON_ACTION_FORBIDDEN on Forever: only through the guarded core / EventAllowed."""
    core = (ADDON / "Core.lua").read_text(encoding="utf-8")
    assert "function TreeAI:CombatLogAllowed" in core and "EventAllowed(event)" in core
    hunter = (ADDON / "Modules" / "Hunter.lua").read_text(encoding="utf-8")
    assert "EventAllowed(e)" in hunter  # its own frame registers its event list


def test_forever_api_compat_helpers():
    core = (ADDON / "Core.lua").read_text(encoding="utf-8")
    for helper in ("API.Pub", "API.IsSecret", "API.SkillLines", "API.PetHappiness", "API.PetFoodTypes",
                   "API.SpellBookSpells", "API.QuestLogEntries", "API.PlayerCasting", "API.HasAction"):
        assert f"function {helper}(" in core, helper
    assert "C_SpellBook" in core and "C_PetInfo" in core and "C_SkillInfo" in core


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_syntax_no_globals():
    assert subprocess.run(["luac5.1", "-p", str(SELFTEST)], capture_output=True).returncode == 0
    listing = subprocess.run(["luac5.1", "-l", "-p", str(SELFTEST)], capture_output=True, text=True).stdout
    assert "SETGLOBAL" not in listing


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor,cls", [("forever", "HUNTER"), ("forever", "MAGE"), ("modern", "HUNTER"),
                                        ("legacy", "WARRIOR")])
def test_selftest_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", cls,
                        "--scenario", str(ROOT / "tests/wow/selftest_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=90, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("cls", ["HUNTER", "WARRIOR"])
def test_forever_storm_clean(cls):
    """Whole addon on the Forever rules: no forbidden call, no Lua error, no leak."""
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", "forever", "--class", cls,
                        str(ADDON)], capture_output=True, text=True, timeout=90, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "violations=0" in r.stdout and "errors=0" in r.stdout
