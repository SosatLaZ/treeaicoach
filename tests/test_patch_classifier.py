"""Tests of treeaicoach/patch_classifier.py and the torch-free parts of the fast recipe."""

from __future__ import annotations

import math
import time

import cv2
import numpy as np
import pytest

from treeaicoach import patch_classifier as PC


def _scene(rng: np.random.Generator, n: int = 4, size: int = 200):
    img = np.full((size, size, 3), (40, 60, 50), np.uint8)
    img += rng.integers(0, 25, img.shape, dtype=np.uint8)
    icons = []
    for k in range(n):
        u, v = 0.15 + 0.2 * k, 0.3 + 0.4 * (k % 2)
        r = 0.045
        team = "enemy" if k % 2 else "ally"
        col = (60, 60, 200) if team == "enemy" else (200, 150, 90)
        c = (int(u * size), int(v * size))
        cv2.circle(img, c, int(r * size), (90, 120, 160), -1)
        cv2.circle(img, c, int(r * size), col, 2)
        icons.append((u, v, r, team))
    return img, icons


def _dataset(seed: int = 0, frames: int = 20):
    rng = np.random.default_rng(seed)
    X, y = [], []
    for _ in range(frames):
        img, icons = _scene(rng)
        cands = [(u, v, r) for u, v, r, _ in icons]
        ys = [1 if t == "enemy" else 2 for *_, t in icons]
        for _ in range(8):
            cands.append((float(rng.uniform(0.05, 0.95)), 0.05, 0.045))
            ys.append(0)
        X.append(PC.patch_features(PC.extract_patches(img, cands)))
        y += ys
    return np.concatenate(X), np.asarray(y)


def test_features_shape_and_speed():
    img, icons = _scene(np.random.default_rng(1))
    cands = [(u, v, r) for u, v, r, _ in icons] * 5
    P = PC.extract_patches(img, cands)
    assert P.shape == (20, PC.PATCH, PC.PATCH, 3)
    F = PC.patch_features(P)
    assert F.shape == (20, PC.feature_dim()) and np.isfinite(F).all()
    assert PC.patch_features(P[:0]).shape == (0, PC.feature_dim())
    t0 = time.perf_counter()
    for _ in range(10):
        PC.patch_features(PC.extract_patches(img, cands))
    assert (time.perf_counter() - t0) / 10 < 0.05      # ~1-2 ms; generous for CI


def test_fit_predict_save_load(tmp_path):
    X, y = _dataset()
    clf = PC.fit(X, y, epochs=30)
    assert (clf.predict_features(X).argmax(1) == y).mean() > 0.95
    img, icons = _scene(np.random.default_rng(7))
    p = clf.verify_candidates(img, [(u, v, r) for u, v, r, _ in icons] + [(0.5, 0.05, 0.045)])
    assert p.shape == (5, 3) and np.allclose(p.sum(1), 1, atol=1e-4)
    assert (p[:4, 0] < 0.5).all() and p[4, 0] > 0.5
    path = tmp_path / "pc.npz"
    clf.save(path)
    clf2 = PC.PatchVerifier.load(path)
    assert clf2 is not None
    assert np.allclose(clf2.verify_candidates(img, [(0.15, 0.3, 0.045)]), clf.verify_candidates(img, [(0.15, 0.3, 0.045)]))


def test_load_missing_and_bad_input(tmp_path):
    assert PC.PatchVerifier.load(tmp_path / "nope.npz") is None
    (tmp_path / "bad.npz").write_bytes(b"garbage")
    assert PC.PatchVerifier.load(tmp_path / "bad.npz") is None
    X, y = _dataset(frames=4)
    clf = PC.fit(X, y, epochs=2)
    assert clf.verify_candidates(np.zeros((50, 50, 3), np.uint8), []).shape == (0, 3)
    out = clf.verify_candidates(None, [(0.5, 0.5, 0.05)])         # never raises
    assert out.shape == (1, 3)


def test_bundled_model_loads_if_present():
    clf = PC.PatchVerifier.load()
    if clf is None:
        pytest.skip("no bundled patch classifier")
    img, icons = _scene(np.random.default_rng(3))
    assert clf.verify_candidates(img, [(u, v, r) for u, v, r, _ in icons]).shape == (4, 3)


def test_partial_fit_and_online_learner(tmp_path):
    X, y = _dataset(frames=6)
    clf = PC.fit(X, y, epochs=1)
    before = (clf.predict_features(X).argmax(1) == y).mean()
    clf.partial_fit(X, y, epochs=5)
    after = (clf.predict_features(X).argmax(1) == y).mean()
    assert after >= before
    learner = PC.OnlineIconLearner(clf, tmp_path / "user.npz", fit_every=8)
    rng = np.random.default_rng(5)
    for _ in range(4):
        img, icons = _scene(rng)
        learner.add_frame(img, icons)
    for _ in range(200):
        if (tmp_path / "user.npz").is_file() and not learner._busy.is_set():
            break
        time.sleep(0.02)
    assert learner.updates >= 1 and PC.PatchVerifier.load(tmp_path / "user.npz") is not None
    learner.add_frame(None, [(0.5, 0.5, 0.04, "ally")])   # never raises


def test_fast_recipe_crop_and_augment_keep_labels_consistent():
    pytest.importorskip("training.fast_train")
    from training.fast_train import augment, random_crop

    rng = np.random.default_rng(0)
    img = np.zeros((160, 160, 3), np.uint8)
    labels = [{"u": 0.3, "v": 0.6, "r": 0.045, "cls": "ally", "cls_valid": True}]
    cv2.circle(img, (48, 96), 7, (255, 255, 255), -1)
    for _ in range(30):
        a, la = augment(img, labels, rng)
        c, lc = random_crop(a, la, 96, rng)
        assert c.shape == (96, 96, 3)
        for l in lc:
            x, y = int(l["u"] * 96), int(l["v"] * 96)
            assert c[y, x].max() > 120                        # label still on the disc
            assert math.isclose(l["r"] * 96, 0.045 * 160, rel_tol=1e-6)
