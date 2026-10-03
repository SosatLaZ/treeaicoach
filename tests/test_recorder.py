"""Tests of treeaicoach.recorder (GameRecorder) + the synthetic 28-minute game generator.

The synthetic game drives the *real* GameRecorder with fake Live Client snapshots, fake tracker
states (at 4 Hz, to exercise the down-sampling) and alerts, exactly like the engine does.
``python tests/test_recorder.py --regen`` rewrites ``tests/fixtures/game_record_sample.json``.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import random
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treeaicoach import recorder as rec_mod  # noqa: E402
from treeaicoach.alerts import Alert, AlertKind, Level  # noqa: E402
from treeaicoach.live_client import GameInfo, PlayerInfo  # noqa: E402
from treeaicoach.recorder import GameRecorder  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "game_record_sample.json"

# ======================================================================================
# synthetic game (28:10, Garen top, blue side, victory)
# ======================================================================================
ROSTER = [
    # riot_id, champion alias, localized name, team, position, smite
    ("Sylvain#EUW", "Garen", "Garen", "ORDER", "TOP", False),
    ("LeBûcheron#1234", "MonkeyKing", "Wukong", "ORDER", "JUNGLE", True),
    ("Mid Ahri#FR1", "Ahri", "Ahri", "ORDER", "MIDDLE", False),
    ("Kai Sa#EUW", "Kaisa", "Kai'Sa", "ORDER", "BOTTOM", False),
    ("Soutien#EUW", "Nunu", "Nunu et Willump", "ORDER", "UTILITY", False),
    ("Darius Main#EUW", "Darius", "Darius", "CHAOS", "TOP", False),
    ("Jungle Diff#KR1", "LeeSin", "Lee Sin", "CHAOS", "JUNGLE", True),
    ("Tank Mid#EUW", "KSante", "K'Santé", "CHAOS", "MIDDLE", False),
    ("Jinx Bot#EUW", "Jinx", "Jinx", "CHAOS", "BOTTOM", False),
    ("Lanterne#EUW", "Thresh", "Thresh", "CHAOS", "UTILITY", False),
]
DURATION = 1690.0
DEATHS = [(250.0, 272.0), (560.0, 588.0), (870.0, 903.0), (1265.0, 1303.0)]   # (death, respawn)

# my waypoints (game_time, u, v); positions interpolated, hidden while dead
MY_PATH = [
    (15, 0.05, 0.95), (60, 0.082, 0.45), (100, 0.085, 0.26), (140, 0.08, 0.22), (180, 0.09, 0.27),
    (220, 0.085, 0.21), (240, 0.09, 0.19), (250, 0.095, 0.17),
    (272, 0.04, 0.96), (310, 0.082, 0.40), (340, 0.085, 0.24), (400, 0.09, 0.20), (418, 0.085, 0.26),
    (430, 0.08, 0.33), (470, 0.085, 0.24), (520, 0.09, 0.18), (550, 0.10, 0.15), (560, 0.11, 0.14),
    (588, 0.04, 0.96), (630, 0.082, 0.36), (700, 0.085, 0.22), (760, 0.09, 0.20), (780, 0.09, 0.21),
    (790, 0.05, 0.95), (805, 0.05, 0.95), (840, 0.082, 0.30), (855, 0.13, 0.20), (866, 0.21, 0.23),
    (870, 0.23, 0.27),
    (903, 0.04, 0.96), (940, 0.30, 0.70), (1000, 0.45, 0.55), (1045, 0.40, 0.45), (1055, 0.34, 0.37),
    (1064, 0.30, 0.42), (1075, 0.22, 0.52), (1110, 0.15, 0.085), (1180, 0.28, 0.082), (1220, 0.30, 0.10),
    (1245, 0.38, 0.17), (1265, 0.42, 0.21),
    (1303, 0.04, 0.96), (1350, 0.40, 0.47), (1420, 0.45, 0.50), (1500, 0.36, 0.34), (1545, 0.335, 0.30),
    (1580, 0.55, 0.40), (1640, 0.75, 0.24), (1690, 0.82, 0.18),
]

# enemy sightings: alias -> [(t0, t1, (u0, v0), (u1, v1))], visible only inside the intervals
ENEMY_PATHS: dict[str, list[tuple[float, float, tuple[float, float], tuple[float, float]]]] = {
    "LeeSin": [
        (148, 156, (0.30, 0.17), (0.27, 0.21)),
        (238, 250, (0.21, 0.23), (0.10, 0.18)),
        (300, 306, (0.73, 0.45), (0.76, 0.50)),
        (386, 392, (0.74, 0.90), (0.79, 0.905)),
        (410, 420, (0.22, 0.22), (0.12, 0.24)),
        (545, 550, (0.55, 0.45), (0.56, 0.44)),
        (655, 662, (0.44, 0.53), (0.47, 0.53)),
        (725, 731, (0.93, 0.83), (0.92, 0.80)),
        (1000, 1010, (0.70, 0.40), (0.72, 0.36)),
        (1135, 1141, (0.84, 0.92), (0.86, 0.915)),
        (1252, 1265, (0.38, 0.28), (0.41, 0.23)),
        (1400, 1415, (0.60, 0.30), (0.52, 0.33)),
        (1530, 1545, (0.36, 0.26), (0.34, 0.29)),
        (1596, 1601, (0.93, 0.74), (0.92, 0.72)),
    ],
    "Darius": [
        (90, 240, (0.085, 0.16), (0.09, 0.15)),
        (242, 250, (0.09, 0.14), (0.095, 0.165)),
        (330, 400, (0.083, 0.16), (0.09, 0.15)),
        (470, 560, (0.086, 0.12), (0.105, 0.14)),
        (640, 760, (0.082, 0.14), (0.09, 0.15)),
        (1255, 1265, (0.46, 0.17), (0.43, 0.20)),
        (1620, 1640, (0.78, 0.20), (0.76, 0.22)),
    ],
    "KSante": [
        (960, 1000, (0.52, 0.47), (0.50, 0.49)),
        (1050, 1063, (0.42, 0.40), (0.35, 0.39)),
        (1256, 1265, (0.46, 0.25), (0.43, 0.22)),
    ],
    "Jinx": [(1290, 1300, (0.62, 0.70), (0.60, 0.68))],
    "Thresh": [(1415, 1420, (0.50, 0.47), (0.47, 0.49))],
    "enemy?1": [(1100, 1103, (0.60, 0.20), (0.61, 0.21))],
}

# alerts actually announced: (game_time, kind, level, text, alias)
ALERTS = [
    (150.0, "jungler_spotted", 0, "Jungler ennemi vu dans la jungle ennemie du haut.", "LeeSin"),
    (244.0, "jungler_approach", 1, "Attention, Lee Sin approche.", "LeeSin"),
    (246.0, "jungler_approach", 2, "Gank ! Lee Sin, recule !", "LeeSin"),
    (252.0, "death_recap", 0, "Mort face à 2 ennemis, dont le jungler.", None),
    (240.0 + 60.0, "objective_soon", 0, "Dragon dans 1 minute.", None),
    (414.0, "jungler_approach", 1, "Attention, Lee Sin approche.", "LeeSin"),
    (417.0, "jungler_approach", 2, "Gank ! Lee Sin, recule !", "LeeSin"),
    (770.0, "recall_gold", 0, "1 450 pièces d'or : pense à rentrer.", None),
    (795.0, "control_ward", 0, "Achète une balise de contrôle.", None),
    (1056.0, "roam_approach", 1, "Attention, K'Santé approche.", "KSante"),
    (1061.0, "roam_approach", 2, "Gank ! K'Santé, recule !", "KSante"),
    (1255.0, "collapse", 2, "Danger, 3 ennemis arrivent, recule !", None),
    (1440.0, "objective_soon", 0, "Baron dans 1 minute.", None),
]

_K = {r[1]: r[0].split("#")[0] for r in ROSTER}   # alias -> Live Client event name
ME = _K["Garen"]
EVENTS = [
    ("GameStart", 0.02, {}),
    ("MinionsSpawning", 65.0, {}),
    ("FirstBlood", 250.0, {"Recipient": _K["LeeSin"]}),
    ("ChampionKill", 250.0, {"KillerName": _K["LeeSin"], "VictimName": ME, "Assisters": [_K["Darius"]]}),
    ("ChampionKill", 330.0, {"KillerName": ME, "VictimName": _K["Darius"], "Assisters": [_K["MonkeyKing"]]}),
    ("ChampionKill", 390.0, {"KillerName": _K["Jinx"], "VictimName": _K["Kaisa"],
                             "Assisters": [_K["LeeSin"], _K["Thresh"]]}),
    ("DragonKill", 440.0, {"DragonType": "Fire", "Stolen": "False", "KillerName": _K["MonkeyKing"],
                           "Assisters": [_K["Kaisa"]]}),
    ("HordeKill", 470.0, {"KillerName": _K["LeeSin"], "Assisters": []}),
    ("HordeKill", 474.0, {"KillerName": _K["LeeSin"], "Assisters": []}),
    ("HordeKill", 480.0, {"KillerName": _K["MonkeyKing"], "Assisters": []}),
    ("ChampionKill", 560.0, {"KillerName": _K["Darius"], "VictimName": ME, "Assisters": []}),
    ("ChampionKill", 660.0, {"KillerName": _K["KSante"], "VictimName": _K["Ahri"], "Assisters": [_K["LeeSin"]]}),
    ("TurretKilled", 700.0, {"TurretKilled": "Turret_T2_L_03_A", "KillerName": ME, "Assisters": []}),
    ("ChampionKill", 730.0, {"KillerName": _K["LeeSin"], "VictimName": _K["Kaisa"], "Assisters": [_K["Jinx"]]}),
    ("DragonKill", 745.0, {"DragonType": "Water", "Stolen": "False", "KillerName": _K["LeeSin"],
                           "Assisters": [_K["Jinx"]]}),
    ("ChampionKill", 870.0, {"KillerName": _K["LeeSin"], "VictimName": ME, "Assisters": [_K["KSante"]]}),
    ("HeraldKill", 920.0, {"Stolen": "False", "KillerName": _K["MonkeyKing"], "Assisters": [_K["Ahri"]]}),
    ("ChampionKill", 950.0, {"KillerName": ME, "VictimName": _K["KSante"], "Assisters": [_K["Ahri"]]}),
    ("ChampionKill", 1000.0, {"KillerName": _K["MonkeyKing"], "VictimName": _K["Jinx"], "Assisters": [_K["Kaisa"]]}),
    ("DragonKill", 1050.0, {"DragonType": "Earth", "Stolen": "False", "KillerName": _K["MonkeyKing"],
                            "Assisters": []}),
    ("TurretKilled", 1100.0, {"TurretKilled": "Turret_T1_R_03_A", "KillerName": _K["Jinx"], "Assisters": []}),
    ("ChampionKill", 1140.0, {"KillerName": _K["LeeSin"], "VictimName": _K["Nunu"],
                              "Assisters": [_K["Jinx"], _K["Thresh"]]}),
    ("ChampionKill", 1180.0, {"KillerName": _K["MonkeyKing"], "VictimName": _K["Darius"], "Assisters": [ME]}),
    ("AtakhanKill", 1215.0, {"KillerName": _K["MonkeyKing"], "Assisters": [ME]}),
    ("ChampionKill", 1265.0, {"KillerName": _K["Darius"], "VictimName": ME,
                              "Assisters": [_K["LeeSin"], _K["KSante"]]}),
    ("ChampionKill", 1300.0, {"KillerName": _K["Kaisa"], "VictimName": _K["Jinx"], "Assisters": [_K["Nunu"], ME]}),
    ("DragonKill", 1350.0, {"DragonType": "Air", "Stolen": "False", "KillerName": _K["MonkeyKing"],
                            "Assisters": [_K["Kaisa"]]}),
    ("ChampionKill", 1420.0, {"KillerName": ME, "VictimName": _K["Thresh"], "Assisters": [_K["MonkeyKing"]]}),
    ("ChampionKill", 1450.0, {"KillerName": _K["Ahri"], "VictimName": _K["KSante"], "Assisters": []}),
    ("ChampionKill", 1470.0, {"KillerName": _K["Kaisa"], "VictimName": _K["Thresh"], "Assisters": [ME, _K["Nunu"]]}),
    ("BaronKill", 1540.0, {"Stolen": "False", "KillerName": _K["MonkeyKing"], "Assisters": [ME, _K["Ahri"]]}),
    ("ChampionKill", 1560.0, {"KillerName": _K["Ahri"], "VictimName": _K["LeeSin"], "Assisters": [ME]}),
    ("TurretKilled", 1580.0, {"TurretKilled": "Turret_T2_C_05_A", "KillerName": _K["Ahri"], "Assisters": []}),
    ("ChampionKill", 1600.0, {"KillerName": _K["Jinx"], "VictimName": _K["Kaisa"], "Assisters": [_K["LeeSin"]]}),
    ("InhibKilled", 1620.0, {"InhibKilled": "Barracks_T2_C1", "KillerName": ME, "Assisters": []}),
    ("ChampionKill", 1640.0, {"KillerName": _K["Kaisa"], "VictimName": _K["Darius"], "Assisters": [ME]}),
    ("GameEnd", 1690.0, {"Result": "Win"}),
]
ITEM_TIMELINE = [(0, [1055, 2003, 3340]), (272, [1055, 1001, 3340]), (588, [1055, 3044, 3047, 3340]),
                 (805, [1055, 3071, 3047, 2055, 3340]), (1303, [1055, 3071, 3047, 3053, 3340]),
                 (1500, [1055, 3071, 3047, 3053, 3742, 3340])]


def _interp(path: list[tuple[float, float, float]], t: float) -> tuple[float, float]:
    for (t0, u0, v0), (t1, u1, v1) in zip(path, path[1:]):
        if t0 <= t <= t1:
            a = 0.0 if t1 <= t0 else (t - t0) / (t1 - t0)
            return u0 + a * (u1 - u0), v0 + a * (v1 - v0)
    return (path[-1][1], path[-1][2]) if t > path[-1][0] else (path[0][1], path[0][2])


def _dead(t: float) -> bool:
    return any(d <= t < r for d, r in DEATHS)


def my_pos(t: float, rng: random.Random) -> tuple[float, float] | None:
    if t < 15 or _dead(t):
        return None
    u, v = _interp(MY_PATH, t)
    return u + rng.uniform(-0.004, 0.004), v + rng.uniform(-0.004, 0.004)


def enemy_pos(alias: str, t: float, rng: random.Random) -> tuple[float, float] | None:
    for t0, t1, a, b in ENEMY_PATHS[alias]:
        if t0 <= t <= t1:
            k = 0.0 if t1 <= t0 else (t - t0) / (t1 - t0)
            # small lane wobble for the long lane intervals
            wob = 0.012 * math.sin(t / 7.0) if t1 - t0 > 30 else 0.0
            return (a[0] + k * (b[0] - a[0]) + rng.uniform(-0.003, 0.003),
                    a[1] + k * (b[1] - a[1]) + wob + rng.uniform(-0.003, 0.003))
    return None


class FakeTrack:
    def __init__(self, key: str, pos: tuple[float, float] | None, visible: bool = True) -> None:
        self.key = key
        self.alias = None if "?" in key else key
        self._pos = pos
        self.visible = visible

    def position(self) -> tuple[float, float] | None:
        return self._pos


class FakeTracker:
    def __init__(self, me: FakeTrack | None, enemies: list[FakeTrack]) -> None:
        self._me = me
        self._enemies = enemies

    def me(self) -> FakeTrack | None:
        return self._me

    def enemies(self, visible_only: bool = True) -> list[FakeTrack]:
        return [e for e in self._enemies if e.visible or not visible_only]


def _scores(t: float) -> dict[str, float]:
    k = sum(1 for n, et, d in EVENTS if n == "ChampionKill" and et <= t and d.get("KillerName") == ME)
    de = sum(1 for n, et, d in EVENTS if n == "ChampionKill" and et <= t and d.get("VictimName") == ME)
    a = sum(1 for n, et, d in EVENTS if n == "ChampionKill" and et <= t and ME in d.get("Assisters", []))
    dead_s = sum(max(0.0, min(t, r) - d) for d, r in DEATHS if t > d)
    cs = max(0.0, (min(t, 1690) - 85.0 - dead_s)) * 0.1135      # ~6.2 CS/min on the whole game
    ward = max(0.0, t - 120.0) / 60.0 * 0.47
    return {"kills": k, "deaths": de, "assists": a, "creepScore": int(cs), "wardScore": round(ward, 2)}


def _level(t: float) -> int:
    return max(1, min(18, 1 + int((t / 60.0) ** 0.85)))


def _items(t: float) -> list[int]:
    cur = ITEM_TIMELINE[0][1]
    for t0, it in ITEM_TIMELINE:
        if t >= t0:
            cur = it
    return list(cur)


def make_players(t: float) -> tuple[PlayerInfo, list[PlayerInfo], list[PlayerInfo]]:
    players = []
    for rid, alias, name, team, pos, smite in ROSTER:
        players.append(PlayerInfo(riot_id=rid, summoner_name=rid.split("#")[0], champion_alias=alias,
                                  champion_name=name, team=team, position=pos, has_smite=smite,
                                  level=_level(t), skin_id=22 if alias == "Garen" else 0))
    me = players[0]
    me.is_dead = _dead(t)
    me.scores = _scores(t)
    me.items = _items(t)
    me.current_gold = float(int(500 + (t % 300) * 4.2))
    return me, players[1:5], players[5:]


def make_game(t: float) -> GameInfo:
    me, allies, enemies = make_players(t)
    events = [{"EventID": i, "EventName": n, "EventTime": et, **d}
              for i, (n, et, d) in enumerate(EVENTS) if et <= t]
    return GameInfo(game_time=t, game_mode="CLASSIC", map_number=11, map_terrain="Infernal", me=me,
                    allies=allies, enemies=enemies, events=events, current_gold=me.current_gold)


def drive_sample_game(out_dir: Path, seed: int = 7, duration: float = DURATION, fps: float = 4.0,
                      finish: bool = True) -> tuple[GameRecorder, Path | None]:
    """Play the synthetic game through a GameRecorder (monotonic t = game_time + 1000)."""
    rng = random.Random(seed)
    clock = {"t": 0.0}
    start_wall = dt.datetime(2026, 9, 12, 20, 31, 0, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    recorder = GameRecorder(out_dir, wallclock=lambda: start_wall + dt.timedelta(seconds=clock["gt"]),
                            monotonic=lambda: clock["t"])
    alerts = sorted(ALERTS)
    ai = 0
    step = 1.0 / fps
    n = int(round(duration / step))
    for i in range(n + 1):
        gt = 12.0 + i * step
        if gt > duration:
            break
        t = gt + 1000.0
        clock["t"], clock["gt"] = t, gt
        if i % int(fps) == 0:
            recorder.on_game_info(make_game(gt), t)
        me = my_pos(gt, rng)
        enemies = []
        for alias in ENEMY_PATHS:
            p = enemy_pos(alias, gt, rng)
            if p is not None:
                enemies.append(FakeTrack(alias, p))
        tracker = FakeTracker(FakeTrack("Garen", me) if me else None, enemies)
        recorder.on_tracks(tracker, t, gt if i % 3 else None)   # sometimes game_time unknown -> estimated
        while ai < len(alerts) and alerts[ai][0] <= gt:
            a = alerts[ai]
            recorder.on_alert(Alert(kind=AlertKind(a[1]), level=Level(a[2]), text=a[3],
                                    key=f"{a[1]}:{a[4]}", t=t, alias=a[4]), a[0])
            ai += 1
    path = recorder.finish() if finish else None
    return recorder, path


def regenerate_fixture() -> Path:
    import shutil
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        _, path = drive_sample_game(Path(d))
        assert path is not None
        data = json.loads(path.read_text(encoding="utf-8"))
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        shutil.rmtree(d, ignore_errors=True)
    return FIXTURE


# ======================================================================================
# tests
# ======================================================================================
@pytest.fixture(scope="module")
def sample_game(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    d = tmp_path_factory.mktemp("games")
    _, path = drive_sample_game(d)
    assert path is not None
    return path, json.loads(path.read_text(encoding="utf-8"))


def test_finish_writes_named_file_and_removes_partial(sample_game: tuple[Path, dict]) -> None:
    path, data = sample_game
    assert path.name == "2026-09-12_2031_Garen.json"
    assert not list(path.parent.glob("*.partial.json"))
    assert not list(path.parent.glob(".tmp-*"))
    assert data["schema"] == 1
    assert data["result"] == "Win" and data["incomplete"] is False
    assert data["duration"] == pytest.approx(1690.0, abs=1.0)


def test_meta_and_roster(sample_game: tuple[Path, dict]) -> None:
    _, data = sample_game
    meta = data["meta"]
    assert meta["champion"] == "Garen" and meta["team"] == "ORDER" and meta["position"] == "TOP"
    assert meta["game_mode"] == "CLASSIC" and meta["map_terrain"] == "Infernal"
    assert meta["start"].startswith("2026-09-12T20:31")
    assert meta["app_version"]
    assert len(data["roster"]) == 10
    lee = next(p for p in data["roster"] if p["alias"] == "LeeSin")
    assert lee["has_smite"] and lee["team"] == "CHAOS" and lee["name"] == "Lee Sin"
    assert sum(p["is_me"] for p in data["roster"]) == 1


def test_downsampling_rates(sample_game: tuple[Path, dict]) -> None:
    _, data = sample_game
    pos = data["my_positions"]
    times = [p[0] for p in pos]
    assert times == sorted(times)
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert min(gaps) >= 0.9                      # 1 Hz (fed at 4 Hz)
    # alive time ~ 1690 - 15 - deaths(~123 s) -> ~1550 samples
    assert 1450 <= len(pos) <= 1600
    for key, lst in data["sightings"].items():
        ts = [p[0] for p in lst]
        assert all(b - a >= 0.45 for a, b in zip(ts, ts[1:])), key   # <= 2 Hz per champion
    assert set(data["sightings"]) == {"LeeSin", "Darius", "KSante", "Jinx", "Thresh", "enemy?1"}
    # only visible intervals are recorded
    lee = data["sightings"]["LeeSin"]
    assert lee[0][0] == pytest.approx(148.0, abs=0.6)


def test_snapshots_events_alerts(sample_game: tuple[Path, dict]) -> None:
    _, data = sample_game
    snaps = data["snapshots"]
    assert 150 <= len(snaps) <= 260          # every 10 s + changes
    last = snaps[-1]
    assert (last["kills"], last["deaths"], last["assists"]) == (3, 4, 5)
    assert set(last) >= {"game_time", "level", "gold", "cs", "kills", "deaths", "assists", "ward_score",
                         "items", "is_dead"}
    assert any(s["is_dead"] for s in snaps)
    ids = [e["EventID"] for e in data["events"]]
    assert ids == sorted(set(ids)) and len(ids) == len(EVENTS)      # deduplicated
    assert len(data["alerts"]) == len(ALERTS)
    a = data["alerts"][2]
    assert a[:4] == [246.0, "jungler_approach", 2, "Gank ! Lee Sin, recule !"]
    assert data["summary"]["ganks"] == 4 and data["summary"]["ganks_survived"] == 2


def test_fixture_matches_generator(sample_game: tuple[Path, dict]) -> None:
    _, data = sample_game
    assert FIXTURE.exists(), "run: python tests/test_recorder.py --regen"
    assert FIXTURE.stat().st_size < 300_000
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for k in ("my_positions", "sightings", "alerts", "events", "snapshots", "roster", "result", "duration"):
        assert fx[k] == data[k], k


def test_autosave_partial_and_crash_listing(tmp_path: Path) -> None:
    recorder, path = drive_sample_game(tmp_path, duration=400.0, finish=False)
    assert path is None and recorder.active
    recorder.autosave()
    partials = list(tmp_path.glob("*.partial.json"))
    assert len(partials) == 1
    data = json.loads(partials[0].read_text(encoding="utf-8"))
    assert data["incomplete"] is True and data["result"] is None
    assert data["summary"]["incomplete"] is True
    # a crashed game (partial left behind) is still listed by report.list_games
    from treeaicoach import report

    games = report.list_games(limit=10, games_dir=tmp_path)
    assert len(games) == 1 and games[0]["incomplete"] is True
    final = recorder.finish()
    assert final is not None and final.exists()
    assert not list(tmp_path.glob("*.partial.json"))
    assert recorder.finish() == final             # idempotent


def test_periodic_autosave_from_on_game_info(tmp_path: Path) -> None:
    """on_game_info triggers an autosave every 60 s of monotonic time."""
    drive_sample_game(tmp_path, duration=200.0, finish=False)
    assert len(list(tmp_path.glob("*.partial.json"))) == 1


def test_never_raises_on_garbage(tmp_path: Path) -> None:
    r = GameRecorder(tmp_path)
    r.on_game_info(None, 0.0)
    r.on_game_info(object(), 0.0)                       # type: ignore[arg-type]
    r.on_tracks(None, 0.0, None)
    r.on_alert(None, None)
    r.autosave()
    assert r.finish() is None and not r.active
    r.on_game_info(make_game(100.0), 5.0)
    assert r.active

    class Broken:
        def me(self) -> Any:
            raise RuntimeError("boom")

        def enemies(self, visible_only: bool = True) -> Any:
            return [FakeTrack("LeeSin", (float("nan"), 0.5)), FakeTrack("Darius", (0.1, 0.2))]

    r.on_tracks(Broken(), 6.0, 101.0)
    r.on_alert(Alert(kind=AlertKind.COLLAPSE, level=Level.DANGER, text="x", key="k", t=6.0), None)
    snap = r.snapshot()
    assert snap is not None and snap["sightings"] == {"Darius": [[101.0, 0.1, 0.2]]}
    assert snap["alerts"][0][0] == pytest.approx(101.0)       # game time estimated from t
    assert r.finish() is not None


def test_new_game_closes_previous_and_post_game_screen_ignored(tmp_path: Path) -> None:
    r = GameRecorder(tmp_path)
    r.on_game_info(make_game(100.0), 1.0)
    r.on_game_info(make_game(110.0), 11.0)
    p1 = r.finish()
    assert p1 is not None
    r.on_game_info(make_game(111.0), 12.0)         # still the same game (post-game screen)
    assert not r.active
    g = make_game(30.0)                            # a new game, other champion
    g.me.champion_alias = "Darius"
    r.on_game_info(g, 100.0)
    assert r.active
    r.on_game_info(make_game(40.0), 110.0)         # yet another game: the Darius record is closed
    files = sorted(p.name for p in tmp_path.glob("*.json"))
    assert len(files) == 2 and any("Darius" in f for f in files)
    r.reset()
    assert not r.active


def test_memory_bound_60_minutes(tmp_path: Path) -> None:
    """Worst case: me + 5 enemies + anonymous tracks visible all game long for 60 min."""
    r = GameRecorder(tmp_path, monotonic=lambda: 0.0)
    r.on_game_info(make_game(1.0), 1.0)
    rng = random.Random(1)
    gt = 1.0
    keys = ["LeeSin", "Darius", "KSante", "Jinx", "Thresh"] + [f"enemy?{i}" for i in range(20)]
    while gt < 3600.0:
        gt += 0.25
        if int(gt * 4) % 4 == 0:
            r.on_game_info(make_game(gt), gt)
        en = [FakeTrack(k, (rng.random(), rng.random())) for k in keys]
        r.on_tracks(FakeTracker(FakeTrack("Garen", (rng.random(), rng.random())), en), gt, gt)
        if int(gt) % 20 == 0 and gt == int(gt):
            r.on_alert(Alert(kind=AlertKind.JUNGLER_APPROACH, level=Level.WARNING,
                             text="Attention, Lee Sin approche." * 3, key="k", t=gt), gt)
    rec = r.snapshot()
    assert rec is not None
    size = len(json.dumps(rec, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert size < 5_000_000, size
    assert len(rec["sightings"]) <= rec_mod.MAX_SIGHTING_KEYS
    assert max(len(v) for v in rec["sightings"].values()) <= rec_mod.MAX_SIGHTINGS_PER_KEY


def test_thread_safety(tmp_path: Path) -> None:
    r = GameRecorder(tmp_path)
    r.on_game_info(make_game(100.0), 100.0)
    stop = threading.Event()
    errors: list[BaseException] = []

    def feeder() -> None:
        try:
            gt = 100.0
            while not stop.is_set() and gt < 400.0:
                gt += 0.1
                r.on_tracks(FakeTracker(FakeTrack("Garen", (0.1, 0.2)), [FakeTrack("LeeSin", (0.3, 0.3))]),
                            gt, gt)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    th = threading.Thread(target=feeder)
    th.start()
    for i in range(20):
        r.on_game_info(make_game(100.0 + i), 100.0 + i)
        r.autosave()
        r.snapshot(recent_s=30)
    stop.set()
    th.join(5)
    assert not errors
    assert r.finish() is not None


def test_death_recap_via_recorder(tmp_path: Path) -> None:
    recorder, _ = drive_sample_game(tmp_path, duration=252.0, finish=False)
    ev = {"EventID": 3, "EventName": "ChampionKill", "EventTime": 250.0, "KillerName": "Jungle Diff",
          "VictimName": "Sylvain", "Assisters": ["Darius Main"]}
    text = recorder.death_recap(ev)
    assert text is not None and "jungler" in text and len(text.split()) <= 20
    recorder.reset()


if __name__ == "__main__":
    if "--regen" in sys.argv:
        p = regenerate_fixture()
        sys.stdout.write(f"written {p} ({p.stat().st_size} bytes)\n")


def test_same_alert_rerouted_within_seconds_is_recorded_once(tmp_path: Path) -> None:
    """Real game 2026-10-03: one call recorded ~25 times in 0.6 s (re-routed every tick)."""
    r = GameRecorder(tmp_path)
    r.on_game_info(make_game(100.0), 1.0)
    for i in range(25):
        r.on_alert(Alert(kind=AlertKind.COLLAPSE, level=Level.DANGER, text="Recule !", key="k", t=1.0), 100.0 + i * 0.02)
    r.on_alert(Alert(kind=AlertKind.COLLAPSE, level=Level.DANGER, text="Autre", key="k2", t=1.0), 100.6)
    r.on_alert(Alert(kind=AlertKind.COLLAPSE, level=Level.DANGER, text="Recule !", key="k", t=1.0), 110.0)
    snap = r.snapshot()
    assert snap is not None
    assert [a[3] for a in snap["alerts"]] == ["Recule !", "Autre", "Recule !"]
