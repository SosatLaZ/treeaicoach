"""Tests for treeaicoach.objective_planner (back timing / stay / don't-die before an epic objective)."""
from __future__ import annotations

from treeaicoach import macro
from treeaicoach import objective_planner as OP

from test_macro import ctx_for, game, obj

BOT_ME = (0.70, 0.90)


def _ctx(rem=90.0, key="dragon", me="Jinx", gold=1400.0, hp=1.0, me_uv=BOT_ME, gt=600.0, dead=None, **kw):
    g = game(gt, me=me, gold=gold, hp=hp, dead=dead)
    return ctx_for(g, me_uv=me_uv, objectives=[obj(key, remaining=rem)], **kw)


def test_trip_and_next_objective():
    c = _ctx()
    trip = OP.back_trip_s(c, "dragon")
    assert trip is not None and 25.0 < trip < 60.0                 # recall + shop + walk base -> dragon
    assert OP.next_objective(c, 0, 120) == ("dragon", 90.0)
    assert OP.next_objective(_ctx(me="Garen"), 0, 120) is None       # a top laner is not sent to dragon
    assert OP.next_objective(_ctx(me="Garen", key="grubs"), 0, 120) == ("grubs", 90.0)


def test_back_now_lands_before_the_spawn():
    c = OP.rule_back_now(_ctx(rem=90.0))
    assert c is not None and c.kind == "wave_recall" and c.text.startswith("Rentre")
    assert "pour le Dragon (1:30)" in c.text and len(c.text) <= 60 and c.target == macro.FOUNTAIN["ORDER"]
    assert OP.rule_back_now(_ctx(rem=90.0, gold=300.0)) is None          # nothing worth buying
    assert OP.rule_back_now(_ctx(rem=200.0)) is None                     # too early
    assert OP.rule_back_now(_ctx(rem=40.0)) is None                      # would not land
    # routed through the planner (no second path)
    assert any(x.kind == "wave_recall" and "Dragon" in x.text for x in macro.evaluate(_ctx(rem=90.0)))


def test_stay_blocks_the_other_recall_calls():
    c = _ctx(rem=35.0)
    s = OP.rule_stay(c)
    assert s is not None and s.text == "Ne rentre pas : Dragon dans 0:35" and s.life_s < 15.0
    assert OP.recall_blocked(c)
    assert not any(x.kind == "wave_recall" for x in macro.evaluate(c))
    assert OP.rule_stay(_ctx(rem=35.0, hp=0.3)) is None                  # low: the back is right
    assert OP.rule_stay(_ctx(rem=35.0, gold=400.0)) is None              # nothing to buy
    assert OP.rule_stay(_ctx(rem=90.0)) is None                          # a back still lands


def test_dont_die_unless_the_fight_is_lost():
    c = OP.rule_dont_die(_ctx(rem=30.0))
    assert c is not None and c.text == "Ne prends aucun risque : mourir = Dragon perdu" and c.kind in macro.SOFT_KINDS
    assert OP.rule_dont_die(_ctx(rem=80.0)) is None
    lost = {"Garen": 50.0, "Vi": 50.0}                                   # 3v5: fog_intel says "laisse"
    assert OP.rule_dont_die(_ctx(rem=30.0, dead=lost)) is None


def test_never_raises_on_empty_context():
    c = macro.MacroCtx()
    for rule in OP.RULES:
        assert rule(c) is None
    assert OP.recall_blocked(c) is False


def test_why_fits_one_line():
    for c in (OP.rule_back_now(_ctx(rem=90.0)), OP.rule_stay(_ctx(rem=35.0)), OP.rule_dont_die(_ctx(rem=30.0))):
        assert c is not None and len(c.why) <= 140 and "—" not in c.text + c.why, c.why


def test_recall_blocked_down_to_the_spawn():
    c = _ctx(rem=12.0)
    assert OP.rule_stay(c) is None and OP.recall_blocked(c)              # no line, but no "Rentre" either
