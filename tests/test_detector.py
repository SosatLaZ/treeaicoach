"""Tests for treeaicoach.detector (decoding, ONNX backend, classic backend, factory)."""

from __future__ import annotations

import json
import math
import random
import time
from pathlib import Path

import numpy as np
import pytest

from treeaicoach import detector as D
from treeaicoach.detector import (CLASSES, ClassicDetector, Detection, OnnxDetector,
                                  create_detector, decode_outputs, preprocess)


# ======================================================================================
# helpers
# ======================================================================================


def _outputs(g: int = 16):
    """Empty CenterNet outputs for a g x g grid."""
    heat = np.zeros((1, 1, g, g), np.float32)
    cls = np.zeros((1, 3, g, g), np.float32)
    cls[0, 0] = 1.0
    off = np.full((1, 2, g, g), 0.5, np.float32)
    rad = np.full((1, 1, g, g), 0.05, np.float32)
    return heat, cls, off, rad


def _set(outs, y, x, score, probs=(0.1, 0.8, 0.1), off=(0.5, 0.5), r=0.05):
    heat, cls, offset, rad = outs
    heat[0, 0, y, x] = score
    cls[0, :, y, x] = probs
    offset[0, :, y, x] = off
    rad[0, 0, y, x] = r


# ======================================================================================
# preprocess
# ======================================================================================


def test_preprocess_shape_dtype_rgb():
    img = np.zeros((300, 300, 3), np.uint8)
    img[..., 0] = 255          # blue channel in BGR
    x = preprocess(img, 64)
    assert x.shape == (1, 3, 64, 64) and x.dtype == np.float32
    assert x[0, 2].min() == pytest.approx(1.0)      # RGB: blue is the last channel
    assert x[0, 0].max() == pytest.approx(0.0)
    assert x.flags["C_CONTIGUOUS"]


def test_preprocess_accepts_grey_and_bgra_rejects_garbage():
    assert preprocess(np.full((50, 50), 128, np.uint8), 32).shape == (1, 3, 32, 32)
    assert preprocess(np.zeros((50, 50, 4), np.uint8), 32).shape == (1, 3, 32, 32)
    with pytest.raises(ValueError):
        preprocess(np.zeros((4, 4, 3), np.uint8), 32)
    with pytest.raises(ValueError):
        preprocess(None, 32)  # type: ignore[arg-type]


# ======================================================================================
# decode_outputs
# ======================================================================================


def test_decode_single_peak_position_radius_class():
    outs = _outputs(16)
    _set(outs, 5, 10, 0.9, probs=(0.7, 0.2, 0.1), off=(0.25, 0.75), r=0.047)
    dets = decode_outputs(*outs, threshold=0.35, stride=4, input_size=64)
    assert len(dets) == 1
    d = dets[0]
    assert d.u == pytest.approx((10 + 0.25) * 4 / 64)
    assert d.v == pytest.approx((5 + 0.75) * 4 / 64)
    assert d.r == pytest.approx(0.047)
    assert d.score == pytest.approx(0.9)
    assert d.cls == "enemy"
    assert sum(d.cls_probs) == pytest.approx(1.0)
    assert d.cls_probs[0] == pytest.approx(0.7)


def test_decode_threshold():
    outs = _outputs(16)
    _set(outs, 2, 2, 0.30)
    _set(outs, 12, 12, 0.50)
    dets = decode_outputs(*outs, threshold=0.35, stride=4, input_size=64)
    assert len(dets) == 1 and dets[0].score == pytest.approx(0.5)
    assert decode_outputs(*outs, threshold=0.6, stride=4, input_size=64) == []


def test_decode_maxpool_nms_keeps_local_maximum_only():
    outs = _outputs(16)
    _set(outs, 8, 8, 0.9)
    _set(outs, 8, 9, 0.8)       # neighbour: suppressed by the 3x3 max-pool
    _set(outs, 9, 9, 0.7)
    _set(outs, 8, 12, 0.6)      # 4 cells away: distinct icon (0.25 > 0.6 * r = 0.03)
    dets = decode_outputs(*outs, threshold=0.35, stride=4, input_size=64)
    assert [round(d.score, 2) for d in dets] == [0.9, 0.6]


def test_decode_suppresses_close_duplicate_peaks():
    # two maxima 2 cells apart (not neighbours) but closer than 0.6 * radius
    outs = _outputs(32)
    _set(outs, 10, 10, 0.9, r=0.2)
    _set(outs, 10, 12, 0.8, r=0.2)
    dets = decode_outputs(*outs, threshold=0.35, stride=4, input_size=128)
    assert len(dets) == 1 and dets[0].score == pytest.approx(0.9)
    # with a small radius the two peaks are separate icons
    outs = _outputs(32)
    _set(outs, 10, 10, 0.9, r=0.01)
    _set(outs, 10, 12, 0.8, r=0.01)
    assert len(decode_outputs(*outs, threshold=0.35, stride=4, input_size=128)) == 2


def test_decode_plateau_gives_one_detection():
    outs = _outputs(16)
    _set(outs, 4, 4, 0.8)
    _set(outs, 4, 5, 0.8)
    dets = decode_outputs(*outs, threshold=0.35, stride=4, input_size=64)
    assert len(dets) == 1


def test_decode_max_det_and_sorting():
    outs = _outputs(32)
    k = 0
    for y in range(1, 32, 4):
        for x in range(1, 32, 4):
            _set(outs, y, x, 0.4 + 0.009 * k, r=0.01)
            k += 1
    dets = decode_outputs(*outs, threshold=0.35, stride=4, input_size=128, max_det=5)
    assert len(dets) == 5
    scores = [d.score for d in dets]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == pytest.approx(0.4 + 0.009 * (k - 1))
    assert decode_outputs(*outs, threshold=0.35, stride=4, input_size=128, max_det=0) == []


def test_decode_accepts_chw_and_sanitises_nan():
    outs = _outputs(8)
    _set(outs, 3, 3, 0.9)
    heat, cls, off, rad = outs
    cls[0, :, 3, 3] = (np.nan, 1.0, 0.0)
    off[0, 0, 3, 3] = np.nan
    rad[0, 0, 3, 3] = np.inf
    dets = decode_outputs(heat[0], cls[0], off[0], rad[0], threshold=0.35, stride=4,
                          input_size=32)
    assert len(dets) == 1
    d = dets[0]
    assert all(math.isfinite(x) for x in (d.u, d.v, d.r, *d.cls_probs))
    assert d.cls == "ally"
    assert 0.0 <= d.u <= 1.0 and 0.0 <= d.r <= 1.0


def test_decode_rejects_inconsistent_shapes():
    heat, cls, off, rad = _outputs(8)
    with pytest.raises(ValueError):
        decode_outputs(heat, cls[:, :, :4], off, rad, 0.35, 4, 32)
    with pytest.raises(ValueError):
        decode_outputs(heat, cls, off[:, :1], rad, 0.35, 4, 32)


# ======================================================================================
# ONNX backend (dummy graph)
# ======================================================================================


def _dummy_model(tmp_path: Path, size: int = 64, peak=(5, 9), meta: dict | None = None,
                 name: str = "minimap_detector.onnx") -> Path:
    onnx = pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    from onnx import TensorProto, helper, numpy_helper

    g = size // 4
    heat = np.zeros((1, 1, g, g), np.float32)
    heat[0, 0, peak[0], peak[1]] = 0.9
    cls = np.zeros((1, 3, g, g), np.float32)
    cls[0, 1] = 1.0
    cls[0, :, peak[0], peak[1]] = (0.8, 0.15, 0.05)
    off = np.full((1, 2, g, g), 0.5, np.float32)
    off[0, :, peak[0], peak[1]] = (0.25, 0.5)
    rad = np.full((1, 1, g, g), 0.05, np.float32)
    rad[0, 0, peak[0], peak[1]] = 0.046

    nodes, inits = [], []
    # zero term depending on the input, so the graph really consumes it
    nodes.append(helper.make_node("AveragePool", ["input"], ["pool"], kernel_shape=[4, 4],
                                  strides=[4, 4]))
    nodes.append(helper.make_node("ReduceMean", ["pool"], ["pm"], keepdims=1, axes=[1]))
    inits.append(numpy_helper.from_array(np.zeros((1,), np.float32), "zero"))
    nodes.append(helper.make_node("Mul", ["pm", "zero"], ["z"]))
    outputs = []
    for nm, arr in (("heatmap", heat), ("cls", cls), ("offset", off), ("radius", rad)):
        inits.append(numpy_helper.from_array(arr, nm + "_c"))
        nodes.append(helper.make_node("Add", [nm + "_c", "z"], [nm]))
        outputs.append(helper.make_tensor_value_info(nm, TensorProto.FLOAT, list(arr.shape)))
    graph = helper.make_graph(
        nodes, "dummy",
        [helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 3, size, size])],
        outputs, initializer=inits)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    path = tmp_path / name
    onnx.save(model, str(path))
    m = {"input_size": size, "stride": 4, "classes": list(CLASSES), "threshold": 0.35,
         "version": "test", "metrics": {}}
    if meta is not None:
        m.update(meta)
    (tmp_path / "model_meta.json").write_text(json.dumps(m), encoding="utf-8")
    return path


def test_onnx_detector_dummy_graph(tmp_path):
    path = _dummy_model(tmp_path, 64, peak=(5, 9))
    det = OnnxDetector(path, threads=1)
    assert det.input_size == 64 and det.stride == 4 and det.threshold == pytest.approx(0.35)
    out = det.detect(np.zeros((230, 230, 3), np.uint8))
    assert len(out) == 1
    d = out[0]
    assert d.u == pytest.approx((9 + 0.25) * 4 / 64, abs=1e-5)
    assert d.v == pytest.approx((5 + 0.5) * 4 / 64, abs=1e-5)
    assert d.r == pytest.approx(0.046, abs=1e-6)
    assert d.cls == "enemy" and d.score == pytest.approx(0.9, abs=1e-5)
    # invalid input -> [] (never raises)
    assert det.detect(None) == []  # type: ignore[arg-type]
    assert det.detect(np.zeros((3, 3, 3), np.uint8)) == []


def test_onnx_detector_threshold_argument(tmp_path):
    path = _dummy_model(tmp_path, 64)
    assert OnnxDetector(path, threshold=0.95).detect(np.zeros((64, 64, 3), np.uint8)) == []
    assert OnnxDetector(path, threshold=0.0).threshold == pytest.approx(0.35)


def test_onnx_detector_validates_meta(tmp_path):
    path = _dummy_model(tmp_path, 64, meta={"input_size": 128})
    with pytest.raises(ValueError):
        OnnxDetector(path)
    path = _dummy_model(tmp_path, 64, meta={"classes": ["a", "b", "c"]})
    with pytest.raises(ValueError):
        OnnxDetector(path)


def test_onnx_detector_missing_or_broken_model(tmp_path):
    pytest.importorskip("onnxruntime")
    with pytest.raises(Exception):
        OnnxDetector(tmp_path / "missing.onnx")
    bad = tmp_path / "bad.onnx"
    bad.write_bytes(b"not a model")
    with pytest.raises(Exception):
        OnnxDetector(bad)


# ======================================================================================
# factory
# ======================================================================================


def test_create_detector_falls_back_to_classic(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "default_model_path", lambda: tmp_path / "missing.onnx")
    assert create_detector("auto").name == "classic"
    assert create_detector("onnx").name == "classic"
    assert create_detector("classic").name == "classic"
    assert create_detector("bogus").name == "classic"          # type: ignore[arg-type]
    bad = tmp_path / "bad.onnx"
    bad.write_bytes(b"garbage")
    monkeypatch.setattr(D, "default_model_path", lambda: bad)
    assert create_detector("auto").name == "classic"


def test_create_detector_uses_onnx_when_present(tmp_path, monkeypatch):
    path = _dummy_model(tmp_path, 64)
    monkeypatch.setattr(D, "default_model_path", lambda: path)
    det = create_detector("auto", threshold=0.5)
    assert det.name == "onnx" and det.threshold == pytest.approx(0.5)
    assert create_detector("classic").name == "classic"


# ======================================================================================
# classic backend on rendered minimaps
# ======================================================================================


def _render_scenes(n: int, seed: int = 0):
    from treeaicoach.champions import ChampionDB
    from treeaicoach.render import ChampionSprite, MinimapRenderer, Scene

    renderer = MinimapRenderer()
    db = ChampionDB(cache_dir=Path("/nonexistent-treeaicoach-cache"))
    aliases = [e.alias for e in db.all()]
    textures = renderer.textures()
    if not aliases or not textures:
        pytest.skip("rendering assets missing")
    rng = random.Random(seed)
    scenes = []
    for _ in range(n):
        size = rng.choice([220, 256, 300, 350, 400])
        champs, gts = [], []
        k = rng.randint(3, 8)
        for _ in range(300):
            if len(champs) >= k:
                break
            r = rng.uniform(0.043, 0.051)
            u, v = rng.uniform(0.07, 0.93), rng.uniform(0.07, 0.93)
            if any((u - g[0]) ** 2 + (v - g[1]) ** 2 < (2.3 * max(r, g[2])) ** 2 for g in gts):
                continue
            rel = rng.choice(["enemy", "ally", "self"])
            champs.append(ChampionSprite(u, v, r, rel, db.load_icon(rng.choice(aliases))))
            gts.append((u, v, r, "enemy" if rel == "enemy" else "ally"))
        vision = [(g[0], g[1], 0.08) for g in gts if g[3] != "enemy"]
        vision += [(rng.random(), rng.random(), 0.09) for _ in range(3)]
        img = renderer.render(Scene(texture=rng.choice(textures), size=size, vision=vision,
                                    champions=champs))
        scenes.append((img, gts))
    return scenes


def test_classic_detector_on_rendered_scenes():
    scenes = _render_scenes(24, seed=3)
    det = ClassicDetector()
    tp = fp = fn = cls_ok = 0
    times = []
    for img, gts in scenes:
        t0 = time.perf_counter()
        dets = det.detect(img)
        times.append(time.perf_counter() - t0)
        used: set[int] = set()
        for d in dets:
            assert isinstance(d, Detection) and d.cls in CLASSES
            assert len(d.cls_probs) == 3 and sum(d.cls_probs) == pytest.approx(1.0, abs=1e-6)
            best = min((j for j in range(len(gts)) if j not in used),
                       key=lambda j: math.hypot(d.u - gts[j][0], d.v - gts[j][1]), default=None)
            if best is not None and math.hypot(d.u - gts[best][0], d.v - gts[best][1]) \
                    < 0.5 * gts[best][2]:
                used.add(best)
                tp += 1
                cls_ok += d.cls == gts[best][3]
            else:
                fp += 1
        fn += len(gts) - len(used)
    precision = tp / max(1, tp + fp)
    recall = tp / max(1, tp + fn)
    cls_acc = cls_ok / max(1, tp)
    print(f"classic: P={precision:.3f} R={recall:.3f} cls={cls_acc:.3f} "
          f"median {1000 * float(np.median(times)):.1f} ms")
    assert precision >= 0.8
    assert recall >= 0.8
    assert cls_acc >= 0.85


def test_classic_detector_blank_and_invalid_inputs():
    det = ClassicDetector()
    assert det.detect(np.zeros((256, 256, 3), np.uint8)) == []
    assert det.detect(np.full((256, 256, 3), 200, np.uint8)) == []
    assert det.detect(None) == []  # type: ignore[arg-type]
    assert det.detect(np.zeros((5, 5, 3), np.uint8)) == []
    assert det.detect("nope") == []  # type: ignore[arg-type]
    noise = np.random.default_rng(0).integers(0, 256, (256, 256, 3), dtype=np.uint8)
    assert isinstance(det.detect(noise), list)
    # float / grey / BGRA inputs are converted
    assert isinstance(det.detect(np.zeros((128, 128), np.float32)), list)
    assert isinstance(det.detect(np.zeros((128, 128, 4), np.uint8)), list)


def test_classic_detector_invalid_radii_use_defaults():
    det = ClassicDetector(r_min=0.5, r_max=0.1)
    assert (det.r_min, det.r_max) == (0.025, 0.075)
