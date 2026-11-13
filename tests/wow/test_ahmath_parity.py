"""5.1 AH — the app's desk maths (treeaicoach/wow/ahmath.py) and the addon's (Modules/AHMath.lua) give the same
numbers to the copper: fees, deposits, sale chance, floors, post price, evidence classes, « sûr » / « probable » /
« réguler » deals, the buy queue, the ledger, the goal, cancel gains, craft spreads and sale-time patterns, on a few
thousand generated cases run through both (tests/wow/ahmath_parity.lua prints the Lua side as JSON)."""
from __future__ import annotations

import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from treeaicoach.wow import ahmath as X

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
AHMATH = ROOT / "wow_addon" / "TreeAI" / "Modules" / "AHMath.lua"
LUA = shutil.which("lua5.1")
pytestmark = pytest.mark.skipif(not LUA, reason="lua5.1 not installed")

PY = {
    "cut": X.cut, "net": X.net, "deposit": X.deposit, "sellPm": X.sell_pm, "days": X.days, "depLoss": X.dep_loss,
    "vendorNet": X.vendor_net, "flipNet": X.flip_net, "need": X.need, "floorUnit": X.floor_unit, "undercut": X.undercut,
    "postUnit": X.post_unit, "evidence": X.evidence, "vendorDeals": X.vendor_deals, "flipDeals": X.flip_deals,
    "allocate": X.allocate, "budget": X.budget, "realized": X.realized, "goal": X.goal, "buyCost": X.buy_cost,
    "resetDeals": X.reset_deals, "cancelGain": X.cancel_gain, "cancelNeed": X.cancel_need,
    "craftSpread": X.craft_spread, "pattern": X.pattern,
}


def lua_lit(v) -> str:
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, (list, tuple)):
        return "{" + ", ".join(lua_lit(x) for x in v) + "}"
    if isinstance(v, dict):
        parts = []
        for k, x in v.items():
            if x is None:
                continue
            key = f"[{k}]" if isinstance(k, int) else f'["{k}"]'
            parts.append(f"{key} = {lua_lit(x)}")
        return "{" + ", ".join(parts) + "}"
    raise TypeError(type(v))


def norm(v):
    """JSON-comparable: tuples → lists, dict keys → str, None fields dropped, trailing None returns stripped."""
    if isinstance(v, dict):
        d = {k: x for k, x in v.items() if x is not None}
        if d and all(isinstance(k, int) and not isinstance(k, bool) for k in d) and sorted(d) == list(range(1, len(d) + 1)):
            return [norm(d[k]) for k in range(1, len(d) + 1)]   # a Lua table with keys 1..n is an array
        return {str(k): norm(x) for k, x in d.items()}
    if isinstance(v, (list, tuple)):
        out = [norm(x) for x in v]
        return out if out else {}
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def returns(v) -> list:
    r = list(v) if isinstance(v, tuple) else [v]
    while r and r[-1] is None:
        r.pop()
    return [norm(x) for x in r]


def gen_cases(n: int, seed: int = 51) -> list[tuple[str, list]]:
    rnd = random.Random(seed)
    ri = rnd.randint
    names = ["Vendeur1", "Vendeur2", "Vendeur3", "Vendeur4", "Vendeur1,Vendeur5"]

    def ev():
        return {"ok": rnd.random() < 0.6, "onlySellers": rnd.random() < 0.3 or None, "resetOnly": rnd.random() < 0.3 or None,
                "days": ri(0, 7), "scans": ri(0, 12), "hours": ri(0, 80), "sold": rnd.choice([None, 0, 0.5, ri(1, 60), round(rnd.uniform(0.1, 50), 2)]),
                "sellers": ri(0, 8), "volPct": ri(0, 50), "ageH": round(rnd.uniform(0, 30), 3), "why": "x"}

    def rows(com: bool, base: int, k: int, sellers=True):
        out = []
        for i in range(k):
            c = ri(1, 30) if com else 1
            u = max(1, base + ri(-base // 2, base))
            r = {"unit": u, "count": c, "buy": u * c + (0 if com else ri(0, c - 1)), "auctionID": 1000 + i}
            if sellers and rnd.random() < 0.95:
                r["seller"] = rnd.choice(names)
            out.append(r)
        out.sort(key=lambda r: (r["unit"], r["auctionID"]))
        return out

    cases: list[tuple[str, list]] = []
    for i in range(n):
        neutral = rnd.random() < 0.15
        sold = rnd.choice([None, 0, ri(1, 50), round(rnd.uniform(0.05, 80), 3)])
        com = rnd.random() < 0.5
        cases += [
            ("cut", [ri(0, 10 ** 7), neutral]), ("net", [ri(0, 10 ** 7), neutral]),
            ("deposit", [ri(0, 5000), ri(0, 200), rnd.choice([12, 24, 48]), neutral, rnd.choice([None, ri(0, 900)])]),
            ("sellPm", [sold, ri(0, 400), ri(0, 200), rnd.choice([12, 24, 48])]), ("days", [sold, ri(0, 400), ri(0, 200)]),
            ("depLoss", [ri(0, 5000), ri(1, 1000)]), ("vendorNet", [ri(0, 10 ** 6), ri(0, 5000), ri(1, 100)]),
            ("flipNet", [ri(0, 10 ** 6), ri(0, 20000), ri(1, 100), ri(0, 3000), ri(50, 950), neutral]),
            ("need", [ri(0, 10 ** 6), ri(1, 500), ri(1, 50)]),
            ("floorUnit", [ri(0, 10 ** 5), ri(1, 40), ri(0, 900), ri(50, 950), neutral, ri(0, 5000)]),
            ("undercut", [ri(0, 9000), rnd.choice([0, 0, ri(1, 20)])]),
            ("postUnit", [{"floor": ri(1, 9000), "low": rnd.choice([None, ri(1, 12000)]), "mine": rnd.choice([None, ri(1, 12000)]),
                           "normal": rnd.choice([None, ri(1, 12000)]), "undercutPct": rnd.choice([None, 0, ri(1, 10)])}]),
            ("evidence", [ev(), None, rnd.choice([None, "sellers", "reset"])]),
            ("buyCost", [rows(True, ri(5, 5000), ri(0, 8)), ri(0, 120)]),
            ("cancelGain", [{"my": ri(1, 9000), "n": ri(1, 20), "dep": ri(0, 400), "aheadKeep": ri(0, 80), "new": ri(1, 9000),
                             "aheadNew": ri(0, 20), "dep2": ri(0, 400), "cost": rnd.choice([0, ri(1, 100)]), "sold": sold,
                             "hours": 24, "neutral": neutral}]),
            ("cancelNeed", [ri(1, 9000), ri(1, 20), neutral]),
            ("budget", [ri(0, 10 ** 7), ri(0, 10 ** 6), ri(0, 100), ri(0, 10 ** 6)]),
            ("goal", [ri(-10 ** 5, 10 ** 6), ri(0, 10 ** 6), rnd.choice([None, ri(0, 10 ** 6)])]),
        ]
        vend = ri(0, 600)
        cases.append(("vendorDeals", [i, rows(com, max(1, vend), ri(0, 10)), vend, com]))
        mk = ri(10, 9000)
        o = {"id": 100000 + i, "rows": rows(com, int(mk * 0.8), ri(0, 13)), "market": mk, "sold": rnd.choice([None, ri(1, 60), round(rnd.uniform(0.5, 60), 2)]),
             "ahead": ri(0, 30), "maxQty": ri(-5, 300), "vendorUnit": ri(0, 300), "hours": rnd.choice([12, 24, 48]), "neutral": neutral,
             "depPm": rnd.choice([None, ri(0, 400)]), "commodity": com, "ev": ev(), "cap": rnd.choice([None, ri(100, 50000)]),
             "mineLow": rnd.choice([None, None, ri(1, 12000)]), "more": rnd.random() < 0.1}
        cases.append(("flipDeals", [o]))
        o2 = dict(o)
        o2["market"] = ri(20, 12000)
        o2["rows"] = rows(com, int(o2["market"] * 0.5), ri(0, 10))
        cases.append(("resetDeals", [o2]))
        cases.append(("craftSpread", [{"cost": ri(0, 20000), "made": ri(1, 5), "market": ri(0, 20000), "sold": o["sold"], "ahead": ri(0, 5),
                                       "vendorUnit": ri(0, 300), "hours": 24, "neutral": neutral, "ev": ev()}]))
        # favourable cases too (the deal paths, not only the refusals)
        good = {"ok": True, "days": 5, "scans": 10, "hours": 60, "sold": ri(5, 60), "sellers": 6, "volPct": 5, "ageH": 1}
        o3 = dict(o, ev=good, sold=good["sold"], maxQty=ri(20, 300), ahead=ri(0, 5), mineLow=None,
                  rows=rows(com, int(mk * 0.55), ri(1, 8)) + [{"unit": mk + ri(0, 50), "count": 5, "buy": (mk + 25) * 5, "auctionID": 999}])
        cases.append(("flipDeals", [o3]))
        rm = ri(50, 9000)
        wall = []
        for j in range(ri(1, 4)):
            c = ri(1, 12) if com else 1
            u = int(rm * rnd.uniform(0.25, 0.6))
            wall.append({"unit": u, "count": c, "buy": u * c, "auctionID": 2000 + j, "seller": rnd.choice(names[:2])})
        wall.sort(key=lambda r: (r["unit"], r["auctionID"]))
        wall.append({"unit": rm + ri(0, 40), "count": 10, "buy": rm * 10, "auctionID": 2999, "seller": "Vendeur3"})
        reset_ev = {"ok": False, "onlySellers": True, "days": 5, "scans": 10, "hours": 60, "sold": ri(8, 60), "sellers": 2, "volPct": 5, "ageH": 1}
        cases.append(("resetDeals", [dict(o, rows=wall, market=rm, ev=reset_ev, sold=reset_ev["sold"], cap=None, mineLow=None, more=False, ahead=ri(0, 3))]))
        cases.append(("craftSpread", [{"cost": ri(10, 2000), "made": ri(1, 3), "market": ri(2500, 20000), "sold": ri(5, 40), "ahead": 0,
                                       "vendorUnit": ri(0, 300), "hours": 24, "neutral": neutral, "ev": good}]))
        b = {}
        for _ in range(ri(0, 30)):
            b[ri(0, 41)] = {"g": ri(0, 500), "h": ri(0, 3000)}
        cases.append(("pattern", [b]))
    # the queue and the ledger on bigger sets
    for i in range(n // 4):
        deals = []
        for j in range(ri(0, 12)):
            g = rnd.choice([None, None, "reset:%d" % ri(1, 3)])
            deals.append({k: v for k, v in {"id": ri(1, 6), "net": ri(-50, 5000), "buy": ri(1, 30000), "auctionID": ri(1, 10 ** 6) if rnd.random() < 0.8 else None,
                                              "class": rnd.choice(["sur", "probable", "regul"]), "group": g, "j": j}.items() if v is not None})
        cases.append(("allocate", [deals, ri(0, 80000), rnd.choice([None, ri(1000, 40000)])]))
        ev_ = []
        for _ in range(ri(0, 25)):
            k = rnd.choice(["buy", "buy", "sale", "vend", "exp", "cancel"])
            e = {"k": k, "id": rnd.choice([1, 2, 3]), "n": ri(1, 20), "c": ri(0, 9000), "cut": ri(0, 400), "dep": ri(0, 300),
                 "day": rnd.choice(["d1", "d2", "d3"])}
            if rnd.random() < 0.1:
                del e["id"]
                e["name"] = "butin"
            ev_.append(e)
        cases.append(("realized", [ev_]))
    return cases


def test_lua_and_python_agree(tmp_path):
    cases = gen_cases(400)
    f = tmp_path / "cases.lua"
    body = []
    for fn, args in cases:
        items = ", ".join(f"[{i + 1}] = {lua_lit(a)}" for i, a in enumerate(args) if a is not None)
        body.append(f'{{ fn = "{fn}", args = {{ n = {len(args)}{", " if items else ""}{items} }} }}')
    f.write_text("return {\n" + ",\n".join(body) + "\n}\n", encoding="utf-8")
    r = subprocess.run([LUA, str(HERE / "ahmath_parity.lua"), str(AHMATH), str(f)], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-3000:]
    lines = r.stdout.splitlines()
    assert len(lines) == len(cases)
    bad, by = [], {}
    for (fn, args), line in zip(cases, lines):
        lua = json.loads(line)
        assert not (isinstance(lua, dict) and "error" in lua), (fn, lua)
        while lua and lua[-1] is None:
            lua.pop()
        lua = [norm(x) for x in lua]
        py = returns(PY[fn](*args))
        by[fn] = by.get(fn, 0) + 1
        if py != lua:
            bad.append((fn, args, lua, py))
    assert not bad, f"{len(bad)} mismatches, first: {bad[0]}"
    for fn in PY:
        assert by.get(fn, 0) >= 50, (fn, by.get(fn))


def test_deals_are_not_all_empty():
    """the generated cases really exercise the deal paths (not only refusals)"""
    cases = gen_cases(400)
    found = {"flipDeals": 0, "resetDeals": 0, "vendorDeals": 0, "craftSpread": 0, "pattern": 0}
    for fn, args in cases:
        if fn in found:
            r = PY[fn](*args)
            v = r[0] if isinstance(r, tuple) else r
            if v:
                found[fn] += 1
    assert all(n >= 5 for n in found.values()), found


def test_known_numbers():
    assert X.cut(10000) == 500 and X.cut(10000, True) == 1500 and X.deposit(100, 5, 24) == 300
    assert X.flip_net(24400, 989, 40, 600, 950)[0] == 989 * 40 - 1978 - X.dep_loss(600, 950) - 24400
    ds, _ = X.reset_deals({"id": 1, "market": 100, "sold": 12, "ahead": 0, "vendorUnit": 5, "hours": 24, "commodity": True,
                           "ev": {"ok": False, "onlySellers": True, "days": 5, "scans": 10, "hours": 60, "sold": 12, "sellers": 2, "volPct": 5, "ageH": 1},
                           "rows": [{"unit": 40, "count": 10, "buy": 400, "seller": "Vendeur1"}, {"unit": 45, "count": 15, "buy": 675, "seller": "Vendeur2"},
                                    {"unit": 98, "count": 20, "buy": 1960, "seller": "Vendeur1"}]})
    assert len(ds) == 1 and ds[0]["qty"] == 25 and ds[0]["buy"] == 1075 and ds[0]["sellUnit"] == 97 and ds[0]["class"] == "regul"


def test_app_reads_the_desk_summary():
    from treeaicoach.wow import auction as A
    db = {"chars": {"x": {"summary": {"ahdesk": {"today": 71241, "week": 120000, "goal": 500000, "buy": 3, "post": 2, "cancel": 1,
                                                 "free": 230825, "tied": 12400}}}}}
    ctx = A.auction_context(db)
    assert ctx["objectif_du_jour"] == "aujourd'hui 7 po 12 pa 41 pc sur 50 po (14 %), 7 jours 12 po"
    assert ctx["files_hv"] == "Acheter 3 · Poster 2 · Annuler 1"
    assert ctx["capital_hv"] == "libre 23 po 8 pa 25 pc, immobilisé 1 po 24 pa"
    assert A.desk.cut(10000) == 500 and A.desk is X
