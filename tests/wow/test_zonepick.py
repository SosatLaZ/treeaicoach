"""4.1 ZONEPICK — « Choisir ma zone » + « Conseille-moi » (Modules/QuestBrain.lua data, Modules/WindowPages.lua page).

tests/wow/zonepick_scenario.lua replays the QUESTS world (level-30 Horde hunter, group of 4, frFR): the strip « Zone »
button, the top line of the Quêtes page and /tai zone open ONE picker; every zone row is honest (quests counted only if
available to him: level, faction, class, prerequisites, not done, not grey, no dungeon), ranked, starred, with French
names, real travel (flights), XP/h, group fit and a one-line why; the chips re-rank; a click switches the route to that
zone and the choice sticks; « Automatique »; « Conseille-moi » gives at most 3 lines that each apply in one click;
nothing in the chat, no blocked action — with QuestieDB and without it."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
ADDON = ROOT / "wow_addon" / "TreeAI"
LUA = shutil.which("lua5.1")
ALLOW = ["LibQuestieDB", "QuestDB", "NpcDB", "ObjectDB", "ItemDB", "TreeAIPlan", "IsQuestFlaggedCompleted", "QuestieLoader"]


def _run(flavor: str, variant: str) -> subprocess.CompletedProcess:
    cmd = [LUA, str(HERE / "harness.lua"), "-q", "--flavor", flavor, "--class", "HUNTER", "--profile", "sur"]
    for a in ALLOW:
        cmd += ["--allow", a]
    cmd += ["--scenario", str(HERE / "zonepick_scenario.lua"), str(ADDON)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=ROOT, check=False,
                          env={**os.environ, "ZP_VARIANT": variant})


@pytest.mark.skipif(not LUA, reason="lua5.1 not installed")
@pytest.mark.parametrize("flavor,variant", [("forever", "full"), ("modern", "full"),
                                            ("forever", "noquestie"), ("modern", "noquestie")])
def test_zone_picker_scenario(flavor, variant):
    r = _run(flavor, variant)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-6000:] + r.stderr[-2000:]


def _code(path: Path) -> str:
    return "\n".join(re.sub(r"--.*$", "", ln) for ln in path.read_text(encoding="utf-8").splitlines())


def test_public_api():
    qb = _code(ADDON / "Modules" / "QuestBrain.lua")
    for fn in ("function QB.ZoneRows(", "function QB.ZoneScan(", "function QB.Advice(", "function QB.PickZone(",
               "function QB.ZoneAuto(", "function QB.ZoneNow(", "function QB.TravelTo(", "function QB.ZoneQuestOK("):
        assert fn in qb, fn
    wp = _code(ADDON / "Modules" / "WindowPages.lua")
    for fn in ("function W.OpenZonePicker(", "function W.ToggleZonePicker(", "function W.ShowAdvice(", "function W.RenderZonePicker("):
        assert fn in wp, fn
    sc = _code(ADDON / "Modules" / "Shortcuts.lua")
    assert "ToggleZonePicker" in sc


def test_nothing_protected_no_chat_no_onupdate():
    for name in ("QuestBrain.lua", "WindowPages.lua"):
        code = _code(ADDON / "Modules" / name)
        for bad in ("SendChatMessage", ":Print(", "print(", 'SetScript("OnUpdate"', "AcceptQuest(", "CompleteQuest(",
                    "TakeTaxiNode", "hooksecurefunc", "SetBinding"):
            assert bad not in code, (name, bad)
