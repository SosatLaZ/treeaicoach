"""Gank detection from the tracked minimap icons (ARCHITECTURE.md §4.13).

:class:`GankAnalyzer` looks at the :class:`~treeaicoach.tracker.Tracker` state once per analysis
tick and returns the **raw** alerts whose condition holds right now (the engine passes them to
:class:`~treeaicoach.alerts.AlertThrottler`, which removes the repetitions):

* **JUNGLER_APPROACH** - the enemy jungler (``GameInfo.enemy_jungler()``) is visible closer than
  the warn radius and comes towards me (radial velocity < -0.006 / s) or has just appeared
  (< 1 s) -> WARNING; closer than the danger radius -> DANGER.
* **ROAM_APPROACH** - same rule for any other enemy (identified or anonymous) that is not my
  lane opponent. Lane opponent: same Riot position (BOTTOM and UTILITY grouped) when both
  positions are known, else >= 50 % of its visible time of the last 90 s spent in my lane
  (with >= 5 s observed).
* **COLLAPSE** - >= 2 visible enemies within the warn radius, at least one of them not my lane
  opponent and coming towards me (or just out of the fog) -> DANGER. (The back-and-forth of my
  lane opponent is laning, not a collapse: its own approach does not count.)
* **JUNGLER_SPOTTED** - the enemy jungler shows up after >= 25 s hidden (or for the first time
  after 1:30 of game time) at least the warn radius away -> INFO with its zone ("dans la
  rivière du bas"). One alert per appearance.
* **LANER_MIA** (option) - my lane opponent is hidden for >= 6 s while I am in my lane, after
  3:00 -> INFO, once per disappearance.

Nothing is produced when I am dead (``game.me.is_dead``), in my base, when my position has been
unknown for more than 3 s, outside Summoner's Rift, or when the matching option is disabled.

Noise handling: the tracker velocity alone is too noisy for the small -0.006 / s threshold
(icon jitter of +-0.01 gives ~0.008 / s of noise), so "comes towards me" also requires the
distance series to decrease *significantly* (least-squares slope below -2 standard errors)
and uses a small hysteresis (2 ticks to switch on, 3 to switch off).

Thread safety: :meth:`GankAnalyzer.update` runs on the analysis thread; :meth:`state` returns
an immutable snapshot for the overlay / UI threads. Never raises from its public methods.
"""

from __future__ import annotations

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
    lane_of,
    normalize_team,
    zone_label_fr,
    zone_owner,
)

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
APPROACH_ON_TICKS = 2            # consecutive positive evaluations to switch "approaching" on
APPROACH_OFF_TICKS = 3           # consecutive negative evaluations to switch it off
TREND_WINDOW_S = 1.5             # distance-series window of the significance test
TREND_MIN_POINTS = 4
TREND_MIN_SPAN_S = 0.35
TREND_T_STAT = -2.0              # slope must be below this many standard errors
TREND_SE_FLOOR = 0.0015          # standard-error floor (perfectly clean data)
DIST_HISTORY_MAXLEN = 48
JUST_APPEARED_S = 1.0            # "vient d'apparaître"
MY_POS_MAX_AGE_S = 3.0           # my position unknown for longer -> no alert
LANE_WINDOW_S = 90.0             # lane-opponent zone statistics window
LANE_MIN_OBSERVED_S = 5.0
LANE_MIN_FRACTION = 0.5
MY_LANE_MIN_FRACTION = 0.4       # my own lane from my zone history (no Riot position)
SPOTTED_HIDDEN_S = 25.0
SPOTTED_FIRST_AFTER_GT = 90.0    # first sighting after 1:30
SPOTTED_WINDOW_S = 1.5           # the appearance must be this recent to be announced
MIA_HIDDEN_S = 6.0
MIA_MAX_HIDDEN_S = 30.0          # stale disappearances are not announced
MIA_AFTER_GT = 180.0
GAME_TIME_EXTRAPOLATION_MAX_S = 3.0
STATE_MAXLEN = 64                # per-track analyser states kept at most

_POSITION_LANE: dict[str, str | None] = {
    "TOP": "top", "MIDDLE": "mid", "MID": "mid", "BOTTOM": "bot", "BOT": "bot",
    "UTILITY": "bot", "SUPPORT": "bot", "JUNGLE": None,
}
_LANES = ("top", "mid", "bot")


def _position_lane(position: Any) -> tuple[bool, str | None]:
    """``(known, lane)`` for a Riot position (``"JUNGLE"`` -> ``(True, None)``)."""
    if not isinstance(position, str):
        return False, None
    p = position.strip().upper()
    if p not in _POSITION_LANE:
        return False, None
    return True, _POSITION_LANE[p]


def _finite(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        f = float(x)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


@dataclass
class _TrackState:
    """Per enemy track memory of the analyser (analysis thread only)."""

    dists: deque = field(default_factory=lambda: deque(maxlen=DIST_HISTORY_MAXLEN))
    last_obs_t: float | None = None
    appeared_at: float | None = None
    approaching: bool = False
    on_count: int = 0
    off_count: int = 0
    spotted_for: float | None = None     # appeared_at already announced (JUNGLER_SPOTTED)
    mia_for: float | None = None         # last_seen already announced (LANER_MIA)
    seen_t: float = 0.0                  # last tick this key existed in the tracker


@dataclass(frozen=True)
class GankState:
    """Immutable snapshot of the last analysis (for the overlay / UI / engine)."""

    t: float | None = None
    level: int = -1                           # highest raw alert level of the tick, -1 = none
    suppressed: str | None = None             # why nothing is analysed ("dead", "base", ...)
    approaching: frozenset[str] = frozenset()  # enemy track keys coming towards me
    lane_opponents: frozenset[str] = frozenset()
    jungler_key: str | None = None
    my_lane: str | None = None


class GankAnalyzer:
    """Turns the tracker state into raw gank alerts. See the module docstring."""

    def __init__(self, cfg: Config) -> None:
        self._lock = threading.RLock()
        self._cfg = cfg
        self._states: dict[str, _TrackState] = {}
        self._state = GankState()
        self._last_t: float | None = None

    # -- public API ---------------------------------------------------------------------

    def apply_config(self, cfg: Config) -> None:
        """Use new settings from the next tick on (toggles, radii, sensitivity)."""
        with self._lock:
            if cfg is not None:
                self._cfg = cfg

    def reset(self) -> None:
        """Forget everything (new game)."""
        with self._lock:
            self._states.clear()
            self._state = GankState()
            self._last_t = None

    def state(self) -> GankState:
        """Snapshot of the last analysis (thread-safe, immutable)."""
        with self._lock:
            return self._state

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

    def _suppress(self, reason: str, t: float) -> list[Alert]:
        self._state = GankState(t=t, suppressed=reason)
        # approach states would be stale when the analysis resumes
        for st in self._states.values():
            st.approaching, st.on_count, st.off_count = False, 0, 0
            st.dists.clear()
        return []

    def _update_locked(self, t: Any, tracker: Tracker, game: GameInfo | None) -> list[Alert]:
        now = _finite(t)
        if now is None or tracker is None:
            return []
        if self._last_t is not None and now < self._last_t - 1.0:
            self.reset()                       # new timeline
        self._last_t = now

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

        warn, danger = self._radii()
        gt = self._game_time(game, now)
        jungler_alias = self._jungler_alias(game)
        my_lane, my_pos_known = self._my_lane(me, me_info, now)

        enemies = tracker.enemies(visible_only=False)
        alerts: list[Alert] = []
        approaching: set[str] = set()
        lane_opps: set[str] = set()
        jungler_key: str | None = None
        near: list[tuple[Track, float, bool, bool]] = []   # (track, d, lane opponent, moving in)

        for tr in enemies:
            st = self._states.get(tr.key)
            if st is None:
                st = self._states[tr.key] = _TrackState()
            st.seen_t = now
            is_jungler = bool(jungler_alias and tr.alias and tr.alias.casefold() == jungler_alias)
            if is_jungler:
                jungler_key = tr.key
            lane_opp = (not is_jungler) and self._is_lane_opponent(
                tr, game, my_lane, my_pos_known, now)
            if lane_opp:
                lane_opps.add(tr.key)
            pos = tr.position()
            if pos is None:
                continue
            if not tr.visible:
                st.approaching, st.on_count, st.off_count = False, 0, 0
                if lane_opp:
                    mia = self._laner_mia(tr, st, game, gt, my_zone, my_lane, now)
                    if mia is not None:
                        alerts.append(mia)
                continue

            d = dist(me_pos, pos)
            self._record_distance(tr, st, d)
            moving_in = self._update_approach(tr, st, me_pos, my_vel, pos, now)
            if moving_in:
                approaching.add(tr.key)
            just_appeared = tr.appeared_at is not None and now - tr.appeared_at < JUST_APPEARED_S

            if is_jungler:
                spotted = self._jungler_spotted(tr, st, game, gt, d, warn, my_team, now)
                if spotted is not None:
                    alerts.append(spotted)
            if d < warn:
                near.append((tr, d, lane_opp, moving_in or just_appeared))
                if lane_opp:
                    continue
                kind = AlertKind.JUNGLER_APPROACH if is_jungler else AlertKind.ROAM_APPROACH
                enabled = self._opt("alert_jungler_approach" if is_jungler else "alert_roam", True)
                if not enabled:
                    continue
                if d < danger:
                    level = Level.DANGER
                elif moving_in or just_appeared:
                    level = Level.WARNING
                else:
                    continue
                alerts.append(self._make(kind, level, now, tr, game))

        if self._opt("alert_collapse", True) and len(near) >= 2 \
                and any(m and not lo for _tr, _d, lo, m in near):
            n = len(near)
            alerts.append(Alert(kind=AlertKind.COLLAPSE, level=Level.DANGER,
                                text=phrase(AlertKind.COLLAPSE, Level.DANGER, None, count=n),
                                key=alert_key(AlertKind.COLLAPSE), t=now, alias=None))

        self._forget(now, {tr.key for tr in enemies})
        alerts.sort(key=lambda a: -int(a.level))
        self._state = GankState(
            t=now, level=max((int(a.level) for a in alerts), default=-1), suppressed=None,
            approaching=frozenset(approaching), lane_opponents=frozenset(lane_opps),
            jungler_key=jungler_key, my_lane=my_lane)
        return alerts

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
    def _jungler_alias(game: GameInfo | None) -> str | None:
        if game is None:
            return None
        try:
            p = game.enemy_jungler()
        except Exception:
            return None
        alias = getattr(p, "champion_alias", None) if p is not None else None
        return alias.casefold() if isinstance(alias, str) and alias else None

    @staticmethod
    def _player(game: GameInfo | None, alias: str | None) -> PlayerInfo | None:
        if game is None or not alias:
            return None
        try:
            return game.player_by_alias(alias)
        except Exception:
            return None

    @staticmethod
    def _my_lane(me: Track, me_info: PlayerInfo | None, now: float) -> tuple[str | None, bool]:
        """``(my lane, known from Riot)``; without a Riot position, from my zone history."""
        known, lane = _position_lane(getattr(me_info, "position", None))
        if known:
            return lane, True
        best, frac = None, 0.0
        for ln in _LANES:
            f = me.zone_fraction(ln, LANE_WINDOW_S, now)
            if f > frac:
                best, frac = ln, f
        if best is not None and frac >= MY_LANE_MIN_FRACTION:
            return best, False
        return lane_of(me.zone()), False

    def _is_lane_opponent(self, tr: Track, game: GameInfo | None, my_lane: str | None,
                          my_pos_known: bool, now: float) -> bool:
        if my_lane is None:
            return False
        player = self._player(game, tr.alias)
        if my_pos_known and player is not None:
            known, lane = _position_lane(getattr(player, "position", None))
            if known:
                return lane == my_lane
        if tr.observed_time(LANE_WINDOW_S, now) < LANE_MIN_OBSERVED_S:
            return False
        return tr.zone_fraction(my_lane, LANE_WINDOW_S, now) >= LANE_MIN_FRACTION

    @staticmethod
    def _record_distance(tr: Track, st: _TrackState, d: float) -> None:
        """Distance series (one sample per new observation of the enemy)."""
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

    def _display_name(self, game: GameInfo | None, alias: str | None) -> str | None:
        player = self._player(game, alias)
        name = getattr(player, "champion_name", None) if player is not None else None
        if isinstance(name, str) and name.strip():
            return name.strip()
        return alias or None

    def _make(self, kind: AlertKind, level: Level, now: float, tr: Track,
              game: GameInfo | None, zone_label: str | None = None) -> Alert:
        name = self._display_name(game, tr.alias)
        return Alert(kind=kind, level=level, text=phrase(kind, level, name, zone_label),
                     key=alert_key(kind, tr.alias or tr.key), t=now, alias=tr.alias)

    def _jungler_spotted(self, tr: Track, st: _TrackState, game: GameInfo | None,
                         gt: float | None, d: float, warn: float, my_team: str | None,
                         now: float) -> Alert | None:
        if not self._opt("alert_jungler_spotted", True):
            return None
        appeared = tr.appeared_at
        if appeared is None or now - appeared > SPOTTED_WINDOW_S or st.spotted_for == appeared:
            return None
        hidden = tr.prev_hidden_s
        if hidden is None:
            ok = gt is not None and gt >= SPOTTED_FIRST_AFTER_GT
        else:
            ok = hidden >= SPOTTED_HIDDEN_S
        if not ok:
            st.spotted_for = appeared              # this appearance does not qualify
            return None
        if d < warn:
            return None                            # the approach alerts speak instead
        st.spotted_for = appeared
        zone = tr.zone()
        label = zone_label_fr(zone, my_team) if zone is not None else None
        return self._make(AlertKind.JUNGLER_SPOTTED, Level.INFO, now, tr, game, label or None)

    def _laner_mia(self, tr: Track, st: _TrackState, game: GameInfo | None, gt: float | None,
                   my_zone: Any, my_lane: str | None, now: float) -> Alert | None:
        if not self._opt("alert_laner_mia", False):
            return None
        if gt is None or gt < MIA_AFTER_GT or my_lane is None or lane_of(my_zone) != my_lane:
            return None
        hidden = now - tr.last_seen
        if hidden < MIA_HIDDEN_S or hidden > MIA_MAX_HIDDEN_S or st.mia_for == tr.last_seen:
            return None
        st.mia_for = tr.last_seen
        return self._make(AlertKind.LANER_MIA, Level.INFO, now, tr, game)

    def _forget(self, now: float, present: set[str]) -> None:
        for k in [k for k in self._states if k not in present]:
            del self._states[k]
        if len(self._states) > STATE_MAXLEN:
            for _ts, k in sorted((st.seen_t, k) for k, st in self._states.items())[
                    : len(self._states) - STATE_MAXLEN]:
                del self._states[k]
