"""Speed techniques added on top of the solved route (research/speed.md). Computed from the export (QuestieDB
spawns, the flight graph, the player's stats), never from a hand-written guide. Each one gives a plain-French
``tip`` on the step where it fires:

* level-gate grind      « Niveau 27 requis pour « X » : 4 min de grind ici plutôt qu'un aller-retour »
* hearthstone timing    « Pierre de foyer maintenant : 6 min gagnées »
* kill on the way       « Tue en chemin : 4 Loups pour « X » »
* flight point on the way « Prends le point de vol de Y en passant (+20 s) : il servira pour la suite »
* grind when quests run out « Plus de quête rentable : grind ici ~22 800 XP/h »

The level-gate grind itself is a solver move (``Solver.gate_grind``); the rest is decided here.
"""

from __future__ import annotations

import math
import re

from treeaicoach.wow.planner import data as DM

WAY_MIN_LEG = 150.0        # yd: shorter legs pass no extra mobs worth a line
WAY_MARGIN = 30.0          # yd added to the spawn radius (pull range)
FP_MIN_LEG = 300.0         # yd: only on real trips
FP_MAX_DETOUR = 150.0      # yd of extra walking to learn a flight point
GRIND_MIN_LEFT = 300.0     # s of session left before a final grind step is worth it
GRIND_NEAR = 500.0         # yd: a gate grind uses a mob cluster this close to the quest giver, else « ici »


def minutes(sec: float) -> int:
    return max(1, int(round(sec / 60.0)))


def saved_text(sec: float) -> str:
    return f"{minutes(sec)} min" if sec >= 90 else f"{int(round(sec))} s"


def mob_name(o: dict) -> str:
    """Creature name from an objective text (« Loup des collines tué : 3/10 » → « Loup des collines »)."""
    t = str(o.get("t") or "").replace(" ", " ")
    t = re.sub(r"\s*:\s*\d+\s*/\s*\d+\s*$", "", t).strip()
    t = re.sub(r"\s+(tuée?s?|slain|killed)$", "", t, flags=re.IGNORECASE).strip()
    return t


def plural(name: str, n: int) -> str:
    """French plural of the head noun (« Loup gris » → « Loups gris »), for n > 1."""
    if n <= 1 or not name:
        return name
    head, _, rest = name.partition(" ")
    if not head[-1:].lower() in ("s", "x", "z"):
        head += "x" if head.lower().endswith(("au", "eu")) else "s"
    return head + (" " + rest if rest else "")


def seg_dist(p: DM.Point, a: DM.Point, b: DM.Point) -> float:
    """Distance (yd) from ``p`` to the segment a→b (same continent; inf otherwise)."""
    if not (p.c == a.c == b.c):
        return math.inf
    dx, dy = b.x - a.x, b.y - a.y
    L2 = dx * dx + dy * dy
    u = 0.0 if L2 <= 0 else max(0.0, min(1.0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / L2))
    return math.hypot(a.x + u * dx - p.x, a.y + u * dy - p.y)


def dist(a: DM.Point, b: DM.Point) -> float:
    return math.hypot(a.x - b.x, a.y - b.y) if a.c == b.c else math.inf


def _obj(pb: DM.Problem, nd: DM.Node) -> dict:
    objs = DM._list(pb.quests[nd.q].raw.get("o"))
    o = objs[nd.o - 1] if 0 < nd.o <= len(objs) else None
    return o if isinstance(o, dict) else {}


def kill_on_the_way(pb: DM.Problem, route: list[int], i: int, a: DM.Point, b: DM.Point,
                    picked_at: dict[int, int]) -> str | None:
    """« Tue en chemin » tip for the walking leg a→b leading to route[i]: a later kill / drop objective of a quest
    already in the log whose spawn cluster lies on the way. The number is half the cluster's spawn points
    (the mobs you run past), capped by the kills still needed."""
    if dist(a, b) < WAY_MIN_LEG:
        return None
    best = None
    for j in range(i + 1, len(route)):
        nd = pb.nodes[route[j]]
        if nd.kind != "obj" or not nd.kills or nd.q == pb.nodes[route[i]].q:
            continue
        if picked_at.get(nd.q, len(route)) >= i:
            continue
        o = _obj(pb, nd)
        r = DM._n(o.get("r"), 60.0) or 60.0
        d = seg_dist(nd.pt, a, b)
        if d > r + WAY_MARGIN:
            continue
        if best is None or d < best[0]:
            best = (d, nd, o)
    if best is None:
        return None
    _, nd, o = best
    sp = int(DM._n(o.get("sp"), 0)) or 4
    n = max(1, min(int(math.ceil(nd.kills - 1e-9)), int(math.ceil(sp / 2))))
    title = pb.quests[nd.q].title
    name = mob_name(o)
    if o.get("k") == "kill" and name:
        return f"Tue en chemin : {n} {plural(name, n)} pour « {title} »"
    extra = f" ({name})" if name else ""
    return f"Tue en chemin : {n} monstres pour « {title} »{extra}"


def unknown_flight_points(pb: DM.Problem) -> list[tuple[int, str, DM.Point]]:
    """Flight points of my faction (or neutral) the character does not know yet, from the shipped graph."""
    nodes = DM.taxi_data().get("nodes") or {}
    known = {int(t) for t in pb.known_taxi}
    fac = {"Alliance": "A", "Horde": "H"}.get(pb.faction, "")
    out = []
    for k, r in nodes.items():
        try:
            tid = int(k)
            side = str(r[4] if len(r) > 4 else "")
            if tid in known or (side and fac and side != fac) or (side and not fac):
                continue
            if str(r[0]).startswith("Transport") or (float(r[2]) == 0.0 and float(r[3]) == 0.0):
                continue                                   # boats / zeppelins, not a flight master
            out.append((tid, str(r[0]).split(",")[0], DM.Point(int(r[1]), float(r[2]), float(r[3]), 0, 0.0, 0.0)))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def flight_point_on_the_way(pb: DM.Problem, a: DM.Point, b: DM.Point, fps: list, used: set[int]
                            ) -> tuple[int, str, DM.Point, float] | None:
    """(taxi id, name, point, extra seconds) of an unknown flight point ≤ FP_MAX_DETOUR yd off the leg a→b."""
    L = dist(a, b)
    if L < FP_MIN_LEG:
        return None
    speed = min(m.speed for m in pb.members) or 7.0
    best = None
    for tid, name, p in fps:
        if tid in used or p.c != a.c:
            continue
        extra = dist(a, p) + dist(p, b) - L
        if extra <= FP_MAX_DETOUR and (best is None or extra < best[3]):
            best = (tid, name, p, extra)
    if best is None:
        return None
    tid, name, p, extra = best
    secs = extra * DM.DETOUR / speed * max(0.6, pb.calib.eff) + DM.TALK
    return tid, name, p, secs


def grind_spot(pb: DM.Problem, level: int, near: DM.Point | None = None, max_d: float = math.inf
               ) -> tuple[DM.Point, str, int, int] | None:
    """Best mob cluster to grind at ``level``: normal mobs of level−2…level+1 from the exported objectives
    (QuestieDB spawns), most spawn points first, then closest to ``level``, then closest to ``near``."""
    best = None
    for q in pb.quests:
        for o in DM._list(q.raw.get("o")):
            if not isinstance(o, dict) or o.get("k") not in ("kill", "item"):
                continue
            ml = int(DM._n(o.get("ml"), 0))
            if not ml or not (level - 2 <= ml <= level + 1) or int(DM._n(o.get("rank"))) != 0:
                continue
            p = DM.make_point(pb.rects, o)
            if p is None:
                continue
            d = dist(near, p) if near is not None else 0.0
            if d > max_d:
                continue
            sp = int(DM._n(o.get("sp"), 0))
            key = (sp, -abs(ml - level), -d)
            if best is None or key > best[0]:
                best = (key, p, mob_name(o) or q.title, ml, sp)
    if best is None:
        return None
    return best[1], best[2], best[3], best[4]


def gate_step(pb: DM.Problem, nd: DM.Node, secs: float, level: int, eta: int) -> dict:
    """Grind step placed before a pick whose required level is one level away."""
    q = pb.quests[nd.q]
    spot = grind_spot(pb, level - 1, nd.pt, GRIND_NEAR)
    p = spot[0] if spot else nd.pt
    m = minutes(secs)
    who = f" ({spot[1]})" if spot else ""
    return {"k": "grind", "map": p.map, "x": p.mx, "y": p.my, "eta": eta,
            "label": f"Tue des monstres ici ~{m} min jusqu'au niveau {level}{who}",
            "tip": f"Niveau {level} requis pour « {q.title} » : {m} min de grind ici plutôt qu'un aller-retour"}


def final_grind_step(pb: DM.Problem, level: int, secs: float, eta: int, grind_xps: float) -> dict | None:
    """« Plus de quête rentable » grind for the rest of the session at the best cluster of the export."""
    spot = grind_spot(pb, level)
    if spot is None or secs < GRIND_MIN_LEFT:
        return None
    p, name, ml, _ = spot
    m = minutes(secs)
    xph = int(round(grind_xps * 3600 / 100.0)) * 100
    return {"k": "grind", "map": p.map, "x": p.mx, "y": p.my, "eta": eta + m,
            "label": f"Grind : tue des {plural(name, 2)} (niv {ml}) ici ~{m} min",
            "tip": f"Plus de quête rentable : grind ici ~{xph:,} XP/h".replace(",", " ")}


def hearth_tip(save: float) -> str:
    return f"Pierre de foyer maintenant : {saved_text(save)} gagnées"

