"""Praise scenarios (praise.py): kills, objectives, survival, farm, vision, lead, shopping, no spam."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from treeaicoach.live_client import parse_allgamedata
from treeaicoach.praise import MIN_GAP_S, PraiseCoach
from treeaicoach.scoreboard import ScoreboardAnalyzer

FIXTURE = Path(__file__).parent / "fixtures" / "allgamedata_sample.json"
ME = "Sylvain"


class Sim:
    """Drives a PraiseCoach with an evolving copy of the fixture (one poll per second)."""

    def __init__(self, seed: int = 1) -> None:
        self.raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.coach = PraiseCoach(seed=seed)
        self.sb = ScoreboardAnalyzer()
        self.t = 0.0
        self.next_id = 100
        self.said: list = []

    def me(self) -> dict:
        return self.raw["allPlayers"][0]

    def player(self, champ: str) -> dict:
        return next(p for p in self.raw["allPlayers"] if p["championName"] == champ)

    def event(self, name: str, **kw) -> None:
        self.next_id += 1
        ev = {"EventID": self.next_id, "EventName": name,
              "EventTime": self.raw["gameData"]["gameTime"]}
        ev.update(kw)
        self.raw["events"]["Events"].append(ev)

    def tick(self, n: int = 1, threat: int = 0, dt: float = 1.0) -> list:
        out = []
        for _ in range(n):
            self.t += dt
            self.raw["gameData"]["gameTime"] += dt
            game = parse_allgamedata(copy.deepcopy(self.raw), now=self.t)
            self.sb.update(game, self.t)
            got = self.coach.update(self.t, game, threat=threat, scoreboard=self.sb.summary(), role="TOP")
            out += got
        self.said += out
        return out


def test_solo_kill_on_lane_opponent():
    s = Sim()
    s.tick()                                   # baseline: past events are never praised
    assert s.said == []
    s.event("ChampionKill", KillerName=ME, VictimName="Darius Main", Assisters=[])
    out = s.tick(2)
    assert len(out) == 1 and out[0].kind == "solo_kill"
    assert "Darius" in out[0].text and out[0].alias == "Darius"
    assert out[0].title == "SOLO KILL"


def test_past_events_not_praised_and_kill_with_assist():
    s = Sim()
    s.event("ChampionKill", KillerName=ME, VictimName="Jinx Bot", Assisters=[])
    s.tick(5)
    assert s.said == []                        # joined after the kill
    s.event("ChampionKill", KillerName=ME, VictimName="Jinx Bot", Assisters=["Kai Sa"])
    out = s.tick(2)
    assert [p.kind for p in out] == ["kill"] and "Jinx" in out[0].text


def test_multikill_supersedes_kills_and_first_blood():
    s = Sim()
    s.tick()
    s.event("ChampionKill", KillerName=ME, VictimName="Jinx Bot", Assisters=["Kai Sa"])
    s.event("ChampionKill", KillerName=ME, VictimName="Lanterne", Assisters=["Kai Sa"])
    s.event("Multikill", KillerName=ME, KillStreak=2)
    out = s.tick(3)
    assert len(out) == 1 and out[0].kind == "multikill" and "Double kill" in out[0].text
    assert s.tick(60) == []                    # the single kills of that fight are not praised later
    s2 = Sim()
    s2.tick()
    s2.event("FirstBlood", Recipient=ME)
    s2.event("ChampionKill", KillerName=ME, VictimName="Jinx Bot", Assisters=["Kai Sa"])
    out = s2.tick(2)
    assert out[0].kind == "first_blood"


def test_shutdown():
    s = Sim()
    s.tick()
    for v in ("Kai Sa", "Soutien", "Mid Ahri"):
        s.event("ChampionKill", KillerName="Jinx Bot", VictimName=v, Assisters=[])
    s.event("ChampionKill", KillerName=ME, VictimName="Jinx Bot", Assisters=["Mid Ahri"])
    out = s.tick(2)
    assert out[0].kind == "shutdown" and "Jinx" in out[0].text


def test_objective_participation_and_steal():
    s = Sim()
    s.tick()
    s.event("DragonKill", KillerName="LeBûcheron", Assisters=[ME], DragonType="Fire", Stolen="False")
    out = s.tick(2)
    assert out and out[0].kind == "objective" and "ragon" in out[0].text
    s.event("BaronKill", KillerName="Jungle Diff", Assisters=[])          # enemy objective: nothing
    assert s.tick(60) == []
    s.event("BaronKill", KillerName=ME, Assisters=[], Stolen="True")
    out = s.tick(2)
    assert out and out[0].kind == "steal"


def test_no_praise_during_threat_then_after():
    s = Sim()
    s.tick()
    s.event("ChampionKill", KillerName=ME, VictimName="Darius Main", Assisters=[])
    assert s.tick(5, threat=1) == []
    assert s.tick(2) == []                     # quiet right after the threat
    out = s.tick(4)
    assert len(out) == 1 and out[0].kind == "solo_kill"


def test_gank_survived_and_death_cancels():
    s = Sim()
    s.tick()
    s.coach.note_danger(s.t)
    s.tick(3, threat=2)
    out = s.tick(15)
    assert [p.kind for p in out] == ["gank_dodge"]
    # second scenario: I die after the DANGER alert
    s2 = Sim()
    s2.tick()
    s2.coach.note_danger(s2.t)
    s2.me()["isDead"] = True
    s2.tick(3, threat=2)
    s2.me()["isDead"] = False
    assert s2.tick(30) == []


def test_escape_at_low_hp():
    s = Sim()
    s.tick()
    stats = s.raw["activePlayer"]["championStats"]
    stats["currentHealth"] = 1300.0
    s.tick(2)
    stats["currentHealth"] = 200.0             # 13 %: a fight
    s.tick(4)
    stats["currentHealth"] = 400.0
    out = s.tick(10)
    assert [p.kind for p in out] == ["escape"]
    # slow decay without fight / threat (e.g. lane sustain): no praise
    s2 = Sim()
    s2.tick()
    st2 = s2.raw["activePlayer"]["championStats"]
    for hp in (700, 600, 500, 400, 350, 280):
        st2["currentHealth"] = float(hp)
        s2.tick(8)
    assert s2.tick(20) == []


def test_cs_checkpoint_and_vision_milestone():
    s = Sim()
    s.raw["gameData"]["gameTime"] = 880.0
    s.me()["scores"]["creepScore"] = 130
    s.tick()
    s.me()["scores"]["creepScore"] = 135               # 135 CS at 15:00 -> 9 CS/min
    out = s.tick(25)
    assert [p.kind for p in out] == ["cs"] and "9,0 CS par minute" in out[0].text
    s.me()["scores"]["wardScore"] = 16.0               # crosses 15
    out = s.tick(60)
    assert [p.kind for p in out] == ["vision"] and "15" in out[0].text


def test_lane_lead_growing():
    s = Sim()
    s.player("Darius")["items"] = [{"itemID": 1054, "slot": 0}]
    garen = s.me()
    garen["items"] = [{"itemID": 1055, "slot": 0}]
    s.tick(2)
    garen["items"].append({"itemID": 3031, "slot": 4})        # +3500 -> crosses 2500 lead
    garen["scores"]["creepScore"] = 140
    out = s.tick(3)
    out += s.tick(int(MIN_GAP_S) + 5)
    # one purchase = one compliment: the lead (higher priority) wins over "item completed"
    assert [p.kind for p in out] == ["lead"]
    assert "sur Darius" in out[0].text and out[0].alias == "Darius"


def test_clean_back():
    s = Sim()
    s.tick(2)
    s.me()["items"].append({"itemID": 3133, "slot": 5})       # Caulfield's Warhammer (1100)
    s.raw["activePlayer"]["currentGold"] = 120.0
    out = s.tick(2)
    assert [p.kind for p in out] == ["back"]


def test_rate_limit_variety_and_seed():
    s = Sim(seed=7)
    s.tick()
    texts = []
    for _ in range(6):
        s.event("ChampionKill", KillerName=ME, VictimName="Jinx Bot", Assisters=["Kai Sa"])
        texts += [p.text for p in s.tick(int(MIN_GAP_S) + 1)]
    assert len(texts) == 6
    assert all(a != b for a, b in zip(texts, texts[1:]))     # never twice in a row
    # rate limit: 3 kills within 10 s -> one praise
    s2 = Sim()
    s2.tick()
    got = []
    for _ in range(3):
        s2.event("ChampionKill", KillerName=ME, VictimName="Jinx Bot", Assisters=["Kai Sa"])
        got += s2.tick(3)
    assert len(got) == 1
    # same seed -> same choices
    a, b = Sim(seed=3), Sim(seed=3)
    for sim in (a, b):
        sim.tick()
        sim.event("ChampionKill", KillerName=ME, VictimName="Jinx Bot", Assisters=["Kai Sa"])
        sim.tick(2)
    assert [p.text for p in a.said] == [p.text for p in b.said]


def test_never_raises():
    c = PraiseCoach()
    assert c.update(1.0, None) == []
    assert c.update(1.0, object()) == []
    c.note_danger(2.0)
    assert c.update(float("nan"), None) == []


# ------------------------------------------------------------------ engine wiring
class _Voice:
    backend = "fake"

    def __init__(self) -> None:
        self.said: list[tuple[str, int]] = []

    def say(self, text: str, level: int = 1) -> None:
        self.said.append((text, int(level)))

    def set_muted(self, on: bool) -> None:
        pass


class _Rec:
    def __init__(self) -> None:
        self.scoreboards: list = []

    def on_game_info(self, game, t):
        pass

    def on_tracks(self, tracker, t, gt):
        pass

    def on_alert(self, alert, gt):
        pass

    def on_scoreboard(self, summary, gt):
        self.scoreboards.append((gt, summary))

    def death_recap(self, ev):
        return None

    def finish(self):
        return None


class _Source:
    is_demo = False

    def __init__(self, sim: Sim) -> None:
        self.sim = sim

    def next(self, t):
        self.sim.raw["gameData"]["gameTime"] += 0.5
        return None, parse_allgamedata(copy.deepcopy(self.sim.raw), now=t)


def test_engine_praise_voice_toast_and_recorder(monkeypatch, tmp_path):
    from treeaicoach import paths
    from treeaicoach.alerts import AlertKind
    from treeaicoach.config import Config
    from treeaicoach.engine import CoachEngine

    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    paths._reset_cache()
    sim = Sim()
    rec = _Rec()
    clock = [0.0]
    voice = _Voice()
    eng = CoachEngine(Config(voice_level="normal"), voice, frame_source=_Source(sim), clock=lambda: clock[0],
                      enable_hotkeys=False, manage_overlay=False, recorder_factory=lambda: rec)
    try:
        said = []
        for i in range(1, 6):
            clock[0] = i * 0.5
            said += eng.step(clock[0])
        sim.event("ChampionKill", KillerName=ME, VictimName="Darius Main", Assisters=[])
        for i in range(6, 20):
            clock[0] = i * 0.5
            said += eng.step(clock[0])
        kinds = [a.kind for a in said]
        # speech budget (one message every 20 s): the praise is spoken, or waits in the budget queue
        queued = eng._tactics.gate.budget.queued()
        praise = next((a for a in list(said) + queued if a.kind == AlertKind.PRAISE), None)
        assert praise is not None and "Darius" in praise.text
        assert praise not in said or any(praise.text == t for t, _l in voice.said)
        # Lee Sin is 4/1 in the fixture: a Tab insight was spoken too (INFO)
        # (spoken, or written when the speech budget is used: one message every 20 s)
        assert (AlertKind.SCOREBOARD in kinds or any(k == "scoreboard" for _t, k, _x in eng.text_messages)
                or any(a.kind == AlertKind.SCOREBOARD for a in queued))
        st = eng._build_overlay_state(clock[0])
        assert st.toasts and {v.toast.kind for v in st.toasts} <= {"praise", "warning", "insight", "danger"}
        assert st.insight                                      # HUD line (coach or Tab summary)
        assert rec.scoreboards and rec.scoreboards[-1][1]["matchups"][0]["role"] == "TOP"
        assert eng.scoreboard_summary().my_matchup.enemy == "Darius"
    finally:
        eng.stop()
        paths._reset_cache()
