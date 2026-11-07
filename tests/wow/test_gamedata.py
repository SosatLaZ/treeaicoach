"""Generated WoW Forever game data (treeaicoach/wow/data + addon Data/*.lua) and the gamedata loader."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from treeaicoach.wow import ai_context as ac
from treeaicoach.wow import gamedata as g

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "treeaicoach" / "wow" / "data"
ADDON = ROOT / "wow_addon" / "TreeAI"


def test_files_present_and_small():
    files = list(DATA.glob("*.json.gz"))
    assert {f.name for f in files} >= {"spells.json.gz", "items.json.gz", "zones.json.gz", "meta.json.gz"}
    assert sum(f.stat().st_size for f in files) <= 3 * 1024 * 1024
    lua = [ADDON / "Data" / "PetFood.lua", ADDON / "Data" / "Consumables.lua"]
    assert sum(f.stat().st_size for f in lua) < 400 * 1024
    assert "1.60.1.70862" in (DATA / "README.md").read_text(encoding="utf-8")


def test_meta_and_available():
    assert g.available()
    m = g.meta()
    assert m["build"] == "1.60.1.70862" and m["locale"] == "frFR"
    assert m["counts"]["items"] > 10000


def test_class_token():
    assert g.class_token("Chasseur") == "HUNTER"
    assert g.class_token("chasseresse") == "HUNTER"
    assert g.class_token("HUNTER") == "HUNTER"
    assert g.class_token("Démoniste") == "WARLOCK"
    assert g.class_token(11) == "DRUID"
    assert g.class_token("") is None and g.class_token(None) is None


def test_hunter_spells_by_level():
    lv28 = {s["name"] for s in g.new_spells("Chasseur", 28)}
    assert "Piège de givre" in lv28 and "Tir des arcanes" in lv28
    lv10 = {s["id"] for s in g.new_spells("HUNTER", 10, 10)}
    assert 883 in lv10 and 6991 in lv10  # Appel du familier, Nourrir le familier
    for s in g.class_spells("HUNTER"):
        assert 1 <= s["level"] <= 60 and s["name"] and not (400000 <= s["id"] < 1000000)
    lines = g.next_spells_text("Chasseur", 26)
    assert len(lines) == 2 and lines[0].startswith("Niv. 28 : ")


def test_all_classes_have_spells():
    for c in g.CLASSES:
        assert len(g.class_spells(c)) > 50, c


def test_items():
    helm = g.item(12640)  # Heaume Coeur-de-lion
    assert helm["quality"] == 4 and helm["req_level"] == 56 and helm["armor"] == 565
    assert helm["stats"]["Force"] == 18
    tf = g.item(19019)
    assert tf["stat_ids"]["3"] == 5 and tf["stat_ids"]["7"] == 8 and tf["speed"] == 1.9 and tf["dps"] > 0
    assert g.item_name(117) == "Viande séchée coriace"
    assert g.item(999999999) is None and g.item("x") is None
    assert any(i["id"] == 5596 for i in g.find_items("arc d'ashwood"))


def test_compare_hunter():
    r = g.compare(2504, 5596, "Chasseur")  # Vieil arc court vs Arc d'Ashwood
    assert r["better"] == "b" and "Arc d'Ashwood est mieux pour toi" in r["text"]
    agi, stam = {"name": "A", "stat_ids": {"3": 10}}, {"name": "B", "stat_ids": {"7": 10}}
    assert g.compare(agi, stam, "HUNTER")["better"] == "a"
    assert g.compare(agi, stam, "WARRIOR", "protection")["better"] == "b"
    assert g.compare(1, 2, "HUNTER")["better"] is None  # unknown ids -> no crash


def test_zones():
    assert g.zone_name(267) == "Contreforts de Hautebrande"
    assert g.zone_id("Contreforts de Hautebrande") == 267
    assert g.zone_name("nope") is None


def test_ai_context_game_data():
    ctx = ac.build_context(None, db={"char": {"class": "Chasseur", "level": 26}}, facts=[])
    assert ctx["donnees_jeu"]["prochains_sorts"][0].startswith("Niv. 28")
    assert g.game_data_context("inconnu", 10) == {}


def test_lua_tables():
    pf = (ADDON / "Data" / "PetFood.lua").read_text(encoding="utf-8")
    rows = re.findall(r'\{(\d+), "(\w+)", (\d+)\}', pf)
    assert len(rows) > 100 and {r[1] for r in rows} <= {"meat", "fish", "bread", "cheese", "fruit", "fungus"}
    assert ("117", "meat") in {(a, b) for a, b, _ in rows}
    cs = (ADDON / "Data" / "Consumables.lua").read_text(encoding="utf-8")
    for cat in ("food", "water", "hpot", "mpot", "bandage", "ammo"):
        assert f"  {cat} = {{" in cs
    assert "{1251, 1, 66, \"\"}" in cs  # Bandage en lin : 66 PV, secourisme 1
    toc = (ADDON / "TreeAI.toc").read_text(encoding="utf-8")
    first = toc.index("Modules\\Macros.lua")  # Modules\Theme.lua (no data use) loads right after UI.lua
    assert toc.index("Data\\PetFood.lua") < first
    assert toc.index("Data\\Consumables.lua") < first


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_lua_syntax():
    for f in ("PetFood.lua", "Consumables.lua"):
        assert subprocess.run(["luac5.1", "-p", str(ADDON / "Data" / f)], capture_output=True).returncode == 0
