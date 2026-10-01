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

Policy: at most one tip every :data:`GLOBAL_GAP_S` (40 s; the "numbers" disadvantage warning
only needs :data:`SAFETY_GAP_S` since the previous tip), nothing while a gank threat is
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
GLOBAL_GAP_S = 40.0            # min time between two spoken tips
SAFETY_GAP_S = 10.0            # the "outnumbered" warning only needs this since the last tip
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

RULE_COOLDOWN_S: dict[str, float] = {
    "jungler_side": 60.0, "jungler_unseen": 60.0, "missing": 60.0, "objective_setup": 30.0,
    "objective_window": 120.0, "numbers_bad": 30.0, "numbers_good": 60.0, "pressure": 90.0,
    "cs_check": 60.0, "vision": 180.0, "level6": 1e9, "deep": 60.0,
}
#: Rules that use enemy positions (disabled in safe mode).
ENEMY_RULES: frozenset[str] = frozenset({
    "jungler_side", "jungler_unseen", "missing", "objective_window", "numbers_bad", "numbers_good",
    "pressure", "deep"})
#: Rules that need me alive.
ALIVE_RULES: frozenset[str] = frozenset({
    "jungler_side", "jungler_unseen", "missing", "numbers_bad", "numbers_good", "pressure", "deep",
    "vision", "objective_window"})
PRIORITY: dict[str, int] = {
    "numbers_bad": 100, "deep": 90, "objective_window": 85, "objective_setup": 80, "missing": 75,
    "jungler_unseen": 70, "jungler_side": 65, "numbers_good": 60, "pressure": 55, "cs_check": 40,
    "level6": 35, "vision": 30,
}

CS_TARGET: dict[str, float] = {"TOP": 7.0, "MIDDLE": 7.0, "BOTTOM": 7.5, "JUNGLE": 5.5}

_DRAGON_PIT = geometry.game_to_uv(9866, 4414)
_BARON_PIT = geometry.game_to_uv(5007, 10471)
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


class MapCoach:
    """Live macro tips + HUD insights. See the module docstring. Thread-safe."""

    def __init__(self, cfg: Any = None) -> None:
        self._lock = threading.RLock()
        self._enabled = True
        self._safe = False
        self.apply_config(cfg)
        self._clear()

    # -- public -------------------------------------------------------------------------
    def apply_config(self, cfg: Any) -> None:
        """``cfg.safe_mode`` and the optional ``cfg.macro_coach`` switch (default on)."""
        try:
            on = getattr(cfg, "macro_coach", True)
            safe = getattr(cfg, "safe_mode", False)
            with self._lock:
                self._enabled = on if isinstance(on, bool) else True
                self._safe = bool(safe) if isinstance(safe, bool) else False
        except Exception:
            log.exception("MapCoach.apply_config failed")

    def reset(self) -> None:
        """Forget everything (new game)."""
        with self._lock:
            self._clear()

    def update(self, t: float, tracker: Any, game: Any, roles: Any = None,
               objectives_states: Iterable[Any] | None = None,
               my_pos: tuple[float, float] | None = None, *, threat: int = 0) -> list[Alert]:
        """One analysis tick. ``threat`` = current gank threat level (0 safe, 1 warning, 2 danger).

        Returns ``[]`` or ``[one MACRO_TIP alert]``. Never raises.
        """
        try:
            with self._lock:
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

    def pressure(self) -> dict[str, Any] | None:
        """Visible enemy team pressure: ``{"visible", "centroid", "side", "grouped"}`` (None in safe mode)."""
        with self._lock:
            return dict(self._pressure) if self._pressure is not None else None

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
        self._pressure: dict[str, Any] | None = None
        self._said: list[tuple[float, str, str]] = []

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
        return _Ctx(t=t, gt=self._game_time(game, t), team=team, enemy_team=enemy_team, me_pos=pos,
                     my_zone=zone, my_lane=my_lane, role_lane=ROLE_LANE.get(my_role or ""), my_role=my_role,
                     dead=bool(getattr(me_player, "is_dead", False)), safe=self._safe,
                     enemies_vis=enemies_vis, allies_vis=allies_vis, enemies_all=enemies_all,
                     jungler_alias=jalias, jungler=jtrack, jungler_hidden_s=jhidden,
                     objectives=list(objectives or []), me_player=me_player)

    def _update_locked(self, t: Any, tracker: Any, game: Any, roles: Any, objectives: Any,
                       my_pos: Any, threat: Any) -> list[Alert]:
        now = _finite(t)
        if now is None:
            return []
        if self._last_t is not None and now < self._last_t - 1.0:
            self._clear()                                   # clock went back: new timeline
        self._last_t = now
        if game is None or getattr(game, "me", None) is None or not bool(getattr(game, "is_summoners_rift", False)):
            self._insights, self._pressure = [], None
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
            gap = SAFETY_GAP_S if rule == "numbers_bad" else GLOBAL_GAP_S
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

    # ---------------------------------------------------------------- personal tracking
    def _track_personal(self, ctx: _Ctx) -> None:
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
                   self._rule_vision):
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
                   if geometry.dist(p, pit) < PIT_RADIUS or (geometry.side_of(*p) == side
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
        my_side = ctx.my_lane or ctx.role_lane
        if tr is not None and getattr(tr, "visible", False):
            appeared = getattr(tr, "appeared_at", None) or getattr(tr, "first_seen", None)
            pos = _track_pos(tr)
            if pos is not None and appeared is not None:
                jside = map_side(*pos)
                if jside in ("top", "bot") and my_side in ("top", "mid", "bot") and my_side != jside:
                    self._jg_side_info = (ctx.t, jside, my_side)
                    fresh = 0.0 <= ctx.t - float(appeared) <= JUNGLER_SIDE_FRESH_S
                    if fresh and appeared != self._jg_side_done and geometry.dist(pos, ctx.me_pos) > 0.35:
                        out.append(("jungler_side", f"Leur jungler est {SIDE_FR[jside]} : tu peux jouer plus "
                                                    f"agressif {SIDE_FR[my_side]}."))
        hidden = ctx.jungler_hidden_s
        if hidden is not None and hidden >= JUNGLER_UNSEEN_S and ctx.my_lane is not None:
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

    # ---------------------------------------------------------------- insights
    def _build_insights(self, ctx: _Ctx) -> list[str]:
        items: list[tuple[int, str]] = []
        if not ctx.safe and not ctx.dead and ctx.me_pos is not None and not self._in_my_base(ctx):
            en, al = self._numbers(ctx)
            if en >= 2 and en - al >= 2:
                items.append((100, f"{en} contre {al} autour de toi"))
            elif en >= 1 and al - en >= 2:
                items.append((60, f"{al} contre {en} autour de toi : engage"))
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
                items.append((80, f"{name} {clock}{extra}"))
            elif pit is not None and not ctx.safe and kind in _WINDOW_NAMES:
                far = [p for _tr, p in ctx.enemies_vis if geometry.dist(p, pit[0]) > WINDOW_FAR]
                if len(far) >= 4:
                    items.append((85, f"{name} dispo · {len(far)} ennemis loin"))
        if not ctx.safe:
            n = self._missing(ctx)
            if n >= MISSING_MIN:
                items.append((70, f"{min(n, 5)} ennemis disparus"))
            info = self._pressure or {}
            if info.get("grouped"):
                items.append((55, f"Ennemis groupés {SIDE_FR.get(info.get('side'), '')} ({info.get('visible')})"))
            js = self._jg_side_info
            if js is not None and 0.0 <= ctx.t - js[0] <= INSIGHT_JUNGLER_S:
                items.append((50, f"JGL {SIDE_FR[js[1]]} → joue agressif {SIDE_FR[js[2]]}"))
            if (self._in_enemy_jungle(ctx) and ctx.jungler_hidden_s is not None
                    and ctx.jungler_hidden_s >= DEEP_UNSEEN_S):
                items.append((90, "Jungle ennemie, leur JGL invisible"))
        target = self._cs_target(ctx)
        if target is not None and ctx.gt >= 300:
            cs = _finite((getattr(ctx.me_player, "scores", None) or {}).get("creepScore")) or 0.0
            cspm = cs / (ctx.gt / 60.0)
            if cspm < target - 0.5:
                items.append((20, f"CS/min {fmt_dec(round(cspm, 1))} · objectif {fmt_dec(target)}"))
        if (self._ward_change_gt is not None and ctx.gt >= VISION_MIN_GT
                and ctx.gt - self._ward_change_gt >= VISION_STALE_S):
            items.append((15, "Pas de balise depuis 3 min"))
        items.sort(key=lambda x: -x[0])
        return [text for _p, text in items[:4]]


__all__ = ["MapCoach", "map_side", "fmt_dec", "PITS", "TURRETS", "GLOBAL_GAP_S", "ENEMY_RULES"]
