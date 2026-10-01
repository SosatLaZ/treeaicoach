"""Real-screenshot minimap recognition benchmark.

Runs the production detection pipeline (``create_detector`` = roster matcher + generic
detector for what the roster does not explain) on the real minimap crops of
``tests/fixtures/real`` (ground truth in ``ground_truth.json``: icon centres, team from the
ring colour, champion when identifiable) and reports, per image, recall / precision / team
accuracy / identity accuracy.

Each crop is fed ``FRAMES`` times (a static scene at 8 fps: calibration + temporal gates
settle) and the detections of the last frame are scored. A detection matches a ground-truth
icon when its centre is within ``MATCH_DIST`` (normalized; icon radius ~ 0.045).

    python tools/real_minimap_bench.py [-v] [--backend auto|onnx|classic]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
# (comparisons: BENCH_PKG_ROOT=<dir containing another treeaicoach package> runs that one)
_PKG = os.environ.get("BENCH_PKG_ROOT") or str(ROOT)
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

FIX = ROOT / "tests" / "fixtures" / "real"
MATCH_DIST = 0.03
FRAMES = 12
FPS = 8.0


@dataclass
class ImageResult:
    name: str
    n_gt: int = 0
    n_det: int = 0
    tp: int = 0
    team_ok: int = 0
    id_n: int = 0
    id_ok: int = 0
    ms: float = 0.0
    misses: list = field(default_factory=list)
    false_pos: list = field(default_factory=list)
    wrong: list = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.tp / self.n_gt if self.n_gt else 1.0

    @property
    def precision(self) -> float:
        return self.tp / self.n_det if self.n_det else 1.0

    @property
    def team_acc(self) -> float:
        return self.team_ok / self.tp if self.tp else 1.0

    @property
    def id_acc(self) -> float:
        return self.id_ok / self.id_n if self.id_n else 1.0


def load_truth(fix: Path = FIX) -> dict:
    return json.loads((Path(fix) / "ground_truth.json").read_text(encoding="utf-8"))


def roster_entries(game: dict, db, seed: int = 0):
    """RosterEntry list of a ground-truth game; unknown players filled with fixed champions."""
    from treeaicoach.roster_matcher import RosterEntry

    allies = list(game.get("ally", []))
    enemies = list(game.get("enemy", []))
    used = {game["self"], *allies, *enemies}
    rng = np.random.default_rng(seed)
    pool = [e.alias for e in db.all() if e.alias not in used and db.load_icon(e.alias) is not None]
    extra = [pool[i] for i in rng.permutation(len(pool))]
    while len(allies) < 4:
        allies.append(extra.pop())
    while len(enemies) < 5:
        enemies.append(extra.pop())
    out = [RosterEntry(game["self"], "self", db.load_icon(game["self"]))]
    out += [RosterEntry(a, "ally", db.load_icon(a)) for a in allies]
    out += [RosterEntry(a, "enemy", db.load_icon(a)) for a in enemies]
    return out


def run_image(name: str, spec: dict, truth: dict, db, backend: str = "auto",
              frames: int = FRAMES, detector=None, fix: Path = FIX) -> ImageResult:
    import cv2

    from treeaicoach.detector import create_detector

    img = cv2.imread(str(Path(fix) / spec["file"]))
    game = truth["rosters"][spec["game"]]
    det = detector or create_detector(backend, db=db, learn_cache=None)
    det.matcher.set_entries(roster_entries(game, db))
    dead = list(spec.get("dead", []))
    dets = []
    times = []
    for f in range(frames):
        if dead:
            det.matcher.set_dead(dead, now=f / FPS)
        t0 = time.perf_counter()
        dets = _detect(det, img, f / FPS)
        times.append((time.perf_counter() - t0) * 1000.0)
    gts = spec["champions"]
    res = ImageResult(name=name, n_gt=len(gts), n_det=len(dets),
                      ms=float(np.median(times[len(times) // 2:])))
    # greedy matching by distance
    pairs = sorted(((math.hypot(d.u - g["u"], d.v - g["v"]), i, j)
                    for i, g in enumerate(gts) for j, d in enumerate(dets)))
    used_g, used_d = set(), set()
    for dist, i, j in pairs:
        if dist > MATCH_DIST or i in used_g or j in used_d:
            continue
        used_g.add(i)
        used_d.add(j)
        g, d = gts[i], dets[j]
        res.tp += 1
        team = "enemy" if d.cls == "enemy" else "ally"
        if team == g["team"]:
            res.team_ok += 1
        else:
            res.wrong.append(("team", g.get("name"), g["team"], team, round(d.u, 3), round(d.v, 3)))
        if g.get("name"):
            res.id_n += 1
            if getattr(d, "alias", None) == g["name"]:
                res.id_ok += 1
            else:
                res.wrong.append(("id", g["name"], getattr(d, "alias", None), round(d.u, 3), round(d.v, 3)))
    res.misses = [(g.get("name"), g["team"], g["u"], g["v"]) for i, g in enumerate(gts) if i not in used_g]
    res.false_pos = [(getattr(d, "alias", None), d.cls, round(d.u, 3), round(d.v, 3), round(d.score, 2))
                     for j, d in enumerate(dets) if j not in used_d]
    return res


def _detect(det, img, t):
    """HybridDetector.detect with the matcher's clock set to ``t`` (static replay)."""
    m = det.matcher
    orig = m.detect
    try:
        m.detect = lambda bgr, t=t, _o=orig: _o(bgr, t=t)
        return list(det.detect(img) or [])
    finally:
        m.detect = orig


def run_all(backend: str = "auto", names=None, verbose: bool = False,
            fix: Path = FIX) -> dict[str, ImageResult]:
    from treeaicoach.champions import get_default_db

    db = get_default_db()
    truth = load_truth(fix)
    out = {}
    for name, spec in truth["images"].items():
        if names and name not in names:
            continue
        out[name] = run_image(name, spec, truth, db, backend, fix=fix)
    return out


def report(results: dict[str, ImageResult], verbose: bool = False) -> str:
    lines = [f"{'image':8s} {'gt':>3s} {'det':>3s} {'recall':>6s} {'prec':>6s} {'team':>6s} {'id':>6s} {'ms':>6s}"]
    T = {"gt": 0, "det": 0, "tp": 0, "team": 0, "idn": 0, "idok": 0}
    for name, r in results.items():
        lines.append(f"{name:8s} {r.n_gt:3d} {r.n_det:3d} {r.recall:6.2f} {r.precision:6.2f} "
                     f"{r.team_acc:6.2f} {r.id_acc:6.2f} {r.ms:6.1f}")
        if verbose:
            for m in r.misses:
                lines.append(f"    miss {m}")
            for fp in r.false_pos:
                lines.append(f"    FP   {fp}")
            for w in r.wrong:
                lines.append(f"    bad  {w}")
        T["gt"] += r.n_gt
        T["det"] += r.n_det
        T["tp"] += r.tp
        T["team"] += r.team_ok
        T["idn"] += r.id_n
        T["idok"] += r.id_ok
    lines.append(f"{'TOTAL':8s} {T['gt']:3d} {T['det']:3d} {T['tp'] / max(1, T['gt']):6.2f} "
                 f"{T['tp'] / max(1, T['det']):6.2f} {T['team'] / max(1, T['tp']):6.2f} "
                 f"{T['idok'] / max(1, T['idn']):6.2f}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", action="store_true")
    ap.add_argument("--backend", default="auto")
    ap.add_argument("--dir", default=str(FIX), help="case folder (ground_truth.json + images)")
    ap.add_argument("images", nargs="*")
    a = ap.parse_args()
    print(report(run_all(a.backend, a.images or None, fix=Path(a.dir)), verbose=a.v))


if __name__ == "__main__":
    main()
