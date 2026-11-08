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
    assert ranked == [f]                                    # (4.1) +30 pc at the vendor < min_net(…): not a deal
    assert {d["kind"] for d in A.rank_deals([f, A.evaluate_vendor_flip(300, 1, 500)], min_margin=0.2, min_profit=100)} == {"flip", "vendor"}
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
    assert "« Scanner »" in A.answer_lines({})[0]                     # the 4.1 AH panel button
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


@needs_lua
def test_lua_forever_scenario():
    """WoW Forever path: C_AuctionHouse (async/throttled ReplicateItems, search throttle, Auctionator API)."""
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--flavor", "modern", "--scenario",
                        "tests/wow/auction_forever_scenario.lua", str(ADDON)],
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


# ------------------------------------------------------------------ liquidity model (« does it really sell? »)
def _hist(meds, gone=0, hrs=0, n=2, sel=6, qty=60):
    """Fake daily history: [day, min, med, qty, scans, vanished units, hours between scans, sellers]."""
    return [[100 + i, m - 2, m, qty, n, gone, hrs, sel] for i, m in enumerate(meds)]


LIQUID = _hist([100, 102, 98, 101, 100], gone=40, hrs=12)


def test_liquid_trade_good_is_kept():
    q = A.liquidity(LIQUID, stack=20, quality=1, prior=0.10, atr=104)
    assert q["ok"] and q["conf"] == 1.0 and q["sold"] == 48.0 and q["market"] == 100 and q["reasons"] == []
    assert q["sellers"] == 6 and q["days"] == 5 and q["scans"] == 10 and q["hours"] == 60
    d, why = A.safe_flip(1200, 20, q, vendor_unit=3, min_profit=100)
    assert why is None and d["safe"] and d["maxQty"] == 144 and d["stop"] == {"unit": 51, "loss": 11}
    assert d["profit"] == 679 and d["days"] == 0.42


@pytest.mark.parametrize("hist,kw,code", [
    (_hist([5000], n=1, sel=1), {"stack": 1, "quality": 2}, "rare"),            # BoE green seen once
    (_hist([5000, 5100, 4900, 5000], gone=0, hrs=12, sel=2), {"stack": 1, "quality": 2}, "rare"),
    (_hist([100, 180, 60, 150, 90], gone=40, hrs=12), {}, "unstable"),          # price jumps around
    (_hist([100, 100, 100, 100, 100], gone=0, hrs=12), {}, "slow"),             # nothing ever sells
    (_hist([100, 100, 100, 100, 100], gone=40, hrs=1), {}, "unmeasured"),       # scans too close together
    (_hist([100, 100]), {}, "short"),                                           # 2 days only
    (_hist([100, 100, 100], n=1, gone=40, hrs=12), {"prior": 0.10}, "scans"),
    (_hist([100, 100, 100, 100, 60], gone=40, hrs=12), {}, "falling"),
    (_hist([100, 100, 100, 100, 100], gone=40, hrs=12, sel=1), {}, "sellers"),  # one seller resets the price
    (LIQUID, {"atr": 300}, "atr"),                                              # Auctionator disagrees
    (LIQUID, {"age_days": 5}, "stale"),
])
def test_illiquid_items_are_rejected(hist, kw, code):
    q = A.liquidity(hist, **kw)
    assert not q["ok"] and q["reasons"][0]["code"] == code, q["reasons"]
    d, why = A.safe_flip(10, 1, q)
    assert d is None and why["code"] == code


def test_slow_reason_text_and_rare_needs_two_sales():
    q = A.liquidity(_hist([100] * 5, gone=0, hrs=12))
    assert q["reasons"][0]["text"] == "se vend peu (0,0 par jour)"
    ok = A.liquidity(_hist([5000, 5100, 4900, 5000, 5000], gone=2, hrs=48, sel=4), stack=1, quality=2)
    assert ok["ok"], ok["reasons"]                       # a green that DOES sell (≥ 2 sales seen) can pass


def test_demand_prior_and_classes():
    assert A.demand_class(7, ilvl=20, level=28) == ("artisanat", 0.10)
    assert A.demand_class(7, ilvl=55, level=28) == ("artisanat", 0.03)
    assert A.demand_class(0) == ("consommable", 0.10) and A.demand_class(6) == ("munitions", 0.10)
    assert A.demand_class(1)[0] == "sac" and A.demand_class(5) == ("composant", 0.05)
    assert A.demand_class(4, quality=2, min_level=30, level=28) == ("équipement de leveling", 0.05)
    assert A.demand_class(2, quality=2, min_level=50, level=28) == ("équipement", 0.0)
    two = _hist([100, 100], gone=40, hrs=12)
    assert A.liquidity(two)["reasons"][0]["code"] == "short"
    assert A.liquidity(two, prior=0.10)["ok"]            # consumables / trade goods: 2 days are enough


def test_hard_rules_quantity_stop_cap():
    assert A.max_quantity(48, 3, held=20) == 124 and A.max_quantity(None) == 0 and A.max_quantity(0.2) == 0
    assert A.stop_loss(1000) == {"unit": 850, "loss": 192}
    assert A.stop_loss(100, vendor_unit=95)["unit"] == 95          # never below the vendor price
    assert A.item_cap(1_000_000) == 200_000 and A.item_cap(1_000_000, 20, 150_000) == 150_000
    q = A.liquidity(LIQUID)
    d, why = A.safe_flip(60 * 200, 200, q)
    assert d is None and why["code"] == "stack" and "144" in why["text"]
    d, why = A.safe_flip(60 * 20, 20, q, held=130)                 # we already hold 130 → only 14 more
    assert why["code"] == "stack"
    d, why = A.safe_flip(95 * 20, 20, q)
    assert why["code"] == "notcheap"
    d, why = A.safe_flip(60 * 20, 20, q, qty_ahead=200)            # 200 cheaper units ahead of us
    assert why["code"] == "tts"
    d, why = A.safe_flip(78 * 20, 20, q, vendor_unit=3, min_profit=5000)
    assert why["code"] == "margin"


def test_sold_between_ignores_reposts():
    assert A.sold_between({"100:20": 60, "90:5": 5}, {"100:20": 20, "95:20": 20}) == 25
    assert A.sold_between({"100:20": 40}, {"100:20": 40}) == 0
    assert A.sold_between({"100:20": 40}, {}) == 40
    assert A.sold_between({}, {"80:20": 20}) == 0


def test_why_none_and_reader():
    assert A.why_none({}, 0) == ["Aucune annonce assez sous le prix du marché parmi les objets suivis."]
    w = A.why_none({"slow": 2, "rare": 1, "unstable": 2, "atr": 1}, 6)
    assert w == ["6 affaire(s) apparente(s) écartée(s) :", "se vend trop peu (2)", "prix instable (2)",
                 "Auctionator pas d'accord (1)"]
    db = {"summary": {"auction": {"deals": [], "none": {"why": w}}}}
    ctx = A.auction_context(db)
    assert ctx["aucun_achat_sur"][0] == "Aucun achat-revente sûr pour l'instant"
    assert A.answer_lines(db)[0] == "Aucun achat-revente sûr pour l'instant"
    d = A.auction_context({"summary": {"auction": {"deals": [{"name": "Lin", "count": 20, "buy": 1200, "sell": 1980,
                                                               "profit": 679, "sold": 48, "stop": 51}]}}})
    assert d["affaires"][0].endswith("se vend ~48,0/jour, stop-loss 51 pc/u")


@needs_lua
@pytest.mark.parametrize("flavor", ["modern", "legacy"])
def test_lua_liquidity_scenario(flavor):
    """Same model in Lua: illiquid bargains refused, liquid kept, quick buttons one action per click."""
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--flavor", flavor, "--scenario",
                        "tests/wow/auction_liquidity_scenario.lua", str(ADDON)],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout


def test_app_hv_tab_shows_no_safe_flip_with_why():
    from treeaicoach import ui_page_wow as pw
    from treeaicoach.wow import live
    snap = {"summary": {"auction": {"deals": [], "none": {"why": ["3 affaire(s) apparente(s) écartée(s) :",
                                                                    "se vend trop peu (3)"]},
                                    "rules": {"capGold": 20, "qtyDays": 3}}}}
    v = live.ah_view(snap)
    assert v["none"][0] == "Aucun achat-revente sûr pour l'instant" and "20 %" in v["rules"]
    hv = pw.tab_blocks("hv", snap)
    assert hv[0]["title"] == "Achat-revente" and hv[0]["rows"][0]["title"].startswith("Aucun achat-revente sûr")
    snap["summary"]["auction"] = {"scanAge": 600,         # (4.0) deals only from a scan of less than 24 h
                                  "deals": [{"name": "Lin", "id": 2589, "count": 20, "kind": "flip", "buy": 1200,
                                             "sell": 1980, "profit": 679, "deposit": 36, "sold": 47.8, "stop": 51}]}
    row = pw.tab_blocks("hv", snap)[0]["rows"][0]
    assert "se vend ~47,8/jour" in row["desc"] and "stop-loss 51 pc/u" in row["desc"]


def test_cancel_advice():
    """Mirror of F.cancelAdvice (tests/wow/auction_owned_scenario.lua asserts the same numbers)."""
    q = A.liquidity(_hist([100] * 5, gone=40, hrs=12))
    comp = [(100 + k, 20) for k in range(1, 16)]
    c = A.cancel_advice(150, 20, comp, q, 13)
    assert c["action"] == "cancel" and c["repost"] == 100 and c["gain"] == 1346 and c["repost"] <= comp[0][0] - 1
    odd = A.cancel_advice(9500, 1, [(325, 1)], q)                  # the user's screen: 95 pa vs 3 pa 25 pc
    assert odd["action"] == "keep" and "plus de 10 fois" in odd["text"]
    assert "anormalement bas" in A.cancel_advice(150, 20, [(50, 20)] + comp, q, 13)["text"]
    small = A.cancel_advice(150, 20, [(101, 20)], q, 13)
    assert small["action"] == "keep" and small["gain"] == -1058 and "dépôt perdu" in small["text"]
    assert A.cancel_advice(150, 20, comp, {"ok": False})["action"] == "none"
    assert A.cancel_advice(100, 20, [(120, 20)], q)["text"] == "le moins cher : garder"
    for mine in (102, 120, 150, 300):                               # never repost above the lowest
        r = A.cancel_advice(mine, 20, comp, q, 13)
        assert r["action"] != "cancel" or r["repost"] <= 100


@needs_lua
@pytest.mark.parametrize("flavor", ["modern", "legacy", "forever"])
def test_lua_owned_scenario(flavor):
    """The user's « Mes ventes » screen: no advice mid-scan, no price advice on special items, sane cancels."""
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--flavor", flavor, "--scenario",
                        "tests/wow/auction_owned_scenario.lua", str(ADDON)],
                       cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout


def test_bindings_xml_rafale_key():
    """« TreeAI : action HV suivante »: a key binding (no default key) calling the module's one-action entry."""
    import xml.etree.ElementTree as ET
    root = ET.parse(ADDON / "Bindings.xml").getroot()
    b = [x for x in root if x.get("name") == "TREEAI_AH_NEXT"]
    assert len(b) == 1 and b[0].get("header") == "TREEAI" and b[0].get("category") == "ADDONS"
    assert b[0].get("default") is None and "TreeAI.AuctionNext()" in b[0].text
    src = MODULE.read_text(encoding="utf-8")
    assert "T.AuctionNext = function()" in src and "BINDING_NAME_TREEAI_AH_NEXT" in src


@needs_lua
@pytest.mark.parametrize("flavor", ["modern", "legacy", "forever"])
def test_lua_rafale_scenario(flavor):
    """Mode rafale: one press = at most one protected call (strict mock), pauses on partial scan / reserve."""
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--flavor", flavor, "--scenario",
                        "tests/wow/auction_rafale_scenario.lua", str(ADDON)],
                       cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout and "violations=0" in r.stdout


# ------------------------------------------------------------------ 4.1 AHPROFIT: honest profit engine (mirrored in Lua)
def test_min_net_threshold():
    assert A.min_net(0) == 100 and A.min_net(500) == 100           # 1 pa floor
    assert A.min_net(1000) == 150 and A.min_net(1001) == 151       # 15 % of the stake, rounded up
    assert A.min_net(1000, 500) == 500                              # player's own minimum
    ranked = A.rank_deals([A.evaluate_vendor_flip(50, 2, 40)])     # +30 pc at the vendor: not worth a click
    assert ranked == []
    assert A.rank_deals([A.evaluate_vendor_flip(300, 1, 500)])[0]["profit"] == 200


def test_flip_accepted_and_rejected_by_threshold():
    q = A.liquidity(LIQUID)                                         # market 100, ~48 sold / day
    d, why = A.safe_flip(60 * 20, 20, q)                           # default threshold (1 pa / 15 %)
    assert why is None and d["profit"] >= A.min_net(1200)
    d, why = A.safe_flip(85 * 20, 20, q)                           # 15 % under market: net < 15 % of the stake
    assert d is None and why["code"] == "margin" and "il faut" in why["text"]
    d, why = A.safe_flip(60 * 20, 20, A.liquidity(_hist([100] * 5, gone=0, hrs=12)))
    assert d is None and why["code"] == "slow"                      # cheap but never sells: refused


def test_undercut_and_ev_and_post_quantity():
    assert A.undercut(1000) == 999 and A.undercut(1000, 5) == 950 and A.undercut(10, 5) == 9
    s = A.suggest_post(5, 20, 1000, 0.1, 10, [(950, 5), (980, 5)], undercut_pct=2)
    assert s["unit"] == 931 and s["reason"] == "2 % sous le moins cher"
    # wool that never sells (volume measured 0): the honest expectation says vendor
    dead = A.suggest_post(20, 33, 306, 0.05, 0.0, [(306, 20)])
    assert dead["better"] == "vendor" and dead["ev"] < dead["vendor"] < dead["net"]
    assert A.post_quantity(20, 3, 24) == 3 and A.post_quantity(20, 3, 48) == 6
    assert A.post_quantity(20, 3, 24, listed=2) == 1 and A.post_quantity(20, 3, 24, listed=5) == 0
    assert A.post_quantity(20, None) == 20 and A.post_quantity(20, 48, 24, split=5) == 5
    assert A.daily_volume([[1, 90, 100, 5, 2, 0, 0.5]]) is None     # 30 min of scans: unknown, not « 0 / day »


def test_vendor_to_ah_and_craft_profit():
    q = A.liquidity(LIQUID)                                         # resale 100 × (1 − ½σ)
    v = A.vendor_to_ah(40, q, vendor_sell=10)
    assert v["count"] == 144 and v["buyUnit"] == 40 and v["profit"] >= A.min_net(40 * 144)
    assert A.vendor_to_ah(95, q, vendor_sell=10) is None            # no margin after the 5 % cut
    assert A.vendor_to_ah(40, {"ok": False}) is None                # no sales evidence: nothing
    assert A.craft_profit(50, 1, q, vendor_sell=10) is None         # one item: +~44 pc < 1 pa → refused
    c3 = A.craft_profit(100, 3, q, vendor_sell=10)
    assert c3 and c3["count"] == 3 and c3["profit"] >= A.min_net(100)


def test_reader_lists_crafts_and_vendor_flips():
    db = {"summary": {"auction": {"deals": [], "crafts": [{"name": "Bandage en laine", "cost": 30, "sell": 300, "profit": 230}],
                                  "vendorFlips": [{"name": "Fil fin", "count": 10, "buyUnit": 9, "sellUnit": 40,
                                                   "profit": 250, "lim": True}]}}}
    ctx = A.auction_context(db)
    assert ctx["a_fabriquer"][0] == "Bandage en laine : composants 30 pc, se vend 3 pa → +2 pa 30 pc net"
    assert "quantité limitée" in ctx["marchand_vers_hv"][0]
    assert any(line.startswith("À fabriquer — Bandage") for line in A.answer_lines(db))


@needs_lua
@pytest.mark.parametrize("flavor", ["modern", "legacy", "forever"])
def test_lua_profit_scenario(flavor):
    """4.1 AHPROFIT: py parity, evidence over days, thresholds, re-checked buys, one action per click, no combat,
    nothing in chat, vendor → AH, crafts, bags, the window's HV page (3 numbers + 4 buttons + lists)."""
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--flavor", flavor, "--scenario",
                        "tests/wow/auction_profit_scenario.lua", str(ADDON)],
                       cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout and "violations=0" in r.stdout
