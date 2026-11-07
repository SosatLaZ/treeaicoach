"""Modules Fight / HunterPro / Comfort (FEATURES50 26-40, 48-50): TOC order, syntax, no globals, harness scenario."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
FILES = ["Fight", "HunterPro", "Comfort"]


def test_toc_lists_modules_after_boost():
    lines = [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    i = lines.index("Modules\\Boost.lua")
    assert lines[i + 1:i + 4] == [f"Modules\\{f}.lua" for f in FILES]


def test_no_forbidden_or_chat():
    for f in FILES:
        src = (ADDON / "Modules" / f"{f}.lua").read_text(encoding="utf-8")
        for bad in ("CastSpellByName", "TargetUnit", "SendChatMessage", "PlaceAction", "PickupSpell", "SetBinding"):
            assert bad not in src, (f, bad)
        assert "section =" in src  # options grouped by feature


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
@pytest.mark.parametrize("name", FILES)
def test_syntax_no_globals(name):
    path = ADDON / "Modules" / f"{name}.lua"
    assert subprocess.run(["luac5.1", "-p", str(path)], capture_output=True).returncode == 0
    listing = subprocess.run(["luac5.1", "-l", "-p", str(path)], capture_output=True, text=True).stdout
    assert "SETGLOBAL" not in listing


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["modern", "legacy"])
def test_x2_scenario(flavor):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", "HUNTER",
                        "--scenario", str(ROOT / "tests/wow/x2_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
