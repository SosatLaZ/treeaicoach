"""Agent DATA: quest data truth (only in-log, unfinished objectives, keyed by questID) + corpse run (Speed)."""
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
ADDON = ROOT / "wow_addon" / "TreeAI"
LUA = shutil.which("lua5.1")


def src(name):
    return (ADDON / "Modules" / f"{name}.lua").read_text(encoding="utf-8")


def test_no_fuzzy_name_matching_left():
    # substring / prefix matching of mob names against objectives produced badges for other quests
    assert "o.name:find(name, 1, true)" not in src("Detect")
    assert "on:find(n, 1, true) == 1" not in src("Quests")
    assert "on:find(n, 1, true) or n:find(on, 1, true)" not in src("Compass")
    # the « map everything to the first item objective » fallback is gone
    assert "un seul objectif objet : on l'associe quand même" not in src("Highlight")


def test_game_signal_and_caches():
    d = src("Detect")
    for api in ("UnitIsRelatedToActiveQuest", "MapObjectives", "NpcObjective", "GameRelated", "QuestByTitle"):
        assert api in d, api
    h = src("Highlight")
    for ev in ("QUEST_ACCEPTED", "QUEST_REMOVED", "QUEST_TURNED_IN", "QUEST_LOG_UPDATE", "UNIT_QUEST_LOG_CHANGED"):
        assert ev in h, ev
    assert "QuestPlayer" in h  # party members' tooltip lines are labelled, never « mine »


def test_corpse_run_single_owner():
    s = src("Speed")
    assert "GetCorpseMapPosition" in s and "PLAYER_UNGHOST" in s and "PLAYER_DEAD" in s
    assert 'source = "corpse"' in s and "SetTarget" in s
    assert not (HERE.parents[1] / "wow_addon/TreeAI/Modules/Radar.lua").exists()  # (5.1 CLEAN) Radar removed: Speed alone
    assert "st.corpse" in src("Compass")


@pytest.mark.skipif(not LUA, reason="lua5.1 not installed")
@pytest.mark.parametrize("flavor", ["modern", "legacy", "forever"])
def test_data_scenario(flavor):
    r = subprocess.run([LUA, str(HERE / "harness.lua"), "-q", "--flavor", flavor, "--class", "HUNTER",
                        "--allow", "C_DeathInfo", "--allow", "C_TooltipInfo",
                        "--scenario", str(HERE / "data_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-3000:] + r.stderr[-2000:]
