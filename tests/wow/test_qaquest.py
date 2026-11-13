"""QA-QUEST (round 9): regression tests for every P0 / P1 finding of the quest-guidance QA (research/qa_quest.md).

* tests/wow/qaquest_scenario.lua replays a real session (level-28 Horde hunter in a duo: Freewind Post → Shimmering
  Flats → death → Orneval → Hillsbrad → log full) on the faithful Forever mock and on the modern API, in three
  variants (Questie present, no Questie, in Orgrimmar), with a check per finding (F1 … F14);
* a compass point saved before a /reload is not restored without a click (F3, real SavedVariables);
* the app solver keeps a quest of the log that is on the way (F26).
Fake character / quest ids, real map ids and bounds of the client."""
# (5.1 CLEAN) harness default = the player's profile (« sur ») ; this file tests modules that are off there: « tout »
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from treeaicoach.wow.planner import data, solver

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
ADDON = ROOT / "wow_addon" / "TreeAI"
LUA = shutil.which("lua5.1")
ALLOW = ["LibQuestieDB", "QuestDB", "NpcDB", "ObjectDB", "ItemDB", "TreeAIPlan", "IsQuestFlaggedCompleted"]


def _harness(flavor: str, scenario: Path, *extra: str, env: dict | None = None) -> subprocess.CompletedProcess:
    cmd = [LUA, str(HERE / "harness.lua"), "-q", "--profile", "tout", "--flavor", flavor, "--class", "HUNTER"]
    for a in ALLOW:
        cmd += ["--allow", a]
    cmd += [*extra, "--scenario", str(scenario), str(ADDON)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=ROOT, check=False,
                          env={**os.environ, **(env or {})})


@pytest.mark.skipif(not LUA, reason="lua5.1 not installed")
@pytest.mark.parametrize("variant", ["full", "noquestie", "org"])
@pytest.mark.parametrize("flavor", ["forever", "modern"])
def test_qaquest_replay(flavor: str, variant: str) -> None:
    r = _harness(flavor, HERE / "qaquest_scenario.lua", env={"QAQ_VARIANT": variant})
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-6000:] + r.stderr[-2000:]


RELOAD_SCENARIO = """
return function(M, ctx)
  local T = TreeAI
  local Cm = T.Compass
  ctx.check(Cm and Cm.char and not Cm.char.custom, "F3: the point saved before the /reload is not aimed at login")
  local t = Cm and Cm.Current and Cm.Current()
  ctx.check(not (t and t.kind == "custom"), "F3: the compass does not show it either")
  local offer
  for _, e in ipairs(T:GetSuggestions() or {}) do if e.id == "Compass:resume" then offer = e end end
  ctx.check(offer and offer.text:find("Réparation — Moulin", 1, true) and offer.onClick, "F3: one line offers it back")
  if offer and offer.onClick then offer.onClick() end
  ctx.check(Cm.char.custom and Cm.char.custom.name == "Réparation — Moulin-de-Tarren", "F3: one click aims at it again")
  Cm.ClearWaypoint()
end
"""

SAVED = """
TreeAIDB = { chars = { ["Testeur-Royaume-Test"] = { modules = { Compass = {
  point = "TOP", x = 0, y = -120,
  custom = { mapID = 1424, x = 0.62, y = 0.20, name = "Réparation — Moulin-de-Tarren", source = "detect", at = 12 },
} } } } }
"""


@pytest.mark.skipif(not LUA, reason="lua5.1 not installed")
@pytest.mark.parametrize("flavor", ["forever", "modern"])
def test_compass_point_never_survives_a_reload_without_a_click(flavor: str, tmp_path: Path) -> None:
    sv = tmp_path / "TreeAI.lua"
    sv.write_text(SAVED, encoding="utf-8")
    sc = tmp_path / "reload_scenario.lua"
    sc.write_text(RELOAD_SCENARIO, encoding="utf-8")
    r = _harness(flavor, sc, "--no-storm", "--savedvars", str(sv))
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-4000:] + r.stderr[-2000:]


# ------------------------------------------------------------------ F26: the app keeps a log quest on the way
MILLE = 1441


def _q(qid, title, lv, st="log", o=None, s=None, f=(MILLE, 81.6, 77.9), done=0):
    q = {"id": qid, "t": title, "lv": lv, "rl": lv - 5, "xp": 55 * lv + lv * lv, "st": st, "done": done, "zone": MILLE,
         "flags": 8, "elite": 0, "o": o or [], "f": {"map": f[0], "x": f[1], "y": f[2], "k": "npc", "id": 9}}
    if s:
        q["s"] = {"map": MILLE, "x": s[0], "y": s[1], "k": "npc", "id": 9}
    return q


def _kill(n, x, y, ml=29, k="kill"):
    o = {"k": k, "id": 1, "n": n, "have": 0, "map": MILLE, "x": x, "y": y, "r": 60, "sp": 8, "ml": ml, "mx": ml + 1, "rank": 0}
    if k == "item":
        o.update(p=0.3, npc=[2])
    return o


def flats_export() -> dict:
    """The Shimmering Flats at level 28: the basilisks the route kills anyway, and « Chargement de foudre » (in the
    log) whose oil drops 300 m from them, both turned in at the raceway."""
    quests = [
        _q(5131, "Un cahot sur la route", 30, o=[_kill(10, 74, 82)]),
        _q(5130, "Chargement de foudre", 30, o=[_kill(5, 72, 78, k="item")], f=(MILLE, 80.2, 75.9)),
        _q(5133, "Le contremaître Mécazod", 30, st="avail", s=(80.2, 75.9), f=(MILLE, 80.2, 75.9), o=[_kill(1, 86, 82, ml=31)]),
    ]
    return {"v": 1, "made": 1762500000, "char": "Testeur-Royaume", "src": "test",
            "player": {"class": "HUNTER", "level": 28, "xp": 0, "xpMax": 41000, "rested": 0, "faction": "Horde",
                       "map": MILLE, "x": 78.0, "y": 77.0, "speed": 7.0, "mount": 0, "logMax": 40,
                       "logCount": 2, "taxi": [], "group": 2},
            "maps": [{"id": MILLE, "name": "Mille pointes", "c": 1, "x0": -3966.7, "y0": -433.3, "x1": -6900.0,
                      "y1": -4833.3}],
            "quests": quests, "squad": []}


def _with_parcel(exp: dict, xp: int = 10, at: tuple[float, float] = (80.6, 76.4)) -> dict:
    """+ « Le colis de Pozzik »: in the log, finished, turned in at the raceway — little XP, 15 s on the way."""
    q = _q(5134, "Le colis de Pozzik", 22, f=(MILLE, at[0], at[1]), done=1)
    q["xp"] = xp
    exp["quests"].append(q)
    exp["player"]["logCount"] += 1
    return exp


def _kept(exp: dict) -> set[int]:
    pb = data.load_problem(exp)
    res = solver.solve(pb, "rate", seconds=1.5, seed=1)
    return {pb.quests[q].id for q in solver.Solver(pb).quests_in(res.route)}


def test_solver_keeps_a_log_quest_on_the_way() -> None:
    """The rate route (« Le plus rapide ») used to drop a quest of the log the route passes by (worse XP/h on paper);
    the game's route does it, an expert does it: same plan in the app now. A long farm is still not forced."""
    kept = _kept(_with_parcel(flats_export()))
    assert 5133 in kept and 5134 in kept, f"the parcel turned in at the raceway on the way is done: {sorted(kept)}"
    far = _kept(_with_parcel(flats_export(), at=(20.0, 20.0)))   # turn-in 3 km away: not « on the way »
    assert 5134 not in far, sorted(far)
    pb = data.load_problem(_with_parcel(flats_export()))
    s = solver.Solver(pb, "rate", seed=1)
    i = {q.id: k for k, q in enumerate(pb.quests)}[5134]
    route = s.construct()
    assert i in s.quests_in(route) and s.on_the_way(i, route), "prune never drops it either"
