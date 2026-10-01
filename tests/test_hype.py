"""hype.py: win probability model, caster lines policy, shareable summary."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from treeaicoach import hype
from treeaicoach.live_client import GameInfo, PlayerInfo


def P(alias, team, name):
    return PlayerInfo(riot_id=f"{name}#EUW", summoner_name=name, champion_alias=alias, champion_name=alias, team=team)


def G(gt=900.0, events=()):
    me = P("Ahri", "ORDER", "Moi")
    allies = [P("Garen", "ORDER", "A1"), P("Jinx", "ORDER", "A2")]
    enemies = [P("Zed", "CHAOS", "E1"), P("Darius", "CHAOS", "E2")]
    return GameInfo(game_time=gt, me=me, allies=allies, enemies=enemies, events=list(events), map_number=11)


def test_model_monotonic_and_documented_values():
    even = hype.win_probability(hype.WinFactors(game_time=900.0, gold_diff=0))
    assert abs(even - 0.5) < 1e-9
    p15 = hype.win_probability(hype.WinFactors(game_time=900.0, gold_diff=3000))
    p30 = hype.win_probability(hype.WinFactors(game_time=1800.0, gold_diff=3000))
    assert 0.68 < p15 < 0.74 and 0.6 < p30 < p15
    baron = hype.win_probability(hype.WinFactors(game_time=1800.0, gold_diff=0, baron_buff=1))
    elder = hype.win_probability(hype.WinFactors(game_time=1800.0, gold_diff=0, elder_buff=1))
    assert 0.6 < baron < elder < 0.75
    huge = hype.win_probability(hype.WinFactors(game_time=600.0, gold_diff=-50000, tower_diff=-11))
    assert huge == hype.P_MIN
    assert hype.win_probability(hype.WinFactors(gold_diff=float("nan"))) == 0.5


def test_factors_from_events():
    ev = [
        {"EventID": 1, "EventName": "ChampionKill", "EventTime": 100.0, "KillerName": "Moi", "VictimName": "E1"},
        {"EventID": 2, "EventName": "TurretKilled", "EventTime": 400.0, "TurretKilled": "Turret_T2_L_03_A",
         "KillerName": "A1"},
        {"EventID": 3, "EventName": "DragonKill", "EventTime": 500.0, "DragonType": "Fire", "KillerName": "E2",
         "Stolen": "False"},
        {"EventID": 4, "EventName": "BaronKill", "EventTime": 1700.0, "KillerName": "A1", "Stolen": "False"},
        {"EventID": 5, "EventName": "InhibKilled", "EventTime": 1750.0, "InhibKilled": "Barracks_T2_L1",
         "KillerName": "A2"},
    ]
    f = hype.factors_from_game(G(1800.0, ev))
    assert (f.kill_diff, f.tower_diff, f.dragon_diff, f.baron_diff, f.baron_buff, f.inhib_diff) == (1, 1, -1, 1, 1, 1)
    sb = SimpleNamespace(players=(1,), team_gold_diff=-2500)
    assert hype.factors_from_game(G(1800.0, ev), sb).gold_diff == -2500
    assert hype.factors_from_game(GameInfo()) is None


def test_caster_lines_only_big_moments_and_new_events():
    old = [{"EventID": 1, "EventName": "Ace", "EventTime": 100.0, "AcingTeam": "ORDER"}]
    c = hype.HypeCaster(SimpleNamespace(caster_style="caster"), seed=1)
    assert c.update(0.0, G(900.0, old)) == []                       # existing events are not announced
    ev = old + [{"EventID": 2, "EventName": "Multikill", "EventTime": 899.0, "KillerName": "A2", "KillStreak": 3}]
    out = c.update(1.0, G(900.0, ev))
    assert len(out) == 1 and "Jinx" in out[0]
    ev2 = ev + [{"EventID": 3, "EventName": "Multikill", "EventTime": 905.0, "KillerName": "Moi", "KillStreak": 3},
                {"EventID": 4, "EventName": "Multikill", "EventTime": 905.0, "KillerName": "A1", "KillStreak": 2}]
    assert c.update(5.0, G(906.0, ev2)) == []                       # mine = praise; doubles are not big
    ev3 = ev2 + [{"EventID": 5, "EventName": "Ace", "EventTime": 950.0, "AcingTeam": "ORDER"}]
    assert c.update(10.0, G(951.0, ev3)) == []                       # 15 s gap between hype lines
    ev4 = ev3 + [{"EventID": 6, "EventName": "BaronKill", "EventTime": 1000.0, "KillerName": "E1",
                  "Stolen": "True"}]
    out = c.update(60.0, G(1001.0, ev4), threat=0)
    assert len(out) == 1 and "Baron" in out[0]
    coach = hype.HypeCaster(SimpleNamespace(caster_style="coach"))
    coach.update(0.0, G(900.0, old))
    assert coach.update(1.0, G(900.0, ev)) == []


def test_shutdown_detection():
    kills = [{"EventID": i, "EventName": "ChampionKill", "EventTime": 300.0 + i, "KillerName": "E1",
              "VictimName": v} for i, v in enumerate(("A1", "A2", "Moi"), start=1)]
    c = hype.HypeCaster(SimpleNamespace(caster_style="caster"), seed=2)
    c.update(0.0, G(400.0, kills))
    sd = kills + [{"EventID": 9, "EventName": "ChampionKill", "EventTime": 500.0, "KillerName": "A1",
                   "VictimName": "E1", "Assisters": []}]
    out = c.update(10.0, G(501.0, sd))
    assert len(out) == 1 and "Garen" in out[0] and "Zed" in out[0]


def test_win_prob_swing_policy():
    c = hype.HypeCaster(SimpleNamespace(caster_style="coach"))
    sb = SimpleNamespace(players=(1,), team_gold_diff=0)
    assert c.update(0.0, G(900.0), sb) == []
    sb.team_gold_diff = 6000
    out = c.update(10.0, G(910.0), sb)
    assert len(out) == 1 and "Probabilité de victoire" in out[0]
    sb.team_gold_diff = -6000
    assert c.update(60.0, G(960.0), sb) == []                        # < 3 min since the last one
    assert c.update(400.0, G(1300.0), sb)
    assert c.hud_text().startswith("Victoire ")
    sobre = hype.HypeCaster(SimpleNamespace(caster_style="sobre"))
    sobre.update(0.0, G(900.0), SimpleNamespace(players=(1,), team_gold_diff=0))
    assert sobre.update(10.0, G(910.0), SimpleNamespace(players=(1,), team_gold_diff=9000)) == []
    assert sobre.win_probability() > 0.8
    assert c.update(0.0, None) == [] and c.update(0.0, object()) == []


def test_restyle_praise():
    assert hype.restyle_praise("multi:12", "Triple kill !", "coach") == "Triple kill !"
    out = hype.restyle_praise("multi:12", "Triple kill !", "caster")
    assert out.endswith("Triple kill !") and out != "Triple kill !"
    assert hype.restyle_praise("cs:900", "Propre.", "caster") == "Propre."


def test_share_summary_from_fixture():
    from treeaicoach.analysis import analyze_game

    rec = json.loads((Path(__file__).parent / "fixtures" / "game_record_sample.json").read_text(encoding="utf-8"))
    text = hype.share_summary(analyze_game(rec), {"win_prob_min": 0.3, "win_prob_max": 0.8, "comeback": True})
    assert text.startswith("TreeAI Coach — ") and "KDA" in text and "remontée" in text
    assert len(text.splitlines()) <= 6
    assert hype.share_summary(None) .startswith("TreeAI Coach")
    c = hype.HypeCaster(SimpleNamespace(caster_style="coach"))
    c.update(0.0, G(900.0))
    assert "win_prob_min" in c.stats()


# ------------------------------------------------------------------------------ engine wiring
class _Voice:
    backend = "fake"

    def __init__(self):
        self.said = []

    def say(self, text, level=1):
        self.said.append(text)

    def set_muted(self, on):
        pass


class _Source:
    def __init__(self, game_fn):
        self.game_fn = game_fn

    def next(self, t):
        return None, self.game_fn(t)


def test_engine_wiring(monkeypatch, tmp_path):
    from dataclasses import replace

    from treeaicoach import paths
    from treeaicoach.config import Config
    from treeaicoach.engine import CoachEngine

    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    paths._reset_cache()
    calls = []

    def fake_call(prov, key, model, system, prompt, timeout, url):
        calls.append(prompt)
        return "Achète une Zhonya. Joue le dragon."

    cfg = Config(ai_provider="groq", ai_api_key="k", caster_style="caster")
    state = {"g": G(900.0)}
    voice = _Voice()
    eng = CoachEngine(cfg, voice, frame_source=_Source(lambda t: state["g"]), clock=lambda: 0.0,
                      enable_hotkeys=False, manage_overlay=False)
    try:
        eng.step(0.0)
        eng._ai._caller = fake_call
        state["g"] = replace(G(910.0), me=replace(G().me, is_dead=True))
        eng.step(1.0)
        eng._ai.wait()
        eng.step(2.0)
        assert calls and getattr(eng, "last_ai_advice", "").startswith("Achète")
        assert eng.win_probability() is not None
        assert "win_prob_min" in eng.hype_stats()
        assert eng.ai_status() == (0, None)
        assert all("Zhonya" not in s for s in voice.said)            # ai_speak off by default
    finally:
        eng.stop()
        paths._reset_cache()
