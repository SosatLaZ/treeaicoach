"""Tests for treeaicoach.demo (simulated game source)."""

from __future__ import annotations

import math
import threading

import numpy as np
import pytest

from treeaicoach.demo import CHAMPIONS, DemoSource, jungler_alias
from treeaicoach.live_client import GameInfo


@pytest.fixture(scope="module")
def src() -> DemoSource:
    return DemoSource(size=240, seed=3)


def test_next_returns_frame_and_game(src: DemoSource):
    src.reset()
    frame, game = src.next(100.0)
    assert isinstance(frame, np.ndarray) and frame.shape == (240, 240, 3) and frame.dtype == np.uint8
    assert frame.std() > 10                       # a real rendered minimap, not a blank image
    assert isinstance(game, GameInfo)
    assert game.is_summoners_rift and game.game_mode == "CLASSIC"
    assert game.me is not None and game.me.champion_alias == "Garen" and game.me.position == "TOP"
    assert len(game.allies) == 4 and len(game.enemies) == 5
    jg = game.enemy_jungler()
    assert jg is not None and jg.champion_alias == "LeeSin" and jg.champion_name == "Lee Sin" and jg.has_smite
    assert game.player_by_alias("Darius").position == "TOP"
    assert game.fetched_at == 100.0


def test_scenario_timeline(src: DemoSource):
    lee = jungler_alias()
    assert lee == "LeeSin"
    assert src.positions(2.0)[lee][2] is True
    for s in (8.0, 15.0, 25.0, 32.0):
        assert src.positions(s)[lee][2] is False, s
    assert src.positions(36.0)[lee][2] is True
    # the gank: Lee Sin comes close to me inside the window
    w0, w1 = src.GANK_WINDOW
    assert 0 < w0 < w1 <= src.SCENARIO_LENGTH
    assert 60.0 <= src.SCENARIO_LENGTH <= 90.0
    dmin = min(math.dist(src.positions(s)[lee][:2], src.positions(s)["Garen"][:2])
               for s in np.arange(w0, w1, 0.5))
    assert dmin < 0.12
    # before the window he is never near me
    dpre = min(math.dist(src.positions(s)[lee][:2], src.positions(s)["Garen"][:2])
               for s in np.arange(0.0, w0 - 3.0, 0.5))
    assert dpre > 0.25
    # Darius stays in the top lane
    for s in np.arange(0, 30, 2.0):
        u, v, vis = src.positions(s)["Darius"]
        assert vis and u < 0.15 and v < 0.3


def test_events_gold_and_loop(src: DemoSource):
    early = src.game_info(5.0, 0.0)
    assert [e["EventName"] for e in early.events] == ["GameStart"]
    late = src.game_info(20.0, 0.0)
    assert any(e["EventName"] == "DragonKill" for e in late.events)
    assert src.my_gold(0.0) < 1300 <= src.my_gold(55.0)
    assert late.game_time - early.game_time == pytest.approx(15.0)
    src.reset()
    assert src.scenario_time(10.0) == 0.0
    assert src.scenario_time(10.0 + src.SCENARIO_LENGTH + 4.0) == pytest.approx(4.0)
    assert src.scenario_time(5.0) == 0.0          # clock going back restarts the scenario


def test_bad_times_never_raise():
    s = DemoSource(size=128)
    for t in (float("nan"), float("inf"), "abc", None, -5.0, 1e12):
        frame, game = s.next(t)  # type: ignore[arg-type]
        assert frame is None or frame.shape == (128, 128, 3)
        assert game is None or isinstance(game, GameInfo)


def test_render_is_deterministic_and_icons_present():
    a = DemoSource(size=160, seed=1).render_at(12.0)
    b = DemoSource(size=160, seed=1).render_at(12.0)
    assert np.array_equal(a, b)
    s = DemoSource(size=160)
    scene = s.scene(36.0)
    aliases_visible = {c.alias for c in CHAMPIONS if s.positions(36.0)[c.alias][2]}
    assert len(scene.champions) == len(aliases_visible)
    assert all(sp.icon is not None for sp in scene.champions)
    assert scene.camera is not None and scene.my_team == "ORDER"


def test_thread_safe_next():
    s = DemoSource(size=96)
    errors: list[BaseException] = []

    def run(k: int) -> None:
        try:
            for i in range(5):
                f, g = s.next(k + i * 0.1)
                assert f is not None and g is not None
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    ths = [threading.Thread(target=run, args=(k,)) for k in range(4)]
    for th in ths:
        th.start()
    for th in ths:
        th.join(30)
    assert not errors
