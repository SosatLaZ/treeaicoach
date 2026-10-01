"""Gank detection from the tracked minimap icons (ARCHITECTURE.md §4.13) - calm policy.

:class:`GankAnalyzer` looks at the :class:`~treeaicoach.tracker.Tracker` state once per analysis
tick and returns the **raw** alerts whose condition holds right now (the engine passes them to
:class:`~treeaicoach.alerts.AlertThrottler`, which removes the repetitions: the same gank is not
repeated for 12 s). Few alerts, but trustworthy ones:

* **Who is an enemy.** The relation of an identified icon comes from the ROSTER: its alias must
  be a player of the enemy team (Live Client Data API). An icon identified as an ally (wrong-team
  identification, "Renata Glasc approche" about my own support) is never announced. Without an
  identity (or with a doubtful one, identity score < 0.6), the relation of the detector class is
  used, but such anonymous icons may only raise a DANGER (see below).
* **Roles.** :class:`~treeaicoach.roles.RoleResolver` gives every player a role (Riot position,
  Smite, else early-game minimap occupancy + champion priors + summoner spells). My lane
  opponents are the enemies with my role (bot lane: the BOTTOM + UTILITY pair); the enemy
  jungler is the enemy assigned JUNGLE. Lane opponents never raise a gank alert (laning is not a
  gank). Without Riot positions, an enemy that spends >= 50 % of its visible time of the last
  90 s in my lane is also a lane opponent, and an anonymous icon that shows up where my
  (identified) lane opponent was last seen, or that comes from my lane while my lane
  opponent(s) are not visible elsewhere, is taken as that lane opponent.
* **Confirmation (latency first).** A threat must be seen on >= 2 consecutive fresh
  observations with a good detection score (>= 0.4) and, when identified, a good identity score
  (>= 0.6). Inside the danger radius, ONE good frame is enough when the identity score is
  >= 0.8 or when the icon popped out of the fog close to me. Anonymous icons need 3
  observations with a detection score >= 0.5 and only ever raise a DANGER.
  Observations overlapping an ally icon (< 0.02) are ambiguous and do not count.
* **Levels.** WARNING: identified enemy jungler / roamer inside the warn radius and coming
  towards me (radial velocity + significant distance trend over ~1 s, hysteresis only on
  switching off), or that popped out of the fog inside the warn radius (immediate). DANGER:
  inside the danger radius.
* **Travel time through the walls.** "Inside the radius" is decided on the estimated time
  the enemy needs to reach me, not on the straight line: geodesic distance on the walkable
  mask (:func:`treeaicoach.fog_tracker.shared_reachability`, one distance field from my cell,
  cached while I stay within 1 cell, computed only when an enemy is within a coarse radius)
  minus a Flash, divided by the enemy's movement speed (boots speed: the speed measured on the
  minimap is too noisy, +-0.03 / s, to tell a faster champion apart). WARNING when that ETA <= ``(warn_radius - Flash) / boots speed`` (~7.4 s by
  default) and the enemy comes towards me, DANGER when <= ``(danger_radius - Flash) / boots
  speed`` (~3.5 s): identical to the radii in the open, but an enemy behind a wall (his
  raptors while I am mid) is far. Without the walkable mask, the straight-line radii are used.
* **Pre-alert** (``cfg.gank_pre_alert``): when the enemy jungler pops out of the fog inside the
  warn radius, "Lee Sin !" right away; the full sentence follows if it keeps coming.
* **Phrases.** One gank alert per tick, merged: "Lee Sin arrive par la rivière !" (WARNING,
  with the direction it comes from), "Gank ! Lee Sin, recule !" (DANGER), and for several
  threats at once "Gank bot : Lee Sin et Ahri !" (``COLLAPSE``, ", recule !" at DANGER).
* **JUNGLER_SPOTTED** (INFO) - only when it changes something: the enemy jungler reappears
  (first sighting after 1:30, or after >= 25 s hidden), at least the warn radius away, on the
  OTHER side of the map (top / bot half) than where it was last seen; at most every 45 s.
* **LANER_MIA** (option) - my lane opponent is hidden for >= 6 s while I am in my lane, after
  3:00 -> INFO, once per disappearance.

* **Roams** (a laner, not the jungler): announced as "Roam ! Ekko, recule !" only when he
  comes at me (approach established / popped out of the fog close / very close), never while
  he farms HIS lane (I am the visitor; the personal danger module speaks then).
* **Earlier jungler warning**: an enemy jungler clearly coming at me raises the WARNING from
  :data:`JUNGLER_EARLY_FACTOR` x the warn radius (~2 s earlier).

Nothing is produced when I am dead (``game.me.is_dead``), in my FOUNTAIN (a siege of my base
is still announced), when my position has been
unknown for more than 3 s, outside Summoner's Rift, when the matching option is disabled, or in
**safe mode** (``cfg.safe_mode``: no gank / jungler-tracking alerts at all).

Thread safety: :meth:`GankAnalyzer.update` runs on the analysis thread; :meth:`state` returns
an immutable snapshot for the overlay / UI threads. Never raises from its public methods.
"""

from __future__ import annotations

import dataclasses
import logging
import math
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from treeaicoach.alerts import Alert, AlertKind, Level, alert_key, phrase
from treeaicoach.fmtutil import finite as _finite
from treeaicoach.geometry import (
    classify_zone,
    dist,
    in_fountain,
    is_jungle,
    is_river,
    lane_of,
    normalize_team,
    side_of,
    zone_label_fr,
    zone_owner,
)
from treeaicoach.roles import ROLE_LANE, RoleInfo, RoleResolver

if TYPE_CHECKING:  # pragma: no cover - typing only
    from treeaicoach.config import Config
    from treeaicoach.live_client import GameInfo, PlayerInfo
    from treeaicoach.tracker import Track, Tracker

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------------------
# Tunables (normalized minimap units, seconds)
# --------------------------------------------------------------------------------------
APPROACH_SPEED = -0.006          # radial velocity (/ s) below which an enemy "comes towards me"
APPROACH_RELEASE_SPEED = -0.003  # hysteresis: above this the approach is over
APPROACH_ON_TICKS = 3            # SLOW tier: consecutive positive evaluations to switch it on
APPROACH_OFF_TICKS = 3           # consecutive negative evaluations to switch it off
# distance-trend significance, two tiers: FAST (~1 s window, very significant: switches
# "approaching" on at once, ~0.3-0.5 s after a clean approach starts) or SLOW (long window,
# 3 consecutive ticks: survives noisy detections).
FAST_TREND_WINDOW_S = 1.0
FAST_TREND_T_STAT = -6.0
TREND_WINDOW_S = 2.5
TREND_T_STAT = -3.5              # slope must be below this many standard errors...
TREND_MIN_POINTS = 3
TREND_MIN_SPAN_S = 0.25
TREND_MIN_SLOPE = -0.010         # ...and below this (/ s): a walking champion is ~-0.025
TREND_SE_FLOOR = 0.0015          # standard-error floor (perfectly clean data)
DIST_HISTORY_MAXLEN = 48
MY_POS_MAX_AGE_S = 3.0           # my position unknown for longer -> no alert
LANE_WINDOW_S = 90.0             # lane-opponent zone statistics window
LANE_MIN_OBSERVED_S = 5.0
LANE_MIN_FRACTION = 0.5
MY_LANE_MIN_FRACTION = 0.4       # my own lane from my zone history (no Riot position)
# confirmation of a threat
CONFIRM_FRAMES = 2               # consecutive good observations for an identified enemy
ANON_CONFIRM_FRAMES = 3          # ... for an anonymous one (DANGER only)
FAST_ID_SCORE = 0.8              # DANGER on the FIRST good frame with an identity this sure...
FOG_POP_WINDOW_S = 1.0           # ...or when the icon popped out of the fog this recently, close
                                 # to me (also: immediate WARNING inside the warn radius)
MIN_DET_SCORE = 0.4              # detection confidence of a counted observation
ANON_MIN_DET_SCORE = 0.5
MIN_ID_SCORE = 0.6               # identification confidence of an identified threat
ALLY_OVERLAP_DIST = 0.02         # an enemy icon stacked on an ally icon is ambiguous
# anonymous icon = my lane opponent seen without identity
LANER_GHOST_DIST = 0.06
LANER_GHOST_MAX_HIDDEN_S = 8.0
# simultaneous arrivals: an enemy coming in within this factor of the warn radius joins the
# alert of another threat ("Gank bot : Lee Sin et Ahri !")
COMPANION_RADIUS_FACTOR = 1.25
# direction of arrival
DIRECTION_LOOKBACK_S = 2.0
# jungler spotted
SPOTTED_HIDDEN_S = 25.0
SPOTTED_FIRST_AFTER_GT = 90.0    # first sighting after 1:30
SPOTTED_WINDOW_S = 1.5           # the appearance must be this recent to be announced
SPOTTED_MIN_INTERVAL_S = 45.0
MIA_HIDDEN_S = 6.0
MIA_MAX_HIDDEN_S = 30.0          # stale disappearances are not announced
MIA_AFTER_GT = 180.0
GAME_TIME_EXTRAPOLATION_MAX_S = 3.0
STATE_MAXLEN = 64                # per-track analyser states kept at most
# travel time (ETA) through the walls
ETA_FLASH = 0.027                # a Flash (~400 units) is free distance for the ganker
ETA_REF_SPEED = 390.0 / 14870.0  # boots speed (normalized / s): the enemy's assumed speed
ETA_REFIELD_CELLS = 1            # the distance field from me is recomputed when I moved more
# earlier jungler warning (real game: deaths 0-4 s after the alert): an enemy jungler clearly
# coming at me is announced from this factor of the warn radius (ETA ~9.5 s instead of ~7.4 s)
JUNGLER_EARLY_FACTOR = 1.25
EARLY_HEADING_COS = 0.92         # ... only when he walks straight at me (cos of the heading angle)
EARLY_TICKS = 5                  # ... on this many consecutive ticks (velocity noise)
EARLY_MIN_SPEED = 0.015          # ... at a walking speed (normalized / s), not drifting
# a roamer (laner) inside the danger radius without coming at me is only a gank when this close
ROAM_STILL_DANGER_FACTOR = 0.6

_LANES = ("top", "mid", "bot")
_LANE_DIRECTION = {"top": "par le haut", "mid": "par le milieu", "bot": "par le bas"}


def pre_alert_text(name: str) -> str:
    """The pre-alert sentence ("Lee Sin !"), pre-generated by ``tts_neural.roster_phrases``."""
    return f"{name} !"


def _key(s: Any) -> str:
    return "".join(ch for ch in s if ch.isalnum()).casefold() if isinstance(s, str) else ""


@dataclass
class _TrackState:
    """Per enemy track memory of the analyser (analysis thread only)."""

    dists: deque = field(default_factory=lambda: deque(maxlen=DIST_HISTORY_MAXLEN))
    last_obs_t: float | None = None
    appeared_at: float | None = None
    approaching: bool = False
    on_count: int = 0
    off_count: int = 0
    confirm: int = 0                     # consecutive good fresh observations
    confirm_obs_t: float | None = None   # last observation counted
    spotted_for: float | None = None     # appeared_at already examined (JUNGLER_SPOTTED)
    mia_for: float | None = None         # last_seen already announced (LANER_MIA)
    pop_d: float | None = None           # distance to me when it popped out of the fog
    pop_for: float | None = None         # appeared_at of ``pop_d``
    pre_for: float | None = None         # appeared_at already pre-announced ("Lee Sin !")
    seen_t: float = 0.0                  # last tick this key existed in the tracker
    early_n: int = 0                     # consecutive ticks walking straight at me (early warning)


@dataclass(frozen=True)
class _Threat:
    track_key: str
    member: str
    name: str | None          # display name (None = anonymous)
    alias: str | None
    level: Level
    d: float
    jungler: bool
    direction: str | None


@dataclass(frozen=True)
class GankState:
    """Immutable snapshot of the last analysis (for the overlay / UI / engine)."""

    t: float | None = None
    level: int = -1                           # highest raw gank level of the tick, -1 = none
    suppressed: str | None = None             # why nothing is analysed ("dead", "safe_mode"...)
    approaching: frozenset[str] = frozenset()  # enemy track keys coming towards me
    lane_opponents: frozenset[str] = frozenset()
    jungler_key: str | None = None
    my_lane: str | None = None
    my_role: str | None = None
    threats: frozenset[str] = frozenset()     # enemy track keys behind this tick's gank alert
    roles: tuple[tuple[str, str, str], ...] = ()   # (alias, side, role) of every player
    etas: tuple[tuple[str, float], ...] = ()  # (enemy track key, seconds to reach me) of close enemies


class _PathDistance:
    """Geodesic distance from me to an enemy on the walkable mask (cached distance field).

    The field is computed from my grid cell up to ``max_d`` and reused while I stay within
    :data:`ETA_REFIELD_CELLS` cells; my displacement since then is subtracted (never
    overestimates). ``None`` when the walkable mask is unavailable (straight-line fallback)."""

    def __init__(self) -> None:
        self._reach: Any = None
        self._failed = False
        self._field: Any = None
        self._cell: tuple[int, int] | None = None
        self._uv: tuple[float, float] | None = None
        self._max_d = 0.0

    def reset(self) -> None:
        self._field, self._cell, self._uv, self._max_d = None, None, None, 0.0

    def _get_reach(self) -> Any:
        if self._reach is None and not self._failed:
            try:
                from treeaicoach.fog_tracker import shared_reachability

                self._reach = shared_reachability()
            except Exception:
                log.exception("Walkable mask unavailable: straight-line gank radii")
                self._failed = True
        return self._reach

    def distance(self, me: tuple[float, float], enemy: tuple[float, float], max_d: float) -> float | None:
        reach = self._get_reach()
        if reach is None:
            return None
        try:
            cell = reach.cell_of(me)
            if self._field is None or self._cell is None or max_d > self._max_d + 1e-9 \
                    or max(abs(cell[0] - self._cell[0]), abs(cell[1] - self._cell[1])) > ETA_REFIELD_CELLS:
                self._field = reach.distance_field(me, max_dist=max_d)
                self._cell, self._uv, self._max_d = cell, (float(me[0]), float(me[1])), max_d
            fld = self._field
            g = fld.shape[0]
            ex, ey = reach.cell_of(enemy)
            best = math.inf
            for rad in (0, 1, 2):            # an icon on a wall edge: nearest reached cell
                win = fld[max(0, ey - rad):ey + rad + 1, max(0, ex - rad):ex + rad + 1]
                m = float(win.min()) if win.size else math.inf
                if math.isfinite(m):
                    best = m + rad / g
                    break
            if not math.isfinite(best):
                return math.inf
            moved = dist(me, self._uv) if self._uv is not None else 0.0
            return max(dist(me, enemy), best - moved)
        except Exception:
            log.exception("Gank travel distance failed: straight-line fallback")
            self._failed = True
            return None


class _Roster:
    """What the API says about the players, resolved once per tick."""

    def __init__(self, game: GameInfo | None, roles: RoleResolver) -> None:
        self.game = game
        self.has = game is not None and (bool(getattr(game, "enemies", None))
                                         or getattr(game, "me", None) is not None)
        self.enemy: dict[str, PlayerInfo] = {}
        self.ally: dict[str, PlayerInfo] = {}
        if game is not None:
            me = getattr(game, "me", None)
            for p in ([me] if me is not None else []) + list(getattr(game, "allies", None) or ()):
                for k in (_key(getattr(p, "champion_alias", "")), _key(getattr(p, "champion_name", ""))):
                    if k:
                        self.ally[k] = p
            for p in getattr(game, "enemies", None) or ():
                for k in (_key(getattr(p, "champion_alias", "")), _key(getattr(p, "champion_name", ""))):
                    if k:
                        self.enemy[k] = p
        jungler = roles.enemy_jungler() if game is not None else None
        if jungler is None and game is not None:
            try:
                p = game.enemy_jungler()
                jungler = getattr(p, "champion_alias", None) if p is not None else None
            except Exception:
                jungler = None
        self.jungler = _key(jungler) or None
        me_info = roles.me()
        self.my_role_info: RoleInfo | None = me_info
        self.lane_opps = frozenset(_key(a) for a in roles.lane_opponents())
        self.roles = roles

    def relation(self, tr: Track) -> str:
        """``"enemy"`` / ``"ally"`` from the identity (roster), ``"anon"`` without identity."""
        k = _key(tr.alias)
        if not k:
            return "anon" if tr.relation == "enemy" else "ally"
        if not self.has:
            return "enemy" if tr.relation == "enemy" else "ally"
        if k in self.enemy and k not in self.ally:
            return "enemy"
        if k in self.ally and k not in self.enemy:
            return "ally"
        if k in self.ally and k in self.enemy:          # mirror match: trust the track side
            return "enemy" if tr.relation == "enemy" else "ally"
        return "anon"                                    # identity outside the roster: ignore it

    def name(self, alias: str | None) -> str | None:
        p = self.enemy.get(_key(alias))
        name = getattr(p, "champion_name", None) if p is not None else None
        if isinstance(name, str) and name.strip():
            return name.strip()
        return alias or None

    def role_certain(self, alias: str | None) -> bool:
        me = self.my_role_info
        other = self.roles.info(alias, "enemy") if alias else None
        sure = ("riot", "smite", "observed")
        return (me is not None and other is not None and me.source in sure and other.source in sure)


class GankAnalyzer:
    """Turns the tracker state into raw gank alerts. See the module docstring."""

    def __init__(self, cfg: Config) -> None:
        self._lock = threading.RLock()
        self._cfg = cfg
        self._states: dict[str, _TrackState] = {}
        self._state = GankState()
        self._last_t: float | None = None
        self._first_t: float | None = None       # first analysed tick of this timeline
        self._roles = RoleResolver()
        self._jg_last_side: str | None = None
        self._spotted_t: float | None = None
        self._paths = _PathDistance()

    # -- public API ---------------------------------------------------------------------

    def apply_config(self, cfg: Config) -> None:
        """Use new settings from the next tick on (toggles, radii, sensitivity, safe mode)."""
        with self._lock:
            if cfg is not None:
                self._cfg = cfg

    def reset(self) -> None:
        """Forget everything (new game)."""
        with self._lock:
            self._states.clear()
            self._state = GankState()
            self._last_t = None
            self._first_t = None
            self._roles.reset()
            self._jg_last_side = None
            self._spotted_t = None
            self._paths.reset()

    def state(self) -> GankState:
        """Snapshot of the last analysis (thread-safe, immutable)."""
        with self._lock:
            return self._state

    @property
    def role_resolver(self) -> RoleResolver:
        return self._roles

    def roles(self) -> dict[str, RoleInfo]:
        """Role of every player (``alias -> RoleInfo``), for the UI / overlay."""
        return self._roles.roles()

    def is_approaching(self, key: str) -> bool:
        """True if the enemy track ``key`` was coming towards me at the last tick."""
        return key in self.state().approaching

    def update(self, t: float, tracker: Tracker, game: GameInfo | None) -> list[Alert]:
        """Raw (unfiltered) alerts for time ``t``. Never raises (logs and returns [])."""
        try:
            with self._lock:
                return self._update_locked(t, tracker, game)
        except Exception:
            log.exception("GankAnalyzer.update failed")
            return []

    # -- configuration helpers ----------------------------------------------------------

    def _radii(self) -> tuple[float, float]:
        cfg = self._cfg
        try:
            warn = float(cfg.effective_warn_radius())
            danger = float(cfg.effective_danger_radius())
        except Exception:
            warn, danger = 0.22, 0.12
        if not math.isfinite(warn) or warn <= 0:
            warn = 0.22
        if not math.isfinite(danger) or danger <= 0:
            danger = 0.12
        return warn, min(danger, warn)

    def _opt(self, name: str, default: bool) -> bool:
        val = getattr(self._cfg, name, default)
        return val if isinstance(val, bool) else default

    # -- main logic -----------------------------------------------------------------------

    def _roles_tuple(self) -> tuple[tuple[str, str, str], ...]:
        return tuple(sorted((i.alias, i.side, i.role or "") for i in self._roles.roles().values()))

    def _suppress(self, reason: str, t: float) -> list[Alert]:
        self._state = GankState(t=t, suppressed=reason, roles=self._roles_tuple(),
                                my_role=self._roles.my_role())
        # approach states would be stale when the analysis resumes
        for st in self._states.values():
            st.approaching, st.on_count, st.off_count = False, 0, 0
            st.confirm, st.confirm_obs_t = 0, None
            st.dists.clear()
        return []

    def _update_locked(self, t: Any, tracker: Tracker, game: GameInfo | None) -> list[Alert]:
        now = _finite(t)
        if now is None or tracker is None:
            return []
        if self._last_t is not None and now < self._last_t - 1.0:
            self.reset()                       # new timeline
        self._last_t = now
        if self._first_t is None:
            self._first_t = now
        self._roles.update(now, tracker, game)

        if self._opt("safe_mode", False):
            return self._suppress("safe_mode", now)
        if game is not None and not bool(getattr(game, "is_summoners_rift", True)):
            return self._suppress("mode", now)
        me_info: PlayerInfo | None = getattr(game, "me", None) if game is not None else None
        if me_info is not None and bool(getattr(me_info, "is_dead", False)):
            return self._suppress("dead", now)
        me = tracker.me()
        me_pos = me.position() if me is not None else None
        if me is None or me_pos is None or now - me.last_seen > MY_POS_MAX_AGE_S:
            return self._suppress("unknown_position", now)
        my_team = normalize_team(getattr(me_info, "team", None)) or normalize_team(me.team)
        my_zone = classify_zone(me_pos[0], me_pos[1])
        # only the fountain is silent: during a siege of my base (inhibitors, Nexus turrets) the
        # ganks must still be announced (real game: 3 deaths in my base at 33-35 min, no alert)
        if in_fountain(me_pos[0], me_pos[1], my_team):
            return self._suppress("base", now)
        my_vel = me.velocity() if me.visible else (0.0, 0.0)
        me_raw = me.raw_position() or me_pos

        warn, danger = self._radii()
        gt = self._game_time(game, now)
        roster = _Roster(game, self._roles)
        my_lane = self._my_lane(me, roster.my_role_info, now)
        ally_pts = [p for p in (a.raw_position() for a in tracker.allies(visible_only=True)
                                if a.key != me.key) if p is not None]
        ally_pts.append(me_raw)

        enemies = tracker.enemies(visible_only=False)
        # dead enemies (Live API): no icon on the map, never a threat nor "missing"
        dead = {_key(getattr(p, "champion_alias", "")) for p in (getattr(game, "enemies", None) or [])
                if bool(getattr(p, "is_dead", False))} - {""} if game is not None else set()
        alerts: list[Alert] = []
        approaching: set[str] = set()
        lane_opps: set[str] = set()
        jungler_key: str | None = None
        threats: list[_Threat] = []
        companions: list[_Threat] = []       # coming in too, approach not yet established
        laner_tracks: list[Track] = []
        pending_anon: list[tuple[Track, _TrackState, float]] = []
        pre_alerts: list[Alert] = []

        coarse = warn * COMPANION_RADIUS_FACTOR + ETA_FLASH   # travel times only computed inside
        etas: list[tuple[str, float]] = []
        for tr in enemies:
            st = self._states.get(tr.key)
            if st is None:
                st = self._states[tr.key] = _TrackState()
                st.confirm = self._seed_confirm(tr)
            st.seen_t = now
            relation = roster.relation(tr)
            if relation == "ally" or (dead and tr.alias and _key(tr.alias) in dead):
                st.confirm = 0                     # an ally, or a dead enemy: never a threat
                st.approaching, st.on_count, st.off_count = False, 0, 0
                continue
            if relation == "enemy" and (_finite(tr.id_score) or 0.0) < MIN_ID_SCORE:
                relation = "anon"                  # doubtful identity: treated as anonymous
            is_jungler = relation == "enemy" and roster.jungler is not None \
                and _key(tr.alias) == roster.jungler
            if is_jungler:
                jungler_key = tr.key
            lane_opp = False
            if relation == "enemy" and not is_jungler:
                lane_opp = self._is_lane_opponent(tr, roster, my_lane, now)
            if lane_opp:
                lane_opps.add(tr.key)
                laner_tracks.append(tr)
            pos = tr.position()
            if pos is None:
                continue
            if not tr.visible:
                st.approaching, st.on_count, st.off_count = False, 0, 0
                st.confirm, st.confirm_obs_t = 0, None
                if lane_opp:
                    mia = self._laner_mia(tr, st, roster, gt, my_zone, my_lane, now)
                    if mia is not None:
                        alerts.append(mia)
                continue

            d_line = dist(me_pos, pos)
            d = self._effective_distance(tr, me_pos, pos, d_line, coarse)
            if d < coarse:
                etas.append((tr.key, round(max(0.0, d - ETA_FLASH) / ETA_REF_SPEED, 2)))
            self._record_distance(tr, st, dist(me_raw, tr.raw_position() or pos))
            self._confirm(tr, st, relation, ally_pts)
            moving_in = self._update_approach(tr, st, me_pos, my_vel, pos, now)
            if moving_in:
                approaching.add(tr.key)
            popped = self._popped_close(tr, st, d, warn, now)
            st.early_n = st.early_n + 1 if is_jungler and moving_in and \
                self._heading_at_me(tr, me_pos, my_vel, pos) else 0

            if is_jungler:
                spotted = self._jungler_spotted(tr, st, roster, gt, d_line, warn, my_team, now)
                if spotted is not None:
                    alerts.append(spotted)
                self._jg_last_side = side_of(pos[0], pos[1])
            if lane_opp or d >= warn * COMPANION_RADIUS_FACTOR:
                continue
            if relation == "enemy" and not is_jungler and self._in_own_lane(tr, roster, pos, my_lane):
                continue                           # a laner farming his own lane is not a roam
            if relation == "anon":
                if d < warn:
                    pending_anon.append((tr, st, d))   # decided once the lane opponents are known
                continue
            if not self._opt("alert_jungler_approach" if is_jungler else "alert_roam", True):
                continue
            if st.confirm < 1:
                continue
            fast = popped or (_finite(tr.id_score) or 0.0) >= FAST_ID_SCORE
            pre = is_jungler and self._opt("gank_pre_alert", True)
            if pre and popped and st.pre_for != tr.appeared_at and d >= danger:
                st.pre_for = tr.appeared_at       # "Lee Sin !" right now, the sentence follows
                pre_alerts.append(self._pre_alert(tr, roster, now))
            if st.confirm < CONFIRM_FRAMES and not (fast and d < danger) \
                    and not (popped and d < warn and not pre):
                continue
            threat = _Threat(track_key=tr.key, member=tr.alias or tr.key,
                             name=roster.name(tr.alias), alias=tr.alias, level=Level.WARNING,
                             d=d, jungler=is_jungler, direction=self._direction(tr, my_team))
            if d < danger and (is_jungler or moving_in or st.on_count >= 1 or popped
                               or d < danger * ROAM_STILL_DANGER_FACTOR):
                threats.append(dataclasses.replace(threat, level=Level.DANGER))
            elif d < danger:
                continue                            # a roamer standing still near me: no gank call
            elif d < warn and (moving_in or (popped and not pre and is_jungler)):
                threats.append(threat)              # popped out of the fog inside the warn radius
            elif is_jungler and moving_in and d < warn * JUNGLER_EARLY_FACTOR \
                    and st.early_n >= EARLY_TICKS:
                threats.append(threat)              # the jungler walking straight at me: ~2 s earlier
            elif moving_in or st.on_count >= 1:
                companions.append(threat)           # coming too, a little behind

        for tr, st, d_eff in pending_anon:
            if self._is_laner_ghost(tr, laner_tracks, roster, my_lane, now):
                lane_opps.add(tr.key)
                continue
            if not self._opt("alert_roam", True):
                continue
            if d_eff >= danger or st.confirm < ANON_CONFIRM_FRAMES:
                continue
            threats.append(_Threat(track_key=tr.key, member=tr.key, name=None, alias=None,
                                   level=Level.DANGER, d=d_eff, jungler=False,
                                   direction=None))

        if threats:
            threats += companions             # simultaneous arrivals: one sentence
        gank = self._gank_alerts(threats, now, my_zone, my_lane)
        if not gank:
            gank = pre_alerts
        alerts = gank + alerts
        self._forget(now, {tr.key for tr in enemies})
        alerts.sort(key=lambda a: -int(a.level))
        self._state = GankState(
            t=now, level=max((int(a.level) for a in gank), default=-1), suppressed=None,
            approaching=frozenset(approaching), lane_opponents=frozenset(lane_opps),
            jungler_key=jungler_key, my_lane=my_lane,
            my_role=roster.my_role_info.role if roster.my_role_info is not None else None,
            threats=frozenset(th.track_key for th in threats), roles=self._roles_tuple(),
            etas=tuple(sorted(etas, key=lambda x: x[1])))
        return alerts

    # -- travel time ------------------------------------------------------------------------

    def _effective_distance(self, tr: Track, me_pos: tuple[float, float], pos: tuple[float, float],
                            d_line: float, coarse: float) -> float:
        """Distance equivalent of the enemy's travel time to me (``Flash + ETA x boots speed``,
        i.e. the path length through the walls): comparing it with the radii is comparing the
        ETA with :meth:`eta_thresholds`. Straight line when the walkable mask is unavailable
        or the enemy is beyond the coarse radius (where only the straight line matters)."""
        if d_line >= coarse:
            return d_line
        geo = self._paths.distance(me_pos, pos, coarse + 0.05)
        if geo is None:
            return d_line
        if not math.isfinite(geo):
            return max(d_line, coarse)
        return geo

    def eta_thresholds(self) -> tuple[float, float]:
        """(WARNING, DANGER) travel-time thresholds in seconds for the current settings."""
        warn, danger = self._radii()
        return (max(0.0, warn - ETA_FLASH) / ETA_REF_SPEED, max(0.0, danger - ETA_FLASH) / ETA_REF_SPEED)

    @staticmethod
    def _seed_confirm(tr: Track) -> int:
        """A track just exposed by the tracker's champion locker already has good observations:
        count them (as :meth:`_confirm` would have), so the locker adds no latency."""
        if tr.alias:
            return 0
        try:
            scores = list(tr.recent_scores())[:-1]   # the last one is counted by _confirm
        except Exception:
            return 0
        n = 0
        for sc in reversed(scores):
            if (_finite(sc) or 0.0) < ANON_MIN_DET_SCORE:
                break
            n += 1
        return min(n, ANON_CONFIRM_FRAMES - 1)

    # -- alert building -------------------------------------------------------------------

    def _gank_alerts(self, threats: list[_Threat], now: float, my_zone: Any,
                     my_lane: str | None) -> list[Alert]:
        """One merged alert for all the threats of the tick."""
        if not threats:
            return []
        threats = sorted(threats, key=lambda th: (-int(th.level), not th.jungler, th.d))
        if len(threats) == 1 or not self._opt("alert_collapse", True):
            return [self._single(threats[0], now)] if len(threats) == 1 else \
                [self._single(th, now) for th in threats]
        level = max(th.level for th in threats)
        # canonical order (jungler first, then alphabetical): the sentence does not change with
        # the distances, so it is the pre-generated one (tts_neural.roster_phrases)
        names = [th.name for th in sorted(threats, key=lambda th: (not th.jungler, (th.name or "").casefold()))
                 if th.name]
        lane = lane_of(my_zone) or my_lane
        members = tuple(sorted(th.member for th in threats))
        text = phrase(AlertKind.COLLAPSE, level, None, lane, count=len(threats), names=names)
        return [Alert(kind=AlertKind.COLLAPSE, level=level, text=text,
                      key=alert_key(AlertKind.COLLAPSE, "+".join(members)), t=now, alias=None,
                      members=members)]

    @staticmethod
    def _single(th: _Threat, now: float) -> Alert:
        kind = AlertKind.JUNGLER_APPROACH if th.jungler else AlertKind.ROAM_APPROACH
        return Alert(kind=kind, level=th.level, text=phrase(kind, th.level, th.name, th.direction),
                     key=alert_key(kind, th.member), t=now, alias=th.alias, members=(th.member,))

    @staticmethod
    def _pre_alert(tr: Track, roster: _Roster, now: float) -> Alert:
        """Short pre-alert "Lee Sin !" (the jungler popped out of the fog close to me).
        No ``members``: the full gank sentence that may follow is not a repetition of it."""
        name = roster.name(tr.alias) or "Le jungler"
        return Alert(kind=AlertKind.JUNGLER_APPROACH, level=Level.WARNING, text=pre_alert_text(name),
                     key=alert_key(AlertKind.JUNGLER_APPROACH, f"pre-{tr.alias or tr.key}"), t=now,
                     alias=tr.alias, members=())

    def _popped_close(self, tr: Track, st: _TrackState, d: float, warn: float, now: float) -> bool:
        """The icon popped out of the fog (or appeared for the first time) less than
        ``FOG_POP_WINDOW_S`` ago, inside the warn radius (distance at its first sighting)."""
        appeared = tr.appeared_at
        if appeared is None or now - appeared > FOG_POP_WINDOW_S:
            return False
        if tr.prev_hidden_s is None and (self._first_t is None or appeared - self._first_t < FOG_POP_WINDOW_S):
            return False                          # already there when the analysis started
        if st.pop_for != appeared:
            st.pop_for, st.pop_d = appeared, d
        return st.pop_d is not None and st.pop_d < warn

    # -- helpers ----------------------------------------------------------------------------

    @staticmethod
    def _game_time(game: GameInfo | None, now: float) -> float | None:
        """Current game time, extrapolated a little from the last API poll."""
        if game is None:
            return None
        gt = _finite(getattr(game, "game_time", None))
        if gt is None:
            return None
        fetched = _finite(getattr(game, "fetched_at", None))
        extra = 0.0 if fetched is None else min(max(0.0, now - fetched), GAME_TIME_EXTRAPOLATION_MAX_S)
        return gt + extra

    @staticmethod
    def _my_lane(me: Track, info: RoleInfo | None, now: float) -> str | None:
        """My lane: from my role when certain, else from my zone history, else my role."""
        role_lane = ROLE_LANE.get(info.role) if info is not None and info.role else None
        if info is not None and info.source in ("riot", "smite"):
            return role_lane
        best, frac = None, 0.0
        for ln in _LANES:
            f = me.zone_fraction(ln, LANE_WINDOW_S, now)
            if f > frac:
                best, frac = ln, f
        if best is not None and frac >= MY_LANE_MIN_FRACTION:
            return best
        if role_lane is not None:
            return role_lane
        return lane_of(me.zone())

    @staticmethod
    def _history_in_lane(tr: Track, my_lane: str | None, now: float) -> bool:
        if my_lane is None or tr.observed_time(LANE_WINDOW_S, now) < LANE_MIN_OBSERVED_S:
            return False
        return tr.zone_fraction(my_lane, LANE_WINDOW_S, now) >= LANE_MIN_FRACTION

    def _is_lane_opponent(self, tr: Track, roster: _Roster, my_lane: str | None,
                          now: float) -> bool:
        by_role = _key(tr.alias) in roster.lane_opps
        if roster.role_certain(tr.alias):
            return by_role
        return by_role or self._history_in_lane(tr, my_lane, now)

    @staticmethod
    def _heading_at_me(tr: Track, me_pos: tuple[float, float], my_vel: tuple[float, float],
                       pos: tuple[float, float]) -> bool:
        """The enemy walks straight at me (not along a camp-to-camp path that passes by)."""
        ev = tr.velocity()
        vx, vy = ev[0] - my_vel[0], ev[1] - my_vel[1]
        sp = math.hypot(vx, vy)
        dx, dy = me_pos[0] - pos[0], me_pos[1] - pos[1]
        dn = math.hypot(dx, dy)
        if sp < EARLY_MIN_SPEED or dn < 1e-6:
            return False
        return (vx * dx + vy * dy) / (sp * dn) >= EARLY_HEADING_COS

    def _in_own_lane(self, tr: Track, roster: _Roster, pos: tuple[float, float],
                     my_lane: str | None) -> bool:
        """A laner (not the jungler) standing in HIS lane, which is not mine: I am the visitor
        (Ekko farming mid while I walk by), not the target of a roam."""
        try:
            info = roster.roles.info(tr.alias, "enemy") if tr.alias else None
            lane = ROLE_LANE.get(info.role) if info is not None and info.role else None
        except Exception:
            lane = None
        if lane is None or lane == my_lane:
            return False
        return lane_of(classify_zone(pos[0], pos[1])) == lane

    def _is_laner_ghost(self, tr: Track, laners: list[Track], roster: _Roster,
                        my_lane: str | None, now: float) -> bool:
        """An anonymous icon that is in fact my lane opponent (not identified this time)."""
        if self._history_in_lane(tr, my_lane, now):
            return True
        pos = tr.position()
        if pos is None:
            return False
        first = tr.points()[0] if tr.points() else None
        start = (first[1], first[2]) if first is not None else pos
        # came from my lane while my lane opponent(s) are nowhere to be seen: it is them
        if my_lane is not None and roster.lane_opps \
                and lane_of(classify_zone(start[0], start[1])) == my_lane \
                and not any(lt.visible and lt.last_seen >= tr.last_seen - 1e-9 for lt in laners):
            return True
        for lt in laners:
            if lt.visible and lt.last_seen >= tr.last_seen - 1e-9:
                continue                          # the lane opponent is seen elsewhere right now
            if now - lt.last_seen > LANER_GHOST_MAX_HIDDEN_S:
                continue
            lp = lt.position()
            if lp is not None and (dist(lp, start) < LANER_GHOST_DIST or dist(lp, pos) < LANER_GHOST_DIST):
                return True
        return False

    @staticmethod
    def _confirm(tr: Track, st: _TrackState, relation: str, ally_pts: list) -> None:
        """Count consecutive good fresh observations of the track."""
        if st.confirm_obs_t is not None and tr.last_seen <= st.confirm_obs_t:
            return
        st.confirm_obs_t = tr.last_seen
        score = _finite(tr.score) or 0.0
        if relation == "anon":
            good = score >= ANON_MIN_DET_SCORE
        else:
            good = score >= MIN_DET_SCORE and (_finite(tr.id_score) or 0.0) >= MIN_ID_SCORE
        if not good:
            st.confirm = 0
            return
        raw = tr.raw_position()
        if raw is not None and any(dist(raw, a) < ALLY_OVERLAP_DIST for a in ally_pts):
            return                                # ambiguous: neither counted nor reset
        st.confirm += 1

    @staticmethod
    def _direction(tr: Track, my_team: str | None) -> str | None:
        """Where the enemy comes from: "par la rivière", "par ta jungle", "par le haut"..."""
        pts = tr.points()
        if not pts:
            return None
        t_last = pts[-1][0]
        origin = next((p for p in pts if t_last - p[0] <= DIRECTION_LOOKBACK_S), pts[-1])
        z = classify_zone(origin[1], origin[2])
        if is_river(z):
            return "par la rivière"
        if is_jungle(z):
            owner = zone_owner(z)
            if my_team is not None and owner is not None:
                return "par ta jungle" if owner == my_team else "par la jungle ennemie"
            return "par la jungle"
        lane = lane_of(z)
        return _LANE_DIRECTION.get(lane) if lane else None

    @staticmethod
    def _record_distance(tr: Track, st: _TrackState, d: float) -> None:
        """Distance series from RAW positions (independent noise -> honest standard error),
        one sample per new observation of the enemy."""
        if st.appeared_at != tr.appeared_at:
            st.appeared_at = tr.appeared_at
            st.dists.clear()
            st.approaching, st.on_count, st.off_count = False, 0, 0
        if st.last_obs_t is not None and tr.last_seen <= st.last_obs_t:
            return
        st.last_obs_t = tr.last_seen
        st.dists.append((tr.last_seen, d))

    @staticmethod
    def _trend(st: _TrackState, window: float = TREND_WINDOW_S) -> tuple[float, float] | None:
        """Least-squares slope of the distance over the last window and its standard error."""
        pts = list(st.dists)
        if not pts:
            return None
        t_last = pts[-1][0]
        pts = [p for p in pts if t_last - p[0] <= window]
        n = len(pts)
        if n < TREND_MIN_POINTS or pts[-1][0] - pts[0][0] < TREND_MIN_SPAN_S:
            return None
        tm = sum(p[0] for p in pts) / n
        dm = sum(p[1] for p in pts) / n
        stt = sum((p[0] - tm) ** 2 for p in pts)
        if stt <= 1e-12:
            return None
        slope = sum((p[0] - tm) * (p[1] - dm) for p in pts) / stt
        rss = sum((p[1] - dm - slope * (p[0] - tm)) ** 2 for p in pts)
        se = math.sqrt(rss / max(1, n - 2) / stt)
        return slope, max(se, TREND_SE_FLOOR)

    def _update_approach(self, tr: Track, st: _TrackState, me_pos: tuple[float, float],
                         my_vel: tuple[float, float], pos: tuple[float, float], now: float) -> bool:
        """Radial velocity test + significance of the distance trend, with hysteresis."""
        rx, ry = pos[0] - me_pos[0], pos[1] - me_pos[1]
        norm = math.hypot(rx, ry)
        ev = tr.velocity()
        vx, vy = ev[0] - my_vel[0], ev[1] - my_vel[1]
        radial = (rx * vx + ry * vy) / norm if norm > 1e-6 else 0.0
        trend = self._trend(st)
        fast = self._trend(st, FAST_TREND_WINDOW_S)
        def sig(tr_: tuple[float, float] | None, t_stat: float) -> bool:
            return tr_ is not None and tr_[0] < TREND_MIN_SLOPE and tr_[0] / tr_[1] < t_stat

        significant = sig(trend, TREND_T_STAT)
        immediate = radial < APPROACH_SPEED and sig(fast, FAST_TREND_T_STAT)
        if st.approaching:
            still = radial < APPROACH_RELEASE_SPEED and trend is not None and trend[0] < 0
            if still:
                st.off_count = 0
            else:
                st.off_count += 1
                if st.off_count >= APPROACH_OFF_TICKS:
                    st.approaching, st.on_count, st.off_count = False, 0, 0
        else:
            if radial < APPROACH_SPEED and (significant or immediate):
                st.on_count += 1
                if st.on_count >= APPROACH_ON_TICKS or immediate:
                    st.approaching, st.off_count = True, 0
            else:
                st.on_count = 0
        return st.approaching

    def _jungler_spotted(self, tr: Track, st: _TrackState, roster: _Roster,
                         gt: float | None, d: float, warn: float, my_team: str | None,
                         now: float) -> Alert | None:
        """Announce the enemy jungler only when it reappears on the other side of the map."""
        if not self._opt("alert_jungler_spotted", True):
            return None
        appeared = tr.appeared_at
        if appeared is None or now - appeared > SPOTTED_WINDOW_S or st.spotted_for == appeared:
            return None
        st.spotted_for = appeared
        hidden = tr.prev_hidden_s
        if hidden is None:
            ok = gt is not None and gt >= SPOTTED_FIRST_AFTER_GT
        else:
            ok = hidden >= SPOTTED_HIDDEN_S
        if not ok or d < warn:
            return None                            # too soon, or the approach alerts speak
        pos = tr.position()
        if pos is None:
            return None
        side = side_of(pos[0], pos[1])
        if side == self._jg_last_side:
            return None                            # nothing changed
        if self._spotted_t is not None and now - self._spotted_t < SPOTTED_MIN_INTERVAL_S:
            return None
        self._spotted_t = now
        zone = tr.zone()
        label = zone_label_fr(zone, my_team) if zone is not None else None
        name = roster.name(tr.alias)
        return Alert(kind=AlertKind.JUNGLER_SPOTTED, level=Level.INFO,
                     text=phrase(AlertKind.JUNGLER_SPOTTED, Level.INFO, name, label or None),
                     key=alert_key(AlertKind.JUNGLER_SPOTTED, tr.alias or tr.key), t=now,
                     alias=tr.alias)

    def _laner_mia(self, tr: Track, st: _TrackState, roster: _Roster, gt: float | None,
                   my_zone: Any, my_lane: str | None, now: float) -> Alert | None:
        if not self._opt("alert_laner_mia", False):
            return None
        if gt is None or gt < MIA_AFTER_GT or my_lane is None or lane_of(my_zone) != my_lane:
            return None
        # a stacked hold (icon drawn under another one) is not a disappearance
        released = _finite(getattr(tr, "stack_released_at", None))
        hidden = now - max(tr.last_seen, released if released is not None else -math.inf)
        if hidden < MIA_HIDDEN_S or hidden > MIA_MAX_HIDDEN_S or st.mia_for == tr.last_seen:
            return None
        st.mia_for = tr.last_seen
        name = roster.name(tr.alias)
        return Alert(kind=AlertKind.LANER_MIA, level=Level.INFO,
                     text=phrase(AlertKind.LANER_MIA, Level.INFO, name),
                     key=alert_key(AlertKind.LANER_MIA, tr.alias or tr.key), t=now,
                     alias=tr.alias)

    def _forget(self, now: float, present: set[str]) -> None:
        for k in [k for k in self._states if k not in present]:
            del self._states[k]
        if len(self._states) > STATE_MAXLEN:
            for _ts, k in sorted((st.seen_t, k) for k, st in self._states.items())[
                    : len(self._states) - STATE_MAXLEN]:
                del self._states[k]
