"""jungle_path.py: early jungle clear model -> heat map of the jungler in his fog region."""

from __future__ import annotations

import math

import numpy as np

from treeaicoach import geometry
from treeaicoach.fog_tracker import FogTracker, shared_reachability
from treeaicoach.jungle_intel import JungleIntelTracker
from treeaicoach.jungle_path import JunglePathModel, _camps_of
from treeaicoach.tracker import Tracker

from test_jungle_intel import RED_BUFF_CHAOS, game, seen


def _mass_near(heat, uv, r=0.05):
    g = heat.shape[0]
    c = (np.arange(g) + 0.5) / g
    return float(heat[np.hypot(c[None, :] - uv[0], c[:, None] - uv[1]) <= r].sum())


def test_heat_is_a_distribution_inside_the_region():
    m = JunglePathModel()
    m.set_team("CHAOS")
    region = shared_reachability().walkable.copy()
    region[:, :40] = False                         # a bound excluding the left part
    h = m.heat(150.0, region)
    assert h is not None and h.dtype == np.float32
    assert abs(float(h.sum()) - 1.0) < 1e-3
    assert float(h[~region].sum()) == 0.0
    assert m.heat(10.0) is None and m.heat(400.0) is None      # outside the early game


def test_sighting_selects_the_start_side_and_follows_the_clear():
    m = JunglePathModel()
    m.set_team("CHAOS")
    camps = _camps_of("CHAOS")
    m.observe_seen(100.0, RED_BUFF_CHAOS)          # red buff start (top side for CHAOS)
    s = m.summary(100.0)
    assert s["p_red_side_start"] > 0.95 and s["broken"] is None
    h = m.heat(140.0)
    near_side = max(_mass_near(h, camps["krugs"]), _mass_near(h, camps["raptors"]))
    far_side = max(_mass_near(h, camps["blue"]), _mass_near(h, camps["gromp"]))
    assert near_side > 0.15 and near_side > 5 * far_side
    # uniform over the walkable map would give a few % around one camp
    uni = shared_reachability().walkable.astype(np.float32)
    uni /= uni.sum()
    assert near_side > 5 * _mass_near(uni, camps["krugs"])


def test_off_route_sighting_and_recall_turn_the_model_off():
    m = JunglePathModel()
    m.set_team("ORDER")
    m.observe_seen(120.0, (0.9, 0.1))              # in the enemy fountain area: invade / odd
    assert m.broken and m.heat(130.0) is None
    m2 = JunglePathModel()
    m2.set_team("ORDER")
    m2.observe_left_route("recall")
    assert m2.heat(130.0) is None


def test_cs_tick_favours_hypotheses_finishing_a_camp():
    m = JunglePathModel()
    m.set_team("CHAOS")
    w0 = m.summary(0)["p_route"]
    m.observe_farm(104.0, 112.0)                   # first camp done ~1:50: normal pace
    assert m.summary(0)["p_route"] >= w0 * 0.9
    assert m.heat(115.0) is not None


def test_fog_estimate_carries_heat_and_is_kept_alive_early():
    """Integration: the Tab watcher anchors the jungler at his fountain at the start and
    plugs the model into the fog tracker; past fog_max_s the estimate stays (low
    confidence) while the model is informative, then goes away."""
    ji, fog, tr = JungleIntelTracker(), FogTracker(), Tracker()
    t, est_late = 0.0, None
    for gt in range(20, 300, 2):
        t = float(gt)
        seen(tr, t)
        g = game(float(gt), t=t)
        ji.update(t, g, tr, fog)
        fog.update(t, tr, g)
        e = fog.estimate_for("LeeSin")
        if gt == 150:
            assert e is not None and e.heat is not None
            assert abs(float(e.heat.sum()) - 1.0) < 1e-3
            assert e.confidence <= 0.2 + 1e-6
            est_late = e
    assert est_late is not None
    assert fog.estimate_for("LeeSin") is None     # 4:58: model off, past max_s: no estimate
    # a sighting resets the region as before (no heat while visible)
    seen(tr, t + 1, RED_BUFF_CHAOS)
    assert fog.update(t + 1, tr, game(300.0, t=t + 1)) == []


def test_heat_beats_uniform_on_a_scripted_clear():
    """Mass near the true position on a scripted red-start full clear, vs uniform."""
    m = JunglePathModel()
    m.set_team("CHAOS")
    route = next(r for r in m._routes["CHAOS"] if r.name == "full_red_start")
    m.observe_seen(100.0, RED_BUFF_CHAOS)
    walk = shared_reachability().walkable
    uni = walk.astype(np.float32) / walk.sum()
    gains = []
    for gt in range(110, 200, 5):
        tu = float(np.interp(gt - 90.0, route.ts, route.us))
        tv = float(np.interp(gt - 90.0, route.ts, route.vs))
        h = m.heat(float(gt))
        gains.append(_mass_near(h, (tu, tv)) / max(_mass_near(uni, (tu, tv)), 1e-9))
    assert float(np.median(gains)) > 5.0, gains
    assert math.isfinite(sum(gains))
    assert geometry.normalize_team("CHAOS") == "CHAOS"
