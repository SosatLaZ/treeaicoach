"""Detection gym: ONE fast, deterministic scoreboard for minimap detection + tracking.

Two parts, both through the production code:

* **real** - the 9 real 2026 minimap crops of ``tests/fixtures/real`` (45 icons), scored by
  :mod:`tools.real_minimap_bench` (HybridDetector = roster matcher + ONNX extras): recall,
  precision, team accuracy, identity accuracy.
* **games** - short synthetic GAMES rendered on the REAL 2026 minimap art
  (:mod:`training.real_art`: clean backgrounds from the real crops + real distractor glyphs)
  with ground-truth trajectories: 10 champions walking real lane / jungle paths at real
  speeds (~0.023-0.027 minimap / s), recalls (halo, then fountain), deaths (Live Client dead
  set + respawn timer) and fountain respawns, fog of war (an enemy is drawn only inside my
  team's vision), stacks (bot duo, 3-5 icon fights, base siege), Flash jumps, the camera
  rectangle (locked on me / panning / jumping), pings, overlay labels, minions, pasted
  tower / camp / marker glyphs, JPEG + blur, minimap sizes 220-320 px, a custom-skin "me"
  (portrait not in the roster) and detection rates of 3 / 6 / 12 img/s.

  Every frame goes through the REAL engine path: ``CoachEngine._vision`` (HybridDetector +
  identifier + camera self fallback) -> ``CoachEngine._stabilize`` -> ``Tracker.update``,
  then the overlay model: ``CoachEngine._enemy_view`` + ``overlay_render.is_ghost`` +
  ``scheduler.MotionSnapshot.predict`` (what the overlay would draw: live icons at the
  render-time predicted position, ghosts / last-seen marks, dead champions hidden).

Metrics (games, after a 1 s warm-up): ``rec`` = visible icons (>= 50 % of the disc not
covered by another icon) drawn live with the right identity within ``TOL``; ``prec`` = live
drawn icons that are the right champion at the right place; ``idsw`` = identity switches
(the live label on a champion's icon changes); ``frag`` = a visible champion's live drawing
lost then found again; ``err`` median / p95 position error (minimap units) of the drawn
position; ``lag`` = error along the walking direction in frames; ``g_live`` = frames where a
visible champion (seen >= 3 frames) is drawn as a ghost / last-seen mark; ``g_dead`` = frames
where a dead champion is drawn at all; ``team`` = live icons drawn with the wrong team at a
champion; ``me`` median / p95 error of my position and frames > 0.03 off; ``ms`` mean / p95
per-frame cost (vision + stabilize + tracker).

    python tools/det_gym.py [--quick] [--only NAME ...] [--no-real] [-v] [--json OUT]

Deterministic: every scenario has a fixed seed; the matcher's clock is the simulated clock.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time as _time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
_PKG = os.environ.get("BENCH_PKG_ROOT") or str(ROOT)
for p in (_PKG, str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import treeaicoach  # noqa: E402,F401  (first, as in the app: single-threaded BLAS, see its __init__)
import numpy as np  # noqa: E402

import cv2  # noqa: E402

TOL = 0.03               # drawn position within this of the truth (icon radius ~0.045)
WARMUP_S = 1.0
ICON_R = 0.045
VISIBLE_FRAC = 0.5       # an icon is "visible" when this much of its disc is not covered
SPEED = (0.023, 0.027)   # walking speed (minimap / s): 345-400 game units / s
FLASH = 0.027
RECALL_S = 8.0
VISION_R = 0.105         # champion sight radius (normalized, ~1500 units incl. wards)
TOWER_VISION_R = 0.065

FOUNTAIN = {"ORDER": (0.045, 0.955), "CHAOS": (0.955, 0.045)}
OTHER = {"ORDER": "CHAOS", "CHAOS": "ORDER"}


# ======================================================================================
# Simulated clock for the matcher (dead deadlines, detection time)
# ======================================================================================

class _SimTime:
    """Stand-in for the ``time`` module of the detection modules: ``monotonic`` = sim clock."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def __getattr__(self, item: str) -> Any:
        return getattr(_time, item)


SIM = _SimTime()


def _patch_clock() -> None:
    """The detection modules read the simulated clock (see :func:`_unpatch_clock`)."""
    from treeaicoach import roster_matcher, self_icon

    roster_matcher.time = SIM          # type: ignore[assignment]
    self_icon.time = SIM               # type: ignore[assignment]


def _unpatch_clock() -> None:
    from treeaicoach import roster_matcher, self_icon

    roster_matcher.time = _time        # type: ignore[assignment]
    self_icon.time = _time             # type: ignore[assignment]


# ======================================================================================
# Map paths
# ======================================================================================

def _polyline(poly) -> tuple[np.ndarray, np.ndarray]:
    P = np.asarray(poly, np.float64)
    seg = np.hypot(*(P[1:] - P[:-1]).T)
    return P, np.concatenate([[0.0], np.cumsum(seg)])


def _at(poly, s: float) -> np.ndarray:
    """Point at the fraction ``s`` of a polyline's length."""
    P, cum = poly
    d = float(np.clip(s, 0, 1)) * cum[-1]
    i = int(np.clip(np.searchsorted(cum, d) - 1, 0, len(P) - 2))
    k = (d - cum[i]) / max(1e-9, cum[i + 1] - cum[i])
    return P[i] + (P[i + 1] - P[i]) * k


def _lanes():
    from treeaicoach import geometry as G

    return {"top": _polyline(G.TOP_LANE_POLYLINE), "mid": _polyline(G.MID_LANE_POLYLINE),
            "bot": _polyline(G.BOT_LANE_POLYLINE)}


def _camps(team: str) -> list[np.ndarray]:
    from treeaicoach.render import CAMPS

    out = []
    for u, v, name in CAMPS:
        if name in ("dragon", "baron", "scuttle"):
            continue
        mine = (u + (1 - v)) < 1.0          # ORDER half: bottom-left
        if mine == (team == "ORDER"):
            out.append(np.array([u, v]))
    return out


# ======================================================================================
# Champions and scenarios
# ======================================================================================

@dataclass
class Champ:
    alias: str
    rel: str                  # self / ally / enemy
    team: str
    role: str                 # TOP JUNGLE MIDDLE BOTTOM UTILITY
    pos: np.ndarray
    speed: float
    portrait: Any = None      # RGBA drawn on the map (custom skin: another portrait)
    ring: tuple = (0, 0, 0)
    plan: list = field(default_factory=list)     # waypoints [(u, v, wait_s)]
    wait: float = 0.0
    dead_until: float = -1.0
    recall_until: float = -1.0
    script: Any = None        # callable(sim, champ, t) -> None, scenario behaviour
    lane_s: float = 0.5
    lane_dir: float = 1.0
    stick: str | None = None  # alias to walk next to (bot duo)
    stick_off: tuple = (0.02, 0.012)

    @property
    def alive(self) -> bool:
        return self.dead_until < 0


@dataclass
class Scenario:
    name: str
    seed: int
    fps: float
    seconds: float
    size: int
    my_team: str = "ORDER"
    camera: str = "locked"                    # locked | pan | jump | free
    custom_me: bool = False
    self_outline: bool = True
    jpeg: int = 75
    blur: float = 0.6
    events: list = field(default_factory=list)   # (t, kind, args)
    pings: float = 0.3                         # pings / s
    labels: float = 0.5                        # overlay labels per icon-second
    distractors: int = 5
    ring_dark: float = 0.0                     # 0..1: rings darkened by up to this factor
    duo_close: float = 0.3                     # probability of a support ON his ADC
    cam_px: int = 0                            # camera line width (0: from the size)
    suite: str = "main"


@dataclass
class Frame:
    t: float
    img: np.ndarray
    truth: list            # (alias, rel, team, u, v, vis_frac) alive + on the map (not fogged)
    dead: list             # aliases dead now
    fogged: list           # alive aliases hidden by the fog
    game: Any
    me_uv: tuple | None    # truth position of me (alive)


ROLES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
LANE_OF = {"TOP": "top", "MIDDLE": "mid", "BOTTOM": "bot", "UTILITY": "bot"}


class Sim:
    """One synthetic game: champions, map art, camera, renderer and Live Client data."""

    def __init__(self, sc: Scenario, db, art) -> None:
        self.sc = sc
        self.db = db
        self.art = art
        self.rng = np.random.default_rng(sc.seed)
        rng = self.rng
        self.lanes = _lanes()
        names = sorted(e.alias for e in db.all() if db.load_icon(e.alias) is not None)
        pick = list(rng.choice(names, 11, replace=False))
        me_team = sc.my_team
        self.champs: list[Champ] = []
        from training import real_art as RA

        for k in range(10):
            team = me_team if k < 5 else OTHER[me_team]
            rel = "self" if k == 0 else ("ally" if k < 5 else "enemy")
            role = ROLES[(k if k < 5 else k - 5)] if k else "TOP"
            if k == 0:
                role = str(rng.choice(["TOP", "MIDDLE", "BOTTOM"]))
            alias = pick[k]
            c = Champ(alias, rel, team, role, np.array(FOUNTAIN[team], float),
                      float(rng.uniform(*SPEED)))
            c.portrait = db.load_icon(alias)
            ring_rgb = RA.ENEMY_RING_RGB if rel == "enemy" else RA.ALLY_RING_RGB
            c.ring = RA._lerp_bgr(rng, ring_rgb)
            if sc.ring_dark > 0:
                k_d = 1.0 - sc.ring_dark * float(rng.uniform(0.3, 1.0))
                c.ring = tuple(int(x * k_d) for x in c.ring)
            self.champs.append(c)
        # roles of allies: the four that are not mine
        roles_left = [r for r in ROLES if r != self.champs[0].role]
        for c, r in zip(self.champs[1:5], roles_left):
            c.role = r
        if sc.custom_me:                              # custom skin: a portrait not in the roster
            self.champs[0].portrait = db.load_icon(pick[10])
        self.by_alias = {c.alias: c for c in self.champs}
        for c in self.champs:
            self._init_place(c)
        # bot duos walk together
        for team in ("ORDER", "CHAOS"):
            adc = next(c for c in self.champs if c.team == team and c.role == "BOTTOM")
            sup = next(c for c in self.champs if c.team == team and c.role == "UTILITY")
            sup.stick = adc.alias
            sup.stick_off = self._duo_offset()
        # art: one real background per game at the native size, static glyphs
        n = sc.size
        # (the map is never flipped: no mirrored background, a slight zoom / shift only)
        bg = art.backgrounds[int(rng.integers(len(art.backgrounds)))]
        z, c0 = rng.uniform(0.97, 1.03), 150 + rng.uniform(-4, 4, 2)
        M = cv2.getRotationMatrix2D((float(c0[0]), float(c0[1])), 0.0, 1.0 / z)
        M[:, 2] += (150 - c0)
        bg = cv2.warpAffine(np.ascontiguousarray(bg), M, (300, 300), flags=cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_REFLECT)
        self.bg = cv2.resize(bg, (n, n), interpolation=cv2.INTER_AREA if n < 300 else cv2.INTER_LINEAR)
        from training.real_art import _paste

        for _ in range(sc.distractors):
            spr = art.sprites[int(rng.integers(len(art.sprites)))]
            kk = n / 300.0 * rng.uniform(0.9, 1.1)
            spr = cv2.resize(spr, None, fx=kk, fy=kk, interpolation=cv2.INTER_LINEAR)
            _paste(self.bg, spr, rng.uniform(0.05, 0.95) * n, rng.uniform(0.05, 0.95) * n)
        self.order = list(rng.permutation(10))       # draw order (no priority for me)
        self.cam_c = self.champs[0].pos + [0.0, -0.022]
        self.pan_dir = rng.normal(0, 1, 2)
        self.pan_dir /= np.linalg.norm(self.pan_dir)
        self.next_jump = 0.0
        self.pings: list = []                        # (u, v, t_end, bgr)
        self.labels: list = []                       # (alias, t_end, text seed)
        self.towers = [(float(u), float(v), str(t)) for _sid, u, v, _k, t in _iter_structs()]
        self.t = 0.0

    # ------------------------------------------------------------------ behaviour
    def _init_place(self, c: Champ) -> None:
        rng = self.rng
        if c.role == "JUNGLE":
            camps = _camps(c.team)
            c.pos = camps[int(rng.integers(len(camps)))] + rng.normal(0, 0.01, 2)
            return
        lane = self.lanes[LANE_OF[c.role]]
        c.lane_s = float(rng.uniform(0.42, 0.5) if c.team == "ORDER" else rng.uniform(0.5, 0.58))
        c.lane_dir = 1.0 if rng.random() < 0.5 else -1.0
        c.pos = _at(lane, c.lane_s) + rng.normal(0, 0.008, 2)

    def _walk_to(self, c: Champ, goal: np.ndarray, dt: float) -> bool:
        d = goal - c.pos
        L = float(np.hypot(*d))
        step = c.speed * dt
        if L <= step:
            c.pos = goal.astype(float).copy()
            return True
        c.pos = c.pos + d / L * step
        return False

    def _duo_offset(self) -> tuple[float, float]:
        """Support next to his ADC: mostly side by side, sometimes on top of him."""
        a = self.rng.uniform(0, 2 * math.pi)
        d = self.rng.uniform(0.012, 0.03) if self.rng.random() < self.sc.duo_close \
            else self.rng.uniform(0.03, 0.07)
        return (float(d * math.cos(a)), float(d * math.sin(a)))

    def _default(self, c: Champ, dt: float) -> None:
        rng = self.rng
        if c.stick and self.by_alias[c.stick].alive and c.plan == []:
            o = self.by_alias[c.stick]
            goal = o.pos + np.asarray(c.stick_off)
            self._walk_to(c, goal, dt)
            if rng.random() < 0.15 * dt:
                c.stick_off = self._duo_offset()
            return
        if c.plan:
            u, v, w = c.plan[0]
            if self._walk_to(c, np.array([u, v]), dt):
                c.wait += dt
                if c.wait >= w:
                    c.plan.pop(0)
                    c.wait = 0.0
            return
        if c.role == "JUNGLE":
            camps = _camps(c.team)
            g = camps[int(rng.integers(len(camps)))]
            c.plan = [(float(g[0]), float(g[1]), float(rng.uniform(1.5, 4.0)))]
            return
        # laning: trade around the wave, along the lane
        lane = self.lanes[LANE_OF[c.role]]
        L = lane[1][-1]
        c.lane_s += c.lane_dir * c.speed * dt / L
        lo, hi = (0.38, 0.5) if c.team == "ORDER" else (0.5, 0.62)
        if c.lane_s < lo or c.lane_s > hi or rng.random() < 0.15 * dt:
            c.lane_dir = -c.lane_dir
            c.lane_s = float(np.clip(c.lane_s, lo, hi))
        goal = _at(lane, c.lane_s)
        perp = rng.normal(0, 0.002, 2)
        self._walk_to(c, goal + perp, dt * 1.6)

    def step(self, t: float, dt: float) -> None:
        self.t = t
        for ev in list(self.sc.events):
            et, kind, args = ev
            if et <= t:
                self.sc_events_done = getattr(self, "sc_events_done", set())
                key = (et, kind, json.dumps(args, default=str))
                if key in self.sc_events_done:
                    continue
                self.sc_events_done.add(key)
                self._event(kind, args, t)
        for c in self.champs:
            if not c.alive:
                if t >= c.dead_until:
                    c.dead_until = -1.0
                    c.pos = np.array(FOUNTAIN[c.team], float) + self.rng.normal(0, 0.004, 2)
                    c.plan = [self._home_exit(c)]
                continue
            if c.recall_until >= 0:
                if t >= c.recall_until:
                    c.recall_until = -1.0
                    c.pos = np.array(FOUNTAIN[c.team], float) + self.rng.normal(0, 0.004, 2)
                    c.plan = [self._home_exit(c), (float(c.pos[0]), float(c.pos[1]), 0.0)][:1]
                continue
            self._default(c, dt)
            c.pos = np.clip(c.pos, 0.03, 0.97)

    def _home_exit(self, c: Champ) -> tuple:
        f = np.array(FOUNTAIN[c.team])
        g = f + (np.array([0.12, -0.12]) if c.team == "ORDER" else np.array([-0.12, 0.12]))
        return (float(g[0]), float(g[1]), 0.0)

    def _sel(self, who) -> list[Champ]:
        if isinstance(who, str):
            who = [who]
        out = []
        for w in who:
            if w == "me":
                out.append(self.champs[0])
            elif isinstance(w, int):
                out.append(self.champs[w])
            else:
                out.append(self.by_alias[w])
        return out

    def _event(self, kind: str, a: dict, t: float) -> None:
        rng = self.rng
        if kind == "goto":            # group walks to a point, then mills around there
            u, v = a["uv"]
            for c in self._sel(a["who"]):
                c.stick = None
                jit = rng.normal(0, a.get("spread", 0.02), 2)
                c.plan = [(u + jit[0], v + jit[1], a.get("stay", 30.0))]
                c.speed = float(rng.uniform(*SPEED)) * a.get("speed_k", 1.0)
        elif kind == "mill":          # random short moves around the current spot (fight)
            for c in self._sel(a["who"]):
                pts = []
                for _ in range(int(a.get("n", 6))):
                    j = rng.normal(0, a.get("spread", 0.025), 2)
                    pts.append((float(np.clip(c.pos[0] + j[0], 0.03, 0.97)),
                                float(np.clip(c.pos[1] + j[1], 0.03, 0.97)), 0.3))
                c.plan = pts
        elif kind == "die":
            for c in self._sel(a["who"]):
                c.dead_until = t + float(a.get("respawn", 10.0))
                c.plan = []
        elif kind == "flash":
            for c in self._sel(a["who"]):
                d = np.asarray(a.get("dir", rng.normal(0, 1, 2)), float)
                d /= max(1e-9, np.linalg.norm(d))
                c.pos = np.clip(c.pos + d * FLASH, 0.03, 0.97)
        elif kind == "recall":
            for c in self._sel(a["who"]):
                c.recall_until = t + RECALL_S
                c.plan = []
        elif kind == "camera":
            self.sc.camera = a["mode"]

    # ------------------------------------------------------------------ vision / camera
    def _vision(self) -> np.ndarray:
        """Soft vision mask of my team (size x size, 0..1)."""
        n = self.sc.size
        m = np.zeros((n, n), np.uint8)
        sh = 4
        for c in self.champs:
            if c.team == self.sc.my_team and c.alive:
                cv2.circle(m, (int(c.pos[0] * n * 16), int(c.pos[1] * n * 16)), int(VISION_R * n * 16), 255, -1,
                           cv2.LINE_AA, sh)
        for u, v, team in self.towers:
            if team == self.sc.my_team:
                cv2.circle(m, (int(u * n * 16), int(v * n * 16)), int(TOWER_VISION_R * n * 16), 255, -1,
                           cv2.LINE_AA, sh)
        f = FOUNTAIN[self.sc.my_team]
        cv2.circle(m, (int(f[0] * n * 16), int(f[1] * n * 16)), int(0.2 * n * 16), 255, -1, cv2.LINE_AA, sh)
        return cv2.GaussianBlur(m, (0, 0), 2.0).astype(np.float32) / 255.0

    def _visible_to_me(self, vis: np.ndarray, c: Champ) -> bool:
        if c.team == self.sc.my_team:
            return True
        n = self.sc.size
        x, y = int(np.clip(c.pos[0] * n, 0, n - 1)), int(np.clip(c.pos[1] * n, 0, n - 1))
        return bool(vis[y, x] > 0.5)

    def _camera(self, dt: float) -> tuple[float, float, float, float]:
        from treeaicoach.render import CAMERA_SIZE

        rng = self.rng
        mode = self.sc.camera
        me = self.champs[0]
        if mode == "locked":
            self.cam_c = me.pos + [0.0, -0.022]
        elif mode == "pan":
            self.cam_c = self.cam_c + self.pan_dir * 0.15 * dt
            if not (0.12 < self.cam_c[0] < 0.88 and 0.1 < self.cam_c[1] < 0.9):
                self.pan_dir = -self.pan_dir + rng.normal(0, 0.3, 2)
                self.pan_dir /= np.linalg.norm(self.pan_dir)
                self.cam_c = np.clip(self.cam_c, 0.12, 0.88)
        elif mode == "jump":
            if self.t >= self.next_jump:
                self.next_jump = self.t + float(rng.uniform(1.0, 2.0))
                tgt = self.champs[int(rng.choice([0, 0, 1, 2, 3, 4]))]
                self.cam_c = tgt.pos + [0.0, -0.022]
        w, h = CAMERA_SIZE
        c = self.cam_c
        return (float(c[0] - w / 2), float(c[1] - h / 2), float(c[0] + w / 2), float(c[1] + h / 2))

    # ------------------------------------------------------------------ rendering
    def render(self, dt: float) -> tuple[np.ndarray, list, list, list]:
        from treeaicoach import render as R
        from training import real_art as RA

        rng = self.rng
        sc = self.sc
        n = sc.size
        vis = self._vision()
        fog = (0.5 + 0.5 * vis)[:, :, None]
        img = (self.bg.astype(np.float32) * fog).astype(np.uint8)
        # minions near the lane fronts
        for lane in self.lanes.values():
            for team, s0 in (("ORDER", 0.46), ("CHAOS", 0.54)):
                for k in range(4):
                    p = _at(lane, s0 + (k - 1.5) * 0.012 * (1 if team == "ORDER" else -1)) + rng.normal(0, 0.004, 2)
                    if team != sc.my_team and vis[int(np.clip(p[1] * n, 0, n - 1)), int(np.clip(p[0] * n, 0, n - 1))] < 0.5:
                        continue
                    col = (230, 160, 90) if team == sc.my_team else (60, 60, 210)
                    cv2.circle(img, (int(p[0] * n * 16), int(p[1] * n * 16)), int(0.009 * n * 16), (20, 20, 20), -1,
                               cv2.LINE_AA, 4)
                    cv2.circle(img, (int(p[0] * n * 16), int(p[1] * n * 16)), int(0.007 * n * 16), col, -1,
                               cv2.LINE_AA, 4)
        # champions
        truth, drawn = [], []
        fogged = []
        r = ICON_R
        for j in self.order:
            c = self.champs[j]
            if not c.alive:
                continue
            if not self._visible_to_me(vis, c):
                fogged.append(c.alias)
                continue
            x, y, rp = c.pos[0] * n, c.pos[1] * n, r * n
            R.draw_champion_icon(img, x, y, rp, c.portrait, c.ring, ring_frac=0.12,
                                 inner_line_bgr=R.INNER_LINE_BGR, outline_px=0.6)
            if c.rel == "self" and sc.self_outline:
                cv2.circle(img, (int(x * 16), int(y * 16)), int(rp * 1.06 * 16), (225, 190, 100),
                           1, cv2.LINE_AA, 4)
            if c.recall_until >= 0:
                cv2.circle(img, (int(x * 16), int(y * 16)), int(rp * 1.18 * 16), (210, 180, 110),
                           max(1, int(round(n / 130))), cv2.LINE_AA, 4)
            drawn.append(c)
        # occlusion: fraction of each icon disc not covered by icons drawn after it
        ang = np.linspace(0, 2 * np.pi, 24, endpoint=False)
        pts = np.concatenate([np.stack([np.cos(ang) * rr, np.sin(ang) * rr], 1) for rr in (0.3, 0.6, 0.9)]
                             + [np.zeros((1, 2))]) * r
        for i, c in enumerate(drawn):
            P = c.pos[None] + pts
            cov = np.zeros(len(P), bool)
            for o in drawn[i + 1:]:
                cov |= np.hypot(*(P - o.pos[None]).T) < r * 1.02
            truth.append((c.alias, c.rel, c.team, float(c.pos[0]), float(c.pos[1]), float(1.0 - cov.mean())))
        # pings (red / yellow / cyan rings, often on champions)
        if rng.random() < sc.pings * dt:
            if drawn and rng.random() < 0.5:
                o = drawn[int(rng.integers(len(drawn)))]
                pu, pv = o.pos + rng.normal(0, 0.01, 2)
            else:
                pu, pv = rng.uniform(0.1, 0.9, 2)
            col = [(67, 34, 248), (15, 190, 245), (253, 188, 33), (128, 215, 9)][int(rng.integers(4))]
            self.pings.append((float(pu), float(pv), self.t + float(rng.uniform(1.0, 2.5)), col))
        self.pings = [p for p in self.pings if p[2] > self.t]
        for pu, pv, te, col in self.pings:
            ph = (te - self.t) * 3.0 % 1.0
            rr = (0.03 + 0.03 * ph) * n
            cv2.circle(img, (int(pu * n * 16), int(pv * n * 16)), int(rr * 16), col, max(1, int(n / 150)),
                       cv2.LINE_AA, 4)
        # our own overlay labels captured with the map ("ADC 3 s")
        if drawn and rng.random() < sc.labels * dt * len(drawn) / 5.0:
            o = drawn[int(rng.integers(len(drawn)))]
            self.labels.append((o.alias, self.t + float(rng.uniform(1.0, 3.0)), int(rng.integers(1 << 30)),
                                float(rng.uniform(-0.1, 0.0)), float(rng.uniform(-0.04, 0.07))))
        self.labels = [lb for lb in self.labels if lb[1] > self.t]
        for alias, _te, seed, dx, dy in self.labels:
            o = self.by_alias[alias]
            if not o.alive:
                continue
            RA._text(img, np.random.default_rng(seed), (o.pos[0] + dx) * n, (o.pos[1] + dy) * n,
                     n / 300.0 * 0.4)
        # camera rectangle (over everything)
        cam = self._camera(dt)
        g = 235
        cv2.rectangle(img, (int(cam[0] * n), int(cam[1] * n)), (int(cam[2] * n), int(cam[3] * n)), (g, g, g),
                      int(sc.cam_px) if sc.cam_px > 0 else max(1, int(round(n / 160))))
        # capture degradations
        if sc.blur > 0:
            img = cv2.GaussianBlur(img, (0, 0), sc.blur)
        img = cv2.imdecode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(sc.jpeg)])[1], 1)
        dead = [c.alias for c in self.champs if not c.alive]
        return img, truth, dead, fogged

    # ------------------------------------------------------------------ Live Client data
    def game_info(self, t: float):
        from treeaicoach.live_client import GameInfo, PlayerInfo

        players = {}
        for c in self.champs:
            rid = "Moi#GYM" if c.rel == "self" else f"{c.alias}#GYM"
            dead = not c.alive
            players[c.alias] = PlayerInfo(
                riot_id=rid, summoner_name=rid, champion_alias=c.alias, champion_name=c.alias, team=c.team,
                position=c.role, is_dead=dead, respawn_timer=max(0.0, c.dead_until - t) if dead else 0.0,
                level=6, has_smite=c.role == "JUNGLE")
        me = players[self.champs[0].alias]
        return GameInfo(game_time=600.0 + t, game_mode="CLASSIC", map_number=11, map_terrain="Default",
                        team_relative_colors=True, me=me,
                        allies=[players[c.alias] for c in self.champs if c.rel == "ally"],
                        enemies=[players[c.alias] for c in self.champs if c.rel == "enemy"],
                        events=[], fetched_at=float(t), current_gold=500.0)


def _iter_structs():
    from treeaicoach.render import iter_structures

    return list(iter_structures())


# ======================================================================================
# Scenario catalogue
# ======================================================================================

def scenarios(quick: bool = False) -> list[Scenario]:
    S = []
    # laning: camera locked on me, everybody in lane / jungle, fog, 6 img/s, 300 px
    S.append(Scenario("laning", 11, 6.0, 30.0, 300, "ORDER", "locked",
                      events=[(12.0, "flash", {"who": [6]}), (20.0, "recall", {"who": ["me"]})]))
    # bot-lane 2v2 + jungler gank: stacks of 3-5 icons, a death, 12 img/s, 260 px, panning
    S.append(Scenario("botfight", 23, 12.0, 14.0, 260, "CHAOS", "pan", jpeg=65,
                      events=[(0.0, "goto", {"who": [3, 4, 8, 9], "uv": (0.86, 0.86), "spread": 0.02}),
                              (0.0, "goto", {"who": [6], "uv": (0.8, 0.8), "spread": 0.01, "speed_k": 1.1}),
                              (4.0, "mill", {"who": [3, 4, 6, 8, 9], "n": 10, "spread": 0.03}),
                              (6.0, "flash", {"who": [8]}),
                              (8.0, "die", {"who": [9], "respawn": 4.0}),
                              (9.0, "die", {"who": [4], "respawn": 30.0})]))
    # siege in my base: 4 enemies + 3 allies stacked near the inhibitor, camera jumping, 6 img/s
    S.append(Scenario("siege", 37, 6.0, 20.0, 320, "CHAOS", "jump", jpeg=70,
                      events=[(0.0, "goto", {"who": [5, 6, 7, 8], "uv": (0.80, 0.22), "spread": 0.03}),
                              (0.0, "goto", {"who": [0, 1, 2], "uv": (0.85, 0.17), "spread": 0.02}),
                              (8.0, "mill", {"who": [0, 1, 2, 5, 6, 7, 8], "n": 10, "spread": 0.025}),
                              (12.0, "die", {"who": [1], "respawn": 5.0}),
                              (14.0, "die", {"who": [5], "respawn": 40.0})]))
    # slow detection (3 img/s): recall to base, deaths + fountain respawn, camera free
    S.append(Scenario("slow3", 41, 3.0, 36.0, 240, "ORDER", "free", blur=0.8, jpeg=60,
                      events=[(3.0, "die", {"who": [7], "respawn": 12.0}),
                              (6.0, "recall", {"who": ["me"]}),
                              (10.0, "die", {"who": [2], "respawn": 8.0}),
                              (16.0, "flash", {"who": [5]})]))
    # custom-skin me (portrait not in the roster), camera locked, small minimap 220 px
    S.append(Scenario("customskin", 53, 6.0, 24.0, 220, "ORDER", "locked", custom_me=True, jpeg=70,
                      events=[(10.0, "goto", {"who": ["me", 1], "uv": (0.3, 0.6), "spread": 0.015}),
                              (16.0, "camera", {"mode": "pan"})]))
    if quick:
        S = [S[0], S[1], S[4]]
        for s in S:
            s.seconds = min(s.seconds, 14.0)
    return S


HARD_FILE = ROOT / "tools" / "det_gym_hard.json"
HISTORY_FILE = ROOT / "tools" / "det_gym_history.jsonl"
SUITES = ("main", "holdout", "hard")


def scenario_from_dict(d: dict) -> Scenario:
    d = dict(d)
    d["events"] = [tuple(e) for e in d.get("events", [])]
    for e in d["events"]:
        if "uv" in e[2]:
            e[2]["uv"] = tuple(e[2]["uv"])
    return Scenario(**{k: v for k, v in d.items() if k in Scenario.__dataclass_fields__})


def suite_scenarios(suite: str = "main", quick: bool = False) -> list[Scenario]:
    """``main``: the tuning games (stable, for comparisons); ``holdout``: the same kinds of
    games with other seeds / rosters / sizes / sides (a change is kept only if it also helps
    here); ``hard``: the worst cases found by ``tools/det_mine.py`` (det_gym_hard.json)."""
    if suite == "main":
        return scenarios(quick)
    if suite == "holdout":
        out = []
        sizes = {"laning": 260, "botfight": 300, "siege": 240, "slow3": 280, "customskin": 250}
        for sc in scenarios(quick):
            sc.seed += 1000
            sc.size = sizes.get(sc.name, sc.size)
            sc.my_team = OTHER[sc.my_team]
            sc.name = "h_" + sc.name
            sc.suite = "holdout"
            out.append(sc)
        return out
    if suite == "hard":
        try:
            data = json.loads(HARD_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        out = [scenario_from_dict(d) for d in data.get("cases", [])]
        for sc in out:
            sc.suite = "hard"
            if quick:
                sc.seconds = min(sc.seconds, 8.0)
        return out
    raise ValueError(f"unknown suite {suite!r}")


# ======================================================================================
# Pipeline (the engine's own methods) and metrics
# ======================================================================================

class _Voice:
    def say(self, *a: Any, **k: Any) -> None:
        pass

    def set_muted(self, *a: Any) -> None:
        pass

    def __getattr__(self, item: str) -> Any:
        return lambda *a, **k: None


class Pipeline:
    def __init__(self, db) -> None:
        from treeaicoach.config import Config
        from treeaicoach.detector import create_detector
        from treeaicoach.engine import CoachEngine

        self.now = 0.0
        self.det = create_detector("auto", db=db, learn_cache=None)
        self.eng = CoachEngine(Config(), _Voice(), detector=self.det, clock=lambda: self.now,
                               champion_db=db, enable_hotkeys=False, manage_overlay=False)
        self.eng._ensure_components()
        self.eng._ensure_detector()
        # the engine's "normal" performance budget (generic detector cadence, threads...)
        from treeaicoach.sysperf import PerfBudget

        self.eng._budget = PerfBudget(os.environ.get("GYM_BUDGET", "normal"))
        self.eng._applied_profile = None
        self.eng._apply_perf_profile()

    def step(self, t: float, frame: np.ndarray, game: Any) -> tuple[list, float]:
        eng = self.eng
        self.now = t
        SIM.now = t
        eng._game, eng._game_t, eng._in_game = game, t, True
        eng._update_roster(game)
        t0, c0 = _time.perf_counter(), _time.process_time()
        ids = eng._stabilize(t, eng._vision(frame))
        eng._self_icon_tick(t, float(game.game_time), game)
        eng._track_update(t, ids)
        ms = (_time.perf_counter() - t0) * 1000.0
        self.cpu_ms = (_time.process_time() - c0) * 1000.0
        return ids, ms

    def drawn(self, t: float, game: Any) -> dict:
        """What the overlay would draw: {"live": [(key, alias, rel, (u, v))], "ghost": [...]}."""
        from treeaicoach.overlay_render import EnemyView, is_ghost
        from treeaicoach.scheduler import MotionSnapshot

        eng = self.eng
        trk = eng._tracker
        snap = MotionSnapshot.from_tracks(t, trk.tracks())
        pred = snap.predict(t)
        me = trk.me()
        me_uv = me.position() if me is not None else None
        live, ghost = [], []
        seen = set()
        dead = {p.champion_alias for p in game.all_players() if p.is_dead}
        players = [(p, "enemy") for p in game.enemies] + [(p, "ally") for p in game.allies]
        views = []
        for p, rel in players:
            tr = trk.get(p.champion_alias)
            v = eng._enemy_view(EnemyView, p.champion_alias, p.champion_alias, 0, tr, me_uv, t, False)
            v.dead = p.champion_alias in dead
            views.append((tr, v, rel))
            if tr is not None:
                seen.add(tr.key)
        for tr in trk.enemies(visible_only=True) + trk.allies(visible_only=True):
            if tr.key in seen or (me is not None and tr.key == me.key):
                continue
            v = eng._enemy_view(EnemyView, tr.alias, tr.alias or "?", 0, tr, me_uv, t, False)
            views.append((tr, v, tr.relation))
        from treeaicoach import overlay_render as OR

        hidden = []
        for tr, v, rel in views:
            if v.dead or v.uv is None:
                if v.dead and tr is not None and v.visible:
                    ghost.append((tr.key, v.alias, rel, v.uv, "dead-live"))
                continue
            if v.visible and not is_ghost(v):
                uv = pred.get(tr.key, (v.uv, 0.0))[0]
                live.append((tr.key, v.alias, rel, uv))
            elif v.visible:
                # dashed "unsure" ring (overlay: detailed mode / allies layer)
                why = "stacked" if getattr(v, "stacked", False) else (
                    "anon" if (getattr(v, "confidence", 1.0) or 1.0) < OR.GHOST_MIN_CONFIDENCE else "stale")
                if not (why == "stacked" and getattr(OR, "STACKED_HIDDEN", False)):
                    ghost.append((tr.key, v.alias, rel, v.uv, f"{rel}-{why}"))
            elif rel == "enemy":
                hidden.append((tr, v))
        if me is not None and me.visible:
            uv = pred.get(me.key, (me.position(), 0.0))[0]
            live.append((me.key, me.alias, "self", uv))
        # last-seen marks of hidden enemies (overlay_render: show_last_seen, compact mode)
        lsmax = OR.LAST_SEEN_MAX_S / 2
        delay = float(getattr(OR, "LAST_SEEN_DELAY_S", 0.0))
        near_r = 1.6 * 0.045
        for tr, v in hidden:
            ago = v.last_seen_ago
            if ago is None or ago > lsmax or ago < delay:
                continue
            if OR._in_enemy_fountain(v.uv, game.my_team):
                continue
            if any(math.hypot(v.uv[0] - uv[0], v.uv[1] - uv[1]) < near_r for _k, _a, _r, uv in live):
                continue
            ghost.append((tr.key, v.alias, "enemy", v.uv, "enemy-lastseen"))
        return {"live": live, "ghost": ghost, "me": me.position() if me is not None else None}


@dataclass
class GameMetrics:
    frames: int = 0
    n_vis: int = 0           # visible truth icons (>= VISIBLE_FRAC)
    tp: int = 0              # drawn live, right identity, within TOL
    n_live: int = 0          # live drawn icons
    live_ok: int = 0         # ... that are the right champion at the right place
    id_wrong: int = 0        # live label of champion X on champion Y's icon
    team_wrong: int = 0
    idsw: int = 0
    frag: int = 0
    g_live: int = 0
    g_dead: int = 0
    det_tp: int = 0          # raw identified detections (diagnostic)
    det_n: int = 0
    err: list = field(default_factory=list)
    lag: list = field(default_factory=list)
    me_err: list = field(default_factory=list)
    me_bad: int = 0
    me_n: int = 0
    ms: list = field(default_factory=list)
    cpu: list = field(default_factory=list)       # CPU ms of the process (all threads)
    fails: list = field(default_factory=list)     # (scenario, t, kind, detail)
    miss_cause: dict = field(default_factory=dict)
    ghost_kind: dict = field(default_factory=dict)

    def merge(self, o: "GameMetrics") -> None:
        for k, v in vars(o).items():
            if isinstance(v, list):
                getattr(self, k).extend(v)
            elif isinstance(v, dict):
                for kk, vv in v.items():
                    getattr(self, k)[kk] = getattr(self, k).get(kk, 0) + vv
            else:
                setattr(self, k, getattr(self, k) + v)

    def summary(self) -> dict:
        def pct(x, q):
            return float(np.percentile(x, q)) if x else float("nan")

        return {
            "rec": self.tp / max(1, self.n_vis), "prec": self.live_ok / max(1, self.n_live),
            "idsw": self.idsw, "frag": self.frag, "id_wrong": self.id_wrong, "team": self.team_wrong,
            "err50": pct(self.err, 50), "err95": pct(self.err, 95),
            "lag": float(np.mean(self.lag)) if self.lag else float("nan"),
            "g_live": self.g_live, "g_dead": self.g_dead,
            "me50": pct(self.me_err, 50), "me95": pct(self.me_err, 95),
            "me_bad": self.me_bad / max(1, self.me_n),
            "det_rec": self.det_tp / max(1, self.n_vis),
            "ms": float(np.mean(self.ms)) if self.ms else float("nan"), "ms95": pct(self.ms, 95),
            "cpu": float(np.mean(self.cpu)) if self.cpu else float("nan"),
            "frames": self.frames, "n_vis": self.n_vis, "miss_cause": dict(self.miss_cause),
            "ghost_kind": dict(self.ghost_kind),
        }


def _miss_cause(alias: str, rel: str, u: float, v: float, ids: list, my_alias: str) -> str:
    """Why a visible champion is not drawn live at its place (raw detections of the frame)."""
    here = []
    for x in ids:
        d = getattr(x, "det", x)
        dist = math.hypot(d.u - u, d.v - v)
        if dist < TOL:
            here.append(x)
    mine = [x for x in ids if getattr(x, "alias", None) == alias]
    if any(getattr(x, "alias", None) == alias for x in here):
        return "tracker"                    # detected + identified here, not drawn live
    if mine:
        d = getattr(mine[0], "det", mine[0])
        return f"elsewhere:{math.hypot(d.u - u, d.v - v):.2f}"
    if here:
        x = here[0]
        return f"anon" if not getattr(x, "alias", None) else f"wrongid:{x.alias}"
    return "nodet"


def run_game(sc: Scenario, db, art, verbose: bool = False, gallery: Any = None) -> GameMetrics:
    sim = Sim(sc, db, art)
    pipe = Pipeline(db)
    M = GameMetrics()
    dt = 1.0 / sc.fps
    n_frames = int(round(sc.seconds * sc.fps))
    prev_truth: dict[str, tuple] = {}
    prev_key: dict[str, str] = {}
    was_ok: dict[str, bool] = {}
    vis_run: dict[str, int] = {}
    for f in range(n_frames):
        t = f * dt
        sim.step(t, dt)
        img, truth, dead, _fog = sim.render(dt)
        game = sim.game_info(t)
        ids, ms = pipe.step(t, img, game)
        out = pipe.drawn(t, game)
        if t < WARMUP_S:
            prev_truth = {a: (u, v) for a, _r, _tm, u, v, _f in truth}
            for a, *_ in truth:
                vis_run[a] = vis_run.get(a, 0) + 1
            continue
        n_fail0 = len(M.fails)
        M.frames += 1
        M.ms.append(ms)
        M.cpu.append(pipe.cpu_ms)
        live = out["live"]
        tmap = {a: (rel, team, u, v, fr) for a, rel, team, u, v, fr in truth}
        # --- raw detections (diagnostic)
        for x in ids:
            d = getattr(x, "det", x)
            a = getattr(x, "alias", None)
            M.det_n += 1
            g = tmap.get(a)
            if g is not None and math.hypot(d.u - g[2], d.v - g[3]) < TOL:
                M.det_tp += 1
        # --- live drawn icons: precision / wrong identity / wrong team
        M.n_live += len(live)
        for key, alias, rel, uv in live:
            g = tmap.get(alias) if alias else None
            if g is None and rel == "self":
                g = tmap.get(sim.champs[0].alias)
            if g is not None and math.hypot(uv[0] - g[2], uv[1] - g[3]) < TOL:
                M.live_ok += 1
                continue
            near = [(math.hypot(uv[0] - u, uv[1] - v), a, r_) for a, (r_, _tm, u, v, _f) in tmap.items()]
            near = [n_ for n_ in near if n_[0] < TOL]
            if near:
                _d, a2, r2 = min(near)
                if (rel == "enemy") != (r2 == "enemy"):
                    M.team_wrong += 1
                    M.fails.append((sc.name, round(t, 2), "team", f"{alias or key} drawn {rel} on {a2} ({r2})"))
                elif alias and alias != a2:
                    M.id_wrong += 1
                    M.fails.append((sc.name, round(t, 2), "id", f"{alias} drawn on {a2}"))
                else:
                    M.fails.append((sc.name, round(t, 2), "fp-anon", f"{key} on {a2}"))
            else:
                M.fails.append((sc.name, round(t, 2), "fp", f"{alias or key} {rel} at "
                                f"({uv[0]:.3f},{uv[1]:.3f})" + (f" truth {alias} at ({g[2]:.3f},{g[3]:.3f})"
                                                                  if g is not None else "")))
        # --- per truth champion: recall, switches, fragmentation, error, lag, ghosts
        ghosts = {g[1]: g[4] for g in out["ghost"] if g[1]}
        # one-to-one truth <-> live matching (identity first, then nearest) for the switches
        assign: dict[str, str] = {}
        used_k: set = set()
        for a, (rel, team, u, v, fr) in tmap.items():
            for key, al, r_, uv in live:
                if (al == a or (r_ == "self" and rel == "self")) and key not in used_k \
                        and math.hypot(uv[0] - u, uv[1] - v) < TOL:
                    assign[a] = key
                    used_k.add(key)
                    break
        pairs = sorted((math.hypot(uv[0] - u, uv[1] - v), a, key)
                       for a, (rel, team, u, v, fr) in tmap.items() if a not in assign
                       for key, al, r_, uv in live if key not in used_k)
        for dd, a, key in pairs:
            if dd < TOL and a not in assign and key not in used_k:
                assign[a] = key
                used_k.add(key)
        for a, (rel, team, u, v, fr) in tmap.items():
            vis_run[a] = vis_run.get(a, 0) + 1
            visible = fr >= VISIBLE_FRAC
            mine = [(math.hypot(uv[0] - u, uv[1] - v), key, al, uv) for key, al, r_, uv in live
                    if (al == a or (r_ == "self" and rel == "self"))]
            ok = bool(mine) and min(mine)[0] < TOL
            if a in assign:
                k = assign[a]
                if a in prev_key and prev_key[a] != k and was_ok.get(a):
                    M.idsw += 1
                    M.fails.append((sc.name, round(t, 2), "idsw", f"{a}: {prev_key[a]} -> {k}"))
                prev_key[a] = k
            if visible:
                M.n_vis += 1
                if ok:
                    M.tp += 1
                    d, _k, _al, uv = min(mine)
                    M.err.append(d)
                    p0 = prev_truth.get(a)
                    if p0 is not None:
                        vel = (u - p0[0], v - p0[1])
                        step = math.hypot(*vel)
                        if step > 0.4 * SPEED[0] * dt and step < 0.02:
                            ex, ey = u - uv[0], v - uv[1]
                            M.lag.append((ex * vel[0] + ey * vel[1]) / step / step)
                else:
                    why = _miss_cause(a, rel, u, v, ids, sim.champs[0].alias)
                    M.fails.append((sc.name, round(t, 2), "miss", f"{a} ({rel}) at ({u:.3f},{v:.3f}) "
                                    f"frac {fr:.2f} [{why}]"))
                    M.miss_cause[why.split(":")[0]] = M.miss_cause.get(why.split(":")[0], 0) + 1
                    if was_ok.get(a):
                        M.frag += 1
                    gk = ghosts.get(a)
                    if gk and vis_run.get(a, 0) >= 3 and rel == "enemy":
                        M.g_live += 1
                        M.ghost_kind[gk] = M.ghost_kind.get(gk, 0) + 1
                        M.fails.append((sc.name, round(t, 2), "g_live", f"{a} {gk}"))
            was_ok[a] = ok if visible else was_ok.get(a, False)
        for a in set(tmap) ^ set(vis_run):
            if a not in tmap:
                vis_run[a] = 0
        for key, alias, rel, uv, why in out["ghost"]:
            if alias in dead:
                M.g_dead += 1
                M.fails.append((sc.name, round(t, 2), "g_dead", f"{alias} {why}"))
        for key, alias, rel, uv in live:
            if alias in dead:
                M.g_dead += 1
                M.fails.append((sc.name, round(t, 2), "g_dead", f"{alias} live"))
        # --- me
        me = sim.champs[0]
        if me.alive and me.recall_until < 0:
            M.me_n += 1
            p = out["me"]
            e = math.hypot(p[0] - me.pos[0], p[1] - me.pos[1]) if p is not None else 1.0
            M.me_err.append(e)
            if e > 0.03:
                M.me_bad += 1
                M.fails.append((sc.name, round(t, 2), "me", f"err {e:.3f}"))
        prev_truth = {a: (u, v) for a, _r, _tm, u, v, _f in truth}
        if gallery is not None and len(M.fails) > n_fail0:
            gallery.add(M.fails[n_fail0:], img, truth, out, tuple(me.pos))
    return M


# ======================================================================================
# Real set
# ======================================================================================

def run_real() -> dict:
    sys.path.insert(0, str(ROOT / "tools"))
    import real_minimap_bench as RB

    res = RB.run_all()
    T = {"gt": 0, "det": 0, "tp": 0, "team": 0, "idn": 0, "idok": 0}
    misses, fps = [], []
    for name, r in res.items():
        T["gt"] += r.n_gt
        T["det"] += r.n_det
        T["tp"] += r.tp
        T["team"] += r.team_ok
        T["idn"] += r.id_n
        T["idok"] += r.id_ok
        misses += [(name,) + tuple(m) for m in r.misses]
        fps += [(name,) + tuple(m) for m in r.false_pos] + [(name,) + tuple(w) for w in r.wrong]
    return {"rec": T["tp"] / max(1, T["gt"]), "prec": T["tp"] / max(1, T["det"]),
            "team": T["team"] / max(1, T["tp"]), "id": T["idok"] / max(1, T["idn"]),
            "gt": T["gt"], "tp": T["tp"], "det": T["det"],
            "ms": float(np.mean([r.ms for r in res.values()])), "misses": misses, "errors": fps}


# ======================================================================================
# Main
# ======================================================================================

def run(quick: bool = False, only: list[str] | None = None, real: bool = True,
        verbose: bool = False, suite: str = "main", gallery: str | None = None) -> dict:
    import logging

    prev_disable = logging.root.manager.disable
    logging.disable(logging.WARNING)
    _patch_clock()
    try:
        from treeaicoach.champions import get_default_db
        from training.real_art import RealArt

        db = get_default_db()
        art = RealArt()
        out: dict = {"games": {}}
        tot = GameMetrics()
        t0 = _time.perf_counter()
        gal = Gallery(Path(gallery)) if gallery else None
        for sc in suite_scenarios(suite, quick):
            if only and sc.name not in only:
                continue
            M = run_game(sc, db, art, verbose, gallery=gal)
            out["games"][sc.name] = M.summary()
            out["games"][sc.name]["fails"] = M.fails
            tot.merge(M)
        out["total"] = tot.summary()
        out["fails"] = tot.fails
        out["games_s"] = _time.perf_counter() - t0
        out["suite"], out["quick"] = suite, bool(quick)
        if gal is not None:
            out["gallery"] = gal.finish()
    finally:
        _unpatch_clock()
        logging.disable(prev_disable)
    if real:
        t1 = _time.perf_counter()
        out["real"] = run_real()
        out["real_s"] = _time.perf_counter() - t1
    return out


# ======================================================================================
# Failure gallery
# ======================================================================================

class Gallery:
    """Zoomed (x4) annotated crops of every failure, one folder per cause + contact sheets.

    Green: ground truth (dot + name, the icon disc); yellow: icons the overlay draws live
    (ring + label); magenta: ghosts / last-seen marks; red cross: the failure's point."""

    ZOOM = 4
    HALF = 0.11                     # crop half-size (normalized)
    PER_CAUSE = 60                  # crops kept per cause (evenly spread over the run)

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.tiles: dict[str, list] = {}
        self.count: dict[str, int] = {}

    def _centre(self, kind: str, detail: str, truth: list, me_pos) -> tuple[float, float] | None:
        import re

        m = re.search(r"\(([\d.]+),([\d.]+)\)", detail)
        if m:
            return float(m.group(1)), float(m.group(2))
        name = detail.split(" ")[0].rstrip(":")
        for a, _r, _tm, u, v, _f in truth:
            if a == name:
                return u, v
        if kind == "me" and me_pos is not None:
            return float(me_pos[0]), float(me_pos[1])
        return None

    def add(self, fails: list, img: np.ndarray, truth: list, out: dict, me_pos) -> None:
        for sc_name, t, kind, detail in fails:
            cause = kind
            if kind == "miss":
                import re

                m = re.search(r"\[(\w+)", detail)
                cause = f"miss_{m.group(1)}" if m else kind
            n = self.count.get(cause, 0)
            self.count[cause] = n + 1
            lst = self.tiles.setdefault(cause, [])
            if len(lst) >= self.PER_CAUSE and n % max(1, n // self.PER_CAUSE) != 0:
                continue
            c = self._centre(kind, detail, truth, me_pos)
            if c is None:
                continue
            tile = self._tile(img, c, truth, out, f"{sc_name} t={t} {kind}", detail)
            d = self.root / cause
            d.mkdir(exist_ok=True)
            cv2.imwrite(str(d / f"{sc_name}_{t:07.2f}_{n:04d}.png"), tile)
            lst.append(tile)
            if len(lst) > self.PER_CAUSE:
                del lst[0]

    def _tile(self, img: np.ndarray, c, truth: list, out: dict, head: str, detail: str) -> np.ndarray:
        n = img.shape[0]
        Z = self.ZOOM
        h = int(round(self.HALF * n))
        cx, cy = int(round(c[0] * n)), int(round(c[1] * n))
        pad = cv2.copyMakeBorder(img, h, h, h, h, cv2.BORDER_CONSTANT)
        crop = pad[cy:cy + 2 * h, cx:cx + 2 * h]
        big = cv2.resize(crop, (2 * h * Z, 2 * h * Z), interpolation=cv2.INTER_NEAREST)

        def P(u, v):
            return int(round((u * n - cx + h) * Z)), int(round((v * n - cy + h) * Z))

        r = int(round(ICON_R * n * Z))
        for a, rel, _tm, u, v, fr in truth:
            x, y = P(u, v)
            cv2.circle(big, (x, y), r, (0, 200, 0), 1)
            cv2.circle(big, (x, y), 3, (0, 255, 0), -1)
            cv2.putText(big, f"{a[:9]} {fr:.1f}", (x - r, y + r + 12), 0, 0.4, (0, 255, 0), 1)
        for key, alias, rel, uv in out["live"]:
            x, y = P(*uv)
            cv2.circle(big, (x, y), r + 4, (0, 230, 255), 2)
            cv2.putText(big, str(alias or key)[:9], (x - r, y - r - 6), 0, 0.4, (0, 230, 255), 1)
        for key, alias, rel, uv, why in out["ghost"]:
            if uv is None:
                continue
            x, y = P(*uv)
            cv2.circle(big, (x, y), r + 8, (255, 0, 255), 1)
            cv2.putText(big, f"{str(alias or key)[:8]}:{why.split('-')[-1]}", (x - r, y - r - 18), 0, 0.35,
                        (255, 0, 255), 1)
        x, y = P(*c)
        cv2.drawMarker(big, (x, y), (0, 0, 255), cv2.MARKER_TILTED_CROSS, 14, 2)
        bar = np.zeros((34, big.shape[1], 3), np.uint8)
        cv2.putText(bar, head, (4, 13), 0, 0.42, (255, 255, 255), 1)
        cv2.putText(bar, detail[:70], (4, 28), 0, 0.38, (200, 200, 200), 1)
        return np.vstack([bar, big])

    def finish(self) -> dict:
        """Contact sheet per cause (``<cause>.png``, 6 tiles per row); returns the counts."""
        for cause, tiles in self.tiles.items():
            if not tiles:
                continue
            th, tw = 200, 180
            small = [cv2.resize(t, (tw, th), interpolation=cv2.INTER_AREA) for t in tiles]
            rows = []
            for i in range(0, len(small), 6):
                row = small[i:i + 6]
                row += [np.zeros_like(small[0])] * (6 - len(row))
                rows.append(np.hstack(row))
            cv2.imwrite(str(self.root / f"{cause}.png"), np.vstack(rows))
        return dict(self.count)


# ======================================================================================
# Score, history, comparison
# ======================================================================================

#: metric -> (direction (+1 higher is better), absolute tolerance before "regression")
TRACKED = {
    "rec": (+1, 0.005), "prec": (+1, 0.005), "idsw": (-1, 2), "frag": (-1, 6), "id_wrong": (-1, 5),
    "team": (-1, 3), "err95": (-1, 0.002), "lag": (-1, 0.1), "g_live": (-1, 8), "g_dead": (-1, 0),
    "me95": (-1, 0.005), "me_bad": (-1, 0.01), "ms": (-1, 4.0), "cpu": (-1, 4.0),
}
REAL_TRACKED = {"rec": (+1, 0.0), "prec": (+1, 0.0), "team": (+1, 0.0), "id": (+1, 0.0)}


def score(total: dict, real: dict | None = None) -> float:
    """One number to rank runs (higher is better): recall + precision, minus the rates of the
    errors the user sees (ghost on a live champion x3, wrong identity / team x2, identity
    switches x5, dead champion drawn x10), my position off, and the cost above 15 ms."""
    n = max(1, int(total.get("n_vis", 0)))
    s = total["rec"] + total["prec"]
    s -= 3.0 * total["g_live"] / n + 2.0 * (total["id_wrong"] + total["team"]) / n
    s -= 5.0 * total["idsw"] / n + 10.0 * total["g_dead"] / n + total["me_bad"]
    s -= 0.01 * max(0.0, total.get("ms", 0.0) - 15.0)
    if real:
        s += 0.5 * (real["rec"] + real["prec"] + real["id"] + real["team"]) - 2.0
    return float(s)


def _git_rev() -> str:
    import subprocess

    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                             text=True, timeout=5).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                               capture_output=True, text=True, timeout=5).stdout.strip()
        return rev + ("+dirty" if dirty else "")
    except Exception:
        return "?"


def history_entry(out: dict, note: str = "") -> dict:
    tot = {k: v for k, v in out["total"].items() if not isinstance(v, (list, dict))}
    games = {g: {k: v for k, v in m.items() if not isinstance(v, (list, dict))}
             for g, m in out["games"].items()}
    real = {k: v for k, v in out.get("real", {}).items() if not isinstance(v, (list, dict))} or None
    return {"time": _time.strftime("%Y-%m-%d %H:%M:%S"), "rev": _git_rev(), "note": note,
            "suite": out.get("suite", "main"), "quick": out.get("quick", False),
            "only": out.get("only"), "score": score(out["total"], real), "total": tot, "games": games,
            "real": real}


def load_history(path: Path = HISTORY_FILE) -> list[dict]:
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return []


def record(out: dict, note: str = "", path: Path = HISTORY_FILE) -> dict:
    e = history_entry(out, note)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(e, default=float) + "\n")
    return e


def regressions(cur: dict, ref: dict) -> list[str]:
    """Metrics of ``cur`` worse than ``ref`` beyond their tolerance."""
    bad = []
    for k, (d, tol) in TRACKED.items():
        a, b = cur["total"].get(k), ref["total"].get(k)
        if a is None or b is None or not (math.isfinite(a) and math.isfinite(b)):
            continue
        if k in ("ms", "cpu"):
            continue                      # timing: reported, never a failure (shared machines)
        if d * (a - b) < -tol:
            bad.append(f"{k} {b:.4g} -> {a:.4g}")
    if cur.get("real") and ref.get("real"):
        for k, (d, tol) in REAL_TRACKED.items():
            a, b = cur["real"].get(k), ref["real"].get(k)
            if a is not None and b is not None and d * (a - b) < -tol - 1e-9:
                bad.append(f"real.{k} {b:.4g} -> {a:.4g}")
    return bad


def compare(cur: dict, history: list[dict]) -> str:
    """Delta table vs the previous comparable run and the best one (same suite / quick)."""
    same = [h for h in history if h.get("suite") == cur.get("suite") and h.get("quick") == cur.get("quick")
            and h.get("only") == cur.get("only") and h is not cur]
    if not same:
        return "compare: no previous run of this suite"
    prev, best = same[-1], max(same, key=lambda h: h.get("score", -1e9))
    L = [f"{'metric':8s} {'now':>9s} {'prev':>9s} {'d_prev':>9s} {'best':>9s} {'d_best':>9s}"]
    for k, (d, tol) in TRACKED.items():
        a, p, b = cur["total"].get(k), prev["total"].get(k), best["total"].get(k)
        if a is None or p is None or b is None:
            continue
        flag = "  REGRESSION" if k not in ("ms", "cpu") and d * (a - b) < -tol else ""
        L.append(f"{k:8s} {a:9.4g} {p:9.4g} {a - p:+9.3g} {b:9.4g} {a - b:+9.3g}{flag}")
    L.append(f"{'score':8s} {cur['score']:9.4f} {prev['score']:9.4f} {cur['score'] - prev['score']:+9.4f} "
             f"{best['score']:9.4f} {cur['score'] - best['score']:+9.4f}   (best: {best['rev']} {best['time']})")
    return "\n".join(L)


def report(out: dict, verbose: bool = False) -> str:
    L = []
    hdr = (f"{'game':11s} {'frm':>4s} {'vis':>5s} {'rec':>6s} {'prec':>6s} {'det':>5s} {'idsw':>4s} {'frag':>4s} "
           f"{'idX':>3s} {'team':>4s} {'err50':>6s} {'err95':>6s} {'lag':>5s} {'gLive':>5s} {'gDead':>5s} "
           f"{'me50':>6s} {'me95':>6s} {'meBad':>5s} {'ms':>5s} {'ms95':>5s} {'cpu':>5s}")
    L.append(hdr)
    rows = list(out["games"].items()) + [("TOTAL", out["total"])]
    for name, s in rows:
        L.append(f"{name:11s} {s['frames']:4d} {s['n_vis']:5d} {s['rec']:6.3f} {s['prec']:6.3f} {s['det_rec']:5.2f} "
                 f"{s['idsw']:4d} {s['frag']:4d} {s['id_wrong']:3d} {s['team']:4d} {s['err50']:6.4f} "
                 f"{s['err95']:6.4f} {s['lag']:5.2f} {s['g_live']:5d} {s['g_dead']:5d} {s['me50']:6.4f} "
                 f"{s['me95']:6.4f} {s['me_bad']:5.3f} {s['ms']:5.1f} {s['ms95']:5.1f} {s['cpu']:5.1f}")
    if "real" in out:
        r = out["real"]
        L.append(f"REAL (9 crops, {r['gt']} icons): recall {r['rec']:.3f}  precision {r['prec']:.3f}  "
                 f"team {r['team']:.3f}  identity {r['id']:.3f}  ms {r['ms']:.1f}")
        if verbose:
            for m in r["misses"]:
                L.append(f"    real miss {m}")
            for e in r["errors"]:
                L.append(f"    real err  {e}")
    if verbose:
        from collections import Counter

        cnt = Counter((f[0], f[2]) for f in out["fails"])
        L.append("failure counts: " + ", ".join(f"{k[0]}/{k[1]}={v}" for k, v in sorted(cnt.items())))
        L.append("miss causes: " + ", ".join(f"{k}={v}" for k, v in sorted(out["total"]["miss_cause"].items())))
        L.append("g_live kinds: " + ", ".join(f"{k}={v}" for k, v in sorted(out["total"]["ghost_kind"].items())))
        shown = Counter()
        for f in out["fails"]:
            k = (f[0], f[2])
            if shown[k] < 6:
                shown[k] += 1
                L.append(f"    {f}")
    L.append(f"time: games {out['games_s']:.1f} s" + (f", real {out['real_s']:.1f} s" if "real" in out else ""))
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--no-real", action="store_true")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("-v", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--suite", default="main", choices=SUITES)
    ap.add_argument("--gallery", help="folder for the failure gallery (crops + contact sheets)")
    ap.add_argument("--compare", action="store_true", help="delta vs the previous / best run")
    ap.add_argument("--no-record", action="store_true", help="do not append to det_gym_history.jsonl")
    ap.add_argument("--note", default="", help="note stored with the history entry")
    a = ap.parse_args()
    out = run(a.quick, a.only, not a.no_real, a.v, suite=a.suite, gallery=a.gallery)
    out["only"] = a.only
    print(report(out, a.v))
    if a.gallery:
        print("gallery:", a.gallery, out.get("gallery"))
    hist = load_history()
    entry = history_entry(out, a.note)
    print(f"score {entry['score']:.4f}")
    if a.compare:
        print(compare(entry, hist))
    if not a.no_record:
        record(out, a.note)
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
