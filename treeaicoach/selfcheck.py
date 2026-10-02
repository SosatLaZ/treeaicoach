"""Self-diagnosis + automatic actions: TreeAI notices its own problems and fixes or explains them.

Real games fail in ways our tests never see (lag, an empty minimap, missed alerts, wrong
detections). :class:`SelfCheck` is a rule-based watchdog evaluated about once per second in game
by the analysis thread (rule 9 also from the Live Client poller outside a game). Every rule is::

    measured symptom  ->  automatic action  ->  short French status for the app

and, only when the player must act, at most ONE discreet in-game notice per problem per game
(through the engine's toast / presenter path, never during a fight, a gank or a siege).

====  ===========  ======================================  ========================================
 #     rule         symptom (measured)                      automatic action -> status
====  ===========  ======================================  ========================================
 1     capture      black / frozen capture >= 3 s           other backend, then a fresh capture
                                                            object; both fail -> "passe le jeu en
                                                            Sans bordure" (+ notice)
 2     minimap      fallback rect / verify score low        engine relocation (cheap hint first,
                    >= 3 s, or slowly drifting score        full search, backoff); 3 failures ->
                                                            "ouvre Réglages > Calibrer" (+ notice)
 3     perf         < 4 img/s analysed, or tick p95 >       load level normal -> allégé -> minimal
                    60 ms, for 20 s                         (ONNX extras rarer, ring / stack
                                                            proposals every Nth frame, overlay 15
                                                            img/s, coaching slower); back up after
                                                            60 s healthy
 4     champions    allies + me (always visible to my       recalibrate the icon scale once, reload
                    team) mostly unseen for 60 s            the icons, then "Détection faible" +
                                                            one automatic 60 s diagnostic per game
 5     identity     a champion at two far places within     that track / the extra anonymous
                    1 s (flip-flop), or more visible        enemy tracks are reset
                    enemies than alive ones
 6     overlay      game not in front, minimap covered     explained once (status)
                    by a window, overlay thread stopped
 7     voice        voice backend failing, synthesis of     dangers become beep-only
                    the alerts > 1.2 s
 8     ai           key refused / quota / repeated errors   no more AI calls this game (rule-based
                                                            plans continue)
 9     api          Live Client unreachable while the       slower retries, the game is kept alive
                    game window exists                      (mid-game outage), "coaching limité"
====  ===========  ======================================  ========================================

Hysteresis everywhere (a symptom must last ``*_ON_S`` to count and the health must last
``*_OFF_S`` to clear it), so statuses and actions never flap.

Pure logic on :class:`Snapshot` objects (unit-tested with fake engine states, see
``tests/test_selfcheck.py``); the engine glue (:func:`snapshot_from_engine`,
:func:`apply_actions`) is at the bottom of this module. Nothing here raises.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

RULES: tuple[str, ...] = ("capture", "minimap", "perf", "champions", "identity", "overlay", "voice", "ai",
                          "api", "adapt")
RULE_LABELS: dict[str, str] = {
    "capture": "Capture d'écran", "minimap": "Minimap", "perf": "Performance",
    "champions": "Détection des champions", "identity": "Identités des champions", "overlay": "Overlay",
    "voice": "Voix", "ai": "IA conseil", "api": "API du jeu", "adapt": "Adaptations automatiques",
}
#: Load levels of rule 3 (sysperf.PerfBudget.load_level): internal name, French label.
LOAD_NAMES: tuple[str, ...] = ("normal", "allege", "minimal")
LOAD_LABELS: dict[int, str] = {0: "normal", 1: "allégé", 2: "minimal"}
_ANALYSE: dict[int, str] = {0: "normale", 1: "allégée", 2: "minimale"}     # "analyse ..." (feminine)

# ------------------------------------------------------------------------------ tunables
EVAL_PERIOD_S = 1.0          # rules evaluated at most this often
EVENTS_MAX = 400             # session event log (diagnostic bundle)
GAME_EVENTS_MAX = 200        # per game (post-game report)
NOTICE_GAP_S = 90.0          # two in-game notices of the self-check at least this far apart
TICK_SAMPLES = 2048          # analysis tick costs kept (t, ms)
FRAME_SAMPLES = 2048         # analysed frame timestamps kept (detection rate, frames per minute)

CAPTURE_ON_S = 3.0           # 1: black / frozen this long -> other backend
CAPTURE_STEP_S = 6.0         # ... still bad this long after each step -> next step
CAPTURE_OFF_S = 5.0          # ... live frames this long -> cleared

MINIMAP_ON_S = 3.0           # 2: lost / covered / fallback this long -> problem
MINIMAP_OFF_S = 5.0
MINIMAP_FAILS = 3            # consecutive failed locations -> "ouvre Réglages > Calibrer"
#: game start (loading screen, opening fade): a minimap not found yet is not a problem before
#: this game time (the engine retries every 2 s meanwhile, see engine_capture.START_GRACE_GT_S;
#: real reports: "Minimap introuvable : ouvre Réglages > Calibrer" at 0:00 every game)
MINIMAP_START_GRACE_S = 90.0
DRIFT_ABS = 0.6              # verify score below this ...
DRIFT_REL = 0.75             # ... and below this fraction of the score at location ...
DRIFT_ON_S = 15.0            # ... for this long -> relocate (cheap hint first)
DRIFT_EVERY_S = 60.0
DRIFT_MAX = 3                # per game

STARVE_FPS = 4.0             # 3: analysed frames per second below min(this, FRAC x target) ...
STARVE_FRAC = 0.75
STARVE_P95_MS = 60.0         # ... or analysis tick p95 above this ...
STARVE_ON_S = 20.0           # ... for this long -> one load level down
HEALTHY_MARGIN = 1.2        # rate >= this x the starving floor and p95 below HEALTHY_P95_MS ...
HEALTHY_P95_MS = 40.0
HEALTHY_ON_S = 60.0          # ... for this long -> one load level up
HEALTHY_MAX_S = 600.0        # (doubled after each relapse within PERF_RELAPSE_S, up to this)
PERF_RELAPSE_S = 300.0       # starving again this soon after a step up = relapse (anti-flapping)
PERF_SETTLE_S = 30.0         # after a level change, judge again only after this
PERF_RATE_WINDOW_S = 5.0     # measured detection rate window
PERF_P95_WINDOW_S = 20.0     # tick p95 window
PERF_MIN_DETECTING_S = 6.0   # the detection must have been running this long to judge its rate

CHAMP_WINDOW_S = 60.0        # 4: allies + me seen in this window ...
CHAMP_MIN_WINDOW_S = 20.0    # (after an action: sightings since the action, at least this window)
CHAMP_MIN_GT_S = 90.0        # (not before 1:30: everybody leaves the fountain stacked)
CHAMP_MIN_EXPECTED = 3       # (me + at least 2 allies alive for the whole window)
CHAMP_BAD_FRAC = 0.4         # seen <= 40 % -> weak ...
CHAMP_GOOD_FRAC = 0.6        # ... seen >= 60 % -> fine
CHAMP_ON_S = 20.0
CHAMP_OFF_S = 20.0
CHAMP_STEP_S = 40.0          # next step of the ladder when still weak this long after the last one
CHAMP_MIN_FRAMES = 60        # frames analysed in the window needed to judge (>= 1 img/s)

FLIP_FAR = 0.2               # 5: a jump this far (normalized minimap units) ...
FLIP_NEAR = 0.06             # ... and back within this of the start ...
FLIP_LEG_S = 1.0             # ... each leg within this long = one champion at two places
IDENT_HISTORY_S = 3.0
IDENT_ALIAS_GAP_S = 5.0      # the same identity is reset at most this often
EXCESS_ON_S = 2.0            # more visible enemies than alive ones this long -> reset the extras
IDENT_CLEAR_S = 30.0         # no anomaly this long -> the identity note disappears
IDENT_UNSTABLE_N = 3         # this many resets within IDENT_UNSTABLE_S -> "dégradé"
IDENT_UNSTABLE_S = 120.0

UNFOCUSED_ON_S = 8.0         # 6: game not in front this long -> explained
OCCLUDED_ON_S = 5.0          # minimap covered by another window this long -> explained
OVERLAY_OFF_S = 2.0
OVERLAY_STALL_S = 5.0        # overlay thread frame counter frozen this long -> stopped

VOICE_SLOW_MS = 1200.0       # 7: synthesis of the alerts (p95) above this -> beep-only dangers
VOICE_OK_MS = 800.0
VOICE_MIN_SAMPLES = 3
VOICE_ON_S = 2.0
VOICE_OFF_S = 120.0

AI_BLOCK_CODES = ("key", "nokey", "quota", "model")   # 8: these errors stop the AI for the game
AI_MAX_ERRORS = 2                                    # ... and that many new errors of any kind

API_INGAME_ON_S = 5.0        # 9: Live Client silent this long in game (window still there)
API_IDLE_ON_S = 60.0         # ... or this long with the game window open but no game data
API_LOADING_ON_S = 240.0     # ... (the API answers an HTTP error: loading screen, up to 4 min)
API_OFF_S = 3.0
API_BACKOFF_AFTER_S = 30.0   # silent this long -> slower polls ...
API_BACKOFF_PERIODS = (3.0, 5.0)   # ... (poll period after 30 s, after 60 s)
API_RECREATE_EVERY_S = 60.0  # a fresh HTTP client this often while silent (real client only)
#: The engine keeps a game alive this long when the API goes silent but the game window is
#: still there (a mid-game API outage is not a game over).
API_OUTAGE_MAX_S = 90.0

# ------------------------------------------------------------------------------ texts (French)
MSG: dict[str, str] = {
    "capture_try": "Capture noire : essai d'une autre méthode de capture…",
    "capture_try_stale": "Capture figée : essai d'une autre méthode de capture…",
    "capture_black": "Capture noire : passe le jeu en Sans bordure",
    "capture_stale": "Capture figée : passe le jeu en Sans bordure",
    "minimap_search": "Minimap perdue : nouvelle recherche en cours…",
    "minimap_lost": "Minimap introuvable : ouvre Réglages > Calibrer",
    "perf_1": "Analyse allégée : PC chargé",
    "perf_2": "Analyse minimale : PC très chargé",
    "champ_recal": "Détection faible : recalibrage de la taille des icônes…",
    "champ_reload": "Détection faible : rechargement des icônes…",
    "champ_weak": "Détection faible : envoie un diagnostic (Ctrl+F8)",
    "champ_weak_auto": "Détection faible : diagnostic automatique enregistré, envoie-le",
    "ident": "Identités corrigées : pistes réinitialisées",
    "ident_unstable": "Identités instables : pistes réinitialisées plusieurs fois",
    "unfocused": "Overlay masqué : le jeu n'est pas au premier plan",
    "occluded": "Une fenêtre couvre la minimap : overlay et analyse en pause",
    "overlay_dead": "Overlay arrêté : redémarre TreeAI",
    "voice_dead": "Voix indisponible : bips seulement pour les dangers",
    "voice_slow": "Voix lente : bips seulement pour les dangers",
    "ai": "IA indisponible ({why}) : plans par règles pour cette partie",
    "api": "API du jeu indisponible : coaching limité",
}
#: In-game notices (the HUD card / toast): an instruction, verb first (presenter.card_line).
NOTICES: dict[str, str] = {
    "capture": "Passe le jeu en Sans bordure : capture noire",
    "capture_stale": "Passe le jeu en Sans bordure : capture figée",
    "minimap": "Va dans Réglages > Calibrer : minimap introuvable",
    "champions": "Fais un diagnostic (Ctrl+F8) : détection faible",
    "champions_auto": "Joue normalement : diagnostic automatique en cours",
}
AI_WHY: dict[str, str] = {"key": "clé refusée", "nokey": "aucune clé", "quota": "quota atteint",
                          "model": "modèle introuvable", "offline": "service injoignable",
                          "server": "erreurs du service", "empty": "réponses vides", "bad": "réponses illisibles",
                          "rate": "limite par minute"}
OUTCOME_FR: dict[str, str] = {"fixed": "corrigé automatiquement", "resolved": "rentré dans l'ordre",
                              "open": "non résolu"}


# ======================================================================================
# Data
# ======================================================================================
@dataclass
class Snapshot:
    """Measured state of the pipeline at time ``t`` (engine clock). Unknown = defaults."""

    t: float
    in_game: bool = True
    game_time: float | None = None
    live: bool = True                     # real capture (not a demo / frame source)
    # window / focus
    window: bool = True                   # game window found
    minimized: bool = False
    focused: bool = True
    occluded: bool = False                # another window covers the minimap
    # 1 capture
    capture_status: str = "ok"            # ok | black | stale | switched
    capture_black: bool = False           # engine state CAPTURE_BLACK
    capture_backend: str | None = None
    capture_alt: str | None = None        # fallback backend still available
    # 2 minimap
    locate_method: str | None = None      # auto | manual | fallback | None
    minimap_score: float | None = None    # last verify score
    locate_score: float | None = None     # score when located
    bad_s: float | None = None            # verify below threshold for this long (None: fine)
    loc_attempts: int = 0                 # locations this game
    loc_fails: int = 0                    # consecutive failed locations
    # 3 detection cost
    detecting: bool = True                # the detection should be running now
    target_fps: float | None = None       # governor rate now
    detect_fps: float | None = None       # measured (None: computed from on_frame())
    tick_p95_ms: float | None = None      # measured (None: computed from on_tick())
    # 4 champions
    me_alias: str | None = None           # my champion (dead / grey minimap: not judged)
    friends_alive: tuple[str, ...] = ()   # me + allies alive now (Live Client)
    dead: tuple[str, ...] = ()            # champions dead now
    friends_seen: dict[str, float] = field(default_factory=dict)   # alias -> last seen (engine time)
    frames_window: int | None = None      # frames analysed in the last CHAMP_WINDOW_S (None: on_frame())
    # 5 identity
    enemies_visible: int = 0
    enemies_alive: int = 5
    anon_enemies: tuple[tuple[str, float], ...] = ()   # visible anonymous enemy tracks (key, score)
    # 6 overlay
    overlay_wanted: bool = False          # overlay enabled + visible + Windows
    overlay_frames: int | None = None     # overlay thread frame counter (None: no overlay thread)
    # 7 voice
    voice_backend: str | None = None
    voice_expected: bool = False          # a real speech backend is expected (Windows)
    voice_muted: bool = False
    voice_alert_p95_ms: float | None = None   # synthesis time of the alerts (level >= WARNING)
    voice_samples: int = 0
    voice_failures: int = 0               # cumulative synthesis failures
    # 8 ai
    ai_enabled: bool = False
    ai_status: str | None = None
    ai_seq: int = 0
    ai_code: str | None = None
    ai_backoff_until: float | None = None    # the advisor's back-off deadline (a later one = a new failure)
    # automatic adaptations of this PC (never silent): budget, capture backend, icon scale
    perf_forced: bool = False             # the player forced the normal profile (no load level)
    budget_profile: str | None = None     # "normal" | "low_end"
    budget_auto: bool = True              # chosen automatically (perf_mode "auto")
    budget_reason: str | None = None
    capture_switches: int = 0
    capture_last_switch: str | None = None
    icon_scale: float | None = None       # roster matcher calibration of this game
    # (9 api: fed by the Live Client poller, see SelfCheck.api_update)


@dataclass(frozen=True)
class Action:
    """An automatic action for the engine (:func:`apply_actions`)."""

    kind: str
    rule: str
    arg: Any = None


@dataclass
class Problem:
    """An active problem: French ``status`` for the app, ``level`` 0 note / 1 dégradé / 2 the
    player must act, optional in-game ``notice`` (shown once per game)."""

    rule: str
    status: str
    level: int
    since: float
    notice: str | None = None


@dataclass
class _GameProblem:
    """Post-game record of one rule's problem (report)."""

    rule: str
    status: str
    level: int
    first_t: float
    first_gt: float | None
    count: int = 1
    active_s: float = 0.0
    started: float | None = None
    actions: list[str] = field(default_factory=list)
    outcome: str = "open"


class _Hold:
    """Debouncer: active after ``on_s`` of continuous symptom, inactive after ``off_s`` of
    continuous health; ``None`` (cannot measure now) freezes both timers."""

    def __init__(self, on_s: float, off_s: float) -> None:
        self.on_s, self.off_s = float(on_s), float(off_s)
        self.active = False
        self.bad_since: float | None = None
        self.good_since: float | None = None

    def update(self, t: float, bad: bool | None) -> bool:
        if bad is None:
            return self.active
        if bad:
            self.good_since = None
            if self.bad_since is None:
                self.bad_since = t
            if not self.active and t - self.bad_since >= self.on_s:
                self.active = True
        else:
            self.bad_since = None
            if self.good_since is None:
                self.good_since = t
            if self.active and t - self.good_since >= self.off_s:
                self.active = False
        return self.active

    def bad_for(self, t: float) -> float:
        return 0.0 if self.bad_since is None else max(0.0, t - self.bad_since)


class _Stats:
    """Tiny rolling mean / p95 (ms) of the self-check's own cost."""

    def __init__(self, n: int = 600) -> None:
        self._d: deque[float] = deque(maxlen=n)

    def add(self, ms: float) -> None:
        if math.isfinite(ms):
            self._d.append(float(ms))

    def summary(self) -> dict[str, float | int | None]:
        vals = sorted(self._d)
        if not vals:
            return {"mean": None, "p95": None, "max": None, "n": 0}
        return {"mean": round(sum(vals) / len(vals), 4), "p95": round(vals[int(0.95 * (len(vals) - 1))], 4),
                "max": round(vals[-1], 4), "n": len(vals)}


def _ns(**kw: Any) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(**kw)


def _fmt_s(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


# ======================================================================================
# The watchdog
# ======================================================================================
class SelfCheck:
    """Rule-based watchdog (see the module doc). Thread-safe; nothing raises.

    Feeds (cheap, every tick): :meth:`on_tick` (analysis tick cost), :meth:`on_frame` (an
    analysed frame), :meth:`on_tracks` (tracker snapshot: identity anomalies). Then
    :meth:`due` / :meth:`evaluate` (about 1 Hz) -> :class:`Action` list, :meth:`mark_notice`
    after an in-game notice was offered, :meth:`api_update` from the Live Client poller.
    Read side: :meth:`summary` (app), :meth:`game_report` (post-game report),
    :meth:`export` (diagnostic bundle).
    """

    def __init__(self, rules: Iterable[str] = RULES, clock: Callable[[], float] = time.monotonic) -> None:
        self._lock = threading.RLock()
        self._clock = clock
        self.enabled = True
        self.rules: set[str] = {r for r in rules if r in RULES}
        self.events: deque[dict[str, Any]] = deque(maxlen=EVENTS_MAX)
        self.cost = _Stats()                       # ms per analysis tick spent in the self-check
        self.eval_cost = _Stats(240)               # ms per rule evaluation (1 Hz)
        self._tick_ms: deque[tuple[float, float]] = deque(maxlen=TICK_SAMPLES)
        self._frames: deque[float] = deque(maxlen=FRAME_SAMPLES)
        self._next_eval = -math.inf
        self._gt: float | None = None
        self.load_level = 0
        self._perf_changed_t = -math.inf
        self._perf_up_t = -math.inf               # last step up (relapse detection)
        self._perf_up_need = HEALTHY_ON_S         # healthy time needed before a step up
        self._voice_override = False
        self._voice_status: str | None = None
        self._api = _ns(down_since=None, window_since=None, ok_since=None, last_recreate=-math.inf,
                        period=None, in_game=False)
        self.games = 0
        self._reset_game(None)

    # ------------------------------------------------------------------ per game
    def _reset_game(self, t: float | None) -> None:
        self._problems: dict[str, Problem] = {}
        self._game: dict[str, _GameProblem] = {}
        self._game_events: list[dict[str, Any]] = []
        self._notified: set[str] = set()
        self._last_notice_t = -math.inf
        self._pending: list[Action] = []
        self._max_level = self.load_level
        self._diag_started = False
        self._cap = _ns(hold=_Hold(CAPTURE_ON_S, CAPTURE_OFF_S), step=0, step_t=-math.inf, kind="black")
        self._mm = _ns(hold=_Hold(MINIMAP_ON_S, MINIMAP_OFF_S), attempts0=None, max_fails=0,
                       drift=_Hold(DRIFT_ON_S, 2.0), drift_n=0, drift_t=-math.inf)
        self._perf = _ns(starve_since=None, healthy_since=None, detecting_since=None)
        self._champ = _ns(hold=_Hold(CHAMP_ON_S, CHAMP_OFF_S), step=0, step_t=-math.inf, last_dead={})
        self._ident = _ns(obs={}, last_reset={}, resets=deque(maxlen=32), excess=_Hold(EXCESS_ON_S, 1.0),
                          excess_resets=deque(maxlen=16), last_t=-math.inf)
        self._ov = _ns(unfocused=_Hold(UNFOCUSED_ON_S, OVERLAY_OFF_S), occluded=_Hold(OCCLUDED_ON_S, OVERLAY_OFF_S),
                       frames=None, frames_t=None, explained=set())
        self._voice = _ns(hold=_Hold(VOICE_ON_S, VOICE_OFF_S), failures=None, fail_t=deque(maxlen=16))
        self._ai = _ns(mark=None, blocked=False, errors=0)
        self._adapt = _ns(scale0=None, scale_last=None, notes={})
        self._t0 = t

    def new_game(self, t: float, game_time: float | None = None) -> None:
        """A new game: per-game problems, notices and records start again. The load level and
        a beep-only voice override are session facts: kept (the rules restore them when the
        health comes back)."""
        with self._lock:
            self.games += 1
            self._gt = game_time
            self._reset_game(float(t))
            if self.load_level > 0:
                self._raise(float(t), "perf", MSG[f"perf_{min(2, self.load_level)}"], 1)
            if self._voice_override:
                self._voice.hold.active = True
                self._raise(float(t), "voice", self._voice_status or MSG["voice_dead"], 1)
            self._event(float(t), "game", "info", "Nouvelle partie")

    # ------------------------------------------------------------------ feeds (every tick)
    def on_tick(self, t: float, ms: float) -> None:
        """Cost of one analysis tick (ms)."""
        try:
            self._tick_ms.append((float(t), float(ms)))
        except (TypeError, ValueError):
            pass

    def on_frame(self, t: float) -> None:
        """One minimap frame analysed (detector + identifier ran)."""
        try:
            self._frames.append(float(t))
        except (TypeError, ValueError):
            pass

    def on_tracks(self, t: float, tracks: Iterable[Any]) -> None:
        """Tracker snapshot of this tick: one identity seen at two far places within
        :data:`FLIP_LEG_S` and back (flip-flop) queues a reset of that track. Cheap."""
        if not self.enabled or "identity" not in self.rules:
            return
        try:
            with self._lock:
                st = self._ident
                for tr in tracks:
                    alias = getattr(tr, "alias", None)
                    if not alias or not getattr(tr, "visible", False):
                        continue
                    ls = getattr(tr, "last_seen", None)
                    if ls is None:
                        continue
                    obs = st.obs.get(alias)
                    if obs is None:
                        obs = st.obs[alias] = deque(maxlen=16)
                    elif obs and obs[-1][0] >= ls:
                        continue                      # no new observation
                    rp = getattr(tr, "raw_position", None)
                    pos = rp() if callable(rp) else tr.position()
                    if pos is None:
                        continue
                    obs.append((float(ls), float(pos[0]), float(pos[1])))
                    while obs and obs[-1][0] - obs[0][0] > IDENT_HISTORY_S:
                        obs.popleft()
                    if len(obs) >= 3 and _flip(obs) and \
                            float(t) - st.last_reset.get(alias, -math.inf) >= IDENT_ALIAS_GAP_S:
                        st.last_reset[alias] = float(t)
                        obs.clear()
                        self._pending.append(Action("forget_track", "identity", alias))
                        self._ident_reset(float(t), f"{alias} vu à deux endroits : piste réinitialisée")
        except Exception:
            log.debug("selfcheck on_tracks failed", exc_info=True)

    # ------------------------------------------------------------------ measures
    def detect_rate(self, now: float, window: float = PERF_RATE_WINDOW_S) -> float:
        """Analysed frames per second over the last ``window`` s (the caller makes sure the
        detection ran during the whole window)."""
        return self.frames_in(now, window) / max(1e-3, float(window))

    def frames_in(self, now: float, window: float) -> int:
        n = 0
        for x in reversed(self._frames):
            if now - x > window:
                break
            n += 1
        return n

    def tick_p95(self, since: float) -> float | None:
        vals = []
        for t_, ms in reversed(self._tick_ms):
            if t_ < since:
                break
            vals.append(ms)
        if len(vals) < 10:
            return None
        vals.sort()
        return vals[int(0.95 * (len(vals) - 1))]

    # ------------------------------------------------------------------ evaluation
    def due(self, t: float) -> bool:
        return self.enabled and float(t) >= self._next_eval

    def evaluate(self, snap: Snapshot) -> list[Action]:
        """Run the rules on ``snap`` (about 1 Hz). Returns the actions to apply."""
        t0 = time.perf_counter()
        out: list[Action] = []
        try:
            with self._lock:
                if not self.enabled:
                    return []
                t = float(snap.t)
                self._next_eval = t + EVAL_PERIOD_S
                if snap.game_time is not None:
                    self._gt = snap.game_time
                self._account(t)
                out, self._pending = list(self._pending), []
                for name, fn in (("capture", self._r_capture), ("minimap", self._r_minimap),
                                 ("perf", self._r_perf), ("champions", self._r_champions),
                                 ("identity", self._r_identity), ("overlay", self._r_overlay),
                                 ("voice", self._r_voice), ("ai", self._r_ai), ("adapt", self._r_adapt)):
                    if name not in self.rules:
                        continue
                    try:
                        out += fn(snap) or []
                    except Exception:
                        log.debug("selfcheck rule %s failed", name, exc_info=True)
                out += self._notices(snap)
        except Exception:
            log.debug("selfcheck evaluate failed", exc_info=True)
        finally:
            self.eval_cost.add((time.perf_counter() - t0) * 1000.0)
        return out

    # ---- 1 capture
    def _r_capture(self, s: Snapshot) -> list[Action]:
        st = self._cap
        t = s.t
        measurable = s.in_game and s.window and not s.minimized and not s.occluded
        bad = (s.capture_black or s.capture_status in ("black", "stale")) if measurable else None
        if bad:
            st.kind = "stale" if s.capture_status == "stale" and not s.capture_black else "black"
        active = st.hold.update(t, bad)
        out: list[Action] = []
        if active:
            stale = st.kind == "stale"
            if st.step == 0:
                st.step, st.step_t = 1, t
                self._raise(t, "capture", MSG["capture_try_stale" if stale else "capture_try"], 1)
                if s.capture_alt:
                    out.append(Action("capture_switch", "capture", s.capture_alt))
                    self._acted(t, "capture", f"capture : passage à {s.capture_alt}")
            elif st.step == 1 and t - st.step_t >= CAPTURE_STEP_S:
                st.step, st.step_t = 2, t
                out.append(Action("capture_recreate", "capture"))
                self._acted(t, "capture", "capture : nouvelle initialisation")
            elif st.step == 2 and t - st.step_t >= CAPTURE_STEP_S:
                st.step = 3
                self._raise(t, "capture", MSG["capture_stale" if stale else "capture_black"], 2,
                            notice=NOTICES["capture_stale" if stale else "capture"])
        elif st.step > 0 and bad is False:
            st.step = 0
            self._resolve(t, "capture", "capture rétablie")
        return out

    # ---- 2 minimap
    def _r_minimap(self, s: Snapshot) -> list[Action]:
        st = self._mm
        t = s.t
        out: list[Action] = []
        if st.attempts0 is None:
            st.attempts0 = int(s.loc_attempts)
        measurable = (s.in_game and s.live and s.window and not s.minimized and not s.occluded
                      and not s.capture_black and s.capture_status not in ("black", "stale")
                      and s.locate_method in ("auto", "fallback"))
        bad = None
        if measurable:
            bad = s.locate_method == "fallback" or s.bad_s is not None or s.loc_fails > 0
            if bad and s.game_time is not None and s.game_time < MINIMAP_START_GRACE_S:
                bad = None                     # game start: not judged yet
        active = st.hold.update(t, bad)
        st.max_fails = max(st.max_fails, int(s.loc_fails))
        if active:
            if s.loc_fails >= MINIMAP_FAILS:
                self._raise(t, "minimap", MSG["minimap_lost"], 2, notice=NOTICES["minimap"])
            elif "minimap" not in self._problems:
                self._raise(t, "minimap", MSG["minimap_search"], 1)
            n = int(s.loc_attempts) - int(st.attempts0)
            if n > 0:
                self._acted(t, "minimap", f"minimap : {n} relocalisation(s)", replace_prefix="minimap : ")
        elif "minimap" in self._problems and bad is False:
            self._resolve(t, "minimap", "minimap retrouvée")
            st.attempts0 = int(s.loc_attempts)
        # slowly drifting score (the minimap moved a little / changed size): cheap relocation
        drift = None
        if measurable and s.locate_method == "auto" and s.bad_s is None and s.minimap_score is not None \
                and s.locate_score:
            drift = s.minimap_score < DRIFT_ABS and s.minimap_score < DRIFT_REL * s.locate_score
        if st.drift.update(t, drift) and st.drift_n < DRIFT_MAX and t - st.drift_t >= DRIFT_EVERY_S:
            st.drift_n += 1
            st.drift_t = t
            st.drift.active = False
            st.drift.bad_since = None
            out.append(Action("relocate", "minimap"))
            self._event(t, "minimap", "action",
                        f"score de la minimap en baisse ({s.minimap_score:.2f}) : relocalisation")
        return out

    # ---- 3 perf
    def _r_perf(self, s: Snapshot) -> list[Action]:
        st = self._perf
        t = s.t
        if s.perf_forced:                  # "Forcer profil normal": no automatic load level
            st.starve_since = st.healthy_since = None
            if self.load_level > 0:
                self.load_level = 0
                self._perf_changed_t = t
                self._resolve(t, "perf", "profil normal forcé")
                return [Action("perf_level", "perf", 0)]
            return []
        if not (s.in_game and s.detecting):
            st.detecting_since = None
            st.starve_since = st.healthy_since = None
            return []
        if st.detecting_since is None:
            st.detecting_since = t
        if t - st.detecting_since < PERF_MIN_DETECTING_S:
            return []
        since = max(t - PERF_P95_WINDOW_S, self._perf_changed_t + 5.0, st.detecting_since)
        rate = s.detect_fps if s.detect_fps is not None else self.detect_rate(t)
        p95 = s.tick_p95_ms if s.tick_p95_ms is not None else self.tick_p95(since)
        target = s.target_fps
        floor = min(STARVE_FPS, STARVE_FRAC * target) if target and target >= 1.0 else None
        slow = floor is not None and rate is not None and rate < floor
        starving = bool(slow or (p95 is not None and p95 > STARVE_P95_MS))
        healthy = (not starving and (floor is None or rate is None or rate >= HEALTHY_MARGIN * floor)
                   and (p95 is None or p95 < HEALTHY_P95_MS))
        st.starve_since = (st.starve_since if st.starve_since is not None else t) if starving else None
        st.healthy_since = (st.healthy_since if st.healthy_since is not None else t) if healthy else None
        out: list[Action] = []
        settled = t - self._perf_changed_t >= PERF_SETTLE_S
        why = []
        if slow:
            why.append(f"{_fmt_s(rate)} img/s")
        if p95 is not None and p95 > STARVE_P95_MS:
            why.append(f"{p95:.0f} ms")
        if st.starve_since is not None and t - st.starve_since >= STARVE_ON_S and settled and self.load_level < 2:
            if t - self._perf_up_t < PERF_RELAPSE_S:        # relapse after a step up: stay down longer
                self._perf_up_need = min(HEALTHY_MAX_S, 2.0 * self._perf_up_need)
            self.load_level += 1
            self._perf_changed_t = t
            st.starve_since = None
            self._max_level = max(self._max_level, self.load_level)
            out.append(Action("perf_level", "perf", self.load_level))
            self._raise(t, "perf", MSG[f"perf_{self.load_level}"], 1)
            self._acted(t, "perf", f"analyse {_ANALYSE[self.load_level]} ({', '.join(why) or 'lente'})")
        elif st.healthy_since is not None and t - st.healthy_since >= self._perf_up_need \
                and t - self._perf_changed_t >= self._perf_up_need and self.load_level > 0:
            self.load_level -= 1
            self._perf_changed_t = self._perf_up_t = t
            st.healthy_since = None
            out.append(Action("perf_level", "perf", self.load_level))
            if self.load_level == 0:
                self._resolve(t, "perf", "analyse revenue à la normale")
            else:
                self._raise(t, "perf", MSG[f"perf_{self.load_level}"], 1)
                self._event(t, "perf", "action", f"analyse {_ANALYSE[self.load_level]} (ça va mieux)")
        elif self.load_level > 0 and "perf" not in self._problems:
            self._raise(t, "perf", MSG[f"perf_{min(2, self.load_level)}"], 1)
        return out

    # ---- 4 champions
    def _r_champions(self, s: Snapshot) -> list[Action]:
        st = self._champ
        t = s.t
        for a in s.dead:
            st.last_dead[a] = t
        gt = s.game_time
        window = CHAMP_WINDOW_S
        if st.step > 0:
            window = min(CHAMP_WINDOW_S, max(CHAMP_MIN_WINDOW_S, t - st.step_t))
        expected = [a for a in s.friends_alive if a and t - st.last_dead.get(a, -math.inf) >= window]
        frames = s.frames_window if s.frames_window is not None else self.frames_in(t, window)
        bad = None
        me_ok = not s.me_alias or t - st.last_dead.get(s.me_alias, -math.inf) >= window    # (greyed minimap)
        if s.in_game and s.detecting and me_ok and gt is not None and gt >= CHAMP_MIN_GT_S \
                and len(expected) >= CHAMP_MIN_EXPECTED and frames >= CHAMP_MIN_FRAMES * window / CHAMP_WINDOW_S:
            seen = sum(1 for a in expected if t - float(s.friends_seen.get(a, -math.inf)) <= window)
            if seen <= math.floor(CHAMP_BAD_FRAC * len(expected)):
                bad = True
            elif seen >= math.ceil(CHAMP_GOOD_FRAC * len(expected)):
                bad = False
        active = st.hold.update(t, bad)
        out: list[Action] = []
        if active:
            if st.step == 0:
                st.step, st.step_t = 1, t
                out.append(Action("recalibrate", "champions"))
                self._raise(t, "champions", MSG["champ_recal"], 1)
                self._acted(t, "champions", "taille des icônes recalibrée")
            elif st.step == 1 and t - st.step_t >= CHAMP_STEP_S and bad:
                st.step, st.step_t = 2, t
                out.append(Action("reload_icons", "champions"))
                self._raise(t, "champions", MSG["champ_reload"], 1)
                self._acted(t, "champions", "icônes des champions rechargées")
            elif st.step == 2 and t - st.step_t >= CHAMP_STEP_S and bad:
                st.step, st.step_t = 3, t
                auto = not self._diag_started
                if auto:
                    self._diag_started = True
                    out.append(Action("auto_diag", "champions"))
                self._raise(t, "champions", MSG["champ_weak"], 2, notice=NOTICES["champions"])
        elif st.step > 0 and bad is False:
            st.step = 0
            self._resolve(t, "champions", "détection rétablie")
        return out

    def diag_started(self, t: float, ok: bool) -> None:
        """Result of the automatic diagnostic (``auto_diag`` action): status + notice wording."""
        with self._lock:
            p = self._problems.get("champions")
            if ok:
                self._event(t, "champions", "action", "diagnostic automatique de 60 s lancé")
                g = self._game.get("champions")
                if g is not None:
                    g.actions.append("diagnostic automatique (60 s)")
                if p is not None:
                    p.status, p.notice = MSG["champ_weak_auto"], NOTICES["champions_auto"]
            else:
                self._diag_started = False

    # ---- 5 identity
    def _ident_reset(self, t: float, text: str) -> None:
        st = self._ident
        st.resets.append(t)
        st.last_t = t
        recent = sum(1 for x in st.resets if t - x <= IDENT_UNSTABLE_S)
        if recent >= IDENT_UNSTABLE_N:
            self._raise(t, "identity", MSG["ident_unstable"], 1)
        else:
            self._raise(t, "identity", MSG["ident"], 0)
        self._acted(t, "identity", text)

    def _r_identity(self, s: Snapshot) -> list[Action]:
        st = self._ident
        t = s.t
        out: list[Action] = []
        excess = s.enemies_visible - max(0, int(s.enemies_alive))
        bad = (excess > 0) if s.in_game else None
        if st.excess.update(t, bad) and excess > 0:
            keys = [k for k, _sc in sorted(s.anon_enemies, key=lambda x: x[1])][:excess]
            st.excess.active = False
            st.excess.bad_since = None
            if sum(1 for x in st.excess_resets if t - x <= IDENT_UNSTABLE_S) >= IDENT_UNSTABLE_N:
                # resetting did not help (a persistent over-count, not a ghost): no churn, explained
                st.last_t = t
                self._raise(t, "identity", MSG["ident_unstable"], 1)
            elif keys:
                st.excess_resets.append(t)
                out.append(Action("forget_tracks", "identity", tuple(keys)))
                self._ident_reset(t, f"{s.enemies_visible} ennemis visibles pour {s.enemies_alive} vivants : "
                                     f"{len(keys)} fantôme(s) retiré(s)")
        if "identity" in self._problems and t - st.last_t >= IDENT_CLEAR_S:
            self._resolve(t, "identity", "identités stables")
        return out

    # ---- 6 overlay
    def _r_overlay(self, s: Snapshot) -> list[Action]:
        st = self._ov
        t = s.t
        live = s.in_game and s.live
        unf = st.unfocused.update(t, (not s.focused and not s.minimized) if live else None)
        occ = st.occluded.update(t, s.occluded if live else None)
        dead = False
        if live and s.overlay_wanted and s.overlay_frames is not None:
            if st.frames is None or s.overlay_frames != st.frames:
                st.frames, st.frames_t = s.overlay_frames, t
            dead = st.frames_t is not None and t - st.frames_t >= OVERLAY_STALL_S
        else:
            st.frames = st.frames_t = None
        key = "dead" if dead else ("occluded" if occ else ("unfocused" if unf else None))
        if key is not None:
            # explained ONCE per game (event log, report); the status stays while it lasts
            quiet = key in st.explained
            st.explained.add(key)
            text, level = {"dead": (MSG["overlay_dead"], 2), "occluded": (MSG["occluded"], 1),
                           "unfocused": (MSG["unfocused"], 0)}[key]
            self._raise(t, "overlay", text, level, quiet=quiet, record=key != "unfocused")
        elif "overlay" in self._problems:
            self._resolve(t, "overlay", "overlay visible", quiet=True)
        return []

    # ---- 7 voice
    def _r_voice(self, s: Snapshot) -> list[Action]:
        st = self._voice
        t = s.t
        if not s.voice_expected or s.voice_muted:
            return []
        if st.failures is None:
            st.failures = int(s.voice_failures)
        if s.voice_failures > st.failures:
            for _ in range(min(8, int(s.voice_failures) - int(st.failures))):
                st.fail_t.append(t)
            st.failures = int(s.voice_failures)
        recent_fails = sum(1 for x in st.fail_t if t - x <= 60.0)
        dead = str(s.voice_backend or "").lower() == "print" or recent_fails >= 2
        slow = s.voice_samples >= VOICE_MIN_SAMPLES and s.voice_alert_p95_ms is not None \
            and s.voice_alert_p95_ms > VOICE_SLOW_MS
        ok = not dead and recent_fails == 0 and (s.voice_samples < VOICE_MIN_SAMPLES
                                                 or s.voice_alert_p95_ms is None
                                                 or s.voice_alert_p95_ms < VOICE_OK_MS)
        bad = True if (dead or slow) else (False if ok else None)
        active = st.hold.update(t, bad)
        out: list[Action] = []
        if active:
            cur = self._problems.get("voice")
            text = MSG["voice_dead"] if dead else (MSG["voice_slow"] if slow or cur is None else cur.status)
            self._voice_status = text
            if not self._voice_override:
                self._voice_override = True
                out.append(Action("voice_beep_only", "voice", True))
                self._raise(t, "voice", text, 1)
                why = "voix indisponible" if dead else f"voix lente ({_fmt_s((s.voice_alert_p95_ms or 0) / 1000)} s)"
                self._acted(t, "voice", f"{why} : dangers en bip seul")
            else:
                self._raise(t, "voice", text, 1)
        elif self._voice_override and bad is False:
            self._voice_override = False
            out.append(Action("voice_beep_only", "voice", False))
            self._resolve(t, "voice", "voix rétablie")
        return out

    # ---- 8 ai
    def _r_ai(self, s: Snapshot) -> list[Action]:
        st = self._ai
        t = s.t
        if not s.ai_enabled or not s.in_game:
            return []
        # a NEW failure this game = the advisor set a later back-off deadline (a stale error text
        # from the previous game does not count; a settings change clears the deadline: not one)
        mark = s.ai_backoff_until
        transient = s.ai_status is not None and s.ai_code == "rate"   # per-minute limit: waits, never stops
        if mark is not None and st.mark is not None and mark > st.mark and not transient:
            st.errors += 1
        if mark is not None:
            st.mark = mark
        if st.blocked:
            return []
        code = s.ai_code if s.ai_status else None
        permanent = code in ("key", "nokey", "model")          # the advisor stopped by itself: say it
        if permanent or (code in AI_BLOCK_CODES and st.errors >= 1) or st.errors >= AI_MAX_ERRORS:
            st.blocked = True
            why = AI_WHY.get(str(code), "erreurs répétées") if code else "erreurs répétées"
            self._raise(t, "ai", MSG["ai"].format(why=why), 1)
            self._acted(t, "ai", f"IA arrêtée pour la partie ({why})")
            return [Action("ai_block", "ai", True)]
        return []

    # ---- 10 automatic adaptations (made visible: "same version, different analysis" between PCs)
    def _r_adapt(self, s: Snapshot) -> list[Action]:
        st = self._adapt
        t = s.t
        if s.budget_profile == "low_end" and s.budget_auto:
            self._adapt_note(t, "budget", f"Profil PC faible activé automatiquement ({s.budget_reason or 'auto'})")
        if s.capture_switches > 0 and s.capture_last_switch:
            self._adapt_note(t, "capture", f"Capture changée automatiquement : {s.capture_last_switch}")
        sc = s.icon_scale
        if sc:
            if st.scale0 is None:
                st.scale0 = st.scale_last = sc
            elif abs(math.log(sc / st.scale_last)) > 0.06:
                self._adapt_note(t, "scale", f"Taille des icônes recalibrée automatiquement "
                                             f"({st.scale0:.3f} → {sc:.3f})".replace(".", ","))
                st.scale_last = sc
        return []

    def _adapt_note(self, t: float, key: str, text: str) -> None:
        notes = self._adapt.notes
        if notes.get(key) == text:
            return
        notes[key] = text
        self._raise(t, "adapt", " · ".join(notes.values()), 0)
        self._acted(t, "adapt", text)

    def note(self, t: float, rule: str, text: str) -> None:
        """An action of the player or of the app worth the log / the report (reset, forced profile)."""
        with self._lock:
            g = self._game.get(rule)
            if g is not None:
                g.actions.append(text)
            self._event(float(t), rule, "action", text)

    def force_normal(self, t: float) -> None:
        """"Forcer profil normal": load level 0 now (the engine pushes the knobs)."""
        with self._lock:
            self.load_level = 0
            self._perf_changed_t = float(t)
            self._problems.pop("perf", None)
            self._adapt.notes.pop("budget", None)
            if not self._adapt.notes:
                self._problems.pop("adapt", None)
            self._event(float(t), "perf", "action", "profil normal forcé")

    # ---- 9 api (fed by the Live Client poller: one source of truth, in and out of a game)
    def api_update(self, t: float, ok: bool, window: bool | None, in_game: bool = False,
                   answering: bool = False) -> list[Action]:
        """Live Client poll result (poller thread). ``window``: the game window exists (None:
        unknown); ``answering``: the API answered with an HTTP error (up, no game data yet: loading
        screen). Returns ``api_backoff`` (poll period, None = normal) / ``api_recreate`` actions.
        Never raises."""
        out: list[Action] = []
        if not self.enabled or "api" not in self.rules:
            return out
        try:
            with self._lock:
                t = float(t)
                st = self._api
                st.in_game = bool(in_game)
                if ok:
                    st.down_since = None
                    if st.ok_since is None:
                        st.ok_since = t
                    if st.period is not None:
                        st.period = None
                        out.append(Action("api_backoff", "api", None))
                    if "api" in self._problems and t - st.ok_since >= API_OFF_S:
                        self._resolve(t, "api", "API du jeu revenue")
                    return out
                st.ok_since = None
                if not window:
                    st.down_since = None
                    if "api" in self._problems and not in_game:
                        self._clear("api")
                    return out
                if st.down_since is None:
                    st.down_since = t
                down = t - st.down_since
                if down >= (API_INGAME_ON_S if in_game else (API_LOADING_ON_S if answering else API_IDLE_ON_S)):
                    self._raise(t, "api", MSG["api"], 1)
                if down >= API_BACKOFF_AFTER_S and not answering:      # (answering: nothing to fix)
                    period = API_BACKOFF_PERIODS[0] if down < 2 * API_BACKOFF_AFTER_S else API_BACKOFF_PERIODS[-1]
                    if period != st.period:
                        st.period = period
                        out.append(Action("api_backoff", "api", period))
                        self._acted(t, "api", f"API du jeu muette : nouvel essai toutes les {period:.0f} s",
                                    replace_prefix="API du jeu muette")
                    if t - st.last_recreate >= API_RECREATE_EVERY_S:
                        st.last_recreate = t
                        out.append(Action("api_recreate", "api"))
        except Exception:
            log.debug("selfcheck api_update failed", exc_info=True)
        return out

    def api_down_for(self, t: float) -> float:
        """Seconds the Live Client has been silent with the game window open (0 when fine)."""
        d = self._api.down_since
        return 0.0 if d is None else max(0.0, float(t) - d)

    # ------------------------------------------------------------------ notices
    def _notices(self, s: Snapshot) -> list[Action]:
        if not s.in_game or self._last_notice_t > s.t - NOTICE_GAP_S:
            return []
        for p in sorted(self._problems.values(), key=lambda p: (-p.level, p.since)):
            if p.notice and p.rule not in self._notified:
                return [Action("notice", p.rule, p.notice)]
        return []

    def mark_notice(self, rule: str, shown: bool, t: float) -> None:
        """The engine offered the notice of ``rule``: shown (at most once per game) or not now."""
        with self._lock:
            if not shown:
                return
            self._notified.add(rule)
            self._last_notice_t = float(t)
            p = self._problems.get(rule)
            self._event(float(t), rule, "notice", p.notice if p is not None and p.notice else "")

    # ------------------------------------------------------------------ problem book-keeping
    def _raise(self, t: float, rule: str, status: str, level: int, notice: str | None = None,
               quiet: bool = False, record: bool = True) -> None:
        """Problem ``rule`` active with this status (new, or updated). ``quiet``: no event (already
        explained); ``record``: kept for the post-game report."""
        p = self._problems.get(rule)
        g = self._game.get(rule)
        if p is None:
            self._problems[rule] = Problem(rule, status, int(level), t, notice)
            if not quiet:
                self._event(t, rule, "problem", status)
            if not record:
                return
            if g is None:
                self._game[rule] = _GameProblem(rule, status, int(level), t, self._gt, started=t)
            else:
                g.count += 1
                g.started = t
                g.outcome = "open"
                if int(level) >= g.level:
                    g.status, g.level = status, int(level)
            return
        if p.status != status or p.level != int(level) or (notice and p.notice != notice):
            p.status, p.level = status, int(level)
            if notice:
                p.notice = notice
            if not quiet:
                self._event(t, rule, "problem", status)
            if record and g is None:
                self._game[rule] = _GameProblem(rule, status, int(level), t, self._gt, started=t)
            elif g is not None and int(level) >= g.level:
                g.status, g.level = status, int(level)

    def _acted(self, t: float, rule: str, text: str, replace_prefix: str | None = None) -> None:
        g = self._game.get(rule)
        if g is not None:
            if replace_prefix and g.actions and g.actions[-1].startswith(replace_prefix):
                if g.actions[-1] == text:
                    return
                g.actions[-1] = text
            else:
                g.actions.append(text)
                del g.actions[:-8]
        self._event(t, rule, "action", text)

    def _resolve(self, t: float, rule: str, text: str, quiet: bool = False) -> None:
        p = self._problems.pop(rule, None)
        g = self._game.get(rule)
        # fixed by our own actions, or back to normal by itself / after the player acted (level 2)
        auto = g is not None and bool(g.actions) and g.level < 2
        if g is not None:
            if g.started is not None:
                g.active_s += max(0.0, t - g.started)
                g.started = None
            g.outcome = "fixed" if auto else "resolved"
        if p is not None and not quiet:
            self._event(t, rule, "fixed" if auto else "resolved", text)

    def _clear(self, rule: str) -> None:
        self._problems.pop(rule, None)
        g = self._game.get(rule)
        if g is not None and g.started is not None:
            g.started = None

    def _account(self, t: float) -> None:
        """Keep the report's durations of the open problems current."""
        for g in self._game.values():
            if g.started is not None and t > g.started:
                g.active_s += t - g.started
                g.started = t

    def _event(self, t: float, rule: str, kind: str, text: str) -> None:
        ev = {"t": round(float(t), 2), "gt": None if self._gt is None else round(float(self._gt), 1),
              "rule": rule, "kind": kind, "text": str(text)}
        self.events.append(ev)
        self._game_events.append(ev)
        del self._game_events[:-GAME_EVENTS_MAX]
        if kind != "info":
            log.info("Self-check [%s] %s: %s", rule, kind, text)

    # ------------------------------------------------------------------ read side
    def problems(self) -> list[Problem]:
        with self._lock:
            return sorted((Problem(**vars(p)) for p in self._problems.values()), key=lambda p: (-p.level, p.since))

    def summary(self) -> dict[str, Any]:
        """``{"state": "ok" | "degraded" | "off", "level", "title", "reasons", "notes", "fixed",
        "profile", "overhead_ms"}`` for the app ("Santé TreeAI"). Never raises."""
        try:
            with self._lock:
                probs = sorted(self._problems.values(), key=lambda p: (-p.level, p.since))
                level = max((p.level for p in probs), default=0)
                fixed = [e["text"] for e in self._game_events if e["kind"] == "fixed"][-5:][::-1]
                actions = [e["text"] for e in self._game_events if e["kind"] == "action"][-5:][::-1]
                state = "off" if not self.enabled else ("ok" if level == 0 else "degraded")
                return {
                    "state": state, "level": int(level),
                    "title": "Santé TreeAI : " + ("OK" if level == 0 else "dégradé"),
                    "reasons": [p.status for p in probs if p.level >= 1],
                    "notes": [p.status for p in probs if p.level == 0],
                    "rules": [p.rule for p in probs],
                    "fixed": fixed, "actions": actions,
                    "profile": LOAD_LABELS.get(self.load_level, "normal"),
                    "load_level": int(self.load_level),
                    "overhead_ms": self.cost.summary(),
                }
        except Exception:
            log.debug("selfcheck summary failed", exc_info=True)
            return {"state": "ok", "level": 0, "title": "Santé TreeAI : OK", "reasons": [], "notes": [],
                    "rules": [], "fixed": [], "actions": [], "profile": "normal", "load_level": 0,
                    "overhead_ms": {}}

    def game_report(self) -> dict[str, Any]:
        """What went wrong this game and what was fixed automatically (post-game report)."""
        try:
            with self._lock:
                probs = []
                for g in sorted(self._game.values(), key=lambda g: g.first_t):
                    probs.append({"rule": g.rule, "label": RULE_LABELS.get(g.rule, g.rule), "status": g.status,
                                  "level": g.level, "first_gt": None if g.first_gt is None else round(g.first_gt),
                                  "count": g.count, "active_s": round(g.active_s, 1), "actions": list(g.actions),
                                  "outcome": g.outcome, "outcome_fr": OUTCOME_FR.get(g.outcome, g.outcome)})
                serious = [p for p in probs if p["level"] >= 1]
                return {"version": 1, "ok": not serious, "problems": probs,
                        "profile_max": LOAD_LABELS.get(self._max_level, "normal"),
                        "notices": sorted(self._notified), "diagnostic": bool(self._diag_started),
                        "overhead_ms": self.cost.summary()}
        except Exception:
            log.debug("selfcheck game_report failed", exc_info=True)
            return {"version": 1, "ok": True, "problems": []}

    def export(self) -> dict[str, Any]:
        """Everything for the diagnostic bundle (``selfcheck.json``)."""
        with self._lock:
            return {"summary": self.summary(), "game": self.game_report(), "events": list(self.events),
                    "rules": sorted(self.rules), "enabled": self.enabled, "load_level": self.load_level,
                    "eval_ms": self.eval_cost.summary(), "tick_ms": self.cost.summary()}

    def log_lines(self) -> list[str]:
        """The event log as text lines (diagnostic bundle)."""
        out = []
        for e in list(self.events):
            gt = e.get("gt")
            when = "--:--" if gt is None else f"{int(gt) // 60}:{int(gt) % 60:02d}"
            out.append(f"[{e.get('t'):>9}] {when} {e.get('rule', ''):<9} {e.get('kind', ''):<8} {e.get('text', '')}")
        return out


def _flip(obs: Any) -> bool:
    """True when the last observation came back near an earlier one after a far jump, each leg
    within FLIP_LEG_S: one identity at two places."""
    tn, un, vn = obs[-1]
    pts = list(obs)
    for j in range(len(pts) - 2, 0, -1):
        tj, uj, vj = pts[j]
        if tn - tj > FLIP_LEG_S:
            break
        if math.hypot(un - uj, vn - vj) < FLIP_FAR:
            continue
        for k in range(j - 1, -1, -1):
            tk, uk, vk = pts[k]
            if tj - tk > FLIP_LEG_S:
                break
            if math.hypot(uk - un, vk - vn) <= FLIP_NEAR and math.hypot(uk - uj, vk - vj) >= FLIP_FAR:
                return True
    return False


def summary_text(summary: Any) -> tuple[str, int]:
    """One line for the app from :meth:`SelfCheck.summary` (``engine.health()["selfcheck"]``):
    ``("Santé TreeAI : dégradé · Analyse allégée : PC chargé", 1)``; level 0 ok / 1 dégradé /
    2 the player must act. Pure, never raises; ``("", 0)`` without data."""
    try:
        if not isinstance(summary, dict) or not summary or summary.get("state") == "off":
            return "", 0
        level = int(summary.get("level") or 0)
        bits = [str(summary.get("title") or ("Santé TreeAI : OK" if level == 0 else "Santé TreeAI : dégradé"))]
        bits += [str(r) for r in (summary.get("reasons") or [])[:3]]
        bits += [str(r) for r in (summary.get("notes") or [])[:2]]      # adaptations: never silent
        fixed = [str(f) for f in (summary.get("fixed") or [])[:2]]
        if fixed:
            bits.append("corrigé : " + ", ".join(fixed))
        return " · ".join(b for b in bits if b), level
    except Exception:
        return "", 0


def attach_to_record(record_path: Any, report: dict[str, Any]) -> bool:
    """Write the game's :meth:`SelfCheck.game_report` into the game record JSON as
    ``record["selfcheck"]`` (atomic), for the post-game report. Never raises."""
    try:
        import json
        import os
        from pathlib import Path

        p = Path(record_path)
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return False
        data["selfcheck"] = report
        tmp = p.with_name(p.name + f".{os.getpid()}.selfcheck.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, p)
        return True
    except Exception:
        log.exception("Cannot add the self-check summary to %s", record_path)
        return False


# ======================================================================================
# Engine glue (CoachEngine private state -> Snapshot; Actions -> engine calls)
# ======================================================================================
def _ai_code(status: str | None) -> str | None:
    if not status:
        return None
    try:
        from treeaicoach import ai_advisor as aa

        for code, text in aa.ERROR_FR.items():
            if status == text:
                return code
        if status == getattr(aa, "OLLAMA_OFFLINE", None):
            return "offline"
    except Exception:
        pass
    return None


def snapshot_from_engine(eng: Any, t: float) -> Snapshot:
    """Measure the engine (analysis thread, inside its tick lock). Never raises: unknown parts
    keep their defaults."""
    s = Snapshot(t=float(t))
    try:
        from treeaicoach.engine_base import EngineState
        from treeaicoach.fmtutil import finite_loose

        with eng._lock:
            game = eng._game
            s.in_game = bool(eng._in_game) and game is not None
            state = eng._state
            game_t = eng._game_t
        s.live = eng._frame_source is None
        if game is not None:
            gt = finite_loose(getattr(game, "game_time", None))
            s.game_time = None if gt is None else gt + min(max(0.0, t - game_t), 3.0)
        s.window = (eng._window is not None) if s.live else True
        s.minimized = eng._paused == "minimized"
        s.focused = eng._unfocused_since is None
        s.occluded = bool(eng._occluded)
        # 1 capture
        s.capture_status = str(eng._capture_status or "ok")
        s.capture_black = state == EngineState.CAPTURE_BLACK
        cap = eng._capture
        other = getattr(cap, "other", None)
        s.capture_backend = getattr(cap, "current", None) or getattr(cap, "name", None)
        try:
            s.capture_alt = other() if callable(other) else None
        except Exception:
            s.capture_alt = None
        # 2 minimap
        s.locate_method = eng._locate_method
        s.minimap_score = eng._minimap_score
        s.locate_score = getattr(eng, "_locate_score", None)
        s.bad_s = None if eng._bad_since is None else max(0.0, t - eng._bad_since)
        s.loc_attempts = int(getattr(eng, "_loc_attempts", 0) or 0)
        s.loc_fails = int(getattr(eng, "_loc_fails", 0) or 0)
        # 3 detection cost
        capture_bad = s.capture_black or s.capture_status in ("black", "stale")
        s.detecting = bool(s.in_game and s.window and not eng._paused and eng._unfocused_since is None
                           and not s.occluded and s.bad_s is None and not capture_bad)
        try:
            if eng._adaptive:
                s.target_fps = float(eng._governor.fps(t, eng._paused, eng._unfocused_since is not None))
            else:
                s.target_fps = float(eng._cfg.target_fps)
        except Exception:
            s.target_fps = None
        # 4 / 5 champions, identities (tracker snapshot)
        tr = eng._tracker
        if game is not None:
            me = getattr(game, "me", None)
            my_alias = getattr(me, "champion_alias", None) if me is not None else None
            s.me_alias = my_alias or None
            friends = ([me] if me is not None else []) + list(getattr(game, "allies", None) or [])
            s.friends_alive = tuple(p.champion_alias for p in friends
                                    if p is not None and p.champion_alias and not p.is_dead)
            s.dead = tuple(p.champion_alias for p in game.all_players() if p.champion_alias and p.is_dead)
            s.enemies_alive = sum(1 for p in getattr(game, "enemies", None) or [] if not p.is_dead) \
                if getattr(game, "enemies", None) else 5
            if tr is not None:
                seen: dict[str, float] = {}
                anon: list[tuple[str, float]] = []
                vis = 0
                for k in tr.tracks():
                    rel = getattr(k, "relation", None)
                    if rel in ("self", "ally") and k.alias:
                        seen[k.alias] = max(seen.get(k.alias, -math.inf), float(k.last_seen))
                    elif rel == "enemy" and k.visible:
                        vis += 1
                        if not k.alias:
                            anon.append((k.key, float(getattr(k, "score", 0.0) or 0.0)))
                me_tr = tr.me()
                if me_tr is not None and my_alias:
                    seen[my_alias] = max(seen.get(my_alias, -math.inf), float(me_tr.last_seen))
                s.friends_seen = seen
                s.enemies_visible = vis
                s.anon_enemies = tuple(anon)
        # 6 overlay
        cfg = eng._cfg
        import sys

        s.overlay_wanted = bool(sys.platform == "win32" and getattr(cfg, "overlay_enabled", True)
                                and eng._overlay_visible)
        if s.overlay_wanted:
            try:
                from treeaicoach import overlay as _ov

                st = _ov.current_stats()
                s.overlay_frames = None if not st else int(st.get("frames") or 0)
            except Exception:
                s.overlay_frames = None
        # 7 voice
        voice = eng._voice
        s.voice_backend = str(getattr(voice, "backend", "") or "") or None
        vh = getattr(voice, "health", None)
        if callable(vh):
            h = vh() or {}
            s.voice_expected = bool(h.get("expected", False))
            s.voice_alert_p95_ms = h.get("alert_p95_ms")
            s.voice_samples = int(h.get("alert_samples") or 0)
            s.voice_failures = int(h.get("failures") or 0)
        s.voice_muted = bool(eng._muted)
        # 8 ai
        ai = getattr(eng, "_ai", None)
        if ai is not None and bool(getattr(ai, "enabled", False)):
            s.ai_enabled = True
            seq, text = ai.status()
            s.ai_seq, s.ai_status = int(seq or 0), text
            s.ai_code = _ai_code(text)
            bu = getattr(ai, "_blocked_until", None)
            s.ai_backoff_until = float(bu) if isinstance(bu, (int, float)) and not math.isnan(bu) else None
        # automatic adaptations (rule "adapt")
        b = eng._budget
        s.budget_profile, s.budget_auto, s.budget_reason = b.name, b.mode == "auto", b.reason
        s.perf_forced = str(getattr(eng._cfg, "perf_mode", "auto")) == "normal"
        st = getattr(cap, "stats", None)
        if isinstance(st, dict):
            s.capture_switches = int(st.get("switches") or 0)
            s.capture_last_switch = st.get("last_switch")
        m = getattr(eng._detector, "matcher", None) if eng._detector is not None else None
        sc_ = getattr(m, "scale", None) if m is not None else None
        s.icon_scale = float(sc_) if isinstance(sc_, (int, float)) and sc_ > 0 else None
    except Exception:
        log.debug("selfcheck snapshot failed", exc_info=True)
    return s


def apply_actions(eng: Any, actions: Iterable[Action], t: float) -> None:
    """Carry out the self-check's actions on the engine (analysis thread). Never raises."""
    sc = getattr(eng, "_selfcheck", None)
    for a in actions:
        try:
            _apply_one(eng, sc, a, float(t))
        except Exception:
            log.debug("selfcheck action %s failed", a.kind, exc_info=True)


def _matcher(eng: Any) -> Any:
    return getattr(eng._detector, "matcher", None) if eng._detector is not None else None


def _apply_one(eng: Any, sc: Any, a: Action, t: float) -> None:
    kind = a.kind
    if kind == "capture_switch":
        cap = eng._capture
        if cap is not None and callable(getattr(cap, "disable", None)) and callable(getattr(cap, "other", None)) \
                and getattr(cap, "current", None) and cap.other():
            cap.disable(cap.current, "self-check: black / frozen frames")
    elif kind == "capture_recreate":
        if eng._window_finder is None and eng._frame_source is None:
            eng._diag_req["recreate_capture"] = True          # a fresh SmartCapture (all backends again)
    elif kind == "relocate":
        eng.request_relocate()
    elif kind == "perf_level":
        set_level = getattr(eng._budget, "set_load_level", None)
        if callable(set_level):
            set_level(int(a.arg or 0))
            eng._applied_profile = None                     # knobs pushed at the next tick
    elif kind == "recalibrate":
        m = _matcher(eng)
        frame = eng._frame
        if m is not None and frame is not None and callable(getattr(m, "calibrate", None)):
            calib = getattr(getattr(m, "_state", None), "calib", None)
            if isinstance(calib, list):
                calib.clear()                              # the new sweep alone decides (not a median with old ones)
            scale = m.calibrate(frame, store=True)
            log.info("Self-check: icon scale recalibrated (%s)", scale)
    elif kind == "reload_icons":
        db = eng._champion_db()
        clear = getattr(db, "clear_icon_cache", None)
        if callable(clear):
            clear()
        m = _matcher(eng)
        if m is not None and callable(getattr(m, "set_entries", None)):
            m.set_entries(())                              # templates, calibration, learned icons forgotten...
        with eng._lock:                                    # ... rebuilt from the roster at the next poll
            eng._roster_sig = None
            eng._prefetched = False
    elif kind == "auto_diag":
        ok = False
        if bool(getattr(eng._cfg, "selfcheck_auto_diag", True)):
            ok = eng.start_diagnostic(announce=False, open_folder=False) is not None
        if sc is not None:
            sc.diag_started(t, ok)
    elif kind == "forget_track":
        fn = getattr(eng._tracker, "forget", None)
        if callable(fn):
            fn(str(a.arg))
    elif kind == "forget_tracks":
        fn = getattr(eng._tracker, "forget", None)
        if callable(fn):
            for k in a.arg or ():
                fn(str(k))
    elif kind == "voice_beep_only":
        eng._set_voice_override("bip" if a.arg else None)
    elif kind == "ai_block":
        eng._ai_game_block(bool(a.arg))
    elif kind == "notice":
        shown = bool(eng._selfcheck_notify(a.rule, str(a.arg or ""), t))
        if sc is not None:
            sc.mark_notice(a.rule, shown, t)


__all__ = ["RULES", "RULE_LABELS", "MSG", "NOTICES", "LOAD_LABELS", "API_OUTAGE_MAX_S", "Snapshot", "Action",
           "Problem", "SelfCheck", "summary_text", "snapshot_from_engine", "apply_actions"]
