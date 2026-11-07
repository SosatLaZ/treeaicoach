"""Auction house math (treeaicoach/wow/auction.py) — exact numbers — and the Lua module scenario."""
import shutil
import subprocess
from pathlib import Path

import pytest

from treeaicoach.wow import auction as A

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
MODULE = ADDON / "Modules" / "Auction.lua"
LUA = shutil.which("lua5.1")
needs_lua = pytest.mark.skipif(not LUA, reason="lua5.1 not installed")


def test_cut():
    assert A.ah_cut(10000) == 500
    assert A.ah_cut(10000, neutral=True) == 1500
    assert A.ah_cut(19) == 0
    assert A.ah_cut(199, neutral=True) == 29
    assert A.net_sale(10000) == 9500


def test_deposit():
    assert A.deposit(100, 5, 24) == 300            # 60 % of vendor value
    assert A.deposit(100, 5, 24, neutral=True) == 1500   # 300 %
    assert A.deposit(100, 5, 2) == 25              # 5 %
    assert A.deposit(100, 5, 8) == 100             # 20 %
    assert A.deposit(0, 20, 24) == 0


def test_profit_roi_break_even():
    assert A.expected_profit(10000, 20000, 300, 0.6) == 8880
    assert A.roi(8880, 10000) == pytest.approx(0.888)
    assert A.roi(5, 0) is None
    assert A.break_even(10000, 300, 0.6) == 10652
    assert A.expected_profit(10000, 10652, 300, 0.6) == 0
    assert A.expected_profit(10000, 10651, 300, 0.6) < 0
    assert A.break_even(10000) == 10526            # 10526 − floor(526.3) = 10000 → profit 0
    assert A.expected_profit(10000, A.break_even(10000)) >= 0


def test_break_even_minimal():
    for buy, dep, p, n in ((10000, 0, 1, False), (777, 33, 0.3, True), (1, 0, 1, False)):
        s = A.break_even(buy, dep, p, n)
        assert A.expected_profit(buy, s, dep, p, n) >= 0
        assert s == 0 or A.expected_profit(buy, s - 1, dep, p, n) < 0


def test_stats():
    assert A.median([5, 1, 3]) == 3
    assert A.median([1, 2, 3, 10]) == 2
    assert A.median([]) is None
    assert A.low_median([100, 90, 500, 95, 99, 101, 300]) == 99
    days = [[d, m - 10, m, 12, 1, 0, 0] for d, m in zip(range(20000, 20005), (100, 110, 90, 105, 400))]
    assert A.market_value(days) == 105
    assert A.volatility(days) == 0.0706
    assert A.volatility([[1, 80, 100, 5, 1, 0, 0]]) == 0.2
    assert A.daily_volume([[1, 90, 100, 5, 2, 6, 12], [2, 90, 100, 5, 2, 4, 12]]) == 6.0
    assert A.daily_volume(days) is None
    flat = [[d, 90, 100, 12, 1, 0, 0] for d in range(5)]
    assert A.confidence(flat) == 1.0
    assert A.confidence(flat, age_days=5) == 0.5
    assert A.confidence([]) == 0.0
    assert A.p_sell(6, 4, 2) == 0.95
    assert A.p_sell(2, 4, 2) == 0.3333
    assert A.p_sell(None) == 0.5
    assert A.sell_through_days(2, 4, 2) == 3.0


def test_flip_and_vendor_flip_and_ranking():
    f = A.evaluate_flip(6000, 1, 10000, 0.1, 6, 0.8, vendor_unit=500, qty_ahead=4)
    assert (f["sellUnit"], f["deposit"], f["p"], f["profit"], f["adj"]) == (9500, 300, 0.95, 3010, 2408)
    assert f["score"] == 4013.33 and f["discount"] == 0.4 and f["days"] == 0.83
    v = A.evaluate_vendor_flip(50, 2, 40)
    assert v["profit"] == 30 and v["kind"] == "vendor"
    assert A.evaluate_vendor_flip(80, 2, 40) is None
    small = A.evaluate_flip(9000, 1, 10000, 0.1, 6, 0.8)   # 10 % discount: filtered
    ranked = A.rank_deals([small, f, v], min_margin=0.2, min_profit=1000)
    assert ranked == [f, v] or ranked == [v, f]
    assert ranked[0]["score"] >= ranked[1]["score"]
    assert A.rank_deals([f], budget=5000) == []


def test_thin_market():
    assert A.thin_market([100, 200, 300])
    assert A.thin_market([100, 150, 160, 170, 180])
    assert not A.thin_market([100, 110, 120, 130, 140])
    assert not A.thin_market([])


def test_suggest_post():
    s = A.suggest_post(5, 20, 1000, 0.1, 10, [(950, 5), (980, 5), (1200, 10)])
    assert (s["unit"], s["deposit"], s["net"], s["better"], s["split"]) == (949, 60, 4505, "ah", None)
    low = A.suggest_post(5, 20, 1000, 0.1, 10, [(800, 5)])
    assert low["unit"] == 900 and "plancher" in low["reason"]
    split = A.suggest_post(20, 20, 1000, 0.1, 10, [(990, 5), (995, 5), (999, 20)])
    assert split["split"] == 5
    junk = A.suggest_post(1, 500, 300, 0.1, 1, [(290, 1)])
    assert junk["better"] == "vendor"
    assert A.suggest_post(1, 10, None, 0.1, None)["unit"] is None


def test_realized_profit():
    r = A.realized_profit([
        {"type": "buy", "item": "Tissu", "qty": 10, "money": 1000},
        {"type": "sell", "item": "Tissu", "qty": 4, "money": 800, "cut": 40, "day": "d1"},
        {"type": "sell", "item": "Tissu", "qty": 10, "money": 2000, "cut": 100, "day": "d2"},
    ])
    assert r["total"] == 1660 and r["days"] == {"d1": 360, "d2": 1300}
    assert r["items"]["Tissu"]["noCost"] == 4 and r["items"]["Tissu"]["held"] == 0


def test_reader():
    db = {"chars": {"Fake-Realm": {"summary": {"auction": {
        "updated": 5, "scanAge": 600, "neutral": False, "budget": 100000, "spentToday": 2500,
        "deals": {1: {"name": "Lin", "count": 20, "kind": "flip", "buy": 2000, "sell": 4000,
                      "profit": 1500, "roi": 0.75, "conf": 0.6},
                  2: {"name": "Griffe", "kind": "vendor", "buy": 3, "profit": 4}},
        "sell": [{"name": "Laine", "count": 5, "unit": 949, "net": 4505, "vendor": 100, "better": "ah"},
                 {"name": "Os", "count": 1, "vendor": 500, "better": "vendor"}],
        "realized": {"today": 1200, "week": 35000, "total": -50},
        "thin": ["Lame runique"]}}}}}
    ctx = A.auction_context(db)
    assert ctx["affaires"][0] == "Lin x20 : acheter 20 pa, revendre 40 pa → +15 pa, ROI 75 %, confiance 60 %"
    assert "garanti" in ctx["affaires"][1]
    assert ctx["a_vendre"][1] == "Os : mieux au marchand (5 pa)"
    assert ctx["profit_realise"] == "aujourd'hui 12 pa, 7 j 3 po 50 pa, total -50 pc"
    assert ctx["dernier_scan"] == "il y a 10 min" and ctx["marches_minces"] == ["Lame runique"]
    assert A.answer_lines(db)[0].startswith("À acheter — Lin")
    assert A.auction_context({}) == {} and A.auction_context(None) == {}
    assert "Scanner" in A.answer_lines({})[0]
    assert A.money_text(12345) == "1 po 23 pa 45 pc" and A.money_text(0) == "0 pc"


@needs_lua
def test_lua_syntax():
    assert subprocess.run([LUA.replace("lua5.1", "luac5.1"), "-p", str(MODULE)]).returncode == 0


def test_toc_lists_module():
    toc = (ADDON / "TreeAI.toc").read_text(encoding="utf-8")
    assert "Modules\\Auction.lua" in toc


@needs_lua
@pytest.mark.parametrize("flavor", ["modern", "legacy"])
def test_lua_scenario(flavor):
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--flavor", flavor, "--scenario",
                        "tests/wow/auction_scenario.lua", str(ADDON)],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout


def test_ai_context_includes_auction():
    from treeaicoach.wow import ai_context
    db = {"chars": {"Fake-Realm": {"summary": {"class": "HUNTER", "level": 26, "updated": 5,
                                               "auction": {"deals": [{"name": "Lin", "kind": "vendor",
                                                                      "buy": 3, "profit": 4}]}}}}}
    ctx = ai_context.character_context(db)
    assert ctx["hotel_des_ventes"]["affaires"][0].startswith("Lin : acheter 3 pc")
