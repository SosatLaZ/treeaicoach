"""« Zone de travail » + always-prepared route (agent ZONEPLAN): harness scenario on the real addon (fake quest log,
fake QuestieDB, fake names) and the app solver honouring the same work zone / ignore list (export.work)."""
from __future__ import annotations

import copy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
ADDON = ROOT / "wow_addon" / "TreeAI"
LUA = shutil.which("lua5.1")
ALLOW = ["LibQuestieDB", "QuestDB", "NpcDB", "ObjectDB", "ItemDB", "TreeAIPlan", "IsQuestFlaggedCompleted"]
sys.path.insert(0, str(HERE))

from planner_synth import make_export  # noqa: E402

from treeaicoach.wow.planner import data, routes  # noqa: E402


@pytest.mark.skipif(not LUA, reason="lua5.1 not installed")
@pytest.mark.parametrize("flavor", ["modern", "forever"])
def test_zoneplan_scenario(flavor):
    cmd = [LUA, str(HERE / "harness.lua"), "-q", "--flavor", flavor, "--class", "HUNTER"]
    for a in ALLOW:
        cmd += ["--allow", a]
    cmd += ["--scenario", str(HERE / "zoneplan_scenario.lua"), str(ADDON)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=ROOT)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-4000:] + r.stderr[-2000:]


def _two_zone_export() -> dict:
    exp = make_export(nq=12, seed=3, in_log=0.4)
    zone = exp["player"]["map"]
    other = 99001                                                    # a fake map on another continent
    exp["maps"].append({"id": other, "c": 9, "name": "Ailleurs", "x0": 0.0, "y0": 0.0, "x1": -3000.0, "y1": -2000.0})
    for q in exp["quests"][:4]:                                      # 4 quests (log or not) live over there
        q["zone"] = other
        for k in ("s", "f"):
            if isinstance(q.get(k), dict):
                q[k]["map"] = other
        for o in q["o"]:
            o["map"] = other
    return exp


def test_solver_keeps_the_work_zone_and_ignore_list():
    exp = _two_zone_export()
    zone = exp["player"]["map"]
    other_ids = {q["id"] for q in exp["quests"][:4]}
    free = data.load_problem(copy.deepcopy(exp))
    assert free and {q.id for q in free.quests} & other_ids        # without a work zone: everything is a candidate
    exp["work"] = {"mode": "auto", "maps": [zone], "ignore": [exp["quests"][5]["id"]], "extra": []}
    pb = data.load_problem(copy.deepcopy(exp), work=True)
    ids = {q.id for q in pb.quests}
    assert not ids & other_ids, "no quest of the other continent unless picked"
    assert exp["quests"][5]["id"] not in ids, "ignored quest left out"
    extra = exp["quests"][0]["id"]
    exp["work"]["extra"] = [extra]                                   # detour accepted in game → planned
    assert extra in {q.id for q in data.load_problem(copy.deepcopy(exp), work=True).quests}
    exp["work"] = {"mode": "continent", "cont": data.map_rects(exp["maps"])[zone].c}
    pb = data.load_problem(copy.deepcopy(exp), work=True)
    assert not {q.id for q in pb.quests} & other_ids


def test_compute_plan_honours_work_zone():
    exp = _two_zone_export()
    zone = exp["player"]["map"]
    exp["work"] = {"mode": "zones", "maps": [zone], "ignore": [], "extra": []}
    plan = routes.compute_plan(exp, seconds=1.0, seed=1, now=1793000000)
    assert plan and plan["routes"]
    other_ids = {q["id"] for q in exp["quests"][:4]}
    for r in plan["routes"]:
        for s in r["steps"]:
            assert s.get("q") not in other_ids, (r["id"], s)


def test_wiring():
    toc = [x.strip() for x in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert toc.index("Modules\\Route.lua") < toc.index("Modules\\ZonePlan.lua")
    core = (ADDON / "Core.lua").read_text(encoding="utf-8")
    assert "ZonePlan = { lite = true" in core and "onRightClick" in core
    sim = (HERE / "session_sim.lua").read_text(encoding="utf-8")
    assert "ZonePlan =" in sim, "perf cap for the module in session_sim"
