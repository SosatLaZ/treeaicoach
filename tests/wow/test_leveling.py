"""Tests for treeaicoach/wow/leveling.py and a Lua smoke run of the F addon modules (fake names only)."""
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from treeaicoach.wow import leveling

ROOT = Path(__file__).resolve().parents[2]

DB = {
    "schema": 1,
    "chars": {
        "Ancien-Royaume": {"summary": {"level": 12, "updated": 100}},
        "Testeur-Royaume": {
            "summary": {
                "class": "HUNTER", "level": 26, "zone": "Contreforts de Hautebrande", "money": 123456,
                "updated": 200,
                "leveling": {
                    "level": 26, "xp": 5000, "xpMax": 20000, "rested": 8000, "trainPending": 26,
                    "talentPoints": 1, "hint": "Bois de la Pénombre", "zoneWarn": None,
                    "skills": [{"name": "Arbalètes", "rank": 100, "max": 130},
                               {"name": "Dépeçage", "rank": 80, "max": 150}],
                    "quests": [{"id": 500, "title": "Sangliers", "level": 25, "done": False,
                                "obj": ["Sanglier tué : 3/8"]},
                               {"id": 501, "title": "Ogres", "level": 31, "done": True, "obj": {}}],
                    "updated": 300,
                },
                "inventory": {"free": 2, "total": 64, "greyCount": 3, "greyValue": 250, "durMin": 20,
                              "broken": 0, "sessionLoot": 10250, "mats": {"Cuir léger": 12}},
                "group": {"members": {"Amie": {"level": 25, "class": "MAGE", "zone": "Orneval", "far": 700}},
                          "shared": [{"title": "Sangliers", "need": ["Amie : Sanglier tué 1/8"], "done": {}}]},
            },
        },
    },
}


def test_format_money():
    assert leveling.format_money(0) == "0 pc"
    assert leveling.format_money(123456) == "12 po 34 pa 56 pc"
    assert leveling.format_money(10000) == "1 po"
    assert leveling.format_money("bad") == "0 pc"


def test_summarize_picks_latest_char_and_flattens():
    s = leveling.summarize(DB)
    assert s["char"] == "Testeur-Royaume"
    assert s["level"] == 26 and s["xp_pct"] == 25 and s["rested_pct"] == 40
    assert s["train_pending"] == 26 and s["talent_points"] == 1
    assert [x["name"] for x in s["skills_behind"]] == ["Arbalètes"]
    assert s["bags_free"] == 2 and s["grey_value"] == 250
    assert s["quests"][1]["done"] is True and s["quests"][1]["obj"] == []
    assert s["members"]["Amie"]["level"] == 25
    assert leveling.summarize(DB, "Ancien-Royaume")["level"] == 12


def test_lines_are_french_and_complete():
    text = "\n".join(leveling.lines(leveling.summarize(DB)))
    for needle in ("Niveau 26 (25 %)", "maître de classe", "Arbalètes 100/130", "2 place(s)",
                   "Équipement à 20 %", "1 po 2 pa 50 pc", "Sangliers", "✓ à rendre", "Ami : Amie",
                   "Groupe : « Sangliers »"):
        assert needle in text, needle


def test_garbage_never_raises():
    for bad in (None, 3, "x", [], {"chars": []}, {"chars": {"A": 5}}, {"chars": {"A": {"summary": []}}}):
        s = leveling.summarize(bad)
        assert s is None or isinstance(s, dict)
        assert isinstance(leveling.lines(s), list)
    assert leveling.lines(None) == []


def test_load_summary_uses_savedvars(monkeypatch, tmp_path):
    fake = types.ModuleType("treeaicoach.wow.savedvars")
    fake.read_db = lambda path, name="TreeAIDB": DB
    monkeypatch.setitem(sys.modules, "treeaicoach.wow.savedvars", fake)
    import treeaicoach.wow as wowpkg
    monkeypatch.setattr(wowpkg, "savedvars", fake, raising=False)
    assert leveling.load_summary(tmp_path / "TreeAI.lua")["level"] == 26
    assert leveling.load_summary(None) is None


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
def test_lua_modules_smoke():
    mods = ROOT / "wow_addon" / "TreeAI" / "Modules"
    args = ["lua5.1", str(Path(__file__).with_name("f_modules_smoke.lua")), str(mods)]
    core = ROOT / "wow_addon" / "TreeAI" / "Core.lua"
    for extra in ([], [str(core)] if core.is_file() else []):
        r = subprocess.run(args + extra, capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr
        assert "1 gris vendu" in r.stdout
