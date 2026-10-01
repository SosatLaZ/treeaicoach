"""Coaching extras: power spikes, matchup card, session goal, death cause, CoachPlus, new tips, spam."""

from __future__ import annotations

from pathlib import Path

import pytest

from treeaicoach import coach, paths, tips
from treeaicoach.coach_plus import CoachPlus, buy_fields
from treeaicoach.death_cause import DeathCoach, DeathSnapshot, classify_death
from treeaicoach.game_plan import fit, map_fields, matchup_card, probable_gank_side
from treeaicoach.goals import GoalTracker, pick_goal
from treeaicoach.live_client import GameInfo, PlayerInfo
from treeaicoach.phase import MapState
from treeaicoach.spikes import SpikeTracker, item_spike, level_spike


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    paths._reset_cache()
    yield
    paths._reset_cache()


def pl(alias: str, team: str, pos: str, level: int = 1, items=(), rid: str | None = None, smite=False,
       dead=False, name=None) -> PlayerInfo:
    rid = rid or f"{alias}#T"
    return PlayerInfo(riot_id=rid, summoner_name=rid, champion_alias=alias, champion_name=name or alias,
                      team=team, position=pos, level=level, items=list(items), has_smite=smite, is_dead=dead)


def game(gt: float, my_lvl=1, opp_lvl=1, my_items=(), opp_items=(), events=(), dead=False, deaths=0,
         cs=0, hp=(500.0, 1000.0)) -> GameInfo:
    me = pl("Garen", "ORDER", "TOP", my_lvl, my_items, rid="Moi#EUW", dead=dead)
    me.scores = {"kills": 0, "deaths": deaths, "assists": 0, "creepScore": cs, "wardScore": 0}
    return GameInfo(game_time=gt, game_mode="CLASSIC", map_number=11, me=me,
                    allies=[pl("Vi", "ORDER", "JUNGLE", smite=True)],
                    enemies=[pl("Darius", "CHAOS", "TOP", opp_lvl, opp_items),
                             pl("LeeSin", "CHAOS", "JUNGLE", smite=True, name="Lee Sin"),
                             pl("Caitlyn", "CHAOS", "BOTTOM"), pl("Nautilus", "CHAOS", "UTILITY")],
                    events=[{"EventID": 0, "EventName": "GameStart", "EventTime": 0.0}] + list(events),
                    champion_stats={"currentHealth": hp[0], "maxHealth": hp[1]})


FACTS = {"my_role": "TOP", "opponents": [{"alias": "Darius", "name": "Darius", "level": 1}], "numbers": (0, 1)}


# ------------------------------------------------------------------ spikes
def test_level_and_item_spike_pure():
    assert level_spike(6, 5) == ("me", 6)
    assert level_spike(5, 6) == ("opp", 6)
    assert level_spike(3, 1) == ("me", 3)               # the highest key level reached alone
    assert level_spike(7, 7) is None and level_spike(4, 5) is None
    assert item_spike([3071], []) == ("me", 1)
    assert item_spike([1036], [3071, 3053]) == ("opp", 2)
    assert item_spike([1036], [1001]) is None             # components are not spikes


def test_spike_tracker_windows_and_factors():
    sp = SpikeTracker()
    assert sp.update(300, game(300, 5, 5), "Darius") is None
    s = sp.update(330, game(330, 5, 6), "Darius")
    assert s is not None and s.who == "opp" and s.level == 6
    assert sp.factors() == [(-1.5, "Darius est 6, pas toi")]
    assert sp.tip_fields()["spike_who"] == "opp"
    assert sp.update(360, game(360, 6, 6), "Darius") is None          # I caught up
    s = sp.update(500, game(500, 7, 6, my_items=[3071]), "Darius")    # first legendary
    assert s is not None and s.who == "me" and s.what == "item" and "Couperet noir" in s.reason
    assert sp.update(500 + 95, game(595, 7, 7, my_items=[3071]), "Darius") is None   # window over


def test_spike_level_window_expires():
    sp = SpikeTracker()
    sp.update(100, game(100, 1, 1), "Darius")
    assert sp.update(110, game(110, 2, 1), "Darius").who == "me"
    assert sp.update(150, game(150, 2, 1), "Darius") is None           # level 2 lead lasts ~35 s


# ------------------------------------------------------------------ game plan
def test_matchup_card_lane_and_jungle():
    card = matchup_card(game(20), "TOP", "Darius")
    assert card is not None and "DARIUS" in card.title
    assert any("plus fort tôt" in x for x in card.lines)                # Darius: early curve
    assert card.jungle and "Lee Sin" in card.jungle
    for line in card.lines + (card.jungle,):
        assert " : " in line and len(line.split()) <= 12
    g = game(20)
    g.me.position = "JUNGLE"
    jc = matchup_card(g, "JUNGLE", None)
    assert jc is not None and jc.title == "PLAN DE JUNGLE" and jc.lines[0].startswith("Premier gank")
    assert matchup_card(game(20), "TOP", "Inconnu") is None


def test_probable_gank_side_and_fit():
    g = game(20)
    assert probable_gank_side(g.enemies) in ("top", "bot", None)
    assert probable_gank_side([]) is None
    assert fit("a b c d e f g h i : k l (probable en bas)") == "a b c d e f g h i : k l"


def test_map_fields_power_plays():
    st = MapState(gt=1600, my_team="ORDER", dragons={"ORDER": 3}, baron_team="CHAOS", baron_left=120.0)
    f = map_fields(st)
    assert f["soul"] == "us" and f["enemy_baron_s"] == 120.0 and "baron_buff_s" not in f
    assert map_fields(None) == {}


# ------------------------------------------------------------------ goals
def test_pick_goal_from_history():
    hist = [{"deaths": 3, "cs": 150, "duration": 1800}] * 3
    g = pick_goal(hist, "TOP")                        # 5 cs/min, target 7 -> CS goal
    assert g.kind == "cs" and 5.5 <= g.target <= 6.0 and "moyenne" in g.why
    hist = [{"deaths": 8, "cs": 240, "duration": 1800}] * 3
    g = pick_goal(hist, "TOP")                        # 8 cs/min but 8 deaths
    assert g.kind == "deaths" and g.target == 7
    assert pick_goal([], "UTILITY").kind == "deaths"
    assert pick_goal([{"deaths": 9, "cs": 0, "duration": 300}] * 5, "TOP").why == ""   # short games ignored


def test_goal_tracker_show_risk_praise():
    gt_ = GoalTracker(lambda: [{"deaths": 6, "cs": 250, "duration": 1800}] * 3)
    notes = gt_.update(10, game(10), "TOP")
    assert notes and notes[0].title == "OBJECTIF DE LA PARTIE" and "morts maximum" in notes[0].text
    assert gt_.goal.kind == "deaths" and gt_.goal.target == 5
    assert gt_.update(20, game(20), "TOP") == []                         # shown once
    gt_.update(600, game(600, deaths=5), "TOP")
    assert gt_.tip_fields()["goal_risk"] == "last_death"
    ok = gt_.update(1500, game(1500, deaths=5), "TOP")
    assert ok and ok[0].kind == "praise" and gt_.status == "réussi"
    failed = GoalTracker(lambda: [{"deaths": 6, "cs": 250, "duration": 1800}] * 3)
    failed.update(10, game(10), "TOP")
    assert failed.update(1500, game(1500, deaths=7), "TOP") == [] and failed.status == "raté"


def test_goal_cs_check():
    gt_ = GoalTracker(lambda: [])
    gt_.update(10, game(10), "TOP")
    assert gt_.goal.kind == "cs"
    gt_.update(610, game(610, cs=40), "TOP")                            # ~3.9 cs/min at 10:10
    assert gt_.tip_fields()["goal_risk"] == "cs_behind"
    out = gt_.update(1201, game(1201, cs=140), "TOP")                   # 7 cs/min at 20:00
    assert out and out[0].title == "OBJECTIF ATTEINT"


# ------------------------------------------------------------------ death cause
def test_classify_death():
    assert classify_death(DeathSnapshot(killer_kind="turret"))[0] == "tower"
    c = classify_death(DeathSnapshot(enemies_near=3, allies_near=1))
    assert c[0] == "outnumbered" and "3 contre 1" in c[1]
    assert classify_death(DeathSnapshot(involved=1, jungler_involved=True, jungler_hidden_s=40))[0] == "jungler"
    assert classify_death(DeathSnapshot(involved=1, jungler_involved=True, jungler_hidden_s=3)) is None
    c = classify_death(DeathSnapshot(involved=1, killer_name="Darius", killer_level_diff=-2))
    assert c[0] == "outlevelled" and "Darius" in c[1]
    assert classify_death(DeathSnapshot(involved=1, hp=0.2))[0] == "low_hp"
    assert classify_death(DeathSnapshot(involved=1, enemy_half=True, missing=3))[0] == "overextended"
    assert classify_death(DeathSnapshot(involved=1)) is None             # nothing clear: no guess
    for snap in (DeathSnapshot(killer_kind="turret"), DeathSnapshot(enemies_near=2)):
        assert len(classify_death(snap)[1].split()) <= 12


def test_death_coach_once_per_death():
    dc = DeathCoach()
    facts = dict(FACTS, gt=0, numbers=(2, 1), jungler={"alias": "LeeSin", "visible": False, "hidden_s": 50})
    for gt in range(400, 412):
        assert dc.update(gt, dict(facts, gt=gt), game(gt)) is None
    kill = {"EventID": 5, "EventName": "ChampionKill", "EventTime": 412.0, "KillerName": "Darius#T",
            "VictimName": "Moi#EUW", "Assisters": ["LeeSin#T"]}
    assert dc.update(412, facts, game(412, events=[kill], dead=True)) is None    # delayed
    res = dc.update(417, facts, game(417, events=[kill], dead=True))
    # V2 audit: the unseen jungler in the kill is the precise cause (not just "2 contre 1")
    assert res is not None and res[0] == "jungler"
    assert dc.update(420, facts, game(420, events=[kill], dead=True)) is None    # once
    assert dc.causes == ["jungler"]


# ------------------------------------------------------------------ CoachPlus
def test_coach_plus_notes_gap_skill_and_busy():
    cp = CoachPlus(history_loader=lambda: [])
    out = cp.update(0.0, 10.0, game(10), FACTS, min_prio=1)
    assert [n.key for n in out] == ["goal:show"]
    assert cp.update(5.0, 20.0, game(20), FACTS, min_prio=1) == []            # card queued: 20 s gap
    assert cp.update(25.0, 40.0, game(40), FACTS, busy=True, min_prio=1) == []   # held during a fight
    out = cp.update(26.0, 41.0, game(41), FACTS, min_prio=1)
    assert out and out[0].key == "plan:card"
    tf = cp.tip_fields()
    assert tf.get("plan1") and tf.get("goal_kind") == "cs"
    expert = CoachPlus(history_loader=lambda: [])
    seen = []
    for i in range(20):
        seen += expert.update(i * 5.0, 10.0 + i * 5.0, game(10 + i * 5.0), FACTS, min_prio=4)
    assert seen == []                                                     # expert: no card / goal toast


def test_coach_plus_factors_feed_the_gauge():
    cp = CoachPlus(history_loader=lambda: [])
    cp.update(0.0, 300.0, game(300, 5, 5), FACTS)
    cp.update(1.0, 301.0, game(301, 6, 5), FACTS)
    f = cp.factors()
    assert f and f[0][0] > 0 and "6 avant Darius" in f[0][1]
    facts = dict(FACTS, gt=301.0)
    base = coach.stance_from_factors(coach.stance_factors(facts, game(301, 6, 5)))
    more = coach.stance_from_factors(coach.stance_factors(facts, game(301, 6, 5), extra=f))
    assert more.score > base.score
    assert cp.tip_fields()["spike_who"] == "me"


def test_buy_fields():
    class Rec:
        buy_now = (3044,)
        buy_now_names = ("Phage",)
        completes = False
        item_name = "Couperet noir"
    f = buy_fields(Rec())
    assert f["buy_names"] == "Phage" and f["buy_value"] >= 1000
    assert buy_fields(Rec(), in_base=True) == {} and buy_fields(None) == {}


# ------------------------------------------------------------------ tips
def _ctx(**kw) -> tips.TipContext:
    c = tips.TipContext(gt=kw.pop("gt", 400.0), role="TOP", lane="top", opp="Darius")
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _applies(tid: str, c: tips.TipContext) -> bool:
    return next(t for t in tips.TIPS if t.id == tid).applies(c)


def test_spike_and_objective_tips():
    c = _ctx(spike_who="me", spike_what="level", spike_level=6)
    assert _applies("spike_me_big", c)
    assert next(t for t in tips.TIPS if t.id == "spike_me_big").render(c) == "Attaque Darius : tu es 6 avant lui"
    assert _applies("spike_opp_big", _ctx(spike_who="opp", spike_what="level", spike_level=2))
    assert not _applies("spike_opp_big", _ctx(spike_who="opp", spike_what="level", spike_level=2, in_base=True))
    # recall timing for the objectives of MY role only
    c = _ctx(gt=1000.0, gold=1200.0, soon={"herald": 100.0})
    assert _applies("obj_recall_now", c)
    assert not _applies("obj_recall_now", _ctx(gt=1000.0, gold=1200.0, soon={"dragon": 100.0}))   # top: not dragon
    c = _ctx(gt=1000.0, gold=1500.0, soon={"herald": 30.0})
    assert _applies("obj_stay", c) and not _applies("gold_back", c)          # no "go back" right before it
    assert _applies("gold_back", _ctx(gt=1000.0, gold=1500.0))
    t = next(t for t in tips.TIPS if t.id == "obj_recall_now")
    assert "le Héraut" in t.render(_ctx(gt=1000.0, gold=1200.0, soon={"herald": 100.0}))


def test_power_play_goal_and_plan_tips():
    assert _applies("soul_us", _ctx(soul="us", soon={"dragon": 60.0}))
    assert _applies("baron_them", _ctx(enemy_baron_s=100.0))
    assert _applies("elder_us", _ctx(elder_buff_s=60.0))
    assert _applies("goal_deaths", _ctx(goal_risk="last_death"))
    assert _applies("goal_cs", _ctx(goal_risk="cs_behind", goal_target=6.5))
    assert _applies("plan_lane", _ctx(gt=60.0, plan1="Reste derrière tes sbires : il a plus de portée"))
    assert not _applies("jg_level3", _ctx(gt=170.0, plan_jg="Balise ta rivière avant 3:00 : X ganke tôt"))
    assert _applies("comp_ready", _ctx(buy_names="Phage", buy_value=1100))
    assert not _applies("comp_ready", _ctx(buy_names="Phage", buy_value=1100, enemies_near=1))
    c = tips.build_context({"gt": 500}, game(500), extra={"spike_who": "me", "nope": 1, "plan1": None})
    assert c.spike_who == "me" and not hasattr(c, "nope")


def test_tp_tip_needs_teleport():
    g = game(700)
    g.me.spell_ids = ("SummonerFlash", "SummonerDot")
    assert tips.build_context({"gt": 700}, g).has_tp is False
    g.me.spell_ids = ("SummonerFlash", "SummonerTeleport")
    assert tips.build_context({"gt": 700}, g).has_tp is True


def test_gauge_reason_stays_short():
    st = coach.stance_from_factors([(-3.0, "tu es à 30 % de vie"), (-2.0, "jungler ennemi vu en haut il y a 12 s")])
    assert st.reason == "tu es à 30 % de vie"
    assert coach.stance_from_factors([]).reason == coach.NEUTRAL_REASON


# ------------------------------------------------------------------ whole game: spam budget
def test_simulated_game_is_not_spammy():
    from treeaicoach import coach_sim

    res = coach_sim.run("intermediaire", minutes=12.0, hz=2.0)
    r = res.rates()
    assert r["voice/min"] <= 1.0 and r["toasts/min"] <= 4.0 and r["tips/min"] <= 9.0, r
    assert res.busiest_minute() <= 20
    titles = [t for _gt, t, _s in res.toasts]
    assert "OBJECTIF DE LA PARTIE" in titles and any(t.startswith("PLAN DE VOIE") for t in titles)
    assert not any("ASTUCE" == t for t in titles)                          # intermediate: no tip toasts
    assert not any("Probabilité" in x for _gt, x in res.voice)             # minimal voice: hype is written
    ex = res.extras
    assert ex.get("goal") and ex.get("plan") and ex.get("death_causes")   # 2 deaths in the first 12 min
