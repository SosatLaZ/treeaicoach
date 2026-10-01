"""Game data integrity: every check of tools/validate_data.py offline on the bundled data, plus the
behaviours the data overhaul (patch 26.19) introduced."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tools import fetch_builds, validate_data
from treeaicoach import game_data, game_plan, itemization as iz, meta, objectives, wards
from treeaicoach.live_client import GameInfo, PlayerInfo
from treeaicoach.paths import asset_path

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def findings():
    return validate_data.run(ROOT, offline=True)


def test_validator_offline_no_errors(findings):
    errors = [f for f in findings if f.level == "ERROR"]
    assert not errors, validate_data.table(errors)
    areas = {f.area for f in findings}
    for area in ("items", "champions", "profiles", "builds", "advice", "matchups", "objectives", "atakhan", "wards"):
        assert area in areas, area


def test_validator_detects_broken_matchups(tmp_path):
    assets = tmp_path / "treeaicoach" / "assets"
    assets.mkdir(parents=True)
    data = json.loads(asset_path("matchups.json").read_text(encoding="utf-8"))
    data["vs_champion"]["Nobody"] = ["Fonce sur lui maintenant sans réfléchir : tu gagnes toujours ce combat-là"]
    (assets / "matchups.json").write_text(json.dumps(data), encoding="utf-8")
    (assets / "champion_meta.json").write_text(asset_path("champion_meta.json").read_text(encoding="utf-8"),
                                               encoding="utf-8")
    a = validate_data.Audit(tmp_path, True, None, lambda _m: None)
    a.aliases = set(meta.aliases())
    validate_data.check_matchups(a)
    detail = " ".join(f.detail for f in a.findings if f.level == "ERROR")
    assert "Nobody: unknown champion" in detail and "not an instruction verb" in detail


# ----------------------------------------------------------------------------- profiles
def test_every_champion_has_a_sourced_profile():
    idx = json.loads(asset_path("icons", "champions", "index.json").read_text(encoding="utf-8"))
    for c in idx["champions"]:
        p = meta.profile(c["alias"])
        assert p.source == "data" and p.positions and p.lane_class in meta.LANE_CLASSES, c["alias"]
        assert p.name == c["name_fr"]
    assert meta.header()["schema"] == 2 and meta.header()["patch"] == "26.19"


def test_new_champions_profiles():
    locke, zaahen = meta.profile("Locke"), meta.profile("Zaahen")
    assert locke.damage == "M" and not locke.ranged and locke.main_position == "MIDDLE" and locke.mobility == 3
    assert zaahen.damage == "P" and zaahen.main_position == "TOP" and zaahen.lane_class == "skirmisher"
    assert iz.champion_class("Locke", "MIDDLE") == "assassin_ap"          # was assassin_ad (wrong damage type)
    assert meta.profile("Leona").female and not meta.profile("Garen").female


def test_champion_released_after_the_build_gets_a_rule_profile(monkeypatch):
    fake = {"version": "99.1.1", "champions": [{"alias": "Newchamp", "key": 999, "name_fr": "Nouvelle", "tags": ["Mage"],
                                                 "st": {"ar": 550, "hp": 600, "hpl": 100, "ms": 335,
                                                        "info": [2, 3, 9, 5]}}]}
    monkeypatch.setattr(game_data, "champions_data", lambda: fake)
    meta._RULE_CACHE.clear()
    p = meta.profile("Newchamp")
    assert p.source == "rule" and p.ranged and p.damage == "M" and p.lane_class == "mage" and p.name == "Nouvelle"
    assert meta.profile("Inconnu").known is False
    meta._RULE_CACHE.clear()


# ----------------------------------------------------------------------------- items / builds
def test_item_table_shop_filter_flags_and_effects():
    data = game_data.items_data()
    items = data["items"]
    assert items["3172"]["k"] == "boots"                                   # tier-3 boots (no "Boots" tag)
    assert {1105, 2051, 3184} <= set(data["not_sr"]) and items["2051"]["p"] == 0
    assert items["1101"]["p"] == 1
    assert "antiheal" in items["3033"]["x"] and "stasis" in items["3157"]["x"]
    assert {"antiheal", "armorpen"} <= game_data.item_effects(3033)
    assert {"stasis", "armor"} <= iz.item_effects(3157)
    assert {"mr", "tenacity"} <= iz.item_effects(3111) and "shield" in iz.item_effects(3053)
    enemies = [PlayerInfo(champion_alias="Darius", champion_name="Darius", items=[6609, 1036]),
               PlayerInfo(champion_alias="Ahri", champion_name="Ahri", items=[3157])]
    eff = iz.enemy_effects(enemies)
    assert eff["antiheal"] == ["Darius"] and eff["stasis"] == ["Ahri"]


def test_runtime_refresh_keeps_the_shop_filter():
    fr = {"2051": {"name": "Corne du gardien", "gold": {"total": 950, "purchasable": True}, "maps": {"11": True}},
          "3157": {"name": "Sablier de Zhonya", "gold": {"total": 3250, "purchasable": True}, "maps": {"11": True}}}
    t = game_data.build_items_table(fr, {}, "99.1.1", exclude=[2051])
    assert t["items"]["2051"]["p"] == 0 and t["items"]["3157"]["p"] == 1 and t["not_sr"] == [2051]


def test_bundled_builds_match_the_generator():
    bundled = json.loads(asset_path("item_builds.json").read_text(encoding="utf-8"))
    built = fetch_builds.build(json.loads(asset_path("items.json").read_text(encoding="utf-8")))
    for k in ("core", "counters", "boots", "start", "support_upgrade", "champions", "same_need"):
        assert built[k] == bundled[k], k
    assert iz.BUILDS_INFO.get("patch") == "26.19" and iz.CORE == {c: tuple(v) for c, v in bundled["core"].items()}


def test_support_quest_upgrade_and_champion_builds():
    me = PlayerInfo(champion_alias="Leona", champion_name="Leona", team="ORDER", items=[3867, 1001],
                    position="UTILITY")
    r = iz.recommend(GameInfo(game_time=900.0, me=me, enemies=[], current_gold=100.0), "UTILITY")
    assert r.need == "support_upgrade" and r.item_id == 3869 and r.completes
    assert iz.champion_class("Kayle", "TOP") == "mage" and iz.core_items("Kayle", "mage")[0] == 3115


# ----------------------------------------------------------------------------- matchups
def test_matchup_tips_specific_first_and_gendered():
    lines = game_plan.lane_lines("Garen", "Darius", "Darius", 3)
    assert len(lines) == 3 and "fort tôt" in lines[0]
    assert game_plan.lane_lines("Garen", "Vladimir", "Vladimir")[0].startswith("Attaque aux niveaux 1 à 3")
    assert any("Leona est forte tôt" in x for x in game_plan.lane_lines("Thresh", "Leona", "Leona", 4))
    tiers = [t for t, _l in game_plan.matchup_lines("Garen", "Zac", "Zac")]
    assert tiers[0] == "pair_class" and "vs_class" in tiers
    for line in game_plan.lane_lines("Garen", "Riven", "Riven", 4):
        assert " il " not in f" {line} "                                  # Riven: elle


# ----------------------------------------------------------------------------- wards / objectives
def test_ward_spots_absolute_for_both_teams():
    dragon_pit, baron_pit = (0.6635, 0.7053), (0.3367, 0.3010)
    for team in ("ORDER", "CHAOS"):
        drag = wards.recommend(team, "UTILITY", objective=("dragon", 60.0))
        assert drag and all(math.dist(p.uv, dragon_pit) < 0.15 for p in drag if p.spot.objective)
        top = [p.spot.id for p in wards.recommend(team, "TOP")]
        assert "river_top_lane" in top and math.dist(wards.SPOT_BY_ID["baron_front"].uv_for(team), baron_pit) < 0.1
    tri = wards.SPOT_BY_ID["tri_own"]
    assert tri.area_for("ORDER") == "own" and tri.area_for("CHAOS") == "enemy"
    assert tri.label_for("CHAOS").startswith("tri-buisson ennemi")


def test_faelights_exact_and_time_relevance():
    data = json.loads(asset_path("ward_spots.json").read_text(encoding="utf-8"))
    game = data["map"]["faelights"]
    assert len(game) == 12 and sum(1 for f in game.values() if f["after_rift"]) == 4
    for s in wards.SPOTS:
        if s.faelight:
            ref = next(f for f in game.values() if math.dist(tuple(f["uv"]), s.uv) < 0.003)
            assert ref["after_rift"] == s.after_rift
    gate = wards.SPOT_BY_ID["fae_gate_top"]
    assert not gate.relevant("laning", 300.0) and gate.relevant("mid", 1200.0)
    early = [p.spot.id for p in wards.recommend("ORDER", "JUNGLE", phase="mid", gt=300.0, n=5)]
    assert "fae_gate_top" not in early


def test_objective_schedule_is_the_2026_one():
    sched = objectives.load_schedule()
    for key, ref in validate_data.OFFICIAL_2026.items():
        for f, v in ref.items():
            assert sched[key].get(f) == (None if v is None else float(v)), (key, f)
    assert "atakhan" not in sched


def test_data_versions_for_about_and_diagnostics():
    v = game_data.data_versions()
    for k in ("items", "champions", "profiles", "builds", "matchups", "wards", "objectives"):
        assert v.get(k), k
    assert v["profiles"]["patch"] == "26.19"
    assert game_data.data_versions_text().startswith("Données du jeu : objets 16.19")
