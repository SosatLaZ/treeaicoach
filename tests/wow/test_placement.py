"""PLACEBUG (4.0.1): « quand je lance le jeu, tout est bien au bon endroit, mais une seconde après tout se remet dans un
coin » (real client, 4.0.0). The harness runs on the real-client geometry (tests/wow/geometry.lua, harness --geometry):
UIParent unsized and every rect nil until the first frame after PLAYER_ENTERING_WORLD, the UI scale applied after login
(UI_SCALE_CHANGED / DISPLAY_SIZE_CHANGED fired during the loading screen or when the scale lands), Edit Mode re-anchoring
Blizzard frames, StartMoving / StopMovingOrSizing re-anchoring like the client (nearest point, or the old
« TOPLEFT relative to BOTTOMLEFT » style). Sessions are chained through the SavedVariables (--dump / --savedvars):
fresh login (one placement, once the screen is stable, nothing in a corner) → drags + edit-mode move → relog (login race),
/reload, 2nd PLAYER_ENTERING_WORLD, UI scale change, resolution changes: the player's frames keep their exact anchors;
4.0.0 SavedVariables with broken records are repaired. tests/wow/placement_scenario.lua has the checks."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = os.environ.get("TREEAI_WOW_ADDON") or str(ROOT / "wow_addon" / "TreeAI")
HARNESS = ROOT / "tests" / "wow" / "harness.lua"
SCENARIO = ROOT / "tests" / "wow" / "placement_scenario.lua"
OLD_SV = ROOT / "tests" / "wow" / "fixtures" / "placebug_old_sv.lua"
# the login race of the real client: scale events during the loading screen, the scale lands 2 s later, the other
# frames not measurable for 3 s after UIParent got its size
RACE = "events=early,scaleAt=2,rectsAt=3"

needs_lua = pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")


def run(mode: str, geometry: str, *, profile: str = "tout", savedvars: Path | None = None, dump: Path | None = None,
        cls: str = "HUNTER") -> subprocess.CompletedProcess:
    args = ["lua5.1", str(HARNESS), "-q", "--no-storm", "--geometry", geometry, "--flavor", "forever", "--class", cls,
            "--level", "28", "--profile", profile, "--scenario", str(SCENARIO)]
    if savedvars:
        args += ["--savedvars", str(savedvars)]
    if dump:
        args += ["--dump", str(dump)]
    args.append(ADDON)
    env = dict(os.environ, TREEAI_PLACEBUG_MODE=mode)
    env.pop("TREEAI_WOW_FORCE_FLAVOR", None)
    r = subprocess.run(args, capture_output=True, text=True, timeout=240, cwd=ROOT, env=env)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, f"[{mode} {geometry}]\n" + r.stdout[-5000:] + r.stderr[-2000:]
    return r


@needs_lua
@pytest.mark.parametrize("move", ["nearest", "topleft"])
def test_login_race_then_relog_scale_and_resolution(tmp_path, move):
    """Fresh login → drags (card header, Raccourcis handle, compass, main window) + edit mode → relog with the same race:
    exact anchors at +2 s / +14 s, nothing re-anchored, zoning + scale events move nothing, scale / resolution changes
    keep the player's anchors; both ways the client may re-anchor a dragged frame."""
    sv = tmp_path / "sv.lua"
    run("fresh", f"{RACE},move={move}", dump=sv)
    assert "__placebug" in sv.read_text(encoding="utf-8")
    run("relog", f"{RACE},move={move}", savedvars=sv)


@needs_lua
def test_reload_with_screen_already_laid_out(tmp_path):
    """/reload: the window is already laid out (no race, no scale change): nothing moves either."""
    sv = tmp_path / "sv.lua"
    run("fresh", "ready=1", dump=sv)
    run("reload", "ready=1", savedvars=sv)


@needs_lua
def test_late_scale_events_and_safe_mode_profile(tmp_path):
    """Scale events fired when the scale lands (after the first frame), « mode sûr » profile (what players get)."""
    sv = tmp_path / "sv.lua"
    run("fresh", "events=late,scaleAt=0.3,rectsAt=1", profile="sur", dump=sv)
    run("relog", "events=late,scaleAt=0.3,rectsAt=1", profile="sur", savedvars=sv)


@needs_lua
def test_broken_400_savedvars_repaired():
    """4.0.0 records saved off screen (anchor point kept, relative point lost) or without x / y: repaired once the
    screen is stable, nothing in a corner / off screen, the valid record kept."""
    run("olddata", RACE, savedvars=OLD_SV)


def test_no_premature_or_duplicate_placement_in_source():
    """Static guards: no fixed-delay re-placement left (4.0.0: After(1) on UI_SCALE_CHANGED, After(6) on READY), no
    invented 1920×1080 screen, drags hooked through StopMovingOrSizing, the live reflow skips player-placed frames."""
    src = (Path(ADDON) / "UI.lua").read_text(encoding="utf-8")
    assert "TreeAI:After(6, function() TreeAI:CheckAutoLayout()" not in src
    assert "TreeAI:After(1, function() pending = false; TreeAI:CheckAutoLayout()" not in src
    assert "or 1920" not in src.split("local function screenSize()", 1)[1].split("end\n", 1)[0]
    assert '"StopMovingOrSizing"' in src and "not playerPlaced(id)" in src
    assert "function TreeAI:RequestLayout" in src and "function TreeAI:ScreenStable" in src
