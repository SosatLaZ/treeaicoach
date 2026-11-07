"""POLISH: anti-bruit (dédoublonnage par sens, budget, encadré ≤ 1/20 s, combat), préréglages, assistant,
page « Tout » cliquable, modules on/off en direct, profil perso, placement auto, mode édition."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["modern", "legacy"])
@pytest.mark.parametrize("cls", ["HUNTER", "MAGE"])
def test_polish_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", cls,
                        "--scenario", str(ROOT / "tests/wow/polish_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "RESULT ok" in r.stdout


def test_layout_never_moves_foreign_frames():
    src = (ADDON / "UI.lua").read_text(encoding="utf-8")
    layout = src[src.index("Placement automatique de NOS cadres"):]
    # the scan only reads (IsVisible / GetRect…); SetPoint is only called on our own frames (cf, tf, panel…)
    for line in layout.splitlines():
        if "SetPoint" in line and "call(" in line:
            assert any(v in line for v in ("call(cf,", "call(tf,", "call(rf,", "call(f, \"SetPoint\"")), line


def test_readme_documents_new_api():
    readme = (ROOT / "wow_addon" / "README.md").read_text(encoding="utf-8")
    for key in ("RegisterMovable", "ApplyPreset", "AutoLayout", "TOPIC_IDS", "SetEditMode", "TreeAI.Fmt"):
        assert key in readme, key
