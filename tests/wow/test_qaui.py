"""FIX-UI (round 9, 4.0 QA): regression tests for research/qa_ui.md — each verified finding replayed on the faithful
WoW: Forever mock (level-28 Horde hunter in Thousand Needles, fake quest ids): the « Donjons » card pool (P0 UI-01,
no frame growth), the « Prochaine étape » card (dead / ghost / dungeon / mode sûr notice / one ellipsis), edit mode,
« Réinitialiser l'interface » and the start-up wizard keep the card as the HUD, menus fit at any UI scale, options
show one title per section, nothing in the chat on any click, combat refusals, the quest automations stay opt-in."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
ALLOW = ["LibQuestieDB", "QuestDB", "NpcDB", "ObjectDB", "ItemDB", "TreeAIPlan", "IsQuestFlaggedCompleted"]


def _run(scenario: str, profile: str, **env: str) -> subprocess.CompletedProcess:
    cmd = ["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--no-storm", "--profile", profile,
           "--flavor", "forever", "--class", "HUNTER", "--level", "28"]
    for a in ALLOW:
        cmd += ["--allow", a]
    cmd += ["--scenario", str(ROOT / "tests/wow" / scenario), str(ADDON)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=180, cwd=ROOT, env={**os.environ, **env})


def _ok(r: subprocess.CompletedProcess) -> None:
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout, r.stdout + r.stderr


lua = pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")


@lua
@pytest.mark.parametrize("profile", ["sur", "tout"])
def test_dungeon_cards_pool_no_leak(profile):
    """P0 UI-01: 60 s on « Donjons » + page switches = the same 9 card frames, none left on another page."""
    _ok(_run("qaui_leak_scenario.lua", profile))


@lua
@pytest.mark.parametrize("profile", ["sur", "tout"])
def test_what_the_player_sees(profile):
    """Card, every window page, options pages, hub menu (UI scale 1.0 and 1.15), shortcuts, chat."""
    _ok(_run("qaui_scenario.lua", profile))


@lua
@pytest.mark.parametrize("profile,noquestie", [("sur", "0"), ("tout", "0"), ("sur", "1")])
def test_card_states(profile, noquestie):
    """Dead / ghost: corpse run first; dungeon with 4: dungeon rows + live section first; group page in a group."""
    _ok(_run("qaui_states_scenario.lua", profile, QAUI_NOQUESTIE=noquestie))


@lua
def test_first_minute():
    """New player (mode sûr): notice on the card, wizard step 2 keeps the card, tour closes with Échap, no chat."""
    _ok(_run("qaui_first_scenario.lua", "sur"))


@lua
@pytest.mark.parametrize("profile", ["sur", "tout"])
@pytest.mark.parametrize("combat", ["0", "1"])
def test_every_click(profile, combat):
    """Every hub entry / Outils row / page action / card / minimap click: no chat, no error, no blocked action;
    edit mode gives the card a handle, reset keeps the card, combat refusals keep everything in place."""
    _ok(_run("qaui_clicks_scenario.lua", profile, QAUI_COMBAT=combat))


def _src(name: str) -> str:
    return (ADDON / name).read_text(encoding="utf-8")


def test_static_contracts():
    win, pages = _src("Modules/Window.lua"), _src("Modules/WindowPages.lua")
    # UI-01: the dungeon cards use the pool registered (and released) by the window
    assert "W._pool = pool" in win and 'W._pool("dcard"' in pages and "W._newPool(function" not in pages
    # UI-15: quest automations never on by default (nor by « Tout allumer »)
    quick = _src("Modules/Quick.lua")
    assert "defaults.autoQuest, defaults.autoReward, defaults.autoGossip = false, false, false" in quick
    # UI-25: the release version in the TOC
    assert re.search(r"^## Version: 4\.0\.\d+$", _src("TreeAI.toc"), re.M)
    # UI-20: no glyph the game fonts lack in the strings QA listed
    for name, bad in (("Modules/Theme.lua", "Échap → Options"), ("Modules/Shortcuts.lua", "Options → Barre"),
                      ("Modules/Region.lua", "Ici → Zones"), ("Modules/Highlight.lua", "à rendre ✓")):
        assert bad not in _src(name), (name, bad)
    # UI-19: no chat line from plain clicks
    assert 'TreeAI:Print(L["Prêt !' not in _src("Options.lua")
    assert "Interface TreeAI réinitialisée" not in _src("UI.lua")
    assert 'MP:Print("Boussole' not in _src("Modules/MinimapPins.lua")
