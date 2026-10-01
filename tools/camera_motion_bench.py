"""Camera-motion / tracking benchmark on synthetic minimap sequences.

The minimap never moves; only the white camera rectangle does. Scenarios (8 fps, ~10 s each,
rendered with :mod:`treeaicoach.render`, real champion portraits, JPEG + blur):

* ``pan``    - edge-scroll: the camera sweeps the map (~0.15 / s), its lines drawn OVER the icons;
* ``jump``   - the camera jumps to allies / back to me every ~1.5 s (F-keys, minimap clicks);
* ``locked`` - camera locked on me (Y), then dragged away while I stand still, then re-centred;
* ``cross``  - champions walking through each other (identity swaps), camera static;
* ``lockhide`` - as ``locked`` but an ally stands ON my icon while the camera is dragged away
  (my icon is not visible: the camera lock must not move me with the camera).

Pipeline: :class:`RosterMatcher` -> ``Identified`` -> :class:`Tracker` (as the engine).
Metrics: detection recall / precision, ID errors (a champion's name on another one's icon),
track drops (visible champion whose track is hidden: fragmentation), ghost frames, lag
(track position behind the truth along the walking direction, in frames) for the smoothed
``Track.position()`` and the Kalman ``kf_position``, my-position errors while the camera moves.

    python tools/camera_motion_bench.py [--seeds N]
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
# (comparisons: BENCH_PKG_ROOT=<dir containing another treeaicoach package> runs that one)
_PKG = os.environ.get("BENCH_PKG_ROOT") or str(ROOT)
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

FPS = 8.0
CAM_W, CAM_H = 0.275, 0.155
WALK = 0.025          # normalized minimap units / s (~350-400 game units / s)


def _champs(db, rng, n=10):
    names = [e.alias for e in db.all() if db.load_icon(e.alias) is not None]
    pick = list(rng.choice(names, n, replace=False))
    rels = ["self"] + ["ally"] * 4 + ["enemy"] * 5
    return pick, rels, [db.load_icon(a) for a in pick]


def make_scenario(kind: str, seed: int, db, n_frames: int = 80, size: int = 300):
    """Frames ``[(img, truth [(name, rel, u, v)], cam (u0, v0, u1, v1))]`` + names / rels."""
    import cv2

    from treeaicoach import render as R

    rng = np.random.default_rng(seed)
    names, rels, icons = _champs(db, rng)
    ren = R.MinimapRenderer()
    tex = str(rng.choice(ren.textures()))
    rad = 0.047
    # positions / walking goals
    pos = rng.uniform(0.15, 0.85, (10, 2))
    goal = rng.uniform(0.1, 0.9, (10, 2))
    vis = np.ones(10, bool)
    vis[rng.choice(np.arange(1, 10), 3, replace=False)] = False      # a few in the fog
    if kind == "cross":
        # pairs walking straight through each other near the map centre
        c = np.array([0.5, 0.5])
        for k, (a, b) in enumerate(((5, 6), (1, 7), (2, 3))):
            off = np.array([0.0, 0.12 * (k - 1)])
            pos[a] = c + off + [-0.12, 0.0]
            goal[a] = c + off + [0.14, 0.0]
            pos[b] = c + off + [0.12, 0.01]
            goal[b] = c + off + [-0.14, 0.01]
            vis[a] = vis[b] = True
    if kind in ("locked", "lockhide"):
        goal[0] = pos[0] + [0.2, 0.1]
        vis[1] = True
    dt = 1.0 / FPS
    frames = []
    cam_c = pos[0].copy()
    pan_dir = rng.normal(0, 1, 2)
    pan_dir /= np.linalg.norm(pan_dir)
    jump_t = 0
    for f in range(n_frames):
        t = f * dt
        # champions walk
        for j in range(10):
            if kind in ("locked", "lockhide") and j == 0 and 3.0 <= t < 7.0:
                continue                                  # I stand still while the camera moves
            if kind == "lockhide" and j == 1 and 2.5 <= t < 7.0:
                pos[1] = pos[0] + [0.004, 0.003]          # an ally drawn over my icon
                continue
            d = goal[j] - pos[j]
            L = float(np.hypot(*d))
            speed = WALK * (1.25 if j % 3 == 0 else 1.0)
            if L < 0.01:
                goal[j] = rng.uniform(0.1, 0.9, 2)
            else:
                pos[j] += d / L * min(L, speed * dt)
            if kind != "cross" and rng.random() < 0.004:      # flash / dash
                pos[j] += rng.normal(0, 1, 2) / math.sqrt(2) * 0.03
        pos[:] = np.clip(pos, 0.05, 0.95)
        # camera
        if kind == "pan":
            cam_c = cam_c + pan_dir * 0.15 * dt
            if not (0.1 < cam_c[0] < 0.9 and 0.1 < cam_c[1] < 0.9):
                pan_dir = -pan_dir + rng.normal(0, 0.3, 2)
                pan_dir /= np.linalg.norm(pan_dir)
                cam_c = np.clip(cam_c, 0.1, 0.9)
        elif kind == "jump":
            if f - jump_t >= 12:
                jump_t = f
                tgt = int(rng.choice([0, 1, 2, 3, 4]))
                cam_c = pos[tgt].copy() + [0.0, -0.022]
        elif kind in ("locked", "lockhide"):
            if t < 3.0 or t >= 7.0:
                cam_c = pos[0] + [0.0, -0.022]           # locked: my icon at a fixed offset
            else:
                cam_c = cam_c + np.array([0.12, 0.05]) * dt  # dragged away, I stand still
        else:  # cross: camera on the crossing
            cam_c = np.array([0.5, 0.5])
        cam = (float(cam_c[0] - CAM_W / 2), float(cam_c[1] - CAM_H / 2),
               float(cam_c[0] + CAM_W / 2), float(cam_c[1] + CAM_H / 2))
        champs, truth = [], []
        order = list(rng.permutation(10))
        if kind == "lockhide":
            order = [0] + [j for j in order if j != 0]          # me first: the ally covers me
        for j in order:
            if not vis[j]:
                continue
            champs.append(R.ChampionSprite(u=float(pos[j][0]), v=float(pos[j][1]), r=rad,
                                           relation=rels[j], icon=icons[j],
                                           self_glow=0.6 if rels[j] == "self" else 0.0))
            truth.append((names[j], rels[j], float(pos[j][0]), float(pos[j][1])))
        sc = R.Scene(texture=tex, size=size, champions=champs, camera=cam, camera_on_top=True,
                     camera_px=2 if f % 2 else 1,
                     minions=[(float(rng.uniform(0.1, 0.9)), float(rng.uniform(0.1, 0.9)),
                               str(rng.choice(["ally", "enemy"]))) for _ in range(12)])
        img = ren.render(sc)
        img = cv2.GaussianBlur(img, (0, 0), 0.6)
        img = cv2.imdecode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])[1], 1)
        frames.append((img, truth, cam))
    return names, rels, icons, rad, frames


class Metrics:
    def __init__(self) -> None:
        self.n_truth = self.tp = self.fp = self.id_err = 0
        self.drops = self.drop_frames = 0
        self.lag_pos: list[float] = []
        self.lag_kf: list[float] = []
        self.err_pos: list[float] = []
        self.err_kf: list[float] = []
        self.me_err_frames = self.me_frames = 0
        self.ms: list[float] = []

    def merge(self, o: "Metrics") -> None:
        for k, v in vars(o).items():
            if isinstance(v, list):
                getattr(self, k).extend(v)
            else:
                setattr(self, k, getattr(self, k) + v)

    def row(self, name: str) -> str:
        rec = self.tp / max(1, self.n_truth)
        prec = self.tp / max(1, self.tp + self.fp)

        def m(x):
            return float(np.mean(x)) if x else float("nan")

        return (f"{name:8s} recall {rec:.3f} prec {prec:.3f} id_err {self.id_err:3d} "
                f"drops {self.drops:3d} drop_frames {self.drop_frames:4d} "
                f"lag(pos) {m(self.lag_pos):5.2f}f lag(kf) {m(self.lag_kf):5.2f}f "
                f"err(pos) {m(self.err_pos) * 1000:4.1f} err(kf) {m(self.err_kf) * 1000:4.1f} (x1e-3) "
                f"me_bad {self.me_err_frames}/{self.me_frames} ms {m(self.ms):5.1f}")


def run_scenario(kind: str, seed: int, db, n_frames: int = 80) -> Metrics:
    from treeaicoach.identifier import Identified
    from treeaicoach.roster_matcher import RosterEntry, RosterMatcher
    from treeaicoach.tracker import Tracker

    names, rels, icons, rad, frames = make_scenario(kind, seed, db, n_frames)
    m = RosterMatcher(db=db)
    m.set_entries([RosterEntry(a, r, ic) for a, r, ic in zip(names, rels, icons)])
    trk = Tracker()
    M = Metrics()
    prev_truth: dict[str, tuple[float, float]] = {}
    was_vis: dict[str, bool] = {}
    tol = 0.6 * rad
    for f, (img, truth, cam) in enumerate(frames):
        t = f / FPS
        t0 = time.perf_counter()
        dets = m.detect(img, t=t)
        ids = [Identified(det=d, alias=d.alias,
                          relation=("self" if rels[names.index(d.alias)] == "self" else d.cls)
                          if d.alias in names else d.cls, team=None, id_score=float(d.score))
               for d in dets]
        trk.update(t, ids)
        M.ms.append((time.perf_counter() - t0) * 1000.0)
        if f < 6:                                    # calibration frames
            prev_truth = {n: (u, v) for n, _r, u, v in truth}
            continue
        tm = {n: (u, v) for n, _r, u, v in truth}
        M.n_truth += len(truth)
        for d in dets:
            g = tm.get(d.alias)
            if g is not None and math.hypot(d.u - g[0], d.v - g[1]) < tol:
                M.tp += 1
            else:
                M.fp += 1
                if any(math.hypot(d.u - u, d.v - v) < tol for n, (u, v) in tm.items() if n != d.alias):
                    M.id_err += 1
        for n, rel, u, v in truth:
            tr = trk.me() if rel == "self" else trk.get(n)
            vis_now = tr is not None and tr.visible
            if not vis_now:
                M.drop_frames += 1
                if was_vis.get(n):
                    M.drops += 1
            was_vis[n] = vis_now
            if rel == "self":
                M.me_frames += 1
                p = tr.position() if tr is not None else None
                if p is None or math.hypot(p[0] - u, p[1] - v) > 0.03:
                    M.me_err_frames += 1
            if not vis_now:
                continue
            p0 = prev_truth.get(n)
            vel = (u - p0[0], v - p0[1]) if p0 is not None else (0.0, 0.0)
            step = math.hypot(*vel)
            for pos, lag, err in ((tr.position(), M.lag_pos, M.err_pos),
                                  (tr.kf_position(), M.lag_kf, M.err_kf)):
                if pos is None:
                    continue
                ex, ey = u - pos[0], v - pos[1]
                err.append(math.hypot(ex, ey))
                if step > 0.5 * WALK / FPS:          # walking: lag along the motion, in frames
                    lag.append((ex * vel[0] + ey * vel[1]) / step / step)
        prev_truth = tm
    return M


def main() -> None:
    global FPS
    from treeaicoach.champions import get_default_db

    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--frames", type=int, default=80)
    ap.add_argument("--fps", type=float, default=FPS, help="detection rate (frames / s)")
    ap.add_argument("kinds", nargs="*", default=["pan", "jump", "locked", "lockhide", "cross"])
    a = ap.parse_args()
    FPS = float(a.fps)
    db = get_default_db()
    tot = Metrics()
    for kind in a.kinds:
        mk = Metrics()
        for s in range(a.seeds):
            mk.merge(run_scenario(kind, 100 * s + 7, db, a.frames))
        print(mk.row(kind), flush=True)
        tot.merge(mk)
    print(tot.row("TOTAL"))


if __name__ == "__main__":
    main()
