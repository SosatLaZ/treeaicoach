"""CONTENTFIX (round 5, in-game screenshot): silence while dead / ghost / on a taxi (buff reminders also mounted /
resting), one single macro line, death recap without invented numbers, clean objective text, no glyphs the WoW
fonts cannot draw, « Mode simple » default preset offered once to existing users."""
# (5.1 CLEAN) harness default = the player's profile (« sur ») ; this file tests modules that are off there: « tout »
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["modern", "legacy", "forever"])
@pytest.mark.parametrize("cls", ["HUNTER", "WARRIOR"])
def test_content_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--profile", "tout", "--flavor", flavor, "--class", cls,
                        "--scenario", str(ROOT / "tests/wow/content_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "RESULT ok" in r.stdout


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["modern", "legacy", "forever"])
@pytest.mark.parametrize("cls", ["HUNTER", "MAGE", "WARRIOR", "PRIEST", "ROGUE"])
def test_every_line_robust(flavor, cls):
    # every line of every module, in dead / ghost / combat / taxi / instance / solo / group / level 1 / 60
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--profile", "tout", "--flavor", flavor, "--class", cls,
                        "--scenario", str(ROOT / "tests/wow/robustness_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=180, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
    assert "RESULT ok" in r.stdout


def test_never_suggest_binding_autorun():
    src = (ADDON / "Modules" / "Binds.lua").read_text(encoding="utf-8")
    assert "TOGGLEAUTORUN" in src and "not isMovement(cmd)" in src  # a key bound to autorun (« ² ») is never "free"


def test_no_old_noise_texts():
    src = "\n".join(p.read_text(encoding="utf-8") for p in (ADDON / "Modules").glob("*.lua"))
    for bad in ("Nouvelle(s) macro(s) dispo", "Macros expertes prêtes pour ta classe", "dégâts en 12 s (plus gros",
                "Aucun aspect actif :", '"▶ "'):
        assert bad not in src, bad


def test_suggest_texts_have_no_unknown_fallbacks():
    # a « ? » in a suggestion text means a number / name we do not have: never show it
    pat = re.compile(r"(?:M|self|R):Suggest\([^\n]*\"\?\"|suggest\([^\n]*\"\?\"")
    for p in (ADDON / "Modules").glob("*.lua"):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "SLOT_FR" in line:
                continue
            assert not pat.search(line), f"{p.name}:{i}: {line.strip()}"
