"""Early-game jungle clear model: WHERE in his fog region the enemy jungler probably is.

The fog region (:mod:`treeaicoach.fog_tracker`) is an honest *bound* (every place he can
have reached); inside it, positions are not equally likely. In the first minutes nearly
every jungler follows one of a few standard clears, at a pace that varies by champion and
player. :class:`JunglePathModel` turns that knowledge into a probability heat map:

* **Hypotheses** = route x pace x start offset. Routes (camp names; each team's camps from
  :data:`treeaicoach.render.CAMPS`): four full clears (red / krugs / raptors side first or
  blue / gromp / wolves side first, ending on the scuttle of the last side, which spawns at
  2:55) and six level-3 gank paths (3 camps, then mid or one side lane at ~2:10-2:40, then
  the scuttle). Walking legs follow the geodesic shortest path on the walkable grid at the
  early nominal speed; camps take a typical clear time; camps spawn at 0:55 (season 2026). Pace
  scales every duration after the camp spawn (0.85..1.18), the offset shifts the start (-8 / 0 / +8 s).
* **Evidence** (all public): a sighting of the jungler (Gaussian likelihood around each
  hypothesis' position; a sighting no hypothesis explains, e.g. an invade, *breaks* the
  model: no heat any more), his creep score going up (Tab, :mod:`treeaicoach.jungle_intel`:
  hypotheses that finish a camp in that window are favoured; the size of the jump is never
  used), a purchase / death (he left his route: model off).
* :meth:`JunglePathModel.heat` = mixture of Gaussian splats at the hypotheses' positions,
  multiplied by the fog region (hard bound) and mixed with ``UNIFORM_MIX`` of the uniform
  region (out-of-model behaviour). ``None`` when the model has nothing to say (before 0:20,
  after ``END_GT`` or when the routes are over, broken model): the region stays uniform.

Pure numpy / OpenCV; nothing raises from the public methods.
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

#: Camps spawn at 0:55 and scuttles at 2:55 since patch 26.1 (35 s earlier than 1:30 / 3:30;
#: krugs / gromp at 1:07 - the 12 s difference is inside the pace / offset spread) (game time, s).
CAMP_SPAWN_GT = 55.0
SCUTTLE_SPAWN_GT = 175.0
#: The jungler leaves his fountain around 0:15 (shopping) and walks to his first camp.
LEAVE_FOUNTAIN_GT = 15.0
#: Clear times (s) at a normal pace; the first camp of the clear is slower (level 1).
CLEAR_S = {"red": 12.0, "blue": 12.0, "krugs": 13.0, "raptors": 10.0, "wolves": 10.0,
           "gromp": 10.0, "scuttle": 8.0}
FIRST_CAMP_EXTRA_S = 8.0
GANK_S = 12.0
#: Walking speed early (normalized units / s): 345 units / s + the jungle pet's bonus.
WALK_SPEED = 360.0 / geometry.MAP_GAME_UNITS
#: Pace factors (duration multipliers after 1:30) and their prior weights; start offsets (s).
PACES = (0.85, 0.93, 1.0, 1.08, 1.18)
PACE_PRIOR = (0.1, 0.22, 0.36, 0.22, 0.1)
OFFSETS = (-8.0, 0.0, 8.0)
#: Gaussian splat of one hypothesis (normalized), observation noise of a sighting.
SPLAT_SIGMA = 0.022
OBS_SIGMA = 0.035
#: A sighting farther than this from every hypothesis (in OBS_SIGMA) breaks the model.
BREAK_SIGMAS = 3.2
#: CS went up: hypotheses without a camp completion in the window keep this factor.
FARM_MISS = 0.25
FARM_SLACK_S = 4.0
#: Share of the uniform region in the heat (honesty for out-of-model behaviour).
UNIFORM_MIX = 0.15
#: Model valid from START_GT to END_GT (game time); off when the routes ended this long ago.
START_GT = 20.0
END_GT = 300.0
ROUTE_END_GRACE_S = 20.0
GRID = 128

#: Lane gank points (normalized): the fights happen there.
GANK_POINTS = {"top": (0.16, 0.16), "mid": (0.5, 0.5), "bot": (0.84, 0.84)}

#: (name, camp sequence, prior weight). "@lane:X" = gank the lane on side X of the jungle
#: ("red_side" = the red buff's quadrant, "blue_side" = the blue buff's, "mid").
ROUTES: tuple[tuple[str, tuple[str, ...], float], ...] = (
    ("full_red_start", ("red", "krugs", "raptors", "wolves", "blue", "gromp", "scuttle"), 0.17),
    ("full_blue_start", ("blue", "gromp", "wolves", "raptors", "red", "krugs", "scuttle"), 0.17),
    ("full_gromp_start", ("gromp", "blue", "wolves", "raptors", "red", "krugs", "scuttle"), 0.10),
    ("full_krugs_start", ("krugs", "red", "raptors", "wolves", "blue", "gromp", "scuttle"), 0.10),
    ("lvl3_red_mid", ("red", "krugs", "raptors", "@lane:mid", "scuttle"), 0.08),
    ("lvl3_blue_mid", ("blue", "gromp", "wolves", "@lane:mid", "scuttle"), 0.08),
    ("lvl3_red_cross", ("red", "krugs", "raptors", "@lane:blue_side", "scuttle"), 0.075),
    ("lvl3_blue_cross", ("blue", "gromp", "wolves", "@lane:red_side", "scuttle"), 0.075),
    ("lvl3_red_same", ("red", "krugs", "raptors", "@lane:red_side", "scuttle"), 0.075),
    ("lvl3_blue_same", ("blue", "gromp", "wolves", "@lane:blue_side", "scuttle"), 0.075),
)


def _camps_of(team: str) -> dict[str, tuple[float, float]]:
    """Camp name -> uv of ``team``'s jungle (+ "scuttle_top" / "scuttle_bot")."""
    from treeaicoach.render import CAMPS

    out: dict[str, tuple[float, float]] = {}
    for u, v, name in CAMPS:
        if name == "scuttle":
            out["scuttle_top" if u + v < 1.0 else "scuttle_bot"] = (float(u), float(v))
            continue
        if name in ("dragon", "baron"):
            continue
        owner = "ORDER" if v > u else "CHAOS"
        if owner == team:
            out[name] = (float(u), float(v))
    return out


@dataclass
class _Route:
    name: str
    prior: float
    ts: np.ndarray            # relative time (s after CAMP_SPAWN_GT at pace 1), increasing
    us: np.ndarray
    vs: np.ndarray
    done: np.ndarray          # relative times of camp completions
    end: float                # relative time of the end of the route
    pre_t: np.ndarray         # pre-spawn walk: absolute game times
    pre_u: np.ndarray
    pre_v: np.ndarray


class JunglePathModel:
    """Probability heat map of the enemy jungler's early clear (see module docstring)."""

    def __init__(self, reach: Any = None) -> None:
        self._lock = threading.Lock()
        self._reach = reach
        self._routes: dict[str, list[_Route]] = {}
        self.reset()

    # ------------------------------------------------------------------ state
    def reset(self) -> None:
        with getattr(self, "_lock", threading.Lock()):
            self.team: str | None = None
            self.broken: str | None = None          # why the model stopped ("invade", ...)
            self._w: np.ndarray | None = None       # [routes, paces, offsets]
            self._cache: tuple | None = None
            self.observations = 0

    def set_team(self, team: str | None) -> None:
        """The enemy jungler's team ("ORDER" / "CHAOS"). Never raises."""
        try:
            team = geometry.normalize_team(team)
            with self._lock:
                if team == self.team:
                    return
                self.team = team
                self._w = None
                self._cache = None
                self.broken = None
                if team is not None and team not in self._routes:
                    self._routes[team] = self._build(team)
        except Exception:
            log.exception("JunglePathModel.set_team failed")
            self.team = None

    def _weights(self) -> np.ndarray | None:
        routes = self._routes.get(self.team or "")
        if not routes:
            return None
        if self._w is None:
            pr = np.asarray([r.prior for r in routes], np.float64)
            w = pr[:, None, None] * np.asarray(PACE_PRIOR)[None, :, None] * \
                np.ones((1, 1, len(OFFSETS)))
            self._w = w / w.sum()
        return self._w

    # ------------------------------------------------------------------ routes
    def _reachability(self) -> Any:
        if self._reach is None:
            from treeaicoach.fog_tracker import shared_reachability

            self._reach = shared_reachability()
        return self._reach

    def _path(self, a: tuple[float, float], b: tuple[float, float]) -> list[tuple[float, float]]:
        """Geodesic path a -> b (grid descent on the distance field from b)."""
        reach = self._reachability()
        field = reach.distance_field(b)
        G = field.shape[0]
        cx, cy = reach.snap(a)
        pts = [a]
        for _ in range(4 * G):
            f0 = field[cy, cx]
            if not math.isfinite(f0) or f0 <= 0:
                break
            best = (f0, cx, cy)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    x, y = cx + dx, cy + dy
                    if (dx or dy) and 0 <= x < G and 0 <= y < G and field[y, x] < best[0]:
                        best = (field[y, x], x, y)
            if best[1:] == (cx, cy):
                break
            _, cx, cy = best
            pts.append(((cx + 0.5) / G, (cy + 0.5) / G))
        pts.append(b)
        return pts

    def _build(self, team: str) -> list[_Route]:
        camps = _camps_of(team)
        fountain = geometry.RED_FOUNTAIN if team == "CHAOS" else geometry.BLUE_FOUNTAIN
        red_q_top = camps["red"][0] + camps["red"][1] < 1.0      # red buff quadrant on top?
        lanes = {"mid": GANK_POINTS["mid"],
                 "red_side": GANK_POINTS["top" if red_q_top else "bot"],
                 "blue_side": GANK_POINTS["bot" if red_q_top else "top"]}
        out: list[_Route] = []
        for name, seq, prior in ROUTES:
            ts, us, vs, done = [0.0], [], [], []
            t = 0.0
            first = camps[seq[0]]
            us.append(first[0])
            vs.append(first[1])
            pos = first
            for k, step in enumerate(seq):
                if step.startswith("@lane:"):
                    target = lanes[step.split(":", 1)[1]]
                    dur = GANK_S
                elif step == "scuttle":
                    st, sb = camps["scuttle_top"], camps["scuttle_bot"]
                    target = st if math.dist(pos, st) < math.dist(pos, sb) else sb
                    dur = CLEAR_S["scuttle"]
                else:
                    target = camps[step]
                    dur = CLEAR_S[step] + (FIRST_CAMP_EXTRA_S if k == 0 else 0.0)
                if k > 0:
                    path = self._path(pos, target)
                    for p0, p1 in zip(path, path[1:]):
                        t += math.dist(p0, p1) / WALK_SPEED
                        ts.append(t)
                        us.append(p1[0])
                        vs.append(p1[1])
                if step == "scuttle":
                    # wait for its spawn (relative to the pace-1 timeline)
                    t = max(t, SCUTTLE_SPAWN_GT - CAMP_SPAWN_GT)
                t += dur
                ts.append(t)
                us.append(target[0])
                vs.append(target[1])
                if not step.startswith("@lane:"):
                    done.append(t)
                pos = target
            # pre-spawn: fountain -> first camp, then wait
            path = self._path(fountain, first)
            pt, pu, pv = [LEAVE_FOUNTAIN_GT], [fountain[0]], [fountain[1]]
            tt = LEAVE_FOUNTAIN_GT
            for p0, p1 in zip(path, path[1:]):
                tt += math.dist(p0, p1) / WALK_SPEED
                pt.append(tt)
                pu.append(p1[0])
                pv.append(p1[1])
            pt.append(max(tt + 0.1, CAMP_SPAWN_GT))
            pu.append(first[0])
            pv.append(first[1])
            ts_a = np.maximum.accumulate(np.asarray(ts, np.float64))
            out.append(_Route(name, prior, ts_a, np.asarray(us), np.asarray(vs),
                              np.asarray(done), float(ts_a[-1]),
                              np.maximum.accumulate(np.asarray(pt)), np.asarray(pu),
                              np.asarray(pv)))
        return out

    # ------------------------------------------------------------------ hypotheses
    def _rel_times(self, gt: float) -> np.ndarray:
        """Relative route time of every (pace, offset) at game time ``gt``: [P, O]."""
        p = np.asarray(PACES)[:, None]
        o = np.asarray(OFFSETS)[None, :]
        return (gt - CAMP_SPAWN_GT - o) / p

    def positions(self, gt: float) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """(u [R,P,O], v [R,P,O], ended [R,P,O] bool) of every hypothesis at ``gt``."""
        routes = self._routes.get(self.team or "")
        if not routes:
            return None
        rel = self._rel_times(gt)
        U = np.empty((len(routes),) + rel.shape)
        V = np.empty_like(U)
        E = np.zeros(U.shape, bool)
        for i, r in enumerate(routes):
            if gt < CAMP_SPAWN_GT:
                U[i] = np.interp(gt, r.pre_t, r.pre_u)
                V[i] = np.interp(gt, r.pre_t, r.pre_v)
                continue
            x = np.clip(rel, 0.0, r.end)
            U[i] = np.interp(x, r.ts, r.us)
            V[i] = np.interp(x, r.ts, r.vs)
            E[i] = rel > r.end + ROUTE_END_GRACE_S
        return U, V, E

    # ------------------------------------------------------------------ evidence
    def observe_seen(self, gt: float, uv: Sequence[float]) -> None:
        """The jungler was seen at ``uv`` at game time ``gt``. Never raises."""
        try:
            with self._lock:
                w = self._weights()
                if w is None or self.broken or not (START_GT <= gt <= END_GT):
                    return
                pos = self.positions(gt)
                if pos is None:
                    return
                U, V, E = pos
                d2 = (U - float(uv[0])) ** 2 + (V - float(uv[1])) ** 2
                live = ~E
                if not live.any() or float(np.sqrt(d2[live].min())) > BREAK_SIGMAS * OBS_SIGMA:
                    self.broken = "off_route"
                    log.debug("Jungle path model: sighting at %s (%.0f s) off every route", uv, gt)
                    return
                lik = np.exp(-0.5 * d2 / OBS_SIGMA ** 2) * live + 1e-6
                w = w * lik
                self._w = w / w.sum()
                self._cache = None
                self.observations += 1
        except Exception:
            log.exception("JunglePathModel.observe_seen failed")

    def observe_farm(self, gt0: float, gt1: float) -> None:
        """His creep score went up between game times ``gt0`` and ``gt1``. Never raises."""
        try:
            with self._lock:
                w = self._weights()
                if w is None or self.broken or gt1 < CAMP_SPAWN_GT or gt0 > END_GT:
                    return
                routes = self._routes[self.team or ""]
                p = np.asarray(PACES)[:, None]
                o = np.asarray(OFFSETS)[None, :]
                f = np.full(w.shape, FARM_MISS)
                for i, r in enumerate(routes):
                    if r.done.size == 0:
                        continue
                    when = CAMP_SPAWN_GT + o[None] + p[None] * r.done[:, None, None]   # [D,P,O]
                    hit = ((when >= gt0 - FARM_SLACK_S) & (when <= gt1 + FARM_SLACK_S)).any(axis=0)
                    f[i][hit] = 1.0
                w = w * f
                self._w = w / w.sum()
                self._cache = None
                self.observations += 1
        except Exception:
            log.exception("JunglePathModel.observe_farm failed")

    def observe_left_route(self, reason: str) -> None:
        """He bought something / died: the early clear is over."""
        with self._lock:
            if not self.broken:
                self.broken = str(reason or "left")
                self._cache = None

    # ------------------------------------------------------------------ output
    def active(self, gt: float | None) -> bool:
        """True while the model has an opinion at game time ``gt`` (early game, not broken,
        most hypotheses still on their route)."""
        if not (gt is not None and math.isfinite(gt) and START_GT <= gt <= END_GT
                and not self.broken and self.team in self._routes):
            return False
        w = self._weights()
        pos = self.positions(float(gt))
        return w is not None and pos is not None and float(w[~pos[2]].sum()) >= 0.5

    def heat(self, gt: float | None, region: np.ndarray | None = None,
             grid: int = GRID) -> np.ndarray | None:
        """Probability map ``float32 [grid, grid]`` (sums to 1 over the region; 0 outside), or
        None when the model has no opinion. ``region``: the fog bound (bool). Never raises."""
        try:
            with self._lock:
                if not self.active(gt):
                    return None
                key = (round(float(gt) * 4), id(region), grid)
                if self._cache is not None and self._cache[0] == key:
                    return self._cache[1]
                w = self._weights()
                pos = self.positions(float(gt))
                if w is None or pos is None:
                    return None
                U, V, E = pos
                live = ~E
                acc = np.zeros((grid, grid), np.float32)
                cx = np.clip((U[live] * grid).astype(np.int32), 0, grid - 1)
                cy = np.clip((V[live] * grid).astype(np.int32), 0, grid - 1)
                np.add.at(acc, (cy, cx), w[live].astype(np.float32))
                acc = cv2.GaussianBlur(acc, (0, 0), max(0.8, SPLAT_SIGMA * grid))
                bound = np.ones((grid, grid), bool) if region is None else np.asarray(region, bool)
                if bound.shape != (grid, grid):
                    bound = cv2.resize(bound.astype(np.uint8), (grid, grid),
                                       interpolation=cv2.INTER_NEAREST).astype(bool)
                walk = self._reachability().walkable
                if walk.shape == bound.shape:
                    bound = bound & walk
                n = int(bound.sum())
                if n == 0:
                    return None
                acc *= bound
                s = float(acc.sum())
                if s <= 1e-9:
                    return None                       # the model disagrees with the bound
                heat = (1.0 - UNIFORM_MIX) * acc / s + UNIFORM_MIX * bound.astype(np.float32) / n
                heat = heat.astype(np.float32)
                heat.setflags(write=False)
                self._cache = (key, heat)
                return heat
        except Exception:
            log.exception("JunglePathModel.heat failed")
            return None

    def summary(self, gt: float | None) -> dict:
        """Most likely route + start side (diagnostics / coach text)."""
        with self._lock:
            w = self._weights()
            routes = self._routes.get(self.team or "")
            if w is None or not routes:
                return {}
            rw = w.sum(axis=(1, 2))
            i = int(np.argmax(rw))
            red_first = sum(float(rw[k]) for k, r in enumerate(routes)
                            if r.name.split("_")[1] in ("red", "krugs"))
            return {"route": routes[i].name, "p_route": float(rw[i]),
                    "p_red_side_start": red_first, "broken": self.broken}


__all__ = ["JunglePathModel", "ROUTES", "UNIFORM_MIX"]
