"""LITE (round 5, « je lag terrible ») : profil « essentiel » par défaut (posé une fois, avis + « Tout réactiver »),
conditions groupe / HV / instance, modules coupés = événements et minuteurs vraiment désinscrits, onglet
« Plus → Modules », /tai perf ; plus d'OnUpdate permanent dans les modules essentiels."""
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
def test_lite_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--no-storm", "--profile", "essentiel",
                        "--flavor", flavor, "--class", cls, "--scenario", str(ROOT / "tests/wow/lite_scenario.lua"),
                        str(ADDON)], capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "RESULT ok" in r.stdout


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
def test_essentiel_storm():
    """the event / click storm with the default profile (most modules asleep, then woken by clicks)"""
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--profile", "essentiel",
                        "--flavor", "forever", "--class", "HUNTER", str(ADDON)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]


def test_every_module_listed_with_a_description():
    core = (ADDON / "Core.lua").read_text(encoding="utf-8")
    names = set()
    for p in (ADDON / "Modules").glob("*.lua"):
        src = p.read_text(encoding="utf-8")
        names.update(re.findall(r'RegisterModule\(\s*"(\w+)"', src))
        names.update(re.findall(r'RegisterModule,\s*T(?:reeAI)?,\s*"(\w+)"', src))
    assert len(names) >= 35
    for n in names:
        assert re.search(r"\b%s = \{ lite = (true|false)[^\n]*desc = \"" % n, core), n


def test_essential_modules_have_no_permanent_onupdate():
    # the always-on modules must not run code every frame (Compass animates its arrow only while shown)
    for name in ("Hunter", "Group", "Quests", "Tracker", "Todo", "Planner", "Macros", "Abandon"):
        src = (ADDON / "Modules" / (name + ".lua")).read_text(encoding="utf-8")
        assert 'SetScript("OnUpdate", onUpdate)' not in src, name
