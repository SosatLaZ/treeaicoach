"""GEAR 5.0 — « un vrai assistant pour l'armure » : tests/wow/gear5_scenario.lua on the real addon.

Spec detection paths (game API incl. WoW Forever's C_Traits groups and nodes, talent spells known, asked once then the
player's choice), value model sanity (a known better bow wins, slow bow, stat stick, mail at 40, hit cap, unreadable
stats = « inconnu »), « stuff rentable » with mocked vendor / AH / quest data (budget, mount reserve, T.Gear.BestNow),
live help (tooltip line, reward pick, keeper for BAGS, bag arrows, page), no chat, zero blocked actions, no plan on a
plain bag change. HUNTER / WARRIOR / MAGE / PRIEST at 30 and 60, on the faithful Forever mock and on modern
(+ HUNTER on legacy). + the generated talent-spell table is up to date, syntax / no globals of the new files.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
NEW_FILES = [ADDON / "Modules" / n for n in ("GearSpec.lua", "GearValue.lua", "GearRent.lua")] + [ADDON / "Data" / "TalentSpells.lua"]
ALLOW = sum((["--allow", n] for n in ("LibQuestieDB", "QuestDB", "NpcDB", "ObjectDB", "ItemDB")), [])
COMBOS = [(c, lvl, f) for c in ("HUNTER", "WARRIOR", "MAGE", "PRIEST") for lvl in (30, 60) for f in ("forever", "modern")]
COMBOS += [("HUNTER", 30, "legacy")]


def test_toc_order():
    lines = [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    order = ["Data\\Talents.lua", "Data\\TalentSpells.lua", "Modules\\Gear.lua", "Modules\\GearSpec.lua", "Modules\\GearValue.lua",
             "Modules\\GearRent.lua", "Modules\\ItemInfo.lua", "Modules\\Talents.lua", "Modules\\Window.lua"]
    idx = [lines.index(n) for n in order]
    assert idx == sorted(idx), idx


def test_talent_spells_generated_file_is_current():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "wow_build_talent_spells.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    text = (ADDON / "Data" / "TalentSpells.lua").read_text(encoding="utf-8")
    assert "19434" in text and "19577" in text and "19263" in text  # Visée, Intimidation, Dissuasion (hunter trees)
    for cls in ("DRUID", "HUNTER", "MAGE", "PALADIN", "PRIEST", "ROGUE", "SHAMAN", "WARLOCK", "WARRIOR"):
        assert f"  {cls} = {{" in text


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
@pytest.mark.parametrize("path", NEW_FILES, ids=lambda p: p.name)
def test_syntax_no_globals(path):
    assert subprocess.run(["luac5.1", "-p", str(path)], capture_output=True).returncode == 0
    listing = subprocess.run(["luac5.1", "-l", "-p", str(path)], capture_output=True, text=True).stdout
    assert "SETGLOBAL" not in listing


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("cls,level,flavor", COMBOS)
def test_gear5_scenario(cls, level, flavor):
    r = subprocess.run(["lua5.1", "tests/wow/harness.lua", "-q", "--flavor", flavor, "--class", cls, "--level", str(level), *ALLOW,
                        "--scenario", "tests/wow/gear5_scenario.lua", str(ADDON)],
                       cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-6000:] + r.stderr[-2000:]
    assert "RESULT ok" in r.stdout
