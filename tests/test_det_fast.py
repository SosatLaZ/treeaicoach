"""tools/det_fast.py: detector record / replay tape, ONNX stage cache, keys, hard subset, and one
short gym game: cached replay == recorded run == det_gym's own run_game (exact metrics)."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

pytest.importorskip("cv2")


@dataclass
class _Det:
    u: float
    v: float


class _Learner:
    def __init__(self) -> None:
        self.lane = None

    def observe(self, t):
        self.lane = "bot" if t > 1 else None
        return self.lane


class _Matcher:
    def __init__(self) -> None:
        self.n = 0
        self.last_dead = set()
        self.learner = _Learner()

    def detect(self, img, t=0.0):
        self.n += 1
        self.last_dead = {"Garen"} if self.n % 2 else set()
        return [_Det(float(img.sum()), t)]


for _c in (_Learner, _Matcher):
    _c.__module__ = "treeaicoach.fake"      # (the tape proxies treeaicoach objects)


def _drive(m, frames):
    out = []
    for i, img in enumerate(frames):
        d = m.detect(img, t=float(i))
        out.append((d, set(m.last_dead), m.learner.observe(i), getattr(m, "missing", "x")))
    return out


def test_tape_record_replay_and_divergence():
    import det_fast as F

    frames = [np.full((4, 4), i, np.uint8) for i in range(4)]
    tape = F.Tape()
    rec = _drive(F._P(_Matcher(), tape, "m"), frames)
    assert rec == _drive(_Matcher(), frames)
    data = tape.data()
    assert not data["unsafe"]
    assert _drive(F._P(None, F.Tape(data), "m"), frames) == rec     # no real object at all
    other = [f + 1 for f in frames]
    with pytest.raises(F.Divergence):
        _drive(F._P(None, F.Tape(data), "m"), other)                 # other arguments
    try:                                    # not swallowed by the engine's `except Exception`
        try:
            F._P(None, F.Tape(data), "m").never_read
        except Exception:
            pytest.fail("Divergence caught as an Exception")
    except F.Divergence:
        pass


def test_onnx_cache_replays_outputs(tmp_path, monkeypatch):
    import det_fast as F

    monkeypatch.setattr(F, "CACHE", tmp_path)

    class D:
        _model_bytes = b"model"

    c = F.OnnxCache()
    c.open("g")
    x = np.arange(12, dtype=np.float32).reshape(1, 3, 2, 2)
    calls = []

    def run():
        calls.append(1)
        return [x * 2]

    a = c.onnx(D(), x, run)
    c.flush()
    c2 = F.OnnxCache()
    c2.open("g")
    b = c2.onnx(D(), x, run)
    assert len(calls) == 1 and np.array_equal(a[0], b[0]) and c2.hits == 1


def test_det_key_follows_detector_constants_only():
    import det_fast as F
    from treeaicoach import roster_matcher, tracker

    k0 = F.det_key()
    name = next(k for k, v in vars(tracker).items() if k.isupper() and isinstance(v, float))
    saved = F._apply({f"tracker.{name}": getattr(tracker, name) + 1.0})
    try:
        assert F.det_key() == k0              # downstream knob: detector tape still valid
    finally:
        F._restore(saved)
    saved = F._apply({"roster_matcher.LOST_EVERY": roster_matcher.LOST_EVERY + 1})
    try:
        assert F.det_key() != k0              # detector knob: the detector is re-run
    finally:
        F._restore(saved)
    assert F.det_key() == k0


def test_metrics_only_and_subset():
    import det_fast as F
    import det_gym as G

    out = {"suites": {"main": {"games": {"laning": {"rec": 1.0, "ms": 3.0, "fails": [
        ("laning", 2.0, "miss", "x"), ("laning", 2.1667, "fp", "y")]}}, "total": {"rec": 1.0, "ms": 3.0}}},
        "timing": {"wall_s": 1.0}, "stats": {}}
    m = F.metrics_only(out)
    assert "ms" not in m["suites"]["main"]["total"] and "timing" not in m
    sub = F.build_subset(out)
    sc = G.suite_scenarios("main")[0]
    wins = sub["main/laning"]
    dt = 1.0 / sc.fps
    assert any(a <= round(2.0 / dt) <= b for a, b, _t in wins)
    assert all(b - a <= round(F.PRE_ROLL_S / dt) + int(sc.seconds * sc.fps) for a, b, _t in wins)
    assert sub["_info"]["run"] < sub["_info"]["frames"]


def test_short_game_cached_equals_det_gym(tmp_path, monkeypatch):
    import det_fast as F
    import det_gym as G

    monkeypatch.setattr(F, "CACHE", tmp_path)
    sc = G.suite_scenarios("main")[0]
    sc.seconds = 2.5
    F._worker_init()
    runs = {m: F._task(("game", ("main", sc, None), {}, m))[2] for m in ("nocache", "auto")}
    replay = F._task(("game", ("main", sc, None), {}, "auto"))[2]
    assert runs["auto"]["st"]["tape"] == "record" and replay["st"]["tape"] == "replay"
    summ = [F.metrics_only(dict(r["M"].summary(), fails=r["M"].fails)) for r in
            (runs["nocache"], runs["auto"], replay)]
    assert F.canon(summ[0]) == F.canon(summ[1]) == F.canon(summ[2])
