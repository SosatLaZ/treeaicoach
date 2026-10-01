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
    assert st.level == "prudent" and "3 ennemis disparus" in st.reason and "40 % de vie" in st.reason
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
    assert tips.tip_count() >= 70
    assert len({t.id for t in tips.TIPS}) == len(tips.TIPS)
    ctx = tips.build_context(_facts(objectives=[{"key": "dragon", "name": "Dragon", "alive": False,
                                                 "remaining": 70.0}]), _game(), None)
    assert ctx.soon["dragon"] == 70.0 and ctx.role == "TOP"
    rot = tips.TipRotator(seed=1)
    first = rot.update(0.0, ctx)
    assert first and "dragon" in first.lower()                 # contextual objective tip first
    assert rot.update(5.0, ctx) == first                       # stays
    seen = {rot.update(30.0 * k, ctx) for k in range(1, 12)}
    assert len(seen) >= 4                                      # rotates through the useful ones
    hist = rot.history()
    assert len(hist) == len(set(hist))
    for t in tips.TIPS:                                        # every tip renders on a neutral context
        text = t.render(tips.TipContext())
        assert text and " : " in text and len(text.split()) <= tips.MAX_WORDS, text   # WHAT : WHY, <= 12 words
        assert "{" not in text


def test_no_vague_tips_and_safe_only_with_a_reason():
    texts = [t.text.lower() for t in tips.TIPS]
    assert not any("minimap" in x for x in texts)
    assert not any(x.startswith("joue safe") and "{" not in x for x in texts)   # "joue safe" always with numbers
    assert not any(w in x for x in texts for w in ("tempo", "freeze", "crash"))


def test_specific_advice_beats_generic_and_never_goes_stale():
    rot = tips.TipRotator(seed=0)
    ctx = tips.build_context(_facts(gt=400.0), _game(gt=400.0), NS(my_matchup=NS(enemy="Darius", level_diff=-2,
                             gold_diff=-300, cs_diff=0), team_gold_diff=0, players=()))
    text = rot.update(0.0, ctx)
    assert text == "Joue prudemment sous ta tour : Darius a 2 niveaux d'avance"
    # the opponent dies: the opportunity replaces it at once (more useful)
    ctx2 = tips.build_context(_facts(gt=402.0, opponents=[{"alias": "Darius", "name": "Darius", "dead": True,
                                                           "level": 9}]), _game(gt=402.0), None)
    assert rot.update(2.0, ctx2) == "Pousse ta vague et tape la tour : Darius est mort"
    # he respawns: the advice disappears after the short grace (never stale)
    ctx3 = tips.build_context(_facts(gt=430.0), _game(gt=430.0), None)
    rot.update(30.0, ctx3)
    out = rot.update(32.0, ctx3)
    assert out is None or "mort" not in out


def test_voice_policy_minimal_and_gate():
    def A(kind, key, text="x", level=Level.INFO):
        return Alert(kind=kind, level=level, text=text, key=key, t=0.0)
    assert vp.route(A(AlertKind.JUNGLER_APPROACH, "g", level=Level.WARNING)) == "voice"
    # V2 voice whitelist: an objective is spoken only at its LAST warning (<= 20 s), the 60 s one is written
    assert vp.route(A(AlertKind.OBJECTIVE_SOON, "objective_soon:dragon:60")) == "text"
    assert vp.route(A(AlertKind.OBJECTIVE_SOON, "objective_soon:dragon:20")) == "voice"
    assert vp.route(A(AlertKind.OBJECTIVE_SOON, "objective_soon:dragon:60"), "bavard") == "voice"
    # visual first: the stance, praise, macro tips and director calls are written at every level but "bavard"
    assert vp.route(A(AlertKind.MACRO_TIP, "stance:prudent")) == "text"
    assert vp.route(A(AlertKind.MACRO_TIP, "stance:prudent"), "normal") == "text"
    assert vp.route(A(AlertKind.MACRO_TIP, "call:retreat")) == "voice"
    assert vp.route(A(AlertKind.MACRO_TIP, "call:engage")) == "text"          # the banner says it
    assert vp.route(A(AlertKind.MACRO_TIP, "urgent:pos:alone")) == "text"
    assert vp.route(A(AlertKind.MACRO_TIP, "urgent:pos:alone"), "normal") == "text"
    assert vp.route(A(AlertKind.MACRO_TIP, "urgent:ace:baron"), "normal") == "voice"   # the big numbers call
    assert vp.route(A(AlertKind.MACRO_TIP, "urgent:ace:baron")) == "text"
    assert vp.route(A(AlertKind.MACRO_TIP, "macro_tip:lane_dead")) == "text"
    assert vp.route(A(AlertKind.PRAISE, "solo:3")) == "text"
    assert vp.route(A(AlertKind.PRAISE, "solo:3"), "normal") == "text"
    assert vp.route(A(AlertKind.PRAISE, "solo:3"), "bavard") == "voice"
    assert vp.route(A(AlertKind.PRAISE, "cs:900")) == "text"
    assert vp.route(A(AlertKind.CONTROL_WARD, "control_ward")) == "text"
    assert vp.route(A(AlertKind.MACRO_TIP, "macro_tip:x"), "normal") == "text"
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
