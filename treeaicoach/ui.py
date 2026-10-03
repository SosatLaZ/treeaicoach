"""Launcher of TreeAI Coach (Qt Widgets, docs/LAUNCHER.md), French, Apple System Settings feel.

Public entry point: :func:`run_app`. It creates the voice, the detector, the analysis engine
(:mod:`treeaicoach.engine`, imported lazily) and the overlay manager, shows the main window and
returns 0 when the window is closed (everything stopped, configuration saved).

Threading rules
    * Widgets are only touched from the Qt main thread. Every slow thing (engine start / stop,
      LCU probe, updater, report list, preview rendering, champion select) runs on a worker thread
      (:class:`ui_common._Dispatcher`); its result comes back through a queue drained by a 30 ms
      ``QTimer`` on the main thread. Hotkeys (their own thread) only ``post`` callbacks.
    * The overlay keeps its own thread and raw Win32 layered windows (overlay.py): Qt never
      touches them, the overlay never touches Qt.
    * Every user callback is guarded: an exception is logged and shown as a French toast.

Pages: the home page is built before the window is shown; the others are built one per event
loop turn right after the first paint (``_prebuild``), each in well under 50 ms on a gaming PC; a
click before that builds the page at once. A page switch is ``QStackedWidget.setCurrentWidget``:
nothing is destroyed, rebuilt or re-laid out (docs/LAUNCHER.md, measurements).

Every setting change -> ``cfg`` updated + validated, saved after 500 ms (debounced) and applied
live (``engine.apply_config`` / ``overlay.apply_config`` / ``voice.set_params``, hotkeys rebound).
"""

from __future__ import annotations

import dataclasses
import functools
import logging
import os
import re
import sys
import threading
import time
import webbrowser  # noqa: F401 - tests patch ui.webbrowser.open
from pathlib import Path
from typing import Any, Callable, Sequence

from treeaicoach import APP_NAME, __version__, paths, ui_kit
from treeaicoach.config import Config, save_config
from treeaicoach.ui_common import (  # noqa: F401 - re-exported for tools / tests
    DETECTOR_FIELDS,
    ENGINE_RESTART_FIELDS,
    HIDDEN_SETTINGS,
    HOTKEY_FIELDS,
    PAGE_ALIASES,
    PAGES,
    PREBUILD_ORDER,
    SAVE_DEBOUNCE_MS,
    STATUS_ICONIC_MS,
    STATUS_IDLE_MS,
    STATUS_MS,
    TOAST_MS,
    UPDATE_CHECK_DELAY_MS,
    VOICE_FIELDS,
    _Dispatcher,
    _game_json_path,
    app_icon_path,
    autostart_support,
    champion_name,
    fmt_clock,
    fmt_int_fr,
    get_windows_autostart,
    session_stats,
    set_windows_autostart,
    state_key,
    ui_scale_of,
)

log = logging.getLogger(__name__)

DISPATCH_MS = 30
MIN_W, MIN_H = 960, 600          # usable down to 1280 x 720 (and a bit below)
DEFAULT_W, DEFAULT_H = 1180, 760
EngineFactory = Callable[[Config, Any, Any, Any], Any]


def _guarded(method: Callable[..., Any]) -> Callable[..., Any]:
    """App callbacks: log + French toast instead of an exception; nothing after close."""
    @functools.wraps(method)
    def wrapper(self: "CoachApp", *args: Any, **kwargs: Any) -> Any:
        if self._closing:
            return None
        try:
            return method(self, *args, **kwargs)
        except Exception as exc:
            if self._closing:
                return None
            log.exception("UI action %s failed", method.__name__)
            try:
                self.show_error(f"Une erreur est survenue : {exc}")
            except Exception:
                pass
            return None
    return wrapper


def parse_geometry(text: Any) -> tuple[int, int, int | None, int | None] | None:
    """``"WxH"`` / ``"WxH+X+Y"`` (also negative offsets) -> (w, h, x, y)."""
    m = re.fullmatch(r"\s*(\d+)x(\d+)(?:([+-]-?\d+)([+-]-?\d+))?\s*", str(text or ""))
    if not m:
        return None
    w, h = int(m.group(1)), int(m.group(2))
    x = int(m.group(3).replace("+-", "-").lstrip("+")) if m.group(3) else None
    y = int(m.group(4).replace("+-", "-").lstrip("+")) if m.group(4) else None
    return w, h, x, y


def fit_geometry(geo: tuple[int, int, int | None, int | None] | None, screen: tuple[int, int, int, int],
                 default: tuple[int, int] = (DEFAULT_W, DEFAULT_H),
                 minimum: tuple[int, int] = (MIN_W, MIN_H)) -> tuple[int, int, int, int]:
    """Window rect (x, y, w, h) fully on ``screen`` (x, y, w, h): saved size if it fits, else the
    default; centred when the saved position is unknown or off screen."""
    sx, sy, sw, sh = screen
    w, h = (geo[0], geo[1]) if geo else default
    w = max(min(minimum[0], sw), min(w, sw))
    h = max(min(minimum[1], sh), min(h, sh))
    x, y = (geo[2], geo[3]) if geo else (None, None)
    if x is None or y is None or x < sx - 8 or y < sy - 8 or x + w > sx + sw + 8 or y + h > sy + sh + 8:
        x, y = sx + (sw - w) // 2, sy + (sh - h) // 2
    return int(x), int(y), int(w), int(h)


def _qt() -> Any:
    from PySide6 import QtCore, QtGui, QtWidgets  # noqa: PLC0415 - imported only for the window

    return QtCore, QtGui, QtWidgets


def ensure_qapp(argv: Sequence[str] | None = None) -> Any:
    """The QApplication (created once; per-monitor DPI handled by Qt 6)."""
    QtCore, QtGui, QtWidgets = _qt()
    app = QtWidgets.QApplication.instance()
    if app is None:
        QtGui.QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        app = QtWidgets.QApplication(list(argv or [sys.argv[0] if sys.argv else APP_NAME]))
        app.setApplicationName(APP_NAME)
        app.setApplicationVersion(__version__)
        try:
            app.setStyle("Fusion")
        except Exception:
            pass
    return app


class CoachApp:
    """Main window + orchestration of engine / overlay / voice. Build on the Qt main thread."""

    def __init__(self, cfg: Config, *, demo: bool = False,
                 engine_factory: EngineFactory | None = None,
                 overlay_factory: Callable[[Config, Callable[[], Any]], Any] | None = None,
                 voice: Any = None, detector_factory: Callable[[Config], Any] | None = None,
                 demo_source_factory: Callable[[], Any] | None = None,
                 hotkeys: bool = True, save_path: Path | None = None, dark: bool | None = None,
                 start_backend: bool = True) -> None:
        self.cfg: Config = (cfg if isinstance(cfg, Config) else Config()).validated()
        if str(getattr(self.cfg, "ui_scaling", "auto")) != "auto" and not os.environ.get("QT_SCALE_FACTOR"):
            os.environ["QT_SCALE_FACTOR"] = f"{ui_scale_of(self.cfg):.2f}"
        QtCore, QtGui, QtWidgets = _qt()
        from treeaicoach import ui_widgets as W  # noqa: PLC0415

        self.QtCore, self.QtGui, self.QtWidgets, self.W = QtCore, QtGui, QtWidgets, W
        self.qapp = ensure_qapp()
        self._save_path = save_path
        self._engine_factory = engine_factory or _default_engine_factory
        self._overlay_factory = overlay_factory or _default_overlay_factory
        self._detector_factory = detector_factory or _default_detector_factory
        self._demo_source_factory = demo_source_factory or _default_demo_source
        self._want_hotkeys = hotkeys
        self.voice: Any = voice
        self._own_voice = voice is None
        self.engine: Any = None
        self.overlay: Any = None
        self._detector: Any = None
        self._hotkeys: Any = None
        self.demo = bool(demo)
        self.engine_error: str | None = None
        self._busy = False
        self._closing = False
        self._closed = threading.Event()
        self._muted = False
        self._move_mode = False
        self._dispatcher = _Dispatcher()
        self._eng_lock = threading.Lock()
        self._created_engines: list[Any] = []
        self._refreshers: dict[str, Callable[[], None]] = {}     # field -> sync its control from cfg
        self._widgets_by_field = self._refreshers                  # older name (tests)
        self._last_state_key = ""
        self._games: list[dict] = []
        self._voices: list[str] = []
        self._ai_test: tuple[bool, str] | None = None
        self._ai_test_busy = False
        self._lcu_text = ""
        self._lcu_polled = 0.0
        self._update_info: Any = None
        self._update_busy = False
        self._diag_watch = False
        self._cs_busy = False
        self._cs_polled = 0.0
        self._cs_sig: Any = None
        self._post_game_watch: str | None = None
        self._overlay_test: Any = None
        self._current_page = ""
        self.pages: dict[str, Any] = {}
        self._failed_pages: set[str] = set()
        self.page_views: dict[str, Any] = {}        # page key -> its controller object (pages/*.py)
        self._switch_ms: list[float] = []
        self._t0 = time.perf_counter()
        self._no_dialogs = False          # --ui-smoke / tests: no modal first-run dialog

        self.dark = W.system_is_dark(self.qapp) if dark is None else bool(dark)
        W.apply_theme(self.qapp, self.dark)

        self.win = _MainWindow(self)
        self.win.setWindowTitle(APP_NAME)
        icon = app_icon_path("ico") or app_icon_path("png")
        if icon:
            self.win.setWindowIcon(QtGui.QIcon(str(icon)))
        central = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.sidebar = W.Sidebar(PAGES)
        self.sidebar.brand.setText(APP_NAME)
        self.sidebar.selected.connect(lambda k: self.show_page(k))
        self.sidebar.update_btn.clicked.connect(lambda: self.show_page("about"))
        self.stack = QtWidgets.QStackedWidget()
        h.addWidget(self.sidebar)
        h.addWidget(self.stack, 1)
        self.win.setCentralWidget(central)
        self.toast = W.Toast(self.win)
        self.win.setMinimumSize(MIN_W, MIN_H)
        self._apply_geometry()
        self._bind_shortcuts()

        self._save_timer = QtCore.QTimer(self.win, singleShot=True)
        self._save_timer.timeout.connect(self.save_now)
        self._dispatch_timer = QtCore.QTimer(self.win)
        self._dispatch_timer.timeout.connect(self._dispatcher.drain)
        self._dispatch_timer.start(DISPATCH_MS)
        self._status_timer = QtCore.QTimer(self.win, singleShot=True)
        self._status_timer.timeout.connect(self._status_loop)

        self.show_page("home")
        self._prebuild_queue = [k for k in PREBUILD_ORDER if k not in self.pages]
        if start_backend:
            self._start_backend()
        self._status_timer.start(STATUS_MS)
        self.later(1500, self._first_run_dialogs)
        if paths.is_frozen() and self.cfg.check_updates_on_start:
            self.later(UPDATE_CHECK_DELAY_MS, lambda: self.check_updates(quiet=True))
        self.later(2500, self._check_last_update)
        self.later(1200, self.refresh_games)

    # ------------------------------------------------------------------ infrastructure
    def later(self, ms: int, fn: Callable[[], Any]) -> None:
        """Run ``fn`` on the main thread after ``ms`` (never after close; exceptions logged)."""
        def run() -> None:
            if self._closing:
                return
            try:
                fn()
            except Exception:
                log.exception("Scheduled UI call failed")
        self.QtCore.QTimer.singleShot(max(0, int(ms)), self.win, run)

    def post(self, fn: Callable[[], Any]) -> None:
        """Hand ``fn`` to the main thread (any thread)."""
        self._dispatcher.post(fn)

    def run_job(self, job: Callable[[], Any], done: Callable[[Any], None] | None = None,
                failed: Callable[[BaseException], None] | None = None, name: str = "TreeAI-ui-job") -> None:
        """``job()`` on a worker thread, ``done(result)`` / ``failed(exc)`` on the main thread
        (dropped after close)."""
        def wrap(cb: Callable[[Any], None] | None) -> Callable[[Any], None] | None:
            if cb is None:
                return None

            def call(arg: Any) -> None:
                if self._closing:
                    return
                try:
                    cb(arg)
                except Exception:
                    log.exception("UI callback of %s failed", name)
            return call
        self._dispatcher.run(job, wrap(done), wrap(failed), name=name)

    def _apply_geometry(self) -> None:
        scr = self.qapp.primaryScreen()
        geo = parse_geometry(self.cfg.ui_geometry)
        if geo and geo[2] is not None:
            s2 = self.qapp.screenAt(self.QtCore.QPoint(geo[2] + 40, geo[3] + 20))
            scr = s2 or scr
        ag = scr.availableGeometry() if scr is not None else self.QtCore.QRect(0, 0, 1280, 720)
        x, y, w, h = fit_geometry(geo, (ag.x(), ag.y(), ag.width(), ag.height()))
        self.win.resize(w, h)
        self.win.move(x, y)

    def _geometry_text(self) -> str:
        g = self.win.normalGeometry() if not self.win.isMaximized() else self.win.geometry()
        if g.width() <= 0:
            g = self.win.geometry()
        return f"{g.width()}x{g.height()}+{g.x()}+{g.y()}"

    def _bind_shortcuts(self) -> None:
        QtGui = self.QtGui
        for i, (key, _l, _i) in enumerate(PAGES, start=1):
            sc = QtGui.QShortcut(QtGui.QKeySequence(f"Ctrl+{i}"), self.win)
            sc.activated.connect(lambda k=key: self.show_page(k))
        for seq, fn in (("Ctrl+M", self.toggle_mute), ("Ctrl+Shift+S", lambda: self.set_safe_mode(not self.cfg.safe_mode)),
                        ("Ctrl+D", self.copy_diagnostic), ("F1", lambda: self.show_page("about"))):
            sc = QtGui.QShortcut(QtGui.QKeySequence(seq), self.win)
            sc.activated.connect(fn)

    # ------------------------------------------------------------------ pages
    def _page_builder(self, key: str) -> Callable[[CoachApp], Any] | None:
        import importlib  # noqa: PLC0415

        mod = {"home": "ui_page_home", "overlay": "ui_page_overlay", "alerts": "ui_page_alerts",
               "analysis": "ui_page_analysis", "settings": "ui_page_settings", "about": "ui_page_about"}.get(key)
        if mod is None:
            return None
        return getattr(importlib.import_module(f"treeaicoach.{mod}"), "build")

    def ensure_page(self, key: str) -> Any:
        """The page widget, built now if needed (an error page if its builder fails)."""
        page = self.pages.get(key)
        if page is not None:
            return page
        t0 = time.perf_counter()
        try:
            view = self._page_builder(key)(self)
            page = view.page
            self.page_views[key] = view
        except Exception:
            log.exception("Cannot build page %s", key)
            self._failed_pages.add(key)
            page = self.W.Page("Page indisponible", "Cette page n'a pas pu s'afficher. Le journal contient le détail "
                                                    "(À propos > Ouvrir les journaux).")
        self.pages[key] = page
        self.stack.addWidget(page)
        log.debug("page %s built in %.0f ms", key, 1000 * (time.perf_counter() - t0))
        return page

    def build_all_pages(self) -> None:
        for key, _l, _i in PAGES:
            self.ensure_page(key)

    def _prebuild(self) -> None:
        """One page per event-loop turn after the first paint (the window stays responsive)."""
        if self._closing:
            return
        while self._prebuild_queue and self._prebuild_queue[0] in self.pages:
            self._prebuild_queue.pop(0)
        if not self._prebuild_queue:
            log.info("all pages built %.0f ms after start", 1000 * (time.perf_counter() - self._t0))
            return
        key = self._prebuild_queue.pop(0)
        page = self.ensure_page(key)
        try:     # lay it out now, hidden, at the current size: its first display costs nothing more
            page.resize(self.stack.size())
            page.ensurePolished()
            if page.widget() is not None:
                page.widget().ensurePolished()
                lay = page.widget().layout()
                if lay is not None:
                    lay.activate()
        except Exception:
            pass
        self.QtCore.QTimer.singleShot(15, self.win, self._prebuild)

    def show_page(self, key: str, tab: str | None = None) -> None:
        if self._closing:
            return
        key = PAGE_ALIASES.get(key, key)
        if tab:
            key = PAGE_ALIASES.get(tab, key)
        if key not in {k for k, _l, _i in PAGES}:
            key = "home"
        t0 = time.perf_counter()
        page = self.ensure_page(key)
        self.stack.setCurrentWidget(page)
        self.sidebar.set_current(key)
        self._current_page = key
        view = self.page_views.get(key)
        if view is not None and callable(getattr(view, "on_show", None)):
            try:
                view.on_show()
            except Exception:
                log.exception("on_show of %s failed", key)
        self._switch_ms.append(1000 * (time.perf_counter() - t0))
        del self._switch_ms[:-50]

    def open_settings(self, tab: str) -> None:
        self.show_page(tab if tab in PAGE_ALIASES else "settings")

    # ------------------------------------------------------------------ settings binding
    def bind(self, field: str, refresh: Callable[[], None]) -> None:
        self._refreshers[field] = refresh

    def refresh_controls(self, fields: Sequence[str] | None = None) -> None:
        for f in (fields if fields is not None else list(self._refreshers)):
            fn = self._refreshers.get(f)
            if fn is not None:
                try:
                    fn()
                except Exception:
                    log.debug("refresh of %s failed", f, exc_info=True)

    def switch_row(self, section: Any, field: str, title: str, desc: str | None = None,
                   on_change: Callable[[bool], None] | None = None) -> Any:
        sw = self.W.Switch(bool(getattr(self.cfg, field)))
        row = section.add(self.W.Row(title, desc, sw))

        def changed(on: bool) -> None:
            if on_change is not None:
                on_change(on)
            else:
                self.set_option(field, bool(on))
        sw.toggled.connect(changed)
        self.bind(field, lambda: sw.set_quiet(bool(getattr(self.cfg, field))))
        row.switch = sw
        return row

    def choice_row(self, section: Any, field: str, title: str, desc: str | None,
                   choices: Sequence[tuple[Any, str]], segmented: bool = False,
                   on_change: Callable[[Any], None] | None = None) -> Any:
        W = self.W
        if segmented:
            ctl = W.Segmented([(str(v), lbl) for v, lbl in choices], str(getattr(self.cfg, field)))
            ctl.changed.connect(lambda v: (on_change or (lambda x: self.set_option(field, x)))(v))
            self.bind(field, lambda: ctl.set_value(str(getattr(self.cfg, field))))
        else:
            ctl = W.Combo(choices, getattr(self.cfg, field))
            ctl.activated.connect(lambda _i: (on_change or (lambda x: self.set_option(field, x)))(ctl.value()))
            self.bind(field, lambda: ctl.set_value(getattr(self.cfg, field)))
        row = section.add(W.Row(title, desc, ctl))
        row.ctl = ctl
        return row

    def slider_row(self, section: Any, field: str, title: str, desc: str | None, lo: float, hi: float,
                   step: float, fmt: Callable[[float], str], to_value: Callable[[float], Any] | None = None,
                   from_value: Callable[[Any], float] | None = None) -> Any:
        get = (lambda: from_value(getattr(self.cfg, field))) if from_value else (lambda: getattr(self.cfg, field))
        sl = self.W.Slider(lo, hi, step, get(), fmt)
        sl.changed.connect(lambda v: self.set_option(field, to_value(v) if to_value else type(
            getattr(self.cfg, field))(v)))
        self.bind(field, lambda: sl.set_value(get()))
        row = section.add(self.W.Row(title, desc, sl))
        row.ctl = sl
        return row

    def entry_row(self, section: Any, field: str, title: str, desc: str | None, secret: bool = False,
                  placeholder: str = "", width: int = 300) -> Any:
        e = self.QtWidgets.QLineEdit(str(getattr(self.cfg, field) or ""))
        e.setFixedWidth(width)
        e.setPlaceholderText(placeholder)
        if secret:
            e.setEchoMode(self.QtWidgets.QLineEdit.Password)
        e.editingFinished.connect(lambda: self.set_option(field, e.text().strip()))
        self.bind(field, lambda: e.setText(str(getattr(self.cfg, field) or "")) if not e.hasFocus() else None)
        row = section.add(self.W.Row(title, desc, e))
        row.ctl = e
        return row

    # ------------------------------------------------------------------ configuration
    def set_option(self, field: str, value: Any) -> None:
        """Change one setting: validate, apply live, save (debounced)."""
        if not hasattr(self.cfg, field):
            log.warning("Unknown setting %s", field)
            return
        if getattr(self.cfg, field) == value:
            return
        new = dataclasses.replace(self.cfg, **{field: value}).validated()
        self._replace_config(new, changed={field})

    def set_options(self, **fields: Any) -> None:
        new = dataclasses.replace(self.cfg, **fields).validated()
        self._replace_config(new, changed=set(fields))

    def _replace_config(self, new: Config, changed: set[str]) -> None:
        old = self.cfg
        self.cfg = new
        diff = {f.name for f in dataclasses.fields(Config) if getattr(old, f.name) != getattr(new, f.name)}
        if not diff:
            return
        self._apply_live(diff)
        self.refresh_controls([f for f in diff if f in self._refreshers])
        for view in list(self.page_views.values()):
            fn = getattr(view, "on_config", None)
            if callable(fn):
                try:
                    fn(diff)
                except Exception:
                    log.debug("on_config failed", exc_info=True)
        self._schedule_save()

    def _apply_live(self, diff: set[str]) -> None:
        cfg = self.cfg
        for obj, name in ((self.engine, "engine"), (self.overlay, "overlay")):
            if obj is not None:
                try:
                    obj.apply_config(cfg)
                except Exception:
                    log.exception("%s.apply_config failed", name)
        if diff & VOICE_FIELDS and self.voice is not None:
            base = dict(voice_name=cfg.voice_name, rate=cfg.voice_rate, volume=cfg.voice_volume,
                        beep_on_danger=cfg.beep_on_danger)
            try:
                extra = {k: getattr(cfg, f) for k, f in (("engine", "voice_engine"), ("neural_voice", "neural_voice"),
                                                         ("neural_rate", "neural_rate")) if hasattr(cfg, f)}
                try:
                    self.voice.set_params(**base, **extra)
                except TypeError:
                    self.voice.set_params(**base)
            except Exception:
                log.exception("voice.set_params failed")
        if "danger_voice" in diff and self.voice is not None:
            setter = getattr(self.voice, "set_danger_voice", None)
            if callable(setter):
                try:
                    setter(cfg.danger_voice)
                except Exception:
                    log.debug("set_danger_voice failed", exc_info=True)
        if diff & HOTKEY_FIELDS:
            self._rebind_hotkeys()
        if diff & DETECTOR_FIELDS and self.engine is not None:
            self._rebuild_engine(self.demo, start=None, new_detector=True)
        elif diff & ENGINE_RESTART_FIELDS and self.engine is not None:
            self._rebuild_engine(self.demo, start=None)
        if diff & {"ai_provider", "ai_api_key", "ai_model"}:
            self._ai_test = None

    def _schedule_save(self) -> None:
        if not self._closing:
            self._save_timer.start(SAVE_DEBOUNCE_MS)

    def save_now(self) -> None:
        save_config(self.cfg, self._save_path)

    # ------------------------------------------------------------------ backend lifecycle
    def _start_backend(self) -> None:
        cfg = self.cfg
        demo = self.demo

        def job() -> dict[str, Any]:
            out: dict[str, Any] = {}
            voice = self.voice
            if voice is None:
                try:
                    from treeaicoach.voice import VoiceEngine  # noqa: PLC0415

                    extra = {k: getattr(cfg, f) for k, f in (("engine", "voice_engine"),
                                                             ("neural_voice", "neural_voice"),
                                                             ("neural_rate", "neural_rate")) if hasattr(cfg, f)}
                    try:
                        voice = VoiceEngine(cfg.voice_name, cfg.voice_rate, cfg.voice_volume, cfg.beep_on_danger,
                                            **extra)
                    except TypeError:
                        voice = VoiceEngine(cfg.voice_name, cfg.voice_rate, cfg.voice_volume, cfg.beep_on_danger)
                    voice.start()
                except Exception:
                    log.exception("Voice unavailable")
                    voice = None
            out["voice"] = voice
            try:
                out["detector"] = self._make_detector(cfg, demo)
            except Exception:
                log.exception("Detector unavailable")
                out["detector"] = None
            try:
                src = self._demo_source_factory() if demo else None
                out["engine"] = self._engine_factory(cfg, voice, out["detector"], src)
            except Exception as exc:
                log.exception("Cannot create the analysis engine")
                out["engine"] = None
                out["error"] = f"Le moteur d'analyse n'a pas pu démarrer : {exc}"
            try:
                ov = self._overlay_factory(cfg, self._overlay_provider)
                out["overlay"] = ov
                if ov is not None:
                    set_cb = getattr(ov, "set_on_moved", None)
                    if callable(set_cb):
                        set_cb(self._on_overlay_moved)
                    ov.start()
            except Exception:
                log.exception("Overlay unavailable")
                out["overlay"] = None
            eng = out.get("engine")
            if eng is not None and (cfg.autostart or demo):
                try:
                    eng.start()
                except Exception as exc:
                    log.exception("Engine start failed")
                    out["error"] = f"Impossible de démarrer l'analyse : {exc}"
            self._track_engine(eng)
            if self._closing:
                self._shutdown_components(None, out.get("overlay"), voice if self._own_voice else None)
            return out

        def failed(exc: BaseException) -> None:
            self._busy = False
            self.engine_error = f"Le démarrage a échoué : {exc}. Clique sur « Démarrer » pour réessayer."
            self.show_error(self.engine_error)
            self._refresh_status()

        self._busy = True
        self._backend_t0 = time.monotonic()
        self._dispatcher.run(job, self._backend_ready, failed, name="TreeAI-ui-init")

    def _make_detector(self, cfg: Config, demo: bool) -> Any:
        if self._detector_factory is _default_detector_factory:
            return _default_detector_factory(cfg, learn=not demo)
        return self._detector_factory(cfg)

    def _backend_ready(self, out: dict[str, Any]) -> None:
        self._busy = False
        if self._closing:
            self._shutdown_components(out.get("engine"), out.get("overlay"),
                                      out.get("voice") if self._own_voice else None)
            return
        self.voice = out.get("voice")
        self._detector = out.get("detector")
        self.engine = out.get("engine")
        self.overlay = out.get("overlay")
        self.engine_error = out.get("error") if self.engine is None else None
        if out.get("error"):
            self.show_error(out["error"])
        self._load_voices()
        if self._want_hotkeys:
            self._rebind_hotkeys()
        self._refresh_status()

    def _load_voices(self) -> None:
        voice = self.voice
        if voice is None or not callable(getattr(voice, "list_voices", None)):
            return

        def done(names: Any) -> None:
            self._voices = [str(n) for n in (names or [])]
            view = self.page_views.get("alerts")
            if view is not None and callable(getattr(view, "on_voices", None)):
                view.on_voices()
        self.run_job(voice.list_voices, done, None, name="TreeAI-ui-voices")

    def _track_engine(self, eng: Any) -> None:
        if eng is None:
            return
        with self._eng_lock:
            self._created_engines.append(eng)
            del self._created_engines[:-4]
            closing = self._closing
        if closing:
            try:
                eng.stop()
            except Exception:
                log.exception("Cannot stop an engine created during shutdown")

    def _on_overlay_moved(self, name: str, x: int, y: int) -> None:
        """Overlay thread callback (move mode): save the new window position (main thread)."""
        def apply() -> None:
            field = {"radar": "radar", "hud": "hud"}.get(str(name))
            if field is not None:
                self.set_options(**{f"{field}_xy": [int(x), int(y)], f"{field}_position": "custom"})
        self.post(apply)

    def _overlay_provider(self) -> Any:
        test = self._overlay_test
        if test is not None:
            t0, states = test
            el = time.monotonic() - t0
            if el < 10.0 and states:
                return states[min(len(states) - 1, int(el / (10.0 / len(states))))]
            self._overlay_test = None
        return self._overlay_state()

    def _overlay_state(self) -> Any:
        eng = self.engine
        if eng is None:
            return None
        try:
            return eng.get_overlay_state()
        except Exception:
            return None

    def _engine_running(self) -> bool:
        try:
            return bool(self.engine is not None and self.engine.is_running())
        except Exception:
            return False

    def _get_status(self) -> Any:
        if self.engine is None:
            return None
        try:
            return self.engine.get_status()
        except Exception:
            return None

    # ------------------------------------------------------------------ actions
    @_guarded
    def toggle_engine(self) -> None:
        if self._busy:
            return
        if self.engine is None:
            if self.engine_error:
                self.show_error(self.engine_error)
            self._rebuild_engine(self.demo, start=True, new_detector=self._detector is None)
            return
        eng, running = self.engine, self._engine_running()
        self._busy = True
        self._refresh_status()

        def job() -> None:
            eng.stop() if running else eng.start()

        def done(_r: Any = None) -> None:
            self._busy = False
            self._refresh_status()

        def failed(exc: BaseException) -> None:
            self._busy = False
            self.show_error(f"Impossible de {'arrêter' if running else 'démarrer'} l'analyse : {exc}")
            self._refresh_status()

        self._dispatcher.run(job, done, failed, name="TreeAI-ui-engine")

    @_guarded
    def toggle_demo(self) -> None:
        if self._busy:
            return
        self._rebuild_engine(not self.demo, start=True)
        self.show_toast("Mode démo : partie simulée, le jungler ennemi va venir te ganker vers 40 s."
                        if not self.demo else "Retour à l'analyse réelle.")

    def _rebuild_engine(self, demo: bool, start: bool | None, new_detector: bool = False) -> None:
        if self._busy:
            self.later(300, lambda: self._rebuild_engine(demo, start, new_detector))
            return
        old, was_running = self.engine, self._engine_running()
        want_start = was_running if start is None else start
        cfg, voice = self.cfg, self.voice
        self._busy = True
        self.demo = demo

        def job() -> tuple[Any, Any, str | None]:
            if old is not None:
                try:
                    old.stop()
                except Exception:
                    log.exception("Old engine stop failed")
            det = self._detector
            if new_detector or det is None:
                try:
                    det = self._make_detector(cfg, demo)
                except Exception:
                    log.exception("Detector unavailable")
            try:
                src = self._demo_source_factory() if demo else None
                eng = self._engine_factory(cfg, voice, det, src)
                try:
                    if want_start:
                        eng.start()
                finally:
                    self._track_engine(eng)
                return eng, det, None
            except Exception as exc:
                log.exception("Cannot rebuild the engine")
                return None, det, f"Le moteur d'analyse n'a pas pu démarrer : {exc}"

        def done(res: tuple[Any, Any, str | None]) -> None:
            self._busy = False
            eng, det, err = res
            if self._closing:
                self._shutdown_components(eng, None, None)
                return
            self.engine, self._detector, self.engine_error = eng, det, err
            if err:
                self.show_error(err)
            self._refresh_status()

        def failed(exc: BaseException) -> None:
            self._busy = False
            self.engine_error = f"Le moteur d'analyse n'a pas pu redémarrer : {exc}"
            self.show_error(self.engine_error)
            self._refresh_status()

        self._dispatcher.run(job, done, failed, name="TreeAI-ui-rebuild")

    @_guarded
    def test_voice(self) -> None:
        """"Tester la voix" / "Écouter": a sample said by the REAL chosen voice (``voice.preview``
        waits for the natural voice instead of switching to a Windows voice; it only queues the line),
        then a note if a Windows voice had to say it."""
        voice = self.voice
        if voice is None:
            self.show_error("La synthèse vocale n'est pas disponible (elle démarre peut-être encore).")
            return
        preview = getattr(voice, "preview", None)
        if callable(preview):
            preview()
        else:
            voice.say("Test de la voix. Attention, Lee Sin arrive par la rivière !", 1)
        if getattr(voice, "backend", "") == "print":
            self.show_toast("Voix indisponible sur ce système : le message est écrit dans le journal.", "warning")
            return
        self.show_toast("Test de la voix en cours…")
        self.later(7500, self._voice_test_report)

    def _voice_test_report(self) -> None:
        src = str(getattr(self.voice, "last_source", "") or "")
        if getattr(self.cfg, "voice_engine", "auto") in ("auto", "neural") and src in ("onecore", "sapi"):
            self.show_toast("La voix naturelle n'a pas répondu (Internet ou antivirus ?) : une voix Windows "
                            "l'a remplacée. Les phrases du match sont préparées dès que la connexion revient.",
                            "warning")

    @_guarded
    def test_overlay(self) -> None:
        """Show the overlay on sample states (sûr / attention / danger) for 10 s, out of game."""
        ov = self.overlay
        if self._in_game():
            self.show_toast("Une partie est en cours : l'overlay affiche déjà la vraie partie.")
            return
        if ov is None or not getattr(ov, "ok", False):
            self.show_page("overlay")
            self.show_toast("L'overlay ne s'affiche que sous Windows, jeu en Sans bordure. Voici l'aperçu.",
                            "warning")
            return
        if not self.cfg.overlay_enabled:
            self.set_option("overlay_enabled", True)

        def job() -> list:
            from treeaicoach import overlay_render as orr  # noqa: PLC0415

            states = orr.sample_states()
            sw, sh = 1920, 1080
            try:
                from treeaicoach.capture import monitor_rects  # noqa: PLC0415

                mons = monitor_rects()
                if mons:
                    sw, sh = int(mons[0].w), int(mons[0].h)
            except Exception:
                pass
            out = []
            for name in ("safe", "warning", "danger"):
                st = states.get(name)
                if st is None:
                    continue
                mm = orr.default_minimap_rect(sw, sh)
                try:
                    rect_t = type(st.minimap_rect) if st.minimap_rect is not None else tuple
                    st = dataclasses.replace(st, minimap_rect=rect_t(*mm), screen_rect=rect_t(0, 0, sw, sh))
                except Exception:
                    pass
                out.append(st)
            return out

        def done(states: list) -> None:
            if not states:
                self.show_error("Aperçu de l'overlay indisponible.")
                return
            self._overlay_test = (time.monotonic(), states)
            self.show_toast("Overlay de test affiché 10 s : sûr, attention, puis danger.")

        self.run_job(job, done, lambda e: self.show_error(f"Test de l'overlay impossible : {e}"),
                     name="TreeAI-ui-overlay-test")

    @_guarded
    def relocate(self, quiet: bool = False) -> None:
        if self.engine is not None:
            self.engine.request_relocate()
            if not quiet:
                self.show_toast("Recherche de la minimap relancée.")
        elif not quiet:
            self.show_error("Le moteur d'analyse n'est pas disponible.")

    @_guarded
    def calibrate(self) -> None:
        from treeaicoach.calibration import run_calibration  # noqa: PLC0415

        rect = run_calibration(self.win, self.cfg)
        if rect:
            self.set_options(manual_minimap_rect=dict(rect), minimap_mode="manual")
            self.relocate(quiet=True)
            self.show_toast(f"Minimap calibrée : {rect['w']} × {rect['h']} px.")

    @_guarded
    def toggle_move_mode(self) -> None:
        ov = self.overlay
        setter = getattr(ov, "set_move_mode", None) if ov is not None else None
        if not callable(setter) or not bool(getattr(ov, "ok", True)):
            self.show_error("Le déplacement des fenêtres de l'overlay n'est disponible que sous Windows.")
            return
        self._move_mode = not self._move_mode
        setter(self._move_mode)
        view = self.page_views.get("overlay")
        if view is not None and callable(getattr(view, "on_move_mode", None)):
            view.on_move_mode(self._move_mode)
        self.show_toast("Fais glisser le radar et le panneau à la souris, puis clique sur « Terminer »."
                        if self._move_mode else "Positions de l'overlay enregistrées.")

    @_guarded
    def apply_skill_level(self, level: str) -> None:
        """Débutant / Intermédiaire / Avancé / Expert: adapts tips, voice and overlay in one click."""
        from treeaicoach import skill as _skill  # noqa: PLC0415

        changes = _skill.preset_changes(self.cfg, level)
        if not changes:
            return
        self.set_options(**changes)
        self.show_toast(f"Niveau « {_skill.label(level)} » : {_skill.SKILL_HELP[_skill.normalize(level)]}")

    def _ai_budget(self) -> str:
        fn = getattr(self.engine, "ai_budget_text", None) if self.engine is not None else None
        if not callable(fn) or not self._in_game():
            return ""
        try:
            return str(fn() or "")
        except Exception:
            return ""

    @_guarded
    def set_safe_mode(self, on: bool) -> None:
        self.set_option("safe_mode", bool(on))
        self.show_toast("Mode sûr activé : plus d'alertes de gank ni de suivi du jungler." if on
                        else "Mode sûr désactivé.")

    def _is_muted(self) -> bool:
        eng = self.engine
        if eng is not None and hasattr(eng, "muted"):
            try:
                return bool(eng.muted)
            except Exception:
                pass
        return self._muted

    @_guarded
    def toggle_mute(self) -> None:
        self._hk_mute()

    def set_muted(self, muted: bool) -> None:
        if self._is_muted() != bool(muted):
            self._hk_mute()

    # ------------------------------------------------------------------ hotkeys (their own thread)
    def _rebind_hotkeys(self) -> None:
        cfg = self.cfg
        bindings: dict[str, Callable[[], None]] = {}
        for key, fn in ((cfg.hotkey_jungler, self._hk_jungler), (cfg.hotkey_mute, self._hk_mute),
                        (cfg.hotkey_overlay, self._hk_overlay), (getattr(cfg, "hotkey_ai", ""), self._hk_ai),
                        (getattr(cfg, "hotkey_ward", ""), self._hk_ward)):
            if key:
                bindings[key] = fn
        try:
            if self._hotkeys is None:
                from treeaicoach.hotkeys import HotkeyListener  # noqa: PLC0415

                self._hotkeys = HotkeyListener(bindings)
                hk = self._hotkeys
                self.run_job(hk.start, self._hotkeys_started, None, name="TreeAI-ui-hotkeys")
            else:
                hk = self._hotkeys
                self.run_job(lambda: hk.set_bindings(bindings), self._hotkeys_started, None, name="TreeAI-ui-hotkeys")
        except Exception:
            log.exception("Hotkeys unavailable")

    def _hotkeys_started(self, _r: Any = None) -> None:
        failed = list(getattr(self._hotkeys, "failed", []) or [])
        if failed:
            self.show_toast(f"Raccourci déjà utilisé par une autre application : {', '.join(failed)}. "
                            "Change-le dans Réglages > Touches en jeu.", "warning")

    def _hk_jungler(self) -> None:
        eng, voice = self.engine, self.voice
        try:
            speak = getattr(eng, "speak_jungler_status", None)
            if callable(speak):
                speak()
                return
            text = eng.jungler_status_text() if eng is not None else ""
            if text and voice is not None:
                voice.say(text, 1)
        except Exception:
            log.exception("Jungler hotkey failed")

    def _hk_ai(self) -> None:
        self.post(self.ask_ai)

    def _hk_ward(self) -> None:
        fn = getattr(self.engine, "request_ward_guide", None)
        if callable(fn):
            try:
                fn()
            except Exception:
                log.debug("ward guide hotkey failed", exc_info=True)

    @_guarded
    def ask_ai(self) -> None:
        fn = getattr(self.engine, "ask_ai", None)
        if not callable(fn):
            self.show_toast("Conseil IA indisponible : le moteur n'est pas démarré.")
            return
        self.run_job(fn, lambda msg: self.show_toast(str(msg or "")),
                     lambda e: self.show_error(f"Conseil IA impossible : {e}"), name="TreeAI-ui-ask-ai")

    def _hk_mute(self) -> None:
        eng = self.engine
        self._muted = not bool(getattr(eng, "muted", self._muted))
        try:
            if eng is not None and hasattr(eng, "mute"):
                eng.mute(self._muted)
            elif self.voice is not None and hasattr(self.voice, "set_muted"):
                self.voice.set_muted(self._muted)
        except Exception:
            log.exception("Mute hotkey failed")
        muted = self._muted
        self.post(lambda: (self.show_toast("Voix coupée." if muted else "Voix rétablie."), self._sync_quick()))

    def _hk_overlay(self) -> None:
        try:
            if self.engine is not None and hasattr(self.engine, "toggle_overlay"):
                self.engine.toggle_overlay()
        except Exception:
            log.exception("Overlay hotkey failed")

    def _sync_quick(self) -> None:
        view = self.page_views.get("home")
        if view is not None:
            view.sync_quick()

    # ------------------------------------------------------------------ status loop
    def _iconic(self) -> bool:
        try:
            return bool(self.win.isMinimized() or not self.win.isVisible())
        except Exception:
            return False

    @staticmethod
    def _launcher_in_front() -> bool:
        try:
            from treeaicoach.capture import foreground_state  # noqa: PLC0415

            game_front, own_front = foreground_state()
        except Exception:
            return True
        return game_front is None or bool(own_front)

    def _status_loop(self) -> None:
        if self._closing:
            return
        try:
            self._refresh_status()
        except Exception:
            log.exception("Status refresh failed")
        if self._iconic():
            delay = STATUS_ICONIC_MS
        elif (self._current_page == "home" and (self._busy or self._last_state_key in ("RUNNING", "LOCATING"))
              and self._launcher_in_front()):
            delay = STATUS_MS
        else:
            delay = STATUS_IDLE_MS
        self._status_timer.start(delay)

    def status_snapshot(self) -> dict[str, Any]:
        """Everything the pages show about the analysis, from thread-safe engine snapshots."""
        st = self._get_status()
        ov = self._overlay_state() if st is not None else None
        running = self._engine_running()
        if self.engine is None:
            key = "STARTING" if self._busy else "NO_ENGINE"
            slow = self._busy and time.monotonic() - getattr(self, "_backend_t0", time.monotonic()) > 30.0
            msg = (("Chargement plus long que prévu (premier lancement ou analyse antivirus)…" if slow else
                    "Chargement du détecteur et de la voix…") if self._busy else
                   (self.engine_error or "Le moteur d'analyse n'est pas disponible."))
        else:
            key = state_key(getattr(st, "state", None)) if st is not None else "STOPPED"
            if not running and key != "ERROR":
                key = "STOPPED"
            if self._busy:
                key = "STARTING"
            msg = str(getattr(st, "message", "") or "") if st is not None and running else ""
        champ, role = "", None
        if key == "RUNNING" and ov is not None:
            me, role, _opp = ui_kit.lane_opponent(ov)
            champ = champion_name(me) if me and me != "me" else ""
            role = role or getattr(ov, "my_role", None)
        title, msg, fix, action = ui_kit.status_line(key, msg, champion=champ, role=role)
        return {"key": key, "title": title, "message": msg, "fix": fix, "action": action, "status": st,
                "overlay": ov, "running": running}

    def _refresh_status(self) -> None:
        snap = self.status_snapshot()
        key = snap["key"]
        if key != self._last_state_key:
            if self._last_state_key == "RUNNING" and key != "RUNNING":     # a game just ended: new record
                self._post_game_watch = str(_game_json_path(self._games[0]) or "") if self._games else ""
                for delay in (4000, 15000, 45000):
                    self.later(delay, self.refresh_games)
            self._last_state_key = key
        level = {"RUNNING": 0, "WAITING_GAME": 0, "LOCATING": 0, "STARTING": -1, "STOPPED": -1,
                 "CAPTURE_BLACK": 1, "UNSUPPORTED_MODE": 1}.get(key, 2)
        self.sidebar.dot.set_level(level)
        self.sidebar.status.setText(snap["title"])
        if self._diag_watch:
            self._poll_diagnostic()
        self._poll_champ_select(key)
        view = self.page_views.get("home")
        if view is not None and not self._iconic():
            view.update_status(snap)

    # ------------------------------------------------------------------ champion select
    def _poll_champ_select(self, key: str, every: float = 2.0) -> None:
        if key == "RUNNING" or self._cs_busy:
            if key == "RUNNING" and self._cs_sig is not None:
                self._show_champ_select(None)
            return
        now = time.monotonic()
        if now - self._cs_polled < every:
            return
        self._cs_polled = now
        self._cs_busy = True

        def job() -> Any:
            from treeaicoach import champ_select  # noqa: PLC0415

            return champ_select.pregame_card()

        def done(card: Any) -> None:
            self._cs_busy = False
            self._show_champ_select(card)

        def failed(_e: BaseException) -> None:
            self._cs_busy = False

        self.run_job(job, done, failed, name="TreeAI-ui-champselect")

    def _show_champ_select(self, card: Any) -> None:
        title = str(getattr(card, "title", "") or "") if card is not None else ""
        lines = tuple(str(x) for x in (getattr(card, "lines", ()) or ()) if x) if card is not None else ()
        sig = (title, lines) if card is not None else None
        if sig == self._cs_sig:
            return
        was_shown = self._cs_sig is not None
        self._cs_sig = sig
        if card is not None and not was_shown and self._current_page != "home":
            self.show_toast("Sélection des champions : ta carte d'avant-partie est sur l'Accueil.")
        view = self.page_views.get("home")
        if view is not None:
            view.show_champ_select(title, lines if card is not None else None)

    # ------------------------------------------------------------------ diagnostic (Ctrl+F8)
    @_guarded
    def start_diagnostic(self) -> None:
        eng = self.engine
        fn = getattr(eng, "start_diagnostic", None) if eng is not None else None
        if not callable(fn):
            self.show_error("Le diagnostic complet demande le moteur d'analyse (il démarre encore ?).")
            return

        def done(path: Any) -> None:
            if path is None:
                self.show_toast("Un diagnostic est déjà en cours (ou impossible pour l'instant).", "warning")
                return
            self._diag_watch = True
            self.show_toast("Diagnostic en cours : joue normalement pendant une minute.")
            self._poll_diagnostic()

        self.run_job(fn, done, lambda e: self.show_error(f"Diagnostic impossible : {e}"), name="TreeAI-ui-diagnostic")

    def _poll_diagnostic(self) -> None:
        eng = self.engine
        fn = getattr(eng, "diagnostic_status", None) if eng is not None else None
        try:
            ds = fn() if callable(fn) else None
        except Exception:
            ds = None
        if not isinstance(ds, dict):
            self._diag_watch = False
            return
        running = bool(ds.get("running"))
        pct = int(round(100 * float(ds.get("progress") or 0)))
        if running:
            msg = f"Diagnostic en cours : {pct} %"
        elif ds.get("error"):
            msg = f"Diagnostic interrompu : {ds['error']}"
        else:
            msg = "Diagnostic prêt : " + str(ds.get("zip") or ds.get("folder") or "")
        view = self.page_views.get("settings")
        if view is not None and callable(getattr(view, "set_diag_text", None)):
            view.set_diag_text(msg, running)
        if not running:
            self._diag_watch = False
            (self.show_error if ds.get("error") else self.show_toast)(msg)

    # ------------------------------------------------------------------ games, reports
    @_guarded
    def refresh_games(self) -> None:
        def job() -> list[dict]:
            fn = _report_function("list_games")
            return [g for g in (fn(50) or []) if isinstance(g, dict)] if fn is not None else []

        def done(games: list[dict]) -> None:
            new_first = str(_game_json_path(games[0]) or "") if games else ""
            if self._post_game_watch is not None and new_first and new_first != self._post_game_watch:
                self._post_game_watch = None
                self.show_toast("Rapport de ta dernière partie prêt : page Analyse.")
            self._games = games
            for key in ("analysis", "home"):
                view = self.page_views.get(key)
                if view is not None and callable(getattr(view, "on_games", None)):
                    view.on_games(games)

        self.run_job(job, done, None, name="TreeAI-ui-games")

    def refresh_lcu(self, done: Callable[[str], None]) -> None:
        def job() -> str:
            if not getattr(self.cfg, "lcu_enabled", True):
                return "Client LoL : désactivé"
            from treeaicoach.lcu import get_default_client  # noqa: PLC0415

            return get_default_client().status_text()

        def ok(text: str) -> None:
            self._lcu_text = str(text or "")
            done(self._lcu_text)
        self.run_job(job, ok, None, name="TreeAI-ui-lcu")

    @_guarded
    def open_report(self, game: dict | None = None) -> None:
        from treeaicoach.ui_common import _game_html_path  # noqa: PLC0415

        game = game if game is not None else (self._games[0] if self._games else None)
        if game is None:
            self.show_toast("Aucune partie enregistrée pour l'instant.")
            return
        src = _game_json_path(game)

        def job() -> Path | None:
            html = _game_html_path(game, src)
            if html is not None and Path(html).exists():
                return Path(html)
            fn = _report_function("write_report")
            if fn is None or src is None:
                return None
            out = fn(src)
            return Path(out) if out else None

        def done(path: Path | None) -> None:
            if path is None or not Path(path).is_file():
                self.show_error("Impossible de générer le rapport de cette partie.")
                return
            webbrowser.open(Path(path).resolve().as_uri())
            self.show_toast("Rapport ouvert dans le navigateur.")
        self.show_toast("Préparation du rapport…")
        self.run_job(job, done, lambda e: self.show_error(f"Rapport impossible : {e}"), name="TreeAI-ui-report")

    def open_last_report(self) -> None:
        self.open_report(None)

    # ------------------------------------------------------------------ updates
    def _check_last_update(self) -> None:
        from treeaicoach import updater  # noqa: PLC0415

        def done(rep: Any) -> None:
            if rep is None:
                return
            if rep.ok:
                self.show_toast(rep.message)
                return
            self._set_update_status(rep.message, "danger", manual=True)
            from treeaicoach.ui_dialogs import message  # noqa: PLC0415

            if message(self, "Mise à jour non appliquée", rep.message.split(" Tu peux aussi")[0],
                       [("Fermer", "close"), ("Télécharger", "download")]) == "download":
                self.open_manual_download()
        self.run_job(updater.startup_report, done, None, name="TreeAI-update-report")

    def _set_update_status(self, text: str, tone: str = "", manual: bool = False) -> None:
        view = self.page_views.get("about")
        if view is not None:
            view.set_update_status(text, tone, manual)

    def check_updates(self, quiet: bool = False) -> None:
        if self._update_busy or self._closing:
            return
        self._update_busy = True
        from treeaicoach import updater  # noqa: PLC0415

        cfg = self.cfg
        if not quiet:
            self._set_update_status("Recherche d'une nouvelle version…")

        def done(res: Any) -> None:
            self._update_busy = False
            self._update_info = res.info if res.available else None
            if self._update_info is not None:
                self.sidebar.update_btn.setText(f"Nouvelle version {getattr(res.info, 'version', '')}".strip())
                self.sidebar.update_btn.show()
            else:
                self.sidebar.update_btn.hide()
            err = res.status == updater.ERROR
            self._set_update_status(updater.manual_hint(res.message) if err else res.message,
                                    "accent" if res.available else ("danger" if err else "ok"), manual=err)
            view = self.page_views.get("about")
            if view is not None:
                view.set_install_enabled(bool(res.available and res.can_install))
            if res.available and quiet:
                self.show_toast(f"Nouvelle version {res.info.version} disponible : bouton en bas à gauche.")
            elif not quiet:
                self.show_toast(res.message, "error" if err else "info")

        def failed(exc: BaseException) -> None:
            self._update_busy = False
            if not quiet:
                self._set_update_status(f"Vérification impossible : {exc}", "danger", manual=True)

        self.run_job(lambda: updater.check_for_update(cfg), done, failed, name="TreeAI-update-check")

    def install_update(self) -> None:
        info = self._update_info
        if info is None or self._update_busy:
            return
        from treeaicoach import updater  # noqa: PLC0415
        from treeaicoach.ui_common import fmt_decimal_fr  # noqa: PLC0415

        self._update_busy = True
        view = self.page_views.get("about")
        self._set_update_status(f"Téléchargement de la version {info.version}…")
        cfg = self.cfg
        last = [0.0]

        def progress(done_b: int, total: int) -> None:      # worker thread: throttled post
            now = time.monotonic()
            if now - last[0] < 0.15 and done_b < total:
                return
            last[0] = now
            frac = done_b / total if total else 0.0
            text = (f"Téléchargement de la version {info.version}… "
                    f"{fmt_decimal_fr(done_b / 1e6, 1)} / {fmt_decimal_fr(total / 1e6, 1)} Mo")
            self.post(lambda: (view.set_progress(frac) if view is not None else None, self._set_update_status(text)))

        def job() -> Any:
            dl = updater.download_update(info, cfg, progress=progress)
            if not dl.ok:
                return dl
            return updater.apply_update(dl.path, info)

        def done(res: Any) -> None:
            self._update_busy = False
            if res.ok and isinstance(res, updater.ApplyResult):
                self._set_update_status(res.message, "ok")
                self.show_toast(res.message)
                self.later(800, self.close)
                return
            if view is not None:
                view.set_progress(None)
            self._set_update_status(updater.manual_hint(res.message), "danger", manual=True)
            self.show_error(res.message)

        def failed(exc: BaseException) -> None:
            done(updater.ApplyResult(False, f"Mise à jour impossible : {exc}"))

        self.run_job(job, done, failed, name="TreeAI-update-install")

    def open_manual_download(self) -> None:
        from treeaicoach import updater  # noqa: PLC0415

        try:
            webbrowser.open(updater.MANUAL_DOWNLOAD_URL)
        except Exception:
            self.copy_text(updater.MANUAL_DOWNLOAD_URL)

    # ------------------------------------------------------------------ AI key test
    def test_ai_key(self) -> None:
        if self._ai_test_busy:
            return
        self._ai_test_busy = True
        cfg = self.cfg

        def done(res: Any) -> None:
            self._ai_test_busy = False
            ok, short, long_text = res
            self._ai_test = (bool(ok), str(short))
            (self.show_toast if ok else self.show_error)(str(long_text or short))
            view = self.page_views.get("settings")
            if view is not None and callable(getattr(view, "on_ai_test", None)):
                view.on_ai_test(bool(ok), str(short))

        def failed(exc: BaseException) -> None:
            self._ai_test_busy = False
            self.show_error(f"Test impossible : {exc}")
        self.run_job(lambda: ui_kit.test_ai_key(cfg), done, failed, name="TreeAI-ui-ai-test")

    # ------------------------------------------------------------------ small services
    def show_toast(self, text: str, level: str = "info") -> None:
        if self._closing or not text:
            return
        try:
            self.toast.show_text(str(text).replace(chr(0x2014), ":"), level, TOAST_MS if level == "info" else 7000)
        except Exception:
            log.debug("toast failed", exc_info=True)

    def show_error(self, text: str) -> None:
        log.warning("UI error: %s", text)
        self.show_toast(text, "error")

    def copy_text(self, text: str) -> None:
        try:
            self.qapp.clipboard().setText(str(text))
        except Exception:
            log.debug("clipboard failed", exc_info=True)

    def diagnostic(self) -> str:
        """Plain-text diagnostic report (no secret)."""
        log_file = None
        try:
            files = sorted(Path(paths.logs_dir()).glob("*.log"), key=lambda f: f.stat().st_mtime)
            log_file = files[-1] if files else None
        except Exception:
            pass
        return ui_kit.diagnostic_text(version=__version__, cfg=self.cfg, status=self._get_status(),
                                      engine=self.engine, overlay=self.overlay, detector=self._detector,
                                      voice=self.voice, log_file=log_file, data_dir=paths.user_data_dir(),
                                      demo=self.demo)

    @_guarded
    def copy_diagnostic(self) -> None:
        self.copy_text(self.diagnostic())
        self.show_toast("Diagnostic copié : colle-le (Ctrl+V) dans ton message.")

    @_guarded
    def open_logs(self) -> None:
        from treeaicoach.ui_common import open_path  # noqa: PLC0415

        open_path(paths.logs_dir())

    def _in_game(self) -> bool:
        return self._last_state_key == "RUNNING"

    def _first_run_dialogs(self) -> None:
        if self._no_dialogs:
            return
        from treeaicoach import ui_dialogs  # noqa: PLC0415

        ui_dialogs.first_run(self)

    # ------------------------------------------------------------------ run / close
    def show(self) -> None:
        self.win.show()
        self.win.raise_()
        self.QtCore.QTimer.singleShot(60, self.win, self._prebuild)

    def run(self, smoke_seconds: float | None = None) -> int:
        self._no_dialogs = self._no_dialogs or smoke_seconds is not None
        self.show()
        if smoke_seconds is not None:
            try:
                delay = max(0, int(float(smoke_seconds) * 1000))
            except (TypeError, ValueError):
                delay = 0
            self.QtCore.QTimer.singleShot(delay, self.close)
        try:
            self.qapp.exec()
        except KeyboardInterrupt:
            self.close()
        return 0

    def request_close(self) -> bool:
        """Window close button: confirm while a game runs (``ui_confirm_quit``); True = closing."""
        if self._closing:
            return True
        if self._in_game() and self.cfg.ui_confirm_quit:
            from treeaicoach.ui_dialogs import message  # noqa: PLC0415

            if message(self, "Quitter TreeAI Coach ?", "Une partie est en cours : le coach et l'overlay "
                       "s'arrêteront.", [("Annuler", "no"), ("Quitter", "yes")]) != "yes":
                return False
        self.close()
        return True

    def close(self) -> None:
        """Stop everything, save the configuration and close the window. Idempotent."""
        if self._closing:
            return
        try:
            if self.win.isVisible() and not self.win.isMinimized():
                self.cfg = dataclasses.replace(self.cfg, ui_geometry=self._geometry_text()).validated()
        except Exception:
            pass
        self._closing = True
        try:     # a modal dialog open (nested loop): close it first
            modal = self.qapp.activeModalWidget()
            if modal is not None:
                modal.reject()
        except Exception:
            pass
        for t in (self._save_timer, self._dispatch_timer, self._status_timer):
            try:
                t.stop()
            except Exception:
                pass
        save_config(self.cfg, self._save_path)
        self._dispatcher.closed = True
        with self._eng_lock:
            engines = list(self._created_engines)
        if self.engine is not None and self.engine not in engines:
            engines.append(self.engine)
        ov, hk = self.overlay, self._hotkeys
        voice = self.voice if self._own_voice else None

        def shutdown() -> None:
            for e in engines[:-1]:
                self._shutdown_components(e, None, None)
            self._shutdown_components(engines[-1] if engines else None, ov, voice, hk)

        t = threading.Thread(target=shutdown, name="TreeAI-ui-shutdown", daemon=True)
        t.start()
        t.join(8.0)
        if t.is_alive():
            log.warning("Shutdown did not finish in 8 s; closing the window anyway")
        for view in list(self.page_views.values()):
            fn = getattr(view, "on_close", None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
        try:
            self.win.hide()
            self.win.deleteLater()
            self.qapp.quit()
        except Exception:
            log.debug("window close failed", exc_info=True)
        self._closed.set()

    @staticmethod
    def _shutdown_components(engine: Any, overlay: Any, voice: Any, hotkeys: Any = None) -> None:
        for name, obj in (("hotkeys", hotkeys), ("overlay", overlay), ("engine", engine), ("voice", voice)):
            if obj is None:
                continue
            try:
                obj.stop()
            except Exception:
                log.exception("Cannot stop %s", name)


def _main_window_class() -> Any:
    _QtCore, _QtGui, QtWidgets = _qt()

    class MainWindow(QtWidgets.QMainWindow):
        def __init__(self, app: CoachApp) -> None:
            super().__init__()
            self._app = app

        def closeEvent(self, e: Any) -> None:  # noqa: N802
            if self._app._closing or self._app.request_close():
                e.accept()
            else:
                e.ignore()

        def resizeEvent(self, e: Any) -> None:  # noqa: N802
            super().resizeEvent(e)
            try:
                if self._app.toast.isVisible():
                    self._app.toast._place()
            except Exception:
                pass

        def changeEvent(self, e: Any) -> None:  # noqa: N802
            super().changeEvent(e)
            try:     # back from minimised: refresh at once
                if e.type() == e.Type.WindowStateChange and not self.isMinimized() and not self._app._closing:
                    self._app._refresh_status()
            except Exception:
                pass
    return MainWindow


def _MainWindow(app: CoachApp) -> Any:  # noqa: N802 - class factory (PySide6 imported lazily)
    return _main_window_class()(app)


# ======================================================================================
# Module-level helpers (tests patch ui._report_function; engine factories)
# ======================================================================================
def _report_function(name: str) -> Callable[..., Any] | None:
    import importlib  # noqa: PLC0415

    for mod in ("treeaicoach.report", "treeaicoach.analysis"):
        try:
            m = importlib.import_module(mod)
        except Exception:
            continue
        fn = getattr(m, name, None)
        if callable(fn):
            return fn
    log.info("%s() is not available (report module missing)", name)
    return None


def _default_engine_factory(cfg: Config, voice: Any, detector: Any, frame_source: Any) -> Any:
    from treeaicoach.engine import CoachEngine  # noqa: PLC0415

    try:   # the UI owns the overlay windows and the global hotkeys (they survive engine rebuilds)
        return CoachEngine(cfg, voice, detector=detector, frame_source=frame_source,
                           manage_overlay=False, enable_hotkeys=False)
    except TypeError:
        return CoachEngine(cfg, voice, detector=detector, frame_source=frame_source)


def _default_overlay_factory(cfg: Config, provider: Callable[[], Any]) -> Any:
    try:
        from treeaicoach.overlay import OverlayManager  # noqa: PLC0415
    except Exception:
        log.info("Overlay module unavailable: overlay disabled", exc_info=log.isEnabledFor(logging.DEBUG))
        return None
    return OverlayManager(cfg, provider)


def _default_detector_factory(cfg: Config, learn: bool = True) -> Any:
    from treeaicoach.detector import create_detector  # noqa: PLC0415

    db = None
    try:
        from treeaicoach.champions import get_default_db  # noqa: PLC0415

        db = get_default_db()
    except Exception:
        log.debug("Champion database unavailable for the detector", exc_info=True)
    try:
        return create_detector(cfg.detector_backend, cfg.detection_threshold, db=db,
                               scale_store=dict(getattr(cfg, "icon_scale_by_res", None) or {}),
                               learn_cache=bool(learn))
    except TypeError:
        return create_detector(cfg.detector_backend, cfg.detection_threshold)


def _default_demo_source() -> Any:
    from treeaicoach.demo import DemoSource  # noqa: PLC0415

    return DemoSource()


def run_app(cfg: Config, *, demo: bool = False, smoke_seconds: float | None = None,
            _engine_factory: EngineFactory | None = None,
            _overlay_factory: Callable[[Config, Callable[[], Any]], Any] | None = None,
            _voice: Any = None, _detector_factory: Callable[[Config], Any] | None = None,
            _demo_source_factory: Callable[[], Any] | None = None, _hotkeys: bool = True,
            _save_path: Path | None = None) -> int:
    """Open the main window (blocking) and return 0 when it is closed; 1 if it cannot be created."""
    try:
        app = CoachApp(cfg, demo=demo, engine_factory=_engine_factory, overlay_factory=_overlay_factory,
                       voice=_voice, detector_factory=_detector_factory,
                       demo_source_factory=_demo_source_factory, hotkeys=_hotkeys, save_path=_save_path)
    except Exception:
        log.exception("Cannot create the main window")
        try:
            save_config(cfg if isinstance(cfg, Config) else Config(), _save_path)
        except Exception:
            pass
        return 1
    return app.run(smoke_seconds)


__all__ = ["run_app", "CoachApp", "fmt_clock", "fmt_int_fr", "state_key", "session_stats", "parse_geometry",
           "fit_geometry", "autostart_support", "get_windows_autostart", "set_windows_autostart", "app_icon_path"]
