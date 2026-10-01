"""Tests of treeaicoach.ui and treeaicoach.calibration.

Pure helpers are tested everywhere. GUI tests build the real window with a FAKE engine
(animated in-game data rendered by overlay_render) and are skipped cleanly when there is no
display / no Tk (headless CI without Xvfb).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import os
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from treeaicoach import calibration, ui
from treeaicoach.config import Config, load_config


# ======================================================================================
# Fake engine (implements the CoachEngine contract, ARCHITECTURE.md §4.15 / §7.5)
# ======================================================================================
class FakeState(str, enum.Enum):
    STOPPED = "stopped"
    WAITING_GAME = "waiting_game"
    RUNNING = "running"


@dataclasses.dataclass
class FakeStatus:
    state: Any
    message: str
    fps: float
    game_time: float | None
    minimap_rect: Any
    enemies_visible: int
    last_alert: str | None
    detector: str
    voice: str
    muted: bool = False
    banner: str | None = None


class FakeEngine:
    """Thread-safe stand-in: cycles through overlay_render.sample_states() while running."""

    ORDER = ("safe", "warning", "danger", "late")

    def __init__(self, cfg: Config, voice: Any = None, detector: Any = None, frame_source: Any = None,
                 period: float = 2.0, in_game: bool = True) -> None:
        from treeaicoach import overlay_render

        self.cfg = cfg
        self.voice = voice
        self.demo = frame_source is not None
        self._states = overlay_render.sample_states()
        self._lock = threading.Lock()
        self._running = False
        self._t0 = time.monotonic()
        self.period = period
        self.in_game = in_game
        self.calls: dict[str, int] = {}
        self.muted = False
        self.banner: str | None = None
        self.fixed_index: int | None = None

    def _count(self, name: str) -> None:
        with self._lock:
            self.calls[name] = self.calls.get(name, 0) + 1

    def start(self) -> None:
        self._count("start")
        with self._lock:
            self._running = True
            self._t0 = time.monotonic()

    def stop(self, timeout: float = 3.0) -> None:
        self._count("stop")
        with self._lock:
            self._running = False

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def _index(self) -> int:
        if self.fixed_index is not None:
            return self.fixed_index
        return int((time.monotonic() - self._t0) / self.period) % len(self.ORDER)

    def get_status(self) -> FakeStatus:
        with self._lock:
            running = self._running
        if not running:
            return FakeStatus(FakeState.STOPPED, "Analyse arrêtée.", 0.0, None, None, 0, None, "onnx", "sapi")
        if not self.in_game:
            return FakeStatus(FakeState.WAITING_GAME, "En attente d'une partie de League of Legends…",
                              0.0, None, None, 0, None, "onnx", "sapi")
        st = self._states[self.ORDER[self._index()]]
        nvis = sum(1 for e in st.enemies if e.visible)
        la = st.last_alert[0] if st.last_alert else None
        return FakeStatus(FakeState.RUNNING, "Minimap trouvée — analyse en cours (1920 × 1080).", 7.9,
                          (st.game_time or 0.0) + (time.monotonic() - self._t0) % 1.0, None, nvis, la,
                          "onnx", "sapi", self.muted, self.banner)

    def get_overlay_state(self) -> Any:
        with self._lock:
            running = self._running
        if not running or not self.in_game:
            return None
        st = self._states[self.ORDER[self._index()]]
        if st.last_alert:       # the alert "happened" when this state began
            age = (time.monotonic() - self._t0) % self.period
            st = dataclasses.replace(st, last_alert=(st.last_alert[0], st.last_alert[1], age))
        return st

    def get_preview(self) -> np.ndarray | None:
        return None

    def request_relocate(self) -> None:
        self._count("request_relocate")

    def apply_config(self, cfg: Config) -> None:
        self._count("apply_config")
        self.cfg = cfg

    def step(self, t: float) -> list:
        return []

    def jungler_status_text(self) -> str:
        return "Lee Sin vu il y a 14 secondes dans la rivière du haut."

    def mute(self, on: bool) -> None:
        self.muted = bool(on)

    def toggle_overlay(self) -> None:
        self._count("toggle_overlay")


class FakeVoice:
    backend = "sapi"

    def __init__(self) -> None:
        self.said: list[tuple[str, int]] = []
        self.params: list[dict] = []
        self.stopped = False

    def start(self) -> None:
        pass

    def stop(self) -> None:
        self.stopped = True

    def say(self, text: str, level: int = 1) -> None:
        self.said.append((text, level))

    def list_voices(self) -> list[str]:
        return ["Microsoft Hortense - French (France)", "Microsoft Paul - French (France)"]

    def set_params(self, **kw: Any) -> None:
        self.params.append(kw)


class FakeOverlay:
    def __init__(self, cfg: Config, provider: Any) -> None:
        self.cfg = cfg
        self.provider = provider
        self.started = self.stopped = False
        self.move: list[bool] = []
        self.applied = 0

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def apply_config(self, cfg: Config) -> None:
        self.applied += 1
        self.cfg = cfg

    def set_move_mode(self, on: bool) -> None:
        self.move.append(on)

    def set_on_moved(self, cb: Any) -> None:
        self.on_moved = cb


# ======================================================================================
# Pure helpers (no display needed)
# ======================================================================================
def test_fmt_helpers() -> None:
    assert ui.fmt_clock(None) == "--:--"
    assert ui.fmt_clock(-3) == "--:--"
    assert ui.fmt_clock(float("nan")) == "--:--"
    assert ui.fmt_clock(84.9) == "1:24"
    assert ui.fmt_clock(3723) == "1:02:03"
    assert ui.fmt_int_fr(3300) == "3 300"
    assert ui.fmt_int_fr("x") == "?"
    assert ui.fmt_decimal_fr(1.25, 1) in ("1,2", "1,3")


def test_state_key_normalizes() -> None:
    assert ui.state_key(FakeState.RUNNING) == "RUNNING"
    assert ui.state_key("waiting_game") == "WAITING_GAME"
    assert ui.state_key("EngineState.LOCATING") == "LOCATING"
    assert ui.state_key(None) == "STOPPED"


def test_session_stats_and_game_fields() -> None:
    today = dt.date(2026, 9, 30)
    games = [
        {"summary": {"start": "2026-09-30T20:10:00", "champion": "Garen", "result": "Win", "kills": 5,
                     "deaths": 2, "assists": 7, "ganks": 4, "ganks_survived": 3}},
        {"start": "2026-09-30T18:00:00", "champion": "Darius", "result": "Lose", "deaths": 6,
         "ganks": 2, "ganks_survived": 0},
        {"start": "2026-09-28T18:00:00", "champion": "Ahri", "result": None, "deaths": 1},
    ]
    st = ui.session_stats(games, today=today)
    assert st["scope"] == "Aujourd'hui"
    assert st["games"] == 2 and st["wins"] == 1
    assert st["deaths_per_game"] == pytest.approx(4.0)
    assert st["ganks"] == 6 and st["ganks_avoided"] == 3
    assert ui.game_result(games[0]) == "win" and ui.game_result(games[2]) is None
    assert ui.fmt_game_date(ui.game_datetime(games[0]), today).startswith("Aujourd'hui 20:10")
    assert ui.fmt_game_date(ui.game_datetime(games[2]), today) == "28/09 18:00"
    empty = ui.session_stats([], today=today)
    assert empty["games"] == 0 and empty["winrate"] is None


def test_autostart_not_supported_off_windows_or_unfrozen() -> None:
    ok, reason = ui.autostart_support()
    if os.name != "nt" or not getattr(__import__("sys"), "frozen", False):
        assert not ok and reason
        assert ui.set_windows_autostart(True) is False
    assert ui.autostart_command().startswith('"')


def test_images_and_icon() -> None:
    icon = np.zeros((64, 64, 4), np.uint8)
    icon[..., 0] = 200
    icon[..., 3] = 255
    im = ui.circle_icon(icon, 44, ui.DANGER)
    assert im.size == (44, 44) and im.mode == "RGB"
    assert ui.circle_icon(None, 44, None, grey=True).size == (44, 44)
    for kind in ("dashboard", "voice", "overlay", "analysis", "settings", "help", "play", "stop", "target",
                 "demo", "folder", "report", "refresh", "move"):
        assert ui.nav_icon(kind, 18).size == (18, 18)
    assert ui.load_logo(40).size == (40, 40)
    assert ui.app_icon_path("png") is not None      # packaging/icon.png is in the repository


def test_calibration_geometry_helpers() -> None:
    from treeaicoach.capture import Rect

    # drag towards bottom-right / top-left, clamped to the image
    assert calibration.constrain_square(10, 10, 60, 30, 100, 100) == (10, 10, 50)
    x, y, s = calibration.constrain_square(50, 50, 0, 20, 100, 100)
    assert (x, y, s) == (0, 0, 50)
    assert calibration.constrain_square(90, 90, 200, 200, 100, 100) == (90, 90, 10)
    assert calibration.fit_scale(1920, 1080, 960, 1080) == pytest.approx(0.5)
    assert calibration.fit_scale(100, 100, 500, 500) == 1.0
    origin = Rect(100, 50, 1920, 1080)
    sel = calibration.Selection(1650.4, 810.6, 255.2)
    rect = calibration.selection_to_rect(sel, origin)
    assert rect == {"screen_w": 1920, "screen_h": 1080, "x": 1750, "y": 861, "w": 255, "h": 255}
    back = calibration.rect_to_selection(rect, origin)
    assert back is not None and back.x == pytest.approx(1650) and back.side == pytest.approx(255)
    assert calibration.rect_to_selection(dict(rect, screen_w=1280), origin) is None
    cfg = dataclasses.replace(Config(), manual_minimap_rect=rect, minimap_mode="manual").validated()
    assert cfg.manual_minimap_rect == rect and cfg.minimap_mode == "manual"
    c = calibration.clamp_selection(calibration.Selection(-5, 2000, 300), 1920, 1080)
    assert c.x == 0 and c.y == 1080 - 300


# ======================================================================================
# GUI tests
# ======================================================================================
def _display_ok() -> bool:
    try:
        import tkinter as tk
    except Exception:
        return False
    if os.name != "nt" and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return False
    try:
        r = tk.Tk()
        r.withdraw()
        r.destroy()
        return True
    except Exception:
        return False


needs_display = pytest.mark.skipif(not _display_ok(), reason="no display / Tk available")


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TREEAICOACH_HOME", str(tmp_path / "home"))
    from treeaicoach import paths

    paths._reset_cache()
    yield tmp_path
    paths._reset_cache()


def _pump(app: Any, seconds: float, until: Any = None) -> None:
    """Run the Tk loop for ``seconds`` (or until ``until()`` becomes true)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.root.update()
        if until is not None:
            try:
                if until():
                    app.root.update()
                    return
            except Exception:
                pass
        time.sleep(0.01)


def _build(tmp_path: Path, **kw: Any) -> tuple[Any, FakeVoice, list]:
    pytest.importorskip("customtkinter")
    voice = FakeVoice()
    engines: list[FakeEngine] = []
    overlays: list[FakeOverlay] = []

    def factory(cfg: Config, v: Any, det: Any, src: Any) -> FakeEngine:
        e = FakeEngine(cfg, v, det, src, period=0.4)
        engines.append(e)
        return e

    def ov_factory(cfg: Config, provider: Any) -> FakeOverlay:
        o = FakeOverlay(cfg, provider)
        overlays.append(o)
        return o

    cfg = kw.pop("cfg", Config())
    app = ui.CoachApp(cfg, engine_factory=factory, overlay_factory=ov_factory, voice=voice,
                      detector_factory=lambda c: None, demo_source_factory=lambda: object(),
                      hotkeys=False, save_path=tmp_path / "config.json", **kw)
    return app, voice, [engines, overlays]


@needs_display
def test_app_pages_and_settings(home: Path, tmp_path: Path) -> None:
    app, voice, (engines, overlays) = _build(tmp_path)
    try:
        _pump(app, 4.0, lambda: app._radar_live and app._journal and app.clock_lbl.cget("text") != "--:--")
        assert engines and engines[0].is_running()          # autostart
        assert overlays and overlays[0].started
        assert overlays[0].provider() is not None            # overlay state provider -> engine snapshot
        assert app.state_title.cget("text") == "Analyse en cours"
        assert app.clock_lbl.cget("text") != "--:--"
        assert app._radar_live                               # radar preview rendered by the worker
        assert app.threat_lbl.cget("text") in ("SÛR", "ATTENTION", "DANGER")
        assert any(s["sig"] and s["sig"][0] for s in app.enemy_slots)
        assert len(app._journal) >= 1                        # alerts collected from the overlay state
        for key, _label, _icon in ui.PAGES:
            app.show_page(key)
            _pump(app, 0.1)
        app.show_page("dashboard")

        # settings callbacks -> cfg validated + applied live + saved (debounced)
        n_apply = engines[0].calls.get("apply_config", 0)
        app.set_option("sensitivity", 1.4)
        app.set_option("voice_rate", 5)
        app.set_option("radar_scale", 7.0)                  # clamped by validation
        app.set_option("fog_mode", "all")
        app.set_option("hotkey_mute", "F7")
        assert app.cfg.sensitivity == pytest.approx(1.4) and app.cfg.radar_scale == 2.0
        assert engines[0].calls["apply_config"] >= n_apply + 4
        assert overlays[0].applied >= 4
        assert voice.params and voice.params[-1]["rate"] == 5
        assert "4\u202f600" in app.radius_lbl.cget("text")      # 0.22 x 1.4 x 14 870
        _pump(app, 3.0, lambda: load_config(tmp_path / "config.json").fog_mode == "all")
        saved = load_config(tmp_path / "config.json")
        assert saved.sensitivity == pytest.approx(1.4) and saved.fog_mode == "all"

        app.test_voice()
        assert voice.said
        app.relocate()
        assert engines[0].calls.get("request_relocate") == 1
        app._hk_jungler()
        assert "Lee Sin" in voice.said[-1][0]
        app._hk_mute()
        assert engines[0].muted
        app.toggle_move_mode()
        overlays[0].on_moved("radar", 1500, 500)          # called from the overlay thread
        threading.Thread(target=overlays[0].on_moved, args=("hud", 20, 40)).start()
        app.toggle_move_mode()
        _pump(app, 2.0, lambda: app.cfg.hud_xy == [20, 40])
        assert overlays[0].move == [True, False]
        assert app.cfg.radar_position == "custom" and app.cfg.radar_xy == [1500, 500]
        assert app.cfg.hud_position == "custom" and app.cfg.hud_xy == [20, 40]
        engines[0].banner = "3 défaites d'affilée : une pause de 10 minutes aide à rester concentré."
        _pump(app, 2.0, lambda: app.banner.grid_info())
        assert app.banner.grid_info() and "pause" in app.banner_lbl.cget("text")
        app._dismiss_banner()
        _pump(app, 0.3)
        assert not app.banner.grid_info()

        # start / stop from the big button
        app.toggle_engine()
        _pump(app, 3.0, lambda: not app._busy and app.btn_start.cget("text") == "Démarrer l'analyse")
        assert not engines[0].is_running()
        assert app.btn_start.cget("text") == "Démarrer l'analyse"
        app.toggle_engine()
        _pump(app, 3.0, lambda: not app._busy and engines[0].is_running())
        assert engines[0].is_running()

        # demo mode: a new engine with a frame source, then back to real mode
        app.toggle_demo()
        _pump(app, 3.0, lambda: not app._busy and app.engine is engines[-1] and len(engines) == 2)
        assert len(engines) == 2 and engines[1].demo and engines[1].is_running() and not engines[0].is_running()
        assert app.btn_demo.cget("text") in ("Quitter la démo", "Fin démo")
        app.toggle_demo()
        _pump(app, 3.0, lambda: not app._busy and app.engine is engines[-1] and len(engines) == 3)
        assert len(engines) == 3 and not engines[2].demo

        # detector backend change -> engine rebuilt
        app.set_option("detector_backend", "classic")
        _pump(app, 3.0, lambda: not app._busy and app.engine is engines[-1] and len(engines) == 4)
        assert len(engines) == 4 and engines[3].is_running()

        # a failing callback never propagates: it shows a toast
        app.cb(lambda: 1 / 0)()
        assert app._toast_frame is not None

        app.reset_settings()
        assert app.cfg.sensitivity == pytest.approx(1.0)
    finally:
        app.close()
    assert engines[-1].calls.get("stop", 0) >= 1 and overlays[0].stopped
    saved = load_config(tmp_path / "config.json")
    assert saved.ui_geometry                                 # window geometry remembered


@needs_display
def test_analysis_page_lists_games(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    games = [
        {"path": str(tmp_path / "g1.json"), "start": dt.datetime.now().isoformat(timespec="seconds"),
         "champion": "Garen", "champion_name": "Garen", "result": "Win", "kills": 7, "deaths": 1,
         "assists": 9, "ganks": 3, "ganks_survived": 3, "duration": 1690, "position": "TOP"},
        {"path": str(tmp_path / "g2.json"), "start": "2026-09-20T21:00:00", "champion": "LeeSin",
         "champion_name": "Lee Sin", "result": "Lose", "kills": 2, "deaths": 8, "assists": 4, "ganks": 5,
         "ganks_survived": 1, "duration": 1500, "position": "JUNGLE"},
    ]
    written: list[Path] = []
    opened: list[str] = []

    def fake_write_report(p: Path) -> Path:
        out = Path(p).with_suffix(".html")
        out.write_text("<html></html>", encoding="utf-8")
        written.append(out)
        return out

    real = ui._report_function
    monkeypatch.setattr(ui, "_report_function", lambda name: {"list_games": lambda n=50: games,
                                                              "write_report": fake_write_report}.get(name)
                        or real(name))
    monkeypatch.setattr(ui.webbrowser, "open", lambda url: opened.append(url))
    app, _voice, _ = _build(tmp_path)
    try:
        app.show_page("analysis")
        _pump(app, 3.0, lambda: app._games == games)
        assert app._games == games
        assert app.stat_labels["games"][0].cget("text") in ("1", "2")
        app.open_report(games[0])
        _pump(app, 3.0, lambda: opened)
        assert written and opened and opened[0].startswith("file:")
    finally:
        app.close()


@needs_display
def test_engine_failure_keeps_ui_open(home: Path, tmp_path: Path) -> None:
    pytest.importorskip("customtkinter")

    def broken(*_a: Any) -> Any:
        raise RuntimeError("module engine absent")

    app = ui.CoachApp(Config(), engine_factory=broken, overlay_factory=lambda c, p: None, voice=FakeVoice(),
                      detector_factory=lambda c: None, hotkeys=False, save_path=tmp_path / "c.json")
    try:
        _pump(app, 3.0, lambda: not app._busy and app.state_title.cget("text") == "Moteur indisponible")
        assert app.engine is None and app.engine_error
        assert app.state_title.cget("text") == "Moteur indisponible"
        assert "module engine absent" in app.state_msg.cget("text")
        app.toggle_engine()          # retries, fails again, no exception
        _pump(app, 2.0, lambda: not app._busy)
    finally:
        app.close()


@needs_display
def test_run_app_smoke(home: Path, tmp_path: Path) -> None:
    pytest.importorskip("customtkinter")
    engines: list[FakeEngine] = []

    def factory(cfg: Config, v: Any, det: Any, src: Any) -> FakeEngine:
        e = FakeEngine(cfg, v, det, src)
        engines.append(e)
        return e

    t0 = time.monotonic()
    rc = ui.run_app(Config(), demo=True, smoke_seconds=1.0, _engine_factory=factory,
                    _overlay_factory=lambda c, p: None, _voice=FakeVoice(), _detector_factory=lambda c: None,
                    _demo_source_factory=lambda: object(), _hotkeys=False, _save_path=tmp_path / "c.json")
    assert rc == 0
    assert time.monotonic() - t0 < 10
    assert engines and engines[0].demo and not engines[0].is_running()
    assert (tmp_path / "c.json").is_file()


@needs_display
def test_run_app_with_real_engine_demo(home: Path, tmp_path: Path) -> None:
    """Integration: the real CoachEngine on the DemoSource drives the dashboard."""
    pytest.importorskip("customtkinter")
    pytest.importorskip("treeaicoach.engine")
    voice = FakeVoice()
    seen: dict[str, Any] = {}
    orig_close = ui.CoachApp.close

    def spy_close(self: Any) -> None:
        seen["engine"] = type(self.engine).__name__
        seen["radar"] = self._radar_live
        seen["state"] = self.state_title.cget("text")
        seen["clock"] = self.clock_lbl.cget("text")
        orig_close(self)

    ui.CoachApp.close = spy_close          # type: ignore[method-assign]
    try:
        rc = ui.run_app(Config(), demo=True, smoke_seconds=4.5, _voice=voice, _hotkeys=False,
                        _overlay_factory=lambda c, p: None, _save_path=tmp_path / "c.json")
    finally:
        ui.CoachApp.close = orig_close     # type: ignore[method-assign]
    assert rc == 0
    assert seen["engine"] == "CoachEngine"
    assert seen["state"] in ("Analyse en cours", "Recherche de la minimap")
    assert seen["clock"] != "--:--" and seen["radar"]


@needs_display
def test_calibration_dialog(tmp_path: Path) -> None:
    ctk = pytest.importorskip("customtkinter")
    from treeaicoach.capture import Rect

    root = ctk.CTk()
    try:
        img = np.full((540, 960, 3), 40, np.uint8)
        img[400:530, 820:950] = (90, 140, 60)            # a fake "minimap" in the bottom-right corner
        origin = Rect(0, 0, 960, 540)
        dlg = calibration.CalibrationDialog(root, Config(), img, origin)
        root.update()
        dlg.top.update()
        assert str(dlg.btn_ok.cget("state")) == "disabled"
        # simulate a drag in canvas coordinates
        cx0, cy0 = dlg._to_canvas(820, 400)
        cx1, cy1 = dlg._to_canvas(950, 520)
        ev = type("E", (), {})
        for fn, (x, y) in ((dlg._on_press, (cx0, cy0)), (dlg._on_motion, (cx1, cy1)), (dlg._on_release, (cx1, cy1))):
            e = ev()
            e.x, e.y = x, y
            fn(e)
        assert dlg.sel is not None and dlg.sel.side == pytest.approx(130, abs=3)
        dlg._nudge(1, 0)
        dlg._grow(2)
        dlg.validate()
        r = dlg.result
        assert r is not None and r["screen_w"] == 960 and r["w"] == r["h"] and r["w"] >= 128
        assert Config(manual_minimap_rect=r).validated().manual_minimap_rect == r

        # run_calibration with an injected screenshot, cancelled by Escape-equivalent
        root.after(300, lambda: [w.destroy() for w in root.winfo_children()
                                 if w.winfo_class() in ("Toplevel", "CTkToplevel")])
        assert calibration.run_calibration(root, Config(), screenshot=(img, origin)) is None

        # no capture -> error message, validate refused
        dlg2 = calibration.CalibrationDialog(root, Config(), None, origin)
        root.update()
        dlg2.validate()
        assert dlg2.result is None
        dlg2.cancel()
    finally:
        root.destroy()
