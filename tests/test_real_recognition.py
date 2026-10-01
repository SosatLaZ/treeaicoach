"""Recognition regression on REAL in-game minimap crops (tests/fixtures/real, ground truth in
ground_truth.json - see tools/real_minimap_bench.py for the format and the scoring).

The user's screenshots (2000 x 1125 client, 300 px minimap, 2025-26 map art: plate-count
turret badges, hourglass camps, gold / green markers, red wall outline late game) also contain
the TreeAI overlay drawn over the minimap (rings, role labels, dashed circles): icons covered
by those marks are the remaining misses. Thresholds = measured values minus a small margin.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import real_minimap_bench as bench  # noqa: E402

#: image -> (min recall, min precision, min team accuracy, min identity accuracy)
#: measured (roster matcher + 2026 ONNX extras): recall shot2 1.0, shot3 0.8, shot4 0.86,
#: shot5 1.0, shot6 0.88, shot7/8/9 0.75, shot10 1.0 (total 0.87); precision and team
#: accuracy 1.0 everywhere; identity 1.0 except shot4 0.83 (one anonymous extra).
THRESHOLDS = {
    "shot2": (1.0, 1.0, 1.0, 1.0),
    "shot3": (0.8, 1.0, 1.0, 1.0),
    "shot4": (0.71, 1.0, 1.0, 0.8),
    "shot5": (0.75, 1.0, 1.0, 1.0),
    "shot6": (0.75, 0.85, 1.0, 1.0),
    "shot7": (0.5, 1.0, 1.0, 1.0),
    "shot8": (0.5, 1.0, 1.0, 1.0),
    "shot9": (0.5, 1.0, 1.0, 1.0),
    "shot10": (0.75, 1.0, 1.0, 1.0),
    "shot14": (0.8, 1.0, 1.0, 1.0),   # 384 px minimap (larger HUD scale), two enemies stacked
}
TOTAL_MIN_RECALL = 0.82
TOTAL_MIN_PRECISION = 0.97


@pytest.fixture(scope="module")
def results():
    from treeaicoach.detector import OnnxDetector, create_detector

    if not isinstance(create_detector("onnx", roster=False), OnnxDetector):
        pytest.skip("ONNX detector unavailable (the app's default generic detector)")
    return bench.run_all("onnx")


def test_ground_truth_is_consistent():
    truth = bench.load_truth()
    assert set(truth["images"]) == set(THRESHOLDS)
    for name, spec in truth["images"].items():
        assert (bench.FIX / spec["file"]).is_file(), name
        roster = truth["rosters"][spec["game"]]
        names = {roster["self"], *roster["ally"], *roster["enemy"]}
        for c in spec["champions"]:
            assert 0.0 <= c["u"] <= 1.0 and 0.0 <= c["v"] <= 1.0
            assert c["team"] in ("ally", "enemy")
            assert c["name"] is None or c["name"] in names, (name, c)


@pytest.mark.parametrize("name", sorted(THRESHOLDS))
def test_real_minimap_recognition(results, name):
    r = results[name]
    rec, prec, team, ident = THRESHOLDS[name]
    assert r.recall >= rec - 1e-9, (name, r.recall, r.misses)
    assert r.precision >= prec - 1e-9, (name, r.precision, r.false_pos)
    assert r.team_acc >= team - 1e-9, (name, r.wrong)
    assert r.id_acc >= ident - 1e-9, (name, r.wrong)


def test_real_minimap_totals(results):
    gt = sum(r.n_gt for r in results.values())
    det = sum(r.n_det for r in results.values())
    tp = sum(r.tp for r in results.values())
    assert tp / gt >= TOTAL_MIN_RECALL, (tp, gt)
    assert tp / max(1, det) >= TOTAL_MIN_PRECISION, (tp, det)
