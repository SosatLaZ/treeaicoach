"""UIREDO: the main « TreeAI » window (Leatrix-like chrome, left navigation, search, one action button), the
« Prochaine étape » card (replaces the always-on text panel), the Adventure-Guide « Quêtes » page, the « Trajet »
page, the Dungeon-Journal « Donjons » cards + detail, the « Options » page (TIDY engine, search), key binding —
harness scenario on the three API flavours + static checks."""
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["modern", "legacy", "forever"])
@pytest.mark.parametrize("cls", ["HUNTER", "WARRIOR"])
def test_uiredo_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", cls,
                        "--scenario", str(ROOT / "tests/wow/uiredo_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
def test_uiredo_safe_profile():
    """Mode sûr (what every player gets): the window and the card are on."""
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", "forever", "--profile", "sur",
                        "--scenario", str(ROOT / "tests/wow/uiredo_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr


def test_toc_and_binding():
    lines = [l.strip() for l in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert lines.index("Modules\\Hub.lua") < lines.index("Modules\\Window.lua") < lines.index("Modules\\WindowPages.lua")
    assert lines.index("Modules\\Dungeon.lua") < lines.index("Modules\\Window.lua")
    root = ET.parse(ADDON / "Bindings.xml").getroot()
    b = [x for x in root if x.get("name") == "TREEAI_MAIN"]
    assert len(b) == 1 and b[0].get("header") == "TREEAI" and b[0].get("default") is None
    assert "TreeAI.ToggleMain()" in b[0].text
    src = (ADDON / "Modules" / "Window.lua").read_text(encoding="utf-8")
    assert "BINDING_NAME_TREEAI_MAIN" in src and "T.ToggleMain = function" in src


def test_rendering_only_no_protected_calls():
    """The window / card only render existing contracts: no protected API, no copied artwork files."""
    for name in ("Window.lua", "WindowPages.lua"):
        src = (ADDON / "Modules" / name).read_text(encoding="utf-8")
        for bad in ("CastSpell", "UseItem", "TargetUnit", "PickupContainerItem", "SetBinding", "C_Timer",
                    "AbandonQuest", "SetAbandonQuest", "SelectQuestLogEntry"):
            assert bad + "(" not in src, (name, bad)
        assert ".blp" not in src and ".tga" not in src, name
    assert not list(ADDON.rglob("*.blp")) and not list(ADDON.rglob("*.tga"))
