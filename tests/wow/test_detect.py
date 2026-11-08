"""Modules/Detect.lua (vignettes, quest units, quest diffs, services, group, combat) + adoption: TOC, rules, scenario."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
SRC = (ADDON / "Modules" / "Detect.lua").read_text(encoding="utf-8")


def test_toc_lists_detect_after_compass_before_consumers():
    lines = [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    i = lines.index("Modules\\Detect.lua")
    assert i > lines.index("Modules\\Compass.lua")
    for consumer in ("Boost", "Highlight", "Radar"):   # Predict removed in 4.0 (CLEAN)
        assert i < lines.index(f"Modules\\{consumer}.lua")


def test_forever_safe_and_read_only():
    code = "\n".join(ln for ln in SRC.splitlines() if not ln.strip().startswith("--"))
    for bad in ("COMBAT_LOG_EVENT", "CombatLogGetCurrentEventInfo", "SetRaidTarget", "SetTracking", "SelectQuestLogEntry",
                "SetSelectedQuest", "ShowUIPanel", "SendChatMessage", "C_TooltipInfo", "GameTooltip", ":SetParent("):
        assert bad not in code, bad
    # the real Forever 1.60.1 API names (Blizzard_APIDocumentationGenerated, branch forever)
    for api in ("C_VignetteInfo", "GetVignettes", "GetVignetteInfo", "GetVignettePosition", "VIGNETTES_UPDATED",
                "UnitIsRelatedToActiveQuest", "UnitIsQuestBoss", "GetQuestObjectives", "GetTaxiNodesForMap",
                "GetAreaPOIForMap", "UNIT_QUEST_LOG_CHANGED", "QUEST_WATCH_UPDATE", "UNIT_COMBAT"):
        assert api in SRC, api
    assert "section =" in SRC
    # every adopter goes through T.Detect guarded
    for mod in ("Boost", "Highlight", "Compass", "Radar"):
        src = (ADDON / "Modules" / f"{mod}.lua").read_text(encoding="utf-8")
        assert re.search(r"T\.Detect", src), mod


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_syntax_no_globals():
    path = ADDON / "Modules" / "Detect.lua"
    assert subprocess.run(["luac5.1", "-p", str(path)], capture_output=True).returncode == 0
    listing = subprocess.run(["luac5.1", "-l", "-p", str(path)], capture_output=True, text=True).stdout
    assert "SETGLOBAL" not in listing


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["modern", "legacy", "forever"])
def test_detect_scenario(flavor):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", "HUNTER",
                        "--scenario", str(ROOT / "tests/wow/detect_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
