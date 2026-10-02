"""Tests of the "resources you forget" coach (treeaicoach/resources_coach.py) and its routing."""

from __future__ import annotations

from types import SimpleNamespace

from treeaicoach import presenter as prs
from treeaicoach.hud_abilities import BarRead, SlotState
from treeaicoach.live_client import parse_allgamedata
from treeaicoach.resources_coach import (
    ResourceInputs,
    ResourcesCoach,
    engine_tick,
    next_ability,
    unspent_points,
)


def _me(**kw):
    d = dict(champion_alias="Garen", level=6, is_dead=False, respawn_timer=0.0, items=[1055, 3340],
             item_counts={}, item_slots={}, spell_ids=("SummonerFlash", "SummonerHeal"), position="TOP")
    d.update(kw)
    return SimpleNamespace(**d)


def _game(me=None, abilities=None, hp=0.9, gold=500.0, gt=400.0):
    me = me or _me()
    return SimpleNamespace(me=me, game_time=gt, current_gold=gold, is_summoners_rift=True,
                           active_info={"abilities": abilities} if abilities is not None else {},
                           champion_stats={"currentHealth": 1000.0 * hp, "maxHealth": 1000.0}, my_team="ORDER")


def _bar(dead=False, valid=True, **states):
    slots = {}
    for k in ("Q", "W", "E", "R", "D", "F", "1", "2", "3", "4", "5", "6", "T"):
        st = states.get(k, "ready" if k in "QWERDF" else "off")
        if k == "T":
            slots[k] = SlotState(k, False, 0.0, True, 120.0, charges=st if isinstance(st, int) else None)
            continue
        cd = st == "cd"
        slots[k] = SlotState(k, cd, 0.5 if cd else 0.0, st == "ready" and not dead, 150.0)
    return BarRead(slots=slots, valid=valid)


def _run(coach, ticks, **kw):
    """Feed ticks ``(t, game, bar)``; returns the notes (shown at once)."""
    out = []
    for t, game, bar in ticks:
        n = coach.update(ResourceInputs(t=t, gt=float(game.game_time), game=game, bar=bar, **kw))
        if n is not None:
            coach.shown(n, t)
            out.append((t, n))
    return out


# ----------------------------------------------------------------------------- skill points
def test_unspent_points_and_next_ability():
    assert unspent_points(6, {"Q": 3, "W": 1, "E": 1, "R": 0}) == 1
    assert next_ability(6, {"Q": 3, "W": 1, "E": 1, "R": 0}) == "R"
    assert next_ability(11, {"Q": 5, "W": 2, "E": 2, "R": 1}) == "R"
    assert next_ability(3, {"Q": 1, "W": 0, "E": 1, "R": 0}) == "W"          # learn the missing one
    assert next_ability(8, {"Q": 3, "W": 1, "E": 2, "R": 1}) == "Q"          # max the main one (cap 4)
    assert next_ability(7, {"Q": 4, "W": 1, "E": 1, "R": 1}) == "E"          # Q capped at 4 at level 7
    assert unspent_points(9, {"Q": 0, "W": 0, "E": 0, "R": 0}, "Aphelios") == 0
    assert unspent_points(6, {}) == 0                                        # unknown: never a line
    assert next_ability(3, {"Q": 1, "W": 1, "E": 0, "R": 1}, "Jayce") == "E"  # R from level 1


def test_skill_point_after_grace_r_first_with_repeats():
    c = ResourcesCoach()
    ab = {"Q": 3, "W": 1, "E": 1, "R": 0}
    ticks = [(t * 0.5, _game(abilities=ab, gt=400 + t * 0.5), None) for t in range(0, 240)]
    notes = _run(c, ticks)
    assert notes and notes[0][1].text == "Monte ton R : tu es niveau 6"
    assert 3.0 <= notes[0][0] < 4.0                                          # 3 s grace
    assert notes[0][1].urgency >= 2                                          # may show in a fight
    assert len(notes) == 3 and notes[1][0] - notes[0][0] >= 45.0             # bounded reminders
    # spent: silent
    c2 = ResourcesCoach()
    assert not _run(c2, [(t, _game(abilities={"Q": 3, "W": 1, "E": 1, "R": 1}), None) for t in range(10)])


def test_skill_basic_only_for_beginners_and_azerty_label():
    ab = {"Q": 4, "W": 1, "E": 1, "R": 1}
    g = _game(me=_me(level=8), abilities=ab)
    n = _run(ResourcesCoach(), [(t, g, None) for t in range(6)], skill="debutant")
    assert n and n[0][1].text == "Monte ton E : tu as un point libre"      # Q capped at 4 -> E
    g8 = _game(me=_me(level=8), abilities={"Q": 3, "W": 2, "E": 1, "R": 1})
    n8 = _run(ResourcesCoach(), [(t, g8, None) for t in range(6)], skill="debutant")
    assert n8 and n8[0][1].text == "Monte ton A : tu as un point libre"    # AZERTY: Q is "A"
    assert not _run(ResourcesCoach(), [(t, g, None) for t in range(6)], skill="avance")


# ----------------------------------------------------------------------------- death lessons
def _death_ticks(bar_alive, bar_dead, cause=False, spells=("SummonerFlash", "SummonerHeal")):
    alive = _me(spell_ids=spells)
    dead = _me(spell_ids=spells, is_dead=True, respawn_timer=15.0)
    ticks = [(t * 0.5, _game(me=alive, abilities={"Q": 3, "W": 1, "E": 1, "R": 1}), bar_alive) for t in range(10)]
    ticks += [(5.0 + t * 0.5, _game(me=dead, abilities={"Q": 3, "W": 1, "E": 1, "R": 1}), bar_dead)
              for t in range(1, 24)]
    return ticks


def test_died_with_heal_up_gives_the_lesson():
    c = ResourcesCoach()
    notes = _run(c, _death_ticks(_bar(D="cd", F="ready"), _bar(dead=True, D="cd", F="up")))
    assert len(notes) == 1
    t, n = notes[0]
    assert n.text == "Utilise ton Soin avant de mourir : il était prêt" and n.kind == "death_cause"
    assert prs.message_kind("insight", n.key) == "death_cause" and t <= 7.5


def test_flash_first_used_spells_no_bar_and_cause_line_first():
    notes = _run(ResourcesCoach(), _death_ticks(_bar(), _bar(dead=True)))
    assert notes[0][1].text.startswith("Utilise ton Flash pour fuir")
    assert not _run(ResourcesCoach(), _death_ticks(_bar(D="cd", F="cd"), _bar(dead=True, D="cd", F="cd")))
    assert not _run(ResourcesCoach(), _death_ticks(None, None))              # no HUD: no guess
    # Heal used in the last second (the alive read says cooldown): no lesson
    assert not _run(ResourcesCoach(), _death_ticks(_bar(D="cd", F="cd"), _bar(dead=True, D="cd", F="up")))
    # the death-cause line is on the card: this lesson waits ~7 s
    notes = _run(ResourcesCoach(), _death_ticks(_bar(D="cd"), _bar(dead=True, D="cd")), cause_shown=True)
    assert notes and notes[0][0] >= 5.0 + 7.0


def test_item_active_lesson_needs_the_item_and_a_ready_frame():
    me_items = dict(items=[3157], item_slots={1: 3157}, spell_ids=("SummonerDot", "SummonerTeleport"))
    c = ResourcesCoach()
    ticks = [(t * 0.5, _game(me=_me(**me_items)), _bar(**{"2": "ready"})) for t in range(10)]
    ticks += [(5.0 + t * 0.5, _game(me=_me(is_dead=True, respawn_timer=20.0, **me_items)), _bar(dead=True))
              for t in range(1, 12)]
    notes = _run(c, ticks)
    assert notes and notes[0][1].text == "Utilise ton Sablier avant de mourir : il était prêt"


# ----------------------------------------------------------------------------- potion / gold / wards
def test_potion_low_hp_then_drink_resets():
    me = _me(items=[1055, 2003], item_counts={2003: 2})
    c = ResourcesCoach()
    notes = _run(c, [(t * 0.5, _game(me=me, hp=0.30), None) for t in range(12)])
    assert notes and notes[0][1].text == "Bois ta potion : 30 % de vie" and notes[0][0] >= 3.0
    assert not _run(ResourcesCoach(), [(t, _game(me=me, hp=0.30), None) for t in range(8)], in_base=True)
    assert not _run(ResourcesCoach(), [(t, _game(me=me, hp=0.30), None) for t in range(8)], alarm=True)
    assert not _run(ResourcesCoach(), [(t, _game(me=_me(), hp=0.30), None) for t in range(8)])  # no potion
    # a refillable potion counts only with charges left (HUD gold frame of its slot)
    refill = _me(items=[2031], item_counts={2031: 1}, item_slots={0: 2031})
    assert not _run(ResourcesCoach(), [(t * 0.5, _game(me=refill, hp=0.3), _bar(**{"1": "off"})) for t in range(12)])
    assert _run(ResourcesCoach(), [(t * 0.5, _game(me=refill, hp=0.3), _bar(**{"1": "ready"})) for t in range(12)])
    # health rising (a potion / heal running): no line
    rising = [(t * 0.5, _game(me=me, hp=0.20 + 0.01 * t), None) for t in range(18)]
    assert not _run(ResourcesCoach(), rising)


def test_gold_held_out_of_base_coordinated_with_recall_lines():
    c = ResourcesCoach()
    notes = _run(c, [(float(t), _game(gold=1720.0), None) for t in range(0, 70)])
    assert len(notes) == 1 and notes[0][0] >= 60.0
    assert notes[0][1].text == "Rentre dépenser : 1\xa0720 or"
    assert not _run(ResourcesCoach(), [(float(t), _game(gold=1720.0), None) for t in range(70)], recall_recent=True)
    assert not _run(ResourcesCoach(), [(float(t), _game(gold=1720.0), None) for t in range(70)], objective_soon=True)
    assert not _run(ResourcesCoach(), [(float(t), _game(gold=1720.0), None) for t in range(70)], fight=True)
    more = _run(c, [(70.0 + t, _game(gold=1750.0), None) for t in range(200)])
    assert not more                                                          # +300 gold needed to repeat


def test_control_ward_and_full_trinket_and_red_trinket():
    me = _me(items=[1055, 2055, 3340], item_counts={2055: 1, 3340: 1})
    notes = _run(ResourcesCoach(), [(float(t), _game(me=me), None) for t in range(0, 200)])
    assert notes and notes[0][1].text == "Pose ta balise de contrôle dans la rivière" and notes[0][0] >= 180.0
    tr = _run(ResourcesCoach(), [(float(t), _game(me=_me()), _bar(T=2)) for t in range(0, 60)], my_lane="top")
    assert tr and tr[0][1].text == "Pose ta balise dans la rivière : tu as 2 charges" and tr[0][0] >= 45.0
    assert not _run(ResourcesCoach(), [(float(t), _game(me=_me()), _bar(T=1)) for t in range(60)])
    assert not _run(ResourcesCoach(), [(float(t), _game(me=_me()), _bar(T=2)) for t in range(60)], skill="avance")
    assert not _run(ResourcesCoach(), [(float(t), _game(me=_me()), _bar(T=2)) for t in range(60)], ward_recent=True)
    sup = _me(items=[3869, 3340], position="UTILITY")
    red = _run(ResourcesCoach(), [(float(t), _game(me=sup), None) for t in range(5)], in_base=True)
    assert len(red) == 1 and red[0][1].text.startswith("Échange ta balise contre le Brouilleur")


def test_r_at_fight_start_and_teleport_to_remote_fight():
    g = _game(abilities={"Q": 3, "W": 1, "E": 1, "R": 1})
    c = ResourcesCoach()
    ticks = [(float(t), g, _bar()) for t in range(4)]
    n = _run(c, ticks[:2]) + _run(c, ticks[2:], fight=True, skill="debutant")
    assert n and "R" in n[0][1].text and n[0][1].urgency >= 2
    assert not _run(ResourcesCoach(), [(t, g, _bar(R="cd")) for t in (0.0, 1.0, 2.0)], fight=True)
    tp_me = _me(spell_ids=("SummonerTeleport", "SummonerFlash"))
    tp = _run(ResourcesCoach(), [(0.0, _game(me=tp_me), _bar())], remote_fight=("en bas", 3, 3), skill="debutant")
    assert tp and tp[0][1].text == "Téléporte-toi en bas : 3 contre 3"
    assert not _run(ResourcesCoach(), [(0.0, _game(me=tp_me), _bar(D="cd"))], remote_fight=("en bas", 3, 3))


# ----------------------------------------------------------------------------- routing / data
def test_presenter_routes_resource_lines():
    p = prs.Presenter()
    calm = prs.Context(t=0.0, skill="debutant")
    assert p.offer(prs.Message("resource", "Pose ta balise dans la rivière : tu as 2 charges"), calm).channel == prs.PANEL
    fight = prs.Context(t=10.0, fight=True, skill="debutant")
    assert p.offer(prs.Message("resource", "Monte ton R : tu es niveau 6", urgency=2), fight).channel == prs.PANEL
    assert p.filter_panel_line("Monte ton R : tu es niveau 6", "info", fight) == "Monte ton R : tu es niveau 6"
    assert p.filter_panel_line("Farme la vague", "info", fight) is None
    assert p.offer(prs.Message("resource", "Rentre dépenser : 1 700 or"), prs.Context(t=30.0, fight=True)).channel \
        == prs.DROP
    gank = prs.Context(t=40.0, gank=True)
    assert p.offer(prs.Message("resource", "Bois ta potion : 30 % de vie", urgency=2), gank).channel == prs.DROP
    assert prs.message_kind("insight", "res:skill:6:R") == "resource"
    assert prs.card_line("Bois ta potion : 30 % de vie") == "Bois ta potion : 30 % de vie"


def test_live_client_item_slots_and_counts():
    data = {"activePlayer": {"summonerName": "Moi", "currentGold": 10,
                             "abilities": {"Q": {"abilityLevel": 1}, "R": {"abilityLevel": 0}}},
            "allPlayers": [{"summonerName": "Moi", "team": "ORDER", "championName": "Garen", "rawChampionName":
                            "game_character_displayname_Garen", "level": 2,
                            "items": [{"itemID": 2003, "slot": 1, "count": 2}, {"itemID": 3340, "slot": 6, "count": 1}]}],
            "gameData": {"gameTime": 100.0, "mapNumber": 11}}
    g = parse_allgamedata(data)
    assert g.me.item_slots == {1: 2003, 6: 3340} and g.me.item_counts == {2003: 2, 3340: 1}
    assert g.active_info["abilities"]["Q"] == 1


def test_engine_tick_never_raises_on_a_bare_engine():
    eng = SimpleNamespace(_cfg=SimpleNamespace(skill_level="debutant"), _tactics=None, _tracker=None,
                          _presenter=prs.Presenter(), _presenter_ctx=lambda t: prs.Context(t=t, skill="debutant"),
                          _alarm_now=lambda t: False, _tip_rotator=None, _base_recent=lambda t: False,
                          _ward_recent=lambda t: False, _objective_line=lambda t: None, _text_msg=None,
                          _text_kind=None, text_messages=[], _window=None, _card_age=lambda t: None, _hud_shown=None)
    g = _game(abilities={"Q": 3, "W": 1, "E": 1, "R": 0})
    shown = []
    for t in range(8):
        shown.append(engine_tick(eng, float(t), g))
        if t >= 5 and eng._text_msg is not None:          # the overlay's card shows the line from t = 5
            eng._hud_shown = (eng._text_msg[1], float(t))
    assert eng._text_msg[1] == "Monte ton R : tu es niveau 6" and eng._text_kind == "resource"
    assert shown.count(None) == 7 and shown[6] is not None and shown[6].text == eng._text_msg[1]
    c = eng._res_glue.coach
    assert c.history and c.history[-1][1] == "Monte ton R : tu es niveau 6"     # confirmed only when on the card
    assert engine_tick(object(), 0.0, None) is None and engine_tick(object(), 0.0, g) is None
