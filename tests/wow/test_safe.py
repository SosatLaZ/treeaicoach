"""REVIEW: « mode sûr » — only the robust modules on by default (one-time migration for new and existing players),
every other module « bêta » (off, one click in « Plus → Modules » or the minimap hub)."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["modern", "legacy", "forever"])
@pytest.mark.parametrize("cls", ["HUNTER", "WARRIOR"])
def test_safe_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--no-storm", "--profile", "sur",
                        "--flavor", flavor, "--class", cls, "--scenario", str(ROOT / "tests/wow/safe_scenario.lua"),
                        str(ADDON)], capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
def test_safe_storm():
    """What a new player gets, under the full event storm: no error, no blocked action."""
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--profile", "sur",
                        "--flavor", "forever", "--class", "HUNTER", str(ADDON)],
                       capture_output=True, text=True, timeout=300, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout


def test_safe_set_is_declared():
    src = (ADDON / "Core.lua").read_text(encoding="utf-8")
    assert "TreeAI.SAFE_MODULES" in src and "PROFILE_V = 2" in src
    assert "Profil Simple : les autres fonctions (bêta) s'allument dans « Fonctions »" in src
