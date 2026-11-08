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
    "LIQ", "demand_class", "sold_between", "liquidity", "max_quantity", "stop_loss", "item_cap", "safe_flip",
    "why_none", "cancel_advice", "scan_age", "STALE_TEXT",
    "MIN_NET", "MIN_ROI", "min_net", "undercut", "post_quantity", "vendor_to_ah", "craft_profit",
]

CUT_PCT = {False: 5, True: 15}          # neutral? -> % of the sale price
DEPOSIT_PCT = {False: 5, True: 25}      # neutral? -> % of the vendor price per 2 h of listing
SOLD_SHARE = 0.6                        # share of listings that vanish between scans because they SOLD
LOW_N = 5                               # « median of the lowest N listings »
MAX_DAYS = 14
MIN_VOLUME_HOURS = 12                   # (4.1) volume measured only over ≥ 12 h of scans (else unknown)


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
    keys = ("day", "min", "med", "qty", "n", "gone", "hrs", "sel")
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
    """Estimated units SOLD per day = Σ vanished units / Σ hours between scans × 24 × 0.6.

    (4.1) None (unknown) until scans span ≥ 12 h: « 0 sold in 20 minutes » is not evidence of no demand."""
    ds = _days(points)
    hrs = sum(d["hrs"] for d in ds)
    if hrs < MIN_VOLUME_HOURS:
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


MIN_NET, MIN_ROI = 100, 0.15            # (4.1 AHPROFIT) a flip needs net ≥ max(1 pa, 15 % of the stake)


def min_net(buy: Any, min_profit: Any = None) -> int:
    """Smallest acceptable net profit for a purchase of ``buy`` copper: max(min_profit or 1 pa, ⌈15 % × buy⌉)."""
    m = _i(min_profit)
    return max(m if m > 0 else MIN_NET, math.ceil(max(0, _i(buy)) * MIN_ROI - 1e-9))


def rank_deals(deals: Iterable[dict], min_margin: float = 0.15, min_profit: int = MIN_NET,
               budget: int | None = None) -> list[dict]:
    """Keep vendor flips and flips (discount ≥ min_margin, risk-adjusted profit ≥ min_profit) whose net profit
    ≥ min_net(buy), within budget; sort by score (profit per gold) desc, then sell-through days asc."""
    out = []
    for d in deals or []:
        if not isinstance(d, dict):
            continue
        if budget is not None and _i(d.get("buy")) > _i(budget):
            continue
        need = min_net(d.get("buy"), min_profit)
        if d.get("kind") == "vendor":
            if _i(d.get("profit")) >= need:
                out.append(d)
        elif ((_f(d.get("discount"), 0.0) or 0.0) >= min_margin and _i(d.get("adj")) >= min_profit
              and _i(d.get("profit")) >= need):
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


# ------------------------------------------------------------------------- liquidity (« ça se vend ? »)
# An item is only recommended for a flip when there is EVIDENCE it sells (see research/ah2.md):
# listings vanishing between scans (= sales, reposts at a lower price removed), several days and scans of
# data, a stable price (median + MAD over days, never one low outlier), several sellers, Auctionator in
# agreement when present, and a stack we can resell within a few days. Demand classes get a small prior.
LIQ = {
    "min_days": 3, "min_days_demand": 2, "min_scans": 4, "min_hours": 12, "min_sold": 1.0,
    "min_sold_single": 0.5, "min_sellers": 3, "max_vol": 0.30, "atr_gap": 0.35, "stale_days": 3,
    "min_conf": 0.70, "max_tts": 3.0, "min_roi": 0.15, "qty_days": 3, "stop_pct": 15, "cap_pct": 20,
}
NONE_TEXT = "Aucun achat-revente sûr pour l'instant"
STALE_TEXT = "Pas d'achat-revente : pas de scan récent de l'HV"
REASON_LABEL = {
    "nohist": "jamais vu en scan", "rare": "objet rare, presque jamais vendu", "short": "historique trop court",
    "scans": "trop peu de scans", "unmeasured": "ventes pas encore mesurées", "slow": "se vend trop peu",
    "unstable": "prix instable", "falling": "prix en chute", "sellers": "trop peu de vendeurs",
    "atr": "Auctionator pas d'accord", "stale": "données trop vieilles", "conf": "confiance trop basse",
    "notcheap": "pas assez sous le marché", "stack": "pile plus grosse que ce qui se vend",
    "tts": "trop long à revendre", "margin": "marge trop faible après commission et dépôt",
}


def _dec(x: float | None) -> str:
    return "0" if x is None else f"{x:.1f}".replace(".", ",")


def demand_class(class_id: Any, quality: Any = 1, stack: Any = 1, ilvl: Any = 0, min_level: Any = 0,
                 level: Any = 0) -> tuple[str, float]:
    """(label, prior) from GetItemInfo's classID: things players always need get a small bonus."""
    c, q, lvl = _i(class_id, -1), _i(quality, 1), _i(level)
    if c == 0:
        return "consommable", 0.10
    if c == 1:
        return "sac", 0.10
    if c == 6:
        return "munitions", 0.10
    if c == 7:
        return "artisanat", 0.10 if lvl <= 0 or _i(ilvl) <= lvl + 20 else 0.03
    if c == 5:
        return "composant", 0.05
    if c in (2, 4):
        if q == 2 and lvl > 0 and lvl - 5 <= _i(min_level) <= lvl + 10:
            return "équipement de leveling", 0.05
        return "équipement", 0.0
    return "autre", 0.0


def _sig_key(k: Any) -> tuple[int, int]:
    a, _, b = str(k).partition(":")
    return _i(a), _i(b)


def sold_between(prev: dict, cur: dict) -> int:
    """Units SOLD between two scans: vanished units (signature « unit:count » → qty) minus reposts
    (a new listing with the same stack size at a lower price than a vanished one = cancel + undercut)."""
    gone, dis = 0, []
    for k, q in (prev or {}).items():
        still = _i((cur or {}).get(k))
        if still < _i(q):
            u, c = _sig_key(k)
            dis.append([u, c, _i(q) - still])
            gone += _i(q) - still
    new = []
    for k, q in (cur or {}).items():
        inc = _i(q) - _i((prev or {}).get(k))
        if inc > 0:
            u, c = _sig_key(k)
            new.append([u, c, inc])
    dis.sort(key=lambda e: (e[1], -e[0]))
    new.sort(key=lambda e: (e[0], e[1]))
    matched = 0
    for n in new:
        for e in dis:
            if n[2] <= 0:
                break
            if e[1] == n[1] and e[0] > n[0] and e[2] > 0:
                m = min(e[2], n[2])
                e[2] -= m
                n[2] -= m
                matched += m
    return gone - matched


def liquidity(points: Iterable[Any], stack: Any = 1, quality: Any = 1, prior: float = 0.0,
              atr: Any = None, age_days: float = 0.0) -> dict:
    """Does this item really sell? → {ok, conf, reasons:[{code,text}], market, vol, sold, days, scans,
    hours, sellers, gone}. ``ok`` only when every gate passes and confidence ≥ LIQ["min_conf"]."""
    L = LIQ
    ds = _days(points)
    res: dict[str, Any] = {"ok": False, "conf": 0.0, "reasons": [], "market": 0, "vol": 0.5, "sold": None,
                           "days": len(ds), "scans": 0, "hours": 0, "sellers": 0, "gone": 0}
    reasons = res["reasons"]

    def why(code: str, text: str) -> None:
        reasons.append({"code": code, "text": text})

    if not ds:
        why("nohist", "jamais vu dans un scan TreeAI")
        return res
    nd = len(ds)
    scans = sum(max(1, d["n"]) for d in ds)
    hours = sum(d["hrs"] for d in ds)
    gone = sum(d["gone"] for d in ds)
    sold = daily_volume(ds)
    sellers = max(d["sel"] for d in ds)
    mv = market_value(ds) or 0
    vol = volatility(ds)
    single = _i(stack, 1) <= 1
    p = _f(prior, 0.0) or 0.0
    res.update(market=mv, vol=vol, sold=sold, scans=scans, hours=hours, sellers=sellers, gone=gone)
    if single and _i(quality, 1) >= 2 and (gone < 2 or nd <= 1):
        why("rare", "objet rare : moins de 2 ventes vues")
    need = L["min_days_demand"] if p >= 0.10 else L["min_days"]
    if nd < need:
        why("short", f"historique trop court ({nd} j sur {need})")
    if scans < L["min_scans"]:
        why("scans", f"trop peu de scans ({scans} sur {L['min_scans']})")
    min_sold = L["min_sold_single"] if single else L["min_sold"]
    if hours < L["min_hours"]:
        why("unmeasured", "ventes pas encore mesurées (il faut des scans espacés de quelques heures)")
    elif sold is None or sold < min_sold:
        why("slow", f"se vend peu ({_dec(sold)} par jour)")
    if vol > L["max_vol"]:
        why("unstable", f"prix instable (±{_round(vol * 100)} %)")
    if mv > 0 and ds[-1]["med"] < mv * (1 - L["max_vol"]):
        why("falling", "prix en chute")
    if 0 < sellers < L["min_sellers"]:
        why("sellers", f"trop peu de vendeurs ({sellers})")
    atr_v, atr_ok = _i(atr), False
    if atr_v > 0 and mv > 0:
        atr_ok = abs(atr_v - mv) / mv <= L["atr_gap"]
        if not atr_ok:
            why("atr", f"Auctionator pas d'accord ({money_text(atr_v)} contre {money_text(mv)})")
    c = (0.25 * min(1.0, nd / 5) + 0.15 * min(1.0, scans / 8) + 0.25 * min(1.0, (sold or 0) / (3 * min_sold))
         + 0.20 * (1 - min(1.0, vol / 0.4)) + (0.15 * min(1.0, sellers / 5) if sellers > 0 else 0.075)
         + p + (0.05 if atr_ok else 0.0))
    age = _f(age_days, 0.0) or 0.0
    if age > L["stale_days"]:
        c *= 0.5
        why("stale", f"données vieilles de {int(age)} j")
    c = _round(_clamp(c, 0.0, 1.0) * 100) / 100
    res["conf"] = c
    if not reasons and c < L["min_conf"]:
        why("conf", f"confiance trop basse ({_round(c * 100)} %)")
    res["ok"] = not reasons
    return res


def max_quantity(sold_per_day: Any, days: int | None = None, held: Any = 0) -> int:
    """Never hold more than what the market absorbed in the last K days (minus what we already hold)."""
    v = _f(sold_per_day)
    if not v or v <= 0:
        return 0
    k = LIQ["qty_days"] if days is None else _i(days)
    return max(0, int(math.floor(v * k)) - max(0, _i(held)))


def stop_loss(buy_unit: Any, vendor_unit: Any = 0, neutral: bool = False) -> dict:
    """Stop-loss: if the market falls under ``unit`` (buy − 15 %, never under the vendor price) or the item
    is still unsold after 2 listings, sell at that price; ``loss`` = max loss per unit after the cut."""
    b = max(0, _i(buy_unit))
    stop = max(max(0, _i(vendor_unit)), b * (100 - LIQ["stop_pct"]) // 100)
    return {"unit": stop, "loss": max(0, b - net_sale(stop, neutral))}


def item_cap(total_gold: Any, pct: Any = None, budget: Any = None) -> int:
    """Most gold allowed in ONE item: pct % of all our gold (and never more than the budget)."""
    cap = max(0, _i(total_gold)) * _i(LIQ["cap_pct"] if pct is None else pct) // 100
    return cap if budget is None else min(cap, max(0, _i(budget)))


def safe_flip(buyout: Any, count: Any, liq: dict, vendor_unit: Any = 0, qty_ahead: Any = 0, held: Any = 0,
              neutral: bool = False, hours: int = 24, min_margin: float = 0.15,
              min_profit: int = MIN_NET) -> tuple[dict | None, dict | None]:
    """(deal, None) when buying this listing is a SAFE flip, else (None, {code, text})."""
    L = LIQ
    if not liq or not liq.get("ok"):
        r = (liq or {}).get("reasons") or [{"code": "nohist", "text": REASON_LABEL["nohist"]}]
        return None, r[0]
    count, buyout = max(1, _i(count, 1)), _i(buyout)
    mv = _i(liq.get("market"))
    unit = buyout // count
    if mv <= 0 or round(1 - unit / mv, 4) < min_margin:
        return None, {"code": "notcheap", "text": f"pas assez sous le marché ({money_text(mv)}/u)"}
    maxq = max_quantity(liq.get("sold"), None, held)
    if count > maxq:
        return None, {"code": "stack", "text": f"pile de {count} : il s'en vend ~{maxq} en {L['qty_days']} j"}
    d = evaluate_flip(buyout, count, mv, _f(liq.get("vol"), 0.0) or 0.0, liq.get("sold"),
                      _f(liq.get("conf"), 0.0) or 0.0, vendor_unit, qty_ahead, neutral, hours)
    if d["days"] is None or d["days"] > L["max_tts"]:
        return None, {"code": "tts", "text": f"trop long à revendre (~{_dec(d['days'])} j)"}
    need = min_net(buyout, min_profit)
    if d["profit"] < need or (d["roi"] or 0) < L["min_roi"]:
        return None, {"code": "margin", "text": f"marge trop faible ({money_text(d['profit'])} net, il faut {money_text(need)})"}
    d.update(safe=True, maxQty=maxq, sold=liq.get("sold"), stop=stop_loss(unit, vendor_unit, neutral))
    return d, None


def why_none(rejects: dict, candidates: int) -> list[str]:
    """The « why » under « Aucun achat-revente sûr pour l'instant »: {code: count} of apparent deals refused."""
    if _i(candidates) <= 0:
        return ["Aucune annonce assez sous le prix du marché parmi les objets suivis."]
    top = sorted(((k, _i(v)) for k, v in (rejects or {}).items() if _i(v) > 0), key=lambda kv: (-kv[1], kv[0]))
    out = [f"{_i(candidates)} affaire(s) apparente(s) écartée(s) :"]
    out += [f"{REASON_LABEL.get(k, k)} ({n})" for k, n in top[:3]]
    return out


def cancel_advice(my_unit: Any, count: Any, competitors: Iterable[Any], liq: dict | None, vendor_unit: Any = 0,
                  neutral: bool = False, hours: int = 24) -> dict:
    """Cancel one of MY beaten auctions? {action: none|keep|cancel, state, text[, repost, gain]}.
    Never without safe data; repost always ≤ lowest − 1 pc; an abnormal lowest (< market − 30 % or < 1/10 of my
    price) → keep; cancel only when the expected gain after the lost deposit, the new deposit at risk and the
    cut beats keeping by ≥ max(1 pa, 5 % of the net)."""
    count, mine = max(1, _i(count, 1)), _i(my_unit)
    if not liq or not liq.get("ok"):
        return {"action": "none", "state": "?", "text": "pas assez de données de prix : laisser"}
    comp = sorted((_i(u), max(1, _i(c, 1))) for u, c in (competitors or []) if _i(u) > 0)
    low = comp[0][0] if comp else None
    if low is None or low >= mine:
        return {"action": "keep", "state": "ok",
                "text": "le moins cher : garder" if low is not None else "aucun concurrent : garder"}
    mv, vol = _i(liq.get("market")), _f(liq.get("vol"), 0.0) or 0.0
    if low * 10 < mine:
        return {"action": "keep", "state": "battue",
                "text": f"laisser : ton prix est plus de 10 fois le plus bas ({money_text(low)}/u), vérifie l'objet"}
    if low < mv * (1 - LIQ["max_vol"]):
        return {"action": "keep", "state": "battue",
                "text": f"laisser : {money_text(low)}/u est anormalement bas (marché {money_text(mv)}/u)"}
    dep = deposit(vendor_unit, count, hours, neutral)
    floor_u = max(1, mv - int(math.floor(mv * vol)))
    vt = _i(vendor_unit) * count
    if vt > 0:
        floor_u = max(floor_u, -(-break_even(vt, dep, p_sell(liq.get("sold"), 0, count, hours), neutral) // count))
    repost = low - 1
    if repost < floor_u:
        return {"action": "keep", "state": "battue", "text": f"laisser : passer sous {money_text(low)}/u ne rapporte "
                                                              f"rien (plancher {money_text(floor_u)}/u)"}
    ahead = sum(c for u, c in comp if u < mine)
    pk, pn = p_sell(liq.get("sold"), ahead, count, hours), p_sell(liq.get("sold"), 0, count, hours)
    keep_v = pk * net_sale(mine * count, neutral) - dep * (1 - pk)
    cancel_v = pn * net_sale(repost * count, neutral) - dep * (1 - pn) - dep
    gain = _round(cancel_v - keep_v)
    need = max(100, net_sale(repost * count, neutral) * 5 // 100)
    if gain < need:
        txt = (f"laisser : annuler et reposter ne rapporte que {money_text(gain)}" if gain > 0 else
               "laisser : annuler (dépôt perdu) coûterait plus qu'attendre")
        return {"action": "keep", "state": "battue", "gain": gain, "text": txt}
    return {"action": "cancel", "state": "battue", "repost": repost, "gain": gain,
            "text": f"annuler, reposter {money_text(repost)}/u (+{money_text(gain)} espéré, dépôt "
                    f"{money_text(dep)} perdu compté)"}


# ---------------------------------------------------------------------------------------- selling
def undercut(lowest: Any, pct: Any = 0) -> int:
    """Our price under the lowest competitor: 1 pc under (pct 0) or pct % under (at least 1 pc)."""
    low = _i(lowest)
    return low - max(1, low * max(0, _i(pct)) // 100)


def suggest_post(count: int, vendor_unit: int, market: int | None, vol: float, volume: float | None,
                 competitors: Iterable[Any] = (), neutral: bool = False, hours: int = 24,
                 undercut_pct: int = 0) -> dict:
    """Suggested unit price for posting ``count`` units.

    competitors: (unit_price, stack_count) of OTHER players' listings. Undercut the lowest (``undercut``) but
    never below floor = max(market − σ, price whose AH net still beats the vendor); no market → no price.
    (4.1) ``ev`` = honest expectation: sold (p) → net; unsold → item back, deposit lost, then the vendor;
    ``better`` = "ah" only when ev beats the vendor.
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
        unit = undercut(comp[0][0], undercut_pct)
        reason = f"{_i(undercut_pct)} % sous le moins cher" if _i(undercut_pct) > 0 else "1 pc sous le moins cher"
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
    ev = _round(p * net_sale(unit * count, neutral) + (1 - p) * (vendor_total - dep))
    res.update(unit=unit, net=ah_net, ev=ev, p=p, reason=reason,
               better="vendor" if vendor_total >= ev else "ah")
    return res


def post_quantity(have: Any, sold_per_day: Any = None, hours: int = 24, listed: Any = 0, split: Any = None) -> int:
    """(4.1) How many to post: what sells during ONE listing (sold/day × hours/24, minus what is already
    listed); the whole stack when sales were never measured; never more than the usual competitor stack."""
    have = max(1, _i(have, 1))
    v = _f(sold_per_day)
    q = have
    if v is not None and v > 0:
        q = min(have, max(0, math.ceil(v * max(1, _i(hours, 24)) / 24) - max(0, _i(listed))))
    if split and _i(split) > 0 and q > _i(split):
        q = _i(split)
    return q


def _resale(liq: dict) -> int:
    return max(1, int(math.floor(_i(liq.get("market")) * (1 - 0.5 * (_f(liq.get("vol"), 0.0) or 0.0)))))


def vendor_to_ah(buy_unit: Any, liq: dict | None, vendor_sell: Any = 0, neutral: bool = False, hours: int = 24,
                 held: Any = 0, min_profit: Any = None) -> dict | None:
    """(4.1) Buy at a merchant, resell at the AH: only with sales evidence, quantity ≤ demand, net ≥ min_net."""
    if not liq or not liq.get("ok") or _i(buy_unit) <= 0:
        return None
    qty = max_quantity(liq.get("sold"), None, held)
    if qty <= 0:
        return None
    sell_unit = _resale(liq)
    dep = deposit(vendor_sell, qty, hours, neutral)
    p = p_sell(liq.get("sold"), 0, qty, hours)
    cost = _i(buy_unit) * qty
    profit = expected_profit(cost, sell_unit * qty, dep, p, neutral)
    if profit < min_net(cost, min_profit):
        return None
    return {"kind": "vendorAH", "buyUnit": _i(buy_unit), "sellUnit": sell_unit, "count": qty, "buy": cost,
            "sell": sell_unit * qty, "deposit": dep, "p": p, "profit": profit,
            "days": sell_through_days(liq.get("sold"), 0, qty), "sold": liq.get("sold")}


def craft_profit(cost: Any, made: Any, liq: dict | None, vendor_sell: Any = 0, neutral: bool = False,
                 hours: int = 24, min_profit: Any = None) -> dict | None:
    """(4.1) Craft (reagent cost of ONE craft making ``made`` items) and sell at the AH: same honest rules."""
    if not liq or not liq.get("ok") or _i(cost) <= 0:
        return None
    made = max(1, _i(made, 1))
    sell_unit = _resale(liq)
    dep = deposit(vendor_sell, made, hours, neutral)
    p = p_sell(liq.get("sold"), 0, made, hours)
    profit = expected_profit(_i(cost), sell_unit * made, dep, p, neutral)
    if profit < min_net(cost, min_profit):
        return None
    return {"kind": "craft", "cost": _i(cost), "count": made, "sellUnit": sell_unit, "sell": sell_unit * made,
            "deposit": dep, "p": p, "profit": profit, "sold": liq.get("sold"),
            "days": sell_through_days(liq.get("sold"), 0, made)}


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


def scan_age(a: Any, data_age: Any = None, db: Any = None, now: Any = None) -> float | None:
    """Seconds since the last AH scan, as of NOW (reader side, the WoW page): ``summary.auction.scanAge`` is the
    age at the addon's save, so the save's own age is added; without it, the module's persisted
    ``lastScanT`` (any scan, account or per-character settings). None when no scan is known. Never raises."""
    try:
        sa = _f(a.get("scanAge")) if isinstance(a, dict) else None
        if sa is not None and sa >= 0:
            return sa + max(0.0, _f(data_age, 0.0) or 0.0)
        d = db if isinstance(db, dict) else {}
        stores = [d.get("modules")]
        chars = d.get("chars")
        for c in (chars.values() if isinstance(chars, dict) else ()):
            if isinstance(c, dict):
                stores.append(c.get("moduleSettings"))
        last = max((_f((s.get("Auction") or {}).get("lastScanT"), 0.0) or 0.0 for s in stores
                    if isinstance(s, dict) and isinstance(s.get("Auction"), dict)), default=0.0)
        if last > 0:
            import time  # noqa: PLC0415

            n = _f(now) or time.time()
            if last <= n + 300:
                return max(0.0, n - last)
    except Exception:
        log.debug("scan_age failed", exc_info=True)
    return None


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
    sold = _f(d.get("sold"))
    sold_t = f", se vend ~{_dec(sold)}/jour" if sold is not None else ""
    stop = _i(d.get("stop"))
    stop_t = f", stop-loss {money_text(stop)}/u" if stop > 0 else ""
    return (f"{label} : acheter {money_text(d.get('buy'))}, revendre {money_text(d.get('sell'))} → "
            f"+{money_text(d.get('profit'))}{roi_t}{conf_t}{sold_t}{stop_t}")


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
        elif isinstance(a.get("none"), dict):
            out["aucun_achat_sur"] = [NONE_TEXT] + [str(x) for x in _list(a["none"].get("why"))][:4]
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
        crafts = []
        for c in _list(a.get("crafts"))[:5]:
            if isinstance(c, dict) and _i(c.get("profit")) > 0:
                crafts.append(f"{c.get('name')} : composants {money_text(c.get('cost'))}, se vend "
                              f"{money_text(c.get('sell'))} → +{money_text(c.get('profit'))} net")
        if crafts:
            out["a_fabriquer"] = crafts
        vf = []
        for v in _list(a.get("vendorFlips"))[:5]:
            if isinstance(v, dict) and _i(v.get("profit")) > 0:
                lim = " (quantité limitée)" if v.get("lim") else ""
                vf.append(f"{v.get('name')} x{_i(v.get('count'), 1)} : marchand {money_text(v.get('buyUnit'))}/u{lim}, "
                          f"HV ~{money_text(v.get('sellUnit'))}/u → +{money_text(v.get('profit'))} net")
        if vf:
            out["marchand_vers_hv"] = vf
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
    lines = list(ctx.get("aucun_achat_sur", []))[:2]
    for k, title in (("affaires", "À acheter"), ("a_vendre", "À vendre"), ("a_fabriquer", "À fabriquer"),
                     ("marchand_vers_hv", "Marchand → HV")):
        for t in ctx.get(k, [])[:3]:
            lines.append(f"{title} — {t}")
    if ctx.get("profit_realise"):
        lines.append("Profit réalisé : " + ctx["profit_realise"])
    return lines
