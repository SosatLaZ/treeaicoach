"""SPIKE PLANNER / OPPONENT MODEL on REAL games: what the lane gold / level gap says about the trades,
and can the lane opponent's next back be predicted?

    python tools/spike_truth.py <games dir> [--all-roles]   (records *.json + truth/*.truth.json)

Inputs per game: the record's Tab timeline (one sample per minute: item gold / creep score / level
gap of each role, ``scoreboard.timeline``) + my inventory (``snapshots``) -> the true item gold of
my role's lane opponent each minute; the LCU truth file (per-minute true total gold, creep score,
level of the 10 champions, kills). Frames are 60 s apart: every count is a sample.

1. **Lead -> trades**: each laning minute (2:00-14:00), lead = spike_planner.lead_score (item gold
   gap / 1000 + 0.35 x level gap) vs the next 120 s of the lane (he dies with me involved = won, I
   die with him involved = lost). Same with the draft phase score (draft_edge.phase_scores).
2. **Backs that flip**: my backs (item gold +300 in a minute) that bring the lead past
   spike_planner.FLIP vs the others, and the minutes where my gold could flip it but I stayed.
3. **Opponent back prediction** (NOT built): his backs (item gold +300 in a minute, no death), his
   unspent gold at each (TRUE earned gold - item gold), predicted with a threshold learned in the
   game (median of his previous backs) or fixed (1 250), time = when his TRUE earned gold reaches
   it: error vs the minute window of the real back.
4. **Hidden gold estimator**: 500 + 2.04 PO/s after 1:05 + 21 / creep + 300 / kill + 140 / assist
   vs the true total gold (what the live model would see).
"""

from __future__ import annotations

import bisect
import glob
import json
import os
import statistics as S
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treeaicoach import draft_edge as DE  # noqa: E402
from treeaicoach import spike_planner as SP  # noqa: E402
from treeaicoach.scoreboard import items_gold  # noqa: E402

BACK_ITEMS = 300
PRIOR_THRESHOLD = 1250


def _interp(xs: list, ys: list, x: float) -> float:
    i = bisect.bisect_left(xs, x)
    if i <= 0:
        return ys[0]
    if i >= len(xs):
        return ys[-1]
    a, b = xs[i - 1], xs[i]
    return ys[i - 1] + (ys[i] - ys[i - 1]) * (x - a) / (b - a)


def load(games: str, all_roles: bool) -> list[dict]:
    out = []
    for f in sorted(glob.glob(os.path.join(games, "*.json"))):
        tf = os.path.join(games, "truth", os.path.basename(f).replace(".json", ".truth.json"))
        if not os.path.exists(tf):
            continue
        try:
            d, t = json.load(open(f, encoding="utf-8")), json.load(open(tf, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        tl = (d.get("scoreboard") or {}).get("timeline") or []
        role = (d.get("summary") or {}).get("position")
        if len(tl) < 8 or not role or (not all_roles and role not in SP.ROLES):
            continue
        me, opp, ma, oa = t.get("me"), None, "", ""
        for p in t.get("participants") or []:
            if p.get("team") != t.get("my_team") and p.get("position") == role:
                opp, oa = p["id"], p.get("alias", "")
            if p.get("id") == me:
                ma = p.get("alias", "")
        if opp is None:
            continue
        snaps = d.get("snapshots") or []
        sg = [s.get("game_time", 0.0) for s in snaps]
        rows = []                                  # (gt, my item gold, opp item gold, level gap, my gold, my level)
        for gt, _, lanes in tl:
            if role not in lanes or not snaps:
                continue
            s = snaps[max(0, bisect.bisect_right(sg, gt) - 1)]
            mine = items_gold(s.get("items") or [])
            rows.append((gt, mine, mine - lanes[role][0], lanes[role][2], s.get("gold", 0), s.get("level", 1)))
        out.append({"name": os.path.basename(f), "role": role, "me": me, "opp": opp, "ma": ma, "oa": oa,
                    "rows": rows, "truth": t})
    return out


def _outcome(g: dict, t0: float, w: float = 120.0) -> int:
    s = 0
    for k in g["truth"].get("kills") or []:
        if t0 < k[0] <= t0 + w:
            inv = [k[1]] + list(k[3] or [])
            if k[2] == g["opp"] and g["me"] in inv:
                s += 1
            if k[2] == g["me"] and g["opp"] in inv:
                s -= 1
    return s


def _wl(xs: list[int]) -> str:
    w, lo = sum(1 for x in xs if x > 0), sum(1 for x in xs if x < 0)
    return f"n={len(xs):3d} won={w:2d} lost={lo:2d} lost/min={lo / max(1, len(xs)):.2f}"


def lead_bins(games: list[dict]) -> None:
    ahead, close, behind, dm, de, do = [], [], [], [], [], []
    for g in games:
        ps = DE.phase_scores(g["ma"], g["oa"])
        for gt, mine, op, lv, _gold, mylv in g["rows"]:
            if not (120 <= gt <= 840):
                continue
            o = _outcome(g, gt)
            c = SP.lead_score(mine, op, lv, 0)
            (ahead if c >= SP.FLIP else close if c >= 0 else behind).append(o)
            p = ps[DE.phase_index(mylv, mine)]
            (dm if p >= 0.2 else do if p <= -0.2 else de).append(o)
    print("1. lead -> next 120 s of the lane")
    print(f"   lead >= {SP.FLIP}: {_wl(ahead)}\n   0..{SP.FLIP}:     {_wl(close)}\n   lead < 0:   {_wl(behind)}")
    print(f"   draft mine: {_wl(dm)}\n   draft even: {_wl(de)}\n   draft his:  {_wl(do)}")


def back_flips(games: list[dict]) -> None:
    fl, nf, stay = [], [], []
    for g in games:
        prev = None
        for gt, mine, op, lv, gold, _ in g["rows"]:
            c = SP.lead_score(mine, op, lv, 0)
            if prev is not None and 150 <= gt <= 840:
                pmine, pc, pgold = prev
                if mine - pmine >= BACK_ITEMS:
                    (fl if pc < SP.FLIP <= c else nf).append(_outcome(g, gt))
                elif pc < SP.FLIP and pgold >= 300 and pc + pgold / 1000.0 >= SP.FLIP:
                    stay.append(_outcome(g, gt - 60))
            prev = (mine, c, gold)
    print("2. my backs (next 120 s)")
    print(f"   flipped the lead: {_wl(fl)}\n   no flip:          {_wl(nf)}\n   could flip, stayed: {_wl(stay)}")


def opp_backs(games: list[dict]) -> None:
    learned, fixed, thr_err = [], [], []
    for g in games:
        t = g["truth"]
        opp = str(g["opp"])
        fx = [fr[0] for fr in t["frames"]]
        earned = [fr[1].get(opp, [0, 0, 0])[2] for fr in t["frames"]]
        deaths = [k[0] for k in t.get("kills") or [] if k[2] == g["opp"]]
        backs, prev = [], None
        for gt, _mine, op, *_ in g["rows"]:
            if prev is not None and op - prev[1] >= BACK_ITEMS and prev[0] >= 90:
                mid = (prev[0] + gt) / 2
                backs.append({"lo": prev[0], "hi": gt, "mid": mid, "after": op,
                              "unspent": _interp(fx, earned, mid) - prev[1],
                              "died": any(prev[0] - 30 < x <= gt for x in deaths)})
            prev = (gt, op)
        hist, last = [], None
        for b in backs:
            if b["hi"] > 900:
                break
            if not b["died"] and last is not None:
                med = S.median(hist) if hist else PRIOR_THRESHOLD
                thr_err.append(abs(med - b["unspent"]))
                for out, thr in ((learned, med), (fixed, PRIOR_THRESHOLD)):
                    tp = last["mid"]
                    while tp < fx[-1] and _interp(fx, earned, tp) - last["after"] < thr:
                        tp += 2.0
                    out.append(abs(tp - b["mid"]))
            if not b["died"]:
                hist.append(b["unspent"])
            last = b
    print("3. opponent back prediction (oracle earned gold)")
    for name, xs in (("learned", learned), ("fixed", fixed)):
        if xs:
            ok = sum(x <= 30 for x in xs) / len(xs)
            print(f"   {name}: n={len(xs)} median {S.median(xs):.0f} s, within 30 s {ok:.2f}")
    if thr_err:
        print(f"   threshold error median {S.median(thr_err):.0f} PO")


def income(games: list[dict]) -> None:
    errs: dict[str, list[float]] = {}
    for g in games:
        t = g["truth"]
        for p in t.get("participants") or []:
            pid = p["id"]
            for gt, row in t["frames"]:
                r = row.get(str(pid))
                if not r or not (120 <= gt <= 900):
                    continue
                k = sum(1 for x in t["kills"] if x[0] <= gt and x[1] == pid)
                a = sum(1 for x in t["kills"] if x[0] <= gt and pid in (x[3] or []))
                est = 500 + 2.04 * max(0.0, gt - 65) + 21 * r[4] + 300 * k + 140 * a
                errs.setdefault(p.get("position", "?"), []).append(abs(est - r[2]))
    print("4. hidden gold estimator vs true total gold (median / p80 PO)")
    for role, xs in sorted(errs.items()):
        xs.sort()
        print(f"   {role:8s} {S.median(xs):5.0f} / {xs[int(0.8 * (len(xs) - 1))]:5.0f}")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    games = load(argv[0], "--all-roles" in argv)
    print(f"{len(games)} games with a Tab timeline and LCU truth")
    lead_bins(games)
    back_flips(games)
    opp_backs(games)
    income(games)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
