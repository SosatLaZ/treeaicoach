"""Live macro coaching from the minimap + my own Live Client data (v2).

:class:`MapCoach` looks at the tracked minimap icons, the epic objective timers and my own
scores once per analysis tick and produces, rarely, ONE short spoken tip (``Alert`` of kind
``MACRO_TIP``, level INFO) plus a list of short live "insights" for the HUD.

Rules (each with its own cooldown; see the ``RULE_*`` constants):

* ``jungler_side``  - the enemy jungler just showed up on the other half of the map than my
  lane: "Leur jungler est en bas : tu peux jouer plus agressif en haut."
* ``jungler_unseen`` - the enemy jungler has not been seen for 45 s while I am in lane:
  "Jungler ennemi pas vu depuis 45 s : prudence." (once per disappearance)
* ``missing``       - >= 3 enemies seen in the last minute are now hidden, I am in a lane far
  from my towers: "3 ennemis disparus : reste prudent."
* ``objective_setup`` - 40-55 s before a dragon / baron / herald / grubs / Atakhan spawn:
  "Dragon dans 45 s : préparez la vision, 2 ennemis visibles en bas."
* ``objective_window`` - an epic monster is up and >= 4 enemies are visible far from its pit:
  "Baron dispo et 4 ennemis visibles en bas : bonne fenêtre pour Baron."
* ``numbers``       - visible numbers around me (not a gank: nobody needs to be approaching):
  "3 contre 1 autour de toi, recule." / "3 contre 1 autour de toi : bonne fenêtre pour engager."
* ``pressure``      - >= 4 visible enemies grouped: "L'équipe ennemie est groupée en bas : tu
  peux pousser en haut."
* ``cs_check``      - CS/min checkpoints at 10:00 and 20:00: "7,2 CS par minute, objectif 8."
* ``vision``        - my ward score has not moved for 3 min: "Pense à placer une balise."
* ``level6``        - "Niveau 6 : cherche une action avec ton ultime." (once)
* ``deep``          - I am in the enemy jungle while their jungler is unseen for 30 s:
  "Tu es dans la jungle ennemie et leur jungler est invisible : attention."

Extra rules (v2.1):

* ``wave_push`` / ``wave_back`` / ``wave_big`` - minion waves read from the minimap dots
  (:mod:`treeaicoach.waves`): "Ta vague pousse vers leur tour : bon moment pour rentrer après
  l'avoir poussée.", "La vague revient vers toi : attends-la sous ta tour.", "Grosse vague
  ennemie qui arrive en bas."
* ``lane_left`` / ``lane_recall`` - my lane opponent left the lane for > 8 s / was seen back in
  his base: "Darius a quitté la voie : pousse et prends des plaques, ping s'il roam." /
  "Darius est rentré : pousse ta vague et récupère des plaques."
* ``bot_missing`` - both enemy bot laners disappeared: "Les deux bot ennemis ont disparu : prudence."
* scoreboard (public Tab data of the Live Client API: levels, items, kills):
  ``level_diff`` ("Tu as 2 niveaux d'avance sur Darius : joue agressif."), ``item_spike``
  ("Darius vient de finir Couperet noir : attention à son pic de puissance."), ``jg_level6``
  ("Leur jungler est niveau 6 avant le vôtre : prudence."), ``kill_lead`` (every 5 min).
* ``lane_dead`` / ``jungler_dead`` - my lane opponent / their jungler is dead (public Tab data):
  "Darius est mort : pousse ta vague et prends des plaques." / "Leur jungler est mort : bonne
  fenêtre pour le dragon." (dead enemies never count as "disparus" / "a quitté la voie")
* ``objective_trade`` - >= 3 enemies on one pit: "4 ennemis au dragon : prenez les larves ou
  des tours en haut."
* ``jungler_side`` is role aware: a jungler gets invade / counter-gank suggestions.

Policy: at most one tip every :data:`GLOBAL_GAP_S` (35 s; the "numbers" disadvantage warning
only needs :data:`SAFETY_GAP_S` and the short "enemy dead" windows :data:`WINDOW_GAP_S` since
the previous tip), nothing while a gank threat is
active nor during :data:`QUIET_AFTER_THREAT_S` after it, nothing about positions while I am
dead. **Safe mode** (``cfg.safe_mode``): only the objective timing and my personal data are
used - no tip or insight derived from enemy positions.

Riot rules: only what the minimap shows + the official Live Client API. No enemy cooldown,
ultimate or summoner spell tracking of any kind.

Pure Python (+ geometry), thread-safe, never raises from its public methods.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Any, Iterable

from treeaicoach import geometry
from treeaicoach.alerts import Alert, AlertKind, Level

log = logging.getLogger(__name__)

_MACRO: Any = getattr(AlertKind, "MACRO_TIP", AlertKind.OBJECTIVE_SOON)

# -------------------------------------------------------------------------- tunables
GLOBAL_GAP_S = 35.0            # min time between two spoken tips
SAFETY_GAP_S = 10.0            # the "outnumbered" warning only needs this since the last tip
WINDOW_GAP_S = 15.0            # short-lived windows (an enemy just died) only need this
QUIET_AFTER_THREAT_S = 8.0     # no tip during / just after a gank threat
NEAR_RADIUS = 0.16             # "around me" (normalized minimap, ~2400 units)
NUMBERS_CONFIRM_S = 1.5        # the numbers situation must hold this long
PIT_RADIUS = 0.20              # "near the pit"
WINDOW_FAR = 0.45              # enemies at least this far from the pit -> objective window
GROUP_RADIUS = 0.20            # max distance to the centroid for a "grouped" enemy team
JUNGLER_UNSEEN_S = 45.0
DEEP_UNSEEN_S = 30.0
DEEP_CONFIRM_S = 2.0
MISSING_HIDDEN_S = 6.0         # hidden at least this long...
MISSING_RECENT_S = 60.0        # ...after being seen during the last minute
MISSING_MIN = 3
TOWER_SAFE_DIST = 0.13         # farther than this from every allied turret = "far from towers"
SETUP_WINDOW = (38.0, 55.0)    # seconds before a spawn for the setup tip
CS_CHECKPOINTS = (600.0, 1200.0)
CS_CHECK_WINDOW_S = 75.0
VISION_STALE_S = 180.0         # game seconds without ward score progress
VISION_MIN_GT = 300.0
JUNGLE_RULES_MIN_GT = 150.0
PRESSURE_MIN_GT = 600.0
JUNGLER_SIDE_FRESH_S = 4.0     # a jungler sighting is "new" this long after appeared_at
INSIGHT_JUNGLER_S = 20.0       # jungler side insight lasts this long after the sighting
MAX_EXTRAPOLATION_S = 2.5
LANE_LEFT_S = 8.0              # lane opponent hidden this long (after being seen in my lane)
LANE_LEFT_MAX_S = 60.0
LANE_RULES_MIN_GT = 150.0
PLATES_END_GT = 840.0          # turret plates fall at 14:00
WAVE_PUSH_S = 0.62             # meeting point (from my base) for "ta vague pousse"
WAVE_BACK_S = 0.42
WAVE_BIG_ENEMY = 7
TRADE_PIT_R = 0.22
TRADE_MIN = 3
LEVEL_DIFF_MIN = 2
ITEM_ANNOUNCE_S = 60.0         # an item completion is news for this long
KILL_MARKS = tuple(float(m) for m in range(600, 3601, 300))
KILL_LEAD_MIN = 3

RULE_COOLDOWN_S: dict[str, float] = {
    "jungler_side": 60.0, "jungler_unseen": 60.0, "missing": 60.0, "objective_setup": 30.0,
    "objective_window": 120.0, "numbers_bad": 30.0, "numbers_good": 60.0, "pressure": 90.0,
    "cs_check": 60.0, "vision": 180.0, "level6": 1e9, "deep": 60.0,
    "wave_push": 120.0, "wave_back": 120.0, "wave_big": 90.0, "lane_left": 45.0, "lane_recall": 45.0,
    "bot_missing": 90.0, "level_diff": 180.0, "item_spike": 20.0, "jg_level6": 1e9, "kill_lead": 120.0,
    "objective_trade": 120.0, "lane_dead": 45.0, "jungler_dead": 90.0,
    # v3 macro (Challenger fundamentals)
    "recall_item": 150.0, "freeze": 240.0, "crash_roam": 150.0, "first_item": 1e9, "level2": 1e9,
    "baron_pick": 90.0, "objective_wave": 60.0,
}
#: Rules that use enemy positions (disabled in safe mode).
ENEMY_RULES: frozenset[str] = frozenset({
    "jungler_side", "jungler_unseen", "missing", "objective_window", "numbers_bad", "numbers_good",
    "pressure", "deep", "wave_push", "wave_back", "wave_big", "lane_left", "lane_recall", "bot_missing",
    "objective_trade"})
#: Rules that need me alive.
ALIVE_RULES: frozenset[str] = frozenset({
    "jungler_side", "jungler_unseen", "missing", "numbers_bad", "numbers_good", "pressure", "deep",
    "vision", "objective_window", "wave_push", "wave_back", "wave_big", "lane_left", "lane_recall",
    "bot_missing", "objective_trade", "level_diff", "lane_dead", "jungler_dead", "recall_item", "freeze",
    "crash_roam", "level2", "baron_pick", "objective_wave"})
PRIORITY: dict[str, int] = {
    "numbers_bad": 100, "deep": 90, "wave_big": 87, "objective_trade": 86, "objective_window": 85,
    "objective_setup": 80, "jungler_dead": 79, "lane_dead": 79, "lane_recall": 78, "lane_left": 77, "missing": 75, "bot_missing": 72,
    "jungler_unseen": 70, "jungler_side": 65, "item_spike": 64, "level_diff": 62, "wave_back": 61,
    "wave_push": 60, "numbers_good": 59, "jg_level6": 58, "pressure": 55, "kill_lead": 45, "cs_check": 40,
    "level6": 35, "vision": 30,
    "baron_pick": 84, "objective_wave": 81, "level2": 76, "recall_item": 66, "crash_roam": 63,
    "first_item": 61, "freeze": 57,
}

CS_TARGET: dict[str, float] = {"TOP": 7.0, "MIDDLE": 7.0, "BOTTOM": 7.5, "JUNGLE": 5.5}

_DRAGON_PIT = (geometry.DRAGON_PIT[0], geometry.DRAGON_PIT[1])
_BARON_PIT = (geometry.BARON_PIT[0], geometry.BARON_PIT[1])
#: objective key -> (pit uv, map half) ; Atakhan's pit depends on the game: no side.
PITS: dict[str, tuple[tuple[float, float], str] | None] = {
    "dragon": (_DRAGON_PIT, "bot"), "elder": (_DRAGON_PIT, "bot"),
    "baron": (_BARON_PIT, "top"), "herald": (_BARON_PIT, "top"), "grubs": (_BARON_PIT, "top"),
    "atakhan": None,
}
_WINDOW_NAMES = {"dragon": "le dragon", "elder": "l'ancestral", "baron": "Baron", "herald": "le Héraut",
                 "grubs": "les larves"}
#: Turret positions (game units) per team (outer / inner / inhibitor turrets).
_TURRETS_GAME: dict[str, tuple[tuple[int, int], ...]] = {
    "ORDER": ((981, 10441), (1512, 6699), (1169, 4287), (5846, 6396), (5048, 4812), (3651, 3696),
              (10504, 1029), (6919, 1483), (4281, 1253)),
    "CHAOS": ((4318, 13875), (7943, 13411), (10481, 13650), (8955, 8510), (9767, 10113),
              (11134, 11207), (13866, 4505), (13327, 8226), (13624, 10572)),
}
TURRETS: dict[str, list[tuple[float, float]]] = {
    team: [geometry.game_to_uv(x, y) for x, y in pts] for team, pts in _TURRETS_GAME.items()}

SIDE_FR = {"top": "en haut", "mid": "au milieu", "bot": "en bas"}
LANE_OPP_ROLES = {"TOP": ("TOP",), "MIDDLE": ("MIDDLE",), "BOTTOM": ("BOTTOM", "UTILITY"),
                  "UTILITY": ("BOTTOM", "UTILITY")}
#: Major (legendary) items worth announcing, itemID -> French name (Live Client ids).
ITEM_NAMES_FR: dict[int, str] = {
    3078: "Force de la trinité", 3071: "Couperet noir", 3031: "Lame d'infini", 3089: "Coiffe de Rabadon",
    3153: "Lame du roi déchu", 3157: "Sablier de Zhonya", 3036: "Salutations de Dominik",
    3072: "Soif-de-sang", 3074: "Hydre vorace", 3748: "Hydre titanesque", 6672: "Tueur de krakens",
    6673: "Arc-bouclier immortel", 3161: "Lance de Shojin", 3508: "Collecteur d'essence",
    3094: "Canon ultrarapide", 3046: "Danseur fantôme", 3087: "Surin de Statikk", 3115: "Dent de Nashor",
    3135: "Bâton du vide", 3165: "Morellonomicon", 4645: "Flamme-ombre", 6653: "Tourment de Liandry",
    3100: "Fléau de liche", 3152: "Ceinture-fusée hextech", 6655: "Compagnon de Luden",
    3142: "Spectre de Youmuu", 6692: "Éclipse", 6694: "Rancune de Serylda", 3814: "Lame de la nuit",
    3026: "Ange gardien", 3065: "Visage spirituel", 3075: "Cotte épineuse", 3068: "Égide de feu solaire",
    3083: "Armure de Warmog", 3143: "Présage de Randuin", 3742: "Plaque du mort", 6333: "Danse de la mort",
    3053: "Gage de Sterak", 6610: "Ciel fracturé", 3124: "Lame enragée de Guinsoo", 3091: "Fin de l'esprit",
    3085: "Ouragan de Runaan", 3033: "Rappel mortel", 3139: "Cimeterre mercuriel", 3156: "Gueule de Malmortius",
}
ROLE_LANE = {"TOP": "top", "MIDDLE": "mid", "BOTTOM": "bot", "UTILITY": "bot"}


# -------------------------------------------------------------------------- helpers
def _finite(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        f = float(x)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


def _uv(pos: Any) -> tuple[float, float] | None:
    try:
        u, v = _finite(pos[0]), _finite(pos[1])
    except (TypeError, IndexError, KeyError):
        return None
    if u is None or v is None:
        return None
    return min(1.0, max(0.0, u)), min(1.0, max(0.0, v))


def fmt_dec(x: float, decimals: int = 1) -> str:
    """French decimal: ``7.25`` -> ``"7,3"``; integers without decimals (``8``)."""
    if float(x).is_integer():
        return str(int(x))
    return f"{x:.{decimals}f}".replace(".", ",")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n > 1 else ''}"


def map_side(u: float, v: float) -> str:
    """``"top"`` / ``"mid"`` / ``"bot"``: lane of the zone, else half of the map (mid band = mid)."""
    try:
        lane = geometry.lane_of(geometry.classify_zone(u, v))
    except Exception:
        lane = None
    if lane:
        return lane
    if abs(u + v - 1.0) < 0.10:
        return "mid"
    return geometry.side_of(u, v)


def _opposite(side: str) -> str | None:
    return {"top": "bot", "bot": "top"}.get(side)


def _track_pos(tr: Any) -> tuple[float, float] | None:
    try:
        return _uv(tr.position())
    except Exception:
        return None


@dataclass
class _Ctx:
    """Everything a rule needs for one tick."""

    t: float
    gt: float
    team: str | None
    enemy_team: str | None
    me_pos: tuple[float, float] | None
    my_zone: Any
    my_lane: str | None             # lane I am standing in (None: jungle / river / base)
    role_lane: str | None           # lane of my role
    my_role: str | None
    dead: bool
    safe: bool
    enemies_vis: list[tuple[Any, tuple[float, float]]]
    allies_vis: list[tuple[Any, tuple[float, float]]]
    enemies_all: list[Any]
    jungler_alias: str | None
    jungler: Any                    # Track or None
    jungler_hidden_s: float | None  # None: unknown jungler
    objectives: list[Any]
    me_player: Any
    game: Any = None
    waves: dict = None              # lane -> waves.LaneWave (None / {} when unknown)
    opponents: list = None          # [(alias, display name, PlayerInfo | None, Track | None)]
    dead_enemies: frozenset = frozenset()   # lower-case aliases of dead enemies (Live Client)


class MapCoach:
    """Live macro tips + HUD insights. See the module docstring. Thread-safe."""

    def __init__(self, cfg: Any = None) -> None:
        self._lock = threading.RLock()
        self._enabled = True
        self._safe = False
        self._scoreboard_on = True
        self._item_tips = True
        self.apply_config(cfg)
        self._clear()

    # -- public -------------------------------------------------------------------------
    def apply_config(self, cfg: Any) -> None:
        """``cfg.safe_mode``, the optional ``cfg.macro_coach`` switch (default on) and
        ``cfg.coach_scoreboard_tips`` (default on: level / item / kill scoreboard tips)."""
        try:
            on = getattr(cfg, "macro_coach", True)
            safe = getattr(cfg, "safe_mode", False)
            board = getattr(cfg, "coach_scoreboard_tips", True)
            with self._lock:
                self._enabled = on if isinstance(on, bool) else True
                self._scoreboard_on = board if isinstance(board, bool) else True
                self._safe = bool(safe) if isinstance(safe, bool) else False
        except Exception:
            log.exception("MapCoach.apply_config failed")

    def set_item_tips(self, on: bool) -> None:
        """Enable / disable the ``item_spike`` tip (off when scoreboard.ScoreboardAnalyzer speaks them)."""
        with self._lock:
            self._item_tips = bool(on)

    def reset(self) -> None:
        """Forget everything (new game)."""
        with self._lock:
            self._clear()

    def update(self, t: float, tracker: Any, game: Any, roles: Any = None,
               objectives_states: Iterable[Any] | None = None,
               my_pos: tuple[float, float] | None = None, *, threat: int = 0,
               minimap_bgr: Any = None) -> list[Alert]:
        """One analysis tick. ``threat`` = current gank threat level (0 safe, 1 warning, 2 danger);
        ``minimap_bgr`` = the minimap crop of this tick (minion waves, analysed at most 1 Hz).

        Returns ``[]`` or ``[one MACRO_TIP alert]``. Never raises.
        """
        try:
            with self._lock:
                self._roles = roles
                self._frame = minimap_bgr
                return self._update_locked(t, tracker, game, roles, objectives_states, my_pos, threat)
        except Exception:
            log.exception("MapCoach.update failed")
            return []

    def insights(self) -> list[str]:
        """Short live insights for the HUD, most relevant first (may be empty)."""
        with self._lock:
            return list(self._insights)

    def insight(self) -> str | None:
        """The most relevant live insight (one HUD line) or None."""
        with self._lock:
            return self._insights[0] if self._insights else None

    def insight_items(self) -> list[tuple[int, str, str]]:
        """``(priority, text, category)`` of the live insights, most relevant first; category
        ``"objective"`` (timer lines, already on the HUD objectives row) or ``"live"``."""
        with self._lock:
            return list(self._insight_items)

    def facts(self) -> dict[str, Any]:
        """What the coach knows right now (for :class:`StanceAdvisor` / :mod:`treeaicoach.tips`):
        ``gt, dead, safe, my_role, role_lane, my_lane, in_base, jungler{...}, missing, numbers,
        wave, opponents[...], objectives[...]``. ``{}`` outside a game."""
        with self._lock:
            return dict(self._facts)

    def pressure(self) -> dict[str, Any] | None:
        """Visible enemy team pressure: ``{"visible", "centroid", "side", "grouped"}`` (None in safe mode)."""
        with self._lock:
            return dict(self._pressure) if self._pressure is not None else None

    def waves(self) -> dict[str, Any]:
        """Latest per-lane wave state (``lane -> LaneWave.as_dict()``), {} if unknown."""
        with self._lock:
            return {k: v.as_dict() for k, v in (self._last_waves or {}).items()}

    def last_tips(self) -> list[tuple[float, str, str]]:
        """``(t, rule, text)`` of the tips produced (diagnostics / tests), oldest first."""
        with self._lock:
            return list(self._said)

    # -- internals ----------------------------------------------------------------------
    def _clear(self) -> None:
        self._last_tip_t: float | None = None
        self._rule_t: dict[str, float] = {}
        self._threat_t: float | None = None
        self._last_t: float | None = None
        self._last_gt: float | None = None
        self._jg_ref_t: float | None = None          # reference when the jungler was never seen
        self._jg_side_done: float | None = None      # appeared_at already used for jungler_side
        self._jg_side_info: tuple[float, str, str] | None = None   # (t, jungler side, my side)
        self._jg_unseen_done: float | None = None    # last_seen already used for jungler_unseen
        self._numbers_since: dict[str, float] = {}
        self._deep_since: float | None = None
        self._setup_done: set[tuple[str, int]] = set()
        self._cs_done: set[float] = set()
        self._ward_score: float | None = None
        self._ward_change_gt: float | None = None
        self._level6_done = False
        self._insights: list[str] = []
        self._insight_items: list[tuple[int, str, str]] = []
        self._facts: dict[str, Any] = {}
        self._pressure: dict[str, Any] | None = None
        self._said: list[tuple[float, str, str]] = []
        self._roles: Any = None
        self._frame: Any = None
        try:
            from treeaicoach.waves import WaveTracker

            self._wave_tracker: Any = WaveTracker()
        except Exception:
            self._wave_tracker = None
        self._last_waves: dict[str, Any] = {}
        self._lane_left_done: dict[str, float] = {}
        self._bot_missing_done: tuple | None = None
        self._level_diff_said: int = 0
        self._items_seen: dict[str, set[int]] = {}
        self._item_news: list[tuple[float, str, str, int]] = []   # (t, alias, name, item id)
        self._jg6_done = False
        self._kill_marks_done: set[float] = set()
        self._dead_done: dict[str, float] = {}      # alias -> game time of the death already used
        self._first_item_done = False
        self._legendaries: int | None = None
        self._level2_done = False
        self._obj_wave_done: set = set()

    def _game_time(self, game: Any, t: float) -> float:
        gt = _finite(getattr(game, "game_time", None)) or 0.0
        fetched = _finite(getattr(game, "fetched_at", None))
        if fetched is not None:
            dt = t - fetched
            if 0.0 <= dt <= MAX_EXTRAPOLATION_S:
                gt += dt
        return gt

    def _context(self, t: float, tracker: Any, game: Any, roles: Any, objectives: Any,
                 my_pos: Any) -> _Ctx:
        me_player = getattr(game, "me", None)
        team = geometry.normalize_team(getattr(me_player, "team", None))
        enemy_team = {"ORDER": "CHAOS", "CHAOS": "ORDER"}.get(team or "")
        pos = _uv(my_pos) if my_pos is not None else None
        zone = geometry.classify_zone(*pos) if pos is not None else None
        my_lane = geometry.lane_of(zone) if zone is not None else None
        my_role = None
        try:
            my_role = roles.my_role() if roles is not None and hasattr(roles, "my_role") else None
        except Exception:
            my_role = None
        if not my_role:
            my_role = str(getattr(me_player, "position", "") or "").upper() or None
        enemies_vis: list[tuple[Any, tuple[float, float]]] = []
        allies_vis: list[tuple[Any, tuple[float, float]]] = []
        enemies_all: list[Any] = []
        if tracker is not None:
            try:
                enemies_all = list(tracker.enemies(visible_only=False) or [])
            except Exception:
                enemies_all = []
            for tr in enemies_all:
                if getattr(tr, "visible", False):
                    p = _track_pos(tr)
                    if p is not None:
                        enemies_vis.append((tr, p))
            try:
                for tr in tracker.allies(visible_only=True) or []:
                    p = _track_pos(tr)
                    if p is not None:
                        allies_vis.append((tr, p))
            except Exception:
                pass
        jalias = None
        try:
            if roles is not None and hasattr(roles, "enemy_jungler"):
                jalias = roles.enemy_jungler()
        except Exception:
            jalias = None
        if not jalias:
            try:
                p = game.enemy_jungler() if hasattr(game, "enemy_jungler") else None
                jalias = getattr(p, "champion_alias", None) if p is not None else None
            except Exception:
                jalias = None
        jtrack = None
        if jalias and tracker is not None:
            try:
                jtrack = tracker.get(jalias)
            except Exception:
                jtrack = None
            if jtrack is None:
                low = str(jalias).lower()
                jtrack = next((tr for tr in enemies_all if str(getattr(tr, "alias", "") or "").lower() == low), None)
        jhidden: float | None = None
        if jalias:
            if jtrack is not None:
                jhidden = 0.0 if getattr(jtrack, "visible", False) else max(0.0, t - float(jtrack.last_seen))
            elif self._jg_ref_t is not None:
                jhidden = max(0.0, t - self._jg_ref_t)
        waves: dict = {}
        if self._wave_tracker is not None and not self._safe:
            if self._frame is not None:
                excl = [p for _tr, p in enemies_vis] + [p for _tr, p in allies_vis] + ([pos] if pos else [])
                self._wave_tracker.update(t, self._frame, excl, team)
            waves = self._wave_tracker.waves(t)
        self._last_waves = waves
        return _Ctx(t=t, gt=self._game_time(game, t), team=team, enemy_team=enemy_team, me_pos=pos,
                     my_zone=zone, my_lane=my_lane, role_lane=ROLE_LANE.get(my_role or ""), my_role=my_role,
                     dead=bool(getattr(me_player, "is_dead", False)), safe=self._safe,
                     enemies_vis=enemies_vis, allies_vis=allies_vis, enemies_all=enemies_all,
                     jungler_alias=jalias, jungler=jtrack, jungler_hidden_s=jhidden,
                     objectives=list(objectives or []), me_player=me_player, game=game, waves=waves,
                     opponents=self._opponents(game, roles, my_role, tracker, enemies_all),
                     dead_enemies=frozenset(str(getattr(p, "champion_alias", "") or "").lower()
                                            for p in (getattr(game, "enemies", None) or [])
                                            if bool(getattr(p, "is_dead", False))))

    @staticmethod
    def _opponents(game: Any, roles: Any, my_role: str | None, tracker: Any,
                   enemies_all: list[Any]) -> list[tuple[str, str, Any, Any]]:
        """My lane opponents: roles.lane_opponents(), else enemies with the facing Riot position."""
        aliases: list[str] = []
        try:
            if roles is not None and hasattr(roles, "lane_opponents"):
                aliases = sorted(str(a) for a in (roles.lane_opponents() or ()))
        except Exception:
            aliases = []
        enemies = list(getattr(game, "enemies", None) or [])
        if not aliases:
            want = LANE_OPP_ROLES.get(my_role or "", ())
            aliases = [p.champion_alias for p in enemies
                       if str(getattr(p, "position", "") or "").upper() in want and p.champion_alias]
        out = []
        for a in aliases:
            p = next((x for x in enemies if str(x.champion_alias).lower() == a.lower()), None)
            name = (getattr(p, "champion_name", "") or a) if p is not None else a
            tr = None
            try:
                tr = tracker.get(a) if tracker is not None else None
            except Exception:
                tr = None
            if tr is None:
                tr = next((x for x in enemies_all if str(getattr(x, "alias", "") or "").lower() == a.lower()), None)
            out.append((a, name, p, tr))
        return out

    def _update_locked(self, t: Any, tracker: Any, game: Any, roles: Any, objectives: Any,
                       my_pos: Any, threat: Any) -> list[Alert]:
        now = _finite(t)
        if now is None:
            return []
        if self._last_t is not None and now < self._last_t - 1.0:
            self._clear()                                   # clock went back: new timeline
        self._last_t = now
        if game is None or getattr(game, "me", None) is None or not bool(getattr(game, "is_summoners_rift", False)):
            self._insights, self._pressure, self._insight_items, self._facts = [], None, [], {}
            return []
        gt_now = self._game_time(game, now)
        if self._last_gt is not None and gt_now < self._last_gt - 5.0:
            self._clear()                                   # another game
            self._last_t = now
        self._last_gt = gt_now
        if self._jg_ref_t is None and gt_now >= 90.0:
            self._jg_ref_t = now
        ctx = self._context(now, tracker, game, roles, objectives, my_pos)
        self._track_personal(ctx)
        candidates = self._candidates(ctx)
        self._insights = self._build_insights(ctx)
        try:
            self._facts = self._make_facts(ctx)
        except Exception:
            log.debug("MapCoach facts failed", exc_info=True)
            self._facts = {}
        lvl = int(_finite(threat) or 0)
        if lvl >= Level.WARNING:
            self._threat_t = now
        if not self._enabled:
            return []
        if self._threat_t is not None and 0.0 <= now - self._threat_t < QUIET_AFTER_THREAT_S:
            return []
        best: tuple[int, str, str] | None = None
        for rule, text in candidates:
            if not self._allowed(rule, ctx):
                continue
            prio = PRIORITY.get(rule, 0)
            if best is None or prio > best[0]:
                best = (prio, rule, text)
        if best is None:
            return []
        _prio, rule, text = best
        self._commit(rule, ctx)
        self._last_tip_t = now
        self._rule_t[rule] = now
        self._said.append((now, rule, text))
        del self._said[:-50]
        return [Alert(kind=_MACRO, level=Level.INFO, text=text, key=f"macro_tip:{rule}", t=now)]

    def _allowed(self, rule: str, ctx: _Ctx) -> bool:
        if ctx.safe and rule in ENEMY_RULES:
            return False
        if ctx.dead and rule in ALIVE_RULES:
            return False
        last = self._rule_t.get(rule)
        if last is not None and 0.0 <= ctx.t - last < RULE_COOLDOWN_S.get(rule, 60.0):
            return False
        if self._last_tip_t is not None:
            gap = (SAFETY_GAP_S if rule == "numbers_bad" else WINDOW_GAP_S if rule in (
                "lane_dead", "jungler_dead", "objective_setup", "objective_wave", "baron_pick")
                   else GLOBAL_GAP_S)
            if 0.0 <= ctx.t - self._last_tip_t < gap:
                return False
        return True

    def _commit(self, rule: str, ctx: _Ctx) -> None:
        """Remember the one-shot conditions consumed by the spoken tip."""
        if rule == "jungler_side" and ctx.jungler is not None:
            self._jg_side_done = getattr(ctx.jungler, "appeared_at", None) or getattr(ctx.jungler, "first_seen", None)
        elif rule == "jungler_unseen":
            self._jg_unseen_done = getattr(ctx.jungler, "last_seen", None) if ctx.jungler is not None else -1.0
        elif rule == "objective_setup":
            key = self._setup_key(ctx)
            if key is not None:
                self._setup_done.add(key)
        elif rule == "cs_check":
            cp = self._cs_checkpoint(ctx)
            if cp is not None:
                self._cs_done.add(cp)
        elif rule == "vision":
            self._ward_change_gt = ctx.gt
        elif rule == "level6":
            self._level6_done = True
        elif rule in ("lane_left", "lane_recall"):
            for alias, _n, _p, tr in ctx.opponents or []:
                if tr is not None and not getattr(tr, "visible", False):
                    self._lane_left_done[alias] = float(getattr(tr, "last_seen", 0.0))
        elif rule == "bot_missing":
            self._bot_missing_done = self._bot_marker(ctx)
        elif rule == "level_diff":
            self._level_diff_said = self._level_diff(ctx)[0]
        elif rule == "item_spike":
            if self._item_news:
                self._item_news.pop(0)
        elif rule == "jg_level6":
            self._jg6_done = True
        elif rule in ("lane_dead", "jungler_dead"):
            for alias in self._dead_targets(ctx, rule):
                self._dead_done[alias] = ctx.gt
        elif rule == "first_item":
            self._first_item_done = True
        elif rule == "level2":
            self._level2_done = True
        elif rule == "objective_wave":
            tgt = self._setup_target_window(ctx, 50.0, 80.0)
            if tgt is not None:
                self._obj_wave_done.add((str(getattr(tgt[0], "key", "")), int(round(ctx.gt + tgt[1]) // 30)))
        elif rule == "kill_lead":
            mark = self._kill_mark(ctx)
            if mark is not None:
                self._kill_marks_done.add(mark)

    # ---------------------------------------------------------------- personal tracking
    def _track_items(self, ctx: _Ctx) -> None:
        """Remember the enemies' items (public scoreboard) and queue fresh major item completions."""
        for p in getattr(ctx.game, "enemies", None) or []:
            alias = str(getattr(p, "champion_alias", "") or "")
            if not alias:
                continue
            items = {int(i) for i in (getattr(p, "items", None) or []) if isinstance(i, int) and not isinstance(i, bool)}
            known = self._items_seen.get(alias)
            self._items_seen[alias] = items
            if known is None:
                continue                                    # first look: nothing is "new"
            for item in sorted(items - known):
                if item in ITEM_NAMES_FR:
                    name = str(getattr(p, "champion_name", "") or alias)
                    self._item_news.append((ctx.t, alias, name, item))
        self._item_news = [x for x in self._item_news if 0.0 <= ctx.t - x[0] <= ITEM_ANNOUNCE_S][-6:]

    def _track_personal(self, ctx: _Ctx) -> None:
        try:
            self._track_items(ctx)
        except Exception:
            log.debug("item tracking failed", exc_info=True)
        me = ctx.me_player
        scores = getattr(me, "scores", None)
        ward = _finite(scores.get("wardScore")) if isinstance(scores, dict) else None
        if ward is None:
            return
        if self._ward_score is None or ward > self._ward_score + 1e-6:
            self._ward_score = ward
            self._ward_change_gt = ctx.gt
        elif ward < self._ward_score - 1e-6:          # new game / data glitch
            self._ward_score = ward
            self._ward_change_gt = ctx.gt

    # ---------------------------------------------------------------- rules
    def _candidates(self, ctx: _Ctx) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for fn in (self._rule_numbers, self._rule_deep, self._rule_objectives, self._rule_missing,
                   self._rule_jungler, self._rule_pressure, self._rule_cs, self._rule_level6,
                   self._rule_vision, self._rule_waves, self._rule_lane, self._rule_bot_missing,
                   self._rule_scoreboard, self._rule_trade, self._rule_dead, self._rule_macro_v3):
            try:
                out.extend(fn(ctx))
            except Exception:
                log.debug("MapCoach rule %s failed", getattr(fn, "__name__", fn), exc_info=True)
        return out

    def _numbers(self, ctx: _Ctx) -> tuple[int, int]:
        if ctx.me_pos is None:
            return 0, 0
        en = sum(1 for _tr, p in ctx.enemies_vis if geometry.dist(p, ctx.me_pos) < NEAR_RADIUS)
        al = 1 + sum(1 for _tr, p in ctx.allies_vis if geometry.dist(p, ctx.me_pos) < NEAR_RADIUS)
        return en, al

    def _rule_numbers(self, ctx: _Ctx) -> list[tuple[str, str]]:
        if ctx.me_pos is None or ctx.dead or self._in_my_base(ctx):
            self._numbers_since.clear()
            return []
        en, al = self._numbers(ctx)
        state = None
        if en >= 2 and en - al >= 2:
            state = "numbers_bad"
        elif en >= 1 and al - en >= 2:
            state = "numbers_good"
        for k in list(self._numbers_since):
            if k != state:
                del self._numbers_since[k]
        if state is None:
            return []
        since = self._numbers_since.setdefault(state, ctx.t)
        if ctx.t - since < NUMBERS_CONFIRM_S:
            return []
        if state == "numbers_bad":
            return [(state, f"{en} contre {al} autour de toi, recule.")]
        return [(state, f"{al} contre {en} autour de toi : bonne fenêtre pour engager.")]

    def _in_my_base(self, ctx: _Ctx) -> bool:
        z = ctx.my_zone
        return z is not None and geometry.is_base(z) and geometry.zone_owner(z) == (ctx.team or "ORDER")

    def _in_enemy_jungle(self, ctx: _Ctx) -> bool:
        z = ctx.my_zone
        return (z is not None and ctx.enemy_team is not None and geometry.is_jungle(z)
                and geometry.zone_owner(z) == ctx.enemy_team)

    def _rule_deep(self, ctx: _Ctx) -> list[tuple[str, str]]:
        if not self._in_enemy_jungle(ctx) or ctx.dead:
            self._deep_since = None
            return []
        if self._deep_since is None:
            self._deep_since = ctx.t
        hidden = ctx.jungler_hidden_s
        if ctx.jungler_alias and str(ctx.jungler_alias).lower() in ctx.dead_enemies:
            return []
        if ctx.t - self._deep_since < DEEP_CONFIRM_S or hidden is None or hidden < DEEP_UNSEEN_S:
            return []
        if ctx.my_role == "JUNGLE":
            return [("deep", "Tu es chez eux et leur jungler est invisible : vole vite et ressors.")]
        return [("deep", "Tu es dans la jungle ennemie et leur jungler est invisible : attention.")]

    def _remaining(self, s: Any, gt: float) -> float | None:
        rem = _finite(getattr(s, "remaining", None))
        nxt = _finite(getattr(s, "next_spawn", None))
        if nxt is not None:
            return nxt - gt
        return rem

    def _setup_target(self, ctx: _Ctx) -> tuple[Any, float] | None:
        """Objective whose spawn is inside the setup window (soonest first)."""
        best = None
        for s in ctx.objectives:
            if getattr(s, "alive", False):
                continue
            rem = self._remaining(s, ctx.gt)
            if rem is None or not SETUP_WINDOW[0] <= rem <= SETUP_WINDOW[1]:
                continue
            if best is None or rem < best[1]:
                best = (s, rem)
        return best

    def _setup_key(self, ctx: _Ctx) -> tuple[str, int] | None:
        tgt = self._setup_target(ctx)
        if tgt is None:
            return None
        s, _rem = tgt
        return (str(getattr(s, "key", "") or getattr(s, "name", "")), int(round(_finite(getattr(s, "next_spawn", 0)) or 0)))

    def _count_side(self, ctx: _Ctx, pit: tuple[float, float], side: str,
                    pts: list[tuple[Any, tuple[float, float]]]) -> int:
        return sum(1 for _tr, p in pts
                   if geometry.dist(p, pit) < PIT_RADIUS or (map_side(*p) == side
                                                             and not geometry.is_base(geometry.classify_zone(*p))))

    def _rule_objectives(self, ctx: _Ctx) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        tgt = self._setup_target(ctx)
        if tgt is not None:
            s, rem = tgt
            key = self._setup_key(ctx)
            if key not in self._setup_done:
                kind = str(getattr(s, "key", "") or "")
                name = str(getattr(s, "name", "") or "Objectif")
                secs = int(round(rem / 5.0) * 5)
                pit = PITS.get(kind)
                if pit is None:
                    out.append(("objective_setup", f"{name} dans {secs} s : regroupez-vous et préparez la vision."))
                else:
                    (pu, pv), side = pit
                    where = SIDE_FR[side]
                    if ctx.safe:
                        out.append(("objective_setup", f"{name} dans {secs} s : préparez la vision {where}."))
                    else:
                        n_en = self._count_side(ctx, (pu, pv), side, ctx.enemies_vis)
                        n_al = self._count_side(ctx, (pu, pv), side, ctx.allies_vis)
                        if ctx.me_pos is not None and self._count_side(ctx, (pu, pv), side, [(None, ctx.me_pos)]):
                            n_al += 1
                        if n_en >= 1:
                            txt = (f"{name} dans {secs} s : préparez la vision, "
                                   f"{_plural(n_en, 'ennemi')} {'visibles' if n_en > 1 else 'visible'} {where}.")
                        elif n_al >= 3:
                            txt = f"{name} dans {secs} s : vous êtes {n_al} {where}, placez la vision."
                        else:
                            txt = f"{name} dans {secs} s : préparez la vision {where}."
                        out.append(("objective_setup", txt))
        if not ctx.safe:
            for s in ctx.objectives:
                kind = str(getattr(s, "key", "") or "")
                pit = PITS.get(kind)
                if not getattr(s, "alive", False) or pit is None or kind not in _WINDOW_NAMES:
                    continue
                (pu, pv), side = pit
                far = [p for _tr, p in ctx.enemies_vis if geometry.dist(p, (pu, pv)) > WINDOW_FAR]
                if len(far) >= 4:
                    sides = [map_side(*p) for p in far]
                    where = SIDE_FR.get(max(set(sides), key=sides.count), "loin")
                    name = str(getattr(s, "name", "") or "Objectif")
                    out.append(("objective_window", f"{name} dispo et {len(far)} ennemis visibles {where} : "
                                                    f"bonne fenêtre pour {_WINDOW_NAMES[kind]}."))
                    break
        return out

    def _far_from_towers(self, ctx: _Ctx) -> bool:
        if ctx.me_pos is None:
            return False
        towers = TURRETS.get(ctx.team or "", [])
        if not towers:
            return True
        return min(geometry.dist(ctx.me_pos, p) for p in towers) > TOWER_SAFE_DIST

    def _missing(self, ctx: _Ctx) -> int:
        n = 0
        for tr in ctx.enemies_all:
            if getattr(tr, "visible", False) or not getattr(tr, "alias", None):
                continue
            if str(tr.alias).lower() in ctx.dead_enemies:
                continue                                    # dead (Tab), not "missing"
            hidden = ctx.t - float(getattr(tr, "last_seen", ctx.t))
            if MISSING_HIDDEN_S <= hidden <= MISSING_RECENT_S:
                n += 1
        return n

    def _rule_missing(self, ctx: _Ctx) -> list[tuple[str, str]]:
        if ctx.me_pos is None or ctx.my_lane is None or ctx.dead:
            return []
        n = self._missing(ctx)
        if n < MISSING_MIN or len(ctx.enemies_vis) > 5 - n or not self._far_from_towers(ctx):
            return []
        return [("missing", f"{min(n, 5)} ennemis disparus : reste prudent.")]

    def _rule_jungler(self, ctx: _Ctx) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if ctx.jungler_alias is None or ctx.gt < JUNGLE_RULES_MIN_GT or ctx.dead or ctx.me_pos is None:
            return out
        tr = ctx.jungler
        is_jungler = ctx.my_role == "JUNGLE"
        my_side = geometry.side_of(*ctx.me_pos) if is_jungler else (ctx.my_lane or ctx.role_lane)
        if tr is not None and getattr(tr, "visible", False):
            appeared = getattr(tr, "appeared_at", None) or getattr(tr, "first_seen", None)
            pos = _track_pos(tr)
            if pos is not None and appeared is not None:
                jside = map_side(*pos)
                fresh = (0.0 <= ctx.t - float(appeared) <= JUNGLER_SIDE_FRESH_S
                         and appeared != self._jg_side_done)
                far = geometry.dist(pos, ctx.me_pos) > 0.35
                if jside in ("top", "bot") and my_side in ("top", "mid", "bot") and my_side != jside:
                    self._jg_side_info = (ctx.t, jside, my_side)
                    if fresh and far:
                        if is_jungler:
                            text = (f"Leur jungler est {SIDE_FR[jside]} : envahis sa jungle "
                                    f"{'du haut' if jside == 'bot' else 'du bas'} ou prends tes camps.")
                        else:
                            text = (f"Leur jungler est {SIDE_FR[jside]} : tu peux jouer plus agressif "
                                    f"{SIDE_FR[my_side]}.")
                        out.append(("jungler_side", text))
                elif is_jungler and jside in ("top", "bot") and my_side == jside and fresh:
                    out.append(("jungler_side", f"Leur jungler est {SIDE_FR[jside]}, de ton côté : "
                                                f"prépare le contre-gank."))
        hidden = ctx.jungler_hidden_s
        jg_dead = str(ctx.jungler_alias).lower() in ctx.dead_enemies
        if hidden is not None and hidden >= JUNGLER_UNSEEN_S and ctx.my_lane is not None and not jg_dead:
            marker = getattr(tr, "last_seen", None) if tr is not None else -1.0
            if marker != self._jg_unseen_done:
                secs = int(hidden // 5 * 5)
                out.append(("jungler_unseen", f"Jungler ennemi pas vu depuis {secs} s : prudence."))
        return out

    def _pressure_info(self, ctx: _Ctx) -> dict[str, Any] | None:
        pts = [p for _tr, p in ctx.enemies_vis]
        if not pts:
            return {"visible": 0, "centroid": None, "side": None, "grouped": False}
        cu = sum(p[0] for p in pts) / len(pts)
        cv = sum(p[1] for p in pts) / len(pts)
        spread = max(geometry.dist(p, (cu, cv)) for p in pts)
        return {"visible": len(pts), "centroid": (round(cu, 3), round(cv, 3)), "side": map_side(cu, cv),
                "grouped": len(pts) >= 4 and spread <= GROUP_RADIUS}

    def _rule_pressure(self, ctx: _Ctx) -> list[tuple[str, str]]:
        info = None if ctx.safe else self._pressure_info(ctx)
        self._pressure = info
        if info is None or not info["grouped"] or ctx.gt < PRESSURE_MIN_GT or ctx.me_pos is None:
            return []
        side = info["side"]
        where = SIDE_FR.get(side, "")
        my_side = map_side(*ctx.me_pos)
        far = geometry.dist(ctx.me_pos, info["centroid"]) > 0.4
        if far and side in ("top", "bot") and _opposite(side) == my_side:
            advice = f"tu peux pousser {SIDE_FR[my_side]}"
        elif far:
            advice = "ne reste pas seul trop loin"
        else:
            advice = "reste avec ton équipe"
        return [("pressure", f"L'équipe ennemie est groupée {where} : {advice}.")]

    def _cs_checkpoint(self, ctx: _Ctx) -> float | None:
        for cp in CS_CHECKPOINTS:
            if cp <= ctx.gt <= cp + CS_CHECK_WINDOW_S and cp not in self._cs_done:
                return cp
        return None

    def _cs_target(self, ctx: _Ctx) -> float | None:
        return CS_TARGET.get(ctx.my_role or "")

    def _rule_cs(self, ctx: _Ctx) -> list[tuple[str, str]]:
        cp = self._cs_checkpoint(ctx)
        target = self._cs_target(ctx)
        if cp is None or target is None:
            return []
        cs = _finite((getattr(ctx.me_player, "scores", None) or {}).get("creepScore")) or 0.0
        cspm = cs / (ctx.gt / 60.0)
        mins = int(cp // 60)
        if cspm < target - 0.2:
            return [("cs_check", f"{mins} min : {fmt_dec(round(cspm, 1))} CS par minute, objectif {fmt_dec(target)}.")]
        return [("cs_check", f"{mins} min : {fmt_dec(round(cspm, 1))} CS par minute, bon farm, continue.")]

    def _rule_level6(self, ctx: _Ctx) -> list[tuple[str, str]]:
        lvl = _finite(getattr(ctx.me_player, "level", None)) or 0
        if self._level6_done or lvl < 6:
            return []
        if lvl > 7:                       # joined late / restarted: not news any more
            self._level6_done = True
            return []
        return [("level6", "Niveau 6 : cherche une action avec ton ultime.")]

    def _rule_vision(self, ctx: _Ctx) -> list[tuple[str, str]]:
        if ctx.gt < VISION_MIN_GT or self._ward_change_gt is None:
            return []
        if ctx.gt - self._ward_change_gt < VISION_STALE_S:
            return []
        return [("vision", "Pense à placer une balise.")]

    # ---------------------------------------------------------------- waves
    def _my_wave(self, ctx: _Ctx) -> Any:
        lane = ctx.role_lane or ctx.my_lane
        return (ctx.waves or {}).get(lane) if lane else None

    def _rule_waves(self, ctx: _Ctx) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        waves = ctx.waves or {}
        if not waves or ctx.dead or ctx.gt < 100:
            return out
        for lane, lw in waves.items():
            if (lw.enemy >= WAVE_BIG_ENEMY and lw.enemy >= 2 * max(1, lw.ally) and lw.enemy_front is not None
                    and lw.enemy_front <= 0.5):
                out.append(("wave_big", f"Grosse vague ennemie qui arrive {SIDE_FR[lane]}."))
                break
        lw = self._my_wave(ctx)
        in_my_lane = ctx.my_lane is not None and ctx.my_lane == (ctx.role_lane or ctx.my_lane)
        if lw is None or not in_my_lane or lw.meet is None:
            return out
        if lw.state == "pushing" and lw.meet >= WAVE_PUSH_S and lw.ally >= 3:
            out.append(("wave_push", "Ta vague pousse vers leur tour : bon moment pour rentrer après l'avoir poussée."))
        elif lw.state == "pushed_in" and lw.meet <= WAVE_BACK_S and lw.enemy >= 3 and lw.enemy >= lw.ally + 2:
            out.append(("wave_back", "La vague revient vers toi : attends-la sous ta tour."))
        return out

    # ---------------------------------------------------------------- lane opponents
    def _gone(self, ctx: _Ctx, tr: Any, lane: str | None) -> str | None:
        """``"recall"`` / ``"left"`` when this opponent left ``lane`` (else None)."""
        if tr is None or getattr(tr, "visible", False):
            return None
        if str(getattr(tr, "alias", "") or "").lower() in ctx.dead_enemies:
            return None                                     # dead: see _rule_dead
        hidden = ctx.t - float(getattr(tr, "last_seen", ctx.t))
        if not LANE_LEFT_S <= hidden <= LANE_LEFT_MAX_S:
            return None
        pos = _track_pos(tr)
        if pos is None:
            return None
        zone = geometry.classify_zone(*pos)
        if geometry.is_base(zone) and geometry.zone_owner(zone) == ctx.enemy_team:
            return "recall"
        if lane is not None and geometry.lane_of(zone) == lane:
            return "left"
        return None

    def _rule_lane(self, ctx: _Ctx) -> list[tuple[str, str]]:
        lane = ctx.role_lane
        if (not ctx.opponents or lane is None or ctx.dead or ctx.gt < LANE_RULES_MIN_GT
                or ctx.my_lane != lane):
            return []
        gone = []
        for alias, name, _p, tr in ctx.opponents:
            why = self._gone(ctx, tr, lane)
            if why is None or self._lane_left_done.get(alias) == float(getattr(tr, "last_seen", -1.0)):
                continue
            gone.append((name, why))
        if not gone:
            return []
        plates = ctx.gt < PLATES_END_GT
        if all(w == "recall" for _n, w in gone):
            who = " et ".join(n for n, _w in gone)
            verb = "sont rentrés" if len(gone) > 1 else "est rentré"
            tail = "pousse ta vague et récupère des plaques" if plates else "pousse ta vague"
            return [("lane_recall", f"{who} {verb} : {tail}.")]
        if len(gone) >= 2:
            who = "Tes deux adversaires ont"
        else:
            who = f"{gone[0][0]} a"
        tail = "pousse et prends des plaques, ping s'il roam" if plates else "pousse ta vague, ping s'il roam"
        if len(gone) >= 2:
            tail = tail.replace("s'il roam", "s'ils roam")
        return [("lane_left", f"{who} quitté la voie : {tail}.")]

    def _bot_pair(self, ctx: _Ctx) -> list[Any]:
        pair = []
        for p in getattr(ctx.game, "enemies", None) or []:
            if str(getattr(p, "position", "") or "").upper() in ("BOTTOM", "UTILITY"):
                low = str(p.champion_alias).lower()
                tr = next((x for x in ctx.enemies_all if str(getattr(x, "alias", "") or "").lower() == low), None)
                pair.append(tr)
        if not pair:
            try:
                aliases = self._roles.bot_lane("enemy") if self._roles is not None else ()
            except Exception:
                aliases = ()
            for a in aliases or ():
                tr = next((x for x in ctx.enemies_all if str(getattr(x, "alias", "") or "").lower() == str(a).lower()), None)
                pair.append(tr)
        return pair

    def _bot_marker(self, ctx: _Ctx) -> tuple | None:
        pair = self._bot_pair(ctx)
        if len(pair) != 2 or any(tr is None for tr in pair):
            return None
        return tuple(float(getattr(tr, "last_seen", 0.0)) for tr in pair)

    def _rule_bot_missing(self, ctx: _Ctx) -> list[tuple[str, str]]:
        if ctx.dead or ctx.gt < LANE_RULES_MIN_GT or ctx.role_lane == "bot" or ctx.me_pos is None:
            return []
        pair = self._bot_pair(ctx)
        if len(pair) != 2 or any(self._gone(ctx, tr, "bot") is None for tr in pair):
            return []
        if self._bot_missing_done == self._bot_marker(ctx):
            return []
        return [("bot_missing", "Les deux bot ennemis ont disparu : prudence, ils peuvent roam.")]

    # ---------------------------------------------------------------- scoreboard
    def _level_diff(self, ctx: _Ctx) -> tuple[int, str | None]:
        """(my level - lane opponent level, opponent name) for my single lane opponent / the ADC."""
        my_lvl = _finite(getattr(ctx.me_player, "level", None))
        if my_lvl is None or not ctx.opponents:
            return 0, None
        opp = ctx.opponents[0]
        if len(ctx.opponents) > 1:      # bot lane: compare with the enemy of my own role
            mine = ctx.my_role
            opp = next((o for o in ctx.opponents if o[2] is not None
                        and str(getattr(o[2], "position", "")).upper() == mine), ctx.opponents[0])
        lvl = _finite(getattr(opp[2], "level", None)) if opp[2] is not None else None
        if lvl is None:
            return 0, None
        return int(my_lvl - lvl), opp[1]

    def _kill_mark(self, ctx: _Ctx) -> float | None:
        for m in KILL_MARKS:
            if m <= ctx.gt <= m + 60.0 and m not in self._kill_marks_done:
                return m
        return None

    def _team_kills(self, ctx: _Ctx) -> tuple[int, int]:
        def kills(players: Iterable[Any]) -> int:
            return sum(int(_finite((getattr(p, "scores", None) or {}).get("kills")) or 0) for p in players)
        g = ctx.game
        ours = kills(([g.me] if getattr(g, "me", None) is not None else []) + list(getattr(g, "allies", None) or []))
        return ours, kills(getattr(g, "enemies", None) or [])

    def _rule_scoreboard(self, ctx: _Ctx) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if not self._scoreboard_on:      # cfg.coach_scoreboard_tips = False (another analyser speaks them)
            return out
        for _t, _alias, name, item in (self._item_news[:1] if self._item_tips else []):
            out.append(("item_spike", f"{name} vient de finir {ITEM_NAMES_FR[item]} : attention à son pic de puissance."))
        diff, name = self._level_diff(ctx)
        if name and abs(diff) >= LEVEL_DIFF_MIN and diff != self._level_diff_said and ctx.gt >= 180:
            n = abs(diff)
            if diff > 0:
                out.append(("level_diff", f"Tu as {n} niveaux d'avance sur {name} : joue agressif."))
            else:
                out.append(("level_diff", f"{name} a {n} niveaux d'avance sur toi : joue prudent."))
        elif abs(diff) < LEVEL_DIFF_MIN:
            self._level_diff_said = 0
        if not self._jg6_done:
            g = ctx.game
            ej = None
            try:
                ej = g.enemy_jungler()
            except Exception:
                ej = None
            mine = [p for p in ([g.me] + list(g.allies or [])) if p is not None
                    and (getattr(p, "has_smite", False) or str(getattr(p, "position", "")).upper() == "JUNGLE")]
            aj = mine[0] if mine else None
            if ej is not None and aj is not None and (ej.level or 0) >= 6 and (aj.level or 0) < 6:
                who = "toi" if aj is g.me else "le vôtre"
                out.append(("jg_level6", f"Leur jungler est niveau 6 avant {who} : prudence."))
            elif aj is not None and (aj.level or 0) >= 6:
                self._jg6_done = True
        if self._kill_mark(ctx) is not None:
            ours, theirs = self._team_kills(ctx)
            if ours - theirs >= KILL_LEAD_MIN:
                out.append(("kill_lead", f"Vous menez {ours} à {theirs} aux kills : jouez les objectifs."))
            elif theirs - ours >= KILL_LEAD_MIN:
                out.append(("kill_lead", f"Vous êtes derrière, {ours} à {theirs} : jouez groupés et farmez."))
        return out

    # ---------------------------------------------------------------- objective trading
    def _rule_trade(self, ctx: _Ctx) -> list[tuple[str, str]]:
        if ctx.dead or not ctx.enemies_vis:
            return []
        states = {str(getattr(s, "key", "") or ""): s for s in ctx.objectives}

        def up(key: str) -> bool:
            s = states.get(key)
            if s is None:
                return False
            if getattr(s, "alive", False):
                return True
            rem = self._remaining(s, ctx.gt)
            return rem is not None and rem <= 30
        for pit_key, pit, label, other_side in (("dragon", _DRAGON_PIT, "au dragon", "top"),
                                                 ("baron", _BARON_PIT, "au Baron", "bot")):
            n = sum(1 for _tr, p in ctx.enemies_vis if geometry.dist(p, pit) < TRADE_PIT_R)
            if n < TRADE_MIN:
                continue
            if pit_key == "dragon" and not (up("dragon") or up("elder")):
                continue
            if pit_key == "baron" and not (up("baron") or up("herald") or up("grubs")):
                continue
            if other_side == "top":
                alt = ("les larves" if up("grubs") else "le Héraut" if up("herald")
                       else "le Baron" if up("baron") else None)
            else:
                alt = "le dragon" if (up("dragon") or up("elder")) else None
            where = SIDE_FR[other_side]
            what = f"{alt} ou des tours {where}" if alt else f"des tours {where}"
            return [("objective_trade", f"{n} ennemis {label} : prenez {what}.")]
        return []

    # ---------------------------------------------------------------- dead enemies (Tab)
    def _dead_targets(self, ctx: _Ctx, rule: str) -> list[str]:
        """Aliases (lower case) of the dead enemies ``rule`` talks about, not announced yet."""
        if rule == "lane_dead":
            cands = [str(a).lower() for a, _n, _p, _tr in ctx.opponents or []]
        else:
            cands = [str(ctx.jungler_alias).lower()] if ctx.jungler_alias else []
        out = []
        for a in cands:
            if a not in ctx.dead_enemies:
                self._dead_done.pop(a, None)                # alive again: next death is news
                continue
            if a not in self._dead_done:
                out.append(a)
        return out

    def _respawn(self, ctx: _Ctx, alias: str) -> float:
        for p in getattr(ctx.game, "enemies", None) or []:
            if str(getattr(p, "champion_alias", "") or "").lower() == alias:
                return _finite(getattr(p, "respawn_timer", None)) or 0.0
        return 0.0

    def _rule_dead(self, ctx: _Ctx) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if ctx.dead:
            return out
        plates = ctx.gt < PLATES_END_GT
        dead_opps = self._dead_targets(ctx, "lane_dead")
        if dead_opps and ctx.role_lane is not None and ctx.my_role != "JUNGLE":
            names = [n for a, n, _p, _tr in ctx.opponents or [] if str(a).lower() in dead_opps]
            if not any(0.0 < self._respawn(ctx, a) < 6.0 for a in dead_opps):   # not respawning now
                who = " et ".join(names)
                verb = "sont morts" if len(names) > 1 else "est mort"
                tail = "pousse ta vague et prends des plaques" if plates else "pousse ta vague et prends la tour"
                out.append(("lane_dead", f"{who} {verb} : {tail}."))
        if self._dead_targets(ctx, "jungler_dead"):
            states = {str(getattr(s, "key", "") or ""): s for s in ctx.objectives}
            target = None
            for key in ("baron", "elder", "dragon", "herald", "grubs", "atakhan"):
                s = states.get(key)
                if s is None:
                    continue
                rem = None if getattr(s, "alive", False) else self._remaining(s, ctx.gt)
                if getattr(s, "alive", False) or (rem is not None and rem <= 20):
                    target = _WINDOW_NAMES.get(key) or str(getattr(s, "name", "") or "")
                    break
            if target:
                out.append(("jungler_dead", f"Leur jungler est mort : bonne fenêtre pour {target}."))
            elif ctx.my_role == "JUNGLE":
                out.append(("jungler_dead", "Leur jungler est mort : envahis sa jungle et prends ses camps."))
            else:
                out.append(("jungler_dead", "Leur jungler est mort : tu peux jouer agressif dans ta voie."))
        return out

    # ---------------------------------------------------------------- v3 macro fundamentals
    def _setup_target_window(self, ctx: _Ctx, lo: float, hi: float) -> tuple[Any, float] | None:
        best = None
        for s in ctx.objectives:
            if getattr(s, "alive", False):
                continue
            rem = self._remaining(s, ctx.gt)
            if rem is not None and lo <= rem <= hi and (best is None or rem < best[1]):
                best = (s, rem)
        return best

    def _rule_macro_v3(self, ctx: _Ctx) -> list[tuple[str, str]]:
        """Wave management, recall timing, power spikes, picks -> objectives."""
        out: list[tuple[str, str]] = []
        if ctx.dead:
            return out
        me = ctx.me_player
        lvl = int(_finite(getattr(me, "level", None)) or 0)
        laner = ctx.my_role in ("TOP", "MIDDLE", "BOTTOM", "UTILITY")
        in_lane = ctx.my_lane is not None and ctx.my_lane == ctx.role_lane
        lw = self._my_wave(ctx)
        # -- level 2 first in lane: the first all-in window of the game
        if not self._level2_done and laner and ctx.gt < 200 and lvl >= 2:
            opp_lv = [int(_finite(getattr(p, "level", None)) or 0) for _a, _n, p, _t in ctx.opponents or [] if p is not None]
            if lvl >= 3 or ctx.gt >= 200:
                self._level2_done = True
            elif opp_lv and lvl == 2 and max(opp_lv) <= 1:
                out.append(("level2", "Niveau 2 avant ton adversaire : c'est le moment d'échanger fort."))
        # -- first legendary item: power spike
        try:
            from treeaicoach.scoreboard import major_items

            n_leg = len(major_items(getattr(me, "items", None) or []))
        except Exception:
            n_leg = 0
        if self._legendaries is not None and n_leg >= 1 and self._legendaries == 0 and not self._first_item_done:
            out.append(("first_item", "Premier objet complet : c'est ton pic de puissance, cherche un échange "
                                      "ou une escarmouche maintenant."))
        self._legendaries = n_leg
        # -- recall timing: enough gold to complete the next item
        gold = _finite(getattr(ctx.game, "current_gold", None)) or 0.0
        if gold >= 900 and not self._in_my_base(ctx) and ctx.me_pos is not None and ctx.gt >= 180:
            try:
                from treeaicoach.itemization import recommend

                rec = recommend(ctx.game, ctx.my_role)
            except Exception:
                rec = None
            if rec is not None and rec.completes:
                if lw is not None and lw.state == "pushing" and in_lane:
                    out.append(("recall_item", f"{int(gold)} PO : assez pour {rec.item_name}. Pousse ta vague "
                                               "sous leur tour puis rentre."))
                else:
                    out.append(("recall_item", f"{int(gold)} PO : assez pour {rec.item_name}, rentre dès que "
                                               "ta vague est poussée."))
        # -- freeze when ahead (laning)
        diff, opp = self._level_diff(ctx)
        if (laner and in_lane and ctx.gt < PLATES_END_GT and lw is not None and lw.state == "pushed_in"
                and diff >= 1 and opp):
            out.append(("freeze", f"Tu es en avance sur {opp} : gèle la vague devant ta tour, il devra "
                                  "s'exposer pour farmer."))
        # -- crash the wave, then roam (mid / support)
        jg = ctx.jungler
        jside = map_side(*_track_pos(jg)) if jg is not None and getattr(jg, "visible", False) and _track_pos(jg) else None
        if (ctx.my_role in ("MIDDLE", "UTILITY") and in_lane and lw is not None and lw.state == "pushing"
                and lw.meet is not None and lw.meet >= WAVE_PUSH_S and jside in ("top", "bot") and ctx.gt >= 240):
            out.append(("crash_roam", f"Ta vague s'écrase et leur jungler est {SIDE_FR[jside]} : bon moment "
                                      f"pour roam de l'autre côté ou prendre la vision."))
        # -- slow push / crash before an objective
        tgt = self._setup_target_window(ctx, 50.0, 80.0)
        if tgt is not None and laner and in_lane:
            s_obj, rem = tgt
            key = str(getattr(s_obj, "key", "") or "")
            ident = (key, int(round(ctx.gt + rem) // 30))
            near_side = PITS.get(key)
            if ident not in self._obj_wave_done and near_side is not None and (
                    ctx.role_lane == near_side[1] or ctx.my_role == "MIDDLE"):
                name = str(getattr(s_obj, "name", "") or "Objectif")
                out.append(("objective_wave", f"{name} dans {int(round(rem / 5) * 5)} s : pousse ta vague maintenant "
                                              "pour arriver le premier à la rivière."))
        # -- picks -> Baron (2-3 enemies dead for long; an ace is handled by phase.EndGameCaller)
        states = {str(getattr(o, "key", "") or ""): o for o in ctx.objectives}
        baron = states.get("baron")
        if baron is not None and getattr(baron, "alive", False) and ctx.gt >= 1200:
            long_dead = [p for p in getattr(ctx.game, "enemies", None) or []
                         if bool(getattr(p, "is_dead", False)) and (_finite(getattr(p, "respawn_timer", 0)) or 0) >= 20]
            if 2 <= len(long_dead) <= 3:
                out.append(("baron_pick", f"{len(long_dead)} ennemis morts pour 20 s et plus : Baron possible "
                                          "si vous êtes au moins 4 autour."))
        return out

    # ---------------------------------------------------------------- facts (stance / tips)
    def _make_facts(self, ctx: _Ctx) -> dict[str, Any]:
        names = {str(getattr(p, "champion_alias", "") or "").lower(): p for p in getattr(ctx.game, "enemies", None) or []}
        jg: dict[str, Any] = {"known": ctx.jungler_alias is not None, "alias": ctx.jungler_alias, "name": None,
                              "visible": False, "side": None, "dist": None, "hidden_s": ctx.jungler_hidden_s,
                              "dead": False, "last_side": None}
        if ctx.jungler_alias:
            p = names.get(str(ctx.jungler_alias).lower())
            jg["name"] = str(getattr(p, "champion_name", "") or ctx.jungler_alias) if p is not None else ctx.jungler_alias
            jg["dead"] = str(ctx.jungler_alias).lower() in ctx.dead_enemies
            tr = ctx.jungler
            pos = _track_pos(tr) if tr is not None else None
            if pos is not None and not ctx.safe:
                jg["last_side"] = map_side(*pos)
                if getattr(tr, "visible", False):
                    jg["visible"] = True
                    jg["side"] = jg["last_side"]
                    if ctx.me_pos is not None:
                        jg["dist"] = round(geometry.dist(pos, ctx.me_pos), 3)
        if ctx.safe:
            jg.update(visible=False, side=None, dist=None, hidden_s=None, last_side=None)
        lw = self._my_wave(ctx)
        opps = []
        for a, name, p, _tr in ctx.opponents or []:
            opps.append({"alias": a, "name": name, "dead": str(a).lower() in ctx.dead_enemies,
                         "level": int(_finite(getattr(p, "level", None)) or 0) if p is not None else None,
                         "respawn": _finite(getattr(p, "respawn_timer", None)) if p is not None else None})
        objs = []
        for s in ctx.objectives:
            objs.append({"key": str(getattr(s, "key", "") or ""), "name": str(getattr(s, "name", "") or ""),
                         "alive": bool(getattr(s, "alive", False)), "remaining": self._remaining(s, ctx.gt)})
        en, al = self._numbers(ctx) if not ctx.safe else (0, 1)
        return {
            "t": ctx.t, "gt": ctx.gt, "dead": ctx.dead, "safe": ctx.safe, "team": ctx.team,
            "my_role": ctx.my_role, "role_lane": ctx.role_lane, "my_lane": ctx.my_lane, "me_pos": ctx.me_pos,
            "in_base": self._in_my_base(ctx), "jungler": jg,
            "missing": 0 if ctx.safe else self._missing(ctx), "numbers": (en, al),
            "visible_enemies": 0 if ctx.safe else len(ctx.enemies_vis),
            "wave": (lw.state if lw is not None and not ctx.safe else None),
            "opponents": opps, "objectives": objs,
        }

    # ---------------------------------------------------------------- insights
    def _build_insights(self, ctx: _Ctx) -> list[str]:
        items: list[tuple] = []
        add = items.append
        if not ctx.safe and not ctx.dead and ctx.me_pos is not None and not self._in_my_base(ctx):
            en, al = self._numbers(ctx)
            if en >= 2 and en - al >= 2:
                add((100, f"{en} contre {al} autour de toi"))
            elif en >= 1 and al - en >= 2:
                add((60, f"{al} contre {en} autour de toi : engage"))
        # objective coming / up
        for s in ctx.objectives:
            kind = str(getattr(s, "key", "") or "")
            name = str(getattr(s, "name", "") or "")
            if not name:
                continue
            pit = PITS.get(kind)
            if not getattr(s, "alive", False):
                rem = self._remaining(s, ctx.gt)
                if rem is None or not 0 < rem <= 90:
                    continue
                clock = f"{int(rem) // 60}:{int(rem) % 60:02d}"
                if pit is not None and not ctx.safe:
                    (pu, pv), side = pit
                    n = self._count_side(ctx, (pu, pv), side, ctx.enemies_vis)
                    extra = f" · {n} ennemi{'s' if n > 1 else ''} {SIDE_FR[side]}" if n else " · prépare la vision"
                else:
                    extra = " · prépare la vision"
                add((80, f"{name} {clock}{extra}", "objective"))
            elif pit is not None and not ctx.safe and kind in _WINDOW_NAMES:
                far = [p for _tr, p in ctx.enemies_vis if geometry.dist(p, pit[0]) > WINDOW_FAR]
                if len(far) >= 4:
                    add((85, f"{name} dispo · {len(far)} ennemis loin", "objective"))
        if not ctx.safe:
            n = self._missing(ctx)
            if n >= MISSING_MIN:
                add((70, f"{min(n, 5)} ennemis disparus"))
            info = self._pressure or {}
            if info.get("grouped"):
                add((55, f"Ennemis groupés {SIDE_FR.get(info.get('side'), '')} ({info.get('visible')})"))
            js = self._jg_side_info
            if js is not None and 0.0 <= ctx.t - js[0] <= INSIGHT_JUNGLER_S:
                add((50, f"JGL {SIDE_FR[js[1]]} → joue agressif {SIDE_FR[js[2]]}"))
            if (self._in_enemy_jungle(ctx) and ctx.jungler_hidden_s is not None
                    and ctx.jungler_hidden_s >= DEEP_UNSEEN_S):
                add((90, "Jungle ennemie, leur JGL invisible"))
        if not ctx.safe:
            lw = self._my_wave(ctx)
            if lw is not None and lw.state == "pushing":
                add((45, f"Ta vague pousse ({SIDE_FR.get(lw.lane, '')})"))
            elif lw is not None and lw.state == "pushed_in":
                add((45, f"La vague revient vers toi ({SIDE_FR.get(lw.lane, '')})"))
            for _a, name, _p, tr in ctx.opponents or []:
                why = self._gone(ctx, tr, ctx.role_lane)
                if why == "recall":
                    add((68, f"{name} est rentré : pousse"))
                elif why == "left" and ctx.my_lane == ctx.role_lane:
                    add((66, f"{name} a quitté la voie"))
        for a, name, _p, _tr in ctx.opponents or []:
            if str(a).lower() in ctx.dead_enemies:
                add((69, f"{name} mort : pousse ta vague"))
        if ctx.jungler_alias and str(ctx.jungler_alias).lower() in ctx.dead_enemies:
            add((67, "Jungler ennemi mort : fenêtre"))
        for _t, _alias, name, item in self._item_news[:1]:
            add((64, f"{name} : {ITEM_NAMES_FR[item]} fini"))
        diff, name = self._level_diff(ctx)
        if name and abs(diff) >= LEVEL_DIFF_MIN:
            add((40, f"{'+' if diff > 0 else '−'}{abs(diff)} niveaux vs {name}"))
        target = self._cs_target(ctx)
        if target is not None and ctx.gt >= 300:
            cs = _finite((getattr(ctx.me_player, "scores", None) or {}).get("creepScore")) or 0.0
            cspm = cs / (ctx.gt / 60.0)
            if cspm < target - 0.5:
                add((20, f"CS/min {fmt_dec(round(cspm, 1))} · objectif {fmt_dec(target)}"))
        if (self._ward_change_gt is not None and ctx.gt >= VISION_MIN_GT
                and ctx.gt - self._ward_change_gt >= VISION_STALE_S):
            add((15, "Pas de balise depuis 3 min"))
        items.sort(key=lambda x: -x[0])
        self._insight_items = [(int(it[0]), str(it[1]), it[2] if len(it) > 2 else "live") for it in items]
        return [it[1] for it in items[:4]]


# ======================================================================================
# Stance: PRUDENT / ÉQUILIBRÉ / AGRESSIF
# ======================================================================================
STANCE_LABEL: dict[str, str] = {"prudent": "PRUDENT", "equilibre": "ÉQUILIBRÉ", "agressif": "AGRESSIF"}
STANCE_AGGRO = 2.0              # score >= this -> agressif
STANCE_SAFE = -2.0              # score <= this -> prudent
STANCE_CONFIRM_S = 4.0          # a new stance must hold this long before it is shown
STANCE_CONFIRM_SAFE_S = 1.5     # ... (going prudent is confirmed faster)
STANCE_VOICE_GAP_S = 120.0      # spoken at most every 2 min, only when it changed
STANCE_MIN_GT = 90.0            # nothing before 1:30 (everyone is still walking to lane)
STANCE_RECENT_DEATHS_S = 180.0
STANCE_RECENT_KILLS_S = 120.0
OPPOSITE_SIDE = {"top": "bot", "bot": "top"}


@dataclass(frozen=True)
class Stance:
    """A live stance recommendation."""

    level: str                  # "prudent" | "equilibre" | "agressif"
    reason: str                 # short French reason ("+1 niveau sur Darius et jungler ennemi vu en bas")
    score: float
    factors: tuple[tuple[float, str], ...] = ()
    t: float = 0.0

    @property
    def label(self) -> str:
        return STANCE_LABEL.get(self.level, self.level.upper())

    @property
    def sentence(self) -> str:
        """Voice sentence: "Joue agressif : +1 niveau sur Darius."."""
        head = {"prudent": "Joue prudent", "agressif": "Joue agressif"}.get(self.level, "Situation équilibrée")
        return f"{head} : {self.reason}." if self.reason else f"{head}."


def _pct(x: float) -> int:
    return int(round(100 * x / 5.0) * 5)


def _gold_short(n: float) -> str:
    a = abs(int(n))
    body = f"{a / 1000:.1f}".replace(".", ",") + " k" if a >= 1000 else str(a)
    return ("+" if n >= 0 else "−") + body + " PO"


def _my_events(game: Any, gt: float) -> tuple[int, int]:
    """(my deaths in the last STANCE_RECENT_DEATHS_S, my kills in the last STANCE_RECENT_KILLS_S)."""
    me = getattr(game, "me", None)
    names = set()
    for attr in ("riot_id", "summoner_name"):
        v = str(getattr(me, attr, "") or "").strip().casefold()
        if v:
            names.add(v)
            names.add(v.split("#", 1)[0])
    deaths = kills = 0
    for e in getattr(game, "events", None) or []:
        if not isinstance(e, dict) or e.get("EventName") != "ChampionKill":
            continue
        T = _finite(e.get("EventTime"))
        if T is None or T > gt + 1.0:
            continue
        victim = str(e.get("VictimName") or "").strip().casefold()
        killer = str(e.get("KillerName") or "").strip().casefold()
        if (victim in names or victim.split("#", 1)[0] in names) and gt - T <= STANCE_RECENT_DEATHS_S:
            deaths += 1
        if (killer in names or killer.split("#", 1)[0] in names) and gt - T <= STANCE_RECENT_KILLS_S:
            kills += 1
    return deaths, kills


def stance_factors(facts: dict[str, Any], game: Any, scoreboard: Any = None, threat: int = 0) -> list[tuple[float, str]]:
    """Weighted reasons (+ = play aggressive, - = play safe), strongest first. Pure, never raises."""
    out: list[tuple[float, str]] = []
    try:
        f = facts or {}
        gt = _finite(f.get("gt")) or _finite(getattr(game, "game_time", None)) or 0.0
        if int(_finite(threat) or 0) >= Level.WARNING:
            out.append((-5.0, "gank en cours"))
        # -- my health (activePlayer.championStats)
        stats = getattr(game, "champion_stats", None) or {}
        cur, mx = _finite(stats.get("currentHealth")), _finite(stats.get("maxHealth"))
        if cur is not None and mx and mx > 0 and not f.get("in_base"):
            frac = max(0.0, min(1.0, cur / mx))
            if frac < 0.35:
                out.append((-3.0, f"tu es à {_pct(frac)} % PV"))
            elif frac < 0.55:
                out.append((-1.5, f"tu es à {_pct(frac)} % PV"))
        # -- lane matchup (Tab: levels / item gold), else Live Client levels
        m = getattr(scoreboard, "my_matchup", None) if scoreboard is not None else None
        opp_name = None
        lvl_diff = gold_diff = None
        if m is not None:
            opp_name, lvl_diff, gold_diff = m.enemy, int(m.level_diff), int(m.gold_diff)
        else:
            opps = [o for o in f.get("opponents") or [] if o.get("level")]
            my_lvl = _finite(getattr(getattr(game, "me", None), "level", None))
            if opps and my_lvl is not None:
                o = opps[0]
                opp_name, lvl_diff = o.get("name"), int(my_lvl - o["level"])
        if opp_name and lvl_diff:
            n = abs(lvl_diff)
            word = "niveau" if n == 1 else "niveaux"
            if lvl_diff > 0:
                out.append((1.5 * min(n, 2), f"+{n} {word} sur {opp_name}"))
            else:
                out.append((-1.5 * min(n, 2), f"{opp_name} a {n} {word} d'avance"))
        if opp_name and gold_diff is not None and abs(gold_diff) >= 700:
            w = 1.5 if abs(gold_diff) >= 1500 else 1.0
            if gold_diff > 0:
                out.append((w, f"{_gold_short(gold_diff)} sur {opp_name}"))
            else:
                out.append((-w, f"{opp_name} a {_gold_short(-gold_diff)[1:]} d'avance"))
        # -- my lane opponent dead
        if f.get("my_role") != "JUNGLE":
            dead = [o for o in f.get("opponents") or [] if o.get("dead")
                    and (o.get("respawn") is None or o.get("respawn") == 0 or o.get("respawn") >= 5)]
            if dead:
                who = " et ".join(str(o.get("name")) for o in dead)
                out.append((3.0, f"{who} {'sont morts' if len(dead) > 1 else 'est mort'}"))
        # -- enemy jungler (positions: none in safe mode, the facts are already stripped)
        jg = f.get("jungler") or {}
        my_side = f.get("role_lane") or f.get("my_lane")
        if f.get("my_role") == "JUNGLE" and f.get("me_pos") is not None:
            my_side = geometry.side_of(*f["me_pos"])
        if jg.get("dead"):
            out.append((2.0, "jungler ennemi mort"))
        elif jg.get("known") and gt >= JUNGLE_RULES_MIN_GT:
            side, dist, hidden = jg.get("side"), _finite(jg.get("dist")), _finite(jg.get("hidden_s"))
            if jg.get("visible") and dist is not None and dist < 0.25:
                out.append((-3.0, "jungler ennemi proche"))
            elif jg.get("visible") and side in ("top", "bot") and OPPOSITE_SIDE.get(side) == my_side:
                out.append((2.0, f"jungler ennemi vu {SIDE_FR[side]}"))
            elif jg.get("visible") and side in ("top", "bot") and side == my_side:
                out.append((-1.5, f"jungler ennemi {SIDE_FR[side]}, de ton côté"))
            elif not jg.get("visible") and hidden is not None:
                last = jg.get("last_side")
                if hidden < 25 and last in ("top", "bot") and last == my_side:
                    out.append((-2.0, f"jungler ennemi vu {SIDE_FR[last]} il y a {int(hidden)} s"))
                elif hidden < 25 and last in ("top", "bot") and OPPOSITE_SIDE.get(last) == my_side:
                    out.append((1.5, f"jungler ennemi vu {SIDE_FR[last]} il y a {int(hidden)} s"))
                elif hidden >= JUNGLER_UNSEEN_S:
                    out.append((-1.0, f"jungler ennemi invisible depuis {int(hidden // 5 * 5)} s"))
        # -- missing / numbers / wave
        miss = int(_finite(f.get("missing")) or 0)
        if miss >= 3:
            out.append((-2.0, f"{min(miss, 5)} ennemis disparus"))
        elif miss == 2:
            out.append((-0.5, "2 ennemis disparus"))
        en, al = (f.get("numbers") or (0, 1))[:2]
        if en >= 2 and en - al >= 2:
            out.append((-3.0, f"{en} contre {al} autour de toi"))
        elif en >= 1 and al - en >= 2:
            out.append((1.5, f"{al} contre {en} autour de toi"))
        wave = f.get("wave")
        if wave == "pushing" and not jg.get("visible") and not jg.get("dead"):
            out.append((-0.75, "ta vague pousse, gare aux ganks"))
        elif wave == "pushed_in":
            out.append((0.5, "la vague est chez toi"))
        # -- team gold (Tab) + objectives
        team = int(scoreboard.team_gold_diff) if scoreboard is not None and getattr(scoreboard, "players", None) else 0
        soon = None
        for o in f.get("objectives") or []:
            rem = _finite(o.get("remaining"))
            if o.get("alive") or (rem is not None and 0 <= rem <= 60):
                soon = o
                break
        if soon is not None and abs(team) >= 1500:
            name = soon.get("name") or "objectif"
            when = "dispo" if soon.get("alive") else f"dans {int(_finite(soon.get('remaining')) or 0)} s"
            if team > 0:
                out.append((1.0, f"équipe en avance, {name} {when}"))
            else:
                out.append((-1.0, f"équipe en retard, {name} {when}"))
        elif abs(team) >= 3000:
            out.append((1.0 if team > 0 else -1.0, f"équipe {_gold_short(team)}"))
        # -- my recent kills / deaths (event feed)
        deaths, kills = _my_events(game, gt)
        if deaths >= 2:
            out.append((-1.5, f"{deaths} morts en 3 min"))
        elif kills >= 2:
            out.append((0.5, "tu es en forme"))
    except Exception:
        log.debug("stance_factors failed", exc_info=True)
    out.sort(key=lambda x: -abs(x[0]))
    return out


def stance_from_factors(factors: list[tuple[float, str]], t: float = 0.0) -> Stance:
    score = round(sum(w for w, _ in factors), 2)
    level = "agressif" if score >= STANCE_AGGRO else "prudent" if score <= STANCE_SAFE else "equilibre"
    pos = [txt for w, txt in factors if w > 0]
    neg = [txt for w, txt in factors if w < 0]
    if level == "agressif":
        reason = " et ".join(pos[:2])
    elif level == "prudent":
        reason = " et ".join(neg[:2])
    elif pos and neg:
        reason = f"{pos[0]} mais {neg[0]}"
    elif pos or neg:
        reason = (pos or neg)[0]
    else:
        reason = "pas d'avantage net, farme et garde ta vision"
    return Stance(level, reason, score, tuple(factors), t)


class StanceAdvisor:
    """Live PRUDENT / ÉQUILIBRÉ / AGRESSIF recommendation with a short reason (HUD pill) and a
    spoken sentence when it CHANGES (at most every :data:`STANCE_VOICE_GAP_S`, never during a
    gank threat, never while dead). Inputs: :meth:`MapCoach.facts` (jungler, missing enemies,
    numbers, wave, lane opponents, objectives), the Live Client data (my health, level, event
    feed) and the Tab summary (:class:`scoreboard.ScoreboardSummary`). Safe mode: the coach
    facts carry no enemy position, so only public / personal data is used. Thread-safe."""

    def __init__(self, cfg: Any = None) -> None:
        self._lock = threading.RLock()
        self._voice = True
        self.apply_config(cfg)
        self.reset()

    def apply_config(self, cfg: Any) -> None:
        v = getattr(cfg, "stance_voice", True)
        with self._lock:
            self._voice = v if isinstance(v, bool) else True

    def reset(self) -> None:
        with self._lock:
            self._current: Stance | None = None
            self._cand: tuple[str, float] | None = None     # (level, since)
            self._spoken_level: str | None = None
            self._spoken_t: float | None = None
            self._last_t: float | None = None

    def current(self) -> Stance | None:
        with self._lock:
            return self._current

    def update(self, t: float, facts: dict[str, Any], game: Any, scoreboard: Any = None,
               threat: int = 0) -> list[Alert]:
        """One tick; returns ``[]`` or ``[the stance sentence]`` (MACRO_TIP, key ``stance:<level>``)."""
        try:
            with self._lock:
                return self._update_locked(float(t), facts or {}, game, scoreboard, int(_finite(threat) or 0))
        except Exception:
            log.exception("StanceAdvisor.update failed")
            return []

    def _update_locked(self, t: float, facts: dict[str, Any], game: Any, scoreboard: Any, threat: int) -> list[Alert]:
        if self._last_t is not None and t < self._last_t - 1.0:
            self.reset()
        self._last_t = t
        me = getattr(game, "me", None) if game is not None else None
        gt = _finite(facts.get("gt")) or _finite(getattr(game, "game_time", None)) or 0.0
        if me is None or not facts or gt < STANCE_MIN_GT:
            self._current, self._cand = None, None
            return []
        if bool(getattr(me, "is_dead", False)) or facts.get("dead"):
            self._current, self._cand = None, None
            return []
        new = stance_from_factors(stance_factors(facts, game, scoreboard, threat), t)
        cur = self._current
        if cur is None:
            self._current = new                         # first value: shown at once
            self._cand = None
        elif new.level == cur.level:
            self._current = new                         # same stance, fresher reason
            self._cand = None
        else:
            if self._cand is None or self._cand[0] != new.level:
                self._cand = (new.level, t)
            need = STANCE_CONFIRM_SAFE_S if new.level == "prudent" else STANCE_CONFIRM_S
            if t - self._cand[1] >= need:
                self._current = new
                self._cand = None
        st = self._current
        if st is None or not self._voice or threat >= Level.WARNING:
            return []
        if self._spoken_level is None and st.level == "equilibre":
            self._spoken_level = st.level               # nothing to say about a neutral start
            return []
        if st.level == self._spoken_level:
            return []
        if self._spoken_t is not None and 0.0 <= t - self._spoken_t < STANCE_VOICE_GAP_S:
            return []
        if any(w <= -5.0 for w, _ in st.factors):
            return []                                   # "gank en cours": the gank alert speaks
        self._spoken_level, self._spoken_t = st.level, t
        return [Alert(kind=_MACRO, level=Level.INFO, text=st.sentence, key=f"stance:{st.level}", t=t)]


__all__ = ["MapCoach", "map_side", "fmt_dec", "PITS", "TURRETS", "GLOBAL_GAP_S", "ENEMY_RULES",
           "Stance", "StanceAdvisor", "stance_factors", "stance_from_factors", "STANCE_LABEL"]
