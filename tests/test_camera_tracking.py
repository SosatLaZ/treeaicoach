"""Camera moves and tracking quality (the minimap never moves, only the white camera rectangle).

* the camera-rectangle lines are erased before matching (roster_matcher.clean_camera_lines);
* the camera lock (my position from the camera rectangle) unlocks at the first camera step
  faster than a champion walks, so my position never follows a dragged camera;
* the tracker's hide timeout follows the measured detection rate (frames, not seconds);
* the smoothed position does not trail a walking champion;
* synthetic sequences with camera pans / jumps / drags and crossing champions
  (tools/camera_motion_bench.py): no identity error, my position stays right.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from treeaicoach.camera_proj import CameraRect, white_mask  # noqa: E402
from treeaicoach.roster_matcher import CameraLock, clean_camera_lines  # noqa: E402
from treeaicoach.tracker import HIDE_AFTER, Tracker  # noqa: E402


def _ident(u, v, alias="Ahri", relation="enemy"):
    from treeaicoach.detector import Detection
    from treeaicoach.identifier import Identified

    det = Detection(u=u, v=v, r=0.047, score=0.9, cls="enemy" if relation == "enemy" else "ally",
                    cls_probs=(0.9, 0.1, 0.0), alias=alias)
    return Identified(det=det, alias=alias, relation=relation, team=None, id_score=0.9)


def test_camera_lines_are_erased_but_icons_kept():
    rng = np.random.default_rng(0)
    img = rng.integers(40, 120, (300, 300, 3), dtype=np.uint8)
    img[140:170, 140:170] = (30, 200, 230)                     # a coloured "icon" under the line
    rect = CameraRect(0.3, 0.5, 0.575, 0.655)
    y = int(round(0.5 * 300 - 0.5))
    with_line = img.copy()
    with_line[y:y + 2, 88:174] = 245                            # 2 px white top side
    out = clean_camera_lines(with_line, rect)
    assert int(white_mask(out)[y - 2:y + 3, 92:170].sum()) == 0
    # the icon's covered rows come back close to the icon colour, the rest is untouched
    assert np.abs(out[y:y + 2, 145:165].astype(int) - (30, 200, 230)).max() < 40
    assert np.array_equal(out[:y - 3], img[:y - 3])
    assert clean_camera_lines(img, None) is img


def test_camera_lock_never_follows_a_dragged_camera():
    lk = CameraLock()
    t, me = 0.0, (0.40, 0.40)
    for _ in range(6):                                          # locked: camera on me
        cam = (me[0], me[1] - 0.02)
        lk.feed_cam(cam, t)
        lk.confirm(me, cam, t)
        t += 0.125
    assert lk.locked
    assert lk.position((me[0], me[1] - 0.02), t) == pytest.approx(me, abs=0.003)
    # my icon hidden, the user drags the camera slowly (0.12 / s, 0.015 per frame)
    cam = (me[0], me[1] - 0.02)
    for k in range(4):
        cam = (cam[0] + 0.015, cam[1])
        lk.feed_cam(cam, t)
        assert lk.position(cam, t) is None, k
        t += 0.125
    # walking with me (a locked camera follows me): stays locked
    lk2 = CameraLock()
    t, me = 0.0, (0.4, 0.4)
    for k in range(12):
        me = (0.4 + 0.004 * k, 0.4)
        cam = (me[0], me[1] - 0.02)
        lk2.feed_cam(cam, t)
        if k < 8:
            lk2.confirm(me, cam, t)
        t += 0.125
    assert lk2.locked and lk2.position(cam, t - 0.125) == pytest.approx(me, abs=0.005)


def test_hide_timeout_follows_the_detection_rate():
    tr = Tracker()
    t = 0.0
    for _ in range(12):                                        # 4 detections / s
        tr.update(t, [_ident(0.5, 0.5)])
        t += 0.25
    assert tr.hide_after == pytest.approx(0.875) and tr.frame_interval == pytest.approx(0.25)
    for _ in range(3):                                         # three misses: still visible
        tr.update(t, [])
        assert tr.get("Ahri").visible
        t += 0.25
    tr.update(t + 0.25, [])
    assert not tr.get("Ahri").visible
    fast = Tracker()
    for k in range(12):
        fast.update(k / 12.0, [_ident(0.5, 0.5)])
    assert fast.hide_after == pytest.approx(HIDE_AFTER)
    fast.update(11 / 12.0 + 3.0, [])                           # a pause is not a rate
    assert fast.hide_after == pytest.approx(HIDE_AFTER)


@pytest.mark.parametrize("fps", [3.0, 8.0])
def test_position_does_not_trail_a_walking_champion(fps):
    tr = Tracker()
    rng = np.random.default_rng(1)
    for k in range(int(3 * fps)):
        t = k / fps
        tr.update(t, [_ident(0.3 + 0.03 * t + rng.normal(0, 0.001), 0.5 + rng.normal(0, 0.001))])
    track = tr.get("Ahri")
    truth = 0.3 + 0.03 * (int(3 * fps) - 1) / fps
    assert abs(track.position()[0] - truth) < 0.003            # (a plain median: 0.03 / fps behind)


def test_camera_motion_sequences():
    import camera_motion_bench as cb
    from treeaicoach.champions import get_default_db

    db = get_default_db()
    for kind in ("pan", "lockhide", "cross"):
        m = cb.run_scenario(kind, 7, db, n_frames=64)
        assert m.id_err == 0, kind
        assert m.tp / m.n_truth >= 0.9, (kind, m.tp, m.n_truth)
        assert m.tp / max(1, m.tp + m.fp) >= 0.95, kind
        assert m.drops <= 1, kind
        assert m.me_err_frames <= (12 if kind == "lockhide" else 2), (kind, m.me_err_frames)
        assert abs(float(np.mean(m.lag_pos))) < 0.5, kind      # frames behind a walking icon
