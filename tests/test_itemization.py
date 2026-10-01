"""Tests for treeaicoach.itemization (offline build advice) and tools/fetch_items.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from treeaicoach import itemization as iz
from treeaicoach.live_client import GameInfo, PlayerInfo

ROOT = Path(__file__).resolve().parents[1]


def P(alias, team="CHAOS", k=0, d=0, items=(), level=9, pos="", dead=False):
    return PlayerInfo(champion_alias=alias, champion_name=alias, team=team, items=list(items), level=level,
                      position=pos, is_dead=dead,
                      scores={"kills": k, "deaths": d, "assists": 0, "creepScore": 0, "wardScore": 0})


def G(me, enemies, gold=0.0, gt=600.0):
    return GameInfo(game_time=gt, me=me, enemies=enemies, current_gold=gold)


def test_item_table_loaded_and_compatible():
    items = iz.load_items()
    assert items[3157].name == "Sablier de Zhonya" and items[3157].rift
    assert 1058 in items[3157].parts
    from treeaicoach.scoreboard import item_info
    assert item_info(3157)[2] == "legendary"


def test_antiheal_vs_healers():
    me = P("Jinx", team="ORDER", items=[1055, 1036], pos="BOTTOM")
    r = iz.recommend(G(me, [P("Soraka"), P("Aatrox"), P("Zed"), P("Jinx"), P("Leona")], gold=1200))
    # V2 audit: no full Rappel mortel as a FIRST item (breaks the build): the cheap component first
    assert r.need == "antiheal" and r.item_id == 3123
    assert "Soraka" in r.text and "Aatrox" in r.text
    assert r.buy_text.startswith("Achat immédiat")
    # once the first legendary is done, the full anti-heal item
    me = P("Jinx", team="ORDER", items=[6672, 1036, 1001], pos="BOTTOM")
    r = iz.recommend(G(me, [P("Soraka"), P("Aatrox"), P("Zed"), P("Jinx"), P("Leona")], gold=1200))
    assert r.need == "antiheal" and r.item_id == 3033
    assert r.buy_now == (3035,)          # Last Whisper: 750 + owned Long Sword + 350 <= 1200


def test_no_second_antiheal():
    me = P("Jinx", team="ORDER", items=[3123], pos="BOTTOM")
    r = iz.recommend(G(me, [P("Soraka"), P("Aatrox"), P("Malphite"), P("Jinx"), P("Leona")]))
    assert r.need != "antiheal" or r.item_id == 3033   # only the upgrade of the owned component


def test_fed_assassins_zhonya_for_mage():
    me = P("Ahri", team="ORDER", pos="MIDDLE")
    r = iz.recommend(G(me, [P("Zed", k=5, d=1), P("Talon", k=4), P("Darius"), P("Jinx"), P("Leona")], gold=3300))
    assert r.item_id == 3157 and "Zed" in r.reason and "kills" in r.reason
    assert r.completes


def test_plan_purchase_exact_components():
    buys, done = iz.plan_purchase(3157, [1058], 1700)
    assert buys == [2420] and not done
    assert iz.remaining_cost(3033, [3123]) == iz.load_items()[3033].gold - iz.load_items()[3123].gold
    assert iz.plan_purchase(3157, [], 0) == ([], False)


def test_core_when_nothing_to_counter():
    me = P("Jinx", team="ORDER", pos="BOTTOM")
    r = iz.recommend(G(me, [P("Garen"), P("Malphite"), P("Annie"), P("Caitlyn"), P("Lux")]))
    assert r is not None and r.item_id in iz.CORE["marksman"] + tuple(
        i for v in iz.NEED_ITEMS.values() for i in v.get("marksman", ()))


def test_spectator_and_garbage_never_raise():
    assert iz.recommend(GameInfo()) is None
    assert iz.recommend(object()) is None
    assert iz.ItemAdvisor().update(0.0, None) == []


def test_advisor_moments_and_antispam():
    adv = iz.ItemAdvisor()
    enemies = [P("Soraka"), P("Aatrox"), P("Zed"), P("Jinx"), P("Leona")]
    me = P("Jinx", team="ORDER", items=[1055], pos="BOTTOM")
    assert adv.update(0.0, G(me, enemies, 500)) == []           # baseline
    me.is_dead = True
    out = adv.update(10.0, G(me, enemies, 500))
    assert len(out) == 1 and out[0].moment == "death" and "Marque du bourreau" in out[0].text
    me.is_dead = False
    adv.update(20.0, G(me, enemies, 500))
    me.is_dead = True                                           # same situation, 40 s later
    assert adv.update(50.0, G(me, enemies, 500)) == []
    me.is_dead = False
    adv.update(60.0, G(me, enemies, 500))
    me.level = 11
    assert adv.update(400.0, G(me, enemies, 500))[0].moment == "level"   # 5 min later: allowed again
    assert adv.current() is not None


def test_fetch_items_build_table():
    spec = importlib.util.spec_from_file_location("fetch_items", ROOT / "tools" / "fetch_items.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fr = {"3123": {"name": "Marque du bourreau", "gold": {"total": 800, "base": 450, "purchasable": True},
                   "tags": ["Damage"], "from": ["1036"], "into": ["3033"], "maps": {"11": True},
                   "stats": {"FlatPhysicalDamageMod": 15}},
          "9999": {"name": "X", "gold": {"total": 10}, "maps": {"11": False}},
          "20001": {"name": "Arena"}}
    en = {"3123": {"name": "Executioner's Calling"}}
    t = mod.build_table(fr, en, "1.0")
    assert set(t["items"]) == {"3123", "9999"}
    e = t["items"]["3123"]
    assert e["n"] == "Marque du bourreau" and e["en"] == "Executioner's Calling" and e["f"] == [1036]
    assert e["k"] == "component" and e["p"] == 1 and t["items"]["9999"]["p"] == 0
