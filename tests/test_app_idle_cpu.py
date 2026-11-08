"""PERF4 (4.0.1): the app costs ~nothing when nothing happens (no League game, WoW not running, or WoW idle).

The real window (offscreen), backend, engine and WoW-mode thread run through ``tools/app_idle_cpu.py``; the process
CPU over the measured window must stay far below one core. Tolerant thresholds (shared CI machines): the target is
< 1 % on a desktop PC (measured: 0.4 % idle, see CHANGELOG 4.0.1)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "app_idle_cpu.py"

pytest.importorskip("PySide6")


@pytest.mark.parametrize("wow", [False, True])
def test_app_idle_cpu(tmp_path, wow):
    out = tmp_path / "idle.json"
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "TREEAICOACH_HOME": str(tmp_path / "home"),
           "TREEAICOACH_NO_SPLASH": "1"}
    cmd = [sys.executable, str(TOOL), "--warmup", "8", "--seconds", "20", "--json", str(out)]
    if wow:
        cmd.append("--wow-running")
    r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=240)
    assert r.returncode == 0 and out.exists(), r.stdout[-3000:] + r.stderr[-3000:]
    res = json.loads(out.read_text(encoding="utf-8"))
    # no busy loop anywhere: a few % of one core at most (desktop: < 1 %)
    assert res["cpu_pct"] < 8.0, res
    # WoW running, no League game: the League vision model (ONNX detector) is not even loaded
    if wow:
        assert res["detector_loaded"] is False, res
