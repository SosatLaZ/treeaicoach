"""Tests for treeaicoach.spike_planner (the purchase that flips my lane lead -> planner calls)."""
from __future__ import annotations

from treeaicoach import macro
from treeaicoach import spike_planner as SP
from treeaicoach.fight import Seen

from test_macro import DARIUS_LANE, TOP_LANE_ME, ctx_for, game


def _ctx(gold=1000.0, mine=(1055,), his=(1055, 1036, 1036), gt=400.0, hp=1.0, me_uv=TOP_LANE_ME, darius=None,
         **kw):
    g = game(gt, gold=gold, hp=hp)
    g.me.items = list(mine)
    d = next(p for p in g.enemies if p.champion_alias == "Darius")
    d.items = list(his)
    if darius:
        for k, v in darius.items():
            setattr(d, k, v)
    return ctx_for(g, me_uv=me_uv, enemies=[Seen("Darius", DARIUS_LANE, True, 0.0)], **kw)


def test_lead_score_and_need():
    assert SP.lead_score(1500, 1000, 9, 9) == 0.5
    assert abs(SP.lead_score(1000, 1000, 9, 10) + SP.LEVEL_K) < 1e-9
    assert SP.need_gold(SP.FLIP) == 0.0 and abs(SP.need_gold(-0.7) - 1100.0) < 1e-6
    assert SP.lead_score(None, "x", None, 3) == -3 * SP.LEVEL_K       # never raises


def test_flip_purchase_spends_at_least_the_gap():
    c = _ctx()
    fp = SP.flip_purchase(c.game, "TOP", 1100, [1055])
    assert fp is not None and fp[0] >= 1100 and fp[0] % SP.GOLD_STEP == 0
    assert SP.flip_purchase(c.game, "TOP", 1100, [1055]) == fp            # cached


def test_plan_then_recall_calls():
    plan = SP.rule_spike(_ctx(gold=1000.0))
    assert plan is not None and plan.kind == SP.KIND and plan.tier == "basic"
    assert "tu gagnes l'échange" in plan.text and "back à" in plan.text.lower() and len(plan.text) <= 60
    assert "Il te manque" in plan.why and "Darius" in plan.why
    ready = SP.rule_spike(_ctx(gold=1600.0))
    assert ready is not None and ready.kind == "wave_recall" and ready.text.startswith("Rentre")
    assert len(ready.text) <= 60 and ready.target == macro.FOUNTAIN["ORDER"]
    # routed through the planner (no second path)
    assert any(x.kind == "wave_recall" and "échange" in x.text for x in macro.evaluate(_ctx(gold=1600.0)))


def test_silent_when_ahead_far_out_of_lane_or_low():
    assert SP.rule_spike(_ctx(mine=(1055, 3044), his=(1055,))) is None           # already ahead
    assert SP.rule_spike(_ctx(gold=300.0)) is None                               # far from the flip gold
    assert SP.rule_spike(_ctx(his=(1055, 3071))) is None                         # gap > one back
    assert SP.rule_spike(_ctx(gt=900.0)) is None                                 # after laning
    assert SP.rule_spike(_ctx(hp=0.2)) is None                                   # wave_recall's job
    assert SP.rule_spike(_ctx(me_uv=(0.5, 0.5))) is None                         # not in my lane
    assert SP.rule_spike(_ctx(darius={"is_dead": True})) is None                 # plates' job
    assert SP.rule_spike(_ctx(darius={"level": 11})) is None                     # 2 levels down: out of reach
