"""V2 release audit: regression tests for every advice fix (scripted states, no rendering).

Each test names the wrong / risky call it guards against (see scratchpad v2_audit.md)."""
from __future__ import annotations

from types import SimpleNamespace

from treeaicoach import death_cause, geometry, macro, phase, tips
from treeaicoach import voice_policy as vp
from treeaicoach.alerts import Alert, AlertKind, Level
from treeaicoach.fight import Seen
from treeaicoach.hype import swing_phrase
from treeaicoach.live_client import GameInfo, PlayerInfo
from treeaicoach.waves import LaneWave

ROSTER = (("Garen", "ORDER", "TOP"), ("Vi", "ORDER", "JUNGLE"), ("Lux", "ORDER", "MIDDLE"),
          ("Jinx", "ORDER", "BOTTOM"), ("Thresh", "ORDER", "UTILITY"),
          ("Darius", "CHAOS", "TOP"), ("LeeSin", "CHAOS", "JUNGLE"), ("Ahri", "CHAOS", "MIDDLE"),
          ("Caitlyn", "CHAOS", "BOTTOM"), ("Nautilus", "CHAOS", "UTILITY"))
TOP_LANE_ME = (0.085, 0.30)


def game(gt=600.0, me="Garen", dead=None, gold=500.0, hp=1.0, events=(), items=None):
    dead = dict(dead or {})
    ps = {}
    for alias, team, pos in ROSTER:
        r = dead.get(alias)
        ps[alias] = PlayerInfo(riot_id=f"{alias}#X", summoner_name=f"{alias}#X", champion_alias=alias,
                               champion_name="Lee Sin" if alias == "LeeSin" else alias, team=team, position=pos,
                               is_dead=r is not None, respawn_timer=float(r or 0.0), level=11,
                               has_smite=pos == "JUNGLE", items=list((items or {}).get(alias, [])))
    my_team = ps[me].team
    return GameInfo(game_time=gt, game_mode="CLASSIC", map_number=11, me=ps[me],
                    allies=[p for a, p in ps.items() if p.team == my_team and a != me],
                    enemies=[p for p in ps.values() if p.team != my_team], events=list(events), current_gold=gold,
                    champion_stats={"currentHealth": 1000.0 * hp, "maxHealth": 1000.0})


def obj(key, alive=False, remaining=None):
    return SimpleNamespace(key=key, alive=alive, remaining=remaining)


def ctx_for(g, *, role=None, me_uv=None, enemies=(), allies=(), objectives=(), waves=None, t=100.0, **kw):
    st = phase.map_state(g, g.game_time, role or g.me.position)
    return macro.build_ctx(t, g.game_time, g, st, role=role or g.me.position, me_uv=me_uv, allies=allies,
                           enemies=enemies, objectives=objectives, waves=waves, **kw)


def kinds(ctx):
    return [c.kind for c in macro.evaluate(ctx)]


# ====================================================================== macro (COUPS DE GÉNIE)
def test_baron_not_called_without_a_real_window():
    mid = (0.42, 0.58)
    # 3 dead but they respawn in 12 s: a Baron started now is a throw
    c = ctx_for(game(1500.0, dead={"Ahri": 12.0, "Caitlyn": 13.0, "Nautilus": 14.0}), me_uv=mid,
                objectives=[obj("baron", True)])
    assert not any("Baron" in x.text for x in macro.evaluate(c) if x.kind == "fight_won")
    # 2 dead, their jungler alive: no Baron (smite steal, 5 v 3 on a long objective)
    c = ctx_for(game(1500.0, dead={"Ahri": 40.0, "Caitlyn": 40.0}), me_uv=mid, objectives=[obj("baron", True)])
    assert not any("Baron" in x.text for x in macro.evaluate(c) if x.kind == "fight_won")
    # 2 dead including their jungler, long timers: Baron
    c = ctx_for(game(1500.0, dead={"LeeSin": 40.0, "Caitlyn": 40.0}), me_uv=mid, objectives=[obj("baron", True)])
    fw = [x for x in macro.evaluate(c) if x.kind == "fight_won"]
    assert fw and "Baron" in fw[0].text
    # 3 dead but 2 of us dead too: not "5 contre 2", no Baron
    c = ctx_for(game(1500.0, dead={"Ahri": 40.0, "Caitlyn": 40.0, "Nautilus": 40.0, "Vi": 30.0, "Lux": 30.0}),
                me_uv=mid, objectives=[obj("baron", True)])
    assert not any("Baron" in x.text for x in macro.evaluate(c) if x.kind == "fight_won")


def test_jungler_dead_alone_never_baron_or_elder_and_laner_needs_his_jungler():
    g = game(1500.0, me="Jinx", dead={"LeeSin": 45.0})
    c = ctx_for(g, me_uv=(0.7, 0.9), objectives=[obj("elder", True), obj("baron", True)])
    assert not [x for x in macro.evaluate(c) if x.kind == "jungler_dead" and ("ancestral" in x.text or "Baron" in x.text)]
    # dragon for the bot lane... only with our jungler alive (no smite, slow kill)
    g = game(700.0, me="Jinx", dead={"LeeSin": 40.0, "Vi": 30.0})
    c = ctx_for(g, me_uv=(0.7, 0.9), objectives=[obj("dragon", True)])
    assert not [x for x in macro.evaluate(c) if x.kind == "jungler_dead" and "dragon" in x.text]
    g = game(700.0, me="Jinx", dead={"LeeSin": 40.0})
    c = ctx_for(g, me_uv=(0.7, 0.9), objectives=[obj("dragon", True)])
    assert [x for x in macro.evaluate(c) if x.kind == "jungler_dead" and "dragon" in x.text]


def test_jungler_dead_laner_call_is_push_not_plates_into_his_laner():
    g = game(400.0, dead={"LeeSin": 30.0})
    c = ctx_for(g, me_uv=TOP_LANE_ME, enemies=[Seen("Darius", (0.09, 0.22), True, 0.0)])
    call = [x for x in macro.evaluate(c) if x.kind == "jungler_dead"][0]
    assert "pousse ta vague" in call.text and "plaque" not in call.text.lower()


def test_plates_blocked_when_jungler_unseen_and_no_wave_at_their_tower():
    back = {"top": LaneWave("top", ally=1, enemy=5, meet=0.3, state="pushed_in")}
    c = ctx_for(game(600.0, dead={"Darius": 20.0}), me_uv=TOP_LANE_ME, waves=back,
                enemies=[Seen("LeeSin", (0.6, 0.6), False, 35.0)])
    assert "plates" not in kinds(c)
    pushed = {"top": LaneWave("top", ally=5, enemy=1, meet=0.7, state="pushing")}
    c = ctx_for(game(600.0, dead={"Darius": 20.0}), me_uv=TOP_LANE_ME, waves=pushed,
                enemies=[Seen("LeeSin", (0.6, 0.6), False, 35.0)])
    call = [x for x in macro.evaluate(c) if x.kind == "plates"][0]
    assert "invisible" in call.why                       # the risk is said, with the plate value


def test_support_gets_no_wave_recall_and_low_hp_never_push_first():
    pushing = {"bot": LaneWave("bot", ally=5, enemy=2, meet=0.7, state="pushing")}
    c = ctx_for(game(400.0, me="Thresh", gold=1500.0), me_uv=(0.7, 0.92), waves=pushing)
    assert "wave_recall" not in kinds(c)
    even = {"top": LaneWave("top", ally=3, enemy=3, meet=0.45, state="even")}
    c = ctx_for(game(400.0, gold=200.0, hp=0.2), me_uv=TOP_LANE_ME, waves=even,
                enemies=[Seen("Darius", (0.09, 0.22), True, 0.0)])
    assert not [x for x in macro.evaluate(c) if x.kind == "wave_recall" and x.text.startswith("Pousse")]


def test_side_wave_needs_enemies_on_the_opposite_half():
    waves = {"bot": LaneWave("bot", ally=1, enemy=6, meet=0.3, state="pushed_in")}
    g = game(1300.0, me="Lux", events=[{"EventName": "TurretKilled", "TurretKilled": "Turret_T1_R_03_A",
                                        "EventTime": 900.0, "KillerName": "Darius#X"}])
    mid_crowd = [Seen(a, (0.5, 0.5), True, 0.0) for a in ("Ahri", "Darius", "Caitlyn")]   # around mid, not top
    assert "side_wave" not in kinds(ctx_for(g, me_uv=(0.45, 0.55), enemies=mid_crowd, waves=waves))


def test_planner_gauge_safe_blocks_go_calls_but_not_post_fight():
    pl = macro.MacroPlanner()
    c = ctx_for(game(600.0, dead={"Darius": 20.0}), me_uv=TOP_LANE_ME, stance_score=-5.0)
    up = pl.update(c, "debutant")
    assert up.new is None or up.new.color != "safe"
    pl = macro.MacroPlanner()
    c = ctx_for(game(1500.0, dead={"Ahri": 40.0, "Caitlyn": 40.0, "Nautilus": 40.0}), me_uv=(0.42, 0.58),
                objectives=[obj("baron", True)], stance_score=-5.0)
    up = pl.update(c, "debutant")
    assert up.new is not None and up.new.kind == "fight_won"


def test_end_game_caller_respects_the_respawn_window():
    caller = phase.EndGameCaller()
    g = game(1700.0, dead={a: 16.0 for a in ("LeeSin", "Ahri", "Caitlyn", "Nautilus")})
    st = phase.map_state(g, 1700.0, "TOP")
    calls = caller.update(100.0, st, [obj("baron", True)])
    assert calls and calls[0].key != "ace:baron"           # 16 s: a tower, not a Baron
    caller = phase.EndGameCaller()
    g = game(1700.0, dead={a: 35.0 for a in ("LeeSin", "Ahri", "Caitlyn", "Nautilus")})
    st = phase.map_state(g, 1700.0, "TOP")
    calls = caller.update(100.0, st, [obj("baron", True)])
    assert calls and calls[0].key == "ace:baron"


# ====================================================================== written tips
def _tc(**kw):
    c = tips.TipContext(gt=kw.pop("gt", 1300.0), role=kw.pop("role", "TOP"), lane=kw.pop("lane", "top"))
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _applies(tip_id, c):
    return next(t for t in tips.TIPS if t.id == tip_id).applies(c)


def test_tip_baron_window_needs_timers_and_jungler_dead_tip_never_baron():
    c = _tc(alive=frozenset({"baron"}), dead_names=("Ahri", "Jinx"), dead_respawn=20.0)
    assert not _applies("baron_window", c)
    c = _tc(alive=frozenset({"baron"}), dead_names=("Ahri", "Jinx"), dead_respawn=45.0)
    assert _applies("baron_window", c)
    c = _tc(alive=frozenset({"baron"}), jg_dead=True, dead_names=("Lee Sin",), jg="Lee Sin")
    assert c.jg_objective() is None and not _applies("jg_dead_window", c)
    c = _tc(role="BOTTOM", lane="bot", alive=frozenset({"dragon"}), jg_dead=True, dead_names=("Lee Sin",), jg="Lee Sin")
    assert _applies("jg_dead_window", c)
    assert next(t for t in tips.TIPS if t.id == "jg_dead_window").render(c) == "Prenez le dragon maintenant : Lee Sin est mort"


def test_tip_side_lane_and_split_honest():
    # a top laner farming top at 15:00 with 2 enemies unseen is doing his job
    assert not _applies("side_late", _tc(gt=900.0, my_lane="top", missing=2))
    assert _applies("side_late", _tc(gt=1300.0, my_lane="top", missing=3))
    # "3 ennemis visibles ailleurs" was claimed without checking: now only with their jungler seen / dead
    assert not _applies("split_top", _tc(gt=1300.0, my_lane="top", missing=1))
    assert _applies("split_top", _tc(gt=1300.0, my_lane="top", missing=0, jg_visible=True))


def test_tip_objective_wording_and_role():
    c = _tc(gt=1300.0, role="BOTTOM", lane="bot", soon={"herald": 0.5, "dragon": 40.0})
    assert not _applies("group_obj", _tc(gt=1300.0, role="BOTTOM", lane="bot", soon={"herald": 0.5}))
    assert next(t for t in tips.TIPS if t.id == "group_obj").render(c) == "Rejoins ton équipe vers le dragon : 40 s"
    assert tips.OBJ_LE["atakhan"] == "Atakhan"              # never "l'Atakhan"
    assert not _applies("tp_obj", _tc(gt=1300.0, soon={"herald": 40.0}))   # TP is for the other side


def test_tip_consistency_with_macro_call_and_recall():
    rot = tips.TipRotator(seed=1)
    c = _tc(gt=500.0, level_diff=-2, opp="Darius", macro_tone="go")
    shown = rot.update(0.0, c) or ""
    assert "prudemment" not in shown                        # no "joue prudemment" next to a "go" call
    c = _tc(gt=900.0, gold=1500.0, recall_said=True)
    assert not _applies("gold_back", c)


# ====================================================================== voice whitelist
def A(kind, key, text="x", level=Level.INFO):
    return Alert(kind=kind, level=level, text=text, key=key, t=0.0)


def test_voice_whitelist_same_at_every_level():
    for level in ("minimal", "normal"):
        assert vp.route(A(AlertKind.JUNGLER_APPROACH, "g", level=Level.DANGER), level) == "voice"
        assert vp.route(A(AlertKind.MACRO_TIP, "call:retreat"), level) == "voice"
        for key, kind in (("stance:prudent", AlertKind.MACRO_TIP), ("macro_tip:lane_dead", AlertKind.MACRO_TIP),
                          ("solo:1", AlertKind.PRAISE), ("death_recap", AlertKind.DEATH_RECAP),
                          ("jungler_spotted:LeeSin", AlertKind.JUNGLER_SPOTTED), ("hype:swing:1", AlertKind.PRAISE),
                          ("urgent:pos:alone", AlertKind.MACRO_TIP), ("urgent:elder_soon", AlertKind.MACRO_TIP)):
            assert vp.route(A(kind, key), level) == "text", (level, key)
    assert vp.route(A(AlertKind.MACRO_TIP, "urgent:genie:fight_won:1"), "normal") == "voice"
    assert vp.route(A(AlertKind.MACRO_TIP, "urgent:genie:fight_won:1"), "minimal") == "text"


def test_gate_objective_only_when_involved_and_never_in_fight():
    gate = vp.VoiceGate()
    drag = A(AlertKind.OBJECTIVE_SOON, "objective_soon:dragon:20", "Dragon dans 20 secondes.")
    top_far = vp.SpeechContext(role="TOP", me_uv=(0.1, 0.2), gt=500.0)
    assert gate.decide(drag, 0.0, top_far) == "text"
    bot = vp.SpeechContext(role="BOTTOM", me_uv=(0.8, 0.9), gt=500.0)
    assert gate.decide(drag, 0.0, bot) == "voice"
    near = vp.SpeechContext(role="TOP", me_uv=(geometry.DRAGON_PIT[0], geometry.DRAGON_PIT[1]), gt=500.0)
    assert gate.decide(drag, 0.0, near) == "voice"
    fight = vp.SpeechContext(role="BOTTOM", in_fight=True)
    assert gate.decide(drag, 0.0, fight) == "text"
    assert gate.decide(A(AlertKind.MACRO_TIP, "call:retreat", "Recule !"), 0.0, fight) == "voice"
    assert gate.decide(A(AlertKind.MACRO_TIP, "call:engage", "Attaque !"), 0.0, fight) == "drop"   # banner only
    # voice duplicates of the HUD / banner are dropped, never written twice
    assert gate.decide(A(AlertKind.MACRO_TIP, "stance:prudent", "Joue prudent"), 0.0, bot) == "drop"
    assert gate.decide(A(AlertKind.MACRO_TIP, "urgent:genie:fight_won:1", "Ils sont 3 morts : Baron !"),
                       0.0, bot, "minimal") == "drop"
    assert gate.decide(A(AlertKind.MACRO_TIP, "urgent:genie:fight_won:1", "Ils sont 3 morts : Baron !"),
                       0.0, bot, "normal") == "voice"


def test_toast_topics_dedupe_across_systems():
    assert vp.topic_of("text:macro_tip:lane_dead") == vp.topic_of("tip:opp_dead") == vp.topic_of("genie:plates")
    assert vp.topic_of("text:recall_gold") == vp.topic_of("tip:gold_back") == "recall"
    assert vp.topic_of("text:objective_soon:dragon:60") == vp.topic_of("tip:drag_prio") == "objective"
    assert vp.topic_of("praise:solo") is None and vp.topic_of("") is None


# ====================================================================== death cause / play ratings
def test_death_cause_does_not_blame_a_dive_and_prefers_the_jungler():
    s = death_cause.DeathSnapshot(enemies_near=4, involved=4, allies_near=1, enemy_half=False)
    cause = death_cause.classify_death(s)
    assert cause is not None and cause[0] == "dive" and "rien à faire" in cause[1]
    s = death_cause.DeathSnapshot(involved=2, allies_near=1, jungler_involved=True, jungler_hidden_s=40.0)
    assert death_cause.classify_death(s)[0] == "jungler"
    # 30 % HP at the end of a fight, full HP 10 s earlier: not "rentre plus tôt"
    s = death_cause.DeathSnapshot(involved=1, hp=0.3, hp_early=0.9)
    assert (death_cause.classify_death(s) or ("", ""))[0] != "low_hp"
    s = death_cause.DeathSnapshot(involved=1, hp=0.3, hp_early=0.3)
    assert death_cause.classify_death(s)[0] == "low_hp"


class _Cfg:
    skill_level = "debutant"
    plays_enabled = True


def _plays_game(gt, dead=False, events=(), gold=500.0, items=()):
    g = game(gt, gold=gold, events=events, items={"Garen": list(items)})
    g.me.is_dead = dead
    g.fetched_at = gt
    return g


def _death_rating(*, involved=("Darius#X",), warn_at=None, gold=500.0, items=(), streak=0, traded=0):
    from treeaicoach.plays import PlayClassifier, PlayContext

    pc = PlayClassifier(_Cfg())
    gt0 = 600.0
    events: list[dict] = []
    eid = [0]

    def ev(**kw):
        eid[0] += 1
        events.append(dict(kw, EventID=eid[0]))
    pc.update(PlayContext(t=0.0, gt=gt0, game=_plays_game(gt0, events=list(events))))     # baseline
    for i in range(streak):
        ev(EventName="ChampionKill", EventTime=gt0 - 120 + i, KillerName="Garen#X", VictimName="Ahri#X", Assisters=[])
    for i in range(traded):
        ev(EventName="ChampionKill", EventTime=gt0 + 8, KillerName="Lux#X", VictimName=("Caitlyn#X", "Nautilus#X")[i],
           Assisters=[])
    for k in range(1, 10):
        threat = 1 if warn_at is not None and k >= warn_at else 0
        pc.update(PlayContext(t=float(k), gt=gt0 + k, game=_plays_game(gt0 + k, events=list(events), gold=gold,
                                                                         items=items), threat=threat))
    ev(EventName="ChampionKill", EventTime=gt0 + 10, KillerName=involved[0], VictimName="Garen#X",
       Assisters=list(involved[1:]))
    pc.update(PlayContext(t=10.0, gt=gt0 + 10, game=_plays_game(gt0 + 10, dead=True, events=list(events), gold=gold,
                                                                  items=items)))
    return [(p.cls, p.rule) for p in pc.history() if p.rule.startswith(("death", "shutdown", "facecheck"))]


def test_plays_no_gaffe_for_things_out_of_the_players_hands():
    # a 4-man dive: no rating at all
    assert _death_rating(involved=("Darius#X", "LeeSin#X", "Ahri#X", "Caitlyn#X")) == []
    # the gank warning came 1 s before the death: no time to react, not "GAFFE"
    assert ("blunder", "death_after_warning") not in _death_rating(warn_at=9)
    # warned 6 s before: that one is on the player
    assert ("blunder", "death_after_warning") in _death_rating(warn_at=4)
    # full build: gold piles up, not a mistake
    full = (3071, 3053, 3742, 6333, 3065, 3047)
    assert not [r for r in _death_rating(gold=2600.0, items=full) if r[1] == "death_gold"]
    assert ("blunder", "death_gold") in _death_rating(gold=2600.0)
    # a shutdown given while the team traded 2 kills is not a blunder
    assert ("blunder", "shutdown_given") in _death_rating(streak=3)
    assert ("inaccuracy", "shutdown_traded") in _death_rating(streak=3, traded=2)


# ====================================================================== wording
def test_win_probability_line_follows_the_level():
    assert "L'avantage est pour nous" not in swing_phrase(0.50, 0.30, "sobre")
    assert "Partie serrée" in swing_phrase(0.50, 0.30, "sobre")
    assert "L'avantage est pour nous" in swing_phrase(0.62, 0.40, "sobre")
    assert "prudent" in swing_phrase(0.38, 0.60, "sobre")


def test_praise_grubs_agreement():
    from treeaicoach.praise import PraiseCoach

    pc = PraiseCoach()
    g = game(500.0)
    pc.update(0.0, g)
    ev = {"EventName": "HordeKill", "EventID": 7, "EventTime": 499.0, "KillerName": "Garen#X", "Assisters": []}
    out = pc.update(1.0, game(501.0, events=[ev]))
    texts = " ".join(p.text for p in out)
    assert out and " pris," not in texts and " sécurisé," not in texts
    import random
    from treeaicoach import praise as pr_mod
    for seed in range(6):                          # every phrase variant agrees with "Larves"
        random.seed(seed)
        pc = pr_mod.PraiseCoach()
        pc.update(0.0, g)
        txt = " ".join(p.text for p in pc.update(1.0, game(501.0, events=[ev])))
        assert " pris," not in txt and " sécurisé," not in txt


# ====================================================================== engine: topics, consistency, new game
class _Voice:
    backend = "fake"

    def __init__(self):
        self.said = []

    def say(self, text, level=1):
        self.said.append(text)

    def start(self):
        pass

    def stop(self):
        pass

    def set_muted(self, on):
        pass


def _engine():
    from treeaicoach.config import Config
    from treeaicoach.engine import CoachEngine

    class Src:
        def next(self, t):
            return None, game(600.0)
    return CoachEngine(Config(), _Voice(), frame_source=Src(), clock=lambda: 0.0, enable_hotkeys=False,
                       manage_overlay=False)


def test_engine_one_toast_per_topic_and_reset_on_new_game():
    eng = _engine()
    try:
        assert eng._topic_seen("text:macro_tip:lane_dead", 100.0) is False
        assert eng._topic_seen("tip:opp_dead", 103.0) is True             # same subject, other system
        assert eng._topic_seen("tip:opp_dead", 150.0) is False            # later: a new moment
        assert eng._topic_seen("praise:solo", 151.0) is False             # no topic: never deduped
        eng.macro_calls = [(1.0, "x")]
        eng._recall_topic_t = 99.0
        eng._start_game(game(10.0), 200.0)
        assert eng.macro_calls == [] and eng._recall_topic_t is None and eng._topic_t == {}
    finally:
        eng.stop()


def test_engine_recall_said_once_and_macro_factor_in_gauge():
    eng = _engine()
    try:
        eng.step(0.0)
        r = Alert(kind=AlertKind.RECALL_GOLD, level=Level.INFO, text="Tu as 1300 pièces d'or, pense à rentrer.",
                  key="recall_gold", t=1.0)
        assert eng._recall_consistency([r], 1.0) == [r]
        assert eng._recall_consistency([r], 30.0) == []                  # already said this trip
        assert eng._tip_consistency_fields(31.0)["recall_said"] is True
        # an active "go" macro call pushes the gauge the same way
        call = macro.GeniusCall("plates", "p", "PLAQUES !", "Plaque la tour", "why", color="safe")
        eng._tactics.macro._active = call
        assert eng._macro_factors() == [(2.0, "appel : plaques")]
        assert eng._tip_consistency_fields(32.0)["macro_tone"] == "go"
    finally:
        eng.stop()
