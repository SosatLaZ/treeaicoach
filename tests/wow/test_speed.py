"""Speed techniques (agent SPEED, research/speed.md): level-gate grind, hearthstone savings, kill on the way,
flight point on the way, grind when quests run out (planner, Python) + death choice / live hearth / plan tips
(addon, Modules/Speed.lua). Exact numbers, synthetic quests and fake names only."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from treeaicoach.wow.planner import data, routes, solver
from treeaicoach.wow.planner import models as M
from treeaicoach.wow.planner import speed as SP

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
LUA = shutil.which("lua5.1")


def quest(i, st, s=None, f=(50, 50), o=None, lv=26, rl=20):
    d = {"id": i, "t": f"Q{i}", "lv": lv, "rl": rl, "xp": 2100, "st": st, "done": 0, "zone": 1424, "flags": 8,
         "f": {"map": 1424, "x": f[0], "y": f[1]}, "o": o or []}
    if s:
        d["s"] = {"map": 1424, "x": s[0], "y": s[1]}
    return d


def talk(x, y):
    return [{"k": "talk", "n": 1, "map": 1424, "x": x, "y": y}]


def export(quests, px=20, py=50, xp=0, taxi=(13,), session=0, hearth=None, level=26):
    pl = {"class": "HUNTER", "level": level, "xp": xp, "faction": "Alliance", "map": 1424, "x": px, "y": py,
          "speed": 7.0, "logMax": 20, "logCount": sum(1 for q in quests if q["st"] == "log"), "taxi": list(taxi),
          "session": session}
    if hearth:
        pl["hearth"] = hearth
    return {"v": 1, "char": "Testeur-Royaume", "player": pl, "maps": [], "quests": quests}


WOLVES = quest(2, "log", f=(90, 90), o=[{"k": "kill", "t": "Loup gris tué : 0/6", "n": 6, "have": 0, "map": 1424,
                                        "x": 50, "y": 51, "r": 60, "sp": 8, "ml": 26}])
FAR = quest(3, "avail", s=(80, 50), f=(80, 50), o=talk(81, 50))
ALL_TAXI = list(range(1, 200))


def test_level_gate_grind_numbers():
    need = M.xp_needed(26)
    assert need == 36400
    pb = data.load_problem(export([quest(1, "avail", s=(25, 50), f=(25, 50), rl=27, o=talk(26, 50))], xp=need - 3000))
    s = solver.Solver(pb, "clear")
    # grind rate = 175 XP / 27.65 s kill cycle / 1.6 efficiency = 3.956 XP/s
    assert s.grate == pytest.approx(175 / (15 + 10 + 15 * 0.15 / 0.85) / 1.6)
    assert s.gate_grind(26, need - 3000, 27) == 3000
    assert s.gate_grind(26, 0, 27) == -1                    # 36 400 XP missing > half a level
    assert s.gate_grind(26, need - 3000, 28) == -1          # two levels away: not on the spot
    sim = s.simulate([0, 1, 2])
    assert sim.grind_before[0] == pytest.approx(3000 / s.grate) and round(sim.grind_before[0]) == 758
    assert sim.level_at == [27, 27, 27] and s.evaluate([0, 1, 2])[0] > -1e9
    st = routes.build_steps(pb, s, [0, 1, 2])
    assert st[0] == {"k": "grind", "map": 1424, "x": 25.0, "y": 50.0, "eta": 0,
                     "label": "Tue des monstres ici ~13 min jusqu'au niveau 27",
                     "tip": "Niveau 27 requis pour « Q1 » : 13 min de grind ici plutôt qu'un aller-retour"}
    assert st[1]["k"] == "pick" and st[1]["eta"] == 14
    # without the gate the quest is impossible (the solver used to drop it)
    s.gate = False
    assert s.evaluate([0, 1, 2])[0] == -float("inf")


def test_kill_on_the_way_tip():
    pb = data.load_problem(export([WOLVES, FAR], taxi=ALL_TAXI))
    s = solver.Solver(pb, "clear")
    assert [(n.kind, n.q) for n in pb.nodes] == [("obj", 0), ("turn", 0), ("pick", 1), ("obj", 1), ("turn", 1)]
    st = routes.build_steps(pb, s, [2, 3, 4, 0, 1])
    # the walk 20 % → 80 % crosses the wolves (8 spawns → 4 run past, 6 still needed)
    assert st[0]["tip"] == "Tue en chemin : 4 Loups gris pour « Q2 »"
    assert sum(1 for x in st if "tip" in x) == 1
    assert SP.plural("Loup gris", 4) == "Loups gris" and SP.plural("Corbeau", 2) == "Corbeaux"
    assert SP.mob_name({"t": "Ours enragé tué : 3/10"}) == "Ours enragé"


def test_flight_point_on_the_way():
    q = quest(4, "avail", s=(49.4, 75), f=(49.4, 75), o=talk(49.4, 76))
    pb = data.load_problem(export([q], px=49.4, py=30, taxi=[13]))
    fps = {t for t, _, _ in SP.unknown_flight_points(pb)}
    assert 14 in fps and 13 not in fps and 50 not in fps    # Austrivage (A) yes; known / boats no
    st = routes.build_steps(pb, solver.Solver(pb, "clear"), [0, 1, 2])
    assert st[0] == {"k": "fp", "map": 1424, "x": 49.4, "y": 52.1, "eta": 0, "label": "Point de vol : Austrivage",
                     "tip": "Prends le point de vol Austrivage en passant (+5 s) : il servira pour la suite"}
    horde = export([q], px=49.4, py=30, taxi=[13])
    horde["player"]["faction"] = "Horde"
    assert 14 not in {t for t, _, _ in SP.unknown_flight_points(data.load_problem(horde))}


def test_hearthstone_tip_with_minutes_saved():
    q = quest(5, "avail", s=(80, 80), f=(80, 80), o=talk(80, 81))
    pb = data.load_problem(export([q], px=5, py=5, taxi=[], hearth={"cd": 0, "map": 1424, "x": 78, "y": 78}))
    s = solver.Solver(pb, "clear")
    sim = s.simulate([0, 1, 2])
    assert sim.hearth_before == [True, False, False] and round(sim.hearth_save[0]) == 824
    st = routes.build_steps(pb, s, [0, 1, 2])
    assert st[0]["k"] == "hs" and st[0]["tip"] == "Pierre de foyer maintenant : 14 min gagnées"
    assert SP.hearth_tip(60) == "Pierre de foyer maintenant : 60 s gagnées"


def test_grind_when_quests_run_out():
    e = export([WOLVES, FAR], session=60, taxi=ALL_TAXI)
    pb = data.load_problem(e)
    spot = SP.grind_spot(pb, 26)
    assert (spot[0].mx, spot[0].my, spot[1], spot[2], spot[3]) == (50.0, 51.0, "Loup gris", 26, 8)
    assert round(solver.grind_rate(pb) * 3600) == 14242
    plan = routes.compute_plan(e, seconds=1, now=1762500000)
    xph = next(r for r in plan["routes"] if r["id"] == "xph")
    last = xph["steps"][-1]
    left = 60 - xph["minutes"]
    assert last["k"] == "grind" and last["eta"] == 60 and (last["x"], last["y"]) == (50.0, 51.0)
    assert last["label"] == f"Grind : tue des Loups gris (niv 26) ici ~{left} min"
    assert last["tip"] == "Plus de quête rentable : grind ici ~14 200 XP/h"
    clear = next(r for r in plan["routes"] if r["id"] == "clear")
    assert clear["steps"][-1]["k"] != "grind"                # « Finir la zone » never pads with grind
    assert SP.final_grind_step(pb, 26, 200, 0, 1.0) is None  # < 5 min left: nothing


@pytest.mark.skipif(not LUA, reason="lua5.1 not installed")
@pytest.mark.parametrize("flavor", ["modern", "legacy", "forever"])
def test_speed_addon_scenario(flavor):
    r = subprocess.run([LUA, str(HERE / "harness.lua"), "-q", "--flavor", flavor, "--allow", "C_DeathInfo",
                        "--scenario", str(HERE / "speed_scenario.lua"), str(ROOT / "wow_addon" / "TreeAI")],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-3000:] + r.stderr[-2000:]


def test_toc_loads_speed_after_planner():
    lines = [x.strip() for x in (ROOT / "wow_addon" / "TreeAI" / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert lines.index("Modules\\Speed.lua") == lines.index("Modules\\Planner.lua") + 1
