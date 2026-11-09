"""4.1 LAYOUT: what the player sees by default and the reorganised window (tests/wow/layout_scenario.lua) — real-client
geometry, level-29 hunter in a group of 4 (party frames top-left), forever + modern: on screen only the button strip,
the compass, the minimap button and its pins (no Horizon / Watch / meter / radar / combat bar), nothing over the party,
player or target frames, the quest tracker or the minimap, nothing in the chat, no blocked action; the one-time 4.1
migration; window first on the right half then where the player left it, 7 pages, every old page id redirected, the 4
quest modes (guarded), Raccourcis, Affichage, « Partager », search across pages, notices on their own page only; QoL
quest dialogs (accept what the plan wants, Maj skips, turn in, a reward choice highlighted never picked); Suivi TreeAI
never over Questie's tracker. Static checks: defaults that keep the screen clean."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
ALLOW = ["AcceptQuest", "CompleteQuest", "GetQuestReward", "GetQuestID", "GetQuestMoneyToGet", "GetTitleText",
         "IsQuestCompletable", "QuestGetAutoAccept", "QuestLogPushQuest", "C_GossipInfo", "ShortestPathForever",
         "ShortestPathForeverDB"]


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["forever", "modern"])
def test_layout_scenario(flavor: str) -> None:
    cmd = ["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--no-storm", "--geometry", "ready=1", "--profile", "sur",
           "--flavor", flavor, "--class", "HUNTER", "--level", "29"]
    for a in ALLOW:
        cmd += ["--allow", a]
    cmd += ["--scenario", str(ROOT / "tests/wow/layout_scenario.lua"), str(ADDON)]
    env = dict(os.environ)
    env.pop("TREEAI_WOW_FORCE_FLAVOR", None)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=240, cwd=ROOT, env=env)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-6000:] + r.stderr[-2000:]


def _src(name: str) -> str:
    return (ADDON / "Modules" / name).read_text(encoding="utf-8")


def test_clean_screen_defaults() -> None:
    core = (ADDON / "Core.lua").read_text(encoding="utf-8")
    m = re.search(r"TreeAI\.SAFE_V = (\d+)\b", core)   # 3 = the 4.1 LAYOUT migration (4 = Prof / CityRun added)
    assert m and int(m.group(1)) >= 3
    for n in ("Horizon", "Radar", "QuickFight", "Quick", "MinimapPins"):
        assert re.search(r"SAFE_REMOVED = \{[^}]*\b%s = 3" % n, core), n
    assert "defaults = { enabled = true, shown = false, stops = 3 }" in _src("Horizon.lua")
    assert "target = false" in _src("MinimapPins.lua")
    assert "hint = false" in _src("Abandon.lua")
    assert "questAuto = false" in _src("QoL.lua")
    assert "onScreen = false" in _src("Coach.lua")
    # unsolicited notices: their own page only
    assert 'ownPage = "addons"' in _src("Addons.lua")
    assert 'opts.ownPage = "macros"' in _src("Macros.lua")
    assert "e.ownPage" in core and "if not e.ownPage then cand" in core


def test_window_navigation_declared() -> None:
    src = _src("Window.lua")
    assert 'W.NAV = { "quests", "route", "group", "dungeon", "gear", "prof", "hv", "hunter", "coach", "binds", "settings" }' in src
    assert 'W.ALIAS = { now = "quests", perso = "gear" }' in src
    # first opening: the right half, never the centre
    assert 'anchorTo(win, "CENTER", 0, 40)' not in src
    assert 'anchorTo(f, "RIGHT", -W.FIRST.right, W.FIRST.y)' in src


def test_reward_choice_never_picked() -> None:
    src = _src("QoL.lua")
    body = src[src.index("function Q.OnQuestComplete"):src.index("function Q.OnGossip")]
    i_choice = body.index("if n > 1 then")
    i_pick = body.index("GetQuestReward")
    assert i_choice < i_pick and "return \"highlight\"" in body[:i_pick]
