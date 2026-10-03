"""Qt launcher (docs/LAUNCHER.md): pages, settings coverage, speed, threading, close, calibration.

Runs headless with ``QT_QPA_PLATFORM=offscreen`` (set below when no display is given)."""
from __future__ import annotations

import dataclasses
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from treeaicoach import ui  # noqa: E402
from treeaicoach.config import Config, load_config  # noqa: E402


class FakeVoice:
    backend = "fake"

    def __init__(self) -> None:
        self.said: list[str] = []
        self.params: dict = {}
        self.stopped = False

    def say(self, text: str, prio: int = 0) -> None:
        self.said.append(text)

    def set_params(self, **kw: object) -> None:
        self.params = kw

    def list_voices(self) -> list[str]:
        return ["Microsoft Hortense"]

    def stop(self) -> None:
        self.stopped = True


class FakeEngine:
    def __init__(self, *a: object, slow_stop: float = 0.0) -> None:
        self.running = False
        self.applied = 0
        self.stopped = 0
        self.muted = False
        self._slow = slow_stop

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        time.sleep(self._slow)
        self.running = False
        self.stopped += 1

    def is_running(self) -> bool:
        return self.running

    def apply_config(self, cfg: Config) -> None:
        self.applied += 1

    def get_status(self) -> SimpleNamespace:
        return SimpleNamespace(state=SimpleNamespace(value="RUNNING"), message="Analyse en cours", game_time=95.0,
                               fps=12.0, enemies_visible=2, last_alert="", detector="onnx", voice="fake",
                               minimap_rect=(1, 2, 3, 4), locate_method="auto")

    def get_overlay_state(self) -> None:
        return None

    def request_relocate(self) -> None:
        pass

    def mute(self, on: bool) -> None:
        self.muted = on


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TREEAICOACH_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(ui, "_report_function", lambda name: (lambda n=50: []) if name == "list_games" else None)
    return tmp_path


def _pump(app: ui.CoachApp, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.qapp.processEvents()
        app._dispatcher.drain()
        time.sleep(0.005)


def _build(tmp_path: Path, **kw: object) -> tuple[ui.CoachApp, FakeVoice, list[FakeEngine]]:
    engines: list[FakeEngine] = []

    def factory(cfg: Config, voice: object, det: object, src: object) -> FakeEngine:
        e = FakeEngine()
        engines.append(e)
        return e

    voice = FakeVoice()
    cfg = Config(ui_onboarding_done=True, ui_seen_changelog="99", lcu_enabled=False)
    app = ui.CoachApp(cfg, engine_factory=factory, overlay_factory=lambda c, p: None, voice=voice,
                      detector_factory=lambda c: None, hotkeys=False, save_path=tmp_path / "config.json", **kw)
    app._no_dialogs = True
    return app, voice, engines


def test_geometry_helpers() -> None:
    assert ui.parse_geometry("1200x800+10+-5") == (1200, 800, 10, -5)
    assert ui.parse_geometry("1200x800") == (1200, 800, None, None)
    assert ui.parse_geometry("nope") is None
    scr = (0, 0, 1280, 720)
    # too big for a 1280 x 720 screen: shrunk and kept on screen
    assert ui.fit_geometry((1920, 1080, 0, 0), scr) == (0, 0, 1280, 720)
    # off-screen position (monitor unplugged): centred
    x, y, w, h = ui.fit_geometry((1000, 640, 3000, 100), scr)
    assert (w, h) == (1000, 640) and 0 <= x and x + w <= 1280
    assert ui.fit_geometry(None, scr)[2:] == (ui.DEFAULT_W, 720)


def test_every_setting_is_reachable(home: Path) -> None:
    """Each Config field has a control, or is listed in HIDDEN_SETTINGS with the reason."""
    app, _v, _e = _build(home)
    try:
        app.build_all_pages()
        assert not app._failed_pages
        names = {f.name for f in dataclasses.fields(Config)}
        missing = sorted(n for n in names if n not in app._refreshers and n not in ui.HIDDEN_SETTINGS)
        assert not missing, missing
        assert not [n for n in ui.HIDDEN_SETTINGS if n not in names]
    finally:
        app.close()


def test_pages_switch_fast_and_status_updates(home: Path) -> None:
    app, voice, engines = _build(home)
    try:
        app.show()
        _pump(app, 0.5)                           # backend started on a worker, pages prebuilt
        assert engines and app.engine is engines[0] and engines[0].running
        _pump(app, 1.0)
        assert set(app.pages) == {k for k, _l, _i in ui.PAGES}
        app._switch_ms.clear()
        for _ in range(3):
            for key, _l, _i in ui.PAGES:
                app.show_page(key)
                app.qapp.processEvents()
        assert max(app._switch_ms) < 50, app._switch_ms
        app.show_page("home")
        app._refresh_status()
        view = app.page_views["home"]
        assert view.title.text().startswith("En jeu") and "1:35" in view.live.text()
        app.show_page("dashboard")                 # old page names still land somewhere sensible
        assert app._current_page == "home"
        app.show_page("settings", "Voix")
        assert app._current_page == "alerts"
    finally:
        app.close()


def test_setting_change_applies_live_and_saves(home: Path) -> None:
    app, voice, engines = _build(home)
    try:
        _pump(app, 0.5)
        app.build_all_pages()
        app.set_option("voice_volume", 40)
        assert voice.params.get("volume") == 40
        app.set_option("overlay_mode", "radar")
        assert app.page_views["overlay"].radar.isVisibleTo(app.page_views["overlay"].page)
        _pump(app, 0.7)                           # debounced save
        assert load_config(home / "config.json").voice_volume == 40
        app.apply_skill_level("debutant")
        assert app.cfg.skill_level == "debutant"
        assert app.page_views["home"].level.value() == "debutant"
    finally:
        app.close()


def test_callbacks_after_close_are_dropped(home: Path) -> None:
    app, voice, engines = _build(home)
    _pump(app, 0.3)
    hit: list[int] = []
    gate = threading.Event()
    app.run_job(lambda: gate.wait(2), lambda _r: hit.append(1))
    app.close()
    gate.set()
    time.sleep(0.1)
    app._dispatcher.drain()
    assert not hit
    assert all(not e.running for e in engines)
    app.later(0, lambda: hit.append(2))           # nothing scheduled after close runs
    app.show_page("about")                        # no page built after close
    app.qapp.processEvents()
    assert not hit


def test_engine_failure_keeps_window_open(home: Path) -> None:
    def boom(*_a: object) -> object:
        raise RuntimeError("no engine")

    app = ui.CoachApp(Config(ui_onboarding_done=True), engine_factory=boom, overlay_factory=lambda c, p: None,
                      voice=FakeVoice(), detector_factory=lambda c: None, hotkeys=False,
                      save_path=home / "c.json")
    try:
        _pump(app, 0.5)
        assert app.engine is None and "moteur" in (app.engine_error or "")
        assert app.status_snapshot()["key"] == "NO_ENGINE"
        app.toggle_engine()                      # retry: no crash
        _pump(app, 0.3)
    finally:
        app.close()


def test_run_app_smoke(home: Path) -> None:
    rc = ui.run_app(Config(ui_onboarding_done=True), smoke_seconds=0.5,
                    _engine_factory=lambda *a: FakeEngine(), _overlay_factory=lambda c, p: None, _voice=FakeVoice(),
                    _detector_factory=lambda c: None, _hotkeys=False, _save_path=home / "c.json")
    assert rc == 0


def test_calibration_dialog_validates(home: Path) -> None:
    from treeaicoach import calibration as cal

    ui.ensure_qapp()
    img = np.zeros((1080, 1920, 3), np.uint8)
    origin = SimpleNamespace(x=0, y=0, w=1920, h=1080)
    dlg = cal.CalibrationDialog(None, Config(), img, origin)
    d = dlg.dialog
    d.press(1600, 780)
    d.motion(1880, 1060)
    d.release()
    d.validate()
    assert d.result_rect == {"screen_w": 1920, "screen_h": 1080, "x": 1600, "y": 780, "w": 280, "h": 280}
