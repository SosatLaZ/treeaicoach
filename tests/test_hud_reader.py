"""Tests of the bottom-centre HUD portrait reader (treeaicoach/hud_reader.py)."""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from treeaicoach.hud_reader import HudReader, locate_portrait

FIXTURE = Path(__file__).parent / "fixtures" / "hud_ingame_1280.jpg"


@pytest.fixture(scope="module")
def screen():
    img = cv2.imread(str(FIXTURE))
    if img is None:
        pytest.skip("HUD fixture missing")
    return img


def test_locate_portrait_on_real_screenshot(screen):
    # real 2000x1125 user screenshot (portrait at ~(748, 1070), r 38) resized to 1280x720
    cx, cy, r = locate_portrait(screen)
    assert abs(cx - 479) <= 4 and abs(cy - 685) <= 4 and 21 <= r <= 27


def test_locate_scales_with_resolution(screen):
    big = cv2.resize(screen, (1920, 1080), interpolation=cv2.INTER_LINEAR)
    cx, cy, r = locate_portrait(big)
    assert abs(cx - 719) <= 6 and abs(cy - 1027) <= 6 and 32 <= r <= 41


def test_read_alive_then_dead_and_fast(screen):
    rd = HudReader()
    a = rd.read(screen, alive_hint=True)
    assert a is not None and a.dead is False and a.portrait.shape == (64, 64, 3)
    t0 = time.perf_counter()
    for _ in range(20):
        rd.read(screen)
    assert (time.perf_counter() - t0) / 20 < 0.003
    grey = screen.copy()
    cx, cy, r = rd.location
    roi = grey[cy - r:cy + r, cx - r:cx + r]
    grey[cy - r:cy + r, cx - r:cx + r] = cv2.cvtColor(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY),
                                                       cv2.COLOR_GRAY2BGR)
    d = rd.read(grey)
    assert d is not None and d.dead is True
    assert rd.read(screen).dead is False          # the dead frame did not spoil the reference


def test_never_raises():
    rd = HudReader()
    assert rd.read(None) is None
    assert rd.read(np.zeros((100, 100, 3), np.uint8)) is None
    assert rd.read(np.zeros((720, 1280, 3), np.uint8)) is None
    assert locate_portrait("x") is None
