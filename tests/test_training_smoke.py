"""Tests of the training pipeline (dataset targets, model, losses, metrics, export, evaluate).

Needs torch (training-only dependency): the whole file is skipped where it is missing
(e.g. the Windows CI, which installs only the runtime requirements). ONNX-related tests also
need ``onnx`` / ``onnxruntime``.

The end-to-end smoke test (train 30 steps at batch 8 -> export ONNX -> evaluate 20 validation
images through the runtime detector) takes ~20-40 s on a shared 4-core CPU; set
``TREEAI_SKIP_SLOW=1`` to skip it.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from training import dataset as D  # noqa: E402
from training import evaluate as EV  # noqa: E402
from training.model import INPUT_SIZE, STRIDE, MinimapNet, count_parameters  # noqa: E402
from training.train import (  # noqa: E402
    ModelEMA,
    build_parser,
    compute_losses,
    focal_loss,
    lr_factor,
    train,
)

SLOW = pytest.mark.skipif(os.environ.get("TREEAI_SKIP_SLOW") == "1", reason="TREEAI_SKIP_SLOW=1")
GRID = INPUT_SIZE // STRIDE


# --------------------------------------------------------------------------------------
# Sample generator: the real synth when available, else a light stand-in (render-based)
# --------------------------------------------------------------------------------------


def _standin_generator():
    """Tiny generator following the synth contract (used only if training.synth is missing)."""
    import cv2

    from treeaicoach import render

    renderer = render.MinimapRenderer()
    textures = renderer.textures() or [render.DEFAULT_TEXTURE]

    def gen(rng: np.random.Generator, size: int = 256):
        champs, labels = [], []
        for k in range(int(rng.integers(0, 8))):
            rel = "enemy" if k % 2 else "ally"
            u, v, r = (float(rng.uniform(0.06, 0.94)), float(rng.uniform(0.06, 0.94)),
                       float(rng.uniform(0.035, 0.06)))
            champs.append(render.ChampionSprite(u, v, r, rel, None))
            labels.append({"u": u, "v": v, "r": r, "cls": rel, "cls_valid": True})
        scene = render.Scene(texture=textures[int(rng.integers(0, len(textures)))],
                             size=int(rng.integers(200, 320)), champions=champs,
                             vision=[(lab["u"], lab["v"], 0.08) for lab in labels])
        img = cv2.resize(renderer.render(scene), (size, size), interpolation=cv2.INTER_AREA)
        return img, labels

    return gen


def _generator():
    try:
        return D.default_generator(), True
    except Exception:  # synth.py missing or broken: stand-in
        return _standin_generator(), False


@pytest.fixture(scope="module")
def generator():
    gen, _real = _generator()
    return gen


# --------------------------------------------------------------------------------------
# Targets
# --------------------------------------------------------------------------------------


def test_encode_targets_basic():
    labels = [
        {"u": 0.5, "v": 0.25, "r": 0.045, "cls": "enemy", "cls_valid": True},
        {"u": 0.1003, "v": 0.9, "r": 0.05, "cls": "self", "cls_valid": True},
        {"u": 0.7, "v": 0.7, "r": 0.04, "cls": "ally", "cls_valid": False},
        {"u": 1.02, "v": 0.5, "r": 0.05, "cls": "enemy", "cls_valid": True},   # centre outside
        {"u": float("nan"), "v": 0.5, "r": 0.05, "cls": "enemy"},             # malformed
    ]
    t = D.encode_targets(labels)
    assert t["heatmap"].shape == (1, GRID, GRID) and t["heatmap"].dtype == np.float32
    assert t["reg_mask"].sum() == 3
    # peak exactly 1 at the integer cell of each kept icon
    hm = t["heatmap"][0]
    assert hm[16, 32] == 1.0 and hm[57, 6] == 1.0 and hm[44, 44] == 1.0
    assert int((hm == 1.0).sum()) == 3
    assert 0.0 <= hm.min() and hm.max() <= 1.0
    # indices / offsets / radius / classes
    assert t["ind"][0] == 16 * GRID + 32
    np.testing.assert_allclose(t["offset"][0], (0.0, 0.0), atol=1e-5)
    np.testing.assert_allclose(t["offset"][1], (0.1003 * GRID - 6, 0.9 * GRID - 57), atol=1e-4)
    np.testing.assert_allclose(t["radius"][:3], (0.045, 0.05, 0.04), atol=1e-6)
    assert list(t["cls"][:3]) == [0, 1, 1]            # "self" is trained as "ally"
    assert list(t["cls_mask"][:3]) == [1.0, 1.0, 0.0]  # random-hue ring: no class loss
    assert (t["offset"][:3] >= 0).all() and (t["offset"][:3] < 1).all()


def test_encode_targets_max_objects_and_sigma():
    labels = [{"u": 0.05 + 0.03 * i, "v": 0.5, "r": 0.045, "cls": "enemy", "cls_valid": True}
              for i in range(25)]
    t = D.encode_targets(labels, max_objects=16)
    assert t["reg_mask"].sum() == 16
    assert D.sigma_for_radius(0.001) == pytest.approx((2 * D.GAUSSIAN_MIN_RADIUS + 1) / 6)
    assert D.sigma_for_radius(0.08) > D.sigma_for_radius(0.04)


def test_stream_seeding_workers_and_epochs():
    a = D.stream_rng(0, 0, 0).random(4)
    assert np.allclose(a, D.stream_rng(0, 0, 0).random(4))
    assert not np.allclose(a, D.stream_rng(0, 0, 1).random(4))       # other worker
    assert not np.allclose(a, D.stream_rng(0, 1, 0).random(4))       # other epoch (resume)
    assert not np.allclose(a, D.val_rng(0, 0).random(4))             # never the val stream

    def fake(rng, size):
        img = np.full((size, size, 3), int(rng.integers(0, 255)), np.uint8)
        return img, [{"u": float(rng.uniform(0.1, 0.9)), "v": 0.5, "r": 0.05, "cls": "ally",
                      "cls_valid": True}]

    s1 = [x.mean().item() for x, _ in D.MinimapStream(seed=3, generator=fake, limit=4)]
    s2 = [x.mean().item() for x, _ in D.MinimapStream(seed=3, generator=fake, limit=4)]
    s3 = [x.mean().item() for x, _ in D.MinimapStream(seed=3, epoch=7, generator=fake, limit=4)]
    assert s1 == s2 and s1 != s3
    x, t = next(iter(D.MinimapStream(generator=fake, limit=1)))
    assert tuple(x.shape) == (3, INPUT_SIZE, INPUT_SIZE) and x.dtype == torch.float32
    assert 0.0 <= float(x.min()) and float(x.max()) <= 1.0
    assert tuple(t["heatmap"].shape) == (1, GRID, GRID)


def test_validation_set_cache(tmp_path, monkeypatch, generator):
    calls = {"n": 0}

    def counting(rng, size):
        calls["n"] += 1
        return generator(rng, size)

    monkeypatch.setattr(D, "default_generator", lambda: counting)
    vs = D.build_validation_set(4, seed=5, cache_dir=tmp_path)
    assert vs.images.shape == (4, INPUT_SIZE, INPUT_SIZE, 3) and vs.images.dtype == np.uint8
    assert calls["n"] == 4 and len(list(tmp_path.glob("val_4_5_*.npz"))) == 1
    vs2 = D.build_validation_set(3, seed=5, cache_dir=tmp_path)       # smaller n: from cache
    assert calls["n"] == 4
    np.testing.assert_array_equal(vs2.images, vs.images[:3])
    for a, b in zip(vs2.labels, vs.labels):
        assert len(a) == len(b)
        for la, lb in zip(a, b):
            assert la["cls"] == lb["cls"] and la["cls_valid"] == lb["cls_valid"]
            assert la["u"] == pytest.approx(lb["u"], abs=1e-6) and la["r"] == pytest.approx(lb["r"], abs=1e-6)


# --------------------------------------------------------------------------------------
# Model, losses, EMA
# --------------------------------------------------------------------------------------


def test_model_outputs_and_size():
    torch.manual_seed(0)
    m = MinimapNet().eval()
    n = count_parameters(m)
    assert 300_000 <= n <= 1_000_000
    with torch.inference_mode():
        hm, cl, off, rad = m(torch.rand(2, 3, INPUT_SIZE, INPUT_SIZE))
    assert tuple(hm.shape) == (2, 1, GRID, GRID) and tuple(cl.shape) == (2, 3, GRID, GRID)
    assert tuple(off.shape) == (2, 2, GRID, GRID) and tuple(rad.shape) == (2, 1, GRID, GRID)
    assert float(hm.min()) >= 0 and float(hm.max()) <= 1
    assert abs(float(hm.mean()) - 0.1) < 0.05                   # prior bias -2.19
    torch.testing.assert_close(cl.sum(1), torch.ones(2, GRID, GRID))
    assert float(off.min()) >= 0 and float(off.max()) <= 1 and float(rad.min()) >= 0
    assert 0.02 < float(rad.mean()) < 0.08                      # radius head starts ~0.047


def test_focal_loss_and_losses_backward():
    gt = torch.zeros(1, 1, 8, 8)
    gt[0, 0, 3, 4] = 1.0
    good = torch.full_like(gt, -8.0)
    good[0, 0, 3, 4] = 8.0
    bad = -good
    assert float(focal_loss(good, gt)) < 0.01 < float(focal_loss(bad, gt))
    m = MinimapNet()
    lab = [{"u": 0.3, "v": 0.6, "r": 0.05, "cls": "enemy", "cls_valid": True}]
    t = {k: torch.from_numpy(v)[None] for k, v in D.encode_targets(lab).items()}
    out = m.forward_train(torch.rand(1, 3, INPUT_SIZE, INPUT_SIZE))
    losses = compute_losses(out, t, {"hm": 1.0, "off": 1.0, "rad": 1.0, "cls": 1.0})
    assert all(math.isfinite(float(v)) for v in losses.values())
    losses["total"].backward()
    assert m.head_hm.out.weight.grad is not None
    # empty image: no division by zero
    t0 = {k: torch.from_numpy(v)[None] for k, v in D.encode_targets([]).items()}
    l0 = compute_losses(out, t0, {"hm": 1.0, "off": 1.0, "rad": 1.0, "cls": 1.0})
    assert math.isfinite(float(l0["total"])) and float(l0["off"]) == 0.0


def test_ema_and_schedule():
    m = torch.nn.Linear(2, 1)
    ema = ModelEMA(m, decay=0.9, tau=1.0)
    with torch.no_grad():
        m.weight.fill_(1.0)
    for _ in range(200):
        ema.update(m)
    torch.testing.assert_close(ema.module.weight, m.weight, atol=1e-4, rtol=0)
    assert lr_factor(0, 100, 10) == pytest.approx(0.1)
    assert lr_factor(9, 100, 10) == pytest.approx(1.0)
    assert lr_factor(100, 100, 10, final=0.02) == pytest.approx(0.02)
    assert lr_factor(55, 100, 10) < lr_factor(20, 100, 10)


# --------------------------------------------------------------------------------------
# Decode / metrics
# --------------------------------------------------------------------------------------


def _outputs(peaks):
    hm = np.zeros((1, 1, GRID, GRID), np.float32)
    cl = np.full((1, 3, GRID, GRID), 1 / 3, np.float32)
    off = np.zeros((1, 2, GRID, GRID), np.float32)
    rad = np.zeros((1, 1, GRID, GRID), np.float32)
    for (x, y, s, ox, oy, r, c) in peaks:
        hm[0, 0, y, x] = s
        off[0, :, y, x] = (ox, oy)
        rad[0, 0, y, x] = r
        cl[0, :, y, x] = 0.05
        cl[0, c, y, x] = 0.9
    return hm, cl, off, rad


def test_decode_numpy_contract():
    hm, cl, off, rad = _outputs([(10, 20, 0.9, 0.25, 0.5, 0.05, 0), (40, 41, 0.5, 0.0, 0.0, 0.04, 1),
                                 (41, 41, 0.45, 0.0, 0.0, 0.04, 1),     # suppressed by 3x3 NMS
                                 (60, 5, 0.2, 0.5, 0.5, 0.04, 1)])      # under threshold
    dets = EV.decode_numpy(hm, cl, off, rad, threshold=0.3)
    assert len(dets) == 2
    d = dets[0]
    assert d.u == pytest.approx((10 + 0.25) * STRIDE / INPUT_SIZE)
    assert d.v == pytest.approx((20 + 0.5) * STRIDE / INPUT_SIZE)
    assert d.r == pytest.approx(0.05) and d.cls == "enemy" and d.score == pytest.approx(0.9)
    assert dets[1].cls == "ally"
    try:
        from treeaicoach.detector import decode_outputs
    except ImportError:
        return
    ref = decode_outputs(hm, cl, off, rad, threshold=0.3, stride=STRIDE, input_size=INPUT_SIZE)
    assert [(round(a.u, 5), round(a.v, 5), a.cls) for a in ref] == \
        [(round(b.u, 5), round(b.v, 5), b.cls) for b in dets]


def test_metrics_matching():
    gts = [EV.GT(0.2, 0.2, 0.05, "enemy"), EV.GT(0.6, 0.6, 0.05, "ally"),
           EV.GT(0.9, 0.1, 0.05, None, ignore=True)]
    dets = [EV.Det(0.21, 0.2, 0.05, 0.9, "enemy", (0.9, 0.05, 0.05)),
            EV.Det(0.61, 0.6, 0.05, 0.6, "enemy", (0.6, 0.3, 0.1)),    # TP, wrong class
            EV.Det(0.40, 0.4, 0.05, 0.7, "ally", (0.1, 0.8, 0.1)),     # FP
            EV.Det(0.90, 0.1, 0.05, 0.8, "ally", (0.1, 0.8, 0.1)),     # on an ignored GT
            EV.Det(0.2, 0.24, 0.05, 0.5, "enemy", (0.9, 0.05, 0.05))]  # duplicate -> FP
    acc = EV.MetricAccumulator()
    acc.add(dets, gts)
    m = acc.metrics(0.55)
    assert (m["tp"], m["det"], m["gt"]) == (2, 3, 2)
    assert m["precision"] == pytest.approx(2 / 3) and m["recall"] == pytest.approx(1.0)
    assert m["cls_acc"] == pytest.approx(0.5)
    assert m["pos_err_pct"] == pytest.approx(1.0, abs=1e-6)
    assert acc.metrics(0.45)["det"] == 4
    assert acc.metrics(0.95)["recall"] == 0.0
    best = acc.best()
    assert best["f1"] == max(r["f1"] for r in acc.sweep())


# --------------------------------------------------------------------------------------
# End to end: train -> export ONNX -> evaluate through the runtime detector
# --------------------------------------------------------------------------------------


@SLOW
def test_smoke_train_export_evaluate(tmp_path, monkeypatch, generator):
    pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    from training import export_onnx as E

    monkeypatch.setattr(D, "default_generator", lambda: generator)
    torch.set_num_threads(2)
    out = tmp_path / "run"
    args = build_parser().parse_args([
        "--steps", "30", "--batch", "8", "--workers", "0", "--threads", "2", "--out", str(out),
        "--val-every", "30", "--val-size", "20", "--cache-dir", "", "--log-every", "10"])
    res = train(args, generator=generator)
    assert res["step"] == 30 and (out / "last.pt").is_file() and (out / "best.pt").is_file()
    rows = (out / "log.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0].startswith("kind,step") and any(r.startswith("val,30") for r in rows)

    # resume continues from the saved step (no extra step to do)
    args2 = build_parser().parse_args([*map(str, [
        "--steps", 30, "--batch", 8, "--workers", 0, "--out", out, "--val-size", 0,
        "--resume", "auto"])])
    assert train(args2, generator=generator)["step"] == 30

    mdir = tmp_path / "model"
    meta = E.export(out / "best.pt", mdir, version="test-2026-09-30")
    assert (mdir / E.MODEL_FILE).stat().st_size < E.MAX_MODEL_BYTES
    saved = json.loads((mdir / E.META_FILE).read_text(encoding="utf-8"))
    assert saved["input_size"] == 256 and saved["stride"] == 4
    assert saved["classes"] == ["enemy", "ally", "self"] and saved["version"] == "test-2026-09-30"
    assert 0.0 < saved["threshold"] < 1.0 and meta["onnx_max_abs_diff"] < 1e-3

    import onnx

    ops = {n.op_type for n in onnx.load(str(mdir / E.MODEL_FILE)).graph.node}
    assert ops <= E.ALLOWED_OPS and "BatchNormalization" not in ops

    st = tmp_path / "selftest"
    rep = EV.run(EV.build_parser().parse_args([
        "--model", str(mdir / E.MODEL_FILE), "--val-size", "20", "--no-cache",
        "--make-selftest", "--selftest-dir", str(st), "--selftest-count", "2"]))
    assert rep["synthetic"]["at_threshold"]["images"] == 20
    assert "selftest" in rep and (st / "labels.json").is_file()
    entries = json.loads((st / "labels.json").read_text(encoding="utf-8"))
    assert len(entries) == 2 and all((st / e["file"]).is_file() for e in entries)
    assert all(220 <= e["size"] <= 360 and e["icons"] for e in entries)
    assert all(set(ic) >= {"u", "v", "r", "cls"} for e in entries for ic in e["icons"])
