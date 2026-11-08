"""Route (agent ROUTE): multimodal fastest path (walk / flight / boat / zeppelin / hearth) in Python and in the addon,
quest selection, Shortest Path Forever hand-off. Exact numbers from the shipped tables (fake places, no names)."""
import math
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from treeaicoach.wow.planner import transport as TR
from treeaicoach.wow.planner.data import taxi_data

ROOT = Path(__file__).resolve().parents[2]
ADDON = Path(os.environ.get("TREEAI_WOW_ADDON") or ROOT / "wow_addon" / "TreeAI")
LUA = shutil.which("lua5.1")
needs_lua = pytest.mark.skipif(not LUA, reason="lua5.1 not installed")
P = TR.Place


def legs(t):
    return [(lg.mode, round(lg.seconds, 4), lg.to.name) for lg in t.legs]


def test_flight_beats_walking_with_known_nodes():
    tx = taxi_data()
    a, b = P(1, 1707.6, -4315.7), P(1, -481.8, -2596.1)          # 30 yd from Orgrimmar FM, 40 yd from La Croisée FM
    t = TR.fastest(a, b, "Horde", tx, [23, 25])
    # 30·1.3/7 + (3304/32 + 5 + 20 boarding) + 40·1.3/7
    assert t.seconds == pytest.approx(141.25, abs=1e-6)
    assert legs(t) == [("walk", 5.5714, "Orgrimmar"), ("fly", 128.25, "La Croisée"), ("walk", 7.4286, "")]
    assert TR.leg_label(t.legs[1]) == "Vol pour La Croisée · 2 min"
    walk = TR.fastest(a, b, "Horde", tx, [])                    # flight path not discovered → walk only
    assert walk.modes == ["walk"] and walk.seconds == pytest.approx(517.0233, abs=1e-3)


def test_zeppelin_for_the_horde_only():
    tx = taxi_data()
    a, b = P(1, 1368.1, -4658.0), P(0, 1992.4, 293.0)           # Orgrimmar tower → Tirisfal tower (other continent)
    t = TR.fastest(a, b, "Horde", tx, [])
    assert t.seconds == pytest.approx(50 * 1.3 / 7 + 356 / 2 + 131 + 70 * 1.3 / 7)   # 331.2857
    assert legs(t) == [("walk", 9.2857, "Orgrimmar"), ("zeppelin", 309.0, "Clairières de Tirisfal"), ("walk", 13.0, "")]
    assert TR.leg_label(t.legs[1]) == "Zeppelin pour Clairières de Tirisfal · 5 min"
    al = TR.fastest(a, b, "Alliance", tx, [])
    assert "zeppelin" not in al.modes and al.modes.count("boat") == 2
    assert al.seconds == pytest.approx(2287.95, abs=0.01)


def test_hearthstone_when_ready_and_bind_is_closer():
    tx = taxi_data()
    a, bind, b = P(1, -441.8, -2596.1), P(1, 1677.6, -4315.7, "Orgrimmar"), P(1, 1577.6, -4315.7)
    t = TR.fastest(a, b, "Horde", tx, [], hearth=bind, hearth_ready=True)
    assert t.seconds == pytest.approx(15 + 100 * 1.3 / 7) and t.modes == ["hs", "walk"]
    assert TR.leg_label(t.legs[0]) == "Pierre de foyer → Orgrimmar"
    assert TR.fastest(a, b, "Horde", tx, [], hearth=bind, hearth_ready=False).modes == ["walk"]


def test_short_trips_stay_on_foot():
    t = TR.fastest(P(1, 0, 0), P(1, 100, 0), "Horde", taxi_data(), [23, 25])
    assert t.modes == ["walk"] and t.seconds == pytest.approx(100 * 1.3 / 7)


def test_hub_matrix_has_the_crossings():
    keys, places, H, nxt, mode = TR.hubs("Horde", taxi_data(), [23, 25])
    i, j = keys.index(("dock", 13)), keys.index(("dock", 14))
    assert H[i][j] == pytest.approx(309.0) and mode[(i, j)] == "zeppelin"
    k23, k25 = keys.index(("taxi", 23)), keys.index(("taxi", 25))
    assert H[k23][k25] == pytest.approx(128.25) and mode[(k23, k25)] == "fly"
    ka, _, Ha, _, ma = TR.hubs("Alliance", taxi_data(), [])
    ia, ja = ka.index(("dock", 13)), ka.index(("dock", 14))
    assert (ia, ja) not in ma and Ha[ia][ja] > 1000                 # no Horde zeppelin: boats round the world
    assert not math.isinf(Ha[ia][ja])


def test_transport_edges_wait_is_half_the_period():
    e = {(a, b): (k, s) for a, b, k, s in TR.transport_edges("Horde")}
    assert e[(1, 2)] == ("boat", 282.5)            # Cabestan → Baie-du-Butin : 351/2 + 107
    assert e[(4, 3)] == ("zeppelin", 229.5)        # Orgrimmar → Grom'gol : 303/2 + 78
    assert (5, 6) not in e                         # Alliance boat


def test_lua_table_is_generated_from_python():
    import importlib.util
    spec = importlib.util.spec_from_file_location("wbt", ROOT / "tools" / "wow_build_travel.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    text = (ADDON / "Data" / "Travel.lua").read_text(encoding="utf-8")
    assert text == mod.build(), "Data/Travel.lua is stale: run python tools/wow_build_travel.py"
    names = re.findall(r'\[(\d+)\] = \{ "([^"]+)", (\d+), ', text.split("\n  lines = {")[0])
    assert {int(i): n for i, n, _ in names} == {d: v[0] for d, v in TR.DOCKS.items()}
    assert "Clairières de Tirisfal" in text and "Campement Grom'gol" in text and "Port de Menethil" in text


def test_plan_steps_name_the_transport():
    from treeaicoach.wow.planner import data as DM, routes  # noqa: PLC0415
    exp = {"v": 1, "char": "Testeur-Royaume", "player": {"level": 30, "class": "HUNTER", "faction": "Horde",
           "map": 1411, "x": 50.5, "y": 14.0, "taxi": []},
           "maps": [], "quests": [
               {"id": 1, "t": "Quête A", "lv": 30, "st": "log", "f": {"map": 1420, "x": 61.0, "y": 59.5},
                "o": []}]}
    pb = DM.load_problem(exp)
    assert pb is not None and pb.faction == "Horde"
    DM.build_matrix(pb)
    assert (pb.START, 0) in pb.via, "the other continent is reached through a hub"
    i, j = pb.via[(pb.START, 0)]
    st = routes.transport_steps(pb, pb.rects, i, j, 0)
    assert any(s["k"] == "zep" and s["label"] == "Zeppelin pour Clairières de Tirisfal" for s in st), st


@needs_lua
def test_syntax():
    for f in ("Modules/Route.lua", "Data/Travel.lua"):
        assert subprocess.run([LUA.replace("lua5.1", "luac5.1"), "-p", str(ADDON / f)]).returncode == 0


def test_toc_order():
    lines = [l.strip() for l in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert lines.index("Modules\\Route.lua") == lines.index("Modules\\Planner.lua") + 1
    assert lines.index("Data\\Travel.lua") < lines.index("Modules\\Route.lua")


@needs_lua
@pytest.mark.parametrize("flavor", ["modern", "forever"])
def test_scenario(flavor):
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--flavor", flavor, "--class", "HUNTER",
                        "--allow", "ShortestPathForever", "--scenario", "tests/wow/route_scenario.lua", str(ADDON)],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
