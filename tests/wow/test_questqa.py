"""QUESTQA: every facet of quest tracking, replayed (tests/wow/questqa_scenario.lua, 3 API flavours) + the app
solver held to the SAME rules as the addon (treeaicoach/wow/planner/rules.py ↔ Modules/ZonePlan.lua).

Fake character / quest ids (titles as the French client shows them), real map ids and bounds of the client."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from treeaicoach.wow.planner import data, routes, rules
from treeaicoach.wow.planner import models as M

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
ADDON = ROOT / "wow_addon" / "TreeAI"
LUA = shutil.which("lua5.1")
ALLOW = ["LibQuestieDB", "QuestDB", "NpcDB", "ObjectDB", "ItemDB", "TreeAIPlan", "IsQuestFlaggedCompleted"]

ORNEVAL, TARIDES, MILLE, FOSSOYEUSE = 1440, 1413, 1441, 1458


@pytest.mark.skipif(not LUA, reason="lua5.1 not installed")
@pytest.mark.parametrize("flavor", ["modern", "legacy", "forever"])
def test_questqa_replay(flavor):
    cmd = [LUA, str(HERE / "harness.lua"), "-q", "--flavor", flavor, "--class", "HUNTER"]
    for a in ALLOW:
        cmd += ["--allow", a]
    cmd += ["--scenario", str(HERE / "questqa_scenario.lua"), str(ADDON)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=ROOT)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-6000:] + r.stderr[-2000:]


# ------------------------------------------------------------------ the user's log, as an export (SCHEMA.md §1)
def _obj(k, n, x, y, m=ORNEVAL, have=0, ml=24, rank=0, t=""):
    o = {"k": k, "id": 1, "n": n, "have": have, "map": m, "x": x, "y": y, "r": 60, "sp": 8, "ml": ml, "mx": ml + 1,
         "rank": rank}
    if t:
        o["t"] = t
    if k == "item":
        o["p"] = 0.5
        o["npc"] = [2]
    return o


def _q(qid, title, lv, st="log", zone=ORNEVAL, s=None, f=None, o=None, **kw):
    q = {"id": qid, "t": title, "lv": lv, "rl": lv - 4, "xp": 55 * lv + lv * lv, "st": st, "done": 0, "zone": zone,
         "flags": 8, "elite": 0, "o": o or []}
    if s:
        q["s"] = {"map": s[0], "x": s[1], "y": s[2], "k": "npc", "id": 9}
    q["f"] = {"map": (f or s or (zone, 50, 50))[0], "x": (f or s or (zone, 50, 50))[1], "y": (f or s or (zone, 50, 50))[2],
              "k": "npc", "id": 9}
    q.update(kw)
    return q


def user_export(work: bool = True, n_avail: int = 30) -> dict:
    """Level-28 Horde hunter in Orneval with the log of the screenshots: dungeon / elite / PvP quests, a failed
    escort, completed quests to turn in at Thunder Bluff and in Fossoyeuse (other continent)."""
    quests = [
        _q(5110, "La flamme sacrée", 25, zone=1456, f=(1456, 30, 30), o=[_obj("item", 1, 49, 69, t="Fiole d'eau forte remplie : 0/1")]),
        _q(5111, "Cœurs tonnants", 33, zone=FOSSOYEUSE, f=(FOSSOYEUSE, 50, 60), done=1, elite=1, tag=1),
        _q(5112, "Source de pouvoir", 33, dg=1, tag=81, o=[_obj("item", 6, 15, 15, ml=26)]),
        _q(5116, "Le Chevalier de la tombe", 33, zone=1420, f=(1420, 80, 30),
           o=[_obj("kill", 1, 85, 33, m=1420, ml=33, rank=3, t="Atrexis le Chevalier de la tombe : 0/1")]),
        _q(5118, "La conquête du bassin d'Arathi", 29, pvp=1, tag=41, o=[_obj("item", 3, 60, 60)]),
        _q(5119, "Accusé à tort, corrigé à raison", 23, zone=TARIDES, f=(TARIDES, 45, 58), tag=1, elite=1,
           o=[_obj("kill", 2, 49, 84, m=TARIDES, ml=25, rank=1)]),
        _q(5204, "Liberté pour Ruul", 24, failed=1, o=[_obj("explore", 1, 76, 66)]),
        _q(5120, "Des armes de choix", 24, zone=TARIDES, f=(TARIDES, 45, 58), o=[_obj("item", 1, 43, 82, m=TARIDES)]),
        _q(5103, "Broie-l'acier", 30, zone=1456, f=(1456, 30, 30), done=1),
    ]
    for i in range(n_avail):                          # Bois-Brisé and around: lots to take
        x, y = 20 + (i * 37) % 60, 20 + (i * 53) % 60
        quests.append(_q(6000 + i, f"Quête d'Orneval {i}", 24 + i % 4, st="avail", s=(ORNEVAL, 73, 61), f=(ORNEVAL, 73, 61),
                         o=[_obj("kill", 6, x, y, ml=24 + i % 4, t=f"Furbolg {i} tué : 0/6")]))
    for i in range(6):                                # tempting quests on the other continent
        quests.append(_q(7000 + i, f"Quête de Fossoyeuse {i}", 28, st="avail", zone=FOSSOYEUSE, s=(FOSSOYEUSE, 50, 60),
                         f=(FOSSOYEUSE, 50, 60), o=[_obj("kill", 5, 40 + i, 40, m=FOSSOYEUSE, ml=28)]))
    quests.append(_q(6900, "Les profondeurs de Brassenoire", 27, st="avail", s=(ORNEVAL, 18, 21), tag=81, dg=1,
                     o=[_obj("kill", 8, 14, 14, ml=26, rank=1)]))
    exp = {
        "v": 1, "made": 1762500000, "char": "Testeur-Royaume", "src": "test",
        "player": {"class": "HUNTER", "level": 28, "xp": 0, "xpMax": 41000, "rested": 0, "faction": "Horde",
                   "map": ORNEVAL, "x": 73.0, "y": 61.0, "speed": 7.0, "mount": 0, "logMax": 20,
                   "logCount": sum(1 for q in quests if q["st"] == "log"), "taxi": [], "group": 1},
        "maps": [], "quests": quests, "squad": [],
    }
    if work:
        exp["work"] = {"mode": "auto", "maps": [ORNEVAL, TARIDES], "ignore": [], "extra": [],
                       "opts": {"elites": False, "groupQ": True, "dungeons": False, "gap": 3}}
    return exp


EXCLUDED = {5112, 5116, 5118, 5119, 5204, 6900}       # dungeon, boss, PvP, elite solo, failed, dungeon (avail)
LABELS = tuple(routes.VERDICT_FR.values())


def _check_plan(plan: dict, excluded: set[int] = EXCLUDED) -> None:
    assert plan and plan["routes"]
    assert plan["log"] and plan["level"] == 28, "the plan carries its fingerprint (addon: Planner.PlanFresh)"
    for r in plan["routes"]:
        qsteps = [s for s in r["steps"] if s.get("q")]
        bad = {s["q"] for s in qsteps if s["q"] in excluded and r["id"] != "squad"}
        assert not bad, (r["id"], bad)
        assert not [s for s in r["steps"] if s.get("map") in (FOSSOYEUSE, 1420)], f"{r['id']}: another continent"
        assert len(qsteps) <= rules.MAX_NODES + 8, (r["id"], len(qsteps))
        assert r["minutes"] <= rules.HORIZON_HI, (r["id"], r["minutes"])
        for why in r["reasons"]:
            assert not why.startswith(LABELS), f"reason repeats its label: {why}"
            assert "→" not in why, why
            for n in re.findall(r"(\d+) %", why):
                assert int(n) <= 100, why
        assert r["xph"] == 0 or r["minutes"] >= 10, "no XP/h measured on < 10 min"
        for s in r["steps"]:
            lab = str(s.get("label") or "")
            assert not re.search(r"(.{6,}) : \1", lab), lab


def test_solver_same_rules_as_the_addon():
    plan = routes.compute_plan(user_export(), seconds=4, now=1762500100)
    _check_plan(plan)
    xph = next(r for r in plan["routes"] if r["id"] == "xph")
    assert any(why.startswith("hors trajet : ") and "donjon" in why for why in xph["reasons"]), xph["reasons"]
    # « La flamme sacrée »: objective in Orneval, turn-in in Thunder Bluff → the objective is a candidate (credited
    # with half the quest XP), the turn-in is not planned (a detour the addon proposes once the vial is filled)
    pb = data.load_problem(user_export(), work=True)
    flamme = next(q for q in pb.quests if q.id == 5110)
    assert [pb.nodes[n].kind for n in flamme.nodes] == ["obj"] and pb.nodes[flamme.nodes[0]].bonus > 0
    for r in plan["routes"]:
        assert not [s for s in r["steps"] if s.get("q") == 5110 and s["k"] == "turn"], r["id"]


def test_without_work_zone_never_another_continent():
    plan = routes.compute_plan(user_export(work=False), seconds=3, now=1762500100)
    _check_plan(plan)


def test_long_routes_are_clipped_to_the_horizon():
    exp = user_export(n_avail=80)
    exp["player"]["logMax"] = 40                      # room for many pick-ups: the route would be hours long
    plan = routes.compute_plan(exp, seconds=4, now=1762500100)
    _check_plan(plan)
    clear = next(r for r in plan["routes"] if r["id"] == "clear")
    assert clear["later"] > 0 and any("pour la suite" in why for why in clear["reasons"]), clear["reasons"]


def test_group_rules_dungeons_only_when_chosen_and_party_fits():
    def kept(exp, group_mode):
        pb = data.load_problem(exp, group_mode=group_mode, work=True)
        return {q.id for q in pb.quests}, pb.excluded

    exp = user_export()
    exp["squad"] = [{"key": f"Ami{i}-Royaume", "class": "WARRIOR", "level": 28, "map": ORNEVAL, "x": 72, "y": 60,
                     "q": [], "done": []} for i in range(3)]
    ids, why = kept(exp, True)
    assert 6900 not in ids and why[6900] == "donjon", "dungeons need the player's opt-in"
    assert 5119 in ids, "4 players: the elite quest is planned"
    exp["work"]["opts"]["dungeons"] = True
    ids, why = kept(exp, True)
    assert 6900 in ids and 5118 not in ids and 5204 not in ids, "opted in + 4 players: dungeon yes, PvP / failed never"
    assert why.get(5112) == "niveau", "a level-33 dungeon quest at 28 stays out (5 levels above)"
    ids, why = kept(exp, False)
    assert 6900 not in ids and 5119 not in ids, "solo routes: never a dungeon / elite quest"


def test_accept_rules_mirror_zoneplan():
    o = rules.options(None)
    assert o == {"elites": False, "groupQ": True, "dungeons": False, "gap": 3}
    ready_dungeon = {"id": 1, "st": "log", "done": 1, "dg": 1, "lv": 33}
    assert rules.accept(ready_dungeon, 28) == (True, ""), "a finished dungeon quest is just a turn-in"
    assert rules.accept({"id": 2, "st": "log", "lv": 28, "o": [{"rank": 3}]}, 28) == (False, "donjon"), "a boss"
    assert rules.accept({"id": 3, "st": "avail", "lv": 32}, 28) == (False, "niveau")
    assert rules.accept({"id": 4, "st": "avail", "lv": 31}, 28) == (True, "")
    assert rules.accept({"id": 5, "st": "log", "lv": 28, "failed": 1}, 28) == (False, "échouée")
    assert rules.accept({"id": 6, "st": "log", "lv": 28, "tag": 41}, 28, 5, {"dungeons": True, "elites": True}) == (False, "JcJ")
    assert rules.accept({"id": 7, "st": "log", "lv": 28, "tag": 62}, 28, 5, {"dungeons": True}) == (False, "raid")
    assert rules.accept({"id": 8, "st": "log", "lv": 28, "elite": 1}, 28, 3) == (True, "")
    assert rules.accept({"id": 9, "st": "log", "lv": 28}, 28, 1, None, {9}) == (False, "ignorée")
    # the Lua side has the same table of reasons
    lua = (ADDON / "Modules" / "ZonePlan.lua").read_text(encoding="utf-8")
    for why in ("ignorée", "échouée", "JcJ", "raid", "donjon", "élite", "groupe", "niveau"):
        assert f'"{why}"' in lua, why


def test_zone_switch_wording_and_numbers():
    sw, why = M.should_switch_zone(1000.0, 9000.0, 5, 90)
    assert sw and why == "ailleurs : plus du double d'XP/h (5 min de trajet)"
    sw, why = M.should_switch_zone(20000.0, 30000.0, 10, 90)
    assert sw and re.fullmatch(r"ailleurs : \+\d+ % d'XP/h \(10 min de trajet\)", why) and int(why.split("+")[1].split(" ")[0]) <= 100
    assert M.should_switch_zone(0.0, 30000.0, 5, 90) == (False, ""), "no measured rate here → no comparison"
    assert M.should_switch_zone(30000.0, 0.0, 0, 90, non_grey_left=1)[1] == "plus que 1 quête utile ici"
    assert M.should_switch_zone(30000.0, 0.0, 0, 90, non_grey_left=0)[1] == "plus aucune quête utile ici"


def test_fingerprint_and_labels():
    fp = rules.plan_fingerprint(user_export())
    assert fp["level"] == 28 and 5110 in fp["log"] and 6000 not in fp["log"]
    assert routes._obj_name("Atrexis le Chevalier de la tombe : 0/1") == "Atrexis le Chevalier de la tombe"
    assert routes._obj_name("Excavateur de Bael'dun tué : 0/15") == "Excavateur de Bael'dun"
    assert rules.horizon_minutes(None) == 90 and rules.horizon_minutes(20) == 60 and rules.horizon_minutes(400) == 120


def test_addon_defaults_estimates_off():
    pro = (ADDON / "Modules" / "Pro.lua").read_text(encoding="utf-8")
    for k in ("xpRank", "eta", "dungeons", "checklist", "rested", "hearth"):
        assert re.search(rf"\b{k} = false\b", pro), f"Pro.{k} must be off by default (unverified estimate / duplicate)"
