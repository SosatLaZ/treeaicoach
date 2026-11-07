"""Squad (group brain): Lua engine + 4-client simulation, and the AI context section (fake names only)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from treeaicoach.wow import ai_context as ac

ROOT = Path(__file__).resolve().parents[2]
SQUAD = ROOT / "wow_addon" / "TreeAI" / "Modules" / "Squad.lua"


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 not installed")
def test_squad_lua_spec():
    r = subprocess.run(["lua5.1", str(Path(__file__).with_name("squad_spec.lua")), str(SQUAD)],
                       capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "RESULT ok" in r.stdout


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 not installed")
@pytest.mark.parametrize("flavor", ["modern", "legacy"])
def test_squad_real_addon_scenario(flavor):
    here = Path(__file__).parent
    r = subprocess.run(["lua5.1", str(here / "harness.lua"), "-q", "--flavor", flavor, "--scenario",
                        str(here / "squad_scenario.lua"), str(SQUAD.parents[1])],
                       capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-3000:] + r.stderr[-2000:]


def test_squad_in_toc_after_group():
    toc = (ROOT / "wow_addon" / "TreeAI" / "TreeAI.toc").read_text(encoding="utf-8").splitlines()
    names = [x.strip() for x in toc]
    assert names.index("Modules\\Squad.lua") > names.index("Modules\\Group.lua")


DB = {"chars": {"Fakeone-Fakerealm": {"updated": 5, "summary": {"class": "HUNTER", "level": 26, "group": {
    "v": 2, "size": 2, "leader": "Fakeone", "xph": 12000,
    "members": [{"name": "Fakeone", "class": "Chasseur", "level": 26, "zone": "Hautebrande", "hp": 80, "me": True},
                {"name": "Fakefriend", "class": "Mage", "level": 24, "zone": "Hautebrande", "mana": 20, "leaveMin": 10}],
    "advices": [{"cat": "xp", "prio": 1, "text": "Fakefriend est 2 niveaux derrière"},
                {"cat": "pull", "prio": 2, "text": "Pause boisson : Fakefriend 20 % mana"}],
    "common": [{"id": 1, "title": "Les ours", "holders": 2, "done": 1}],
}}}}}


def test_ai_context_reads_group_snapshot_without_names():
    ctx = ac.character_context(DB)
    g = ctx["groupe_treeai"]
    assert g["conseils"][0] == "Mage 24 est 2 niveaux derrière"
    assert any("part dans 10 min" in m for m in g["membres"])
    assert g["quetes_communes"] == ["Les ours (2/2, 1 fini)"]
    assert g["xp_h_par_joueur"] == 12000
    assert "Fakefriend" not in json.dumps(g, ensure_ascii=False)


def test_ai_context_ignores_old_or_garbage_group():
    assert "groupe_treeai" not in ac.character_context({"chars": {"x": {"summary": {"group": {"v": 1}}}}})
    assert ac._squad({"summary": {"group": {"v": 2, "members": "nope"}}}) is None
