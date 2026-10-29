"""Cost cuts of the detection hot paths keep the exact same results (tools/perf_budget.py)."""

from __future__ import annotations

import math
import random
from types import SimpleNamespace

import numpy as np

from treeaicoach import roster_matcher as rm
from treeaicoach import self_icon as si
from treeaicoach.ui_common import _Dispatcher


def test_keep_far_is_the_exact_hypot_filter() -> None:
    rnd = random.Random(3)
    for _ in range(100):
        pts = [(rnd.random(), rnd.random()) for _ in range(rnd.randint(0, 200))]
        cen = [(rnd.random(), rnd.random()) for _ in range(rnd.randint(0, 30))]
        d = rnd.choice([0.03, 0.09, 0.25])
        ref = [p for p in pts if not any(math.hypot(p[0] - a, p[1] - b) < d for a, b in cen)]
        assert rm._keep_far(pts, cen, d) == ref


def test_lut_index_uint16_same_indices() -> None:
    b = np.random.default_rng(0).integers(0, 256, (64, 80, 3), dtype=np.uint8)
    q = (b >> (8 - si._LUT_BITS)).astype(np.int32)
    ref = (q[:, :, 0] << (2 * si._LUT_BITS)) | (q[:, :, 1] << si._LUT_BITS) | q[:, :, 2]
    assert np.array_equal(si._lut_index(b), ref)


def test_ring_mean_uint8_equals_float_path() -> None:
    m = (np.random.default_rng(1).random((70, 90, 3)) > 0.6).astype(np.uint8)
    for rw in (3.2, 7.9, 11.0):
        a = si._ring_mean(m, rw, si._RING_PTS)
        b = si._ring_mean(m.astype(np.float32), rw, si._RING_PTS)
        assert a.dtype == b.dtype == np.float32 and np.array_equal(a, b)


def test_ring_classify_unchanged_by_min_before_sqrt() -> None:
    model = rm.RingColorModel()
    x = (np.random.default_rng(2).random((120, 3)) * 255).astype(np.float32)
    w = np.asarray([0.35, 1.0, 1.0], np.float32)
    pe, pa = model._protos("enemy"), model._protos("ally") + model._protos("self")
    P = np.asarray(pe + pa, np.float32) * w
    X = x * w
    D = np.sqrt(np.maximum((X * X).sum(1)[:, None] - 2.0 * (X @ P.T) + (P * P).sum(1)[None], 0.0))
    de, da = D[:, :len(pe)].min(1), D[:, len(pe):].min(1)
    ref = (float(np.mean((de < model.NEAR) & (de < 0.8 * da))), float(np.mean((da < model.NEAR) & (da < 0.8 * de))))
    assert model.classify(x) == ref


def test_dispatcher_wakes_once_per_batch() -> None:
    d = _Dispatcher()
    pokes, ran = [], []
    d.wake = lambda: pokes.append(1)
    d.post(lambda: ran.append(1))
    d.post(lambda: ran.append(2))
    assert len(pokes) == 1                  # armed: one wake-up for the batch
    d.drain()
    assert ran == [1, 2]
    d.post(lambda: ran.append(3))
    assert len(pokes) == 2                  # re-armed after the drain


def test_perf_profile_reaches_a_detector_created_later(monkeypatch) -> None:
    import cv2

    from treeaicoach import overlay
    from treeaicoach.engine import CoachEngine
    from treeaicoach.sysperf import PROFILES

    # module-level knobs the call pushes: restored after the test
    monkeypatch.setattr(rm, "LOST_EVERY", rm.LOST_EVERY)
    monkeypatch.setattr(overlay, "_budget_fps", overlay._budget_fps)
    monkeypatch.setattr(cv2, "setNumThreads", lambda n: None)

    prof = PROFILES["low_end"]
    eng = SimpleNamespace(_budget=SimpleNamespace(profile=prof, reason="test"), _applied_profile=None,
                          _governor=SimpleNamespace(configure=lambda *a: None),
                          _heavy=SimpleNamespace(hz=0.0), _detector=None)
    CoachEngine._apply_perf_profile(eng)            # before the detector exists (engine start)
    eng._detector = SimpleNamespace(FALLBACK_EVERY=99)
    CoachEngine._apply_perf_profile(eng)            # next tick: the new detector gets the knobs
    assert eng._detector.FALLBACK_EVERY == prof.onnx_every
