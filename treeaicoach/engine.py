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

import dataclasses
import logging
import math
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol

import cv2
import numpy as np

from treeaicoach import geometry
from treeaicoach.alerts import Alert, AlertKind, AlertThrottler, Level, make_alert
from treeaicoach.capture import Rect, is_black_frame
from treeaicoach.config import Config
from treeaicoach.live_client import GameInfo, PlayerInfo

log = logging.getLogger(__name__)

# ------------------------------------------------------------------------------ tunables
POLL_IN_GAME_S = 1.0             # Live Client poll period in game
POLL_IDLE_S = 2.0                # ... and outside a game (0.5 Hz)
GAME_GONE_S = 8.0                # API silent this long after a game -> game over
GAME_TIME_BACK_S = 3.0           # game_time going back more than this -> new game
WINDOW_REFRESH_S = 2.0           # game window rectangle cache
VERIFY_PERIOD_S = 1.0            # minimap verify() period
VERIFY_BAD_S = 3.0               # verify() below threshold this long -> relocate
LOCATE_RETRY_S = 10.0            # retry the auto location this often while on the fallback rect
THREAT_HOLD_S = 2.0              # overlay threat = max raw gank level over this window
DEATH_RECAP_DELAY_S = 2.0        # the death recap is spoken this long after my death
OVERLAY_MIN_PERIOD_S = 1.0 / 12  # get_overlay_state() rebuilt at most at 12 Hz
FLASH_DECAY_S = 2.0              # danger flash fades out over this duration
HOTKEY_DEBOUNCE_S = 0.8          # repeated F9 presses closer than this are ignored
MUTE_DELAY_S = 1.6               # "Voix coupée" is spoken, then the voice is muted
ERROR_LOG_EVERY_S = 30.0         # full traceback of a failing tick at most this often
CONSECUTIVE_ERRORS_STATE = 5     # that many failing ticks in a row -> ERROR state
BACKOFF_MAX_S = 10.0
CAMERA_SELF_MAX_DIST = 0.12      # camera-centre fallback for "self": ally icon within this
STICKY_SELF_S = 0.6              # my icon misread: my track seen this recently...
STICKY_SELF_DIST = 0.04          # ... and an unidentified icon this close to it -> it is me
JUMP_CHECK_S = 1.0               # an identity seen this recently cannot jump farther than
JUMP_SPEED = 0.08                # ... JUMP_SPEED * dt + JUMP_SLACK (walk + Flash + detector noise)
JUMP_SLACK = 0.06
IDENTITY_SWAP_HIDDEN_S = 1.5     # an enemy hidden this long popping up on another enemy's spot
DUP_DIST = 0.055                 # enemy detection this close to an established enemy icon...
DUP_KEEP_ID_SCORE = 0.8          # ... and not confidently identified -> duplicate, dropped
IDENTITY_SWAP_RECENT_S = 3.0     # ... of another enemy seen this recently ...
RELABEL_RECENT_S = 1.5           # unidentified "ally" icon on the spot of an enemy seen this
RELABEL_DIST = 0.03              # recently (this close) and far from every friend -> that enemy
IDENTITY_SWAP_DIST = 0.05        # (this close) is that other enemy misidentified
COLLECT_MAX_FILES = 2000
RECENT_ALERTS_MAX = 50
BREAK_LOSS_STREAK = 3
BREAK_TEXT = "3 défaites d'affilée : une pause de 10 minutes aide à rester concentré."

GANK_KINDS = frozenset({AlertKind.JUNGLER_APPROACH, AlertKind.ROAM_APPROACH, AlertKind.COLLAPSE})

MSG_STOPPED = "Analyse arrêtée."
MSG_WAITING = "En attente d'une partie de League of Legends…"
MSG_LOCATING = "Recherche de la minimap…"
MSG_NO_WINDOW = "Fenêtre du jeu introuvable (jeu réduit ?)."
MSG_RUNNING = "Analyse de la minimap en cours."
MSG_RUNNING_DEMO = "Mode démo : partie simulée."
MSG_FALLBACK = ("Minimap non trouvée automatiquement : position par défaut utilisée "
                "(calibre-la dans Réglages si les alertes sont fausses).")
MSG_BLACK = ("Capture noire : passe LoL en mode Sans bordure "
             "(Paramètres > Vidéo > Mode fenêtre : Sans bordure).")
MSG_UNSUPPORTED = "Mode de jeu non pris en charge : uniquement la Faille de l'invocateur."
MSG_SPECTATOR = "Mode spectateur : aucune analyse."
MSG_ERROR = "Erreur d'analyse répétée (voir les journaux) : l'analyse continue."
MSG_NO_FRAME = "Image de la minimap indisponible."


class EngineState(str, Enum):
    """High-level state shown by the UI."""

    STOPPED = "stopped"
    WAITING_GAME = "waiting_game"
    LOCATING = "locating"
    RUNNING = "running"
    UNSUPPORTED_MODE = "unsupported_mode"
    CAPTURE_BLACK = "capture_black"
    ERROR = "error"


@dataclass(frozen=True)
class EngineStatus:
    """Immutable status snapshot (French ``message``)."""

    state: EngineState
    message: str
    fps: float
    game_time: float | None
    minimap_rect: Rect | None
    enemies_visible: int
    last_alert: str | None
    detector: str
    voice: str
    # extras (defaults keep the documented positional signature working)
    muted: bool = False
    overlay_visible: bool = True
    errors: int = 0
    tick_ms: float = 0.0
    demo: bool = False
    banner: str | None = None
    locate_method: str | None = None
    session: tuple[int, int, int] = (0, 0, 0)     # (games, wins, losses)


class FrameSource(Protocol):
    """Replaces the capture + Live Client (demo, tests)."""

    def next(self, t: float) -> tuple[np.ndarray | None, GameInfo | None]: ...


# ------------------------------------------------------------------------------ helpers
def _finite(x: Any) -> float | None:
    try:
        f = float(x)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


def _as_bgr(img: Any) -> np.ndarray | None:
    """Validate / convert a frame to BGR uint8 (None if unusable)."""
    if not isinstance(img, np.ndarray) or img.ndim not in (2, 3) or img.size == 0:
        return None
    if min(img.shape[:2]) < 16 or max(img.shape[:2]) > 4096:
        return None
    try:
        if img.dtype != np.uint8:
            if not (np.issubdtype(img.dtype, np.integer) or np.issubdtype(img.dtype, np.floating)):
                return None
            img = np.clip(np.nan_to_num(img.astype(np.float32)), 0, 255).astype(np.uint8)
        if img.ndim == 2:
            return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        c = img.shape[2]
        if c == 1:
            return cv2.cvtColor(img[:, :, 0], cv2.COLOR_GRAY2BGR)
        if c == 4:
            return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        if c == 3:
            return np.ascontiguousarray(img)
    except Exception:
        return None
    return None


def seconds_fr(n: int) -> str:
    """``"une seconde"`` / ``"23 secondes"`` / ``"une minute"`` / ``"1 minute 30"``."""
    n = max(0, int(n))
    if n < 60:
        return "une seconde" if n <= 1 else f"{n} secondes"
    m, s = divmod(n, 60)
    head = "une minute" if m == 1 else f"{m} minutes"
    return head if s == 0 else f"{m} minute{'s' if m > 1 else ''} {s}"


def find_camera_center(minimap_bgr: np.ndarray) -> tuple[float, float] | None:
    """Centre ``(u, v)`` of the white camera rectangle of a minimap, or None. Cheap (< 1 ms)."""
    try:
        h, w = minimap_bgr.shape[:2]
        mask = (minimap_bgr.min(axis=2) >= 225).astype(np.uint8)
        if int(mask.sum()) < 0.3 * w:
            return None
        mask = cv2.dilate(mask, np.ones((3, 3), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best: tuple[float, float, float] | None = None
        for c in contours:
            x, y, cw, ch = cv2.boundingRect(c)
            fw, fh = cw / w, ch / w
            if not (0.17 <= fw <= 0.40 and 0.08 <= fh <= 0.26):
                continue
            err = abs(fw - 0.275) + abs(fh - 0.155)
            if best is None or err < best[0]:
                best = (err, (x + cw / 2.0) / w, (y + ch / 2.0) / h)
        return (best[1], best[2]) if best is not None else None
    except Exception:
        return None


class _PassThroughIdentifier:
    """Stand-in when ``identifier.py`` is unavailable: relation from the detector class."""

    @dataclass
    class _Item:
        det: Any
        alias: str | None
        relation: str
        team: str | None
        id_score: float

    def set_roster(self, game: Any) -> None:
        return None

    def identify(self, minimap_bgr: np.ndarray, detections: list[Any]) -> list[Any]:
        out = []
        for d in detections or ():
            rel = getattr(d, "cls", "enemy")
            out.append(self._Item(d, None, "ally" if rel == "self" else rel, None, 0.0))
        return out


class _Throttle:
    """Rate-limited error logging for the loops."""

    def __init__(self) -> None:
        self.last = -math.inf

    def exception(self, msg: str, *args: Any) -> None:
        now = time.monotonic()
        if now - self.last >= ERROR_LOG_EVERY_S:
            self.last = now
            log.exception(msg, *args)
        else:
            log.debug(msg, *args)


def _default_opener(path: Path) -> None:
    """Open the report in the web browser (never while running the test-suite)."""
    if "pytest" in sys.modules:
        log.info("Report not opened (tests): %s", path)
        return
    import webbrowser

    webbrowser.open(Path(path).resolve().as_uri())


# ------------------------------------------------------------------------------ engine
class CoachEngine:
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
        self._fog: Any = None
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
                if (old.minimap_mode, old.minimap_side, old.manual_minimap_rect) != \
                        (new.minimap_mode, new.minimap_side, new.manual_minimap_rect):
                    self._relocate = True
            for comp in (self._gank, self._objectives, self._reminders, self._fog, self._overlay_mgr):
                fn = getattr(comp, "apply_config", None)
                if callable(fn):
                    try:
                        fn(new)
                    except Exception:
                        log.exception("apply_config failed for %r", type(comp).__name__)
            set_params = getattr(self._voice, "set_params", None)
            if callable(set_params):
                try:
                    set_params(voice_name=new.voice_name, rate=new.voice_rate,
                               volume=new.voice_volume, beep_on_danger=new.beep_on_danger)
                except Exception:
                    log.exception("voice.set_params failed")
            hk = (old.hotkey_jungler, old.hotkey_mute, old.hotkey_overlay)
            if hk != (new.hotkey_jungler, new.hotkey_mute, new.hotkey_overlay) and self._hotkeys is not None:
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
            from treeaicoach.fog_tracker import FogTracker

            self._fog = FogTracker(max_s=cfg.fog_max_s)
        except Exception:
            log.exception("Fog tracker unavailable")
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
            return
        from treeaicoach.detector import create_detector

        old = self._detector
        self._detector = create_detector(cfg.detector_backend, cfg.detection_threshold)
        self._detector_key = key
        if old is not None:
            try:
                old.close()
            except Exception:
                pass

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
            for comp in (self._hotkeys, self._overlay_mgr):
                if comp is not None:
                    try:
                        comp.stop()
                    except Exception:
                        log.exception("stop failed for %r", type(comp).__name__)
            self._hotkeys = None
            self._overlay_mgr = None
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
            period = 1.0 / max(1.0, min(30.0, float(self._cfg.target_fps)))
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
                         (cfg.hotkey_overlay, self.toggle_overlay)):
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
            self._message = MSG_SPECTATOR if game.me is None else MSG_UNSUPPORTED
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
        for comp in (self._tracker, self._gank, self._objectives, self._reminders, self._fog,
                     self._throttler):
            fn = getattr(comp, "reset", None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    log.exception("reset failed for %r", type(comp).__name__)
        self._ended = False
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

    def _end_game(self, result: str | None, t: float) -> None:
        """Game over: finish the record + report in the background, session statistics."""
        log.info("Game over (result: %s)", result or "inconnu")
        self._ended = True
        self._in_game = False
        self._game_evt.clear()
        self._death_due = None
        self._state, self._message = EngineState.WAITING_GAME, MSG_WAITING
        rec, self._recorder = self._recorder, None
        if result in ("Win", "Lose") and not self._demo:
            s = self._session
            s["games"] += 1
            if result == "Win":
                s["wins"] += 1
                s["loss_streak"] = 0
            else:
                s["losses"] += 1
                s["loss_streak"] += 1
                if s["loss_streak"] >= BREAK_LOSS_STREAK and self._cfg.break_reminder:
                    self._banner = BREAK_TEXT
                    self._say(BREAK_TEXT, 0)
        if rec is not None:
            th = threading.Thread(target=self._finish_job, args=(rec,), name="TreeAICoach-report",
                                  daemon=True)
            self._bg_threads = [b for b in self._bg_threads if b.is_alive()] + [th]
            th.start()

    def _finish_job(self, rec: Any) -> None:
        try:
            path = rec.finish()
            if path is None:
                return
            self.last_record_path = Path(path)
            cfg = self._cfg
            if not cfg.post_game_report:
                return
            writer = self._report_writer
            if writer is None:
                from treeaicoach.report import write_report as writer
            html = writer(Path(path))
            if html is None:
                return
            self.last_report_path = Path(html)
            log.info("Post-game report: %s", html)
            if cfg.open_report_automatically:
                self._report_opener(Path(html))
        except Exception:
            log.exception("Post-game record / report failed")

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
        gt = (_finite(game.game_time) or 0.0) + min(max(0.0, t - game_t), 3.0)
        if self._frame_source is None:
            frame = self._grab_minimap(t)
        elif frame is None:
            self._set_state(EngineState.RUNNING, MSG_NO_FRAME)
        else:
            self._set_state(EngineState.RUNNING, MSG_RUNNING_DEMO if self._demo else MSG_RUNNING)
        identified: list[Any] = []
        if frame is not None:
            if is_black_frame(frame):
                self._set_state(EngineState.CAPTURE_BLACK, MSG_BLACK)
                frame = None
            else:
                identified = self._stabilize(t, self._vision(frame))
        tracker = self._tracker
        tracker.update(t, identified)
        raw_alerts: list[Alert] = []
        gank_alerts: list[Alert] = []
        if self._gank is not None:
            try:
                gank_alerts = list(self._gank.update(t, tracker, game) or [])
            except Exception:
                self._errors += 1
                self._err.exception("GankAnalyzer.update failed")
        raw_alerts += gank_alerts
        threat = self._update_threat(t, gank_alerts)
        if self._objectives is not None:
            raw_alerts += list(self._objectives.update(game, t) or [])
        me = tracker.me()
        me_pos = me.position() if me is not None else None
        if self._reminders is not None and threat < Level.WARNING:
            in_base = False
            if me_pos is not None:
                z = geometry.classify_zone(*me_pos)
                in_base = geometry.is_base(z) and geometry.zone_owner(z) == game.my_team
            raw_alerts += list(self._reminders.update(t, game, me_pos, in_base) or [])
        if self._fog is not None and not getattr(self._cfg, "safe_mode", False):
            self._fog.update(t, tracker, game, mode=self._cfg.fog_mode)
        rec = self._recorder
        if rec is not None:
            rec.on_tracks(tracker, t, gt)
        raw_alerts += self._death_recap_alerts(t)
        said = self._throttler.filter(raw_alerts, t)
        for a in said:
            self._say(a.text, int(a.level))
            with self._lock:
                self._last_alert, self._last_alert_t = a, t
                self._recent.append((gt, a.text, int(a.level), a.kind.value if isinstance(a.kind, AlertKind)
                                     else str(a.kind)))
            if rec is not None:
                rec.on_alert(a, gt)
        with self._lock:
            self._frame = frame
            self._identified = identified
            self._frame_id += 1
        if frame is not None and self._cfg.collect_samples:
            self._collect(frame, t)
        return said

    def _set_state(self, state: EngineState, message: str) -> None:
        with self._lock:
            self._state, self._message = state, message

    def _update_fps(self, t: float) -> None:
        last = self._last_tick_t
        self._last_tick_t = t
        if last is not None and t > last:
            inst = 1.0 / (t - last)
            self._fps = inst if self._fps <= 0 else 0.85 * self._fps + 0.15 * inst

    def _vision(self, frame: np.ndarray) -> list[Any]:
        """Detector + identifier (+ camera fallback for "self")."""
        try:
            self._ensure_detector()
            dets = list(self._detector.detect(frame) or [])
        except Exception:
            self._errors += 1
            self._err.exception("Detector failed")
            return []
        try:
            identified = list(self._identifier.identify(frame, dets) or [])
        except Exception:
            self._errors += 1
            self._err.exception("Identifier failed")
            identified = _PassThroughIdentifier().identify(frame, dets)
        if identified and not any(getattr(x, "relation", None) == "self" for x in identified):
            self._camera_self_fallback(frame, identified)
        return identified

    @staticmethod
    def _with(item: Any, **changes: Any) -> Any:
        """Copy of an ``Identified`` with some fields changed (in place for non-dataclasses)."""
        try:
            if dataclasses.is_dataclass(item) and not isinstance(item, type):
                return dataclasses.replace(item, **changes)
            for k, v in changes.items():
                setattr(item, k, v)
        except Exception:
            log.debug("Cannot update %r", item, exc_info=True)
        return item

    def _stabilize(self, t: float, identified: list[Any]) -> list[Any]:
        """Temporal sanity checks between the identifier and the tracker.

        * an unidentified icon exactly where I was a moment ago, while no icon is "self" in
          this frame, is my own icon with a misread ring colour (it would otherwise look like
          an enemy standing on me);
        * an enemy identity that pops up exactly on the spot of another enemy that was visible
          a moment ago (and is missing from this frame), while its own track has been hidden
          for a while, is that other enemy misidentified.
        """
        tracker = self._tracker
        if tracker is None or not identified:
            return identified
        out = list(identified)
        tracks = {tr.alias: tr for tr in tracker.tracks() if tr.alias}
        for i, x in enumerate(out):
            alias = getattr(x, "alias", None)
            own = tracks.get(alias) if alias else None
            if own is None or getattr(x, "relation", None) == "self":
                continue
            dt = t - own.last_seen
            pos = own.raw_position() if hasattr(own, "raw_position") else own.position()
            if pos is None or not (0.0 <= dt <= JUMP_CHECK_S):
                continue
            det = getattr(x, "det", x)
            d = math.hypot(float(det.u) - pos[0], float(det.v) - pos[1])
            if d > JUMP_SPEED * dt + JUMP_SLACK:
                # physically impossible move: the identity (not the icon) is wrong
                out[i] = self._with(x, alias=None, id_score=0.0)
        rel = [getattr(x, "relation", None) for x in out]
        me = tracker.me() if "self" not in rel else None
        if me is not None and t - me.last_seen <= STICKY_SELF_S:
            pos = me.position()
            if pos is not None:
                best, best_d = None, math.inf
                for i, x in enumerate(out):
                    det = getattr(x, "det", x)
                    if getattr(x, "alias", None) is None or getattr(x, "alias", None) == me.alias:
                        d = math.hypot(float(det.u) - pos[0], float(det.v) - pos[1])
                        if d < best_d:
                            best, best_d = i, d
                if best is not None and best_d <= STICKY_SELF_DIST:
                    out[best] = self._with(out[best], relation="self", alias=me.alias,
                                           team=getattr(out[best], "team", None) or me.team)
        present = {getattr(x, "alias", None) for x in out if getattr(x, "alias", None)}
        enemy_tracks = [tr for tr in tracker.enemies(visible_only=False) if tr.alias]
        friends = [tr for tr in tracks.values() if tr.relation != "enemy" and t - tr.last_seen <= 1.0]
        for i, x in enumerate(out):
            # unidentified "ally" ring exactly where an enemy stood a moment ago: misread ring colour
            if getattr(x, "alias", None) or getattr(x, "relation", None) != "ally":
                continue
            det = getattr(x, "det", x)
            u, v = float(det.u), float(det.v)

            def near(tr: Any) -> float:
                p = tr.position()
                return math.hypot(u - p[0], v - p[1]) if p is not None else math.inf

            if any(near(tr) <= RELABEL_DIST for tr in friends):
                continue
            cands = [(near(tr), tr) for tr in enemy_tracks
                     if tr.alias not in present and t - tr.last_seen <= RELABEL_RECENT_S]
            cands = [c for c in cands if c[0] <= RELABEL_DIST]
            if cands:
                tr = min(cands, key=lambda c: c[0])[1]
                out[i] = self._with(x, alias=tr.alias, relation="enemy", team=tr.team)
                present.add(tr.alias)
        by_alias = {tr.alias: tr for tr in enemy_tracks}
        for i, x in enumerate(out):
            alias = getattr(x, "alias", None)
            if not alias or getattr(x, "relation", None) != "enemy":
                continue
            own = by_alias.get(alias)
            if own is not None and t - own.last_seen < IDENTITY_SWAP_HIDDEN_S:
                continue
            if float(getattr(x, "id_score", 0.0) or 0.0) >= DUP_KEEP_ID_SCORE:
                continue                  # a confident portrait match is trusted
            det = getattr(x, "det", x)
            for tr in enemy_tracks:
                if tr.alias in present or t - tr.last_seen > IDENTITY_SWAP_RECENT_S:
                    continue
                pos = tr.position()
                if pos is not None and math.hypot(float(det.u) - pos[0], float(det.v) - pos[1]) \
                        <= IDENTITY_SWAP_DIST:
                    out[i] = self._with(x, alias=tr.alias)
                    present.discard(alias)
                    present.add(tr.alias)
                    break
        return self._drop_duplicates(t, out, tracks)

    def _drop_duplicates(self, t: float, out: list[Any], tracks: dict[str, Any]) -> list[Any]:
        """Drop enemy detections that duplicate another enemy icon of the same frame.

        A second, overlapping detection of one icon (camera-rectangle edge, partial occlusion,
        imprecise detector) would otherwise become a phantom enemy right next to the real one
        (unidentified, or identified as the next-best portrait: often the hidden jungler).
        Non-maximum suppression among enemy detections within :data:`DUP_DIST`: identities seen
        a moment ago and confident identifications are always kept and win over the others.
        """
        def rank(x: Any) -> tuple[int, float]:
            alias = getattr(x, "alias", None)
            tr = tracks.get(alias) if alias else None
            if tr is not None and t - tr.last_seen <= STICKY_SELF_S:
                return 0, 0.0
            ids = float(getattr(x, "id_score", 0.0) or 0.0)
            if alias and ids >= DUP_KEEP_ID_SCORE:
                return 1, -ids
            det = getattr(x, "det", x)
            return (2 if alias else 3), -float(getattr(det, "score", 0.0) or 0.0)

        enemies = [(rank(x), i, x) for i, x in enumerate(out) if getattr(x, "relation", None) == "enemy"]
        if len(enemies) < 2:
            return out
        enemies.sort(key=lambda e: (e[0], e[1]))
        kept: list[Any] = []
        drop: set[int] = set()
        for (level, _s), i, x in enemies:
            det = getattr(x, "det", x)
            if level >= 2 and any(math.hypot(float(det.u) - float(getattr(k, "det", k).u),
                                             float(det.v) - float(getattr(k, "det", k).v)) <= DUP_DIST
                                  for k in kept):
                drop.add(i)
                continue
            kept.append(x)
        return [x for i, x in enumerate(out) if i not in drop] if drop else out

    def _camera_self_fallback(self, frame: np.ndarray, identified: list[Any]) -> None:
        """No icon identified as me: the ally icon nearest to the camera centre is me."""
        game = self._game
        my_alias = game.me.champion_alias if game is not None and game.me is not None else None
        allies = [x for x in identified if getattr(x, "relation", None) == "ally"
                  and (getattr(x, "alias", None) in (None, my_alias))]
        if not allies:
            return
        center = find_camera_center(frame)
        if center is None:
            return
        best, best_d = None, CAMERA_SELF_MAX_DIST
        for x in allies:
            det = getattr(x, "det", x)
            d = math.hypot(float(det.u) - center[0], float(det.v) - center[1])
            if d < best_d:
                best, best_d = x, d
        if best is None:
            return
        idx = identified.index(best)
        try:
            if dataclasses.is_dataclass(best) and not isinstance(best, type):
                identified[idx] = dataclasses.replace(best, relation="self")
            else:
                best.relation = "self"
        except Exception:
            log.debug("Camera self fallback failed", exc_info=True)

    def _update_threat(self, t: float, gank_alerts: list[Alert]) -> int:
        for a in gank_alerts:
            lvl = int(a.level)
            if a.kind in GANK_KINDS:
                self._threat_hist.append((t, lvl, a))
                if lvl >= Level.DANGER:
                    self._last_danger_t = t
        while self._threat_hist and t - self._threat_hist[0][0] > THREAT_HOLD_S:
            self._threat_hist.popleft()
        return max((lvl for _t, lvl, _a in self._threat_hist), default=0)

    def _death_recap_alerts(self, t: float) -> list[Alert]:
        due = self._death_due
        if due is None or t < due[0]:
            return []
        self._death_due = None
        rec = self._recorder
        text = None
        if rec is not None:
            try:
                text = rec.death_recap(due[1])
            except Exception:
                self._err.exception("Death recap failed")
        if not text:
            return []
        return [make_alert(AlertKind.DEATH_RECAP, Level.INFO, t, text=text, key="death_recap")]

    def _collect(self, frame: np.ndarray, t: float) -> None:
        if t - self._last_collect < max(0.5, float(self._cfg.collect_interval_s)):
            return
        if self._collect_count >= COLLECT_MAX_FILES:
            return
        self._last_collect = t
        try:
            from treeaicoach.paths import collect_dir

            name = time.strftime("%Y%m%d_%H%M%S") + f"_{self._collect_count:04d}.png"
            if cv2.imwrite(str(collect_dir() / name), frame):
                self._collect_count += 1
        except Exception:
            self._err.exception("Sample collection failed")

    # ================================================================== capture / location
    def _find_window(self, t: float) -> Rect | None:
        if t - self._window_t < WINDOW_REFRESH_S and self._window_t > -math.inf:
            return self._window
        self._window_t = t
        try:
            if self._window_finder is not None:
                win = self._window_finder()
            else:
                from treeaicoach.capture import find_game_window

                win = find_game_window()
        except Exception:
            self._err.exception("find_game_window failed")
            win = None
        if win != self._window:
            if win is not None and self._window is not None and \
                    (win.w, win.h) != (self._window.w, self._window.h):
                self._relocate = True
            self._window = win
        return win

    def _grabber(self) -> Any:
        if self._capture is None:
            from treeaicoach.capture import ScreenCapture

            self._capture = ScreenCapture()
        return self._capture

    def _manual_rect(self, win: Rect) -> Rect | None:
        r = self._cfg.manual_minimap_rect
        if self._cfg.minimap_mode != "manual" or not isinstance(r, dict):
            return None
        try:
            sw, sh = float(r["screen_w"]), float(r["screen_h"])
            x, y, w, h = float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"])
            if (int(sw), int(sh)) == (win.w, win.h) and win.x <= x and win.y <= y \
                    and x + w <= win.x + win.w and y + h <= win.y + win.h:
                return Rect(int(x), int(y), int(w), int(h))
            sx, sy = win.w / sw, win.h / sh
            return Rect(win.x + int(round(x * sx)), win.y + int(round(y * sy)),
                        max(1, int(round(w * sx))), max(1, int(round(h * sy))))
        except Exception:
            log.warning("Invalid manual minimap rectangle %r", r)
            return None

    def _locate(self, t: float, win: Rect) -> None:
        """(Re)compute the minimap rectangle for window ``win``."""
        self._relocate = False
        self._bad_since = None
        self._next_verify = t + VERIFY_PERIOD_S
        manual = self._manual_rect(win)
        if manual is not None:
            self._minimap_rect, self._locate_method = manual, "manual"
            self._rect_window = win
            return
        side = self._cfg.minimap_side
        self._set_state(EngineState.LOCATING, MSG_LOCATING)
        loc = None
        try:
            screen = self._grabber().grab(win)
            if screen is not None and not is_black_frame(screen):
                loc = self._ensure_locator().locate(screen, win, side=side)
            elif screen is not None:
                self._set_state(EngineState.CAPTURE_BLACK, MSG_BLACK)
        except Exception:
            self._err.exception("Minimap location failed")
        self._rect_window = win
        if loc is not None:
            self._minimap_rect, self._locate_method = loc.rect, "auto"
            log.info("Minimap located at %s (score %.2f)", loc.rect, loc.score)
            return
        from treeaicoach.minimap_locator import fallback_rect

        fb_side = "left" if side == "left" else "right"
        self._minimap_rect, self._locate_method = fallback_rect(win, fb_side), "fallback"
        self._next_locate = t + LOCATE_RETRY_S
        log.info("Minimap not found: fallback rectangle %s", self._minimap_rect)

    def _grab_minimap(self, t: float) -> np.ndarray | None:
        win = self._find_window(t)
        if win is None:
            self._set_state(EngineState.LOCATING, MSG_NO_WINDOW)
            return None
        if self._relocate or self._minimap_rect is None or self._rect_window != win or (
                self._locate_method == "fallback" and t >= self._next_locate):
            self._locate(t, win)
        rect = self._minimap_rect
        if rect is None:
            return None
        frame = _as_bgr(self._grabber().grab(rect))
        if frame is None:
            self._set_state(EngineState.RUNNING, MSG_NO_FRAME)
            return None
        if self._locate_method == "auto" and t >= self._next_verify:
            self._next_verify = t + VERIFY_PERIOD_S
            try:
                from treeaicoach.minimap_locator import VERIFY_MIN_SCORE

                score = float(self._ensure_locator().verify(frame))
            except Exception:
                self._err.exception("Minimap verify failed")
                score = 1.0
            if score < VERIFY_MIN_SCORE:
                if self._bad_since is None:
                    self._bad_since = t
                elif t - self._bad_since >= VERIFY_BAD_S:
                    log.info("Minimap verification low (%.2f) for %.0f s: relocating", score, VERIFY_BAD_S)
                    self._relocate = True
            else:
                self._bad_since = None
        self._set_state(EngineState.RUNNING,
                        MSG_FALLBACK if self._locate_method == "fallback" else MSG_RUNNING)
        return frame

    def request_relocate(self) -> None:
        """Locate the minimap again at the next tick."""
        with self._lock:
            self._relocate = True

    # ================================================================== queries
    def get_status(self) -> EngineStatus:
        """Immutable status snapshot. Never raises."""
        try:
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
                    tick_ms=round(self._tick_ms, 1), demo=self._demo, banner=self._banner,
                    locate_method=self._locate_method,
                    session=(s["games"], s["wins"], s["losses"]))
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

    def get_preview(self) -> np.ndarray | None:
        """Annotated copy of the last minimap (detections, identities, me, fog), BGR. Never raises."""
        try:
            with self._lock:
                frame, identified, fid = self._frame, list(self._identified), self._frame_id
                cache = self._preview_cache
            if frame is None:
                return None
            if cache is not None and cache[0] == fid:
                return cache[1].copy()
            img = self._annotate(frame, identified)
            with self._lock:
                self._preview_cache = (fid, img)
            return img.copy()
        except Exception:
            self._err.exception("get_preview failed")
            return None

    def _annotate(self, frame: np.ndarray, identified: list[Any]) -> np.ndarray:
        img = frame.copy()
        h, w = img.shape[:2]
        fogs = self._fog.estimates() if self._fog is not None and not getattr(self._cfg, "safe_mode", False) else []
        for fe in fogs:
            region = getattr(fe, "region", None)
            if isinstance(region, np.ndarray) and region.ndim == 2 and region.any():
                m = cv2.resize(region.astype(np.uint8) * 255, (w, h), interpolation=cv2.INTER_LINEAR)
                sel = m > 127
                tint = img[sel].astype(np.float32) * 0.65 + np.array([40, 40, 200], np.float32) * 0.35
                img[sel] = tint.astype(np.uint8)
                cnts, _ = cv2.findContours((m > 127).astype(np.uint8), cv2.RETR_EXTERNAL,
                                           cv2.CHAIN_APPROX_SIMPLE)
                cv2.drawContours(img, cnts, -1, (60, 60, 235), 1, cv2.LINE_AA)
            lu, lv = fe.last_uv
            cv2.drawMarker(img, (int(lu * w), int(lv * h)), (60, 60, 235), cv2.MARKER_TILTED_CROSS,
                           max(6, w // 30), 2, cv2.LINE_AA)
        colors = {"enemy": (70, 70, 240), "ally": (235, 170, 60), "self": (60, 220, 250)}
        labels: list[tuple[int, int, int, str, tuple[int, int, int]]] = []
        for x in identified:
            det = getattr(x, "det", x)
            rel = getattr(x, "relation", getattr(det, "cls", "enemy"))
            col = colors.get(rel, (200, 200, 200))
            cx, cy = int(det.u * w), int(det.v * h)
            rad = max(4, int(det.r * w) + 2)
            cv2.circle(img, (cx, cy), rad, col, 3 if rel == "self" else 2, cv2.LINE_AA)
            label = getattr(x, "alias", None) or "?"
            if rel == "self":
                label = "moi"
            labels.append((cx, cy, rad, label, col))
        fs = max(0.3, w / 800.0)
        for cx, cy, rad, label, col in labels:
            (tw, th), _base = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, fs, 1)
            x0 = int(min(max(0, cx - tw // 2), w - tw - 2))
            y0 = cy + rad + th + 3
            if y0 > h - 2:
                y0 = cy - rad - 4
            sub = img[max(0, y0 - th - 2):min(h, y0 + 3), max(0, x0 - 2):min(w, x0 + tw + 2)]
            sub[:] = (sub.astype(np.uint16) * 2 // 7).astype(np.uint8)
            cv2.putText(img, label, (x0, y0), cv2.FONT_HERSHEY_SIMPLEX, fs, col, 1, cv2.LINE_AA)
        return img

    # ---------------------------------------------------------------- overlay state
    def _icon(self, alias: str | None, skin: int = 0) -> np.ndarray | None:
        if not alias:
            return None
        key = f"{alias}:{skin}"
        if key not in self._icons:
            icon = None
            db = self._champion_db()
            if db is not None:
                try:
                    icon = db.load_icon(alias, skin)
                except Exception:
                    icon = None
            if len(self._icons) > 40:
                self._icons.clear()
            self._icons[key] = icon
        return self._icons[key]

    def get_overlay_state(self) -> Any:
        """Immutable :class:`OverlayState` snapshot (None outside a game / overlay hidden)."""
        try:
            now = self._clock()
            with self._lock:
                if not self._overlay_visible or not self._in_game or self._game is None:
                    return None
                cache = self._overlay_cache
                if cache is not None and 0.0 <= now - cache[0] < OVERLAY_MIN_PERIOD_S:
                    return cache[1]
            state = self._build_overlay_state(now)
            with self._lock:
                self._overlay_cache = (now, state)
            return state
        except Exception:
            self._err.exception("get_overlay_state failed")
            return None

    def _screen_rects(self) -> tuple[Rect | None, Rect | None]:
        if self._frame_source is None:
            return self._minimap_rect, self._window
        # demo / frame source: pretend the minimap sits at its usual place on the main screen
        if self._demo_rects is None:
            rects: tuple[Rect | None, Rect | None] = (None, None)
            try:
                from treeaicoach.capture import monitor_rects
                from treeaicoach.minimap_locator import fallback_rect

                mons = monitor_rects()
                if mons:
                    rects = (fallback_rect(mons[0], "right"), mons[0])
            except Exception:
                log.debug("No monitor information", exc_info=True)
            self._demo_rects = rects          # computed once (monitor enumeration is not free)
        return self._demo_rects

    def _build_overlay_state(self, now: float) -> Any:
        from treeaicoach.overlay_render import EnemyView, OverlayState

        with self._lock:
            game = self._game
            game_t = self._game_t
            last_alert, last_alert_t = self._last_alert, self._last_alert_t
            hist = list(self._threat_hist)
            last_danger = self._last_danger_t
        tracker = self._tracker
        cfg = self._cfg
        me = tracker.me() if tracker is not None else None
        me_uv = me.position() if me is not None and me.visible else (
            me.position() if me is not None and now - me.last_seen < 3.0 else None)
        jungler = game.enemy_jungler() if game is not None else None
        jungler_alias = jungler.champion_alias if jungler is not None else None
        enemies: list[Any] = []
        roster: list[PlayerInfo] = list(game.enemies) if game is not None else []
        seen_keys: set[str] = set()
        for p in roster[:5]:
            tr = tracker.get(p.champion_alias) if (tracker is not None and p.champion_alias) else None
            enemies.append(self._enemy_view(EnemyView, p.champion_alias, p.champion_name, p.skin_id, tr,
                                            me_uv, now, p.champion_alias == jungler_alias))
            if tr is not None:
                seen_keys.add(tr.key)
        if tracker is not None:
            for tr in tracker.enemies(visible_only=True):
                if tr.key not in seen_keys and len(enemies) < 10:
                    enemies.append(self._enemy_view(EnemyView, tr.alias, tr.alias or "?", 0, tr, me_uv,
                                                    now, False))
        allies, roles = self._overlay_allies_roles(EnemyView, game, tracker, now)
        for v in enemies:
            v.role = roles.get(v.key) or roles.get(v.alias or "")
        level = max((lvl for t_, lvl, _a in hist if now - t_ <= THREAT_HOLD_S), default=0)
        top = max((a for t_, lvl, a in hist if now - t_ <= THREAT_HOLD_S and lvl == level),
                  key=lambda a: a.t, default=None)
        if level >= Level.DANGER:
            text = "DANGER — GANK !"
        elif level == Level.WARNING:
            who = self._display_name(game, top.alias) if top is not None else None
            text = f"ATTENTION — {who} approche" if who else "ATTENTION — ennemi proche"
        else:
            text = "SÛR"
        flash = 0.0
        if cfg.danger_flash and last_danger is not None and 0.0 <= now - last_danger < FLASH_DECAY_S:
            flash = float(1.0 - (now - last_danger) / FLASH_DECAY_S)
        la = None
        if last_alert is not None and last_alert_t is not None:
            la = (last_alert.text, int(last_alert.level), max(0.0, now - last_alert_t))
        minimap_rect, screen_rect = self._screen_rects()
        gt = (_finite(game.game_time) or 0.0) + min(max(0.0, now - game_t), 3.0) if game else None
        fogs = self._fog.estimates() if self._fog is not None and not getattr(self._cfg, "safe_mode", False) else []
        return OverlayState(
            minimap_rect=minimap_rect, screen_rect=screen_rect, me_uv=me_uv,
            my_team=game.my_team if game is not None else None,
            enemies=enemies, fogs=fogs, threat_level=int(level), threat_text=text, last_alert=la,
            objectives=self._objectives.states() if self._objectives is not None else [],
            game_time=gt, warn_radius=cfg.effective_warn_radius(),
            danger_radius=cfg.effective_danger_radius(), flash=flash,
            jungler_line=self._jungler_line(game, jungler, now),
            hint=self._reminders.hint() if self._reminders is not None else None,
            me_icon=self._icon(game.me.champion_alias, game.me.skin_id) if game and game.me else None,
            allies=allies, roles=roles,
        )

    def _overlay_allies_roles(self, cls: Any, game: GameInfo | None, tracker: Any,
                              now: float) -> tuple[list[Any], dict[str, str]]:
        """Allied views (roster order, then anonymous visible allies) + alias -> role map. Never raises."""
        allies: list[Any] = []
        roles: dict[str, str] = {}
        try:
            for p in (game.all_players() if game is not None else []):
                if p.champion_alias and getattr(p, "position", ""):
                    roles[p.champion_alias] = str(p.position)
            # inferred roles (roles.RoleResolver: alias -> RoleInfo), when the engine has one
            resolver = getattr(self, "_role_resolver", None) or getattr(self, "_roles", None)
            extra = resolver.roles() if callable(getattr(resolver, "roles", None)) else resolver
            if isinstance(extra, dict):
                for k, info in extra.items():
                    role = getattr(info, "role", info)
                    if k and isinstance(role, str) and role and not roles.get(str(k)):
                        roles[str(k)] = role
            seen: set[str] = set()
            for p in (list(game.allies) if game is not None else [])[:4]:
                tr = tracker.get(p.champion_alias) if (tracker is not None and p.champion_alias) else None
                v = self._enemy_view(cls, p.champion_alias, p.champion_name, p.skin_id, tr, None, now, False)
                v.relation, v.role = "ally", roles.get(p.champion_alias)
                allies.append(v)
                if tr is not None:
                    seen.add(tr.key)
            if tracker is not None and hasattr(tracker, "allies"):
                for tr in tracker.allies(visible_only=True):
                    if tr.key not in seen and len(allies) < 8:
                        v = self._enemy_view(cls, tr.alias, tr.alias or "?", 0, tr, None, now, False)
                        v.relation, v.role = "ally", roles.get(tr.alias or "")
                        allies.append(v)
        except Exception:
            log.debug("overlay allies / roles failed", exc_info=True)
        return allies, roles

    def _enemy_view(self, cls: Any, alias: str | None, name: str, skin: int, tr: Any,
                    me_uv: tuple[float, float] | None, now: float, is_jungler: bool) -> Any:
        uv = tr.position() if tr is not None else None
        visible = bool(tr.visible) if tr is not None else False
        ago = max(0.0, now - tr.last_seen) if tr is not None else None
        vel = tr.velocity() if tr is not None and visible else None
        approaching = False
        if visible and uv is not None and me_uv is not None and vel is not None:
            dx, dy = me_uv[0] - uv[0], me_uv[1] - uv[1]
            d = math.hypot(dx, dy)
            if d > 1e-6:
                approaching = (vel[0] * dx + vel[1] * dy) / d > 0.006 and d < 0.3
        return cls(key=tr.key if tr is not None else (alias or "?"), alias=alias, name=name or (alias or "?"),
                   visible=visible, uv=uv, last_seen_ago=ago, is_jungler=is_jungler,
                   approaching=approaching, icon=self._icon(alias, skin), velocity=vel)

    @staticmethod
    def _display_name(game: GameInfo | None, alias: str | None) -> str | None:
        if not alias:
            return None
        if game is not None:
            p = game.player_by_alias(alias)
            if p is not None and p.champion_name:
                return p.champion_name
        return alias

    def _jungler_line(self, game: GameInfo | None, jungler: PlayerInfo | None, now: float) -> str | None:
        if game is None or jungler is None:
            return None
        name = jungler.champion_name or jungler.champion_alias
        tr = self._tracker.get(jungler.champion_alias) if self._tracker is not None else None
        if tr is None or tr.position() is None:
            return f"Jungler : {name} — pas encore vu"
        zone = geometry.zone_name_fr(geometry.classify_zone(*tr.position()), game.my_team)
        if tr.visible:
            return f"Jungler : {name} — visible, {zone}" if zone else f"Jungler : {name} — visible"
        ago = int(max(0.0, now - tr.last_seen))
        return f"Jungler : {name} — vu il y a {ago} s, {zone}" if zone else f"Jungler : {name} — vu il y a {ago} s"

    # ---------------------------------------------------------------- F9
    def jungler_status_text(self) -> str:
        """French answer to "where is the enemy jungler?" (F9). Never raises."""
        try:
            with self._lock:
                game, in_game = self._game, self._in_game
            if game is None or not in_game:
                return "Pas de partie en cours."
            jungler = game.enemy_jungler()
            if jungler is None:
                return "Jungler ennemi inconnu."
            name = jungler.champion_name or jungler.champion_alias or "Le jungler ennemi"
            tr = self._tracker.get(jungler.champion_alias) if self._tracker is not None else None
            pos = tr.position() if tr is not None else None
            if pos is None:
                return "Jungler ennemi pas encore vu."
            zone = geometry.classify_zone(*pos)
            if tr.visible:
                label = geometry.zone_label_fr(zone, game.my_team)
                return f"{name} est visible, {label}." if label else f"{name} est visible."
            ago = int(round(max(0.0, self._clock() - tr.last_seen)))
            where = geometry.zone_name_fr(zone, game.my_team)
            if ago >= 120:
                head = f"{name} vu il y a plus de 2 minutes"
            else:
                head = f"{name} vu il y a {seconds_fr(ago)}"
            return f"{head}, {where}." if where else f"{head}."
        except Exception:
            log.exception("jungler_status_text failed")
            return "Position du jungler ennemi inconnue."

    def speak_jungler_status(self) -> None:
        """Hotkey callback: speak :meth:`jungler_status_text` (debounced). Never raises."""
        try:
            now = self._clock()
            if now - self._last_where_t < HOTKEY_DEBOUNCE_S:
                return
            self._last_where_t = now
            text = self.jungler_status_text()
            self._say(text, 1)
            with self._lock:
                self._recent.append((None, text, 0, AlertKind.JUNGLER_WHERE.value))
        except Exception:
            log.exception("speak_jungler_status failed")

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


__all__ = ["EngineState", "EngineStatus", "FrameSource", "CoachEngine", "find_camera_center",
           "seconds_fr"]
