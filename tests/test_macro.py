"""Tests for treeaicoach.macro (COUPS DE GÉNIE macro planner) on scripted states."""
from __future__ import annotations

from types import SimpleNamespace

from treeaicoach import geometry, macro, phase
from treeaicoach.fight import Seen
from treeaicoach.live_client import GameInfo, PlayerInfo
from treeaicoach.waves import LaneWave

ROSTER = (("Garen", "ORDER", "TOP"), ("Vi", "ORDER", "JUNGLE"), ("Lux", "ORDER", "MIDDLE"),
          ("Jinx", "ORDER", "BOTTOM"), ("Thresh", "ORDER", "UTILITY"),
          ("Darius", "CHAOS", "TOP"), ("LeeSin", "CHAOS", "JUNGLE"), ("Ahri", "CHAOS", "MIDDLE"),
          ("Caitlyn", "CHAOS", "BOTTOM"), ("Nautilus", "CHAOS", "UTILITY"))


def game(gt=600.0, me="Garen", dead=None, gold=500.0, hp=1.0, events=()):
    dead = dict(dead or {})
    ps = {}
    for alias, team, pos in ROSTER:
        r = dead.get(alias)
        ps[alias] = PlayerInfo(riot_id=f"{alias}#X", summoner_name=f"{alias}#X", champion_alias=alias,
                               champion_name="Lee Sin" if alias == "LeeSin" else alias, team=team, position=pos,
                               is_dead=r is not None, respawn_timer=float(r or 0.0), level=9,
                               has_smite=pos == "JUNGLE")
    my_team = ps[me].team
    g = GameInfo(game_time=gt, game_mode="CLASSIC", map_number=11, me=ps[me],
                 allies=[p for a, p in ps.items() if p.team == my_team and a != me],
                 enemies=[p for p in ps.values() if p.team != my_team], events=list(events), current_gold=gold,
                 champion_stats={"currentHealth": 1000.0 * hp, "maxHealth": 1000.0})
    return g


def obj(key, alive=False, remaining=None):
    return SimpleNamespace(key=key, alive=alive, remaining=remaining)


def turret_event(name, T=500.0):
    return {"EventName": "TurretKilled", "TurretKilled": name, "EventTime": T, "KillerName": "Darius#X"}


def ctx_for(g, *, role=None, me_uv=None, enemies=(), allies=(), objectives=(), waves=None, t=100.0, jint=None,
            **kw):
    me = g.me
    st = phase.map_state(g, g.game_time, role or me.position)
    return macro.build_ctx(t, g.game_time, g, st, role=role or me.position, me_uv=me_uv, allies=allies,
                           enemies=enemies, objectives=objectives, waves=waves, jint=jint, **kw)


def kinds(ctx):
    return [c.kind for c in macro.evaluate(ctx)]


TOP_LANE_ME = (0.085, 0.30)
DARIUS_LANE = (0.09, 0.22)


# ------------------------------------------------------------------ helpers
def test_cannon_wave_timing():
    assert macro.wave_number(29.0) == 0 and macro.wave_number(30.0) == 1 and macro.wave_number(95.0) == 3
    assert macro.is_cannon_wave(3, 90.0) and not macro.is_cannon_wave(4, 120.0)
    assert macro.is_cannon_wave(2, 960.0) and macro.is_cannon_wave(7, 1600.0)
    assert 0.0 <= macro.next_cannon_arrival(100.0) <= 90.0
    assert macro.next_cannon_arrival(100.0) == 18.0          # wave 3 (cannon) spawned 90 s, arrives 118 s


def test_lane_uv_follows_my_base():
    blue = macro.lane_uv("top", 0.1, "ORDER")
    red = macro.lane_uv("top", 0.1, "CHAOS")
    assert blue[0] < 0.15 and blue[1] > 0.6          # near the blue base, left edge
    assert red[1] < 0.15 and red[0] > 0.6            # near the red base, top edge


def test_jungler_location_sources():
    g = game(400.0)
    c = ctx_for(g, me_uv=TOP_LANE_ME, enemies=[Seen("LeeSin", (0.68, 0.72), True, 0.0)])
    loc = macro.jungler_location(c)
    assert loc.side == "bot" and loc.conf == 1.0 and loc.source == "seen"
    c = ctx_for(g, me_uv=TOP_LANE_ME, enemies=[Seen("LeeSin", (0.68, 0.72), False, 30.0)])
    assert macro.jungler_location(c).conf < 0.3
    ji = SimpleNamespace(alias="LeeSin", farm_side="top", last_farm_t=95.0, farm_points=((0.3, 0.3),))
    c = ctx_for(g, me_uv=TOP_LANE_ME, jint=ji)
    loc = macro.jungler_location(c)
    assert loc.side == "top" and loc.source == "tab" and 0.5 < loc.conf <= 0.8
    c = ctx_for(game(400.0, dead={"LeeSin": 20.0}), me_uv=TOP_LANE_ME)
    assert macro.jungler_location(c).dead


# ------------------------------------------------------------------ rules
def test_fight_won_baron_with_respawn_window():
    g = game(1500.0, dead={"Ahri": 32.0, "Caitlyn": 30.0, "Nautilus": 28.0})
    c = ctx_for(g, me_uv=(0.42, 0.58), objectives=[obj("baron", True)])
    calls = macro.evaluate(c)
    assert calls[0].kind == "fight_won"
    call = calls[0]
    assert "3 morts (28 s)" in call.text and "Baron" in call.text and call.genius
    assert call.target == (geometry.BARON_PIT[0], geometry.BARON_PIT[1])
    assert "5 contre 2" in call.why
    # only one dead: no follow-up
    assert "fight_won" not in kinds(ctx_for(game(1500.0, dead={"Ahri": 30.0}), me_uv=(0.42, 0.58),
                                            objectives=[obj("baron", True)]))
    # no Baron: a tower / inhibitor instead
    c = ctx_for(game(1500.0, dead={"Ahri": 32.0, "Caitlyn": 30.0}), me_uv=(0.42, 0.58))
    fw = [x for x in macro.evaluate(c) if x.kind == "fight_won"]
    assert fw and fw[0].target is not None and "tour" in fw[0].text


def test_fight_lost_go_defend():
    g = game(1300.0, dead={"Vi": 30.0, "Lux": 28.0, "Jinx": 25.0})
    c = ctx_for(g, me_uv=(0.5, 0.5))
    call = macro.evaluate(c)[0]
    assert call.kind == "fight_lost" and call.text.startswith("Recule")
    assert "2 contre 5" in call.why and call.color == "danger" and call.target is not None


def test_jungler_dead_invade_or_objective():
    g = game(700.0, me="Vi", dead={"LeeSin": 30.0})
    call = [c for c in macro.evaluate(ctx_for(g, me_uv=(0.3, 0.6))) if c.kind == "jungler_dead"][0]
    assert "envahis" in call.text and call.genius and call.target in macro.JUNGLE_UV.values()
    g = game(700.0, me="Jinx", dead={"LeeSin": 30.0})
    call = [c for c in macro.evaluate(ctx_for(g, me_uv=(0.7, 0.9), objectives=[obj("dragon", True)]))
            if c.kind == "jungler_dead"][0]
    assert "dragon" in call.text and "(30 s)" in call.text
    assert call.target == (geometry.DRAGON_PIT[0], geometry.DRAGON_PIT[1])


def test_plates_when_lane_opponent_dead():
    g = game(600.0, dead={"Darius": 20.0})
    c = ctx_for(g, me_uv=TOP_LANE_ME)
    call = [x for x in macro.evaluate(c) if x.kind == "plates"][0]
    assert call.text.startswith("Plaque la tour (20 s)") and "Darius" in call.text and "125 PO" in call.why
    assert call.target == phase.TURRET_UV[("CHAOS", "top", 1)]
    # after 14:00: the tower itself
    c = ctx_for(game(900.0, dead={"Darius": 25.0}), me_uv=TOP_LANE_ME)
    assert [x for x in macro.evaluate(c) if x.kind == "plates"][0].text.startswith("Frappe la tour")
    # their jungler right at the tower: no call
    c = ctx_for(g, me_uv=TOP_LANE_ME, enemies=[Seen("LeeSin", phase.TURRET_UV[("CHAOS", "top", 1)], True, 0.0)])
    assert "plates" not in kinds(c)
    # opponent alive and in lane: nothing
    c = ctx_for(game(600.0), me_uv=TOP_LANE_ME, enemies=[Seen("Darius", DARIUS_LANE, True, 0.0)])
    assert "plates" not in kinds(c)


def test_cross_map_trade_and_free_dragon():
    g = game(500.0)
    c = ctx_for(g, me_uv=TOP_LANE_ME, enemies=[Seen("LeeSin", (0.67, 0.71), True, 0.0)],
                objectives=[obj("grubs", True), obj("dragon", True)])
    call = [x for x in macro.evaluate(c) if x.kind == "cross_trade"][0]
    # a top laner HELPS his jungler (a solo laner does not take grubs / Herald alone)
    assert call.text.startswith("Aide ton jungler aux larves") and "au dragon" in call.text and call.genius
    cj = ctx_for(game(500.0, me="Vi"), me_uv=(0.3, 0.45), enemies=[Seen("LeeSin", (0.67, 0.71), True, 0.0)],
                 objectives=[obj("grubs", True)])
    assert [x for x in macro.evaluate(cj) if x.kind == "cross_trade"][0].text.startswith("Prends les larves")
    g = game(500.0, me="Jinx")
    c = ctx_for(g, me_uv=(0.7, 0.9), enemies=[Seen("LeeSin", (0.25, 0.25), True, 0.0)],
                objectives=[obj("dragon", True)])
    call = [x for x in macro.evaluate(c) if x.kind == "free_dragon"][0]
    assert call.text.startswith("Le dragon est libre") and call.target == macro.DRAGON_UV
    # nothing known about the jungler: no trade
    c = ctx_for(g, me_uv=(0.7, 0.9), objectives=[obj("dragon", True)])
    assert "free_dragon" not in kinds(c)


def test_rotate_mid_after_bot_tower():
    g = game(900.0, me="Jinx", events=[turret_event("Turret_T1_R_03_A", 880.0)])
    c = ctx_for(g, me_uv=(0.75, 0.92))
    call = [x for x in macro.evaluate(c) if x.kind == "rotate_mid"][0]
    assert call.text.startswith("Va mid") and call.title == "VA MID"
    assert call.target == macro.LANE_POINT["ORDER"]["mid"]
    # already mid: nothing
    assert "rotate_mid" not in kinds(ctx_for(g, me_uv=(0.45, 0.55)))


def test_side_wave_safe_split_rule():
    waves = {"bot": LaneWave("bot", ally=1, enemy=6, meet=0.3, state="pushed_in")}
    g = game(1300.0, me="Lux", events=[turret_event("Turret_T2_L_03_A", 1000.0), turret_event("Turret_T1_R_03_A", 900)])
    jg_top = [Seen("LeeSin", (0.3, 0.25), True, 0.0)]
    c = ctx_for(g, me_uv=(0.45, 0.55), enemies=jg_top, waves=waves)
    call = [x for x in macro.evaluate(c) if x.kind == "side_wave"][0]
    assert call.text == "Change de voie : va bot, la vague arrive et personne n'y est."
    assert "leur jungler est en haut" in call.why
    # jungler unknown and no enemies seen elsewhere: not safe, no call
    assert "side_wave" not in kinds(ctx_for(g, me_uv=(0.45, 0.55), waves=waves))
    # an ally already on that wave: not my job
    mate = [Seen("Jinx", macro.lane_uv("bot", 0.3, "ORDER"), True, 0.0)]
    assert "side_wave" not in kinds(ctx_for(g, me_uv=(0.45, 0.55), enemies=jg_top, allies=mate, waves=waves))


def test_split_safe_needs_three_enemies_far():
    g = game(1400.0, events=[turret_event("Turret_T1_R_03_A", 900)])
    far = [Seen(a, (0.8, 0.75), True, 0.0) for a in ("LeeSin", "Ahri", "Caitlyn", "Nautilus")]
    c = ctx_for(g, me_uv=(0.085, 0.30), enemies=far)
    call = [x for x in macro.evaluate(c) if x.kind == "split_safe"][0]
    assert "4 ennemis sont en bas" in call.text and "disparaissent" in call.why
    assert "split_safe" not in kinds(ctx_for(g, me_uv=(0.085, 0.30), enemies=far[:2]))
    # V2 audit: 3 seen far but THEIR JUNGLER unseen (+ Darius): no split call (no vision info)
    assert "split_safe" not in kinds(ctx_for(g, me_uv=(0.085, 0.30), enemies=far[1:]))


def test_lane_swap_top_leaves_two_versus_one():
    g = game(200.0)
    duo = [Seen("Caitlyn", (0.09, 0.25), True, 0.0), Seen("Nautilus", (0.1, 0.24), True, 0.0),
           Seen("Darius", (0.85, 0.9), True, 0.0)]
    call = [x for x in macro.evaluate(ctx_for(g, me_uv=TOP_LANE_ME, enemies=duo)) if x.kind == "lane_swap"][0]
    assert call.text == "Va bot : leur duo est en haut." and "Darius" in call.why


def test_wave_calls_recall_freeze_backoff():
    pushing = {"top": LaneWave("top", ally=5, enemy=2, meet=0.7, state="pushing")}
    g = game(400.0, gold=1400.0)
    c = ctx_for(g, me_uv=TOP_LANE_ME, waves=pushing, enemies=[Seen("Darius", DARIUS_LANE, True, 0.0)])
    call = [x for x in macro.evaluate(c) if x.kind == "wave_recall"][0]
    assert call.text == "Ta vague s'écrase sur leur tour : rentre maintenant."
    assert "1400 PO" in call.why
    even = {"top": LaneWave("top", ally=4, enemy=4, meet=0.5, state="even")}
    c = ctx_for(g, me_uv=TOP_LANE_ME, waves=even, enemies=[Seen("Darius", DARIUS_LANE, True, 0.0)])
    assert [x for x in macro.evaluate(c) if x.kind == "wave_recall"][0].text == "Pousse ta vague puis rentre."
    # their jungler close on my side while I push: freeze
    c = ctx_for(game(400.0), me_uv=TOP_LANE_ME, waves=pushing,
                enemies=[Seen("Darius", DARIUS_LANE, True, 0.0), Seen("LeeSin", (0.25, 0.33), False, 2.0)])
    call = [x for x in macro.evaluate(c) if x.kind == "wave_freeze"][0]
    assert call.text.startswith("Arrête de pousser") and call.color == "danger"
    # lane opponent missing, jungler unknown, I am pushed forward: back off
    c = ctx_for(game(400.0), me_uv=(0.2, 0.085), waves=pushing, enemies=[Seen("Darius", (0.3, 0.08), False, 12.0)])
    call = [x for x in macro.evaluate(c) if x.kind == "back_off"][0]
    assert call.text == "Recule vers ta tour : Darius a disparu."


def test_team_edge_counts_numbers_and_gold():
    g = game(1200.0, dead={"Ahri": 20.0, "Caitlyn": 20.0})
    c = ctx_for(g, me_uv=(0.5, 0.5), scoreboard=SimpleNamespace(team_gold_diff=3000))
    e, why = macro.team_edge(c)
    assert e > 0.5 and "5 contre 3" in why and any("or" in w for w in why)


def test_garbage_never_raises():
    assert macro.evaluate(macro.MacroCtx()) == []
    assert macro.build_ctx(0.0, 0.0, None, None, role=None, me_uv=None).me_alive is False
    assert macro.MacroPlanner().update(macro.MacroCtx(), "expert").new is None


# ------------------------------------------------------------------ planner policy
def _plates_ctx(t, gt=600.0, dead=20.0, **kw):
    return ctx_for(game(gt, dead={"Darius": dead} if dead else {}), me_uv=TOP_LANE_ME, t=t, **kw)


def test_planner_one_call_hold_and_cancel_when_invalid():
    pl = macro.MacroPlanner()
    up = pl.update(_plates_ctx(100.0), "debutant")
    assert up.new is not None and up.new.kind == "plates" and pl.active() is up.new
    assert pl.update(_plates_ctx(101.0), "debutant").new is None             # one call at a time
    # Darius respawned: invalid -> cancelled after the grace period
    assert pl.update(_plates_ctx(102.0, dead=None), "debutant").cancelled is None
    up = pl.update(_plates_ctx(104.5, dead=None), "debutant")
    assert up.cancelled is not None and up.cancel_reason == "invalide" and pl.active() is None


def test_planner_hold_blocks_weaker_replacement_and_fight_cancels():
    pl = macro.MacroPlanner()
    assert pl.update(_plates_ctx(100.0), "debutant").new is not None
    # a jungler-dead window appears 3 s later: the active call is held (>= 10 s)
    c = ctx_for(game(603.0, dead={"Darius": 17.0, "LeeSin": 30.0}), me_uv=TOP_LANE_ME, t=103.0)
    assert pl.update(c, "debutant").new is None
    # a fight starts: the plan is cancelled, nothing new during it
    c = _plates_ctx(105.0, in_fight=True)
    up = pl.update(c, "debutant")
    assert up.cancelled is not None and up.cancel_reason == "combat" and up.new is None
    assert pl.update(_plates_ctx(106.0, threat=1), "debutant").new is None    # gank threat: nothing


def test_planner_skill_levels():
    pushing = {"top": LaneWave("top", ally=5, enemy=2, meet=0.7, state="pushing")}
    mk = lambda t: ctx_for(game(400.0, gold=1400.0), me_uv=TOP_LANE_ME, waves=pushing, t=t,  # noqa: E731
                           enemies=[Seen("Darius", DARIUS_LANE, True, 0.0)])
    assert macro.MacroPlanner().update(mk(100.0), "debutant").new.kind == "wave_recall"
    assert macro.MacroPlanner().update(mk(100.0), "expert").new is None       # basic tier: not for experts
    # a high-value call reaches the expert
    c = ctx_for(game(1500.0, dead={"Ahri": 32.0, "Caitlyn": 30.0, "Nautilus": 28.0}), me_uv=(0.42, 0.58),
                objectives=[obj("baron", True)])
    assert macro.MacroPlanner().update(c, "expert").new.kind == "fight_won"


def test_planner_never_repeats_and_respects_gap():
    pl = macro.MacroPlanner()
    pushing = {"top": LaneWave("top", ally=5, enemy=2, meet=0.7, state="pushing")}
    mk = lambda t, base=False: ctx_for(game(300.0 + t, gold=1400.0), me_uv=(0.04, 0.96) if base else TOP_LANE_ME,  # noqa: E731
                                       waves=pushing, t=t, enemies=[Seen("Darius", DARIUS_LANE, True, 0.0)],
                                       in_base=base)
    assert pl.update(mk(100.0), "debutant").new is not None
    news = [pl.update(mk(100.0 + k), "debutant").new for k in range(1, 200)]
    assert not any(news)                           # recall call: once per trip (no base visit yet)
    pl.update(mk(300.0, base=True), "debutant")    # back in base
    assert pl.update(mk(400.0), "debutant").new is not None


def test_badge_only_for_strong_calls_not_repeated():
    pl = macro.MacroPlanner()
    c = ctx_for(game(1500.0, dead={"Ahri": 32.0, "Caitlyn": 30.0, "Nautilus": 28.0}), me_uv=(0.42, 0.58),
                objectives=[obj("baron", True)], t=10.0)
    first = pl.update(c, "debutant").new
    assert first.genius
    c2 = ctx_for(game(1600.0, dead={"Ahri": 32.0, "Caitlyn": 30.0, "Nautilus": 28.0}), me_uv=(0.42, 0.58),
                 objectives=[obj("baron", True)], t=120.0)
    second = pl.update(c2, "debutant").new
    assert second is not None and second.kind == "fight_won" and not second.genius   # badge: 4 min gap


def test_suppressed_coach_rules_and_director_overlap():
    from treeaicoach.alerts import Alert, AlertKind, Level
    from treeaicoach.tactics import TacticalDirector

    tac = TacticalDirector(SimpleNamespace(skill_level="debutant"))
    tac.macro.update(_plates_ctx(100.0), "debutant")
    assert "lane_dead" in tac.macro.suppressed_rules(110.0)
    alerts = [Alert(kind=AlertKind.MACRO_TIP, level=Level.INFO, text="Darius est mort : pousse.", key="macro_tip:lane_dead",
                    t=110.0),
              Alert(kind=AlertKind.MACRO_TIP, level=Level.INFO, text="Niveau 6", key="macro_tip:level6", t=110.0)]
    kept = tac.drop_overlaps(alerts, 110.0)
    assert [a.key for a in kept] == ["macro_tip:level6"]
    assert len(tac.drop_overlaps(alerts, 300.0)) == 2


def test_director_shows_arrow_and_banner():
    from treeaicoach.tactics import TacticalDirector, TickOut

    tac = TacticalDirector(SimpleNamespace(skill_level="debutant"))
    g = game(600.0, dead={"Darius": 20.0})
    st = phase.map_state(g, 600.0, "TOP")
    out = TickOut()
    tac._macro_tick(out, 100.0, 600.0, g, st, "TOP", TOP_LANE_ME, [], [], [], None, None, False, False)
    assert out.macro_new is not None and out.macro_new.kind == "plates"
    gl = [x for x in tac.guides(100.5) if x.kind == "genie"]
    assert gl and gl[0].uv == phase.TURRET_UV[("CHAOS", "top", 1)] and gl[0].arrow and gl[0].label == "VA ICI · TOUR"
    b = tac.banner(100.5)
    assert b is not None and b.title == "PLAQUES !" and "125 PO" in b.subtitle
    assert tac.macro_active() is out.macro_new


def test_ai_plan_format():
    c = macro.evaluate(ctx_for(game(600.0, dead={"Darius": 20.0}), me_uv=TOP_LANE_ME))[0]
    p = macro.ai_plan(c)
    assert p["plan"] == c.text and p["etapes"] == [c.why]
    assert macro.ai_plan(None) is None


# ------------------------------------------------------------------ whole game (coach_sim)
def test_simulated_game_macro_calls_per_level():
    from treeaicoach import coach_sim

    deb = coach_sim.run("debutant", minutes=16.0, hz=2.0)
    exp = coach_sim.run("expert", minutes=16.0, hz=2.0)
    n_deb, n_exp = len(deb.extras["genie"]), len(exp.extras["genie"])
    assert n_deb >= 4 and n_exp >= 1 and n_exp < n_deb, (n_deb, n_exp)
    assert deb.genie_rate() <= 1.2                      # beginners get the most, still not spam
    kinds_deb = {c.kind for _gt, c in deb.extras["genie"]}
    assert {"plates", "cross_trade"} <= kinds_deb and kinds_deb & {"wave_recall", "wave_freeze", "back_off"}
    assert all(c.tier == "high" for _gt, c in exp.extras["genie"])
    for _gt, c in deb.extras["genie"]:                  # every call: imperative line + one-line why + target
        assert c.text and c.why and len(c.text) <= 110 and len(c.why) <= 140 and "—" not in c.text + c.why
    # one call at a time, held >= 10 s: two starts are never closer than the hold
    starts = [gt for gt, _c in deb.extras["genie"]]
    assert all(b - a >= macro.HOLD_S - 0.6 for a, b in zip(starts, starts[1:]))
    assert deb.extras["badges"] and len(deb.extras["badges"]) <= len(deb.extras["genie"])


def test_offline_ai_plan_uses_the_active_macro_call():
    from treeaicoach import ai_advisor

    plan = ai_advisor.rule_plan("objective", {"genie": {"appel": "Prends le Héraut MAINTENANT : Lee Sin est en bas.",
                                                         "pourquoi": "Ils sont de l'autre côté."}})
    assert plan["plan"].startswith("Prends le Héraut") and plan["etapes"] == ["Ils sont de l'autre côté."]
    assert plan["urgence"] == "haute" and plan["objectif"] == "heraut"
    assert ai_advisor.rule_plan("objective", {}) is None or "genie" not in str(ai_advisor.rule_plan("objective", {}))


def test_situational_buys_boots_and_control_ward():
    from treeaicoach import itemization as iz

    def P(a, team="CHAOS", items=(), pos=""):
        return PlayerInfo(champion_alias=a, champion_name=a, team=team, items=list(items), level=9, position=pos)

    ap_team = [P("Lux"), P("Ahri"), P("Annie"), P("Syndra"), P("Nautilus")]
    me = P("Jinx", "ORDER", [1055], "BOTTOM")
    r = iz.recommend(GameInfo(game_time=500.0, me=me, enemies=ap_team, current_gold=700.0))
    assert iz.BOOTS in r.extras and "Bottes" in r.buy_text
    me = P("Garen", "ORDER", [1055, 1001, 3071], "TOP")
    r = iz.recommend(GameInfo(game_time=900.0, me=me, enemies=ap_team, current_gold=1400.0), objective_soon=True)
    assert 3111 in r.extras or iz.CONTROL_WARD in r.extras        # Mercury vs AP / CC, or a control ward
    me = P("Thresh", "ORDER", [3876, 1001], "UTILITY")
    r = iz.recommend(GameInfo(game_time=600.0, me=me, enemies=ap_team, current_gold=80.0), objective_soon=True)
    assert r.extras == (iz.CONTROL_WARD,) and "balise de contrôle" in r.buy_text
    me = P("Thresh", "ORDER", [3876, 1001, iz.CONTROL_WARD], "UTILITY")
    r = iz.recommend(GameInfo(game_time=600.0, me=me, enemies=ap_team, current_gold=80.0), objective_soon=True)
    assert iz.CONTROL_WARD not in r.extras                        # already one in the inventory
