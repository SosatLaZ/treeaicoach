"""tools/real_gate.py: regression gate on all real minimap cases (idea 87)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import real_gate as G  # noqa: E402


def _s(recall, prec, team=1.0, ident=1.0, frames=None):
    return {"recall": recall, "precision": prec, "team": team, "id": ident, "gt": 10,
            "frames": frames or {}}


def test_compare_flags_drops_beyond_the_swarm_tolerances():
    base = {"a": _s(0.900, 1.000, frames={"f1": [3, 0], "f2": [2, 0]}), "b": _s(0.9, 0.9)}
    cur = {"a": _s(0.896, 0.996, frames={"f1": [2, 0], "f2": [2, 0]}), "b": _s(0.9, 0.9), "c": _s(1, 1)}
    bad, notes = G.compare(cur, base)
    assert len(bad) == 1 and bad[0].startswith("a: recall 0.900->0.896") and "f1" in bad[0]
    assert "f2" not in bad[0] and any("new case" in n for n in notes)
    # within tolerance (precision -0.005, recall -0.003): no regression; a gain is a note
    bad, notes = G.compare({"a": _s(0.897, 0.995, ident=1.0)}, {"a": _s(0.900, 1.000, ident=0.98)})
    assert bad == [] and any("id" in n and "better" in n for n in notes)


def test_summarize_and_update_round_trip(tmp_path, monkeypatch):
    import real_minimap_bench as RB

    r = RB.ImageResult(name="f", n_gt=4, n_det=3, tp=3, team_ok=3, id_n=2, id_ok=1)
    s = G.summarize({"f": r})
    assert s["recall"] == 0.75 and s["precision"] == 1.0 and s["id"] == 0.5 and s["frames"] == {"f": [3, 0]}
    monkeypatch.setattr(G, "case_dirs", lambda: {"x": tmp_path})
    monkeypatch.setattr(G, "run_case", lambda p: s)
    bl = tmp_path / "b.json"
    assert G.main(["--update", "--baseline", str(bl)]) == 0
    assert json.loads(bl.read_text())["x"]["recall"] == 0.75
    assert G.main(["--baseline", str(bl)]) == 0
    monkeypatch.setattr(G, "run_case", lambda p: {**s, "recall": 0.5})
    assert G.main(["--baseline", str(bl)]) == 1


def test_baseline_covers_every_real_case():
    if not G.BASELINE.is_file():
        pytest.skip("no baseline yet")
    base = json.loads(G.BASELINE.read_text(encoding="utf-8"))
    assert set(G.case_dirs()) <= set(base)


@pytest.mark.skipif(os.environ.get("TREEAI_REAL_GATE") != "1",
                    reason="full real-case gate (minutes): TREEAI_REAL_GATE=1")
def test_full_real_gate():
    assert G.main(["--jobs", "2"]) == 0
