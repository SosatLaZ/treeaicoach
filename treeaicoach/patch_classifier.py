"""Champion-icon candidate VERIFIER: icon (enemy / ally) vs distractor, in numpy.

Given candidate circles ``(u, v, r)`` on a minimap (normalized like ``Detection``: centre
and radius / minimap side), :meth:`PatchVerifier.verify` returns, per candidate, the
probabilities ``(background, enemy, ally)``. It re-scores the candidates of a cheap,
recall-oriented stage (ring-colour candidates, Hough circles, the ONNX CNN at a low
threshold) and rejects the 2026 distractors: plate-number tower glyphs, hourglass camps,
gold diamond / green square markers, pings, camera-rectangle lines, our own overlay rings
and labels.

Usage::

    from treeaicoach.patch_classifier import PatchVerifier, extract_patches
    ver = PatchVerifier.load()                    # bundled weights, None if missing
    patches = extract_patches(minimap_bgr, [(u, v, r), ...])   # [N, 24, 24, 3] uint8
    probs = ver.verify(patches)                   # [N, 3] float32 (background, enemy, ally)
    p_icon = 1 - probs[:, 0]

Patches: a 24 x 24 grid spanning ``SPAN`` (3) radii around the centre, sampled from the
minimap with ONE ``cv2.remap`` call for all candidates (from a ``pyrDown`` level of it for big
minimaps, so the grid never aliases). Features (inside :meth:`verify`): the patch
average-pooled to 12 x 12 (colours and layout) + a polar ring profile (per radius bin: mean
B, G, R, brightness spread over the angles, chroma), standardized. Model: a 2-layer MLP
(``F -> HIDDEN -> 3``, ReLU, softmax). Weights file ``assets/model/patch_verifier.npz``:

* ``W1 [F, H]``, ``b1 [H]``, ``W2 [H, 3]``, ``b2 [3]`` float32,
* ``mu [F]``, ``sd [F]`` feature standardization (``F = feature_dim()`` = 492),
* ``meta_keys`` / ``meta_vals`` (training stats; informative only).

Training (seconds, numpy only): :func:`fit` on samples cut from rendered minimaps
(``training/patch_train.py``). On-device adaptation: :meth:`PatchVerifier.partial_fit`
(minibatch Adam from the current weights, standardization kept) driven by
:class:`OnlineIconLearner` with the user's own confident icons (e.g. roster matches with a
high identity score) as positives and random points away from them as negatives, in a
background thread, saved to the user data folder.

``verify`` / ``extract_patches`` never raise (neutral output on bad input).
"""

from __future__ import annotations

import logging
import math
import os
import threading
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

log = logging.getLogger(__name__)

PATCH = 24                 # patch grid (px)
SPAN = 3.0                 # patch side / icon radius
POOL = 12                  # pooled raw input side
HIDDEN = 32
CLASSES = ("background", "enemy", "ally")
MODEL_FILE = "patch_verifier.npz"
#: Largest icon radius (px) sampled at full resolution; bigger -> pyrDown levels.
MAX_R_PX = 16.0
_POLAR_R, _POLAR_A = 12, 16
_POLAR_MAX = 1.45          # polar profile up to 1.45 radii (ring + outside)


def default_model_path() -> Path:
    try:
        from treeaicoach.paths import asset_path

        return asset_path("model", MODEL_FILE)
    except Exception:
        return Path(__file__).resolve().parent / "assets" / "model" / MODEL_FILE


def feature_dim() -> int:
    return POOL * POOL * 3 + _POLAR_R * 5


# ======================================================================================
# Patch extraction (one cv2.remap for all candidates)
# ======================================================================================

_GRID = ((np.arange(PATCH, dtype=np.float32) + 0.5) / PATCH - 0.5) * SPAN   # in radii


def extract_patches(img_bgr: np.ndarray, cands: Sequence[Sequence[float]]) -> np.ndarray:
    """``[N, PATCH, PATCH, 3]`` uint8 patches (side ``SPAN * r``) around ``(u, v, r)``."""
    n = len(cands) if cands is not None else 0
    out = np.zeros((n, PATCH, PATCH, 3), np.uint8)
    try:
        if n == 0 or img_bgr is None or img_bgr.ndim != 3 or img_bgr.shape[2] < 3:
            return out
        img = img_bgr[:, :, :3]
        h, w = img.shape[:2]
        c = np.asarray(cands, np.float32).reshape(n, -1)[:, :3]
        side = float(min(h, w))
        rp = np.maximum(c[:, 2] * side, 1.0)
        # one pyramid level for the whole frame (icons share one scale on a minimap)
        level = 0
        r_med = float(np.median(rp))
        while r_med / (2 ** level) > MAX_R_PX and level < 3 and min(h, w) >> (level + 1) >= 16:
            level += 1
        k = 1.0
        for _ in range(level):
            img = cv2.pyrDown(img)
            k *= 0.5
        sx = img.shape[1] / float(w)
        sy = img.shape[0] / float(h)
        cx = (c[:, 0] * w * sx)[:, None, None]
        cy = (c[:, 1] * h * sy)[:, None, None]
        rr = (rp * k)[:, None, None]
        # one cv2.remap for all candidates: maps stacked as an [N*PATCH, PATCH] image
        mx = (cx + _GRID[None, None, :] * rr - 0.5) + np.zeros((1, PATCH, 1), np.float32)
        my = (cy + _GRID[None, :, None] * rr - 0.5) + np.zeros((1, 1, PATCH), np.float32)
        res = cv2.remap(np.ascontiguousarray(img), mx.reshape(n * PATCH, PATCH).astype(np.float32),
                        my.reshape(n * PATCH, PATCH).astype(np.float32), cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_REPLICATE)
        out[:] = res.reshape(n, PATCH, PATCH, 3)
    except Exception:
        log.debug("extract_patches failed", exc_info=True)
    return out


def _polar_maps() -> tuple[np.ndarray, np.ndarray]:
    """Fixed remap coordinates ``[A, R]`` of the polar profile inside a patch."""
    c = PATCH / 2.0
    r_px = PATCH / SPAN                                     # icon radius in patch px
    radii = (np.arange(_POLAR_R, dtype=np.float32) + 0.5) / _POLAR_R * _POLAR_MAX * r_px
    ang = np.arange(_POLAR_A, dtype=np.float32) * np.float32(2 * math.pi / _POLAR_A)
    xs = c + radii[None, :] * np.cos(ang)[:, None] - 0.5
    ys = c + radii[None, :] * np.sin(ang)[:, None] - 0.5
    return xs.astype(np.float32), ys.astype(np.float32)


_PX, _PY = _polar_maps()
_MAP_CACHE: dict[int, tuple[np.ndarray, np.ndarray]] = {}


def _stacked_polar_maps(n: int) -> tuple[np.ndarray, np.ndarray]:
    m = _MAP_CACHE.get(n)
    if m is None:
        off = (np.arange(n, dtype=np.float32) * PATCH)[:, None, None]
        mx = np.broadcast_to(_PX[None], (n,) + _PX.shape).reshape(n * _POLAR_A, _POLAR_R)
        my = (_PY[None] + off).reshape(n * _POLAR_A, _POLAR_R)
        m = (np.ascontiguousarray(mx, np.float32), np.ascontiguousarray(my, np.float32))
        if len(_MAP_CACHE) < 64:
            _MAP_CACHE[n] = m
    return m


def patch_features(patches: np.ndarray) -> np.ndarray:
    """``[N, F]`` float32 features of ``[N, PATCH, PATCH, 3]`` uint8 patches."""
    n = len(patches)
    if n == 0:
        return np.zeros((0, feature_dim()), np.float32)
    pu8 = np.ascontiguousarray(patches, np.uint8)
    stacked = pu8.reshape(n * PATCH, PATCH, 3)
    pooled = cv2.resize(stacked, (POOL, n * POOL), interpolation=cv2.INTER_AREA)
    pooled = pooled.reshape(n, -1).astype(np.float32) * np.float32(1.0 / 255.0)
    mx, my = _stacked_polar_maps(n)
    pol = cv2.remap(stacked, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    pol = pol.reshape(n, _POLAR_A, _POLAR_R, 3).astype(np.float32) * np.float32(1.0 / 255.0)
    b, g, r = pol[..., 0], pol[..., 1], pol[..., 2]
    inv = np.float32(1.0 / _POLAR_A)
    mean = np.stack([b.sum(1), g.sum(1), r.sum(1)], axis=2) * inv           # [N, R, 3]
    br = (b + g + r) * np.float32(1.0 / 3.0)
    bm = br.sum(1) * inv
    spread = np.sqrt(np.maximum((br * br).sum(1) * inv - bm * bm, 0.0))
    chroma = (np.maximum(np.maximum(b, g), r) - np.minimum(np.minimum(b, g), r)).sum(1) * inv
    f_pol = np.concatenate([mean, spread[..., None], chroma[..., None]], axis=2).reshape(n, -1)
    return np.concatenate([pooled, f_pol], axis=1).astype(np.float32, copy=False)


def _softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


# ======================================================================================
# Model
# ======================================================================================


class PatchVerifier:
    """2-layer MLP on :func:`patch_features` (standardized)."""

    def __init__(self, W1: np.ndarray, b1: np.ndarray, W2: np.ndarray, b2: np.ndarray,
                 mu: np.ndarray, sd: np.ndarray, meta: dict | None = None) -> None:
        self.W1 = np.asarray(W1, np.float32)
        self.b1 = np.asarray(b1, np.float32)
        self.W2 = np.asarray(W2, np.float32)
        self.b2 = np.asarray(b2, np.float32)
        self.mu = np.asarray(mu, np.float32)
        self.sd = np.asarray(sd, np.float32)
        self.meta = dict(meta or {})
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ io
    @classmethod
    def load(cls, path: Path | str | None = None) -> "PatchVerifier | None":
        """Load weights (default: the bundled model). None when missing/invalid."""
        p = Path(path) if path is not None else default_model_path()
        try:
            with np.load(p, allow_pickle=False) as d:
                meta = {}
                if "meta_keys" in d:
                    meta = dict(zip([str(k) for k in d["meta_keys"]],
                                    [float(v) for v in d["meta_vals"]]))
                m = cls(d["W1"], d["b1"], d["W2"], d["b2"], d["mu"], d["sd"], meta)
            f = feature_dim()
            if m.W1.shape[0] != f or m.mu.shape != (f,) or m.W2.shape != (m.W1.shape[1], 3):
                raise ValueError(f"bad shapes {m.W1.shape} {m.W2.shape}")
            return m
        except Exception as exc:
            log.debug("Patch verifier unavailable (%s): %s", p, exc)
            return None

    def save(self, path: Path | str) -> None:
        """Atomic save (``.npz``)."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.stem + ".tmp.npz")
        keys = sorted(self.meta)
        with self._lock:
            np.savez(tmp, W1=self.W1, b1=self.b1, W2=self.W2, b2=self.b2, mu=self.mu, sd=self.sd,
                     meta_keys=np.asarray(keys, dtype="U32"),
                     meta_vals=np.asarray([float(self.meta[k]) for k in keys], np.float64))
        os.replace(tmp, p)

    # ------------------------------------------------------------------ inference
    def predict_features(self, X: np.ndarray) -> np.ndarray:
        with self._lock:
            h = np.maximum(((X - self.mu) / self.sd) @ self.W1 + self.b1, 0.0)
            return _softmax(h @ self.W2 + self.b2)

    def verify(self, patches: np.ndarray) -> np.ndarray:
        """``[N, 3]`` probabilities (background, enemy, ally) of ``extract_patches`` output.
        Never raises (uniform probabilities on error)."""
        n = 0
        try:
            n = len(patches) if patches is not None else 0
            if n == 0:
                return np.zeros((0, 3), np.float32)
            return self.predict_features(patch_features(patches)).astype(np.float32)
        except Exception:
            log.debug("Patch verification failed", exc_info=True)
            return np.full((n, 3), 1.0 / 3.0, np.float32)

    def verify_candidates(self, img_bgr: np.ndarray, cands: Sequence[Sequence[float]]
                          ) -> np.ndarray:
        """``verify(extract_patches(img_bgr, cands))``."""
        return self.verify(extract_patches(img_bgr, cands))

    def icon_prob(self, img_bgr: np.ndarray, cands: Sequence[Sequence[float]]) -> np.ndarray:
        """``[N]`` probability that each candidate is a champion icon."""
        p = self.verify_candidates(img_bgr, cands)
        return (1.0 - p[:, 0]) if len(p) else np.zeros(0, np.float32)

    # ------------------------------------------------------------------ learning
    def partial_fit(self, X: np.ndarray, y: np.ndarray, lr: float = 1e-3, epochs: int = 3,
                    batch: int = 64, l2: float = 1e-4, seed: int = 0) -> None:
        """Incremental minibatch Adam from the current weights (``y`` in 0..2)."""
        X = np.asarray(X, np.float32)
        y = np.asarray(y, np.int64)
        if not len(X):
            return
        with self._lock:
            params = [self.W1.copy(), self.b1.copy(), self.W2.copy(), self.b2.copy()]
            mu, sd = self.mu, self.sd
        params = _train_mlp(params, (X - mu) / sd, y, lr, epochs, batch, l2, None, seed)
        with self._lock:
            self.W1, self.b1, self.W2, self.b2 = params


def _train_mlp(params: list[np.ndarray], Xs: np.ndarray, y: np.ndarray, lr: float, epochs: int,
               batch: int, l2: float, class_weight: Sequence[float] | None, seed: int
               ) -> list[np.ndarray]:
    """Minibatch Adam on a 2-layer MLP (weighted cross-entropy), cosine lr."""
    rng = np.random.default_rng(seed)
    W1, b1, W2, b2 = (np.array(p, np.float32, copy=True) for p in params)
    k = W2.shape[1]
    cw = np.asarray(class_weight if class_weight is not None else [1.0] * k, np.float32)
    ps = [W1, b1, W2, b2]
    m = [np.zeros_like(p) for p in ps]
    v = [np.zeros_like(p) for p in ps]
    n = len(Xs)
    steps = max(1, epochs * math.ceil(n / batch))
    t = 0
    for _ in range(max(1, epochs)):
        order = rng.permutation(n)
        for s in range(0, n, batch):
            j = order[s:s + batch]
            xb, yb = Xs[j], y[j]
            hpre = xb @ W1 + b1
            h = np.maximum(hpre, 0.0)
            G = _softmax(h @ W2 + b2)
            G[np.arange(len(j)), yb] -= 1.0
            wt = cw[yb][:, None]
            G *= wt / wt.sum()
            gh = (G @ W2.T) * (hpre > 0)
            grads = [xb.T @ gh + l2 * W1, gh.sum(0), h.T @ G + l2 * W2, G.sum(0)]
            t += 1
            a = lr * 0.5 * (1 + math.cos(math.pi * min(1.0, t / steps)))
            a *= math.sqrt(1 - 0.999 ** t) / (1 - 0.9 ** t)
            for i, (p, g) in enumerate(zip(ps, grads)):
                m[i] = 0.9 * m[i] + 0.1 * g
                v[i] = 0.999 * v[i] + 0.001 * g * g
                p -= (a * m[i] / (np.sqrt(v[i]) + 1e-8)).astype(np.float32)
    return ps


def fit(X: np.ndarray, y: np.ndarray, hidden: int = HIDDEN, epochs: int = 8, lr: float = 3e-3,
        batch: int = 256, l2: float = 1e-4, class_weight: Sequence[float] | None = None,
        seed: int = 0) -> PatchVerifier:
    """Train a fresh :class:`PatchVerifier` (numpy, seconds for ~100k samples)."""
    X = np.asarray(X, np.float32)
    y = np.asarray(y, np.int64)
    mu = X.mean(axis=0)
    sd = X.std(axis=0) + 1e-3
    rng = np.random.default_rng(seed)
    f = X.shape[1]
    params = [(rng.standard_normal((f, hidden)) * math.sqrt(2.0 / f)).astype(np.float32),
              np.zeros(hidden, np.float32),
              (rng.standard_normal((hidden, 3)) * math.sqrt(1.0 / hidden)).astype(np.float32),
              np.zeros(3, np.float32)]
    params = _train_mlp(params, (X - mu) / sd, y, lr, epochs, batch, l2, class_weight, seed)
    return PatchVerifier(*params, mu, sd, {"n_train": float(len(X)), "hidden": float(hidden)})


# ======================================================================================
# On-device learning
# ======================================================================================


class OnlineIconLearner:
    """Adapts a :class:`PatchVerifier` to one user's client from their own games.

    ``add_frame(img, icons)`` with confident icons ``(u, v, r, team)`` (e.g. roster matches
    with a high identity score): their patches are positives (class from the team), random
    points away from them are negatives. Every ``fit_every`` new positives,
    :meth:`PatchVerifier.partial_fit` runs in a background thread on the buffer, then the
    model is saved to ``save_path`` (e.g. ``<user data>/model/patch_verifier_user.npz``;
    load it with ``PatchVerifier.load(path)`` at the next start, falling back to the
    bundled one). One ``add_frame`` costs ~0.2 ms; never raises.
    """

    def __init__(self, model: PatchVerifier, save_path: Path | str | None = None,
                 fit_every: int = 200, neg_per_frame: int = 4, max_buffer: int = 4000,
                 seed: int = 0) -> None:
        self.model = model
        self.save_path = Path(save_path) if save_path else None
        self.fit_every = int(fit_every)
        self.neg_per_frame = int(neg_per_frame)
        self.max_buffer = int(max_buffer)
        self._rng = np.random.default_rng(seed)
        self._X: list[np.ndarray] = []
        self._y: list[int] = []
        self._new_pos = 0
        self._busy = threading.Event()
        self.updates = 0

    def add_frame(self, img_bgr: np.ndarray, icons: Sequence[tuple[float, float, float, str]]
                  ) -> None:
        try:
            if img_bgr is None or not icons:
                return
            cands = [(float(u), float(v), float(r)) for u, v, r, _ in icons]
            ys = [1 if t == "enemy" else 2 for *_, t in icons]
            r0 = float(np.median([c[2] for c in cands]))
            negs = 0
            for _ in range(self.neg_per_frame * 3):
                if negs >= self.neg_per_frame:
                    break
                u, v = self._rng.uniform(0.03, 0.97, 2)
                if all(math.hypot(u - a, v - b) > 1.6 * max(r0, rr) for a, b, rr, _ in icons):
                    cands.append((float(u), float(v), r0))
                    ys.append(0)
                    negs += 1
            X = patch_features(extract_patches(img_bgr, cands))
            self._X.extend(X)
            self._y.extend(ys)
            self._new_pos += len(icons)
            if len(self._X) > self.max_buffer:
                del self._X[: len(self._X) - self.max_buffer]
                del self._y[: len(self._y) - self.max_buffer]
            if self._new_pos >= self.fit_every and not self._busy.is_set():
                self._new_pos = 0
                self._busy.set()
                threading.Thread(target=self._fit, args=(np.asarray(self._X), np.asarray(self._y)),
                                 daemon=True, name="patch-learner").start()
        except Exception:
            log.debug("OnlineIconLearner.add_frame failed", exc_info=True)

    def _fit(self, X: np.ndarray, y: np.ndarray) -> None:
        try:
            self.model.partial_fit(X, y, seed=self.updates)
            self.updates += 1
            if self.save_path is not None:
                self.model.save(self.save_path)
        except Exception:
            log.debug("OnlineIconLearner fit failed", exc_info=True)
        finally:
            self._busy.clear()


#: Alias (earlier name of the class).
PatchClassifier = PatchVerifier

__all__ = ["PatchVerifier", "PatchClassifier", "OnlineIconLearner", "extract_patches",
           "patch_features", "fit", "feature_dim", "default_model_path", "CLASSES", "PATCH"]
