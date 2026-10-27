"""Kill streak bounties (treeaicoach/bounty.py) + their live_edge note + control-ward objective tips."""
from __future__ import annotations

from types import SimpleNamespace as NS

from treeaicoach import bounty as B
from treeaicoach import tips


def _p(alias, rid, team, dead=False):
    return NS(champion_alias=alias, champion_name=alias, riot_id=f"{rid}#EUW", summoner_name=rid, team=team,
              is_dead=dead)


def _kill(t, killer, victim):
    return {"EventName": "ChampionKill", "EventTime": t, "KillerName": killer, "VictimName": victim}


ME = _p("Garen", "Moi", "ORDER")
ALLY = _p("Lux", "Ami", "ORDER")
CAIT = _p("Caitlyn", "Cait", "CHAOS")
DAR = _p("Darius", "Dar", "CHAOS")


def _game(events, gold=0.0, me=ME):
    return NS(me=me, allies=[ALLY], enemies=[CAIT, DAR], events=events, current_gold=gold, game_time=900.0)


def test_streaks_reset_on_death_and_ignore_minions():
    evs = [_kill(100, "Cait", "Moi"), _kill(200, "Cait", "Ami"), _kill(300, "Minion", "Ami"),
           _kill(350, "Cait#EUW", "Moi"), _kill(400, "Moi", "Cait"), _kill(500, "Dar", "Moi")]
    st = B.streaks(evs, [ME, ALLY, CAIT, DAR])
    assert st["caitlyn"] == 0 and st["garen"] == 0 and st["darius"] == 1


def test_enemy_streak_call_and_me_target_call():
    evs = [_kill(100 + i, "Cait", v) for i, v in enumerate(("Moi", "Ami", "Moi"))]
    text, ident = B.bounty_call(_game(evs))
    assert text.startswith("Vise Caitlyn en premier") and "série de 3" in text and ident == "caitlyn:3"
    evs += [_kill(200 + i, "Cait", "Ami") for i in range(2)]
    assert B.bounty_call(_game(evs))[0].endswith("énorme prime (série de 5)")
    mine = [_kill(300 + i, "Moi", v) for i, v in enumerate(("Cait", "Dar", "Cait"))]
    text, ident = B.bounty_call(_game(mine, gold=1430.0))
    assert text == "Rentre dépenser tes 1 400 PO : ta tête vaut une prime (série de 3)" and ident == "me:3"
    assert B.bounty_call(_game(mine, gold=200.0))[0].startswith("Ne te bats pas seul")
    assert B.bounty_call(_game([])) is None and B.bounty_call(None) is None


def test_live_edge_emits_the_bounty_note_once():
    from treeaicoach.live_edge import LiveEdge

    evs = [_kill(100 + i, "Cait", v) for i, v in enumerate(("Moi", "Ami", "Moi"))]
    me = NS(**vars(ME), level=9, items=[], scores={}, position="TOP")
    g = NS(me=me, allies=[ALLY], enemies=[CAIT, DAR], events=evs, current_gold=0.0, game_time=900.0,
           game_mode="CLASSIC", champion_stats={})
    le = LiveEdge()
    le.update(0.0, g)
    notes = le.update(60.0, g)
    assert [n.feature for n in notes] == ["bounty"] and "Caitlyn" in notes[0].text
    assert le.update(300.0, g) == []                         # same streak: said once


def test_control_ward_objective_tips():
    by = {t.id: t for t in tips.TIPS}
    c = tips.TipContext(role="UTILITY", gt=600.0, soon={"dragon": 45.0}, items=frozenset({tips.CONTROL_WARD}))
    assert by["drag_vision_cw"].applies(c) and "45" in by["drag_vision_cw"].render(c)
    c = tips.TipContext(role="UTILITY", gt=600.0, soon={"dragon": 90.0}, in_base=True, gold=300.0)
    assert by["drag_buy_base"].applies(c) and not by["drag_vision_cw"].applies(c)
    c = tips.TipContext(role="TOP", gt=1150.0, soon={"baron": 50.0}, items=frozenset({tips.CONTROL_WARD}))
    assert by["baron_soon_cw"].applies(c)
