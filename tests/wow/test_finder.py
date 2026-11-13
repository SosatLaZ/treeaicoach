"""Finder (agent FINDER): hunter pet table builder (tools/wow_build_pets.py → Data/Pets.lua) and the in-game
« Chercher » module (best pet now, generic finder, route hand-off). Fake beasts / NPCs only in the scenario."""
# (5.1 CLEAN) harness default = the player's profile (« sur ») ; this file tests modules that are off there: « tout »
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = Path(os.environ.get("TREEAI_WOW_ADDON") or ROOT / "wow_addon" / "TreeAI")
LUA = shutil.which("lua5.1")
needs_lua = pytest.mark.skipif(not LUA, reason="lua5.1 not installed")


def tool():
    spec = importlib.util.spec_from_file_location("wbp", ROOT / "tools" / "wow_build_pets.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fake_tables():
    return {
        "CreatureFamily": [
            {"ID": "1", "Name_lang": "Loup", "PetFoodMask": "1", "IconFileID": "11", "SkillLine_0": "208", "SkillLine_1": "270"},
            {"ID": "2", "Name_lang": "Félin", "PetFoodMask": "3", "IconFileID": "12", "SkillLine_0": "209", "SkillLine_1": "270"},
            {"ID": "16", "Name_lang": "Marcheur", "PetFoodMask": "0", "IconFileID": "0", "SkillLine_0": "204", "SkillLine_1": "0"},
        ],
        "SpellName": [{"ID": "100", "Name_lang": "Morsure"}, {"ID": "101", "Name_lang": "Morsure"},
                      {"ID": "200", "Name_lang": "Griffe"}, {"ID": "300", "Name_lang": "Tamed Pet Passive (DND)"},
                      {"ID": "301", "Name_lang": "Tamed Pet Passive (DND)"}, {"ID": "400", "Name_lang": "Hurlement furieux"}],
        "SpellLevels": [{"SpellID": "100", "BaseLevel": "1", "SpellLevel": "1"}, {"SpellID": "101", "BaseLevel": "8", "SpellLevel": "8"},
                        {"SpellID": "200", "BaseLevel": "1", "SpellLevel": "1"}, {"SpellID": "400", "BaseLevel": "0", "SpellLevel": "10"}],
        "SkillLineAbility": [
            {"SkillLine": "208", "Spell": "100"}, {"SkillLine": "208", "Spell": "101"}, {"SkillLine": "208", "Spell": "400"},
            {"SkillLine": "208", "Spell": "300"}, {"SkillLine": "209", "Spell": "100"}, {"SkillLine": "209", "Spell": "101"},
            {"SkillLine": "209", "Spell": "200"}, {"SkillLine": "209", "Spell": "301"}, {"SkillLine": "204", "Spell": "999"},
        ],
        "SpellEffect": [
            {"SpellID": "301", "Effect": "6", "EffectAura": "79", "EffectBasePointsF": "10"},
            {"SpellID": "301", "Effect": "6", "EffectAura": "133", "EffectBasePointsF": "-2"},
            {"SpellID": "301", "Effect": "6", "EffectAura": "142", "EffectBasePointsF": "0"},
            {"SpellID": "100", "Effect": "2", "EffectAura": "0", "EffectBasePointsF": "5"},
        ],
    }


def test_extract_families_abilities_modifiers():
    d = tool().extract(fake_tables())
    fams, ab = d["families"], d["abilities"]
    assert set(fams) == {1, 2}, "only hunter pet families with a diet"
    assert fams[2] == {"n": "Félin", "diet": 3, "icon": 12, "dmg": 10, "armor": 0, "hp": -2, "learn": ["Griffe", "Morsure"]}
    assert fams[1]["dmg"] == 0 and fams[1]["learn"] == ["Hurlement furieux", "Morsure"]
    assert ab["Morsure"] == {"ids": [100, 101], "lv": [1, 8], "fam": [1, 2]}
    assert ab["Hurlement furieux"]["lv"] == [10], "SpellLevel used when BaseLevel is 0"
    assert "Tamed Pet Passive (DND)" not in ab


def test_beasts_are_checked_against_the_client_facts(monkeypatch):
    t = tool()
    facts = t.extract(fake_tables())
    monkeypatch.setattr(t, "BEASTS", [(90001, "Bête-test", 2, 20, 21, 1424, 50, 50, ("Griffe", 3), None, "")])
    b = t.beasts(facts)
    assert b == [{"npc": 90001, "n": "Bête-test", "fam": 2, "lo": 20, "hi": 21, "map": 1424, "x": 50, "y": 50,
                  "zone": "Contreforts de Hautebrande", "fz": "", "teach": ["Griffe", 3], "atk": None, "rare": False}]
    monkeypatch.setattr(t, "BEASTS", [(90002, "Loup-test", 1, 20, 21, 1424, 50, 50, ("Griffe", 3), None, "")])
    with pytest.raises(ValueError):
        t.beasts(facts)          # a wolf cannot learn Griffe
    monkeypatch.setattr(t, "BEASTS", [(90003, "X", 77, 20, 21, 1424, 50, 50, None, None, "")])
    with pytest.raises(ValueError):
        t.beasts(facts)


def test_render_is_deterministic_lua():
    t = tool()
    d = t.extract(fake_tables())
    d["beasts"], d["build"] = [], "0.0.0"
    a, b = t.render_lua(d), t.render_lua(d)
    assert a == b and "T.Data.Pets = {" in a and '[2] = { n = "Félin", diet = 3, dmg = 10' in a
    assert "APPROXIMATIFS" in a


def test_shipped_table_matches_the_builder():
    t = tool()
    d = t.load_json()
    d["beasts"] = t.beasts(d | {"families": {int(k): v for k, v in d["families"].items()}})
    text = (ADDON / "Data" / "Pets.lua").read_text(encoding="utf-8")
    assert text == t.render_lua(d), "Data/Pets.lua is stale: python tools/wow_build_pets.py --render"
    fams = {int(k): v for k, v in d["families"].items()}
    assert fams[2]["n"] == "Félin" and fams[2]["dmg"] == 10 and fams[5]["armor"] == 9   # era modifiers (client)
    assert d["abilities"]["Griffe"]["lv"][:4] == [1, 8, 16, 24]
    assert all(b["lo"] <= b["hi"] for b in d["beasts"]) and len(d["beasts"]) >= 20


@needs_lua
def test_syntax():
    luac = LUA.replace("lua5.1", "luac5.1")
    for f in ("Modules/Finder.lua", "Data/Pets.lua"):
        assert subprocess.run([luac, "-p", str(ADDON / f)]).returncode == 0


def test_toc_and_hooks():
    toc = [l.strip() for l in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert toc.index("Data\\Pets.lua") < toc.index("Modules\\Finder.lua")
    assert toc.index("Modules\\Finder.lua") > toc.index("Modules\\Planner.lua")
    # 4.0 (CLEAN): Finder is the only owner of taming; Boost has no pet code, HunterPro is gone, Hunter links to Finder
    assert not (ADDON / "Modules" / "HunterPro.lua").exists()
    assert "Apprivoise" not in (ADDON / "Modules" / "Boost.lua").read_text(encoding="utf-8")
    assert "TreeAI.Finder" in (ADDON / "Modules" / "Hunter.lua").read_text(encoding="utf-8")
    assert "Fd.Inject(steps, cursor)" in (ADDON / "Modules" / "Planner.lua").read_text(encoding="utf-8")
    sim = (ROOT / "tests" / "wow" / "session_sim.lua").read_text(encoding="utf-8")
    assert "Finder = 60" in sim


@needs_lua
@pytest.mark.parametrize("flavor", ["modern", "forever"])
def test_scenario(flavor):
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--profile", "tout", "--flavor", flavor, "--class", "HUNTER",
                        "--allow", "ShortestPathForever", "--scenario", "tests/wow/finder_scenario.lua", str(ADDON)],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
