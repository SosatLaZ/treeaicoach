"""Post-game replay coach (replay_coach.py): the 3 decisive moments, report section, Discord recap line."""

from __future__ import annotations

import json
from pathlib import Path

from treeaicoach import postgame_edge as pe
from treeaicoach import replay_coach as rc
from treeaicoach import report

FIX = Path(__file__).parent / "fixtures" / "lcu_truth"


def _truth() -> dict:
    """Me = 1 (Garen top, ORDER). 6:40 we win a 2v0 fight with the dragon up and take nothing;
    14:20 I die alone in the bot river and they take the dragon 35 s later; 25:00 we lose 1 v 3
    and they take the Baron."""
    parts = [{"id": i, "team": "ORDER" if i <= 5 else "CHAOS", "alias": a, "name": a,
              "position": ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")[(i - 1) % 5]}
             for i, a in enumerate(("Garen", "Vi", "Lux", "Jinx", "Thresh", "Darius", "LeeSin", "Ahri", "Caitlyn",
                                    "Nautilus"), start=1)]
    frames = []
    for m in range(0, 31):
        row = {}
        for p in parts:
            u, v = ((0.2, 0.8) if p["team"] == "ORDER" else (0.8, 0.2))
            if p["id"] == 1 and m == 14:
                u, v = 0.7, 0.7          # me, alone in the bot river
            row[str(p["id"])] = [u, v, 500 + 400 * m, 0, 6 * m, min(18, 1 + m // 2)]
        frames.append([60.0 * m, row])
    kills = [[400.0, 1, 7, [2], 0.5, 0.5], [405.0, 2, 8, [1], 0.5, 0.5],
             [860.0, 7, 1, [9], 0.7, 0.7],
             [1500.0, 6, 2, [7], 0.4, 0.3], [1503.0, 7, 3, [8], 0.4, 0.3], [1506.0, 8, 4, [6], 0.4, 0.3],
             [1508.0, 1, 9, [], 0.4, 0.3]]
    monsters = [[895.0, 7, "CHAOS", "DRAGON", "FIRE_DRAGON", 0.67, 0.7],
                [1560.0, 7, "CHAOS", "BARON_NASHOR", "", 0.33, 0.3]]
    return {"participants": parts, "frames": frames, "kills": kills, "monsters": monsters, "buildings": [],
            "me": 1, "my_team": "ORDER", "duration": 1800.0}


def test_three_moments_from_the_client_timeline() -> None:
    ms = rc.decisive_moments({}, _truth())
    assert [m["kind"] for m in ms] == ["missed", "death_obj", "death_obj"]
    assert [m["time"] for m in ms] == ["6:40", "14:20", "25:00"]
    assert ms[0]["what"] == "Combat gagné 2 à 0 mais rien pris derrière (le dragon était là)"
    assert "ils n'étaient que 3 en vie" in ms[0]["instead"]
    assert ms[1]["what"].startswith("Tu meurs seul (") and "le dragon 35 s après" in ms[1]["what"]
    assert ms[1]["instead"] == "À l'approche du dragon, ne reste pas seul : recule vers ton équipe"
    assert "3 morts chez vous" in ms[2]["what"] and "le Baron" in ms[2]["what"]
    assert ms[1]["after"] < ms[1]["before"] and ms[2]["swing"] < 0
    for m in ms:
        assert m["line"].startswith(m["time"] + " · ") and " À faire : " in m["line"] and "\n" not in m["line"]
    assert len(rc.decisive_moments({}, _truth(), limit=1)) == 1


def test_record_events_fallback_and_recap_line() -> None:
    rec = {"meta": {"team": "ORDER", "champion_name": "Garen"},
           "roster": [{"alias": "Garen", "name": "Garen", "team": "ORDER", "riot_id": "Moi#EUW", "is_me": True},
                      {"alias": "Vi", "name": "Vi", "team": "ORDER", "riot_id": "Ami#EUW"},
                      {"alias": "LeeSin", "name": "Lee Sin", "team": "CHAOS", "riot_id": "Lee#EUW"},
                      {"alias": "Ahri", "name": "Ahri", "team": "CHAOS", "riot_id": "Ahri#EUW"}],
           "events": [{"EventName": "ChampionKill", "EventTime": 860.0, "KillerName": "Lee", "VictimName": "Moi"},
                      {"EventName": "ChampionKill", "EventTime": 870.0, "KillerName": "Ahri", "VictimName": "Ami"},
                      {"EventName": "DragonKill", "EventTime": 900.0, "KillerName": "Lee", "DragonType": "Fire"}]}
    ms = rc.decisive_moments(rec)
    assert len(ms) == 1 and ms[0]["kind"] == "death_obj" and ms[0]["time"] == "14:20"
    assert ms[0]["what"] == "Tu meurs, 2 morts chez vous, et l'ennemi prend le dragon 40 s après"
    line = rc.recap_line(ms, "Garen")
    assert line.startswith("Moment décisif 14:20 : Garen meurt") and "Tu " not in line
    assert rc.recap_line([]) == ""


def test_garbage_never_raises() -> None:
    assert rc.decisive_moments(None) == [] and rc.decisive_moments({}, {}) == []
    assert rc.decisive_moments({"events": [{"EventName": "ChampionKill"}]}, {"frames": [[0, None]]}) == []


def test_real_truth_fixtures_give_at_most_three_spaced_moments() -> None:
    n = 0
    for f in sorted(FIX.glob("*.truth.json")):
        ms = rc.decisive_moments({}, json.loads(f.read_text(encoding="utf-8")))
        assert len(ms) <= 3
        assert all(b["t"] - a["t"] >= rc.MIN_GAP_S for a, b in zip(ms, ms[1:]))
        assert all(len(m["line"]) <= 220 for m in ms)
        n += len(ms)
    assert n >= 6


def test_report_section_and_team_recap_line() -> None:
    ms = rc.decisive_moments({}, _truth())
    html = report._replay_section({}, {"replay": ms, "replay_source": "client"})
    assert "Les 3 moments décisifs" in html and "14:20" in html and "À faire : " in html
    assert "positions et événements du client" in html
    assert report._replay_section({}, {"replay": []}) == ""
    rec = {"meta": {"team": "ORDER", "champion_name": "Garen"}, "result": "Lose", "duration": 1500.0,
           "roster": [{"alias": "Garen", "name": "Garen", "team": "ORDER", "riot_id": "Moi#EUW", "is_me": True},
                      {"alias": "Jinx", "name": "Jinx", "team": "ORDER", "riot_id": "Ami#EUW"},
                      {"alias": "LeeSin", "name": "Lee Sin", "team": "CHAOS", "riot_id": "Lee#EUW"}],
           "events": [{"EventName": "ChampionKill", "EventTime": 860.0, "KillerName": "Lee", "VictimName": "Moi"},
                      {"EventName": "DragonKill", "EventTime": 900.0, "KillerName": "Lee", "DragonType": "Fire"}],
           "scoreboard": {"final": {"ally_kills": 10, "players": [
               {"alias": "Jinx", "name": "Jinx", "side": "ally", "role": "BOTTOM", "kills": 3, "deaths": 2,
                "assists": 4, "cs": 150, "level": 13, "ward_score": 10}]}}}
    msg = pe.team_recap(rec, {"Jinx": "Paul"})
    assert msg.splitlines()[-1].startswith("_Moment décisif 14:20 : Garen meurt") and "Moi#EUW" not in msg
