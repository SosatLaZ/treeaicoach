"""Agent NAV: Compass redesign, NavTime (ETA calibration, recorded fake trips), Horizon strip, Glow.
Lua scenario on the three API flavours + static rules (no Blizzard frame writes, no projection API)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = Path(os.environ.get("TREEAI_WOW_ADDON") or ROOT / "wow_addon" / "TreeAI")
NEW = ["NavTime", "Horizon", "Glow"]
LUA = shutil.which("lua5.1")
needs_lua = pytest.mark.skipif(not LUA, reason="lua5.1 not installed")


def code(name):
    src = (ADDON / "Modules" / f"{name}.lua").read_text(encoding="utf-8")
    return "\n".join(l.split("--", 1)[0] for l in src.splitlines())


def test_toc_lists_new_modules():
    lines = [l.strip() for l in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    c = lines.index("Modules\\Compass.lua")
    assert lines.index("Modules\\NavTime.lua") > c and lines.index("Modules\\Horizon.lua") > c
    assert lines.index("Modules\\Glow.lua") < lines.index("Modules\\Highlight.lua")


@pytest.mark.parametrize("name", NEW + ["Compass"])
def test_no_taint_or_automation(name):
    src = code(name)
    for bad in ("SetParent(", "hooksecurefunc", "ShowUIPanel", "SetSuperTrackedQuestID", "CastSpell", "UseItem",
                "TargetUnit", "MoveForward", "WorldToScreen", "SendChatMessage", "SetBinding"):
        assert bad not in src, f"{name}: {bad}"


def test_horizon_and_compass_never_allocate_per_frame():
    # the fast paths (Step / Draw) build no table and no closure
    for name, fn in (("Compass", "function M.Step"), ("Horizon", "function Hz.Draw")):
        src = code(name)
        body = src[src.index(fn):]
        body = body[:body.index("\nend\n")]
        assert "{" not in body and "function(" not in body, f"{name}: allocation in {fn}"


def test_estimate_prefers_spf():
    src = code("NavTime")
    est = src[src.index("function N.Estimate"):]
    assert est.index("C.Travel") < est.index("N.Walk(")


@needs_lua
@pytest.mark.parametrize("flavor", ["modern", "legacy", "forever"])
def test_scenario(flavor):
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--flavor", flavor, "--class", "HUNTER", "--scenario",
                        "tests/wow/nav_scenario.lua", str(ADDON)], cwd=ROOT, capture_output=True, text=True,
                       timeout=300)
    out = r.stdout + r.stderr
    assert "RESULT ok" in out, out[-3000:]
