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


def test_fog_tracker_flag_follows_config_default_on():
    from treeaicoach.config import Config

    assert Config().jungle_paths is True
    fog = FogTracker()
    fog.apply_config(Config())
    assert fog.paths_enabled is True

    class Off:
        fog_max_s = 60.0
        jungle_paths = False

    fog.apply_config(Off())
    assert fog.paths_enabled is False


def test_config_migration_turns_the_2_4_trial_on_once(tmp_path):
    import json

    from treeaicoach.config import load_config, save_config

    p = tmp_path / "config.json"
    p.write_text(json.dumps({"config_version": 1, "jungle_paths": False}), encoding="utf-8")
    cfg = load_config(p)
    assert cfg.jungle_paths is True                       # 2.4 default False -> on once
    cfg.jungle_paths = False
    assert save_config(cfg, p)
    assert load_config(p).jungle_paths is False           # an explicit choice after 2.5 sticks


def test_gank_spots_follow_our_laners_and_memory_carries_camps():
    f = JunglerFilter()
    f.reset((0.30, 0.55), 400.0, "ORDER")
    f.set_victims([(0.85, 0.80)])                         # one ally near the bot lane
    g = f.graph
    moved = [j for j, n in enumerate(g.nodes) if n.kind == "gank" and tuple(f.node_uv[j]) == (0.85, 0.80)]
    assert moved and all(g.nodes[j].name.startswith("gank_bot") for j in moved)
    f.step(460.0)
    up, since = f.memory()
    assert up is not None and since is not None and since > 0
    f2 = JunglerFilter()
    f2.reset((0.5, 0.5), 470.0, "ORDER", camps_up=up, since_recall=since)
    assert np.all(f2.up[0] >= up - 1e-6)


def test_compact_minimap_never_draws_the_region_contour():
    from treeaicoach import overlay_render as orr

    region = np.zeros((128, 128), bool)
    region[20:110, 20:110] = True                          # a big quadrant-like region
    est = FogEstimate(key="k", alias="Kindred", name="Kindred", last_uv=(0.5, 0.5), last_seen=0.0,
                      elapsed=20.0, speed=0.025, radius=0.4, region=region, confidence=0.6,
                      is_jungler=True)
    st = orr.OverlayState(minimap_rect=None, screen_rect=None, me_uv=None, my_team="ORDER",
                          enemies=[], fogs=[est])
    img = orr.render_minimap(st, 256, now=0.0)
    edge = img[34:48, 80:180, 3]                           # where the old contour runs (y ~ 40 px)
    assert int(edge.max()) == 0
    assert int(img[128, 128, 3]) > 0                       # the "JGL 20 s" last seen mark stays
    detailed = orr.render_minimap(orr.OverlayState(**{**st.__dict__, "hud_detailed": True}), 256, now=0.0)
    assert int(detailed[34:48, 80:180, 3].max()) > 0       # detailed mode keeps the outline


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
