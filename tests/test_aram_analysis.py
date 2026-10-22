"""ARAM analysis (record) and light live coaching (Live Client only)."""
from __future__ import annotations

from types import SimpleNamespace as NS

from treeaicoach import analysis, aram_analysis as aa, report


def _record() -> dict:
    def p(alias, team, me=False, rid=None):
        return {"alias": alias, "name": alias, "team": team, "is_me": me, "riot_id": rid or f"{alias}#EUW"}

    roster = [p("Garen", "ORDER", True, "Moi#EUW"), p("Lux", "ORDER"), p("Leona", "ORDER"), p("Jinx", "ORDER"), p("Ahri", "ORDER"),
              p("Soraka", "CHAOS"), p("Vladimir", "CHAOS"), p("Syndra", "CHAOS"), p("Veigar", "CHAOS"), p("Annie", "CHAOS")]

    def kill(t, killer, victim, assisters=()):
        return {"EventName": "ChampionKill", "EventTime": t, "KillerName": killer, "VictimName": victim,
                "Assisters": list(assisters)}

    ev = [kill(70, "Soraka", "Moi"), kill(100, "Annie", "Moi"), kill(300, "Syndra", "Lux"), kill(305, "Syndra", "Moi"),
          kill(400, "Garen", "Veigar"), kill(500, "Moi", "Annie"), kill(800, "Veigar", "Moi")]
    return {"meta": {"riot_id": "Moi#EUW", "team": "ORDER", "game_mode": "ARAM", "map_number": 12},
            "summary": {"kills": 1, "deaths": 4, "assists": 2, "duration": 900.0, "game_mode": "ARAM"},
            "duration": 900.0, "result": "Lose", "roster": roster, "events": ev,
            "snapshots": [{"game_time": 900.0, "items": [3071, 3047, 6333]}]}


def test_analyze_aram_record() -> None:
    z = aa.analyze(_record())
    assert z["available"] and z["kills"] == 1 and z["deaths_total"] == 4
    d = z["deaths"]
    assert d["early"] == 2 and d["first_in_fight"] == 0 and d["isolated"] >= 2
    assert z["kill_participation"] == 100          # 3 team kills? K+A=3 of team kills
    assert len(z["tips"]) == 3 and all(isinstance(t, str) for t in z["tips"])
    assert any(n["need"] == "antiheal" for n in z["item_needs"])      # Soraka + Vladimir heal, none owned
    assert z["comp_enemy"]["size"] == 5


def test_analyze_never_raises() -> None:
    assert aa.analyze(None)["available"] is False
    assert aa.analyze({"roster": "x", "events": [1, None]})["available"] in (True, False)


def test_analysis_and_report_use_it() -> None:
    rec = _record()
    a = analysis.analyze_game(rec)
    assert a["limited_mode"] == "ARAM" and a["aram"]["available"] and a["tips"] == a["aram"]["tips"]
    html = report.render_report_html(rec, a)
    assert "Analyse ARAM" in html and "Participation" in html


def _game(dead: bool, t_events=(), enemies=("Soraka", "Vladimir", "Syndra"), gold=500.0):
    me = NS(is_dead=dead, items=[3071, 3047, 6333], champion_alias="Garen", current_gold=gold)
    ally = lambda n: NS(riot_id=f"{n}#EUW", summoner_name=n)      # noqa: E731
    return NS(me=me, allies=[ally("A"), ally("B")], enemies=[NS(champion_alias=e) for e in enemies],
              events=list(t_events))


def test_live_coach_buy_on_death_rejoin_and_retreat() -> None:
    c = aa.AramCoach()
    assert c.update(30.0, _game(False)) is None                      # nothing in the first minute
    assert c.update(100.0, _game(False)) is None
    txt = c.update(110.0, _game(True))
    assert txt and txt.startswith("Tu es mort") and "se soigne" in txt
    assert "rejoins ton équipe" in (c.update(140.0, _game(False)) or "")
    ev = [{"EventName": "ChampionKill", "EventTime": 298.0, "VictimName": "A"},
          {"EventName": "ChampionKill", "EventTime": 301.0, "VictimName": "B"}]
    assert "recule" in (c.update(302.0, _game(False, ev)) or "")
    assert c.update(303.0, _game(False, ev)) is None                 # spaced out
    c.reset()
    assert c.update(500.0, NS(me=None)) is None
