"""jungler_map.py: per-game probability map of the enemy jungler (+ essentials / overlay use)."""

from __future__ import annotations

import time
from types import SimpleNamespace

import numpy as np

from treeaicoach import essentials, geometry
from treeaicoach import jungler_map as JM
from treeaicoach import overlay_render as orr


def _map(team: str = "ORDER") -> JM.JunglerMap:
    m = JM.JunglerMap()
    m.reset(team)
    return m


def test_zones_and_canonical_frame() -> None:
    assert JM.zone_of((0.1, 0.1), "ORDER") == "top"
    assert JM.zone_of((0.5, 0.5), "ORDER") == "mid"
    assert JM.zone_of((0.9, 0.9), "ORDER") == "bot"
    assert JM.zone_of(geometry.BLUE_FOUNTAIN, "ORDER") == "base"
    # the red buff (CHAOS jungle) is MY jungle when I am CHAOS
    assert JM.zone_of((0.48, 0.27), "CHAOS") == "my_jungle"
    assert JM.zone_of((0.48, 0.27), "ORDER") == "his_jungle"
    assert JM.canonical("top", "ORDER") == "bot" and JM.canonical("top", "CHAOS") == "top"
    assert JM.canonical("his_jungle_top", "ORDER") == "his_jungle_bot"
    masks = JM.zone_masks("ORDER")
    assert set(masks) == set(JM.ZONE_KEYS)
    total = sum(m.astype(int) for m in masks.values())
    assert total.max() == 1                          # a partition of the grid


def test_prior_table_shape() -> None:
    assert len(JM.ZONE_PRIOR) >= 20
    for row in JM.ZONE_PRIOR:
        assert len(row) == len(JM.FINE_KEYS)
        assert abs(sum(row) - 1.0) < 0.02
    p = JM.prior_zones(150.0, "CHAOS")
    assert abs(sum(p.values()) - 1.0) < 1e-6
    # early game: in his own jungle, not mine
    assert p["his_jungle_top"] + p["his_jungle_bot"] > p["my_jungle_top"] + p["my_jungle_bot"]


def test_seen_then_hidden_then_prior() -> None:
    m = _map()
    m.seen(300.0, (0.62, 0.70))                      # bot river, near the dragon
    st = m.step(300.5, (0.85, 0.9))
    assert st.visible and st.best()[0] == "bot"
    assert st.side_text() is None                    # visible: the strip says "visible"
    st = m.step(310.0, (0.85, 0.9))
    assert not st.visible and st.top_half < 0.3
    assert st.side_text() is not None and st.side_text().endswith("bas")
    assert abs(sum(st.zones.values()) - 1.0) < 1e-6
    assert st.grid is not None and abs(float(st.grid.sum()) - 1.0) < 1e-4
    far = m.step(900.0, (0.85, 0.9))                 # 10 min later: the minute prior
    assert far.since_fact > 500
    assert max(far.zones.values()) < 0.6


def test_near_me_follows_the_last_sighting() -> None:
    near, far = _map(), _map()
    for m, uv in ((near, (0.80, 0.86)), (far, (0.12, 0.12))):
        m.seen(400.0, uv)
        m.step(400.2, (0.85, 0.9))
    a = near.step(405.0, (0.85, 0.9)).p_near
    b = far.step(405.0, (0.85, 0.9)).p_near
    assert a is not None and b is not None and a > 0.7 > 0.2 > b


def test_dead_respawn_and_anchor() -> None:
    m = _map("ORDER")                                 # he is CHAOS
    m.dead(500.0, 530.0)
    st = m.step(510.0, (0.2, 0.8))
    assert st.dead and st.p_near is None and st.side_text() is None
    st = m.step(535.0, (0.2, 0.8))
    assert not st.dead and st.zones["base"] > 0.4    # just respawned in his fountain
    m.anchor(600.0, (geometry.DRAGON_PIT[0], geometry.DRAGON_PIT[1]), "DragonKill")
    st = m.step(603.0, (0.2, 0.8))
    assert st.top_half < 0.3


def test_game_start_fact_and_never_raises() -> None:
    m = _map("CHAOS")
    st = m.step(70.0, (0.9, 0.1))
    assert st.since_fact == 70.0                     # his fountain at 0:00
    assert m.step(float("nan")) is st
    m.seen(80.0, ("x", None))                        # garbage: ignored
    assert m.step(81.0) is not None


def test_step_cost() -> None:
    m = _map()
    m.seen(100.0, (0.5, 0.5))
    m.step(100.0, (0.2, 0.8))
    t0 = time.perf_counter()
    n = 12 * 60
    for i in range(n):
        m.step(101.0 + i / 12.0, (0.2, 0.8), [(0.2, 0.8)])
    assert (time.perf_counter() - t0) * 1000.0 / n < 1.0


def test_live_adapter_with_stand_ins() -> None:
    jg = SimpleNamespace(champion_alias="Kindred", is_dead=False, respawn_timer=0.0, team="CHAOS")
    game = SimpleNamespace(me=SimpleNamespace(team="ORDER"), enemy_jungler=lambda: jg)
    tr = SimpleNamespace(alias="Kindred", visible=True, stacked_with=None, position=lambda: (0.5, 0.3))
    me = SimpleNamespace(position=lambda: (0.2, 0.8))
    tracker = SimpleNamespace(enemies=lambda visible_only=False: [tr], me=lambda: me,
                              allies=lambda visible_only=True: [])
    fog = SimpleNamespace(anchors=lambda: {})
    live = JM.LiveJunglerMap()
    st = live.update(10.0, 200.0, tracker, fog, game, None)
    assert st is not None and st.visible
    tr.visible = False
    fog.anchors = lambda: {"kindred": (19.0, (0.66, 0.70), "DragonKill")}
    st = live.update(20.0, 210.0, tracker, fog, game, SimpleNamespace(last_farm_t=19.5))
    assert st is not None and not st.visible and st.top_half < 0.5
    assert live.update(21.0, None, tracker, fog, game) is None
    assert live.update(22.0, 212.0, None, None, None) is None


def test_essentials_cell_uses_the_map() -> None:
    m = _map()
    m.seen(300.0, (0.62, 0.70))
    st = m.step(330.0, (0.85, 0.9))
    hidden = SimpleNamespace(is_jungler=True, dead=False, uv=(0.62, 0.70), visible=False, last_seen_ago=30.0)
    c = essentials.jungler_cell([hidden], st)
    assert c is not None and c.value.endswith("bas") and "%" in c.value
    fresh = SimpleNamespace(is_jungler=True, dead=False, uv=(0.62, 0.70), visible=False, last_seen_ago=5.0)
    assert "%" not in essentials.jungler_cell([fresh], st).value     # fresh sighting: "bas 5 s"
    seen = SimpleNamespace(is_jungler=True, dead=False, uv=(0.62, 0.70), visible=True, last_seen_ago=0.0)
    assert "visible" in essentials.jungler_cell([seen], st).value
    assert "%" not in essentials.jungler_cell([hidden], None).value


def test_overlay_shading_layer() -> None:
    m = _map()
    m.seen(300.0, (0.62, 0.70))
    st = m.step(330.0, (0.85, 0.9))
    cov = orr._jungler_map_layer(st, 200, 200, "ORDER")
    assert isinstance(cov, np.ndarray) and cov.shape == (200, 200)
    assert 0.0 < float(cov.max()) <= 1.0
    assert orr._jungler_map_layer(SimpleNamespace(visible=True, zones={"bot": 1.0}), 200, 200, "ORDER") is None
    view = orr.EnemyView(key="jg", alias="Kindred", visible=False, uv=(0.62, 0.70), last_seen_ago=30.0,
                         is_jungler=True)
    state = orr.OverlayState(me_uv=(0.85, 0.9), my_team="ORDER", enemies=[view], jungler_map=st, game_time=330.0)
    img = orr.render_minimap(state, 200)
    plain = orr.render_minimap(orr.OverlayState(me_uv=(0.85, 0.9), my_team="ORDER", enemies=[view],
                                                game_time=330.0), 200)
    assert img.shape == plain.shape and int(np.abs(img.astype(int) - plain.astype(int)).sum()) > 0
