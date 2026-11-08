"""CHEAT2: Modules/Edge2.lua — 15 more legit « cheated » advantages (contested camp, best mob by measured XP/h, elite
patrols, drop bad luck, unlooted corpses, gathering loop, drink-or-chain, expiring buffs, big cooldowns for an elite,
rested planner, turn-in level-up, trainer order, class quests, quest timers, what to destroy when bags are full).
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


def test_in_toc_right_after_edge():
    toc = (ADDON / "TreeAI.toc").read_text(encoding="utf-8").split()
    i = toc.index("Modules\\Edge.lua")
    assert toc[i + 1] == "Modules\\Edge2.lua"


def test_15_features_each_togglable_and_used():
    keys = re.findall(r'^\s*\{ "(\w+)", "[^"]+", "(?:Farm|Combat|Progrès|Sacs)" \},$', SRC, re.M)
    assert len(keys) == 15 and len(set(keys)) == 15
    edge = (ADDON / "Modules" / "Edge.lua").read_text(encoding="utf-8")
    edge_keys = set(re.findall(r'^\s*\{ "(\w+)", "[^"]+", "\w+" \},$', edge, re.M))
    assert not set(keys) & edge_keys, "same key as Edge"
    for k in keys:
        if k != "dropCheap":   # dropCheap lives in Inventory's tooltip + the tab, not as its own line
            assert f'say("{k}"' in SRC, f"{k}: no line"
        assert f'drop("{k}")' in SRC or f'opt("{k}")' in SRC, f"{k}: never hidden / never checks its option"


def test_lines_start_with_a_verb_and_have_no_unknowns():
    texts = re.findall(r'say\("\w+", (?:fmt\()?"([^"]+)"', SRC)
    texts += re.findall(r'(?:text = (?:\w+(?:\.\w+)? and )?|^\s+or )fmt\("([^"]+)"', SRC, re.M)
    assert len(texts) >= 14
    verbs = ("Change", "Tue", "Attends", "Vérifie", "Fouille", "Va ", "Mange", "Enchaîne", "Renouvelle", "Garde",
             "Déconnecte", "Rends", "Apprends", "Fais", "Finis", "Farme", "Méfie", "Jette")
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
    assert "E2.DropTip" in inv and inv.count("M.DropTip()") >= 2


def test_session_sim_caps_edge2():
    sim = (ROOT / "tests" / "wow" / "session_sim.lua").read_text(encoding="utf-8")
    assert re.search(r"kinstrModuleMin = \{[^}]*Edge2 = 80", sim)


def test_registered_in_core_options_and_plus_menu():
    core = (ADDON / "Core.lua").read_text(encoding="utf-8")
    assert "Edge2 = { lite = false" in core
    assert '"Edge", "Edge2"' in (ADDON / "Options.lua").read_text(encoding="utf-8")
    assert 'adv = "Avant."' in (ADDON / "UI.lua").read_text(encoding="utf-8")
    assert 'RegisterTab(TAB, "Avantages"' in SRC
