"""MACROFIX (round 5): « les macros n'ont pas l'air de marcher » — every real-client failure mode of macro
installation is reproduced by the mock (CreateMacro forbidden / ignored, GetMacroIndexByName missing, rank-1 ID
unknown, slots full) and the fallbacks are asserted (one attempt, copy-paste window, diagnostic)."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
SRC = (ADDON / "Modules" / "Macros.lua").read_text(encoding="utf-8")


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["forever", "modern", "legacy"])
@pytest.mark.parametrize("cls", ["HUNTER", "WARRIOR"])
def test_macrofix_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", cls,
                        "--scenario", str(ROOT / "tests/wow/macrofix_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "RESULT ok" in r.stdout


def test_no_glyphs_missing_from_wow_fonts():
    # FRIZQT / chat font have no ✓ ✗ → ‹ › : Core.SafeText rewrites them in suggestions only, not in chat
    for ch in "✓✗→‹›":
        assert ch not in SRC, ch


def test_create_macro_checked_by_return_value_and_forbidden_listener():
    assert "CreateMacro, gn" in SRC and "macroName(ret) == gn" in SRC   # (MACRO3) gn = name in game (renamable)
    assert '"ADDON_ACTION_FORBIDDEN"' in SRC
    assert "Constants.MacroConsts" in SRC
    assert "FindSpellBookSlotForSpell" in SRC
    assert "ShowMacroFrame" in SRC   # only from the « Ouvrir les macros » button
