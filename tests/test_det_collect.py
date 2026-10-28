"""treeaicoach.det_collect: hard negatives, uncertain frames, "faux" feedback (ideas 34/35/120)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from treeaicoach import det_collect as DC
from treeaicoach.detector import Detection

ROOT = Path(__file__).resolve().parents[1]


def _d(u, v, score, cls="enemy", alias=None):
    return Detection(u, v, 0.045, score, cls, (0.0, 1.0, 0.0), alias)


def _img():
    return np.full((120, 120, 3), 60, np.uint8)


def test_hard_negatives_dead_and_team_excess():
    dets = [_d(0.1, 0.1, 0.9, alias="Zed"), _d(0.5, 0.5, 0.95), _d(0.6, 0.6, 0.4), _d(0.7, 0.7, 0.8),
            _d(0.2, 0.8, 0.9, "ally")]
    assert DC.hard_negatives(dets, dead=["Zed"]) == [0]
    # 2 enemies alive, 3 enemy icons besides the dead one: the lowest score is false
    assert DC.hard_negatives(dets, dead=["Zed"], alive_counts={"enemy": 2, "ally": 5}) == [0, 2]
    assert DC.hard_negatives([object()], alive_counts={"enemy": 0}) == []    # never raises


def test_uncertain_band_and_weak_identity():
    dets = [_d(0.1, 0.1, 0.3), _d(0.2, 0.2, 0.9, alias="Lux"), _d(0.3, 0.3, 0.9, alias="Ahri")]
    assert DC.uncertain(dets, {"Lux": 0.4, "Ahri": 0.9}) == [0, 1]


def test_collector_rate_limit_cap_and_export(tmp_path):
    c = DC.DetCollector(max_frames=3, min_gap_s=5.0)
    assert c.feed(_img(), [_d(0.5, 0.5, 0.9)], now=0.0) is None              # nothing to learn
    assert c.feed(_img(), [_d(0.5, 0.5, 0.9, alias="Zed")], now=1.0, dead=["Zed"]) == "negative"
    assert c.feed(_img(), [_d(0.5, 0.5, 0.9, alias="Zed")], now=2.0, dead=["Zed"]) is None   # gap
    assert c.feed(_img(), [_d(0.4, 0.4, 0.3)], now=3.0) == "uncertain"
    for t in (10.0, 20.0, 30.0):
        c.feed(_img(), [_d(0.4, 0.4, 0.3)], now=t)
    assert len(c) == 3 and c.counts()["negative"] == 1                     # oldest common dropped
    assert c.mark_wrong(now=31.0) >= 1 and c.counts().get("disputed", 0) >= 1
    out = c.export_case(tmp_path / "case")
    gt = json.loads((out / "ground_truth.json").read_text())
    specs = list(gt["images"].values())
    neg = [s for s in specs if s["kind"] == "negative"][0]
    assert neg["focus"] and neg["champions"] == [] and not neg.get("needs_review")
    assert all(s.get("needs_review") for s in specs if s["kind"] != "negative")
    assert all((out / s["file"]).is_file() for s in specs)
    assert all(k.startswith("det_collect/") for k in c.zip_entries())
    assert DC.DetCollector().export_case(tmp_path / "none") is None


def test_trainer_loads_collected_frames_focus_only(tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import pytest
    pytest.importorskip("torch")
    import det_train_fast as dtf

    c = DC.DetCollector(min_gap_s=0.0)
    c.feed(_img(), [_d(0.5, 0.5, 0.9, alias="Zed"), _d(0.2, 0.2, 0.9)], now=1.0, dead=["Zed"])
    c.feed(_img(), [_d(0.4, 0.4, 0.3)], now=2.0)                              # needs review: skipped
    case = c.export_case(tmp_path / "case")
    tr, val = dtf.load_real_cases(64, case)
    assert len(tr) == 1 and val == []
    _img_, labs, zones = tr[0]
    assert labs == [] and any(z.get("focus") for z in zones)
    hm = np.zeros((1, 16, 16), np.float32)
    dtf._ignore_mask(hm, zones, 0.0, 0.0, 64.0, 64.0)
    assert hm[0, 8, 8] == 0.0 and abs(hm[0, 1, 14] - dtf.IGNORE) < 1e-6                # trained only at the FP


def test_case_backgrounds_paint_out_labels_and_unsure_zones():
    """Idea 36: the real cases become training backgrounds (icons + unsure zones painted out)."""
    from training import real_art

    case = ROOT / "tests" / "fixtures" / "real_cases" / "teemo_top_20261024"
    if not (case / "ground_truth.json").is_file():
        import pytest
        pytest.skip("case missing")
    bgs = real_art.case_backgrounds(case.name, size=120)
    gt = json.loads((case / "ground_truth.json").read_text())
    assert len(bgs) == len(gt["images"]) and bgs[0].shape == (120, 120, 3)
    art = real_art.RealArt(real_art.load_real(names=["shot3", "shot4", "shot6"]), cases=[case.name])
    assert len(art.backgrounds) == 4 + len(bgs)
    assert real_art.case_backgrounds("no_such_case") == []
