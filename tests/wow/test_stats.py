"""STATS: the stats hub `TreeAI.Stats` (Modules/Stats.lua) — drop rates (Beta posterior, exact numbers), kill
time, XP/h, travel speed, sheet, tab + export — scenario on the real addon (3 API flavours), static cost /
ToS checks, and the app-side reader (treeaicoach/wow/stats.py + WoW page Session tab)."""
# (5.1 CLEAN) harness default = the player's profile (« sur ») ; this file tests modules that are off there: « tout »
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from treeaicoach.wow import stats as wstats

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
SRC = ADDON / "Modules" / "Stats.lua"


def _code(p):
    return "\n".join(re.sub(r"--.*$", "", ln) for ln in p.read_text(encoding="utf-8").splitlines())


def test_toc_early():
    toc = [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    i = toc.index("Modules\\Stats.lua")
    assert i < toc.index("Modules\\Macros.lua")   # Predict removed in 4.0: Stats carries the T.Predict facade
    if "Modules\\Detect.lua" in toc:
        assert toc.index("Modules\\Detect.lua") < i


def test_event_driven_and_allowed():
    code = _code(SRC)
    assert "OnUpdate" not in code
    assert "COMBAT_LOG_EVENT" not in code and "CombatLogGetCurrentEventInfo" not in code
    for sec in re.findall(r"NewTicker\((\d+)", code):
        assert int(sec) >= 10
    for bad in ("SendChatMessage", "CreateMacro", "EditMacro", "PostAuction", "RepairAllItems"):
        assert bad not in code, bad


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_syntax_no_globals():
    assert subprocess.run(["luac5.1", "-p", str(SRC)], capture_output=True).returncode == 0
    assert "SETGLOBAL" not in subprocess.run(["luac5.1", "-l", "-p", str(SRC)], capture_output=True, text=True).stdout


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor,cls", [("modern", "HUNTER"), ("legacy", "MAGE"), ("forever", "HUNTER"), ("forever", "WARRIOR")])
def test_stats_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--profile", "tout", "--flavor", flavor, "--class", cls,
                        "--scenario", str(ROOT / "tests/wow/stats_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout


SNAP = {"stats": {
    "v": 1, "played": 95, "sheet": {"str": 40, "agi": 120, "sta": 80, "int": 30, "spi": 45, "ap": 120, "rap": 320,
                                    "rcrit": 8.2, "crit": 5.3, "armor": 1200, "ilvl": 23.6},
    "kill": {"0": 12.4, "2": 17.0}, "dmgPct": 31, "downSec": 18.2, "deathsH": 0.4, "deathTop": "Ours-test",
    "xph": {"q": 21400, "g": 15200}, "goldH": 12050, "questMin": 21.6, "speed": {"w": 7.0, "m": 9.8},
    "perH": {"ammo": 410, "food": 3.2, "water": 2, "repair": 2500}, "ah": {"rate": 0.67, "hours": 1.5, "n": 3}}}


def test_reader_rows():
    rows = dict(wstats.stats_rows(SNAP))
    assert rows["Fiche"] == "For 40 · Agi 120 · End 80 · Int 30 · Esp 45 · niv. obj. 24"
    assert rows["Attaque"] == "PA dist. 320 · crit 8,2 % · armure 1200"
    assert rows["Temps de kill"] == "~12 s même niveau · ~17 s à +2 · 31 % PV perdus / combat"
    assert rows["XP/h mesurée"] == "quêtes 21 k · farm 15 k"
    assert rows["Or / heure"] == "1 po 20 pa"
    assert rows["Trajets"] == "7 m/s à pied · 9,8 m/s monté · quête ~22 min"
    assert rows["Par heure"] == "410 munitions · 3,2 nourriture · 2 boisson · réparations 25 pa"
    assert rows["Hôtel des ventes"] == "67 % vendu en ~2 h"


def test_reader_never_invents():
    assert wstats.stats_rows({}) == []
    assert wstats.stats_rows({"stats": {"v": 2, "sheet": {"agi": 1}}}) == []
    assert wstats.stats_rows({"stats": "garbage"}) == []
    rows = dict(wstats.stats_rows({"stats": {"v": 1, "sheet": {"agi": 50}, "xph": {}}}))
    assert list(rows) == ["Fiche"]


def test_session_tab_block():
    from treeaicoach import ui_page_wow  # noqa: PLC0415
    blocks = ui_page_wow.tab_blocks("session", {"summary": SNAP})
    st = [b for b in blocks if b["title"] == "Mes stats mesurées"]
    assert st and len(st[0]["rows"]) >= 6
