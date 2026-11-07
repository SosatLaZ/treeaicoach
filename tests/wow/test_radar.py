"""Radar (agent RADAR): TOC order, taint rules (static), projection/pins scenario on both API flavours."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = Path(os.environ.get("TREEAI_WOW_ADDON") or ROOT / "wow_addon" / "TreeAI")
SRC = ADDON / "Modules" / "Radar.lua"
LUA = shutil.which("lua5.1")
needs_lua = pytest.mark.skipif(not LUA, reason="lua5.1 not installed")


def code():
    return "\n".join(l.split("--", 1)[0] for l in SRC.read_text(encoding="utf-8").splitlines())


def test_toc_after_compass_and_squad():
    lines = [l.strip() for l in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    r = lines.index("Modules\\Radar.lua")
    for dep in ("Compass", "Squad", "Quests"):
        assert lines.index(f"Modules\\{dep}.lua") < r, dep


def test_own_frame_never_blizzard_minimap():
    src = code()
    for bad in ("Minimap:", "Minimap.", "MinimapCluster", "hooksecurefunc", "SetParent(", "WorldMapFrame",
                "SetSuperTrackedQuestID", "SelectQuestLogEntry", "SendChatMessage", "ShowUIPanel"):
        assert bad not in src, bad
    assert '"TreeAIRadarFrame", _G.UIParent' in src
    assert "RegisterMovable" in src and 'type(T.RegisterMovable) == "function"' in src  # guarded


def test_options_have_sections_and_layers():
    src = SRC.read_text(encoding="utf-8")
    for key in ("size", "zoom", "opacity", "rotate", "quests", "turnins", "target", "path", "squad", "rares",
                "death", "taxi", "danger"):
        assert f'key = "{key}"' in src, key
    assert src.count("section = ") >= 15


@needs_lua
def test_syntax():
    assert subprocess.run([LUA.replace("lua5.1", "luac5.1"), "-p", str(SRC)]).returncode == 0


@needs_lua
@pytest.mark.parametrize("flavor", ["modern", "legacy"])
def test_scenario(flavor):
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--flavor", flavor, "--class", "HUNTER", "--scenario",
                        "tests/wow/radar_scenario.lua", str(ADDON)],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
