"""Groupe (Modules/Group.lua, GROUPMERGE 4.0): one module for the group of 2–4 friends (with or without TreeAI), replacing
Squad + Missions + Team + the old Group. 4-client Lua simulation over a fake PARTY bus (wire v4, deltas only, ≤ 1 msg /
5 s, gap recovery, 3.9.x decoding), the real addon in the harness (cards, pull line, buffs, inspect, goals, « Prêt ? »,
meeting point, facades, summaries), static ToS checks, and the app's readers of the summaries it keeps writing
(summary.group v2, summary.missions v1). 4.0.1 GROUPJOIN (Modules/GroupJoin.lua, part of the same module): join a friend
fastest / profitably, meeting point + « J'y vais », vote, shared route + « Suivre le groupe », the group's intent inferred
from party chat + movements, one « Maintenant » line for the group. Fake names only."""
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
GROUP = ADDON / "Modules" / "Group.lua"
JOIN = ADDON / "Modules" / "GroupJoin.lua"
HERE = Path(__file__).parent
lua = pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 not installed")


def _code(path: Path = GROUP) -> str:
    return "\n".join(ln.split("--", 1)[0] for ln in path.read_text(encoding="utf-8").splitlines())


@lua
def test_group_lua_spec():
    r = subprocess.run(["lua5.1", str(HERE / "group_spec.lua"), str(GROUP)], capture_output=True, text=True, timeout=120,
                       cwd=ROOT)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-3000:] + r.stderr[-2000:]


@lua
@pytest.mark.parametrize("flavor,cls", [("forever", "HUNTER"), ("modern", "HUNTER"), ("legacy", "MAGE"),
                                        ("forever", "PRIEST"), ("modern", "DRUID"), ("forever", "WARRIOR")])
def test_group_real_addon_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(HERE / "harness.lua"), "-q", "--flavor", flavor, "--class", cls, "--scenario",
                        str(HERE / "group_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-3000:] + r.stderr[-2000:]


@lua
@pytest.mark.parametrize("flavor,cls", [("forever", "HUNTER"), ("modern", "HUNTER"), ("forever", "WARRIOR")])
def test_groupjoin_real_addon_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(HERE / "harness.lua"), "-q", "--flavor", flavor, "--class", cls, "--scenario",
                        str(HERE / "groupjoin_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=180, cwd=ROOT)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-3000:] + r.stderr[-2000:]


def test_one_module_in_toc():
    names = [x.strip() for x in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert "Modules\\Group.lua" in names
    # GroupJoin = part of « Groupe » (no RegisterModule of its own), loaded right after it
    assert names.index("Modules\\GroupJoin.lua") == names.index("Modules\\Group.lua") + 1
    assert "RegisterModule" not in _code(JOIN)
    for old in ("Squad", "Missions", "Team"):
        assert f"Modules\\{old}.lua" not in names, old
        assert not (ADDON / "Modules" / f"{old}.lua").exists(), old
    # Quests reads ns.Group (friends on a quest) and the facades must exist before the modules that read them
    for later in ("Quests", "Tracker", "Planner", "Export", "Dungeon", "QuickTravel", "Todo"):
        assert names.index("Modules\\Group.lua") < names.index(f"Modules\\{later}.lua"), later


def test_groupjoin_reads_chat_never_writes_and_keeps_no_text():
    code = _code(JOIN)
    for bad in ("SendChatMessage", "CastSpell", "TargetUnit", "UseItem", "TakeTaxiNode", "CreateFrame", "C_Timer", "SetScript",
                "PlaySound", ":Print(", "SendAddonMessage", "T:Send(", "QuestLogPushQuest", "SetUserWaypoint"):
        assert bad not in code, bad
    group = _code()
    assert 'RegisterEvent("CHAT_MSG_PARTY"' in group and 'RegisterEvent("CHAT_MSG_PARTY_LEADER"' in group
    # the chat text goes through pub() (secret in chat lockdown) and only derived places are kept in memory
    assert "text, sender = pub(text), pub(sender)" in code
    on_chat = code.split("function J.OnChat", 1)[1].split("\nfunction ", 1)[0]
    assert "text" not in on_chat.split("JS.chat[#JS.chat + 1] =", 1)[1].split("}", 1)[0]
    # the guidance is TreeAI's compass / zone of work / planned dungeon, on a click only
    assert 'source = "join"' in code and "Z.SetMode" in code and "D.SetPlanned" in code


def test_readme_documents_groupjoin():
    readme = (ROOT / "wow_addon" / "README.md").read_text(encoding="utf-8")
    for s in ("Modules/GroupJoin.lua", "T.GroupJoin", "E.Detour", "E.Intent", "Grp:Offer(", "GroupJoin.Destination()",
              "`ps`", "`go`", "`vote`", "`ballot`", "`plan`", "CHAT_MSG_PARTY", "summary.group.intent",
              "kinstrModuleMin.GroupJoin", "groupjoin_scenario.lua"):
        assert s in readme, s


def test_no_chat_no_automation_no_frames():
    code = _code()
    for bad in ("SendChatMessage", "QuestLogPushQuest", "CastSpell", "TargetUnit", "SetSelectedQuest", "SelectQuestLogEntry",
                "InspectUnit", "SetRaidTarget", "DoReadyCheck", "AcceptQuest", "CompleteQuest", "GetQuestReward",
                "ShowUIPanel", "SetParent", "CreateFrame", "C_Timer", "SetScript"):
        assert bad not in code, bad
    assert "T:NewTicker(" in code   # the single scheduler of the core, never a raw C_Timer ticker
    # the chat only carries short answers to commands the player typed (/tai partir, /tai pret, /tai objectif)
    starts = [(m.start(), m.group(1)) for m in re.finditer(r"\nfunction ([\w.:]+)", code)]
    for pos in [m.start() for m in re.finditer(r":Print\(", code)]:
        owner = max((st for st in starts if st[0] < pos), default=(0, "?"))[1]
        assert owner in {"M:SetLeave", "M:SetReady", "M:SlashGoal"}, owner


def test_one_protocol():
    code = _code()
    sent = set(re.findall(r'T:Send\("(\w+)"', code))
    assert sent == {"GRP"}, sent
    for legacy in ("SQS", "SQQ", "SQD", "QTM"):   # 3.9.x decoded for one version (receive only)
        assert f'"{legacy}"' in code, legacy
    assert '"PARTY"' in code and "RAID" not in code


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_syntax_no_globals():
    assert subprocess.run(["luac5.1", "-p", str(GROUP)], capture_output=True).returncode == 0
    listing = subprocess.run(["luac5.1", "-l", "-p", str(GROUP)], capture_output=True, text=True).stdout
    assert "SETGLOBAL" not in listing


def test_session_sim_single_cap():
    src = (HERE / "session_sim.lua").read_text(encoding="utf-8")
    m = re.search(r"kinstrModuleMin\.Group = (\d+)", src)
    assert m and int(m.group(1)) <= 150
    j = re.search(r"kinstrModuleMin\.GroupJoin = (\d+)", src)
    assert j and int(j.group(1)) <= 80   # ROUND7: ≤ 80 kinstr / min per module


# ---------------------------------------------------------------- the app reads the summaries (formats kept)
DB_GROUP = {"chars": {"Fakeone-Fakerealm": {"updated": 5, "summary": {"class": "HUNTER", "level": 26, "group": {
    "v": 2, "size": 2, "leader": "Fakeone", "xph": 12000,
    "members": [{"name": "Fakeone", "class": "Chasseur", "level": 26, "zone": "Hautebrande", "hp": 80, "me": True},
                {"name": "Fakefriend", "class": "Mage", "level": 24, "zone": "Hautebrande", "mana": 20, "leaveMin": 10}],
    "advices": [{"cat": "group", "prio": 1, "text": "Fakefriend est 2 niveaux derrière : partage-lui « Les ours »"},
                {"cat": "group", "prio": 2, "text": "Avant de tirer, attends : Fakefriend 20 % mana"}],
    "common": [{"id": 1, "title": "Les ours", "holders": 2, "done": 1}],
}}}}}


def test_ai_context_reads_group_snapshot_without_names():
    g = ac.character_context(DB_GROUP)["groupe_treeai"]
    assert g["conseils"][0] == "Mage 24 est 2 niveaux derrière : partage-lui « Les ours »"
    assert any("part dans 10 min" in m for m in g["membres"])
    assert g["quetes_communes"] == ["Les ours (2/2, 1 fini)"]
    assert "Fakefriend" not in json.dumps(g, ensure_ascii=False)


def test_ai_context_ignores_old_or_garbage_group():
    assert "groupe_treeai" not in ac.character_context({"chars": {"x": {"summary": {"group": {"v": 1}}}}})
    assert ac._squad({"summary": {"group": {"v": 2, "members": "nope"}}}) is None


DB_MISSIONS = {"chars": {"Fakeone-Fakerealm": {"updated": 5, "summary": {"class": "HUNTER", "level": 26, "missions": {
    "v": 1, "size": 2, "noAddon": 1,
    "members": [{"name": "Fakeone", "class": "Chasseur", "level": 26}, {"name": "Fakefriend", "class": "Mage", "level": 24}],
    "board": [{"title": "Sang de lion", "holders": 2, "done": 2, "pct": 100, "ready": True},
              {"title": "Les ours", "holders": 2, "done": 1, "pct": 70, "status": "Fakefriend : 3 Ours tué restants"}],
    "next": "Rendez ensemble « Sang de lion » (2/2 prêts)", "assign": [], "claims": [],
    "travel": ["Regroupez-vous : Fakefriend (Les Paluns) est dans une autre zone"], "crafts": [],
    "session": {"duration": 5400, "turned": 4, "missions": 0, "abandoned": 0, "deaths": 1,
                "levels": [{"name": "Fakefriend", "from": 23, "to": 24}],
                "goal": {"kind": "level", "text": "Objectif niveau 27 ensemble : 0/2 — toi 26 (40 %), Fakefriend 24 (10 %)"},
                "leaderboard": [{"name": "Fakeone", "xp": 12000}], "loot": [],
                "events": ["Fakefriend passe niveau 24"], "titles": ["Sang de lion"]},
}}}}}


def test_ai_context_reads_missions_without_names():
    m = ac.character_context(DB_MISSIONS)["missions_groupe"]
    assert m["missions"][0] == "Sang de lion : 2/2 prêts, 100 % (à rendre ensemble)"
    assert "Mage 24 : 3 Ours tué restants" in m["missions"][1]
    assert m["prochaine"].startswith("Rendez ensemble")
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
