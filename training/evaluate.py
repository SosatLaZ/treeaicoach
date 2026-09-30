"""Evaluate the exported detector through the RUNTIME path, and build the app selftest set.

Usage (from the repository root)::

    python -m training.evaluate                                  # synthetic val set (600 images)
    python -m training.evaluate --real-dir path/to/crops         # + real crops with labels.json
    python -m training.evaluate --make-selftest                  # write treeaicoach/assets/selftest

The runtime path is ``treeaicoach.detector.OnnxDetector`` (preprocess -> onnxruntime ->
decode_outputs), exactly what the app runs. If that module is unavailable, a local
implementation of the documented contract (ARCHITECTURE §4.9, :func:`decode_numpy`) is
used instead and the report says so.

Metrics: a detection matches a ground-truth icon when their centres are closer than
``0.5 * r_gt`` (greedy one-to-one matching by descending score). Precision / recall / F1,
class accuracy (on matched icons whose class is known; "self" counts as "ally"), mean
position error in % of the minimap width, per-class numbers and a threshold sweep.

Real crops: a directory with ``labels.json`` = ``[{"file": ..., "icons": [{"x", "y", "r",
"team"}]}]`` in pixels (``team``: enemy | ally | self | anything else = unknown class;
``"occluded": true`` icons are "don't care": neither a miss nor a false positive).

The pure-numpy helpers (:func:`decode_numpy`, :func:`match_image`, :class:`MetricAccumulator`)
are also used by ``train.py`` for its periodic validation. This module does not need torch.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, NamedTuple, Sequence

import cv2
import numpy as np

if __package__ in (None, ""):  # allow "python training/evaluate.py"
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from training.dataset import (  # noqa: E402
    CLASSES,
    INPUT_SIZE,
    STRIDE,
    VAL_SEED_DEFAULT,
    build_validation_set,
    normalize_labels,
)

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = REPO_ROOT / "treeaicoach" / "assets" / "model"
SELFTEST_DIR = REPO_ROOT / "treeaicoach" / "assets" / "selftest"
MATCH_FRAC = 0.5                       # match if centre distance < MATCH_FRAC * r_gt
DUP_FRAC = 0.6                         # decode: drop peaks closer than DUP_FRAC * r to a better one
SWEEP = tuple(round(t, 2) for t in np.arange(0.10, 0.91, 0.05))
SWEEP_MIN = 0.05
GROUPS = ("enemy", "ally")             # evaluation classes ("self" merged into "ally")
SELFTEST_SIZES = (224, 240, 256, 272, 288, 312, 336, 360)


# ======================================================================================
# Contract decode (pure numpy)
# ======================================================================================


class Det(NamedTuple):
    """Detection with the same fields as ``treeaicoach.detector.Detection``."""

    u: float
    v: float
    r: float
    score: float
    cls: str
    cls_probs: tuple[float, float, float]


def _maxpool3(a: np.ndarray) -> np.ndarray:
    """3x3 max filter with -inf padding (same size)."""
    p = np.pad(a, 1, mode="constant", constant_values=-np.inf)
    h, w = a.shape
    out = p[0:h, 0:w].copy()
    for dy in range(3):
        for dx in range(3):
            np.maximum(out, p[dy:dy + h, dx:dx + w], out=out)
    return out


def decode_numpy(heatmap: np.ndarray, cls: np.ndarray, offset: np.ndarray, radius: np.ndarray,
                 threshold: float, stride: int = STRIDE, input_size: int = INPUT_SIZE,
                 max_det: int = 20, dup_frac: float = DUP_FRAC) -> list[Det]:
    """Decode one image's outputs per ARCHITECTURE §4.9 (shapes ``[1,C,H,W]`` or ``[C,H,W]``).

    3x3 max-pool NMS, peaks > ``threshold``, ``u = (x + off_x) * stride / S`` (same for v),
    ``r = radius``; best ``max_det`` by score; a peak closer than ``dup_frac * r_kept``
    (at least one cell) to a better kept one is dropped - the same rules as
    ``treeaicoach.detector.decode_outputs``.
    """
    hm = np.nan_to_num(np.asarray(heatmap, np.float32).reshape(-1, *np.shape(heatmap)[-2:])[0])
    cl = np.asarray(cls, np.float32).reshape(-1, *np.shape(cls)[-2:])
    off = np.asarray(offset, np.float32).reshape(-1, *np.shape(offset)[-2:])
    rad = np.asarray(radius, np.float32).reshape(-1, *np.shape(radius)[-2:])[0]
    keep = (hm >= _maxpool3(hm)) & (hm > threshold)
    ys, xs = np.nonzero(keep)
    if ys.size == 0:
        return []
    scores = hm[ys, xs]
    order = np.argsort(-scores, kind="stable")
    min_dist = 0.99 * stride / input_size
    dets: list[Det] = []
    for i in order:
        y, x = int(ys[i]), int(xs[i])
        u = min(1.0, max(0.0, (x + min(1.0, max(0.0, float(off[0, y, x])))) * stride / input_size))
        v = min(1.0, max(0.0, (y + min(1.0, max(0.0, float(off[1, y, x])))) * stride / input_size))
        r = min(1.0, max(0.0, float(rad[y, x])))
        if dup_frac > 0 and any(math.hypot(u - d.u, v - d.v) < max(dup_frac * d.r, min_dist)
                                for d in dets):
            continue
        probs = tuple(float(p) for p in cl[:, y, x][:3])
        probs = probs + (0.0,) * (3 - len(probs))
        dets.append(Det(u, v, r, float(scores[i]), CLASSES[int(np.argmax(probs))], probs))  # type: ignore[arg-type]
        if len(dets) >= max_det:
            break
    return dets


# ======================================================================================
# Matching and metrics
# ======================================================================================


def eval_group(cls: str | None) -> str | None:
    """Evaluation group of a class name ("self" -> "ally"); None if unknown."""
    if cls in ("ally", "self"):
        return "ally"
    if cls == "enemy":
        return "enemy"
    return None


@dataclass
class GT:
    """Ground-truth icon for evaluation."""

    u: float
    v: float
    r: float
    group: str | None          # "enemy" | "ally" | None (class unknown / not supervised)
    ignore: bool = False       # occluded: neither a miss nor a false positive


def gts_from_labels(labels: Iterable[dict]) -> list[GT]:
    """Synthetic labels ``{"u","v","r","cls","cls_valid"}`` -> :class:`GT` list."""
    out = []
    for lab in normalize_labels(list(labels)):
        out.append(GT(lab["u"], lab["v"], lab["r"], eval_group(lab["cls"]) if lab["cls_valid"] else None))
    return out


def match_image(dets: Sequence[Any], gts: Sequence[GT], frac: float = MATCH_FRAC
                ) -> list[tuple[int, int | None]]:
    """Greedy matching by descending score. Returns ``(det index, gt index or None)`` pairs,
    in descending score order. A detection is matched to the nearest free GT with
    ``distance < frac * r_gt``."""
    order = sorted(range(len(dets)), key=lambda i: -float(dets[i].score))
    used = [False] * len(gts)
    pairs: list[tuple[int, int | None]] = []
    for i in order:
        d = dets[i]
        best, best_d = None, math.inf
        for j, g in enumerate(gts):
            if used[j]:
                continue
            dist = math.hypot(float(d.u) - g.u, float(d.v) - g.v)
            if dist < frac * g.r and dist < best_d:
                best, best_d = j, dist
        if best is not None:
            used[best] = True
        pairs.append((i, best))
    return pairs


@dataclass
class MetricAccumulator:
    """Collects matched detections of many images; metrics for any threshold afterwards."""

    frac: float = MATCH_FRAC
    scores: list[float] = field(default_factory=list)
    tp: list[bool] = field(default_factory=list)
    ignored: list[bool] = field(default_factory=list)
    pred_group: list[str | None] = field(default_factory=list)
    gt_group: list[str | None] = field(default_factory=list)
    pos_err: list[float] = field(default_factory=list)
    r_err: list[float] = field(default_factory=list)
    n_gt: int = 0
    n_gt_group: dict[str, int] = field(default_factory=lambda: {g: 0 for g in GROUPS})
    n_images: int = 0

    def add(self, dets: Sequence[Any], gts: Sequence[GT]) -> None:
        """Add one image (``dets`` should be decoded at a low threshold for sweeps)."""
        self.n_images += 1
        for g in gts:
            if not g.ignore:
                self.n_gt += 1
                if g.group in self.n_gt_group:
                    self.n_gt_group[g.group] += 1
        for i, j in match_image(dets, gts, self.frac):
            d = dets[i]
            g = gts[j] if j is not None else None
            self.scores.append(float(d.score))
            self.tp.append(g is not None and not g.ignore)
            self.ignored.append(g is not None and g.ignore)
            self.pred_group.append(eval_group(str(d.cls)))
            self.gt_group.append(g.group if g is not None else None)
            self.pos_err.append(math.hypot(float(d.u) - g.u, float(d.v) - g.v) if g else math.nan)
            self.r_err.append(abs(float(d.r) - g.r) if g else math.nan)

    def metrics(self, threshold: float) -> dict[str, Any]:
        """Precision / recall / F1 / class accuracy / errors for detections >= threshold."""
        s = np.asarray(self.scores, np.float64)
        sel = s >= threshold
        tp = np.asarray(self.tp, bool) & sel
        ign = np.asarray(self.ignored, bool) & sel
        n_tp = int(tp.sum())
        n_det = int(sel.sum() - ign.sum())
        prec = n_tp / n_det if n_det else (1.0 if self.n_gt == 0 else 0.0)
        rec = n_tp / self.n_gt if self.n_gt else 1.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec > 0 else 0.0
        pg = np.asarray(self.pred_group, object)
        gg = np.asarray(self.gt_group, object)
        known = tp & np.asarray([g is not None for g in self.gt_group], bool)
        n_known = int(known.sum())
        cls_ok = int((known & (pg == gg)).sum())
        pe = np.asarray(self.pos_err, np.float64)[tp]
        re = np.asarray(self.r_err, np.float64)[tp]
        per_class: dict[str, dict[str, float | int]] = {}
        for grp in GROUPS:
            gt_c = tp & (gg == grp)
            pred_c = sel & ~ign & (pg == grp)
            n_pred_c = int(pred_c.sum())
            per_class[grp] = {
                "gt": self.n_gt_group[grp],
                "recall": float(gt_c.sum() / self.n_gt_group[grp]) if self.n_gt_group[grp] else math.nan,
                "precision": float((pred_c & tp).sum() / n_pred_c) if n_pred_c else math.nan,
                "cls_acc": float((gt_c & (pg == grp)).sum() / gt_c.sum()) if gt_c.sum() else math.nan,
            }
        return {
            "threshold": float(threshold), "images": self.n_images, "gt": self.n_gt,
            "det": n_det, "tp": n_tp, "precision": prec, "recall": rec, "f1": f1,
            "cls_acc": cls_ok / n_known if n_known else math.nan,
            "pos_err_pct": float(pe.mean() * 100.0) if pe.size else math.nan,
            "r_err_pct": float(re.mean() * 100.0) if re.size else math.nan,
            "per_class": per_class,
        }

    def sweep(self, thresholds: Sequence[float] = SWEEP) -> list[dict[str, Any]]:
        """Metrics for each threshold."""
        return [self.metrics(t) for t in thresholds]

    def best(self, thresholds: Sequence[float] = SWEEP) -> dict[str, Any]:
        """Metrics at the threshold with the best F1 (ties -> higher threshold)."""
        rows = self.sweep(thresholds)
        return max(rows, key=lambda m: (round(m["f1"], 6), m["threshold"]))


def format_metrics(m: dict[str, Any], title: str = "") -> str:
    """One-block human-readable summary."""
    lines = []
    if title:
        lines.append(title)
    lines.append(
        f"  thr {m['threshold']:.2f} | P {m['precision']:.3f} R {m['recall']:.3f} F1 {m['f1']:.3f}"
        f" | cls acc {m['cls_acc']:.3f} | pos err {m['pos_err_pct']:.2f} % | r err "
        f"{m['r_err_pct']:.2f} % | {m['tp']}/{m['gt']} icons, {m['det']} dets, {m['images']} images")
    for grp, pc in m["per_class"].items():
        lines.append(f"    {grp:6s} gt {pc['gt']:5d} | recall {pc['recall']:.3f}"
                     f" | precision {pc['precision']:.3f} | cls acc {pc['cls_acc']:.3f}")
    return "\n".join(lines)


def format_sweep(rows: Sequence[dict[str, Any]]) -> str:
    """Threshold sweep table."""
    out = ["  thr    P      R      F1     clsacc"]
    for m in rows:
        out.append(f"  {m['threshold']:.2f}  {m['precision']:.3f}  {m['recall']:.3f}  {m['f1']:.3f}"
                   f"  {m['cls_acc']:.3f}")
    return "\n".join(out)


# ======================================================================================
# Runtime detector
# ======================================================================================


class ContractOnnxDetector:
    """Stand-in for ``treeaicoach.detector.OnnxDetector`` following ARCHITECTURE §4.9."""

    name = "onnx-contract"

    def __init__(self, model_path: Path, meta_path: Path | None = None, threshold: float = 0.0,
                 threads: int = 2) -> None:
        import onnxruntime as ort  # noqa: PLC0415

        meta = {}
        mp = Path(meta_path) if meta_path else Path(model_path).with_name("model_meta.json")
        if mp.is_file():
            meta = json.loads(mp.read_text(encoding="utf-8"))
        self.input_size = int(meta.get("input_size", INPUT_SIZE))
        self.stride = int(meta.get("stride", STRIDE))
        self.threshold = float(threshold) if threshold > 0 else float(meta.get("threshold", 0.35))
        so = ort.SessionOptions()
        so.intra_op_num_threads = max(1, int(threads))
        so.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model_path), so, providers=["CPUExecutionProvider"])

    def detect(self, minimap_bgr: np.ndarray) -> list[Det]:
        """Detect icons on a BGR minimap of any size."""
        img = cv2.resize(minimap_bgr, (self.input_size, self.input_size), interpolation=cv2.INTER_AREA)
        x = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        hm, cl, off, rad = self.session.run(["heatmap", "cls", "offset", "radius"], {"input": x})
        return decode_numpy(hm, cl, off, rad, self.threshold, self.stride, self.input_size)


def load_runtime_detector(model_path: Path, meta_path: Path | None, threshold: float,
                          threads: int = 2, allow_fallback: bool = True) -> tuple[Any, str]:
    """``(detector, description)``: the app's OnnxDetector, else the contract stand-in."""
    try:
        from treeaicoach.detector import OnnxDetector  # noqa: PLC0415

        det = OnnxDetector(model_path=Path(model_path), meta_path=Path(meta_path) if meta_path else None,
                           threshold=threshold, threads=threads)
        return det, "treeaicoach.detector.OnnxDetector (runtime path)"
    except ImportError as exc:
        if not allow_fallback:
            raise
        log.warning("treeaicoach.detector unavailable (%s): using the contract stand-in", exc)
    return (ContractOnnxDetector(Path(model_path), meta_path, threshold, threads),
            "training.evaluate.ContractOnnxDetector (runtime module unavailable)")


def evaluate_images(detector: Any, images: Iterable[np.ndarray], gts: Iterable[Sequence[GT]],
                    frac: float = MATCH_FRAC) -> tuple[MetricAccumulator, float]:
    """Run ``detector.detect`` on every image; returns (accumulator, mean ms per image)."""
    acc = MetricAccumulator(frac=frac)
    t_total, n = 0.0, 0
    for img, g in zip(images, gts):
        t0 = time.perf_counter()
        dets = detector.detect(img)
        t_total += time.perf_counter() - t0
        n += 1
        acc.add(dets, list(g))
    return acc, (1000.0 * t_total / n if n else 0.0)


# ======================================================================================
# Real crops
# ======================================================================================


def read_image(path: Path) -> np.ndarray | None:
    """Unicode-safe ``cv2.imread`` (BGR)."""
    try:
        data = np.fromfile(str(path), np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
    except (OSError, cv2.error):
        return None
    return img


def load_real_crops(directory: Path) -> tuple[list[np.ndarray], list[list[GT]], list[str]]:
    """Images + normalized GTs of a ``labels.json`` directory (pixel coordinates)."""
    directory = Path(directory)
    entries = json.loads((directory / "labels.json").read_text(encoding="utf-8"))
    images, gts, names = [], [], []
    for e in entries:
        img = read_image(directory / str(e.get("file", "")))
        if img is None:
            log.warning("Unreadable crop %s", e.get("file"))
            continue
        h, w = img.shape[:2]
        g = []
        for ic in e.get("icons", []):
            try:
                x, y, r = float(ic["x"]), float(ic["y"]), float(ic["r"])
            except (KeyError, TypeError, ValueError):
                continue
            g.append(GT(x / w, y / h, r / w, eval_group(str(ic.get("team", ""))),
                        ignore=bool(ic.get("occluded", False))))
        images.append(img)
        gts.append(g)
        names.append(str(e.get("file")))
    return images, gts, names


# ======================================================================================
# Selftest set
# ======================================================================================


def _selftest_ok(labels: list[dict]) -> bool:
    """Easy, unambiguous scene: 3-10 separated icons fully inside, known classes."""
    if not 3 <= len(labels) <= 10:
        return False
    for lab in labels:
        if not lab["cls_valid"]:
            return False
        if not (1.1 * lab["r"] <= lab["u"] <= 1 - 1.1 * lab["r"]
                and 1.1 * lab["r"] <= lab["v"] <= 1 - 1.1 * lab["r"]):
            return False
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            if math.hypot(a["u"] - b["u"], a["v"] - b["v"]) < 1.05 * (a["r"] + b["r"]):
                return False
    groups = {eval_group(lab["cls"]) for lab in labels}
    return groups == {"enemy", "ally"}


def make_selftest(out_dir: Path = SELFTEST_DIR, count: int = 8, seed: int = 4242,
                  sizes: Sequence[int] = SELFTEST_SIZES, generator: Any = None) -> Path:
    """Write ``count`` synthetic minimaps at native sizes + ``labels.json``; returns its path.

    ``labels.json`` = ``[{"file", "size", "icons": [{"u","v","r","cls","cls_valid"}]}]``
    (normalized by the image width, like the detector outputs).
    """
    from training.dataset import default_generator  # noqa: PLC0415

    gen = generator or default_generator()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("selftest_*.png"):
        old.unlink()
    rng = np.random.default_rng([0x5E1F, seed])
    entries = []
    for k in range(count):
        size = int(sizes[k % len(sizes)])
        for _attempt in range(400):
            img, labels = gen(rng, size)
            labels = normalize_labels(labels)
            if _selftest_ok(labels):
                break
        name = f"selftest_{k + 1:02d}.png"
        ok, buf = cv2.imencode(".png", img)
        if not ok:
            raise RuntimeError(f"cannot encode {name}")
        buf.tofile(str(out_dir / name))
        entries.append({"file": name, "size": size, "icons": [
            {"u": round(lab["u"], 5), "v": round(lab["v"], 5), "r": round(lab["r"], 5),
             "cls": "ally" if lab["cls"] == "self" else lab["cls"], "cls_valid": lab["cls_valid"]}
            for lab in labels]})
    path = out_dir / "labels.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entries, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_selftest(directory: Path = SELFTEST_DIR) -> tuple[list[np.ndarray], list[list[GT]]]:
    """Images + GTs of a selftest directory written by :func:`make_selftest`."""
    entries = json.loads((Path(directory) / "labels.json").read_text(encoding="utf-8"))
    images, gts = [], []
    for e in entries:
        img = read_image(Path(directory) / e["file"])
        if img is None:
            continue
        images.append(img)
        gts.append(gts_from_labels(e["icons"]))
    return images, gts


# ======================================================================================
# CLI
# ======================================================================================


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Evaluation driven by parsed CLI arguments; returns the report dict."""
    report: dict[str, Any] = {}
    if args.make_selftest:
        path = make_selftest(Path(args.selftest_dir), args.selftest_count, args.selftest_seed)
        print(f"Selftest set written: {path}")
        report["selftest_labels"] = str(path)
    model = Path(args.model)
    if not model.is_file():
        print(f"No model at {model}: nothing to evaluate.")
        return report
    meta = Path(args.meta) if args.meta else model.with_name("model_meta.json")
    meta_thr = 0.35
    if meta.is_file():
        meta_thr = float(json.loads(meta.read_text(encoding="utf-8")).get("threshold", meta_thr))
    thr = args.threshold if args.threshold > 0 else meta_thr
    detector, desc = load_runtime_detector(model, meta if meta.is_file() else None,
                                           min(SWEEP_MIN, thr), args.threads)
    print(f"Detector: {desc}\nModel: {model}\nOperating threshold: {thr:.2f}")
    report.update({"detector": desc, "model": str(model), "threshold": thr})

    def section(name: str, images: list[np.ndarray], gts: list[list[GT]]) -> None:
        acc, ms = evaluate_images(detector, images, gts)
        at = acc.metrics(thr)
        best = acc.best()
        print(f"\n== {name}: {len(images)} images, {acc.n_gt} icons, {ms:.1f} ms/image ==")
        print(format_metrics(at, "At the operating threshold:"))
        print(format_metrics(best, "Best F1 threshold:"))
        print("Threshold sweep:\n" + format_sweep(acc.sweep()))
        report[name] = {"at_threshold": at, "best": best, "ms_per_image": ms}

    if args.val_size > 0:
        vs = build_validation_set(args.val_size, args.val_seed,
                                  cache_dir=None if args.no_cache else Path(args.cache_dir))
        section("synthetic", list(vs.images), [gts_from_labels(ls) for ls in vs.labels])
    if args.real_dir:
        rd = Path(args.real_dir)
        if (rd / "labels.json").is_file():
            images, gts, _names = load_real_crops(rd)
            section("real", images, gts)
        else:
            print(f"\nNo labels.json in {rd}: real crops skipped.")
    st = Path(args.selftest_dir)
    if (st / "labels.json").is_file():
        images, gts = load_selftest(st)
        section("selftest", images, gts)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=1, default=float), encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    """Command-line interface."""
    p = argparse.ArgumentParser(description="Evaluate the minimap detector (runtime path).")
    p.add_argument("--model", default=str(MODEL_DIR / "minimap_detector.onnx"))
    p.add_argument("--meta", default="", help="model_meta.json (default: next to the model)")
    p.add_argument("--threshold", type=float, default=0.0, help="0 = threshold of model_meta.json")
    p.add_argument("--val-size", type=int, default=600, help="synthetic validation images (0 = skip)")
    p.add_argument("--val-seed", type=int, default=VAL_SEED_DEFAULT)
    p.add_argument("--cache-dir", default=str(REPO_ROOT / "training" / "data"))
    p.add_argument("--no-cache", action="store_true", help="do not read/write the val-set cache")
    p.add_argument("--real-dir", default="", help="directory of real crops with labels.json")
    p.add_argument("--threads", type=int, default=2)
    p.add_argument("--make-selftest", action="store_true", help="write the app selftest set first")
    p.add_argument("--selftest-dir", default=str(SELFTEST_DIR))
    p.add_argument("--selftest-count", type=int, default=8)
    p.add_argument("--selftest-seed", type=int, default=4242)
    p.add_argument("--json-out", default="", help="also write the report as JSON")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run(build_parser().parse_args(argv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
