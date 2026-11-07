"""SafePath (agent SAFEPATH): TOC order, no movement / automation APIs (static), Lua scenario on both flavours."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = Path(os.environ.get("TREEAI_WOW_ADDON") or ROOT / "wow_addon" / "TreeAI")
SRC = ADDON / "Modules" / "SafePath.lua"
LUA = shutil.which("lua5.1")
needs_lua = pytest.mark.skipif(not LUA, reason="lua5.1 not installed")


def test_toc_order():
    lines = [l.strip() for l in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert lines.index("Modules\\SafePath.lua") == lines.index("Modules\\Compass.lua") + 1


def test_never_moves_the_player():
    code = "\n".join(l.split("--", 1)[0] for l in SRC.read_text(encoding="utf-8").splitlines())
    for bad in ("MoveForward", "MoveBackward", "TurnLeft", "TurnRight", "StrafeLeft", "StrafeRight", "JumpOrAscend",
                "InteractUnit", "CameraOrSelectOrMove", "MoveAndSteer", "ToggleAutoRun", "FollowUnit", "CastSpell",
                "TargetNearest", "SetUserWaypoint", "Navigate", "SetSuperTracked", "hooksecurefunc", "SetParent(",
                "ImportModule(", "SendChatMessage"):
        assert bad not in code, bad


@needs_lua
def test_syntax():
    assert subprocess.run([LUA.replace("lua5.1", "luac5.1"), "-p", str(SRC)]).returncode == 0


@needs_lua
@pytest.mark.parametrize("flavor", ["modern", "legacy"])
def test_scenario(flavor):
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--flavor", flavor, "--class", "HUNTER",
                        "--allow", "LibQuestieDB", "--allow", "QuestieLoader", "--allow", "ShortestPathForever",
                        "--scenario", "tests/wow/safepath_scenario.lua", str(ADDON)],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
