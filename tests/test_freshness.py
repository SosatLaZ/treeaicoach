"""Freshness guard (freshness.py): stale enemy levels and stale queued lines.

Real bug: "the opponent has been level 10 for 5 minutes and the coach says he is level 9".
"""

from __future__ import annotations

import threading
import time

from treeaicoach import freshness as F
from treeaicoach.alerts import AlertKind, Level, make_alert
from treeaicoach.live_client import GameInfo, PlayerInfo
from treeaicoach.scoreboard import ScoreboardAnalyzer


def _game(gt: float, opp_level: int, my_level: int = 10, opp_dead: bool = False, me_dead: bool = False) -> GameInfo:
    me = PlayerInfo(champion_alias="Garen", champion_name="Garen", team="ORDER", position="TOP",
                    level=my_level, level_api=my_level, is_dead=me_dead)
    opp = PlayerInfo(champion_alias="Darius", champion_name="Darius", team="CHAOS", position="TOP",
                     level=opp_level, level_api=opp_level, is_dead=opp_dead)
    jg = PlayerInfo(champion_alias="LeeSin", champion_name="Lee Sin", team="CHAOS", position="JUNGLE",
                    level=opp_level, level_api=opp_level, has_smite=True)
    return GameInfo(game_time=gt, game_mode="CLASSIC", map_number=11, map_terrain="Default",
                    team_relative_colors=True, me=me, allies=[], enemies=[opp, jg], events=[], fetched_at=0.0)


# ------------------------------------------------------------------ 1. the API level of an unseen enemy
def test_unseen_enemy_level_is_raised_seen_one_is_kept():
    """Level 9 in the API for 5 minutes while unseen at 18:00: he is not still 9 (truth data:
    unseen enemies are always under-reported). Seen: the API is right."""
    lw = F.LevelWatch()
    t0 = 1000.0
    lw.update(_game(780.0, 9), t0, lambda a: t0)              # 13:00, seen, API 9
    g = _game(1080.0, 9)                                        # 18:00, the API still says 9
    lw.update(g, t0 + 300.0, lambda a: t0 if a == "Darius" else t0 + 300.0)
    darius, lee = g.enemies
    assert darius.level_api == 9 and darius.level >= 10 and darius.level_estimated
    assert lee.level == 9 and not lee.level_estimated            # seen just now: kept
    assert darius.level <= 9 + F.MAX_RAISE


def test_level_estimate_never_goes_down_while_unseen_and_resets_when_seen():
    lw = F.LevelWatch()
    lw.update(_game(600.0, 7), 0.0, lambda a: 0.0)
    g1 = _game(900.0, 7)
    lw.update(g1, 300.0, lambda a: 0.0)
    raised = g1.enemies[0].level
    assert raised > 7
    g2 = _game(901.0, 7)
    lw.update(g2, 301.0, lambda a: 0.0)
    assert g2.enemies[0].level >= raised
    g3 = _game(902.0, 7)
    lw.update(g3, 302.0, lambda a: 302.0)                       # seen: the API is fresh again
    assert g3.enemies[0].level == 7


def test_estimate_is_conservative_early_and_on_truth_floor():
    assert F.estimate_level(3, "JUNGLE", 200.0, 10.0) == 3       # seen recently: untouched
    assert F.estimate_level(1, "JUNGLE", 303.0, 300.0) >= 3      # real case: API 1, truth 4-5
    assert F.estimate_level(16, "TOP", 1800.0, 600.0) <= 18


# ------------------------------------------------------------------ 2. lines re-checked before speech
def test_level_line_dropped_after_level_change():
    g = _game(600.0, 9)
    a = make_alert(AlertKind.MACRO_TIP, Level.INFO, 100.0, alias="Darius",
                   text="Darius est niveau 9 : joue prudent", key="macro_tip:level_diff")
    F.stamp(a, g)
    assert F.stale_reason(a, 101.0, _game(601.0, 9)) is None
    assert F.stale_reason(a, 101.0, _game(601.0, 10)) == "level"
    assert F.stale_reason(a, 101.0, _game(601.0, 9, me_dead=True)) == "me_dead"
    assert F.stale_reason(a, 130.0, _game(630.0, 9)) == "age"


def test_danger_line_dropped_when_target_died():
    a = make_alert(AlertKind.PERSONAL_DANGER, Level.WARNING, 50.0, alias="Darius",
                   text="Darius te domine : recule", key="pd:Darius")
    F.stamp(a, _game(600.0, 9))
    assert F.stale_reason(a, 52.0, _game(602.0, 9)) is None
    assert F.stale_reason(a, 52.0, _game(602.0, 9, opp_dead=True)) == "target_dead"
    assert F.stale_reason(a, 55.0, _game(605.0, 9)) == "age"     # a danger is about THIS second


def test_queued_voice_line_goes_stale_before_it_is_spoken():
    """A line waits behind a long sentence; meanwhile the opponent levels up: never said."""
    from treeaicoach.voice import VoiceEngine

    spoken: list[str] = []
    gate = threading.Event()

    class Slow:
        name = "fake"

        def configure(self, *a, **k): ...
        def speak(self, text, purge):
            spoken.append(text)
        def is_speaking(self):
            return not gate.is_set() and bool(spoken)
        def purge(self): ...
        def beep(self, volume): return 0.0
        def voices(self): return []
        def pump(self): ...
        def close(self): ...

    state = {"game": _game(600.0, 9)}
    a = make_alert(AlertKind.MACRO_TIP, Level.INFO, time.monotonic(), alias="Darius",
                   text="Darius est niveau 9 : joue prudent", key="macro_tip:x")
    F.stamp(a, state["game"])
    v = VoiceEngine(_backend_factory=Slow, beep_on_danger=False, _max_age_s=10.0)
    v.chime_before_voice = False
    v.start()
    try:
        v.say("Phrase longue en cours", 1)
        deadline = time.monotonic() + 3.0
        while not spoken and time.monotonic() < deadline:
            time.sleep(0.01)
        v.say(a.text, 1, check=F.checker(a, time.monotonic, lambda: state["game"]))
        state["game"] = _game(601.0, 10)                         # level up while it waits
        gate.set()
        time.sleep(0.6)
    finally:
        v.stop()
    assert a.text not in spoken


def test_scoreboard_level_insight_dropped_once_i_caught_up():
    sb = ScoreboardAnalyzer()
    from treeaicoach.scoreboard import ScoreInsight

    sb.update(_game(600.0, 5, my_level=5), 0.0)
    sb._queue = [ScoreInsight("spike", "level:Darius:6", "Darius est niveau 6 avant toi : prudence.",
                              "NIVEAU 6", "", "warning", "Darius", 0.0)]
    sb._last_emit = -1e9
    g = _game(610.0, 6, my_level=6)
    g.fetched_at = 1.0
    assert sb.update(g, 10.0) == []                              # I am 6 too: no longer true
