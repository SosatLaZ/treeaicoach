"""Shared definitions of the analysis engine: tunables, UI status messages, :class:`EngineState`,
:class:`EngineStatus`, frame helpers and the base siege / ace rule.

Split out of ``engine.py`` (zero behaviour change); everything public is re-exported by
:mod:`treeaicoach.engine`, which stays the import path used by the app, tools and tests.
"""

from __future__ import annotations

import logging
import math
import re
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np

from treeaicoach.alerts import AlertKind
from treeaicoach.capture import Rect
from treeaicoach.fmtutil import finite_loose as _finite
from treeaicoach.live_client import GameInfo

log = logging.getLogger("treeaicoach.engine")   # same logger as before the split

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
GO_WORDS = ("à toi de jouer", "joue agressif", "utilise ton ultime", "vas-y", "va-y", "attaque", "engage", "force ", "punis")
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
DEAD_TEXT_KINDS = frozenset({"death_cause", "death_recap", "death", "objective", "objective_soon", "genie",
                             "macro", "siege"})
HUD_LINGER_S = 4.0               # a HUD line no longer valid stays this long (unless replaced)
HUD_GAP_S = 6.0                  # the card emptied: no new non-urgent line before this
HUD_RETIRE_S = 40.0              # a HUD line replaced by another is not shown again this long
HUD_DWELL_S = 6.0                # a HUD advice line stays at least this long (unless a danger replaces it)
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


_UNSEEN_RE = re.compile(r"(?i)(pas encore vu|caché depuis|invisible depuis|jungler invisible|, \w+( \w+)? invisible$)")
_BEHIND_RE = re.compile(r"(?i)(plus fort|d'avance|te domine|est \d+, pas toi|prudem|sans combattre|fort tôt)")


def _line_shape(text: str) -> str:
    """A HUD line without its live numbers ("Dragon dans 45 s" == "Dragon dans 44 s"); the lines
    that all say "your lane opponent is stronger, play safe" are ONE subject."""
    t = str(text or "")
    if _BEHIND_RE.search(t) and not re.search(r"(?i)^(pousse|prends|attaque|va taper|joue agressif)", t):
        return "#matchup-behind"
    if _UNSEEN_RE.search(t):
        return "#jungler-unseen"
    return re.sub(r"\d+([,.]\d+)?", "#", t)


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


# ------------------------------------------------------------------------------ base siege / ace
_BASE_TURRET_RE = re.compile(r"_(?:[LR]_01|C_0[123])_")
SIEGE_EVENT_S = 45.0             # a base structure of ours fell this recently -> siege
ACE_EVENT_S = 35.0               # enemy ace this recently -> ace state
SIEGE_TEXT = "Défends ta base avec ton équipe : ils attaquent"
ACE_TEXT = "Défends ta base à ta réapparition, avec ton équipe"
ACE_TEXT_FAR = "Attends ton équipe en base : ne sors pas seul"


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
