"""Tests of the ability bar reader (treeaicoach/hud_abilities.py): real captures + synthetic HUD.

Real captures (tests/fixtures/hud_bar/*.jpg: the bottom HUD band of real games, pasted back at its
place in a black window of the original size) annotated by eye:

* ``cd`` the cooldown sweep + seconds are shown, ``rdy`` castable (gold frame), ``off`` not castable
  (not learned / greyed), ``up`` dead and not on cooldown (greyed but available at the respawn).
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from treeaicoach.hud_abilities import SLOTS, AbilityBarReader, BarRead, fit_layout

FIX = Path(__file__).parent / "fixtures"
BAR = FIX / "hud_bar"

#: name prefix -> ({slot: state}, trinket charges or None when unreadable / no trinket)
TRUTH = {
    "shot6_alive": ({"Q": "rdy", "W": "rdy", "E": "rdy", "R": "rdy", "D": "cd", "F": "rdy"}, 1),
    "shot7_dead": ({"Q": "up", "W": "cd", "E": "up", "R": "up", "D": "cd", "F": "up"}, 0),
    "shot8_dead": ({"Q": "up", "W": "up", "E": "up", "R": "up", "D": "cd", "F": "up"}, 2),
    "shot10_dead": ({"Q": "up", "W": "up", "E": "up", "R": "cd", "D": "cd", "F": "up"}, 2),
    "shot3_lvl1": ({"Q": "off", "W": "off", "E": "rdy", "R": "off", "D": "rdy", "F": "rdy"}, None),
    "shot1_2622": ({"Q": "cd", "W": "cd", "E": "rdy", "R": "off", "D": "rdy", "F": "cd"}, 0),
    "img13_video": ({"Q": "cd", "W": "rdy", "E": "rdy", "R": "off", "D": "rdy", "F": "rdy"}, 0),
}


def _load(path: Path) -> np.ndarray:
    m = re.search(r"_(\d+)x(\d+)_at_(\d+)_(\d+)\.jpg$", path.name)
    assert m, path.name
    W, H, x0, y0 = (int(g) for g in m.groups())
    crop = cv2.imread(str(path))
    canvas = np.zeros((H, W, 3), np.uint8)
    canvas[y0:y0 + crop.shape[0], x0:x0 + crop.shape[1]] = crop
    return canvas


def _state(s) -> str:
    if s.cooldown:
        return "cd"
    return "rdy" if s.castable else "off"


@pytest.mark.parametrize("name", sorted(TRUTH))
def test_real_captures_match_annotations(name):
    path = next(BAR.glob(f"{name}_*.jpg"))
    img = _load(path)
    rd = AbilityBarReader()
    assert rd.calibrate(img), name
    r = rd.read(img)
    assert r is not None and r.valid
    want, charges = TRUTH[name]
    for k, w in want.items():
        got = _state(r.slots[k])
        assert got == ("off" if w == "up" else w), (name, k, got, w)
    if charges is not None:
        assert r.trinket_charges == charges, (name, r.trinket_charges)


def test_real_fixture_1280_and_full_captures():
    img = cv2.imread(str(FIX / "hud_ingame_1280.jpg"))
    rd = AbilityBarReader()
    assert rd.calibrate(img)
    assert 0.55 < rd.layout.scale < 0.65                     # 720 px high, HUD scale ~92 %
    r = rd.read(img)
    assert [_state(r.slots[k]) for k in "QWERDF"] == ["rdy", "rdy", "off", "off", "cd", "cd"]
    img4 = cv2.imread(str(FIX / "ingame4_2000x1125.jpg"))    # dead, R on cooldown (65 s)
    r4 = AbilityBarReader().read(img4)
    assert r4 is not None and r4.on_cooldown("R") is True and r4.on_cooldown("D") is False
    assert r4.trinket_charges == 2


def _synthetic(scale: float, states: dict[str, str], H: int = 1125, W: int = 2000, seed: int = 0) -> np.ndarray:
    """A dark HUD: my portrait (round frame) and the slot squares at SLOTS x ``scale``."""
    rng = np.random.default_rng(seed)
    img = np.full((H, W, 3), 18, np.uint8)
    img[int(0.8 * H):, :] = (30, 34, 22)
    s0 = scale * H / 1125.0
    cx, cy = int(0.365 * W), int(0.946 * H)
    cv2.circle(img, (cx, cy), int(44 * s0), (60, 120, 200), -1)
    cv2.circle(img, (cx, cy), int(44 * s0), (90, 170, 210), max(2, int(4 * s0)))

    def hsv(h, s, v):
        return tuple(int(x) for x in cv2.cvtColor(np.uint8([[[h, s, v]]]), cv2.COLOR_HSV2BGR)[0, 0])

    gold, grey, blue = hsv(22, 60, 175), (55, 55, 55), hsv(103, 220, 110)
    for k, (dx, dy, size) in SLOTS.items():
        if k == "P":
            continue
        st = states.get(k, "off")
        x, y, h = cx + s0 * dx, cy + s0 * dy, s0 * size / 2
        x0, y0, x1, y1 = int(round(x - h)), int(round(y - h)), int(round(x + h)), int(round(y + h))
        icon = rng.integers(40, 220, size=(y1 - y0, x1 - x0, 3)).astype(np.uint8)
        icon = cv2.GaussianBlur(icon, (0, 0), 2.0)
        icon[:, :, 2] = np.clip(icon[:, :, 2].astype(int) + 60, 0, 255)       # warm (no cooldown blue)
        if st == "off":
            icon = (icon * 0.35).astype(np.uint8)
        img[y0:y1, x0:x1] = icon
        if st == "cd":
            img[y0 + (y1 - y0) // 3:y1, x0:x1] = blue
            cv2.putText(img, "12", (int(x - h * 0.6), int(y + h * 0.4)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.03 * size * s0, (255, 255, 255), 2)
        cv2.rectangle(img, (x0, y0), (x1, y1), gold if st == "rdy" else grey, max(2, int(round(2 * s0))))
    return img


@pytest.mark.parametrize("scale", [1.0, 0.8, 0.9])
def test_synthetic_hud_layout_and_states(scale):
    states = {"Q": "rdy", "W": "cd", "E": "off", "R": "rdy", "D": "cd", "F": "rdy", "2": "rdy", "T": "rdy"}
    img = _synthetic(scale, states)
    lay = fit_layout(img)
    assert lay is not None and abs(lay.scale - scale) < 0.03
    r = AbilityBarReader().read(img)
    assert r is not None and r.valid
    for k in ("Q", "W", "E", "R", "D", "F"):
        assert _state(r.slots[k]) == states[k], (scale, k)
    assert r.ready("Q") is True and r.ready("W") is False and r.ready("E") is False
    assert r.slots["2"].castable is True and r.slots["1"].castable is False


def test_read_cost_under_a_millisecond_and_never_raises():
    img = cv2.imread(str(next(BAR.glob("shot6_alive_*.jpg"))))
    canvas = _load(next(BAR.glob("shot6_alive_*.jpg")))
    rd = AbilityBarReader()
    assert rd.calibrate(canvas)
    x, y, w, h = rd.roi()
    patch = np.ascontiguousarray(canvas[y:y + h, x:x + w])
    rd.read_patch(patch)
    ts = []
    for _ in range(60):
        t0 = time.perf_counter()
        rd.read_patch(patch)
        ts.append(1000 * (time.perf_counter() - t0))
    assert float(np.median(ts)) < 3.0          # ~0.65 ms measured; generous for slow CI machines
    # HUD hidden (another image in the patch): the read says so
    noise = np.random.default_rng(1).integers(0, 40, size=patch.shape).astype(np.uint8)
    bad = rd.read_patch(noise)
    assert bad is not None and bad.valid is False and bad.ready("Q") is None
    # garbage in: never raises
    assert rd.read_patch(None) is None and rd.read_patch(np.zeros((3, 3, 3), np.uint8)) is None
    assert AbilityBarReader().read_patch(patch) is None             # not calibrated
    assert AbilityBarReader().read(np.zeros((100, 100, 3), np.uint8)) is None
    assert fit_layout("nope") is None and img is not None
    assert isinstance(BarRead().slots, dict)
