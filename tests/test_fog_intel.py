"""Tests for treeaicoach.fog_intel (Tab facts about unseen enemies -> planner calls)."""
from __future__ import annotations

from types import SimpleNamespace

from treeaicoach import fog_intel as FI
from treeaicoach import game_changers as gcm
from treeaicoach import geometry, macro
from treeaicoach.fight import Seen
from treeaicoach.jungle_intel import JungleIntel, JungleIntelTracker

from test_macro import DARIUS_LANE, TOP_LANE_ME, ctx_for, game, obj


def jint(*recalls, alias="LeeSin"):
    return JungleIntel(alias=alias, name="Lee Sin", recalls=tuple((a.lower(), t) for a, t in recalls))


def _based_ctx(t=100.0, bought=98.0, hidden=20.0, gt=400.0, **kw):
    enemies = [Seen("LeeSin", (0.6, 0.4), False, hidden), Seen("Darius", DARIUS_LANE, True, 0.0)]
    return ctx_for(game(gt), me_uv=TOP_LANE_ME, enemies=enemies, t=t, jint=jint(("LeeSin", bought)), **kw)


def test_fountain_eta_is_a_floor_of_the_walk():
    top = FI.fountain_eta("CHAOS", TOP_LANE_ME)
    straight = geometry.dist(geometry.RED_FOUNTAIN, TOP_LANE_ME) / FI.BASE_SPEED
    assert top is not None and top >= straight - 1e-6 and 15.0 < top < 40.0
    assert FI.fountain_eta("CHAOS", geometry.RED_FOUNTAIN) < 2.0
    assert FI.fountain_eta(None, TOP_LANE_ME) is None and FI.fountain_eta("ORDER", None) is None


def test_jungler_based_call_once_fresh_and_never_when_seen_since():
    c = FI.rule_jungler_based(_based_ctx())
    assert c is not None and c.kind == FI.KIND_BASED and c.color == "safe"
    assert c.text.startswith("Joue fort : Lee Sin est à la base") and " s sans gank" in c.text
    assert c.voice == "Jungler à la base : joue fort !" and len(c.voice) <= gcm.VOICE_MAX_CHARS
    assert FI.rule_jungler_based(_based_ctx(t=120.0, bought=98.0, hidden=40.0)) is None      # old news
    assert FI.rule_jungler_based(_based_ctx(hidden=1.0)) is None                              # seen after it
    vis = _based_ctx()
    vis.enemies[0] = Seen("LeeSin", (0.6, 0.4), True, 0.0)
    assert FI.rule_jungler_based(vis) is None                                                 # on the minimap
    assert FI.rule_jungler_based(_based_ctx(gt=900.0)) is None                                # after laning
    soon = _based_ctx(objectives=[obj("grubs", False, 40.0)])
    assert FI.rule_jungler_based(soon) is None                    # the grubs coming are my topic, not "joue fort"


def test_base_fact_feeds_jungler_location_and_silences_jungler_unseen():
    c = _based_ctx()
    loc = macro.jungler_location(c)
    assert loc.source == "base" and loc.side == "base" and loc.conf >= 0.85
    assert loc.uv == macro.FOUNTAIN["CHAOS"]
    wave = {"top": {"meet": 0.8, "state": "pushing"}}
    unseen = ctx_for(game(400.0), me_uv=TOP_LANE_ME, waves=wave, t=100.0,
                     enemies=[Seen("LeeSin", (0.6, 0.4), False, 40.0), Seen("Darius", DARIUS_LANE, True, 0.0)])
    assert gcm.rule_jungler_unseen(unseen) is not None                       # no fact: "recule"
    unseen.jint = jint(("LeeSin", 95.0))
    assert gcm.rule_jungler_unseen(unseen) is None                           # he just bought: at his base
    late = ctx_for(game(400.0), me_uv=TOP_LANE_ME, waves=wave, t=150.0, jint=jint(("LeeSin", 95.0)),
                   enemies=[Seen("LeeSin", (0.6, 0.4), False, 90.0), Seen("Darius", DARIUS_LANE, True, 0.0)])
    assert macro.jungler_location(late).source != "base"                     # 55 s later: he can be anywhere


def test_lane_opponent_purchase_means_plates_or_sync_recall():
    enemies = [Seen("Darius", DARIUS_LANE, False, 12.0)]
    c = ctx_for(game(500.0), me_uv=TOP_LANE_ME, enemies=enemies, t=100.0, jint=jint(("Darius", 97.0)))
    calls = macro.evaluate(c)
    plates = next(x for x in calls if x.kind == "plates")
    assert "Darius est en base" in plates.text and "PO" in plates.why
    v = gcm.voice_for(plates, c, "debutant")
    assert v is not None and v[1] == "Il est rentré : prends la plaque !"
    assert FI.rule_sync_recall(c) is None
    low = ctx_for(game(500.0, hp=0.4), me_uv=TOP_LANE_ME, enemies=enemies, t=100.0, jint=jint(("Darius", 97.0)))
    assert not any(x.kind == "plates" for x in macro.evaluate(low))
    s = FI.rule_sync_recall(low)
    assert s is not None and s.kind == "wave_recall" and s.text == "Rentre aussi : Darius est en base"
    # no purchase / seen since -> nothing
    assert FI.rule_sync_recall(ctx_for(game(500.0, hp=0.4), me_uv=TOP_LANE_ME, enemies=enemies, t=100.0)) is None
    seen = ctx_for(game(500.0, hp=0.4), me_uv=TOP_LANE_ME, enemies=[Seen("Darius", DARIUS_LANE, False, 1.0)],
                   t=100.0, jint=jint(("Darius", 97.0)))
    assert FI.rule_sync_recall(seen) is None


def _items(g, team_gold):
    for p in [g.me] + list(g.allies) + list(g.enemies):
        p.items = [3071] if p.team == team_gold else [1001]
    return g


def test_fight_calculator_force_and_leave():
    pit = macro.DRAGON_UV
    near = (pit[0] - 0.05, pit[1] + 0.05)
    g = _items(game(1000.0, me="Jinx", dead={"Ahri": 30.0}), "ORDER")
    c = ctx_for(g, me_uv=near, objectives=[obj("dragon", True)], t=100.0)
    odds = FI.fight_odds(c)
    assert odds["us"] == 5 and odds["them"] == 4 and odds["gold"] > 0 and odds["verdict"] == "win"
    call = FI.rule_objective_fight(c)
    assert call is not None and call.text.startswith("Force le dragon : combat 5v4") and call.color == "safe"
    assert call.voice == "Combat gagné : force le dragon !"
    g2 = _items(game(1000.0, me="Jinx", dead={"Lux": 30.0}), "CHAOS")
    c2 = ctx_for(g2, me_uv=near, objectives=[obj("dragon", True), obj("grubs", True)], t=100.0)
    lose = FI.rule_objective_fight(c2)
    assert lose is not None and lose.color == "danger"
    assert lose.text == "Laisse le dragon, prends les larves : combat perdu (4v5)"
    # 2+ dead: fight_won / fight_lost say it; far from the pit: nothing; even and close gold: nothing
    g3 = _items(game(1000.0, me="Jinx", dead={"Ahri": 30.0, "Caitlyn": 30.0}), "ORDER")
    assert FI.rule_objective_fight(ctx_for(g3, me_uv=near, objectives=[obj("dragon", True)])) is None
    assert FI.rule_objective_fight(ctx_for(g, me_uv=TOP_LANE_ME, objectives=[obj("dragon", True)])) is None
    g4 = game(1000.0, me="Jinx")
    assert FI.rule_objective_fight(ctx_for(g4, me_uv=near, objectives=[obj("dragon", True)])) is None
    # a respawn in 4 s is no 5v4
    g5 = _items(game(1000.0, me="Jinx", dead={"Ahri": 4.0}), "ORDER")
    assert FI.fight_odds(ctx_for(g5, me_uv=near))["them"] == 5


def test_fog_intel_voice_lines_are_static_and_short():
    phrases = gcm.voice_phrases()
    for p in FI.VOICE_PHRASES:
        assert p in phrases and len(p) <= gcm.VOICE_MAX_CHARS
    assert gcm.voice_class(FI.KIND_BASED) == "lane" and gcm.voice_class(FI.KIND_FIGHT) == "big"


def test_tracker_exposes_every_enemy_purchase():
    tr = JungleIntelTracker()

    def snap(gt, items):
        p = SimpleNamespace(champion_alias="Darius", champion_name="Darius", team="CHAOS", items=items,
                            scores={"creepScore": 10}, is_dead=False, level=5)
        return SimpleNamespace(fetched_at=gt, game_time=gt, enemies=[p], enemy_jungler=lambda: None)

    tr.update(10.0, snap(300.0, [1055]))
    st = tr.update(11.0, snap(301.0, [1055, 1001]))
    assert st.recalls and st.recalls[0][0] == "darius" and 9.0 <= st.recalls[0][1] <= 11.0
    assert tr.update(80.0, snap(370.0, [1055, 1001])).recalls == ()          # 60 s later: no news


def test_rules_never_raise_on_empty_ctx():
    ctx = macro.MacroCtx()
    for rule in FI.RULES:
        assert rule(ctx) is None
    assert FI.jungler_base(ctx) is None and FI.fight_odds(ctx) is None
