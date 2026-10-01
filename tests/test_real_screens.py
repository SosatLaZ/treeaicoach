"""Regression tests on REAL in-game screenshots sent by a user (2000 x 1125, minimap 300 px):

* ingame3 (0:26, red side, overlay visible in the capture): minimap located, generic detectors
  find the walking allies and never fire on the new turret "5" plate badges / camp hourglasses;
  the stacked bot duo stays a known limit;
* ingame4 (15:25, dead, shop open): minimap located;
* ingame5..10 minimap crops (client on top, late game, deaths, base siege, ace): the crops are
  still recognised as the minimap (verify), and an occluded minimap is never analysed.

Ground truth = icon centres measured on the screenshots (normalized u, v).
"""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
import pytest

from treeaicoach.capture import Rect
from treeaicoach.minimap_locator import VERIFY_MIN_SCORE, MinimapLocator
from treeaicoach.render import CAMPS, STRUCTURES

FIX = Path(__file__).resolve().parent / "fixtures"
MM = Rect(1687, 809, 300, 300)
#: ingame3 (0:26): walking allies (mid, jungle) + the stacked bot duo + me (Garen, red fountain)
TRUTH3 = {"mid": (0.533, 0.450), "jungle": (0.756, 0.539), "bot1": (0.886, 0.628), "bot2": (0.889, 0.667),
          "me": (0.965, 0.03)}


def load(name: str) -> np.ndarray:
    img = cv2.imread(str(FIX / name))
    assert img is not None, name
    return img


@pytest.fixture(scope="module")
def locator() -> MinimapLocator:
    return MinimapLocator()


@pytest.mark.parametrize("name,min_score", [("ingame3_2000x1125.jpg", 0.85), ("ingame4_2000x1125.jpg", 0.8)])
def test_minimap_located_on_real_screens(locator, name, min_score):
    img = load(name)
    loc = locator.locate(img, Rect(0, 0, img.shape[1], img.shape[0]))
    assert loc is not None and loc.score >= min_score
    r = loc.rect
    assert abs(r.x - MM.x) <= 3 and abs(r.y - MM.y) <= 3 and abs(r.w - MM.w) <= 4


@pytest.mark.parametrize("n", [3, 4, 5, 6, 7, 8, 9, 10])
def test_real_minimap_crops_verify(locator, n):
    """Late game (reddish wall outline), death screens, base siege, ace: still the minimap."""
    crop = load(f"ingame{n}_minimap.png")
    assert crop.shape[:2] == (300, 300)
    assert locator.verify(crop) >= max(VERIFY_MIN_SCORE, 0.6)


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


@pytest.mark.parametrize("backend", ["onnx", "classic"])
def test_no_detection_on_plate_badges_or_camps(backend):
    from treeaicoach.detector import HybridDetector, create_detector

    det = create_detector(backend, roster=False)
    crop = load("ingame3_minimap.png")
    structs = [(u, v) for u, v, _k, _t in STRUCTURES]
    camps = [(u, v) for u, v, _n in CAMPS]
    for d in det.detect(crop):
        near_truth = min(_dist((d.u, d.v), p) for p in TRUTH3.values()) <= 0.04
        if near_truth:
            continue
        # anything else must be a structure glyph / fountain the hybrid filter drops - never a
        # "5" plate badge away from its turret, never a camp hourglass
        near_struct = min(_dist((d.u, d.v), p) for p in structs) <= HybridDetector.STRUCTURE_DIST
        assert near_struct, (backend, round(d.u, 3), round(d.v, 3))
        assert min(_dist((d.u, d.v), p) for p in camps) > 0.03


def test_generic_detectors_find_the_walking_allies():
    from treeaicoach.detector import create_detector

    crop = load("ingame3_minimap.png")
    found = {k: False for k in ("mid", "jungle")}
    for backend in ("onnx", "classic"):
        for d in create_detector(backend, roster=False).detect(crop):
            for k in found:
                if _dist((d.u, d.v), TRUTH3[k]) <= 0.02:
                    found[k] = True
    assert all(found.values()), found
    # known limit (reported): the stacked bot duo (0.886, 0.628) / (0.889, 0.667) is one blob


def test_occluded_minimap_is_never_analysed():
    """Screenshot 5: the League client covers the game; the minimap crop is client / taskbar
    pixels at its border. With the occlusion probe True the engine reads no frame at all."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import test_engine as TE
    from treeaicoach.config import Config
    from treeaicoach.engine import MSG_OCCLUDED

    cap = TE.FakeCapture()
    loc = TE.FakeLocator(cap)
    eng, clock, _ = TE.live_engine(cap, loc, cfg=Config())
    eng.occlusion_probe = lambda r: True
    for _ in range(10):
        clock.t += 0.125
        eng.step(clock.t)
    rect = eng.get_status().minimap_rect
    assert rect is not None and not any(g == rect for g in cap.grabs)
    assert eng._identified == [] and eng.get_status().message == MSG_OCCLUDED
