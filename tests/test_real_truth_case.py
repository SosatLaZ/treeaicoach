"""tools/real_truth_case.py (real captures + LCU truth -> labelled case) and the bench's
truth-case rules (ignore zones, stack tie-break)."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import real_truth_case as RT  # noqa: E402

CASE = ROOT / "tests" / "fixtures" / "real_cases" / "tk_botlane_20261005"


def _truth():
    parts = [{"id": i, "team": "ORDER" if i <= 5 else "CHAOS", "alias": f"C{i}", "name": f"C{i}",
              "position": "UTILITY" if i == 5 else "TOP"} for i in range(1, 11)]
    frames = [[60.0, {str(i): [0.1 * i, 0.5, 0, 0, 0, 3] for i in range(1, 11)}],
              [120.0, {str(i): [0.1 * i + (0.01 if i != 3 else 0.2), 0.5, 0, 0, 0, 5] for i in range(1, 11)}]]
    return {"me": 5, "my_team": "ORDER", "participants": parts, "frames": frames,
            "kills": [[90.0, 7, 2, [], 0.3, 0.3]], "duration": 200.0}


def test_truth_estimate_anchor_interp_dead():
    T = RT.Truth(_truth())
    assert T.side(5) == "ally" and T.side(7) == "enemy" and T.alias(5) == "C5"
    u, v, reach, how = T.estimate(1, 62.0)                 # 2 s from the minute frame
    assert how == "anchor" and abs(u - 0.1) < 1e-9 and reach == pytest.approx(0.03 + 2 * RT.MAX_SPEED)
    u, v, reach, how = T.estimate(1, 90.0)                 # barely moved: interpolated
    assert how == "interp" and abs(u - 0.105) < 1e-9
    assert T.estimate(3, 90.0) is None                     # moved 0.2 in the minute: unknown
    assert T.is_dead(2, 95.0) and not T.is_dead(2, 89.0)  # the kill at 90 s (level 3: 12 s timer)
    assert T.estimate(2, 91.0) is not None and T.estimate(2, 91.0)[3] == "anchor"   # the kill spot


def test_fit_offset_finds_the_delta():
    start = datetime.fromisoformat("2026-10-05T16:00:00+02:00")
    caps, walls, dets = [], {}, {}
    record = {"meta": {"champion": "C5"}, "my_positions": [], "sightings": {"C7": []}, "allies": {}}
    for k in range(30):
        name = f"20261005_16{k // 6:02d}{(k % 6) * 10:02d}_0001.png"      # every 10 s
        caps.append(Path(name))
        walls[name] = RT.wall_time(name, start.tzinfo)
        tw = (walls[name] - start).total_seconds()
        gt = tw - 13.0                                                      # the true delta
        dets[name] = [{"u": 0.2 + 0.001 * gt, "v": 0.5, "cls": "enemy", "alias": "C7", "score": 0.9}]
    for t in np.arange(0, 400, 0.5):
        record["sightings"]["C7"].append([float(t), 0.2 + 0.001 * t, 0.5])
    delta, mean, n = RT.fit_offset(caps, walls, start, dets, record, lo=0.0, hi=30.0, step=0.5)
    assert delta == pytest.approx(13.0) and mean < 0.002 and n >= 20


def test_label_frame_snaps_unique_icons_and_flags_the_rest(monkeypatch):
    T = RT.Truth(_truth())
    from treeaicoach.self_icon import RingCand

    # icons: C1 alone, C3 unknown, C4 + C5 (me) stacked on one icon, nothing for C2
    cands = [RingCand(0.102, 0.503, "ally", 0.8), RingCand(0.45, 0.5, "ally", 0.8),
             RingCand(0.602, 0.5, "enemy", 0.8), RingCand(0.7, 0.5, "enemy", 0.8)]
    monkeypatch.setattr(RT, "icon_candidates", lambda img: cands)
    spec = RT.label_frame(np.zeros((8, 8, 3), np.uint8), T, 62.0)
    by = {}
    for c in spec["champions"]:
        by.setdefault(c["name"], []).append(c)
    assert by["C1"][0]["u"] == pytest.approx(0.102) and "self" not in by["C1"][0]
    assert by["C6"] and by["C7"] and "C2" not in by          # C2: no icon -> unsure zone
    assert any(z["name"] == "C2" and "r" in z for z in spec["unsure"])
    me = [c for c in spec["champions"] if c.get("self")]
    assert me and me[0]["name"] == "C5" and me[0]["stacked"] and by["C4"][0]["stacked"]
    assert spec["fp_judged"] == {"ally": True, "enemy": True}
    assert all(set(c) <= {"u", "v", "team", "name", "how", "self", "stacked", "could_be"}
               for c in spec["champions"])


@pytest.mark.skipif(not CASE.is_dir(), reason="case not committed")
def test_committed_case_is_sanitized_and_consistent():
    gt = json.loads((CASE / "ground_truth.json").read_text(encoding="utf-8"))
    txt = json.dumps(gt)
    assert "#" not in txt and "riot" not in txt.lower() and "summoner" not in txt.lower()
    ro = gt["rosters"]["g1"]
    names = {ro["self"], *ro["ally"], *ro["enemy"]}
    assert len(names) == 10
    assert 20 <= len(gt["images"]) <= 40
    for name, spec in gt["images"].items():
        assert (CASE / spec["file"]).is_file() and spec["champions"]
        for c in spec["champions"]:
            assert 0.0 <= c["u"] <= 1.0 and 0.0 <= c["v"] <= 1.0 and c["team"] in ("ally", "enemy")
            assert c.get("name") is None or c["name"] in names
            assert c.get("name") not in spec["dead"]
        assert set(spec["fp_judged"]) == {"ally", "enemy"}


def test_bench_ignores_unsure_zones_and_breaks_stack_ties(monkeypatch):
    import real_minimap_bench as RB
    from treeaicoach.detector import Detection

    class Det:
        class matcher:
            @staticmethod
            def set_entries(e):
                pass

            @staticmethod
            def set_dead(d, now=0.0):
                pass

            detect = None

        def detect(self, img):
            return [Detection(0.5, 0.5, 0.04, 0.9, "ally", (0.03, 0.97, 0.0), "B"),
                    Detection(0.8, 0.8, 0.04, 0.9, "enemy", (0.97, 0.03, 0.0), "X"),
                    Detection(0.2, 0.2, 0.04, 0.9, "enemy", (0.97, 0.03, 0.0), "Y")]

    monkeypatch.setattr(RB, "_detect", lambda det, img, t: det.detect(img))
    monkeypatch.setattr(RB, "roster_entries", lambda game, db, seed=0: [])
    monkeypatch.setattr(RB.cv2 if hasattr(RB, "cv2") else __import__("cv2"), "imread",
                        lambda p: np.zeros((8, 8, 3), np.uint8))
    truth = {"rosters": {"g": {"self": "A", "ally": ["B"], "enemy": ["X", "Y"]}}}
    spec = {"game": "g", "file": "x.png", "champions": [
        {"u": 0.5, "v": 0.5, "team": "ally", "name": "A", "self": True, "stacked": True},
        {"u": 0.5, "v": 0.5, "team": "ally", "name": "B", "stacked": True}],
        "unsure": [{"name": "X", "team": "enemy", "u": 0.78, "v": 0.78, "r": 0.05}],
        "fp_judged": {"ally": True, "enemy": True}}
    r = RB.run_image("x", spec, truth, db=None, detector=Det(), frames=1, fix=Path("."))
    assert r.tp == 1 and r.id_ok == 1 and r.id_n == 1          # B's detection took B's label
    assert r.false_pos == [("Y", "enemy", 0.2, 0.2, 0.9)]     # X ignored (unsure zone), Y judged
    spec["fp_judged"]["enemy"] = False
    r = RB.run_image("x", spec, truth, db=None, detector=Det(), frames=1, fix=Path("."))
    assert r.false_pos == [] and r.n_det == 1
