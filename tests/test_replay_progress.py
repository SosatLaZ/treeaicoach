"""Replay viewer model / rendering (replay.py) and progress trends (progress.py): pure, offline."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from PIL import Image

from treeaicoach import progress, replay

FIX = Path(__file__).resolve().parent / "fixtures" / "game_record_sample.json"


def _record() -> dict:
    return json.loads(FIX.read_text(encoding="utf-8"))


def test_replay_model_and_frames() -> None:
    rec = _record()
    rec["allies"] = {"Vi": [[240.0, 0.2, 0.3], [244.0, 0.21, 0.29]]}
    m = replay.ReplayModel(rec)
    assert m.start <= 15.0 and m.end >= 1690.0 and m.jungler == "LeeSin"
    kinds = {mk.kind for mk in m.markers}
    assert {"death", "gank", "kill", "objective"} <= kinds
    assert m.deaths and abs(m.deaths[0] - 250.0) < 1
    fr = m.frame(248.0)
    assert fr.me is not None and any(e.alias == "LeeSin" for e in fr.enemies)
    assert any(a.alias == "Vi" for a in fr.allies)
    assert fr.alerts and fr.alerts[0][1] >= 1
    # last known positions are forgotten after ENEMY_MEMORY_S
    late = m.frame(m.end)
    assert all(e.age <= replay.ENEMY_MEMORY_S for e in late.enemies)
    nxt = m.next_marker(0.0)
    assert nxt is not None and nxt.kind in ("death", "gank")
    assert m.prev_marker(nxt.t + 30) is not None
    img = replay.render_frame(m, 248.0, 200)
    assert isinstance(img, Image.Image) and img.size == (200, 200)
    tl = replay.render_timeline(m, 400, 30, 248.0)
    assert tl.size == (400, 30)
    t = replay.time_at_x(m, 200, 400)
    assert m.start < t < m.end
    cap = replay.frame_caption(m, 248.0)
    assert "visible" in cap and chr(0x2014) not in cap


def test_replay_degraded_inputs() -> None:
    for rec in (None, {}, {"my_positions": "x", "sightings": [1], "fog": [[1, 2]], "alerts": [["a"]]}):
        m = replay.ReplayModel(rec)
        assert m.end > m.start
        assert replay.render_frame(m, 0.0, 64).size == (64, 64)
        assert replay.render_timeline(m, 100).size[0] == 100
        assert isinstance(replay.frame_caption(m, 0.0), str)


def test_progress_metrics_trends_focus(tmp_path: Path) -> None:
    rec = _record()
    rows = []
    for i in range(6):
        r = json.loads(json.dumps(rec))
        for sn in r["snapshots"]:
            sn["cs"] = int(sn["cs"] * (0.8 + 0.05 * i))
        r["summary"]["start"] = r["meta"]["start"] = f"2026-09-{10 + i:02d}T20:00:00+02:00"
        (tmp_path / f"2026-09-{10 + i:02d}_2000_Garen.json").write_text(json.dumps(r), encoding="utf-8")
        m = progress.game_metrics(r)
        assert m is not None and m["cs_per_min"] > 0 and m["deaths"] == 4
        rows.append(m)
    tr = progress.trends(rows)
    assert tr["cs_per_min"]["direction"] == "up" and tr["cs_per_min"]["better"] is True
    assert tr["gold_diff10"]["avg"] is None                      # no League Client truth here
    pts = progress.focus_points(rows, 3)
    assert 1 <= len(pts) <= 3 and all(t and a for t, a in pts)
    assert all(chr(0x2014) not in a for _t, a in pts)
    got = progress.collect(tmp_path, last=20)
    assert len(got) == 6 and (tmp_path / progress.CACHE_NAME).is_file()
    again = progress.collect(tmp_path, last=4)                    # from the cache
    assert [g["file"] for g in again] == [g["file"] for g in got][-4:]
    sp = progress.sparkline([1, None, 3, 2, 5], 120, 30, baseline=0.0)
    assert sp.size == (120, 30)
    assert progress.sparkline([], 50, 20).size == (50, 20)
    shutil.rmtree(tmp_path, ignore_errors=True)
    assert progress.collect(tmp_path) == []
    assert progress.game_metrics(None) is None and progress.focus_points([]) == []


def _plays_record() -> dict:
    from treeaicoach import plays

    rec = _record()
    rec["plays"] = plays.summarize([
        {"cls": "brilliant", "rule": "x", "reason": "Baron volé", "gt": 1520.0, "title": "COUP DE MAÎTRE"},
        {"cls": "blunder", "rule": "y", "reason": "Mort avec 2 100 PO en poche", "gt": 560.0, "title": "GAFFE"},
        {"cls": "good", "rule": "z", "reason": "Balise posée", "gt": 400.0, "title": "BON COUP"}])
    return rec


def test_rated_plays_in_replay_progress_and_report() -> None:
    from treeaicoach import report
    from treeaicoach.analysis import analyze_game

    rec = _plays_record()
    m = replay.ReplayModel(rec)
    pm = [mk for mk in m.markers if mk.kind == "play"]
    assert len(pm) == 3 and {mk.cls for mk in pm} == {"brilliant", "blunder", "good"}
    assert replay.play_rgb("blunder") != replay.play_rgb("brilliant")
    assert replay.render_timeline(m, 300, 30, 600.0).size == (300, 30)
    met = progress.game_metrics(rec)
    assert met is not None and met["precision"] is not None and 0 <= met["precision"] <= 100
    assert progress.game_metrics(_record())["precision"] is None          # old game: no rating
    page = report.render_report_html(rec, analyze_game(rec))
    assert "Coups notés" in page and "Précision" in page and 'class="badge-img"' in page
    assert "Précision des coups" in page                                   # 3-line summary
    old = report.render_report_html(_record(), analyze_game(_record()))
    assert "Coups notés" not in old
    img = report.play_badge_image("great", "EXCELLENT", "Gank esquivé")
    assert img is None or (img.mode == "RGBA" and img.width > img.height)
