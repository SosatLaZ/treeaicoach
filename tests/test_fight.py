"""Fight detection + fight calculator scenarios (fight.py)."""

from treeaicoach import fight as fg
from treeaicoach.live_client import GameInfo, PlayerInfo


def P(alias, team, level=9, items=(), pos="", dead=False, respawn=0.0, name=None):
    return PlayerInfo(riot_id=f"{alias}#1", summoner_name=alias, champion_alias=alias, champion_name=name or alias,
                      team=team, position=pos, level=level, items=list(items), is_dead=dead, respawn_timer=respawn)


FULL = [3031, 3006]          # Infinity Edge + boots (~3800 gold)


def game(me_hp=1.0, allies=(), enemies=(), gt=900.0, events=()):
    me = P("Jinx", "ORDER", pos="BOTTOM")
    return GameInfo(game_time=gt, map_number=11, me=me, allies=list(allies), enemies=list(enemies),
                    events=list(events), champion_stats={"currentHealth": 1000 * me_hp, "maxHealth": 1000.0})


ME = (0.6, 0.85)


def run(tr, g, allies, enemies, t0=0.0, n=8, dt=0.25):
    up = None
    for i in range(n):
        up = tr.update(t0 + i * dt, g, ME, allies, enemies)
    return up


def test_2v2_even_is_balanced():
    al = [P("Thresh", "ORDER", pos="UTILITY")]
    en = [P("Ezreal", "CHAOS", pos="BOTTOM"), P("Leona", "CHAOS", pos="UTILITY")]
    g = game(allies=al, enemies=en)
    ev = fg.evaluate(g, ME, [fg.Seen("Thresh", (0.62, 0.86))],
                     [fg.Seen("Ezreal", (0.66, 0.82)), fg.Seen("Leona", (0.65, 0.84))])
    assert 0.40 < ev.win < 0.60
    assert (ev.allies_in, ev.enemies_in) == (2, 2)


def test_3v2_with_fed_enemy_is_not_a_free_win():
    al = [P("Thresh", "ORDER"), P("Vi", "ORDER")]
    fed = P("Ezreal", "CHAOS", level=13, items=[3031, 3508, 3006, 3036])
    en = [fed, P("Leona", "CHAOS", level=11)]
    g = game(allies=al, enemies=en)
    allies = [fg.Seen("Thresh", (0.62, 0.86)), fg.Seen("Vi", (0.58, 0.83))]
    enemies = [fg.Seen("Ezreal", (0.66, 0.82)), fg.Seen("Leona", (0.65, 0.84))]
    fed_ev = fg.evaluate(g, ME, allies, enemies)
    even = game(allies=al, enemies=[P("Ezreal", "CHAOS"), P("Leona", "CHAOS")])
    even_ev = fg.evaluate(even, ME, allies, enemies)
    assert even_ev.win > 0.75                       # plain 3v2 at equal levels: take it
    assert fed_ev.win < even_ev.win - 0.15          # the fed carry changes the picture


def test_low_hp_me_retreats_and_hysteresis():
    al = [P("Thresh", "ORDER")]
    en = [P("Ezreal", "CHAOS"), P("Leona", "CHAOS")]
    tr = fg.FightTracker()
    allies = [fg.Seen("Thresh", (0.62, 0.86))]
    enemies = [fg.Seen("Ezreal", (0.66, 0.82)), fg.Seen("Leona", (0.65, 0.84))]
    up = run(tr, game(me_hp=0.15, allies=al, enemies=en), allies, enemies)
    st = up.state
    assert st.active and st.call == "retreat" and st.banner.startswith("RECULE")
    assert st.safe_uv is not None
    # HP back: the decision cannot flip before CALL_GAP_S
    up = tr.update(2.1, game(me_hp=1.0, allies=al, enemies=en[:1]), ME, allies, enemies[:1])
    assert up.state.call == "retreat" and up.new_call is None


def test_enemy_jungler_missing_close_lowers_the_chance():
    al = [P("Thresh", "ORDER")]
    en = [P("Ezreal", "CHAOS"), P("Leona", "CHAOS"), P("LeeSin", "CHAOS", pos="JUNGLE")]
    g = game(allies=al, enemies=en)
    allies = [fg.Seen("Thresh", (0.62, 0.86))]
    base = [fg.Seen("Ezreal", (0.66, 0.82)), fg.Seen("Leona", (0.65, 0.84))]
    away = fg.evaluate(g, ME, allies, base + [fg.Seen("LeeSin", (0.2, 0.2), False, 4.0)])
    close = fg.evaluate(g, ME, allies, base + [fg.Seen("LeeSin", (0.62, 0.70), False, 4.0)])
    assert close.win < away.win - 0.05
    assert close.enemies_coming > 0 and away.enemies_coming == 0
    assert "de plus arrive" in close.reason


def test_fight_detection_engage_call_and_summary():
    al = [P("Thresh", "ORDER"), P("Vi", "ORDER")]
    en = [P("Ezreal", "CHAOS", level=7), P("Leona", "CHAOS", level=7)]
    tr = fg.FightTracker()
    allies = [fg.Seen("Thresh", (0.62, 0.86)), fg.Seen("Vi", (0.58, 0.83))]
    enemies = [fg.Seen("Ezreal", (0.66, 0.82)), fg.Seen("Leona", (0.65, 0.84))]
    g = game(allies=al, enemies=en)
    ups = [tr.update(i * 0.25, g, ME, allies, enemies) for i in range(6)]
    assert any(u.started for u in ups)
    calls = [u.new_call for u in ups if u.new_call]
    assert calls == ["engage"]                        # ONE call, not one per tick
    assert tr.state().banner.startswith("ATTAQUE")
    # fight over (enemies gone) with 2 kills for us -> one summary line
    kills = [{"EventName": "ChampionKill", "EventTime": 901.0, "KillerName": "Jinx#1", "VictimName": "Ezreal#1"},
             {"EventName": "ChampionKill", "EventTime": 902.0, "KillerName": "Vi#1", "VictimName": "Leona#1"}]
    g2 = game(allies=al, enemies=en, gt=905.0, events=kills)
    ends = [tr.update(2.0 + i * 0.5, g2, ME, allies, []) for i in range(12)]
    summ = [u.ended_summary for u in ends if u.ended_summary]
    assert summ == ["Combat gagné 2 à 0 !"]
    assert not tr.in_fight()


def test_lane_2v2_in_laning_is_not_a_fight_but_a_dive_is():
    tr = fg.FightTracker()
    al = [P("Thresh", "ORDER")]
    en = [P("Ezreal", "CHAOS"), P("Leona", "CHAOS")]
    g = game(allies=al, enemies=en, gt=300.0)
    from treeaicoach.phase import MapState
    st = MapState(gt=300.0, phase="laning", my_team="ORDER")
    allies = [fg.Seen("Thresh", (0.55, 0.88))]
    enemies = [fg.Seen("Ezreal", (0.70, 0.86)), fg.Seen("Leona", (0.71, 0.88))]
    ups = [tr.update(i * 0.25, g, ME, allies, enemies, map_state=st, lane_opponents=("Ezreal", "Leona"))
           for i in range(8)]
    assert not any(u.state.active for u in ups)          # 2v2 farming in lane, ~0.1 apart
    # alone against 2 diving enemies: that is a GANK (the gank alert speaks), not a fight
    dive = [fg.Seen("Ezreal", (0.63, 0.85)), fg.Seen("Leona", (0.60, 0.88))]
    ups = [tr.update(3 + i * 0.25, g, ME, [], dive, map_state=st, lane_opponents=("Ezreal", "Leona"))
           for i in range(4)]
    assert not ups[-1].state.active
    # ... with my support next to me, it is a fight (2v2 + their jungler arriving)
    en3 = en + [P("LeeSin", "CHAOS", pos="JUNGLE")]
    g3 = game(allies=al, enemies=en3, gt=300.0)
    dive3 = dive + [fg.Seen("LeeSin", (0.62, 0.80))]
    ups = [tr.update(5 + i * 0.25, g3, ME, [fg.Seen("Thresh", (0.61, 0.86))], dive3, map_state=st,
                     lane_opponents=("Ezreal", "Leona")) for i in range(4)]
    assert ups[-1].state.active and ups[-1].state.call == "retreat"


def test_win_chance_and_arrival():
    assert abs(fg.win_chance(1.0) - 0.5) < 1e-9
    assert fg.win_chance(1.5) > 0.75 and fg.win_chance(0.67) < 0.25
    assert fg.arrival_weight(0.2) > 0 and fg.arrival_weight(0.5) == 0
    assert fg.arrival_weight(0.3, 6.0) > 0 and fg.arrival_weight(0.3, 30.0) == 0
