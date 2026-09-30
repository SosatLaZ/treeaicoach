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
* **Confirmation.** A threat must be seen on >= 3 consecutive fresh observations with a good
  detection score (>= 0.4) and, when identified, a good identity score (>= 0.6). Anonymous
  icons need 5 observations with a detection score >= 0.5 and only ever raise a DANGER.
  Observations overlapping an ally icon (< 0.02) are ambiguous and do not count.
* **Levels.** WARNING: identified enemy jungler / roamer inside the warn radius and coming
  towards me (radial velocity + significant distance trend, with hysteresis). DANGER: inside
  the danger radius.
* **Phrases.** One gank alert per tick, merged: "Lee Sin arrive par la rivière !" (WARNING,
  with the direction it comes from), "Gank ! Lee Sin, recule !" (DANGER), and for several
  threats at once "Gank bot : Lee Sin et Ahri !" (``COLLAPSE``, ", recule !" at DANGER).
* **JUNGLER_SPOTTED** (INFO) - only when it changes something: the enemy jungler reappears
  (first sighting after 1:30, or after >= 25 s hidden), at least the warn radius away, on the
  OTHER side of the map (top / bot half) than where it was last seen; at most every 45 s.
* **LANER_MIA** (option) - my lane opponent is hidden for >= 6 s while I am in my lane, after
  3:00 -> INFO, once per disappearance.

Nothing is produced when I am dead (``game.me.is_dead``), in my base, when my position has been
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
from treeaicoach.geometry import (
    classify_zone,
    dist,
    in_fountain,
    is_base,
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
APPROACH_ON_TICKS = 3            # consecutive positive evaluations to switch "approaching" on
APPROACH_OFF_TICKS = 3           # consecutive negative evaluations to switch it off
TREND_WINDOW_S = 2.5             # distance-series window of the significance test
TREND_MIN_POINTS = 4
TREND_MIN_SPAN_S = 0.35
TREND_T_STAT = -3.5              # slope must be below this many standard errors
TREND_SE_FLOOR = 0.0015          # standard-error floor (perfectly clean data)
DIST_HISTORY_MAXLEN = 48
MY_POS_MAX_AGE_S = 3.0           # my position unknown for longer -> no alert
LANE_WINDOW_S = 90.0             # lane-opponent zone statistics window
LANE_MIN_OBSERVED_S = 5.0
LANE_MIN_FRACTION = 0.5
MY_LANE_MIN_FRACTION = 0.4       # my own lane from my zone history (no Riot position)
# confirmation of a threat
CONFIRM_FRAMES = 3               # consecutive good observations for an identified enemy
ANON_CONFIRM_FRAMES = 5          # ... for an anonymous one (DANGER only)
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

_LANES = ("top", "mid", "bot")
_LANE_DIRECTION = {"top": "par le haut", "mid": "par le milieu", "bot": "par le bas"}


def _finite(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        f = float(x)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


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
    seen_t: float = 0.0                  # last tick this key existed in the tracker


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
        return (me is not None and other is not None and me.source in ("riot", "smite")
                and other.source in ("riot", "smite"))


class GankAnalyzer:
    """Turns the tracker state into raw gank alerts. See the module docstring."""

    def __init__(self, cfg: Config) -> None:
        self._lock = threading.RLock()
        self._cfg = cfg
        self._states: dict[str, _TrackState] = {}
        self._state = GankState()
        self._last_t: float | None = None
        self._roles = RoleResolver()
        self._jg_last_side: str | None = None
        self._spotted_t: float | None = None

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
            self._roles.reset()
            self._jg_last_side = None
            self._spotted_t = None

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
        if (is_base(my_zone) and (my_team is None or zone_owner(my_zone) == my_team)) \
                or in_fountain(me_pos[0], me_pos[1], my_team):
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
        alerts: list[Alert] = []
        approaching: set[str] = set()
        lane_opps: set[str] = set()
        jungler_key: str | None = None
        threats: list[_Threat] = []
        companions: list[_Threat] = []       # coming in too, approach not yet established
        laner_tracks: list[Track] = []
        pending_anon: list[tuple[Track, _TrackState]] = []

        for tr in enemies:
            st = self._states.get(tr.key)
            if st is None:
                st = self._states[tr.key] = _TrackState()
            st.seen_t = now
            relation = roster.relation(tr)
            if relation == "ally":                 # identified as one of my allies: never
                st.confirm = 0
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

            d = dist(me_pos, pos)
            self._record_distance(tr, st, dist(me_raw, tr.raw_position() or pos))
            self._confirm(tr, st, relation, ally_pts)
            moving_in = self._update_approach(tr, st, me_pos, my_vel, pos, now)
            if moving_in:
                approaching.add(tr.key)

            if is_jungler:
                spotted = self._jungler_spotted(tr, st, roster, gt, d, warn, my_team, now)
                if spotted is not None:
                    alerts.append(spotted)
                self._jg_last_side = side_of(pos[0], pos[1])
            if lane_opp or d >= warn * COMPANION_RADIUS_FACTOR:
                continue
            if relation == "anon":
                if d < warn:
                    pending_anon.append((tr, st))   # decided once the lane opponents are known
                continue
            if not self._opt("alert_jungler_approach" if is_jungler else "alert_roam", True):
                continue
            if st.confirm < CONFIRM_FRAMES:
                continue
            threat = _Threat(track_key=tr.key, member=tr.alias or tr.key,
                             name=roster.name(tr.alias), alias=tr.alias, level=Level.WARNING,
                             d=d, jungler=is_jungler, direction=self._direction(tr, my_team))
            if d < danger:
                threats.append(dataclasses.replace(threat, level=Level.DANGER))
            elif d < warn and moving_in:
                threats.append(threat)
            elif moving_in or st.on_count >= 1:
                companions.append(threat)           # coming too, a little behind

        for tr, st in pending_anon:
            if self._is_laner_ghost(tr, laner_tracks, roster, my_lane, now):
                lane_opps.add(tr.key)
                continue
            if not self._opt("alert_roam", True):
                continue
            pos = tr.position()
            if pos is None or dist(me_pos, pos) >= danger or st.confirm < ANON_CONFIRM_FRAMES:
                continue
            threats.append(_Threat(track_key=tr.key, member=tr.key, name=None, alias=None,
                                   level=Level.DANGER, d=dist(me_pos, pos), jungler=False,
                                   direction=None))

        if threats:
            threats += companions             # simultaneous arrivals: one sentence
        gank = self._gank_alerts(threats, now, my_zone, my_lane)
        alerts = gank + alerts
        self._forget(now, {tr.key for tr in enemies})
        alerts.sort(key=lambda a: -int(a.level))
        self._state = GankState(
            t=now, level=max((int(a.level) for a in gank), default=-1), suppressed=None,
            approaching=frozenset(approaching), lane_opponents=frozenset(lane_opps),
            jungler_key=jungler_key, my_lane=my_lane,
            my_role=roster.my_role_info.role if roster.my_role_info is not None else None,
            threats=frozenset(th.track_key for th in threats), roles=self._roles_tuple())
        return alerts

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
        names = [th.name for th in sorted(threats, key=lambda th: (not th.jungler, th.d)) if th.name]
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
    def _trend(st: _TrackState) -> tuple[float, float] | None:
        """Least-squares slope of the distance over the last window and its standard error."""
        pts = list(st.dists)
        if not pts:
            return None
        t_last = pts[-1][0]
        pts = [p for p in pts if t_last - p[0] <= TREND_WINDOW_S]
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
        significant = trend is not None and trend[0] < 0 and trend[0] / trend[1] < TREND_T_STAT
        if st.approaching:
            still = radial < APPROACH_RELEASE_SPEED and trend is not None and trend[0] < 0
            if still:
                st.off_count = 0
            else:
                st.off_count += 1
                if st.off_count >= APPROACH_OFF_TICKS:
                    st.approaching, st.on_count, st.off_count = False, 0, 0
        else:
            if radial < APPROACH_SPEED and significant:
                st.on_count += 1
                if st.on_count >= APPROACH_ON_TICKS:
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
        hidden = now - tr.last_seen
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
