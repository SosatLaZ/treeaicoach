"""Diagnostic bundle recorder (diag.py) + engine hooks (health monitor, snapshot, start)."""

from __future__ import annotations

import json
import time
import zipfile

import numpy as np

from treeaicoach import diag
from treeaicoach.config import Config


class FakeEngine:
    def __init__(self) -> None:
        self.cfg = Config(ai_api_key="SECRET-KEY-123")
        self._settings_watcher = None
        self.requests = []
        self.snaps = 0

    def health(self):
        return {"capture_backend": "dxgi", "capture_fps": 6.0, "detect_ms": {"p50": 12.0}}

    def request_diag_snapshot(self, full_screen=False):
        self.requests.append(full_screen)

    def diag_snapshot(self):
        self.snaps += 1
        return {"t": time.monotonic(), "state": "running", "frame": np.full((40, 40, 3), 90, np.uint8),
                "preview": np.full((40, 40, 3), 120, np.uint8), "detections": [{"u": 0.5, "v": 0.5}],
                "tracks": [], "game": {"players": [{"champion": "Ahri", "spells": ["Flash", "Ignite"]}]},
                "health": self.health()}


def test_recorder_writes_zip_and_opens_folder(tmp_path):
    eng = FakeEngine()
    opened = []
    rec = diag.DiagRecorder(eng, duration_s=1.0, interval_s=0.2, out_root=tmp_path, opener=opened.append)
    folder = rec.start()
    assert folder is not None and folder.parent == tmp_path and eng.requests == [True]
    rec.put_screen(np.full((1080, 1920, 3), 50, np.uint8))
    rec.put_screen(np.zeros((10, 10, 3), np.uint8))          # only the first one is kept
    rec.join(10.0)
    st = rec.status()
    assert not st["running"] and st["zip"] and st["samples"] >= 4 and st["error"] is None
    assert opened == [tmp_path]
    assert not folder.exists()                               # zipped then removed
    with zipfile.ZipFile(st["zip"]) as zf:
        names = zf.namelist()
        meta = json.loads(zf.read(f"{folder.name}/meta.json"))
        assert f"{folder.name}/screen.jpg" in names
        assert any(n.endswith("000_minimap.png") for n in names)
        assert any(n.endswith("000_annotated.png") for n in names)
        assert any(n.endswith("000.json") for n in names)
        assert f"{folder.name}/summary.json" in names
        blob = b"".join(zf.read(n) for n in names if n.endswith(".json"))
    assert b"SECRET-KEY-123" not in blob                       # whitelist: no API key
    assert meta["config"]["capture_backend"] == "auto" and "ai_api_key" not in meta["config"]
    assert meta["system"]["cores"] >= 1


def test_mask_paths_and_jsonable():
    txt = diag._mask_paths(r"open C:\Users\Jean Dupont\AppData\Roaming\TreeAICoach\x.log")
    assert "Jean" not in txt and "<user>" in txt
    j = diag._jsonable({"a": np.float32(1.5), "b": np.int64(3), "c": float("nan"), "d": (1, 2)})
    assert j == {"a": 1.5, "b": 3, "c": None, "d": [1, 2]}


def _engine_with_demo():
    from treeaicoach.demo import DemoSource
    from treeaicoach.engine import CoachEngine

    class V:
        backend = "print"

        def say(self, *a, **k):
            pass

    eng = CoachEngine(Config(), V(), frame_source=DemoSource(size=200, seed=2),
                      enable_hotkeys=False, manage_overlay=False)
    for i in range(30):
        eng.step(i * 0.125)
    return eng


def test_engine_snapshot_health_and_start(tmp_path, monkeypatch):
    eng = _engine_with_demo()
    snap = eng.diag_snapshot()
    assert snap["frame"] is not None and snap["frame"].shape[2] == 3
    assert isinstance(snap["detections"], list) and isinstance(snap["tracks"], list)
    players = snap["game"]["players"]
    assert players and all(set(p) >= {"champion", "team", "spells"} for p in players)
    assert not any("riot" in k or "summoner_name" in k for p in players for k in p)    # no personal data
    h = eng.health()
    for k in ("capture_fps", "detect_ms", "tick_ms", "champions_seen", "champions_expected", "minimap_score",
              "detect_rate", "budget", "cpu_percent", "overlay"):
        assert k in h
    assert h["detect_ms"]["n"] > 0 and h["champions_expected"] >= 1
    assert eng.get_status().health is not None
    # start_diagnostic from any thread: recorder in the user data folder (redirected here)
    monkeypatch.setattr(diag.DiagRecorder, "_root", lambda self: tmp_path)
    monkeypatch.setattr(diag, "_default_opener", lambda p: None)
    folder = eng.start_diagnostic(duration_s=0.4, interval_s=0.1)
    assert folder is not None and eng.start_diagnostic() is None        # one at a time
    eng.step(4.0)                                                          # serves the screen request
    eng._diag.join(10.0)
    st = eng.diagnostic_status()
    assert st["zip"] and st["samples"] >= 2
