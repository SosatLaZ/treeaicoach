"""Talents: data build (Forever trees + validated builds), Python context, addon module + scenario."""
import gzip
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
SRC = ADDON / "Modules" / "Talents.lua"
sys.path.insert(0, str(ROOT / "tools"))

import wow_build_data as wbd  # noqa: E402
from treeaicoach.wow import ai_context, talent_builds, talents  # noqa: E402

DATA = json.load(gzip.open(ROOT / "treeaicoach/wow/data/talents.json.gz", "rt", encoding="utf-8"))


def test_trees_all_classes_three_tabs():
    assert set(DATA["classes"]) == set(talent_builds.DUAL)
    for cls, c in DATA["classes"].items():
        assert len(c["tabs"]) == 3, cls
        assert sum(len(t["talents"]) for t in c["tabs"]) >= 40, cls
    hunt = DATA["classes"]["HUNTER"]["tabs"][0]
    assert hunt["name"] == "Maîtrise des bêtes"
    names = {t["name"] for t in hunt["talents"]}
    assert "Aspects mortels" in names and "Courroux bestial" in names  # Forever-reworked tree


def test_every_build_is_legal_and_complete():
    ids = {b["id"] for b in DATA["builds"]}
    assert len(DATA["builds"]) == 18
    for b in DATA["builds"]:
        assert b["points"] == 51, b["id"]
        assert all(s["why"] for s in b["steps"])
    for d in talent_builds.DUAL.values():
        assert d["solo"] in ids and d["group"] in ids


def test_validator_rejects_bad_builds():
    with pytest.raises(ValueError):
        wbd.validate_build({"id": "X", "class": "HUNTER", "steps": [(1386, 1, "capstone d'abord")]}, DATA["classes"])
    with pytest.raises(ValueError):
        wbd.validate_build({"id": "Y", "class": "HUNTER", "steps": [(1382, 6, "trop")]}, DATA["classes"])
    with pytest.raises(ValueError):
        wbd.validate_build({"id": "Z", "class": "HUNTER", "steps": [(124, 1, "talent guerrier")]}, DATA["classes"])


def test_build_output_matches_sources():
    for b in talent_builds.BUILDS:
        assert wbd.validate_build(b, DATA["classes"]) == next(x for x in DATA["builds"] if x["id"] == b["id"])


def test_resolve_desc_tokens():
    eff, dur = {(1, 0): 15.0, (2, 1): 3.0}, {1: 12000}
    assert wbd.resolve_desc("Augmente de $s1 % pendant $d, $2s2 et $lpoint:points;.", 1, eff, dur, {}) == \
        "Augmente de 15 % pendant 12 s, 3 et points."


def test_lua_data_file():
    lua = (ADDON / "Data" / "Talents.lua").read_text(encoding="utf-8")
    assert "T.Data.Talents" in lua and 'id = "HUNTER_BM_SOLO"' in lua
    toc = [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert toc.index("Data\\Talents.lua") < toc.index("Modules\\Talents.lua")


def test_respec_cost():
    assert talent_builds.respec_cost_gold(0) == 1
    assert talent_builds.respec_cost_gold(3) == 15
    assert talent_builds.respec_cost_gold(20) == 50
    assert talent_builds.respec_cost_gold(4, months_since_last=2) == 10


def test_ai_context_has_talents():
    db = {"chars": {"x": {"class": "HUNTER", "level": 38, "zone": "Z", "summary": {"talents": {
        "unspent": 2, "points": "13/0/0", "ranks": "1:1:2=5,1:1:3=5,1:3:3=3"}}}}}
    ctx = ai_context.build_context(db=db, facts=[])
    tal = ctx["talents"]
    assert tal["points_libres"] == 2 and "Fureur libérée 4/5" in tal["prochains_solo"][0]
    assert "Précision" in tal["groupe"] and "50 po" in tal["double_spe"]
    assert "talents" in ai_context.build_prompt("quels talents ?", ctx)[:2500]
    assert talents.talents_context("inconnu", 10) == {}
    assert talents.builds_for("Chasseur")


def test_never_spends_points():
    code = "\n".join(re.sub(r"--.*$", "", ln) for ln in SRC.read_text(encoding="utf-8").splitlines())
    for bad in ("LearnTalent", "SendChatMessage", "ConfirmTalentWipe", "SetActiveTalentGroup"):
        assert bad not in code, bad


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_syntax_no_globals():
    for f in (SRC, ADDON / "Data" / "Talents.lua"):
        assert subprocess.run(["luac5.1", "-p", str(f)], capture_output=True).returncode == 0
    assert "SETGLOBAL" not in subprocess.run(["luac5.1", "-l", "-p", str(SRC)], capture_output=True, text=True).stdout


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["modern", "legacy"])
def test_talents_scenario(flavor):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", "HUNTER",
                        "--scenario", str(ROOT / "tests/wow/talents_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
