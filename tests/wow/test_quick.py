"""QUICK1: Modules/Quick.lua — « Raccourcis » bar (quest item, next target, share, sell+repair, mail, hearth,
mount) + quest automation (accept / turn in / obvious reward, never when two upgrades conflict), safe gossip,
auto-loot hint. Secure frames only touched out of combat; no chat output; every automatism is an option."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
SRC = (ADDON / "Modules" / "Quick.lua").read_text(encoding="utf-8")


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["modern", "legacy", "forever"])
@pytest.mark.parametrize("cls", ["HUNTER", "WARRIOR"])
def test_quick_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", cls,
                        "--scenario", str(ROOT / "tests/wow/quick_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "RESULT ok" in r.stdout


def test_in_toc():
    assert "Modules\\Quick.lua" in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").split()


def test_every_option_has_a_section_and_is_used():
    keys = re.findall(r'\{ key = "(\w+)", type = "toggle", label = "[^"]+", section = "[^"]+" \}', SRC)
    assert len(keys) >= 12
    for k in keys:
        assert f'opt("{k}")' in SRC, k


def test_no_chat_and_secure_frames_out_of_combat():
    assert "Print(" not in SRC and "AddMessage" not in SRC and "SendChatMessage" not in SRC
    # secure templates only created by makeButton, itself refused in combat; layout always through RunOutOfCombat
    assert SRC.count('"SecureActionButtonTemplate"') == 1
    assert 'T:RunOutOfCombat("Quick:layout"' in SRC
    for bad in ("CastSpell", "TargetUnit", "UseAction", "SetBinding", "SelectQuestLogEntry", "SetSelectedQuest",
                "ShowUIPanel", "BuyMerchantItem", "MoveForwardStart"):
        assert bad not in SRC, bad
