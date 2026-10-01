"""Train the patch classifier (treeaicoach/patch_classifier.py) in seconds.

Samples come from the fast recipe's rendered cache (``fast_train.build_cache``: synthetic
minimaps + real-art composites, rendered once in parallel): positives at every labelled icon
(centre jitter <= 0.15 r, radius x 0.9-1.1), negatives at random points, at saturated glyphs
(towers, camps, markers: the hard negatives) and next to icons (0.6-1.3 r off-centre: teaches
the classifier to reject badly centred candidates).

    python training/patch_train.py --out treeaicoach/assets/model/patch_classifier.npz

``--eval-onnx MODEL`` measures it as a verifier of an ONNX detector (detections at a low
threshold re-scored by the classifier) on the synthetic / real / real-art evaluation sets.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np

os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from treeaicoach import patch_classifier as PC  # noqa: E402

log = logging.getLogger("training.patch_train")


def sample_candidates(img: np.ndarray, labels: list[dict], rng: np.random.Generator,
                      n_rand: int = 6, n_hard: int = 4, n_near: int = 2
                      ) -> tuple[list[tuple[float, float, float]], list[int]]:
    cands: list[tuple[float, float, float]] = []
    ys: list[int] = []
    rs = [l["r"] for l in labels]
    r0 = float(np.median(rs)) if rs else float(rng.uniform(0.04, 0.05))
    for l in labels:
        if l.get("vis", 1.0) < 0.55:
            continue
        j = rng.normal(0, 0.07, 2) * l["r"]
        cands.append((l["u"] + j[0], l["v"] + j[1], l["r"] * rng.uniform(0.9, 1.1)))
        ys.append(1 if l["cls"] == "enemy" else 2)

    def far(u: float, v: float, k: float = 0.6) -> bool:
        return all(math.hypot(u - l["u"], v - l["v"]) > k * l["r"] for l in labels)

    for _ in range(n_rand):
        u, v = rng.uniform(0.02, 0.98, 2)
        if far(u, v):
            cands.append((u, v, r0 * rng.uniform(0.85, 1.15)))
            ys.append(0)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    yy, xx = np.nonzero((hsv[:, :, 1] > 80) & (hsv[:, :, 2] > 80))
    if len(xx):
        s = img.shape[0]
        for k in rng.integers(0, len(xx), n_hard):
            u, v = (xx[k] + 0.5) / s, (yy[k] + 0.5) / s
            if far(u, v):
                cands.append((u, v, r0 * rng.uniform(0.85, 1.15)))
                ys.append(0)
    for _ in range(n_near):
        if not labels:
            break
        l = labels[int(rng.integers(len(labels)))]
        a = rng.uniform(0, 2 * math.pi)
        d = rng.uniform(0.6, 1.3) * l["r"]
        u, v = l["u"] + d * math.cos(a), l["v"] + d * math.sin(a)
        if far(u, v):
            cands.append((u, v, l["r"]))
            ys.append(0)
    return cands, ys


def build_xy(imgs: np.ndarray, labels: list[list[dict]], seed: int = 0, limit: int = 0
             ) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    X, y = [], []
    n = len(imgs) if not limit else min(limit, len(imgs))
    for i in range(n):
        c, ys = sample_candidates(imgs[i], labels[i], rng)
        if c:
            X.append(PC.patch_features(PC.extract_patches(imgs[i], c)))
            y.extend(ys)
    return np.concatenate(X), np.asarray(y, np.int64)


def verifier_eval(model_path: Path, clf: PC.PatchClassifier, holdout: bool = True,
                  mode: str = "mix") -> dict[str, Any]:
    """ONNX detections (thr 0.05) re-scored by the classifier; P/R/F1 sweeps per set."""
    from training.dataset import build_validation_set
    from training.evaluate import MetricAccumulator, gts_from_labels, load_runtime_detector
    from training.fast_train import real_art_eval_set, real_eval_set

    det, _ = load_runtime_detector(model_path, None, 0.05, 2)
    vs = build_validation_set(200)
    sets = {"synthetic": (list(vs.images), [gts_from_labels(l) for l in vs.labels]),
            "real": real_eval_set(holdout)[:2], "real_art": real_art_eval_set()}
    out = {}
    for name, (imgs, gts) in sets.items():
        a_cnn, a_ver = MetricAccumulator(), MetricAccumulator()
        ms = []
        for img, g in zip(imgs, gts):
            dets = det.detect(img)
            a_cnn.add(dets, g)
            t0 = time.perf_counter()
            p = clf.predict(img, [(d.u, d.v, d.r) for d in dets])
            ms.append((time.perf_counter() - t0) * 1000)
            new = []
            for d, pr in zip(dets, p):
                s = float(math.sqrt(d.score * (1 - pr[0]))) if mode == "mix" else float(1 - pr[0])
                new.append(dataclasses.replace(d, score=s))
            a_ver.add(new, g)
        bc, bv = a_cnn.best(), a_ver.best()
        out[name] = {"cnn": {k: bc[k] for k in ("threshold", "precision", "recall", "f1")},
                     "verified": {k: bv[k] for k in ("threshold", "precision", "recall", "f1")},
                     "verify_ms": float(np.mean(ms))}
        log.info("%-9s CNN best F1 %.3f (P %.3f R %.3f) | +verifier %.3f (P %.3f R %.3f) | %.2f ms",
                 name, bc["f1"], bc["precision"], bc["recall"], bv["f1"], bv["precision"],
                 bv["recall"], out[name]["verify_ms"])
    return out


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    p = argparse.ArgumentParser(description="Train the patch classifier in seconds.")
    p.add_argument("--n-synth", type=int, default=3000)
    p.add_argument("--n-real", type=int, default=3000)
    p.add_argument("--size", type=int, default=160)
    p.add_argument("--holdout", action="store_true")
    p.add_argument("--limit", type=int, default=0, help="use only the first N cached images")
    p.add_argument("--max-fit", type=int, default=40000, help="samples used by the fit")
    p.add_argument("--iters", type=int, default=200)
    p.add_argument("--out", default="")
    p.add_argument("--eval-onnx", default="")
    p.add_argument("--mode", default="mix", choices=("mix", "replace"))
    a = p.parse_args(argv)
    from training import real_art
    from training.fast_train import HOLDOUT_SHOTS, build_cache

    bg = [s for s in real_art.BACKGROUND_SHOTS if not (a.holdout and s in HOLDOUT_SHOTS)]
    imgs, labels, _ = build_cache(a.n_synth, a.n_real, a.size, 0, 0, bg)
    # use both halves (synthetic first, real-art second)
    order = np.random.default_rng(1).permutation(len(imgs))
    if a.limit:
        order = order[: a.limit]
    t0 = time.perf_counter()
    X, y = build_xy(imgs[order], [labels[i] for i in order])
    t1 = time.perf_counter()
    if a.max_fit and len(y) > a.max_fit:     # the fit is memory-bound: a subsample is enough
        keep = np.random.default_rng(2).choice(len(y), a.max_fit, replace=False)
        Xf, yf = X[keep], y[keep]
    else:
        Xf, yf = X, y
    clf = PC.fit(Xf, yf, class_weight=[1.0, 1.5, 1.0], iters=a.iters)
    t2 = time.perf_counter()
    acc = float((clf.predict_features(X).argmax(1) == y).mean())
    log.info("features: %d samples (%d pos) in %.1f s | fit %.1f s | train acc %.3f",
             len(y), int((y > 0).sum()), t1 - t0, t2 - t1, acc)
    clf.meta.update({"features_s": t1 - t0, "fit_s": t2 - t1, "train_acc": acc})
    if a.out:
        clf.save(a.out)
        log.info("saved %s (%d bytes)", a.out, Path(a.out).stat().st_size)
    if a.eval_onnx:
        rep = verifier_eval(Path(a.eval_onnx), clf, a.holdout, a.mode)
        print(json.dumps(rep, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
