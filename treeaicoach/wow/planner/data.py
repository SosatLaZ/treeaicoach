"""Planner input: TreeAIDB.export + learned stats (SavedVariables) + the flight graph (wago client tables).

``load_problem()`` turns the addon export (SCHEMA.md §1) into a :class:`Problem`: quests as requests of task
nodes (pick → objectives → turn-in) with world positions in yards, plus the travel-time matrix (walk with a
detour factor, known flight paths, the hearthstone handled by the solver). Pure data work. ``None`` / empty
problem on bad input, never raises in the app path.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from treeaicoach.wow.planner import models as M

log = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DETOUR = 1.3            # ground path / straight line
FLY_SPEED = 32.0        # yd/s along the spline
FLY_HOP = 5.0           # s per hop (landing / take-off)
FLY_BOARD = 20.0        # s to talk to the flight master
SEA_COST = 600.0        # s to change continent (boat / zeppelin wait + crossing), when no better data
TALK = 5.0              # s at a quest giver


def _n(v: Any, d: float = 0.0) -> float:
    try:
        return d if v is None else float(v)
    except (TypeError, ValueError):
        return d


def _list(v: Any) -> list:
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        try:
            return [v[k] for k in sorted(v, key=lambda k: float(k))]
        except (TypeError, ValueError):
            return list(v.values())
    return []


_TAXI: dict | None = None


def taxi_data() -> dict:
    """The shipped flight graph / map bounds (``data/taxi.json.gz``), {} when missing."""
    global _TAXI
    if _TAXI is None:
        try:
            with gzip.open(os.path.join(DATA_DIR, "taxi.json.gz"), "rt", encoding="utf-8") as fh:
                _TAXI = json.load(fh)
        except Exception:
            log.debug("taxi data missing", exc_info=True)
            _TAXI = {}
    return _TAXI


# ------------------------------------------------------------------ geometry
@dataclass(frozen=True)
class MapRect:
    c: int
    x0: float
    y0: float
    x1: float
    y1: float

    def world(self, x: float, y: float) -> tuple[int, float, float]:
        """Map percent → (continent, worldX, worldY) in yards."""
        mx, my = x / 100.0, y / 100.0
        return self.c, self.x0 + (self.x1 - self.x0) * my, self.y0 + (self.y1 - self.y0) * mx


def map_rects(export_maps: list) -> dict[int, MapRect]:
    out: dict[int, MapRect] = {}
    for mid, r in (taxi_data().get("maps") or {}).items():
        try:
            out[int(mid)] = MapRect(int(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
        except (TypeError, ValueError, IndexError):
            pass
    for m in export_maps:                         # the client's own numbers win
        if isinstance(m, dict) and m.get("id") is not None and _n(m.get("x0")) != _n(m.get("x1")):
            out[int(_n(m["id"]))] = MapRect(int(_n(m.get("c"))), _n(m.get("x0")), _n(m.get("y0")),
                                            _n(m.get("x1")), _n(m.get("y1")))
    return out


@dataclass
class Point:
    c: int
    x: float
    y: float
    map: int
    mx: float          # map percent (for plan steps)
    my: float


def make_point(rects: dict[int, MapRect], p: Any) -> Point | None:
    if not isinstance(p, dict):
        return None
    mid = int(_n(p.get("map")))
    r = rects.get(mid)
    if r is None or p.get("x") is None or p.get("y") is None:
        return None
    x, y = _n(p.get("x")), _n(p.get("y"))
    c, wx, wy = r.world(x, y)
    return Point(c, wx, wy, mid, round(x, 1), round(y, 1))


# ------------------------------------------------------------------ problem model
@dataclass
class Node:
    kind: str                 # "pick" | "obj" | "turn"
    q: int                    # index in Problem.quests
    o: int = 0                # objective index (1-based, obj only)
    pt: Point | None = None
    svc: float = TALK         # median seconds (level of the plan start)
    svc80: float = TALK
    kills: float = 0.0
    ml: int = 0
    elite: bool = False


@dataclass
class Quest:
    id: int
    title: str
    level: int
    req: int
    xp: int
    in_log: bool
    zone: int
    pre: list[int] = field(default_factory=list)      # quest ids, all needed
    pre_any: list[int] = field(default_factory=list)
    excl: list[int] = field(default_factory=list)
    flags: int = 0
    raw: dict = field(default_factory=dict)
    nodes: list[int] = field(default_factory=list)    # node ids in order pick?, obj*, turn
    holders: int = 1                                  # squad members (incl. me) who have / can take it
    galere: M.Galere | None = None
    feas: float = 9.0


@dataclass
class Problem:
    char: str
    me: M.Member
    members: list[M.Member]
    level: int
    xp: int
    rested: int
    start: Point | None
    zone: int
    quests: list[Quest]
    nodes: list[Node]
    calib: M.Calib
    log_cap: int = 20
    log_count: int = 0
    hearth: Point | None = None
    hearth_cd: float = 0.0
    session_min: float = 0.0
    raid: bool = False
    group: int = 1
    known_taxi: list[int] = field(default_factory=list)
    maps: dict[int, str] = field(default_factory=dict)
    bands: dict[int, tuple[int, int]] = field(default_factory=dict)
    src: str = ""
    rects: dict[int, MapRect] = field(default_factory=dict)
    squad_raw: list = field(default_factory=list)
    # filled by build_matrix: index n = start, n+1 = hearth
    D: list[list[float]] = field(default_factory=list)
    via: dict[tuple[int, int], tuple[int, int]] = field(default_factory=dict)
    taxi_names: dict[int, str] = field(default_factory=dict)
    taxi_pos: dict[int, Point] = field(default_factory=dict)

    @property
    def START(self) -> int:
        return len(self.nodes)

    @property
    def BIND(self) -> int:
        return len(self.nodes) + 1


def _members(exp: dict, me: M.Member) -> list[M.Member]:
    out = [me]
    for s in _list(exp.get("squad")):
        if isinstance(s, dict) and s.get("key"):
            out.append(M.Member(str(s["key"]), str(s.get("class") or ""), int(_n(s.get("level"), me.level)),
                                _n(s.get("dps")), _n(s.get("speed"), 7.0) or 7.0))
    return out[:5]


def load_problem(export: dict | None, learn: dict | None = None, char_learn: dict | None = None,
                 group_mode: bool = False, zones: set[int] | None = None) -> Problem | None:
    """Problem for the solver from TreeAIDB.export. ``group_mode`` plans the squad as one unit (union of logs,
    slowest member's speed). ``zones`` keeps only quests of these uiMapIDs (plus the ones in my log)."""
    try:
        return _load(export, learn, char_learn, group_mode, zones)
    except Exception:
        log.debug("planner: bad export", exc_info=True)
        return None


def _load(exp, learn, char_learn, group_mode, zones) -> Problem | None:
    if not isinstance(exp, dict) or int(_n(exp.get("v"))) != 1:
        return None
    pl = exp.get("player") if isinstance(exp.get("player"), dict) else {}
    level = int(_n(pl.get("level"), 1)) or 1
    me = M.Member(str(exp.get("char") or "moi"), str(pl.get("class") or ""), level, _n(pl.get("dps")),
                  _n(pl.get("speed"), 7.0) or 7.0)
    members = _members(exp, me) if group_mode else [me]
    rects = map_rects(_list(exp.get("maps")))
    calib = M.calib_from_learn(learn, char_learn)
    names, bands = {}, {}
    for m in _list(exp.get("maps")):
        if isinstance(m, dict) and m.get("id") is not None:
            names[int(_n(m["id"]))] = str(m.get("name") or "")
            if m.get("lo") and m.get("hi"):
                bands[int(_n(m["id"]))] = (int(_n(m["lo"])), int(_n(m["hi"])))
    start = make_point(rects, pl)
    zone = int(_n(pl.get("map")))
    k = len(members)
    holders_of: dict[int, set[str]] = {}
    squad_done: dict[str, set[int]] = {}
    for s in _list(exp.get("squad")):
        if isinstance(s, dict) and s.get("key"):
            for qid in _list(s.get("q")):
                holders_of.setdefault(int(_n(qid)), set()).add(str(s["key"]))
            squad_done[str(s["key"])] = {int(_n(x)) for x in _list(s.get("done"))}
    pb = Problem(char=me.key, me=me, members=members, level=level, xp=int(_n(pl.get("xp"))),
                 rested=int(_n(pl.get("rested"))), start=start, zone=zone, quests=[], nodes=[], calib=calib,
                 log_cap=max(1, int(_n(pl.get("logMax"), 20)) or 20), log_count=int(_n(pl.get("logCount"))),
                 session_min=_n(pl.get("session")), raid=bool(pl.get("raid")), group=k,
                 known_taxi=[int(_n(t)) for t in _list(pl.get("taxi"))], maps=names, bands=bands,
                 src=str(exp.get("src") or ""), rects=rects,
                 squad_raw=[s for s in _list(exp.get("squad")) if isinstance(s, dict)])
    h = pl.get("hearth") if isinstance(pl.get("hearth"), dict) else None
    if h:
        pb.hearth = make_point(rects, h)
        pb.hearth_cd = max(0.0, _n(h.get("cd")))
    seen: set[int] = set()
    for raw in _list(exp.get("quests")):
        if not isinstance(raw, dict) or raw.get("id") is None:
            continue
        qid = int(_n(raw["id"]))
        if qid in seen:
            continue
        seen.add(qid)
        in_log = raw.get("st") == "log"
        who = {str(w) for w in _list(raw.get("who"))} | holders_of.get(qid, set())
        if not in_log and not (group_mode and who):
            if raw.get("st") != "avail":
                continue
        qzone = int(_n(raw.get("zone"))) or int(_n((raw.get("f") or {}).get("map") if isinstance(raw.get("f"), dict) else 0))
        if zones is not None and not in_log and qzone not in zones:
            continue
        if int(_n(raw.get("dg"))) and not group_mode:
            continue                                   # dungeon quests: squad route only
        lv = int(_n(raw.get("lv"), level)) or level
        xp = int(_n(raw.get("xp"))) or M.quest_xp_estimate(lv)
        q = Quest(qid, str(raw.get("t") or f"Quête {qid}"), lv, int(_n(raw.get("rl"))), xp, in_log, qzone,
                  [int(_n(x)) for x in _list(raw.get("pre"))], [int(_n(x)) for x in _list(raw.get("preAny"))],
                  [int(_n(x)) for x in _list(raw.get("excl"))], int(_n(raw.get("flags"))), raw)
        if group_mode:
            share = bool(q.flags & 8)
            have = (1 if in_log else 0) + len(who)
            can = sum(1 for m in members[1:] if m.key not in who and m.level >= q.req
                      and qid not in squad_done.get(m.key, set()))
            q.holders = max(1, min(k, have + (can if share or not in_log else 0)))
            if not in_log and not who:
                q.holders = sum(1 for m in members if m.level >= q.req) or 1
        q.galere = M.galere(raw, members, calib, qzone)
        qi = len(pb.quests)
        nodes: list[Node] = []
        if not in_log:
            sp = make_point(rects, raw.get("s"))
            if sp is None:
                continue                               # never invent a place
            nodes.append(Node("pick", qi, 0, sp))
        done = bool(int(_n(raw.get("done"))))
        ok = True
        if not done:
            for oi, o in enumerate(_list(raw.get("o")), 1):
                if not isinstance(o, dict):
                    continue
                need = int(_n(o.get("n"), 1)) - int(_n(o.get("have")))
                if need <= 0:
                    continue
                op = make_point(rects, o)
                if op is None:
                    ok = False
                    break
                ot = M.objective_time(o, me.cls, level, calib, me.dps, k, qzone)
                ml = int(_n(o.get("ml"), level)) or level
                nodes.append(Node("obj", qi, oi, op, ot.median, ot.p80, ot.kills if o.get("k") in ("kill", "item") else 0,
                                  ml, int(_n(o.get("rank"))) in (1, 2, 3)))
        fp = make_point(rects, raw.get("f"))
        if not ok or fp is None:
            continue
        nodes.append(Node("turn", qi, 0, fp))
        feas = 9.0
        for nd in nodes:
            if nd.kind == "obj" and nd.kills:
                feas = min(feas, M.feasibility(members, nd.ml, 1 if nd.elite else 0))
        q.feas = feas
        if feas < 0.6 or M.quest_color(level, lv) == "red" and k == 1:
            continue                                   # impossible: never planned
        base = len(pb.nodes)
        q.nodes = list(range(base, base + len(nodes)))
        pb.nodes.extend(nodes)
        pb.quests.append(q)
    build_matrix(pb)
    return pb


# ------------------------------------------------------------------ travel matrix
def build_matrix(pb: Problem) -> None:
    """All-pairs travel seconds between nodes (+ start, + hearth bind point): walk with a detour factor at
    the slowest member's speed, or walk → known flight path(s) → walk when faster. × E_player."""
    import numpy as np  # noqa: PLC0415
    pts = [nd.pt for nd in pb.nodes] + [pb.start or (pb.nodes[0].pt if pb.nodes else None),
                                         pb.hearth or pb.start or (pb.nodes[0].pt if pb.nodes else None)]
    n = len(pts)
    if n == 0 or pts[0] is None:
        pb.D = [[0.0] * n for _ in range(n)]
        return
    speed = min(m.speed for m in pb.members) or 7.0
    det = DETOUR
    if pb.calib.detour:
        det = sum(pb.calib.detour.values()) / len(pb.calib.detour)
    eff = max(0.6, pb.calib.eff)
    xs = np.array([p.x for p in pts]); ys = np.array([p.y for p in pts]); cs = np.array([p.c for p in pts])
    W = np.hypot(xs[:, None] - xs[None, :], ys[:, None] - ys[None, :]) * det / speed
    W = np.where(cs[:, None] == cs[None, :], W, W.clip(max=3000.0 * det / speed) + SEA_COST)
    D = W.copy()
    via: dict[tuple[int, int], tuple[int, int]] = {}
    tx = taxi_data()
    nodes = tx.get("nodes") or {}
    known = [t for t in pb.known_taxi if str(t) in nodes]
    if len(known) >= 2:
        idx = {t: i for i, t in enumerate(known)}
        m = len(known)
        F = np.full((m, m), np.inf)
        np.fill_diagonal(F, 0.0)
        for e in tx.get("edges") or []:
            a, b, length = int(e[0]), int(e[1]), float(e[2])
            if a in idx and b in idx:
                t = pb.calib.flights.get((a, b), length / FLY_SPEED + FLY_HOP)
                F[idx[a], idx[b]] = min(F[idx[a], idx[b]], t)
        for kk in range(m):                             # Floyd–Warshall (multi-hop routes)
            F = np.minimum(F, F[:, kk:kk + 1] + F[kk:kk + 1, :])
        tpos = []
        for t in known:
            r = nodes[str(t)]
            p = Point(int(r[1]), float(r[2]), float(r[3]), 0, 0.0, 0.0)
            pb.taxi_names[t] = str(r[0]).split(",")[0]
            pb.taxi_pos[t] = p
            tpos.append(p)
        txs = np.array([p.x for p in tpos]); tys = np.array([p.y for p in tpos]); tcs = np.array([p.c for p in tpos])
        WT = np.hypot(xs[:, None] - txs[None, :], ys[:, None] - tys[None, :]) * det / speed
        WT = np.where(cs[:, None] == tcs[None, :], WT, np.inf)          # n × m
        A = (WT[:, :, None] + F[None, :, :] + FLY_BOARD)                # n × m × m : to FM i, fly to j
        best_j = A.min(axis=1)                                          # n × m : arrive at FM j
        arg_i = A.argmin(axis=1)
        tot = best_j[:, None, :] + WT[None, :, :]                       # n × n × m
        V = tot.min(axis=2)
        arg_j = tot.argmin(axis=2)
        better = V + 30.0 < D
        D = np.where(better, V, D)
        for a, b in zip(*np.nonzero(better)):
            j = int(arg_j[a, b])
            via[(int(a), int(b))] = (known[int(arg_i[a, j])], known[j])
    pb.D = (D * eff).tolist()
    pb.via = via
