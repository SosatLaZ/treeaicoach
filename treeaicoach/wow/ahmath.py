"""Buy-and-resell desk maths (5.1 AH) — the Python twin of ``wow_addon/TreeAI/Modules/AHMath.lua``.

Same functions, same integer (copper) rounding, same float operations in the same order, so the app and the addon
give the same numbers to the copper. ``tests/wow/test_ahmath_parity.py`` runs both on thousands of generated cases
and compares every field.

Rule: a number is exact or absent. Classes: « sur » (vendor resale, provable), « probable » (multi-day evidence of
sales), « regul » (resetting a thin cheap market: explicit thresholds, risk stated), « speculatif » (never queued).
Formulas are documented in ``research/ah5.md``. Pure functions, no I/O; deals are plain dicts (the Lua tables' keys).
"""

from __future__ import annotations

import math
from typing import Any, Iterable

__all__ = [
    "CUT_PCT", "DEP_PCT", "RULES", "cut", "net", "deposit", "sell_pm", "days", "dep_loss", "vendor_net", "flip_net",
    "need", "floor_unit", "undercut", "post_unit", "evidence", "vendor_deals", "flip_deals", "allocate", "budget",
    "realized", "goal", "buy_cost", "reset_deals", "cancel_gain", "cancel_need", "craft_spread", "pattern",
]

CUT_PCT = {False: 5, True: 15}
DEP_PCT = {False: 5, True: 25}
RULES: dict[str, Any] = {
    "minNet": 100, "marginPct": 15, "sharePct": 50, "itemPct": 20, "maxDays": 3,
    "minDays": 3, "minScans": 4, "minHours": 12, "minSold": 1, "minSellers": 3, "maxVolPct": 30,
    "maxAgeH": 12, "rows": 12,
    "resetMaxSellers": 3, "resetMaxUnit": 10000, "resetWallPct": 75, "resetCap": 30000, "resetMinSold": 2,
    "resetMaxDays": 4, "resetMarginPct": 30,
    "cancelMinPct": 5,
    "patMinHours": 600, "patMinBuckets": 8, "patMinDays": 4, "patMinPm": 1200,
}


# ------------------------------------------------------------------ Lua-like helpers
def _num(v: Any) -> float | None:
    """Lua ``tonumber`` on a value (numbers and numeric strings; bools are not numbers)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return v if math.isfinite(v) else None
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _n0(v: Any) -> float:
    x = _num(v)
    return 0 if x is None else x


def _int(v: Any) -> int:
    return int(math.floor(_n0(v)))


def _neutral(n: Any) -> bool:
    return bool(n) and n is not None


def _R(R: dict | None) -> dict:
    return R if R is not None else RULES


# ------------------------------------------------------------------ fees
def cut(total: Any, neutral: Any = False) -> int:
    return max(0, _int(total)) * CUT_PCT[_neutral(neutral)] // 100


def net(total: Any, neutral: Any = False) -> int:
    return max(0, _int(total)) - cut(total, neutral)


def deposit(vendor_unit: Any, qty: Any, hours: Any = 24, neutral: Any = False, pm: Any = None) -> int:
    base = max(0, _int(vendor_unit)) * max(1, _int(qty))
    if pm is not None and _num(pm) is not None and _n0(pm) >= 0:
        return base * _int(pm) // 1000
    h = 24 if hours is None else hours
    return base * DEP_PCT[_neutral(neutral)] * max(0, _int(h)) // 200


def sell_pm(sold_per_day: Any, ahead: Any = 0, qty: Any = 1, hours: Any = 24) -> int | None:
    v = _num(sold_per_day)
    if v is None:
        return None
    if v <= 0:
        return 50
    need_ = max(0, _n0(ahead)) + max(1, _n0(qty))
    h = 24 if hours is None else hours
    p = math.floor(v * max(1, _n0(h)) / 24 / need_ * 1000)
    return int(max(50, min(950, p)))


def days(sold_per_day: Any, ahead: Any = 0, qty: Any = 1) -> float | None:
    v = _num(sold_per_day)
    if v is None or v <= 0:
        return None
    return math.floor((max(0, _n0(ahead)) + max(1, _n0(qty))) / v * 100 + 0.5) / 100


def dep_loss(dep: Any, pm: Any) -> int:
    d = max(0, _int(dep))
    p = max(1, min(1000, _int(1000 if pm is None else pm)))
    return int(math.ceil(d * (1000 - p) / p))


def vendor_net(buy_total: Any, vendor_unit: Any, qty: Any) -> int:
    return max(0, _int(vendor_unit)) * max(1, _int(qty)) - max(0, _int(buy_total))


def flip_net(buy_total: Any, sell_unit: Any, qty: Any, dep: Any, pm: Any, neutral: Any = False) -> tuple[int, int, int, int]:
    gross = max(0, _int(sell_unit)) * max(1, _int(qty))
    c = cut(gross, neutral)
    loss = dep_loss(dep, pm)
    return gross - c - loss - max(0, _int(buy_total)), gross, c, loss


def need(buy: Any, min_net: Any = None, margin_pct: Any = None) -> int:
    mn = RULES["minNet"] if min_net is None else min_net
    mp = RULES["marginPct"] if margin_pct is None else margin_pct
    return int(max(_int(mn), math.ceil(max(0, _int(buy)) * _n0(mp) / 100)))


def floor_unit(cost_total: Any, qty: Any, dep: Any, pm: Any, neutral: Any, need_: Any) -> int:
    q = max(1, _int(qty))
    want = max(0, _int(cost_total)) + dep_loss(dep, pm) + max(0, _int(need_))
    u = int(max(1, math.ceil(want * 100 / (100 - CUT_PCT[_neutral(neutral)]) / q)))
    while u > 1 and flip_net(cost_total, u - 1, q, dep, pm, neutral)[0] >= _int(need_):
        u -= 1
    while flip_net(cost_total, u, q, dep, pm, neutral)[0] < _int(need_):
        u += 1
    return u


def undercut(low: Any, pct: Any = 0) -> int:
    lo = max(1, _int(low))
    if _n0(pct) <= 0:
        return max(1, lo - 1)
    return int(max(1, min(lo - 1, math.floor(lo * (100 - _n0(pct)) / 100))))


def post_unit(o: dict) -> tuple[int | None, str | None, str | None]:
    fl = max(1, _int(o.get("floor")))
    low, mine, normal = _num(o.get("low")), _num(o.get("mine")), _num(o.get("normal"))
    if low is not None and low > 0:
        if mine is not None and mine > 0 and mine <= low:
            u = _int(mine)
        else:
            u = undercut(low, o.get("undercutPct"))
        if normal is not None and normal > 0 and u > math.floor(normal * 3 / 2):
            u = int(math.floor(normal * 3 / 2))
        if u < fl:
            return None, "wait", "le moins cher est sous ton plancher"
        return u, ("match" if (mine is not None and mine > 0 and mine <= low) else "undercut"), None
    if normal is not None and normal > 0:
        if normal < fl:
            return None, "wait", "le prix habituel est sous ton plancher"
        return _int(normal), "normal", None
    return None, "unknown", "aucun prix connu : relis les prix"


def evidence(ev: Any, R: dict | None = None, mode: str | None = None) -> tuple[str, str | None]:
    R = _R(R)
    if not isinstance(ev, dict):
        return "speculatif", "jamais vu en scan"
    no_sellers = mode in ("sellers", "reset")
    if _n0(ev.get("days")) < R["minDays"]:
        return "speculatif", "historique trop court (%d j sur %d)" % (_n0(ev.get("days")), R["minDays"])
    if _n0(ev.get("scans")) < R["minScans"]:
        return "speculatif", "trop peu de scans (%d sur %d)" % (_n0(ev.get("scans")), R["minScans"])
    if _n0(ev.get("hours")) < R["minHours"]:
        return "speculatif", "ventes pas encore mesurées"
    if ev.get("sold") is None or _n0(ev.get("sold")) < R["minSold"]:
        return "speculatif", "se vend trop peu"
    if not no_sellers and _n0(ev.get("sellers")) > 0 and _n0(ev.get("sellers")) < R["minSellers"]:
        return "speculatif", "trop peu de vendeurs"
    if _n0(ev.get("volPct")) > R["maxVolPct"]:
        return "speculatif", "prix instable"
    if _n0(ev.get("ageH")) > R["maxAgeH"]:
        return "speculatif", "prix trop vieux"
    if ev.get("ok") is False and not ((mode == "sellers" and ev.get("onlySellers"))
                                      or (mode == "reset" and (ev.get("onlySellers") or ev.get("resetOnly")))):
        return "speculatif", ev.get("why") or "preuves insuffisantes"
    return "probable", None


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def vendor_deals(item_id: Any, rows: list[dict], vendor_unit: Any, commodity: Any, R: dict | None = None) -> list[dict]:
    R = _R(R)
    out: list[dict] = []
    vu = _int(vendor_unit)
    if vu <= 0:
        return out

    def mk(qty: int, buy: int, row: dict | None, max_unit: int) -> None:
        n = vendor_net(buy, vu, qty)
        if n < R["minNet"]:
            return
        out.append(_drop_none({
            "id": item_id, "kind": "vendor", "class": "sur", "qty": qty, "buy": buy, "buyUnit": int(math.ceil(buy / qty)),
            "maxUnit": max_unit, "sellUnit": vu, "gross": vu * qty, "cut": 0, "dep": 0, "depLoss": 0, "fees": 0, "net": n,
            "capital": buy, "pm": 1000, "days": 0, "commodity": True if commodity else None,
            "auctionID": row.get("auctionID") if row else None, "count": row.get("count") if row else None}))

    if commodity:
        qty = buy = top = 0
        for r in rows:
            if r["unit"] < vu:
                qty, buy, top = qty + r["count"], buy + r["unit"] * r["count"], r["unit"]
            else:
                break
        if qty > 0:
            mk(qty, buy, None, top)
    else:
        for r in rows:
            if r["buy"] < vu * r["count"]:
                mk(r["count"], r["buy"], r, int(math.floor(r["buy"] / r["count"])))
            else:
                break
    return out


def flip_deals(o: dict, R: dict | None = None) -> tuple[list[dict], str | None]:
    R = _R(R)
    out: list[dict] = []
    klass, why = evidence(o.get("ev"), R)
    if klass != "probable":
        return out, why
    rows, market = o.get("rows") or [], _int(o.get("market"))
    if market <= 0 or len(rows) == 0:
        return out, "prix habituel inconnu"
    max_qty = _int(o.get("maxQty"))
    if max_qty <= 0:
        return out, "tu en as déjà autant qu'il s'en vend"
    best = None
    best_net = None
    nrows = min(len(rows), R["rows"])
    com = bool(o.get("commodity"))
    neutral = o.get("neutral")
    for k in range(1, nrows + 1):
        wall = rows[k] if k < len(rows) else None
        sell = market
        if wall is not None and wall["unit"] - 1 < sell:
            sell = wall["unit"] - 1
        sset: list[dict] = []
        qty = buy = total = 0
        ok_all = sell > 0
        for i in range(1, k + 1):
            if not ok_all:
                break
            r = rows[i - 1]
            take = r["count"]
            if qty + take > max_qty:
                take = (max_qty - qty) if com else 0
            if take <= 0:
                break
            cost = r["unit"] * take if com else r["buy"]
            pm = sell_pm(o.get("sold"), _n0(o.get("ahead")) + qty, take, o.get("hours"))
            dep = deposit(o.get("vendorUnit"), take, o.get("hours"), neutral, o.get("depPm"))
            n, gross, c, loss = flip_net(cost, sell, take, dep, pm, neutral)
            if n < need(cost, R["minNet"], R["marginPct"]):
                ok_all = False
                break
            d = days(o.get("sold"), _n0(o.get("ahead")) + qty, take)
            if d is None or d > R["maxDays"]:
                ok_all = False
                break
            qty, buy, total = qty + take, buy + cost, total + n
            sset.append(_drop_none({
                "id": o.get("id"), "kind": "flip", "class": "probable", "qty": take, "buy": cost,
                "buyUnit": int(math.ceil(cost / take)), "maxUnit": r["unit"], "sellUnit": sell, "gross": gross, "cut": c,
                "dep": dep, "depLoss": loss, "fees": c + loss, "net": n, "capital": cost, "pm": pm, "days": d,
                "auctionID": r.get("auctionID"), "count": r.get("count")}))
        if ok_all and len(sset) == k and (best_net is None or total > best_net):
            best, best_net = {"set": sset, "qty": qty, "buy": buy, "sell": sell}, total
    if best is None:
        return out, "pas assez sous le prix habituel"
    if com:
        qty, buy, sell = best["qty"], best["buy"], best["sell"]
        pm = sell_pm(o.get("sold"), _n0(o.get("ahead")), qty, o.get("hours"))
        dep = deposit(o.get("vendorUnit"), qty, o.get("hours"), neutral, o.get("depPm"))
        n, gross, c, loss = flip_net(buy, sell, qty, dep, pm, neutral)
        d = days(o.get("sold"), _n0(o.get("ahead")), qty)
        if n < need(buy, R["minNet"], R["marginPct"]) or d is None or d > R["maxDays"]:
            return out, "marge trop faible sur le lot"
        out.append({"id": o.get("id"), "kind": "flip", "class": "probable", "qty": qty, "buy": buy,
                    "buyUnit": int(math.ceil(buy / qty)), "maxUnit": best["set"][-1]["maxUnit"], "sellUnit": sell,
                    "gross": gross, "cut": c, "dep": dep, "depLoss": loss, "fees": c + loss, "net": n, "capital": buy,
                    "pm": pm, "days": d, "commodity": True})
    else:
        out.extend(best["set"])
    return out, None


def allocate(deals: Iterable[dict], budget_: Any, item_cap: Any = None) -> tuple[list[dict], int, int]:
    units: list[dict] = []
    by_group: dict[Any, dict] = {}
    for d in deals:
        g = d.get("group")
        if g:
            u = by_group.get(g)
            if u is None:
                u = {"group": g, "members": [], "net": 0, "buy": 0, "class": d.get("class"), "id": d.get("id"),
                     "aid": _n0(d.get("auctionID"))}
                by_group[g] = u
                units.append(u)
            u["members"].append(d)
            u["net"] += d["net"]
            u["buy"] += d["buy"]
            if _n0(d.get("auctionID")) < u["aid"]:
                u["aid"] = _n0(d.get("auctionID"))
        else:
            units.append({"members": [d], "net": d["net"], "buy": d["buy"], "class": d.get("class"), "id": d.get("id"),
                          "aid": _n0(d.get("auctionID"))})
    units.sort(key=lambda u: (-u["net"], 0 if u["class"] == "sur" else 1, u["buy"], u["id"], u["aid"]))
    out: list[dict] = []
    left, per, spent, gain = max(0, _int(budget_)), {}, 0, 0
    for u in units:
        cap_ok = u["class"] == "sur" or item_cap is None or _n0(per.get(u["id"])) + u["buy"] <= item_cap
        if u["net"] > 0 and u["buy"] <= left and cap_ok:
            out.extend(sorted(u["members"], key=lambda d: _n0(d.get("auctionID"))))
            left, spent, gain = left - u["buy"], spent + u["buy"], gain + u["net"]
            per[u["id"]] = _n0(per.get(u["id"])) + u["buy"]
    return out, spent, gain


def budget(gold: Any, tied: Any, share_pct: Any, reserve: Any) -> int:
    g, t = max(0, _int(gold)), max(0, _int(tied))
    cap = int(math.floor((g + t) * max(0, min(100, _n0(share_pct))) / 100))
    return max(0, min(cap - t, g - max(0, _int(reserve))))


def realized(ev: Iterable[dict]) -> dict:
    lots: dict[Any, list[dict]] = {}
    days_: dict[Any, int] = {}
    items: dict[Any, dict] = {}
    total = 0

    def item(e: dict) -> tuple[dict, Any]:
        key = e.get("id") or e.get("name") or "?"
        it = items.get(key)
        if it is None:
            it = {"id": e.get("id"), "name": e.get("name"), "bought": 0, "sold": 0, "spent": 0, "income": 0, "fees": 0,
                  "profit": 0}
            items[key] = it
        return it, key

    def take(key: Any, n: int) -> int:
        q, cost = lots.get(key), 0
        while q and n > 0:
            lot = q[0]
            k = min(n, lot["n"])
            part = lot["c"] if k == lot["n"] else int(math.floor(lot["c"] * k / lot["n"]))
            cost, n = cost + part, n - k
            lot["n"], lot["c"] = lot["n"] - k, lot["c"] - part
            if lot["n"] <= 0:
                q.pop(0)
        return cost

    for e in ev:
        it, key = item(e)
        n = max(1, _int(e.get("n")))
        g = None
        k = e.get("k")
        if k == "buy":
            lots.setdefault(key, []).append({"n": n, "c": _int(e.get("c"))})
            it["bought"], it["spent"] = it["bought"] + n, it["spent"] + _int(e.get("c"))
        elif k in ("sale", "vend"):
            cost = take(key, n)
            g = _int(e.get("c")) - _int(e.get("cut")) - cost
            it["sold"], it["income"], it["fees"] = it["sold"] + n, it["income"] + _int(e.get("c")), it["fees"] + _int(e.get("cut"))
        elif k in ("exp", "cancel"):
            g = -_int(e.get("dep"))
            it["fees"] = it["fees"] + _int(e.get("dep"))
        if g is not None:
            it["profit"], total = it["profit"] + g, total + g
            if e.get("day"):
                days_[e["day"]] = _n0(days_.get(e["day"])) + g
    tied = 0
    for key, q in lots.items():
        c = sum(lot["c"] for lot in q)
        n = sum(lot["n"] for lot in q)
        tied += c
        if key in items:
            items[key]["held"], items[key]["heldCost"] = n, c
    return {"days": days_, "items": items, "tied": tied, "total": total}


def goal(today: Any, goal_: Any, visible_net: Any = None) -> dict:
    t, g = _int(today), max(0, _int(goal_))
    left = max(0, g - t)
    r: dict[str, Any] = {"pct": min(100, max(0, int(math.floor(t * 100 / g)))) if g > 0 else 100, "left": left}
    if left == 0:
        r["reachable"] = True
    elif visible_net is not None:
        r["reachable"] = _int(visible_net) >= left
    return r


def buy_cost(rows: Iterable[dict], qty: Any) -> int | None:
    left, cost = max(0, _int(qty)), 0
    for r in rows or []:
        if left <= 0:
            break
        k = min(left, _int(r.get("count")))
        cost, left = cost + _int(r.get("unit")) * k, left - k
    return None if left > 0 else cost


def reset_deals(o: dict, R: dict | None = None) -> tuple[list[dict], str | None]:
    R = _R(R)
    out: list[dict] = []
    klass, why = evidence(o.get("ev"), R, "reset")
    if klass != "probable":
        return out, why
    rows, market = o.get("rows") or [], _int(o.get("market"))
    if market <= 0:
        return out, "prix habituel inconnu"
    if market > R["resetMaxUnit"]:
        return out, "pas un petit objet (prix habituel > plafond)"
    sold = _num(o.get("sold"))
    if sold is None or sold < R["resetMinSold"]:
        return out, "se vend trop peu pour réguler"
    if _n0((o.get("ev") or {}).get("sellers")) > R["resetMaxSellers"]:
        return out, "trop de vendeurs sur ce marché"
    lim = int(math.floor(market * R["resetWallPct"] / 100))
    wall: list[dict] = []
    owners: set[str] = set()
    for r in rows:
        if _int(r.get("unit")) >= lim:
            break
        who = r.get("seller") or r.get("owner")
        if not isinstance(who, str) or who == "":
            return out, "vendeurs du mur inconnus"
        owners.update(x for x in who.split(",") if x)
        wall.append(r)
    if not wall:
        return out, "pas de mur bas"
    if len(wall) == len(rows) and o.get("more"):
        return out, "mur trop long (annonces non lues au-delà)"
    if len(owners) > R["resetMaxSellers"]:
        return out, "trop de vendeurs dans le mur"
    nxt = rows[len(wall)] if len(wall) < len(rows) else None
    sell = market
    if nxt is not None and _int(nxt.get("unit")) - 1 < sell:
        sell = _int(nxt.get("unit")) - 1
    if sell <= _int(wall[-1].get("unit")):
        return out, "pas d'écart entre le mur et le prix habituel"
    if o.get("mineLow") is not None and _int(o.get("mineLow")) < sell:
        return out, "ta propre vente est sous le prix de revente"
    com = bool(o.get("commodity"))
    qty = sum(_int(r.get("count")) for r in wall)
    buy = sum((_int(r.get("unit")) * _int(r.get("count")) if com else _int(r.get("buy"))) for r in wall)
    cap = R["resetCap"]
    if o.get("cap") is not None and _int(o.get("cap")) < cap:
        cap = _int(o.get("cap"))
    if buy > cap:
        return out, "mur trop cher (plafond dépassé)"
    d = days(sold, o.get("ahead"), qty)
    if d is None or d > R["resetMaxDays"]:
        return out, "mur trop gros pour ce qui se vend"
    pm = sell_pm(sold, o.get("ahead"), qty, o.get("hours"))
    base = {"id": o.get("id"), "kind": "reset", "class": "regul", "sellUnit": sell, "pm": pm, "days": d,
            "group": "reset:" + _lua_tostring(o.get("id")), "wallQty": qty, "wallBuy": buy, "wallRows": len(wall),
            "sellers": len(owners), "worst": max(0, _int(o.get("vendorUnit"))) * qty - buy}
    neutral = o.get("neutral")

    def mk(take: int, cost: int, r: dict | None) -> dict:
        dep = deposit(o.get("vendorUnit"), take, o.get("hours"), neutral, o.get("depPm"))
        n, gross, c, loss = flip_net(cost, sell, take, dep, pm, neutral)
        dd = {"qty": take, "buy": cost, "buyUnit": int(math.ceil(cost / take)),
              "maxUnit": _int(r.get("unit")) if r else _int(wall[-1].get("unit")), "gross": gross, "cut": c, "dep": dep,
              "depLoss": loss, "fees": c + loss, "net": n, "capital": cost, "auctionID": r.get("auctionID") if r else None,
              "count": r.get("count") if r else None, "commodity": True if com else None}
        dd.update(base)
        return _drop_none(dd)

    if com:
        out.append(mk(qty, buy, None))
    else:
        for r in wall:
            out.append(mk(_int(r.get("count")), _int(r.get("buy")), r))
    total = sum(x["net"] for x in out)
    if total < need(buy, R["minNet"], R["resetMarginPct"]):
        return [], "marge trop faible pour le risque"
    for x in out:
        x["groupNet"], x["groupBuy"] = total, buy
    return out, None


def _lua_tostring(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def cancel_gain(o: dict) -> tuple[int, int, int, int, int] | None:
    n = max(1, _int(o.get("n")))
    pk = sell_pm(o.get("sold"), o.get("aheadKeep"), n, o.get("hours"))
    pn = sell_pm(o.get("sold"), o.get("aheadNew"), n, o.get("hours"))
    if pk is None or pn is None:
        return None
    neutral = o.get("neutral")
    keep_v = int(math.floor(pk * (net(_int(o.get("my")) * n, neutral) + max(0, _int(o.get("dep")))) / 1000))
    dep2 = max(0, _int(o.get("dep2")))
    cxl_v = int(math.floor(pn * (net(_int(o.get("new")) * n, neutral) + dep2) / 1000)) - dep2 - max(0, _int(o.get("cost")))
    return cxl_v - keep_v, keep_v, cxl_v, pk, pn


def cancel_need(new: Any, n: Any, neutral: Any = False, R: dict | None = None) -> int:
    R = _R(R)
    return int(max(R["minNet"], math.ceil(net(_int(new) * max(1, _int(n)), neutral) * R["cancelMinPct"] / 100)))


def craft_spread(o: dict, R: dict | None = None) -> tuple[dict | None, str | None]:
    R = _R(R)
    klass, why = evidence(o.get("ev"), R)
    if klass != "probable":
        return None, why
    cost, made, market = _int(o.get("cost")), max(1, _int(o.get("made"))), _int(o.get("market"))
    if cost <= 0 or market <= 0:
        return None, "prix inconnu"
    pm = sell_pm(o.get("sold"), o.get("ahead"), made, o.get("hours"))
    d = days(o.get("sold"), o.get("ahead"), made)
    if pm is None or d is None:
        return None, "ventes non mesurées"
    dep = deposit(o.get("vendorUnit"), made, o.get("hours"), o.get("neutral"), o.get("depPm"))
    n, gross, c, loss = flip_net(cost, market, made, dep, pm, o.get("neutral"))
    if n < need(cost, R["minNet"], R["marginPct"]):
        return None, "marge trop faible"
    return {"class": "probable", "cost": cost, "made": made, "sellUnit": market, "gross": gross, "cut": c, "dep": dep,
            "depLoss": loss, "fees": c + loss, "net": n, "pm": pm, "days": d}, None


def pattern(b: dict | None, R: dict | None = None) -> tuple[dict | None, str | None]:
    R = _R(R)
    G = H = n = nd = 0
    seen: set[int] = set()
    best: dict | None = None
    for k in sorted((b or {}).keys(), key=lambda x: _n0(x)):
        e = b[k]
        g, h = max(0, _int(e.get("g"))), max(0, _int(e.get("h")))
        if h >= R["patMinHours"]:
            n, G, H = n + 1, G + g, H + h
            wd = int(math.floor(_int(k) / 6))
            if wd not in seen:
                seen.add(wd)
                nd += 1
            if best is None or g * best["h"] > best["g"] * h:
                best = {"key": _int(k), "g": g, "h": h}
    if n < R["patMinBuckets"] or nd < R["patMinDays"]:
        return None, "créneaux mesurés : %d sur %d" % (n, R["patMinBuckets"])
    if G <= 0 or best is None:
        return None, "aucune vente mesurée"
    pm = int(math.floor(best["g"] * H * 1000 / (best["h"] * G)))
    return {"key": best["key"], "wd": best["key"] // 6, "blk": best["key"] % 6, "pm": pm, "n": n,
            "clear": pm >= R["patMinPm"]}, None
