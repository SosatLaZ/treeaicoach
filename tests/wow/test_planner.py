"""Route planner, addon side (Modules/Export.lua + Modules/Planner.lua): harness scenarios on the real addon,
a 2-client plan sync check, TOC wiring and the TreeAI_Plan data-addon stub (fake names only)."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).parent
ADDON = ROOT / "wow_addon" / "TreeAI"
PLAN_ADDON = ROOT / "wow_addon" / "TreeAI_Plan"
ALLOW = ["LibQuestieDB", "QuestDB", "NpcDB", "ObjectDB", "ItemDB", "TreeAIPlan", "TomTom", "ShortestPathForever",
         "UiMapPoint", "IsQuestFlaggedCompleted", "C_SuperTrack"]
LUA = shutil.which("lua5.1")


def run(flavor: str = "modern", env: dict | None = None) -> subprocess.CompletedProcess:
    cmd = [LUA, str(HERE / "harness.lua"), "-q", "--flavor", flavor]
    for a in ALLOW:
        cmd += ["--allow", a]
    cmd += ["--scenario", str(HERE / "planner_scenario.lua"), str(ADDON)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=ROOT,
                          env={**os.environ, **(env or {})})


@pytest.mark.skipif(not LUA, reason="lua5.1 not installed")
@pytest.mark.parametrize("flavor", ["modern", "legacy"])
def test_planner_scenario(flavor):
    r = run(flavor)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-4000:] + r.stderr[-2000:]


@pytest.mark.skipif(not LUA, reason="lua5.1 not installed")
def test_planner_two_clients_sync(tmp_path):
    f = tmp_path / "pln.txt"
    a = run(env={"PLANNER_MODE": "syncA", "PLANNER_SYNC_FILE": str(f)})
    assert a.returncode == 0 and "RESULT ok" in a.stdout, a.stdout[-3000:]
    msgs = f.read_text(encoding="utf-8").split()
    assert msgs and all(len(m.encode()) <= 255 and "|" not in m for m in msgs)
    b = run(env={"PLANNER_MODE": "syncB", "PLANNER_SYNC_FILE": str(f)})
    assert b.returncode == 0 and "RESULT ok" in b.stdout, b.stdout[-3000:]


def test_toc_wiring():
    lines = [x.strip() for x in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    assert any(l.startswith("## OptionalDeps:") and "TreeAI_Plan" in l for l in lines)
    q, e, p = (lines.index(f"Modules\\{n}.lua") for n in ("Quests", "Export", "Planner"))
    assert q < e < p, "Export then Planner right after Quests"


def test_plan_addon_stub_is_pure_data():
    toc = (PLAN_ADDON / "TreeAI_Plan.toc").read_text(encoding="utf-8")
    assert "Plan.lua" in toc.split() and "## Interface:" in toc and "SavedVariables" not in toc
    code = [ln for ln in (PLAN_ADDON / "Plan.lua").read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("--")]
    assert code == [], "the stub defines nothing (the app writes TreeAIPlan = {...})"


def test_planner_never_touches_blizzard_quest_ui():
    for name in ("Planner.lua", "Export.lua"):
        src = (ADDON / "Modules" / name).read_text(encoding="utf-8")
        code = "\n".join(ln.split("--")[0] for ln in src.splitlines())
        for bad in ("SelectQuestLogEntry", "SetSelectedQuest", "QuestMapFrame", "ShowUIPanel", "QuestLogPushQuest",
                    "hooksecurefunc", "SetParent(", "COMBAT_LOG_EVENT"):
            assert bad not in code, f"{name}: {bad}"
"""Route planner: exact formula numbers (research report 4), solver vs brute force, Monte Carlo zone run,
solve time, plan file format. Synthetic data only (fake quests / names)."""

from __future__ import annotations

import math
import os
import random
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from planner_synth import brute_force, make_export  # noqa: E402

from treeaicoach.wow import planner, savedvars  # noqa: E402
from treeaicoach.wow.planner import data, models as M, routes, solver, writer  # noqa: E402


# ------------------------------------------------------------------ formulas (exact numbers)
def test_kill_xp_level_26():
    assert [M.kill_xp(26, ml) for ml in (26, 27, 28, 30, 25, 23, 20, 19)] == [175, 184, 193, 210, 159, 127, 80, 0]
    assert M.kill_xp(26, 40) == 210                         # capped at +4 (+20 %)
    assert M.kill_xp(26, 26, elite=True) == 438             # ×2.5 outside raids
    assert M.kill_xp(26, 26, rested=True) == 350


def test_grey_zero_diff_and_colours():
    assert [M.grey_level(pl) for pl in (5, 26, 30, 40, 50, 60)] == [0, 19, 22, 31, 39, 51]
    assert [M.zero_diff(pl) for pl in (7, 9, 11, 15, 19, 26, 35, 60)] == [5, 6, 7, 8, 9, 11, 12, 17]
    assert [M.quest_color(26, q) for q in (31, 29, 28, 24, 20, 19)] == ["red", "orange", "yellow", "yellow",
                                                                        "green", "grey"]
    assert M.green_range(26) == 6


def test_quest_xp_decay_and_estimate():
    assert M.quest_xp_estimate(26) == 2106
    assert [M.quest_xp_estimate(lv) for lv in (10, 20, 30)] == [650, 1500, 2550]
    assert [M.quest_xp(2100, 26, ql) for ql in (21, 20, 19, 18, 17, 16, 10)] == [2100, 1680, 1260, 840, 420, 210, 210]
    assert M.quest_xp(2100, 60, 59) == 0


def test_xp_curve_total():
    assert sum(M.XP_NEEDED.values()) == 4_084_700


def test_group_kill_xp():
    assert M.group_kill_xp([26] * 4, 26) == 57               # 175 × 1.3 / 4 = 56.9 → 32.5 % each
    assert M.group_kill_xp([26, 26], 26) == 88               # rate(2) = 1.0
    assert M.group_kill_xp([26] * 3, 26) == 68               # 175 × 1.166 / 3
    # the top member (40) greys a level-26 mob → the others get half (+1), the top member nothing
    assert M.group_kill_xp([26, 40], 26, me=0) == math.floor(175 * 1.0 * 26 / 66 * 0.5 + 1 + 0.5)
    assert M.group_kill_xp([26, 40], 26, me=1) == 0


def test_power_feasibility_examples():
    assert [round(M.lf(d), 3) for d in (0, 2, -1, -3, -4, -5, -6, -9)] == [1.0, 1.21, 0.909, 0.62, 0.5, 0.38,
                                                                          0.25, 0.25]
    hunter = M.Member("a", "HUNTER", 26)
    assert M.feasibility([hunter], 26, rank=1) == pytest.approx(0.65)          # risqué
    assert M.feas_label(0.65) == "risque"
    assert M.feasibility([hunter], 24, rank=1) == pytest.approx(0.7865, abs=1e-4)
    pals = [M.Member(str(i), "PALADIN", 26) for i in range(4)]
    assert sum(M.capacity(m, 27) for m in pals) / 8 == pytest.approx(1.0, abs=0.001)
    assert M.feasibility(pals, 27, rank=1, pull=2) == pytest.approx(1.25, abs=0.002)  # tank + healer
    assert M.feas_label(1.3) == "facile" and M.feas_label(1.0) == "faisable" and M.feas_label(0.5) == "impossible"
    assert M.gear_factor("HUNTER", 26, 2.1 * 26 * 1.21) == pytest.approx(1.1)


def test_kill_cycle_grind_rate_and_death():
    c = M.kill_cycle("HUNTER", 26, 26)
    assert c == pytest.approx(15 + 10 + 15 * 0.15 / 0.85)    # TTK + search + downtime
    assert 175 / c * 3600 == pytest.approx(22787, abs=5)     # « ≈ 22k XP/h » grind baseline
    assert M.p_death(0.85) == pytest.approx(0.5)
    assert M.p_death(0.5) < 0.02 and M.p_death(1.2) > 0.98


def test_objective_time_and_group_drops():
    c = M.kill_cycle("HUNTER", 26, 26)
    kill = M.objective_time({"k": "kill", "n": 10, "ml": 26}, "HUNTER", 26)
    assert kill.median == pytest.approx(10 * c) and kill.kills == 10
    item = M.objective_time({"k": "item", "n": 8, "p": 0.35, "id": 1, "ml": 26}, "HUNTER", 26)
    assert item.kills == pytest.approx(8 / M.Calib().drop_p(1, 0.35))
    assert item.p80 > item.median
    # Bayesian drop rate: (k + 10·p0) / (n + 10)
    cal = M.Calib(drops={7: (3, 9)})
    assert cal.drop_p(7, 0.35) == pytest.approx((3 + 3.5) / 19)
    assert cal.drop_p(8, None) == pytest.approx(0.45)
    # 4 players, 10 drops at 25 % → 160 corpses: « galère »
    four = [M.Member(str(i), "HUNTER", 26) for i in range(4)]
    g = M.galere({"lv": 26, "o": [{"k": "item", "n": 10, "p": 0.25, "id": 9, "ml": 26}]}, four)
    assert "160 corps" in g.reason and g.label in ("galere", "eviter")
    solo = M.galere({"lv": 26, "o": [{"k": "kill", "n": 10, "ml": 25}]}, four[:1])
    assert solo.label == "fluide"


def test_zone_switch_rule():
    assert M.should_switch_zone(20000, 30000, 10, 90)[0] is True       # 20k < 30k·(1−10/90)/1.1 = 24.2k
    assert M.should_switch_zone(25000, 30000, 10, 90)[0] is False
    assert M.should_switch_zone(30000, 0, 0, 90, non_grey_left=2)[0] is True
    assert M.should_switch_zone(30000, 0, 0, 90, level=31, zone_median=26)[0] is True


def test_calibration_from_learned_stats():
    learn = {"drops": {"77": {"k": 2, "n": 4}}, "comp": {1424: {"s": 4.0, "n": 2}},
             "flights": {"13-14": {"s": 120, "n": 2}}, "respawn": [{"s": 600, "n": 2}]}
    cl = {"ttk": {"0": {"s": 100, "n": 10}, "2": {"s": 1, "n": 1}}, "eff": {"s": 6, "n": 4},
          "search": {"s": 50, "n": 10}, "downtime": {"s": 1, "n": 10}}
    c = M.calib_from_learn(learn, cl)
    assert c.ttk == {0: 10.0} and c.eff == 1.5 and c.search == 5.0 and c.downtime == 0.1
    assert c.k(1424) == 2.0 and c.flights[(13, 14)] == 60.0 and c.respawn[1] == 300.0
    assert c.drops[77] == (2.0, 4.0)
    assert M.calib_from_learn("junk", None).eff == 1.6


# ------------------------------------------------------------------ data
def test_map_conversion_and_taxi_graph():
    tx = data.taxi_data()
    assert tx.get("build") == "1.60.1.70862" and len(tx["edges"]) > 200
    rects = data.map_rects([])
    node = next(v for v in tx["nodes"].values() if v[0].startswith("La Croisée"))
    r = rects[1413]                                         # Les Tarides
    mx = (node[3] - r.y0) / (r.y1 - r.y0) * 100
    my = (node[2] - r.x0) / (r.x1 - r.x0) * 100
    assert (round(mx, 1), round(my, 1)) == (51.5, 30.4)     # verified against the in-game map
    c, wx, wy = r.world(mx, my)
    assert (c, round(wx), round(wy)) == (1, round(node[2]), round(node[3]))


def test_load_problem_and_flights():
    exp = make_export(20, seed=3, alt_quests=6)
    pb = data.load_problem(exp)
    assert pb is not None and len(pb.quests) >= 20 and len(pb.D) == len(pb.nodes) + 2
    assert all(len(row) == len(pb.D) for row in pb.D)
    assert pb.via, "far Arathi quests should use the known flight path"
    assert data.load_problem({"v": 2}) is None and data.load_problem(None) is None
    bad = make_export(3, seed=1)
    bad["quests"][0]["f"] = {"map": 999999, "x": 1, "y": 1}           # unknown map: never invent a place
    assert len(data.load_problem(bad).quests) == 2


# ------------------------------------------------------------------ solver
def _feasible(s: solver.Solver, route: list[int]) -> bool:
    return s.evaluate(route)[0] > -math.inf


def test_solver_vs_brute_force():
    gaps = []
    cases = [(5, 0, 0.3, 4), (6, 1, 0.3, 4), (7, 3, 0.3, 4), (8, 4, 0.6, 4), (9, 5, 1.0, 4), (7, 7, 0.2, 20),
             (8, 8, 0.5, 20), (9, 9, 0.8, 20)]
    for nq, seed, inlog, cap in cases:
        e = make_export(nq, seed=seed, max_obj=1, in_log=inlog, level=30, cap=cap)
        for q in e["quests"]:
            q["rl"] = 1
        pb = data.load_problem(e)
        s = solver.Solver(pb, "clear", hearth=False)
        bf = brute_force(s)
        r = solver.solve(pb, "clear", seconds=1.2, hearth=False)
        assert _feasible(s, r.route) and not r.unserved
        assert r.seconds >= bf - 1e-6
        gaps.append(r.seconds / bf - 1)
    assert sum(gaps) / len(gaps) <= 0.02, gaps
    assert max(gaps) <= 0.05, gaps


def test_rate_mode_beats_grind_and_greedy():
    pb = data.load_problem(make_export(30, seed=4))
    r = solver.solve(pb, "rate", seconds=3)
    s = solver.Solver(pb, "rate")
    assert _feasible(s, r.route)
    assert r.xph > solver.grind_rate(pb) * 3600 * 0.8
    # nearest-neighbour-like baseline: all quests in default order (clear construction)
    c = solver.Solver(pb, "clear")
    base = c.construct()
    _, bxp, bt = c.evaluate(base)
    assert r.xph >= bxp / bt * 3600


def test_level_gates_and_log_cap_respected():
    e = make_export(25, seed=6, cap=8, rl_spread=0)
    pb = data.load_problem(e)
    r = solver.solve(pb, "clear", seconds=2)
    s = solver.Solver(pb, "clear")
    sim = s.simulate(r.route)
    logc, lv = pb.log_count, pb.level
    for i, n in enumerate(r.route):
        nd = pb.nodes[n]
        if nd.kind == "pick":
            assert (sim.level_at[i - 1] if i else pb.level) >= pb.quests[nd.q].req
            logc += 1
        elif nd.kind == "turn":
            logc -= 1
        assert logc <= pb.log_cap
    assert sim.level_end >= lv


def test_monte_carlo_zone_run():
    """Replay the XP/h route 300× with random drops (negative binomial) and kill times: the predicted median
    must be within 20 % of the simulated median, and the p80 prediction must cover the median."""
    pb = data.load_problem(make_export(25, seed=11, kinds=("kill", "item")))
    r = solver.solve(pb, "rate", seconds=2)
    s = solver.Solver(pb, "rate")
    s80 = solver.Solver(pb, "rate", p80=True)
    pred, pred80 = s.evaluate(r.route)[2], s80.evaluate(r.route)[2]
    eff = pb.calib.eff
    c = M.kill_cycle(pb.me.cls, pb.level, pb.level, pb.calib)
    R = random.Random(5)
    totals = []
    for _ in range(300):
        t = 0.0
        p = pb.START
        for n in r.route:
            t += pb.D[p][n]
            nd = pb.nodes[n]
            if nd.kind == "obj":
                o = data._list(pb.quests[nd.q].raw["o"])[nd.o - 1]
                need = int(o["n"]) - int(o.get("have", 0))
                cyc = M.kill_cycle(pb.me.cls, pb.level, int(o["ml"]), pb.calib)
                if o["k"] == "item":
                    pr = pb.calib.drop_p(int(o["id"]), o.get("p"))
                    kills, got = 0, 0
                    while got < need:
                        kills += 1
                        got += R.random() < pr
                else:
                    kills = need
                t += sum(R.gammavariate(4, cyc / 4) for _ in range(kills)) * eff
            else:
                t += 5.0
            p = n
        totals.append(t)
    totals.sort()
    med = totals[len(totals) // 2]
    assert abs(pred / med - 1) < 0.2, (pred, med)
    assert pred80 >= med
    assert c > 0


def test_solve_time_250_nodes():
    pb = data.load_problem(make_export(83, seed=1))
    assert len(pb.nodes) >= 240
    t = time.monotonic()
    r = solver.solve(pb, "rate", seconds=12)
    assert time.monotonic() - t < 20
    assert _feasible(solver.Solver(pb, "rate"), r.route) and r.route


def test_hearthstone_is_used_when_shorter():
    e = make_export(6, seed=2)
    e["player"]["x"], e["player"]["y"] = 5.0, 5.0             # far from everything; inn at a hub
    pb = data.load_problem(e)
    r = solver.solve(pb, "clear", seconds=1)
    sim = solver.Solver(pb, "clear").simulate(r.route)
    assert any(sim.hearth_before)


# ------------------------------------------------------------------ routes / plan / writer
def test_compute_plan_routes_and_verdicts():
    plan = routes.compute_plan(make_export(30, seed=2, squad=3, alt_quests=5), seconds=6, now=1762500000)
    ids = [r["id"] for r in plan["routes"]]
    assert ids == ["xph", "clear", "squad", "safe"]
    assert plan["v"] == 1 and plan["char"] == "Testeur-Royaume" and plan["zone"] == 1424
    for r in plan["routes"]:
        assert r["verdict"] in routes.VERDICT_FR and r["reasons"] and r["steps"]
        assert {"k", "map", "x", "y", "label", "eta"} <= set(r["steps"][0])
        assert all(st["k"] in ("pick", "obj", "turn", "fly", "hs", "meet") for st in r["steps"])
        etas = [st["eta"] for st in r["steps"]]
        assert etas == sorted(etas)
    sq = plan["routes"][2]
    assert sq["title"] == "Escouade (4)" and sq["steps"][0]["k"] == "meet"
    assert any(st.get("who") for st in sq["steps"])
    solo = routes.compute_plan(make_export(12, seed=2), seconds=2)
    assert [r["id"] for r in solo["routes"]] == ["xph", "clear", "safe"]


def test_verdict_impossible_in_raid_and_level_spread():
    e = make_export(8, seed=1)
    e["player"]["raid"] = True
    plan = routes.compute_plan(e, seconds=1.5)
    assert all(r["verdict"] == "impossible" for r in plan["routes"])
    assert "raid" in plan["routes"][0]["reasons"][0]
    e = make_export(10, seed=1, squad=1)
    e["squad"][0]["level"] = 33
    plan = routes.compute_plan(e, seconds=2)
    sq = next(r for r in plan["routes"] if r["id"] == "squad")
    assert any("grise les mobs" in x for x in sq["reasons"])


def test_session_too_long():
    e = make_export(30, seed=2)
    e["player"]["session"] = 20
    plan = routes.compute_plan(e, seconds=2)
    clear = next(r for r in plan["routes"] if r["id"] == "clear")
    assert clear["verdict"] in ("long", "zone") and any("Trop long" in x for x in clear["reasons"])


def test_lua_writer_round_trip(tmp_path):
    plan = {"v": 1, "made": 1762500000, "char": "Testeur-Royaume", "zone": 1424,
            "routes": [{"id": "xph", "title": "XP/h max", "xph": 20000, "minutes": 60, "verdict": "galere",
                        "reasons": ['Drop « 20 % »\n"x" \\ y'],
                        "steps": [{"k": "pick", "q": 101, "map": 1424, "x": 40.5, "y": 50.25, "label": "Prendre",
                                   "eta": 1}]}]}
    ok, msg = writer.write_plan(tmp_path, plan)
    assert ok and "/reload" in msg
    d = tmp_path / "TreeAI_Plan"
    toc = (d / "TreeAI_Plan.toc").read_text(encoding="utf-8")
    assert "## Interface: 11507, 16001" in toc and toc.strip().endswith("Plan.lua")
    assert writer.read_plan(tmp_path) == plan
    assert not list(tmp_path.glob(".TreeAI_Plan.*"))
    plan["zone"] = 1417
    assert writer.write_plan(tmp_path, plan)[0] and writer.read_plan(tmp_path)["zone"] == 1417
    luac = shutil.which("luac5.1") or shutil.which("luac")
    if luac:
        assert subprocess.run([luac, "-p", str(d / "Plan.lua")], capture_output=True).returncode == 0
    assert savedvars.parse("X = " + writer.to_lua({"end": 1, "a b": [1, 2], 3: True}))["X"] == \
        {"end": 1, "a b": [1, 2], 3: True}
    assert writer.to_lua({"a": 1, "b": "x"}) == '{ a = 1, b = "x" }'
    assert writer.read_plan(None) is None and writer.read_plan(tmp_path / "nope") is None


def test_compute_and_write_from_savedvars(tmp_path):
    inst = SimpleNamespace(addons_dir=tmp_path / "Interface" / "AddOns")
    lua = "TreeAIDB = " + writer.to_lua({"export": make_export(10, seed=3), "chars": {
        "Testeur-Royaume": {"learn": {"v": 1, "eff": {"s": 4.0, "n": 4}}}}}) + "\n"
    db = savedvars.parse(lua)["TreeAIDB"]
    exp, learn, cl = planner.inputs_from_db(db)
    assert exp["char"] == "Testeur-Royaume" and cl["eff"]["n"] == 4 and learn is None
    res = planner.compute_and_write(inst, seconds=2, db=db)
    assert res["ok"], res["message"]
    assert writer.read_plan(inst.addons_dir)["routes"][0]["id"] == "xph"
    assert planner.compute_and_write(inst, seconds=1, db={})["ok"] is False
    assert planner.compute_and_write(None)["ok"] is False


def test_summaries_for_ui_and_coach():
    plan = routes.compute_plan(make_export(12, seed=5), seconds=2)
    lines = routes.summary_lines(plan, "clear")
    assert lines[0].startswith("Trajet « Finir la zone »") and "Prochaines étapes" in lines[2]
    from treeaicoach.ui_page_wow import plan_rows
    from treeaicoach.wow import ai_context

    rows = plan_rows(plan)
    assert len(rows) == 3 and "XP/h" in rows[0][0]
    assert plan_rows(None) == [] and routes.summary_lines(None) == []
    db = {"chars": {"Testeur-Royaume": {"class": "HUNTER", "level": 26, "updated": 1,
                                        "plan": {"route": "safe", "step": 1}}}}
    ctx_lines = ai_context.plan_context(plan, db)
    assert ctx_lines and "Prudent" in ctx_lines[0]
    ctx = ai_context.build_context(SimpleNamespace(_wow_plan=plan, _wow_db=db), facts=[])
    assert list(ctx)[:3] == ["classe", "niveau", "trajet"]
