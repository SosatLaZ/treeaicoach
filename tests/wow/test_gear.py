"""Modules/Gear.lua (harness scenario, syntax, TOC) + treeaicoach/wow/gear.py (data, AI context)."""
import shutil
import subprocess
from pathlib import Path

import pytest

from treeaicoach.wow import ai_context, gear

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
LUA_FILES = [ADDON / "Modules" / "Gear.lua", ADDON / "Data" / "Gear.lua"]
ALLOW = sum((["--allow", n] for n in ("LibQuestieDB", "QuestDB", "NpcDB", "ObjectDB", "ItemDB")), [])


def test_toc_lists_gear():
    lines = [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert "Data\\Gear.lua" in lines and "Modules\\Gear.lua" in lines
    assert lines.index("Data\\Gear.lua") < lines.index("Modules\\Gear.lua")


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
@pytest.mark.parametrize("path", LUA_FILES, ids=lambda p: p.name)
def test_syntax_no_globals(path):
    assert subprocess.run(["luac5.1", "-p", str(path)], capture_output=True).returncode == 0
    listing = subprocess.run(["luac5.1", "-l", "-p", str(path)], capture_output=True, text=True).stdout
    assert "SETGLOBAL" not in listing


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["modern", "legacy"])
def test_gear_scenario(flavor):
    r = subprocess.run(["lua5.1", "tests/wow/harness.lua", "-q", "--flavor", flavor, "--class", "HUNTER", *ALLOW,
                        "--scenario", "tests/wow/gear_scenario.lua", str(ADDON)],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout


def test_crafted_data():
    d = gear.data()
    assert d.get("build") == "1.60.1.70862"
    assert len(d.get("crafted", {})) > 500
    c = gear.crafted(2307)  # leatherworking boots
    assert c and c["prof"] == "Travail du cuir" and c["skill"] >= 1
    assert gear.crafted(999999999) is None


def test_offline_upgrades_hunter_rules():
    ups = gear.upgrades("Chasseur", 26, {})
    assert "Distance" in ups and "Torse" in ups
    for lst in ups.values():
        for u in lst:
            assert u["req"] <= 29
    mail = {"class": 4, "subclass": 3, "inv_type": 5, "allowable_class": -1}
    assert not gear.usable(mail, "HUNTER", 39) and gear.usable(mail, "HUNTER", 40)
    assert not gear.usable({"class": 2, "subclass": 19, "inv_type": 26, "allowable_class": -1}, "HUNTER", 30)


def test_gear_context_and_ai_hook():
    summary = {"gear": {
        "priority": "Agilité > DPS de l'arme à distance", "specName": "Précision", "specSource": "api",
        "slots": [{"slot": "Torse", "item": "Gilet", "score": 5,
                   "next": {"name": "Gilet-test", "gain": 156, "where": "marchand Marchand-test", "cost": 5000}},
                  {"slot": "Mains", "item": None, "score": 0,
                   "next": {"name": "Gants-test", "gain": 999, "where": "dans tes sacs"}}],
        "shopping": [{"name": "Gilet-test", "cost": 5000, "gain": 156}], "total": 5000,
        "hunter": {"ranged": "Arc-test", "dps": 8, "speed": 2.3, "ammo": "flèches", "ammoOK": False}}}
    ctx = gear.gear_context({"chars": {"x": {"summary": summary}}})
    assert ctx["priorite_stats"].startswith("Agilité")
    assert ctx["spe"] == "Précision (lue dans les talents)"   # (5.0 GEAR) the AI knows the spec and its source
    assert ctx["ameliorations"][0].startswith("Mains: Gants-test (nouvel emplacement)")
    assert "50 pa" in ctx["ameliorations"][1]
    assert "MANQUANTES" in ctx["chasseur"]
    assert ctx["liste_de_courses"].startswith("1 objet(s)")
    full = ai_context.character_context({"char": {"class": "HUNTER", "level": 26, "summary": summary}})
    assert "equipement" in full
    assert gear.gear_context({}) == {}
