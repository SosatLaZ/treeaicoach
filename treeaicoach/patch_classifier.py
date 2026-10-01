"""Tiny champion-icon patch classifier (numpy + OpenCV, trained in seconds).

Given candidate circles ``(u, v, r)`` on a minimap (normalized like :class:`Detection`),
it returns, for each one, the probabilities ``(background, enemy, ally)``. It is meant to
**verify / re-score** the candidates of a cheap or recall-oriented detector (the ONNX CNN at
a low threshold, the classic Hough detector, the roster matcher's ring candidates), not to
search the whole image.

Features per candidate (~0.02 ms each): the patch around the circle is resampled to 24 x 24
(side = 3 radii, sub-pixel), then

* HOG (cv2.HOGDescriptor, 6 px cells, 2 x 2-cell blocks, 9 bins) - shape of the ring,
* a polar ring profile (``cv2.warpPolar``): per radius bin, mean B, G, R and the angular
  spread of the brightness - ring colour, thickness and uniformity.

Model: multinomial logistic regression on standardized features (weights in a ~10 KB
``.npz``). :meth:`PatchClassifier.partial_fit` updates it incrementally (SGD), which is what
:class:`OnlineIconLearner` uses to adapt to one user's client from their own games
(positives = icons the roster matcher identified with high confidence, negatives = random
points away from them), saved in the user data folder.

Never raises from :meth:`predict` (returns an empty / neutral array on bad input).
"""

from __future__ import annotations

import logging
import math
import os
import threading
from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np

log = logging.getLogger(__name__)

PATCH = 24
#: Patch side / icon radius (the patch covers the icon + a margin of 0.5 r).
SPAN = 3.0
CLASSES = ("background", "enemy", "ally")
MODEL_FILE = "patch_classifier.npz"
_POLAR_R, _POLAR_A = 12, 24

_HOG = None
_HOG_LOCK = threading.Lock()


def _hog() -> Any:
    global _HOG
    with _HOG_LOCK:
        if _HOG is None:
            _HOG = cv2.HOGDescriptor((PATCH, PATCH), (12, 12), (6, 6), (6, 6), 9)
        return _HOG


def default_model_path() -> Path:
    try:
        from treeaicoach.paths import asset_path

        return asset_path("model", MODEL_FILE)
    except Exception:
        return Path(__file__).resolve().parent / "assets" / "model" / MODEL_FILE


def extract_patches(img_bgr: np.ndarray, cands: Sequence[Sequence[float]]) -> np.ndarray:
    """``[N, PATCH, PATCH, 3]`` uint8 patches centred on the candidates (side ``SPAN * r``)."""
    h, w = img_bgr.shape[:2]
    side = float(min(h, w))
    out = np.empty((len(cands), PATCH, PATCH, 3), np.uint8)
    for i, c in enumerate(cands):
        u, v, r = float(c[0]), float(c[1]), float(c[2])
        cx, cy, rp = u * w, v * h, max(1.0, r * side)
        k = PATCH / (SPAN * rp)
        M = np.array([[k, 0.0, PATCH / 2 - k * cx], [0.0, k, PATCH / 2 - k * cy]], np.float32)
        out[i] = cv2.warpAffine(img_bgr, M, (PATCH, PATCH),
                                flags=cv2.INTER_AREA if k < 1 else cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_REPLICATE)
    return out


def patch_features(patches: np.ndarray) -> np.ndarray:
    """``[N, F]`` float32 features of ``[N, PATCH, PATCH, 3]`` uint8 patches."""
    hog = _hog()
    feats = []
    c = (PATCH / 2.0, PATCH / 2.0)
    max_r = PATCH / 2.0
    for p in patches:
        g = cv2.cvtColor(p, cv2.COLOR_BGR2GRAY)
        f_hog = hog.compute(g).reshape(-1)
        pol = cv2.warpPolar(p, (_POLAR_R, _POLAR_A), c, max_r,
                            cv2.WARP_POLAR_LINEAR | cv2.INTER_LINEAR).astype(np.float32) / 255.0
        # pol: [angle, radius, 3]
        mean = pol.mean(axis=0)                                  # [R, 3]
        bright = pol.mean(axis=2)                                # [A, R]
        spread = bright.std(axis=0)[:, None]                     # [R, 1]
        chroma = (pol.max(axis=2) - pol.min(axis=2)).mean(axis=0)[:, None]
        f_pol = np.concatenate([mean, spread, chroma], axis=1).reshape(-1)
        feats.append(np.concatenate([f_hog, f_pol]))
    if not feats:
        return np.zeros((0, feature_dim()), np.float32)
    return np.asarray(feats, np.float32)


def feature_dim() -> int:
    return 324 + _POLAR_R * 5


def _softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


class PatchClassifier:
    """Multinomial logistic regression on :func:`patch_features`."""

    def __init__(self, W: np.ndarray, b: np.ndarray, mu: np.ndarray, sd: np.ndarray,
                 meta: dict | None = None) -> None:
        self.W = np.asarray(W, np.float32)          # [F, 3]
        self.b = np.asarray(b, np.float32)          # [3]
        self.mu = np.asarray(mu, np.float32)        # [F]
        self.sd = np.asarray(sd, np.float32)        # [F]
        self.meta = dict(meta or {})
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ io
    @classmethod
    def load(cls, path: Path | str | None = None) -> "PatchClassifier | None":
        """Load weights (default: the bundled model). None when missing/invalid."""
        p = Path(path) if path is not None else default_model_path()
        try:
            with np.load(p, allow_pickle=False) as d:
                meta = {}
                if "meta_keys" in d:
                    meta = dict(zip([str(k) for k in d["meta_keys"]],
                                    [float(v) for v in d["meta_vals"]]))
                m = cls(d["W"], d["b"], d["mu"], d["sd"], meta)
            if m.W.shape != (feature_dim(), len(CLASSES)):
                raise ValueError(f"bad weight shape {m.W.shape}")
            return m
        except Exception as exc:
            log.debug("Patch classifier unavailable (%s): %s", p, exc)
            return None

    def save(self, path: Path | str) -> None:
        """Atomic save (``.npz``)."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.stem + ".tmp.npz")
        keys = sorted(self.meta)
        with self._lock:
            np.savez(tmp, W=self.W, b=self.b, mu=self.mu, sd=self.sd,
                     meta_keys=np.asarray(keys, dtype="U32"),
                     meta_vals=np.asarray([float(self.meta[k]) for k in keys], np.float64))
        os.replace(tmp, p)

    # ------------------------------------------------------------------ inference
    def predict_features(self, X: np.ndarray) -> np.ndarray:
        with self._lock:
            z = ((X - self.mu) / self.sd) @ self.W + self.b
        return _softmax(z)

    def predict(self, img_bgr: np.ndarray, cands: Sequence[Sequence[float]]) -> np.ndarray:
        """``[N, 3]`` probabilities (background, enemy, ally). Never raises."""
        try:
            if not len(cands):
                return np.zeros((0, 3), np.float32)
            X = patch_features(extract_patches(img_bgr, cands))
            return self.predict_features(X).astype(np.float32)
        except Exception:
            log.debug("Patch classification failed", exc_info=True)
            return np.tile(np.float32([1 / 3, 1 / 3, 1 / 3]), (len(cands), 1))

    def icon_prob(self, img_bgr: np.ndarray, cands: Sequence[Sequence[float]]) -> np.ndarray:
        """``[N]`` probability that each candidate is a champion icon."""
        p = self.predict(img_bgr, cands)
        return 1.0 - p[:, 0] if len(p) else np.zeros(0, np.float32)

    # ------------------------------------------------------------------ learning
    def partial_fit(self, X: np.ndarray, y: np.ndarray, lr: float = 0.02, epochs: int = 2,
                    l2: float = 1e-3, batch: int = 64, seed: int = 0) -> None:
        """Incremental SGD on new samples (``y`` in 0..2); standardization is kept."""
        X = np.asarray(X, np.float32)
        y = np.asarray(y, np.int64)
        if not len(X):
            return
        rng = np.random.default_rng(seed)
        Xs = (X - self.mu) / self.sd
        Y = np.eye(len(CLASSES), dtype=np.float32)[y]
        with self._lock:
            W, b = self.W.copy(), self.b.copy()
        for _ in range(max(1, epochs)):
            order = rng.permutation(len(Xs))
            for s in range(0, len(order), batch):
                j = order[s:s + batch]
                P = _softmax(Xs[j] @ W + b)
                G = (P - Y[j]) / len(j)
                W -= lr * (Xs[j].T @ G + l2 * W)
                b -= lr * G.sum(axis=0)
        with self._lock:
            self.W, self.b = W, b


def fit(X: np.ndarray, y: np.ndarray, l2: float = 1e-3, iters: int = 300, lr: float = 0.05,
        class_weight: Sequence[float] | None = None, seed: int = 0) -> PatchClassifier:
    """Full-batch Adam fit of a fresh :class:`PatchClassifier` (seconds for ~50k samples)."""
    X = np.asarray(X, np.float32)
    y = np.asarray(y, np.int64)
    mu = X.mean(axis=0)
    sd = X.std(axis=0) + 1e-4
    Xs = (X - mu) / sd
    k = len(CLASSES)
    Y = np.eye(k, dtype=np.float32)[y]
    cw = np.asarray(class_weight if class_weight is not None else [1.0] * k, np.float32)
    sw = cw[y][:, None]
    sw = sw / sw.mean()
    rng = np.random.default_rng(seed)
    W = (rng.standard_normal((X.shape[1], k)) * 0.01).astype(np.float32)
    b = np.zeros(k, np.float32)
    mW, vW, mb, vb = (np.zeros_like(W), np.zeros_like(W), np.zeros_like(b), np.zeros_like(b))
    b1, b2 = 0.9, 0.999
    for t in range(1, iters + 1):
        P = _softmax(Xs @ W + b)
        G = (P - Y) * sw / len(Xs)
        gW = Xs.T @ G + l2 * W
        gb = G.sum(axis=0)
        mW = b1 * mW + (1 - b1) * gW
        vW = b2 * vW + (1 - b2) * gW * gW
        mb = b1 * mb + (1 - b1) * gb
        vb = b2 * vb + (1 - b2) * gb * gb
        a = lr * math.sqrt(1 - b2 ** t) / (1 - b1 ** t)
        W -= a * mW / (np.sqrt(vW) + 1e-8)
        b -= a * mb / (np.sqrt(vb) + 1e-8)
    return PatchClassifier(W, b, mu, sd, {"n_train": float(len(X))})


class OnlineIconLearner:
    """Adapts a :class:`PatchClassifier` to one user's client from their own games.

    Feed it frames with confident icons (``add_frame``: e.g. roster matches with a high
    identity score); it keeps patches of those icons (positives, class from the team) and
    of random points away from them (negatives). Every ``fit_every`` positives it runs
    :meth:`PatchClassifier.partial_fit` in a background thread and saves the model to
    ``save_path`` (e.g. ``<user data>/model/patch_classifier_user.npz``). Cheap: one
    ``add_frame`` costs ~0.3 ms (a few patches); never raises.
    """

    def __init__(self, model: PatchClassifier, save_path: Path | str | None = None,
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
        """``icons`` = confident ``(u, v, r, team)`` (team "enemy" | "ally" | "self")."""
        try:
            if not icons:
                return
            cands = [(u, v, r) for u, v, r, _ in icons]
            ys = [1 if t == "enemy" else 2 for *_, t in icons]
            r0 = float(np.median([c[2] for c in cands]))
            for _ in range(self.neg_per_frame * 3):
                if len(cands) - len(icons) >= self.neg_per_frame:
                    break
                u, v = self._rng.uniform(0.03, 0.97, 2)
                if all(math.hypot(u - a, v - b_) > 1.6 * max(r0, rr) for a, b_, rr, _ in icons):
                    cands.append((float(u), float(v), r0))
                    ys.append(0)
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
                X_all, y_all = np.asarray(self._X), np.asarray(self._y)
                threading.Thread(target=self._fit, args=(X_all, y_all), daemon=True,
                                 name="patch-learner").start()
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


__all__ = ["PatchClassifier", "OnlineIconLearner", "extract_patches", "patch_features", "fit",
           "default_model_path", "CLASSES", "PATCH"]
