"""UI2 (round 5): sounds per kind (cooldowns, only danger in combat), polished windows (header, ESC for our
windows only, collapse, bounds, fade), live placement (growing tracker, drop on a frame, 21:9 / 4:3),
dispositions Compacte / Confort / Coins."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["forever", "modern", "legacy"])
@pytest.mark.parametrize("cls", ["HUNTER", "MAGE"])
def test_ui2_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", cls,
                        "--scenario", str(ROOT / "tests/wow/ui2_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "RESULT ok" in r.stdout


def test_sounds_go_through_core():
    """Only Core's sound system calls PlaySound (Tracker keeps a guarded fallback for old cores)."""
    for p in ADDON.rglob("*.lua"):
        src = p.read_text(encoding="utf-8")
        if "PlaySound" not in src:
            continue
        rel = p.relative_to(ADDON).as_posix()
        assert rel in ("Core.lua", "Modules/Tracker.lua"), rel
    tracker = (ADDON / "Modules" / "Tracker.lua").read_text(encoding="utf-8")
    assert 'T:Sound("quest")' in tracker


def test_sound_kinds_defined():
    core = (ADDON / "Core.lua").read_text(encoding="utf-8")
    for kind in ("danger", "alert", "rare", "quest", "group", "ah", "levelup", "reminder"):
        assert re.search(r"^\s*%s\s*=\s*\{ label =" % kind, core, re.M), kind
    assert 'kind ~= "danger" and self:InCombat()' in core


def test_esc_only_our_frames():
    """UISpecialFrames only ever receives TreeAI… names (never a Blizzard frame)."""
    theme = (ADDON / "Modules" / "Theme.lua").read_text(encoding="utf-8")
    assert 'name:find("^TreeAI")' in theme and '"TreeAIEsc_"' in theme
    for p in ADDON.rglob("*.lua"):
        for line in p.read_text(encoding="utf-8").splitlines():
            if "UISpecialFrames" in line and "tinsert" in line:
                assert "TreeAI" in line, (p, line)
