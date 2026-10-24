"""JUNGLER MAP on REAL games: is treeaicoach/jungler_map.py right, and better than the fog estimate?

    python tools/jungler_map_truth.py <games dir> [--prior]   (records *.json + truth/*.truth.json)

Replays every game at 1 Hz from what the app saw (the record: minimap sightings of the enemy
jungler, my positions, ally positions) plus the public facts of the LCU truth that the Live Client
also gives (kills he takes part in -> the victim's place, his deaths -> respawn at his fountain,
epic monsters he takes -> the pit). At each truth frame (one per minute, true positions), scores:

* **zones** (6 map parts of jungler_map.ZONE_KEYS) and **half** (top / bottom): log-loss and
  accuracy of the argmax, for the minutes the jungler is alive and NOT visible on the minimap;
* **near**: P(he can be on me within 20 s) vs the truth (geodesic distance on the walkable mask
  between the two true positions <= 20 s of walking + Flash): Brier score and log-loss.
* **danger rule variants** (:data:`MAP_RULES`): alert episodes (45 s cooldown), how many the
  truth confirms / proves false, and my deaths with the jungler warned 3-25 s before by the rule,
  by the alerts the app really showed (``app``, the record) and by either (``app+``).

Models: ``fog`` = today's fog_tracker estimate (walk model from the last fact for 70 s, then
nothing = uniform over the walkable map, no game-start fact); ``prior`` = minute prior only;
``map`` = jungler_map (walk model + prior, game-start fact). The prior is built LEAVE-ONE-GAME-OUT
(the game scored never feeds its own prior). ``--prior`` prints the full-data table for
jungler_map.ZONE_PRIOR. Development tool only (aggregated numbers, never ids).
"""

from __future__ import annotations

import bisect
import glob
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from treeaicoach import geometry  # noqa: E402
from treeaicoach import ground_truth as GT  # noqa: E402
from treeaicoach import jungler_map as JM  # noqa: E402
from treeaicoach.fog_tracker import shared_reachability  # noqa: E402
from treeaicoach.jungle_graph import speed_at  # noqa: E402
from team_calls_truth import respawn_s  # noqa: E402

EPS = 1e-3
#: danger.py jungler_fog rule: ETA, cooldown; the death of mine is "warned" by an alert in this window
DANGER_ETA_S = 7.0
WARN_COOLDOWN_S = 45.0
WARN_BEFORE_S = (3.0, 25.0)
MINUTES = 30
#: walking horizons (s) of the near-me mass kept per tick (the danger variants read them)
HORIZONS = (7.0, 10.0, 14.0)
ALLY_NEAR_R = 0.12


#: alert kinds of the record that warn me of a gank / of a danger (the "app" column)
APP_DANGER_KINDS = ("jungler_approach", "roam_approach", "collapse", "personal_danger")


def _vanish(th: float, horizon: float = 7.0, max_since: float = 10.0, pm: bool = False):
    """danger.py ``jungler_vanished``: he left the minimap <= ``max_since`` s ago and >= ``th`` of
    the map mass can be on me within ``horizon`` s (+ past the middle with ``pm``)."""
    return lambda x: (x["since"] is not None and x["since"] <= max_since and x["ms"][horizon] >= th
                      and (x["pm"] or not pm))


#: variants of the jungler-map danger rule (tick dict -> alert?); "vanish75" is danger.py's
MAP_RULES = {
    "fog60:p35": lambda x: x["since"] is not None and 8.0 <= x["since"] <= 60.0 and x["m7"] >= 0.35 and x["pm"],
    "p35": lambda x: x["since"] is not None and x["since"] >= 8.0 and x["m7"] >= 0.35 and x["pm"],
    "p55": lambda x: x["since"] is not None and x["since"] >= 8.0 and x["m7"] >= 0.55 and x["pm"],
    "vanish55pm": _vanish(0.55, pm=True),
    "vanish65": _vanish(0.65),
    "vanish75": _vanish(0.75),
    "vanish75h10": _vanish(0.75, 10.0),
    "vanish75+exp": lambda x: _vanish(0.75)(x) or (x["ms"][14.0] >= 0.3 and x["pm"] and x["ally"] == 0
                                                   and x["opp_age"] >= 15.0 and (x["since"] or 1e9) >= 60.0),
}


def _in_lane(uv) -> bool:
    z = geometry.lane_of(geometry.classify_zone(*uv))
    return z in ("top", "mid", "bot")


def _norm(s) -> str:
    return "".join(ch for ch in str(s or "").casefold() if ch.isalnum())


def load(root: str) -> list[tuple[dict, dict]]:
    out = []
    for f in sorted(glob.glob(os.path.join(root, "truth", "*.truth.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
            rp = os.path.join(root, str(d.get("record") or ""))
            if os.path.isfile(rp):
                out.append((d, json.load(open(rp, encoding="utf-8"))))
        except Exception as e:  # noqa: BLE001
            print(f"skip {os.path.basename(f)}: {e}")
    return out


def dead_windows(tr: GT._Truth, pid: int, d: dict) -> list[tuple[float, float]]:
    out = []
    for t, _k, victim, _a, _uv in tr.kills:
        if victim != pid:
            continue
        lvl = 1
        for ft, row in tr.frames:
            if ft <= t and str(pid) in row:
                lvl = row[str(pid)][5]
        out.append((t, t + respawn_s(lvl, t)))
    return out


def zone_counts(d: dict) -> np.ndarray:
    """[MINUTES, 6] counts of the jungler's canonical zone per minute (alive)."""
    tr = GT._Truth(d)
    jg = tr.jungler
    his = geometry.normalize_team({p["id"]: p for p in d["participants"]}.get(jg, {}).get("team"))
    mine = tr.my_team
    deadw = dead_windows(tr, jg, d)
    c = np.zeros((MINUTES, len(JM.FINE_KEYS)))
    for gt, row in tr.frames:
        p = row.get(str(jg))
        if not p or any(a <= gt < b for a, b in deadw):
            continue
        z = JM.canonical(JM.fine_zone_of((p[0], p[1]), mine), his)
        c[min(MINUTES - 1, int(gt // 60)), JM.FINE_KEYS.index(z)] += 1
    return c


def prior_table(counts: np.ndarray) -> list[list[float]]:
    """Dirichlet-smoothed, +-1 minute pooled shares."""
    tot = counts.sum(axis=1)
    last = int(np.nonzero(tot)[0].max()) if tot.any() else 0
    rows = []
    glob_share = (counts.sum(axis=0) + 1) / (counts.sum() + len(JM.FINE_KEYS))
    for m in range(last + 1):
        pool = counts[max(0, m - 1):m + 2].sum(axis=0)
        pr = (pool + 2.0 * glob_share) / (pool.sum() + 2.0)
        rows.append([round(float(x), 3) for x in pr])
    return rows


def area_table() -> list[list[float]]:
    s = JM._static()
    masks = JM.fine_masks("ORDER")
    a = [float((s["walk"] * masks[k]).sum()) for k in JM.FINE_KEYS]
    tot = sum(a)
    return [[x / tot for x in a]]


def replay(d: dict, rec: dict, model: JM.JunglerMap, reach, ticks: list | None = None) -> list[dict]:
    tr = GT._Truth(d)
    jg, me = tr.jungler, tr.me
    parts = {p["id"]: p for p in d["participants"]}
    jp = parts.get(jg, {})
    keys = {_norm(jp.get("alias")), _norm(jp.get("name"))} - {""}
    sight = sorted((float(p[0]), float(p[1]), float(p[2])) for k, pts in (rec.get("sightings") or {}).items()
                   if _norm(k) in keys for p in pts)
    op = parts.get(tr.opp, {}) if tr.opp is not None else {}
    okeys = {_norm(op.get("alias")), _norm(op.get("name"))} - {""}
    opp_sight = sorted(float(p[0]) for k, pts in (rec.get("sightings") or {}).items() if _norm(k) in okeys for p in pts)
    mine = GT._series(rec.get("my_positions"))
    allies = [GT._series(v) for v in (rec.get("allies") or {}).values()]
    deadw = dead_windows(tr, jg, d)
    facts = []      # (gt, kind, payload)
    for t, killer, victim, assists, uv in tr.kills:
        if victim == jg:
            w = next((b for a, b in deadw if abs(a - t) < 1e-6), t + 30)
            facts.append((t, "dead", w))
        elif (killer == jg or jg in assists) and parts.get(victim, {}).get("team") == tr.my_team:
            facts.append((t, "kill", uv))
    for m in tr.monsters:
        if GT._i(m[1]) == jg and GT._f(m[5]) is not None:
            facts.append((float(m[0]), "pit", (float(m[5]), float(m[6]))))
    facts.sort(key=lambda f: f[0])
    model.reset(tr.my_team)
    frames = {int(round(t)): row for t, row in tr.frames}
    end = int(min(float(d.get("duration") or 0) or 1e9, max(frames) if frames else 0))
    si = fi = mi = 0
    ai = [0] * len(allies)
    me_uv = None
    out = []
    for gt in range(0, end + 1):
        while si < len(sight) and sight[si][0] <= gt:
            model.seen(sight[si][0], sight[si][1:])
            si += 1
        while fi < len(facts) and facts[fi][0] <= gt:
            t, kind, pay = facts[fi]
            if kind == "dead":
                model.dead(t, pay)
            else:
                model.anchor(t, pay, kind)
            fi += 1
        while mi < len(mine) and mine[mi][0] <= gt:
            me_uv = mine[mi][1:]
            mi += 1
        viewers = [me_uv] if me_uv is not None else []
        for k, ser in enumerate(allies):
            while ai[k] < len(ser) and ser[ai[k]][0] <= gt:
                ai[k] += 1
            if ai[k] and gt - ser[ai[k] - 1][0] < 3.0:
                viewers.append(ser[ai[k] - 1][1:])
        row = frames.get(gt)
        true_me = row.get(str(me)) if row else None
        st = model.step(float(gt), true_me[:2] if true_me else me_uv, viewers)
        if ticks is not None and me_uv is not None and not st.visible and not st.dead and st.grid is not None:
            ms = {h: float(st.grid[JM.JunglerMap.near_mask(me_uv, gt, h)].sum()) for h in HORIZONS}
            ally = sum(1 for p in viewers[1:] if math.hypot(p[0] - me_uv[0], p[1] - me_uv[1]) < ALLY_NEAR_R)
            oi = bisect.bisect_right(opp_sight, gt)
            opp_age = gt - opp_sight[oi - 1] if oi else 1e9
            ticks.append({"gt": float(gt), "since": st.since_fact, "m7": ms[DANGER_ETA_S], "ms": ms, "me": me_uv,
                          "ally": ally, "opp_age": opp_age, "in_lane": _in_lane(me_uv)})
        if row is None or gt < 60:
            continue
        j = row.get(str(jg))
        if not j or any(a <= gt < b for a, b in deadw) or st.dead:
            continue
        hidden = not st.visible
        z = JM.zone_of((j[0], j[1]), tr.my_team)
        near = None
        if true_me:
            budget = speed_at(gt) * JM.NEAR_S + JM.FLASH
            fld = reach.distance_field((true_me[0], true_me[1]), max_dist=budget + 0.05)
            cx, cy = reach.cell_of((j[0], j[1]))
            near = bool(fld[cy, cx] <= budget)
        out.append({"gt": gt, "hidden": hidden, "zone": z, "zones": dict(st.zones), "top": j[0] + j[1] < 1.0,
                    "p_top": st.top_half, "shown": st.side_shown, "near": near, "p_near": st.p_near})
    return out


def score(rows: list[dict]) -> dict:
    rows = [r for r in rows if r["hidden"]]
    n = len(rows)
    if not n:
        return {"n": 0}
    ll = sum(-math.log(max(EPS, r["zones"].get(r["zone"], 0.0))) for r in rows) / n
    acc = sum(max(r["zones"], key=r["zones"].get) == r["zone"] for r in rows) / n
    hl = sum(-math.log(max(EPS, r["p_top"] if r["top"] else 1 - r["p_top"])) for r in rows) / n
    hacc = sum((r["p_top"] >= 0.5) == r["top"] for r in rows) / n
    nr = [r for r in rows if r["near"] is not None and r["p_near"] is not None]
    brier = sum((r["p_near"] - r["near"]) ** 2 for r in nr) / max(1, len(nr))
    nll = sum(-math.log(max(EPS, min(1 - EPS, r["p_near"] if r["near"] else 1 - r["p_near"]))) for r in nr) / max(1, len(nr))
    conf = [r for r in rows if r["shown"]]
    cacc = sum(r["shown"].endswith("haut") == r["top"] for r in conf) / max(1, len(conf))
    return {"n": n, "zone_ll": round(ll, 3), "zone_acc": round(acc, 3), "half_ll": round(hl, 3),
            "half_acc": round(hacc, 3), "shown": len(conf), "shown_acc": round(cacc, 3),
            "near_brier": round(brier, 3), "near_ll": round(nll, 3), "near_rate": round(sum(r["near"] for r in nr) / max(1, len(nr)), 3)}


def _app_alerts(rec: dict) -> list[float]:
    """Game times of the danger / gank alerts the app really showed in that game (the record)."""
    return [float(a[0]) for a in rec.get("alerts") or []
            if isinstance(a, list) and len(a) >= 3 and str(a[1]).lower() in APP_DANGER_KINDS and a[0] is not None]


def warnings(d: dict, rec: dict, ticks: list, rule) -> dict:
    """danger.py-style episodes (cooldown) of ``rule(tick)`` scored on the truth. ``tick``: the
    dict of :func:`replay` (+ ``pm``: I stand past the middle of the map). ``app+``: my deaths warned
    by the rule OR by an alert the app really showed (gank.py / danger.py, the record)."""
    from treeaicoach.danger import past_mid

    tr = GT._Truth(d)
    my_pos = GT._my_pos_fn(rec, tr)
    anchors = tr.anchors(tr.jungler)
    alerts, last = [], -1e9
    for x in ticks:
        x["pm"] = past_mid(x["me"], tr.my_team)
        if x["gt"] - last >= WARN_COOLDOWN_S and rule(x):
            alerts.append(x["gt"])
            last = x["gt"]
    verdicts = [GT._classify(t, my_pos(t), anchors, my_pos) for t in alerts]
    deaths = [t for t, k, v, a, _uv in tr.kills if v == tr.me and (k == tr.jungler or tr.jungler in a)]
    lo, hi = WARN_BEFORE_S
    warned = sum(any(t - hi <= w <= t - lo for w in alerts) for t in deaths)
    app = _app_alerts(rec)
    return {"alerts": len(alerts), "confirmed": verdicts.count("confirmed"),
            "false": verdicts.count("false") + verdicts.count("probable_false"), "deaths": len(deaths), "warned": warned,
            "app": sum(any(t - hi <= w <= t - lo for w in app) for t in deaths),
            "app+": sum(any(t - hi <= w <= t - lo for w in alerts + app) for t in deaths)}


def main(argv: list[str]) -> int:
    root = next((a for a in argv if not a.startswith("--")), ".")
    games = load(root)
    counts = [zone_counts(d) for d, _r in games]
    if "--prior" in argv:
        print(json.dumps(prior_table(sum(counts)), separators=(",", ":")))
        return 0
    taus = [float(a.split("=")[1]) for a in argv if a.startswith("--tau=")] or [JM.TAU_S]
    reach = shared_reachability()
    res: dict[str, list] = {}
    warn: dict[str, dict] = {}
    for i, (d, rec) in enumerate(games):
        loo = prior_table(sum(c for k, c in enumerate(counts) if k != i))
        fog = JM.JunglerMap(prior=area_table(), tau_s=1e9, max_fact_s=70.0, start_fact=False)
        fog.prior_floor = 0.0
        models = {"fog": fog,
                  "prior": JM.JunglerMap(prior=loo, use_filter=False)}
        floors = [float(a.split("=")[1]) for a in argv if a.startswith("--floor=")] or [JM.PRIOR_FLOOR]
        for tau in taus:
            for fl in floors:
                m = JM.JunglerMap(prior=loo, tau_s=tau)
                m.prior_floor = fl
                models[f"map{int(tau)}" + (f"f{fl:g}" if len(floors) > 1 else "")] = m
        for name, m in models.items():
            ticks: list = []
            res.setdefault(name, []).extend(replay(d, rec, m, reach, ticks))
            if name == "prior" or not name.startswith(("fog", "map")):
                continue
            rules = {"fog": {"prod": lambda x: x["since"] is not None and 8.0 <= x["since"] <= 60.0
                             and x["m7"] >= 0.35 and x["pm"]}}.get(name, MAP_RULES)
            for rn, rule in rules.items():
                w = warnings(d, rec, ticks, rule)
                acc = warn.setdefault(f"{name}:{rn}", {})
                for k, v in w.items():
                    acc[k] = acc.get(k, 0) + v
    print(f"{len(games)} parties, minutes où le jungler est vivant et caché :")
    for name, rows in res.items():
        print(f"  {name:8s} {score(rows)}")
    print("alerte 'jungler peut arriver' (danger.py) : épisodes, vérité, mes morts avec lui prévenues 3-25 s avant")
    print("  (app = prévenues par une alerte que l'appli a vraiment montrée, app+ = app ou la règle)")
    for name, w in warn.items():
        print(f"  {name:12s} {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
