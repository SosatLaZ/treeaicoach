"""Tests of treeaicoach.analysis on the synthetic 28-minute game (tests/fixtures/game_record_sample.json)."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treeaicoach import analysis  # noqa: E402
from treeaicoach.analysis import analyze_game, death_recap, is_my_death  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "game_record_sample.json"


@pytest.fixture(scope="module")
def record() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def result(record: dict) -> dict:
    return analyze_game(record)


def test_fixture_is_small_and_valid(record: dict) -> None:
    assert FIXTURE.stat().st_size < 300_000
    assert record["schema"] == 1
    assert len(record["roster"]) == 10
    assert record["duration"] == pytest.approx(1690.0, abs=1.0)


def test_summary(result: dict) -> None:
    assert result["ok"] and result["errors"] == []
    s = result["summary"]
    assert s["champion"] == "Garen" and s["team"] == "ORDER" and s["position"] == "TOP"
    assert s["result"] == "Win" and s["result_label"] == "Victoire"
    assert s["duration_text"] == "28:10"
    assert (s["kills"], s["deaths"], s["assists"]) == (3, 4, 5)
    assert s["kda_ratio"] == pytest.approx(2.0)
    assert s["cs_per_min"] == pytest.approx(s["cs"] / (1690 / 60), abs=0.01)
    assert 5.5 < s["cs_per_min"] < 6.5
    assert 0.35 < s["vision_per_min"] < 0.5
    assert s["level"] == 18
    assert s["team_kills"] == 10 and s["kill_participation"] == pytest.approx(0.8)


def test_deaths_with_context(result: dict) -> None:
    deaths = result["deaths"]
    assert [d["time"] for d in deaths] == ["4:10", "9:20", "14:30", "21:05"]
    d1, d2, d3, d4 = deaths
    # 4:10 top lane gank: Lee Sin (killer) + Darius, warned 4 s before
    assert d1["zone"] == "top_lane"
    assert d1["killer"]["label"] == "Lee Sin" and d1["killer"]["is_jungler"]
    assert set(d1["participants"]) == {"LeeSin", "Darius"}
    assert set(d1["nearby"]) == {"LeeSin", "Darius"}
    assert d1["jungler_involved"] and d1["warned"] and d1["alert_before_s"] == pytest.approx(4.0)
    assert d1["verdict"] == "alerte ignorée" and d1["verdict_key"] == "ignored" and not d1["app_fault"]
    assert d1["alert_lead_s"] == pytest.approx(6.0)        # the WARNING 6 s before (DANGER 4 s before)
    # 9:20 solo death to the lane opponent, visible, no alert: a lost lane duel, not an app failure
    assert d2["involved"] == ["Darius"] and not d2["jungler_involved"]
    assert not d2["warned"] and d2["nearby"] == ["Darius"]
    assert d2["lane_duel"] and d2["verdict_key"] == "duel" and d2["verdict"] == "1v1 perdu"
    assert not d2["app_fault"] and "Duel perdu" in d2["recap"]
    # 14:30 in the top river, nobody visible
    assert d3["zone"] == "top_river" and d3["nearby"] == [] and not d3["visible_any"]
    assert d3["jungler_involved"] and d3["enemy_count"] == 2 and not d3["warned"]
    assert d3["verdict_key"] == "unseen"
    # 21:05 collapse in the enemy jungle, warned 10 s before
    assert d4["zone"] == "red_jungle_top" and d4["enemy_count"] == 3
    assert d4["warned"] and d4["alert_before_s"] == pytest.approx(10.0) and d4["alert_kind"] == "collapse"
    assert result["deaths_warned"] == 2 and result["deaths_unwarned"] == 2
    assert result["death_verdicts"]["ignored"] == 2 and result["death_verdicts"]["duel"] == 1
    assert result["alert_lead"]["n"] == 2 and result["alert_lead"]["late"] == 0
    for d in deaths:
        assert len(d["recap"].split()) <= 20


def test_ganks(result: dict) -> None:
    ganks = result["ganks"]
    assert [g["time"] for g in ganks] == ["4:06", "6:57", "17:41", "20:55"]
    assert [g["outcome"] for g in ganks] == ["death", "survived", "survived", "death"]
    assert result["ganks_faced"] == 4 and result["ganks_survived"] == 2
    assert ganks[0]["jungler"] and ganks[0]["death_time"] == 250.0 and ganks[0]["warning_lead_s"] == 2.0
    assert ganks[2]["aliases"] == ["KSante"] and not ganks[2]["jungler"]
    # the COLLAPSE alert carries no alias: enemies seen near me are used
    assert set(ganks[3]["aliases"]) == {"Darius", "LeeSin", "KSante"} and ganks[3]["jungler"]


def test_enemy_jungler(result: dict) -> None:
    j = result["jungler"]
    assert j["known"] and j["alias"] == "LeeSin" and j["name"] == "Lee Sin"
    assert j["first_seen"] == pytest.approx(148.0, abs=0.6) and j["first_seen_time"] == "2:28"
    assert j["first_zone"] == "red_jungle_top"
    assert j["appearances"] == 14
    assert sum(j["by_phase"]["0-10"].values()) == 6
    assert sum(j["by_phase"]["10-20"].values()) == 4
    assert sum(j["by_phase"]["20+"].values()) == 4
    assert j["ganks_by_lane"] == {"top": 3, "mid": 1, "bot": 4, "jungle": 0}
    assert j["kills_involved"] == 8 and j["my_deaths"] == 3


def test_zones(result: dict) -> None:
    z = result["zones"]
    groups = {g["key"]: g for g in z["groups"]}
    assert groups["lane_top"]["percent"] > 45            # a top laner
    assert groups["lane_bot"]["seconds"] == 0
    assert groups["my_base"]["seconds"] > 60
    assert sum(g["percent"] for g in z["groups"]) == pytest.approx(100.0, abs=0.5)
    assert 1450 <= z["total_s"] <= 1700


def test_objectives(result: dict) -> None:
    o = result["objectives"]
    assert o["mine"]["dragons"] == 3 and o["theirs"]["dragons"] == 1
    assert o["mine"]["barons"] == 1 and o["mine"]["heralds"] == 1 and o["mine"]["atakhan"] == 1
    assert o["mine"]["grubs"] == 1 and o["theirs"]["grubs"] == 2
    assert o["mine"]["turrets"] == 2 and o["theirs"]["turrets"] == 1 and o["mine"]["inhibitors"] == 1
    assert o["timeline"][0]["label"] == "Dragon infernal" and o["timeline"][0]["mine"]


def test_tips(result: dict) -> None:
    tips = result["tips"]
    assert 3 <= len(tips) <= 8
    rules = [t["rule"] for t in result["tip_items"]]
    assert rules[0] == "ignored_alerts"
    assert "2 morts dans les 12 s après une alerte" in tips[0]
    assert any("Lee Sin a ganké 4 fois en bas" in t and "5:45" in t for t in tips)
    assert any(t.startswith("CS/min 6,0 : objectif 7+") for t in tips)
    assert any("Lee Sin a participé à 3 de tes 4 morts" in t for t in tips)
    for t in tips:
        assert any(ch.isdigit() for ch in t)          # every tip carries a number


def test_death_recap_live(record: dict) -> None:
    ev = next(e for e in record["events"] if e.get("EventName") == "ChampionKill" and e["EventTime"] == 250.0)
    partial = copy.deepcopy(record)          # "record so far": cut at the death time
    cut = 251.0
    partial["my_positions"] = [p for p in partial["my_positions"] if p[0] <= cut]
    partial["sightings"] = {k: [p for p in v if p[0] <= cut] for k, v in partial["sightings"].items()}
    partial["alerts"] = [a for a in partial["alerts"] if a[0] <= cut]
    partial["events"] = [e for e in partial["events"] if e["EventTime"] <= cut]
    text = death_recap(partial, ev)
    assert text == "Mort face à 2 ennemis, dont le jungler. L'alerte avait été donnée 6 secondes avant."
    assert len(text.split()) <= 20
    ev3 = next(e for e in record["events"] if e.get("EventName") == "ChampionKill" and e["EventTime"] == 870.0)
    t3 = death_recap(record, ev3)
    assert t3 is not None and "personne n'était visible sur la minimap" in t3
    assert is_my_death(record, ev3)
    # someone else's death -> None
    other = next(e for e in record["events"] if e.get("EventName") == "ChampionKill" and e["EventTime"] == 390.0)
    assert death_recap(record, other) is None and not is_my_death(record, other)
    # plain game time / nothing known
    assert death_recap({}, 100.0) == "Mort sans alerte : personne n'était visible sur la minimap."


def test_death_matching_by_riot_id_and_champion(record: dict) -> None:
    r = copy.deepcopy(record)
    for e in r["events"]:
        if e.get("VictimName") == "Sylvain":
            e["VictimName"] = "Sylvain#EUW"
    assert len(analyze_game(r)["deaths"]) == 4
    r2 = copy.deepcopy(record)
    for p in r2["roster"]:
        p.pop("is_me", None)
    for e in r2["events"]:
        if e.get("VictimName") == "Sylvain":
            e["VictimName"] = "Garen"            # bot games / old clients: champion name
    assert len(analyze_game(r2)["deaths"]) == 4


def test_fallback_deaths_from_snapshots(record: dict) -> None:
    r = copy.deepcopy(record)
    r["events"] = [e for e in r["events"] if e.get("EventName") != "ChampionKill"]
    a = analyze_game(r)
    assert len(a["deaths"]) == 4
    assert all(d["source"] == "snapshot" for d in a["deaths"])


@pytest.mark.parametrize("bad", [None, {}, [], "x", 3, {"schema": 1}, {"events": "nope", "sightings": [1, 2],
                                  "my_positions": [[None, "a"], [1]], "alerts": [[1], "x", {"t": "no"}],
                                  "snapshots": [None, {"game_time": "z"}], "roster": [None, 1]}])
def test_robust_to_garbage(bad: Any) -> None:
    a = analyze_game(bad)
    assert isinstance(a, dict) and "summary" in a and 3 <= len(a["tips"]) <= 8
    assert a["deaths"] == [] and a["ganks"] == []
    assert death_recap(bad, {"EventTime": "x"}) is None or isinstance(death_recap(bad, {"EventTime": 1}), str)


def test_partial_record(record: dict) -> None:
    """A crash after 8 minutes: whatever exists is analysed."""
    r = copy.deepcopy(record)
    cut = 480.0
    r["my_positions"] = [p for p in r["my_positions"] if p[0] <= cut]
    r["sightings"] = {k: [p for p in v if p[0] <= cut] for k, v in r["sightings"].items()}
    r["alerts"] = [a for a in r["alerts"] if a[0] <= cut]
    r["events"] = [e for e in r["events"] if e["EventTime"] <= cut]
    r["snapshots"] = [s for s in r["snapshots"] if s["game_time"] <= cut]
    r["result"] = None
    r["duration"] = cut
    r["incomplete"] = True
    del r["roster"]
    a = analyze_game(r)
    assert a["summary"]["result"] is None and a["summary"]["result_label"] == "Partie non terminée"
    assert len(a["deaths"]) == 1 and a["ganks_faced"] == 2
    assert a["jungler"]["known"] is False
    assert not a["summary"]["complete"]


def test_section_failure_is_isolated(monkeypatch: pytest.MonkeyPatch, record: dict) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(analysis, "_objectives", boom)
    a = analyze_game(record)
    assert not a["ok"] and any("objectives" in e for e in a["errors"])
    assert len(a["deaths"]) == 4 and a["tips"]


def test_formatting_helpers() -> None:
    assert analysis.fmt_time(250) == "4:10" and analysis.fmt_time(None) == "?"
    assert analysis.fmt_num(5.83) == "5,8"
    assert analysis.fmt_target(7.0) == "7" and analysis.fmt_target(7.5) == "7,5"
    assert analysis.phase_of(599) == "0-10" and analysis.phase_of(600) == "10-20" and analysis.phase_of(5000) == "20+"


def test_analysis_speed(record: dict) -> None:
    import time

    t0 = time.perf_counter()
    analyze_game(record)
    assert time.perf_counter() - t0 < 2.0     # typically ~0.1 s (shared CI cores: generous bound)


# ------------------------------------------------------------------------------ v2 coaching sections
def test_phase_breakdown(result: dict) -> None:
    ph = result["phases"]
    assert [p["phase"] for p in ph] == ["laning", "mid", "late"]
    assert [p["deaths"] for p in ph] == [2, 2, 0]
    assert sum(p["kills"] for p in ph) == 3 and sum(p["assists"] for p in ph) == 5
    assert sum(p["cs"] for p in ph) == 168
    assert ph[0]["range"] == "0:00–14:00" and ph[2]["range"] == "25:00–28:10"
    assert ph[0]["cs_per_min"] == pytest.approx(79 / 14, abs=0.01)
    assert ph[0]["lane_percent"] > ph[1]["lane_percent"] > ph[2]["lane_percent"]
    assert [p["ganks"] for p in ph] == [2, 2, 0]
    assert ph[1]["objectives_ours"] == 4


def test_presence_vs_ideal(result: dict) -> None:
    pres = result["presence"]
    assert pres["role"] == "TOP" and [p["phase"] for p in pres["phases"]] == ["laning", "mid", "late"]
    lan = pres["phases"][0]
    assert lan["own_key"] == "lane_top" and lan["own_ideal"] == pytest.approx(70.0)
    assert lan["own_mine"] > 80 and 0 <= lan["match"] <= 100
    for p in pres["phases"]:
        assert sum(r["ideal"] for r in p["rows"]) == pytest.approx(100.0, abs=0.5)
    ideal_j = analysis._ideal_presence("JUNGLE", "laning")
    assert max(ideal_j, key=ideal_j.get) == "my_jungle"


def test_deaths_jungler_unseen_and_exposure(result: dict) -> None:
    assert [d["jungler_unseen_s"] for d in result["deaths"]] == [1.0, 10.0, 139.0, 1.0]
    assert [d["jungler_unseen"] for d in result["deaths"]] == [False, False, True, False]
    exp = result["exposure"]
    assert exp["score"] == pytest.approx(100 * exp["exposed_s"] / exp["total_s"], abs=0.1)
    assert 0 < exp["score"] < 30 and exp["spots"]


def test_objective_presence(result: dict) -> None:
    op = result["objective_presence"]
    assert op["ours"] == 7 and op["ours_near"] == 2 and op["percent"] == 29
    baron = [i for i in op["items"] if i["kind"] == "baron"][0]
    assert baron["near"] and baron["took_part"] and baron["mine"]
    assert {i["kind"] for i in op["items"]} == {"dragon", "grubs", "herald", "atakhan", "baron"}


def test_trends_and_pathing(result: dict) -> None:
    tr = result["trends"]
    assert len(tr["series"]) == 28 and tr["series"][9]["minute"] == 10
    assert tr["cs_per_min_10"] == pytest.approx(5.1) and tr["cs_per_min_after_10"] == pytest.approx(6.44, abs=0.01)
    assert tr["series"][-1]["kp"] == pytest.approx(0.8)
    pa = result["pathing"]
    assert pa["first_side"] == "top" and pa["first_time"] == "2:28" and pa["main_lane"] == "bot"
    assert pa["summary"] == "Lee Sin vu d'abord en haut à 2:28, ganks surtout en bas (4 kills)."
    assert 5 <= len(pa["path"]) <= 10


def test_new_tips_are_prioritised(result: dict) -> None:
    items = result["tip_items"]
    assert 5 <= len(items) <= 8
    assert [t["priority"] for t in items] == sorted((t["priority"] for t in items), reverse=True)
    assert any(t["rule"] == "objective_presence" and "2 des 7 objectifs" in t["text"] for t in items)


def test_new_tips_jungler_unseen_and_exposure(record: dict) -> None:
    r = copy.deepcopy(record)
    # remove every Lee Sin sighting: he is never seen -> unseen deaths + high exposure
    r["sightings"].pop("LeeSin", None)
    a = analyze_game(r)
    rules = [t["rule"] for t in a["tip_items"]]
    assert "jungler_unseen_deaths" in rules
    text = next(t["text"] for t in a["tip_items"] if t["rule"] == "jungler_unseen_deaths")
    assert text.startswith("4 morts alors que Lee Sin était invisible")
    assert a["exposure"]["score"] > 10


def test_spoken_summary(result: dict) -> None:
    text = analysis.spoken_summary(result)
    assert text == result["spoken_summary"]
    assert text == ("Victoire en 28 minutes. 3 kills, 4 morts, 5 assistances, 6 CS par minute. "
                    "2 morts juste après une alerte. Priorité : recule dès l'annonce vocale.")
    assert len(text.split()) <= 45
    assert analysis.spoken_summary({}) == "Partie terminée. 0 kill, 0 mort, 0 assistance."
    assert isinstance(analysis.spoken_summary(None), str)
    assert isinstance(analysis.spoken_summary(analyze_game({})), str)


def test_death_verdicts_separate_app_failures_from_player_mistakes(record: dict) -> None:
    """Real game report (Garen 1/15/5): deaths 0-1 s after the alert were blamed on the player,
    lane 1v1 deaths were "mort sans alerte". Now: late alert = the app's fault, duel = neither."""
    rec = copy.deepcopy(record)
    # the 21:05 collapse alert moved to 1 s before the death: too late to react
    death_t = 1265.0
    rec["alerts"] = [a for a in rec["alerts"] if not (death_t - 12 <= a[0] <= death_t)]
    rec["alerts"].append([death_t - 1.0, "collapse", 2, "Danger, 3 ennemis arrivent, recule !", None])
    rec["alerts"].sort(key=lambda a: a[0])
    res = analyze_game(rec)
    d4 = res["deaths"][3]
    assert d4["warned"] and d4["verdict_key"] == "late" and d4["app_fault"]
    assert d4["verdict"] == "alerte trop tardive" and "trop tard" in d4["recap"]
    assert res["deaths_warned"] == 1                    # only the in-time one counts against the player
    assert res["alert_lead"]["late"] == 1
    tip = next(t for t in res["tip_items"] if t["rule"] == "ignored_alerts")
    assert tip["text"].startswith("1 mort dans les 12 s")
    # a personal danger warning ("Darius te domine") 5 s before the duel death counts as a warning
    rec2 = copy.deepcopy(record)
    t2 = res["deaths"][1]["game_time"]
    rec2["alerts"].append([t2 - 5.0, "personal_danger", 1, "Darius te domine : ne trade pas.", "Darius"])
    rec2["alerts"].sort(key=lambda a: a[0])
    d2 = analyze_game(rec2)["deaths"][1]
    assert d2["warned"] and d2["verdict_key"] == "ignored" and d2["alert_lead_s"] == pytest.approx(5.0)
