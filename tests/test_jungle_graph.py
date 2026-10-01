"""Logical paths of the hidden enemy jungler (jungle_graph.py) + fog / overlay wiring."""

import numpy as np

from treeaicoach.fog_tracker import FogEstimate, FogTracker
from treeaicoach.jungle_graph import JunglerFilter, JunglePath, build_nodes


def test_nodes_cover_camps_pits_ganks_fountains():
    kinds = {n.kind for n in build_nodes()}
    assert kinds == {"camp", "scuttle", "pit", "gank", "fountain"}
    assert sum(n.kind == "camp" for n in build_nodes()) == 12


def test_filter_paths_probabilities_and_reach():
    f = JunglerFilter()
    f.reset((0.30, 0.55), 200.0, "ORDER")
    f.step(212.0)
    paths = f.paths(me=(0.16, 0.16))
    assert 1 <= len(paths) <= 2
    assert all(0.0 < p.prob <= 1.0 and len(p.points) >= 2 for p in paths)
    assert abs(float(f.w.sum()) - 1.0) < 1e-6
    p_near = f.p_reach((0.30, 0.55))
    p_far = f.p_reach((0.90, 0.10))
    assert p_near is not None and p_far is not None and p_near > p_far
    h = f.heat()
    assert h is not None and abs(float(h.sum()) - 1.0) < 1e-3


def test_vision_and_farm_evidence_reweight():
    f = JunglerFilter()
    f.reset((0.30, 0.55), 400.0, "ORDER")
    f.step(410.0)
    before = f.p_reach((0.30, 0.55), 2.0)
    f.observe_vision([(0.30, 0.55)], radius=0.08)
    after = f.p_reach((0.30, 0.55), 2.0)
    assert after <= before
    f.observe_farm()
    assert abs(float(f.w.sum()) - 1.0) < 1e-6


def test_fog_tracker_flag_off_by_default_and_paths_when_on():
    fog = FogTracker()
    assert fog.paths_enabled is False

    class Cfg:
        fog_max_s = 60.0
        jungle_paths = True

    fog.apply_config(Cfg())
    assert fog.paths_enabled is True


def test_overlay_draws_paths_without_raising():
    from treeaicoach import overlay_render as orr

    jp = JunglePath(points=((0.3, 0.55), (0.25, 0.4), (0.16, 0.2)), prob=0.6, label="loups → gank top",
                    target="gank_top_ORDER", eta_me=11.0)
    est = FogEstimate(key="k", alias="Kindred", name="Kindred", last_uv=(0.3, 0.55), last_seen=0.0,
                      elapsed=12.0, speed=0.025, radius=0.3, region=np.ones((128, 128), bool),
                      confidence=0.8, is_jungler=True, paths=(jp,), p_reach=0.4)
    st = orr.OverlayState(minimap_rect=None, screen_rect=None, me_uv=(0.16, 0.16), my_team="ORDER",
                          enemies=[], fogs=[est])
    img = orr.render_minimap(st, 256, now=0.0)
    assert img.shape == (256, 256, 4) and int(img[..., 3].max()) > 0
