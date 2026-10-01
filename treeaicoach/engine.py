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
import re
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Protocol

import cv2
import numpy as np

from treeaicoach import geometry
from treeaicoach.alerts import Alert, AlertKind, AlertThrottler, Level, make_alert
from treeaicoach.capture import Rect, is_black_frame
from treeaicoach.config import Config
from treeaicoach.live_client import GameInfo, PlayerInfo
from treeaicoach.scheduler import HeavyScheduler, MotionSnapshot, RateGovernor, burst_reason
from treeaicoach.sysperf import CpuMeter, PerfBudget, RateMeter, RollingStats

log = logging.getLogger(__name__)

# ------------------------------------------------------------------------------ tunables
POLL_IN_GAME_S = 1.0             # Live Client poll period in game
POLL_IDLE_S = 2.0                # ... and outside a game (0.5 Hz)
GAME_GONE_S = 8.0                # API silent this long after a game -> game over
GAME_TIME_BACK_S = 3.0           # game_time going back more than this -> new game
WINDOW_REFRESH_S = 1.0           # game window rectangle / focus cache
VERIFY_PERIOD_S = 1.0            # first minimap verify() after a location (then the budget's verify_s)
UNFOCUSED_HIDE_S = 1.5           # game not in the foreground this long -> overlay hidden
STATS_EVERY_S = 1.0              # health monitor refresh (CPU %, rates)
STALE_MIN_GAME_S = 90.0          # frozen-capture check only once minions walk (game time, s)
TRIVIAL_BUY_AFTER_S = 1200.0     # after 20:00 ...
TRIVIAL_BUY_GOLD = 500           # ... no HUD chip for a lone component cheaper than this (not completing)
EARLY_ADVICE_GT_S = 30.0         # no lane-phase tip / insight on the HUD line before the minions spawn (0:30 since 26.1)
#: words of a "go" HUD line (hidden under a PRUDENT / SAFE gauge: no contradiction on the card)
GO_WORDS = ("à toi de jouer", "joue agressif", "vas-y", "va-y", "attaque", "engage", "force ", "punis")
VERIFY_BAD_S = 3.0               # verify() below threshold this long -> relocate
LOCATE_RETRY_S = 10.0            # retry the auto location this often while on the fallback rect
HEAVY_HZ = 2.0                   # rate of the coaching stages (coach, Tab, tips, items, hype / AI)
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
SELF_ICON_PERIOD_S = 0.5         # HUD portrait / icon learner status refresh (2 Hz)
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
#: Coaching chatter (macro tips, compliments, Tab insights): never spoken during a gank threat.
COACH_KINDS = frozenset({AlertKind.MACRO_TIP, AlertKind.PRAISE, AlertKind.SCOREBOARD})
ON_SCREEN_MARGIN = 0.015        # a gank threat this far inside the camera view is on my screen
ROLE_NOTICE_S = 20.0             # the "role detected (lane swap)" HUD notice stays this long
TIP_TOAST_GAP_S = 60.0           # beginner tip toasts: at most one per minute (the HUD line shows them all)
HUD_DWELL_S = 5.0                # a HUD advice line stays at least this long (unless a danger replaces it)
TEXT_MSG_S = 10.0                # a written-only message stays on the HUD line this long

MSG_STOPPED = "Analyse arrêtée."
MSG_WAITING = "En attente d'une partie de League of Legends…"
MSG_LOCATING = "Recherche de la minimap…"
MSG_NO_WINDOW = "Fenêtre du jeu introuvable (jeu réduit ?)."
MSG_RUNNING = "Analyse de la minimap en cours."
MSG_RUNNING_DEMO = "Mode démo : partie simulée."
MSG_FALLBACK = ("Minimap non trouvée automatiquement : position par défaut utilisée "
                "(calibre-la dans Réglages si les alertes sont fausses).")
MSG_BLACK = ("Capture noire : passe le jeu en Sans bordure "
             "(Paramètres > Vidéo > Mode fenêtre : Sans bordure).")
MSG_FROZEN = ("Capture figée : l'image de la minimap ne change plus. Passe le jeu en Sans bordure "
              "(Paramètres > Vidéo > Mode fenêtre).")
MSG_FULLSCREEN = ("Le jeu est en Plein écran : l'overlay ne peut pas s'afficher et la capture peut "
                  "être noire. Passe en Sans bordure (Paramètres > Vidéo > Mode fenêtre).")
MSG_MINIMIZED = "Jeu réduit : analyse en pause."
MSG_OCCLUDED = "Minimap cachée par une autre fenêtre : analyse en pause."
MSG_UNSUPPORTED = "Mode de jeu non pris en charge : uniquement la Faille de l'invocateur."
MSG_MINIMAP_COVERED = "Minimap masquée (boutique ou tableau des scores) : analyse en pause."
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
    #: health monitor (CoachEngine.health()): capture fps / backend, detect ms p50 / p95, overlay fps,
    #: champions seen / expected, minimap score, CPU %, budget... - for the UI's "Système" panel
    health: dict | None = None


class FrameSource(Protocol):
    """Replaces the capture + Live Client (demo, tests)."""

    def next(self, t: float) -> tuple[np.ndarray | None, GameInfo | None]: ...


# ------------------------------------------------------------------------------ helpers
_MAP_NAMES = {12: "ARAM", 30: "Arène", 21: "Nexus Blitz", 22: "TFT", 453: "League Classic"}


def unsupported_message(game: Any) -> str:
    """MSG_UNSUPPORTED + the detected map / mode (ARAM, Arène...) when known."""
    try:
        name = _MAP_NAMES.get(int(getattr(game, "map_number", 0) or 0)) or \
            ("League Classic" if getattr(game, "is_league_classic", False) is True else None) or \
            (str(getattr(game, "game_mode", "") or "").strip() or None)
    except (TypeError, ValueError):
        name = None
    return f"{MSG_UNSUPPORTED} (partie détectée : {name})" if name else MSG_UNSUPPORTED


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
# ------------------------------------------------------------------------------ base siege / ace
_BASE_TURRET_RE = re.compile(r"_(?:[LR]_01|C_0[123])_")
SIEGE_EVENT_S = 45.0             # a base structure of ours fell this recently -> siege
ACE_EVENT_S = 35.0               # enemy ace this recently -> ace state
SIEGE_TEXT = "Ils sont dans ta base : défends le nexus en réapparaissant, attends le groupe."
ACE_TEXT = "Ace : ils prennent ta base, attendez la réapparition ensemble."
ACE_TEXT_FAR = "Ace : attendez la réapparition ensemble, ne sortez pas seuls."


def structure_owner(name: Any) -> str | None:
    """"Turret_T1_C_05_A" / "Barracks_T2_L1" -> "ORDER" / "CHAOS" (None if unknown)."""
    n = str(name or "")
    return "ORDER" if "_T1_" in n or n.endswith("_T1") else "CHAOS" if "_T2_" in n or n.endswith("_T2") else None


def siege_state(game: Any, gt: float, enemies_in_base: int = 0) -> tuple[str | None, str | None]:
    """``("ace" | "siege" | None, HUD line)`` from the Live Client events + enemies seen in my
    base: an enemy ace (or 4+ of us dead) dominates everything; a siege is my base open (an
    inhibitor or a base turret of mine destroyed) with enemies inside, or a base structure of mine
    falling right now. Pure, never raises."""
    try:
        my = getattr(game, "my_team", None)
        if my not in ("ORDER", "CHAOS"):
            return None, None
        events = list(getattr(game, "events", None) or [])
        ace = False
        base_open = recent = False
        for e in events:
            name = e.get("EventName") if isinstance(e, dict) else None
            et = _finite(e.get("EventTime")) if isinstance(e, dict) else None
            age = (gt - et) if et is not None else 1e9
            if name == "Ace" and e.get("AcingTeam") not in (None, my) and 0 <= age <= ACE_EVENT_S:
                ace = True
            elif name in ("TurretKilled", "InhibKilled"):
                struct = e.get("TurretKilled") or e.get("InhibKilled")
                if structure_owner(struct) != my:
                    continue
                base = name == "InhibKilled" or bool(_BASE_TURRET_RE.search(str(struct)))
                base_open = base_open or base
                if base and 0 <= age <= SIEGE_EVENT_S:
                    recent = True
        team = [p for p in (game.all_players() if hasattr(game, "all_players") else []) if p.team == my]
        dead = sum(1 for p in team if getattr(p, "is_dead", False))
        if ace or (len(team) >= 5 and dead >= 4):
            return "ace", (ACE_TEXT if base_open or enemies_in_base > 0 else ACE_TEXT_FAR)
        if recent or (base_open and enemies_in_base >= 2):
            return "siege", SIEGE_TEXT
        return None, None
    except Exception:
        return None, None


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
        plays_summary = self.plays_summary()
        if rec is not None:
            th = threading.Thread(target=self._finish_job, args=(rec, plays_summary), name="TreeAICoach-report",
                                  daemon=True)
            self._bg_threads = [b for b in self._bg_threads if b.is_alive()] + [th]
            th.start()

    def _say_game_summary(self, record_path: Path) -> None:
        """Speak the short end-of-game summary (analysis.spoken_summary). Never raises."""
        try:
            if not getattr(self._cfg, "post_game_summary", True):
                return
            import json

            from treeaicoach.analysis import analyze_game, spoken_summary

            data = json.loads(Path(record_path).read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("meta"), dict):
                text = spoken_summary(analyze_game(data))
                if text:
                    self._say(text, 0)
        except Exception:
            log.debug("End-of-game summary unavailable", exc_info=True)

    def _finish_job(self, rec: Any, plays_summary: dict | None = None) -> None:
        try:
            path = rec.finish()
            if path is None:
                return
            if plays_summary:                   # play ratings + "précision" (plays.py) in the record
                from treeaicoach.plays import attach_to_record

                attach_to_record(path, plays_summary)
            self.last_record_path = Path(path)
            cfg = self._cfg
            self._say_game_summary(Path(path))
            lcu = self._postgame_lcu()          # League Client found: its timeline comes after the report
            if not cfg.post_game_report:
                if lcu is not None:
                    self._lcu_truth_job(lcu, Path(path), None)
                return
            writer = self._report_writer
            if writer is None:
                from treeaicoach.report import write_report

                def writer(p: Path, _pending: bool = lcu is not None) -> Path | None:
                    return write_report(p, lcu_pending=_pending)
            html = writer(Path(path))
            if html is None:
                return
            self.last_report_path = Path(html)
            log.info("Post-game report: %s", html)
            prev_review = getattr(self, "last_ai_review", None)
            self._ai_postgame_review(Path(path), Path(html))
            if cfg.open_report_automatically:
                self._report_opener(Path(html))
            if lcu is not None:
                review = getattr(self, "last_ai_review", None)
                self._lcu_truth_job(lcu, Path(path), Path(html), review if review is not prev_review else None)
        except Exception:
            log.exception("Post-game record / report failed")

    # ------------------------------------------------------------------ League Client (lcu.py)
    def _postgame_lcu(self) -> Any:
        """The League Client API client when enabled and the client is running, else None. Never raises."""
        try:
            if self._demo or not getattr(self._cfg, "lcu_enabled", True):
                return None
            client = getattr(self, "_lcu", None)
            if client is None:
                from treeaicoach.lcu import get_default_client

                client = self._lcu = get_default_client()
            return client if client.available() else None
        except Exception:
            log.debug("League Client unavailable", exc_info=True)
            return None

    def _lcu_truth_job(self, client: Any, record_path: Path, html: Path | None, review: str | None = None) -> None:
        """Wait for the client's match timeline (~2 min max), save the ground truth next to the
        record, then rewrite the report with it (the pending page reloads itself). Never raises."""
        truth = None
        try:
            import json

            from treeaicoach import ground_truth
            from treeaicoach.lcu import fetch_postgame_truth

            record = json.loads(Path(record_path).read_text(encoding="utf-8"))
            truth = fetch_postgame_truth(record, client, cancel=self._stop_evt,
                                         timeout_s=getattr(self, "_lcu_timeout_s", 120.0),
                                         poll_s=getattr(self, "_lcu_poll_s", 8.0))
            if truth is not None:
                truth["record"] = Path(record_path).name
                try:
                    from treeaicoach.analysis import analyze_game

                    t = analyze_game(record, truth=truth).get("truth") or {}
                    truth["score"] = ground_truth.compact_score(t.get("reliability"), record)
                except Exception:
                    log.exception("Cannot score the alerts against the client timeline")
                self.last_truth_path = ground_truth.save_truth(record_path, truth)
        except Exception:
            log.exception("League Client post-game data failed")
        if html is None:
            return
        try:   # final page (with the truth, or without the "pending" banner)
            writer = self._report_writer
            if writer is None:
                from treeaicoach.report import write_report as writer
            out = writer(Path(record_path))
            if out is not None and review:
                from treeaicoach.ai_advisor import append_review_html

                append_review_html(out, review, str(self._cfg.ai_provider))
            if out is not None and truth is not None:
                log.info("Post-game report updated with the League Client data: %s", out)
        except Exception:
            log.exception("Report update with the League Client data failed")

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
        tracker.update(t, identified)
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
        # v3 director: fight decision + speech context every tick, macro / positioning / wards at HEAVY_HZ
        tac_alerts, gank_now = self._tactics_tick(t, gt, game, tracker, gank_alerts, threat=threat)
        self._ward_guide_tick(t, game, tracker, frame, identified)
        # latency first: a gank alert (or the fight call) is spoken NOW, before the heavier stages
        said_now = self._say_gank_now(gank_now + danger_now, t, gt, frame)
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

    def _tactics_tick(self, t: float, gt: float, game: GameInfo, tracker: Any,
                      gank_alerts: list[Alert], threat: int = 0) -> tuple[list[Alert], list[Alert]]:
        """v3 director (tactics.py): ``(alerts to route, gank alerts + fight call for the fast path)``.
        Gank alerts not worth the voice (grouped, screened, fight...) are written or dropped here."""
        gank = [a for a in gank_alerts if a.kind in GANK_KINDS]
        tac = self._tactics
        if tac is None:
            return [], gank
        try:
            heavy = "tactics" in self._heavy_now
            out = tac.tick(t, gt, game, tracker, heavy=heavy, scoreboard=self.scoreboard_summary(),
                           roles=self._role_resolver,
                           objectives=self._objectives.states() if self._objectives is not None else [],
                           danger_radius=self._cfg.effective_danger_radius(),
                           stance=self._stance.current() if self._stance is not None else None,
                           waves=self._macro_waves(), jungle_intel=self.jungle_intel(), threat=threat)
            for kind, title, sub, key in out.toasts:
                self._toast(kind, title, sub, None, key, t)
            self._macro_show(out, t, gt)
            calls = [a for a in out.alerts if str(a.key).startswith("call:")]
            keep, written = tac.triage_ganks(gank, game, self.scoreboard_summary())
            for a in written:
                self._write_text(a, t, gt)
            return [a for a in out.alerts if a not in calls], calls + keep
        except Exception:
            self._errors += 1
            self._err.exception("Tactical director failed")
            return [], gank

    # ================================================================== COUPS DE GÉNIE (macro.py)
    def _macro_waves(self) -> dict:
        """Per-lane wave state of the coach (waves.py, as dicts) for the macro planner."""
        try:
            return dict(self._coach.waves() or {}) if self._coach is not None else {}
        except Exception:
            return {}

    def _macro_show(self, out: Any, t: float, gt: float) -> None:
        """A new macro call: HUD line (held while active) + "COUP DE GÉNIE" badge (plays fx) +
        record; a cancelled one leaves the HUD line. The arrow / banner are the director's."""
        c = getattr(out, "macro_cancelled", None)
        if c is not None:
            msg = self._text_msg
            if msg is not None and msg[1] == c.text:
                self._text_msg = None
        c = getattr(out, "macro_new", None)
        if c is None:
            return
        self._text_msg = (t, c.text)
        self._topic_seen(f"genie:{c.kind}", t)          # the call owns its topic: no toast repeats it
        self.text_messages.append((t, "genie", f"{c.text} ({c.why})"))
        del self.text_messages[:-100]
        self.macro_calls = (getattr(self, "macro_calls", []) + [(gt, c)])[-50:]
        rec = self._recorder
        if rec is not None:
            try:
                rec.on_alert(make_alert(AlertKind.MACRO_TIP, Level.INFO, t, text=c.text,
                                        key=f"macro:genie:{c.kind}"), gt)
            except Exception:
                log.debug("macro call record failed", exc_info=True)
        if c.genius:
            self._push_fx(SimpleNamespace(cls="brilliant", rule=f"genie:{c.kind}", reason=c.text, t=t, gt=gt,
                                          key=f"genie:{c.ident}", alias=None, size="big", title="COUP DE GÉNIE"))

    def _push_fx(self, play: Any) -> None:
        """Animate one badge (fx_overlay.PlayFx, Windows only; recorded in ``fx_pushed`` elsewhere)."""
        try:
            self.fx_pushed = (getattr(self, "fx_pushed", []) + [play])[-50:]
            fx = getattr(self, "_play_fx", None)
            if fx is None and sys.platform == "win32" and self._running:
                from treeaicoach.fx_overlay import PlayFx

                fx = self._play_fx = PlayFx(self._cfg, self._screen_rects,
                                            lambda: self._overlay_visible and self._in_game)
            if fx is not None:
                fx.apply_config(self._cfg)
                fx.push(play)
        except Exception:
            self._err.exception("Badge animation failed")

    # ================================================================== ward guide
    def request_ward_guide(self) -> bool:
        """Hotkey (``cfg.hotkey_ward``, F7): show the best ward spots now (minimap + game view).
        Visual only. Never raises."""
        try:
            wg = self._ward_guide
            return bool(wg is not None and self._in_game and wg.request(self._clock()))
        except Exception:
            log.exception("request_ward_guide failed")
            return False

    def _ward_recommend(self, game: Any, tracker: Any) -> list[Any]:
        """Best 1-2 ward spots right now (hotkey), from wards.recommend. Never raises."""
        try:
            from treeaicoach import wards

            me = tracker.me() if tracker is not None else None
            me_pos = me.position() if me is not None else None
            role = None
            res = self._role_resolver
            if res is not None and hasattr(res, "my_role"):
                role = res.my_role()
            role = role or (str(getattr(game.me, "position", "") or "").upper() if game.me is not None else None)
            obj = None
            for o in (self._objectives.states() if self._objectives is not None else []):
                rem = _finite(getattr(o, "remaining", None))
                key = str(getattr(o, "key", "") or "")
                if key in wards.OBJ_PIT and (getattr(o, "alive", False) or (rem is not None and rem <= 90.0)):
                    r = 0.0 if getattr(o, "alive", False) or rem is None else float(rem)
                    if obj is None or r < obj[1]:
                        obj = (key, r)
            tac = self._tactics
            phase = (tac.phase() if tac is not None else None) or "laning"
            st = self._stance.current() if self._stance is not None else None
            ahead = _finite(getattr(st, "score", None)) or 0.0
            return list(wards.recommend(game.my_team, role, phase=phase, me_pos=me_pos, objective=obj,
                                        ahead=ahead, n=2))
        except Exception:
            self._err.exception("ward recommend failed")
            return []

    def _ward_guide_tick(self, t: float, game: Any, tracker: Any, frame: Any, identified: list[Any]) -> None:
        """Feed the ward guide: the director's ward advice / the hotkey -> guides; camera rectangle and
        ward-placed detection on the minimap frame while a guide is active. Never raises."""
        wg = self._ward_guide
        if wg is None:
            return
        try:
            tac = self._tactics
            fighting = tac is not None and tac.in_fight()
            advice = tac.wards.current(t) if tac is not None and not fighting else None
            avoid = []
            for d in identified or ():
                det = getattr(d, "det", d)
                u, v = _finite(getattr(det, "u", None)), _finite(getattr(det, "v", None))
                if u is not None and v is not None:
                    avoid.append((u, v))
            wg.update(t, advice, frame, lambda: self._ward_recommend(game, tracker), avoid)
        except Exception:
            self._err.exception("Ward guide failed")

    def _ward_overlay(self, now: float, tac: Any, minimap_rect: Any, screen_rect: Any,
                      me_uv: Any) -> tuple[list, list]:
        """``(minimap guides, game-view markers)``: the director's guides with its plain ward rings
        replaced by the ward guide's (when enabled). Nothing from the guide during a fight."""
        guides = list(tac.guides(now)) if tac is not None else []
        wg = self._ward_guide
        if wg is None or not wg.enabled:
            return guides, []
        try:
            guides = [g for g in guides if getattr(g, "kind", "") != "ward"]
            if tac is not None and tac.in_fight():
                return guides, []
            guides += wg.minimap_guides(now)
            return guides, wg.world_markers(now, screen_rect, minimap_rect, me_uv)
        except Exception:
            self._err.exception("Ward guide overlay failed")
            return guides, []

    def _speech_budget(self, said: list[Alert], t: float, busy: bool = False) -> list[Alert]:
        """THE voice gate's budget (voice_policy.VoiceGate): critical alerts pass, the rest within
        the budget (or queued); a queued message may be released when nothing else is said."""
        tac = self._tactics
        if tac is None:
            return said
        try:
            ctx = tac.speech_context()
            out = tac.gate.filter_speech(said, t, ctx)
            if not out and not busy:
                q = tac.gate.pop_ready(t, ctx)
                if q is not None:
                    out = [q]
            return out
        except Exception:
            self._err.exception("Speech budget failed")
            return said

    def _speak_alerts(self, said: list[Alert], t: float, gt: float) -> None:
        rec = self._recorder
        if self._gate is not None:
            for a in said:
                self._gate.record(a, t)
        if self._tactics is not None:
            for a in said:
                self._tactics.gate.note_spoken(a, t)
        for a in said:
            self._say(a.text, int(a.level))
            with self._lock:
                self._last_alert, self._last_alert_t = a, t
                self._recent.append((gt, a.text, int(a.level), a.kind.value if isinstance(a.kind, AlertKind)
                                     else str(a.kind)))
            if rec is not None:
                rec.on_alert(a, gt)

    def _say_gank_now(self, gank: list[Alert], t: float, gt: float, frame: Any = None) -> list[Alert]:
        """Gank alerts go to the voice right after the gank check (throttled, routed), before
        the coaching stages of the tick. Returns the alerts said. Never raises.

        A WARNING gank alert whose enemies are all inside my camera view (white rectangle of the
        minimap: they are on my screen already) is written, not spoken (DANGER is always spoken)."""
        if not gank:
            return []
        try:
            calls = [a for a in gank if str(a.key).startswith("call:")]   # fight decision: not throttled
            gank = self._written_if_on_screen([a for a in gank if a not in calls], t, gt, frame) + calls
            routed = self._route_messages([a for a in gank if a not in calls], t, gt)
            said = self._speech_budget(self._route_messages(calls, t, gt) + self._throttler.filter(routed, t), t)
            self._speak_alerts(said, t, gt)
            return said
        except Exception:
            self._errors += 1
            self._err.exception("Gank alert fast path failed")
            return []

    def _camera_rect_now(self, t: float, frame: Any) -> Any:
        """Camera rectangle on the minimap (``camera_proj.CameraRect``-like: u0, v0, u1, v1), from
        a camera tracker already fed elsewhere, else found on this frame; None if unknown."""
        for obj in (getattr(self, "_camera", None), getattr(self, "_camera_tracker", None),
                    getattr(getattr(self, "_ward_guide", None), "camera", None)):
            cur = getattr(obj, "current", None)
            if callable(cur):
                rect = cur(t)
                if rect is not None:
                    return rect
        if frame is None:
            return None
        from treeaicoach import camera_proj

        find = getattr(camera_proj, "find_camera_rect", None)
        return find(frame) if callable(find) else None

    def _written_if_on_screen(self, gank: list[Alert], t: float, gt: float, frame: Any) -> list[Alert]:
        """Gank alerts to speak: WARNING ones (and pre-alerts) whose enemies are all inside the
        camera view (on my screen) are throttled and written instead (HUD line + toast; the
        overlay threat level is unchanged). DANGER ("recule !") is always spoken: it is an
        instruction, not news, and its latency is guaranteed. Without a camera rectangle, or for
        a beginner (``skill_level == "debutant"``: real game, he died to enemies on his screen),
        everything is spoken. Never raises."""
        tracker = self._tracker
        if tracker is None or not any(int(a.level) < Level.DANGER for a in gank):
            return gank
        if str(getattr(self._cfg, "skill_level", "") or "") == "debutant":
            return gank          # a beginner does not read an enemy on his screen as a gank: spoken
        try:
            rect = self._camera_rect_now(t, frame)
            box = tuple(_finite(getattr(rect, k, None)) for k in ("u0", "v0", "u1", "v1")) \
                if rect is not None else ()
            if len(box) != 4 or any(c is None for c in box):
                return gank
            u0, v0, u1, v1 = (float(c) for c in box)        # type: ignore[arg-type]
            m = ON_SCREEN_MARGIN
            speak: list[Alert] = []
            seen: list[Alert] = []
            for a in gank:
                if int(a.level) >= Level.DANGER:
                    speak.append(a)
                    continue
                members = tuple(a.members) or ((a.alias,) if a.alias else ())
                pts = []
                for k in members:
                    tr = tracker.get(k)
                    pts.append(tr.position() if tr is not None and tr.visible else None)
                on = bool(pts) and all(p is not None and u0 + m <= p[0] <= u1 - m and v0 + m <= p[1] <= v1 - m
                                       for p in pts)
                (seen if on else speak).append(a)
            for a in self._throttler.filter(self._route_messages(seen, t, gt), t):
                self._write_text(a, t, gt)
            return speak
        except Exception:
            self._err.exception("On-screen gank check failed")
            return gank

    def _scoreboard_and_praise(self, t: float, tracker: Any, game: GameInfo, threat: int,
                               gank_alerts: list[Alert], gt: float) -> list[Alert]:
        """Tab scoreboard insights + praise -> INFO alerts (voice, throttled) and toasts. Never raises."""
        out: list[Alert] = []
        cfg = self._cfg
        roles = self._role_resolver
        sb, pr = self._scoreboard, self._praise
        summary = None
        try:
            if sb is not None:
                sb.track_positions(t, tracker, game)
                insights = sb.update(game, t, roles=roles, tracker=tracker, threat=threat)
                summary = sb.summary()
                if getattr(cfg, "scoreboard_insights", True):
                    for ins in insights:
                        out.append(make_alert(AlertKind.SCOREBOARD, Level.INFO, t, alias=ins.alias,
                                              text=ins.text, key=ins.key))
                        self._toast(ins.toast_kind, ins.title, ins.subtitle, ins.alias, ins.key, t)
                rec = self._recorder
                if rec is not None and summary is not self._sb_recorded and summary.players:
                    self._sb_recorded = summary
                    on_sb = getattr(rec, "on_scoreboard", None)
                    if callable(on_sb):
                        on_sb(summary.to_dict(), gt)
        except Exception:
            self._errors += 1
            self._err.exception("Scoreboard analysis failed")
        try:
            if pr is not None:
                if any(int(a.level) >= Level.DANGER and a.kind in GANK_KINDS for a in gank_alerts):
                    pr.note_danger(t)
                role = None
                try:
                    role = roles.my_role() if roles is not None else None
                except Exception:
                    role = None
                for p in pr.update(t, game, threat=threat, scoreboard=summary, role=role):
                    if not getattr(cfg, "praise_enabled", True):
                        continue
                    out.append(make_alert(AlertKind.PRAISE, Level.INFO, t, alias=p.alias, text=p.text, key=p.key))
                    self._toast("praise", p.title, p.subtitle, p.alias, p.key, t)
        except Exception:
            self._errors += 1
            self._err.exception("Praise failed")
        return out

    def _item_advice(self, t: float, game: GameInfo, me_pos: Any, gt: float) -> list[Alert]:
        """Build advice (itemization.ItemAdvisor): toast + HUD line, spoken only if enabled. Never raises."""
        cfg = self._cfg
        if not getattr(cfg, "item_advice", True):
            return []
        try:
            adv = getattr(self, "_item_adv", None)
            if adv is None or gt + 5.0 < getattr(self, "_item_adv_gt", 0.0):
                from treeaicoach.itemization import ItemAdvisor
                adv = self._item_adv = ItemAdvisor()
            self._item_adv_gt = gt
            in_base = False
            if me_pos is not None:
                z = geometry.classify_zone(*me_pos)
                in_base = geometry.is_base(z) and geometry.zone_owner(z) == game.my_team
            roles = self._role_resolver
            role = roles.my_role() if roles is not None and hasattr(roles, "my_role") else None
            out: list[Alert] = []
            soon = False
            try:
                for o in (self._objectives.states() if self._objectives is not None else []):
                    rem = getattr(o, "remaining", None)
                    if getattr(o, "key", "") in ("dragon", "baron", "herald", "grubs", "elder") and (
                            getattr(o, "alive", False) or (rem is not None and 0 <= rem <= 120)):
                        soon = True
            except Exception:
                soon = False
            for a in adv.update(t, game, role=role, in_base=in_base, objective_soon=soon):
                if getattr(cfg, "item_advice_toasts", True):
                    self._toast("insight", a.title, a.subtitle, None, a.key, t)
                if getattr(cfg, "item_advice_speak", False):
                    out.append(make_alert(AlertKind.MACRO_TIP, Level.INFO, t, text=a.text, key=a.key))
            return out
        except Exception:
            self._errors += 1
            self._err.exception("Item advice failed")
            return []

    def _hype_and_ai(self, t: float, game: GameInfo, gt: float, threat: int, me_pos: Any,
                     alerts: list[Alert]) -> list[Alert]:
        """hype.py (win probability, caster lines) + ai_advisor.py (optional LLM tip). Never raises."""
        cfg = self._cfg
        try:
            from treeaicoach.ai_advisor import AIAdvisor
            from treeaicoach.hype import HypeCaster, restyle_praise

            hc, ai = getattr(self, "_hype", None), getattr(self, "_ai", None)
            if hc is None or ai is None:
                hc, ai = self._hype, self._ai = HypeCaster(cfg), AIAdvisor(cfg, clock=self._clock)
            elif gt + 5.0 < getattr(self, "_extras_gt", 0.0):          # new game
                hc.reset()
                ai.reset()
            self._extras_gt = gt
            hc.apply_config(cfg)
            ai.apply_config(cfg)
            summary = self.scoreboard_summary()
            if hc.style == "caster":
                alerts = [dataclasses.replace(a, text=restyle_praise(a.key, a.text, "caster"))
                          if a.kind == AlertKind.PRAISE else a for a in alerts]
            # hype / win-probability lines go through the voice gate like every other message
            # (visual first: written in "minimal" / "normal", spoken in "bavard")
            for i, line in enumerate(hc.update(t, game, summary, threat=threat)):
                swing = "Victoire" in line or "victoire" in line
                alerts = list(alerts) + [make_alert(AlertKind.PRAISE, Level.INFO, t, text=line,
                                                    key=f"hype:swing:{int(t)}" if swing else f"caster:{int(t)}:{i}")]
            in_base = False
            if me_pos is not None:
                z = geometry.classify_zone(*me_pos)
                in_base = geometry.is_base(z) and geometry.zone_owner(z) == game.my_team
            from treeaicoach.ai_advisor import engine_context

            ai.update(t, game, in_base=in_base, roles=self._role_resolver, scoreboard=summary,
                      objectives=self._objectives.states() if self._objectives is not None else [],
                      item_text=self.item_advice_text(), threat=threat, context=lambda: engine_context(self, t),
                      win_prob=hc.win_probability(), in_fight=self._ai_in_fight())
            adv = ai.poll()
            if adv is not None:
                self.last_ai_advice = adv.text
                self.ai_answer_seq = getattr(self, "ai_answer_seq", 0) + 1
                self._text_msg = (t, adv.text)
                self.text_messages.append((t, "ai", adv.text))
                title = adv.title
                self._toast("warning" if adv.error else "insight", title, adv.text, None, f"ai:{adv.t:.0f}", t)
                if getattr(cfg, "ai_speak", False) and threat < Level.WARNING and not adv.error:
                    self._say(adv.text, int(Level.INFO))
        except Exception:
            self._errors += 1
            self._err.exception("Hype / AI advice failed")
        return alerts

    def _plays_tick(self, t: float, gt: float, game: GameInfo, threat: int, me_pos: Any) -> None:
        """Play ratings (plays.py, chess.com style): classify, feed the AI advisor, animate the badge
        (fx_overlay.py, own click-through window, Windows only). Never raises."""
        try:
            from treeaicoach import plays

            pc = getattr(self, "_plays", None)
            if pc is None:
                pc = self._plays = plays.PlayClassifier(self._cfg)
                self.recent_plays: list[Any] = []
            pc.apply_config(self._cfg)
            n_before = len(pc.history())
            shown = pc.update(plays.build_context(self, t, gt, game, threat, me_pos))
            ai = getattr(self, "_ai", None)
            note = getattr(ai, "note_play", None)
            if callable(note):
                for p in pc.history()[n_before:]:
                    note(p)
            for p in shown:
                self.recent_plays = (self.recent_plays + [p])[-20:]
                self.text_messages.append((t, "play", f"{p.title} : {p.reason}"))
                fx = getattr(self, "_play_fx", None)
                if fx is None and sys.platform == "win32" and self._running:
                    from treeaicoach.fx_overlay import PlayFx

                    fx = self._play_fx = PlayFx(self._cfg, self._screen_rects,
                                                lambda: self._overlay_visible and self._in_game)
                if fx is not None:
                    fx.apply_config(self._cfg)
                    fx.push(p)
        except Exception:
            self._errors += 1
            self._err.exception("Play ratings failed")

    def plays_summary(self) -> dict | None:
        """Counts per rating class + "précision" 0-100 of the current / last game (plays.summarize)."""
        pc = getattr(self, "_plays", None)
        try:
            return pc.summary() if pc is not None and pc.history() else None
        except Exception:
            return None

    def _ai_in_fight(self) -> bool:
        for attr in ("_fight", "_fight_tracker"):
            ft = getattr(self, attr, None)
            fn = getattr(ft, "in_fight", None)
            if fn is not None:
                try:
                    return bool(fn() if callable(fn) else fn)
                except Exception:
                    return False
        return False

    def ask_ai(self) -> str:
        """"Demander à l'IA" (hotkey / button): manual AI request, answer later as toast + HUD line.

        Returns a French acknowledgement (also shown as a toast). Never raises, never blocks."""
        try:
            from treeaicoach.ai_advisor import AIAdvisor, engine_context

            ai = getattr(self, "_ai", None)
            if ai is None:
                ai = self._ai = AIAdvisor(self._cfg, clock=self._clock)
            ai.apply_config(self._cfg)
            now = self._clock()
            with self._lock:
                game = self._game if self._in_game else None
            msg = ai.ask(now, game, roles=self._role_resolver, scoreboard=self.scoreboard_summary(),
                         objectives=self._objectives.states() if self._objectives is not None else [],
                         item_text=self.item_advice_text(), context=lambda: engine_context(self, now))
            self.last_ai_ack = msg
            self._toast("insight", "IA", msg, None, f"ai-ask:{now:.0f}", now)
            return msg
        except Exception:
            self._err.exception("ask_ai failed")
            return "Conseil IA indisponible."

    def _ai_postgame_review(self, record: Path, html: Path) -> None:
        """AI review of the finished game appended to the HTML report (provider configured). Never raises."""
        try:
            cfg = self._cfg
            if str(getattr(cfg, "ai_provider", "off") or "off") == "off" or self._demo:
                return
            import json

            from treeaicoach.ai_advisor import append_review_html, postgame_review
            from treeaicoach.analysis import analyze_game

            data = json.loads(Path(record).read_text(encoding="utf-8"))
            review = postgame_review(cfg, analyze_game(data))
            if review and append_review_html(html, review, str(cfg.ai_provider)):
                self.last_ai_review = review
                log.info("AI post-game review added to %s", html)
        except Exception:
            log.exception("AI post-game review failed")

    def win_probability(self) -> float | None:
        """Live probability (0..1) that my team wins (hype.py model), None outside a game."""
        hc = getattr(self, "_hype", None)
        return hc.win_probability() if hc is not None and self._in_game else None

    def hype_stats(self) -> dict:
        """Win-probability statistics of the current / last game (shareable summary)."""
        hc = getattr(self, "_hype", None)
        return hc.stats() if hc is not None else {}

    def ai_status(self) -> tuple[int, str | None]:
        """``(sequence, French error)`` of the optional AI advisor (the sequence changes per new error)."""
        ai = getattr(self, "_ai", None)
        return ai.status() if ai is not None else (0, None)

    def ai_budget(self) -> dict | None:
        """Per-game AI counters (``auto_used / auto_max``, ``urgent_used``, ``manual``), None when off."""
        ai = getattr(self, "_ai", None)
        try:
            return ai.budget_info() if ai is not None and ai.enabled else None
        except Exception:
            return None

    def ai_budget_text(self) -> str:
        """"IA 3/5" (empty when the AI advice is off)."""
        from treeaicoach.ai_advisor import budget_text

        return budget_text(self.ai_budget())

    def play_gauge(self) -> Any:
        """The "jouer plus fort ou non" gauge (:class:`treeaicoach.coach.Gauge`), None outside a game."""
        g = self._gauge
        return g.current() if g is not None and self._in_game else None

    def coach_extras(self) -> dict:
        """Coaching extras of the current / last game for the UI and the report (coach_plus.py):
        ``goal`` (label), ``goal_status`` ("en cours" / "réussi" / "raté"), ``plan`` (matchup card
        lines), ``death_causes`` (cause keys of my deaths). {} before the first game. Never raises."""
        try:
            plus = getattr(self, "_coach_plus", None)
            if plus is None:
                return {}
            g = plus.goals.goal
            card = plus.card
            return {"goal": g.label if g is not None else None, "goal_status": plus.goals.status,
                    "plan": list(card.lines) + ([card.jungle] if card is not None and card.jungle else [])
                    if card is not None else [],
                    "death_causes": plus.death_causes()}
        except Exception:
            return {}

    def top_tip(self) -> tuple[str, str] | None:
        """``(text, tone)`` of the ONE written advice shown on the HUD right now, else None."""
        try:
            text = self._hud_line(self._clock())
            return (text, self._tip_tone(text)) if text else None
        except Exception:
            return None

    def _is_go_line(self, text: str | None) -> bool:
        """A "play harder" line (tone "go" or its wording): hidden under a PRUDENT / SAFE gauge
        and during a base siege / ace."""
        if not text:
            return False
        low = text.casefold()
        return self._tip_tone(text) == "go" or any(w in low for w in GO_WORDS)

    def _tip_tone(self, text: str | None) -> str:
        """Tone of the HUD line ("danger" / "warning" / "go" / "info")."""
        if not text:
            return "info"
        rot = self._tip_rotator
        tip = rot.current_tip() if rot is not None else None
        if tip is not None and text == self._tip_text:
            return str(getattr(tip, "tone", "info") or "info")
        low = text.casefold()
        if any(w in low for w in ("recule", "danger", "gank", "rentre", "fuis", "ta base", "ace :")):
            return "danger"
        if any(w in low for w in ("attention", "prudent", "évite", "safe")):
            return "warning"
        return "go" if any(w in low for w in GO_WORDS) else "info"

    def detected_role(self) -> tuple[str | None, str | None]:
        """``(my role short name, swap notice)`` for the dashboard, e.g. ``("MID", None)``."""
        try:
            from treeaicoach.roles import ROLE_SHORT

            game = self._game
            res = self._role_resolver
            role = None
            if res is not None and hasattr(res, "my_role"):
                role = res.my_role()
            if role is None and game is not None and game.me is not None:
                role = getattr(game.me, "position", None) or None
            return (ROLE_SHORT.get(role, role) if role else None), self._role_notice(self._clock())
        except Exception:
            return None, None

    def item_advice_text(self) -> str | None:
        """Current build advice line for the UI / HUD ("Prochain objet : ..."), None if none."""
        adv = getattr(self, "_item_adv", None)
        rec = adv.current() if adv is not None and getattr(self._cfg, "item_advice", True) else None
        return rec.text if rec is not None else None

    def _stance_and_tips(self, t: float, game: GameInfo, threat: int) -> list[Alert]:
        """Stance (HUD pill, spoken on change) + rotating written tip. Never raises."""
        out: list[Alert] = []
        try:
            facts = self._coach.facts() if self._coach is not None else {}
            summary = self.scoreboard_summary()
            plus = self._coach_plus_tick(t, game, facts, threat)
            if self._stance is not None:
                extra_f = list(plus.factors() if plus is not None else []) + self._macro_factors()
                out += list(self._stance.update(t, facts, game, summary, threat=threat, extra=extra_f) or [])
            if self._gauge is not None:
                st = self._stance.current() if self._stance is not None else None
                tac = self._tactics
                fs = tac.fight.state() if tac is not None else None
                me = getattr(game, "me", None)
                alive = me is not None and not bool(getattr(me, "is_dead", False))
                self._gauge.update(t, st, fs, summary, threat,
                                   active=alive and (st is not None or bool(getattr(fs, "active", False))))
            rot = self._tip_rotator
            if rot is not None and getattr(self._cfg, "text_tips", True):
                from treeaicoach.tips import build_context

                stance = self._stance.current() if self._stance is not None else None
                prev_id = rot.current_id()
                adv = getattr(self, "_item_adv", None)
                rec = adv.current() if adv is not None else None
                item = getattr(rec, "item_name", None) if rec is not None else None
                extra: dict = {}
                if plus is not None:
                    from treeaicoach.coach_plus import buy_fields
                    extra = {**plus.tip_fields(), **buy_fields(rec, bool(facts.get("in_base")))}
                extra.update(self._tip_consistency_fields(t))
                self._tip_text = rot.update(t, build_context(facts, game, summary, stance, item=item, extra=extra))
                # a toast only for a NEW tip (its live numbers refreshing is not news)
                # (and at most one tip toast every TIP_TOAST_GAP_S, contextual tips only: the HUD line
                # already shows every tip, the toast is a beginner's extra nudge)
                tip_now = rot.current_tip()
                if self._tip_text and rot.current_id() != prev_id and getattr(self._cfg, "tip_toasts", False) \
                        and int(getattr(tip_now, "prio", 1) or 1) >= 3 \
                        and t - getattr(self, "_tip_toast_t", -math.inf) >= TIP_TOAST_GAP_S:
                    self._tip_toast_t = t
                    self._toast("insight", "ASTUCE", self._tip_text, None, f"tip:{rot.current_id()}", t)
            else:
                self._tip_text = None
        except Exception:
            self._errors += 1
            self._err.exception("Stance / tips failed")
        return out

    # ------------------------------------------------------------------ cross-system consistency (V2 audit)
    RECALL_KEYS = ("recall_gold",)
    RECALL_TIP_IDS = frozenset({"gold_back", "comp_ready", "wave_push_back", "obj_recall_now"})
    RECALL_TOPIC_S = 120.0

    def _macro_factors(self) -> list[tuple[float, str]]:
        """The active COUP DE GÉNIE call as a gauge reason, so the gauge and the call never
        disagree ("Plaque la tour" while the gauge says SAFE): +2 for a "go" call, -2 for a
        "recule" call. Never raises."""
        try:
            tac = self._tactics
            c = tac.macro_active() if tac is not None else None
            if c is None:
                return []
            w = {"safe": 2.0, "danger": -2.0}.get(getattr(c, "color", ""), 0.0)
            return [(w, f"appel : {str(c.title).rstrip(' !').lower()}")] if w else []
        except Exception:
            log.debug("macro gauge factor failed", exc_info=True)
            return []

    def _tip_consistency_fields(self, t: float) -> dict:
        """TipContext fields that keep the written tip in line with the other systems: the tone of
        the active macro call (a "go" call hides the cautious tips and vice versa) and whether a
        recall reminder was shown recently (one recall message per trip, not four)."""
        out: dict = {}
        try:
            tac = self._tactics
            c = tac.macro_active() if tac is not None else None
            if c is not None:
                out["macro_tone"] = {"safe": "go", "danger": "danger"}.get(getattr(c, "color", ""))
                if getattr(c, "kind", "") == "wave_recall":
                    self._recall_topic_t = t
            last = getattr(self, "_recall_topic_t", None)
            out["recall_said"] = last is not None and 0.0 <= t - last < self.RECALL_TOPIC_S
        except Exception:
            log.debug("tip consistency failed", exc_info=True)
            pass
        return out

    def _recall_consistency(self, alerts: list[Alert], t: float) -> list[Alert]:
        """Recall reminders ("Tu as 1300 pièces d'or, pense à rentrer") are dropped when another
        system already said it (macro "rentre" call, a recall tip on screen) or while a macro call
        asks for something else; any shown one marks the recall topic. Never raises."""
        try:
            keep = []
            rot = self._tip_rotator
            tip_id = rot.current_id() if rot is not None else None
            tac = self._tactics
            active = tac.macro_active() if tac is not None else None
            for a in alerts:
                if str(a.key).startswith(self.RECALL_KEYS):
                    last = getattr(self, "_recall_topic_t", None)
                    if (last is not None and 0.0 <= t - last < self.RECALL_TOPIC_S) or tip_id in self.RECALL_TIP_IDS \
                            or active is not None:
                        continue
                    self._recall_topic_t = t
                keep.append(a)
            return keep
        except Exception:
            log.debug("recall consistency failed", exc_info=True)
            return alerts

    def _coach_plus_tick(self, t: float, game: GameInfo, facts: dict, threat: int) -> Any:
        """coach_plus.CoachPlus (power spikes, matchup card, session goal, death cause): its toasts
        (filtered by the player's level, held during a gank / fight) + the object for the gauge /
        tips. Visual only. Never raises."""
        try:
            plus = getattr(self, "_coach_plus", None)
            if plus is None:
                from treeaicoach.coach_plus import CoachPlus
                plus = self._coach_plus = CoachPlus()
            from treeaicoach import skill
            tac = self._tactics
            busy = threat >= Level.WARNING or (tac is not None and tac.in_fight())
            gt = _finite(facts.get("gt")) or _finite(getattr(game, "game_time", None)) or 0.0
            notes = plus.update(t, gt, game, facts, tac.map_state() if tac is not None else None,
                                busy=busy, min_prio=skill.tip_min_prio(self._cfg))
            for n in notes:
                if n.kind == "praise" and not getattr(self._cfg, "praise_enabled", True):
                    continue
                self._toast(n.kind, n.title, n.text, None, n.key, t)
                if n.hud:
                    self._text_msg = (t, n.text)
                    self.text_messages.append((t, "coach_plus", n.text))
                    del self.text_messages[:-100]
            return plus
        except Exception:
            self._errors += 1
            self._err.exception("Coach extras failed")
            return None

    def _route_messages(self, alerts: list[Alert], t: float, gt: float) -> list[Alert]:
        """Voice policy: returns the alerts to SPEAK (through the throttler); the others are
        written (HUD line + toast), all of them anti-spam gated. Never raises."""
        try:
            from treeaicoach import voice_policy as vp
        except Exception:
            return alerts
        level = getattr(self._cfg, "voice_level", vp.DEFAULT_VOICE_LEVEL)
        gate = self._gate
        tac = self._tactics
        ctx = tac.speech_context() if tac is not None else None
        voice: list[Alert] = []
        for a in alerts:
            try:
                way = tac.gate.decide(a, t, ctx, level) if tac is not None else vp.route(a, level)
                if way == "drop":
                    continue
                if way == "voice":
                    if gate is None or gate.check(a, t):
                        voice.append(a)
                    continue
                if gate is not None and not gate.allow(a, t):
                    continue
                self._write_text(a, t, gt)
            except Exception:
                self._err.exception("Message routing failed")
        return voice

    def _write_text(self, a: Alert, t: float, gt: float) -> None:
        """A written-only message: HUD line + toast (by kind) + record."""
        from treeaicoach import voice_policy as vp

        kind = vp.kind_name(a)
        self._text_msg = (t, a.text)
        self.text_messages.append((t, kind, a.text))
        del self.text_messages[:-100]
        toast = vp.TEXT_TOAST.get(kind)
        banner = self._tactics.banner(t) if self._tactics is not None else None
        if toast is not None and not (banner is not None and banner.subtitle == a.text):
            self._toast(toast[0], toast[1], a.text, a.alias, f"text:{a.key}", t)
        rec = self._recorder
        if rec is not None:
            rec.on_alert(a, gt)

    def _hud_line(self, now: float) -> str | None:
        """The ONE written HUD line: a fresh written-only message (10 s), else an urgent live
        insight of the coach, else the rotating tip, else the coach / Tab line. A line stays at
        least :data:`HUD_DWELL_S` (readable) while it is still valid, unless the new one is a danger."""
        valid: list[str] = []
        cand: str | None = None
        msg = self._text_msg
        if msg is not None and 0.0 <= now - msg[0] < TEXT_MSG_S:
            valid.append(msg[1])
        mc = self._tactics.macro_active() if self._tactics is not None else None
        if mc is not None and mc.text not in valid and (msg is None or msg[0] <= mc.t or now - msg[0] >= TEXT_MSG_S):
            valid.insert(0, mc.text)                  # an active macro call keeps the line while it is valid
        coach = self._coach
        # before the minions (1:05) the lane-phase advice makes no sense (seen in a real game:
        # "Joue agressif avant le niveau 6" in the fountain at 0:26): written messages / calls only
        game = self._game
        early = False
        try:
            if game is not None:
                gt_now = (_finite(game.game_time) or 0.0) + min(max(0.0, now - self._game_t), 3.0)
                early = gt_now < EARLY_ADVICE_GT_S
        except Exception:
            early = False
        me_dead = bool(getattr(getattr(game, "me", None), "is_dead", False)) if game is not None else False
        if me_dead:      # dead: the respawn countdown + the death cause / active call only
            early = True
        siege, siege_line = self._siege(now)
        if siege is not None:   # ace / siege: that line first, no "à toi de jouer", no tip
            valid = [siege_line] + [v for v in valid if not self._is_go_line(v)]
            early = True
        if coach is not None and not early:
            try:
                urgent = [it for it in coach.insight_items() if it[0] >= 65 and it[2] != "objective"]
                valid += [it[1] for it in urgent[:1]]
            except Exception:
                pass
        if self._tip_text and not early:
            valid.append(self._tip_text)
        # never contradict the gauge: no "à toi de jouer" line under a PRUDENT / SAFE gauge
        try:
            g = self._gauge.current() if self._gauge is not None else None
            if g is not None and int(g.step) <= -1 and len(valid) > 0:
                valid = [v for v in valid if not self._is_go_line(v)]
        except Exception:
            pass
        cand = valid[0] if valid else None
        shown = getattr(self, "_hud_shown", None)
        try:
            if cand is not None and shown is not None and shown[0] != cand and 0.0 <= now - shown[1] < HUD_DWELL_S \
                    and shown[0] in valid and self._tip_tone(cand) != "danger":
                return shown[0]
        except Exception:
            pass
        if shown is None or shown[0] != cand:
            self._hud_shown = (cand, now)
        return cand

    def _topic_seen(self, key: str, t: float) -> bool:
        """One toast per subject (voice_policy.topic_of): True when this topic was already shown in
        the last TOPIC_TOAST_S seconds (the toast is then dropped; the HUD line still updates).
        Danger / praise toasts are never deduplicated here. Records the topic otherwise."""
        try:
            from treeaicoach import voice_policy as vp

            topic = vp.topic_of(key)
            if topic is None:
                return False
            seen = getattr(self, "_topic_t", None)
            if seen is None:
                seen = self._topic_t = {}
            last = seen.get(topic)
            if last is not None and 0.0 <= t - last < vp.TOPIC_TOAST_S:
                return True
            seen[topic] = t
            return False
        except Exception:
            log.debug("toast topic failed", exc_info=True)
            return False

    def _toast(self, kind: str, title: str, subtitle: str, alias: str | None, key: str, t: float) -> None:
        q = self._toasts
        if q is None or not getattr(self._cfg, "toasts_enabled", True):
            return
        if kind not in ("danger", "praise") and self._topic_seen(key, t):
            return
        icon = None
        if alias:
            skin = 0
            game = self._game
            p = game.player_by_alias(alias) if game is not None else None
            if p is not None:
                skin = p.skin_id
            icon = self._icon(alias, skin)
        q.push(kind, title, subtitle, icon=icon, key=key, t=t)

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

    def _vision(self, frame: np.ndarray) -> list[Any]:
        """Detector + identifier (+ camera fallback for "self")."""
        try:
            self._ensure_detector()
            status = getattr(self._detector, "set_game_status", None)
            if callable(status):        # dead champions: never searched on the map
                status(self._game)
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

    # ------------------------------------------------------------------ my icon / HUD
    def _icon_learner(self) -> Any:
        """The roster matcher's icon learner (self_icon.IconLearner), or None."""
        return getattr(getattr(self._detector, "matcher", None), "learner", None)

    def my_observed_lane(self) -> str | None:
        """Lane ("top" / "mid" / "bot") where MY icon was seen laning (1:30-10:00), from the
        icon learner (works with custom skins), or None. Never raises."""
        try:
            lr = self._icon_learner()
            return lr.observed_lane() if lr is not None else None
        except Exception:
            return None

    def _self_icon_tick(self, t: float, gt: float, game: Any) -> None:
        """2 Hz: HUD portrait (dead flag, skin guess), dead players and my lane occupancy for
        the icon learner; hooks my observed lane into roles.RoleResolver. Never raises."""
        if t < self._selficon_next and t >= self._selficon_next - 1.0:
            return
        self._selficon_next = t + SELF_ICON_PERIOD_S
        try:
            matcher = getattr(self._detector, "matcher", None)
            lr = getattr(matcher, "learner", None)
            if lr is None:
                return
            hud = self._read_hud(t, game) if self._frame_source is None else None
            if hud is not None:
                lr.feed_hud(hud.portrait, hud.dead)
                if lr.skin_guesser is None and not self._demo:
                    from treeaicoach.self_icon import SkinGuesser

                    lr.skin_guesser = SkinGuesser(self._champion_db(), allow_network=bool(
                        getattr(self._cfg, "download_skin_icons", True)))
            matcher.set_status(game, me_dead=hud.dead if hud is not None else None, game_time=gt)
            lr.observe_lane(t, gt)
            res = self._role_resolver
            if res is not None and getattr(res, "my_lane_hook", False) is None:
                res.my_lane_hook = self.my_observed_lane
        except Exception:
            self._err.exception("Self icon tick failed")

    def _read_hud(self, t: float, game: Any) -> Any:
        """HUD portrait read (hud_reader.HudReader): one full-window grab to calibrate (per
        window size, retried every 15 s), then only the small portrait patch. None if unknown."""
        win = self._window
        if win is None:
            return None
        if self._hud_reader is None:
            from treeaicoach.hud_reader import HudReader

            self._hud_reader = HudReader()
        hr = self._hud_reader
        size = (win.w, win.h)
        cal_t, cal_size = self._hud_cal
        if cal_size != size:
            if t - cal_t < 15.0 and cal_t <= t:
                return None
            self._hud_cal = (t, None)
            screen = self._grabber().grab(win)
            if screen is None or is_black_frame(screen) or not hr.calibrate(screen):
                return None
            self._hud_cal = (t, size)
            log.info("HUD portrait found at %s", hr.location)
        roi = hr.roi()
        if roi is None:
            return None
        x, y, w, h = roi
        patch = self._grabber().grab(Rect(win.x + x, win.y + y, w, h))
        alive = None
        me = getattr(game, "me", None)
        if me is not None:
            alive = not bool(getattr(me, "is_dead", False))
        return hr.read_patch(patch, alive_hint=alive)

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
        dead = self._dead_aliases()     # a dead champion has no icon: never relabel to him
        enemy_tracks = [tr for tr in tracker.enemies(visible_only=False)
                        if tr.alias and tr.alias not in dead]
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

    def _dead_aliases(self) -> set[str]:
        """Champions dead right now (roster matcher's respawn-timed view, else the Live API)."""
        try:
            m = getattr(self._detector, "matcher", None)
            if m is not None and getattr(m, "has_roster", False):
                return set(getattr(m, "last_dead", None) or ())
            game = self._game
            return {p.champion_alias for p in game.all_players() if p.is_dead} \
                if game is not None else set()
        except Exception:
            return set()

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

    def _personal_danger(self, t: float, gt: float, game: GameInfo, tracker: Any, threat: int) -> list[Alert]:
        """Personal danger (danger.py): my HP / level / items vs the visible enemies on me, lane
        opponent and on-screen enemies included (written warning, spoken "Recule !" when low).
        Goes through the gank fast path (latency first). Never raises."""
        if getattr(self._cfg, "safe_mode", False):
            return []
        try:
            pd = getattr(self, "_danger", None)
            if pd is None:
                from treeaicoach.danger import PersonalDanger

                pd = self._danger = PersonalDanger()
            roles = self._role_resolver
            lane = list(roles.lane_opponents() or ()) if roles is not None else []
            jg = roles.enemy_jungler() if roles is not None else None
            if not jg and game.enemy_jungler() is not None:
                jg = game.enemy_jungler().champion_alias
            tac = self._tactics
            fog = self._fog.estimates() if self._fog is not None else []
            return pd.update(t, gt, game, tracker, lane_opponents=lane, jungler=jg, threat=threat,
                             gank_danger_t=self._last_danger_t,
                             in_fight=bool(tac is not None and tac.in_fight()), fog=fog)
        except Exception:
            self._err.exception("Personal danger failed")
            return []

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
        info = None
        try:
            if self._window_finder is not None:
                win = self._window_finder()
            else:
                from treeaicoach.capture import game_window_info

                info = game_window_info()
                win = info.rect if info is not None else None
        except Exception:
            self._err.exception("find_game_window failed")
            win = None
        self._win_info = info
        # focus: the overlay hides and the detection slows down while the game is not in the
        # foreground (alt-tab); our own windows (settings, preview) do not count as "away"
        focused = info is None or info.foreground or info.own_foreground
        if focused or not getattr(self._cfg, "pause_when_unfocused", True):
            self._unfocused_since = None
        elif self._unfocused_since is None:
            self._unfocused_since = t
        self._paused = "minimized" if (info is not None and info.minimized) else None
        if win != self._window:
            old = self._window
            if win is not None and old is not None and (win.w, win.h) != (old.w, old.h):
                self._relocate = True
            elif win is not None and old is not None and self._minimap_rect is not None \
                    and self._rect_window == old and (win.x, win.y) != (old.x, old.y):
                # window moved (same size): the minimap moved with it, no new search
                self._minimap_rect = self._minimap_rect.offset(win.x - old.x, win.y - old.y)
                self._rect_window = win
                log.info("Game window moved: minimap rect now %s", self._minimap_rect)
            self._window = win
        return win

    def _grabber(self) -> Any:
        if self._diag_req.pop("recreate_capture", False) and self._capture is not None:
            try:
                self._capture.close()
            except Exception:
                pass
            self._capture = None
        if self._capture is None:
            from treeaicoach.capture import SmartCapture

            self._capture = SmartCapture(str(getattr(self._cfg, "capture_backend", "auto") or "auto"))
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
        gs, hint, hint_key = None, None, None
        try:
            gs = self._settings_watcher.get() if self._settings_watcher is not None else None
            if side == "auto" and gs is not None and gs.minimap_side():
                side = gs.minimap_side()          # FlipMiniMap from the game's own settings
            if self._rect_cache is not None:
                hint_key = self._rect_cache.key(win.w, win.h, gs)
                hint = self._rect_cache.get(hint_key)
        except Exception:
            log.debug("Game settings prior failed", exc_info=True)
        self._set_state(EngineState.LOCATING, MSG_LOCATING)
        loc = None
        try:
            screen = self._grabber().grab(win)
            if screen is not None and not is_black_frame(screen):
                locator = self._ensure_locator()
                try:
                    loc = locator.locate(screen, win, side=side, hint=hint)
                except TypeError:                 # a locator without the hint parameter
                    loc = locator.locate(screen, win, side=side)
            elif screen is not None:
                self._set_state(EngineState.CAPTURE_BLACK, MSG_BLACK)
        except Exception:
            self._err.exception("Minimap location failed")
        self._rect_window = win
        if loc is not None:
            self._minimap_rect, self._locate_method = loc.rect, "auto"
            log.info("Minimap located at %s (score %.2f)", loc.rect, loc.score)
            if hint_key is not None:
                self._rect_cache.put(hint_key, loc.rect.x - win.x, loc.rect.y - win.y,
                                     loc.rect.w, loc.rect.h, loc.score)
            return
        from treeaicoach.minimap_locator import fallback_rect

        fb_side = "left" if side == "left" else "right"
        self._minimap_rect, self._locate_method = fallback_rect(win, fb_side), "fallback"
        self._next_locate = t + LOCATE_RETRY_S
        log.info("Minimap not found: fallback rectangle %s", self._minimap_rect)

    def _grab_minimap(self, t: float, gt: float | None = None) -> np.ndarray | None:
        win = self._find_window(t)
        if win is None:
            self._set_state(EngineState.LOCATING, MSG_MINIMIZED if self._paused else MSG_NO_WINDOW)
            return None
        self._settings_changed_check()
        if self._relocate or self._minimap_rect is None or self._rect_window != win or (
                self._locate_method == "fallback" and t >= self._next_locate):
            self._locate(t, win)
        rect = self._minimap_rect
        if rect is None:
            return None
        self._occluded = bool(self._occlusion(rect))
        if self._occluded:
            # another window (League client, browser...) covers the minimap: its pixels must never
            # become detections; the tick is frozen (tracks keep their state) until it is visible
            self._set_state(EngineState.RUNNING, MSG_OCCLUDED)
            return None
        cap = self._grabber()
        frame = _as_bgr(cap.grab(rect))
        if frame is None:
            self._set_state(EngineState.RUNNING, MSG_NO_FRAME)
            return None
        check = getattr(cap, "check", None)
        if callable(check):      # black / frozen frames -> other capture backend (capture.SmartCapture)
            try:
                st = check(frame, t, rect, allow_stale=gt is not None and gt >= STALE_MIN_GAME_S)
            except Exception:
                st = "ok"
            self._capture_status = st
            if st == "switched":
                frame = _as_bgr(cap.grab(rect))
                if frame is None:
                    return None
            elif st == "black":
                self._set_state(EngineState.CAPTURE_BLACK, MSG_BLACK)
                return None
            elif st == "stale":
                self._capture_note = MSG_FROZEN
        verify_due = t >= self._next_verify
        if verify_due and self._bad_since is None and self._heavy_now and not self._verify_due_deferred:
            # keep the verification off the tick running a coaching slot (no spike); next tick
            self._verify_due_deferred = True
            verify_due = False
        if self._locate_method == "auto" and verify_due:
            self._verify_due_deferred = False
            self._next_verify = t + self._budget.profile.verify_s
            try:
                from treeaicoach.minimap_locator import VERIFY_MIN_SCORE

                score = float(self._ensure_locator().verify(frame))
            except Exception:
                self._err.exception("Minimap verify failed")
                score = 1.0
            self._minimap_score = score
            if score < VERIFY_MIN_SCORE:
                if self._bad_since is None:
                    self._bad_since = t
                elif t - self._bad_since >= VERIFY_BAD_S:
                    log.info("Minimap verification low (%.2f) for %.0f s: relocating", score, VERIFY_BAD_S)
                    self._relocate = True
            else:
                self._bad_since = None
            if self._bad_since is not None:
                # the crop does not look like the minimap (shop / scoreboard over it, scale
                # being changed...): no detection on it (phantoms), checked again next tick
                self._next_verify = t
                self._set_state(EngineState.RUNNING, MSG_MINIMAP_COVERED)
                return None
        self._set_state(EngineState.RUNNING,
                        MSG_FALLBACK if self._locate_method == "fallback" else MSG_RUNNING)
        return frame

    def _occlusion(self, rect: Rect) -> bool | None:
        """Is the minimap covered by another window? (``occlusion_probe`` for tests; live
        capture only). Never raises."""
        probe = self.occlusion_probe
        try:
            if probe is not None:
                return probe(rect)
            if self._window_finder is not None or self._frame_source is not None:
                return False
            from treeaicoach.capture import rect_occluded

            info = self._win_info
            return rect_occluded(rect, game_hwnd=info.hwnd if info is not None else None)
        except Exception:
            return False

    def _settings_changed_check(self) -> None:
        """The game's own settings changed (minimap scale, flip, resolution, HUD scale): the
        minimap moved / was resized -> locate it again (cheap: SettingsWatcher re-reads the
        files only when their mtime changed, at most every 10 s)."""
        w = self._settings_watcher
        if w is None:
            return
        try:
            gs = w.get()
            fp = gs.fingerprint() if gs is not None else None
        except Exception:
            return
        old = getattr(self, "_settings_fp", None)
        self._settings_fp = fp
        if old is not None and fp is not None and fp != old:
            log.info("Game display settings changed (%s -> %s): relocating the minimap", old, fp)
            self._relocate = True

    def _fullscreen_check(self, game: Any = None) -> None:
        """Exclusive fullscreen (WindowMode 0 in game.cfg): layered overlay windows cannot show
        over it and screen capture may be black -> clear French warning (status + log)."""
        w = self._settings_watcher
        if w is None:
            return
        try:
            gs = w.get()
            if gs is not None and gs.exclusive_fullscreen:
                self._capture_note = MSG_FULLSCREEN
                log.warning("Game in exclusive fullscreen (WindowMode=0): overlay invisible, capture may be black")
        except Exception:
            log.debug("fullscreen check failed", exc_info=True)

    # ================================================================== health / diagnostics (v2)
    def overlay_paused(self, now: float | None = None) -> bool:
        """True while the overlay must hide: game minimized, or not in the foreground for
        :data:`UNFOCUSED_HIDE_S` (our own windows excepted). Never raises."""
        try:
            if self._paused:
                return True
            since = self._unfocused_since
            now = self._clock() if now is None else float(now)
            return since is not None and now - since >= UNFOCUSED_HIDE_S
        except Exception:
            return False

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
                from treeaicoach import overlay as _ov   # stats published by the overlay thread

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
            if self.overlay_paused(now):     # minimized / alt-tabbed: never draw over other apps
                return None
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
        dead = {p.champion_alias for p in roster if getattr(p, "is_dead", False)}
        for v in enemies:              # dead enemies: no ghost on the map (HUD row shows the timer)
            if v.alias in dead and hasattr(v, "dead"):
                v.dead = True
        allies, roles = self._overlay_allies_roles(EnemyView, game, tracker, now)
        for v in enemies:
            v.role = roles.get(v.key) or roles.get(v.alias or "")
        level = max((lvl for t_, lvl, _a in hist if now - t_ <= THREAT_HOLD_S), default=0)
        top = max((a for t_, lvl, a in hist if now - t_ <= THREAT_HOLD_S and lvl == level),
                  key=lambda a: a.t, default=None)
        siege, _siege_line = self._siege(now)
        if siege is not None:           # base siege / ace dominate: never "SÛR" while the base falls
            level = max(level, int(Level.DANGER))
        if siege == "ace":
            text = "DANGER — ACE"
        elif siege == "siege":
            text = "DANGER — TA BASE EST ATTAQUÉE"
        elif level >= Level.DANGER:
            text = "DANGER — GANK !"
        elif level == Level.WARNING:
            who = self._display_name(game, top.alias) if top is not None else None
            text = f"ATTENTION — {who} approche" if who else "ATTENTION — ennemi proche"
        else:
            text = "SÛR"
        flash = 0.0
        tac = self._tactics
        fighting = tac is not None and tac.in_fight()
        if cfg.danger_flash and last_danger is not None and 0.0 <= now - last_danger < FLASH_DECAY_S and not fighting:
            flash = float(1.0 - (now - last_danger) / FLASH_DECAY_S)
        la = None
        if last_alert is not None and last_alert_t is not None:
            la = (last_alert.text, int(last_alert.level), max(0.0, now - last_alert_t))
        minimap_rect, screen_rect = self._screen_rects()
        guides, world = self._ward_overlay(now, tac, minimap_rect, screen_rect, me_uv)
        tip = self._hud_line(now)
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
            insight=(self._coach.insight() if self._coach is not None else None) or self._scoreboard_hud_line(),
            tip=tip,
            stance=getattr(self._stance.current(), "level", None) if self._stance is not None else None,
            stance_reason=getattr(self._stance.current(), "reason", None) if self._stance is not None else None,
            show_allies=bool(getattr(cfg, "overlay_show_allies", False)),
            show_roles=bool(getattr(cfg, "overlay_show_roles", False)) or bool(getattr(cfg, "layer_roles", False)),
            show_ghosts=bool(getattr(cfg, "overlay_show_ghosts", False)) or bool(getattr(cfg, "layer_ghosts", False)),
            show_last_seen=bool(getattr(cfg, "overlay_show_last_seen", True)),
            hud_detailed=bool(getattr(cfg, "hud_detailed", False)),
            me_icon=self._icon(game.me.champion_alias, game.me.skin_id) if game and game.me else None,
            allies=allies, roles=roles,
            toasts=self._overlay_toasts(now),
            guides=guides, world=world,
            phase=tac.phase() if tac is not None else None,
            role_notice=self._role_notice(now),
            **self._hud_card_fields(game, me_uv, tip, now),
            **self._prediction_fields(me, game, game_t, now),
        )

    def _prediction_fields(self, me: Any, game: Any = None, game_t: float = 0.0,
                           now: float = 0.0) -> dict[str, Any]:
        """``predict`` / ``me_key`` (render-time positions) and ``me_dead`` / ``respawn_s`` (HUD
        dead state) of the overlay state, when the renderer supports them."""
        try:
            from treeaicoach.overlay_render import OverlayState

            names = {f.name for f in dataclasses.fields(OverlayState)}
            out: dict[str, Any] = {}
            if "predict" in names and self._motion is not None:
                out["predict"] = self.predict_positions
            if "me_key" in names:
                out["me_key"] = me.key if me is not None else None
            p = getattr(game, "me", None)
            if "me_dead" in names and p is not None and bool(getattr(p, "is_dead", False)):
                out["me_dead"] = True
                rt = _finite(getattr(p, "respawn_timer", None))
                if rt is not None and "respawn_s" in names:
                    out["respawn_s"] = max(0.0, rt - max(0.0, now - game_t))
            return out
        except Exception:
            return {}

    def _hud_card_fields(self, game: Any, me_uv: Any, tip: str | None, now: float) -> dict[str, Any]:
        """HUD v3 card extras: gauge (+ reason, since), advice tone + fade start, item chip (in
        base), AI counter. Times are converted to ``time.monotonic`` (the overlay's clock). Never raises."""
        out: dict[str, Any] = {}
        try:
            to_mono = time.monotonic() - now
            g = self._gauge.current() if self._gauge is not None else None
            if g is not None:
                out.update(gauge=int(g.step), gauge_reason=g.reason or None, gauge_since=float(g.since) + to_mono)
            if tip != self._hud_tip_prev:
                self._hud_tip_prev, self._hud_tip_since = tip, now
            if tip:
                out.update(tip_tone=self._tip_tone(tip), tip_since=self._hud_tip_since + to_mono)
            in_base = False
            if me_uv is not None and game is not None:
                z = geometry.classify_zone(*me_uv)
                in_base = geometry.is_base(z) and geometry.zone_owner(z) == game.my_team
            out["in_base"] = bool(in_base)
            adv = getattr(self, "_item_adv", None)
            rec = adv.current() if adv is not None and getattr(self._cfg, "item_advice", True) else None
            if rec is not None and not self._trivial_component_buy(rec, game, now):
                names = list(getattr(rec, "buy_now_names", ()) or ())
                out["item_hint"] = "Achète " + (" + ".join(names[:2]) if names and not rec.completes
                                                else rec.item_name)
            out["ai_counter"] = self.ai_budget_text() or None
        except Exception:
            log.debug("HUD card fields failed", exc_info=True)
        return out

    def _siege(self, now: float) -> tuple[str | None, str | None]:
        """Current base-siege / ace state (see :func:`siege_state`). Never raises."""
        game = self._game
        if game is None:
            return None, None
        try:
            gt = (_finite(game.game_time) or 0.0) + min(max(0.0, now - self._game_t), 3.0)
            n = 0
            tr = self._tracker
            if tr is not None:
                for e in tr.enemies(visible_only=False):
                    pos = e.position()
                    if pos is None or now - e.last_seen > 5.0:
                        continue
                    z = geometry.classify_zone(*pos)
                    if geometry.is_base(z) and geometry.zone_owner(z) == game.my_team:
                        n += 1
            return siege_state(game, gt, n)
        except Exception:
            return None, None

    def _trivial_component_buy(self, rec: Any, game: Any, now: float) -> bool:
        """After 20:00, a lone cheap component (< 500 gold, e.g. "Épée longue" with a near-complete
        build) that does not complete an item is not worth the HUD chip (real screenshot, 25:08)."""
        try:
            if getattr(rec, "completes", False) or game is None:
                return False
            gt = (_finite(game.game_time) or 0.0) + min(max(0.0, now - self._game_t), 3.0)
            if gt < TRIVIAL_BUY_AFTER_S:
                return False
            ids = list(getattr(rec, "buy_now", ()) or ())
            if not ids:
                return False
            from treeaicoach.itemization import load_items

            items = load_items()
            return all(i in items and int(items[i].gold) < TRIVIAL_BUY_GOLD for i in ids)
        except Exception:
            return False

    def _role_notice(self, now: float) -> str | None:
        """"Rôle détecté : MID (échange de voie)" for 20 s after a lane swap is detected. Never raises."""
        try:
            from treeaicoach.roles import ROLE_SHORT

            res = self._role_resolver
            sw = res.my_swap() if res is not None and hasattr(res, "my_swap") else None
            if sw is None or not (0.0 <= now - float(sw[1]) <= ROLE_NOTICE_S):
                return None
            return f"Rôle détecté : {ROLE_SHORT.get(sw[0], sw[0])} (échange de voie)"
        except Exception:
            return None

    def _overlay_toasts(self, now: float) -> list:
        """Toasts of the overlay, the director's big banner (live fight decision) on top."""
        views = list(self._toasts.active(now)) if self._toasts is not None else []
        tac = self._tactics
        if tac is None or not getattr(self._cfg, "toasts_enabled", True):
            return views
        try:
            b = tac.banner(now)
            if b is not None:
                from treeaicoach.toasts import banner_view

                v = banner_view(b, now)
                if v is not None:
                    views = [v] + views[:1]
        except Exception:
            log.debug("banner view failed", exc_info=True)
        return views

    def _scoreboard_hud_line(self) -> str | None:
        try:
            s = self.scoreboard_summary()
            line = s.hud_line() if s is not None else None
            wp = self.win_probability() if getattr(self._cfg, "win_prob_hud", True) else None
            if wp is not None:
                line = f"{line} · victoire {int(round(100 * wp))} %" if line else f"Victoire {int(round(100 * wp))} %"
            return line
        except Exception:
            return None

    def _overlay_allies_roles(self, cls: Any, game: GameInfo | None, tracker: Any,
                              now: float) -> tuple[list[Any], dict[str, str]]:
        """Allied views (roster order, then anonymous visible allies) + alias -> role map. Never raises."""
        allies: list[Any] = []
        roles: dict[str, str] = {}
        try:
            # resolved roles first (roles.RoleResolver: observed lanes beat the champ select
            # position after a lane swap), the Riot position only as a fallback
            resolver = getattr(self, "_role_resolver", None) or getattr(self, "_roles", None)
            extra = resolver.roles() if callable(getattr(resolver, "roles", None)) else resolver
            if isinstance(extra, dict):
                for k, info in extra.items():
                    role = getattr(info, "role", info)
                    if k and isinstance(role, str) and role:
                        roles[str(k)] = role
            for p in (game.all_players() if game is not None else []):
                if p.champion_alias and getattr(p, "position", "") and not roles.get(p.champion_alias):
                    roles[p.champion_alias] = str(p.position)
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
        view = cls(key=tr.key if tr is not None else (alias or "?"), alias=alias, name=name or (alias or "?"),
                   visible=visible, uv=uv, last_seen_ago=ago, is_jungler=is_jungler,
                   approaching=approaching, icon=self._icon(alias, skin), velocity=vel)
        if tr is not None:     # freshness (pipeline v2): stale / stacked / anonymous -> drawn as a ghost
            try:
                view.age = ago
                view.stacked = getattr(tr, "stacked_with", None) is not None
                view.confidence = 1.0 if tr.alias else 0.4
            except Exception:
                pass
        return view

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
        if bool(getattr(jungler, "is_dead", False)):     # Live Client: never "vu il y a 55 s" while dead
            rt = _finite(getattr(jungler, "respawn_timer", None))
            left = None if rt is None else max(0.0, rt - max(0.0, now - self._game_t))
            return f"Jungler : {name} — mort ({int(math.ceil(left))} s)" if left else f"Jungler : {name} — mort"
        tr = self._tracker.get(jungler.champion_alias) if self._tracker is not None else None
        if tr is None or tr.position() is None:
            return f"Jungler : {name} — pas encore vu"
        zone = geometry.zone_name_fr(geometry.classify_zone(*tr.position()), game.my_team)
        if tr.visible:
            return f"Jungler : {name} — visible, {zone}" if zone else f"Jungler : {name} — visible"
        ago = int(max(0.0, now - tr.last_seen))
        when = ("vu à l'instant" if ago < 2 else f"vu il y a {ago} s" if ago < 60
                else f"vu il y a {ago // 60}:{ago % 60:02d}")
        return f"Jungler : {name} — {when}, {zone}" if zone else f"Jungler : {name} — {when}"

    def jungle_intel(self) -> Any:
        """Enemy jungler Tab intel (``jungle_intel.JungleIntel``: farming side, recall, text
        line), or None. Never raises."""
        try:
            ji = self._jungle_intel
            return ji.state() if ji is not None and self._in_game else None
        except Exception:
            return None

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
