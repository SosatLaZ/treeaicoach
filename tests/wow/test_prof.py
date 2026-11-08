"""Modules/Prof.lua + treeaicoach/wow/professions.py + Data/Recipes.lua (agent PROF)."""
import importlib.util
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from treeaicoach.wow import professions as p

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
SRC = ADDON / "Modules" / "Prof.lua"
RECIPES = ADDON / "Data" / "Recipes.lua"


def test_formula():
    assert p.chance(20, 30, 60) == 1.0
    assert p.chance(45, 30, 60) == pytest.approx(0.5)
    assert p.chance(59, 30, 60) == pytest.approx(1 / 30)
    assert p.chance(60, 30, 60) == 0.0
    assert p.chance(10, 30, 60, min_rank=20) == 0.0
    assert p.chance(45, 30, 60, overtime=5) == pytest.approx(0.7)
    assert p.chance(31, 30, 60, overtime=5) == 1.0
    row = [1, 0, 1, 1, 30, 60, 1, 0, "x", []]
    assert [p.color(s, row) for s in (10, 40, 50, 60)] == ["orange", "yellow", "green", "grey"]


def test_plan_synthetic():
    rows = [[10, 0, 1, 1, 5, 10, 1, 0, "A", [900, 1]], [11, 0, 1, 1, 20, 40, 1, 0, "B", [901, 2]]]
    r = p.plan(0, 1, 12, lambda i: 10 if i == 900 else 50, stock={900: 3}, rows=rows)
    assert r["reached"] == 12 and r["steps"][0]["name"] == "A" and r["steps"][-1]["name"] == "B"
    assert 900 in r["shopping"] and r["shopping"][901] >= 4


def test_legacy_and_ranks():
    lp = p.legacy_points({197: 230, 182: 300, 171: 140})
    assert lp["earned"] == 2 and lp["next"] == (171, 150)
    assert p.next_rank(140, 150)[0] == "Expert"


def test_data_files():
    d = p.load()
    assert d.get("build") == "1.60.1.70862"
    rec = d["recipes"]
    assert sum(len(v) for v in rec.values()) > 2000
    fa = {r[p.NAME]: r for r in rec["129"]}
    lin = fa["Bandage en lin"]
    assert lin[p.ITEM] == 1251 and lin[p.YELLOW] == 30 and lin[p.GREY] == 60 and lin[p.MATS] == [2589, 1]
    assert all(not (400000 <= r[0] < 1000000) for rows in rec.values() for r in rows)  # no SoD
    assert d["mats"]["3371"][2] == 1  # empty vial: vendor
    full = p.plan(129, 1, 300, lambda i: 100)
    assert full["reached"] >= 300 and full["cost"] > 0
    size = RECIPES.stat().st_size
    assert 50_000 < size <= 300 * 1024
    assert "T.Data.Recipes" in RECIPES.read_text(encoding="utf-8")


def test_build_step_pure():
    spec = importlib.util.spec_from_file_location("wbd", ROOT / "tools" / "wow_build_data.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    T = {
        "SkillLineAbility": [
            {"SkillLine": "129", "Spell": "3275", "AcquireMethod": "0", "MinSkillLineRank": "1",
             "TrivialSkillLineRankLow": "30", "TrivialSkillLineRankHigh": "60", "NumSkillUps": "1"},
            {"SkillLine": "129", "Spell": "470349", "AcquireMethod": "0", "MinSkillLineRank": "1",
             "TrivialSkillLineRankLow": "300", "TrivialSkillLineRankHigh": "320", "NumSkillUps": "1"},
            {"SkillLine": "6", "Spell": "116", "AcquireMethod": "0", "MinSkillLineRank": "1",
             "TrivialSkillLineRankLow": "0", "TrivialSkillLineRankHigh": "0", "NumSkillUps": "1"},
        ],
        "SpellName": [{"ID": "3275", "Name_lang": "Bandage en lin"}, {"ID": "470349", "Name_lang": "SoD"},
                      {"ID": "116", "Name_lang": "Éclair de givre"}],
        "SpellEffect": [{"SpellID": "3275", "Effect": "24", "DifficultyID": "0", "EffectItemType": "1251",
                         "EffectBasePointsF": "1"}],
        "SpellReagents": [{"SpellID": "3275", **{f"Reagent_{k}": "0" for k in range(8)},
                           **{f"ReagentCount_{k}": "0" for k in range(8)}, "Reagent_0": "2589", "ReagentCount_0": "1"}],
        "ItemSparse": [{"ID": "2589", "Display_lang": "Étoffe de lin", "BuyPrice": "0", "SellPrice": "13"},
                       {"ID": "2447", "Display_lang": "Pacifique", "BuyPrice": "0", "SellPrice": "4"}],
        "Item": [], "ItemEffect": [], "ItemXItemEffect": [],
    }
    rec = m.build_recipes(T)
    # (PROFLEVEL) trainer recipe: the client has no requirement (MinSkillLineRank 1) -> learn = yellow, flag 2
    assert rec["recipes"][129] == [[3275, 1251, 1, 30, 30, 60, 1, 0, "Bandage en lin", [2589, 1], 2]]
    assert rec["mats"][2589] == ["Étoffe de lin", 0, 0, 13, 1]
    assert rec["gather"][182] == [[2447, 1]]
    T["SkillLineAbility"][0]["AcquireMethod"] = "1"          # learned with the profession -> 1, flag 1
    assert m.build_recipes(T)["recipes"][129][0][3:4] + m.build_recipes(T)["recipes"][129][0][10:] == [1, 1]


def test_zone_names_exist():
    from treeaicoach.wow import gamedata as g
    import unicodedata

    def fold(s):
        s = re.sub(r"\s*\(.*\)\s*$", "", s).replace("’", "'")
        return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    zones = {fold(v[0]) for v in g._load("zones").get("zones", {}).values()}
    if not zones:
        pytest.skip("zones data absent")
    for bands in p.load()["zones"].values():
        for _, _, names in bands:
            for n in names:
                assert fold(n) in zones, n


def test_static_rules():
    src = SRC.read_text(encoding="utf-8")
    for bad in ("CastSpellByName", "DoTradeSkill", "C_TradeSkillUI.CraftRecipe", "SendChatMessage", "RunMacroText"):
        assert bad not in src
    assert "InCombat" in src
    toc = [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert "Data\\Recipes.lua" in toc and "Modules\\Prof.lua" in toc


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_syntax_no_globals():
    for f in (SRC, RECIPES):
        assert subprocess.run(["luac5.1", "-p", str(f)], capture_output=True).returncode == 0
    listing = subprocess.run(["luac5.1", "-l", "-p", str(SRC)], capture_output=True, text=True).stdout
    assert "SETGLOBAL" not in listing


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["modern", "legacy"])
def test_prof_scenario(flavor):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", "HUNTER",
                        "--scenario", str(ROOT / "tests/wow/prof_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
