"""Modules/PvPMark.lua: enemy attackable players marked on nameplates (own frames only, taint-safe), harness scenario."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
ADDON = ROOT / "wow_addon" / "TreeAI"
SRC = (ADDON / "Modules" / "PvPMark.lua").read_text(encoding="utf-8")
CODE = re.sub(r"--[^\n]*", "", SRC)   # code without comments
lua = pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 not installed")


def test_toc_and_registration():
    toc = (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()
    names = [ln.strip() for ln in toc]
    assert "Modules\\PvPMark.lua" in names and names.index("Modules\\PvPMark.lua") > names.index("Modules\\PvP.lua")
    assert "PvPMark = {" in (ADDON / "Core.lua").read_text(encoding="utf-8")
    assert '"PvPMark"' in (ADDON / "Options.lua").read_text(encoding="utf-8")


def test_taint_rules():
    # never touches Blizzard plates: no SetParent, no protected calls, no per-frame work, no combat log
    for bad in ("SetParent", "OnUpdate", "COMBAT_LOG_EVENT", "TargetUnit", "CastSpell", "SetRaidTarget", "print("):
        assert bad not in CODE, bad
    assert "FrameLocked" in SRC and "_ns(" in SRC
    assert not re.search(r"plate:\s*(SetPoint|ClearAllPoints|Show|Hide|SetAlpha|SetScale)", CODE)
    assert "settings.pvp" not in CODE.split("T.PvPMark = PM")[1]   # own toggle, independent of the old PvP aids


@lua
@pytest.mark.parametrize("flavor", ["forever", "modern"])
def test_pvpmark_scenario(flavor):
    r = subprocess.run(["lua5.1", str(HERE / "harness.lua"), "-q", "--flavor", flavor, "--class", "HUNTER",
                        "--scenario", str(HERE / "pvpmark_scenario.lua"), str(ADDON)],
                       cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-4000:] + r.stderr[-2000:]
