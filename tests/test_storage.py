"""storage.py (Réglages > Stockage) and collect_policy.py (fewer, useful minimap captures)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from treeaicoach import collect_policy as cp
from treeaicoach import storage

DAY = 86400


def _touch(p: Path, n: int = 100, age_days: float = 0.0) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * n)
    t = time.time() - age_days * DAY
    os.utime(p, (t, t))
    return p


def _folder(base: Path) -> None:
    for i in range(12):
        stem = f"2026-10-{i + 1:02d}_2000_Garen"
        _touch(base / "games" / f"{stem}.json", 1000)
        _touch(base / "games" / f"{stem}.html", 50_000)
        _touch(base / "games" / "truth" / f"abc-{stem}.truth.json", 3000)
    _touch(base / "games" / "progress_cache.json", 10)
    _touch(base / "collect" / "20261001_200000_0000.png", 30_000, age_days=20)
    _touch(base / "collect" / "20261027_200000_0001_fight.jpg", 30_000, age_days=1)
    _touch(base / "reports" / "rapport_old.zip", 9000, age_days=8)
    _touch(base / "exports" / "TreeAI_envoi_new.zip", 9000, age_days=0)
    (base / "diagnostics" / "diag_old").mkdir(parents=True)
    _touch(base / "diagnostics" / "diag_old" / "meta.json", 500, age_days=9)
    os.utime(base / "diagnostics" / "diag_old", (time.time() - 9 * DAY,) * 2)
    _touch(base / "logs" / "treeaicoach.log", 700, age_days=30)          # active log: never
    _touch(base / "logs" / "treeaicoach.log.2", 700, age_days=10)
    _touch(base / "cache" / "voice" / "model.bin", 5000, age_days=60)     # model: never by default
    _touch(base / "cache" / "skins" / "a.png", 400, age_days=60)


def test_usage_and_cleanup_rules(tmp_path: Path) -> None:
    base = tmp_path / "TreeAICoach"
    _folder(base)
    u = storage.usage(base)
    assert u["captures"]["files"] == 2 and u["models"]["bytes"] == 5000 and u["caches"]["bytes"] == 400
    assert u["games"]["files"] == 37 and "Total" in storage.usage_text(u)
    plan = storage.plan_cleanup(base)
    gone = {p.relative_to(base).as_posix() for p, _b, _k in plan.items}
    assert gone == {"games/2026-10-01_2000_Garen.html", "games/2026-10-02_2000_Garen.html",
                    "games/truth/abc-2026-10-01_2000_Garen.truth.json",
                    "games/truth/abc-2026-10-02_2000_Garen.truth.json",
                    "collect/20261001_200000_0000.png", "reports/rapport_old.zip", "diagnostics/diag_old",
                    "logs/treeaicoach.log.2"}
    assert plan.bytes == 2 * 53_000 + 30_000 + 9000 + 500 + 700
    text = storage.describe_plan(plan)
    assert "Espace libéré" in text and "Captures de minimap : 1 fichier" in text
    n, freed = storage.run_cleanup(plan)
    assert n == 8 and freed == plan.bytes
    assert (base / "games" / "2026-10-01_2000_Garen.json").exists()           # records kept (stats)
    assert (base / "cache" / "voice" / "model.bin").exists() and (base / "logs" / "treeaicoach.log").exists()
    assert not storage.plan_cleanup(base).items
    assert storage.describe_plan(storage.plan_cleanup(base)).startswith("Rien")
    models = storage.plan_cleanup(base, include_models=True)
    assert [k for _p, _b, k in models.items] == ["models"]


def test_auto_cleanup_setting(tmp_path: Path) -> None:
    base = tmp_path / "d"
    _folder(base)
    assert storage.auto_cleanup(SimpleNamespace(storage_auto_clean=False), base) == (0, 0)
    n, _ = storage.auto_cleanup(SimpleNamespace(storage_auto_clean=True, storage_keep_games=11,
                                                storage_keep_days=7), base)
    assert n == 6                                         # 1 old game (html + truth) + 4 old files


def _ident(alias, rel, u, v, ids=0.9):
    return SimpleNamespace(det=SimpleNamespace(u=u, v=v), alias=alias, relation=rel, id_score=ids)


def test_frame_reason() -> None:
    calm = [_ident("Garen", "self", 0.1, 0.9), _ident("Darius", "enemy", 0.5, 0.5)]
    roster = ["Garen", "Darius", "Ahri", "Zed", "Lux", "Jinx"]
    assert cp.frame_reason(calm, (), roster) is None
    assert cp.frame_reason(calm + [_ident("Annie", "enemy", 0.3, 0.3)], (), roster) == "roster"
    assert cp.frame_reason(calm, (), roster, dead=["Darius"]) == "roster"
    assert cp.frame_reason(calm + [_ident(None, "enemy", 0.3, 0.3, None)], (), roster) == "lowconf"
    assert cp.frame_reason(calm + [_ident("Zed", "enemy", 0.3, 0.3, 0.3)], (), roster) == "lowconf"
    stack = [SimpleNamespace(visible=True, stacked_with="Jinx")]
    assert cp.frame_reason(calm, stack, roster) == "stack"
    fight = [_ident("Garen", "self", 0.5, 0.5), _ident("Ahri", "ally", 0.52, 0.5),
             _ident("Darius", "enemy", 0.55, 0.55), _ident("Zed", "enemy", 0.5, 0.58)]
    assert cp.frame_reason(fight, (), roster) == "fight"


def test_budget_caps_and_new_game() -> None:
    b = cp.GameBudget()
    b.new_game(1, 100.0)
    assert b.crop_due(0.0)
    b.add(30_000, 0.0)
    assert not b.crop_due(100.0) and b.crop_due(cp.MIN_GAP_S)
    for _ in range(cp.MAX_FILES):
        b.add(10)
    assert b.full() and not b.crop_due(1e9)
    b.new_game(1, 20.0)                                   # game time went back: a new game
    assert not b.full() and b.files == 0
    b.add(cp.MAX_BYTES)
    assert b.full()
    b.new_game(2, None)
    assert not b.full()


def test_engine_collect_writes_few_useful_jpegs(tmp_path: Path, monkeypatch) -> None:
    from treeaicoach import paths
    from treeaicoach.engine_vision import VisionMixin

    monkeypatch.setattr(paths, "collect_dir", lambda: tmp_path)
    players = [SimpleNamespace(champion_alias=a, is_dead=False) for a in ("Garen", "Darius")]
    eng = SimpleNamespace(_cfg=SimpleNamespace(collect_interval_s=30.0), _collect_count=0, _last_collect=-1e9,
                          _game=SimpleNamespace(game_time=0.0, all_players=lambda: players), _recorder=object(),
                          _tracker=None, _identified=[_ident(None, "enemy", 0.3, 0.3, None)],
                          _err=SimpleNamespace(exception=lambda *a, **k: None))
    frame = np.random.default_rng(0).integers(0, 255, (300, 300, 3), dtype=np.uint8)
    for t in range(0, 3600, 2):                           # one hour of ticks every 2 s
        eng._game.game_time = float(t)
        VisionMixin._collect(eng, frame, float(t))
    files = sorted(tmp_path.iterdir())
    assert len(files) == 3600 // int(cp.MIN_GAP_S) == 20
    assert all(f.name.endswith("_lowconf.jpg") for f in files)
    eng._identified = [_ident("Garen", "self", 0.2, 0.8)]          # calm frame: nothing more
    eng._game.game_time, eng._recorder = 0.0, object()             # new game
    for t in range(4000, 5000, 2):
        VisionMixin._collect(eng, frame, float(t))
    assert len(list(tmp_path.iterdir())) == 20
