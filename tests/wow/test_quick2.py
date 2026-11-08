"""QUICK2 « Raccourcis » (Modules/QuickFight.lua): TOC, syntax, no globals, secure-button rules, harness scenario."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
SRC = ADDON / "Modules" / "QuickFight.lua"


def test_toc_lists_module():
    lines = [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert "Modules\\QuickFight.lua" in lines
    assert lines.index("Modules\\QuickFight.lua") > lines.index("Modules\\Macros.lua")
    assert lines.index("Modules\\QuickFight.lua") > lines.index("Modules\\Hunter.lua")


def test_no_forbidden_calls_or_chat():
    src = SRC.read_text(encoding="utf-8")
    for bad in ("CastSpellByName", "CastSpell(", "TargetUnit", "SendChatMessage", "UseAction", "SetBinding",
                "PickupSpell", "UseContainerItem", "CreateMacro", "EditMacro"):
        assert bad not in src, bad
    assert "SecureActionButtonTemplate" in src
    assert 'title = "Barre de combat"' in src   # (3.9.9) Quick is « Raccourcis »
    assert "section =" in src


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_syntax_no_globals():
    assert subprocess.run(["luac5.1", "-p", str(SRC)], capture_output=True).returncode == 0
    listing = subprocess.run(["luac5.1", "-l", "-p", str(SRC)], capture_output=True, text=True).stdout
    assert "SETGLOBAL" not in listing


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor,cls", [("forever", "HUNTER"), ("legacy", "HUNTER"), ("forever", "MAGE"),
                                        ("forever", "WARRIOR")])
def test_quick2_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", cls,
                        "--scenario", str(ROOT / "tests/wow/quick2_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
