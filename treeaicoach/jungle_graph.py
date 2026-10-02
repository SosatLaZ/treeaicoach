"""Enemy jungler as LOGICAL PATHS: a jungle graph + a particle filter (v1, opt-in).

Instead of a growing blob, the hidden jungler is a cloud of particles walking on a graph of
the places a jungler actually goes to:

* **Nodes** (:func:`build_nodes`): the 12 camps (both jungles, 2026 positions from
  :data:`treeaicoach.render.CAMPS`), both scuttles, the dragon and baron / grubs / herald pits,
  the six lane gank spots (top / mid / bot, each side of the river) and both fountains.
* **Edges**: every pair of nodes, travel = geodesic polyline on the walkable minimap mask
  (:func:`treeaicoach.fog_tracker.shared_reachability`), computed lazily and cached.
* **Particles** (:class:`JunglerFilter`, ``N_PARTICLES``): each one walks from its current node
  to a chosen target, then performs the target's activity (clear a camp, gank, take an
  objective, recall). The behaviour model picks targets with weights: own camps that are UP
  (per-particle respawn timers, 2026 spawns 0:55 / scuttles 2:55), scuttles, objectives
  (grubs 8:00, dragon 5:00, herald 15:00, baron 20:00), lane ganks, enemy camps (invade, low),
  recall (grows with time), divided by the travel time (close places first).
* **Evidence**: the start is the last sighting / anchor (purchase = fountain, kill, objective,
  respawn: :mod:`treeaicoach.fog_tracker`); a CS tick (Tab) favours particles busy on a camp;
  our vision (ally icons on the minimap, sight radius) down-weights particles we would see.
* **Output**: the 1-2 most likely PATHS (last known spot -> next stops, polyline), each with its
  probability and the ETA to me; ``p_reach(me, 8 s)`` for the gank early warning; a heat map
  (particle splat) that keeps the ``FogEstimate.heat`` interface used by danger.py.

Pure numpy / OpenCV, deterministic (seeded), never raises from the public methods.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Any, Sequence

import cv2
import numpy as np

from treeaicoach import geometry

log = logging.getLogger(__name__)

N_PARTICLES = 300
#: Walking speed (normalized / s): 345 u/s + jungle pet early, 390 + after boots (~2:30).
SPEED_EARLY = 360.0 / geometry.MAP_GAME_UNITS
SPEED_LATE = 400.0 / geometry.MAP_GAME_UNITS
BOOTS_GT = 150.0
#: Activities (s).
CLEAR_S = {"red": 13.0, "blue": 13.0, "krugs": 14.0, "raptors": 11.0, "wolves": 10.0,
           "gromp": 10.0, "scuttle": 8.0}
GANK_S = 10.0
OBJ_S = {"dragon": 25.0, "baron": 30.0}
RECALL_S = 9.0
#: Respawn of the small camps / buffs / scuttle (s) and first spawns (game time).
CAMP_RESPAWN_S = 135.0
BUFF_RESPAWN_S = 300.0
SCUTTLE_RESPAWN_S = 150.0
CAMP_SPAWN_GT = 55.0
SCUTTLE_SPAWN_GT = 175.0
#: Sight radius of an ally icon (normalized) and the weight of a particle inside it.
SIGHT_R = 0.075
SEEN_W = 0.15
#: CS tick: weight of particles NOT on a camp.
FARM_MISS_W = 0.2
#: Gank-warning horizon (s) and the Flash allowance (normalized).
REACH_S = 8.0
#: A path shows its second stop when at least this share of its particles agree on it.
SECOND_STOP_SHARE = 0.45
#: Behaviour weights of a gank spot with / without one of our laners on it.
GANK_W = 0.3
GANK_W_EMPTY = 0.06
#: A lane gank spot follows our laner closest to it within this distance.
VICTIM_R = 0.2
FLASH = 0.027
GRID = 128


@dataclass(frozen=True)
class Node:
    name: str
    uv: tuple[float, float]
    kind: str            # camp | scuttle | pit | gank | fountain
    owner: str | None    # camp owner team ("ORDER" / "CHAOS"), lane for gank spots


@dataclass(frozen=True)
class JunglePath:
    """One likely path of the hidden jungler (for the overlay)."""

    points: tuple[tuple[float, float], ...]    # polyline, normalized
    prob: float
    label: str                                  # e.g. "camp -> gank top"
    target: str                                 # node name of the last stop
    eta_me: float | None = None                 # s until he can be on me along this path


def build_nodes() -> list[Node]:
    from treeaicoach.render import CAMPS

    out: list[Node] = []
    for u, v, name in CAMPS:
        uv = (float(u), float(v))
        if name == "scuttle":
            out.append(Node("scuttle_top" if u + v < 1.0 else "scuttle_bot", uv, "scuttle", None))
        elif name in ("dragon", "baron"):
            out.append(Node(name, uv, "pit", None))
        else:
            owner = "ORDER" if v > u else "CHAOS"
            out.append(Node(f"{name}_{owner}", uv, "camp", owner))
    # lane gank spots: each lane, on each side of the river
    spots = {
        "top_ORDER": (0.10, 0.26), "top_CHAOS": (0.26, 0.10),
        "mid_ORDER": (0.44, 0.56), "mid_CHAOS": (0.56, 0.44),
        "bot_ORDER": (0.74, 0.90), "bot_CHAOS": (0.90, 0.74),
    }
    for k, uv in spots.items():
        lane, side = k.split("_")
        out.append(Node(f"gank_{k}", uv, "gank", f"{lane}:{side}"))
    out.append(Node("fountain_ORDER", geometry.BLUE_FOUNTAIN, "fountain", "ORDER"))
    out.append(Node("fountain_CHAOS", geometry.RED_FOUNTAIN, "fountain", "CHAOS"))
    return out


class JungleGraph:
    """Nodes + lazily computed geodesic edges (polyline, length)."""

    def __init__(self, reach: Any = None) -> None:
        self._reach = reach
        self.nodes = build_nodes()
        self.index = {n.name: i for i, n in enumerate(self.nodes)}
        self.uv = np.asarray([n.uv for n in self.nodes], np.float64)
        n = len(self.nodes)
        self._fields: dict[int, np.ndarray] = {}
        self._len = np.full((n, n), np.nan)
        self._poly: dict[tuple[int, int], tuple[tuple[float, float], ...]] = {}
        self._lock = threading.Lock()

    def reach(self) -> Any:
        if self._reach is None:
            from treeaicoach.fog_tracker import shared_reachability

            self._reach = shared_reachability()
        return self._reach

    def field(self, j: int) -> np.ndarray:
        f = self._fields.get(j)
        if f is None:
            f = self._fields[j] = self.reach().distance_field(tuple(self.uv[j]))
        return f

    def dist_from_point(self, uv: Sequence[float], j: int) -> float:
        """Geodesic distance point -> node j (normalized)."""
        f = self.field(j)
        cx, cy = self.reach().snap((float(uv[0]), float(uv[1])))
        d = float(f[cy, cx])
        return d if math.isfinite(d) else math.dist(uv, self.uv[j]) * 1.3

    def lengths_to(self, j: int) -> np.ndarray:
        """Geodesic length of every node -> node j."""
        with self._lock:
            col = self._len[:, j]
            if np.isnan(col).any():
                for i in range(len(self.nodes)):
                    self._len[i, j] = self.dist_from_point(self.uv[i], j)
            return self._len[:, j].copy()

    def polyline(self, a: Sequence[float], j: int) -> tuple[tuple[float, float], ...]:
        """Geodesic polyline from point ``a`` to node ``j`` (descent on node j's field)."""
        reach = self.reach()
        f = self.field(j)
        G = f.shape[0]
        cx, cy = reach.snap((float(a[0]), float(a[1])))
        pts = [(float(a[0]), float(a[1]))]
        for _ in range(4 * G):
            f0 = f[cy, cx]
            if not math.isfinite(f0) or f0 <= 0:
                break
            best = (f0, cx, cy)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    x, y = cx + dx, cy + dy
                    if (dx or dy) and 0 <= x < G and 0 <= y < G and f[y, x] < best[0]:
                        best = (f[y, x], x, y)
            if best[1:] == (cx, cy):
                break
            _, cx, cy = best
            pts.append(((cx + 0.5) / G, (cy + 0.5) / G))
        pts.append(tuple(self.uv[j]))
        # light simplification: keep every 3rd point
        if len(pts) > 6:
            pts = pts[:1] + pts[1:-1:3] + pts[-1:]
        return tuple(pts)


_SHARED: dict[str, JungleGraph] = {}
_SHARED_LOCK = threading.Lock()


def shared_graph() -> JungleGraph:
    with _SHARED_LOCK:
        g = _SHARED.get("g")
        if g is None:
            g = _SHARED["g"] = JungleGraph()
        return g


def speed_at(gt: float | None) -> float:
    return SPEED_EARLY if gt is not None and gt < BOOTS_GT else SPEED_LATE


class JunglerFilter:
    """Particle filter of the hidden enemy jungler on the :class:`JungleGraph`."""

    def __init__(self, graph: JungleGraph | None = None, n: int = N_PARTICLES, seed: int = 7) -> None:
        self._g = graph
        self.n = int(n)
        self._seed = seed
        self._lock = threading.Lock()
        self.team: str | None = None
        self.start_key: Any = None
        self._ok = False

    @property
    def graph(self) -> JungleGraph:
        if self._g is None:
            self._g = shared_graph()
        return self._g

    # ------------------------------------------------------------------ state
    def reset(self, uv: Sequence[float], gt: float, team: str | None, key: Any = None,
              camps_up: np.ndarray | None = None, since_recall: float | None = None) -> None:
        """New start: he was at ``uv`` at game time ``gt``. ``camps_up`` / ``since_recall``:
        memory of the previous filter (:meth:`memory`) - camps he cleared stay down."""
        g = self.graph
        n, m = self.n, len(g.nodes)
        self.rng = np.random.default_rng(self._seed)
        self.team = geometry.normalize_team(team)
        self.start_key = key
        self.start_uv = (float(uv[0]), float(uv[1]))
        self.gt = float(gt)
        self.w = np.full(n, 1.0 / n)
        #: Node positions for this filter: gank spots follow our laners (set_victims).
        self.node_uv = g.uv.copy()
        self.src = np.tile(np.asarray(self.start_uv), (n, 1))     # leg start point
        self.tgt = np.full(n, -1, np.int64)                       # target node (-1: choose)
        self.leg_t = np.zeros(n)                                  # leg duration (s)
        self.prog = np.zeros(n)                                   # time spent on the leg
        self.dwell = np.zeros(n)                                  # activity time left at target
        self.busy = np.zeros(n, bool)                             # doing an activity
        self.first = np.full(n, -1, np.int64)                     # first stop (for path labels)
        self.second = np.full(n, -1, np.int64)
        # per particle, game time when each node is up again (camps / scuttles / pits)
        up = np.zeros(m)
        for i, nd in enumerate(g.nodes):
            if nd.kind == "camp":
                up[i] = CAMP_SPAWN_GT
            elif nd.kind == "scuttle":
                up[i] = SCUTTLE_SPAWN_GT
            elif nd.name == "dragon":
                up[i] = 300.0
            elif nd.name == "baron":
                up[i] = 480.0
        if camps_up is not None and camps_up.shape == up.shape:
            up = np.maximum(up, camps_up)
        self.up = np.tile(up, (n, 1))
        self.since_recall = np.full(n, max(0.0, gt - 90.0) if since_recall is None else float(since_recall))
        self._ok = True
        self._choose(np.ones(n, bool))

    def memory(self) -> tuple[np.ndarray | None, float | None]:
        """(expected respawn game time of every node, time since his last recall) to carry
        into the next reset (weighted over the particles)."""
        try:
            if not self._ok:
                return None, None
            return (self.w[:, None] * self.up).sum(axis=0), float((self.w * self.since_recall).sum())
        except Exception:
            return None, None

    def set_victims(self, laners: Sequence[Sequence[float]]) -> None:
        """Our champions' positions (me + visible allies): each lane gank spot moves to the
        laner closest to it (within ``VICTIM_R``) - he ganks people, not fixed points."""
        try:
            if not self._ok:
                return
            g = self.graph
            pts = [(float(p[0]), float(p[1])) for p in laners or ()]
            for j, nd in enumerate(g.nodes):
                if nd.kind != "gank":
                    continue
                best = None
                for p in pts:
                    d = math.dist(p, nd.uv)
                    if d < VICTIM_R and (best is None or d < best[0]):
                        best = (d, p)
                self.node_uv[j] = best[1] if best is not None else g.uv[j]
        except Exception:
            log.debug("set_victims failed", exc_info=True)

    # ------------------------------------------------------------------ behaviour
    def _choose(self, mask: np.ndarray) -> None:
        idx = np.nonzero(mask)[0]
        if idx.size == 0:
            return
        g = self.graph
        gt = self.gt
        spd = speed_at(gt)
        own = self.team
        base = np.zeros(len(g.nodes))
        for j, nd in enumerate(g.nodes):
            if nd.kind == "camp":
                base[j] = 1.0 if nd.owner == own else 0.12
            elif nd.kind == "scuttle":
                base[j] = 0.9
            elif nd.kind == "gank":
                # someone to gank there (a laner of ours moved the spot onto him)?
                manned = math.dist(self.node_uv[j], g.uv[j]) > 1e-3
                base[j] = GANK_W if manned else GANK_W_EMPTY
            elif nd.kind == "pit":
                base[j] = 0.5 if gt >= (300.0 if nd.name == "dragon" else 480.0) - 30 else 0.0
            elif nd.kind == "fountain":
                base[j] = 0.0
        own_f = g.index.get(f"fountain_{own}") if own else None
        # distance from each particle's current point to each node (straight line x 1.25, cheap)
        P = self.src[idx]
        nu = self.node_uv
        d = np.hypot(P[:, None, 0] - nu[None, :, 0], P[:, None, 1] - nu[None, :, 1]) * 1.25
        travel = d / spd
        arrive = gt + travel
        w = np.tile(base, (idx.size, 1))
        # camps / scuttles / pits: must be up when he arrives (+ small wait allowed)
        up = self.up[idx]
        wait = np.maximum(0.0, up - arrive)
        w = w * np.exp(-wait / 8.0)
        w = w / (4.0 + travel) ** 1.6
        w[d < 0.02] = 0.0                         # not the place he stands on
        # recall: grows with time since the last recall
        if own_f is not None:
            rec = np.clip((self.since_recall[idx] - 150.0) / 200.0, 0.0, 1.0) * 0.02
            w[:, own_f] = rec
        s = w.sum(axis=1, keepdims=True)
        s[s <= 0] = 1.0
        w = w / s
        cum = np.cumsum(w, axis=1)
        r = self.rng.random(idx.size)[:, None]
        choice = np.minimum((cum < r).sum(axis=1), len(g.nodes) - 1)
        lens = np.hypot(P[:, 0] - nu[choice, 0], P[:, 1] - nu[choice, 1]) * 1.25
        jitter = self.rng.uniform(0.9, 1.15, idx.size)
        self.tgt[idx] = choice
        self.leg_t[idx] = lens / spd * jitter
        self.prog[idx] = 0.0
        self.busy[idx] = False
        nf = self.first[idx] < 0
        self.first[idx[nf]] = choice[nf]
        ns = (~nf) & (self.second[idx] < 0)
        self.second[idx[ns]] = choice[ns]

    def _arrive(self, mask: np.ndarray) -> None:
        g = self.graph
        idx = np.nonzero(mask)[0]
        for i in idx:
            j = int(self.tgt[i])
            nd = g.nodes[j]
            self.src[i] = self.node_uv[j]
            self.busy[i] = True
            if nd.kind == "camp":
                base = nd.name.split("_")[0]
                self.dwell[i] = CLEAR_S.get(base, 11.0) * self.rng.uniform(0.85, 1.2)
                self.up[i, j] = self.gt + self.dwell[i] + (BUFF_RESPAWN_S if base in ("red", "blue")
                                                         else CAMP_RESPAWN_S)
            elif nd.kind == "scuttle":
                self.dwell[i] = CLEAR_S["scuttle"]
                self.up[i, j] = self.gt + 8.0 + SCUTTLE_RESPAWN_S
            elif nd.kind == "gank":
                self.dwell[i] = GANK_S
            elif nd.kind == "pit":
                self.dwell[i] = OBJ_S.get(nd.name, 25.0)
                self.up[i, j] = self.gt + 300.0
            elif nd.kind == "fountain":
                self.dwell[i] = RECALL_S + 10.0
                self.since_recall[i] = 0.0
            else:
                self.dwell[i] = 3.0

    def step(self, gt: float) -> None:
        """Advance the particles to game time ``gt`` (1 s sub-steps). Never raises."""
        try:
            with self._lock:
                if not self._ok:
                    return
                gt = float(gt)
                while self.gt < gt - 1e-6:
                    dt = min(1.0, gt - self.gt)
                    self.gt += dt
                    self.since_recall += dt
                    moving = ~self.busy
                    self.prog[moving] += dt
                    arrived = moving & (self.prog >= self.leg_t)
                    if arrived.any():
                        self._arrive(arrived)
                    b = self.busy & ~arrived
                    self.dwell[b] -= dt
                    done = self.busy & (self.dwell <= 0)
                    if done.any():
                        self._choose(done)
        except Exception:
            log.exception("JunglerFilter.step failed")
            self._ok = False

    # ------------------------------------------------------------------ evidence
    def positions(self) -> np.ndarray:
        """Current particle positions [n, 2] (straight-line interpolation of the leg)."""
        g = self.graph
        tgt = np.clip(self.tgt, 0, len(g.nodes) - 1)
        f = np.where(self.busy, 1.0, np.clip(self.prog / np.maximum(self.leg_t, 1e-3), 0.0, 1.0))
        return self.src + (self.node_uv[tgt] - self.src) * f[:, None]

    def observe_vision(self, viewers: Sequence[Sequence[float]], radius: float = SIGHT_R) -> None:
        """He is NOT seen while our champions at ``viewers`` see ``radius`` around them."""
        try:
            with self._lock:
                if not self._ok or not viewers:
                    return
                P = self.positions()
                V = np.asarray(viewers, np.float64).reshape(-1, 2)
                d = np.min(np.hypot(P[:, None, 0] - V[None, :, 0], P[:, None, 1] - V[None, :, 1]), axis=1)
                f = np.where(d < radius, SEEN_W, 1.0)
                self._reweight(f)
        except Exception:
            log.debug("observe_vision failed", exc_info=True)

    def observe_farm(self) -> None:
        """His creep score went up: particles busy on a camp / scuttle are favoured."""
        try:
            with self._lock:
                if not self._ok:
                    return
                kinds = np.asarray([self.graph.nodes[max(0, int(j))].kind for j in self.tgt])
                on = self.busy & np.isin(kinds, ("camp", "scuttle", "pit"))
                self._reweight(np.where(on, 1.0, FARM_MISS_W))
        except Exception:
            log.debug("observe_farm failed", exc_info=True)

    def _reweight(self, f: np.ndarray) -> None:
        w = self.w * f
        s = float(w.sum())
        if s <= 1e-12:
            return                      # evidence against everything: ignore it
        self.w = w / s
        ess = 1.0 / float((self.w ** 2).sum())
        if ess < self.n / 3:
            self._resample()

    def _resample(self) -> None:
        idx = self.rng.choice(self.n, self.n, p=self.w)
        for name in ("src", "tgt", "leg_t", "prog", "dwell", "busy", "first", "second", "up",
                     "since_recall"):
            setattr(self, name, getattr(self, name)[idx].copy())
        self.w = np.full(self.n, 1.0 / self.n)

    # ------------------------------------------------------------------ output
    def p_reach(self, me: Sequence[float] | None, horizon_s: float = REACH_S) -> float | None:
        """Probability that he can be on ``me`` within ``horizon_s`` (geodesic, + Flash)."""
        try:
            if not self._ok or me is None:
                return None
            reach = self.graph.reach()
            budget = speed_at(self.gt) * horizon_s + FLASH
            fld = reach.distance_field((float(me[0]), float(me[1])), max_dist=budget + 0.02)
            P = self.positions()
            G = fld.shape[0]
            xs = np.clip((P[:, 0] * G).astype(int), 0, G - 1)
            ys = np.clip((P[:, 1] * G).astype(int), 0, G - 1)
            return float(self.w[fld[ys, xs] <= budget].sum())
        except Exception:
            log.debug("p_reach failed", exc_info=True)
            return None

    def heat(self, grid: int = GRID) -> np.ndarray | None:
        """Particle splat (float32 [grid, grid], sums to 1), or None."""
        try:
            if not self._ok:
                return None
            P = self.positions()
            acc = np.zeros((grid, grid), np.float32)
            xs = np.clip((P[:, 0] * grid).astype(int), 0, grid - 1)
            ys = np.clip((P[:, 1] * grid).astype(int), 0, grid - 1)
            np.add.at(acc, (ys, xs), self.w.astype(np.float32))
            acc = cv2.GaussianBlur(acc, (0, 0), 1.5)
            s = float(acc.sum())
            if s <= 0:
                return None
            out = (acc / s).astype(np.float32)
            out.setflags(write=False)
            return out
        except Exception:
            return None

    def paths(self, me: Sequence[float] | None = None, k: int = 2, min_p: float = 0.12
              ) -> list[JunglePath]:
        """The ``k`` most likely (first stop, second stop) paths from the start point."""
        try:
            if not self._ok:
                return []
            g = self.graph
            acc: dict[tuple[int, int], float] = {}
            for a, b, w in zip(self.first, self.second, self.w):
                key = (int(a), int(b))
                acc[key] = acc.get(key, 0.0) + float(w)
            # merge per first stop when the second differs (keep the best second)
            firsts: dict[int, tuple[float, int, float]] = {}
            for (a, b), p in acc.items():
                tot, bb, pb = firsts.get(a, (0.0, -1, 0.0))
                if b >= 0 and p > pb:
                    bb, pb = b, p
                firsts[a] = (tot + p, bb, pb)
            ranked = sorted(firsts.items(), key=lambda kv: -kv[1][0])
            out: list[JunglePath] = []
            spd = speed_at(self.gt)
            for a, (p, b, pb) in ranked[:k]:
                if p < min_p or a < 0:
                    continue
                pts = list(g.polyline(self.start_uv, a))
                if math.dist(self.node_uv[a], g.uv[a]) > 1e-3:
                    pts.append(tuple(self.node_uv[a]))        # the laner he goes to
                label = _label(g.nodes[a])
                if b >= 0 and pb >= SECOND_STOP_SHARE * p:
                    pts += list(g.polyline(pts[-1], b))[1:]
                    if math.dist(self.node_uv[b], g.uv[b]) > 1e-3:
                        pts.append(tuple(self.node_uv[b]))
                    label += " → " + _label(g.nodes[b])
                eta = None
                if me is not None:
                    d = min(math.dist(q, me) for q in pts)
                    if d < 0.08:
                        # along the path to the closest point, minus the time already spent
                        along, best = 0.0, (math.inf, 0.0)
                        for q0, q1 in zip(pts, pts[1:]):
                            along += math.dist(q0, q1)
                            dd = math.dist(q1, me)
                            if dd < best[0]:
                                best = (dd, along)
                        eta = max(0.0, best[1] / spd - 0.0)
                out.append(JunglePath(tuple((float(x), float(y)) for x, y in pts), float(p), label,
                                      g.nodes[b if b >= 0 and pb >= SECOND_STOP_SHARE * p else a].name, eta))
            return out
        except Exception:
            log.debug("paths failed", exc_info=True)
            return []

    def far_side(self, me: Sequence[float] | None) -> bool:
        """True when >= 75 % of the mass is on the other half of the map (top/bot) than me."""
        try:
            if not self._ok or me is None:
                return False
            P = self.positions()
            mine = np.sign(me[0] + me[1] - 1.0)
            if mine == 0:
                return False
            other = np.sign(P[:, 0] + P[:, 1] - 1.0) == -mine
            return float(self.w[other].sum()) >= 0.75
        except Exception:
            return False


def _label(nd: Node) -> str:
    if nd.kind == "camp":
        return {"red": "rouge", "blue": "bleu", "krugs": "krugs", "raptors": "raptors",
                "wolves": "loups", "gromp": "gromp"}.get(nd.name.split("_")[0], "camp")
    if nd.kind == "scuttle":
        return "crabe"
    if nd.kind == "gank":
        return "gank " + (nd.owner or "?").split(":")[0]
    if nd.kind == "pit":
        return "drake" if nd.name == "dragon" else "nashor"
    return "base"


__all__ = ["JungleGraph", "JunglerFilter", "JunglePath", "Node", "build_nodes", "shared_graph",
           "N_PARTICLES", "REACH_S"]
