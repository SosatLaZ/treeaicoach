"""CLEAN 4.0 / ADVICE 4.0.1: Modules/Edge2.lua — « Avantages » : 7 small legit advantages, each built on a FACT the
game shows (contested camp from tapped nameplates, quest item bad luck from Stats' measured drop rates, corpses the game
still flags as lootable (CanLootUnit) in solo, gathering tracking off, failed escort, useless breadcrumb quest,
world-buff yell in a capital). « Finished quests = next level » moved to « Conseils pro » (Coach.lua). Former Edge (« Coups d'avance ») merged in; guesses and duplicates removed.
Each one = one verb-first line (no « ? »), hidden unless useful, togglable, routed to the « Avantages » tab, never
automation, never chat, ≤ 80 kinstr/min in session_sim."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
SRC = (ADDON / "Modules" / "Edge2.lua").read_text(encoding="utf-8")


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["modern", "legacy", "forever"])
@pytest.mark.parametrize("cls", ["HUNTER", "WARRIOR", "MAGE"])
def test_edge2_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", cls,
                        "--scenario", str(ROOT / "tests/wow/edge2_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "RESULT ok" in r.stdout


def test_edge_module_removed():
    toc = (ADDON / "TreeAI.toc").read_text(encoding="utf-8").split()
    assert "Modules\\Edge.lua" not in toc and "Modules\\Edge2.lua" in toc
    assert not (ADDON / "Modules" / "Edge.lua").exists()


def test_7_features_each_togglable_and_used():
    keys = re.findall(r'^\s*\{ "(\w+)", "[^"]+", "(?:Farm|Quêtes|Ville)" \},$', SRC, re.M)
    assert len(keys) == 7 and len(set(keys)) == 7
    assert "questTimer" not in keys  # 4.0 QA: the timed-quest countdown has one owner, Quests
    assert "turninLevel" not in keys  # 4.0.1: moved to « Conseils pro » (Coach.lua)
    for k in keys:
        assert f'say("{k}"' in SRC, f"{k}: no line"
        assert f'drop("{k}")' in SRC or f'opt("{k}")' in SRC, f"{k}: never hidden / never checks its option"


def test_no_guesses_no_duplicates_left():
    for gone in ("respawnCamp", "namedRepop", "grindSpot", "eliteGuild", "ChatFrame_OpenChat", "dualSpec", "legacyNext",
                 "campBuff", "vendorFlip", "boeWorth", "patrolWarn", "bigPull", "restPlan", "trainerPick", "drinkSmart",
                 "dropCheap", "bestMob", "CHAT_MSG_COMBAT_XP_GAIN"):
        assert gone not in SRC, gone


def test_lines_start_with_a_verb_and_have_no_unknowns():
    texts = re.findall(r'say\("\w+", (?:fmt\()?"([^"]+)"', SRC)
    texts += re.findall(r'(?:text = (?:\w+(?:\.\w+)? and )?|^\s+or )fmt\("([^"]+)"', SRC, re.M)
    assert len(texts) >= 7
    verbs = ("Change", "Tue", "Vérifie", "Fouille", "Rends", "Finis", "Abandonne", "Active", "Reste")
    for t in texts:
        assert t.startswith(verbs), t
        assert "?" not in t and "nil" not in t, t


def test_never_automates_never_chats():
    for bad in ("BuyMerchantItem", "UseContainerItem", "PickupContainerItem", "DeleteCursorItem", "BuyTrainerService",
                "TargetUnit", "CastSpell", "InviteUnit", "SendChatMessage", "SetRaidTarget", "AcceptQuest",
                "CompleteQuest", "GetQuestReward", "SelectQuestLogEntry", "SetSelectedQuest"):
        assert bad not in SRC, bad
    assert "o.chat, o.tab = false, TAB" in SRC


def test_one_owner_for_bag_drop_advice():
    inv = (ADDON / "Modules" / "Inventory.lua").read_text(encoding="utf-8")
    assert "function M.DropTip(" in inv and "Edge2" not in inv


def test_session_sim_caps_edge2():
    sim = (ROOT / "tests" / "wow" / "session_sim.lua").read_text(encoding="utf-8")
    assert re.search(r"kinstrModuleMin = \{[^}]*Edge2 = \d+", sim)


def test_registered_in_core_and_plus_menu():
    core = (ADDON / "Core.lua").read_text(encoding="utf-8")
    assert "Edge2 = { lite = false" in core and "Edge = { lite" not in core
    assert 'adv = "Avant."' in (ADDON / "UI.lua").read_text(encoding="utf-8")
    assert 'RegisterTab(TAB, "Avantages"' in SRC
