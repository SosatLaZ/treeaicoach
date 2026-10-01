"""Champion icon detection on the minimap.

Two interchangeable backends share the :class:`BaseDetector` interface:

* :class:`OnnxDetector` runs the small CenterNet-like CNN exported by
  ``training/export_onnx.py`` (``assets/model/minimap_detector.onnx`` + ``model_meta.json``)
  with onnxruntime on the CPU and decodes its outputs with :func:`decode_outputs`.
* :class:`ClassicDetector` is the no-ML fallback: Hough circles on the blurred grey minimap,
  then each candidate is kept only if its ring is consistently red / blue / yellow and its
  interior looks like a textured portrait.

:func:`create_detector` picks the backend and never raises. Detections are in normalized
minimap coordinates (``u``, ``v`` in [0, 1], radius ``r`` normalized by the minimap width).

The model contract (docs/ARCHITECTURE.md §4.9): input ``input`` float32 ``[1,3,S,S]`` RGB in
[0, 1]; outputs ``heatmap`` ``[1,1,S/4,S/4]`` (sigmoid), ``cls`` ``[1,3,S/4,S/4]`` (softmax),
``offset`` ``[1,2,S/4,S/4]`` (sigmoid, fraction of a cell), ``radius`` ``[1,1,S/4,S/4]``
(radius / S). The class ``self`` is kept for compatibility but is not learned visually (the
local player's ring is the same blue as the allies'): see ``identifier.py``.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

log = logging.getLogger(__name__)

#: Detector classes (fixed order, shared with the model and the training code).
CLASSES: tuple[str, str, str] = ("enemy", "ally", "self")

MODEL_FILE = "minimap_detector.onnx"
META_FILE = "model_meta.json"
DEFAULT_INPUT_SIZE = 256
DEFAULT_STRIDE = 4
DEFAULT_THRESHOLD = 0.35
DEFAULT_MAX_DET = 20
#: Output names of the ONNX model, in the canonical order.
OUTPUT_NAMES: tuple[str, str, str, str] = ("heatmap", "cls", "offset", "radius")
_OUTPUT_CHANNELS = {"heatmap": 1, "cls": len(CLASSES), "offset": 2, "radius": 1}
#: Duplicate peaks closer than this fraction of the radius are merged by decode_outputs.
DUPLICATE_FRAC = 0.6
#: Smallest image side accepted by the detectors (px).
MIN_IMAGE_SIDE = 16
_MAX_IMAGE_SIDE = 8192
_ERROR_LOG_INTERVAL_S = 30.0


@dataclass
class Detection:
    """One detected champion icon (normalized minimap coordinates)."""

    u: float                               # centre, left -> right
    v: float                               # centre, top -> bottom
    r: float                               # icon radius / minimap width
    score: float                           # confidence "this is a champion icon" (0..1)
    cls: str                               # most likely class (see CLASSES)
    cls_probs: tuple[float, float, float]  # probabilities in CLASSES order
    #: Champion recognised by the detector itself (roster matcher), None otherwise.
    alias: str | None = None


class BaseDetector:
    """Common interface of the detectors. ``detect`` never raises (returns [] on error)."""

    name: str = "base"

    def detect(self, minimap_bgr: np.ndarray) -> list[Detection]:
        """Champion icons found in a BGR minimap image."""
        raise NotImplementedError

    def close(self) -> None:
        """Release resources (optional)."""


# ======================================================================================
# Helpers
# ======================================================================================


class _RateLimitedLog:
    """Logs an exception at most once per interval (the detection loop runs at ~8 Hz)."""

    def __init__(self, interval_s: float = _ERROR_LOG_INTERVAL_S) -> None:
        self.interval_s = interval_s
        self._last = -math.inf
        self._suppressed = 0
        self._lock = threading.Lock()

    def exception(self, msg: str, *args: Any) -> None:
        now = time.monotonic()
        with self._lock:
            if now - self._last < self.interval_s:
                self._suppressed += 1
                return
            suppressed, self._suppressed, self._last = self._suppressed, 0, now
        if suppressed:
            msg += " (%d similar errors suppressed)"
            args = (*args, suppressed)
        log.exception(msg, *args)


def _as_bgr(img: Any) -> np.ndarray | None:
    """Validate / convert an image to BGR uint8 (grey and BGRA accepted); None if unusable."""
    if not isinstance(img, np.ndarray) or img.ndim not in (2, 3):
        return None
    h, w = img.shape[:2]
    if min(h, w) < MIN_IMAGE_SIDE or max(h, w) > _MAX_IMAGE_SIDE:
        return None
    if img.dtype != np.uint8:
        if img.dtype == np.uint16:
            img = (img >> 8).astype(np.uint8)
        elif np.issubdtype(img.dtype, np.floating):
            arr = np.nan_to_num(img.astype(np.float32), nan=0.0, posinf=255.0, neginf=0.0)
            if arr.size and float(arr.max()) <= 1.0:
                arr = arr * 255.0
            img = np.clip(arr + 0.5, 0, 255).astype(np.uint8)
        else:
            img = np.clip(img, 0, 255).astype(np.uint8)
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    ch = img.shape[2]
    if ch == 3:
        return np.ascontiguousarray(img)
    if ch == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    if ch == 1:
        return cv2.cvtColor(np.ascontiguousarray(img[:, :, 0]), cv2.COLOR_GRAY2BGR)
    return None


def _resize_to(img: np.ndarray, size: int) -> np.ndarray:
    """Square resize, INTER_AREA (the filter used for training, see :func:`preprocess`)."""
    h, w = img.shape[:2]
    if (h, w) == (size, size):
        return img
    return cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)


def preprocess(minimap_bgr: np.ndarray, input_size: int) -> np.ndarray:
    """BGR minimap -> float32 ``[1, 3, S, S]`` RGB in [0, 1] (resized with INTER_AREA).

    Grey / BGRA images are accepted. Raises ``ValueError`` for an unusable image.
    """
    size = int(input_size)
    if size < 8:
        raise ValueError(f"invalid input size {input_size!r}")
    bgr = _as_bgr(minimap_bgr)
    if bgr is None:
        shape = getattr(minimap_bgr, "shape", None)
        raise ValueError(f"unusable minimap image (shape {shape})")
    img = _resize_to(bgr, size)
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    x = rgb.astype(np.float32)
    x *= np.float32(1.0 / 255.0)
    return np.ascontiguousarray(x.transpose(2, 0, 1)[None])


def _chw(a: Any, name: str, channels: int | None) -> np.ndarray:
    """Model output -> float32 [C, H, W] (accepts [1,C,H,W], [C,H,W] or [H,W] for C == 1)."""
    arr = np.asarray(a, dtype=np.float32)
    if arr.ndim == 4:
        if arr.shape[0] < 1:
            raise ValueError(f"{name}: empty batch")
        arr = arr[0]
    elif arr.ndim == 2:
        arr = arr[None]
    if arr.ndim != 3:
        raise ValueError(f"{name}: expected [1,C,H,W], got shape {np.shape(a)}")
    if channels is not None and arr.shape[0] != channels:
        raise ValueError(f"{name}: expected {channels} channel(s), got {arr.shape[0]}")
    return arr


def _maxpool3(h: np.ndarray) -> np.ndarray:
    """3x3 max-pool (stride 1, same size) with -inf padding, pure numpy."""
    H, W = h.shape
    p = np.pad(h, 1, mode="constant", constant_values=-np.inf)
    out = p[1:H + 1, 1:W + 1].copy()
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            if dy != 1 or dx != 1:
                np.maximum(out, p[dy:dy + H, dx:dx + W], out=out)
    return out


def _normalize_probs(p: np.ndarray) -> tuple[float, float, float]:
    """Clean class probabilities (3 values, finite, >= 0, summing to 1)."""
    q = np.zeros(len(CLASSES), np.float64)
    n = min(len(CLASSES), p.shape[0])
    q[:n] = np.nan_to_num(p[:n].astype(np.float64), nan=0.0, posinf=1.0, neginf=0.0)
    q = np.clip(q, 0.0, None)
    s = float(q.sum())
    if s <= 1e-9:
        q[:] = 1.0 / len(CLASSES)
    else:
        q /= s
    return float(q[0]), float(q[1]), float(q[2])


def _dedupe(dets: list[Detection], frac: float, min_dist: float, max_det: int,
            use_max_radius: bool = False) -> list[Detection]:
    """Greedy suppression (by descending score) of detections closer than ``frac`` * radius.

    The radius is the kept detection's one, or the larger of the two if ``use_max_radius``.
    """
    kept: list[Detection] = []
    for d in sorted(dets, key=lambda x: -x.score):
        dup = False
        for k in kept:
            lim = max(frac * (max(k.r, d.r) if use_max_radius else k.r), min_dist)
            if (d.u - k.u) ** 2 + (d.v - k.v) ** 2 < lim * lim:
                dup = True
                break
        if not dup:
            kept.append(d)
            if len(kept) >= max_det:
                break
    return kept


# ======================================================================================
# Decoding of the CNN outputs
# ======================================================================================


def decode_outputs(heatmap: Any, cls: Any, offset: Any, radius: Any, threshold: float,
                   stride: int, input_size: int, max_det: int = DEFAULT_MAX_DET) -> list[Detection]:
    """Decode CenterNet outputs into detections (pure numpy).

    ``heatmap`` [1,1,H,W] (sigmoid), ``cls`` [1,3,H,W] (softmax), ``offset`` [1,2,H,W] (0..1
    in cells, x then y), ``radius`` [1,1,H,W] (radius / S). Peaks = 3x3 max-pool maxima with
    ``heatmap > threshold``; ``u = (x + off_x) * stride / S`` (idem ``v``), ``r = radius``.
    Duplicate peaks closer than 0.6 * radius are merged (highest score kept); at most
    ``max_det`` detections, sorted by descending score. Raises ``ValueError`` if the arrays
    have inconsistent shapes.
    """
    heat = _chw(heatmap, "heatmap", 1)[0]
    H, W = heat.shape
    cls_a = _chw(cls, "cls", None)
    off = _chw(offset, "offset", 2)
    rad = _chw(radius, "radius", 1)[0]
    for name, arr in (("cls", cls_a), ("offset", off)):
        if arr.shape[1:] != (H, W):
            raise ValueError(f"{name}: spatial shape {arr.shape[1:]} != heatmap {(H, W)}")
    if rad.shape != (H, W):
        raise ValueError(f"radius: spatial shape {rad.shape} != heatmap {(H, W)}")
    S = float(input_size)
    st = float(stride)
    if not (S > 0 and st > 0):
        raise ValueError(f"invalid stride / input size: {stride!r} / {input_size!r}")
    max_det = int(max_det)
    if max_det <= 0 or H == 0 or W == 0:
        return []
    thr = float(threshold) if math.isfinite(float(threshold)) else DEFAULT_THRESHOLD

    heat = np.nan_to_num(heat, nan=0.0, posinf=1.0, neginf=0.0)
    peaks = (heat >= _maxpool3(heat)) & (heat > thr)
    ys, xs = np.nonzero(peaks)
    if ys.size == 0:
        return []
    scores = heat[ys, xs]
    order = np.argsort(-scores, kind="stable")[: max(4 * max_det, 32)]
    ys, xs, scores = ys[order], xs[order], scores[order]

    off_x = np.clip(np.nan_to_num(off[0, ys, xs], nan=0.5), 0.0, 1.0)
    off_y = np.clip(np.nan_to_num(off[1, ys, xs], nan=0.5), 0.0, 1.0)
    us = np.clip((xs + off_x) * st / S, 0.0, 1.0)
    vs = np.clip((ys + off_y) * st / S, 0.0, 1.0)
    rs = np.clip(np.nan_to_num(rad[ys, xs], nan=0.0, posinf=0.0, neginf=0.0), 0.0, 1.0)
    probs = cls_a[:, ys, xs]

    dets: list[Detection] = []
    for i in range(ys.size):
        p = _normalize_probs(probs[:, i])
        dets.append(Detection(
            u=float(us[i]), v=float(vs[i]), r=float(rs[i]),
            score=float(min(1.0, max(0.0, scores[i]))),
            cls=CLASSES[int(np.argmax(p))], cls_probs=p,
        ))
    # two local maxima in neighbouring cells (distance 1 or sqrt 2 cells) can only be an
    # equal-valued plateau: merge them; peaks 2+ cells apart stay distinct unless closer
    # than DUPLICATE_FRAC * radius
    return _dedupe(dets, DUPLICATE_FRAC, 1.5 * st / S, max_det)


# ======================================================================================
# ONNX backend
# ======================================================================================


def default_model_path() -> Path:
    """Bundled model: ``assets/model/minimap_detector.onnx``."""
    try:
        from treeaicoach.paths import asset_path

        return Path(asset_path("model", MODEL_FILE))
    except Exception:  # pragma: no cover - paths module broken
        return Path(__file__).resolve().parent / "assets" / "model" / MODEL_FILE


def default_meta_path() -> Path:
    """Bundled model metadata: ``assets/model/model_meta.json``."""
    return default_model_path().with_name(META_FILE)


def _static_dim(d: Any) -> int | None:
    return int(d) if isinstance(d, (int, np.integer)) and int(d) > 0 else None


def load_model_meta(meta_path: Path | None) -> dict:
    """Read ``model_meta.json``; {} if absent or unreadable (a warning is logged)."""
    if meta_path is None:
        return {}
    try:
        with open(meta_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
        if not isinstance(meta, dict):
            raise ValueError("not a JSON object")
        return meta
    except FileNotFoundError:
        log.warning("Model metadata not found: %s (defaults used)", meta_path)
    except Exception as exc:
        log.warning("Model metadata unreadable: %s (%s), defaults used", meta_path, exc)
    return {}


class OnnxDetector(BaseDetector):
    """CNN detector (onnxruntime, CPU). Initialization errors raise; ``detect`` never raises."""

    name = "onnx"

    def __init__(self, model_path: Path | None = None, meta_path: Path | None = None,
                 threshold: float = 0.0, threads: int = 2) -> None:
        try:
            import onnxruntime as ort
        except Exception as exc:  # ImportError, DLL load failure on Windows...
            raise RuntimeError(f"onnxruntime unavailable: {exc}") from exc

        self.model_path = Path(model_path) if model_path is not None else default_model_path()
        if meta_path is None:
            meta_path = self.model_path.with_name(META_FILE)
        self.meta_path = Path(meta_path)
        if not self.model_path.is_file():
            raise FileNotFoundError(f"ONNX model not found: {self.model_path}")
        self.meta = load_model_meta(self.meta_path)

        # ---- metadata ------------------------------------------------------------
        classes = self.meta.get("classes")
        if classes is not None and [str(c) for c in classes] != list(CLASSES):
            raise ValueError(f"model classes {classes!r} != expected {list(CLASSES)!r}")
        stride = self.meta.get("stride", DEFAULT_STRIDE)
        if not isinstance(stride, (int, float)) or int(stride) != stride or int(stride) not in (1, 2, 4, 8, 16, 32):
            raise ValueError(f"invalid stride in model metadata: {stride!r}")
        self.stride = int(stride)
        meta_size = self.meta.get("input_size")
        if meta_size is not None and (not isinstance(meta_size, (int, float))
                                      or int(meta_size) != meta_size or not 32 <= int(meta_size) <= 2048):
            raise ValueError(f"invalid input_size in model metadata: {meta_size!r}")

        try:
            thr = float(threshold)
        except (TypeError, ValueError):
            thr = 0.0
        if not math.isfinite(thr) or thr <= 0.0:
            try:
                thr = float(self.meta.get("threshold", DEFAULT_THRESHOLD))
            except (TypeError, ValueError):
                thr = DEFAULT_THRESHOLD
            if not math.isfinite(thr) or not 0.0 < thr < 1.0:
                thr = DEFAULT_THRESHOLD
        self.threshold = float(min(0.99, max(0.01, thr)))
        self.max_det = DEFAULT_MAX_DET

        # ---- session -------------------------------------------------------------
        so = ort.SessionOptions()
        so.intra_op_num_threads = max(1, min(16, int(threads or 1)))
        so.inter_op_num_threads = 1
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        so.log_severity_level = 3
        providers = ["CPUExecutionProvider"]
        try:
            available = ort.get_available_providers()
            if "CPUExecutionProvider" not in available:
                raise RuntimeError(f"CPUExecutionProvider unavailable ({available})")
        except AttributeError:  # pragma: no cover - very old onnxruntime
            pass
        # bytes rather than a path: works with any (non-ASCII) Windows path
        model_bytes = self.model_path.read_bytes()
        self._sess = ort.InferenceSession(model_bytes, sess_options=so, providers=providers)

        # ---- input ---------------------------------------------------------------
        inputs = self._sess.get_inputs()
        if not inputs:
            raise ValueError("ONNX model has no input")
        inp = next((i for i in inputs if i.name == "input"), inputs[0])
        if len(inputs) > 1:
            raise ValueError(f"ONNX model has {len(inputs)} inputs, expected 1")
        if "float" not in str(inp.type) or "16" in str(inp.type):
            raise ValueError(f"ONNX input must be float32, got {inp.type}")
        shape = list(inp.shape or [])
        if len(shape) != 4:
            raise ValueError(f"ONNX input must be [1,3,S,S], got {shape}")
        if _static_dim(shape[1]) not in (None, 3):
            raise ValueError(f"ONNX input must have 3 channels, got {shape}")
        h_dim, w_dim = _static_dim(shape[2]), _static_dim(shape[3])
        if h_dim is not None and w_dim is not None and h_dim != w_dim:
            raise ValueError(f"ONNX input must be square, got {shape}")
        size = int(meta_size) if meta_size is not None else (h_dim or DEFAULT_INPUT_SIZE)
        if h_dim is not None and h_dim != size:
            raise ValueError(f"model input size {h_dim} != model_meta.json input_size {size}")
        if size % self.stride:
            raise ValueError(f"input size {size} is not a multiple of the stride {self.stride}")
        self.input_size = size
        self._input_name = inp.name

        # ---- outputs -------------------------------------------------------------
        out_names = [o.name for o in self._sess.get_outputs()]
        if all(n in out_names for n in OUTPUT_NAMES):
            self._output_names = list(OUTPUT_NAMES)
        elif len(out_names) == len(OUTPUT_NAMES):
            log.warning("ONNX outputs named %s; assuming the order %s", out_names, OUTPUT_NAMES)
            self._output_names = out_names
        else:
            raise ValueError(f"ONNX outputs {out_names} do not match {list(OUTPUT_NAMES)}")

        # ---- warm-up + shape validation ------------------------------------------
        x = np.zeros((1, 3, size, size), np.float32)
        outs = self._sess.run(self._output_names, {self._input_name: x})
        g = size // self.stride
        for name, arr in zip(OUTPUT_NAMES, outs):
            exp = (1, _OUTPUT_CHANNELS[name], g, g)
            if tuple(np.shape(arr)) != exp:
                raise ValueError(f"ONNX output {name!r} has shape {np.shape(arr)}, expected {exp}")
        self._errors = _RateLimitedLog()
        log.info("ONNX detector ready: %s (input %d, stride %d, threshold %.2f, %d thread(s))",
                 self.model_path.name, size, self.stride, self.threshold, so.intra_op_num_threads)

    def detect(self, minimap_bgr: np.ndarray) -> list[Detection]:
        """Run the CNN on a BGR minimap; [] on invalid input or error. Never raises."""
        try:
            bgr = _as_bgr(minimap_bgr)
            if bgr is None:
                return []
            x = preprocess(bgr, self.input_size)
            outs = self._sess.run(self._output_names, {self._input_name: x})
            return decode_outputs(*outs, threshold=self.threshold, stride=self.stride,
                                  input_size=self.input_size, max_det=self.max_det)
        except Exception:
            self._errors.exception("ONNX detection failed")
            return []


# ======================================================================================
# Classic backend (no ML)
# ======================================================================================

# Ring colour classes (index = CLASSES index): 0 red (enemy), 1 blue (ally), 2 yellow (self).
# OpenCV HSV: H in [0, 180), S and V in [0, 255]. Measured ring colours (MINIMAP_FACTS.md):
# ally ~ RGB(75-81, 140-162, 200-230) -> H ~104; pale 2024 variant ~ RGB(133-183, 143-187,
# 162-204) -> H 110-115, S 26-46; enemy ~ RGB(195-204, 51, 51) -> H 0; JPEG variants desaturated.
_RED_H = (10, 168)          # H <= 10 or H >= 168
_RED_S, _RED_V = 85, 75
_BLUE_H = (99, 128)         # >= 99 excludes most of the cyan recall halo (H ~ 97)
_BLUE_S, _BLUE_V = 60, 105
_PALE_H = (100, 135)
_PALE_S, _PALE_V = 20, 150
_YEL_H = (16, 36)
_YEL_S, _YEL_V = 100, 130
# Bright teal / cyan outline of the local player (2025+ clients) and the recall halo: ally
# side. Bright only (V), so the dark teal river does not count.
_TEAL_H = (80, 99)
_TEAL_S, _TEAL_V = 60, 150


def ring_color_labels(hsv: np.ndarray) -> np.ndarray:
    """Per-pixel ring colour evidence: bool array ``[3, ...]`` (red, blue, yellow) from HSV.

    "blue" (ally side) also covers the pale 2024 ring and the bright teal outline of the
    local player / the cyan recall halo.
    """
    h = hsv[..., 0]
    s = hsv[..., 1]
    v = hsv[..., 2]
    red = ((h <= _RED_H[0]) | (h >= _RED_H[1])) & (s >= _RED_S) & (v >= _RED_V)
    blue = (((h >= _BLUE_H[0]) & (h <= _BLUE_H[1]) & (s >= _BLUE_S) & (v >= _BLUE_V))
            | ((h >= _PALE_H[0]) & (h <= _PALE_H[1]) & (s >= _PALE_S) & (v >= _PALE_V))
            | ((h >= _TEAL_H[0]) & (h <= _TEAL_H[1]) & (s >= _TEAL_S) & (v >= _TEAL_V)))
    yellow = (h >= _YEL_H[0]) & (h <= _YEL_H[1]) & (s >= _YEL_S) & (v >= _YEL_V)
    return np.stack([red, blue, yellow])


class ClassicDetector(BaseDetector):
    """Fallback detector without ML: Hough circles + ring colour + textured interior.

    The minimap is resized to :attr:`WORK_SIZE`; Hough circles with radii in
    ``[r_min, r_max] * width`` give candidates; each candidate's ring is sampled on
    :attr:`N_ANGLES` rays at several radii (HSV) and must be consistently red / blue /
    yellow; the interior must be textured (portrait) and not ring-coloured. Classes come
    from the dominant ring colour (red -> enemy, blue -> ally, yellow -> self).

    Live calibration (optional, fed by the roster matcher): :meth:`set_ring_colors` adds
    the ring colours actually seen on the user's screen (any client, colourblind mode,
    capture colour shifts) to the fixed HSV ranges, and :meth:`set_scale` gives the icon
    size. Besides the Hough circles, candidates also come from a ring-colour annulus filter
    (colour mask convolved with a ring of the expected radius), which finds icons whose
    edges are too blurred / overlapped for Hough.
    """

    name = "classic"
    WORK_SIZE = 256
    N_ANGLES = 48
    #: Sampled radii, relative to the Hough radius.
    RADIUS_STEPS = tuple(np.round(np.linspace(0.68, 1.30, 15), 4))
    #: Icon radius / outermost sampled radius where the ring colour is still dominant.
    OUTER_TO_RADIUS = 1.0
    MIN_COVERAGE = 0.55         # fraction of the ring (valid rays) with the dominant colour
    MAX_OTHER_COVERAGE = 0.45   # a second colour this present -> not a consistent ring
    MIN_VALID_RAYS = 0.45       # partially out-of-frame icons: at least this part visible
    MAX_OUTSIDE_COVERAGE = 0.6  # ring colour just outside the ring -> blob, not a ring
    MAX_INSIDE_COVERAGE = 0.85  # ring colour almost everywhere inside -> filled disc
    MIN_TEXTURE_STD = 8.0       # grey std of the interior (flat discs rejected)
    NESTED_RATIO = 0.85         # a ring this much smaller inside another one is a detail
    #: Detections closer than this x the larger radius are the same icon (Hough gives
    #: several shifted circles per icon; truly stacked icons cannot be separated anyway).
    DUPLICATE_FRAC = 0.95
    MAX_CANDIDATES = 160
    MAX_DET = DEFAULT_MAX_DET
    HOUGH_DP = 1.0
    HOUGH_PARAM1 = 80           # Canny high threshold
    HOUGH_PARAM2 = 11           # accumulator threshold (low: recall first, then filtering)
    BLUR_SIGMA = 1.0

    def __init__(self, r_min: float = 0.025, r_max: float = 0.075) -> None:
        try:
            r_min, r_max = float(r_min), float(r_max)
        except (TypeError, ValueError):
            r_min, r_max = 0.025, 0.075
        if not (math.isfinite(r_min) and math.isfinite(r_max) and 0.0 < r_min < r_max <= 0.5):
            log.warning("Invalid classic detector radii (%r, %r): defaults used", r_min, r_max)
            r_min, r_max = 0.025, 0.075
        self.r_min = r_min
        self.r_max = r_max
        ang = np.linspace(0.0, 2.0 * np.pi, self.N_ANGLES, endpoint=False)
        self._cos = np.cos(ang).astype(np.float32)
        self._sin = np.sin(ang).astype(np.float32)
        self._steps = np.asarray(self.RADIUS_STEPS, np.float32)
        # interior sampling pattern (fractions of the ring radius): centre + 3 circles
        pts = [(0.0, 0.0)]
        for rr, n in ((0.22, 8), (0.42, 12), (0.60, 16)):
            a = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False) + rr
            pts += [(rr * math.cos(t), rr * math.sin(t)) for t in a]
        self._inner = np.asarray(pts, np.float32)
        self._errors = _RateLimitedLog()
        #: Rejection counters of the last call (diagnostics).
        self.last_rejections: dict[str, int] = {}
        #: Learned ring colours: class index (0 enemy, 1 ally) -> Lab centroids [m, 3].
        self._learned: dict[int, np.ndarray] = {}
        #: Calibrated icon diameter / minimap width (None: unknown).
        self._scale: float | None = None

    # ------------------------------------------------------------------ calibration
    LEARNED_DIST = 24.0          # weighted Lab distance to a learned ring colour
    _LAB_W = np.asarray([0.35, 1.0, 1.0], np.float32)

    def set_ring_colors(self, colors: dict[str, Any] | None) -> None:
        """Ring colours seen live (relation -> BGR), e.g. ``RosterMatcher.ring_colors``."""
        learned: dict[int, list[np.ndarray]] = {}
        for rel, bgr in (colors or {}).items():
            try:
                c = 0 if rel == "enemy" else 1 if rel in ("ally", "self") else None
                if c is None:
                    continue
                px = np.clip(np.asarray(bgr, np.float64)[:3], 0, 255).astype(np.uint8)
                lab = cv2.cvtColor(px.reshape(1, 1, 3), cv2.COLOR_BGR2LAB).reshape(3)
                if float(np.hypot(float(lab[1]) - 128.0, float(lab[2]) - 128.0)) < 10.0:
                    continue             # a grey "ring colour" would match everything
                learned.setdefault(c, []).append(lab.astype(np.float32))
            except Exception:
                continue
        self._learned = {c: np.stack(v) for c, v in learned.items()}

    def set_scale(self, diameter_ratio: float | None) -> None:
        """Calibrated icon diameter / minimap width (None to forget it)."""
        try:
            d = float(diameter_ratio) if diameter_ratio is not None else None
        except (TypeError, ValueError):
            d = None
        self._scale = d if d is not None and math.isfinite(d) and 0.03 <= d <= 0.2 else None

    def _labels(self, bgr: np.ndarray, hsv: np.ndarray) -> np.ndarray:
        """Ring colour evidence ``[3, ...]`` from the HSV ranges + the learned colours."""
        lab = ring_color_labels(hsv)
        if self._learned:
            L = cv2.cvtColor(np.ascontiguousarray(bgr.reshape(-1, 1, 3), np.uint8),
                             cv2.COLOR_BGR2LAB).reshape(bgr.shape).astype(np.float32)
            for c, cents in self._learned.items():
                d = np.min(np.stack([np.sqrt((((L - ct) * self._LAB_W) ** 2).sum(axis=-1))
                                     for ct in cents]), axis=0)
                lab[c] |= d < self.LEARNED_DIST
        return lab

    def _ring_candidates(self, work: np.ndarray) -> np.ndarray:
        """Circles ``[n, 3]`` where a ring of the expected radius has a ring colour."""
        S = work.shape[0]
        hsv = cv2.cvtColor(work, cv2.COLOR_BGR2HSV)
        lab = self._labels(work, hsv)
        if self._scale is not None:
            radii = [0.5 * self._scale * S * f for f in (0.92, 1.0, 1.08)]
        else:
            radii = list(np.linspace(0.043, 0.052, 3) * S)
        # filtered at half resolution (fractional coverage): 16x cheaper, precise enough
        # for candidates (the ring verification runs at full resolution)
        h = 0.5
        radii = [r * h for r in radii]
        out: list[tuple[float, float, float]] = []
        for c in (0, 1):
            m = lab[c].astype(np.float32)
            if float(m.mean()) < 1e-4:
                continue
            m = cv2.resize(m, (S // 2, S // 2), interpolation=cv2.INTER_AREA)
            best = None
            for r in radii:
                k = int(math.ceil(1.1 * r)) * 2 + 1
                yy, xx = np.mgrid[0:k, 0:k].astype(np.float32) - (k - 1) / 2.0
                d = np.sqrt(xx * xx + yy * yy)
                ring = ((d >= 0.8 * r) & (d <= 1.02 * r)).astype(np.float32)
                inner = (d <= 0.5 * r).astype(np.float32)
                cov = cv2.filter2D(m, -1, ring / ring.sum(), borderType=cv2.BORDER_CONSTANT)
                ins = cv2.filter2D(m, -1, inner / inner.sum(), borderType=cv2.BORDER_CONSTANT)
                sc = cov - 0.8 * ins
                if best is None:
                    best, rad = sc, np.full(sc.shape, r, np.float32)
                else:
                    better = sc > best
                    best = np.where(better, sc, best)
                    rad = np.where(better, np.float32(r), rad)
            dil = cv2.dilate(best, np.ones((5, 5), np.uint8))
            ys, xs = np.nonzero((best >= dil) & (best > 0.4))
            order = np.argsort(-best[ys, xs])[:40]
            out += [((xs[j] + 0.5) / h - 0.5, (ys[j] + 0.5) / h - 0.5, rad[ys[j], xs[j]] / h)
                    for j in order]
        return np.asarray(out, np.float32).reshape(-1, 3)

    # ------------------------------------------------------------------ public
    def detect(self, minimap_bgr: np.ndarray) -> list[Detection]:
        """Detect champion icons; [] on invalid input or error. Never raises."""
        try:
            return self._detect(minimap_bgr)
        except Exception:
            self._errors.exception("Classic detection failed")
            return []

    # ------------------------------------------------------------------ internals
    def _candidates(self, work: np.ndarray) -> np.ndarray:
        """Hough circles ``[n, 3]`` (x, y, r in work pixels, pixel-index coordinates)."""
        S = work.shape[0]
        gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (0, 0), self.BLUR_SIGMA)
        rmin = max(2, int(math.floor(self.r_min * S)))
        rmax = max(rmin + 1, int(math.ceil(self.r_max * S)))
        circles = cv2.HoughCircles(blur, cv2.HOUGH_GRADIENT, dp=self.HOUGH_DP,
                                   minDist=max(3.0, 0.5 * self.r_min * S),
                                   param1=self.HOUGH_PARAM1, param2=self.HOUGH_PARAM2,
                                   minRadius=rmin, maxRadius=rmax)
        if circles is None:
            return np.zeros((0, 3), np.float32)
        c = np.asarray(circles, np.float32).reshape(-1, 3)
        c = c[np.isfinite(c).all(axis=1) & (c[:, 2] > 0)]
        return c[: self.MAX_CANDIDATES]

    def _sample(self, img: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        """Bilinear samples of ``img`` at float coordinates (any shape) -> [..., C]."""
        shp = xs.shape
        mx = np.ascontiguousarray(xs.reshape(-1, shp[-1]), np.float32)
        my = np.ascontiguousarray(ys.reshape(-1, shp[-1]), np.float32)
        out = cv2.remap(img, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                        borderValue=0)
        if out.ndim == 2:
            out = out[:, :, None]
        return out.reshape(*shp, out.shape[-1])

    def _detect(self, minimap_bgr: np.ndarray) -> list[Detection]:
        bgr = _as_bgr(minimap_bgr)
        if bgr is None:
            return []
        S = self.WORK_SIZE
        work = cv2.resize(bgr, (S, S), interpolation=cv2.INTER_AREA
                          if min(bgr.shape[:2]) >= S else cv2.INTER_LINEAR) \
            if bgr.shape[:2] != (S, S) else bgr
        cand = self._candidates(work)
        try:
            extra = self._ring_candidates(work)
            if extra.size:
                cand = np.concatenate([extra, cand])[: self.MAX_CANDIDATES]
        except Exception:
            self._errors.exception("Ring-colour candidates failed")
        n = cand.shape[0]
        if n == 0:
            return []
        cx, cy, rh = cand[:, 0], cand[:, 1], cand[:, 2]
        K, N = self._steps.size, self.N_ANGLES

        # ---- ring: HSV samples on N rays x K radii ---------------------------------
        rad = rh[:, None] * self._steps[None, :]                          # [n, K]
        xs = cx[:, None, None] + rad[:, :, None] * self._cos[None, None, :]  # [n, K, N]
        ys = cy[:, None, None] + rad[:, :, None] * self._sin[None, None, :]
        valid = (xs >= -0.5) & (xs <= S - 0.5) & (ys >= -0.5) & (ys <= S - 0.5)
        samp = self._sample(work, xs, ys)                                   # [n, K, N, 3]
        hsv = cv2.cvtColor(samp.reshape(n * K, N, 3), cv2.COLOR_BGR2HSV).reshape(n, K, N, 3)
        lab = self._labels(samp, hsv) & valid[None]                         # [3, n, K, N]
        # tolerate small centre / radius errors: a ray counts at radius k if the colour is
        # present at k-1, k or k+1
        band = lab.copy()
        band[:, :, 1:] |= lab[:, :, :-1]
        band[:, :, :-1] |= lab[:, :, 1:]
        n_valid = valid.sum(axis=2).astype(np.float32)                      # [n, K]
        enough = (n_valid >= self.MIN_VALID_RAYS * N)[None]
        denom = np.maximum(n_valid, 1.0)[None]
        frac = np.where(enough, band.sum(axis=3) / denom, 0.0)              # [3, n, K]
        raw = np.where(enough, lab.sum(axis=3) / denom, 0.0)                # [3, n, K]
        best_k = frac.argmax(axis=2)                                        # [3, n]
        best = np.take_along_axis(frac, best_k[:, :, None], axis=2)[:, :, 0]  # [3, n]

        passing = np.nonzero(best.max(axis=0) >= self.MIN_COVERAGE)[0]
        reasons: dict[str, int] = {"coverage": int(n - passing.size)}
        dets: list[Detection] = []
        for i in passing:
            cov = best[:, i]
            ok = np.nonzero(cov >= self.MIN_COVERAGE)[0]
            c = int(ok[0])
            if ok.size > 1:
                # best coverage; near-ties (e.g. a recall halo around the ring): innermost wins
                top = ok[cov[ok] >= float(cov[ok].max()) - 0.05]
                c = int(top[np.argmin(best_k[top, i])])
            # banded coverage tolerates centre errors; the raw (single radius) coverage
            # rewards well-centred circles, so it dominates the score
            coverage = 0.35 * float(cov[c]) + 0.65 * float(raw[c, i].max())
            others = np.delete(frac[:, i, best_k[c, i]], c)   # other colours on the same ring
            if float(others.max()) > self.MAX_OTHER_COVERAGE:
                reasons["mixed"] = reasons.get("mixed", 0) + 1
                continue
            # radial extent of the ring colour (raw, unbanded profile)
            prof = raw[c, i]
            on = np.nonzero(prof >= 0.5 * max(float(prof.max()), 1e-6))[0]
            k_in, k_outer = int(on[0]), int(on[-1])
            r_outer = float(rad[i, k_outer])
            # the ring colour must stop just outside the ring (else: blob / coloured region)
            beyond = np.nonzero(rad[i] >= 1.2 * r_outer)[0]
            if beyond.size and float(raw[c, i, beyond[0]]) > self.MAX_OUTSIDE_COVERAGE:
                reasons["outside"] = reasons.get("outside", 0) + 1
                continue
            r_in = float(rad[i, k_in])
            d = self._finish(work, float(cx[i]), float(cy[i]), r_in, r_outer, c, coverage, cov,
                             reasons)
            if d is not None:
                dets.append(d)
        self.last_rejections = reasons   # diagnostics only (replaced atomically)
        return self._suppress(dets, 1.5 / S)

    def _suppress(self, dets: list[Detection], min_dist: float) -> list[Detection]:
        """Remove circles nested in a larger ring (portrait details), then near duplicates."""
        dets = sorted(dets, key=lambda d: -d.r)
        keep: list[Detection] = []
        for d in dets:
            nested = False
            for big in keep:
                dist = math.hypot(d.u - big.u, d.v - big.v)
                if d.r < self.NESTED_RATIO * big.r and dist + d.r <= 1.1 * big.r:
                    nested = True
                    break
            if not nested:
                keep.append(d)
        return _dedupe(keep, self.DUPLICATE_FRAC, min_dist, self.MAX_DET, use_max_radius=True)

    def _finish(self, work: np.ndarray, x: float, y: float, r_in: float, r_outer: float,
                c: int, coverage: float, cov: np.ndarray, reasons: dict[str, int]
                ) -> Detection | None:
        """Interior checks + Detection for one candidate (work-image pixel coordinates)."""
        S = work.shape[0]
        r_ref = 0.95 * r_in
        px = x + r_ref * self._inner[:, 0]
        py = y + r_ref * self._inner[:, 1]
        inside = (px >= -0.5) & (px <= S - 0.5) & (py >= -0.5) & (py <= S - 0.5)
        if inside.sum() < 0.4 * inside.size:
            reasons["frame"] = reasons.get("frame", 0) + 1
            return None
        samp = self._sample(work, px[None], py[None])[0][inside]          # [m, 3]
        gray = samp.astype(np.float32) @ np.asarray([0.114, 0.587, 0.299], np.float32)
        std = float(gray.std())
        if std < self.MIN_TEXTURE_STD:
            reasons["flat"] = reasons.get("flat", 0) + 1
            return None
        hsv = cv2.cvtColor(samp[None].astype(np.uint8), cv2.COLOR_BGR2HSV)[0]
        if float(self._labels(samp.astype(np.uint8), hsv)[c].mean()) > self.MAX_INSIDE_COVERAGE \
                and std < 2.5 * self.MIN_TEXTURE_STD:
            reasons["inside"] = reasons.get("inside", 0) + 1
            return None
        R = r_outer * self.OUTER_TO_RADIUS
        tex = min(1.0, std / 30.0)
        score = float(min(1.0, coverage * (0.75 + 0.25 * tex)))
        eps = 0.02
        p = np.asarray(cov, np.float64) + eps
        p[c] += 0.5          # the chosen colour dominates even when several are present
        probs = _normalize_probs(p)
        return Detection(u=(x + 0.5) / S, v=(y + 0.5) / S, r=R / S, score=score,
                         cls=CLASSES[c], cls_probs=probs)


# ======================================================================================
# Factory
# ======================================================================================


class _NullDetector(BaseDetector):
    """Last-resort detector (finds nothing) if even the classic one cannot be built."""

    name = "none"

    def detect(self, minimap_bgr: np.ndarray) -> list[Detection]:
        return []


class HybridDetector(BaseDetector):
    """Roster matcher first, the generic detector only for what the roster does not explain.

    With a roster (:meth:`set_roster`), :class:`treeaicoach.roster_matcher.RosterMatcher`
    finds the match's champions by their portraits (``alias`` set). The generic detector
    (ONNX or classic) still runs, every ``FALLBACK_EVERY`` frames, when some champions are
    not matched (unknown skin portrait, heavy occlusion...): its detections that do not
    overlap a roster match are added with ``alias=None``. The classic detector is fed the
    ring colours and the icon scale learned by the matcher (live calibration). Without a
    roster it is exactly the generic detector. Never raises.
    """

    FALLBACK_EVERY = 2
    EXTRA_MIN_SCORE = 0.6
    #: Extra detections this close (x the sum of radii) to a roster match are the same icon.
    EXTRA_OVERLAP = 0.8
    #: Extra detections this close (normalized) to a structure glyph or to a fountain are
    #: ignored: structure glyphs have team-coloured rings too, and a champion standing there
    #: is normally recognised by its portrait anyway.
    STRUCTURE_DIST = 0.04
    FOUNTAIN_DIST = 0.12

    def __init__(self, fallback: BaseDetector, matcher: Any) -> None:
        self.fallback = fallback
        self.matcher = matcher
        self._frame = 0
        self._last_extra: list[Detection] = []
        self._errors = _RateLimitedLog()
        self._structures: list[tuple[float, float, str]] | None = None
        self._fountains: list[tuple[float, float]] = []

    @property
    def name(self) -> str:  # type: ignore[override]
        fb = str(getattr(self.fallback, "name", "") or "none")
        return f"roster+{fb}" if self._has_roster() else fb

    def __getattr__(self, item: str) -> Any:
        # attributes of the generic detector (threshold, model_path...) stay reachable
        if item in ("fallback", "matcher"):
            raise AttributeError(item)
        return getattr(self.fallback, item)

    def _has_roster(self) -> bool:
        try:
            return bool(self.matcher is not None and self.matcher.has_roster)
        except Exception:
            return False

    def set_roster(self, game: Any) -> None:
        """Roster of the current game (``GameInfo``; None clears it). Never raises."""
        try:
            if self.matcher is not None:
                self.matcher.set_roster(game)
            self._last_extra = []
        except Exception:
            self._errors.exception("Hybrid detector set_roster failed")

    def set_game_status(self, game: Any) -> None:
        """Live game state (dead champions) for the roster matcher. Never raises."""
        try:
            fn = getattr(self.matcher, "set_game_status", None)
            if callable(fn):
                fn(game)
        except Exception:
            self._errors.exception("Hybrid detector set_game_status failed")

    def _structure_uv(self) -> list[tuple[float, float, str]]:
        if self._structures is None:
            try:
                from treeaicoach.render import iter_structures

                from treeaicoach.render import game_to_uv

                self._structures = [(float(u), float(v), str(t))
                                    for _, u, v, _, t in iter_structures()]
                self._fountains = [game_to_uv(394, 461), game_to_uv(14340, 14390)]
            except Exception:
                self._structures, self._fountains = [], []
        return self._structures

    def _extras(self, img: np.ndarray, dets: list[Detection]) -> list[Detection]:
        m = self.matcher
        if isinstance(self.fallback, ClassicDetector):
            self.fallback.set_ring_colors(getattr(m, "ring_colors", None))
            self.fallback.set_scale(getattr(m, "scale", None))
        n_roster = len(getattr(m, "entries", ()) or ())
        # dead champions (Live API) have no icon: they leave no free slot for an extra
        dead = set(getattr(m, "last_dead", None) or ())
        if n_roster and len(dets) + len(dead) >= n_roster:
            self._last_extra = []
            return []
        self._frame += 1
        if self._frame % self.FALLBACK_EVERY == 1 or self.FALLBACK_EVERY <= 1:
            raw = [d for d in (self.fallback.detect(img) or []) if d.score >= self.EXTRA_MIN_SCORE]
            structs = self._structure_uv()
            game = getattr(m, "_game", None)
            my_team = str(getattr(getattr(game, "me", None), "team", "") or "")
            keep = []
            for d in raw:
                # a ring of the structure's own colour on a structure glyph: the glyph itself
                # (an icon of the other colour there is a champion standing on it)
                if any(math.hypot(d.u - u, d.v - v) < self.STRUCTURE_DIST
                       and (not my_team or (d.cls == "enemy") == (t != my_team))
                       for u, v, t in structs):
                    continue
                if any(math.hypot(d.u - u, d.v - v) < self.FOUNTAIN_DIST
                       for u, v in self._fountains):
                    continue
                keep.append(d)
            self._last_extra = keep
        # at most as many extras per side as roster champions of that side not matched
        ents = getattr(m, "entries", ()) or ()
        n_enemy = sum(1 for e in ents if getattr(e, "relation", "") == "enemy"
                      and getattr(e, "alias", None) not in dead)
        n_ally = sum(1 for e in ents if getattr(e, "relation", "") != "enemy"
                     and getattr(e, "alias", None) not in dead)
        free = {"enemy": n_enemy - sum(1 for k in dets if k.cls == "enemy"),
                "ally": n_ally - sum(1 for k in dets if k.cls != "enemy")}
        out = []
        for d in sorted(self._last_extra, key=lambda x: -x.score):
            side = "enemy" if d.cls == "enemy" else "ally"
            if free[side] <= 0:
                continue
            if any(math.hypot(d.u - k.u, d.v - k.v) < self.EXTRA_OVERLAP * (d.r + k.r)
                   for k in dets + out):
                continue
            free[side] -= 1
            out.append(Detection(u=d.u, v=d.v, r=d.r, score=d.score, cls=d.cls,
                                 cls_probs=d.cls_probs, alias=None))
        return out

    def detect(self, minimap_bgr: np.ndarray) -> list[Detection]:
        """Roster matches (+ unexplained generic detections); [] on error. Never raises."""
        try:
            if not self._has_roster():
                return list(self.fallback.detect(minimap_bgr) or [])
            dets = list(self.matcher.detect(minimap_bgr) or [])
            img = _as_bgr(minimap_bgr)
            if img is None:
                return dets
            try:
                dets += self._extras(img, dets)
            except Exception:
                self._errors.exception("Hybrid detector fallback failed")
            return dets
        except Exception:
            self._errors.exception("Hybrid detection failed")
            return []

    def close(self) -> None:
        try:
            self.fallback.close()
        except Exception:
            pass


def _generic_detector(b: str, threshold: float) -> BaseDetector:
    if b in ("auto", "onnx"):
        try:
            model = default_model_path()
            if not model.is_file():
                log.info("No ONNX model at %s: using the classic detector", model)
            else:
                det = OnnxDetector(model, threshold=threshold)
                log.info("Detector: ONNX (%s)", model.name)
                return det
        except Exception as exc:
            log.warning("ONNX detector unavailable (%s): using the classic detector", exc,
                        exc_info=log.isEnabledFor(logging.DEBUG))
    try:
        det_c = ClassicDetector()
        log.info("Detector: classic (Hough circles + ring colour)")
        return det_c
    except Exception:
        log.exception("Classic detector unavailable: detection disabled")
        return _NullDetector()


def create_detector(backend: str = "auto", threshold: float = 0.0, *, db: Any = None,
                    scale_store: dict | None = None,
                    on_scale: Any = None, roster: bool = True,
                    learn_cache: Any = None) -> BaseDetector:
    """Build the detector for ``backend`` ("auto" | "onnx" | "classic"). Never raises.

    "auto" and "onnx" try the bundled ONNX model and fall back to :class:`ClassicDetector`
    (with a logged reason). ``threshold`` 0 = value from ``model_meta.json`` (ONNX only).
    With ``roster`` (default) the result is a :class:`HybridDetector`: once the engine gives
    it the game's roster (``set_roster``) it finds the 10 champions by their portraits
    (:mod:`treeaicoach.roster_matcher`, champion icons from ``db``; icon scale prior /
    persistence in ``scale_store`` + ``on_scale(key, ratio)``; ``learn_cache``: where the
    learned icon of a custom skin is kept, see self_icon.py), and behaves exactly like
    the generic detector otherwise.
    """
    try:
        b = str(backend or "auto").strip().lower()
    except Exception:
        b = "auto"
    if b not in ("auto", "onnx", "classic"):
        log.warning("Unknown detector backend %r: using auto", backend)
        b = "auto"
    base = _generic_detector(b, threshold)
    if not roster:
        return base
    try:
        from treeaicoach.roster_matcher import RosterMatcher

        matcher = RosterMatcher(db=db, scale_store=scale_store, on_scale=on_scale,
                                learn_cache=learn_cache)
        return HybridDetector(base, matcher)
    except Exception:
        log.exception("Roster matcher unavailable: generic detector only")
        return base


__all__ = [
    "CLASSES", "Detection", "BaseDetector", "OnnxDetector", "ClassicDetector", "HybridDetector",
    "preprocess", "decode_outputs", "create_detector", "ring_color_labels",
    "default_model_path", "default_meta_path", "load_model_meta",
]

