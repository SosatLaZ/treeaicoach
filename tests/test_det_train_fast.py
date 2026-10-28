"""tools/det_train_fast.py: warm start from the app's ONNX, unsure zones ignored, keep/reject gate."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

torch = pytest.importorskip("torch")
pytest.importorskip("onnx")

import det_train_fast as dtf  # noqa: E402


def test_warm_start_reproduces_the_app_model():
    import onnxruntime as ort

    from training.model import build_model

    model = build_model(dtf.CFG)
    onnx_path = dtf.MODEL_DIR / "minimap_detector.onnx"
    dtf.load_onnx_weights(model, onnx_path, 160)
    model.eval()
    x = np.random.default_rng(0).random((1, 3, 160, 160), dtype=np.float32)
    sess = ort.InferenceSession(onnx_path.read_bytes(), providers=["CPUExecutionProvider"])
    ref = sess.run(None, {"input": x})
    with torch.no_grad():
        got = [t.numpy() for t in model(torch.from_numpy(x))]
    for a, b in zip(ref, got):
        assert np.abs(a - b).max() < 1e-4


def test_unsure_zone_is_ignored_by_the_focal_loss():
    hm = np.zeros((1, 40, 40), np.float32)
    dtf._ignore_mask(hm, [{"u": 0.5, "v": 0.5, "r": 0.05}], 0, 0, 160, 160)
    assert hm.max() == pytest.approx(dtf.IGNORE) and (hm > 0).sum() > 4
    assert (1.0 - hm.max()) ** 4 < 1e-10            # negative weight (1 - gt)^4 ~ 0
    assert hm.max() < 1.0 - 1e-4                    # never a positive of the focal loss


def test_gate():
    base = {"quality": 1.80, "prec": 0.978, "rec": 0.98, "real": 1.9, "case:a": 1.85}
    ok, why = dtf.gate(base, {**base, "quality": 1.81})
    assert ok and not why
    assert not dtf.gate(base, {**base, "quality": 1.79})[0]
    assert not dtf.gate(base, {**base, "quality": 1.9, "prec": 0.97})[0]
    assert not dtf.gate(base, {**base, "quality": 1.9, "case:a": 1.84})[0]


def test_real_cases_load_with_unsure(tmp_path):
    tr, val = dtf.load_real_cases(160)
    assert tr and val and len(tr) > len(val)
    img, labs, uns = tr[0]
    assert img.shape == (160, 160, 3)
    assert all(0.03 <= lab["r"] <= 0.07 and lab["cls"] in ("ally", "enemy") for lab in labs)
    x, t = dtf.real_batch(tr, np.arange(4), np.random.default_rng(0), 160, 96)
    assert tuple(x.shape) == (4, 3, 96, 96) and tuple(t["heatmap"].shape) == (4, 1, 24, 24)
