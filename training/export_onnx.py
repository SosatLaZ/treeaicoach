"""Export a MinimapNet checkpoint (EMA weights) to the ONNX model bundled with the app.

Usage (from the repository root)::

    python -m training.export_onnx --ckpt training/runs/main/best.pt --version 2026-10-01

Writes ``treeaicoach/assets/model/minimap_detector.onnx`` and ``model_meta.json`` following
the contract of ARCHITECTURE §4.9: input ``input`` float32 ``[1,3,256,256]`` (RGB 0..1),
outputs ``heatmap`` (sigmoid), ``cls`` (softmax), ``offset`` (sigmoid), ``radius`` (>= 0),
opset 17, BatchNorm folded (constant folding). The exported graph is checked with
``onnx.checker``, its operators are checked against a small allow-list, and onnxruntime is
compared with torch on random and synthetic inputs (max abs diff < 1e-3) before the files
are (atomically) replaced.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import io
import json
import logging
import os
import sys
import warnings
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import torch

if __package__ in (None, ""):  # allow "python training/export_onnx.py"
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from training.model import CLASSES, INPUT_SIZE, STRIDE, MinimapNet, build_model, count_parameters  # noqa: E402

log = logging.getLogger("training.export_onnx")

REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = REPO_ROOT / "treeaicoach" / "assets" / "model"
MODEL_FILE = "minimap_detector.onnx"
META_FILE = "model_meta.json"
OPSET = 17
OUTPUT_NAMES = ("heatmap", "cls", "offset", "radius")
ALLOWED_OPS = frozenset({"Conv", "BatchNormalization", "Relu", "Clip", "Add", "Concat", "Resize",
                         "Sigmoid", "Softmax", "Constant", "Identity", "Mul"})
MAX_ABS_DIFF = 1e-3
MAX_MODEL_BYTES = 4 * 1024 * 1024


class ExportError(RuntimeError):
    """The exported model failed a check."""


def load_for_export(ckpt_path: Path, use_ema: bool = True) -> tuple[MinimapNet, dict[str, Any]]:
    """Model (eval mode, EMA weights when available) + the checkpoint dict."""
    ck = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    model = build_model(ck.get("model_config"))
    state = None
    if use_ema and isinstance(ck.get("ema"), dict):
        state = ck["ema"].get("module")
    if state is None:
        state = ck.get("model", ck)
    model.load_state_dict(state)
    model.eval()
    return model, ck


def export_bytes(model: MinimapNet, input_size: int = INPUT_SIZE) -> bytes:
    """ONNX bytes (opset 17, static shape ``[1,3,S,S]``, constant folding)."""
    model = model.eval()
    x = torch.zeros(1, 3, input_size, input_size)
    buf = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # legacy-exporter deprecation notices
        torch.onnx.export(model, (x,), buf, input_names=["input"], output_names=list(OUTPUT_NAMES),
                          opset_version=OPSET, do_constant_folding=True, dynamo=False)
    return buf.getvalue()


def check_graph(data: bytes) -> dict[str, Any]:
    """``onnx.checker`` + operator allow-list + I/O names and shapes. Returns a summary."""
    import onnx  # noqa: PLC0415

    m = onnx.load_from_string(data)
    onnx.checker.check_model(m)
    ops = sorted({n.op_type for n in m.graph.node})
    bad = [o for o in ops if o not in ALLOWED_OPS]
    if bad:
        raise ExportError(f"operators outside the allow-list: {bad}")
    ins = [i.name for i in m.graph.input]
    outs = [o.name for o in m.graph.output]
    if ins != ["input"] or outs != list(OUTPUT_NAMES):
        raise ExportError(f"unexpected I/O names {ins} -> {outs}")
    opset = max((o.version for o in m.opset_import if o.domain in ("", "ai.onnx")), default=0)
    return {"ops": ops, "nodes": len(m.graph.node), "opset": int(opset)}


def verify_onnxruntime(data: bytes, model: MinimapNet, inputs: Sequence[np.ndarray],
                       threads: int = 2) -> float:
    """Max abs difference between onnxruntime and torch over ``inputs`` ([1,3,S,S] float32)."""
    import onnxruntime as ort  # noqa: PLC0415

    so = ort.SessionOptions()
    so.intra_op_num_threads = max(1, threads)
    sess = ort.InferenceSession(data, so, providers=["CPUExecutionProvider"])
    worst = 0.0
    with torch.inference_mode():
        for x in inputs:
            ref = [t.numpy() for t in model(torch.from_numpy(x))]
            got = sess.run(list(OUTPUT_NAMES), {"input": x})
            for name, a, b in zip(OUTPUT_NAMES, ref, got):
                if a.shape != b.shape:
                    raise ExportError(f"{name}: onnxruntime shape {b.shape} != torch {a.shape}")
                worst = max(worst, float(np.abs(a - b).max()))
    return worst


def verification_inputs(input_size: int = INPUT_SIZE, n_synth: int = 3, seed: int = 0) -> list[np.ndarray]:
    """Random tensors + a few synthetic minimaps (when the generator is available)."""
    rng = np.random.default_rng(seed)
    xs = [rng.random((1, 3, input_size, input_size), dtype=np.float32) for _ in range(2)]
    try:
        from training.dataset import default_generator, to_input  # noqa: PLC0415

        gen = default_generator()
        for _ in range(n_synth):
            img, _labels = gen(rng, input_size)
            xs.append(to_input(img, input_size)[None])
    except Exception as exc:  # synth unavailable: random inputs are enough for the check
        log.warning("Synthetic verification inputs unavailable: %s", exc)
    return xs


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _clean_metrics(m: Any) -> Any:
    """JSON-friendly copy of a metrics dict (NaN -> None, floats rounded)."""
    if isinstance(m, dict):
        return {str(k): _clean_metrics(v) for k, v in m.items() if k != "sweep"}
    if isinstance(m, (list, tuple)):
        return [_clean_metrics(v) for v in m]
    if isinstance(m, (float, np.floating)):
        f = float(m)
        return None if f != f else round(f, 5)
    if isinstance(m, (np.integer,)):
        return int(m)
    return m


def export(ckpt: Path, out_dir: Path = MODEL_DIR, version: str | None = None,
           threshold: float | None = None, use_ema: bool = True, threads: int = 2) -> dict[str, Any]:
    """Export + verify + write the model and its metadata. Returns the metadata dict."""
    model, ck = load_for_export(Path(ckpt), use_ema)
    input_size = int(ck.get("input_size", INPUT_SIZE))
    data = export_bytes(model, input_size)
    graph = check_graph(data)
    if len(data) > MAX_MODEL_BYTES:
        raise ExportError(f"model is {len(data) / 1e6:.2f} MB (> 4 MB)")
    diff = verify_onnxruntime(data, model, verification_inputs(input_size), threads)
    if not diff < MAX_ABS_DIFF:
        raise ExportError(f"onnxruntime differs from torch: max abs diff {diff:.2e}")
    thr = float(threshold) if threshold else float(ck.get("best_threshold", 0.35))
    thr = min(0.95, max(0.05, thr))
    today = _dt.date.today().isoformat()
    meta = {
        "input_size": input_size,
        "stride": int(ck.get("stride", STRIDE)),
        "classes": list(CLASSES),
        "threshold": round(thr, 3),
        "version": version or f"minimapnet-{today}",
        "metrics": _clean_metrics(ck.get("best_metrics") or {}),
        "train_step": int(ck.get("step", 0)),
        "params": count_parameters(model),
        "weights": "ema" if use_ema and isinstance(ck.get("ema"), dict) else "raw",
        "opset": graph["opset"],
        "exported": today,
        "onnx_max_abs_diff": float(f"{diff:.3g}"),
        "size_bytes": len(data),
        "note": "class 'self' is not learned visually (self icons are trained as 'ally')",
    }
    out_dir = Path(out_dir)
    _atomic_write(out_dir / MODEL_FILE, data)
    _atomic_write(out_dir / META_FILE, (json.dumps(meta, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    log.info("Exported %s (%.2f MB, ops %s, max diff %.2e)", out_dir / MODEL_FILE, len(data) / 1e6,
             graph["ops"], diff)
    return meta


def build_parser() -> argparse.ArgumentParser:
    """Command-line interface."""
    p = argparse.ArgumentParser(description="Export a MinimapNet checkpoint to ONNX (+ model_meta.json).")
    p.add_argument("--ckpt", default="training/runs/main/best.pt")
    p.add_argument("--out-dir", default=str(MODEL_DIR))
    p.add_argument("--version", default="", help="version string, e.g. 'minimapnet-2026-10-01' "
                   "(default: minimapnet-<today>)")
    p.add_argument("--threshold", type=float, default=0.0,
                   help="detection threshold for model_meta.json (0 = best-F1 threshold of the checkpoint)")
    p.add_argument("--no-ema", action="store_true", help="export the raw weights instead of the EMA")
    p.add_argument("--threads", type=int, default=2)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    try:
        meta = export(Path(args.ckpt), Path(args.out_dir), args.version or None,
                      args.threshold or None, not args.no_ema, args.threads)
    except (ExportError, FileNotFoundError) as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({k: meta[k] for k in ("version", "threshold", "params", "size_bytes",
                                           "onnx_max_abs_diff")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
