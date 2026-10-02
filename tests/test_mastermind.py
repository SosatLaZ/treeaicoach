"""Mastermind (mastermind.py): compositions, power windows, win conditions, threats, consumers.

Validated on the real game of the user (Garen top vs Vladimir / Kindred / Ekko / Séraphine / Thresh,
allies Maître Yi / Mel / Ezreal / Brand: tests/fixtures/real/ground_truth.json "g1" + the numbers of
its post-game report) and on scripted comps (scaling vs early, enemy fed carry, our engage comp).
"""

from __future__ import annotations

import json
import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from treeaicoach import game_changers as gc  # noqa: E402
from treeaicoach import macro, mastermind as mm, phase  # noqa: E402
from treeaicoach.live_client import GameInfo, PlayerInfo  # noqa: E402

ROLES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
HERE = os.path.dirname(os.path.abspath(__file__))
REAL = json.load(open(os.path.join(HERE, "fixtures", "real", "ground_truth.json"), encoding="utf-8"))["rosters"]["g1"]
REAL_US = [REAL["self"]] + REAL["ally"]           # Garen, MasterYi, Mel, Ezreal, Brand
REAL_THEM = REAL["enemy"]                         # Vladimir, Kindred, Ekko, Seraphine, Thresh


def game(us, them, gt, lv=None, items=None, kda=None, stats=None, spells=None):
    lv, items, kda = lv or {}, items or {}, kda or {}

    def p(a, team, pos):
        k = kda.get(a, (0, 0, 0))
        return PlayerInfo(riot_id=f"{a}#X", summoner_name=f"{a}#X", champion_alias=a,
                          champion_name=mm._profile(a).name, team=team, position=pos,
                          level=lv.get(a, max(1, min(18, int(gt / 96) + 1))), items=list(items.get(a, [])),
                          scores={"kills": k[0], "deaths": k[1], "assists": k[2]},
                          spell_ids=tuple((spells or {}).get(a, ())), has_smite=pos == "JUNGLE")
    ps = [p(a, "ORDER", r) for a, r in zip(us, ROLES)]
    es = [p(a, "CHAOS", r) for a, r in zip(them, ROLES)]
    return GameInfo(game_time=float(gt), game_mode="CLASSIC", map_number=11, me=ps[0], allies=ps[1:], enemies=es,
                    events=[], champion_stats=stats or {"armor": 80.0, "magicResist": 45.0,
                                                        "currentHealth": 900.0, "maxHealth": 1000.0})


# ----------------------------------------------------------------------------- the real game
def test_real_game_start_reads_like_a_coach():
    r = mm.analyze(game(REAL_US, REAL_THEM, 90.0, spells={"Garen": ("SummonerFlash", "SummonerTeleport")}))
    assert r is not None
    # comps: our poke (Mel / Ezreal / Brand) with ONE frontline (me), their pick (Thresh / Ekko), magic damage
    assert r.us.identity[0] == "poke" and r.us.frontline == ("Garen",)
    assert "pick" in r.them.identity and r.them.damage == "magique"
    assert any("Résistance magique en priorité" in x for x in r.buy)
    assert any("Anti-soin" in x and "Vladimir" in x for x in r.buy)
    # my lane: Garen is stronger than Vladimir early, Vladimir scales: a window with an end
    assert r.lane is not None and r.lane.leader == "us" and r.lane.until is not None
    assert r.lane.who == "Vladimir" and "punis-le" in r.lane.text
    assert 15 * 60 <= r.lane.until <= 26 * 60
    # their plan / our counter, my role (only tank, top, TP)
    assert any("isolés" in x for x in r.ours) and any("Attraper un isolé" in x for x in r.theirs)
    assert r.role.startswith("Toi : seul tank") and "TP sur les dragons" in r.role
    # threats: my lane opponent is on the list, tagged
    vlad = next(t for t in r.threats if t.alias == "Vladimir")
    assert "ton adversaire" in vlad.tags
    assert len(r.ours) <= 3 and len(r.theirs) <= 3


def test_real_game_ten_minutes_vladimir_ahead_flips_the_lane():
    # report: 10 min, -787 gold and -1568 XP against Vladimir, Kindred involved in the deaths
    g = game(REAL_US, REAL_THEM, 600.0,
             lv={"Garen": 7, "Vladimir": 9, "MasterYi": 7, "Kindred": 9, "Mel": 8, "Ekko": 9, "Ezreal": 8,
                 "Seraphine": 8, "Brand": 7, "Thresh": 7},
             items={"Garen": [1055, 3047, 1036], "Vladimir": [1056, 3020, 3152], "Kindred": [1103, 3006, 6672],
                    "Ekko": [3020, 1058], "Seraphine": [3020, 1058], "Thresh": [3876]},
             kda={"Garen": (0, 3, 0), "Vladimir": (3, 0, 1), "Kindred": (4, 0, 3), "Ekko": (1, 0, 2)})
    r = mm.analyze(g)
    assert r.lane.leader == "them" and "évite les échanges" in r.lane.text
    assert r.team.leader == "them"
    assert r.jungle is not None and r.jungle.leader == "them" and r.jungle.who == "Kindred"
    # the gauge never claims "Vladimir est faible" while he is two levels up (curve vs the real gap)
    assert not any("faible" in why for _w, why in r.gauge_factors())
    assert "sans mourir" in r.role
    top = r.threats[0]
    assert top.alias in ("Vladimir", "Kindred") and top.reasons
    snap = r.snapshot()
    json.dumps(snap, ensure_ascii=False)
    assert snap["fenetre"]["voie"]["qui"] == "eux" and snap["mon_role"] == r.role
    assert snap["menaces"] and snap["nous"]["devant"] == ["Garen"]


# ----------------------------------------------------------------------------- scripted comps
def test_scaling_comp_against_early_comp_plays_the_clock():
    r = mm.analyze(game(["Kassadin", "MasterYi", "Kayle", "Jinx", "Sona"],
                        ["Darius", "LeeSin", "Pantheon", "Draven", "Leona"], 150.0))
    assert r.us.tempo == "scaling" and r.them.tempo == "early"
    assert r.team.leader == "them" and r.team.until is not None and r.team.then == "us"
    assert r.ours[0].startswith("Joue le temps : ton équipe est plus forte après ~")
    assert r.theirs[0].startswith("Forcer les objectifs avant ~")
    # the other way round: we are the early comp
    r2 = mm.analyze(game(["Darius", "LeeSin", "Pantheon", "Draven", "Leona"],
                         ["Kassadin", "MasterYi", "Kayle", "Jinx", "Sona"], 150.0))
    assert r2.team.leader == "us" and r2.team.until is not None
    assert r2.ours[0].startswith("Force les objectifs avant ~")
    assert "Ton équipe est plus forte jusqu'à ~" in r2.team.text


def test_enemy_fed_carry_is_the_first_threat_and_tilts_the_window():
    them = ["Darius", "LeeSin", "Ahri", "Caitlyn", "Nautilus"]
    g = game(["Garen", "Vi", "Lux", "Jinx", "Thresh"], them, 1200.0,
             items={"Caitlyn": [3031, 3006, 3094, 3036], "Ahri": [3020, 6655], "Darius": [3071]},
             kda={"Caitlyn": (9, 1, 3), "Garen": (1, 4, 2)})
    r = mm.analyze(g)
    assert r.threats[0].alias == "Caitlyn"
    assert "le plus avancé" in r.threats[0].tags and "9/1/3" in r.threats[0].reasons
    assert r.team.leader == "them"


def test_our_engage_comp_says_engage():
    r = mm.analyze(game(["Malphite", "Amumu", "Orianna", "MissFortune", "Leona"],
                        ["Jayce", "Nidalee", "Xerath", "Ezreal", "Karma"], 900.0))
    assert r.us.identity[0] == "engage" and len(r.us.frontline) >= 3
    assert "poke" in r.them.identity
    assert any(x.startswith("Engage à 5 sur les objectifs avec") for x in r.ours)
    assert any("harcèlent de loin" in x for x in r.ours)
    assert "ouvre les combats" in r.role


def test_curve_and_projection_basics():
    assert mm.curve_value("early", 5) > mm.curve_value("late", 5)
    assert mm.curve_value("late", 35) > mm.curve_value("early", 35)
    assert abs(mm.curve_value("mid", 20.5) - (mm.curve_value("mid", 20) + mm.curve_value("mid", 21)) / 2) < 1e-9
    assert mm.analyze(None) is None
    assert mm.analyze(GameInfo(game_time=100.0)) is None            # spectating / no me


def test_analyze_is_cheap():
    g = game(REAL_US, REAL_THEM, 1500.0, items={"Vladimir": [3020, 3152, 4645], "Kindred": [3006, 6672]})
    mm.analyze(g)                                                   # warm the tables
    t0 = time.perf_counter()
    n = 100
    for i in range(n):
        g.game_time = 1500.0 + i                                    # cache miss on every call
        mm.analyze(g)
    per = (time.perf_counter() - t0) / n * 1000.0
    assert per < 3.0, f"{per:.2f} ms per analyse"                  # ~0.7 ms on the dev box; CI margin


# ----------------------------------------------------------------------------- live model
def test_model_history_freshness_voice_budget_and_summary():
    m = mm.MastermindModel()
    g = game(REAL_US, REAL_THEM, 20.0)
    assert m.update(20.0, g) is not None
    assert m.first_gt == 20.0 and m.since("lane")[0] == "us"
    assert m.watched(80.0) == 60.0
    assert m.voice_ok(100.0)
    m.note_voice(100.0)
    assert not m.voice_ok(200.0) and m.voice_ok(281.0)
    m.note_call(150.0, "Punis Vladimir maintenant : tu es plus fort jusqu'à ~21:00")
    s = m.summary()
    json.dumps(s, ensure_ascii=False)
    assert s["start"] is not None and s["calls"] and s["windows"]
    m.update(10.0, game(REAL_US, REAL_THEM, 10.0))                 # new game (time went back)
    assert m.first_gt == 10.0 and not m.calls and not m.plan_shown


def _ctx(g, role="TOP", me_uv=(0.085, 0.30), objectives=()):
    st = phase.map_state(g, g.game_time, role)
    return macro.build_ctx(100.0, g.game_time, g, st, role=role, me_uv=me_uv, objectives=objectives)


def test_window_rule_lane_call_once_at_game_start_never_at_app_start():
    mm.set_active(None)
    m = mm.MastermindModel()
    mm.set_active(m)
    try:
        for gt in (20.0, 60.0, 120.0, 150.0, 160.0):
            g = game(REAL_US, REAL_THEM, gt)
            m.update(gt, g)
        c = gc.rule_power_window(_ctx(g))
        assert c is not None and c.kind == "gc_window"
        assert c.text.startswith("Punis Vladimir maintenant : tu es plus fort jusqu'à ~") and len(c.text) <= 60
        assert c.voice == gc.VOICE_WINDOW_NOW and c.color == "safe" and c.tier == "basic"
        # app started mid-game (first reading at 6:00): the same window is not news
        m2 = mm.MastermindModel()
        mm.set_active(m2)
        for gt in (360.0, 400.0):
            g2 = game(REAL_US, REAL_THEM, gt)
            m2.update(gt, g2)
        assert gc.rule_power_window(_ctx(g2)) is None
    finally:
        mm.set_active(None)


def _watched(g, gt0=300.0):
    """A live model registered for ``g`` that has watched the game since ``gt0``."""
    m = mm.MastermindModel()
    m.update(gt0, g)
    m.update(g.game_time, g)
    mm.set_active(m)
    return m


def _as_bottom(g):
    g.me, g.allies = g.allies[2], [g.me] + g.allies[:2] + g.allies[3:]
    return g


def test_window_rule_objective_call_for_the_roles_that_play_it():
    us, them = ["Darius", "LeeSin", "Pantheon", "Draven", "Leona"], ["Kassadin", "MasterYi", "Kayle", "Jinx", "Sona"]
    soon = [SimpleNamespace(key="dragon", alive=False, remaining=30.0)]
    try:
        g = _as_bottom(game(us, them, 400.0))                     # I am Draven (BOTTOM)
        keep = [_watched(g)]                                      # registered weakly: hold it
        c = gc.rule_power_window(_ctx(g, role="BOTTOM", me_uv=(0.80, 0.91), objectives=soon))
        assert c is not None and c.text.startswith("Prépare le dragon : ton équipe domine") and len(c.text) <= 60
        assert c.title == "TA FENÊTRE" and c.tier == "basic"
        # without the live model (app just started): nothing
        mm.set_active(None)
        assert gc.rule_power_window(_ctx(g, role="BOTTOM", me_uv=(0.80, 0.91), objectives=soon)) is None
        # the top laner is not sent to the dragon
        g_top = game(us, them, 400.0)
        keep.append(_watched(g_top))
        assert gc.rule_power_window(_ctx(g_top, role="TOP", objectives=soon)) is None
        # the weaker team: do not contest it
        g3 = _as_bottom(game(them, us, 400.0))
        keep.append(_watched(g3))
        c3 = gc.rule_power_window(_ctx(g3, role="BOTTOM", me_uv=(0.80, 0.91), objectives=soon))
        assert c3 is not None and c3.text.startswith("Ne conteste pas le dragon") and c3.title == "LEUR FENÊTRE"
    finally:
        mm.set_active(None)


def test_window_voice_budget_one_per_three_minutes():
    m = mm.MastermindModel()
    mm.set_active(m)
    try:
        m.update(300.0, game(REAL_US, REAL_THEM, 300.0))
        call = SimpleNamespace(kind="gc_window", ident="gc_window:x", voice=gc.VOICE_WINDOW_NOW, title="TA FENÊTRE",
                               text="Punis Vladimir maintenant : tu es plus fort")
        ctx = SimpleNamespace(gt=300.0, game=None)
        assert gc.voice_for(call, ctx, "debutant") is not None
        assert gc.voice_for(call, SimpleNamespace(gt=400.0, game=None), "debutant") is None
        assert gc.voice_for(call, SimpleNamespace(gt=300.0, game=None), "intermediaire") is None
        assert gc.VOICE_WINDOW_NOW in gc.voice_phrases() and gc.topic_of("gc_window") == "power_window"
    finally:
        mm.set_active(None)


def test_soft_window_call_never_blocks_a_situational_call():
    p = macro.MacroPlanner()
    soft = macro._call("gc_window", "gc_window:a", "TA FENÊTRE", "Punis Darius maintenant : tu es plus fort",
                       "why", None, tier="basic", score=0.38, priority=58)
    hard = macro._call("gc_jungler_far", "gc_jg_far:bot:1", "JOUE AGRESSIF", "Joue agressif : Lee Sin est en bas",
                       "why", None, tier="basic", score=0.62, priority=77)
    rules = [[soft]]
    orig = macro.evaluate
    macro.evaluate = lambda ctx: list(rules[0])                    # type: ignore[assignment]
    try:
        ctx = macro.MacroCtx(t=100.0, gt=300.0)
        assert p.update(ctx, "debutant").new is soft
        rules[0] = [hard, soft]
        up = p.update(macro.MacroCtx(t=103.0, gt=303.0), "debutant")   # within the hold and the gap
        assert up.new is not None and up.new.kind == "gc_jungler_far"
    finally:
        macro.evaluate = orig                                      # type: ignore[assignment]


# ----------------------------------------------------------------------------- game plan + report
def test_team_card_is_card_material():
    from treeaicoach import game_plan, presenter

    r = mm.analyze(game(["Garen", "Vi", "Lux", "Jinx", "Thresh"], ["Darius", "LeeSin", "Ahri", "Caitlyn", "Nautilus"],
                        100.0))
    card = game_plan.team_card(r)
    assert card is not None and card.title == "PLAN D'ÉQUIPE"
    assert presenter.card_line(card.subtitle) == card.subtitle and len(card.subtitle) <= 60
    assert any(x.startswith("Toi : ") for x in card.lines)
    assert game_plan.team_card(None) is None


def _real_record() -> dict:
    """A record of the real game (Garen 1/15/5, 36:12, dragons 0-4) rebuilt from its report."""
    us = [("Garen", "TOP", True), ("MasterYi", "JUNGLE", False), ("Mel", "MIDDLE", False),
          ("Ezreal", "BOTTOM", False), ("Brand", "UTILITY", False)]
    them = [("Vladimir", "TOP"), ("Kindred", "JUNGLE"), ("Ekko", "MIDDLE"), ("Seraphine", "BOTTOM"),
            ("Thresh", "UTILITY")]
    roster = [{"alias": a, "name": mm._profile(a).name, "team": "ORDER", "position": p, "riot_id": f"{a}#EUW",
               "summoner_name": a, "is_me": me} for a, p, me in us]
    roster += [{"alias": a, "name": mm._profile(a).name, "team": "CHAOS", "position": p, "riot_id": f"{a}#EUW",
                "summoner_name": a, "is_me": False} for a, p in them]
    events = [{"EventName": "DragonKill", "EventTime": t, "KillerName": "Kindred#EUW", "DragonType": "Fire"}
              for t in (330.0, 900.0, 1250.0, 1660.0)]
    events += [{"EventName": "ChampionKill", "EventTime": t, "KillerName": "Vladimir#EUW", "VictimName": "Garen#EUW"}
               for t in (156.0, 298.0, 545.0, 779.0, 900.0, 1124.0, 1265.0, 1422.0, 1578.0, 1751.0, 1822.0, 1920.0,
                         1989.0, 2050.0, 2146.0)]
    timeline = [[60.0 * m, -300.0 * m, {}] for m in range(1, 37)]
    final = {"players": [{"alias": "Garen", "level": 16, "kills": 1, "deaths": 15, "assists": 5, "item_gold": 9000},
                         {"alias": "Vladimir", "level": 18, "kills": 10, "deaths": 2, "assists": 6, "item_gold": 15500}]}
    return {"schema": 1, "roster": roster, "events": events, "duration": 2172.0, "result": "Lose",
            "meta": {"team": "ORDER"}, "scoreboard": {"final": final, "timeline": timeline}}


def test_post_game_understanding_of_the_real_game():
    u = mm.understanding(_real_record())
    assert u is not None
    assert u["plan"]["voie"].startswith("Tu es plus fort que Vladimir jusqu'à ~")
    assert u["nous"]["devant"] == ["Garen"] and "pick" in u["eux"]["identite"]
    assert u["objectifs"] and all(side == "them" for _g, w, side in u["objectifs"] if w == "dragon")
    assert any("ils étaient plus forts" in x for x in u["fait"])
    assert "morts avant 14:00" in u["role"] or "mort" in u["role"]
    assert u["fenetres"][-1][2] == "them"
    json.dumps(u, ensure_ascii=False)


def test_report_has_the_understanding_section():
    from treeaicoach import report

    html = report.render_report_html(_real_record())
    assert "Compréhension de la partie" in html and "Qui était le plus fort" in html
    assert "Le plan au début" in html
    assert report.render_report_html({"roster": []}).count("Compréhension") == 0


def test_attach_to_record_roundtrip(tmp_path):
    p = tmp_path / "g.json"
    p.write_text(json.dumps(_real_record()), encoding="utf-8")
    m = mm.MastermindModel()
    m.update(100.0, game(REAL_US, REAL_THEM, 100.0))
    assert mm.attach_to_record(p, m.summary())
    rec = json.loads(p.read_text(encoding="utf-8"))
    assert rec["mastermind"]["schema"] == 1
    u = mm.understanding(rec)
    assert u is not None and u["live"] is True
    assert not mm.attach_to_record(tmp_path / "missing.json", {})
