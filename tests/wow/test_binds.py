"""Modules/Binds.lua (agent BINDS): TOC order, syntax, no globals, click-only actions, harness scenario."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
SRC = ADDON / "Modules" / "Binds.lua"


def test_toc_lists_binds_after_macros():
    lines = [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert lines.index("Modules\\Binds.lua") == lines.index("Modules\\Macros.lua") + 1


def test_actions_guarded():
    src = SRC.read_text(encoding="utf-8")
    for bad in ("CastSpellByName", "UseAction", "SendChatMessage", "RunMacroText"):
        assert bad not in src
    assert "InCombatLockdown" in src and "UserAction" in src
    # protected writes only inside Place/Bind (player click), never from events / timers
    for fn in ("PlaceAction", "SetBinding", "SaveBindings", "PickupMacro", "PickupSpell"):
        calls = [m.start() for m in re.finditer(r"G\.%s\(" % fn, src)]
        assert calls, fn
        for pos in calls:
            head = src[:pos]
            owner = max(head.rfind("function M:Place("), head.rfind("function M:Bind("))
            assert owner > head.rfind("\nfunction ") - 1 and owner >= 0, fn


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_syntax_no_globals():
    assert subprocess.run(["luac5.1", "-p", str(SRC)], capture_output=True).returncode == 0
    listing = subprocess.run(["luac5.1", "-l", "-p", str(SRC)], capture_output=True, text=True).stdout
    assert "SETGLOBAL" not in listing


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["modern", "legacy"])
def test_binds_scenario(flavor):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", "HUNTER",
                        "--scenario", str(ROOT / "tests/wow/binds_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
