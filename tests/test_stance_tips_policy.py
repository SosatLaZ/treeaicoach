"""Tests for coach.StanceAdvisor, tips.TipRotator, voice_policy and the coach 'enemy dead' rules."""

from __future__ import annotations

from types import SimpleNamespace as NS

from treeaicoach import tips, voice_policy as vp
from treeaicoach.alerts import Alert, AlertKind, Level
from treeaicoach.coach import StanceAdvisor, stance_factors, stance_from_factors
from treeaicoach.analysis import analyze_game
from treeaicoach.report import render_report_html


def _game(hp=1.0, gt=600.0, level=9, events=()):
    me = NS(is_dead=False, level=level, riot_id="Me#EUW", summoner_name="Me", position="TOP",
            scores={"creepScore": 50, "wardScore": 2.0}, items=[1055])
    return NS(me=me, game_time=gt, champion_stats={"currentHealth": 1000 * hp, "maxHealth": 1000.0},
              events=list(events), current_gold=1500.0)


def _facts(**kw):
    f = {"gt": 600.0, "dead": False, "safe": False, "my_role": "TOP", "role_lane": "top", "my_lane": "top",
         "me_pos": (0.1, 0.2), "in_base": False, "missing": 0, "numbers": (0, 1), "wave": None,
         "jungler": {"known": True, "name": "Lee Sin", "visible": False, "side": None, "dist": None,
                     "hidden_s": 10.0, "dead": False, "last_side": "mid"},
         "opponents": [{"alias": "Darius", "name": "Darius", "dead": False, "level": 9, "respawn": 0.0}],
         "objectives": []}
    f.update(kw)
    return f


def test_stance_aggressive_and_safe_with_reasons():
    sb = NS(my_matchup=NS(enemy="Darius", level_diff=1, gold_diff=900, cs_diff=10), team_gold_diff=0, players=())
    jg = dict(_facts()["jungler"], visible=True, side="bot", dist=0.8)
    st = stance_from_factors(stance_factors(_facts(jungler=jg), _game(), sb))
    assert st.level == "agressif" and "Darius" in st.reason and "jungler ennemi vu en bas" in st.reason
    st = stance_from_factors(stance_factors(_facts(missing=3), _game(hp=0.4), None))
    assert st.level == "prudent" and "3 ennemis disparus" in st.reason and "40 % PV" in st.reason
    assert stance_from_factors([]).level == "equilibre"


def test_stance_speaks_only_on_change_every_2_min_never_during_threat():
    adv = StanceAdvisor()
    g = _game(hp=0.3)
    said = []
    for i in range(0, 400):
        t = i * 0.5
        threat = 1 if 20 <= t < 30 else 0
        said += adv.update(t, _facts(missing=3 if t < 100 else 0), g, None, threat=threat)
    assert said and all(a.key.startswith("stance:") for a in said)
    times = [a.t for a in said]
    assert all(b - a >= 120 for a, b in zip(times, times[1:]))
    assert not any(20 <= x < 30 for x in times)
    assert adv.current() is not None and adv.current().label in ("PRUDENT", "ÉQUILIBRÉ", "AGRESSIF")
    assert StanceAdvisor().update(0, {}, None) == []


def test_tip_library_and_rotation():
    assert tips.tip_count() >= 100
    assert len({t.id for t in tips.TIPS}) == len(tips.TIPS)
    ctx = tips.build_context(_facts(objectives=[{"key": "dragon", "name": "Dragon", "alive": False,
                                                 "remaining": 70.0}]), _game(), None)
    assert ctx.soon["dragon"] == 70.0 and ctx.role == "TOP"
    rot = tips.TipRotator(seed=1)
    first = rot.update(0.0, ctx)
    assert first and "Dragon" in first                         # urgent contextual tip first
    assert rot.update(5.0, ctx) == first                       # stays
    seen = {rot.update(30.0 * k, ctx) for k in range(1, 12)}
    assert len(seen) >= 8                                      # rotates without repeating
    hist = rot.history()
    assert len(hist) == len(set(hist))
    for t in tips.TIPS:                                        # every tip renders on a neutral context
        assert t.render(tips.TipContext())


def test_voice_policy_minimal_and_gate():
    def A(kind, key, text="x", level=Level.INFO):
        return Alert(kind=kind, level=level, text=text, key=key, t=0.0)
    assert vp.route(A(AlertKind.JUNGLER_APPROACH, "g", level=Level.WARNING)) == "voice"
    assert vp.route(A(AlertKind.OBJECTIVE_SOON, "objective_soon:dragon:60")) == "voice"
    assert vp.route(A(AlertKind.OBJECTIVE_SOON, "objective_soon:dragon:20")) == "text"
    assert vp.route(A(AlertKind.MACRO_TIP, "stance:prudent")) == "voice"
    assert vp.route(A(AlertKind.MACRO_TIP, "macro_tip:lane_dead")) == "text"
    assert vp.route(A(AlertKind.PRAISE, "solo:3")) == "voice"
    assert vp.route(A(AlertKind.PRAISE, "cs:900")) == "text"
    assert vp.route(A(AlertKind.CONTROL_WARD, "control_ward")) == "text"
    assert vp.route(A(AlertKind.MACRO_TIP, "macro_tip:x"), "normal") == "voice"
    assert vp.route(A(AlertKind.RECALL_GOLD, "recall_gold"), "normal") == "text"
    assert vp.route(A(AlertKind.RECALL_GOLD, "recall_gold"), "bavard") == "voice"
    gate = vp.MessageGate()
    ward = A(AlertKind.CONTROL_WARD, "control_ward", "Achète une balise de contrôle.")
    shown = [t for t in range(0, 1200, 5) if gate.allow(ward, float(t))]
    assert shown == [0, 600]                                   # anti-spam: no "15000 times"
    recall = A(AlertKind.RECALL_GOLD, "recall_gold", "Rentre.")
    assert [t for t in range(0, 400, 10) if gate.allow(recall, float(t))] == [0, 180, 360]
    gank = A(AlertKind.COLLAPSE, "c", level=Level.DANGER)
    assert all(gate.allow(gank, float(t)) for t in range(5))


def test_analysis_and_report_scoreboard_section():
    record = {"meta": {"team": "ORDER"}, "alerts": [[100.0, "praise", 0, "Joli solo kill sur Darius !", "Darius"]],
              "scoreboard": {"final": {"players": [{"alias": "Darius", "name": "Darius"}], "team_gold_diff": 1200,
                                       "ally_kills": 5, "enemy_kills": 3, "fed": ["Darius"],
                                       "matchups": [{"role": "TOP", "ally": "Garen", "enemy": "Darius",
                                                     "gold_diff": -1800, "cs_diff": -35, "level_diff": -1,
                                                     "involves_me": True}]},
                             "timeline": [[60.0, 100, {"TOP": [-200, -3, 0]}]]}}
    a = analyze_game(record)
    sb = a["scoreboard"]
    assert sb["available"] and sb["my_matchup"]["enemy"] == "Darius" and sb["praise_count"] == 1
    assert any(t["rule"] == "lane_lost" for t in a["tip_items"])
    assert "Darius" in a["spoken_summary"]
    html = render_report_html(record, a)
    assert "Tableau des scores" in html and "Joli solo kill" in html
