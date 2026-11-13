"""5.0 STABLE — « jouer sans que ça bugue »: the core mechanisms that keep the game fluid (tests/wow/stable_scenario.lua):
quest-log / roster noise dropped, coalesced flows (T:OnBags / OnQuestLog / OnRoster / OnItemInfo / OnPlates), unit
filters, cached unit tooltips, T.Spikes (calls over 8 ms, frames over 12 ms), the watchdog (module put to sleep, ONE
line), T:SetQuestGuide (one switch for all the quest guidance), the API probe; and the app side: TreeAI-erreurs.txt shows
the spikes and the API probe kept in TreeAIDB.diag."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor,profile", [("forever", "sur"), ("forever", "tout"), ("modern", "tout"), ("legacy", "sur")])
def test_stable_scenario(flavor, profile):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--no-storm", "--flavor", flavor,
                        "--class", "HUNTER", "--level", "30", "--profile", profile,
                        "--scenario", str(ROOT / "tests/wow/stable_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=300, cwd=ROOT)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-6000:] + r.stderr[-2000:]
    assert "STABLE Pics de lenteur depuis" in r.stdout
    assert "ZzProbe · ZZ_WORK : 21.0 ms" in r.stdout


def test_core_publishes_the_contracts():
    core = (ADDON / "Core.lua").read_text(encoding="utf-8")
    for api in ("function TreeAI:OnBags(owner, fn, opts)", "function TreeAI:OnQuestLog(owner, fn, opts)",
                "function TreeAI:OnRoster(owner, fn, opts)", "function TreeAI:OnItemInfo(owner, fn, opts)",
                "function TreeAI:OnPlates(owner, fn, gone)", "function TreeAI:OnUnitTip(owner, fn, opts)",
                "function TreeAI:Guard(owner, name, fn)", "function TreeAI:QuestGuideOn()",
                "function TreeAI:SetQuestGuide(on)", "function TreeAI:Spread(owner, fn, a1, a2, a3, a4, ctx)",
                "function Spk.Note(modName, job, ms, calls, big)", "function API.QuestieMemo(src, name, raw)"):
        assert api in core, api
    assert 'self:Fire("QUEST_GUIDE_CHANGED", on)' in core
    # the watchdog never touches the window, the error report or the self-test (how the player sees / reports a problem)
    assert "exempt = { Window = true, Bugs = true, SelfTest = true }" in core


def test_errlog_shows_spikes_and_api_probe():
    from treeaicoach.wow import errlog
    db = {"diag": {
        "spikes": {"ver": "5.0.0", "since": 1794500000, "calls": 3, "frames": 1,
                   "asleep": {"Radar": {"why": "slow", "t": 1}, "Suivi": {"why": "err", "t": 2}},
                   "top": {1: {"m": "Trajet", "j": "QUEST_LOG_UPDATE", "ms": 23.4, "k": "appel", "c": True, "p": 31, "g": 4,
                               "t": 1794500100, "x": 2},
                           2: {"m": "Suivi", "j": "après 1.2 s", "ms": 31, "k": "image", "n": 14, "b": 9.2, "p": 0, "g": 0,
                               "t": 1794500200, "x": 1}},
                   "last": [{"m": "Trajet", "j": "QUEST_LOG_UPDATE", "ms": 11, "k": "appel", "t": 1794500100}]},
        "api": {"t": 1794500000, "lines": ["-- Talents", "GetTalentInfo : 8 retours (string, number, number)"]}}}
    text = errlog.build_text(db, now=1794600000)
    assert "== Pics de lenteur (mesurés en jeu par TreeAI) ==" in text
    assert "3 appel(s) de plus de 8 ms, 1 image(s) de plus de 12 ms." in text
    assert "Mis en pause par le chien de garde : Radar (trop lent), Suivi (erreurs)" in text
    assert " 1. image entière 31.0 ms (14 appels, le plus gros : Suivi · après 1.2 s 9.2 ms)" in text
    assert " 2. Trajet · QUEST_LOG_UPDATE : 23.4 ms ×2 (en combat, 31 barres de nom, groupe de 4)" in text
    assert "== API du client (sonde du " in text and "GetTalentInfo : 8 retours (string, number, number)" in text
    # nothing recorded: no section, and junk never raises
    assert "Pics de lenteur" not in errlog.build_text({"diag": {}}, now=1794600000)
    assert isinstance(errlog.build_text({"diag": {"spikes": {"top": "x", "last": 3}, "api": {"lines": 7}}}, now=1), str)
