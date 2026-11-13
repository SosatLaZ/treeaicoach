"""5.0 INTEG — the player's REAL interface (tests/wow/realui_scenario.lua).

The screen of the player who judges TreeAI in the real game, rebuilt rectangle by rectangle from his capture
(2000×1125, TreeAI off, crowded capital): damage / threat meters, the « Suivi Forever » column of Shortest Path Forever,
chat, custom XP bar with another addon's waypoint arrow under it, minimap, the game's quest tracker, custom unit frames
and action bars, micro / bag bar, buffs. Known addons have named frames; the meters, unit frames, bars and arrow are
anonymous frames that only a generic scan of the visible top-level frames can see.

At UI scales 0.64 and 0.75, with the column named or anonymous: the strip, the compass, the toast and every other TreeAI
frame are on screen with ZERO overlap with any occupied rectangle and never in the middle of the screen; the generic
scan runs on placement only (never from an OnUpdate, none at rest); with Shortest Path Forever present TreeAI shows no
second route list and no second list of profession reagents; nothing in the chat, nothing blocked.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
LUA = shutil.which("lua5.1")
pytestmark = pytest.mark.skipif(not LUA, reason="lua5.1 absent")


def run(scale: str, spf: str, profile: str = "sur") -> str:
    env = dict(os.environ, REALUI_SCALE=scale, REALUI_SPF=spf, REALUI_DEFAULT="1" if profile == "sur" else "0")
    env.pop("TREEAI_WOW_FORCE_FLAVOR", None)
    r = subprocess.run([LUA, str(ROOT / "tests/wow/harness.lua"), "-q", "--no-storm", "--allow", "ShortestPathForever",
                        "--geometry", "ready=1,w=2000,h=1125", "--profile", profile, "--flavor", "forever",
                        "--class", "HUNTER", "--level", "30",
                        "--scenario", str(ROOT / "tests/wow/realui_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=300, cwd=ROOT, env=env)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-7000:] + r.stderr[-2000:]
    return r.stdout


@pytest.mark.parametrize("scale", ["0.64", "0.75"])
@pytest.mark.parametrize("spf", ["named", "anon"])
def test_real_screen_zero_overlap(scale: str, spf: str) -> None:
    out = run(scale, spf)
    for frame in ("TreeAIStrip", "TreeAICompassFrame", "TreeAIAlert"):
        assert re.search(rf"REALUI\s+{frame} : x \d+–\d+, y \d+–\d+ px", out), out[-3000:]
    m = re.search(r"scans génériques : (\d+) \(EnumerateFrames\) \+ (\d+) \(enfants d'UIParent\) en 34 s de jeu, dont (\d+) depuis", out)
    assert m and int(m.group(3)) == 0 and int(m.group(1)) + int(m.group(2)) <= 8, out[-3000:]
    assert "carte Métiers chez le marchand (Suivi Forever présent) : aucune" in out
    assert "chat:" not in out


def test_real_screen_every_module_on() -> None:
    """« tout » : the player switched everything on (Suivi TreeAI included) — still zero overlap."""
    out = run("0.64", "named", profile="tout")
    assert "REALUI   TreeAIStrip" in out


def test_generic_obstacles_are_read_on_placement_only() -> None:
    ui = (ADDON / "UI.lua").read_text(encoding="utf-8")
    assert "function TreeAI:ScreenObstacles(maxAge)" in ui and "function TreeAI:SpotIsFree(" in ui
    sc = (ADDON / "Modules" / "Shortcuts.lua").read_text(encoding="utf-8")
    # asked only when a place is chosen (PlaceStrip) or at the two one-off looks after login — never from a refresh
    assert sc.count("S.Avoid(W, H, true)") == 2
    body = sc[sc.index("function S.Refresh("):sc.index("function S.Refresh(") + 1500]
    assert "ScreenObstacles" not in body and "S.Recheck" not in body
