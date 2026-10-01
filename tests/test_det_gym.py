"""Detection gym (tools/det_gym.py): ceilings on the quick scoreboard + determinism.

The gym renders short synthetic games on the real 2026 minimap art and runs every frame
through the engine's own detection path (HybridDetector -> identifier -> stabilize -> tracker
-> overlay views). The ceilings below are a regression net a little under the measured
values (see the gym's docstring for the metric definitions); the real-crop set is scored too.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

pytest.importorskip("cv2")


@pytest.fixture(scope="module")
def board():
    import det_gym

    if not (ROOT / "tests" / "fixtures" / "real" / "ground_truth.json").is_file():
        pytest.skip("real minimap crops missing")
    return det_gym.run(quick=True, real=True)


def test_quick_games_scoreboard(board):
    s = board["total"]
    assert s["frames"] >= 250
    assert s["rec"] >= 0.93, s                  # visible icons drawn live, right identity
    assert s["prec"] >= 0.95, s                 # live icons that are the right champion
    assert s["g_dead"] == 0, s                  # a dead champion is never drawn
    assert s["idsw"] <= 12, s
    assert s["team"] <= 10, s
    assert s["err95"] <= 0.016, s               # drawn position error (minimap units)
    assert s["me_bad"] <= 0.02, s               # my position > 0.03 off
    assert s["me50"] <= 0.006, s
    assert s["g_live"] <= 25, s                 # visible enemy drawn as a ghost / last-seen mark


def test_real_crops(board):
    r = board["real"]
    assert r["gt"] == 50
    assert r["rec"] >= 0.88, r
    assert r["prec"] >= 0.99, r
    assert r["team"] >= 0.99, r
    assert r["id"] >= 0.97, r


def test_gym_is_deterministic():
    import det_gym

    from training.real_art import RealArt
    from treeaicoach.champions import get_default_db

    db, art = get_default_db(), RealArt()
    runs = []
    det_gym._patch_clock()
    try:
        for _ in range(2):
            sc = [s for s in det_gym.scenarios(quick=True) if s.name == "laning"][0]
            sc.seconds = 4.0
            runs.append(det_gym.run_game(sc, db, art).summary())
    finally:
        det_gym._unpatch_clock()
    a, b = runs
    keys = ("rec", "prec", "idsw", "frag", "err50", "g_live", "me50")
    assert {k: a[k] for k in keys} == {k: b[k] for k in keys}
