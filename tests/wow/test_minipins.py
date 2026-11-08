"""MinimapPins (agent MINIPINS): our pins on Blizzard's real minimap (HereBeDragons-Pins or own engine),
radar off by default, taint rules (static), exact pin offsets scenario on three API flavours."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = Path(os.environ.get("TREEAI_WOW_ADDON") or ROOT / "wow_addon" / "TreeAI")
SRC = ADDON / "Modules" / "MinimapPins.lua"
LUA = shutil.which("lua5.1")
needs_lua = pytest.mark.skipif(not LUA, reason="lua5.1 not installed")


def code():
    return "\n".join(l.split("--", 1)[0] for l in SRC.read_text(encoding="utf-8").splitlines())


def test_toc_after_radar():
    lines = [l.strip() for l in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert lines.index("Modules\\Radar.lua") < lines.index("Modules\\MinimapPins.lua")


def test_never_touches_minimap_itself():
    src = code()
    for bad in ("hooksecurefunc", "HookScript", "SetParent(", "Minimap:SetScript", "Minimap:SetSize", "Minimap:SetMaskTexture",
                "Minimap:SetZoom", "SetCVar", "SetSuperTrackedQuestID", "SendChatMessage", "ShowUIPanel"):
        assert bad not in src, bad
    assert 'CreateFrame("Button", nil, mm)' in src  # pins are our own children of Minimap
    assert "HereBeDragons-Pins-2.0" in src and "RemoveMinimapIcon" in src


def test_radar_off_by_default_with_migration():
    src = (ADDON / "Modules" / "Radar.lua").read_text(encoding="utf-8")
    assert "shown = false," in src and "offByDefault" in src
    opt = (ADDON / "Options.lua").read_text(encoding="utf-8")
    assert "Afficher le radar" in opt and "MinimapPins" in opt


def test_layer_options():
    src = SRC.read_text(encoding="utf-8")
    for key in ("rares", "danger", "drops", "squad", "taxi", "services"):
        assert f"{key} = true" in src, key
    # 4.1 LAYOUT: no 2nd arrow near the minimap (the compass is THE pointer) -> the target layer is off by default
    assert "target = false" in src
    assert "path = true" not in src   # 4.0: « chemin sûr » removed with SafePath (a dead option)


@needs_lua
def test_syntax():
    assert subprocess.run([LUA.replace("lua5.1", "luac5.1"), "-p", str(SRC)]).returncode == 0


@needs_lua
@pytest.mark.parametrize("flavor", ["modern", "legacy", "forever"])
def test_scenario(flavor):
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--flavor", flavor, "--class", "HUNTER", "--scenario",
                        "tests/wow/minipins_scenario.lua", str(ADDON)],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
