"""The 3–4 selectable routes, with verdict, reasons and plan steps (TreeAIPlan format, SCHEMA.md §3).

* ``xph``: « XP/h max ». Rate mode over every quest in the export (current zone + neighbours).
* ``clear``: « Finir la zone ». Every doable quest of the current zone, in the least time.
* ``squad``: « Escouade (n) ». The group as one unit: union of logs, the slowest member's speed. Only when grouped.
* ``safe``: « Prudent ». Rate mode without « galère » / risky quests, scored on p80 times.

Verdicts (report 5 C3, report 4 §6): impossible > zone > long > galere > ok. The first reason is the cause.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from treeaicoach.wow.planner import data as DM
from treeaicoach.wow.planner import models as M
from treeaicoach.wow.planner import solver as S
from treeaicoach.wow.planner import speed as SP

log = logging.getLogger(__name__)

TITLES = {"xph": "XP/h max", "clear": "Finir la zone", "squad": "Escouade", "safe": "Prudent"}
VERDICT_FR = {"ok": "OK", "galere": "On va galérer", "long": "Trop long", "impossible": "Pas faisable",
              "zone": "Changez de zone"}
ORDER = ("xph", "clear", "squad", "safe")


def _short(key: str) -> str:
    return str(key or "").split("-")[0] or "?"


def world_to_map(pb: DM.Problem, rects: dict[int, DM.MapRect], c: int, wx: float, wy: float) -> tuple[int, float, float]:
    """(uiMapID, x%, y%) of a world point: the player's zone first, else the smallest zone map containing it."""
    best = None
    for mid, r in rects.items():
        if r.c != c or r.x1 == r.x0 or r.y1 == r.y0:
            continue
        my = (wx - r.x0) / (r.x1 - r.x0)
        mx = (wy - r.y0) / (r.y1 - r.y0)
        if 0 <= mx <= 1 and 0 <= my <= 1:
            area = abs((r.x1 - r.x0) * (r.y1 - r.y0))
            pref = 0 if mid == pb.zone else 1
            if best is None or (pref, area) < best[0]:
                best = ((pref, area), mid, mx * 100, my * 100)
    if best is None:
        return 0, 0.0, 0.0
    return best[1], round(best[2], 1), round(best[3], 1)


def _obj_label(pb: DM.Problem, nd: DM.Node) -> str:
    q = pb.quests[nd.q]
    objs = DM._list(q.raw.get("o"))
    o = objs[nd.o - 1] if 0 < nd.o <= len(objs) and isinstance(objs[nd.o - 1], dict) else {}
    txt = str(o.get("t") or "").strip()
    need = int(DM._n(o.get("n"), 0)) - int(DM._n(o.get("have"), 0))
    if txt:
        return f"{txt} (« {q.title} »)"
    verb = {"kill": "Tuer", "item": "Ramasser", "obj": "Fouiller", "explore": "Explorer", "talk": "Parler",
            "spell": "Utiliser l'objet"}.get(str(o.get("k")), "Objectif")
    n = f" {need}" if need > 0 and o.get("k") in ("kill", "item", "obj") else ""
    return f"{verb}{n} — « {q.title} »"


def build_steps(pb: DM.Problem, s: S.Solver, route: list[int], squad: bool = False,
                grind_left: float = 0.0) -> list[dict]:
    """Plan steps of a route, with the speed techniques (speed.py) as extra steps / ``tip`` lines.
    ``grind_left``: seconds of session left after the route, filled with a final grind step."""
    sim = s.simulate(route)
    picked_at = {qi: -1 for qi, q in enumerate(pb.quests) if q.in_log}
    for i, n in enumerate(route):
        if pb.nodes[n].kind == "pick":
            picked_at.setdefault(pb.nodes[n].q, i)
    fps = SP.unknown_flight_points(pb) if pb.start is not None else []
    fp_used: set[int] = set()
    rects = pb.rects
    steps: list[dict] = []
    prev = pb.START
    if squad and len(pb.members) > 1 and route:
        meet = meeting_point(pb, route[0])
        if meet is not None:
            steps.append({"k": "meet", "map": meet.map, "x": meet.mx, "y": meet.my,
                          "label": "Regroupement ici avant de partir", "eta": 0})
    shared: set[int] = set()
    for i, n in enumerate(route):
        nd = pb.nodes[n]
        q = pb.quests[nd.q]
        eta = int(round(sim.done_at[i] / 60.0))
        eta0 = int(round((sim.done_at[i - 1] if i else 0) / 60.0))
        a = pb.nodes[prev].pt if prev < len(pb.nodes) else pb.start
        walk = not sim.hearth_before[i] and (prev, n) not in pb.via
        tip = None
        if walk and a is not None and nd.pt is not None:
            fp = SP.flight_point_on_the_way(pb, a, nd.pt, fps, fp_used)
            if fp is not None:
                tid, name, fpp, secs = fp
                fp_used.add(tid)
                mid, x, y = world_to_map(pb, rects, fpp.c, fpp.x, fpp.y)
                if mid:
                    steps.append({"k": "fp", "map": mid, "x": x, "y": y, "eta": eta0,
                                  "label": f"Point de vol : {name}",
                                  "tip": f"Prends le point de vol {name} en passant (+{SP.saved_text(secs)}) : "
                                         "il servira pour la suite"})
            tip = SP.kill_on_the_way(pb, route, i, a, nd.pt, picked_at)
        if sim.hearth_before[i] and pb.hearth is not None:
            h = pb.hearth
            steps.append({"k": "hs", "map": h.map, "x": h.mx, "y": h.my, "label": "Pierre de foyer",
                          "eta": eta0, "tip": SP.hearth_tip(sim.hearth_save[i])})
        elif (prev, n) in pb.via:
            a, b = pb.via[(prev, n)]
            pa = pb.taxi_pos.get(a)
            if pa is not None:
                mid, x, y = world_to_map(pb, rects, pa.c, pa.x, pa.y)
                steps.append({"k": "fly", "map": mid, "x": x, "y": y,
                              "label": f"Vol : {pb.taxi_names.get(a, '?')} → {pb.taxi_names.get(b, '?')}",
                              "eta": int(round((sim.done_at[i - 1] if i else 0) / 60.0))})
        if sim.grind_before[i] > 0:
            steps.append(SP.gate_step(pb, nd, sim.grind_before[i], q.req, eta0))
        st: dict[str, Any] = {"k": nd.kind, "q": q.id, "map": nd.pt.map, "x": nd.pt.mx, "y": nd.pt.my, "eta": eta}
        if tip:
            st["tip"] = tip
        if nd.kind == "pick":
            st["label"] = f"Prendre « {q.title} »"
        elif nd.kind == "turn":
            st["label"] = f"Rendre « {q.title} »"
        else:
            st["o"] = nd.o
            st["label"] = _obj_label(pb, nd)
        steps.append(st)
        if squad and nd.q not in shared and (nd.kind == "pick" or (q.in_log and i == route.index(q.nodes[0]))):
            shared.add(nd.q)
            if q.flags & 8:
                have = {str(w) for w in DM._list(q.raw.get("who"))}
                for m in pb.members[1:]:
                    if m.key not in have and m.level >= q.req:
                        steps.append({"k": "pick", "q": q.id, "map": nd.pt.map, "x": nd.pt.mx, "y": nd.pt.my,
                                      "eta": eta, "who": m.key,
                                      "label": f"Partager « {q.title} » avec {_short(m.key)} (à côté de toi)"})
        prev = n
    if grind_left > 0:
        lv = sim.level_end if route else pb.level
        last = steps[-1]["eta"] if steps else 0
        g = SP.final_grind_step(pb, lv, grind_left, last, S.grind_rate(pb))
        if g is not None:
            steps.append(g)
    return steps


def meeting_point(pb: DM.Problem, first: int) -> DM.Point | None:
    """Among the members' positions and the first step, the point minimising the farthest member's walk."""
    pos: list[DM.Point] = [p for p in [pb.start] if p is not None]
    for sq in pb.squad_raw:
        p = DM.make_point(pb.rects, sq)
        if p is not None:
            pos.append(p)
    cands = pos + [pb.nodes[first].pt]
    best, bp = None, None
    for c in cands:
        if c is None:
            continue
        worst = max(((c.x - p.x) ** 2 + (c.y - p.y) ** 2) ** 0.5 if c.c == p.c else 1e9 for p in pos) if pos else 0
        if best is None or worst < best:
            best, bp = worst, c
    return bp


def _galere_reasons(pb: DM.Problem, quests: list[int]) -> list[str]:
    out = []
    for qi in quests:
        g = pb.quests[qi].galere
        if g is not None and g.label in ("galere", "eviter"):
            out.append((g.score, f"On va galérer : {g.reason} (« {pb.quests[qi].title} »)"))
    out.sort(reverse=True)
    return [t for _, t in out[:3]]


def verdict(pb: DM.Problem, s: S.Solver, res: S.Result, zone_switch: tuple[bool, str] | None,
            rid: str) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if pb.raid:
        return "impossible", ["Vous êtes en raid : les quêtes normales n'avancent pas (repassez en groupe)."]
    if not res.route:
        if pb.log_count >= pb.log_cap:
            return "impossible", ["Journal de quêtes plein : rends ou abandonne des quêtes."]
        if s.usable and s.mode == "rate":
            return "long", ["Trop long : aucune quête ne rapporte plus que farmer ici, change de zone ou farme."]
        return "impossible", ["Aucune quête faisable ici avec les données actuelles."]
    minutes = res.seconds / 60.0
    quests = s.quests_in(res.route)
    sim = s.simulate(res.route)
    summary = f"{len(quests)} quêtes, ~{int(round(minutes))} min, niv {pb.level} → {sim.level_end}"
    v = "ok"
    if zone_switch and zone_switch[0] and rid in ("xph", "clear"):
        v = "zone"
        reasons.append("Changez de zone : " + zone_switch[1])
    grind = S.grind_rate(pb) * 3600
    long_r = []
    if pb.session_min and minutes > pb.session_min * 1.05:
        long_r.append(f"Trop long : ~{int(round(minutes))} min pour {int(pb.session_min)} min de jeu prévues")
    if pb.calib.xph and res.xph < 0.7 * pb.calib.xph:
        long_r.append(f"Trop long : {int(res.xph):,} XP/h contre {int(pb.calib.xph):,} d'habitude".replace(",", " "))
    elif res.xph < 0.6 * grind and rid != "clear":
        long_r.append("Trop long : moins rentable que farmer des mobs de ton niveau")
    if v == "ok" and long_r:
        v = "long"
    reasons += long_r
    gal = _galere_reasons(pb, quests)
    if rid == "squad" and len(pb.members) > 1:
        lv = [m.level for m in pb.members]
        if max(lv) - min(lv) >= 5:
            top = max(pb.members, key=lambda m: m.level)
            gal.insert(0, f"On va galérer : {_short(top.key)} (niv {top.level}) grise les mobs des autres")
    if v == "ok" and gal:
        v = "galere"
    reasons += gal
    if res.unserved and rid == "clear":
        reasons.append(f"{len(res.unserved)} quête(s) laissée(s) de côté (niveau ou prérequis)")
    reasons.append(summary)
    return v, reasons[:5]


def zone_analysis(pb: DM.Problem, seconds: float = 1.0) -> tuple[bool, str]:
    """Should the player leave the current zone? (report 4 §6.5)."""
    try:
        here = {i for i, q in enumerate(pb.quests) if q.zone == pb.zone or q.in_log}
        others: dict[int, set[int]] = {}
        for i, q in enumerate(pb.quests):
            if q.zone and q.zone != pb.zone and not q.in_log:
                others.setdefault(q.zone, set()).add(i)
        non_grey = sum(1 for i in here if M.quest_color(pb.level, pb.quests[i].level) != "grey")
        rh = S.solve(pb, "rate", seconds / 2, quests=here, rounds=1)
        med = sorted(pb.quests[i].level for i in here)
        zone_median = med[len(med) // 2] if med else 0
        best_alt, best_tr, best_z = 0.0, 0.0, 0
        per = seconds / 2 / max(1, len(others))
        for z, qs in others.items():
            ra = S.solve(pb, "rate", per, quests=qs, rounds=1)
            if not ra.route:
                continue
            tr = pb.D[pb.START][ra.route[0]]
            rate = ra.xp / max(60.0, ra.seconds - tr) * 3600
            if rate > best_alt:
                best_alt, best_tr, best_z = rate, tr / 60.0, z
        h = min(pb.session_min or 90.0, 90.0)
        sw, why = M.should_switch_zone(rh.xph, best_alt, best_tr, h, non_grey, pb.level, zone_median)
        if sw and best_z and pb.maps.get(best_z):
            why += f" → {pb.maps[best_z]}"
        return sw, why
    except Exception:
        log.debug("zone analysis failed", exc_info=True)
        return False, ""


def compute_plan(export: dict | None, learn: dict | None = None, char_learn: dict | None = None,
                 seconds: float = 16.0, seed: int = 0, now: float | None = None) -> dict | None:
    """TreeAIPlan dict (SCHEMA.md §3) with the 3–4 routes, or None when the export is unusable."""
    pb = DM.load_problem(export, learn, char_learn)
    if pb is None:
        return None
    grouped = len(pb.squad_raw) > 0
    budget = {"xph": 0.3, "clear": 0.25, "squad": 0.25 if grouped else 0.0, "safe": 0.2}
    tot = sum(budget.values())
    zone_s = seconds * 0.08
    zs = zone_analysis(pb, zone_s)
    routes = []
    for rid in ORDER:
        if budget[rid] <= 0:
            continue
        sec = (seconds - zone_s) * budget[rid] / tot
        try:
            if rid == "squad":
                gp = DM.load_problem(export, learn, char_learn, group_mode=True)
                if gp is None:
                    continue
                prob, mode, qs, p80 = gp, "rate", None, False
            elif rid == "clear":
                prob, mode, p80 = pb, "clear", False
                qs = {i for i, q in enumerate(pb.quests) if q.zone == pb.zone or q.in_log}
            elif rid == "safe":
                prob, mode, p80 = pb, "rate", True
                qs = {i for i, q in enumerate(pb.quests)
                      if (q.galere is None or q.galere.score < 45) and q.feas >= 0.8}
            else:
                prob, mode, qs, p80 = pb, "rate", None, False
            res = S.solve(prob, mode, sec, quests=qs, p80=p80, seed=seed)
            sv = S.Solver(prob, mode, qs, p80, seed)
            sv.lam = res.lam
            v, reasons = verdict(prob, sv, res, zs, rid)
            title = TITLES[rid] + (f" ({len(prob.members)})" if rid == "squad" else "")
            left = 0.0
            if rid in ("xph", "safe") and not pb.raid:          # quests run out → grind the rest of the session
                left = (prob.session_min or (30.0 if not res.route else 0.0)) * 60.0 - res.seconds
            routes.append({"id": rid, "title": title, "xph": int(round(res.xph)),
                           "minutes": int(round(res.seconds / 60.0)), "verdict": v, "reasons": reasons,
                           "steps": build_steps(prob, sv, res.route, squad=rid == "squad", grind_left=left)})
        except Exception:
            log.debug("route %s failed", rid, exc_info=True)
    return {"v": 1, "made": int(now if now is not None else time.time()), "char": pb.char, "zone": pb.zone,
            "routes": routes}


def summary_lines(plan: dict | None, route_id: str | None = None, steps: int = 3) -> list[str]:
    """Short French lines about a plan (the chosen route first), for the coach / UI."""
    if not isinstance(plan, dict):
        return []
    routes = [r for r in DM._list(plan.get("routes")) if isinstance(r, dict)]
    if not routes:
        return []
    chosen = next((r for r in routes if r.get("id") == route_id), routes[0])
    out = [f"Trajet « {chosen.get('title')} » : {chosen.get('xph', 0)} XP/h, ~{chosen.get('minutes', 0)} min, "
           f"verdict {VERDICT_FR.get(str(chosen.get('verdict')), '?')}."]
    rs = [str(x) for x in DM._list(chosen.get("reasons"))][:2]
    if rs:
        out.append("Pourquoi : " + " ; ".join(rs))
    st = [x for x in DM._list(chosen.get("steps")) if isinstance(x, dict)][:steps]
    if st:
        out.append("Prochaines étapes : " + " ; ".join(str(x.get("label")) for x in st))
    others = [f"{r.get('title')} {r.get('xph', 0)} XP/h ({VERDICT_FR.get(str(r.get('verdict')), '?')})"
              for r in routes if r is not chosen]
    if others:
        out.append("Autres trajets : " + ", ".join(others))
    return out
