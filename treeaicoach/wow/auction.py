"""Auction house (« hôtel des ventes ») math for WoW Forever + reader of the addon's summary.

The in-game module ``wow_addon/TreeAI/Modules/Auction.lua`` mirrors these formulas EXACTLY (same integer
rounding), so TreeAI's AI and the addon give the same numbers. Everything here is pure, copper-based
(1 po = 100 pa = 10 000 pc), never raises in the app path (readers return {} / [] on bad input).

Fee rules (Classic-family auction house, which WoW Forever keeps — verify in game, the addon prefers the
client's own deposit API when it exists):

* AH cut (« commission »), taken on the sale price when the item sells:
  faction AH (Alliance / Horde)  5 %  ·  neutral (goblin: Baie-du-Butin, Gadgetzan, Long-guet) 15 %.
  ``cut = floor(price × pct / 100)``.
* Deposit (« dépôt »), paid when posting, refunded if the auction sells, LOST if it expires / is cancelled.
  Based on the VENDOR sell price (not on the asking price):
  ``deposit = floor(vendor_unit × count × rate_pct × hours / 200)`` with rate 5 % (faction) / 25 % (neutral)
  per 2 h of listing → 2 h ×1, 8 h ×4, 24 h ×12 (faction 24 h = 60 % of the vendor price, neutral 300 %).
  Sources: Wowhead Classic « Auction House economy » guide (faction 5/20/60 %), Ten Ton Hammer auctioneer
  guide (neutral 25/100/300 %), Blizzard support « How is the auction house fee calculated? »; the 5 % /
  15 % cut is the long-standing Classic rule. If Forever uses 12/24/48 h listings the same per-2 h rate
  applies (×6/×12/×24) — the addon uses ``GetAuctionDeposit``/``C_AuctionHouse.Calculate*Deposit`` first.
* Expected profit of a flip (one listing cycle, as specified):
  ``profit = sell × (1 − cut) − buy − deposit × P(not sold)``  (cut floored as above)
  ROI = profit / buy; break-even = smallest sell price with profit ≥ 0.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Iterable

log = logging.getLogger(__name__)

__all__ = [
    "CUT_PCT", "DEPOSIT_PCT", "ah_cut", "deposit", "net_sale", "expected_profit", "roi", "break_even",
    "median", "low_median", "market_value", "volatility", "daily_volume", "confidence", "p_sell",
    "sell_through_days", "evaluate_flip", "evaluate_vendor_flip", "rank_deals", "thin_market",
    "suggest_post", "realized_profit", "money_text", "auction_summary", "auction_context", "answer_lines",
]

CUT_PCT = {False: 5, True: 15}          # neutral? -> % of the sale price
DEPOSIT_PCT = {False: 5, True: 25}      # neutral? -> % of the vendor price per 2 h of listing
SOLD_SHARE = 0.6                        # share of listings that vanish between scans because they SOLD
LOW_N = 5                               # « median of the lowest N listings »
MAX_DAYS = 14


def _i(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _f(v: Any, default: float | None = None) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _round(x: float) -> int:
    """Round half up (the Lua side does ``floor(x + 0.5)``)."""
    return int(math.floor(x + 0.5))


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


# ------------------------------------------------------------------------------------------- fees
def ah_cut(price: int, neutral: bool = False) -> int:
    """AH commission on a sale: floor(price × 5 %) (15 % at a neutral AH)."""
    return max(0, _i(price)) * CUT_PCT[bool(neutral)] // 100


def deposit(vendor_unit: int, count: int = 1, hours: int = 24, neutral: bool = False) -> int:
    """Classic deposit: floor(vendor × count × rate% × hours / 200)."""
    v, c, h = max(0, _i(vendor_unit)), max(1, _i(count, 1)), max(0, _i(hours, 24))
    return v * c * DEPOSIT_PCT[bool(neutral)] * h // 200


def net_sale(price: int, neutral: bool = False) -> int:
    """What the seller receives for a sale (deposit refunded separately)."""
    return max(0, _i(price)) - ah_cut(price, neutral)


def expected_profit(buy: int, sell: int, dep: int = 0, p: float = 1.0, neutral: bool = False) -> int:
    """sell × (1 − cut) − buy − deposit × P(not sold), rounded half up to the copper."""
    p = _clamp(_f(p, 1.0) or 0.0, 0.0, 1.0)
    return _round(net_sale(sell, neutral) - _i(buy) - _i(dep) * (1.0 - p))


def roi(profit: int, buy: int) -> float | None:
    b = _i(buy)
    return None if b <= 0 else _i(profit) / b


def break_even(buy: int, dep: int = 0, p: float = 1.0, neutral: bool = False) -> int:
    """Smallest sell price (copper) for which expected_profit ≥ 0."""
    p = _clamp(_f(p, 1.0) or 0.0, 0.0, 1.0)
    need = _i(buy) + _i(dep) * (1.0 - p)
    pct = CUT_PCT[bool(neutral)]
    s = max(0, int(math.ceil(need * 100 / (100 - pct))))

    def ok(x: int) -> bool:
        return expected_profit(buy, x, dep, p, neutral) >= 0

    while s > 0 and ok(s - 1):
        s -= 1
    while not ok(s):
        s += 1
    return s


# ------------------------------------------------------------------------------------ statistics
def median(values: Iterable[Any]) -> int | None:
    """Integer median (even count: floor of the mean of the two middle values)."""
    xs = sorted(_i(v) for v in values if _f(v) is not None)
    if not xs:
        return None
    n = len(xs)
    if n % 2:
        return xs[n // 2]
    return (xs[n // 2 - 1] + xs[n // 2]) // 2


def low_median(unit_prices: Iterable[Any], n: int = LOW_N) -> int | None:
    """Median of the N lowest unit buyouts of one scan (robust « current price »)."""
    xs = sorted(_i(v) for v in unit_prices if _i(v) > 0)
    return median(xs[:max(1, n)])


def _days(points: Iterable[Any]) -> list[dict]:
    """Daily points as dicts {day, min, med, qty, n, gone, hrs} (lists accepted in that order)."""
    out = []
    keys = ("day", "min", "med", "qty", "n", "gone", "hrs")
    for pt in points or []:
        if isinstance(pt, dict):
            d = {k: _i(pt.get(k)) for k in keys}
        elif isinstance(pt, (list, tuple)):
            d = {k: _i(pt[i]) if i < len(pt) else 0 for i, k in enumerate(keys)}
        else:
            continue
        if d["med"] > 0:
            out.append(d)
    out.sort(key=lambda d: d["day"])
    return out[-MAX_DAYS:]


def market_value(points: Iterable[Any]) -> int | None:
    """Robust market value = median over days of the daily « median of lowest N »."""
    return median(d["med"] for d in _days(points))


def volatility(points: Iterable[Any]) -> float:
    """Relative σ: 1.4826 × MAD(daily med) / market (≥ 3 days); fewer days: max(0.10, (med−min)/med)."""
    ds = _days(points)
    if not ds:
        return 0.5
    if len(ds) < 3:
        last = ds[-1]
        return round(max(0.10, (last["med"] - last["min"]) / last["med"]), 4)
    mv = median(d["med"] for d in ds) or 1
    mad = median(abs(d["med"] - mv) for d in ds) or 0
    return round(1.4826 * mad / mv, 4)


def daily_volume(points: Iterable[Any]) -> float | None:
    """Estimated units SOLD per day = Σ vanished units / Σ hours between scans × 24 × 0.6."""
    ds = _days(points)
    hrs = sum(d["hrs"] for d in ds)
    if hrs <= 0:
        return None
    return round(sum(d["gone"] for d in ds) / hrs * 24 * SOLD_SHARE, 2)


def confidence(points: Iterable[Any], age_days: float = 0.0) -> float:
    """0..1: days of history (½), scans (¼), depth of the market (¼); halved if data > 3 days old;
    × (1 − min(0.5, volatility))."""
    ds = _days(points)
    if not ds:
        return 0.0
    nd = len(ds)
    scans = sum(max(1, d["n"]) for d in ds)
    avg_qty = sum(d["qty"] for d in ds) / nd
    c = 0.5 * min(1.0, nd / 4) + 0.25 * min(1.0, scans / 4) + 0.25 * min(1.0, avg_qty / 10)
    if (_f(age_days, 0.0) or 0.0) > 3:
        c *= 0.5
    c *= 1 - min(0.5, volatility(ds))
    return round(c, 2)


def p_sell(volume: float | None, qty_ahead: int = 0, count: int = 1, hours: int = 24) -> float:
    """P(our listing sells within its duration): hours/24 ÷ days needed to sell everything in front of us
    plus our stack, clamped to [0.05, 0.95]; unknown volume → 0.5."""
    v = _f(volume)
    if v is None:
        return 0.5
    if v <= 0:
        return 0.05
    need = (max(0, _i(qty_ahead)) + max(1, _i(count, 1))) / v
    return round(_clamp((max(1, _i(hours, 24)) / 24) / need, 0.05, 0.95), 4)


def sell_through_days(volume: float | None, qty_ahead: int = 0, count: int = 1) -> float | None:
    v = _f(volume)
    if not v or v <= 0:
        return None
    return round((max(0, _i(qty_ahead)) + max(1, _i(count, 1))) / v, 2)


# ------------------------------------------------------------------------------------------ deals
def evaluate_flip(buyout: int, count: int, market: int, vol: float, volume: float | None, conf: float,
                  vendor_unit: int = 0, qty_ahead: int = 0, neutral: bool = False, hours: int = 24) -> dict:
    """Buy one listing (whole stack) and repost at a conservative target market × (1 − ½σ)."""
    count = max(1, _i(count, 1))
    buyout = _i(buyout)
    target_unit = max(1, int(math.floor(_i(market) * (1 - 0.5 * (_f(vol, 0.0) or 0.0)))))
    sell = target_unit * count
    dep = deposit(vendor_unit, count, hours, neutral)
    p = p_sell(volume, qty_ahead, count, hours)
    profit = expected_profit(buyout, sell, dep, p, neutral)
    c = _clamp(_f(conf, 0.0) or 0.0, 0.0, 1.0)
    adj = int(math.floor(profit * c)) if profit > 0 else profit
    unit = buyout // count
    return {
        "kind": "flip", "buy": buyout, "count": count, "unit": unit, "sell": sell, "sellUnit": target_unit,
        "deposit": dep, "p": p, "profit": profit, "adj": adj, "roi": roi(profit, buyout),
        "breakEven": break_even(buyout, dep, p, neutral),
        "discount": round(1 - unit / _i(market), 4) if _i(market) > 0 else 0.0,
        "days": sell_through_days(volume, qty_ahead, count),
        "score": round(adj / buyout * 10000, 2) if buyout > 0 else 0.0,  # risk-adj. profit per 1 po invested
        "conf": c,
    }


def evaluate_vendor_flip(buyout: int, count: int, vendor_unit: int) -> dict | None:
    """Buyout below the vendor price: guaranteed profit (no AH fee when selling to a vendor)."""
    count = max(1, _i(count, 1))
    gain = _i(vendor_unit) * count - _i(buyout)
    if _i(buyout) <= 0 or gain <= 0:
        return None
    return {"kind": "vendor", "buy": _i(buyout), "count": count, "unit": _i(buyout) // count,
            "sell": _i(vendor_unit) * count, "deposit": 0, "p": 1.0, "profit": gain, "adj": gain,
            "roi": roi(gain, buyout), "breakEven": _i(buyout), "discount": 0.0, "days": 0.0,
            "score": round(gain / _i(buyout) * 10000, 2), "conf": 1.0}


def rank_deals(deals: Iterable[dict], min_margin: float = 0.20, min_profit: int = 5000,
               budget: int | None = None) -> list[dict]:
    """Keep vendor flips (any profit) and flips with discount ≥ min_margin and risk-adjusted profit ≥
    min_profit, within budget; sort by score (profit per gold) desc, then sell-through days asc."""
    out = []
    for d in deals or []:
        if not isinstance(d, dict):
            continue
        if budget is not None and _i(d.get("buy")) > _i(budget):
            continue
        if d.get("kind") == "vendor":
            if _i(d.get("profit")) > 0:
                out.append(d)
        elif (_f(d.get("discount"), 0.0) or 0.0) >= min_margin and _i(d.get("adj")) >= min_profit:
            out.append(d)
    out.sort(key=lambda d: (-(_f(d.get("score"), 0.0) or 0.0),
                            _f(d.get("days"), 999.0) if d.get("days") is not None else 999.0))
    return out


def thin_market(unit_prices: Iterable[Any]) -> bool:
    """« Reset » risk: ≤ 3 listings, or ≤ 6 listings with the 2nd lowest ≥ 1.5 × the lowest."""
    xs = sorted(_i(v) for v in unit_prices if _i(v) > 0)
    if not xs:
        return False
    if len(xs) <= 3:
        return True
    return len(xs) <= 6 and xs[1] * 2 >= xs[0] * 3


# ---------------------------------------------------------------------------------------- selling
def suggest_post(count: int, vendor_unit: int, market: int | None, vol: float, volume: float | None,
                 competitors: Iterable[Any] = (), neutral: bool = False, hours: int = 24) -> dict:
    """Suggested unit price for posting ``count`` units.

    competitors: (unit_price, stack_count) of OTHER players' listings. Undercut the lowest by 1 pc but
    never below floor = max(market − σ, price whose AH net still beats the vendor); no market → no price.
    """
    count = max(1, _i(count, 1))
    vendor_total = _i(vendor_unit) * count
    comp = sorted(((_i(u), max(1, _i(c, 1))) for u, c in (competitors or []) if _i(u) > 0))
    mv = _i(market) if market else 0
    sigma = int(math.floor(mv * (_f(vol, 0.0) or 0.0)))
    dep = deposit(vendor_unit, count, hours, neutral)
    res: dict[str, Any] = {"count": count, "deposit": dep, "vendor": vendor_total, "market": mv or None,
                           "unit": None, "net": None, "p": None, "better": "vendor" if vendor_total else None,
                           "reason": "pas de prix de marché", "split": None, "lowest": comp[0][0] if comp else None}
    if mv <= 0 and not comp:
        return res
    # stacks: post in the size buyers see most (mode of competitor stack sizes) when smaller than ours
    if comp:
        sizes: dict[int, int] = {}
        for _, c in comp:
            sizes[c] = sizes.get(c, 0) + 1
        typical = max(sizes.items(), key=lambda kv: (kv[1], -kv[0]))[0]
        if typical < count:
            res["split"] = typical
    floor_unit = max(1, mv - sigma) if mv else 1
    # beat the vendor: unit price u with net(u×count) − dep×(1−p) ≥ vendor_total
    p0 = p_sell(volume, 0, count, hours)
    vend_unit = -(-break_even(vendor_total, dep, p0, neutral) // count)
    floor_unit = max(floor_unit, vend_unit if vendor_total else 1)
    if comp:
        unit = comp[0][0] - 1
        reason = "1 pc sous le moins cher"
        if unit < floor_unit:
            unit = floor_unit
            reason = "concurrent trop bas : ne pas sous-coter sous le plancher"
    else:
        unit = mv
        reason = "aucun concurrent : prix du marché"
    unit = max(unit, floor_unit)
    ahead = sum(c for u, c in comp if u < unit)
    p = p_sell(volume, ahead, count, hours)
    ah_net = _round(net_sale(unit * count, neutral) - dep * (1 - p))
    res.update(unit=unit, net=ah_net, p=p, reason=reason,
               better="vendor" if vendor_total >= ah_net else "ah")
    return res


# ----------------------------------------------------------------------------------------- ledger
def realized_profit(events: Iterable[dict]) -> dict:
    """Ledger → realized profit with average cost. events: {type: buy|sell, item, qty, money[, cut], day}.
    A sale's cost basis = average buy cost × min(qty, units held); sales of never-bought items are pure
    revenue (counted, flagged ``noCost``)."""
    items: dict[str, dict] = {}
    days: dict[str, int] = {}
    total = 0
    for e in events or []:
        if not isinstance(e, dict):
            continue
        it = items.setdefault(str(e.get("item") or "?"),
                              {"held": 0, "cost": 0, "profit": 0, "sold": 0, "bought": 0, "noCost": 0})
        q = max(1, _i(e.get("qty"), 1))
        money = _i(e.get("money"))
        if e.get("type") == "buy":
            it["held"] += q
            it["cost"] += money
            it["bought"] += q
        elif e.get("type") == "sell":
            net = money - _i(e.get("cut"))
            covered = min(q, it["held"])
            basis = it["cost"] * covered // it["held"] if it["held"] > 0 else 0
            it["cost"] -= basis
            it["held"] -= covered
            it["noCost"] += q - covered
            it["sold"] += q
            gain = net - basis
            it["profit"] += gain
            total += gain
            day = str(e.get("day") or "")
            days[day] = days.get(day, 0) + gain
    return {"items": items, "days": days, "total": total}


# ----------------------------------------------------------------------------------------- reader
def money_text(copper: Any) -> str:
    """Copper → « 1 po 20 pa 5 pc » (sign kept)."""
    c = _i(copper)
    sign, c = ("-" if c < 0 else ""), abs(c)
    g, s, cp = c // 10000, (c // 100) % 100, c % 100
    parts = [f"{g} po"] if g else []
    if s:
        parts.append(f"{s} pa")
    if cp or not parts:
        parts.append(f"{cp} pc")
    return sign + " ".join(parts)


def _list(v: Any) -> list:
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        ks = sorted(k for k in v if isinstance(k, int))
        return [v[k] for k in ks]
    return []


def auction_summary(db_or_char: Any) -> dict:
    """``summary.auction`` from TreeAIDB, a char entry, or a summary table ({} if absent)."""
    try:
        d = db_or_char if isinstance(db_or_char, dict) else {}
        if "auction" in d and isinstance(d["auction"], dict):
            return d["auction"]
        if isinstance(d.get("summary"), dict):
            return auction_summary(d["summary"])
        chars = d.get("chars")
        if isinstance(chars, dict) and chars:
            best = max((c for c in chars.values() if isinstance(c, dict)),
                       key=lambda c: _i(((c.get("summary") or {}).get("auction") or {}).get("updated")),
                       default=None)
            return auction_summary(best) if best else {}
    except Exception:
        log.debug("auction_summary failed", exc_info=True)
    return {}


def _deal_text(d: dict) -> str:
    name = str(d.get("name") or d.get("item") or "?")
    cnt = _i(d.get("count"), 1)
    label = f"{name} x{cnt}" if cnt > 1 else name
    if d.get("kind") == "vendor":
        return f"{label} : acheter {money_text(d.get('buy'))}, revendre au marchand → +{money_text(d.get('profit'))} garanti"
    r = _f(d.get("roi"))
    roi_t = f", ROI {round(r * 100)} %" if r is not None else ""
    conf = _f(d.get("conf"))
    conf_t = f", confiance {round(conf * 100)} %" if conf is not None else ""
    return (f"{label} : acheter {money_text(d.get('buy'))}, revendre {money_text(d.get('sell'))} → "
            f"+{money_text(d.get('profit'))}{roi_t}{conf_t}")


def auction_context(db_or_char: Any) -> dict:
    """Compact French facts for the AI prompt (« quoi revendre ? »). {} when the addon has no AH data."""
    try:
        a = auction_summary(db_or_char)
        if not a:
            return {}
        out: dict[str, Any] = {}
        deals = [_deal_text(d) for d in _list(a.get("deals")) if isinstance(d, dict)][:5]
        if deals:
            out["affaires"] = deals
        sells = []
        for s in _list(a.get("sell"))[:5]:
            if not isinstance(s, dict):
                continue
            if s.get("better") == "vendor":
                sells.append(f"{s.get('name')} : mieux au marchand ({money_text(s.get('vendor'))})")
            elif s.get("unit"):
                sells.append(f"{s.get('name')} x{_i(s.get('count'), 1)} : poster à {money_text(s.get('unit'))}/u "
                             f"(net {money_text(s.get('net'))}, marchand {money_text(s.get('vendor'))})")
        if sells:
            out["a_vendre"] = sells
        r = a.get("realized") if isinstance(a.get("realized"), dict) else {}
        if r:
            out["profit_realise"] = (f"aujourd'hui {money_text(r.get('today'))}, 7 j {money_text(r.get('week'))}, "
                                     f"total {money_text(r.get('total'))}")
        if a.get("budget"):
            out["budget"] = f"{money_text(a.get('budget'))} (dépensé aujourd'hui {money_text(a.get('spentToday'))})"
        if a.get("scanAge") is not None:
            out["dernier_scan"] = f"il y a {round(_i(a.get('scanAge')) / 60)} min"
        if a.get("neutral"):
            out["hv"] = "neutre (commission 15 %)"
        warn = [str(w) for w in _list(a.get("thin"))][:3]
        if warn:
            out["marches_minces"] = warn
        return out
    except Exception:
        log.debug("auction_context failed", exc_info=True)
        return {}


def answer_lines(db_or_char: Any) -> list[str]:
    """Short French lines for the WoW page / a voice answer."""
    ctx = auction_context(db_or_char)
    if not ctx:
        return ["Pas encore de données d'hôtel des ventes : ouvre l'HV, clique « Scanner », puis /reload."]
    lines = []
    for k, title in (("affaires", "À acheter"), ("a_vendre", "À vendre")):
        for t in ctx.get(k, [])[:3]:
            lines.append(f"{title} — {t}")
    if ctx.get("profit_realise"):
        lines.append("Profit réalisé : " + ctx["profit_realise"])
    return lines
