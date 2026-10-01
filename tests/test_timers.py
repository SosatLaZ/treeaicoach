"""Timers column of the minimap layer (overlay_render.timer_rows / _draw_timers) + buff tracking."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from treeaicoach import overlay_render as R
from treeaicoach.config import BOOL_FIELDS, Config
from treeaicoach.objectives import ObjectiveState, ObjectiveTimers


def _state(**kw):
    base = dict(game_time=600.0, my_team="ORDER", my_role="BOTTOM", threat_level=0)
    base.update(kw)
    return R.OverlayState(**base)


def _texts(state):
    return [t for t, _c in R.timer_rows(state)]


def test_nothing_to_show():
    assert R.timer_rows(_state()) == []
    assert R.timer_rows(R.OverlayState()) == []          # no game time


def test_objective_soon_for_my_role():
    drag = ObjectiveState(name="Dragon", next_spawn=645.0, alive=False, source="event", key="dragon")
    far = ObjectiveState(name="Baron", next_spawn=1200.0, alive=False, source="schedule", key="baron")
    assert _texts(_state(objectives=[drag, far])) == ["Dragon 0:45"]
    # a top laner does not play the dragon: hidden in compact mode, shown in detailed mode
    assert _texts(_state(objectives=[drag], my_role="TOP")) == []
    assert "Dragon 0:45" in _texts(_state(objectives=[drag], my_role="TOP", hud_detailed=True))


def test_objective_up_and_role_after_20():
    baron = ObjectiveState(name="Baron", next_spawn=1200.0, alive=True, source="schedule", key="baron")
    assert _texts(_state(objectives=[baron], game_time=1230.0, my_role="TOP")) == ["Baron UP"]
    # up for a long time: compact mode drops it (the game shows the icon), detailed keeps it
    assert _texts(_state(objectives=[baron], game_time=1500.0)) == []
    assert _texts(_state(objectives=[baron], game_time=1500.0, hud_detailed=True)) == ["Baron UP"]


def test_baron_buff_enemy_and_ours():
    st = _state(game_time=1500.0, buffs=[("baron", "CHAOS", 1634.0)])
    rows = R.timer_rows(st)
    assert rows[0][0] == "Baron ennemi 2:14" and rows[0][1] == R.TAI_DANGER
    assert _texts(_state(game_time=1500.0, buffs=[("elder", "ORDER", 1560.0)])) == ["Ancestral allié 1:00"]
    assert _texts(_state(game_time=1700.0, buffs=[("baron", "CHAOS", 1634.0)])) == []   # expired


def test_enemy_death_window():
    st = _state(enemy_respawns=[618.0, 625.0, 640.0])
    assert _texts(st) == ["3 morts · 18 s"]
    assert _texts(_state(enemy_respawns=[618.0])) == []      # a single dead enemy: nothing


def test_priority_cap_and_danger_hides():
    drag = ObjectiveState(name="Dragon", next_spawn=645.0, alive=False, source="event", key="dragon")
    st = _state(objectives=[drag], buffs=[("baron", "CHAOS", 700.0), ("elder", "ORDER", 650.0)],
                enemy_respawns=[618.0, 625.0])
    rows = _texts(st)
    assert len(rows) == R.TIMER_MAX_ROWS
    assert rows[0].startswith("Baron ennemi") and rows[1].startswith("Ancestral allié") and "morts" in rows[2]
    assert R.timer_rows(_state(objectives=[drag], threat_level=2)) == []
    assert R.timer_rows(_state(objectives=[drag], threat_level=1)) == []
    assert _texts(_state(objectives=[drag], threat_level=2, me_dead=True)) == ["Dragon 0:45"]
    assert R.timer_rows(_state(objectives=[drag], show_timers=False)) == []
    assert all("—" not in t for t in rows)


def test_render_smoke_top_right_column():
    st = _state(enemy_respawns=[618.0, 625.0, 640.0], buffs=[("baron", "CHAOS", 700.0)])
    img = R.render_minimap(st, 256, 256, now=0.0)
    assert img.shape == (256, 256, 4) and img.dtype == np.uint8
    assert img[:60, 128:, 3].max() > 0                       # drawn in the top-right corner
    assert img[:60, :100, 3].max() == 0
    empty = R.render_minimap(_state(), 256, 256, now=0.0)
    assert empty[:60, 128:, 3].max() == 0
    framed = R.render_minimap(st, 300, 300, now=0.0, show_frame=True)
    assert framed.shape == (300, 300, 4)


def test_buffs_from_kill_events():
    timers = ObjectiveTimers(SimpleNamespace(objective_timers=True))
    players = [SimpleNamespace(team="ORDER", riot_id="Me#EUW", summoner_name="Me", champion_alias="Ahri"),
               SimpleNamespace(team="CHAOS", riot_id="Foe#EUW", summoner_name="Foe", champion_alias="Zed")]
    game = SimpleNamespace(game_time=1300.0, fetched_at=0.0, is_summoners_rift=True,
                           all_players=lambda: players,
                           events=[{"EventID": 7, "EventName": "BaronKill", "EventTime": 1250.0,
                                    "KillerName": "Foe", "Assisters": []}])
    timers.update(game, 0.0)
    assert timers.buffs() == [("baron", "CHAOS", 1430.0)]


def test_config_toggle():
    assert "overlay_timers" in BOOL_FIELDS
    assert Config().overlay_timers is True
