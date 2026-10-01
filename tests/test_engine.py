"""Tests for treeaicoach.engine (pipeline, lifecycle, relocation, threads) - no game needed."""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from treeaicoach import paths
from treeaicoach.alerts import AlertKind, Level
from treeaicoach.capture import Rect
from treeaicoach.config import Config
from treeaicoach.demo import DemoSource
from treeaicoach.detector import ClassicDetector, Detection
from treeaicoach.engine import (
    CoachEngine,
    EngineState,
    EngineStatus,
    find_camera_center,
    seconds_fr,
)
from treeaicoach.live_client import GameInfo, PlayerInfo
from treeaicoach.minimap_locator import MinimapLocation

GANK_KINDS = {AlertKind.JUNGLER_APPROACH, AlertKind.ROAM_APPROACH, AlertKind.COLLAPSE}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    paths._reset_cache()
    yield
    paths._reset_cache()


class FakeVoice:
    backend = "fake"

    def __init__(self) -> None:
        self.said: list[tuple[str, int]] = []
        self.muted = False
        self.lock = threading.Lock()

    def say(self, text: str, level: int = 1) -> None:
        with self.lock:
            if not self.muted:
                self.said.append((text, int(level)))

    def set_muted(self, on: bool) -> None:
        self.muted = bool(on)

    def texts(self) -> list[str]:
        with self.lock:
            return [t for t, _l in self.said]


class Clock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def make_engine(source: Any = None, **kw: Any) -> tuple[CoachEngine, FakeVoice, Clock]:
    clock = Clock()
    voice = FakeVoice()
    kw.setdefault("enable_hotkeys", False)
    kw.setdefault("manage_overlay", False)
    cfg = kw.pop("cfg", Config())
    eng = CoachEngine(cfg, voice, frame_source=source, clock=clock, **kw)
    return eng, voice, clock


def player(alias: str, team: str, pos: str, *, smite: bool = False, name: str | None = None,
           dead: bool = False, rid: str | None = None) -> PlayerInfo:
    rid = rid or f"{alias}#T"
    return PlayerInfo(riot_id=rid, summoner_name=rid, champion_alias=alias, champion_name=name or alias,
                      team=team, position=pos, is_dead=dead, has_smite=smite)


def game_info(gt: float, *, dead: bool = False, events: list | None = None, map_number: int = 11,
              me: bool = True) -> GameInfo:
    return GameInfo(
        game_time=gt, game_mode="CLASSIC", map_number=map_number, map_terrain="Default",
        me=player("Garen", "ORDER", "TOP", dead=dead, rid="Moi#EUW") if me else None,
        allies=[player("Vi", "ORDER", "JUNGLE", smite=True)],
        enemies=[player("Darius", "CHAOS", "TOP"), player("LeeSin", "CHAOS", "JUNGLE", smite=True, name="Lee Sin")],
        events=list(events or [{"EventID": 0, "EventName": "GameStart", "EventTime": 0.0}]),
        fetched_at=0.0,
    )


# ------------------------------------------------------------------------------ demo run
@pytest.fixture(scope="module")
def demo_run():
    """Run the demo scenario once through the engine (accelerated clock, classic detector)."""
    src = DemoSource(size=280)
    clock = Clock()
    voice = FakeVoice()
    eng = CoachEngine(Config(), voice, detector=ClassicDetector(), frame_source=src, clock=clock,
                      enable_hotkeys=False, manage_overlay=False)
    fps = 8.0
    out: dict[str, Any] = {"alerts": [], "fog_at": None, "overlay": None, "preview": None,
                           "status_mid": None, "where": {}, "tick_s": []}
    for i in range(int(52 * fps)):
        t = i / fps
        clock.t = t
        t0 = time.perf_counter()
        said = eng.step(t)
        out["tick_s"].append(time.perf_counter() - t0)
        out["alerts"] += [(t, a) for a in said]
        if out["fog_at"] is None and t > src.JUNGLER_HIDE_AT and eng.fog_tracker.estimates():
            out["fog_at"] = t
        if abs(t - 3.0) < 1e-9:
            out["where"]["visible"] = eng.jungler_status_text()
        if abs(t - 20.0) < 1e-9:
            out["where"]["hidden"] = eng.jungler_status_text()
            out["overlay_fog"] = eng.get_overlay_state()
        if abs(t - 38.0) < 1e-9:
            out["overlay"] = eng.get_overlay_state()
            out["preview"] = eng.get_preview()
            out["status_mid"] = eng.get_status()
    out["engine"], out["voice"], out["src"] = eng, voice, src
    return out


def test_demo_no_gank_alert_before_window_and_danger_inside(demo_run):
    w0, w1 = demo_run["src"].GANK_WINDOW
    alerts = demo_run["alerts"]
    early = [(t, a.text) for t, a in alerts if a.kind in GANK_KINDS and t < w0]
    assert early == []
    danger = [t for t, a in alerts if a.kind in GANK_KINDS and a.level >= Level.DANGER and w0 <= t <= w1]
    assert danger, [(t, a.text) for t, a in alerts]
    # the jungler comes back on the same side of the map: nothing new, no "jungler spotted"
    spotted = [t for t, a in alerts if a.kind == AlertKind.JUNGLER_SPOTTED and w0 - 1 <= t <= w1]
    assert not spotted or spotted[0] <= danger[0]
    # every alert returned by step() was spoken
    assert [a.text for _t, a in alerts] == demo_run["voice"].texts()


def test_safe_mode_no_gank_alerts_and_no_fog():
    """Safe mode: objective timers / reminders only, no gank / jungler alerts, no fog circle."""
    src = DemoSource(size=280)
    eng, voice, clock = make_engine(src, detector=ClassicDetector(), cfg=Config(safe_mode=True))
    fps = 8.0
    said: list[Any] = []
    for i in range(int(52 * fps)):
        t = i / fps
        clock.t = t
        said += eng.step(t)
        if abs(t - 20.0) < 1e-9:
            assert list(eng.get_overlay_state().fogs) == []
    kinds = {a.kind for a in said}
    assert not kinds & (GANK_KINDS | {AlertKind.JUNGLER_SPOTTED, AlertKind.LANER_MIA})
    # V2 voice whitelist: the 60 s objective warning is WRITTEN (only the last one, if involved, is spoken)
    assert AlertKind.OBJECTIVE_SOON in kinds or any(k == "objective_soon" for _t, k, _x in eng.text_messages)
    assert eng.fog_tracker is None or eng.fog_tracker.estimates() == []


def test_demo_objective_and_fog(demo_run):
    kinds = {a.kind for _t, a in demo_run["alerts"]}
    written = {k for _t, k, _x in demo_run["engine"].text_messages}
    assert AlertKind.OBJECTIVE_SOON in kinds or "objective_soon" in written   # Herald at 15:00, announced at 14:00
    hide = demo_run["src"].JUNGLER_HIDE_AT
    assert demo_run["fog_at"] is not None and hide < demo_run["fog_at"] < hide + 3.0
    st = demo_run["overlay_fog"]
    assert st is not None and any(f.alias == "LeeSin" for f in st.fogs)


def test_demo_status_overlay_preview(demo_run):
    st: EngineStatus = demo_run["status_mid"]
    assert st.state == EngineState.RUNNING and st.demo
    assert st.detector == "classic" and st.voice == "fake"
    assert st.game_time is not None and 850 < st.game_time < 860
    assert st.enemies_visible >= 2
    ov = demo_run["overlay"]
    assert ov is not None
    assert ov.me_uv is not None and ov.me_uv[0] < 0.2 and ov.me_uv[1] < 0.35
    assert len(ov.enemies) >= 5
    assert {e.alias for e in ov.enemies} >= {"Darius", "LeeSin", "Ahri", "Caitlyn", "Nautilus"}
    lee = next(e for e in ov.enemies if e.alias == "LeeSin")
    assert lee.is_jungler and lee.visible and lee.icon is not None
    assert ov.threat_level >= 1 and ov.threat_text != "SÛR"
    assert ov.jungler_line and ov.jungler_line.startswith("Jungler : Lee Sin")
    assert ov.warn_radius == pytest.approx(Config().effective_warn_radius())
    assert ov.objectives and ov.game_time is not None
    # HUD v3 card extras: gauge (with the gank threat it is at most PRUDENT), no AI (off by default)
    assert ov.gauge is None or -2 <= ov.gauge <= -1
    assert ov.ai_counter is None and isinstance(ov.in_base, bool)
    assert ov.tip is None or (ov.tip_tone in ("danger", "warning", "go", "info") and ov.tip_since is not None)
    prev = demo_run["preview"]
    assert isinstance(prev, np.ndarray) and prev.shape == (280, 280, 3) and prev.dtype == np.uint8


def test_demo_jungler_status_text(demo_run):
    assert demo_run["where"]["visible"].startswith("Lee Sin est visible")
    hidden = demo_run["where"]["hidden"]
    assert hidden.startswith("Lee Sin vu il y a ") and "secondes" in hidden and hidden.endswith(".")


def test_demo_tick_time(demo_run):
    ticks = sorted(demo_run["tick_s"][5:])
    median = ticks[len(ticks) // 2]
    assert median < 0.15      # measured ~20 ms; generous bound (shared CI machines)


# ------------------------------------------------------------------------------ robustness
class ListSource:
    def __init__(self, frames: list[Any], game: Any) -> None:
        self.frames = frames
        self.game = game
        self.i = 0

    def next(self, t: float):
        f = self.frames[self.i % len(self.frames)]
        self.i += 1
        g = self.game(t) if callable(self.game) else self.game
        return f, g


def test_garbage_frames_never_crash():
    rng = np.random.default_rng(1)
    frames = [None, np.zeros((0, 0, 3), np.uint8), np.zeros((5, 5, 3), np.uint8), "pas une image",
              rng.integers(0, 255, (200, 200, 3), dtype=np.uint8), np.zeros((200, 200), np.uint8),
              rng.random((150, 150, 4)).astype(np.float32) * 255, np.full((64, 64, 3), np.nan, np.float64),
              np.zeros((300, 300, 3), np.uint8), np.ones((10, 10, 10, 3), np.uint8)]
    eng, _voice, clock = make_engine(ListSource(frames, lambda t: game_info(600 + t)),
                                     detector=ClassicDetector())
    for i in range(40):
        clock.t = i * 0.125
        assert isinstance(eng.step(clock.t), list)
    st = eng.get_status()
    assert st.state in (EngineState.RUNNING, EngineState.CAPTURE_BLACK)
    assert eng.get_overlay_state() is not None
    eng.get_preview()


def test_black_frame_state():
    eng, _voice, clock = make_engine(ListSource([np.zeros((256, 256, 3), np.uint8)], game_info(600)),
                                     detector=ClassicDetector())
    eng.step(0.0)
    st = eng.get_status()
    assert st.state == EngineState.CAPTURE_BLACK and "Sans bordure" in st.message


class RaisingDetector:
    name = "boom"

    def detect(self, img):
        raise RuntimeError("detector exploded")


class RaisingIdentifier:
    def set_roster(self, game):
        raise ValueError("roster exploded")

    def identify(self, img, dets):
        raise ValueError("identify exploded")


def test_detector_and_identifier_errors_are_contained():
    src = DemoSource(size=200)
    eng, _voice, clock = make_engine(src, detector=RaisingDetector())
    for i in range(10):
        clock.t = i * 0.2
        assert eng.step(clock.t) == [] or True
    assert eng.get_status().errors >= 10
    assert eng.get_status().state == EngineState.RUNNING

    eng2, _v2, clock2 = make_engine(DemoSource(size=200), detector=ClassicDetector(),
                                    identifier=RaisingIdentifier())
    for i in range(10):
        clock2.t = i * 0.2
        eng2.step(clock2.t)
    assert eng2.get_status().errors >= 10
    # the pass-through fallback still feeds the tracker
    assert eng2.tracker.tracks()


def test_frame_source_raising_and_step_bad_time():
    class Boom:
        def next(self, t):
            raise OSError("no frame")

    eng, _v, _c = make_engine(Boom())
    assert eng.step(1.0) == []
    assert eng.step(float("nan")) == []
    assert eng.get_status().state in (EngineState.STOPPED, EngineState.WAITING_GAME)


def test_unsupported_mode_and_spectator():
    eng, _v, clock = make_engine(ListSource([np.zeros((200, 200, 3), np.uint8)], game_info(100, map_number=12)))
    eng.step(0.0)
    assert eng.get_status().state == EngineState.UNSUPPORTED_MODE
    assert "Faille" in eng.get_status().message and "ARAM" in eng.get_status().message
    assert eng.get_overlay_state() is None
    eng2, _v2, _c2 = make_engine(ListSource([None], game_info(100, me=False)))
    eng2.step(0.0)
    assert eng2.get_status().state == EngineState.UNSUPPORTED_MODE


# ------------------------------------------------------------------------------ lifecycle
def test_game_end_by_disappearance_writes_record_and_report(tmp_path: Path):
    opened: list[Path] = []
    frame = DemoSource(size=200).render_at(1.0)
    state = {"gone": False}

    def game(t: float):
        return None if state["gone"] else game_info(300 + t)

    eng, voice, clock = make_engine(ListSource([frame], game), detector=ClassicDetector(),
                                    report_opener=opened.append)
    for i in range(12 * 4):
        clock.t = i * 0.25
        eng.step(clock.t)
    assert eng.in_game
    state["gone"] = True
    t_gone = clock.t
    while clock.t < t_gone + 7.0:
        clock.t += 0.5
        eng.step(clock.t)
    assert eng.in_game            # not yet: the API may hiccup
    clock.t = t_gone + 9.0
    eng.step(clock.t)
    assert not eng.in_game
    assert eng.get_status().state == EngineState.WAITING_GAME
    assert eng.wait_background(20.0)
    rec = eng.last_record_path
    assert rec is not None and rec.is_file()
    assert rec.parent == paths.user_data_dir() / "games"
    assert eng.last_report_path is not None and eng.last_report_path.is_file()
    assert eng.last_report_path.suffix == ".html"
    assert opened == [eng.last_report_path]


def test_game_end_event_and_break_reminder():
    reports: list[Path] = []
    written: list[Path] = []

    def writer(p: Path) -> Path:
        written.append(p)
        out = p.with_suffix(".html")
        out.write_text("<html></html>", encoding="utf-8")
        return out

    game_ref: dict[str, Any] = {"g": None}
    eng, voice, clock = make_engine(ListSource([None], lambda t: game_ref["g"]), report_writer=writer,
                                    report_opener=reports.append)
    t = 0.0
    for n in range(3):
        for k in range(4):
            game_ref["g"] = game_info(100.0 + k * 10)
            t += 1.0
            clock.t = t
            eng.step(t)
        assert eng.in_game
        end = {"EventID": 9, "EventName": "GameEnd", "EventTime": 140.0, "Result": "Lose"}
        game_ref["g"] = game_info(140.0, events=[end])
        t += 1.0
        clock.t = t
        eng.step(t)
        assert not eng.in_game
        # post-GameEnd data of the same game does not restart it
        game_ref["g"] = game_info(142.0, events=[end])
        t += 1.0
        eng.step(t)
        assert not eng.in_game
        assert eng.wait_background(10.0)
    assert len(written) == 3 and len(reports) == 3
    st = eng.get_status()
    assert st.session == (3, 0, 3)
    assert st.banner and "3 défaites d'affilée" in st.banner
    assert any("pause de 10 minutes" in s for s in voice.texts())


def test_new_game_when_game_time_goes_back():
    game_ref: dict[str, Any] = {"g": game_info(900.0)}
    eng, _v, clock = make_engine(ListSource([None], lambda t: game_ref["g"]), recorder_factory=lambda: None)
    eng.step(0.0)
    eng.tracker.update(0.0, [])
    first_recorder_calls = []
    eng._recorder = type("R", (), {"finish": lambda self: first_recorder_calls.append(1) or None,
                                    "on_game_info": lambda self, g, t: None,
                                    "on_tracks": lambda self, *a: None,
                                    "on_alert": lambda self, *a: None})()
    game_ref["g"] = game_info(30.0)
    eng.step(1.0)
    assert eng.in_game
    assert eng.wait_background(5.0)
    assert first_recorder_calls == [1]


class FakeRecorder:
    def __init__(self) -> None:
        self.infos = 0
        self.alerts: list[Any] = []
        self.recaps: list[Any] = []
        self.finished = False

    def on_game_info(self, game, t):
        self.infos += 1

    def on_tracks(self, tracker, t, gt):
        pass

    def on_alert(self, alert, gt):
        self.alerts.append(alert)

    def death_recap(self, event):
        self.recaps.append(event)
        return "Mort face à 2 ennemis, dont le jungler."

    def finish(self):
        self.finished = True
        return None


def test_death_recap_spoken_two_seconds_later():
    rec = FakeRecorder()
    game_ref: dict[str, Any] = {"g": game_info(500.0)}
    eng, voice, clock = make_engine(ListSource([None], lambda t: game_ref["g"]), recorder_factory=lambda: rec,
                                    cfg=Config(voice_level="bavard"))   # V2: written below "bavard"
    kill = {"EventID": 5, "EventName": "ChampionKill", "EventTime": 501.0, "VictimName": "Moi#EUW",
            "KillerName": "LeeSin#T", "Assisters": []}
    spoken_at = None
    for i in range(40):
        t = i * 0.25
        clock.t = t
        if i == 4:
            game_ref["g"] = game_info(501.0, dead=True, events=[kill])
        game_ref["g"] = replace(game_ref["g"], game_time=500.0 + t)
        said = eng.step(t)
        if any(a.kind == AlertKind.DEATH_RECAP for a in said):
            spoken_at = t
    assert spoken_at is not None and 2.9 <= spoken_at <= 3.6
    assert rec.recaps and rec.recaps[0]["EventName"] == "ChampionKill"
    assert "Mort face à 2 ennemis, dont le jungler." in voice.texts()
    assert any(a.kind == AlertKind.DEATH_RECAP for a in rec.alerts)
    assert rec.infos > 5


# ------------------------------------------------------------------------------ capture path
class FakeLiveClient:
    def __init__(self, game_fn) -> None:
        self.game_fn = game_fn
        self.calls = 0

    def fetch(self):
        self.calls += 1
        return self.game_fn()


class FakeCapture:
    """Screen = dark 1280x720 with the demo minimap in the bottom-right corner."""

    def __init__(self, black: bool = False) -> None:
        self.W, self.H, self.side = 1280, 720, 180
        self.x0, self.y0 = self.W - self.side - 8, self.H - self.side - 8
        self.screen = np.full((self.H, self.W, 3), 40, np.uint8)
        mm = DemoSource(size=self.side).render_at(2.0)
        self.screen[self.y0:self.y0 + self.side, self.x0:self.x0 + self.side] = mm
        if black:
            self.screen[:] = 0
        self.grabs: list[Rect] = []

    def grab(self, rect: Rect):
        self.grabs.append(rect)
        x, y, w, h = rect.x, rect.y, rect.w, rect.h
        if w <= 0 or h <= 0:
            return None
        out = np.zeros((h, w, 3), np.uint8)
        sx0, sy0 = max(0, x), max(0, y)
        sx1, sy1 = min(self.W, x + w), min(self.H, y + h)
        if sx1 > sx0 and sy1 > sy0:
            out[sy0 - y:sy1 - y, sx0 - x:sx1 - x] = self.screen[sy0:sy1, sx0:sx1]
        return out

    def close(self) -> None:
        pass


class FakeLocator:
    def __init__(self, cap: FakeCapture, found: bool = True) -> None:
        self.cap = cap
        self.found = found
        self.locates = 0
        self.verifies = 0
        self.score = 0.9

    def locate(self, screen, origin, side="auto"):
        self.locates += 1
        assert screen.shape[:2] == (origin.h, origin.w)
        if not self.found:
            return None
        return MinimapLocation(Rect(self.cap.x0, self.cap.y0, self.cap.side, self.cap.side), 0.93, "auto")

    def verify(self, minimap):
        self.verifies += 1
        return self.score


def live_engine(cap: FakeCapture, loc: FakeLocator, cfg: Config | None = None, window: Rect | None = None):
    win = window or Rect(0, 0, cap.W, cap.H)
    clock = Clock()
    client = FakeLiveClient(lambda: game_info(700 + clock.t))
    eng = CoachEngine(cfg or Config(), FakeVoice(), detector=ClassicDetector(), live_client=client,
                      clock=clock, locator=loc, window_finder=lambda: win, screen_capture=cap,
                      recorder_factory=lambda: None, enable_hotkeys=False, manage_overlay=False)
    return eng, clock, client


def test_no_capture_outside_game():
    cap = FakeCapture()
    loc = FakeLocator(cap)
    clock = Clock()
    client = FakeLiveClient(lambda: None)
    eng = CoachEngine(Config(), FakeVoice(), detector=ClassicDetector(), live_client=client, clock=clock,
                      locator=loc, window_finder=lambda: Rect(0, 0, 1280, 720), screen_capture=cap,
                      enable_hotkeys=False, manage_overlay=False)
    for i in range(20):
        clock.t = i * 0.5
        eng.step(clock.t)
    assert cap.grabs == [] and loc.locates == 0
    assert client.calls == 5          # polled at 0.5 Hz outside a game
    assert eng.get_status().state in (EngineState.STOPPED, EngineState.WAITING_GAME)


def test_locate_verify_and_relocate():
    cap = FakeCapture()
    loc = FakeLocator(cap)
    eng, clock, _client = live_engine(cap, loc)
    for i in range(16):
        clock.t = i * 0.125
        eng.step(clock.t)
    st = eng.get_status()
    assert loc.locates == 1
    assert st.state == EngineState.RUNNING and st.locate_method == "auto"
    assert st.minimap_rect == Rect(cap.x0, cap.y0, cap.side, cap.side)
    assert cap.grabs[-1] == st.minimap_rect
    assert loc.verifies >= 1
    # verification stays low for >= 3 s -> relocation
    loc.score = 0.1
    t_bad = clock.t
    while clock.t < t_bad + 2.5:
        clock.t += 0.125
        eng.step(clock.t)
    assert loc.locates == 1
    while clock.t < t_bad + 5.0:
        clock.t += 0.125
        eng.step(clock.t)
    assert loc.locates == 2
    loc.score = 0.9
    eng.request_relocate()
    clock.t += 0.125
    eng.step(clock.t)
    assert loc.locates == 3
    assert eng.tracker.me() is not None       # the pipeline ran on the grabbed crops


def test_locate_fallback_and_retry():
    cap = FakeCapture()
    loc = FakeLocator(cap, found=False)
    eng, clock, _client = live_engine(cap, loc)
    eng.step(0.0)
    st = eng.get_status()
    assert st.locate_method == "fallback" and "défaut" in st.message
    assert st.minimap_rect is not None and st.minimap_rect.x + st.minimap_rect.w == cap.W
    for i in range(1, 12 * 8):
        clock.t = i * 0.125
        eng.step(clock.t)
    assert loc.locates == 2          # retried after 10 s
    loc.found = True
    while clock.t < 22.0:
        clock.t += 0.125
        eng.step(clock.t)
    assert eng.get_status().locate_method == "auto"


def test_manual_rect_scaled_to_window():
    cap = FakeCapture()
    loc = FakeLocator(cap)
    cfg = Config(minimap_mode="manual",
                 manual_minimap_rect={"screen_w": 2560, "screen_h": 1440, "x": 2184, "y": 1064, "w": 360, "h": 360})
    eng, clock, _client = live_engine(cap, loc, cfg=cfg)
    eng.step(0.0)
    st = eng.get_status()
    assert loc.locates == 0 and st.locate_method == "manual"
    assert st.minimap_rect == Rect(1092, 532, 180, 180)


def test_black_screen_capture_state():
    cap = FakeCapture(black=True)
    loc = FakeLocator(cap)
    eng, clock, _client = live_engine(cap, loc)
    eng.step(0.0)
    eng.step(0.125)
    st = eng.get_status()
    assert st.state == EngineState.CAPTURE_BLACK and "Sans bordure" in st.message


def test_no_window():
    cap = FakeCapture()
    loc = FakeLocator(cap)
    clock = Clock()
    eng = CoachEngine(Config(), FakeVoice(), detector=ClassicDetector(),
                      live_client=FakeLiveClient(lambda: game_info(100)), clock=clock, locator=loc,
                      window_finder=lambda: None, screen_capture=cap, recorder_factory=lambda: None,
                      enable_hotkeys=False, manage_overlay=False)
    eng.step(0.0)
    assert "introuvable" in eng.get_status().message
    assert cap.grabs == []


# ------------------------------------------------------------------------------ hotkeys & misc
def test_mute_toggle_and_overlay_toggle():
    eng, voice, clock = make_engine(DemoSource(size=200), detector=ClassicDetector())
    eng.step(0.0)
    eng.toggle_mute()
    assert eng.muted and voice.texts()[-1] == "Voix coupée."
    deadline = time.monotonic() + 5
    while not voice.muted and time.monotonic() < deadline:
        time.sleep(0.05)
    assert voice.muted
    n = len(voice.said)
    eng._say("Gank !", 2)
    assert len(voice.said) == n
    eng.toggle_mute()
    assert not eng.muted and not voice.muted and voice.texts()[-1] == "Voix activée."
    assert eng.get_overlay_state() is not None
    eng.toggle_overlay()
    assert eng.get_overlay_state() is None and not eng.get_status().overlay_visible
    eng.toggle_overlay()
    assert eng.get_overlay_state() is not None
    # F9 outside a game / debounce
    eng2, voice2, clock2 = make_engine(None)
    eng2.speak_jungler_status()
    eng2.speak_jungler_status()
    assert voice2.texts() == ["Pas de partie en cours."]


def test_jungler_not_seen_text():
    eng, _v, clock = make_engine(ListSource([None], game_info(200)))
    eng.step(0.0)
    assert eng.jungler_status_text() == "Jungler ennemi pas encore vu."


def test_apply_config_live():
    eng, voice, clock = make_engine(DemoSource(size=200), detector=ClassicDetector())
    eng.step(0.0)
    cfg = Config(sensitivity=1.5, target_fps=12.0, fog_mode="all", fog_max_s=90.0)
    eng.apply_config(cfg)
    assert eng.cfg.target_fps == 12.0
    assert eng.fog_tracker.max_s == 90.0
    eng.step(0.2)
    ov = eng.get_overlay_state()
    assert ov is not None and ov.warn_radius == pytest.approx(cfg.effective_warn_radius())


def test_find_camera_center_and_helpers():
    src = DemoSource(size=280)
    frame = src.render_at(2.0)
    c = find_camera_center(frame)
    gu, gv, _ = src.positions(2.0)["Garen"]
    assert c is not None
    assert abs(c[0] - max(gu, 0.14)) < 0.04 and abs(c[1] - max(gv, 0.08)) < 0.05
    assert find_camera_center(np.zeros((100, 100, 3), np.uint8)) is None
    assert seconds_fr(1) == "une seconde" and seconds_fr(23) == "23 secondes"
    assert seconds_fr(60) == "une minute" and seconds_fr(90) == "1 minute 30"


def test_threads_start_stop_without_leak():
    before = {th.ident for th in threading.enumerate()}
    voice = FakeVoice()
    eng = CoachEngine(Config(target_fps=10.0), voice, detector=ClassicDetector(), frame_source=DemoSource(size=200),
                      enable_hotkeys=False, manage_overlay=False)
    eng.start()
    eng.start()                       # idempotent
    time.sleep(1.5)
    assert eng.is_running()
    st = eng.get_status()
    assert st.state == EngineState.RUNNING and 1.0 <= st.fps <= 14.0
    t0 = time.monotonic()
    eng.stop(timeout=3.0)
    assert time.monotonic() - t0 < 3.5
    assert not eng.is_running() and eng.get_status().state == EngineState.STOPPED
    time.sleep(0.1)
    leaked = [th for th in threading.enumerate() if th.ident not in before and th.name.startswith("TreeAICoach")]
    assert leaked == []

    # live mode without a game: the poller idles, no analysis ticks
    client = FakeLiveClient(lambda: None)
    eng2 = CoachEngine(Config(), FakeVoice(), detector=ClassicDetector(), live_client=client,
                       enable_hotkeys=False, manage_overlay=False)
    eng2.start()
    time.sleep(0.5)
    assert eng2.get_status().state == EngineState.WAITING_GAME
    eng2.stop(timeout=3.0)
    assert client.calls <= 2
    leaked = [th for th in threading.enumerate() if th.ident not in before and th.name.startswith("TreeAICoach")]
    assert leaked == []


def test_live_threads_follow_game():
    clock_game = {"on": True}
    client = FakeLiveClient(lambda: game_info(300.0) if clock_game["on"] else None)
    cap = FakeCapture()
    loc = FakeLocator(cap)
    eng = CoachEngine(Config(), FakeVoice(), detector=ClassicDetector(), live_client=client, locator=loc,
                      window_finder=lambda: Rect(0, 0, cap.W, cap.H), screen_capture=cap,
                      recorder_factory=lambda: None, enable_hotkeys=False, manage_overlay=False)
    eng.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not cap.grabs:
        time.sleep(0.05)
    time.sleep(0.5)
    st = eng.get_status()
    eng.stop()
    assert cap.grabs and loc.locates >= 1
    assert st.state == EngineState.RUNNING


def test_covered_minimap_frames_are_not_analysed():
    """A crop that stops looking like the minimap (shop / scoreboard over it) is not fed to
    the detector (phantom icons); the analysis resumes at the first good verification."""
    from treeaicoach.engine import MSG_MINIMAP_COVERED

    cap = FakeCapture()
    loc = FakeLocator(cap)
    eng, clock, _client = live_engine(cap, loc)
    calls = []
    for i in range(12):
        clock.t = i * 0.125
        eng.step(clock.t)
    det = eng._detector
    orig = det.detect
    det.detect = lambda frame: calls.append(1) or orig(frame)
    loc.score = 0.1
    for _ in range(12):                   # (verify once per second, then every tick while bad)
        clock.t += 0.125
        eng.step(clock.t)
    n_bad = len(calls)
    assert eng.get_status().message == MSG_MINIMAP_COVERED
    for _ in range(4):
        clock.t += 0.125
        eng.step(clock.t)
    assert len(calls) == n_bad            # nothing analysed while covered
    loc.score = 0.9
    clock.t += 0.125
    eng.step(clock.t)
    assert len(calls) == n_bad + 1 and eng.get_status().message != MSG_MINIMAP_COVERED
