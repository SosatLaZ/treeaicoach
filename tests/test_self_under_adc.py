"""Me (support) partly under my ADC's icon: my position comes from my visible ring arc only when
the arc, my portrait, the camera point and continuity agree (roster_matcher._under_arc), a
learner position of me that jumps away is ignored (_self_jump), and the ring colour table never
depends on the wall clock (self_icon._label_lut)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

CASES = ROOT / "tests" / "fixtures" / "real_cases"

#: real frames: me 0.035-0.045 from my ADC's centre, its icon on top of mine (2 games)
FRAMES = [
    ("seraphine_support_20261028", "20261028_155140_0001"),
    ("seraphine_support_20261028", "20261028_162141_0031"),
    ("seraphine_support_20261028", "20261028_162742_0037"),
    ("tk_botlane_20261005", "20261005_163950_0284"),
    ("teemo_top_20261024", "20261024_225311_0044"),
]


@pytest.mark.parametrize("case,frame", FRAMES)
def test_me_found_next_to_the_icon_covering_mine(case, frame):
    import real_minimap_bench as bench
    from treeaicoach.detector import OnnxDetector, create_detector

    if not isinstance(create_detector("onnx", roster=False), OnnxDetector):
        pytest.skip("ONNX detector unavailable")
    fix = CASES / case
    if not (fix / f"{frame}.png").is_file():
        pytest.skip("case frame not present")
    r = bench.run_all(fix=fix, names=[frame])[frame]
    truth = bench.load_truth(fix)["images"][frame]
    me = next(c["name"] for c in truth["champions"] if c.get("self"))
    assert not [m for m in r.misses if m[0] == me], r.misses
    assert not [f for f in r.false_pos if f[0] == me], r.false_pos


def test_learner_jump_of_me_is_ignored_without_the_camera():
    import numpy as np

    from treeaicoach import roster_matcher as rm

    m = rm.RosterMatcher.__new__(rm.RosterMatcher)
    m._tracks = {0: rm._Track(0.5, 0.5, 10.0)}
    m.camlock = rm.CameraLock()
    m._cam_point = lambda bgr: (0.66, 0.5)            # camera not locked: no position
    img = np.zeros((8, 8, 3), np.uint8)
    assert m._self_jump((0.66, 0.5), 10.2, img, 0)       # 0.16 in 0.2 s: impossible walk
    assert not m._self_jump((0.52, 0.5), 10.2, img, 0)   # reachable
    assert not m._self_jump((0.66, 0.5), 12.0, img, 0)   # my track too old: believed
    m.camlock.position = lambda p, now: (0.66, 0.5)      # the locked camera confirms it
    assert not m._self_jump((0.66, 0.5), 10.2, img, 0)


def test_ring_colour_table_does_not_depend_on_the_clock(monkeypatch):
    from treeaicoach import self_icon as si
    from treeaicoach.roster_matcher import RingColorModel

    rings = RingColorModel()
    si._LUT_CACHE.clear()
    a = si._label_lut(rings)
    rings.centroid["ally"] = rings.centroid["ally"] + 6.0       # slight drift
    t = time.monotonic()
    monkeypatch.setattr(si.time, "monotonic", lambda: t + 100.0)
    b = si._label_lut(rings)
    assert b is a                       # within LUT_REBUILD_CALLS calls: same table
    for _ in range(si.LUT_REBUILD_CALLS):
        si._label_lut(rings, count=True)
    assert si._label_lut(rings) is not a
    si._LUT_CACHE.clear()               # (module state: other tests start clean)
