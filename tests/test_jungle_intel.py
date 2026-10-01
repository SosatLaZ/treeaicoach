"""jungle_intel.py: enemy jungler intel from the public Tab data (purchases, creep score)."""

from __future__ import annotations

import math

import numpy as np

from treeaicoach import geometry
from treeaicoach.detector import Detection
from treeaicoach.fog_tracker import FogTracker
from treeaicoach.identifier import Identified
from treeaicoach.jungle_intel import JungleIntelTracker
from treeaicoach.live_client import GameInfo, PlayerInfo
from treeaicoach.tracker import Tracker

RED_BUFF_CHAOS = (0.4775, 0.2724)


def game(gt: float, items=(1102,), cs=40, dead=False, t=0.0, top_items=(1055,)) -> GameInfo:
    me = PlayerInfo(champion_alias="Garen", champion_name="Garen", team="ORDER", position="TOP")
    jg = PlayerInfo(champion_alias="LeeSin", champion_name="Lee Sin", team="CHAOS",
                    position="JUNGLE", has_smite=True, items=list(items), is_dead=dead,
                    scores={"creepScore": cs}, level=6)
    top = PlayerInfo(champion_alias="Darius", champion_name="Darius", team="CHAOS",
                     position="TOP", items=list(top_items))
    return GameInfo(game_time=gt, game_mode="CLASSIC", map_number=11, me=me, enemies=[jg, top],
                    fetched_at=t)


def seen(tracker: Tracker, t: float, uv=None) -> None:
    items = [Identified(det=Detection(u=0.1, v=0.2, r=0.047, score=0.9, cls="ally",
                                      cls_probs=(0.05, 0.9, 0.05)),
                        alias="Garen", relation="self", team="ORDER", id_score=0.9)]
    if uv is not None:
        items.append(Identified(det=Detection(u=uv[0], v=uv[1], r=0.047, score=0.9, cls="enemy",
                                              cls_probs=(0.9, 0.05, 0.05)),
                                alias="LeeSin", relation="enemy", team="CHAOS", id_score=0.9))
    tracker.update(t, items)


def test_purchase_reanchors_fog_at_fountain_and_reports_recall():
    ji, fog, tr = JungleIntelTracker(), FogTracker(), Tracker()
    for k in range(9):                       # seen at his red buff, then hidden
        seen(tr, k * 0.125, RED_BUFF_CHAOS)
        fog.update(k * 0.125, tr, game(400.0, t=k * 0.125))
    t = 1.0
    for gt, items in ((401.0, (1102,)), (420.0, (1102,)), (421.0, (1102, 1001, 2003))):
        t += 1.0 if gt != 420.0 else 19.0
        seen(tr, t)
        g = game(gt, items=items, t=t)
        st = ji.update(t, g, tr, fog)
        fog.update(t, tr, g)
    assert st.recalled and st.recalled_t == 21.0 and "a rappelé" in st.text
    est = fog.estimate_for("LeeSin")
    assert est is not None and math.dist(est.last_uv, geometry.RED_FOUNTAIN) < 0.02
    eta = st.earliest_eta((0.1, 0.2), now=t)
    assert eta is not None and eta > 15.0     # from his fountain to my top lane
    assert ji.recalled_at("LeeSin") == 21.0 and ji.recalled_at("Darius") is None


def test_ignored_item_changes():
    ji = JungleIntelTracker()
    ji.update(0.0, game(400.0, items=(3003,), t=0.0))
    st = ji.update(1.0, game(401.0, items=(3040,), t=1.0))           # Seraph's: automatic
    assert not st.recalled
    st = ji.update(2.0, game(402.0, items=(3040, 2010), t=2.0))      # biscuit from a rune
    assert not st.recalled
    st = ji.update(3.0, game(403.0, items=(3040, 2010, 1001), dead=True, t=3.0))
    assert not st.recalled and st.dead and st.text is None           # bought while dead
    st = ji.update(4.0, game(404.0, items=(3040,), t=4.0))           # consumed / sold: no
    assert not st.recalled
    ji2 = JungleIntelTracker()                                       # game start: no
    ji2.update(0.0, game(20.0, items=(), t=0.0))
    assert not ji2.update(1.0, game(21.0, items=(1102,), t=1.0)).recalled
    assert ji2.update(1.5, None).alias == "LeeSin"                  # never raises
    assert JungleIntelTracker().update(0.0, object()).alias is None


def test_creep_score_tick_shrinks_the_fog_region_to_reachable_camps():
    ji, fog, tr = JungleIntelTracker(), FogTracker(), Tracker()
    ref = FogTracker()
    for k in range(9):
        t = k * 0.125
        seen(tr, t, RED_BUFF_CHAOS)
        fog.update(t, tr, game(400.0 + t, t=t))
        ref.update(t, tr, game(400.0 + t, t=t))
    t, gt = 1.0, 401.0
    ji.update(t, game(gt, cs=40, t=t), tr, fog)
    for step in range(1, 27):                 # hidden 25 s, then his CS goes up (t = 26)
        t, gt = 1.0 + step, 401.0 + step
        seen(tr, t)
        g = game(gt, cs=40 if step < 25 else 44, t=t)
        st = ji.update(t, g, tr, fog)
        fog.update(t, tr, g)
        ref.update(t, tr, g)
    assert st.farming and st.last_farm_t == 25.0 and "farme" in st.text
    # 3 s later: the region is refined (intersection with the camps / lane points he could
    # reach), never larger than the plain one; the display circle stays the last sighting
    t += 3.0
    seen(tr, t)
    g = game(gt + 3.0, cs=44, t=t)
    est, est0 = fog.update(t, tr, g)[0], ref.update(t, tr, g)[0]
    assert len(est.seeds) > 1 and est.region.sum() <= est0.region.sum()
    assert est.last_uv == est0.last_uv and est.radius == est0.radius
    grid = est.region.shape[0]
    for u, v in st.farm_points:              # a candidate place he could reach stays in
        cell = (min(grid - 1, int(v * grid)), min(grid - 1, int(u * grid)))
        assert est.region[cell] or not est0.region[cell]
    assert np.isfinite(est.radius)
    eta = st.earliest_eta((0.1, 0.2), now=t)
    assert eta is not None and eta >= 0.0


def test_creep_score_tick_side_shortly_after_losing_him():
    ji, fog, tr = JungleIntelTracker(), FogTracker(), Tracker()
    for k in range(9):
        t = k * 0.125
        seen(tr, t, RED_BUFF_CHAOS)
        fog.update(t, tr, game(400.0 + t, t=t))
    t, gt = 1.0, 401.0
    ji.update(t, game(gt, cs=40, t=t), tr, fog)
    for step in range(1, 5):                  # CS goes up at t = 4
        t, gt = 1.0 + step, 401.0 + step
        seen(tr, t)
        g = game(gt, cs=40 if step < 3 else 44, t=t)
        st = ji.update(t, g, tr, fog)
        fog.update(t, tr, g)
    assert st.farming and st.farm_side == "top" and "farme côté haut" in st.text
    assert any(math.dist(RED_BUFF_CHAOS, p) < 0.01 for p in st.farm_points)
    assert not any(p[0] + p[1] > 1.06 for p in st.farm_points)      # nothing on the bot side
