"""Tests for treeaicoach.objectives (epic objective timers and announcements)."""

from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path

import pytest

from treeaicoach import objectives as obj_mod
from treeaicoach import paths
from treeaicoach.alerts import AlertKind, AlertThrottler, Level
from treeaicoach.config import Config
from treeaicoach.live_client import GameInfo, PlayerInfo
from treeaicoach.objectives import (
    OBJECTIVE_SCHEDULE,
    ObjectiveState,
    ObjectiveTimers,
    announcement_text,
    load_schedule,
    merge_schedule,
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "systemp"))
    (tmp_path / "systemp").mkdir()
    paths._reset_cache()
    yield
    paths._reset_cache()


ORDER_NAMES = ["Moi", "Allie1", "Allie2", "Allie3", "Allie4"]
CHAOS_NAMES = ["Ennemi1", "Ennemi2", "Ennemi3", "Ennemi4", "Ennemi5"]


def _player(name: str, team: str, alias: str) -> PlayerInfo:
    return PlayerInfo(riot_id=f"{name}#EUW", summoner_name=f"{name}#EUW", champion_alias=alias,
                      champion_name=alias, team=team)


def make_game(game_time: float, events=(), fetched_at: float | None = None, map_number: int = 11,
              roster_suffix: str = "") -> GameInfo:
    order = [_player(n, "ORDER", f"Champ{i}{roster_suffix}") for i, n in enumerate(ORDER_NAMES)]
    chaos = [_player(n, "CHAOS", f"Champ{i + 5}{roster_suffix}") for i, n in enumerate(CHAOS_NAMES)]
    return GameInfo(game_time=game_time, game_mode="CLASSIC", map_number=map_number, me=order[0],
                    allies=order[1:], enemies=chaos, events=list(events),
                    fetched_at=game_time if fetched_at is None else fetched_at)


def ev(eid, name: str, time: float, **extra) -> dict:
    d = {"EventID": eid, "EventName": name, "EventTime": time}
    d.update(extra)
    return d


def dragon(eid, time: float, killer: str = "Moi", dtype: str = "Fire") -> dict:
    return ev(eid, "DragonKill", time, DragonType=dtype, Stolen="False", KillerName=killer, Assisters=[])


def run(timers: ObjectiveTimers, start: float, end: float, step: float = 0.25, events_at=None):
    """Simulate the analysis loop (t == game time, one snapshot per tick); returns [(gt, text)]."""
    out = []
    n = int(round((end - start) / step))
    for i in range(n + 1):
        gt = start + i * step
        events = events_at(gt) if events_at else []
        for a in timers.update(make_game(gt, events), gt):
            out.append((gt, a.text))
    return out


def by_key(states: list[ObjectiveState]) -> dict[str, ObjectiveState]:
    return {s.key: s for s in states}


# --------------------------------------------------------------------------- phrases


def test_announcement_phrases_are_short_french():
    assert announcement_text("dragon", 60) == "Dragon dans une minute."
    assert announcement_text("baron", 20) == "Baron dans 20 secondes."
    assert announcement_text("grubs", 60) == "Les larves apparaissent dans une minute."
    assert announcement_text("herald", 90) == "Héraut dans 1 minute 30."
    assert announcement_text("elder", 120) == "Dragon ancestral dans 2 minutes."
    assert announcement_text("unknown", 1) == "Objectif dans une seconde."


# --------------------------------------------------------------------------- schedule


def test_default_schedule_announcements_whole_game():
    timers = ObjectiveTimers(Config(), schedule={})
    said = run(timers, 0.0, 1560.0, step=0.5)
    assert said == [
        (240.0, "Dragon dans une minute."),
        (280.0, "Dragon dans 20 secondes."),
        (420.0, "Les larves apparaissent dans une minute."),
        (460.0, "Les larves apparaissent dans 20 secondes."),
        (840.0, "Héraut dans une minute."),
        (880.0, "Héraut dans 20 secondes."),
        (1140.0, "Baron dans une minute."),           # 2026: Atakhan removed, Baron back at 20:00
        (1180.0, "Baron dans 20 secondes."),
    ]


def test_alert_fields_and_throttler_compatibility():
    timers = ObjectiveTimers(Config(), schedule={})
    timers.update(make_game(239.0), 239.0)
    alerts = timers.update(make_game(240.0), 240.0)
    assert len(alerts) == 1
    a = alerts[0]
    assert a.kind == AlertKind.OBJECTIVE_SOON and a.level == Level.INFO and a.t == 240.0
    assert a.key == "objective_soon:dragon:60" and a.alias is None
    thr = AlertThrottler()
    assert thr.filter(alerts, 240.0) == alerts
    a20 = timers.update(make_game(280.0), 280.0)
    assert a20 and a20[0].key == "objective_soon:dragon:20"
    assert thr.filter(a20, 280.0) == a20        # distinct key: not blocked by the 30 s INFO cooldown


def test_states_before_during_and_after_spawns():
    timers = ObjectiveTimers(Config(), schedule={})
    timers.update(make_game(100.0), 100.0)
    st = by_key(timers.states())
    assert list(st) == ["dragon", "grubs", "herald", "baron"]
    assert st["dragon"] == ObjectiveState("Dragon", 300.0, False, "schedule", "dragon", 200.0)
    assert st["herald"].name == "Héraut" and st["grubs"].name == "Larves"
    timers.update(make_game(500.0), 500.0)
    st = by_key(timers.states())
    assert st["dragon"].alive and st["dragon"].remaining == 0.0 and st["dragon"].next_spawn == 300.0
    assert st["grubs"].alive                     # 2026: grubs at 8:00
    timers.update(make_game(890.0), 890.0)
    st = by_key(timers.states())
    assert "grubs" not in st                     # despawned at 14:45
    timers.update(make_game(1190.0), 1190.0)
    assert "herald" not in by_key(timers.states())   # despawned at 19:45


def test_dragon_kill_respawn_five_minutes():
    timers = ObjectiveTimers(Config(), schedule={})
    kill = dragon(4, 421.7)
    said = run(timers, 400.0, 725.0, step=0.5, events_at=lambda gt: [kill] if gt >= 422 else [])
    assert [(round(g), s) for g, s in said if s.startswith("Dragon")] == [
        (662, "Dragon dans une minute."), (702, "Dragon dans 20 secondes.")]
    st = by_key(timers.states())["dragon"]
    assert st.alive and st.source == "event" and st.next_spawn == pytest.approx(721.7)


def test_soul_then_elder_and_elder_respawn():
    timers = ObjectiveTimers(Config(), schedule={})
    events = [dragon(1, 400, "Moi"), dragon(2, 720, "Ennemi2"), dragon(3, 1030, "Allie3"),
              dragon(4, 1340, "allie1"), dragon(5, 1650, "Moi#EUW")]   # 4th ORDER dragon at 1650
    timers.update(make_game(1700.0, events), 1700.0)
    st = by_key(timers.states())
    assert "dragon" not in st
    assert st["elder"].name == "Dragon ancestral" and st["elder"].next_spawn == 2010.0
    said = run(timers, 1940.0, 1960.0, events_at=lambda gt: events)
    assert said == [(1950.0, "Dragon ancestral dans une minute.")]
    events.append(dragon(9, 2100.0, "Ennemi1", "Elder"))
    timers.update(make_game(2101.0, events), 2101.0)
    elder = by_key(timers.states())["elder"]
    assert elder.next_spawn == 2460.0 and not elder.alive


def test_soul_split_three_three_and_unknown_killers():
    timers = ObjectiveTimers(Config(), schedule={})
    evs = [dragon(i, 300 + 310 * i, "Moi" if i % 2 else "Ennemi1") for i in range(6)]
    timers.update(make_game(2200.0, evs), 2200.0)
    assert "dragon" in by_key(timers.states())          # 3 - 3: no soul yet
    evs.append(dragon(6, 2300.0, "Moi"))
    timers.update(make_game(2301.0, evs), 2301.0)
    assert by_key(timers.states())["elder"].next_spawn == 2660.0
    # killers that cannot be resolved (minion / unknown names): the 7th dragon still means a soul
    timers2 = ObjectiveTimers(Config(), schedule={})
    evs2 = [dragon(i, 300 + 310 * i, "???") for i in range(6)]
    timers2.update(make_game(2200.0, evs2), 2200.0)
    assert "dragon" in by_key(timers2.states())
    evs2.append(dragon(6, 2300.0, "???"))
    timers2.update(make_game(2301.0, evs2), 2301.0)
    assert "elder" in by_key(timers2.states())


def test_soul_team_from_assisters():
    timers = ObjectiveTimers(Config(), schedule={})
    evs = [ev(i, "DragonKill", 300 + 310 * i, DragonType="Air", KillerName="Minion_T200",
              Assisters=["Ennemi2", "Ennemi3"]) for i in range(4)]
    timers.update(make_game(1500.0, evs), 1500.0)
    assert "elder" in by_key(timers.states())


def test_baron_herald_grubs_events_and_removed_atakhan():
    timers = ObjectiveTimers(Config(), schedule={})
    events = [
        ev(10, "HordeKill", 500.0, KillerName="Moi"),
        ev(11, "HordeKill", 501.0, KillerName="Moi"),
    ]
    timers.update(make_game(502.0, events), 502.0)
    assert by_key(timers.states())["grubs"].alive       # 1 grub left
    events.append(ev(12, "HordeKill", 501.0, KillerName="Allie1"))
    events.append(ev(20, "HeraldKill", 950.0, KillerName="Ennemi1"))
    events.append(ev(30, "AtakhanKill", 1300.0, KillerName="Moi"))     # removed in 26.1: ignored
    events.append(ev(40, "BaronKill", 1600.0, KillerName="Ennemi1"))
    timers.update(make_game(1610.0, events), 1610.0)
    st = by_key(timers.states())
    assert set(st) == {"dragon", "baron"}
    assert st["baron"].next_spawn == 1960.0 and st["baron"].source == "event" and not st["baron"].alive
    said = run(timers, 1895.0, 1945.0, events_at=lambda gt: events)
    assert said == [(1900.0, "Baron dans une minute."), (1940.0, "Baron dans 20 secondes.")]


def test_duplicate_events_are_ignored():
    timers = ObjectiveTimers(Config(), schedule={})
    grub = ev(10, "HordeKill", 500.0, KillerName="Moi")
    for gt in (501.0, 502.0, 503.0):
        timers.update(make_game(gt, [grub, dict(grub), dict(grub)]), gt)   # same EventID 3x, 3 polls
    assert by_key(timers.states())["grubs"].alive
    no_id = {"EventName": "HordeKill", "EventTime": 505.0, "KillerName": "Moi"}
    timers.update(make_game(506.0, [grub, no_id, dict(no_id)]), 506.0)    # no id: dedupe by content
    assert by_key(timers.states())["grubs"].alive


def test_events_kept_when_the_list_gets_shorter_and_replaced_when_renumbered():
    timers = ObjectiveTimers(Config(), schedule={})
    kill = dragon(4, 420.0)
    timers.update(make_game(430.0, [kill]), 430.0)
    timers.update(make_game(431.0, []), 431.0)                      # reconnect: empty list
    assert by_key(timers.states())["dragon"].next_spawn == 720.0
    other = dragon(4, 425.0)                                        # same id, other event
    timers.update(make_game(432.0, [other]), 432.0)
    assert by_key(timers.states())["dragon"].next_spawn == 725.0


def test_clock_jump_forward_skips_or_adapts_announcements():
    cases = {
        250.0: [(250.0, "Dragon dans 50 secondes."), (280.0, "Dragon dans 20 secondes.")],
        275.0: [(280.0, "Dragon dans 20 secondes.")],
        282.0: [(282.0, "Dragon dans 20 secondes.")],
        284.0: [(284.0, "Dragon dans 16 secondes.")],
        286.0: [],                                      # 6 s late for a 20 s notice: skipped
        290.0: [],
    }
    for start, expected in cases.items():
        timers = ObjectiveTimers(Config(), schedule={})
        timers.update(make_game(100.0), 100.0)                       # first poll long before
        assert run(timers, start, 299.0) == expected, start


def test_new_game_resets_state():
    timers = ObjectiveTimers(Config(), schedule={})
    timers.update(make_game(1000.0, [dragon(4, 420.0), ev(5, "HeraldKill", 900.0)]), 1000.0)
    assert "herald" not in by_key(timers.states())
    timers.update(make_game(10.0, [ev(0, "GameStart", 0.02)]), 5000.0)          # clock back
    st = by_key(timers.states())
    assert st["dragon"].next_spawn == 300.0 and st["dragon"].source == "schedule" and "herald" in st
    timers.update(make_game(200.0, [dragon(4, 150.0)]), 5190.0)
    assert by_key(timers.states())["dragon"].next_spawn == 450.0
    timers.update(make_game(201.0, [], roster_suffix="x"), 5191.0)              # another roster
    assert by_key(timers.states())["dragon"].next_spawn == 300.0
    timers.reset()
    assert timers.states() == [] and timers.game_time is None


def test_not_summoners_rift_or_disabled():
    timers = ObjectiveTimers(Config(), schedule={})
    for gt in (239.0, 240.0, 241.0):
        assert timers.update(make_game(gt, map_number=12), gt) == []        # ARAM
    assert timers.states() == []
    off = ObjectiveTimers(Config(objective_timers=False), schedule={})
    assert run(off, 230.0, 250.0) == []
    assert by_key(off.states())["dragon"].remaining == pytest.approx(50.0)  # HUD timers still work
    off.apply_config(Config(objective_timers=True))
    assert run(off, 250.0, 285.0) == [(280.0, "Dragon dans 20 secondes.")]  # no backlog
    empty = ObjectiveTimers(Config(objective_lead_s=[]), schedule={})
    assert run(empty, 230.0, 290.0) == []


def test_custom_lead_times():
    timers = ObjectiveTimers(Config(objective_lead_s=[30, 90]), schedule={})
    assert run(timers, 200.0, 280.0) == [
        (210.0, "Dragon dans 1 minute 30."),
        (270.0, "Dragon dans 30 secondes."),
    ]
    assert run(timers, 380.0, 460.0) == [
        (390.0, "Les larves apparaissent dans 1 minute 30."),
        (450.0, "Les larves apparaissent dans 30 secondes."),
    ]


def test_simultaneous_announcements_are_spaced():
    timers = ObjectiveTimers(Config(), schedule={"herald": {"first": 900}, "baron": {"first": 900}})
    said = run(timers, 830.0, 850.0, step=0.125)
    assert [s for _g, s in said] == ["Héraut dans une minute.", "Baron dans une minute."]
    assert said[1][0] - said[0][0] == pytest.approx(obj_mod.ANNOUNCE_SPACING_S)


def test_clock_extrapolated_between_polls():
    timers = ObjectiveTimers(Config(), schedule={})
    snap = make_game(239.5, fetched_at=1000.0)     # one API poll, then 8 analysis ticks
    got = []
    for i in range(8):
        t = 1000.0 + i * 0.125
        got += [(t, a.text) for a in timers.update(snap, t)]
    assert got == [(1000.5, "Dragon dans une minute.")]
    assert timers.game_time == pytest.approx(240.375)
    stale = make_game(100.0, fetched_at=0.0)       # clocks not comparable: no extrapolation
    timers.reset()
    timers.update(stale, 99999.0)
    assert timers.game_time == 100.0


def test_game_none_keeps_state_and_garbage_never_raises():
    timers = ObjectiveTimers(Config(), schedule={})
    timers.update(make_game(100.0), 100.0)
    before = timers.states()
    assert timers.update(None, 101.0) == []
    assert timers.states() == before
    garbage = [None, 3, "x", {"EventName": "DragonKill"}, {"EventName": "DragonKill", "EventTime": "abc"},
               {"EventName": "BaronKill", "EventTime": float("nan"), "EventID": 1},
               {"EventName": "BaronKill", "EventTime": -5, "EventID": 2},
               {"EventName": "DragonKill", "EventTime": 400, "EventID": True, "DragonType": None,
                "KillerName": ["x"], "Assisters": "Moi"},
               {"EventName": ["DragonKill"], "EventTime": 500}]
    g = make_game(600.0, garbage)
    assert isinstance(timers.update(g, 600.0), list)

    class Weird:
        game_time = "soon"
        events = 5

    assert timers.update(Weird(), 1.0) == []
    assert timers.update(make_game(float("inf")), 2.0) == []
    assert ObjectiveTimers(object(), schedule={}).update(make_game(240.0), 240.0) is not None


def test_thread_safety_update_and_states():
    timers = ObjectiveTimers(Config(), schedule={})
    errors = []

    def reader():
        try:
            for _ in range(2000):
                for s in timers.states():
                    assert isinstance(s.name, str)
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    th = threading.Thread(target=reader)
    th.start()
    run(timers, 0.0, 1000.0, step=1.0, events_at=lambda gt: [dragon(1, 400.0)] if gt > 400 else [])
    th.join(10)
    assert not errors


# --------------------------------------------------------------------------- schedule files


def test_bundled_schedule_file_matches_defaults():
    data = json.loads(Path(obj_mod.__file__).with_name("assets").joinpath("objectives.json").read_text("utf-8"))
    assert merge_schedule({}, data) == OBJECTIVE_SCHEDULE
    assert load_schedule() == OBJECTIVE_SCHEDULE


def test_user_schedule_override(caplog):
    user = paths.user_data_dir() / "objectives.json"
    user.write_text(json.dumps({
        "_comment": "patch 26.1",
        "dragon": {"first": 310},
        "atakhan": {"first": None},
        "baron": {"first": -5, "bogus": 1},
        "voidgrubs": {"first": 1},
        "grubs": "nope",
    }), encoding="utf-8")
    sched = load_schedule()
    assert sched["dragon"] == {"first": 310.0, "respawn": 300, "soul": 4}
    assert "atakhan" not in sched and sched["baron"]["first"] == 1200      # legacy key: silently ignored
    assert "atakhan" not in caplog.text
    assert sched["grubs"] == OBJECTIVE_SCHEDULE["grubs"]
    assert "invalid value baron.first" in caplog.text and "unknown key baron.bogus" in caplog.text
    timers = ObjectiveTimers(Config())                 # schedule=None: files are read
    timers.update(make_game(100.0), 100.0)
    st = by_key(timers.states())
    assert st["dragon"].next_spawn == 310.0 and "atakhan" not in st
    assert ObjectiveTimers(Config(), schedule={}).schedule == OBJECTIVE_SCHEDULE   # explicit: no files


def test_corrupt_or_huge_schedule_files_are_ignored(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    huge = tmp_path / "huge.json"
    huge.write_text(json.dumps({"dragon": {"first": 1}, "pad": "x" * 70000}), encoding="utf-8")
    lst = tmp_path / "list.json"
    lst.write_text("[1, 2]", encoding="utf-8")
    assert load_schedule([bad, huge, lst, tmp_path / "missing.json", tmp_path]) == OBJECTIVE_SCHEDULE
    assert merge_schedule(OBJECTIVE_SCHEDULE, {"grubs": {"count": 0, "respawn": 240}})["grubs"] == {
        "first": 480, "respawn": 240.0, "despawn": 885, "count": 3}


def test_grubs_second_wave_when_respawn_configured():
    timers = ObjectiveTimers(Config(), schedule={"grubs": {"respawn": 240, "despawn": 900}})
    events = [ev(i, "HordeKill", 400.0 + i, KillerName="Moi") for i in range(3)]
    timers.update(make_game(410.0, events), 410.0)
    st = by_key(timers.states())["grubs"]
    assert st.next_spawn == 642.0 and not st.alive
    timers.update(make_game(700.0, events), 700.0)
    assert by_key(timers.states())["grubs"].alive
