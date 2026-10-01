"""Micro-gyms: one detection stage at a time, each in a few seconds (fast iteration).

* ``ring``     - team from the ring: isolated icons on real 2026 map art, random ring
                 colours from the measured ranges (darkened up to 60 %), sizes 200-320 px,
                 blur, JPEG; accuracy of the ring-colour model, of the patch verifier and of
                 the matcher's combined rule (colour, verifier second opinion when the colour
                 does not vote).
* ``identity`` - portrait identity: the 10 roster portraits rendered at their known spots;
                 top-1 accuracy of the matcher's NCC among the champion's team (and the
                 margin to the second best) per minimap size.
* ``stacks``   - 2-4 icons overlapping (0.3-0.9 diameter apart) on real art, a static scene
                 fed 6 times to a fresh HybridDetector: recall of the icons >= 50 % visible,
                 identity accuracy, false positives.
* ``tracker``  - no images: champions walking through each other with identification noise
                 (wrong / missing names, dropped frames) fed to the Tracker: identity
                 switches (a track drawn on another champion), lag (frames).

    python tools/det_micro.py [ring|identity|stacks|tracker|all] [--n N] [--seed S]
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import treeaicoach  # noqa: E402,F401  (single-threaded BLAS)
import cv2  # noqa: E402
import numpy as np  # noqa: E402

R_ICON = 0.045


def _art():
    from training.real_art import RealArt

    return RealArt()


def _bg(art, rng, size: int) -> np.ndarray:
    bg = art.backgrounds[int(rng.integers(len(art.backgrounds)))]
    return cv2.resize(bg, (size, size), interpolation=cv2.INTER_AREA if size < 300 else cv2.INTER_LINEAR)


def _degrade(img: np.ndarray, rng) -> np.ndarray:
    img = cv2.GaussianBlur(img, (0, 0), float(rng.uniform(0.3, 1.1)))
    return cv2.imdecode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(rng.integers(50, 91))])[1], 1)


def _ring_colour(rng, team: str, dark_max: float = 0.6) -> tuple:
    from training import real_art as RA

    c = RA._lerp_bgr(rng, RA.ENEMY_RING_RGB if team == "enemy" else RA.ALLY_RING_RGB)
    k = 1.0 - float(rng.uniform(0.0, dark_max))
    return tuple(int(x * k) for x in c)


def micro_ring(n: int = 300, seed: int = 0) -> dict:
    from treeaicoach import render as R
    from treeaicoach import roster_matcher as RM
    from treeaicoach.champions import get_default_db
    from treeaicoach.patch_classifier import PatchVerifier

    rng = np.random.default_rng(seed)
    art, db, ver = _art(), get_default_db(), PatchVerifier.load()
    names = [e.alias for e in db.all() if db.load_icon(e.alias) is not None]
    rings = RM.RingColorModel()
    ok_c = ok_v = ok_comb = 0
    no_vote = 0
    for _ in range(n):
        size = int(rng.integers(200, 321))
        img = _bg(art, rng, size).copy()
        team = "enemy" if rng.random() < 0.5 else "ally"
        u, v = rng.uniform(0.15, 0.85, 2)
        R.draw_champion_icon(img, u * size, v * size, R_ICON * size, db.load_icon(str(rng.choice(names))),
                             _ring_colour(rng, team), ring_frac=0.12, inner_line_bgr=R.INNER_LINE_BGR,
                             outline_px=0.6)
        img = _degrade(img, rng)
        f_en, f_al = rings.classify(RM.RosterMatcher._ring_pixels(img, u * size, v * size, R_ICON * size))
        col = "enemy" if f_en > f_al else "ally"
        ok_c += col == team
        p = ver.verify_candidates(img, [(u, v, R_ICON)])[0] if ver is not None else np.array([0, .5, .5])
        vt = "enemy" if p[1] > p[2] else "ally"
        ok_v += vt == team
        own = f_en if team == "enemy" else f_al        # (the matcher knows the champion's team)
        comb = col
        if max(f_en, f_al) < RM.RING_VERIFY_OWN:
            no_vote += 1
            icon = 1.0 - float(p[0])
            if icon >= RM.VERIFY_MIN and max(p[1], p[2]) / max(icon, 1e-6) >= RM.RING_VERIFY_TEAM:
                comb = vt
        del own
        ok_comb += comb == team
    return {"n": n, "colour_acc": ok_c / n, "verifier_acc": ok_v / n, "combined_acc": ok_comb / n,
            "colour_no_vote": no_vote / n}


def _matcher_bank(m, img: np.ndarray, scale: float):
    """(feat, bank, kx) of the matcher at ``scale`` (as in RosterMatcher._detect)."""
    from treeaicoach import roster_matcher as RM

    H, W = img.shape[:2]
    inner_full = RM.INNER_RATIO * scale * W
    factor = min(1.0, RM.WORK_INNER_PX / inner_full)
    work = m._work(img, factor)
    fx = work.shape[1] / W
    return RM._features(work), m._bank(inner_full * fx), W * fx


def micro_identity(n: int = 12, seed: int = 0) -> dict:
    from treeaicoach import render as R
    from treeaicoach import roster_matcher as RM
    from treeaicoach.champions import get_default_db

    rng = np.random.default_rng(seed)
    art, db = _art(), get_default_db()
    names = [e.alias for e in db.all() if db.load_icon(e.alias) is not None]
    per: dict[str, list] = {}
    margins = []
    for _ in range(n):
        size = int(rng.choice([200, 230, 260, 300, 320]))
        pick = list(rng.choice(names, 10, replace=False))
        m = RM.RosterMatcher(db=db)
        rels = ["self"] + ["ally"] * 4 + ["enemy"] * 5
        m.set_entries([RM.RosterEntry(a, r, db.load_icon(a)) for a, r in zip(pick, rels)])
        img = _bg(art, rng, size).copy()
        pos = []
        for k, a in enumerate(pick):
            u, v = 0.1 + 0.18 * (k % 5) + rng.uniform(-0.02, 0.02), 0.25 + 0.4 * (k // 5) + rng.uniform(-0.03, 0.03)
            R.draw_champion_icon(img, u * size, v * size, R_ICON * size, db.load_icon(a),
                                 _ring_colour(rng, "enemy" if k >= 5 else "ally", 0.3), ring_frac=0.12,
                                 inner_line_bgr=R.INNER_LINE_BGR, outline_px=0.6)
            pos.append((u, v))
        img = _degrade(img, rng)
        feat, bank, kx = _matcher_bank(m, img, 2 * R_ICON)
        half = (bank.size - 1) / 2.0
        for k, (u, v) in enumerate(pos):
            team = [j for j in range(10) if (j >= 5) == (k >= 5)]
            x0 = int(round(u * kx - half - 0.5)) - 1
            y0 = int(round(v * kx - half - 0.5)) - 1
            if x0 < 0 or y0 < 0:
                continue
            roi = feat[y0:y0 + bank.size + 2, x0:x0 + bank.size + 2]
            sc = []
            for j in team:
                res = RM.local_ncc(roi, bank, j, wl=m._wl)
                sc.append(float(res[0].max()) if res is not None else -1.0)
            order = np.argsort(sc)[::-1]
            per.setdefault(str(size), []).append(team[order[0]] == k)
            margins.append(sc[order[0]] - sc[order[1]])
    return {"top1": {s: round(float(np.mean(v)), 3) for s, v in sorted(per.items())},
            "top1_all": round(float(np.mean([x for v in per.values() for x in v])), 3),
            "margin_median": round(float(np.median(margins)), 3)}


def micro_stacks(n: int = 10, seed: int = 0) -> dict:
    from treeaicoach import render as R
    from treeaicoach.champions import get_default_db
    from treeaicoach.detector import create_detector
    from treeaicoach.roster_matcher import RosterEntry

    rng = np.random.default_rng(seed)
    art, db = _art(), get_default_db()
    names = [e.alias for e in db.all() if db.load_icon(e.alias) is not None]
    tp = n_vis = id_ok = fp = 0
    for _ in range(n):
        size = int(rng.integers(230, 321))
        pick = list(rng.choice(names, 10, replace=False))
        rels = ["self"] + ["ally"] * 4 + ["enemy"] * 5
        det = create_detector("auto", db=db, learn_cache=None)
        det.matcher.set_entries([RosterEntry(a, r, db.load_icon(a)) for a, r in zip(pick, rels)])
        img = _bg(art, rng, size).copy()
        k_st = int(rng.integers(2, 5))
        members = list(rng.choice(np.arange(1, 10), k_st, replace=False))
        cu, cv_ = rng.uniform(0.25, 0.75, 2)
        D = 2 * R_ICON
        placed = []
        for j, idx in enumerate(members):
            if j == 0:
                u, v = cu, cv_
            else:
                a = rng.uniform(0, 2 * math.pi)
                d = rng.uniform(0.3, 0.9) * D
                bu, bv = placed[int(rng.integers(len(placed)))][1:]
                u, v = bu + d * math.cos(a), bv + d * math.sin(a)
            placed.append((idx, float(u), float(v)))
        for idx, u, v in placed:
            R.draw_champion_icon(img, u * size, v * size, R_ICON * size, db.load_icon(pick[idx]),
                                 _ring_colour(rng, "enemy" if idx >= 5 else "ally", 0.3), ring_frac=0.12,
                                 inner_line_bgr=R.INNER_LINE_BGR, outline_px=0.6)
        img = _degrade(img, rng)
        # visible fraction (drawn later = on top)
        vis = []
        for j, (idx, u, v) in enumerate(placed):
            pts = np.array([[u + R_ICON * r * math.cos(t), v + R_ICON * r * math.sin(t)]
                            for r in (0.3, 0.6, 0.9) for t in np.linspace(0, 6.28, 24)])
            cov = np.zeros(len(pts), bool)
            for _i2, u2, v2 in placed[j + 1:]:
                cov |= np.hypot(pts[:, 0] - u2, pts[:, 1] - v2) < R_ICON * 1.02
            vis.append(1.0 - cov.mean())
        dets = []
        for f in range(6):
            dets = list(det.matcher.detect(img, t=f / 6.0) or [])
            dets += det._extras(img, dets)
        used = set()
        for (idx, u, v), fr in zip(placed, vis):
            near = [(math.hypot(d.u - u, d.v - v), i) for i, d in enumerate(dets) if i not in used]
            near = [z for z in near if z[0] < 0.03]
            if fr >= 0.5:
                n_vis += 1
            if near:
                _dd, i = min(near)
                used.add(i)
                if fr >= 0.5:
                    tp += 1
                id_ok += dets[i].alias == pick[idx]
        fp += len([i for i in range(len(dets)) if i not in used and
                   min(math.hypot(dets[i].u - u, dets[i].v - v) for _x, u, v in placed) < 0.08])
    return {"stacks": n, "recall_visible": round(tp / max(1, n_vis), 3), "identity_ok": id_ok,
            "fp_near_stack": fp}


def micro_tracker(n: int = 40, seed: int = 0) -> dict:
    from treeaicoach.detector import Detection
    from treeaicoach.identifier import Identified
    from treeaicoach.tracker import Tracker

    rng = np.random.default_rng(seed)
    sw = lag_n = 0
    lags = []
    frames = 0
    for _ in range(n):
        trk = Tracker()
        fps = float(rng.choice([3.0, 6.0, 12.0]))
        k = int(rng.integers(2, 5))
        names = [f"C{i}" for i in range(k)]
        side = ["enemy" if rng.random() < 0.5 else "ally" for _ in names]
        c = rng.uniform(0.3, 0.7, 2)
        start = [c + rng.normal(0, 0.12, 2) for _ in names]
        end = [2 * c - s for s in start]                     # all cross the centre
        T = 8.0
        for f in range(int(T * fps)):
            t = f / fps
            ids = []
            truth = {}
            for i, a in enumerate(names):
                p = start[i] + (end[i] - start[i]) * min(1.0, t / T)
                truth[a] = p
                if rng.random() < 0.15:
                    continue                                  # missed
                alias = a if rng.random() > 0.15 else (None if rng.random() < 0.7 else str(rng.choice(names)))
                if alias is not None and alias != a and side[names.index(alias)] != side[i]:
                    alias = None
                pn = p + rng.normal(0, 0.002, 2)
                d = Detection(u=float(pn[0]), v=float(pn[1]), r=0.045, score=0.8, cls=side[i],
                              cls_probs=(0.9, 0.1, 0.0) if side[i] == "enemy" else (0.1, 0.9, 0.0), alias=alias)
                ids.append(Identified(det=d, alias=alias, relation=side[i], team=None, id_score=0.8))
            trk.update(t, ids)
            frames += 1
            for a, p in truth.items():
                tr = trk.get(a)
                if tr is None or not tr.visible or tr.stacked_with is not None:
                    continue                      # (stacked holds are not drawn)
                q = tr.position()
                if q is None:
                    continue
                if math.hypot(q[0] - p[0], q[1] - p[1]) > 0.03:
                    others = [b for b, pb in truth.items() if b != a and math.hypot(q[0] - pb[0], q[1] - pb[1]) < 0.02]
                    sw += bool(others)
                else:
                    v = (end[names.index(a)] - start[names.index(a)]) / T / fps
                    sp = float(np.hypot(*v))
                    if sp > 1e-4:
                        lags.append(float(((p - np.asarray(q)) @ v) / sp / sp))
                        lag_n += 1
    return {"runs": n, "frames": frames, "wrong_place_frames": sw,
            "lag_frames_mean": round(float(np.mean(lags)), 3) if lags else None}


GYMS = {"ring": micro_ring, "identity": micro_identity, "stacks": micro_stacks, "tracker": micro_tracker}


def main() -> None:
    import logging

    ap = argparse.ArgumentParser()
    ap.add_argument("which", nargs="*", default=["all"])
    ap.add_argument("--n", type=int)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    logging.disable(logging.WARNING)
    which = list(GYMS) if "all" in a.which else a.which
    for w in which:
        t0 = time.perf_counter()
        kw = {"seed": a.seed}
        if a.n:
            kw["n"] = a.n
        res = GYMS[w](**kw)
        print(f"{w:9s} {res}  ({time.perf_counter() - t0:.1f} s)", flush=True)


if __name__ == "__main__":
    main()
