"""Missions (group quest tracking): 4-client Lua simulation, real-addon harness scenario, taint guard and
the AI context reader for « Hey coach, on en est où ? » (fake names only)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from treeaicoach.wow import ai_context as ac

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
MISSIONS = ADDON / "Modules" / "Missions.lua"
HERE = Path(__file__).parent
lua = pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 not installed")


@lua
def test_missions_lua_spec():
    r = subprocess.run(["lua5.1", str(HERE / "missions_spec.lua"), str(ADDON / "Modules" / "Squad.lua"), str(MISSIONS)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-3000:] + r.stderr[-2000:]


@lua
@pytest.mark.parametrize("flavor", ["modern", "legacy"])
def test_missions_real_addon_scenario(flavor):
    r = subprocess.run(["lua5.1", str(HERE / "harness.lua"), "-q", "--flavor", flavor, "--scenario",
                        str(HERE / "missions_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-3000:] + r.stderr[-2000:]


def test_missions_in_toc_after_squad():
    names = [x.strip() for x in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert names.index("Modules\\Missions.lua") == names.index("Modules\\Squad.lua") + 1


def test_missions_never_taints_blizzard_ui():
    src = re.sub(r"--[^\n]*", "", MISSIONS.read_text(encoding="utf-8"))
    for bad in ("SelectQuestLogEntry", "SetSelectedQuest", "QuestMapFrame", "ShowUIPanel", "SetParent",
                "QuestLogPushQuest", "DoReadyCheck", "AcceptQuest", "CompleteQuest", "GetQuestReward"):
        assert bad not in src, bad
    assert "SetRaidTarget" in src and "InCombat" in src  # marking only on click, out of combat


DB = {"chars": {"Fakeone-Fakerealm": {"updated": 5, "summary": {"class": "HUNTER", "level": 26, "missions": {
    "v": 1, "size": 2, "noAddon": 1,
    "members": [{"name": "Fakeone", "class": "Chasseur", "level": 26}, {"name": "Fakefriend", "class": "Mage", "level": 24}],
    "board": [{"title": "Sang de lion", "holders": 2, "done": 2, "pct": 100, "ready": True},
              {"title": "Les ours", "holders": 2, "done": 1, "pct": 70, "status": "Fakefriend : 3 Ours tué restants"}],
    "next": "Rendez ensemble « Sang de lion » (2/2 prêts)",
    "assign": ["« Les ours » : Fakefriend loote Croc d'abord (3)"],
    "claims": [{"quest": "Les ours", "by": "Fakefriend"}],
    "travel": ["Rendez-vous : Hautebrande"], "crafts": ["Fakefriend (Couture 90) : sacs en laine (8) pour Fakeone"],
    "session": {"duration": 5400, "turned": 4, "missions": 2, "abandoned": 1, "deaths": 1,
                "levels": [{"name": "Fakefriend", "from": 23, "to": 24}],
                "goal": {"kind": "level", "text": "Objectif : niveau 27 pour tous — 0/2, 40 % · ~1 h 10 (Fakefriend)"},
                "leaderboard": [{"name": "Fakeone", "xp": 12000}], "loot": ["Fakefriend : 3 objets, 1 bleu"],
                "events": ["Fakefriend a fini « Les ours »"], "titles": ["Sang de lion"]},
}}}}}


def test_ai_context_reads_missions_without_names():
    m = ac.character_context(DB)["missions_groupe"]
    assert m["missions"][0] == "Sang de lion : 2/2 prêts, 100 % (à rendre ensemble)"
    assert "Mage 24 : 3 Ours tué restants" in m["missions"][1]
    assert m["prochaine"].startswith("Rendez ensemble")
    assert m["pris_en_charge"] == ["Mage 24 : Les ours"]
    assert m["session"]["quetes_rendues"] == 4 and m["session"]["duree_min"] == 90
    assert m["session"]["niveaux"] == ["Mage 24 23→24"]
    assert "niveau 27" in m["session"]["objectif"] and m["session"]["classement_xp"] == ["Chasseur 26 +12000 XP"]
    assert m["membres_sans_treeai"] == 1
    dumped = json.dumps(m, ensure_ascii=False)
    assert "Fakefriend" not in dumped and "Fakeone" not in dumped


def test_ai_context_ignores_bad_missions():
    assert ac._missions({"summary": {"missions": {"v": 9}}}) is None
    assert ac._missions({"summary": {"missions": {"v": 1, "board": "x", "session": 3}}}) is None
    assert "missions_groupe" not in ac.character_context({"chars": {"x": {"summary": {}}}})
