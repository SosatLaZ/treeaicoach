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


def test_roi_patch_read_matches_full_read(screen):
    rd = HudReader()
    assert rd.roi() is None and rd.read_patch(screen) is None      # not calibrated yet
    full = rd.read(screen, alive_hint=True)
    x, y, w, h = rd.roi()
    assert w == h and w > 10
    patch = screen[y:y + h, x:x + w]
    t0 = time.perf_counter()
    for _ in range(20):
        p = rd.read_patch(patch)
    assert (time.perf_counter() - t0) / 20 < 0.002
    assert p is not None and p.dead is False
    assert np.abs(p.portrait.astype(int) - full.portrait.astype(int)).mean() < 1.0
    grey = cv2.cvtColor(cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    assert rd.read_patch(grey).dead is True
    assert rd.read_patch(None) is None


def test_calibration_timing(screen):
    big = cv2.resize(screen, (1920, 1080))
    t0 = time.perf_counter()
    assert locate_portrait(big) is not None
    assert time.perf_counter() - t0 < 0.1                         # once per window size


def test_dead_greyscale_portrait_on_real_bars():
    """Real 2000 x 1125 bars (tests/fixtures/hud_bar, crops at their screen offsets): the red
    death timer in the portrait centre and a too-big Hough circle (gold frame / XP arc in the
    crop) kept dead at 0.40 x the alive saturation (threshold 0.45); the ring measure keeps
    every dead read far below (real captures: <= 0.18)."""
    import re

    bars = sorted((Path(__file__).parent / "fixtures" / "hud_bar").glob("shot*_2000x1125_*.jpg"))
    if len(bars) < 4:
        pytest.skip("hud_bar fixtures missing")

    def screen_of(p: Path) -> np.ndarray:
        x, y = (int(v) for v in re.search(r"_at_(\d+)_(\d+)", p.name).groups())
        img = np.full((1125, 2000, 3), 30, np.uint8)
        crop = cv2.imread(str(p))
        img[y:y + crop.shape[0], x:x + crop.shape[1]] = crop[:1125 - y, :2000 - x]
        return img

    alive = [p for p in bars if "alive" in p.name or "lvl1" in p.name]
    dead = [p for p in bars if "dead" in p.name]
    assert alive and len(dead) >= 3
    h = HudReader()
    for p in alive:
        r = h.read(screen_of(p), alive_hint=True)
        assert r is not None
    for p in dead:
        r = h.read(screen_of(p))
        assert r is not None and r.dead is True, p.name
        assert r.saturation < 0.25 * h._ref_sat, (p.name, r.saturation, h._ref_sat)
    for p in alive:
        assert h.read(screen_of(p)).dead is False
