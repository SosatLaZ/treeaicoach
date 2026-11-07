"""CHEAT25: Modules/Edge.lua — 25 legit « cheated » advantages (learned respawn timers from the GUID spawn time,
grinding spot from QuestieDB, guild helpers for elite quests, vendor -> AH flips, BoE worth, food for 1 h…).
Each one = one actionable line (verb first, no « ? », ≤ 2 lines), hidden unless relevant, togglable, never
automation (no buy / sell / target / cast: a click opens at most the compass, the quest log, a pre-filled
whisper or the minimap tracking)."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
SRC = (ADDON / "Modules" / "Edge.lua").read_text(encoding="utf-8")


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["modern", "legacy", "forever"])
@pytest.mark.parametrize("cls", ["HUNTER", "WARRIOR", "MAGE"])
def test_edge_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", cls,
                        "--scenario", str(ROOT / "tests/wow/edge_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "RESULT ok" in r.stdout


def test_in_toc_last():
    toc = (ADDON / "TreeAI.toc").read_text(encoding="utf-8").split()
    assert "Modules\\Edge.lua" in toc


def test_25_features_each_togglable_and_used():
    keys = re.findall(r'^\s*\{ "(\w+)", "[^"]+", "(?:Groupe|Farm|Quêtes|Or|Progrès)" \},$', SRC, re.M)
    assert len(keys) == 25 and len(set(keys)) == 25
    for k in keys:
        assert f'say("{k}"' in SRC, f"{k}: no line"
        assert f'drop("{k}")' in SRC or f'opt("{k}")' in SRC, f"{k}: never hidden / never checks its option"


def test_lines_start_with_a_verb_and_have_no_unknowns():
    texts = re.findall(r'say\("\w+", (?:fmt\()?"([^"]+)"', SRC)
    assert len(texts) >= 25
    verbs = ("Demande", "Laisse", "Reste", "Farme", "Tue", "Garde", "Parle", "Abandonne", "Achète", "Vends", "Lancez",
             "Dépense", "Va ", "Apprends", "Active", "Aide", "Remonte", "Monte", "Pose")
    for t in texts:
        assert t.startswith(verbs), t
        assert "?" not in t and "nil" not in t, t


def test_never_automates():
    # no protected / automation call, no buying, selling, targeting or casting from this module
    for bad in ("BuyMerchantItem", "UseContainerItem", "PlaceAuctionBid", "PostAuction", "TargetUnit", "CastSpell",
                "InviteUnit", "SendChatMessage", "PickupMerchantItem", "AcceptQuest", "AbandonQuest",
                "SetAbandonQuest", "CompleteQuest", "GetQuestReward"):
        assert bad not in SRC, bad
    # the only game-state change is the minimap tracking, inside a click (TreeAI:UserAction)
    assert "T:UserAction(function() MM.SetTracking(idx, true) end)" in SRC


def test_respawn_needs_a_real_spawn_time():
    # learned timers come from the creature GUID spawn time, never from « a new mob of the same name appeared »
    assert "function E.SpawnTime" in SRC and "sampleFromGUID" in SRC
    assert "E.Respawn(rec)" not in SRC or "#rec.s < 3" in SRC


def test_session_sim_has_edge_budgets():
    sim = (ROOT / "tests" / "wow" / "session_sim.lua").read_text(encoding="utf-8")
    for k in ("edgeNowPct", "edgeDistinctMax", "edgeKinstrMin"):
        assert k in sim
