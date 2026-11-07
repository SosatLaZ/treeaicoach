"""UIFIX (round 5): the 3.9.0 screenshot layout rebuilt with mock rects — no overlap, 4 tabs + « Plus »,
2-line rows, min width 300, single waypoint arrow, one-shot re-placement, interface reset, autorun hint,
radar legend/zoom, « Apparence » page."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["forever", "modern", "legacy"])
@pytest.mark.parametrize("cls", ["HUNTER", "MAGE"])
def test_uifix_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", cls,
                        "--scenario", str(ROOT / "tests/wow/uifix_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "RESULT ok" in r.stdout


def test_never_calls_protected_autorun():
    for p in ADDON.rglob("*.lua"):
        src = p.read_text(encoding="utf-8")
        assert "ToggleAutoRun(" not in src and "G.ToggleAutoRun" not in src, p
    assert "TOGGLEAUTORUN" in (ADDON / "Modules" / "Binds.lua").read_text(encoding="utf-8")


def test_layout_marks_trackers_party_minimap():
    src = (ADDON / "UI.lua").read_text(encoding="utf-8")
    for name in ("MinimapCluster", "ObjectiveTrackerFrame", "QuestWatchFrame", "WatchFrame", "Questie_BaseFrame",
                 "PartyMemberFrame1", "CompactPartyFrame", "CompactRaidFrameContainer", "DetailsBaseFrame1",
                 "Recount_MainWindow", "ChatFrame", "MainMenuBar", "PlayerFrame", "TargetFrame", "FocusFrame",
                 "BuffFrame", "LibDBIcon10_"):
        assert '"%s' % name in src, name
