"""Game phase awareness + end-game calls (phase.py) and positioning (positioning.py)."""

from dataclasses import dataclass

from treeaicoach import geometry, phase as ph
from treeaicoach.fight import Seen
from treeaicoach.live_client import GameInfo, PlayerInfo
from treeaicoach.positioning import PositionCoach, roles_for


def P(alias, team, pos="", dead=False, respawn=0.0, level=12):
    return PlayerInfo(riot_id=f"{alias}#1", summoner_name=alias, champion_alias=alias, champion_name=alias,
                      team=team, position=pos, is_dead=dead, respawn_timer=respawn, level=level)


ALLIES = [P("Vi", "ORDER", "JUNGLE"), P("Lux", "ORDER", "MIDDLE"), P("Jinx", "ORDER", "BOTTOM"),
          P("Thresh", "ORDER", "UTILITY")]


def enemies(dead=(), respawn=30.0):
    names = [("Darius", "TOP"), ("LeeSin", "JUNGLE"), ("Ahri", "MIDDLE"), ("Caitlyn", "BOTTOM"), ("Nautilus", "UTILITY")]
    return [P(a, "CHAOS", r, a in dead, respawn if a in dead else 0.0) for a, r in names]


def game(gt, events=(), dead=(), respawn=30.0, me_pos="TOP"):
    return GameInfo(game_time=gt, map_number=11, me=P("Garen", "ORDER", me_pos), allies=list(ALLIES),
                    enemies=enemies(dead, respawn), events=list(events))


@dataclass
class Obj:
    key: str
    alive: bool = False
    remaining: float | None = None
    name: str = ""


def test_parse_structures():
    assert ph.parse_turret("Turret_T2_L_03_A") == ("CHAOS", "top", 1)
    assert ph.parse_turret("Turret_T1_C_05_A") == ("ORDER", "mid", 1)
    assert ph.parse_turret("Turret_T1_R_01_A") == ("ORDER", "bot", 3)
    assert ph.parse_turret("garbage") is None
    assert ph.parse_inhib("Barracks_T2_L1") == ("CHAOS", "top")
    for (team, _lane, _tier), uv in ph.TURRET_UV.items():   # blue turrets in the blue half
        assert (uv[1] > uv[0]) == (team == "ORDER")


def test_phase_transitions():
    assert ph.map_state(game(300.0)).phase == "laning"
    tw = [{"EventName": "TurretKilled", "EventTime": 600.0, "TurretKilled": "Turret_T2_L_03_A", "KillerName": "Garen#1"},
          {"EventName": "TurretKilled", "EventTime": 650.0, "TurretKilled": "Turret_T1_R_03_A", "KillerName": "Caitlyn#1"}]
    st = ph.map_state(game(700.0, tw))
    assert st.phase == "mid" and ("CHAOS", "top", 1) in st.turrets_down
    assert ph.map_state(game(900.0)).phase == "mid"
    baron = [{"EventName": "BaronKill", "EventTime": 1250.0, "KillerName": "Vi#1"}]
    st = ph.map_state(game(1260.0, baron))
    assert st.phase == "late" and st.baron_team == "ORDER" and st.baron_left > 150
    inhib = baron + [{"EventName": "InhibKilled", "EventTime": 1300.0, "InhibKilled": "Barracks_T2_C1", "KillerName": "Lux#1"}]
    st = ph.map_state(game(1320.0, inhib))
    assert st.phase == "end" and ("CHAOS", "mid") in st.inhibs_down
    drag = [{"EventName": "DragonKill", "EventTime": 600.0 + 300 * i, "KillerName": "Caitlyn#1", "DragonType": "Fire"}
            for i in range(3)]
    assert ph.map_state(game(1300.0, drag)).soul_point() == "CHAOS"


def test_nearest_safe_turret_and_destroyed_ones():
    st = ph.map_state(game(900.0))
    me = (0.09, 0.30)                                   # top lane, blue side
    safe = st.nearest_safe_uv(me)
    assert safe == ph.TURRET_UV[("ORDER", "top", 1)]
    tw = [{"EventName": "TurretKilled", "EventTime": 800.0, "TurretKilled": "Turret_T1_L_03_A"}]
    st = ph.map_state(game(900.0, tw))
    assert st.nearest_safe_uv(me) == ph.TURRET_UV[("ORDER", "top", 2)]


def test_end_game_calls_ace_baron_finish_and_elder():
    c = ph.EndGameCaller()
    st = ph.map_state(game(1500.0, dead=("Darius", "LeeSin", "Ahri", "Caitlyn"), respawn=30.0))
    calls = c.update(10.0, st, [Obj("baron", alive=True)])
    assert calls and calls[0].key == "ace:baron" and calls[0].speak and calls[0].target is not None
    assert "Baron" in calls[0].text
    assert c.update(12.0, st, [Obj("baron", alive=True)]) == []          # cooled down
    inhib = [{"EventName": "InhibKilled", "EventTime": 1490.0, "InhibKilled": "Barracks_T2_C1"}]
    st = ph.map_state(game(1500.0, inhib, dead=("Darius", "LeeSin", "Ahri", "Caitlyn"), respawn=40.0))
    calls = ph.EndGameCaller().update(10.0, st, [])
    assert calls[0].key == "ace:end" and "finissez" in calls[0].text
    st = ph.map_state(game(2000.0))
    calls = ph.EndGameCaller().update(1.0, st, [Obj("elder", remaining=30.0)])
    assert calls and calls[0].key == "elder_soon" and "catch" not in calls[0].text and "Ancestral" in calls[0].text
    st = ph.map_state(game(1500.0, dead=("Caitlyn", "Ahri"), respawn=45.0))
    calls = ph.EndGameCaller().update(1.0, st, [])
    assert calls and calls[0].key == "carries_dead" and "45 s" in calls[0].text


def test_positioning_objective_setup_and_roles():
    pc = PositionCoach()
    st = ph.map_state(game(1000.0), my_role="BOTTOM")
    me = (0.10, 0.20)                                  # far top
    adv = pc.update(1.0, game(1000.0, me_pos="BOTTOM"), st, role="BOTTOM", me_pos=me,
                    objectives=[Obj("dragon", remaining=60.0)])
    assert adv is not None and adv.kind == "objective" and adv.text.startswith("Va en bas")
    assert adv.target == (geometry.DRAGON_PIT[0], geometry.DRAGON_PIT[1])
    # a top laner in laning phase is not asked to go to the dragon
    pc2 = PositionCoach()
    st2 = ph.map_state(game(400.0))
    assert pc2.update(1.0, game(400.0), st2, role="TOP", me_pos=(0.09, 0.3),
                      objectives=[Obj("dragon", remaining=60.0)]) is None
    assert "TOP" not in roles_for("dragon", "laning") and "TOP" in roles_for("baron", "mid")


def test_positioning_alone_in_side_lane_late():
    pc = PositionCoach()
    g = game(1700.0)
    st = ph.map_state(g)
    me = (0.12, 0.10)                                  # deep top lane (red half)
    hidden = [Seen(a, (0.6, 0.5), False, 20.0) for a in ("LeeSin", "Ahri", "Caitlyn", "Nautilus")]
    advs = [pc.update(t * 0.5, g, st, role="TOP", me_pos=me, allies=[Seen("Vi", (0.6, 0.6))],
                      enemies=hidden + [Seen("Darius", (0.3, 0.08), True)]) for t in range(10)]
    adv = next(a for a in advs if a is not None)
    assert adv.kind == "alone" and adv.speak and "seul en haut" in adv.text and "4 ennemis" in adv.text
    assert adv.target is not None
    assert pc.scores()["late"].alone_s > 0


def test_positioning_praise_when_present_for_our_dragon():
    pc = PositionCoach()
    pit = (geometry.DRAGON_PIT[0], geometry.DRAGON_PIT[1])
    g0 = game(1000.0, me_pos="BOTTOM")
    st = ph.map_state(g0)
    pc.update(0.0, g0, st, role="BOTTOM", me_pos=pit)
    ev = [{"EventID": 9, "EventName": "DragonKill", "EventTime": 1001.0, "KillerName": "Vi#1", "DragonType": "Fire"}]
    g1 = game(1002.0, ev, me_pos="BOTTOM")
    pc.update(2.0, g1, ph.map_state(g1), role="BOTTOM", me_pos=pit)
    pr = pc.pop_praise()
    assert pr and "dragon" in pr[0][1]
    sc = pc.scores()["mid"]
    assert sc.moments == 1 and sc.present == 1
