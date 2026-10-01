"""Play ratings wired in the engine: demo run (gank survived), record summary, never breaks a tick."""
from __future__ import annotations

import json
from types import SimpleNamespace

from test_engine import Clock, FakeVoice

from treeaicoach.config import Config
from treeaicoach.demo import DemoSource
from treeaicoach.detector import ClassicDetector
from treeaicoach.engine import CoachEngine


def test_demo_run_rates_moments_without_errors():
    src = DemoSource(size=240)
    clock = Clock()
    eng = CoachEngine(Config(skill_level="debutant"), FakeVoice(), detector=ClassicDetector(), frame_source=src,
                      clock=clock, enable_hotkeys=False, manage_overlay=False)
    fps = 6.0
    for i in range(int(70 * fps)):
        clock.t = i / fps
        eng.step(clock.t)
    pc = eng._plays
    assert pc is not None and eng._errors == 0
    # the demo's gank (Lee Sin, DANGER) is survived: rated, then shown once the threat is over
    rules = [p.rule for p in pc.history()]
    assert "gank_survived" in rules, rules
    assert any(p.rule == "gank_survived" for p in eng.recent_plays)
    assert any(kind == "play" for _t, kind, _txt in eng.text_messages)
    summ = eng.plays_summary()
    assert summ["total"] >= 1 and 0 <= summ["precision"] <= 100


def test_finish_job_writes_plays_into_record(tmp_path):
    path = tmp_path / "2026-10-01_1200_Garen.json"
    path.write_text(json.dumps({"schema": 3, "meta": {"champion": "Garen"}}), encoding="utf-8")
    rec = SimpleNamespace(finish=lambda: path)
    cfg = Config(post_game_report=False, post_game_summary=False) if hasattr(Config(), "post_game_summary") \
        else Config(post_game_report=False)
    eng = CoachEngine(cfg, FakeVoice(), frame_source=DemoSource(size=120), enable_hotkeys=False,
                      manage_overlay=False, recorder_factory=lambda: None)
    eng._postgame_lcu = lambda: None
    summary = {"schema": 1, "counts": {"great": 2}, "total": 2, "precision": 90, "plays": []}
    eng._finish_job(rec, summary)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["plays"]["precision"] == 90 and data["meta"]["champion"] == "Garen"
