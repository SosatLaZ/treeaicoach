"""PathDraw (agent PATH): the Route/Planner path drawn on the world map (dashed segments + numbered badges +
transport icons, on our own overlay) and as a rotating dotted trail on the minimap (pooled, ≤ 5 Hz).
Taint rules (static) + mocked 4-stop route scenario on three API flavours + session_sim CPU cap."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = Path(os.environ.get("TREEAI_WOW_ADDON") or ROOT / "wow_addon" / "TreeAI")
SRC = ADDON / "Modules" / "PathDraw.lua"
LUA = shutil.which("lua5.1")
needs_lua = pytest.mark.skipif(not LUA, reason="lua5.1 not installed")


def code():
    return "\n".join(l.split("--", 1)[0] for l in SRC.read_text(encoding="utf-8").splitlines())


def test_toc_right_after_route():
    lines = [l.strip() for l in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert lines.index("Modules\\PathDraw.lua") == lines.index("Modules\\Route.lua") + 1


def test_taint_rules():
    src = code()
    for bad in ("SetParent(", "hooksecurefunc", "SetCVar", "ShowUIPanel", "SetUserWaypoint", "SetSuperTracked",
                "CreateFrame(\"Frame\", nil, child)", "CreateFrame(\"Frame\", nil, sc)", "CreateFrame(\"Frame\", nil, wm)"):
        assert bad not in src, bad
    assert "AddDataProvider" in src and "MapCanvasDataProviderMixin" in src     # Blizzard's supported pattern
    assert "SetStartPoint" in src and "CreateLine" in src                        # Line objects, with a fallback
    assert 'CF("Frame", nil, up)' in src and 'CF("Frame", nil, mm)' in src       # our frames: UIParent / Minimap


def test_options_and_budget():
    src = SRC.read_text(encoding="utf-8")
    for label in ("Afficher le chemin", "Couleur", "Épaisseur", "minimap"):
        assert label in src, label
    sim = (ROOT / "tests" / "wow" / "session_sim.lua").read_text(encoding="utf-8")
    assert "PathDraw = 60" in sim


@needs_lua
def test_syntax():
    luac = LUA.replace("lua5.1", "luac5.1")
    if shutil.which(luac):
        assert subprocess.run([luac, "-p", str(SRC)]).returncode == 0


@needs_lua
@pytest.mark.parametrize("flavor", ["modern", "legacy", "forever"])
def test_scenario(flavor):
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--flavor", flavor, "--class", "HUNTER", "--scenario",
                        "tests/wow/pathdraw_scenario.lua", str(ADDON)], cwd=ROOT, capture_output=True, text=True,
                       timeout=300)
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "RESULT ok" in out, out[-4000:]
