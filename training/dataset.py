"""Training data for MinimapNet: an endless stream of synthetic minimaps + CenterNet targets.

* :class:`MinimapStream` (``torch.utils.data.IterableDataset``) renders samples on the fly with
  :func:`training.synth.generate_sample`. Every DataLoader worker gets its own random stream
  seeded from ``(base seed, epoch, worker id)`` so workers never repeat each other, and a
  resumed run (different ``epoch``) never replays the first run's samples.
* :func:`encode_targets` builds the CenterNet targets at stride 4 (heatmap with gaussians
  whose sigma follows the icon radius, sub-cell offsets, radius, class, masks) for at most
  :data:`MAX_OBJECTS` icons.
* :func:`build_validation_set` renders a fixed, seeded validation set once and caches it as
  ``.npz`` (keyed by a fingerprint of ``synth.py`` + ``render.py`` so it is rebuilt when the
  generator changes).

Label convention (ARCHITECTURE §5, MINIMAP_FACTS): the local player's icon looks exactly
like an ally's, so class ``"self"`` is trained as ``"ally"``; ``cls_valid=False`` icons
(random-hue rings) take part in the heatmap / offset / radius losses but not in the class
loss. Images are BGR ``uint8``; network inputs are RGB ``float32`` in 0..1 (like
``treeaicoach.detector.preprocess``).

This module imports without torch (the validation-set helpers are used by ``evaluate.py``,
which only needs onnxruntime); :class:`MinimapStream` then derives from ``object``.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

import cv2
import numpy as np

try:  # torch is only needed for training
    import torch
    from torch.utils.data import IterableDataset, get_worker_info
except ImportError:  # pragma: no cover - evaluation-only environments
    torch = None  # type: ignore[assignment]
    IterableDataset = object  # type: ignore[assignment,misc]

    def get_worker_info() -> Any:  # type: ignore[misc]
        return None

log = logging.getLogger(__name__)

INPUT_SIZE = 256
STRIDE = 4
MAX_OBJECTS = 16
CLASSES = ("enemy", "ally", "self")
#: Class index stored in the validation cache (original labels, "self" kept apart).
LABEL_INDEX = {"enemy": 0, "ally": 1, "self": 2}
#: Training class index: "self" is learned as "ally" (same ring colour in the real game).
TRAIN_CLASS_INDEX = {"enemy": 0, "ally": 1, "self": 1}
GAUSSIAN_MIN_OVERLAP = 0.7
GAUSSIAN_MIN_RADIUS = 1.0            # in output cells
VAL_SIZE_DEFAULT = 600
VAL_SEED_DEFAULT = 20260930
_TRAIN_TAG = 0x7EA1                  # seed-sequence tags: training and validation streams
_VAL_TAG = 0x7A11                    # can never produce the same entropy tuple
_MAX_CONSECUTIVE_FAILURES = 25
REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "training" / "data"

SampleFn = Callable[[np.random.Generator, int], tuple[np.ndarray, list[dict]]]


# ======================================================================================
# Sample generator
# ======================================================================================


def default_generator() -> SampleFn:
    """``training.synth.generate_sample`` (imported lazily)."""
    from training.synth import generate_sample  # noqa: PLC0415 - optional heavy import

    return generate_sample


def generator_fingerprint() -> str:
    """Short hash of the generator sources (``synth.py`` + ``render.py``) for cache keys."""
    h = hashlib.sha1()
    for p in (REPO_ROOT / "training" / "synth.py", REPO_ROOT / "treeaicoach" / "render.py"):
        try:
            h.update(p.read_bytes())
        except OSError:
            h.update(b"missing:" + p.name.encode())
    return h.hexdigest()[:10]


def to_input(img_bgr: np.ndarray, input_size: int = INPUT_SIZE) -> np.ndarray:
    """BGR uint8 image -> float32 ``[3, S, S]`` RGB in 0..1 (``INTER_AREA`` resize)."""
    if img_bgr.shape[0] != input_size or img_bgr.shape[1] != input_size:
        img_bgr = cv2.resize(img_bgr, (input_size, input_size), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    return np.ascontiguousarray(rgb.transpose(2, 0, 1), dtype=np.float32) * np.float32(1.0 / 255.0)


def normalize_labels(labels: Sequence[dict]) -> list[dict]:
    """Keep well-formed labels ``{"u","v","r","cls","cls_valid"}`` (finite, r > 0)."""
    out = []
    for lab in labels or ():
        try:
            u, v, r = float(lab["u"]), float(lab["v"]), float(lab["r"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(u) and math.isfinite(v) and math.isfinite(r)) or r <= 0:
            continue
        cls = str(lab.get("cls", "enemy"))
        valid = bool(lab.get("cls_valid", True)) and cls in LABEL_INDEX
        out.append({"u": u, "v": v, "r": r, "cls": cls if cls in LABEL_INDEX else "enemy",
                    "cls_valid": valid})
    return out


# ======================================================================================
# CenterNet targets
# ======================================================================================


def gaussian_radius(height: float, width: float, min_overlap: float = GAUSSIAN_MIN_OVERLAP) -> float:
    """CenterNet's gaussian radius (in cells) for a box of ``height`` x ``width`` cells."""
    a1 = 1.0
    b1 = height + width
    c1 = width * height * (1 - min_overlap) / (1 + min_overlap)
    r1 = (b1 + math.sqrt(max(0.0, b1 ** 2 - 4 * a1 * c1))) / 2
    a2 = 4.0
    b2 = 2 * (height + width)
    c2 = (1 - min_overlap) * width * height
    r2 = (b2 + math.sqrt(max(0.0, b2 ** 2 - 4 * a2 * c2))) / 2
    a3 = 4 * min_overlap
    b3 = -2 * min_overlap * (height + width)
    c3 = (min_overlap - 1) * width * height
    r3 = (b3 + math.sqrt(max(0.0, b3 ** 2 - 4 * a3 * c3))) / 2
    return min(r1, r2, r3)


def sigma_for_radius(r_norm: float, input_size: int = INPUT_SIZE, stride: int = STRIDE) -> float:
    """Gaussian sigma (cells) for an icon of normalized radius ``r_norm``."""
    side = 2.0 * r_norm * input_size / stride
    rad = max(GAUSSIAN_MIN_RADIUS, gaussian_radius(side, side))
    return (2.0 * rad + 1.0) / 6.0


def draw_gaussian(heatmap: np.ndarray, cx: int, cy: int, sigma: float) -> None:
    """Max-merge a gaussian peak (value 1 at integer cell ``(cx, cy)``) into ``heatmap``."""
    h, w = heatmap.shape
    rad = max(1, int(math.ceil(3.0 * sigma)))
    x0, x1 = max(0, cx - rad), min(w, cx + rad + 1)
    y0, y1 = max(0, cy - rad), min(h, cy + rad + 1)
    if x0 >= x1 or y0 >= y1:
        return
    xs = np.arange(x0, x1, dtype=np.float32) - cx
    ys = np.arange(y0, y1, dtype=np.float32) - cy
    g = np.exp(-(xs[None, :] ** 2 + ys[:, None] ** 2) / np.float32(2.0 * sigma * sigma))
    np.maximum(heatmap[y0:y1, x0:x1], g, out=heatmap[y0:y1, x0:x1])


def encode_targets(labels: Sequence[dict], input_size: int = INPUT_SIZE, stride: int = STRIDE,
                   max_objects: int = MAX_OBJECTS) -> dict[str, np.ndarray]:
    """CenterNet targets for one image.

    Returns ``heatmap [1,H,W]`` float32, and per object slot (``max_objects``): ``ind``
    (flat cell index, int64), ``reg_mask`` (1 = slot used), ``offset [M,2]`` (sub-cell
    position 0..1, x then y), ``radius [M]`` (normalized, like the label ``r``), ``cls``
    (training index, int64) and ``cls_mask`` (1 = class supervised). Icons whose centre lies
    outside the image are ignored (the decoder cannot produce them).
    """
    hs = ws = input_size // stride
    heatmap = np.zeros((hs, ws), np.float32)
    ind = np.zeros(max_objects, np.int64)
    reg_mask = np.zeros(max_objects, np.float32)
    offset = np.zeros((max_objects, 2), np.float32)
    radius = np.zeros(max_objects, np.float32)
    cls = np.zeros(max_objects, np.int64)
    cls_mask = np.zeros(max_objects, np.float32)
    k = 0
    for lab in normalize_labels(labels):
        if k >= max_objects:
            break
        cx_f, cy_f = lab["u"] * ws, lab["v"] * hs
        if not (0.0 <= cx_f < ws and 0.0 <= cy_f < hs):
            continue
        cx, cy = int(cx_f), int(cy_f)
        draw_gaussian(heatmap, cx, cy, sigma_for_radius(lab["r"], input_size, stride))
        ind[k] = cy * ws + cx
        reg_mask[k] = 1.0
        offset[k] = (cx_f - cx, cy_f - cy)
        radius[k] = lab["r"]
        cls[k] = TRAIN_CLASS_INDEX[lab["cls"]]
        cls_mask[k] = 1.0 if lab["cls_valid"] else 0.0
        k += 1
    return {"heatmap": heatmap[None], "ind": ind, "reg_mask": reg_mask, "offset": offset,
            "radius": radius, "cls": cls, "cls_mask": cls_mask}


# ======================================================================================
# Streaming dataset
# ======================================================================================


def stream_rng(seed: int, epoch: int, worker_id: int) -> np.random.Generator:
    """Independent generator for one (seed, epoch, worker) training stream."""
    return np.random.default_rng([_TRAIN_TAG, int(seed) & 0xFFFFFFFF, int(epoch), int(worker_id)])


class MinimapStream(IterableDataset):  # type: ignore[misc,valid-type]
    """Endless stream of ``(image [3,S,S] float32 RGB, targets dict)`` training samples.

    ``epoch`` is an offset mixed into the seed (``train.py`` uses the start step, so a resumed
    run draws fresh samples). ``limit`` optionally stops each worker after that many samples.
    """

    def __init__(self, seed: int = 0, input_size: int = INPUT_SIZE, stride: int = STRIDE,
                 max_objects: int = MAX_OBJECTS, epoch: int = 0, generator: SampleFn | None = None,
                 limit: int | None = None) -> None:
        super().__init__()
        self.seed = int(seed)
        self.input_size = int(input_size)
        self.stride = int(stride)
        self.max_objects = int(max_objects)
        self.epoch = int(epoch)
        self.generator = generator
        self.limit = limit

    def set_epoch(self, epoch: int) -> None:
        """Change the seed offset (takes effect the next time an iterator is created)."""
        self.epoch = int(epoch)

    def __iter__(self) -> Iterator[tuple[Any, dict[str, Any]]]:
        info = get_worker_info()
        wid = info.id if info is not None else 0
        rng = stream_rng(self.seed, self.epoch, wid)
        gen = self.generator or default_generator()
        failures = 0
        produced = 0
        while self.limit is None or produced < self.limit:
            try:
                img, labels = gen(rng, self.input_size)
                x = to_input(img, self.input_size)
                t = encode_targets(labels, self.input_size, self.stride, self.max_objects)
            except Exception:  # a rare generator bug must not kill a long training run
                failures += 1
                log.exception("Sample generation failed (%d in a row)", failures)
                if failures >= _MAX_CONSECUTIVE_FAILURES:
                    raise
                continue
            failures = 0
            produced += 1
            if torch is not None:
                yield torch.from_numpy(x), {k: torch.from_numpy(v) for k, v in t.items()}
            else:  # pragma: no cover
                yield x, t


def worker_init(_worker_id: int) -> None:
    """DataLoader ``worker_init_fn``: one thread per worker (the cores are for the model)."""
    try:
        cv2.setNumThreads(1)
    except Exception:  # pragma: no cover
        pass
    if torch is not None:
        torch.set_num_threads(1)


# ======================================================================================
# Fixed validation set
# ======================================================================================


@dataclass
class ValidationSet:
    """Fixed validation images (BGR uint8 ``[N,S,S,3]``) and their labels."""

    images: np.ndarray
    labels: list[list[dict]]
    seed: int
    fingerprint: str

    def __len__(self) -> int:
        return int(self.images.shape[0])

    def subset(self, n: int) -> "ValidationSet":
        """The first ``n`` images."""
        n = max(0, min(int(n), len(self)))
        return ValidationSet(self.images[:n], self.labels[:n], self.seed, self.fingerprint)


def val_rng(seed: int, index: int) -> np.random.Generator:
    """Generator of validation image ``index`` (independent of the set size)."""
    return np.random.default_rng([_VAL_TAG, int(seed) & 0xFFFFFFFF, int(index)])


def _labels_to_array(labels: list[list[dict]]) -> np.ndarray:
    """Pack labels as ``[N, K, 6]`` float32: u, v, r, class index, cls_valid, present."""
    k = max([len(ls) for ls in labels] + [1])
    arr = np.zeros((len(labels), k, 6), np.float32)
    for i, ls in enumerate(labels):
        for j, lab in enumerate(ls):
            arr[i, j] = (lab["u"], lab["v"], lab["r"], LABEL_INDEX[lab["cls"]],
                         1.0 if lab["cls_valid"] else 0.0, 1.0)
    return arr


def _array_to_labels(arr: np.ndarray) -> list[list[dict]]:
    out: list[list[dict]] = []
    for row in arr:
        ls = []
        for u, v, r, c, valid, present in row:
            if present > 0.5:
                ls.append({"u": float(u), "v": float(v), "r": float(r),
                           "cls": CLASSES[int(round(c))], "cls_valid": bool(valid > 0.5)})
        out.append(ls)
    return out


def generate_validation_set(n: int = VAL_SIZE_DEFAULT, seed: int = VAL_SEED_DEFAULT,
                            input_size: int = INPUT_SIZE, generator: SampleFn | None = None,
                            ) -> ValidationSet:
    """Render ``n`` seeded validation images (no cache)."""
    gen = generator or default_generator()
    images = np.zeros((n, input_size, input_size, 3), np.uint8)
    labels: list[list[dict]] = []
    for i in range(n):
        img, labs = gen(val_rng(seed, i), input_size)
        if img.shape[:2] != (input_size, input_size):
            img = cv2.resize(img, (input_size, input_size), interpolation=cv2.INTER_AREA)
        images[i] = img
        labels.append(normalize_labels(labs))
    return ValidationSet(images, labels, seed, generator_fingerprint() if generator is None else "custom")


def build_validation_set(n: int = VAL_SIZE_DEFAULT, seed: int = VAL_SEED_DEFAULT,
                         input_size: int = INPUT_SIZE, cache_dir: Path | str | None = DATA_DIR,
                         generator: SampleFn | None = None) -> ValidationSet:
    """Fixed validation set, cached to ``cache_dir/val_<n>_<seed>_<size>_<fingerprint>.npz``.

    A cache for a larger ``n`` with the same key is reused (its first ``n`` images are the
    same, since image ``i`` only depends on ``(seed, i)``). ``cache_dir=None`` disables the
    cache; a custom ``generator`` is never cached.
    """
    fp = generator_fingerprint()
    if cache_dir is None or generator is not None:
        return generate_validation_set(n, seed, input_size, generator)
    cache_dir = Path(cache_dir)
    stem = f"val_{{n}}_{seed}_{input_size}_{fp}.npz"
    try:
        candidates = sorted(cache_dir.glob(stem.replace("{n}", "*")))
    except OSError:
        candidates = []
    for path in candidates:
        try:
            cached_n = int(path.name.split("_")[1])
        except (IndexError, ValueError):
            continue
        if cached_n < n:
            continue
        try:
            with np.load(path) as data:
                images = data["images"][:n]
                labels = _array_to_labels(data["labels"][:n])
            log.info("Validation set loaded from %s", path)
            return ValidationSet(images, labels, seed, fp)
        except Exception as exc:  # corrupt cache -> rebuild
            log.warning("Ignoring unreadable validation cache %s: %s", path, exc)
    t0 = time.perf_counter()
    vs = generate_validation_set(n, seed, input_size)
    vs.fingerprint = fp
    log.info("Validation set: %d images rendered in %.1f s", n, time.perf_counter() - t0)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / stem.replace("{n}", str(n))
        tmp = path.with_suffix(".tmp.npz")
        np.savez_compressed(tmp, images=vs.images, labels=_labels_to_array(vs.labels))
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("Cannot cache the validation set: %s", exc)
    return vs
