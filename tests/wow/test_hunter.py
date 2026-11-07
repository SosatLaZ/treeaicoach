import re
import shutil
import subprocess
from pathlib import Path

import pytest

from treeaicoach.wow.classes import hunter

ROOT = Path(__file__).resolve().parents[2]
MODS = ROOT / "wow_addon" / "TreeAI" / "Modules"


def test_food_table_matches_lua():
    src = (MODS / "Hunter.lua").read_text(encoding="utf-8")
    block = src.split("local FOODS = {", 1)[1].split("\n}\n", 1)[0]
    lua = {(int(a), b, int(c)) for a, b, c in re.findall(r'\{(\d+), "(\w+)", (\d+)\}', block)}
    assert lua == set(hunter.FOODS)
    assert len({f[0] for f in hunter.FOODS}) == len(hunter.FOODS)


def test_pick_food_prefers_cheapest_full_effect():
    assert hunter.pick_food([3770, 3771, 4599], ["meat"], 26) == (3771, "full")
    assert hunter.pick_food([3770, 3771, 4599], ["meat"], 25) == (3770, "full")
    assert hunter.pick_food([3770, 4536], ["fruit"], 20) == (4536, "partial")


def test_pick_food_partial_and_none():
    assert hunter.pick_food([3770, 117], ["meat"], 30) == (3770, "partial")
    assert hunter.pick_food([117], ["meat"], 40) == (None, None)
    assert hunter.pick_food([4536], ["meat"], 5) == (None, None)
    assert hunter.pick_food(None, None, None) == (None, None)
    assert hunter.pick_food(["x"], ["meat"], 5) == (None, None)


def test_tips_from_savedvars():
    db = {"chars": {
        "Fakehunt-Fakerealm": {"summary": {"level": 26, "hunter": {
            "t": 5, "level": 26, "clips": 7,
            "pet": {"level": 22, "happiness": 1, "loyaltyRate": -1, "diet": {1: "meat", 2: "fish"},
                    "tpFree": 3},
            "ammo": {"kind": "arrow", "total": 40, "quiver": False, "mismatch": False}}}},
        "Fakemage-Fakerealm": {"summary": {"level": 12}},
    }}
    assert list(hunter.load_snapshots(db)) == ["Fakehunt-Fakerealm"]
    tips = hunter.hunter_tips(hunter.latest_snapshot(db))
    assert tips[0].startswith(("Familier mécontent", "Plus que"))
    joined = " ".join(tips)
    for part in ("viande, poisson", "40 flèches", "carquois", "3 point(s)", "7 tirs", "niveau 22"):
        assert part in joined


def test_tips_happy_hunter_is_quiet():
    snap = {"level": 26, "pet": {"level": 26, "happiness": 3, "diet": ["meat"], "food": 3771},
            "ammo": {"kind": "bullet", "total": 800, "quiver": True}}
    assert hunter.hunter_tips(snap) == []


def test_tips_robust():
    assert hunter.hunter_tips(None) == []
    assert hunter.hunter_tips({"pet": "x", "ammo": 3, "level": "bad"}) == []
    assert hunter.load_snapshots({}) == {}
    assert hunter.load_snapshots({"chars": {"a": "b", "c": {"summary": None}}}) == {}
    assert hunter.latest_snapshot(None) is None


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
@pytest.mark.parametrize("name", ["Hunter.lua", "Classes.lua"])
def test_lua_syntax_and_no_globals(name):
    path = str(MODS / name)
    assert subprocess.run(["luac5.1", "-p", path], capture_output=True).returncode == 0
    listing = subprocess.run(["luac5.1", "-l", "-p", path], capture_output=True, text=True).stdout
    assert "SETGLOBAL" not in listing


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
def test_lua_smoke():
    r = subprocess.run(["lua5.1", str(ROOT / "tests" / "wow" / "hunter_smoke.lua"), str(ROOT)],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "OK" in r.stdout
