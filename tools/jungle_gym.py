"""Jungle gym: where is the hidden enemy jungler? Old fog region / heat vs logical paths.

Simulated games drive the REAL production pipeline (:class:`treeaicoach.fog_tracker.FogTracker`
+ :class:`treeaicoach.jungle_intel.JungleIntelTracker`, fed through stand-in tracker / Live
Client objects), once with ``jungle_paths`` off (old model: geodesic region, early-clear heat)
and once with it on (particle filter on the jungle graph, :mod:`treeaicoach.jungle_graph`).

**Truth simulator** (independent of the filter's behaviour model): an enemy (CHAOS) jungler
walks geodesic paths on the walkable mask with one of three styles:

* ``farm``: full clears, camps in nearest-up order, scuttles, dragon / grubs, rare ganks;
* ``gank``: 3 camps then a gank on one of our laners (their REAL position), often;
* ``invade``: steals our camps (ORDER jungle) and ganks sometimes.

Camps respawn (small 2:15, buffs 5:00, scuttle 2:30), clear times vary (+-20 %), positions
are jittered, he recalls every 3-5 min (purchase -> Tab items), CS ticks after each camp
(Tab, 1 Hz). Our vision: our 4 laners (lane positions, oscillating) and 2 river wards per
90 s (unknown to the models); sight 0.08, 10 % detection misses.

**Metrics** (hidden ticks only, 1 Hz):

* ``mass05`` / ``mass10``: probability mass within 0.05 / 0.10 of the truth (heat, else the
  region uniformly; 0 when the model shows nothing);
* ``top1`` / ``top2``: the truth's next stop (camp / scuttle / pit / gank / fountain) is the
  model's 1st / one of its 2 first predicted stops (paths model: aggregated particle first
  stops; old model: graph nodes ranked by heat mass around them), checked 10 s after a loss;
* gank warning: production rule of danger.py (``fog_mass_near`` >= 0.35 within 7 s, hidden
  8-60 s) on each model's estimate, plus the paths model's ``p_reach`` (8 s) >= 0.3, both
  OR-ed with the shared "visible and <= 8 s away" baseline: ``lead`` (s of warning before the
  contact, median / share >= 5 s) and ``false/10min`` (fog-warning episodes with no contact
  within 15 s).

``python -m tools.jungle_gym [--seeds 4] [--minutes 14] [--real]``; ``--real`` replays the
real Kindred path read from the real report (1 point per minute, LCU truth): 60 s-ahead
prediction from each point. Development tool only.
"""

from __future__ import annotations

import argparse
import math
import random
import zlib
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from treeaicoach import geometry
from treeaicoach.danger import fog_mass_near
from treeaicoach.fog_tracker import FogTracker, shared_reachability
from treeaicoach.jungle_graph import shared_graph
from treeaicoach.jungle_intel import JungleIntelTracker

UV = tuple[float, float]
U_PER_NORM = geometry.MAP_GAME_UNITS
SIGHT = 0.08
WARD_SIGHT = 0.06
MISS = 0.10
CONTACT = 0.05
LEAD_OK_S = 5.0
FALSE_WINDOW_S = 15.0
WARN_MASS = 0.35
WARN_P = 0.30
#: p_reach thresholds swept (lead vs false warnings trade-off).
SWEEP = (0.15, 0.2, 0.3, 0.4, 0.5, 0.6)

#: Real Kindred path (ORDER jungler), LCU per-minute positions read from the real report
#: (2026-09 game, Garen top, image "Vrai parcours de Kindred", 360 px, 1 point = 1 minute).
KINDRED_REAL: tuple[tuple[int, UV], ...] = tuple(
    (m, (x / 360.0, y / 360.0)) for m, (x, y) in enumerate([
        (78, 173), (90, 200), (204, 298), (222, 210), (56, 202), (218, 312), (10, 348),
        (104, 134), (44, 47), (171, 260), (319, 278), (254, 248), (46, 292), (174, 238),
        (222, 238)], start=1))


# ---------------------------------------------------------------------- geodesic walking
_FIELDS: dict[tuple[int, int], np.ndarray] = {}


def geo_path(a: UV, b: UV) -> list[UV]:
    reach = shared_reachability()
    cell = reach.snap(b)
    f = _FIELDS.get(cell)
    if f is None:
        f = reach.distance_field(((cell[0] + 0.5) / reach.grid, (cell[1] + 0.5) / reach.grid))
        if len(_FIELDS) < 400:
            _FIELDS[cell] = f
    G = f.shape[0]
    cx, cy = reach.snap(a)
    pts = [a]
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
    pts.append(b)
    return pts


# ---------------------------------------------------------------------- truth simulator
CAMP_CLEAR = {"red": 14.0, "blue": 14.0, "krugs": 15.0, "raptors": 12.0, "wolves": 11.0,
              "gromp": 11.0, "scuttle": 9.0}


@dataclass
class Leg:
    pts: list[UV]
    stop: str            # node-ish name of the destination ("gank:top", "camp:red_CHAOS"...)
    dwell: float
    act: str             # camp | scuttle | pit | gank | recall | walk


class TruthJungler:
    """Scripted enemy (CHAOS) jungler, independent of the filter's behaviour model."""

    def __init__(self, style: str, rng: random.Random) -> None:
        self.style = style
        self.rng = rng
        self.team = "CHAOS"
        g = shared_graph()
        self.nodes = {n.name: (n.uv[0] + rng.uniform(-0.01, 0.01), n.uv[1] + rng.uniform(-0.01, 0.01))
                      for n in g.nodes}
        self.kind = {n.name: n.kind for n in g.nodes}
        self.owner = {n.name: n.owner for n in g.nodes}
        self.up = {n: (55.0 if k == "camp" else 175.0 if k == "scuttle" else 300.0 if n == "dragon"
                       else 480.0 if n == "baron" else 0.0) for n, k in self.kind.items()}
        self.pos: UV = geometry.RED_FOUNTAIN
        self.leg: Leg | None = None
        self.leg_i = 0
        self.dwell_left = 0.0
        self.busy = False
        self.cs = 0
        self.items = 1
        self.last_recall = 0.0
        self.camps_since_gank = 0
        self.pace = rng.uniform(0.93, 1.08)
        self.next_recall = rng.uniform(200.0, 300.0)

    def speed(self, gt: float) -> float:
        return (370.0 if gt < 150 else 410.0) * self.pace / U_PER_NORM

    def _choose(self, gt: float, laners: dict[str, UV]) -> Leg:
        rng = self.rng
        own = [n for n, k in self.kind.items() if k == "camp" and self.owner[n] == self.team]
        enemy = [n for n, k in self.kind.items() if k == "camp" and self.owner[n] != self.team]
        scut = [n for n, k in self.kind.items() if k == "scuttle"]
        if gt >= self.next_recall:
            return Leg(geo_path(self.pos, geometry.RED_FOUNTAIN), "fountain_CHAOS", 8.0 + rng.uniform(5, 15),
                       "recall")
        p_gank = {"farm": 0.12, "gank": 0.6, "invade": 0.25}[self.style]
        need = {"farm": 4, "gank": 2, "invade": 3}[self.style]
        if gt > 150 and self.camps_since_gank >= need and rng.random() < p_gank and laners:
            lane = rng.choice(sorted(laners))
            tgt = laners[lane]
            return Leg(geo_path(self.pos, tgt), f"gank:{lane}", rng.uniform(6.0, 12.0), "gank")
        for pit, first in (("dragon", 300.0), ("baron", 480.0)):
            if self.up[pit] <= gt + 20 and gt >= first - 10 and rng.random() < (0.35 if self.style == "farm" else 0.2):
                return Leg(geo_path(self.pos, self.nodes[pit]), pit, rng.uniform(20, 35), "pit")
        cands = [n for n in own + scut if self.up[n] <= gt + 12]
        if self.style == "invade" or rng.random() < 0.08:
            cands += [n for n in enemy if self.up[n] <= gt + 12 and (self.style == "invade" or rng.random() < 0.3)]
        if not cands:
            # nothing up: wait in the river / go to the earliest camp
            n = min(own, key=lambda c: self.up[c])
            return Leg(geo_path(self.pos, self.nodes[n]), n, max(0.0, self.up[n] - gt) + CAMP_CLEAR[n.split("_")[0]],
                       "camp")
        d = {n: math.dist(self.pos, self.nodes[n]) for n in cands}
        cands.sort(key=lambda n: d[n] + (0.08 if self.kind[n] == "scuttle" and gt < 300 else 0.0)
                   - (0.15 if self.style == "invade" and self.owner[n] != self.team else 0.0)
                   + rng.uniform(0.0, 0.08))
        n = cands[0]
        base = "scuttle" if self.kind[n] == "scuttle" else n.split("_")[0]
        return Leg(geo_path(self.pos, self.nodes[n]), n, CAMP_CLEAR[base] * rng.uniform(0.8, 1.2) * self.pace,
                   "scuttle" if self.kind[n] == "scuttle" else "camp")

    def step(self, gt: float, dt: float, laners: dict[str, UV]) -> dict[str, Any]:
        """Advance; returns events {"cs": bool, "bought": bool, "contact": lane or None}."""
        ev: dict[str, Any] = {"cs": False, "bought": False}
        if gt < 15.0:
            return ev
        if self.leg is None:
            if gt < 30.0:
                # level 1: first camp (style dependent start side)
                first = self.rng.choice(["red_CHAOS", "blue_CHAOS"] if self.style != "invade"
                                        else ["red_CHAOS", "blue_CHAOS", "raptors_ORDER"])
                self.leg = Leg(geo_path(self.pos, self.nodes[first]), first,
                               max(0.0, 55.0 - gt - 20) + 20.0, "camp")
            else:
                self.leg = self._choose(gt, laners)
            self.leg_i, self.busy = 0, False
        leg = self.leg
        if not self.busy:
            budget = self.speed(gt) * dt
            if leg.act == "gank":   # follow the victim
                lane = leg.stop.split(":")[1]
                if lane in laners:
                    leg.pts[-1] = laners[lane]
            while budget > 0 and self.leg_i < len(leg.pts) - 1:
                a, b = leg.pts[self.leg_i], leg.pts[self.leg_i + 1]
                seg = math.dist(self.pos, b)
                if seg <= budget:
                    self.pos = b
                    budget -= seg
                    self.leg_i += 1
                else:
                    f = budget / max(seg, 1e-9)
                    self.pos = (self.pos[0] + (b[0] - self.pos[0]) * f, self.pos[1] + (b[1] - self.pos[1]) * f)
                    budget = 0
            if self.leg_i >= len(leg.pts) - 1:
                self.busy = True
                self.dwell_left = leg.dwell
                if leg.act in ("camp", "scuttle", "pit") and self.up.get(leg.stop, 0.0) > gt:
                    self.dwell_left += self.up[leg.stop] - gt          # wait for the spawn
        else:
            if leg.act == "gank":
                lane = leg.stop.split(":")[1]
                if lane in laners:
                    v = laners[lane]
                    self.pos = (self.pos[0] + (v[0] - self.pos[0]) * 0.5, self.pos[1] + (v[1] - self.pos[1]) * 0.5)
            self.dwell_left -= dt
            if self.dwell_left <= 0:
                if leg.act in ("camp", "scuttle", "pit"):
                    base = leg.stop.split("_")[0]
                    self.up[leg.stop] = gt + (300.0 if base in ("red", "blue") else 150.0 if leg.act == "scuttle"
                                              else 300.0 if leg.act == "pit" else 135.0)
                    self.cs += 4
                    ev["cs"] = True
                    self.camps_since_gank += 1
                elif leg.act == "gank":
                    self.camps_since_gank = 0
                elif leg.act == "recall":
                    self.pos = geometry.RED_FOUNTAIN
                    self.items += 1
                    ev["bought"] = True
                    self.next_recall = gt + self.rng.uniform(190.0, 300.0)
                self.leg = None
        return ev

    def next_stop(self) -> tuple[str, UV] | None:
        if self.leg is None:
            return None
        s = self.leg.stop
        if s.startswith("gank:"):
            lane = s.split(":")[1]
            return s, self.leg.pts[-1]
        return s, self.nodes.get(s, self.leg.pts[-1])


# ---------------------------------------------------------------------- stand-in API objects
class P:
    def __init__(self, alias: str, team: str) -> None:
        self.champion_alias = alias
        self.champion_name = alias
        self.team = team
        self.scores = {"creepScore": 0}
        self.items: list[int] = [1055]
        self.is_dead = False
        self.level = 1
        self.respawn_timer = 0.0
        self.position = "JUNGLE"
        self.riot_id = alias
        self.summoner_name = alias


class Game:
    def __init__(self, jg: P, me: P) -> None:
        self.jg, self.me = jg, me
        self.enemies = [jg]
        self.allies: list[P] = []
        self.events: list[dict] = []
        self.game_time = 0.0
        self.fetched_at = 0.0

    def enemy_jungler(self) -> P:
        return self.jg

    def player_by_alias(self, alias: str) -> P | None:
        a = "".join(c for c in str(alias).lower() if c.isalnum())
        return self.jg if a == self.jg.champion_alias.lower() else None

    def all_players(self) -> list[P]:
        return [self.me, self.jg]


class T:
    def __init__(self, key: str, alias: str, relation: str) -> None:
        self.key, self.alias, self.relation = key, alias, relation
        self.visible = False
        self.last_seen: float | None = None
        self._pos: UV | None = None
        self._vel: UV = (0.0, 0.0)
        self.has_smite = relation == "enemy"
        self.stacked_with = None

    def position(self) -> UV | None:
        return self._pos

    def velocity(self) -> UV:
        return self._vel


class Tracker:
    def __init__(self) -> None:
        self.jg = T("e0", "Kindred", "enemy")
        self.me_t = T("me", "Garen", "ally")
        self.ally_ts: list[T] = []
        self.ever = False

    def enemies(self, visible_only: bool = True) -> list[T]:
        if not self.ever:
            return []
        return [self.jg] if (self.jg.visible or not visible_only) else []

    def me(self) -> T:
        return self.me_t

    def allies(self, visible_only: bool = True) -> list[T]:
        return list(self.ally_ts)

    def get(self, key: str) -> T | None:
        return self.jg if str(key).lower() in ("e0", "kindred") and self.ever else None

    def tracks(self) -> list[T]:
        return [self.jg, self.me_t, *self.ally_ts]


# ---------------------------------------------------------------------- lanes / vision
LANE_ANCHOR = {"top": (0.11, 0.11), "mid": (0.50, 0.50), "bot": (0.89, 0.89)}
LANE_DIR = {"top": ((0.0, 1.0), (1.0, 0.0)), "mid": ((-0.707, 0.707), (0.707, -0.707)),
            "bot": ((-1.0, 0.0), (0.0, -1.0))}


def laner_pos(lane: str, gt: float, phase: float) -> UV:
    """Our (ORDER) laner: oscillates along his lane around the river crossing, slightly on our side."""
    ax, ay = LANE_ANCHOR[lane]
    s = 0.07 * math.sin(gt / 37.0 + phase) + 0.02           # >0: our side of the lane
    (ox, oy), (cx, cy) = LANE_DIR[lane]
    if s >= 0:
        return (ax + ox * s, ay + oy * s)
    return (ax + cx * (-s), ay + cy * (-s))


@dataclass
class ModelRun:
    name: str
    paths: bool
    fog: FogTracker = field(default=None)          # type: ignore[assignment]
    intel: JungleIntelTracker = field(default=None)  # type: ignore[assignment]
    mass05: list[float] = field(default_factory=list)
    mass10: list[float] = field(default_factory=list)
    shown: list[bool] = field(default_factory=list)
    top1: list[bool] = field(default_factory=list)
    top2: list[bool] = field(default_factory=list)
    warn: list[tuple[float, bool, bool]] = field(default_factory=list)   # (gt, any warning, fog warning)
    warn_p: list[tuple[float, bool, bool]] = field(default_factory=list)
    sig: list[tuple[float, bool, float | None]] = field(default_factory=list)   # (gt, visible warn, p_reach)
    ms: list[float] = field(default_factory=list)

    def setup(self) -> None:
        self.fog = FogTracker()

        class Cfg:
            fog_max_s = 60.0
            jungle_paths = self.paths

        self.fog.apply_config(Cfg())
        self.intel = JungleIntelTracker()


def _dist_grid(grid: int, uv: UV) -> np.ndarray:
    c = (np.arange(grid) + 0.5) / grid
    return np.hypot(c[None, :] - uv[0], c[:, None] - uv[1])


def _mass_near(est: Any, uv: UV, r: float) -> float:
    if est is None:
        return 0.0
    heat = getattr(est, "heat", None)
    if heat is not None:
        h = np.asarray(heat, np.float64)
    elif getattr(est, "region", None) is not None:
        h = np.asarray(est.region, np.float64)
    else:
        return 0.0
    s = h.sum()
    if s <= 0:
        return 0.0
    return float(h[_dist_grid(h.shape[0], uv) <= r].sum() / s)


def _ranked_stops(run: ModelRun, est: Any) -> list[UV]:
    """Predicted next stops, best first (uv)."""
    g = shared_graph()
    pf = getattr(run.fog, "_pf", None)
    if run.paths and pf is not None and getattr(pf, "_ok", False):
        acc = np.zeros(len(g.nodes))
        np.add.at(acc, np.clip(pf.tgt, 0, len(g.nodes) - 1), pf.w)
        order = np.argsort(-acc)
        return [tuple(pf.node_uv[j]) if hasattr(pf, "node_uv") else tuple(g.uv[j]) for j in order[:5] if acc[j] > 0]
    if est is None:
        return []
    m = [(_mass_near(est, tuple(g.uv[j]), 0.05), j) for j in range(len(g.nodes))]
    m.sort(reverse=True)
    return [tuple(g.uv[j]) for v, j in m[:5] if v > 0]


def run_episode(style: str, lane: str, seed: int, minutes: float, models: list[ModelRun],
                verbose: bool = False) -> dict[str, Any]:
    rng = random.Random(seed * 1000 + zlib.crc32(f"{style}:{lane}".encode()) % 997)
    truth = TruthJungler(style, rng)
    phases = {ln: rng.uniform(0, 6.28) for ln in ("top", "mid", "bot")}
    wards: list[tuple[UV, float]] = []
    contacts: list[float] = []
    states = []
    for m in models:
        m.setup()
        jg, me = P("Kindred", "CHAOS"), P("Garen", "ORDER")
        game = Game(jg, me)
        tr = Tracker()
        tr.ally_ts = [T(f"a{i}", ln, "ally") for i, ln in enumerate(("top", "mid", "bot")) if ln != lane]
        states.append((game, tr))
    gt = 0.0
    dt = 1.0
    reach = shared_reachability()
    last_contact = -1e9
    hidden_since: float | None = None
    loss_eval_at: float | None = None
    end = minutes * 60.0
    while gt < end:
        gt += dt
        laners = {ln: laner_pos(ln, gt, phases[ln]) for ln in ("top", "mid", "bot")}
        me_uv = laners[lane]
        ev = truth.step(gt, dt, laners)
        if rng.random() < dt / 90.0 or not wards:
            river = [(0.30, 0.38), (0.38, 0.30), (0.62, 0.70), (0.70, 0.62), (0.45, 0.45), (0.55, 0.55)]
            wards = [w for w in wards if gt - w[1] < 90.0][-1:] + [(rng.choice(river), gt)]
        viewers = list(laners.values())
        seen = any(math.dist(truth.pos, v) < SIGHT for v in viewers) or \
            any(math.dist(truth.pos, w) < WARD_SIGHT for w, _ in wards)
        if math.dist(truth.pos, geometry.RED_FOUNTAIN) < 0.12:
            seen = False
        if seen and rng.random() < MISS:
            seen = False
        if math.dist(truth.pos, me_uv) < CONTACT and gt - last_contact > 20.0 and gt > 90:
            contacts.append(gt)
            last_contact = gt
        if seen:
            hidden_since = None
        elif hidden_since is None:
            hidden_since = gt
            loss_eval_at = gt + 10.0
        ns = truth.next_stop()
        for m, (game, tr) in zip(models, states):
            game.game_time = gt
            game.fetched_at = gt
            jg = game.jg
            if ev["cs"]:
                jg.scores = {"creepScore": truth.cs}
            if ev["bought"]:
                jg.items = jg.items + [3000 + truth.items]
            tr.me_t._pos, tr.me_t.visible, tr.me_t.last_seen = me_uv, True, gt
            for a in tr.ally_ts:
                a._pos, a.visible, a.last_seen = laners[a.alias], True, gt
            if seen:
                tr.ever = True
                prev = tr.jg._pos
                tr.jg._pos, tr.jg.visible, tr.jg.last_seen = truth.pos, True, gt
                if prev is not None:
                    tr.jg._vel = ((truth.pos[0] - prev[0]) / dt, (truth.pos[1] - prev[1]) / dt)
            else:
                tr.jg.visible = False
            t0 = time.perf_counter()
            m.intel.update(gt, game, tr, m.fog)
            ests = m.fog.update(gt, tr, game, mode="jungler")
            m.ms.append((time.perf_counter() - t0) * 1000.0)
            est = next((e for e in ests if e.is_jungler), None)
            # gank warning signals
            vis_warn = seen and _geo_eta(reach, truth.pos, me_uv, gt) <= 8.0
            fog_warn = False
            fog_warn_p = False
            if not seen and est is not None and 8.0 <= est.elapsed <= 60.0:
                mass = fog_mass_near(est, me_uv, 7.0, reach)
                fog_warn = mass is not None and mass >= WARN_MASS
            if not seen and est is not None and getattr(est, "p_reach", None) is not None and est.elapsed >= 4.0:
                fog_warn_p = est.p_reach >= WARN_P
            m.warn.append((gt, vis_warn or fog_warn, fog_warn))
            m.warn_p.append((gt, vis_warn or fog_warn_p, fog_warn_p))
            pr = est.p_reach if (not seen and est is not None and est.elapsed >= 4.0) else None
            m.sig.append((gt, vis_warn, pr))
            if not seen and tr.ever and gt > 90:
                m.mass05.append(_mass_near(est, truth.pos, 0.05))
                m.mass10.append(_mass_near(est, truth.pos, 0.10))
                m.shown.append(est is not None)
                if loss_eval_at is not None and abs(gt - loss_eval_at) < 0.5 and ns is not None:
                    stops = _ranked_stops(m, est)
                    hit = [math.dist(s, ns[1]) < 0.07 for s in stops]
                    m.top1.append(bool(hit[:1] and hit[0]))
                    m.top2.append(any(hit[:2]))
    return {"contacts": contacts, "minutes": minutes}


def _geo_eta(reach: Any, a: UV, b: UV, gt: float) -> float:
    d = math.dist(a, b) * 1.2
    return max(0.0, d - 0.027) / ((390.0 if gt > 150 else 345.0) / U_PER_NORM)


def lead_stats(warn: list[tuple[float, bool, bool]], contacts: list[float]) -> tuple[list[float], int, float]:
    on = {round(t): (w, f) for t, w, f in warn}
    leads = []
    for c in contacts:
        t = round(c)
        L = 0
        miss = 0
        while t - L - 1 in on and (on[t - L - 1][0] or miss < 1) and L < 40:
            if not on[t - L - 1][0]:
                miss += 1
            L += 1
        # warning must be on at (or just before) contact
        leads.append(float(L) if on.get(t, (False,))[0] or on.get(t - 1, (False,))[0] else 0.0)
    # false fog-warning episodes
    false = 0
    eps = 0
    prev = False
    for t, _w, f in warn:
        if f and not prev:
            eps += 1
            if not any(t - 2 <= c <= t + FALSE_WINDOW_S for c in contacts):
                false += 1
        prev = f
    return leads, false, eps


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Jungle gym: old fog region/heat vs logical paths")
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--minutes", type=float, default=14.0)
    ap.add_argument("--styles", default="farm,gank,invade")
    ap.add_argument("--lanes", default="top,mid,bot")
    ap.add_argument("--real", action="store_true", help="also replay the real Kindred LCU path")
    args = ap.parse_args(argv)
    res = evaluate(args.seeds, args.minutes, args.styles.split(","), args.lanes.split(","))
    print_table(res)
    if args.real:
        print_real(replay_real())
    return 0


def evaluate(seeds: int, minutes: float, styles: list[str], lanes: list[str]) -> dict[str, Any]:
    agg: dict[str, dict[str, Any]] = {}
    per_style: dict[tuple[str, str], dict[str, Any]] = {}
    for style in styles:
        for lane in lanes:
            for seed in range(seeds):
                models = [ModelRun("old", False), ModelRun("paths", True)]
                ep = run_episode(style, lane, seed, minutes, models)
                for m in models:
                    for key in (m.name, f"{m.name}:{style}"):
                        a = agg.setdefault(key, {"mass05": [], "mass10": [], "shown": [], "top1": [],
                                                 "top2": [], "leads": [], "false": 0, "eps": 0,
                                                 "leads_p": [], "false_p": 0, "eps_p": 0, "min": 0.0,
                                                 "ms": []})
                        a["mass05"] += m.mass05
                        a["mass10"] += m.mass10
                        a["shown"] += m.shown
                        a["top1"] += m.top1
                        a["top2"] += m.top2
                        leads, false, eps = lead_stats(m.warn, ep["contacts"])
                        a["leads"] += leads
                        a["false"] += false
                        a["eps"] += eps
                        leads, false, eps = lead_stats(m.warn_p, ep["contacts"])
                        a["leads_p"] += leads
                        a["false_p"] += false
                        a["eps_p"] += eps
                        a["min"] += minutes
                        if m.paths:
                            for th in SWEEP:
                                w = [(t, v or (p is not None and p >= th), p is not None and p >= th)
                                     for t, v, p in m.sig]
                                leads, false, _e = lead_stats(w, ep["contacts"])
                                sw = a.setdefault("sweep", {}).setdefault(th, {"leads": [], "false": 0})
                                sw["leads"] += leads
                                sw["false"] += false
                        a["ms"] += m.ms
    return agg


def _row(a: dict[str, Any], p_rule: bool = False) -> dict[str, float]:
    leads = a["leads_p"] if p_rule else a["leads"]
    false = a["false_p"] if p_rule else a["false"]
    mean = lambda x: float(np.mean(x)) if x else float("nan")  # noqa: E731
    return {
        "mass05": mean(a["mass05"]), "mass10": mean(a["mass10"]), "shown": mean(a["shown"]),
        "top1": mean(a["top1"]), "top2": mean(a["top2"]),
        "ganks": len(leads), "lead_med": float(statistics.median(leads)) if leads else float("nan"),
        "lead>=5s": mean([x >= LEAD_OK_S for x in leads]),
        "false/10min": 10.0 * false / max(1e-9, a["min"]), "ms": mean(a["ms"]),
    }


def print_table(agg: dict[str, Any]) -> None:
    cols = ["mass05", "mass10", "shown", "top1", "top2", "ganks", "lead_med", "lead>=5s", "false/10min", "ms"]
    print(f"{'model':<22}" + "".join(f"{c:>12}" for c in cols))
    for key in sorted(agg, key=lambda k: (k.split(":")[-1] if ":" in k else "", k)):
        rows = [(key, _row(agg[key]))]
        if key.startswith("paths"):
            rows.append((key + " (p8s)", _row(agg[key], p_rule=True)))
        for name, r in rows:
            print(f"{name:<22}" + "".join(f"{r[c]:>12.3f}" if isinstance(r[c], float) else f"{r[c]:>12}" for c in cols))
    a = agg.get("paths")
    if a and a.get("sweep"):
        print("\np_reach(8 s) threshold sweep (paths, all styles): threshold -> lead>=5s share, median lead, false/10min")
        for th, sw in sorted(a["sweep"].items()):
            L = sw["leads"]
            print(f"  {th:.2f}: {np.mean([x >= LEAD_OK_S for x in L]) if L else float('nan'):.3f}  "
                  f"{statistics.median(L) if L else float('nan'):.1f} s  {10.0 * sw['false'] / max(1e-9, a['min']):.2f}")


# ---------------------------------------------------------------------- real replay
def replay_real() -> dict[str, list[float]]:
    """60 s-ahead prediction from each real per-minute Kindred position (ORDER jungler)."""
    out: dict[str, list[float]] = {"old05": [], "old10": [], "paths05": [], "paths10": [],
                                    "old_shown": [], "paths_shown": []}
    pts = list(KINDRED_REAL)
    for (m0, a), (m1, b) in zip(pts, pts[1:]):
        for paths in (False, True):
            fog = FogTracker()

            class Cfg:
                fog_max_s = 60.0
                jungle_paths = paths

            fog.apply_config(Cfg())
            intel = JungleIntelTracker()
            jg, me = P("Kindred", "ORDER"), P("Garen", "CHAOS")
            game = Game(jg, me)
            tr = Tracker()
            tr.me_t._pos, tr.me_t.visible, tr.me_t.last_seen = (0.11, 0.11), True, 0.0
            est = None
            for k in range(0, 61):
                gt = 60.0 * m0 + k
                game.game_time = game.fetched_at = gt
                tr.ever = True
                if k == 0:
                    tr.jg._pos, tr.jg.visible, tr.jg.last_seen = a, True, gt
                else:
                    tr.jg.visible = False
                intel.update(gt, game, tr, fog)
                ests = fog.update(gt, tr, game, mode="jungler")
                est = next((e for e in ests if e.is_jungler), None)
            key = "paths" if paths else "old"
            out[key + "05"].append(_mass_near(est, b, 0.05))
            out[key + "10"].append(_mass_near(est, b, 0.10))
            out[key + "_shown"].append(float(est is not None))
    return out


def print_real(r: dict[str, list[float]]) -> None:
    print("\nReal Kindred path (LCU, 1 point / minute), 60 s-ahead from each point (n=%d):" % len(r["old05"]))
    for k in ("old", "paths"):
        print(f"  {k:<6} mass05={np.mean(r[k + '05']):.3f}  mass10={np.mean(r[k + '10']):.3f}  "
              f"shown={np.mean(r[k + '_shown']):.2f}")


if __name__ == "__main__":
    sys.exit(main())
