"""CustomTkinter user interface of TreeAI Coach (ARCHITECTURE.md §8.2) - "régie esport" look (docs/DESIGN.md), French.

Public entry point: :func:`run_app`. It creates the voice, the detector, the analysis engine
(:mod:`treeaicoach.engine`, imported lazily) and the overlay manager, shows the main window and
returns 0 when the window is closed (everything stopped, configuration saved).

Threading rules
    * Widgets are only touched from the Tk main thread. Other threads (engine start/stop,
      report generation, radar rendering, hotkeys...) hand their results over through
      :class:`_Dispatcher` (a queue polled by ``root.after``).
    * The dashboard reads **thread-safe snapshots** only (``engine.get_status()``,
      ``engine.get_overlay_state()``) every 250 ms; the radar preview is rendered at 5 Hz by a
      background thread and pasted into a ``PhotoImage`` by the main thread (200 ms loop).
    * Every user callback is wrapped (:func:`_guarded` / :meth:`CoachApp.cb`): an exception is
      logged and shown as a French toast; the app never crashes.

Every setting change -> ``cfg`` updated + validated, saved after 500 ms (debounced) and applied
live (``engine.apply_config`` / ``overlay.apply_config`` / ``voice.set_params``, hotkeys rebound).
"""

from __future__ import annotations

import dataclasses
import inspect
import logging
import math
import sys
import threading
import time
import webbrowser  # noqa: F401 - tests patch ui.webbrowser.open
from collections import deque
from pathlib import Path
from typing import Any, Callable, Sequence

from PIL import Image

from treeaicoach import APP_NAME, __version__, paths, ui_kit
from treeaicoach.config import Config, save_config
from treeaicoach.ui_common import (  # noqa: F401 - public names re-exported
    ACCENT,
    ACCENT_DIM,
    ACCENT_HOVER,
    ALLY_RING,
    AUTO_VOICE,
    BG,
    BODY_FONTS,
    BORDER,
    BORDER_GOLD,
    BTN_H,
    BTN_H_SMALL,
    CAPTURE_BACKENDS,
    CARD_PAD,
    CONTENT_MAX,
    CTL_GAP,
    CTL_H,
    DANGER,
    DANGER_DARK,
    DANGER_HOVER,
    DANGER_MODES,
    DEFAULT_H,
    DEFAULT_W,
    DETECTOR_FIELDS,
    DETECTORS,
    DIM,
    DISPATCH_ICONIC_MS,
    DISPATCH_MS,
    DISPLAY_FONTS,
    EM_DASH,
    ENEMY_RING,
    ENGINE_LABELS,
    ENGINE_RESTART_FIELDS,
    FOG_MODES,
    GAMES_PAGE,
    GAUGE_UI_COLORS,
    GOLD,
    GOLD_DARK,
    GOLD_HOVER,
    HIDDEN_SETTINGS,
    HOTKEY_CHOICES,
    HOTKEY_FIELDS,
    HOVER,
    HUD_POSITIONS,
    ICON_BTN,
    IDLE_LOOP_MS,
    JOURNAL_MAX,
    LEVEL_COLORS,
    LINE,
    LINE_STRONG,
    LINK_H,
    MIN_H,
    MIN_W,
    MINIMAP_MODES,
    MINIMAP_SIDES,
    MUTED,
    ON_ACCENT,
    ON_DANGER,
    ON_GOLD,
    OVERLAY_MODES,
    PAGE_ALIASES,
    PAGE_PAD,
    PAGES,
    PANEL,
    PANEL_HI,
    PANEL_LO,
    PERF_MODES,
    PILL_TEXT,
    _PLAIN_SCALE,
    PLAYS_POSITIONS,
    PREBUILD_GAP_MS,
    PREBUILD_ORDER,
    PREVIEW_MS,
    PULSE_MS,
    RADAR_POSITIONS,
    RADAR_PX,
    RADIUS,
    RADIUS_DIALOG,
    RAISED,
    ROLE_GAMER,
    ROW_CTL_GAP,
    ROW_LINE,
    ROW_MIN_H,
    ROW_PAD_Y,
    RUN_KEY,
    RUN_VALUE,
    SAFE,
    SAVE_DEBOUNCE_MS,
    SECTION_GAP,
    SETTINGS_TABS,
    SIDEBAR_W,
    SLIDER_KNOB_R,
    STATE_INFO,
    STATUS_ICONIC_MS,
    STATUS_IDLE_MS,
    STATUS_MS,
    SUNKEN,
    SURFACE,
    SWITCH_OFF,
    TAB_GAP,
    TEAL,
    TEXT,
    THREAT_COLORS,
    THREAT_LABELS,
    TIP_UI_COLORS,
    TOAST_MS,
    TOGGLE_H,
    TOGGLE_RADIUS,
    TOGGLE_SMALL,
    TOGGLE_W,
    TRACK,
    UI_SCALINGS,
    UPDATE_CHECK_DELAY_MS,
    VOICE_FIELDS,
    _VOICE_FR,
    VOICE_LEVELS,
    WARNING,
    WARNING_BG,
    WIDE_MAX,
    _Dispatcher,
    Dropdown,
    _Fonts,
    HeroBanner,
    _LazyPages,
    _RadarWorker,
    Segmented,
    Toggle,
    _alert_entry,
    app_icon_path,
    _apply_theme,
    autostart_command,
    autostart_support,
    champion_name,
    circle_icon,
    detector_short,
    display_weight,
    _ellipsize,
    flat_placeholder,
    _fmt_ago,
    _game_json_path,
    fmt_clock,
    fmt_decimal_fr,
    fmt_game_date,
    fmt_int_fr,
    game_datetime,
    game_field,
    game_result,
    get_windows_autostart,
    _guarded,
    health_text,
    _hex_rgb,
    _import_ctk,
    _int_or_none,
    load_logo,
    nav_icon,
    open_path,
    _pick_display,
    _pick_family,
    pick_font,
    _plain_classes,
    precision_color,
    radar_placeholder,
    rounded_on_bg,
    scroll_frame_class,
    session_stats,
    set_windows_autostart,
    square_icon,
    state_key,
    threat_fraction,
    toggle_image,
    ui_scale_of,
    ui_text,
)
from treeaicoach.ui_dialogs import DialogsMixin
from treeaicoach.ui_page_alerts import AlertsPageMixin
from treeaicoach.ui_page_analysis import AnalysisPageMixin
from treeaicoach.ui_page_dashboard import DashboardPageMixin
from treeaicoach.ui_page_overlay import OverlayPageMixin
from treeaicoach.ui_page_settings import SettingsPageMixin

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ lazy pages (patched by tests: stays here)
PREBUILD_DELAY_MS = 1500          # other pages are built in idle slots after this delay (0 = on first visit only)

# ======================================================================================
# The application
# ======================================================================================
EngineFactory = Callable[[Config, Any, Any, Any], Any]


class CoachApp(DashboardPageMixin, AlertsPageMixin, OverlayPageMixin, AnalysisPageMixin, SettingsPageMixin,
               DialogsMixin):
    """Main window + orchestration of engine / overlay / voice. Build on the Tk thread."""

    def __init__(self, cfg: Config, *, demo: bool = False,
                 engine_factory: EngineFactory | None = None,
                 overlay_factory: Callable[[Config, Callable[[], Any]], Any] | None = None,
                 voice: Any = None, detector_factory: Callable[[Config], Any] | None = None,
                 demo_source_factory: Callable[[], Any] | None = None,
                 hotkeys: bool = True, save_path: Path | None = None) -> None:
        ctk = _import_ctk()
        self.ctk = ctk
        self.cfg: Config = (cfg if isinstance(cfg, Config) else Config()).validated()
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
        self._busy = False              # engine start/stop/rebuild in progress
        self._closing = False
        self._closed = threading.Event()
        self._muted = False
        self._move_mode = False
        self._save_job: str | None = None
        self._overlay_preview_job: str | None = None
        self._journal: deque[tuple[float | None, int, str]] = deque(maxlen=JOURNAL_MAX)
        self._journal_sig: tuple = ()
        self._journal_hidden: set = set()
        self._last_alert_seen: tuple[str, float] | None = None
        self._ai_test: tuple[bool, str] | None = None     # last "Tester la clé" result (ok, short text)
        self._ai_test_busy = False
        self._last_status_alert: str | None = None
        self._last_state_key = ""
        self._games: list[dict] = []
        self._voices: list[str] = []
        self._current_page = ""
        self._pulse_phase = 0.0
        self._enemy_cache: dict[tuple, Any] = {}
        self._images: dict[str, Any] = {}     # keep CTkImage references alive
        self._widgets_by_field: dict[str, Callable[[], None]] = {}   # field -> refresh function
        self._row_slots: list[Any] = []       # setting rows (their description wraps with the window)
        self._wrap_width = 0
        self._dispatcher = _Dispatcher()
        self._eng_lock = threading.Lock()
        self._created_engines: list[Any] = []   # every engine built (stopped again at close)
        self._built: set[str] = set()            # pages built so far (lazy)
        self._failed_pages: set[str] = set()     # pages whose builder failed (an error page is shown)
        self._pregame_data: dict[str, Any] | None = None
        self._pregame_sig: Any = None
        self._pregame_busy = False
        self._games_sig: Any = None              # history read by refresh_games
        self._games_shown_sig: Any = ()          # history drawn in the Analyses table
        self._game_icons: dict[str, Any] = {}    # alias -> PIL portrait (loaded off the Tk thread)
        self._games_limit = GAMES_PAGE
        self._lcu_text = ""
        self._lcu_polled = 0.0
        self._status_job: str | None = None
        self._update_info: Any = None             # updater result (settings page, may be built later)
        self._update_busy = False
        self._diag_watch = False                  # a diagnostic bundle is recording (engine.start_diagnostic)
        self._wrap_labels: list[tuple[Any, int]] = []   # (label, inset) wrapped to the content column
        self._radar_worker = _RadarWorker(self._radar_source, RADAR_PX)
        self._radar_seq = -1
        self._radar_live = False

        _apply_theme(ctk)
        self._ui_scale = ui_scale_of(self.cfg)
        try:   # Windows display scaling is applied on top by CustomTkinter (per-monitor DPI factor)
            ctk.set_widget_scaling(self._ui_scale)
            ctk.set_window_scaling(self._ui_scale)       # a bigger interface gets a bigger window
            ctk.ScalingTracker.update_loop_interval = 1000     # DPI-change poll: 1 s instead of 100 ms
        except Exception:
            log.debug("Cannot set the UI scaling", exc_info=True)
        self.root = ctk.CTk()
        # NB: never withdraw() the CTk root before mainloop: on Windows CTk re-applies the
        # saved "withdrawn" state after colouring the title bar and the window never shows.
        self.root.title(APP_NAME)
        self.root.configure(fg_color=BG)
        self.root.report_callback_exception = self._tk_exception
        _body = _pick_family(self.root)
        self.fonts = _Fonts(ctk, _body, _pick_display(self.root, _body))
        self._PFrame, self._PLabel = _plain_classes()
        _PLAIN_SCALE[0] = self._scaled(1000) / 1000
        try:   # plain widgets follow a DPI change like the CTk ones (window moved to another monitor)
            ctk.ScalingTracker.add_widget(self._on_scaling, self.root)
        except Exception:
            log.debug("Cannot track the DPI scaling", exc_info=True)
        self._set_window_icon(self.root)
        self._apply_geometry()
        self.root.minsize(*getattr(self, "_min_size", (MIN_W, MIN_H)))
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self.content = ctk.CTkFrame(self.root, fg_color=BG, corner_radius=0)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        # Pages are built lazily: the first one now, each other one on its first visit (or when
        # an attribute of a page not built yet is read: see __getattr__).
        self._page_builders: dict[str, Callable[[], Any]] = {
            "dashboard": self._build_dashboard, "analysis": self._build_analysis_page,
            "settings": self._build_settings_page, "help": self._build_help_page}
        self.pages: _LazyPages = _LazyPages(self._build_page, self._page_builders)
        self._toast_frame: Any = None
        self._toast_job: str | None = None
        self._compact: bool | None = None
        self._layout_job: str | None = None
        self._settings_tab = ""                  # tab shown on the Réglages page
        self._post_game_watch: str | None = None  # path of the newest game when a game ended (post-game toast)
        self.root.bind("<Configure>", self._on_root_configure, add="+")
        self._bind_shortcuts()
        self.show_page("dashboard")              # status first: the app always opens on "En jeu"
        self.root.protocol("WM_DELETE_WINDOW", self.request_close)
        self.root.after(1200, self._first_run_dialogs)
        self.root.after(350, self._ensure_visible)
        self.root.after(1500, self._ensure_visible)

        self._radar_worker.start()
        self.root.after(DISPATCH_MS, self._dispatch_loop)
        self._status_job: str | None = self.root.after(STATUS_MS, self._status_loop)
        self.root.after(PREVIEW_MS, self._preview_loop)
        self.root.after(PULSE_MS, self._pulse_loop)
        self._start_backend()
        if paths.is_frozen() and self.cfg.check_updates_on_start:   # silent update check (updater.py)
            self.root.after(UPDATE_CHECK_DELAY_MS, self._startup_update_check)
        self.root.after(2500, self.cb(self._check_last_update))      # did the last update apply?
        self.root.after(1800, self.cb(self.refresh_games))           # dashboard "avant la partie" + table
        if PREBUILD_DELAY_MS:
            self.root.after(PREBUILD_DELAY_MS, lambda: self.root.after_idle(self._prebuild_next))

    # ------------------------------------------------------------------ lazy pages
    def _build_page(self, key: str) -> Any:
        """Build one page (Tk thread), grid it hidden; an error page if its builder fails."""
        building = self.__dict__.setdefault("_building", set())
        if key in building:
            raise KeyError(key)
        building.add(key)
        t0 = time.perf_counter()
        try:
            try:
                page = self._page_builders[key]()
            except Exception:
                log.exception("Cannot build page %s", key)
                self._failed_pages.add(key)
                page = self._error_page(key)
            page.grid(row=0, column=0, sticky="nsew")
            page.grid_remove()
            dict.__setitem__(self.pages, key, page)
            self._built.add(key)
        finally:
            building.discard(key)
        log.debug("page %s built in %.0f ms", key, 1000 * (time.perf_counter() - t0))
        try:
            self.root.after_idle(lambda: self._wrap_rows(int(self.content.winfo_width()
                                                             / max(0.5, self._scaled(100) / 100)), force=True))
        except Exception:
            pass
        return page

    def _prebuild_next(self) -> None:
        """Build the next page not visited yet while the app is idle (one page per idle slot), so
        that even a first visit is instant. Never while minimised or during a game."""
        if self._closing:
            return
        try:
            pending = [k for k in PREBUILD_ORDER if k not in self._built]
            tabs = [(k, t) for k in PREBUILD_ORDER if k in self._built
                    for t in getattr(dict.get(self.pages, k), "pending_tabs", lambda: [])()]
            if not pending and not tabs:
                return
            if self._iconic() or self._in_game() or self._busy or self.__dict__.get("_building"):
                self.root.after(PREBUILD_GAP_MS * 10, self._prebuild_next)
                return
            if pending:
                self.pages[pending[0]]
            else:                            # one SECTION per idle slot: never a long freeze
                key, tab = tabs[0]
                done = dict.get(self.pages, key).step_tab(tab)
                if done and tab == "Affichage":     # its preview (sample game screen), off the Tk thread
                    self._dispatcher.run(_prewarm_preview, None, None, name="TreeAI-ui-prewarm")
            self.root.after(PREBUILD_GAP_MS, lambda: self.root.after_idle(self._prebuild_next))
        except Exception:
            log.exception("page prebuild failed")

    def build_all_pages(self) -> None:
        """Build every page (and every tab of a page) not built yet (tests, diagnostics)."""
        for key in self._page_builders:
            if key not in self._built and not self.__dict__.get("_building"):
                self.pages[key]
            page = dict.get(self.pages, key)
            for tab in list(getattr(page, "pending_tabs", lambda: [])()):
                page.ensure_tab(tab)

    # ------------------------------------------------------------------ infrastructure
    def cb(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Wrap any widget callback (lambda...) like :func:`_guarded`."""
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                log.exception("UI callback failed")
                try:
                    self.show_error(f"Une erreur est survenue : {exc}")
                except Exception:
                    pass
                return None
        return wrapper

    def _tk_exception(self, exc_type: Any, exc: Any, tb: Any) -> None:
        log.error("Unhandled Tk callback exception", exc_info=(exc_type, exc, tb))
        try:
            self.show_error(f"Une erreur inattendue est survenue : {exc}")
        except Exception:
            pass

    def _ensure_visible(self) -> None:
        """Make sure the main window is shown and in front (CTk/Windows title-bar quirk)."""
        try:
            if self._closing or not self.root.winfo_exists():
                return
            if self.root.state() in ("withdrawn", "iconic"):
                self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(200, lambda: self._safe_untop())
            self.root.focus_force()
        except Exception:
            log.debug("ensure_visible failed", exc_info=True)

    def _safe_untop(self) -> None:
        try:
            self.root.attributes("-topmost", False)
        except Exception:
            pass

    def _dispatch_loop(self) -> None:
        if self._closing:
            return
        self._dispatcher.drain()
        self._dispatch_n = (getattr(self, "_dispatch_n", 0) + 1) % 20
        if self._dispatch_n == 0:
            self._dispatch_slow = self._iconic()
        self.root.after(DISPATCH_ICONIC_MS if getattr(self, "_dispatch_slow", False) else DISPATCH_MS,
                        self._dispatch_loop)

    def _set_window_icon(self, win: Any) -> None:
        """Window / taskbar icon (``.ico`` on Windows, PNG photo elsewhere)."""
        try:
            if sys.platform == "win32":
                try:  # own taskbar group + icon when run from python.exe
                    import ctypes  # noqa: PLC0415

                    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TreeAI.Coach")
                except Exception:
                    pass
                ico = app_icon_path("ico")
                if ico is not None:
                    win.iconbitmap(str(ico))
                    win.after(260, lambda: self._safe_iconbitmap(win, ico))   # CTk resets it after 200 ms
                    return
            from PIL import ImageTk  # noqa: PLC0415

            photo = ImageTk.PhotoImage(load_logo(64), master=win)
            self._images[f"icon-{id(win)}"] = photo
            win.iconphoto(True, photo)
        except Exception:
            log.debug("Cannot set the window icon", exc_info=True)

    @staticmethod
    def _safe_iconbitmap(win: Any, ico: Path) -> None:
        try:
            if win.winfo_exists():
                win.iconbitmap(str(ico))
        except Exception:
            pass

    def _apply_geometry(self) -> None:
        """Restore the saved window geometry, kept on screen; default 1100x720 centred."""
        sw = max(800, int(self.root.winfo_screenwidth()))
        sh = max(600, int(self.root.winfo_screenheight()))
        geo = self.cfg.ui_geometry or ""
        w, h, x, y = DEFAULT_W, DEFAULT_H, None, None
        try:
            import re  # noqa: PLC0415

            m = re.fullmatch(r"(\d+)x(\d+)(?:([+-]-?\d+)([+-]-?\d+))?", geo.strip())
            if m:
                w, h = int(m.group(1)), int(m.group(2))
                if m.group(3) is not None:
                    x, y = int(m.group(3)), int(m.group(4))
        except Exception:
            pass
        # CTk geometry: width / height in logical px (x window scaling = DPI x ui scale), x / y physical
        try:
            k = max(0.5, float(self.ctk.ScalingTracker.get_window_scaling(self.root)))
        except Exception:
            k = 1.0
        lw, lh = int(sw / k), int(sh / k)                      # screen size in logical px
        min_w, min_h = min(MIN_W, lw - 16), min(MIN_H, lh - 72)
        self._min_size = (max(640, min_w), max(480, min_h))
        w = min(max(w, self._min_size[0]), max(self._min_size[0], lw - 16))
        h = min(max(h, self._min_size[1]), max(self._min_size[1], lh - 72))
        pw, ph = int(w * k), int(h * k)
        if x is None or y is None or x < -pw + 120 or y < 0 or x > sw - 120 or y > sh - 80:
            x, y = max(0, (sw - pw) // 2), max(0, (sh - ph) // 3)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _on_root_configure(self, event: Any) -> None:
        if event.widget is not self.root or self._closing:
            return
        if self._layout_job is not None:
            try:
                self.root.after_cancel(self._layout_job)
            except Exception:
                pass
        self._layout_job = self.root.after(60, self._apply_layout)

    def _apply_layout(self) -> None:
        """Responsive tweaks: short toolbar labels and text wrapping at small window sizes."""
        self._layout_job = None
        try:
            scale = max(0.5, self._scaled(100) / 100)
            width = self.root.winfo_width() / scale
            compact = width < 1240
            if "dashboard" in self._built and compact != self._compact:
                self._compact = compact
                self.btn_test_voice.configure(text="Voix" if compact else "Tester la voix")
                self.btn_test_overlay.configure(text="Overlay" if compact else "Tester l'overlay")
                self._demo_button_text()
            if "dashboard" in self._built:
                sub = getattr(dict.get(self.pages, "dashboard"), "subtitle", None)
                if sub is not None:      # narrow window: the toolbar needs the room next to the title
                    narrow = width < 1080
                    if narrow and sub.winfo_manager():
                        sub.grid_remove()
                    elif not narrow and not sub.winfo_manager():
                        sub.grid()
                en_w = self.enemies_card.winfo_width() / scale
                if en_w > 50 and self.enemies_card.winfo_ismapped():
                    self.jungler_lbl.configure(wraplength=int(max(200, en_w - 40)))
                    self.coach_role_lbl.configure(wraplength=int(max(160, en_w - 150)))
                    self.coach_tip_lbl.configure(wraplength=int(max(240, en_w - 40)))
            self._wrap_rows(int(self.content.winfo_width() / scale))
        except Exception:
            log.debug("Layout update failed", exc_info=True)

    def _wrap_rows(self, content_w: int, force: bool = False) -> None:
        """Setting descriptions wrap before the control on the right (any window width)."""
        if content_w < 200 or (content_w == self._wrap_width and not force):
            return
        self._wrap_width = content_w
        scale = max(0.5, self._scaled(100) / 100)
        col_w = min(content_w - 2 * PAGE_PAD - 10, CONTENT_MAX)    # centred column (minus the scrollbar)
        row_w = col_w - 2 * CARD_PAD - 2
        for slot in list(self._row_slots):
            lbl = getattr(slot, "desc_label", None)
            if lbl is None:
                continue
            try:
                if not lbl.winfo_exists():
                    continue
                sw = slot.winfo_reqwidth() / scale
                below = row_w - sw - ROW_CTL_GAP < 260   # wide control, narrow window: control under the text
                if below != getattr(slot, "_below", False):
                    slot._below = below
                    if below:
                        slot.grid_configure(row=2, column=0, rowspan=1, sticky="w", padx=0, pady=(CTL_GAP, 0))
                    else:
                        slot.grid_configure(row=0, column=1, rowspan=2, sticky="e", padx=(ROW_CTL_GAP, 0), pady=0)
                lbl.configure(wraplength=int(max(200, min(640, row_w if below else row_w - sw - ROW_CTL_GAP))))
            except Exception:
                pass
        for lbl, inset in list(self._wrap_labels):
            try:
                if lbl.winfo_exists():
                    lbl.configure(wraplength=int(max(240, col_w - inset)))
            except Exception:
                pass

    def _demo_button_text(self) -> None:
        if self.demo:
            text = "Fin démo" if self._compact else "Quitter la démo"
        else:
            text = "Démo" if self._compact else "Mode démo"
        self._set_text(self.btn_demo, text)

    # ------------------------------------------------------------------ small widget factories
    def _on_scaling(self, widget_scaling: float, _window_scaling: float) -> None:
        """CustomTkinter detected a new DPI factor: rescale fonts / paddings of the plain widgets."""
        try:
            k = float(widget_scaling)
            if abs(k - _PLAIN_SCALE[0]) > 1e-3:
                _PLAIN_SCALE[0] = k
                self._PFrame.rescale_all()
        except Exception:
            log.debug("plain widgets rescale failed", exc_info=True)

    def _frame(self, parent: Any, fg_color: str = "transparent", **kw: Any) -> Any:
        """Layout-only container (plain Tk frame: cheap, background of its parent)."""
        return self._PFrame(parent, fg_color=fg_color, **kw)

    def _hline(self, parent: Any, color: str = LINE) -> Any:
        """1 px separator (the design uses lines, not nested boxes)."""
        f = self._PFrame(parent, fg_color=color)
        f.configure(height=1)
        return f

    def _caption(self, parent: Any, text: str, color: str = DIM, **kw: Any) -> Any:
        """Caption style: small spaced capitals (column headers, section titles)."""
        return self._label(parent, str(text).upper(), self.fonts.caps, color, **kw)

    def _label(self, parent: Any, text: str = "", font: Any = None, color: str = TEXT, **kw: Any) -> Any:
        """Text label. A plain Tk label (cheap) unless it needs CTk features (fixed width, image)."""
        if "width" in kw or "image" in kw or "corner_radius" in kw:
            kw.setdefault("height", 1)      # size to the text (CTkLabel's default minimum is 28 px)
            return self.ctk.CTkLabel(parent, text=text, font=font or self.fonts.body, text_color=color,
                                     fg_color="transparent", **kw)
        return self._PLabel(parent, text=text, font=font or self.fonts.body, text_color=color, **kw)

    def _icon(self, kind: str, size: int = 18, color: str = MUTED) -> Any:
        key = f"{kind}-{size}-{color}"
        img = self._images.get(key)
        if img is None:
            if kind in ui_kit.EXTRA_ICONS:     # drawn at 2x for HiDPI, shown at ``size``
                pil = ui_kit.extra_icon(kind, size * 2, color)
            else:
                pil = nav_icon(kind, size * 2, color)
            img = self.ctk.CTkImage(light_image=pil, dark_image=pil, size=(size, size))
            self._images[key] = img
        return img

    def _image_label(self, parent: Any, pil: Any, size: tuple[int, int], key: str, **kw: Any) -> Any:
        """A static image as one plain Tk label, scaled like the CTk widgets (a CTkLabel with an image
        costs three windows: about 5x slower to build and to map, which adds up in lists)."""
        k = _PLAIN_SCALE[0]
        w, h = max(1, int(round(size[0] * k))), max(1, int(round(size[1] * k)))
        ck = f"p-{key}-{w}x{h}"
        photo = self._images.get(ck)
        if photo is None or getattr(photo, "_src", None) is not pil:
            from PIL import Image, ImageTk  # noqa: PLC0415

            img = pil if pil.size == (w, h) else pil.resize((w, h), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img, master=self.root)
            photo._src = pil  # type: ignore[attr-defined]
            self._images[ck] = photo
        return self._PLabel(parent, image=photo, **kw)

    def _icon_label(self, parent: Any, kind: str, size: int = 14, color: str = MUTED, **kw: Any) -> Any:
        """A static icon (see :meth:`_image_label`)."""
        src = f"src-{kind}-{size}-{color}"
        pil = self._images.get(src)
        if pil is None:
            pil = (ui_kit.extra_icon(kind, size * 3, color) if kind in ui_kit.EXTRA_ICONS
                   else nav_icon(kind, size * 3, color))
            self._images[src] = pil
        return self._image_label(parent, pil, (size, size), f"icon-{kind}-{color}", **kw)

    def _light_icon_button(self, parent: Any, kind: str, size: int, color: str, command: Callable[[], Any],
                           tip: str | None = None) -> Any:
        """A small clickable icon (plain label + hover background): for buttons repeated in lists."""
        lbl = self._icon_label(parent, kind, size, color, cursor="hand2", anchor="center")
        lbl.configure(padx=6, pady=4)
        base = lbl.cget("bg")
        lbl.bind("<Enter>", lambda _e: lbl.configure(bg=PANEL_HI), add="+")
        lbl.bind("<Leave>", lambda _e: lbl.configure(bg=base), add="+")
        lbl.bind("<Button-1>", lambda _e: self.cb(command)(), add="+")
        if tip:
            self._tip(lbl, tip)
        return lbl

    def _tip(self, widget: Any, text: str | Callable[[], str]) -> None:
        """Hover tooltip (delayed, never raises)."""
        try:
            ui_kit.Tooltip(widget, text, bg=PANEL_HI, fg=TEXT, border=GOLD_DARK,
                           font=(self.fonts.family, self._font_px(12)))
        except Exception:
            log.debug("Tooltip failed", exc_info=True)

    def _hoverable(self, frame: Any, normal: str, hover: str, state: dict | None = None) -> None:
        """Border highlight while the mouse is over ``frame`` (or any of its children).

        ``state`` (optional dict) may hold a ``"border"`` key overriding ``normal`` (live colour).
        """
        def inside() -> bool:
            try:
                x, y = frame.winfo_pointerxy()
                w = frame.winfo_containing(x, y)
                while w is not None:
                    if w is frame:
                        return True
                    w = w.master
            except Exception:
                pass
            return False

        def enter(_e: Any = None) -> None:
            try:
                frame.configure(border_color=hover)
            except Exception:
                pass

        def leave(_e: Any = None) -> None:
            if inside():
                return
            try:
                frame.configure(border_color=(state or {}).get("border", normal))
            except Exception:
                pass

        def bind_all(w: Any) -> None:
            try:
                w.bind("<Enter>", enter, add="+")
                w.bind("<Leave>", leave, add="+")
            except Exception:
                pass
            try:
                for ch in w.winfo_children():
                    bind_all(ch)
            except Exception:
                pass

        frame.after(50, lambda: bind_all(frame))

    def _button(self, parent: Any, text: str, command: Callable[[], Any], kind: str = "secondary",
                icon: str | None = None, **kw: Any) -> Any:
        """Button in one of 4 styles. Two heights only: 34 (default) and 30 (``height`` < 32: toolbars,
        table rows), so that buttons line up everywhere."""
        styles = {
            "primary": dict(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ON_ACCENT, border_width=0),
            "secondary": dict(fg_color=PANEL_HI, hover_color=HOVER, text_color=TEXT, border_width=1,
                              border_color=LINE_STRONG),
            "ghost": dict(fg_color="transparent", hover_color=PANEL_HI, text_color=TEXT, border_width=0),
            "danger": dict(fg_color=DANGER_DARK, hover_color=DANGER_HOVER, text_color=ON_DANGER, border_width=1,
                           border_color=DANGER),
        }
        h = kw.pop("height", None)
        h = BTN_H if h is None or h >= 32 else BTN_H_SMALL
        opts: dict[str, Any] = dict(height=h, corner_radius=RADIUS, font=self.fonts.button,
                                    text_color_disabled=DIM)
        if text:
            opts["width"] = 0          # size to the text (+ the padding below)
        opts.update(styles.get(kind, styles["secondary"]))
        isz = 16 if h >= BTN_H else 15
        if icon:
            col = ON_ACCENT if kind == "primary" else MUTED
            opts["image"] = self._icon(icon, isz, col)
            opts["compound"] = "left"
        opts.update(kw)
        if kind == "primary" and opts.get("state") == "disabled":     # a disabled primary must not look active
            opts.update(fg_color=PANEL_HI, image=self._icon(icon, isz, DIM) if icon else None)
        if text:
            opts.setdefault("border_spacing", 8 if h >= BTN_H else 6)    # inner padding around the label
        btn = self.ctk.CTkButton(parent, text=text, command=self.cb(command), **opts)
        btn._tree_kind = kind  # type: ignore[attr-defined]
        btn._tree_icon = icon  # type: ignore[attr-defined]
        btn._tree_isz = isz  # type: ignore[attr-defined]
        return btn

    def _btn_state(self, btn: Any, enabled: bool) -> None:
        """Enable / disable a button made by :meth:`_button` (a disabled primary turns grey)."""
        try:
            btn.configure(state="normal" if enabled else "disabled")
            if getattr(btn, "_tree_kind", "") == "primary":
                icon = getattr(btn, "_tree_icon", None)
                btn.configure(fg_color=ACCENT if enabled else PANEL_HI)
                if icon:
                    btn.configure(image=self._icon(icon, getattr(btn, "_tree_isz", 16), ON_ACCENT if enabled else DIM))
        except Exception:
            log.debug("button state failed", exc_info=True)

    def _page(self, title: str, subtitle: str, scroll: bool = True,
              icon: str | None = None, max_width: int = CONTENT_MAX) -> tuple[Any, Any, Any]:
        """(page frame, header right slot, body frame).

        Header (title + subtitle, actions on the right, tabs below) and body share one centred
        column at most ``max_width`` px wide (no controls stuck to the far edge of a big window);
        the scrollbar stays on the window edge.
        """
        ctk = self.ctk
        page = ctk.CTkFrame(self.content, fg_color=BG, corner_radius=0)
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(2, weight=1)
        head = self._frame(page)
        head.grid(row=0, column=0, sticky="ew", padx=PAGE_PAD, pady=(26, 0))
        head.grid_columnconfigure(0, weight=1)
        tl = self._frame(head)
        tl.grid(row=0, column=0, sticky="w")
        self._label(tl, title, self.fonts.title, TEXT, anchor="w").grid(row=0, column=0, sticky="w")
        sub = self._label(tl, subtitle, self.fonts.small, MUTED, anchor="w")
        sub.grid(row=1, column=0, sticky="w", pady=(2, 0))
        page.subtitle = sub  # type: ignore[attr-defined]
        right = self._frame(head, width=1, height=1)
        right.grid(row=0, column=1, sticky="e")
        page.head = head  # type: ignore[attr-defined]
        rule = self._hline(page)
        rule.grid(row=1, column=0, sticky="ew", padx=PAGE_PAD, pady=(14, 0))
        targets: list[tuple[Any, int, int]] = [(head, 0, 0), (rule, 0, 0)]
        if scroll:
            body = scroll_frame_class()(page, fg_color=BG)
            body.grid(row=2, column=0, sticky="nsew", padx=(0, 4), pady=(0, 2))
            body.grid_columnconfigure(0, weight=1)
            page.scroll_frame = body  # type: ignore[attr-defined]
            inner = self._frame(body)
            inner.grid(row=0, column=0, sticky="nsew", padx=PAGE_PAD, pady=(22, 28))
            inner.grid_columnconfigure(0, weight=1)
            targets.append((inner, 0, -10))          # the scrollbar already takes 10 px on the right
            self._center_column(page, targets, max_width)
            return page, right, inner
        body = self._frame(page)
        body.grid(row=2, column=0, sticky="nsew", padx=PAGE_PAD, pady=(18, 20))
        targets.append((body, 0, 0))
        self._center_column(page, targets, max_width)
        return page, right, body

    def _center_column(self, page: Any, targets: Sequence[tuple[Any, int, int]], max_width: int) -> None:
        """Keep ``targets`` (gridded with padx) in a centred column of at most ``max_width`` px."""
        state = {"pad": None, "job": None}

        def apply() -> None:
            state["job"] = None
            try:
                scale = max(0.5, self._scaled(100) / 100)
                w = page.winfo_width() / scale
                if w < 50:
                    w = self.content.winfo_width() / scale
                if w < 50:
                    return
                pad = int(max(PAGE_PAD, (w - max_width) / 2))
                if pad == state["pad"]:
                    return
                state["pad"] = pad
                for wdg, dl, dr in targets:
                    wdg.grid_configure(padx=(max(8, pad + dl), max(8, pad + dr)))
            except Exception:
                log.debug("centre column failed", exc_info=True)

        def on_conf(_e: Any = None) -> None:
            if state["job"] is None:
                state["job"] = page.after(30, apply)

        page.bind("<Configure>", on_conf, add="+")
        page.center_apply = apply  # type: ignore[attr-defined]

    def _section(self, parent: Any, row: int, title: str, subtitle: str | None = None,
                 icon: str | None = None) -> Any:
        """A titled section: title (display face) + optional one-line explanation above a single
        card (SURFACE, 1 px border, rows separated by 1 px lines). Returns the card's content frame."""
        ctk = self.ctk
        wrap = self._frame(parent)
        wrap.grid(row=row, column=0, sticky="ew", pady=(0, SECTION_GAP))
        wrap.title = title  # type: ignore[attr-defined]
        try:
            parent._sections.append(wrap)
        except AttributeError:
            parent._sections = [wrap]
        wrap.grid_columnconfigure(0, weight=1)
        th = self._frame(wrap)
        th.grid(row=0, column=0, sticky="ew")
        th.grid_columnconfigure(1, weight=1)
        self._label(th, title, self.fonts.h2, TEXT, anchor="w").grid(row=0, column=0, sticky="w")
        wrap.head = th  # type: ignore[attr-defined]
        if subtitle:
            sub = self._label(wrap, subtitle, self.fonts.small, MUTED, anchor="w", justify="left", wraplength=760)
            sub.grid(row=1, column=0, sticky="w", pady=(4, 0))
            self._wrap_labels.append((sub, 0))
        card = ctk.CTkFrame(wrap, fg_color=SURFACE, corner_radius=RADIUS_DIALOG, border_width=1, border_color=LINE)
        card.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        card.grid_columnconfigure(0, weight=1)
        body = self._frame(card)
        body.grid(row=0, column=0, sticky="ew", padx=CARD_PAD, pady=4)
        body.grid_columnconfigure(0, weight=1)
        body._rows = 0  # type: ignore[attr-defined]
        body.card = card  # type: ignore[attr-defined]
        body.wrap = wrap  # type: ignore[attr-defined]
        return body

    def _section_button(self, body: Any, text: str, command: Callable[[], Any], icon: str | None = None,
                        tip: str | None = None) -> Any:
        """A small action on the right of a section title ("Tester la voix", "Déplacer"...)."""
        b = self._button(body.wrap.head, text, command, "ghost", icon=icon, height=BTN_H_SMALL)
        col = len(body.wrap.head.grid_slaves(row=0)) + 1
        b.grid(row=0, column=col, sticky="e", padx=(CTL_GAP, 0))
        if tip:
            self._tip(b, tip)
        return b

    def _set_section_visible(self, body: Any, visible: bool) -> None:
        """Show / hide a whole section (kept hidden by the tabs too, e.g. "Radar" outside the radar mode)."""
        wrap = getattr(body, "wrap", None)
        if wrap is None:
            return
        wrap.hidden = not visible  # type: ignore[attr-defined]
        page_tab = getattr(wrap, "tab_shown", True)
        if visible and page_tab:
            wrap.grid()
        elif not visible:
            wrap.grid_remove()

    def _tabs(self, page: Any, body: Any, groups: Sequence[tuple[str, Sequence[str]]],
              default: str | None = None, on_select: Callable[[str], Any] | None = None,
              lazy: dict[str, Callable[[], Any]] | None = None) -> dict[str, Any]:
        """Underlined tabs in the page header showing one group of sections at a time.

        ``groups`` = (tab label, section titles); sections not listed go to the last tab
        (usually "Avancé", i.e. collapsed by default). ``lazy`` = {tab label: builder}: that tab's
        sections are built on its first selection (or by ``page.ensure_tab(label)``: idle prebuild,
        a widget read before), so a page with many tabs opens fast. ``on_select(label)`` runs after
        every change of tab, clicked or programmatic (``page.select_tab``): lazy loads (progress,
        replay, preview). Returns {label: button}.
        """
        ctk = self.ctk
        sections = list(getattr(body, "_sections", []))
        pending = dict(lazy or {})
        bar = self._frame(page.head)
        bar.grid(row=1, column=0, columnspan=2, sticky="w", pady=(16, 0))
        labels = [g for g, _t in groups]
        owner: dict[int, str] = {}
        for g, titles in groups:
            for card in sections:
                if getattr(card, "title", None) in titles:
                    owner[id(card)] = g
        for card in sections:
            owner.setdefault(id(card), labels[-1])
        btns: dict[str, Any] = {}
        unders: dict[str, Any] = {}
        state = {"cur": None}

        running: dict[str, Any] = {}      # tab label -> its builder generator, part-way through

        cold: list[tuple[str, Any]] = []     # (tab, section) built by the idle prebuild, never laid out

        def adopt(label: str, first: int, warm: bool = False) -> None:
            for card in list(getattr(body, "_sections", []))[first:]:
                owner[id(card)] = label
                sections.append(card)
                card.tab_shown = state["cur"] == label
                if not card.tab_shown or getattr(card, "hidden", False):
                    card.grid_remove()
                    if warm:
                        cold.append((label, card))

        def prewarm_one() -> bool:
            """Idle prebuild of a hidden page: lay ONE built section out (gridded in the unmapped page,
            nothing is drawn). Tk computes a widget's geometry (text measuring) on its first show,
            about half the cost of a first tab switch: done here, in its own idle slot."""
            while cold:
                label, card = cold.pop(0)
                if page.winfo_ismapped():         # on screen: no hidden layout pass (it would flash)
                    cold.clear()
                    return True
                try:
                    if not card.winfo_exists() or state["cur"] == label:
                        continue
                    card.grid()
                    body.update_idletasks()
                    if state["cur"] != label:
                        card.grid_remove()
                except Exception:
                    log.debug("section prewarm failed", exc_info=True)
                return False
            return True

        def advance(label: str, one: bool) -> bool:
            """Run the lazy builder of a tab: one section (``one``, idle prebuild) or to the end.
            True once the tab is complete. A builder may be a generator yielding after each section."""
            if one and cold:
                prewarm_one()
                return False
            if label not in pending:
                return True
            first = len(getattr(body, "_sections", []))
            building = self.__dict__.setdefault("_building", set())
            building.add(f"tab:{label}")
            try:
                gen = running.get(label)
                if gen is None:
                    res = pending[label]()
                    if not inspect.isgenerator(res):
                        pending.pop(label, None)
                        return True
                    running[label] = gen = res
                while True:
                    try:
                        next(gen)
                    except StopIteration:
                        running.pop(label, None)
                        pending.pop(label, None)
                        return True
                    if one:
                        return False
            except Exception:
                log.exception("Cannot build the %s tab", label)
                running.pop(label, None)
                pending.pop(label, None)
                return True
            finally:
                building.discard(f"tab:{label}")
                adopt(label, first, warm=one)

        def ensure(label: str) -> None:
            """Build a lazy tab completely now (its sections stay hidden unless it is the current tab)."""
            advance(label, one=False)
            cold[:] = [(t, c) for t, c in cold if t != label]

        def select(label: str) -> None:
            if state["cur"] == label:
                return
            ensure(label)
            state["cur"] = label
            for card in sections:
                card.tab_shown = owner[id(card)] == label
                if card.tab_shown and not getattr(card, "hidden", False):
                    card.grid()
                else:
                    card.grid_remove()
            for g, b in btns.items():
                on = g == label
                b.configure(text_color=TEXT if on else MUTED, font=self.fonts.nav_active if on else self.fonts.nav)
                unders[g].configure(fg_color=ACCENT if on else "transparent")
            try:
                page.scroll_frame._parent_canvas.yview_moveto(0)
            except Exception:
                pass
            page.current_tab = label  # type: ignore[attr-defined]
            if on_select is not None:
                try:
                    on_select(label)
                except Exception:
                    log.debug("tab hook failed", exc_info=True)

        for i, g in enumerate(labels):
            if g not in pending and not any(owner[id(c)] == g for c in sections):
                continue
            try:
                scale = max(0.5, self._scaled(100) / 100)
                tw = int(self.fonts.nav_active.measure(g) / scale) + 8
            except Exception:
                tw = 9 * len(g)
            b = ctk.CTkButton(bar, text=g, width=tw, height=BTN_H_SMALL, corner_radius=0, fg_color="transparent",
                              hover_color=BG, text_color=MUTED, font=self.fonts.nav, border_spacing=0,
                              command=self.cb(lambda gg=g: select(gg)))
            b.grid(row=0, column=i, padx=(0, TAB_GAP), sticky="w")
            u = self._frame(bar, width=1, height=3)
            u.grid(row=1, column=i, padx=(0, TAB_GAP), sticky="ew")
            btns[g], unders[g] = b, u
        page.select_tab = select  # type: ignore[attr-defined]
        page.ensure_tab = ensure  # type: ignore[attr-defined]
        page.step_tab = lambda label: advance(label, one=True)  # type: ignore[attr-defined]
        page.pending_tabs = lambda: list(dict.fromkeys([*pending, *(t for t, _c in cold)]))  # type: ignore[attr-defined]
        first = default if default in btns else next(iter(btns), None)
        if first is not None:
            select(first)
        return btns

    def _row(self, body: Any, title: str, desc: str | None = None) -> tuple[Any, Any]:
        """A setting row: title + description on the left, the control right next to it (column 1).

        Every row has the same paddings and a minimum height, rows are separated by 1 px lines;
        the description wraps before the control (see :meth:`_wrap_rows`).
        """
        r = body._rows
        if r:
            self._hline(body, ROW_LINE).grid(row=2 * r - 1, column=0, sticky="ew")
        row = self._frame(body, height=ROW_MIN_H)
        row.grid(row=2 * r, column=0, sticky="ew", pady=ROW_PAD_Y)
        row.grid_columnconfigure(0, weight=1)
        body._rows = r + 1
        # flat: title / description / control directly in the row (no nested frame: fewer windows
        # to build and to map on every tab switch); the control spans both text lines
        row.title_label = self._label(row, title, self.fonts.body, TEXT, anchor="w")  # type: ignore[attr-defined]
        row.title_label.grid(row=0, column=0, sticky="w")
        desc_lbl = None
        if desc:
            desc_lbl = self._label(row, desc, self.fonts.small, MUTED, anchor="w", justify="left",
                                   wraplength=440)
            desc_lbl.grid(row=1, column=0, sticky="w", pady=(3, 0))
        else:
            row.grid_rowconfigure(0, minsize=CTL_H)
        slot = self._frame(row)
        slot.grid(row=0, column=1, rowspan=2 if desc else 1, sticky="e", padx=(ROW_CTL_GAP, 0))
        slot.desc_label = desc_lbl  # type: ignore[attr-defined]
        row.slot = slot  # type: ignore[attr-defined]
        self._last_slot = slot
        self._last_row = row
        self._row_slots.append(slot)
        return row, slot

    def _switch_row(self, body: Any, field: str, title: str, desc: str | None = None,
                    on_change: Callable[[bool], None] | None = None) -> Any:
        _row, slot = self._row(body, title, desc)
        var = self.ctk.BooleanVar(value=bool(getattr(self.cfg, field)))

        def changed() -> None:
            self.set_option(field, bool(var.get()))
            if on_change is not None:
                on_change(bool(var.get()))

        sw = self._toggle(slot, var, changed)
        sw.grid(row=0, column=0)
        self._widgets_by_field[field] = lambda: var.set(bool(getattr(self.cfg, field)))
        return sw

    def _toggle(self, parent: Any, var: Any, command: Callable[[], Any] | None = None, color: str = ACCENT,
                small: bool = False, text: str = "") -> Toggle:
        """The app's on / off switch (see :class:`Toggle`)."""
        return Toggle(self, parent, var, self.cb(command) if command is not None else None, color=color,
                      small=small, text=text)

    def _slider_row(self, body: Any, field: str, title: str, desc: str | None, lo: float, hi: float,
                    step: float, fmt: Callable[[float], str], cast: Callable[[float], Any] = float,
                    on_change: Callable[[Any], None] | None = None,
                    to_float: Callable[[Any], float] = float) -> Any:
        _row, slot = self._row(body, title, desc)
        value_lbl = self._label(slot, fmt(getattr(self.cfg, field)), self.fonts.num, GOLD, width=84,
                                anchor="e")
        steps = max(1, int(round((hi - lo) / step)))

        def moved(v: float) -> None:
            val = cast(round(float(v) / step) * step)
            value_lbl.configure(text=fmt(val))
            self.set_option(field, val)
            if on_change is not None:
                on_change(val)

        sl = self.ctk.CTkSlider(slot, from_=lo, to=hi, number_of_steps=steps, width=240, height=22,
                                command=self.cb(moved), fg_color=SWITCH_OFF, progress_color=ACCENT_DIM,
                                button_color=GOLD, button_hover_color=GOLD_HOVER, button_length=0,
                                button_corner_radius=SLIDER_KNOB_R, corner_radius=3)
        sl.set(to_float(getattr(self.cfg, field)))
        sl.grid(row=0, column=0, padx=(0, 8))
        value_lbl.grid(row=0, column=1)

        def refresh() -> None:
            sl.set(to_float(getattr(self.cfg, field)))
            value_lbl.configure(text=fmt(getattr(self.cfg, field)))
        self._widgets_by_field[field] = refresh
        return sl

    def _choice_row(self, body: Any, field: str, title: str, desc: str | None,
                    choices: Sequence[tuple[str, str]], segmented: bool = False, width: int = 220,
                    on_change: Callable[[str], None] | None = None) -> Any:
        _row, slot = self._row(body, title, desc)
        pad = "  " if segmented else ""         # breathing room inside each segment
        labels = [f"{pad}{lbl}{pad}" for _v, lbl in choices]
        to_value = {f"{pad}{lbl}{pad}": v for v, lbl in choices}
        to_label = {v: f"{pad}{lbl}{pad}" for v, lbl in choices}

        def changed(label: str) -> None:
            value = to_value.get(label)
            if value is None:
                return
            self.set_option(field, value)
            if on_change is not None:
                on_change(value)
            refresh()

        if segmented:
            w = Segmented(self, slot, labels, self.cb(changed))
        else:
            w = Dropdown(self, slot, labels, self.cb(changed), width=width + 20)
        w.grid(row=0, column=0)

        def refresh() -> None:
            w.set(to_label.get(getattr(self.cfg, field), labels[0]))
        refresh()
        self._widgets_by_field[field] = refresh
        return w

    # ------------------------------------------------------------------ sidebar
    def _build_sidebar(self) -> None:
        ctk = self.ctk
        sb = ctk.CTkFrame(self.root, width=SIDEBAR_W, fg_color=SURFACE, corner_radius=0)
        sb.grid(row=0, column=0, sticky="nsw")
        sb.grid_propagate(False)
        sb.grid_columnconfigure(0, weight=1)
        sb.grid_rowconfigure(3, weight=1)
        self.sidebar = sb
        # right edge line
        ctk.CTkFrame(self.root, width=1, fg_color=LINE, corner_radius=0).grid(
            row=0, column=0, sticky="nse")

        brand = self._frame(sb)
        brand.grid(row=0, column=0, sticky="ew", padx=18, pady=(20, 18))
        logo = load_logo(64)
        self._images["logo"] = ctk.CTkImage(light_image=logo, dark_image=logo, size=(34, 34))
        ctk.CTkLabel(brand, text="", image=self._images["logo"], fg_color="transparent").grid(
            row=0, column=0, rowspan=2, padx=(0, 10))
        self._label(brand, "TreeAI Coach", self.fonts.brand, TEXT, anchor="w").grid(
            row=0, column=1, sticky="sw")
        self._label(brand, f"version {__version__}", self.fonts.tiny, DIM, anchor="w").grid(
            row=1, column=1, sticky="nw")

        nav = self._frame(sb)
        nav.grid(row=1, column=0, sticky="new", padx=(0, 10))
        nav.grid_columnconfigure(1, weight=1)
        self._nav: dict[str, tuple[Any, Any, str]] = {}
        for i, (key, label, icon) in enumerate(PAGES, start=1):
            ind = self._frame(nav, width=3, height=40)
            ind.grid(row=i, column=0, sticky="nsw", pady=1)
            btn = ctk.CTkButton(nav, text="  " + label, anchor="w", height=40, corner_radius=RADIUS,
                                fg_color="transparent", hover_color=PANEL_HI, text_color=MUTED,
                                font=self.fonts.nav, image=self._icon(icon, 18, MUTED), compound="left",
                                border_spacing=10, command=self.cb(lambda k=key: self.show_page(k)))
            btn.grid(row=i, column=1, sticky="ew", pady=1, padx=(8, 0))
            self._nav[key] = (btn, ind, icon)
            self._tip(btn, f"{label}   (Ctrl+{i})")
        try:
            self._build_quick_toggles(sb)
        except Exception:
            log.exception("Cannot build the quick toggles")

        foot = self._frame(sb)
        foot.grid(row=4, column=0, sticky="sew", padx=14, pady=(8, 14))
        foot.grid_columnconfigure(0, weight=1)
        # a new version found by the update check: one click to the "Mises à jour" tab (hidden until then)
        self._update_side = self._button(foot, "Nouvelle version", lambda: self.open_settings("Mises à jour"),
                                         "primary", icon="download")
        self._update_side.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self._update_side.grid_remove()
        pill = ctk.CTkFrame(foot, fg_color=RAISED, corner_radius=RADIUS, height=40)
        pill.grid(row=1, column=0, sticky="ew")
        pill.grid_columnconfigure(1, weight=1)
        dot = self._scaled(12)
        self.pill_dot = ctk.CTkCanvas(pill, width=dot, height=dot, bg=RAISED, highlightthickness=0, bd=0)
        self.pill_dot.grid(row=0, column=0, padx=(12, 8), pady=11)
        self._pill_dot_item = self.pill_dot.create_rectangle(dot // 6, dot // 6, dot - dot // 6, dot - dot // 6,
                                                             fill=DIM, outline="")
        self.pill_text = self._label(pill, "Démarrage…", self.fonts.small, TEXT, anchor="w")
        self.pill_text.grid(row=0, column=1, sticky="w", padx=(0, 10))
        for w in (pill, self.pill_text, self.pill_dot):      # the status is one click from "En jeu"
            w.bind("<Button-1>", lambda _e: self.show_page("dashboard"), add="+")
        self._tip(pill, "État de l'analyse : clique pour revenir sur « En jeu ».")
        meta = self._frame(foot)
        meta.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        meta.grid_columnconfigure(0, weight=1)
        ver = ctk.CTkButton(meta, text="Nouveautés", anchor="w", width=0, height=BTN_H_SMALL, corner_radius=RADIUS,
                            font=self.fonts.small, fg_color="transparent", hover_color=PANEL_HI, text_color=MUTED,
                            image=self._icon("star", 15, MUTED), compound="left", border_spacing=6,
                            command=self.cb(self.show_changelog))
        ver.grid(row=0, column=0, sticky="w")
        self._tip(ver, f"Nouveautés de la version {ui_kit.CHANGELOG_VERSION}")
        for col, (icon, tip, fn) in enumerate((("info", "À propos et mentions légales", lambda: self.show_about()),
                                               ("minimize", "Réduire la fenêtre (l'analyse continue)",
                                                lambda: self.minimize()))):
            b = ctk.CTkButton(meta, text="", width=ICON_BTN, height=BTN_H_SMALL, corner_radius=RADIUS,
                              fg_color="transparent", hover_color=PANEL_HI, image=self._icon(icon, 16, MUTED),
                              command=self.cb(fn))
            b.grid(row=0, column=col + 1, padx=(2, 0))
            self._tip(b, tip)

    def _build_quick_toggles(self, sb: Any) -> None:
        """Sidebar "ACCÈS RAPIDE" (safe mode, voice, overlay) and the one-click player level: what a player
        changes 30 s before a game is always one click away, on every page."""
        ctk = self.ctk
        box = self._frame(sb)
        box.grid(row=2, column=0, sticky="new", padx=18, pady=(16, 0))
        box.grid_columnconfigure(1, weight=1)
        self._hline(box).grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 12))
        self._label(box, "ACCÈS RAPIDE", self.fonts.caps, DIM, anchor="w").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(0, 4))
        self._quick: dict[str, tuple[Any, Any]] = {}
        def in_game(field: str) -> str:
            key = str(getattr(self.cfg, field, "") or "")
            return f", {key} en jeu" if key else ""

        specs = (("voice", "voice", "Voix",
                  lambda: f"Couper / rétablir les annonces vocales (Ctrl+M{in_game('hotkey_mute')})."),
                 ("overlay", "overlay", "Overlay",
                  lambda: "Afficher / masquer les indications sur ton écran"
                          f"{' (' + in_game('hotkey_overlay')[2:] + ')' if in_game('hotkey_overlay') else ''}."),
                 ("safe", "shield", "Mode sûr", "Mode sûr : aucune alerte de gank ni suivi du jungler, aucune zone "
                                                "dans le brouillard. Minuteurs et rappels restent actifs "
                                                "(Ctrl+Maj+S)."))
        for i, (key, icon, text, tip) in enumerate(specs, start=2):
            self._icon_label(box, icon, 17, MUTED).grid(row=i, column=0, padx=(0, 10), pady=4)
            lbl = self._label(box, text, self.fonts.body, TEXT, anchor="w")
            lbl.grid(row=i, column=1, sticky="w")
            var = ctk.BooleanVar(value=False)
            sw = self._toggle(box, var, lambda k=key: self._quick_toggled(k),
                              color=WARNING if key == "safe" else ACCENT, small=True)
            sw.grid(row=i, column=2, sticky="e")
            self._tip(lbl, tip)
            self._tip(sw.lbl, tip)
            self._quick[key] = (var, sw)
        # one-click player level (skill.py): the higher the level, the fewer basic indications
        try:
            from treeaicoach import skill as _skill
            row = 2 + len(specs)
            self._label(box, "TON NIVEAU", self.fonts.caps, DIM, anchor="w").grid(
                row=row, column=0, columnspan=3, sticky="w", pady=(16, 6))
            grid = self._frame(box)
            grid.grid(row=row + 1, column=0, columnspan=3, sticky="ew")
            grid.grid_columnconfigure((0, 1), weight=1, uniform="lvl")
            self._skill_btns: dict[str, Any] = {}
            for j, (k, label) in enumerate(_skill.SKILL_LEVELS):
                b = ctk.CTkButton(grid, text=label, height=BTN_H_SMALL, width=10, corner_radius=RADIUS,
                                  font=self.fonts.small, fg_color=RAISED, hover_color=HOVER, text_color=MUTED,
                                  border_width=1, border_color=LINE, border_spacing=2,
                                  command=self.cb(lambda kk=k: (self.apply_skill_level(kk), self._sync_skill_seg())))
                b.grid(row=j // 2, column=j % 2, sticky="ew", padx=(0 if j % 2 == 0 else 3, 0 if j % 2 else 3),
                       pady=(0, 6))
                self._tip(b, f"{label} : {_skill.SKILL_HELP.get(k, '')}")
                self._skill_btns[k] = b
            self.skill_seg = grid
            self._sync_skill_seg()
        except Exception:
            log.exception("Cannot build the skill level selector")
        self._sync_quick()

    @_guarded
    def apply_skill_level(self, level: str) -> None:
        """Débutant / Intermédiaire / Avancé / Expert: adapts tips, voice and overlay in one click."""
        from treeaicoach import skill as _skill
        changes = _skill.preset_changes(self.cfg, level)
        new = dataclasses.replace(self.cfg, **changes).validated()
        self._replace_config(new, changed=set(changes))
        self._refresh_all_widgets()
        self.show_toast(f"Niveau « {_skill.label(level)} » : {_skill.SKILL_HELP[_skill.normalize(level)]}")

    def _sync_quick(self, muted: bool | None = None) -> None:
        """Quick toggles <- configuration / engine state (never fires their callbacks)."""
        q = getattr(self, "_quick", None)
        if not q:
            return
        try:
            if muted is None:
                muted = self._is_muted()
            want = {"safe": bool(getattr(self.cfg, "safe_mode", False)), "voice": not muted,
                    "overlay": bool(self.cfg.overlay_enabled)}
            for key, (var, _sw) in q.items():
                if bool(var.get()) != want[key]:
                    var.set(want[key])
            pill = getattr(self, "pill_text", None)
            if pill is not None:
                pill.configure(text_color=WARNING if want["safe"] else TEXT)
        except Exception:
            log.debug("quick toggles sync failed", exc_info=True)

    def _is_muted(self) -> bool:
        eng = self.engine
        try:
            if eng is not None and hasattr(eng, "muted"):
                return bool(eng.muted)
        except Exception:
            pass
        return bool(self._muted)

    def _quick_toggled(self, key: str) -> None:
        var = self._quick[key][0]
        on = bool(var.get())
        if key == "safe":
            self.set_safe_mode(on)
        elif key == "voice":
            if on == self._is_muted():
                self.toggle_mute()
        elif key == "overlay":
            self.set_option("overlay_enabled", on)
            self.show_toast("Overlay affiché." if on else "Overlay masqué.")

    @_guarded
    def set_safe_mode(self, on: bool) -> None:
        """One-click "mode sûr" (sidebar, Ctrl+Maj+S)."""
        if not hasattr(self.cfg, "safe_mode"):
            return
        self.set_option("safe_mode", bool(on))
        self._sync_quick()
        self.show_toast("Mode sûr activé : aucune alerte de gank ni suivi du jungler." if on else
                        "Mode sûr désactivé : toutes les alertes choisies sont actives.",
                        "warning" if on else "info")

    @_guarded
    def toggle_mute(self) -> None:
        """Mute / unmute the voice (Tk thread)."""
        self._hk_mute()
        self.root.after(60, self._sync_quick)

    @_guarded
    def minimize(self) -> None:
        """Reduce the window to the taskbar (the analysis keeps running)."""
        self.root.iconify()

    def show_page(self, key: str, tab: str | None = None) -> None:
        """Switch the visible page (built on its first visit); ``tab`` selects a tab of that page.
        Keys of older versions ("alerts", "overlay") open the Réglages tab that replaced them."""
        if key in PAGE_ALIASES:
            key, alias_tab = PAGE_ALIASES[key]
            tab = tab or alias_tab
        if tab and key in self.pages:
            sel = getattr(self.pages[key], "select_tab", None)
            if callable(sel):
                sel(tab)
        if key not in self.pages or key == self._current_page:
            return
        t0 = time.perf_counter()
        prev = self._current_page
        page = self.pages[key]                 # builds it the first time
        apply = getattr(page, "center_apply", None)
        if callable(apply):
            apply()                            # right column width before it is shown (no jump)
        old = dict.get(self.pages, prev) if prev else None
        if old is not None:
            old.grid_remove()
        page.grid()
        self._current_page = key
        for k in (prev, key):
            if k not in self._nav:
                continue
            btn, ind, icon = self._nav[k]
            active = k == key
            btn.configure(fg_color=PANEL_HI if active else "transparent",
                          text_color=TEXT if active else MUTED,
                          font=self.fonts.nav_active if active else self.fonts.nav,
                          image=self._icon(icon, 18, ACCENT if active else MUTED))
            ind.configure(fg_color=ACCENT if active else "transparent")
        if key == "dashboard":
            self._radar_worker.active.set()
            self._radar_seq = -1
            if self._status_job is not None:      # refresh now (it slows down while the page is hidden)
                try:
                    self.root.after_cancel(self._status_job)
                except Exception:
                    pass
                self._status_job = self.root.after(10, self._status_loop)
        else:
            self._radar_worker.active.clear()
        if key == "analysis":
            if self._games_shown_sig != self._games_sig:
                self._show_games_table()
            self.refresh_games()
        if key == "settings" and self._settings_tab == "Affichage":
            self._schedule_overlay_preview()
        log.debug("page %s shown in %.0f ms", key, 1000 * (time.perf_counter() - t0))

    def open_settings(self, tab: str) -> None:
        """Réglages page on ``tab`` (one of :data:`SETTINGS_TABS`)."""
        self.show_page("settings", tab)

    def _display_tab_live(self) -> bool:
        """The Réglages > Affichage tab (overlay preview) is on screen."""
        return self._current_page == "settings" and self._settings_tab == "Affichage" and not self._iconic()

    def _bind_shortcuts(self) -> None:
        """Window shortcuts: Ctrl+1..4 pages, Ctrl+M mute, Ctrl+Shift+S safe mode, Ctrl+D diagnostic, F1."""
        r = self.root

        def page(k: str) -> str:
            self.cb(self.show_page)(k)
            return "break"

        for i, (key, _l, _i) in enumerate(PAGES, start=1):
            r.bind(f"<Control-Key-{i}>", lambda _e, k=key: page(k), add="+")
            r.bind(f"<Control-KP_{i}>", lambda _e, k=key: page(k), add="+")
        r.bind("<Control-m>", lambda _e: self.toggle_mute(), add="+")
        r.bind("<Control-S>", lambda _e: self.set_safe_mode(not getattr(self.cfg, "safe_mode", False)), add="+")
        r.bind("<Control-d>", lambda _e: self.copy_diagnostic(), add="+")
        r.bind("<F1>", lambda _e: page("help"), add="+")

    # ------------------------------------------------------------------ settings plumbing
    def set_option(self, field: str, value: Any) -> None:
        """Change one setting: validate, apply live, save (debounced)."""
        if not hasattr(self.cfg, field):
            log.warning("Unknown setting %s", field)
            return
        if getattr(self.cfg, field) == value:
            return
        new = dataclasses.replace(self.cfg, **{field: value}).validated()
        self._replace_config(new, changed={field})

    def _replace_config(self, new: Config, changed: set[str]) -> None:
        old = self.cfg
        self.cfg = new
        diff = {f for f in changed if getattr(old, f, None) != getattr(new, f, None)} | (
            {f.name for f in dataclasses.fields(Config) if getattr(old, f.name) != getattr(new, f.name)})
        if not diff:
            return
        self._apply_live(diff)
        self._schedule_save()

    def _apply_live(self, diff: set[str]) -> None:
        cfg = self.cfg
        if self.engine is not None:
            try:
                self.engine.apply_config(cfg)
            except Exception:
                log.exception("engine.apply_config failed")
        if self.overlay is not None:
            try:
                self.overlay.apply_config(cfg)
            except Exception:
                log.exception("overlay.apply_config failed")
        if diff & VOICE_FIELDS and self.voice is not None:
            base = dict(voice_name=cfg.voice_name, rate=cfg.voice_rate, volume=cfg.voice_volume,
                        beep_on_danger=cfg.beep_on_danger)
            try:
                extra = {k: getattr(cfg, f) for k, f in (("engine", "voice_engine"), ("neural_voice", "neural_voice"),
                                                         ("neural_rate", "neural_rate")) if hasattr(cfg, f)}
                try:
                    self.voice.set_params(**base, **extra)
                except TypeError:          # older voice module without engine selection
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
        if any(f.startswith("hotkey_") for f in diff):      # texts that name the keys follow the bindings
            self._refresh_key_texts()
        if diff & DETECTOR_FIELDS and self.engine is not None:
            self._rebuild_engine(self.demo, start=None, new_detector=True)
        elif diff & ENGINE_RESTART_FIELDS and self.engine is not None:
            self._rebuild_engine(self.demo, start=None)
        if diff & {"ai_provider", "ai_api_key", "ai_model"}:
            self._ai_test = None           # the last key test no longer applies
        if diff & {"sensitivity", "warn_radius", "danger_radius"}:
            self._refresh_radius_text()
        if "manual_minimap_rect" in diff or "minimap_mode" in diff:
            try:
                if getattr(self, "_rect_desc", None) is not None:
                    self._rect_desc.configure(text=self._manual_rect_text())
            except Exception:
                pass

    def _refresh_key_texts(self) -> None:
        """Help "Touches" table and the dashboard's diagnostic hint, after a hotkey change."""
        try:
            if "help" in self._built:
                self._fill_help_keys()
            hint = getattr(self, "_diag_hint", None)
            if hint is not None:
                hk = str(getattr(self.cfg, "hotkey_diag", "") or "").replace("+", " + ")
                self._set_text(hint, hk + " en jeu" if hk else "")
        except Exception:
            log.debug("key texts refresh failed", exc_info=True)

    def _schedule_save(self) -> None:
        if self._save_job is not None:
            try:
                self.root.after_cancel(self._save_job)
            except Exception:
                pass
        self._save_job = self.root.after(SAVE_DEBOUNCE_MS, self.save_now)

    def save_now(self) -> None:
        """Write the configuration now (Tk thread)."""
        self._save_job = None
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
                eng = self._engine_factory(cfg, voice, out["detector"], src)
                out["engine"] = eng
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
            if self._closing:     # window closed during start-up: _backend_ready will never run
                self._shutdown_components(None, out.get("overlay"), voice if self._own_voice else None)
            return out

        def failed(exc: BaseException) -> None:
            # never leave the launcher stuck on "Démarrage…" (start button disabled forever)
            self._busy = False
            self.engine_error = f"Le démarrage a échoué : {exc}. Clique sur « Démarrer l'analyse » pour réessayer."
            self.show_error(self.engine_error)
            self._refresh_status()

        self._busy = True
        self._backend_t0 = time.monotonic()
        self._dispatcher.run(job, self._backend_ready, self.cb(failed), name="TreeAI-ui-init")

    def _make_detector(self, cfg: Config, demo: bool) -> Any:
        """Detector for a new engine. The default factory gets what the engine gives the detectors it
        builds itself (champion DB, learned custom-skin icon cache outside the demo)."""
        if self._detector_factory is _default_detector_factory:
            return _default_detector_factory(cfg, learn=not demo)
        return self._detector_factory(cfg)

    @_guarded
    def _backend_ready(self, out: dict[str, Any]) -> None:
        self._busy = False
        if self._closing:
            # the window was closed during start-up: stop what was created
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

    def _on_overlay_moved(self, name: str, x: int, y: int) -> None:
        """Overlay thread callback (move mode): save the new window position (Tk thread)."""
        def apply() -> None:
            field = {"radar": "radar", "hud": "hud"}.get(str(name))
            if field is None:
                return
            upd = {f"{field}_xy": [int(x), int(y)], f"{field}_position": "custom"}
            new = dataclasses.replace(self.cfg, **upd).validated()
            self._replace_config(new, changed=set(upd))
            self._refresh_position_menus()
        self._dispatcher.post(self.cb(apply))

    def _refresh_position_menus(self) -> None:
        for field, which in (("radar_position", "radar"), ("hud_position", "hud")):
            menu = self._position_menus.get(which) if hasattr(self, "_position_menus") else None
            if menu is None:
                continue
            choices = self._position_choices(which)
            try:
                menu.configure(values=[lbl for _v, lbl in choices])
                menu.set(dict(choices).get(getattr(self.cfg, field), choices[0][1]))
            except Exception:
                log.debug("Position menu refresh failed", exc_info=True)

    def _track_engine(self, eng: Any) -> None:
        """Remember an engine built on a worker thread so that close() always stops it."""
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

    def _update_system(self, st: Any, key: str, running: bool) -> None:
        """Dashboard "Système" rows (ui_kit.subsystem_rows) + a LoL client probe every 30 s."""
        rows = getattr(self, "sys_rows", None)
        if not rows:
            return
        now = time.monotonic()
        if now - self._lcu_polled > 30.0 and self._current_page == "dashboard":
            self._lcu_polled = now
            self._refresh_lcu_status()
        det = str(getattr(st, "detector", "") or getattr(self._detector, "name", "") or "")
        vb = str(getattr(self.voice, "backend", "") or getattr(st, "voice", "") or "")
        data = ui_kit.subsystem_rows(
            state=key, message=str(getattr(st, "message", "") or ""), running=running, demo=self.demo,
            minimap_found=getattr(st, "minimap_rect", None) is not None,
            minimap_method=getattr(st, "locate_method", None), detector=det, voice_backend=vb,
            muted=self._is_muted(), lcu_text=self._lcu_text, lcu_enabled=bool(getattr(self.cfg, "lcu_enabled", True)),
            engine_ok=self.engine is not None or self._busy, ai_provider=str(getattr(self.cfg, "ai_provider", "off")),
            ai_key_set=bool(str(getattr(self.cfg, "ai_api_key", "") or "").strip()), ai_budget=self._ai_budget(),
            ai_test=None if self._ai_test_busy else self._ai_test)
        if self._ai_test_busy:     # "test en cours" replaces the AI row while the request runs
            data = [r if r[0] != "ai" else (r[0], r[1], -1, "test en cours…", "", "") for r in data]
        cols = {0: SAFE, 1: WARNING, 2: DANGER, -1: DIM}
        for k, label, level, text, fix, action in data:
            row = rows.get(k)
            if row is None:            # a row added to ui_kit.subsystem_rows later: shown as it comes
                row = self._add_sys_row(k, label)
                if row is None:
                    continue
            sig = (level, text, fix)
            if row["sig"] == sig:
                continue
            row["sig"] = sig
            row["action"] = action
            row["dot"].configure(fg_color=cols.get(level, DIM))
            row["val"].configure(text=ui_text(text), text_color=TEXT if level in (1, 2) else MUTED)
            if fix and action:
                row["btn"].configure(text=fix, text_color=DANGER if level == 2 else ACCENT)
                row["btn"].grid()
            else:
                row["btn"].grid_remove()

    @_guarded
    def _system_fix(self, key: str) -> None:
        row = (getattr(self, "sys_rows", {}) or {}).get(key) or {}
        self._run_fix(str(row.get("action") or ""))

    @_guarded
    def _run_fix(self, action: str) -> None:
        """One-click fix of a "Système" row / of the first-game checklist, by action name."""
        if action == "start":
            self.toggle_engine()
        elif action == "calibrate":
            self.calibrate()
        elif action == "help_borderless":
            self.show_page("help")
            self.show_toast("Dans le jeu : Options > Vidéo > Mode d'affichage : Sans bordure.", "warning")
        elif action == "relocate":
            self.relocate()
        elif action == "diagnostic":
            self.copy_diagnostic()
        elif action == "settings_ia":
            self.open_settings("Détection")
        elif action == "settings_ai":
            self.open_settings("IA")
        elif action == "test_ai":
            self.test_ai_key()
        elif action == "voice":
            self.test_voice()
        elif action == "unmute":
            self.toggle_mute()
        elif action == "voice_settings":
            self.open_settings("Voix")
            self.show_toast("Aucune voix Windows trouvée : choisis la voix neurale (Internet) ou installe une "
                            "voix française (Paramètres Windows > Heure et langue > Voix).", "warning")
        elif action == "lcu_help":
            self.show_toast("Laisse le client League of Legends ouvert : après la partie, TreeAI y lit tes "
                            "vraies stats (lecture seule). Rien à régler.")

    @_guarded
    def test_overlay(self) -> None:
        """Show the overlay on sample states (sûr / attention / danger) for 10 s, out of game."""
        ov = self.overlay
        if self._in_game():
            self.show_toast("Une partie est en cours : l'overlay affiche déjà la vraie partie.")
            return
        if ov is None or not getattr(ov, "ok", False):
            self.open_settings("Affichage")
            self.show_toast("L'overlay ne s'affiche que sous Windows, jeu en Sans bordure. Voici l'aperçu.",
                            "warning")
            return
        if not self.cfg.overlay_enabled:
            self.set_option("overlay_enabled", True)
            self._sync_quick()

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
                log.debug("monitor size unknown", exc_info=True)
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
                    log.debug("sample state not resized", exc_info=True)
                out.append(st)
            return out

        def done(states: list) -> None:
            if not states:
                self.show_error("Aperçu de l'overlay indisponible.")
                return
            self._overlay_test = (time.monotonic(), states)
            self.show_toast("Overlay de test affiché 10 s : sûr, attention, puis danger.")

        self._dispatcher.run(job, done, self.cb(lambda e: self.show_error(f"Test de l'overlay impossible : {e}")),
                             name="TreeAI-ui-overlay-test")

    def _overlay_provider(self) -> Any:
        """State provider given to the overlay thread: the "Tester l'overlay" samples for 10 s,
        else the engine snapshot."""
        test = getattr(self, "_overlay_test", None)
        if test is not None:
            t0, states = test
            el = time.monotonic() - t0
            if el < 10.0 and states:
                return states[min(len(states) - 1, int(el / (10.0 / len(states))))]
            self._overlay_test = None
        return self._overlay_state()

    def _overlay_state(self) -> Any:
        """Thread-safe engine snapshot of the overlay state (None out of game)."""
        eng = self.engine
        if eng is None:
            return None
        try:
            return eng.get_overlay_state()
        except Exception:
            log.debug("get_overlay_state failed", exc_info=True)
            return None

    def _radar_source(self) -> tuple[Any, Any]:
        """(overlay state, raw preview) for the radar worker thread."""
        eng = self.engine
        if eng is None or not self._engine_running():     # stopped: no stale "EN DIRECT" picture
            return None, None
        state = None
        try:
            state = eng.get_overlay_state()
        except Exception:
            state = None
        if state is not None:
            return state, None
        try:
            return None, eng.get_preview()
        except Exception:
            return None, None

    def _engine_running(self) -> bool:
        try:
            return bool(self.engine is not None and self.engine.is_running())
        except Exception:
            return False

    @_guarded
    def toggle_engine(self) -> None:
        """Start / stop the analysis (worker thread: stop may take up to 3 s)."""
        if self._busy:
            return
        if self.engine is None:
            if self.engine_error:
                self.show_error(self.engine_error)
            self._rebuild_engine(self.demo, start=True, new_detector=self._detector is None)
            return
        eng = self.engine
        running = self._engine_running()
        self._busy = True
        self.btn_start.configure(state="disabled", text="Arrêt…" if running else "Démarrage…")

        def job() -> None:
            if running:
                eng.stop()
            else:
                eng.start()

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
        """Switch between the simulated game (DemoSource) and the real screen analysis."""
        if self._busy:
            return
        self._rebuild_engine(not self.demo, start=True)
        self.show_toast("Mode démo : partie simulée, le jungler ennemi va venir te ganker vers 40 s."
                        if not self.demo else "Retour à l'analyse réelle.")

    def _rebuild_engine(self, demo: bool, start: bool | None, new_detector: bool = False) -> None:
        """Replace the engine (demo toggle, detector change). ``start`` None = keep the running state."""
        if self._busy:
            # try again once the current operation is over
            self.root.after(300, lambda: self._rebuild_engine(demo, start, new_detector))
            return
        old = self.engine
        was_running = self._engine_running()
        want_start = was_running if start is None else start
        cfg, voice = self.cfg, self.voice
        self._busy = True
        self.demo = demo
        try:
            self.btn_start.configure(state="disabled")
        except Exception:
            pass

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
            self._journal.clear()
            self._last_alert_seen = None
            self._render_journal()
            if err:
                self.show_error(err)
            self._refresh_status()

        def failed(exc: BaseException) -> None:
            self._busy = False
            self.engine_error = f"Le moteur d'analyse n'a pas pu redémarrer : {exc}"
            self.show_error(self.engine_error)
            self._refresh_status()

        self._dispatcher.run(job, done, self.cb(failed), name="TreeAI-ui-rebuild")

    @_guarded
    def test_voice(self) -> None:
        if self.voice is None:
            self.show_error("La synthèse vocale n'est pas disponible.")
            return
        self.voice.say("Test de la voix. Attention, Lee Sin approche !", 1)
        backend = getattr(self.voice, "backend", "")
        if backend == "print":
            self.show_toast("Voix indisponible sur ce système : le message est écrit dans le journal.", "warning")
        else:
            self.show_toast("Test de la voix en cours…")

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
        """Manual minimap calibration (modal)."""
        from treeaicoach.calibration import run_calibration  # noqa: PLC0415

        rect = run_calibration(self.root, self.cfg)
        if rect:
            new = dataclasses.replace(self.cfg, manual_minimap_rect=dict(rect), minimap_mode="manual").validated()
            self._replace_config(new, changed={"manual_minimap_rect", "minimap_mode"})
            refresh = self._widgets_by_field.get("minimap_mode")
            if refresh:
                refresh()
            self.relocate(quiet=True)
            self.show_toast(f"Minimap calibrée : {rect['w']} × {rect['h']} px.")

    @_guarded
    def toggle_move_mode(self) -> None:
        """Overlay "move" mode: the windows become draggable; each drop is saved (on_moved)."""
        ov = self.overlay
        setter = getattr(ov, "set_move_mode", None) if ov is not None else None
        if not callable(setter) or not bool(getattr(ov, "ok", True)):
            self.show_error("Le déplacement des fenêtres de l'overlay n'est disponible que sous Windows.")
            return
        self._move_mode = not self._move_mode
        setter(self._move_mode)
        btn = getattr(self, "btn_move", None)
        if self._move_mode:
            if btn is not None:
                btn.configure(text="Terminer", fg_color=GOLD, text_color=ON_GOLD, hover_color=GOLD_HOVER,
                              image=self._icon("move", 16, ON_GOLD))
            self.show_toast("Fais glisser le radar et le panneau à la souris, puis clique sur « Terminer ».")
        else:
            if btn is not None:
                btn.configure(text="Déplacer", fg_color=PANEL_HI, text_color=TEXT, hover_color=HOVER,
                              image=self._icon("move", 16, MUTED))
            self.show_toast("Positions de l'overlay enregistrées.")

    # ------------------------------------------------------------------ hotkeys
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
                self._dispatcher.run(hk.start, self._hotkeys_started, name="TreeAI-ui-hotkeys")
            else:
                hk = self._hotkeys
                self._dispatcher.run(lambda: hk.set_bindings(bindings), self._hotkeys_started,
                                     name="TreeAI-ui-hotkeys")
        except Exception:
            log.exception("Hotkeys unavailable")

    def _hotkeys_started(self, _r: Any = None) -> None:
        hk = self._hotkeys
        failed = list(getattr(hk, "failed", []) or [])
        if failed:
            self.show_toast(f"Raccourci déjà utilisé par une autre application : {', '.join(failed)}. "
                            "Change-le dans Réglages > Avancé > Touches en jeu.", "warning")

    def _hk_jungler(self) -> None:          # hotkey thread
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
            log.exception("F9 hotkey failed")

    def _hk_ai(self) -> None:               # hotkey thread
        self._dispatcher.post(self.ask_ai)

    def _hk_ward(self) -> None:             # hotkey thread: "where to ward?" (visual only, cheap)
        fn = getattr(self.engine, "request_ward_guide", None)
        if callable(fn):
            try:
                fn()
            except Exception:
                log.debug("ward guide hotkey failed", exc_info=True)

    @_guarded
    def ask_ai(self) -> None:
        """"Demander à l'IA": manual request through the engine (the answer comes back as a toast)."""
        fn = getattr(self.engine, "ask_ai", None)
        if not callable(fn):
            self.show_toast("Conseil IA indisponible : le moteur n'est pas démarré.")
            return
        self._dispatcher.run(fn, lambda msg: self.show_toast(str(msg or "")),
                             self.cb(lambda e: self.show_error(f"Conseil IA impossible : {e}")),
                             name="TreeAI-ui-ask-ai")

    def _hk_mute(self) -> None:             # hotkey thread
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
        self._dispatcher.post(lambda: self.show_toast("Voix coupée." if muted else "Voix rétablie."))

    def _hk_overlay(self) -> None:          # hotkey thread
        try:
            if self.engine is not None and hasattr(self.engine, "toggle_overlay"):
                self.engine.toggle_overlay()
        except Exception:
            log.exception("Overlay hotkey failed")

    # ------------------------------------------------------------------ periodic refresh
    def _iconic(self) -> bool:
        try:
            return self.root.state() in ("iconic", "withdrawn")
        except Exception:
            return False

    def _dash_live(self) -> bool:
        """The dashboard is built, shown and the window is not minimised (live widgets worth updating)."""
        return ("dashboard" in self._built and "dashboard" not in self._failed_pages
                and self._current_page == "dashboard" and not self._iconic())

    def _status_loop(self) -> None:
        """Status refresh: 4 Hz only while the dashboard is on screen during a game (or a start-up),
        1 Hz otherwise, every 2 s when the window is minimised (sidebar pill + journal only)."""
        self._status_job = None
        if self._closing:
            return
        try:
            self._refresh_status()
        except Exception:
            log.exception("Status refresh failed")
        if self._iconic():
            delay = STATUS_ICONIC_MS
        elif self._current_page == "dashboard" and (self._busy or self._last_state_key in ("RUNNING", "LOCATING")):
            delay = STATUS_MS
        else:
            delay = STATUS_IDLE_MS
        self._status_job = self.root.after(delay, self._status_loop)

    def _get_status(self) -> Any:
        if self.engine is None:
            return None
        try:
            return self.engine.get_status()
        except Exception:
            log.debug("get_status failed", exc_info=True)
            return None

    def _refresh_status(self) -> None:
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
            if not running and key not in ("ERROR",):
                key = "STOPPED"
            msg = str(getattr(st, "message", "") or "") if st is not None and running else ""
        champ, role = "", None
        if key == "RUNNING" and ov is not None:
            me, role, _opp = ui_kit.lane_opponent(ov)
            champ = champion_name(me) if me and me != "me" else ""
            role = role or getattr(ov, "my_role", None)
        title, msg, fix, action = ui_kit.status_line(key, msg, champion=champ, role=role)
        color = STATE_INFO.get(key, (title, GOLD))[1]
        self._state_color = color
        if key != self._last_state_key:
            if self._last_state_key == "RUNNING" and key != "RUNNING":       # a game just ended: new record
                self._post_game_watch = str(_game_json_path(self._games[0]) or "") if self._games else ""
                for delay in (4000, 15000, 45000):      # the report may take a while (client LoL, AI review)
                    self.root.after(delay, self.cb(self._post_game_refresh))
            self._last_state_key = key
            self.pill_dot.itemconfigure(self._pill_dot_item, fill=color)
        self._set_text(self.pill_text, PILL_TEXT.get(key, title) + (" · démo" if self.demo else ""))
        muted = self._is_muted()
        if muted != getattr(self, "_quick_muted", None):
            self._quick_muted = muted
            self._sync_quick(muted)
        if self._diag_watch:
            self._poll_diagnostic()
        if not self._dash_live():
            self._collect_alerts(st, ov)        # keep the journal up to date; no hidden widget work
            if not self._iconic():
                self._poll_champ_select(key, every=6.0)     # champion select: a toast on the other pages
            return
        cpu = self._cpu.sample() if getattr(self, "_cpu", None) is not None else None
        self._update_health(getattr(st, "health", None) if st is not None and key == "RUNNING" else None, cpu)
        self._poll_champ_select(key)
        msg = self._with_extras(msg, key)
        self._set_text(self.state_title, title)
        self._set_text(self.state_msg, msg or " ")
        self._set_fix(fix, action)
        self._set_live_layout(ov is not None or (running and key in ("RUNNING", "LOCATING")))
        self.hero.set_matchup(ov is not None)
        if self.demo:
            self.demo_badge.grid(row=0, column=1, padx=(10, 0))
        else:
            self.demo_badge.grid_remove()

        # start / stop button
        if self._busy:
            self.btn_start.configure(state="disabled")
        elif running:
            self._style_start(False)
        else:
            self._style_start(True)
        self._demo_button_text()

        gt = getattr(st, "game_time", None) if st is not None else None
        self._set_text(self.clock_lbl, fmt_clock(gt))
        self.clock_lbl.configure(text_color=TEXT if gt is not None else DIM)

        banner = getattr(st, "banner", None) if st is not None else None
        if banner and banner != self._banner_dismissed:
            self._set_text(self.banner_lbl, str(banner))
            if not self.banner.grid_info():
                self.banner.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        elif self.banner.grid_info():
            self.banner.grid_remove()

        try:
            self._update_system(st, key, running)
        except Exception:
            log.debug("system rows update failed", exc_info=True)
        self._update_threat(ov)
        try:
            self._set_text(self.hero.timers, ui_kit.objectives_text(ov) if ov is not None else "")
        except Exception:
            log.debug("objective timers failed", exc_info=True)
        self._update_enemies(ov, st)
        try:
            self._update_team(ov)
        except Exception:
            log.debug("team update failed", exc_info=True)
        self._collect_alerts(st, ov)
        self._journal_caption()
        self._pregame_tick = (getattr(self, "_pregame_tick", 0) + 1) % 8
        if self._pregame_tick == 0 and self._current_page == "dashboard" and not self._journal:
            self._render_pregame()          # the game's own goal / its status can change
        try:
            self._update_coach_strip(ov, running and key == "RUNNING")
        except Exception:
            log.debug("coach strip update failed", exc_info=True)
        lvl = min(max(int(getattr(ov, "threat_level", 0) or 0), 0), 2) if ov is not None else 0
        self.hero.set_glow(THREAT_COLORS[lvl] if ov is not None and lvl > 0 else color)
        if self._current_page == "dashboard":
            self._draw_gauge_step()

    def _post_game_refresh(self) -> None:
        """Reload the history after a game until its record appears (then ``_show_games`` says so once)."""
        if self._post_game_watch is not None and not self._closing:
            self.refresh_games()

    def _set_fix(self, label: str, action: str) -> None:
        """The status strip's fix button ("Calibrer", "Aide", "Diagnostic"...), hidden when all is fine."""
        btn = getattr(self, "btn_fix", None)
        if btn is None:
            return
        self._fix_action = action
        on = bool(label and action)
        if on:
            self._set_text(btn, label)
        self.hero.set_fix(on)

    def _poll_champ_select(self, key: str, every: float = 2.0) -> None:
        """Pre-game card while the player is in champion select (no game running):
        champ_select.pregame_card() on a worker thread, at most every ``every`` s."""
        if "dashboard" not in self._built:
            return
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

        self._dispatcher.run(job, done, failed, name="TreeAI-ui-champselect")

    def _show_champ_select(self, card: Any) -> None:
        box = getattr(self, "cs_card", None)
        if box is None:
            return
        title = str(getattr(card, "title", "") or "") if card is not None else ""
        lines = tuple(str(x) for x in (getattr(card, "lines", ()) or ()) if x) if card is not None else ()
        sig = (title, lines) if card is not None else None
        if sig == self._cs_sig:
            return
        was_shown = self._cs_sig is not None
        self._cs_sig = sig
        for w in box.winfo_children():
            w.destroy()
        if card is None:
            box.grid_remove()
            return
        if not was_shown and self._current_page != "dashboard":
            self.show_toast("Sélection des champions : ta carte d'avant-partie est sur « En jeu ».")
        head = self._frame(box)
        head.grid(row=0, column=0, sticky="ew", padx=CARD_PAD, pady=(14, 4))
        head.grid_columnconfigure(1, weight=1)
        self._caption(head, "Sélection des champions", ACCENT, anchor="w").grid(row=0, column=0, sticky="w")
        self._label(head, ui_text(title), self.fonts.h2, TEXT, anchor="w").grid(row=1, column=0, columnspan=2,
                                                                                 sticky="w", pady=(2, 0))
        for i, line in enumerate(lines[:6]):
            lbl = self._label(box, ui_text(line), self.fonts.small, TEXT if i == 0 else MUTED, anchor="w",
                              justify="left", wraplength=760)
            lbl.grid(row=1 + i, column=0, sticky="w", padx=CARD_PAD, pady=(2, 14 if i == min(len(lines), 6) - 1
                                                                         else 0))
            self._wrap_labels.append((lbl, 2 * CARD_PAD + 8))
        box.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        self._wrap_rows(self._wrap_width, force=True)

    def _update_health(self, h: Any, cpu: float | None = None) -> None:
        """Dashboard health line: ``status.health`` in game (capture, detection timings, overlay,
        champions, CPU), else this app's CPU use."""
        lbl = getattr(self, "health_lbl", None)
        if lbl is None:
            return
        text, level = health_text(h)
        if not text:
            text = f"Processeur utilisé par TreeAI : {cpu:.0f} %" if isinstance(cpu, (int, float)) else ""
        col = {0: MUTED, 1: WARNING, 2: DANGER}.get(level, MUTED)
        sig = (text, col)
        if sig != self._health_sig:
            self._health_sig = sig
            lbl.configure(text=text, text_color=col)

    @_guarded
    def start_diagnostic(self) -> None:
        """"Diagnostic complet": the engine records a bundle for 60 s (engine.start_diagnostic)."""
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

        self._dispatcher.run(fn, done, self.cb(lambda e: self.show_error(f"Diagnostic impossible : {e}")),
                             name="TreeAI-ui-diagnostic")

    def _poll_diagnostic(self) -> None:
        """Progress of the running diagnostic (status loop, >= 1 s): labels, then the result toast."""
        eng = self.engine
        fn = getattr(eng, "diagnostic_status", None) if eng is not None else None
        ds = None
        try:
            ds = fn() if callable(fn) else None
        except Exception:
            log.debug("diagnostic_status failed", exc_info=True)
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
        btn = getattr(self, "btn_diag", None)
        if btn is not None:
            self._set_text(btn, f"Diagnostic {pct} %" if running else "Diagnostic complet")
        desc = getattr(self, "_diag_desc", None)
        if desc is not None:
            self._set_text(desc, msg)
        if not running:
            self._diag_watch = False
            if ds.get("error"):
                self.show_error(msg)
            else:
                self.show_toast(msg)

    def _update_coach_strip(self, ov: Any, live: bool) -> None:
        """Dashboard coach strip: gauge, detected role (+ swap notice), top tip, AI counter."""
        if getattr(self, "coach_gauge_lbl", None) is None:
            return
        eng = self.engine
        gauge = tip = None
        role = notice = None
        ai = ""
        if eng is not None and live:
            fn = getattr(eng, "play_gauge", None)
            gauge = fn() if callable(fn) else None
            fn = getattr(eng, "top_tip", None)
            tip = fn() if callable(fn) else None
            fn = getattr(eng, "detected_role", None)
            role, notice = fn() if callable(fn) else (None, None)
            fn = getattr(eng, "ai_budget_text", None)
            ai = (fn() if callable(fn) else "") or ""
        step = getattr(gauge, "step", None)
        sig = (step, getattr(gauge, "reason", None), tip, role, notice, ai)
        if sig == self._coach_sig:
            return
        self._coach_sig = sig
        if step is None:
            self.coach_gauge_lbl.configure(text="-" if live else "", text_color=DIM)
        else:
            col = GAUGE_UI_COLORS.get(int(step), MUTED)
            self.coach_gauge_lbl.configure(text=str(getattr(gauge, "label", "") or "-"), text_color=col)
        role_txt = f"Rôle : {role}" if role else ("Rôle : -" if live else "")
        if notice:
            role_txt += " · échange de voie"
        reason = str(getattr(gauge, "reason", "") or "")
        if reason:
            role_txt += f" · {reason}"
        self.coach_role_lbl.configure(text=role_txt, text_color=WARNING if notice else MUTED)
        self.coach_ai_lbl.configure(text=ai)
        if tip:
            text, tone = tip
            self.coach_tip_lbl.configure(text=str(text), text_color=TIP_UI_COLORS.get(str(tone), TEXT))
        else:
            self.coach_tip_lbl.configure(
                text="Le conseil du moment s'affichera ici pendant la partie." if not live else "Rien à signaler.",
                text_color=DIM)

    def _with_extras(self, msg: str, key: str) -> str:
        """Win probability (hype.py) appended to the dashboard message; new AI error shown once."""
        eng = self.engine
        if eng is None:
            return msg
        try:
            status = getattr(eng, "ai_status", None)
            if callable(status):
                seq, text = status()
                if text and seq != getattr(self, "_ai_status_seq", 0):
                    self._ai_status_seq = seq
                    self._set_ai_status(text, DANGER)
                    self.show_error(text)
            aseq = getattr(eng, "ai_answer_seq", 0)
            if isinstance(aseq, int) and aseq != getattr(self, "_ai_answer_seq", 0):
                self._ai_answer_seq = aseq
                answer = getattr(eng, "last_ai_advice", None)
                if answer:
                    self.show_toast(f"IA : {answer}")
            wp = getattr(eng, "win_probability", None)
            p = wp() if callable(wp) and key == "RUNNING" and getattr(self.cfg, "win_prob_hud", True) else None
            if isinstance(p, (int, float)):
                prob = f"Probabilité de victoire : {int(round(100 * p))} %"
                return f"{msg} · {prob}" if msg and not msg.startswith("Alertes et overlay actifs") else prob
        except Exception:
            log.debug("win probability / AI status unavailable", exc_info=True)
        return msg

    def _dismiss_banner(self) -> None:
        self._banner_dismissed = self.banner_lbl.cget("text")
        self.banner.grid_remove()

    def _style_start(self, start: bool) -> None:
        want = "start" if start else "stop"
        if getattr(self, "_start_style", None) == want and str(self.btn_start.cget("state")) == "normal":
            return
        self._start_style = want
        if start:
            self.btn_start.configure(state="normal" if self.engine is not None or not self._busy else "disabled",
                                     text="Démarrer l'analyse", fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                     text_color=ON_ACCENT, border_width=0, image=self._icon("play", 12, ON_ACCENT))
        else:
            self.btn_start.configure(state="normal", text="Arrêter l'analyse", fg_color=PANEL_HI,
                                     hover_color=DANGER_DARK, text_color=TEXT, border_width=1,
                                     border_color=LINE_STRONG, image=self._icon("stop", 10, DANGER))

    @staticmethod
    def _set_text(widget: Any, text: str) -> None:
        text = ui_text(text)
        try:
            if widget.cget("text") != text:
                widget.configure(text=text)
        except Exception:
            pass

    def _update_threat(self, ov: Any) -> None:
        if ov is None:
            self._set_text(self.threat_lbl, "-")
            self.threat_lbl.configure(text_color=DIM)
            self._set_text(self.threat_detail, "Hors partie")
            self._gauge_target, self._gauge_color = 0.0, DIM
            return
        lvl = int(getattr(ov, "threat_level", 0) or 0)
        lvl = min(max(lvl, 0), 2)
        text = str(getattr(ov, "threat_text", "") or THREAT_LABELS[lvl])
        head = THREAT_LABELS[lvl]
        detail = text.split(EM_DASH, 1)[1].strip() if EM_DASH in text else (
            "Aucun ennemi menaçant" if lvl == 0 else text)
        self._set_text(self.threat_lbl, head)
        self.threat_lbl.configure(text_color=THREAT_COLORS[lvl])
        self._set_text(self.threat_detail, detail)
        self._gauge_target, self._gauge_color = threat_fraction(lvl), THREAT_COLORS[lvl]

    def _draw_gauge_step(self) -> None:
        diff = self._gauge_target - self._gauge_frac
        if abs(diff) < 0.004 and getattr(self, "_gauge_drawn_color", None) == self._gauge_color:
            return
        self._gauge_frac += diff * 0.55 if abs(diff) > 0.01 else diff
        self._gauge_drawn_color = self._gauge_color
        self._draw_gauge()

    def _update_enemies(self, ov: Any, st: Any) -> None:
        enemies = list(getattr(ov, "enemies", []) or [])[:5] if ov is not None else []
        roles = dict(getattr(ov, "roles", {}) or {}) if ov is not None else {}
        if enemies:     # order the cards like the scoreboard: top, jungle, mid, adc, support
            order = {r: i for i, r in enumerate(ui_kit.ROLE_ORDER)}

            def rank(e: Any) -> int:
                r = ui_kit.norm_role(getattr(e, "role", None) or roles.get(getattr(e, "alias", "") or "")
                                     or roles.get(getattr(e, "key", "") or ""))
                return order.get(r, 9) if r else 9
            if all(rank(e) < 9 for e in enemies):
                enemies.sort(key=rank)
        jl = getattr(ov, "jungler_line", None) if ov is not None else None
        if ov is None:
            jl = "Jungler : en attente d'une partie"
        self._set_text(self.jungler_lbl, jl or "Jungler : inconnu")
        nvis = sum(1 for e in enemies if getattr(e, "visible", False))
        if ov is not None:
            self._set_text(self.visible_lbl, f"{nvis} visible{'s' if nvis > 1 else ''} sur la minimap")
        else:
            self._set_text(self.visible_lbl, "")
        for i, slot in enumerate(self.enemy_slots):
            e = enemies[i] if i < len(enemies) else None
            if e is None:
                sig: tuple = ("empty",)
                if slot["sig"] != sig:
                    slot["icon"].configure(image=self._enemy_image(None, None, "empty"))
                    slot["name"].configure(text="-", text_color=DIM)
                    slot["status"].configure(text=" ", text_color=DIM)
                    slot["box"].configure(border_color=BORDER)
                    slot["border"] = BORDER
                    slot["tip"] = ""
                    slot["sig"] = sig
                continue
            visible = bool(getattr(e, "visible", False))
            appr = bool(getattr(e, "approaching", False))
            jungler = bool(getattr(e, "is_jungler", False))
            ago = getattr(e, "last_seen_ago", None)
            alias = getattr(e, "alias", None)
            role = ui_kit.norm_role(getattr(e, "role", None) or roles.get(alias or "")
                                    or roles.get(getattr(e, "key", "") or ""))
            mode = "approach" if visible and appr else "visible" if visible else "mia"
            name = str(getattr(e, "name", "") or alias or "?")
            mia = None
            if visible:
                status, scol = ("Approche !", WARNING) if appr else ("Visible", SAFE)
            elif isinstance(ago, (int, float)) and math.isfinite(ago):
                status, scol = f"caché {_fmt_ago(ago)}", (WARNING if jungler and ago < 45 else MUTED)
                mia = float(ago)
            else:
                status, scol = "Jamais vu", DIM
            sig = (alias, mode, name, status, jungler, role)
            if slot["sig"] == sig:
                continue
            slot["sig"] = sig
            role_fr = ui_kit.ROLE_FR.get(role or "", "rôle inconnu")
            slot["tip"] = f"{name} · {role_fr}" + (" · jungler ennemi" if jungler and role != "JUNGLE" else "") + \
                f" · {status}"
            slot["icon"].configure(image=self._enemy_image(getattr(e, "icon", None), alias, mode, role, mia, jungler))
            slot["name"].configure(text=_ellipsize(name, 11), text_color=GOLD if jungler else TEXT)
            slot["status"].configure(text=status, text_color=scol)
            border = WARNING if appr else (GOLD_DARK if jungler else BORDER)
            slot["border"] = border
            slot["box"].configure(border_color=border)

    def _update_team(self, ov: Any) -> None:
        """Allies row + lane match-up (me vs the enemy of my role)."""
        allies = list(getattr(ov, "allies", []) or [])[:4] if ov is not None else []
        roles = dict(getattr(ov, "roles", {}) or {}) if ov is not None else {}
        order = {r: i for i, r in enumerate(ui_kit.ROLE_ORDER)}
        allies.sort(key=lambda a: order.get(ui_kit.norm_role(getattr(a, "role", None)
                                                             or roles.get(getattr(a, "alias", "") or "")) or "", 9))
        for i, slot in enumerate(self.ally_slots):
            a = allies[i] if i < len(allies) else None
            if a is None:
                sig: tuple = ("empty",)
                if slot["sig"] != sig:
                    slot["sig"] = sig
                    slot["icon"].configure(image=self._ally_image(None, None, None))
                    slot["name"].configure(text="-", text_color=DIM)
                    slot["tip"] = ""
                continue
            alias = getattr(a, "alias", None)
            role = ui_kit.norm_role(getattr(a, "role", None) or roles.get(alias or ""))
            visible = bool(getattr(a, "visible", False))
            name = str(getattr(a, "name", "") or alias or "?")
            sig = (alias, role, visible, name)
            if slot["sig"] == sig:
                continue
            slot["sig"] = sig
            slot["tip"] = f"{name} · {ui_kit.ROLE_FR.get(role or '', 'rôle inconnu')}" + \
                ("" if visible else " · hors de vue")
            slot["icon"].configure(image=self._ally_image(getattr(a, "icon", None), alias, role))
            slot["name"].configure(text=_ellipsize(name, 9), text_color=TEXT if visible else MUTED)
        me, my_role, opp = ui_kit.lane_opponent(ov) if ov is not None else (None, None, None)
        if ov is None:
            text, col = "En attente", DIM
        elif my_role is None:
            text, col = "Rôle inconnu", DIM
        elif opp is None:
            text, col = f"{ui_kit.ROLE_FR.get(my_role, my_role)} · adversaire inconnu", MUTED
        else:
            ago = getattr(opp, "last_seen_ago", None)
            oname = _ellipsize(str(getattr(opp, "name", "") or getattr(opp, "alias", "") or "?"), 12)
            if getattr(opp, "visible", False):
                text, col = f"{oname} · visible", SAFE
            elif isinstance(ago, (int, float)) and math.isfinite(ago):
                text, col = f"{oname} · caché {_fmt_ago(ago)}", WARNING if ago > 20 else MUTED
            else:
                text, col = f"{oname} · jamais vu", MUTED
        self._set_text(self.matchup_lbl, text)
        try:
            self.matchup_lbl.configure(text_color=col)
        except Exception:
            pass
        sig2 = (me, my_role, getattr(opp, "alias", None),
                getattr(ov, "me_icon", None) is not None if ov is not None else False)
        if sig2 != self._matchup_sig:
            self._matchup_sig = sig2
            self.mu_me.configure(image=self._ally_image(getattr(ov, "me_icon", None) if ov is not None else None,
                                                        me or ("me" if my_role else None), my_role))
            self.mu_opp.configure(image=self._ally_image(getattr(opp, "icon", None) if opp is not None else None,
                                                         getattr(opp, "alias", None), my_role if opp else None,
                                                         ring=ENEMY_RING))

    # ------------------------------------------------------------------ alerts journal
    def _collect_alerts(self, st: Any, ov: Any) -> None:
        gt = getattr(st, "game_time", None) if st is not None else None
        eng = self.engine
        recent = getattr(eng, "recent_alerts", None) if eng is not None else None
        if callable(recent):
            try:
                items = list(recent() or [])[-JOURNAL_MAX:]
                entries = [_alert_entry(a) for a in items]
                entries = [e for e in entries if e is not None and e not in self._journal_hidden]
                sig = tuple(entries)
                if sig != self._journal_sig:
                    self._journal_sig = sig
                    self._journal.clear()
                    self._journal.extend(entries)
                    self._render_journal()
                return
            except Exception:
                log.debug("recent_alerts failed", exc_info=True)
        la = getattr(ov, "last_alert", None) if ov is not None else None
        now = time.monotonic()
        if isinstance(la, (tuple, list)) and len(la) >= 3 and la[0]:
            text, lvl, age = str(la[0]), int(la[1] or 0), float(la[2] or 0.0)
            born = now - age
            prev = self._last_alert_seen
            if prev is None or prev[0] != text or abs(prev[1] - born) > 6.0:
                self._last_alert_seen = (text, born)
                self._journal.append(((gt - age) if isinstance(gt, (int, float)) else None, lvl, text))
                self._render_journal()
            return
        last = getattr(st, "last_alert", None) if st is not None else None
        if last and last != self._last_status_alert:
            self._last_status_alert = str(last)
            self._journal.append((gt, 1, str(last)))
            self._render_journal()

    def _render_journal(self) -> None:
        tb = getattr(self, "journal", None)
        if tb is None:          # dashboard not built yet: rendered when it is
            return
        try:
            empty = not self._journal
            pg = getattr(self, "pregame", None)
            if pg is not None:
                shown = pg.winfo_manager() == "grid"
                if empty and not shown:
                    tb.grid_remove()
                    pg.grid(row=1, column=0, sticky="nsew")
                    self.journal_clear_btn.grid_remove()
                    self._render_pregame()
                elif not empty and shown:
                    pg.grid_remove()
                    tb.grid()
                    self.journal_clear_btn.grid()
                self._journal_caption()
            tb.configure(state="normal")
            tb.delete("1.0", "end")
            if empty:
                tb.insert("end", "Aucune alerte pour l'instant. Les annonces vocales apparaîtront ici.", "empty")
            for gt, lvl, text in reversed(self._journal):
                lvl = min(max(int(lvl), 0), 2)
                tb.insert("end", f"{fmt_clock(gt) if gt is not None else '  -  '}   ", ("time", "line"))
                tb.insert("end", "● ", (f"lvl{lvl}", "line"))
                tb.insert("end", text + "\n", (f"lvl{lvl}", "line"))
            tb.configure(state="disabled")
        except Exception:
            log.debug("Journal render failed", exc_info=True)

    # ------------------------------------------------------------------ "avant la partie" (empty journal)
    def refresh_pregame(self) -> None:
        """Recompute the empty-journal panel on a worker thread: goal, point to work on, game display mode."""
        if self._pregame_busy or self._closing:
            return
        self._pregame_busy = True
        games = list(self._games)

        def job() -> dict[str, Any]:
            out: dict[str, Any] = {"games": games}
            try:
                from treeaicoach import goals  # noqa: PLC0415

                role = next((str(game_field(g, "position") or "") for g in games if game_field(g, "position")), "")
                out["goal"] = goals.pick_goal(games, role) if games else None
            except Exception:
                log.debug("goal unavailable", exc_info=True)
            try:
                from treeaicoach import progress  # noqa: PLC0415

                rows = progress.collect(paths.user_data_dir() / "games", last=20) if len(games) >= 2 else []
                out["focus"] = (progress.focus_points(rows, 1) or [None])[0]
            except Exception:
                log.debug("focus points unavailable", exc_info=True)
            try:
                from treeaicoach import game_settings  # noqa: PLC0415

                gs = game_settings.load_game_settings()
                out["window"] = ui_kit.window_mode_status(getattr(gs, "window_mode", None))
            except Exception:
                out["window"] = ui_kit.window_mode_status(None)
            return out

        def done(data: dict[str, Any]) -> None:
            self._pregame_busy = False
            self._pregame_data = data
            self._render_pregame()

        def failed(_exc: BaseException) -> None:
            self._pregame_busy = False

        self._dispatcher.run(job, done, self.cb(failed), name="TreeAI-ui-pregame")

    def _render_pregame(self) -> None:
        """Fill the "avant la partie" panel (rebuilt only when its data changed)."""
        pg = getattr(self, "pregame", None)
        data = self._pregame_data
        if pg is None or data is None or pg.winfo_manager() != "grid":
            return
        games = data.get("games") or []
        goal = data.get("goal")
        focus = data.get("focus")
        goal_label, goal_why, goal_status = getattr(goal, "label", "") or "", getattr(goal, "why", "") or "", ""
        if self._last_state_key == "RUNNING" and self.engine is not None:   # this game's own goal
            try:
                ex = self.engine.coach_extras() if callable(getattr(self.engine, "coach_extras", None)) else {}
                if isinstance(ex, dict) and ex.get("goal"):
                    goal_label, goal_why = str(ex["goal"]), ""
                    goal_status = str(ex.get("goal_status") or "")
            except Exception:
                log.debug("coach extras unavailable", exc_info=True)
        window = data.get("window") or (-1, "")
        sig = (tuple(str(g.get("path", "")) + str(g.get("precision")) for g in games[:10]),
               goal_label, goal_status, focus, window)
        if sig == self._pregame_sig:
            return
        self._pregame_sig = sig
        for w in pg.winfo_children():
            w.destroy()
        if not games:
            self._pregame_checklist(pg, window)
            return
        g = games[0]
        # last game: champion, result, K/D/A, duration | précision | report
        row = self._frame(pg)
        row.grid(row=0, column=0, sticky="ew", pady=(2, 8))
        row.grid_columnconfigure(1, weight=1)
        alias = str(game_field(g, "champion", "alias", default="") or "")
        pil = self._game_icons.get(alias) or square_icon(None, 56, bg=BG)     # loaded by refresh_games
        self._image_label(row, pil, (36, 36), "pregame-last").grid(row=0, column=0, rowspan=3, padx=(0, 12),
                                                                   sticky="n")
        name = str(game_field(g, "champion_name", "name", default="") or alias or "Champion inconnu")
        res = game_result(g)
        rtxt, rcol = {"win": ("Victoire", SAFE), "lose": ("Défaite", DANGER)}.get(res or "", ("Inachevée", MUTED))
        head = self._frame(row)
        head.grid(row=0, column=1, sticky="w")
        self._label(head, name, self.fonts.h3, TEXT, anchor="w").grid(row=0, column=0, sticky="w")
        self._label(head, rtxt, self.fonts.h3, rcol, anchor="w").grid(row=0, column=1, sticky="w", padx=(10, 0))
        k, d, a = (_int_or_none(game_field(g, x)) for x in ("kills", "deaths", "assists"))
        dur = game_field(g, "duration")
        bits = ["Dernière partie · " + fmt_game_date(game_datetime(g))]
        if k is not None and d is not None and a is not None:
            bits.append(f"{k} / {d} / {a}")
        if isinstance(dur, (int, float)) and dur > 0:
            bits.append(fmt_clock(dur))
        self._label(row, " · ".join(bits), self.fonts.tiny, MUTED, anchor="w").grid(row=1, column=1, sticky="w")
        brief = g.get("plays_brief") if isinstance(g.get("plays_brief"), dict) else {}
        moments = [(brief.get(k), col) for k, col in (("best", SAFE), ("worst", DANGER)) if brief.get(k)]
        if moments:
            mf = self._frame(row)
            mf.grid(row=2, column=1, sticky="w", pady=(2, 0))
            for i, (pl, col) in enumerate(moments):
                gt = pl.get("gt")
                self._label(mf, str(pl.get("title") or "").upper(), self.fonts.tiny_bold, col, anchor="w").grid(
                    row=i, column=0, sticky="w", padx=(0, 6))
                txt = _ellipsize(ui_text(str(pl.get("reason") or "")), 40)
                if isinstance(gt, (int, float)):
                    txt += f" ({fmt_clock(gt)})"
                self._label(mf, txt, self.fonts.tiny, MUTED, anchor="w").grid(row=i, column=1, sticky="w")
        prec = _int_or_none(game_field(g, "precision"))
        pc = self._frame(row)
        pc.grid(row=0, column=2, rowspan=2, sticky="ne", padx=(12, 0))
        self._caption(pc, "Précision", DIM, anchor="e").grid(row=0, column=0, sticky="e")
        self._label(pc, "-" if prec is None else str(prec), self.fonts.stat, precision_color(prec),
                    anchor="e").grid(row=1, column=0, sticky="e")
        self._tip(pc, "Précision des coups notés de ta dernière partie (sur 100)." if prec is not None
                  else "Cette partie n'a pas de coups notés.")
        acts = self._frame(row)            # under the text: never squeezed by a narrow window
        acts.grid(row=3, column=1, columnspan=2, sticky="w", pady=(8, 0))
        self._button(acts, "Rapport", lambda gg=g: self.open_report(gg), "secondary", icon="report",
                     height=BTN_H_SMALL).grid(row=0, column=0, padx=(0, CTL_GAP))
        self._button(acts, "Replay", lambda gg=g: self.open_replay(gg), "secondary", icon="play",
                     height=BTN_H_SMALL).grid(row=0, column=1, padx=(0, CTL_GAP))
        self._button(acts, "Progrès", lambda: self.show_page("analysis", "Progrès"), "ghost",
                     height=BTN_H_SMALL).grid(row=0, column=2)
        self._hline(pg).grid(row=1, column=0, sticky="ew")
        # goal of the next game | point to work on
        cols = self._frame(pg)
        cols.grid(row=2, column=0, sticky="ew", pady=(8, 8))
        cols.grid_columnconfigure(0, weight=2, uniform="pg")
        cols.grid_columnconfigure(1, weight=3, uniform="pg")
        gl = self._frame(cols)
        gl.grid(row=0, column=0, sticky="nw", padx=(0, 16))
        self._caption(gl, "Objectif de la partie", DIM, anchor="w").grid(row=0, column=0, sticky="w")
        self._label(gl, goal_label or "-", self.fonts.num,
                    {"raté": DANGER, "réussi": SAFE}.get(goal_status, ACCENT), anchor="w").grid(
            row=1, column=0, sticky="w", pady=(2, 0))
        why = goal_status or goal_why
        if why:
            self._label(gl, why, self.fonts.tiny, DIM, anchor="w").grid(row=2, column=0, sticky="w")
        fl = self._frame(cols)
        fl.grid(row=0, column=1, sticky="nwe")
        fl.grid_columnconfigure(0, weight=1)
        self._caption(fl, "À travailler", DIM, anchor="w").grid(row=0, column=0, sticky="w")
        if focus:
            title, text = focus
            self._label(fl, str(title), self.fonts.h3, TEXT, anchor="w").grid(row=1, column=0, sticky="w",
                                                                             pady=(2, 0))
            ft = self._label(fl, ui_text(text), self.fonts.small, MUTED, anchor="w", justify="left",
                             wraplength=340)
            ft.grid(row=2, column=0, sticky="w")
            fl.bind("<Configure>", lambda e, lbl=ft: lbl.configure(
                wraplength=max(160, int(e.width / max(0.5, self._scaled(100) / 100)) - 8)), add="+")
        else:
            self._label(fl, "Rien d'urgent : continue comme ça." if len(games) >= 2 else
                        "Joue encore une partie pour voir tes points à travailler.", self.fonts.small, MUTED,
                        anchor="w").grid(row=1, column=0, sticky="w", pady=(2, 0))
        self._hline(pg).grid(row=3, column=0, sticky="ew")
        st = session_stats(games)
        parts = [f"{st['games']} partie{'s' if st['games'] > 1 else ''}",
                 f"{st['wins']} victoire{'s' if st['wins'] > 1 else ''}"]
        if st.get("deaths_per_game") is not None:
            parts.append(f"{fmt_decimal_fr(st['deaths_per_game'], 1)} morts par partie")
        if st.get("precision") is not None:
            parts.append(f"précision moyenne {int(round(st['precision']))}")
        line = self._frame(pg)
        line.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        self._caption(line, "Session · " + st["scope"], DIM, anchor="w").grid(row=0, column=0, sticky="w",
                                                                               padx=(0, 10))
        self._label(line, " · ".join(parts), self.fonts.tiny, MUTED, anchor="w").grid(row=0, column=1, sticky="w")
        if window[0] == 2:      # the game is in exclusive fullscreen: say it before the next game
            self._label(pg, "Ton jeu est en plein écran exclusif : passe en Sans bordure (Options > Vidéo).",
                        self.fonts.small, WARNING, anchor="w").grid(row=5, column=0, sticky="w", pady=(6, 0))

    def _journal_caption(self) -> None:
        cap = getattr(self, "journal_cap", None)
        if cap is not None:
            self._set_text(cap, "JOURNAL" if self._journal else
                           ("JOURNAL · AUCUNE ALERTE POUR L'INSTANT" if self._last_state_key == "RUNNING"
                            else "AVANT LA PARTIE"))

    def _pregame_checklist(self, pg: Any, window: tuple[int, str]) -> None:
        """No game recorded yet: 3 checks before the first game."""
        cols = {0: SAFE, 1: WARNING, 2: DANGER, -1: DIM}
        self._label(pg, "Trois vérifications avant ta première partie.", self.fonts.small, MUTED, anchor="w").grid(
            row=0, column=0, sticky="w", pady=(2, 6))
        voice_ok = str(getattr(self.voice, "backend", "") or "").lower() in ("sapi", "onecore", "neural")
        items = (
            ("Jeu en Sans bordure", window[0], window[1], "Aide", lambda: self._run_fix("help_borderless")),
            ("Overlay", 0 if getattr(self.cfg, "overlay_enabled", True) else 1,
             "affiche un exemple de gank 10 s" if getattr(self.cfg, "overlay_enabled", True) else "désactivé",
             "Tester", self.test_overlay),
            ("Voix", 0 if voice_ok else 1, "écoute une alerte d'exemple" if voice_ok else "aucune voix Windows",
             "Écouter" if voice_ok else "Réglages",
             self.test_voice if voice_ok else (lambda: self._run_fix("voice_settings"))),
        )
        for i, (title, level, text, btn, fn) in enumerate(items):
            r = 1 + 2 * i
            if i:
                self._hline(pg).grid(row=r - 1, column=0, sticky="ew")
            row = self._frame(pg)
            row.grid(row=r, column=0, sticky="ew", pady=6)
            row.grid_columnconfigure(1, weight=1)
            self._label(row, str(i + 1), self.fonts.num, ACCENT, width=18, anchor="w").grid(
                row=0, column=0, rowspan=2, sticky="n", padx=(0, 8))
            self._label(row, title, self.fonts.body, TEXT, anchor="w").grid(row=0, column=1, sticky="w")
            self._label(row, ui_text(text), self.fonts.small, cols.get(level, MUTED) if level == 2 else MUTED,
                        anchor="w").grid(row=1, column=1, sticky="w")
            self._button(row, btn, fn, "secondary", width=96, height=BTN_H_SMALL).grid(
                row=0, column=2, rowspan=2, sticky="e", padx=(12, 0))

    # ------------------------------------------------------------------ radar preview & pulse
    def _preview_loop(self) -> None:
        """Paste the latest radar frame (5 Hz) while the dashboard is on screen; 1 Hz check otherwise."""
        if self._closing:
            return
        visible = False
        try:
            visible = self._dash_live()
            if visible != self._radar_worker.active.is_set():
                (self._radar_worker.active.set if visible else self._radar_worker.active.clear)()
            if visible:
                seq, img = self._radar_worker.latest()
                if seq != self._radar_seq:
                    self._radar_seq = seq
                    live = img is not None
                    if live:
                        if img.size != (self._radar_size, self._radar_size):
                            img = img.resize((self._radar_size, self._radar_size), Image.LANCZOS)
                        self._radar_photo.paste(img)
                    elif self._radar_live:
                        self._radar_photo.paste(self._radar_placeholder)
                    if live != self._radar_live:
                        self._radar_live = live
                        if live:
                            self.radar_msg.place_forget()
                            self.radar_badge.configure(text=" EN DIRECT ", text_color=ON_GOLD, fg_color=TEAL)
                        else:
                            self.radar_msg.place(relx=0.5, rely=0.5, anchor="center")
                            self.radar_badge.configure(text=" HORS LIGNE ", text_color=MUTED, fg_color=PANEL_HI)
        except Exception:
            log.exception("Radar preview update failed")
        self.root.after(PREVIEW_MS if visible else IDLE_LOOP_MS, self._preview_loop)

    def _pulse_loop(self) -> None:
        """The live dot breathes (12 fps) only on the visible dashboard during a game; else 1 Hz check."""
        if self._closing:
            return
        active = False
        try:
            color = getattr(self, "_state_color", DIM)
            if color != getattr(self, "_pill_drawn", None):       # sidebar status square: steady colour
                self._pill_drawn = color
                self.pill_dot.itemconfigure(self._pill_dot_item, fill=color)
            if self._dash_live():
                active = self._last_state_key == "RUNNING"     # pulse = "live", nothing else moves
                if active:
                    self._pulse_phase = (self._pulse_phase + PULSE_MS / 1000 / 1.6) % 1.0
                k = 0.5 - 0.5 * math.cos(2 * math.pi * self._pulse_phase) if active else 0.0
                sig = (color, round(k, 2), active)
                if sig != getattr(self, "_pulse_sig", None):
                    self._pulse_sig = sig
                    self.hero.pulse(color, k, active)
                self._draw_gauge_step()
                active = active or abs(self._gauge_target - self._gauge_frac) >= 0.004
        except Exception:
            log.debug("Pulse failed", exc_info=True)
        self.root.after(PULSE_MS if active else IDLE_LOOP_MS,
                        self._pulse_loop)

    # ------------------------------------------------------------------ run / close
    def run(self, smoke_seconds: float | None = None) -> int:
        """Main loop; returns 0 once the window is closed."""
        if smoke_seconds is not None:
            try:
                delay = max(0, int(float(smoke_seconds) * 1000))
            except (TypeError, ValueError):
                delay = 0
            self.root.after(delay, self.close)
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            self.close()
        except Exception as exc:
            if self._closing:
                log.debug("Tk main loop ended during shutdown: %s", exc)
            else:
                log.exception("Tk main loop failed")
                self.close()
        return 0

    def close(self) -> None:
        """Stop everything, save the configuration and destroy the window. Idempotent."""
        if self._closing:
            return
        self._closing = True
        try:
            geo = self.root.geometry()
            if self.root.state() == "normal":
                self.cfg = dataclasses.replace(self.cfg, ui_geometry=geo).validated()
        except Exception:
            pass
        if self._save_job is not None:
            try:
                self.root.after_cancel(self._save_job)
            except Exception:
                pass
            self._save_job = None
        save_config(self.cfg, self._save_path)
        self._radar_worker.stop()
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
        try:   # cancel every pending after() (ours and CustomTkinter's): no "invalid command name" noise
            for job in self.root.tk.splitlist(self.root.tk.call("after", "info")):
                try:   # plain Tcl cancel: the widgets still own (and delete) their callback commands
                    self.root.tk.call("after", "cancel", job)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            log.debug("root destroy failed", exc_info=True)
        self._closed.set()

    @staticmethod
    def _shutdown_components(engine: Any, overlay: Any, voice: Any, hotkeys: Any = None) -> None:
        for name, obj, call in (("hotkeys", hotkeys, "stop"), ("overlay", overlay, "stop"),
                                ("engine", engine, "stop"), ("voice", voice, "stop")):
            if obj is None:
                continue
            try:
                getattr(obj, call)()
            except Exception:
                log.exception("Cannot stop %s", name)


# ======================================================================================
# Module-level helpers that stay here (tests patch ui._report_function; engine factories)
# ======================================================================================
def _report_function(name: str) -> Callable[..., Any] | None:
    """``list_games`` / ``write_report`` from report.py (or analysis.py), None if unavailable."""
    for mod in ("treeaicoach.report", "treeaicoach.analysis"):
        try:
            import importlib  # noqa: PLC0415

            m = importlib.import_module(mod)
        except Exception:
            continue
        fn = getattr(m, name, None)
        if callable(fn):
            return fn
    log.info("%s() is not available (report module missing)", name)
    return None


def _prewarm_preview() -> None:
    """Build the Overlay page preview once in the background (sample state, backdrop) so the page opens fast."""
    try:
        from treeaicoach import ui_preview  # noqa: PLC0415

        ui_preview.compose(Config())
    except Exception:
        log.debug("overlay preview prewarm failed", exc_info=True)


def _default_engine_factory(cfg: Config, voice: Any, detector: Any, frame_source: Any) -> Any:
    from treeaicoach.engine import CoachEngine  # noqa: PLC0415 - written in parallel, imported lazily

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
    """Same detector as the engine builds itself (engine._ensure_detector): champion DB for the
    roster matcher, icon-scale prior, and the learned icon of a custom skin (``learn_cache``,
    real games only). The engine adopts it and plugs its own ``on_scale`` callback."""
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
    except TypeError:          # older detector module
        return create_detector(cfg.detector_backend, cfg.detection_threshold)


def _default_demo_source() -> Any:
    from treeaicoach.demo import DemoSource  # noqa: PLC0415

    return DemoSource()


# ======================================================================================
# Entry point
# ======================================================================================
def run_app(cfg: Config, *, demo: bool = False, smoke_seconds: float | None = None,
            _engine_factory: EngineFactory | None = None,
            _overlay_factory: Callable[[Config, Callable[[], Any]], Any] | None = None,
            _voice: Any = None, _detector_factory: Callable[[Config], Any] | None = None,
            _demo_source_factory: Callable[[], Any] | None = None, _hotkeys: bool = True,
            _save_path: Path | None = None) -> int:
    """Open the main window (blocking) and return 0 when it is closed.

    ``demo`` starts the engine on the simulated game; ``smoke_seconds`` closes the window
    automatically after that delay (CI). The underscore parameters inject stand-ins (tests).
    Returns 1 only if the window itself cannot be created (no display...).
    """
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


__all__ = ["run_app", "CoachApp", "fmt_clock", "fmt_int_fr", "state_key", "session_stats",
           "autostart_support", "get_windows_autostart", "set_windows_autostart", "app_icon_path"]
