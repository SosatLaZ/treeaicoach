"""tools/perf_budget.py smoke test: the real engine + overlay threads run on the real screenshot,
every measurement is produced, and the overlay stays within its upload budget on a static scene."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from treeaicoach import paths

ROOT = Path(__file__).resolve().parents[1]


def _tool():
    spec = importlib.util.spec_from_file_location("perf_budget", ROOT / "tools" / "perf_budget.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["perf_budget"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skipif(not (ROOT / "tests" / "fixtures" / "ingame4_2000x1125.jpg").exists(), reason="fixture")
def test_perf_budget_smoke(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    paths._reset_cache()
    pb = _tool()
    r = pb.run(seconds=4.0, warmup=6.0, ui_hz=2.0, perf_mode="normal", quiet=True)
    assert r["ticks"]["n"] > 0 and r["ticks"]["p95_ms"] > 0
    assert r["threads_cpu_pct"].get("TreeAICoach-analysis", 0.0) > 0.0
    assert r["capture"]["calls_per_s"].get("minimap", 0.0) > 0.0
    assert r["capture"]["calls_per_s"].get("full", 0.0) == 0.0       # never the full window in steady state
    assert r["overlay"]["loop_fps"] <= 21.0                            # paced (was 30 whatever happened)
    assert r["overlay"]["pushes_per_s"] <= 10.0
    assert r["engine"]["performance"]["level"] in ("ok", "eleve", "lourd")
    pb.print_report(r)
