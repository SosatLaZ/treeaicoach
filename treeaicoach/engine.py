"""Main analysis engine (ARCHITECTURE.md §4.15 + §7.5): threads, game lifecycle, pipeline.

:class:`CoachEngine` glues every runtime module together:

* a **Live Client poller** thread (1 Hz in game, 0.5 Hz outside) feeds the game lifecycle
  (new game -> every analyser reset and a new :class:`GameRecorder`; ``GameEnd`` event or the
  API gone for more than :data:`GAME_GONE_S` s -> record finished, post-game report written
  in a background thread and optionally opened; session statistics / break reminder);
* an **analysis** thread ticks at ``cfg.target_fps`` with a monotonic scheduler (it sleeps
  until the next tick, never busy-waits, and does nothing - no capture at all - outside a
  game). One tick (:meth:`CoachEngine.step`)::

      game window (cached 2 s) -> minimap rect (manual / auto locate / fallback, verified)
      -> grab -> black-frame check -> detector -> identifier (+ camera "self" fallback)
      -> tracker -> gank -> objectives -> reminders -> fog tracker -> recorder
      -> throttler -> voice

* a :class:`FrameSource` (the demo, tests) can replace the capture + Live Client: its
  ``next(t)`` gives both the minimap and the :class:`GameInfo`.

Everything public is thread-safe and never raises; every tick body is wrapped (rate-limited
logging + error counter) and the thread loops restart with a backoff if anything escapes.
Nothing here reads game memory, injects input or hooks the game: screen pixels and the
official Live Client Data API only.
"""

from __future__ import annotations

import logging
import math
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from treeaicoach import geometry
from treeaicoach.alerts import Alert, AlertKind, AlertThrottler, Level
from treeaicoach.capture import Rect, is_black_frame
from treeaicoach.config import Config
from treeaicoach.engine_base import (
    ACE_TEXT,
    ACE_TEXT_FAR,
    BACKOFF_MAX_S,
    COACH_KINDS,
    CONSECUTIVE_ERRORS_STATE,
    DEATH_RECAP_DELAY_S,
    GAME_GONE_S,
    GAME_TIME_BACK_S,
    GANK_KINDS,
    MSG_BLACK,
    MSG_ERROR,
    MSG_LOCATING,
    MSG_MINIMAP_COVERED,
    MSG_NO_FRAME,
    MSG_OCCLUDED,
    MSG_RUNNING,
    MSG_RUNNING_DEMO,
    MSG_SPECTATOR,
    MSG_STOPPED,
    MSG_WAITING,
    MUTE_DELAY_S,
    POLL_IDLE_S,
    POLL_IN_GAME_S,
    RECENT_ALERTS_MAX,
    SIEGE_TEXT,
    STATS_EVERY_S,
    THREAT_HOLD_S,
    EngineState,
    EngineStatus,
    FrameSource,
    _as_bgr,
    _default_opener,
    _PassThroughIdentifier,
    _Throttle,
    find_camera_center,
    siege_state,
    structure_owner,
    unsupported_message,
)
from treeaicoach.engine_capture import CaptureMixin
from treeaicoach.engine_coaching import CoachingMixin
from treeaicoach.engine_overlay_state import OverlayStateMixin
from treeaicoach.engine_postgame import PostgameMixin
from treeaicoach.engine_vision import VisionMixin
from treeaicoach.fmtutil import finite_loose as _finite, seconds_fr
from treeaicoach.live_client import GameInfo
from treeaicoach.scheduler import HeavyScheduler, MotionSnapshot, RateGovernor, burst_reason
from treeaicoach.sysperf import CpuMeter, PerfBudget, RateMeter, RollingStats

log = logging.getLogger(__name__)


class CoachEngine(PostgameMixin, CoachingMixin, VisionMixin, CaptureMixin, OverlayStateMixin):
    """The analysis engine. See the module docstring. All public methods are thread-safe."""

    def __init__(self, cfg: Config, voice: Any, detector: Any = None,
                 live_client: Any = None, frame_source: FrameSource | None = None,
                 clock: Callable[[], float] = time.monotonic, *,
                 identifier: Any = None, gank: Any = None, locator: Any = None,
                 champion_db: Any = None,
                 window_finder: Callable[[], Rect | None] | None = None,
                 screen_capture: Any = None,
                 recorder_factory: Callable[[], Any] | None = None,
                 report_writer: Callable[[Path], Path | None] | None = None,
                 report_opener: Callable[[Path], None] | None = None,
                 enable_hotkeys: bool = True, manage_overlay: bool = True) -> None:
        self._cfg = self._validated(cfg)
        self._voice = voice
        self._clock = clock
        self._frame_source = frame_source
        self._demo = bool(getattr(frame_source, "is_demo", False))
        self._live_client = live_client
        self._own_detector = detector is None
        self._detector = detector
        self._detector_key: tuple | None = None
        self._identifier_arg = identifier
        self._identifier: Any = identifier
        self._gank: Any = gank
        self._locator = locator
        self._db = champion_db
        self._window_finder = window_finder
        self._capture = screen_capture
        # game_settings.py: League's own config files (FlipMiniMap, MinimapScale...) and the
        # last located minimap per settings; only for the real screen (not tests / demo)
        self._settings_watcher: Any = None
        self._rect_cache: Any = None
        if window_finder is None and screen_capture is None and frame_source is None:
            try:
                from treeaicoach.game_settings import RectCache, SettingsWatcher

                self._settings_watcher, self._rect_cache = SettingsWatcher(), RectCache()
            except Exception:
                log.debug("Game settings unavailable", exc_info=True)
        self._recorder_factory = recorder_factory
        self._report_writer = report_writer
        self._report_opener = report_opener or _default_opener
        self._enable_hotkeys = bool(enable_hotkeys)
        self._manage_overlay = bool(manage_overlay)

        self._lock = threading.RLock()         # engine state (short sections)
        self._step_lock = threading.Lock()     # one tick at a time
        self._stop_evt = threading.Event()
        self._game_evt = threading.Event()     # set while a supported game is running
        self._threads: list[threading.Thread] = []
        self._bg_threads: list[threading.Thread] = []
        self._running = False
        self._err = _Throttle()

        # analysers (created lazily: module import must stay cheap)
        self._components_ready = False
        self._tracker: Any = None
        self._objectives: Any = None
        self._reminders: Any = None
        self._coach: Any = None                # coach.MapCoach (live macro tips + HUD insight)
        self._fog: Any = None
        self._jungle_intel: Any = None      # jungle_intel.JungleIntelTracker (Tab data)
        self._scoreboard: Any = None           # scoreboard.ScoreboardAnalyzer (Tab analysis)
        self._praise: Any = None               # praise.PraiseCoach (compliments)
        self._toasts: Any = None               # toasts.ToastQueue (top-centre banners)
        self._stance: Any = None               # coach.StanceAdvisor (PRUDENT / ÉQUILIBRÉ / AGRESSIF)
        self._tip_rotator: Any = None          # tips.TipRotator (written tips, HUD)
        self._gauge: Any = None                # coach.PlayGauge ("jouer plus fort ou non", HUD + dashboard)
        self._gate: Any = None                 # voice_policy.MessageGate (anti-spam, per game)
        self._tactics: Any = None              # tactics.TacticalDirector (fight / phase / positioning / wards / voice gate)
        self._ward_guide: Any = None           # ward_guide.WardGuideManager (minimap + game-view ward spot guide)
        self._tip_text: str | None = None
        self._hud_tip_prev: str | None = None   # HUD advice line shown last + since when (fade-in)
        self._hud_tip_since = 0.0
        self._text_msg: tuple[float, str] | None = None   # latest written-only message (HUD line)
        self.text_messages: list[tuple[float, str, str]] = []   # (t, kind, text) written-only, this game
        self._sb_recorded: Any = None
        self._next_heavy_t = -math.inf          # next tick running the coaching stages
        self._throttler = AlertThrottler()
        self._recorder: Any = None
        self._hotkeys: Any = None
        self._overlay_mgr: Any = None

        # game lifecycle
        self._game: GameInfo | None = None
        self._game_t: float = 0.0              # engine time of the last GameInfo
        self._last_game_seen: float | None = None
        self._in_game = False
        self._ended = False                    # GameEnd seen for the current game
        self._last_gt: float | None = None
        self._roster_sig: tuple | None = None
        self._prefetched = False
        self._was_dead = False
        self._death_due: tuple[float, Any] | None = None
        self._next_poll = -math.inf
        self._icons: dict[str, np.ndarray | None] = {}

        # capture / location
        self._window: Rect | None = None
        self._window_t = -math.inf
        self._minimap_rect: Rect | None = None
        self._rect_window: Rect | None = None
        self._locate_method: str | None = None
        self._relocate = True
        self._next_locate = -math.inf
        self._next_verify = -math.inf
        self._bad_since: float | None = None
        self._last_collect = -math.inf
        self._collect_count = 0

        # results / status
        self._state = EngineState.STOPPED
        self._message = MSG_STOPPED
        self._fps = 0.0
        self._tick_ms = 0.0
        self._last_tick_t: float | None = None
        self._errors = 0
        self._consecutive_errors = 0
        self._last_alert: Alert | None = None
        self._last_alert_t: float | None = None
        self._recent: deque[tuple[float | None, str, int, str]] = deque(maxlen=RECENT_ALERTS_MAX)
        self._threat_hist: deque[tuple[float, int, Alert]] = deque(maxlen=64)
        self._last_danger_t: float | None = None
        self._frame: np.ndarray | None = None
        self._identified: list[Any] = []
        # learned minimap icon of me (custom skins) + HUD portrait (self_icon.py, hud_reader.py)
        self._selficon_next = -math.inf
        self._selficon_t: float | None = None
        self._hud_reader: Any = None
        self._hud_cal: tuple[float, tuple[int, int] | None] = (-math.inf, None)
        self._preview_cache: tuple[int, np.ndarray] | None = None
        self._frame_id = 0
        self._overlay_cache: tuple[float, Any] | None = None
        self._overlay_visible = True
        self._demo_rects: tuple[Rect | None, Rect | None] | None = None
        self._muted = False
        self._mute_timer: threading.Timer | None = None
        self._last_where_t = -math.inf
        self._banner: str | None = None
        self._session = {"games": 0, "wins": 0, "losses": 0, "loss_streak": 0}
        self.last_record_path: Path | None = None
        self.last_report_path: Path | None = None
        self._init_runtime()

    def _init_runtime(self) -> None:
        """Scheduling / health / diagnostics state (pipeline v2: adaptive rate, staggered
        coaching stages, render-time prediction, capture health, low-end budget)."""
        cfg = self._cfg
        self._budget = PerfBudget(str(getattr(cfg, "perf_mode", "auto")), target_fps=float(cfg.target_fps))
        prof = self._budget.profile
        self._governor = RateGovernor(prof.calm_fps, prof.burst_fps)
        self._adaptive = bool(getattr(cfg, "adaptive_rate", True))
        self._heavy = HeavyScheduler(prof.heavy_hz)
        self._heavy_now: set[str] = set()
        #: None = auto (stagger the coaching stages when the analysis thread runs)
        self.stagger: bool | None = None
        self._applied_profile: str | None = None
        self._motion: MotionSnapshot | None = None
        self._stats = {k: RollingStats() for k in ("tick", "vision", "grab", "coach", "latency")}
        self._cap_rate = RateMeter()
        self._cpu = CpuMeter()
        self._stats_next = -math.inf
        self._win_info: Any = None
        self._unfocused_since: float | None = None
        self._paused: str | None = None
        self._minimap_score: float | None = None
        self._verify_due_deferred = False
        self._capture_status = "ok"
        self._capture_note: str | None = None      # exclusive fullscreen / frozen capture warning
        self._last_frame_t: float | None = None
        self._overlay_stats: Any = None             # OverlayManager.stats (set by whoever owns it)
        self._diag: Any = None                      # diag.DiagRecorder while a bundle is recorded
        self._diag_req: dict[str, Any] = {}         # requests served by the analysis thread
        self._diag_hotkeys: Any = None
        self._occluded = False
        #: tests: ``probe(minimap_rect) -> bool`` replaces the WindowFromPoint occlusion check
        self.occlusion_probe: Callable[[Rect], bool | None] | None = None

    # ================================================================== configuration
    @staticmethod
    def _validated(cfg: Any) -> Config:
        try:
            return cfg.validated() if isinstance(cfg, Config) else Config()
        except Exception:
            log.exception("Invalid configuration: defaults used")
            return Config()

    @property
    def cfg(self) -> Config:
        return self._cfg

    def apply_config(self, cfg: Config) -> None:
        """Apply new settings live (voice, radii, fps, detector, overlay, hotkeys). Never raises."""
        try:
            new = self._validated(cfg)
            with self._lock:
                old = self._cfg
                self._cfg = new
                if (getattr(old, "perf_mode", "auto"), old.target_fps) != \
                        (getattr(new, "perf_mode", "auto"), new.target_fps):
                    self._budget = PerfBudget(str(getattr(new, "perf_mode", "auto")),
                                              target_fps=float(new.target_fps))
                    self._applied_profile = None
                self._adaptive = bool(getattr(new, "adaptive_rate", True))
                if getattr(old, "capture_backend", "auto") != getattr(new, "capture_backend", "auto") \
                        and self._window_finder is None and self._frame_source is None:
                    self._diag_req["recreate_capture"] = True
                if (old.minimap_mode, old.minimap_side, old.manual_minimap_rect) != \
                        (new.minimap_mode, new.minimap_side, new.manual_minimap_rect):
                    self._relocate = True
            for comp in (self._gank, self._objectives, self._reminders, self._fog, self._overlay_mgr,
                         self._coach, self._stance, self._tactics, self._ward_guide):
                fn = getattr(comp, "apply_config", None)
                if callable(fn):
                    try:
                        fn(new)
                    except Exception:
                        log.exception("apply_config failed for %r", type(comp).__name__)
            if self._tip_rotator is not None:
                try:
                    from treeaicoach.skill import tip_min_prio
                    self._tip_rotator.min_prio = tip_min_prio(new)
                except Exception:
                    log.debug("skill level unavailable", exc_info=True)
            sdv = getattr(self._voice, "set_danger_voice", None)
            if callable(sdv):
                try:
                    sdv(getattr(new, "danger_voice", "bip_voix"))
                except Exception:
                    log.debug("voice.set_danger_voice failed", exc_info=True)
            set_params = getattr(self._voice, "set_params", None)
            if callable(set_params):
                try:
                    set_params(voice_name=new.voice_name, rate=new.voice_rate,
                               volume=new.voice_volume, beep_on_danger=new.beep_on_danger,
                               engine=getattr(new, "voice_engine", None),
                               neural_voice=getattr(new, "neural_voice", None),
                               neural_rate=getattr(new, "neural_rate", None))
                except Exception:
                    log.exception("voice.set_params failed")
            hk = (old.hotkey_jungler, old.hotkey_mute, old.hotkey_overlay, getattr(old, "hotkey_ward", ""))
            if hk != (new.hotkey_jungler, new.hotkey_mute, new.hotkey_overlay, getattr(new, "hotkey_ward", "")) \
                    and self._hotkeys is not None:
                try:
                    self._hotkeys.set_bindings(self._hotkey_bindings())
                except Exception:
                    log.exception("Hotkey rebinding failed")
        except Exception:
            log.exception("CoachEngine.apply_config failed")

    # ================================================================== components
    def _ensure_components(self) -> None:
        """Create the analysers on first use (tracker, gank, objectives, reminders, fog...)."""
        if self._components_ready:
            return
        cfg = self._cfg
        from treeaicoach.tracker import Tracker

        self._tracker = Tracker()
        if self._gank is None:
            try:
                from treeaicoach.gank import GankAnalyzer

                self._gank = GankAnalyzer(cfg)
            except Exception:
                log.exception("Gank analyzer unavailable: no gank alerts")
        try:
            from treeaicoach.objectives import ObjectiveTimers

            self._objectives = ObjectiveTimers(cfg)
        except Exception:
            log.exception("Objective timers unavailable")
        try:
            from treeaicoach.reminders import PersonalReminders

            self._reminders = PersonalReminders(cfg)
        except Exception:
            log.exception("Personal reminders unavailable")
        try:
            from treeaicoach.coach import MapCoach

            self._coach = MapCoach(cfg)
        except Exception:
            log.exception("Map coach unavailable")
        try:
            from treeaicoach.coach import PlayGauge, StanceAdvisor
            from treeaicoach.tips import TipRotator
            from treeaicoach.voice_policy import MessageGate

            self._gauge = PlayGauge()
            self._stance = StanceAdvisor(cfg)
            self._tip_rotator = TipRotator()
            try:
                from treeaicoach.skill import tip_min_prio
                self._tip_rotator.min_prio = tip_min_prio(cfg)
            except Exception:
                log.debug("skill level unavailable", exc_info=True)
            self._gate = MessageGate()
        except Exception:
            log.exception("Stance / tips / voice policy unavailable")
        try:
            from treeaicoach.tactics import TacticalDirector

            self._tactics = TacticalDirector(cfg)
        except Exception:
            log.exception("Tactical director unavailable (fight calls / positioning / wards)")
        try:
            from treeaicoach.ward_guide import WardGuideManager

            self._ward_guide = WardGuideManager(cfg)
        except Exception:
            log.exception("Ward guide unavailable")
        try:
            from treeaicoach.fog_tracker import FogTracker

            self._fog = FogTracker(max_s=cfg.fog_max_s)
            self._fog.apply_config(cfg)
        except Exception:
            log.exception("Fog tracker unavailable")
        try:
            from treeaicoach.jungle_intel import JungleIntelTracker

            self._jungle_intel = JungleIntelTracker()
        except Exception:
            log.exception("Jungle intel unavailable")
        try:
            from treeaicoach.praise import PraiseCoach
            from treeaicoach.scoreboard import ScoreboardAnalyzer

            self._scoreboard = ScoreboardAnalyzer()
            self._praise = PraiseCoach()
            set_items = getattr(self._coach, "set_item_tips", None)
            if callable(set_items):     # item completions are announced by the Tab analyser
                set_items(False)
        except Exception:
            log.exception("Scoreboard / praise unavailable")
        try:
            from treeaicoach.toasts import ToastQueue

            self._toasts = ToastQueue(clock=self._clock)
        except Exception:
            log.exception("Toasts unavailable")
        if getattr(self, "_presenter", None) is None and getattr(self, "presenter_enabled", True):
            from treeaicoach.presenter import Presenter  # ONE router: banner / panel / badge / drop

            self._presenter = Presenter()
        if self._identifier is None:
            try:
                from treeaicoach.identifier import ChampionIdentifier

                self._identifier = ChampionIdentifier(self._champion_db())
            except Exception:
                log.exception("Champion identifier unavailable: relations from the detector only")
                self._identifier = _PassThroughIdentifier()
        self._components_ready = True

    def _champion_db(self) -> Any:
        if self._db is None:
            try:
                from treeaicoach.champions import get_default_db

                self._db = get_default_db()
            except Exception:
                log.exception("Champion database unavailable")
        return self._db

    def _ensure_detector(self) -> None:
        cfg = self._cfg
        key = (cfg.detector_backend, float(cfg.detection_threshold))
        if self._detector is not None and (not self._own_detector or key == self._detector_key):
            if not self._own_detector and self._detector_key is None:
                self._adopt_detector()
            return
        from treeaicoach import detector as _det
        from treeaicoach.detector import create_detector

        _det.ONNX_THREADS = self._budget.profile.onnx_threads
        old = self._detector
        self._detector = create_detector(cfg.detector_backend, cfg.detection_threshold,
                                         db=self._champion_db(),
                                         scale_store=dict(getattr(cfg, "icon_scale_by_res",
                                                                  None) or {}),
                                         on_scale=self._store_icon_scale,
                                         learn_cache=self._frame_source is None)
        self._detector_key = key
        if self._game is not None and hasattr(self._detector, "set_roster"):
            self._detector.set_roster(self._game)   # roster matcher (portraits of the 10)
        if old is not None:
            try:
                old.close()
            except Exception:
                pass

    def _adopt_detector(self) -> None:
        """A detector built by the caller (the UI's factory calls ``create_detector(backend,
        threshold)`` only): give its roster matcher the persisted icon scale prior and the
        callback storing new calibrations, as for a detector the engine builds itself (the
        harnesses always had them, the real app did not)."""
        self._detector_key = ("adopted",)
        try:
            m = getattr(self._detector, "matcher", None)
            if m is None:
                return
            if getattr(m, "scale_store", None) is None:
                m.scale_store = dict(getattr(self._cfg, "icon_scale_by_res", None) or {})
            if getattr(m, "on_scale", None) is None:
                m.on_scale = self._store_icon_scale
        except Exception:
            log.debug("Cannot adopt the detector", exc_info=True)

    def _apply_perf_profile(self) -> None:
        """Push the budget's knobs (detection rates, coaching rate, generic detector cadence,
        thread counts) to the components; cheap no-op when unchanged."""
        prof = self._budget.profile
        sig = (prof.name, prof.calm_fps, prof.burst_fps, prof.heavy_hz)
        if sig == self._applied_profile:
            return
        self._applied_profile = sig
        self._governor.configure(prof.calm_fps, prof.burst_fps)
        self._heavy.hz = prof.heavy_hz
        try:
            if cv2.getNumThreads() > prof.cv_threads:
                cv2.setNumThreads(prof.cv_threads)
        except Exception:
            pass
        det = self._detector
        if det is not None and hasattr(det, "FALLBACK_EVERY"):
            try:
                det.FALLBACK_EVERY = int(prof.onnx_every)     # instance override of the class constant
            except Exception:
                pass
        try:   # overlay frame-rate cap (the overlay thread may belong to the UI)
            from treeaicoach import overlay as _overlay

            _overlay.set_budget_fps(prof.overlay_fps)
        except Exception:
            pass
        try:   # whole-map search of lost champions (module constant read at each frame)
            from treeaicoach import roster_matcher

            roster_matcher.LOST_EVERY = int(prof.lost_every)
        except Exception:
            pass
        log.info("Performance budget %s (%s): detection %.0f-%.0f img/s, overlay %.0f img/s",
                 prof.name, self._budget.reason or "auto", prof.calm_fps, prof.burst_fps, prof.overlay_fps)

    def _store_icon_scale(self, key: str, ratio: float) -> None:
        """Calibrated icon scale (roster matcher) -> config, prior of the next games."""
        try:
            store = getattr(self._cfg, "icon_scale_by_res", None)
            if isinstance(store, dict):
                store[str(key)] = round(float(ratio), 5)
        except Exception:
            log.debug("Cannot store the icon scale", exc_info=True)

    def _ensure_locator(self) -> Any:
        if self._locator is None:
            from treeaicoach.minimap_locator import MinimapLocator

            self._locator = MinimapLocator()
        return self._locator

    # ================================================================== threads
    def start(self) -> None:
        """Start the poller + analysis threads (idempotent). Never raises."""
        try:
            with self._lock:
                if self._running:
                    return
                self._running = True
                self._stop_evt.clear()
                self._state, self._message = EngineState.WAITING_GAME, MSG_WAITING
            start_voice = getattr(self._voice, "start", None)
            if callable(start_voice):
                try:
                    start_voice()
                except Exception:
                    log.exception("voice.start failed")
            threads = [threading.Thread(target=self._guarded_loop, args=(self._analysis_loop, "analysis"),
                                        name="TreeAICoach-analysis", daemon=True)]
            if self._frame_source is None:
                threads.append(threading.Thread(target=self._guarded_loop, args=(self._poll_loop, "poller"),
                                                name="TreeAICoach-poller", daemon=True))
            self._threads = threads
            for th in threads:
                th.start()
            self._start_hotkeys()
            self._start_diag_hotkey()
            self._start_overlay()
            log.info("Engine started (%s)", "demo" if self._demo else
                     ("frame source" if self._frame_source is not None else "live"))
        except Exception:
            log.exception("CoachEngine.start failed")

    def stop(self, timeout: float = 3.0) -> None:
        """Stop the threads (joined with ``timeout``), finish the current record. Never raises."""
        try:
            with self._lock:
                was_running = self._running
                self._running = False
                self._stop_evt.set()
                self._game_evt.set()          # wake the analysis loop
                threads, self._threads = self._threads, []
            deadline = time.monotonic() + max(0.1, float(timeout))
            for th in threads:
                if th is not threading.current_thread():
                    th.join(max(0.05, deadline - time.monotonic()))
                    if th.is_alive():
                        log.warning("Thread %s did not stop in time", th.name)
            for comp in (self._hotkeys, self._overlay_mgr, getattr(self, "_play_fx", None),
                         self._diag_hotkeys):
                if comp is not None:
                    try:
                        comp.stop()
                    except Exception:
                        log.exception("stop failed for %r", type(comp).__name__)
            self._hotkeys = None
            self._overlay_mgr = None
            self._diag_hotkeys = None
            timer = self._mute_timer
            if timer is not None:
                timer.cancel()
            if was_running:
                with self._lock:
                    rec, self._recorder = self._recorder, None
                if rec is not None:
                    try:
                        rec.finish()    # partial game kept (no report for an interrupted game)
                    except Exception:
                        log.exception("recorder.finish failed")
            self.wait_background(max(0.05, deadline - time.monotonic()))
            with self._lock:
                self._state, self._message = EngineState.STOPPED, MSG_STOPPED
                self._in_game = False
                self._game_evt.clear()
                self._fps = 0.0
            cap = self._capture
            if cap is not None and self._window_finder is None:
                try:
                    cap.close()
                except Exception:
                    pass
                self._capture = None
            if was_running:
                log.info("Engine stopped")
        except Exception:
            log.exception("CoachEngine.stop failed")

    def is_running(self) -> bool:
        return self._running and any(th.is_alive() for th in self._threads)

    def wait_background(self, timeout: float = 10.0) -> bool:
        """Wait for the background jobs (record + report writing). True if all finished."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._lock:
            jobs = list(self._bg_threads)
        for th in jobs:
            th.join(max(0.0, deadline - time.monotonic()))
        with self._lock:
            self._bg_threads = [th for th in self._bg_threads if th.is_alive()]
            return not self._bg_threads

    def _guarded_loop(self, body: Callable[[], None], name: str) -> None:
        """Run ``body`` until stop; restart it with a backoff if it ever raises."""
        backoff = 0.5
        while not self._stop_evt.is_set():
            try:
                body()
                return
            except Exception:
                self._errors += 1
                self._err.exception("Engine %s loop crashed: restarting in %.1f s", name, backoff)
                self._stop_evt.wait(backoff)
                backoff = min(BACKOFF_MAX_S, backoff * 2)

    def _poll_loop(self) -> None:
        while not self._stop_evt.is_set():
            t = self._clock()
            self._poll_once(t)
            period = POLL_IN_GAME_S if self._in_game or self._game is not None else POLL_IDLE_S
            self._stop_evt.wait(period)

    def _poll_once(self, t: float) -> None:
        client = self._live_client
        if client is None:
            try:
                from treeaicoach.live_client import LiveClient

                client = self._live_client = LiveClient()
            except Exception:
                self._err.exception("Live Client unavailable")
                return
        try:
            game = client.fetch()
        except Exception:
            self._err.exception("LiveClient.fetch failed")
            game = None
        self._handle_game_info(game if isinstance(game, GameInfo) else None, t)

    def _analysis_loop(self) -> None:
        next_t = self._clock()
        while not self._stop_evt.is_set():
            if self._frame_source is None and not self._in_game:
                # no game: no capture, no CPU; wake up when the poller sees a game
                self._fps = 0.0
                self._last_tick_t = None
                self._game_evt.wait(1.0)
                next_t = self._clock()
                continue
            self.step(self._clock())
            period = self.detect_period(self._clock())
            next_t += period
            now = self._clock()
            if next_t < now - period:        # late (slow machine / suspended): skip, no burst
                next_t = now
            self._stop_evt.wait(max(0.0, next_t - now))

    # ================================================================== hotkeys / overlay
    def _hotkey_bindings(self) -> dict[str, Callable[[], None]]:
        cfg = self._cfg
        out: dict[str, Callable[[], None]] = {}
        for name, cb in ((cfg.hotkey_jungler, self.speak_jungler_status),
                         (cfg.hotkey_mute, self.toggle_mute),
                         (cfg.hotkey_overlay, self.toggle_overlay),
                         (getattr(cfg, "hotkey_ai", ""), self.ask_ai),
                         (getattr(cfg, "hotkey_ward", ""), self.request_ward_guide)):
            if isinstance(name, str) and name.strip():
                out[name.strip()] = cb
        return out

    def _start_hotkeys(self) -> None:
        if not self._enable_hotkeys:
            return
        try:
            from treeaicoach.hotkeys import HotkeyListener

            self._hotkeys = HotkeyListener(self._hotkey_bindings())
            self._hotkeys.start()
        except Exception:
            log.exception("Hotkeys unavailable")
            self._hotkeys = None

    def _start_diag_hotkey(self) -> None:
        """``cfg.hotkey_diag`` (default Ctrl+F8) -> :meth:`start_diagnostic`. Registered by the
        engine itself (also when the UI owns the other hotkeys), live capture only."""
        key = str(getattr(self._cfg, "hotkey_diag", "") or "").strip()
        if not key or self._frame_source is not None or sys.platform != "win32":
            return
        try:
            from treeaicoach.hotkeys import HotkeyListener

            self._diag_hotkeys = HotkeyListener({key: self.start_diagnostic})
            self._diag_hotkeys.start()
        except Exception:
            log.exception("Diagnostic hotkey unavailable")
            self._diag_hotkeys = None

    def _start_overlay(self) -> None:
        if not self._manage_overlay or sys.platform != "win32":
            return
        try:
            from treeaicoach.overlay import OverlayManager

            self._overlay_mgr = OverlayManager(self._cfg, self.get_overlay_state)
            self._overlay_mgr.start()
        except Exception:
            log.exception("Overlay unavailable: voice only")
            self._overlay_mgr = None

    @property
    def overlay_manager(self) -> Any:
        return self._overlay_mgr

    @property
    def hotkeys(self) -> Any:
        return self._hotkeys

    def _danger_beep(self, a: Alert) -> None:
        """The distinct danger tone, at once (voice.VoiceEngine.alert_beep): "recule" for the
        personal danger, "siege" for a gank in my base, "gank" otherwise. Never raises."""
        beep = getattr(self._voice, "alert_beep", None)
        if not callable(beep):
            return
        try:
            sdv = getattr(self._voice, "set_danger_voice", None)
            if callable(sdv) and getattr(self._voice, "danger_voice", None) != getattr(self._cfg, "danger_voice", None):
                sdv(getattr(self._cfg, "danger_voice", "bip_voix"))
            tone = "gank"
            if str(a.key).endswith(":siege"):
                tone = "siege"
            elif a.kind == AlertKind.PERSONAL_DANGER or str(a.key).startswith("call:retreat"):
                tone = "recule"
            elif a.kind in GANK_KINDS and self._tracker is not None:
                me = self._tracker.me()
                pos = me.position() if me is not None else None
                game = self._game
                if pos is not None and game is not None:
                    z = geometry.classify_zone(*pos)
                    if geometry.is_base(z) and geometry.zone_owner(z) == game.my_team:
                        tone = "siege"
            beep(tone)
        except Exception:
            self._err.exception("Danger beep failed")

    def _say(self, text: str, level: int, force: bool = False) -> None:
        if not text or (self._muted and not force):
            return
        try:
            self._voice.say(text, int(level))
        except Exception:
            self._err.exception("voice.say failed")

    def mute(self, on: bool) -> None:
        """Mute / unmute the voice with a spoken confirmation. Never raises."""
        try:
            on = bool(on)
            with self._lock:
                if on == self._muted:
                    return
                self._muted = on
                timer, self._mute_timer = self._mute_timer, None
            if timer is not None:
                timer.cancel()
            set_muted = getattr(self._voice, "set_muted", None)
            if on:
                self._say("Voix coupée.", 1, force=True)
                if callable(set_muted):
                    tm = threading.Timer(MUTE_DELAY_S, self._apply_mute)
                    tm.daemon = True
                    with self._lock:
                        self._mute_timer = tm
                    tm.start()
            else:
                if callable(set_muted):
                    set_muted(False)
                self._say("Voix activée.", 1, force=True)
            log.info("Voice %s", "muted" if on else "unmuted")
        except Exception:
            log.exception("CoachEngine.mute failed")

    def _apply_mute(self) -> None:
        try:
            if self._muted:
                self._voice.set_muted(True)
        except Exception:
            log.exception("voice.set_muted failed")

    def toggle_mute(self) -> None:
        self.mute(not self._muted)

    @property
    def muted(self) -> bool:
        return self._muted

    def toggle_overlay(self) -> None:
        """Show / hide the visual indicators (F11). Never raises."""
        with self._lock:
            self._overlay_visible = not self._overlay_visible
            self._overlay_cache = None
            on = self._overlay_visible
        self._say("Indicateurs visuels affichés." if on else "Indicateurs visuels masqués.", 0)

    @property
    def overlay_visible(self) -> bool:
        return self._overlay_visible

    # ================================================================== game lifecycle
    def _handle_game_info(self, game: GameInfo | None, t: float) -> None:
        """Lifecycle: new game / game over / death / roster. Called by the poller or step()."""
        try:
            with self._lock:
                self._ensure_components()
                if game is None:
                    self._game_absent(t)
                    return
                self._game_present(game, t)
        except Exception:
            self._errors += 1
            self._err.exception("Game lifecycle update failed")

    def _game_absent(self, t: float) -> None:
        if self._last_game_seen is None:
            return
        if t - self._last_game_seen <= GAME_GONE_S:
            return
        if self._in_game and not self._ended:
            log.info("Live Client gone for %.0f s: game over", t - self._last_game_seen)
            self._end_game(None, t)
        self._last_game_seen = None
        self._game = None
        self._ended = False
        self._last_gt = None
        self._set_idle()

    def _set_idle(self) -> None:
        self._in_game = False
        self._game_evt.clear()
        if self._running or self._frame_source is not None:
            self._state, self._message = EngineState.WAITING_GAME, MSG_WAITING

    def _game_present(self, game: GameInfo, t: float) -> None:
        gt = _finite(game.game_time) or 0.0
        new_game = self._last_game_seen is None or (
            self._last_gt is not None and gt < self._last_gt - GAME_TIME_BACK_S)
        if new_game and self._in_game and not self._ended:
            self._end_game(None, t)
        if new_game:
            self._start_game(game, t)
        self._last_game_seen = t
        self._last_gt = gt
        self._game = game
        self._game_t = t
        if self._ended:
            return                                    # post-GameEnd data of a finished game
        if game.game_result is not None:
            if self._recorder is not None:
                self._recorder.on_game_info(game, t)
            self._end_game(game.game_result, t)
            return
        supported = game.is_summoners_rift and game.me is not None
        if not supported:
            self._in_game = False
            self._game_evt.clear()
            self._state = EngineState.UNSUPPORTED_MODE
            self._message = MSG_SPECTATOR if game.me is None else unsupported_message(game)
            return
        if not self._in_game:
            self._in_game = True
            self._game_evt.set()
            self._relocate = True
            if self._state in (EngineState.STOPPED, EngineState.WAITING_GAME, EngineState.UNSUPPORTED_MODE):
                self._state, self._message = EngineState.LOCATING, MSG_LOCATING
        if self._recorder is not None:
            self._recorder.on_game_info(game, t)
        self._update_roster(game)
        self._check_death(game, t, gt)

    def _start_game(self, game: GameInfo, t: float) -> None:
        log.info("New game detected (game time %.0f s, mode %s)", game.game_time, game.game_mode)
        self._sb_recorded = None
        for comp in (self._tracker, self._gank, self._objectives, self._reminders, self._fog,
                     self._jungle_intel,
                     self._throttler, self._coach, self._scoreboard, self._praise, self._toasts,
                     self._stance, self._tip_rotator, self._gate, self._tactics, self._gauge,
                     getattr(self, "_ai", None), self._ward_guide, getattr(self, "_plays", None),
                     getattr(self, "_coach_plus", None), getattr(self, "_item_adv", None),
                     getattr(self, "_hype", None), getattr(self, "_danger", None)):
            fn = getattr(comp, "reset", None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    log.exception("reset failed for %r", type(comp).__name__)
        self._ended = False
        self._next_heavy_t = -math.inf
        self._heavy.reset()
        self._motion = None
        self._capture_status, self._capture_note = "ok", None
        self._minimap_score = None
        self._fullscreen_check(game)
        self._tip_text = None
        self._text_msg = None
        self.text_messages = []
        if getattr(self, "_presenter", None) is not None:
            self._presenter.reset()
        # per-game advice state that lives on the engine (V2 audit: stale across games otherwise)
        self.macro_calls = []
        self.recent_plays = []
        self._topic_t = {}
        self._hud_shown = None
        self._recall_topic_t = None
        self._tip_toast_t = -math.inf
        self.last_ai_advice = None
        self._roster_sig = None
        self._prefetched = False
        self._was_dead = bool(game.me.is_dead) if game.me is not None else False
        self._death_due = None
        self._threat_hist.clear()
        self._last_danger_t = None
        self._last_alert = None
        self._last_alert_t = None
        self._recent.clear()
        self._overlay_cache = None
        self._icons = {}
        self._relocate = True
        self._bad_since = None
        self._banner = None
        self._recorder = None
        if not self._demo and game.me is not None:
            try:
                if self._recorder_factory is not None:
                    self._recorder = self._recorder_factory()
                else:
                    from treeaicoach.recorder import GameRecorder

                    self._recorder = GameRecorder()
            except Exception:
                log.exception("Game recorder unavailable")
                self._recorder = None
            note = getattr(self._recorder, "note_settings", None)
            if callable(note):
                note(self._cfg)

    def _update_roster(self, game: GameInfo) -> None:
        try:
            sig = tuple((p.champion_alias, p.skin_id, p.team) for p in game.all_players()) + (
                game.me.champion_alias if game.me else "",)
        except Exception:
            sig = None
        if sig == self._roster_sig:
            return
        self._roster_sig = sig
        try:
            self._identifier.set_roster(game)
        except Exception:
            self._err.exception("identifier.set_roster failed")
        try:
            if self._detector is not None and hasattr(self._detector, "set_roster"):
                self._detector.set_roster(game)
        except Exception:
            self._err.exception("detector.set_roster failed")
        try:  # natural voice: pre-generate the sentences of this game (voice.VoiceEngine.prewarm)
            prewarm = getattr(self._voice, "prewarm", None)
            if callable(prewarm):
                from treeaicoach.tts_neural import build_phrase_list
                prewarm(build_phrase_list(game))
        except Exception:
            log.debug("voice.prewarm failed", exc_info=True)
        if not self._prefetched and self._cfg.download_skin_icons and not self._demo:
            self._prefetched = True
            db = self._champion_db()
            if db is not None:
                try:
                    db.prefetch_skin_icons(game.all_players())
                except Exception:
                    log.exception("Skin icon prefetch failed")

    def _check_death(self, game: GameInfo, t: float, gt: float) -> None:
        me = game.me
        dead = bool(me.is_dead) if me is not None else False
        if dead and not self._was_dead and self._cfg.death_recap:
            event: Any = gt
            names = {n for n in (me.riot_id, me.summoner_name, me.riot_id.split("#")[0]) if n}
            for ev in reversed(game.events):
                if isinstance(ev, dict) and ev.get("EventName") == "ChampionKill" \
                        and ev.get("VictimName") in names:
                    event = ev
                    break
            self._death_due = (t + DEATH_RECAP_DELAY_S, event)
        if not dead:
            self._death_due = None
        self._was_dead = dead

    # ================================================================== tick
    def step(self, t: float) -> list[Alert]:
        """One synchronous iteration at engine time ``t``; returns the alerts spoken. Never raises."""
        with self._step_lock:
            t0 = time.perf_counter()
            try:
                out = self._step(float(t))
                self._consecutive_errors = 0
            except Exception:
                out = []
                self._errors += 1
                self._consecutive_errors += 1
                self._err.exception("Engine tick failed")
                if self._consecutive_errors >= CONSECUTIVE_ERRORS_STATE:
                    with self._lock:
                        self._state, self._message = EngineState.ERROR, MSG_ERROR
            dt = time.perf_counter() - t0
            self._tick_ms = 0.8 * self._tick_ms + 0.2 * dt * 1000.0 if self._tick_ms else dt * 1000.0
            try:
                self._observe_tick_cost(float(t), dt * 1000.0)
            except Exception:
                pass
            return out

    def _step(self, t: float) -> list[Alert]:
        self._ensure_components()
        frame: np.ndarray | None = None
        if self._frame_source is not None:
            try:
                raw, game = self._frame_source.next(t)
            except Exception:
                self._errors += 1
                self._err.exception("FrameSource.next failed")
                raw, game = None, None
            self._handle_game_info(game if isinstance(game, GameInfo) else None, t)
            frame = _as_bgr(raw)
        elif not self._threads or not any(th.name.endswith("poller") for th in self._threads):
            if t >= self._next_poll:           # synchronous use (tests): poll from here
                self._poll_once(t)
                self._next_poll = t + (POLL_IN_GAME_S if self._in_game else POLL_IDLE_S)
        with self._lock:
            in_game, game = self._in_game, self._game
            game_t = self._game_t
        if not in_game or game is None:
            self._last_tick_t = None
            return []
        self._update_fps(t)
        self._apply_perf_profile()
        self._serve_diag_requests(t)
        gt = (_finite(game.game_time) or 0.0) + min(max(0.0, t - game_t), 3.0)
        stagger = self.stagger if self.stagger is not None else bool(self._running and self._threads)
        verify_due = (self._frame_source is None and self._locate_method == "auto" and self._bad_since is None
                      and t >= self._next_verify)
        self._heavy_now = self._heavy.plan(t, stagger, busy=verify_due)
        if self._frame_source is None:
            t_grab = time.perf_counter()
            frame = self._grab_minimap(t, gt)
            if frame is not None:
                self._stats["grab"].add((time.perf_counter() - t_grab) * 1000.0)
            elif self._occluded or self._paused:
                # minimap covered / game minimized: frozen tick (no "not seen" for the tracker, no
                # alert from stale data); resumes with the first visible frame
                self._last_tick_t = None
                return []
        elif frame is None:
            self._set_state(EngineState.RUNNING, MSG_NO_FRAME)
        else:
            self._set_state(EngineState.RUNNING, MSG_RUNNING_DEMO if self._demo else MSG_RUNNING)
        identified: list[Any] = []
        if frame is not None:
            self._cap_rate.tick(t)
            if is_black_frame(frame):
                self._set_state(EngineState.CAPTURE_BLACK, MSG_BLACK)
                frame = None
            else:
                t_vis = time.perf_counter()
                identified = self._stabilize(t, self._vision(frame))
                self._stats["vision"].add((time.perf_counter() - t_vis) * 1000.0)
                self._last_frame_t = t
            self._self_icon_tick(t, gt, game)
        tracker = self._tracker
        self._track_update(t, identified)
        raw_alerts: list[Alert] = []
        gank_alerts: list[Alert] = []
        if self._gank is not None:
            try:
                gank_alerts = list(self._gank.update(t, tracker, game) or [])
            except Exception:
                self._errors += 1
                self._err.exception("GankAnalyzer.update failed")
        threat = self._update_threat(t, gank_alerts)
        danger_now = self._personal_danger(t, gt, game, tracker, threat)
        pd = getattr(self, "_danger", None)
        if pd is not None:          # 2 v 1 at low HP: gauge SAFE, no "go" advice (not a gank: no flash)
            threat = max(threat, int(getattr(pd.state(), "level", 0) or 0))
        # v3 director: fight decision + speech context every tick, macro / positioning / wards at the heavy rate
        tac_alerts, gank_now = self._tactics_tick(t, gt, game, tracker, gank_alerts, threat=threat)
        self._ward_guide_tick(t, game, tracker, frame, identified)
        # latency first: a gank alert (or the fight call) is spoken NOW, before the heavier stages
        said_now = self._say_gank_now(gank_now + danger_now + self._siege_alert(t), t, gt, frame)
        raw_alerts += [a for a in gank_alerts if a.kind not in GANK_KINDS] + tac_alerts
        # coaching stages (coach, Tab, tips, items, hype / AI) at the budget's heavy rate, the gank
        # check every tick; threaded: one slot per tick (staggered, no spike), see scheduler.py
        slots = self._heavy_now
        t_coach = time.perf_counter()
        if self._objectives is not None:
            raw_alerts += list(self._objectives.update(game, t) or [])
        me = tracker.me()
        me_pos = me.position() if me is not None else None
        if self._reminders is not None and threat < Level.WARNING:
            in_base = False
            if me_pos is not None:
                z = geometry.classify_zone(*me_pos)
                in_base = geometry.is_base(z) and geometry.zone_owner(z) == game.my_team
            raw_alerts += self._recall_consistency(list(self._reminders.update(t, game, me_pos, in_base) or []), t)
        if self._coach is not None and "coach" in slots:
            raw_alerts += list(self._coach.update(
                t, tracker, game, self._role_resolver,
                self._objectives.states() if self._objectives is not None else [],
                me_pos, threat=threat, minimap_bgr=frame) or [])
        if "board" in slots:
            raw_alerts += self._scoreboard_and_praise(t, tracker, game, threat, gank_alerts, gt)
        if "tips" in slots:
            raw_alerts += self._stance_and_tips(t, game, threat)
        if threat < Level.WARNING and "tips" in slots:
            raw_alerts += self._item_advice(t, game, me_pos, gt)
        if "board" in slots:      # (restyles the praise of the board slot: same tick)
            raw_alerts = self._hype_and_ai(t, game, gt, threat, me_pos, raw_alerts)
        self._plays_tick(t, gt, game, threat, me_pos)    # play ratings (plays.py) -> badge animation
        if self._jungle_intel is not None:   # Tab data: purchases / CS -> fog anchors
            self._jungle_intel.update(t, game, tracker, self._fog)
        if self._fog is not None and not getattr(self._cfg, "safe_mode", False):
            self._fog.update(t, tracker, game, mode=self._cfg.fog_mode)
        rec = self._recorder
        if rec is not None:
            rec.on_tracks(tracker, t, gt)
            on_fog = getattr(rec, "on_fog", None)
            if callable(on_fog) and self._fog is not None and not getattr(self._cfg, "safe_mode", False):
                on_fog(self._fog.estimates(), gt)
        raw_alerts += self._death_recap_alerts(t)
        self._stats["coach"].add((time.perf_counter() - t_coach) * 1000.0)
        if threat >= Level.WARNING:     # gank first: no macro tip / praise / Tab insight now
            raw_alerts = [a for a in raw_alerts if a.kind not in COACH_KINDS]
        if self._tactics is not None:   # fight: nothing but the call (praise held for after)
            raw_alerts = self._tactics.hold_if_fighting(raw_alerts, t)
            raw_alerts = self._tactics.drop_overlaps(raw_alerts, t)   # a macro call already said it
        raw_alerts = self._route_messages(raw_alerts, t, gt)
        # one message per tick: nothing else when a gank alert was just said
        said = [] if said_now else self._throttler.filter(raw_alerts, t)
        if threat >= Level.WARNING:     # (a held-back coaching alert released by the throttler)
            said = [a for a in said if a.kind not in COACH_KINDS]
        said = self._speech_budget(said, t, bool(said_now))
        self._speak_alerts(said, t, gt)
        said = said_now + said
        with self._lock:
            self._frame = frame
            self._identified = identified
            self._frame_id += 1
        self._after_tick(t, tracker, me_pos, threat)
        if frame is not None and self._cfg.collect_samples:
            self._collect(frame, t)
        return said

    # ================================================================== scheduling / health (v2)
    def _after_tick(self, t: float, tracker: Any, me_pos: Any, threat: int) -> None:
        """Motion snapshot for the overlay (render-time prediction), burst decision. Never raises."""
        try:
            tracks = tracker.tracks() if tracker is not None else []
            self._motion = MotionSnapshot.from_tracks(t, tracks)
            if self._adaptive:
                tac = self._tactics
                why = burst_reason(t, tracks, me_pos, self._cfg.effective_warn_radius(), threat=int(threat),
                                   fighting=bool(tac is not None and tac.in_fight()))
                if why is not None:
                    self._governor.trigger(t, why)
        except Exception:
            self._err.exception("Post-tick bookkeeping failed")

    def detect_period(self, t: float) -> float:
        """Seconds until the next analysis tick: adaptive (calm / burst / unfocused / paused)
        or the fixed ``cfg.target_fps`` when ``cfg.adaptive_rate`` is off."""
        if self._paused:
            return 1.0
        if not self._adaptive:
            return 1.0 / max(1.0, min(30.0, float(self._cfg.target_fps)))
        unfocused = self._unfocused_since is not None and t - self._unfocused_since >= 3.0
        return self._governor.period(t, None, unfocused)

    def _observe_tick_cost(self, t: float, ms: float) -> None:
        self._stats["tick"].add(ms)
        if self._in_game and self._last_frame_t is not None and not self._paused:
            if self._budget.observe_tick(t, ms):
                self._applied_profile = None      # switched to low-end: push the knobs next tick

    def scoreboard_summary(self) -> Any:
        """Latest :class:`scoreboard.ScoreboardSummary` (None before the first game poll)."""
        sb = self._scoreboard
        return sb.summary() if sb is not None else None

    def _set_state(self, state: EngineState, message: str) -> None:
        with self._lock:
            self._state, self._message = state, message

    def _update_fps(self, t: float) -> None:
        last = self._last_tick_t
        self._last_tick_t = t
        if last is not None and t > last:
            inst = 1.0 / (t - last)
            self._fps = inst if self._fps <= 0 else 0.85 * self._fps + 0.15 * inst

    def motion(self) -> MotionSnapshot | None:
        """Latest per-tick track snapshot (render-time prediction, see scheduler.py)."""
        return self._motion

    def predict_positions(self, now: float | None = None) -> dict[str, tuple[tuple[float, float], float]]:
        """``{track key: ((u, v) extrapolated to now, age of the data in s)}`` for the overlay."""
        m = self._motion
        if m is None:
            return {}
        return m.predict(self._clock() if now is None else float(now))

    def health(self) -> dict[str, Any]:
        """Lightweight live health monitor (the UI's "Système" panel, diagnostic bundles).

        Keys: ``capture_backend``, ``capture_fps``, ``grab_ms`` / ``detect_ms`` / ``tick_ms`` /
        ``coach_ms`` ({p50, p95, ...}), ``overlay`` (fps, per-layer render ms, UpdateLayeredWindow
        ms), ``champions_seen`` / ``champions_expected``, ``minimap_score``, ``locate_method``,
        ``detect_rate`` (current target img/s + why), ``budget``, ``cpu_percent`` (of one core),
        ``capture_status``, ``capture_note``, ``paused``, ``latency_ms``. Never raises."""
        out: dict[str, Any] = {}
        try:
            now = self._clock()
            if now >= self._stats_next:
                self._stats_next = now + STATS_EVERY_S
                self._cpu.sample()
            cap = self._capture
            timings = cap.timings() if cap is not None and hasattr(cap, "timings") else {}
            out["capture_backend"] = timings.get("backend") or (type(cap).__name__ if cap is not None else None)
            out["capture"] = timings
            out["capture_fps"] = round(self._cap_rate.rate(now), 2)
            for k in ("grab", "vision", "tick", "coach"):
                out[{"vision": "detect_ms"}.get(k, f"{k}_ms")] = self._stats[k].summary()
            seen = expected = 0
            tr = self._tracker
            game = self._game
            if tr is not None and self._in_game:
                seen = sum(1 for x in tr.tracks() if x.visible)
            if game is not None:
                try:
                    expected = sum(1 for p in game.all_players() if not p.is_dead)
                except Exception:
                    expected = 0
            out["champions_seen"], out["champions_expected"] = seen, expected
            out["minimap_score"] = None if self._minimap_score is None else round(self._minimap_score, 3)
            out["locate_method"] = self._locate_method
            out["minimap_rect"] = self._minimap_rect.to_dict() if self._minimap_rect is not None else None
            fps = self._governor.fps(now, self._paused, self._unfocused_since is not None)
            out["detect_rate"] = {"target_fps": round(fps, 1), "measured_fps": round(self._fps, 1),
                                  "burst": self._governor.bursting(now), "why": self._governor.reason,
                                  "adaptive": self._adaptive}
            out["budget"] = self._budget.describe()
            out["heavy_runs"] = dict(self._heavy.runs)
            out["cpu_percent"] = self._cpu.percent
            out["capture_status"] = self._capture_status
            out["capture_note"] = self._capture_note
            out["paused"] = self._paused or ("unfocused" if self.overlay_paused(now) else None)
            info = self._win_info
            if info is not None:
                out["window"] = {"rect": info.rect.to_dict() if info.rect else None, "dpi": info.dpi,
                                 "scale_pct": round(info.dpi / 96.0 * 100), "foreground": info.foreground}
            if self._last_frame_t is not None:
                out["frame_age_ms"] = round(max(0.0, now - self._last_frame_t) * 1000.0, 1)
            try:
                from treeaicoach import overlay as _ov  # stats published by the overlay thread

                out["overlay"] = _ov.current_stats()
            except Exception:
                out["overlay"] = None
            d = self._diag
            out["diagnostic"] = d.status() if d is not None else None
        except Exception:
            log.debug("health() failed", exc_info=True)
        return out

    # ---------------------------------------------------------------- diagnostic bundle
    def start_diagnostic(self, duration_s: float | None = None, interval_s: float | None = None) -> Any:
        """Record a diagnostic bundle (minimap crops, detections / tracks, capture + timings,
        settings...) every ``interval_s`` for ``duration_s`` (config ``diag_*``), then zip it
        into ``<user data>/diagnostics`` and open the folder. Returns the bundle folder (Path)
        or None when one is already recording / not possible. Safe from any thread (UI button,
        hotkey). Never raises."""
        try:
            from treeaicoach.diag import DiagRecorder

            with self._lock:
                if self._diag is not None and self._diag.running:
                    log.info("Diagnostic already recording")
                    return None
                dur = float(duration_s if duration_s is not None else getattr(self._cfg, "diag_duration_s", 60.0))
                itv = float(interval_s if interval_s is not None else getattr(self._cfg, "diag_interval_s", 2.0))
                self._diag = DiagRecorder(self, duration_s=dur, interval_s=itv)
            path = self._diag.start()
            try:
                self._say("Diagnostic en cours : joue normalement pendant une minute.", int(Level.INFO),
                          force=True)
            except Exception:
                pass
            return path
        except Exception:
            log.exception("Cannot start the diagnostic recorder")
            return None

    def diagnostic_status(self) -> dict[str, Any] | None:
        """``{"running", "progress", "zip", "folder", "error"}`` of the last bundle, or None."""
        d = self._diag
        return d.status() if d is not None else None

    def request_diag_snapshot(self, full_screen: bool = False) -> None:
        """Ask the analysis thread to keep a copy of the next frame (+ the full game window
        once when ``full_screen``) for the diagnostic recorder (served in :meth:`_step`)."""
        with self._lock:
            self._diag_req["snapshot"] = True
            if full_screen:
                self._diag_req["screen"] = True

    def _serve_diag_requests(self, t: float) -> None:
        """Analysis thread: full-window thumbnail for the diagnostic (the capture objects are
        owned by this thread)."""
        if not self._diag_req.get("screen"):
            return
        self._diag_req.pop("screen", None)
        try:
            win = self._window
            img = None
            if win is not None and self._frame_source is None:
                img = _as_bgr(self._grabber().grab(win))
            elif self._frame is not None:
                img = self._frame.copy()
            d = self._diag
            if d is not None and img is not None:
                d.put_screen(img)
        except Exception:
            self._err.exception("Diagnostic screen grab failed")

    def diag_snapshot(self) -> dict[str, Any]:
        """Everything the diagnostic recorder saves for one sample (thread-safe copies). The
        Live Client data is reduced to champion names / teams / positions / summoner spells:
        no summoner name, no Riot ID."""
        now = self._clock()
        with self._lock:
            frame = None if self._frame is None else self._frame.copy()
            identified = list(self._identified)
            game = self._game
            st_state, st_msg = self._state, self._message
        snap: dict[str, Any] = {"t": now, "state": getattr(st_state, "value", str(st_state)), "message": st_msg,
                                "frame": frame}
        try:
            snap["preview"] = self.get_preview()
        except Exception:
            snap["preview"] = None
        dets = []
        for x in identified:
            det = getattr(x, "det", x)
            dets.append({k: (round(float(v), 4) if isinstance(v, float) else v) for k, v in (
                ("u", getattr(det, "u", None)), ("v", getattr(det, "v", None)), ("r", getattr(det, "r", None)),
                ("score", getattr(det, "score", None)), ("cls", getattr(det, "cls", None)),
                ("det_alias", getattr(det, "alias", None)), ("alias", getattr(x, "alias", None)),
                ("relation", getattr(x, "relation", None)), ("id_score", getattr(x, "id_score", None)))})
        snap["detections"] = dets
        tracks = []
        tr = self._tracker
        for k in (tr.tracks() if tr is not None else []):
            pos = k.position()
            kf = k.kf_position() if hasattr(k, "kf_position") else None
            tracks.append({"key": k.key, "alias": k.alias, "relation": k.relation, "visible": k.visible,
                           "pos": None if pos is None else [round(pos[0], 4), round(pos[1], 4)],
                           "kf": None if kf is None else [round(kf[0], 4), round(kf[1], 4)],
                           "age_s": round(now - k.last_seen, 3), "score": round(k.score, 3),
                           "id_score": round(k.id_score, 3), "stacked_with": k.stacked_with})
        snap["tracks"] = tracks
        try:
            m = getattr(self._detector, "matcher", None)
            snap["detector"] = {
                "name": str(getattr(self._detector, "name", "")),
                "matcher_mode": getattr(m, "last_mode", None), "matcher_ms": getattr(m, "last_time_ms", None),
                "matcher_changed": getattr(m, "last_changed", None),
                "matcher_scale": getattr(m, "scale", None), "grey": getattr(m, "grey", None),
                "matches": [{k2: (round(v2, 4) if isinstance(v2, float) else v2)
                             for k2, v2 in vars(mi).items() if not k2.startswith("_")
                             and isinstance(v2, (int, float, str, bool, type(None)))}
                            for mi in list(getattr(m, "last_matches", None) or [])[:12]],
                "dead": list(getattr(m, "last_dead", None) or []),
            }
        except Exception:
            snap["detector"] = {"name": str(getattr(self._detector, "name", ""))}
        if game is not None:
            def player(p: Any) -> dict[str, Any]:
                return {"champion": p.champion_alias, "name": p.champion_name, "team": p.team,
                        "position": p.position, "level": p.level, "dead": p.is_dead, "skin": p.skin_id,
                        "smite": p.has_smite,
                        "spells": [str(s) for s in (getattr(p, "summoner_spells", None) or [])][:2]}
            snap["game"] = {"game_time": game.game_time, "mode": game.game_mode, "map": game.map_number,
                            "my_team": game.my_team, "me": player(game.me) if game.me else None,
                            "players": [player(p) for p in game.all_players()]}
        snap["health"] = self.health()
        return snap

    def request_relocate(self) -> None:
        """Locate the minimap again at the next tick."""
        with self._lock:
            self._relocate = True

    # ================================================================== queries
    def get_status(self) -> EngineStatus:
        """Immutable status snapshot. Never raises."""
        try:
            health = self.health() if self._in_game else None
            with self._lock:
                game = self._game
                gt = None
                if game is not None and self._in_game:
                    gt = (_finite(game.game_time) or 0.0) + min(max(0.0, self._clock() - self._game_t), 3.0)
                visible = 0
                if self._tracker is not None and self._in_game:
                    visible = len(self._tracker.enemies(visible_only=True))
                s = self._session
                return EngineStatus(
                    state=self._state, message=self._message,
                    fps=round(self._fps, 1) if self._in_game else 0.0, game_time=gt,
                    minimap_rect=self._minimap_rect if self._frame_source is None else None,
                    enemies_visible=visible,
                    last_alert=self._last_alert.text if self._last_alert is not None else None,
                    detector=str(getattr(self._detector, "name", "") or self._cfg.detector_backend),
                    voice=str(getattr(self._voice, "backend", "") or type(self._voice).__name__),
                    muted=self._muted, overlay_visible=self._overlay_visible, errors=self._errors,
                    tick_ms=round(self._tick_ms, 1), demo=self._demo,
                    banner=self._banner or (self._capture_note if self._in_game else None),
                    locate_method=self._locate_method,
                    session=(s["games"], s["wins"], s["losses"]),
                    health=health)
        except Exception:
            log.exception("get_status failed")
            return EngineStatus(EngineState.ERROR, MSG_ERROR, 0.0, None, None, 0, None, "", "")

    def session_stats(self) -> dict[str, int]:
        with self._lock:
            return dict(self._session)

    def recent_alerts(self, n: int = 20) -> list[tuple[float | None, str, int, str]]:
        """Last spoken alerts: (game time, text, level, kind), oldest first."""
        with self._lock:
            return list(self._recent)[-max(0, int(n)):]

    # ---------------------------------------------------------------- misc (tests / UI)
    @property
    def tracker(self) -> Any:
        return self._tracker

    @property
    def fog_tracker(self) -> Any:
        return self._fog

    @property
    def _role_resolver(self) -> Any:
        """Role of every player (roles.RoleResolver owned by the gank analyser), or None."""
        return getattr(self._gank, "role_resolver", None)

    @property
    def detector(self) -> Any:
        return self._detector

    @property
    def in_game(self) -> bool:
        return self._in_game

    def current_threat(self) -> int:
        """Max raw gank alert level over the last 2 s (0 safe, 1 warning, 2 danger)."""
        now = self._clock()
        with self._lock:
            return max((lvl for t_, lvl, _a in self._threat_hist if now - t_ <= THREAT_HOLD_S), default=0)


__all__ = ["CoachEngine", "EngineState", "EngineStatus", "FrameSource", "find_camera_center", "seconds_fr",
           "ACE_TEXT", "ACE_TEXT_FAR", "MSG_MINIMAP_COVERED", "MSG_OCCLUDED", "SIEGE_TEXT", "siege_state",
           "structure_owner"]
