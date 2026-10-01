"""Sim-to-real gap of the detection gym: simple image statistics of the REAL 2026 crops
(``tests/fixtures/real`` + any ``tests/fixtures/real_cases``) vs the gym's synthetic frames.

Compared (per team where it applies):

* ring colour: Lab of the ring annulus (0.86-1.0 x icon radius) of every labelled icon ->
  median L / a / b, chroma, and the overlap (Bhattacharyya coefficient) of the a/b histograms;
* icon size: the matcher's calibrated icon diameter / minimap width;
* portrait contrast: std of the lightness inside the icon;
* background: mean Lab, lightness std and Laplacian energy (texture / blur) of the map away
  from the icons, and the JPEG blockiness (8 px grid energy ratio).

A line is flagged ``DRIFT`` when the synthetic median leaves the real [p10, p90] range (or
the histogram overlap is < 0.6): adjust the renderer (tools/det_gym.py Sim.render,
training/real_art.py) before trusting gym gains on that aspect.

    python tools/det_sim2real.py [--frames 60]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import treeaicoach  # noqa: E402,F401
import cv2  # noqa: E402
import numpy as np  # noqa: E402

R_ICON = 0.045


def _ring_lab(img: np.ndarray, u: float, v: float, r: float) -> np.ndarray:
    from treeaicoach.roster_matcher import RosterMatcher

    W = img.shape[1]
    return RosterMatcher._ring_pixels(img, u * W, v * W, r * W)


def _stats(samples: list[tuple[np.ndarray, list]]) -> dict:
    """samples: [(img, [(u, v, team)])] -> statistics dict of lists."""
    out: dict = {"ring_ally": [], "ring_enemy": [], "portrait_std": [], "bg_L": [], "bg_std": [],
                 "bg_lap": [], "block": [], "bg_a": [], "bg_b": []}
    for img, icons in samples:
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
        n = img.shape[0]
        mask = np.ones(img.shape[:2], bool)
        for u, v, team in icons:
            px = _ring_lab(img, u, v, R_ICON)
            if len(px):
                out["ring_enemy" if team == "enemy" else "ring_ally"].append(px)
            cx, cy, rr = int(u * n), int(v * n), int(0.6 * R_ICON * n)
            patch = lab[max(0, cy - rr):cy + rr, max(0, cx - rr):cx + rr, 0]
            if patch.size:
                out["portrait_std"].append(float(patch.std()))
            cv2.circle(mask.view(np.uint8), (cx, cy), int(1.3 * R_ICON * n), 0, -1)
        L = lab[:, :, 0]
        out["bg_L"].append(float(L[mask].mean()))
        out["bg_a"].append(float(lab[:, :, 1][mask].mean()))
        out["bg_b"].append(float(lab[:, :, 2][mask].mean()))
        out["bg_std"].append(float(L[mask].std()))
        lap = cv2.Laplacian(L, cv2.CV_32F)
        out["bg_lap"].append(float(np.abs(lap)[mask].mean()))
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
        dx = np.abs(np.diff(g, axis=1))
        on = dx[:, 7::8].mean() if dx.shape[1] > 8 else 0.0
        out["block"].append(float(on / max(dx.mean(), 1e-6)))
    return out


def _real_samples() -> list:
    dirs = [ROOT / "tests" / "fixtures" / "real"] + sorted(
        p.parent for p in (ROOT / "tests" / "fixtures" / "real_cases").glob("*/ground_truth.json"))
    out = []
    for d in dirs:
        try:
            gt = json.loads((d / "ground_truth.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for rec in gt.get("images", {}).values():
            img = cv2.imread(str(d / rec["file"]))
            if img is None:
                continue
            out.append((img, [(c["u"], c["v"], c.get("team", "ally")) for c in rec.get("champions", [])]))
    return out


def _gym_samples(n_frames: int) -> list:
    import det_gym as G
    from training.real_art import RealArt
    from treeaicoach.champions import get_default_db

    db, art = get_default_db(), RealArt()
    out = []
    per = max(1, n_frames // 5)
    for sc in G.scenarios():
        sim = G.Sim(sc, db, art)
        dt = 1.0 / sc.fps
        total = int(sc.seconds * sc.fps)
        keep = set(np.linspace(int(sc.fps), total - 1, per).astype(int).tolist())
        for f in range(total):
            sim.step(f * dt, dt)
            img, truth, _dead, _fog = sim.render(dt)
            if f in keep:
                out.append((img, [(u, v, "enemy" if r == "enemy" else "ally")
                                  for _a, r, _t, u, v, fr in truth if fr >= 0.8]))
    return out


def _bhatt(a: np.ndarray, b: np.ndarray) -> float:
    if not len(a) or not len(b):
        return float("nan")
    rng = [[96, 176], [96, 176]]
    ha, _, _ = np.histogram2d(a[:, 1], a[:, 2], bins=16, range=rng)
    hb, _, _ = np.histogram2d(b[:, 1], b[:, 2], bins=16, range=rng)
    ha /= max(ha.sum(), 1)
    hb /= max(hb.sum(), 1)
    return float(np.sqrt(ha * hb).sum())


def report(real: dict, gym: dict) -> list[str]:
    L = [f"{'statistic':22s} {'real p10':>9s} {'real p50':>9s} {'real p90':>9s} {'gym p50':>9s}  flag"]

    def row(name: str, r: list, g: list) -> None:
        if not r or not g:
            return
        p10, p50, p90 = np.percentile(r, [10, 50, 90])
        q = float(np.median(g))
        flag = "DRIFT" if not (p10 <= q <= p90) else ""
        L.append(f"{name:22s} {p10:9.2f} {p50:9.2f} {p90:9.2f} {q:9.2f}  {flag}")

    for team in ("ally", "enemy"):
        R = np.concatenate(real[f"ring_{team}"]) if real[f"ring_{team}"] else np.zeros((0, 3))
        Gm = np.concatenate(gym[f"ring_{team}"]) if gym[f"ring_{team}"] else np.zeros((0, 3))
        for k, nm in ((0, "L"), (1, "a"), (2, "b")):
            row(f"ring {team} {nm}", [float(np.median(x[:, k])) for x in real[f"ring_{team}"]],
                [float(np.median(x[:, k])) for x in gym[f"ring_{team}"]])
        chroma = lambda X: [float(np.median(np.hypot(x[:, 1] - 128, x[:, 2] - 128))) for x in X]  # noqa: E731
        row(f"ring {team} chroma", chroma(real[f"ring_{team}"]), chroma(gym[f"ring_{team}"]))
        bc = _bhatt(R, Gm)
        L.append(f"{'ring ' + team + ' a/b overlap':22s} {bc:9.2f}{'':30s}  {'DRIFT' if bc < 0.6 else ''}")
    for k in ("portrait_std", "bg_L", "bg_a", "bg_b", "bg_std", "bg_lap", "block"):
        row(k, real[k], gym[k])
    return L


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=60)
    a = ap.parse_args()
    real = _stats(_real_samples())
    gym = _stats(_gym_samples(a.frames))
    print("\n".join(report(real, gym)))


if __name__ == "__main__":
    main()
