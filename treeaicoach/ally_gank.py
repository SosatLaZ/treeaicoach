"""Ganks on my FRIENDS: the gank rules of gank.py applied from an ally's position.

The bot Discord announces "Bot gank" / "Top gank" / "Mid gank" / "Jungle gank" for every friend of
the voice channel who plays in my team (discord_friends.py maps the voice members to the allied
champions). :class:`AllyGankWatcher` runs once per analysis tick with the champion aliases of those
friends and returns at most one call per friend and per gank:

* **The friend's position** is his allied track on the minimap (tracker.py: allies are tracked like
  enemies; under a stack the track keeps the occluder's position). Unknown for more than
  :data:`ALLY_POS_MAX_AGE_S`, dead (Live Client) or on his fountain: nothing for him.
* **Threats** are judged exactly as gank.py judges them for me (same constants, same code for the
  approach test: radial velocity + significant distance trend, hysteresis): an IDENTIFIED enemy
  (identity score >= 0.6, in the enemy roster) seen on >= 2 good observations (one when he popped
  out of the fog close / with a very sure identity inside the danger radius); the enemy jungler
  coming at the friend inside the warn radius (``JUNGLER_EARLY_FACTOR`` x the radius when he walks
  straight at him), a roamer coming at him, anyone popping out of the fog inside the warn radius,
  the jungler inside the danger radius. The distance is the travel distance through the walls
  (walkable mask, one cached distance field per friend) like mine.
* **Not a gank**: the friend's lane opponents (the enemy laners of his role; the BOTTOM + UTILITY
  pair for the bot lane), a laner farming HIS own lane while the friend walks by, anonymous icons
  (no identity: for me they may only raise a DANGER after 3 frames; for a friend we keep the
  precision, the call goes to 4 people), and the settings that switch my own calls off
  (``alert_jungler_approach`` / ``alert_roam``, safe mode).
* **The lane said** is the friend's: the lane he stands in, the nearest lane from a river bush,
  "jungle" in a jungle, else his role's lane.
* **Rate**: one call per friend per :data:`ALLY_REPEAT_S`; the bot's filter then merges the
  friends of one lane (bot duo: one "Bot gank") and keeps the global limits.

Pure (no Qt / Discord), the analysis thread only; never raises from :meth:`update`.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from treeaicoach.alerts import Level
from treeaicoach.fmtutil import finite as _finite
from treeaicoach.gank import (
    CONFIRM_FRAMES,
    COMPANION_RADIUS_FACTOR,
    EARLY_TICKS,
    ETA_FLASH,
    FAST_ID_SCORE,
    FOG_POP_WINDOW_S,
    JUNGLER_EARLY_FACTOR,
    LANE_MIN_FRACTION,
    LANE_MIN_OBSERVED_S,
    LANE_WINDOW_S,
    MIN_ID_SCORE,
    ROAM_STILL_DANGER_FACTOR,
    GankAnalyzer,
    _key,
    _PathDistance,
    _Roster,
    _TrackState,
)
from treeaicoach.geometry import classify_zone, dist, in_fountain, is_base, is_river, lane_of, normalize_team
from treeaicoach.roles import ROLE_LANE, RoleResolver

log = logging.getLogger(__name__)

ALLY_POS_MAX_AGE_S = 3.0      # the friend's position unknown for longer: no call for him
ALLY_REPEAT_S = 30.0          # one call per friend per gank (the bot also dedupes per lane)
STATE_MAXLEN = 160            # (friend, enemy) analyser states kept at most
GROUP_ALLIES = 2              # this many other allies (me included) around the friend: a group fight,
GROUP_RADIUS = 0.15           # not a gank (the friends are not alone: no call)

_LANES = ("top", "mid", "bot")


@dataclass(frozen=True)
class AllyGankCall:
    """One gank on a friend: who, which lane to say, how urgent, by whom."""

    alias: str                    # the friend's champion alias
    lane: str                     # "top" / "mid" / "bot" / "jungle"
    level: Level
    t: float
    threats: tuple[str, ...] = ()   # enemy aliases (jungler first)
    d: float = 0.0                # travel distance of the closest threat (normalised minimap units)


@dataclass
class _Friend:
    paths: _PathDistance = field(default_factory=_PathDistance)
    states: dict = field(default_factory=dict)   # enemy track key -> _TrackState
    last_call: float = -1e9
    seen_t: float = 0.0


def nearest_lane(u: float, v: float) -> str:
    """The lane nearest to a minimap position (mid ``u + v = 1``, top along the left / top edges)."""
    d = {"top": min(u, v), "mid": abs(u + v - 1.0) / 1.4142, "bot": min(1.0 - u, 1.0 - v)}
    return min(d, key=d.get)


def lane_at(pos: tuple[float, float] | None, role: str | None = None) -> str | None:
    """The lane a gank on someone standing at ``pos`` is called with: his lane, the nearest lane from a
    river bush, "jungle" in a jungle, his role's lane otherwise (None in a base)."""
    if pos is not None:
        z = classify_zone(pos[0], pos[1])
        ln = lane_of(z)
        if ln:
            return ln
        if is_river(z):
            return nearest_lane(pos[0], pos[1])
        if not is_base(z):
            return "jungle"
        return None
    return ROLE_LANE.get(str(role or "").upper()) if role else None


class AllyGankWatcher:
    """See the module docstring. ``update(t, tracker, game, friends)`` -> new calls."""

    def __init__(self, cfg: Any = None) -> None:
        self._cfg = cfg
        self._friends: dict[str, _Friend] = {}
        self._own_roles: RoleResolver | None = None
        self._last_t: float | None = None
        self._first_t: float | None = None
        self.calls: list[AllyGankCall] = []      # every call made this game (tests / diagnostics)

    def apply_config(self, cfg: Any) -> None:
        if cfg is not None:
            self._cfg = cfg

    def reset(self) -> None:
        self._friends.clear()
        self._own_roles = None
        self._last_t = self._first_t = None
        self.calls = []

    # ------------------------------------------------------------------ settings
    def _opt(self, name: str, default: bool) -> bool:
        val = getattr(self._cfg, name, default)
        return val if isinstance(val, bool) else default

    def _radii(self) -> tuple[float, float]:
        try:
            warn = float(self._cfg.effective_warn_radius())
            danger = float(self._cfg.effective_danger_radius())
        except Exception:
            warn, danger = 0.22, 0.12
        if not math.isfinite(warn) or warn <= 0:
            warn = 0.22
        if not math.isfinite(danger) or danger <= 0:
            danger = 0.12
        return warn, min(danger, warn)

    # ------------------------------------------------------------------ main
    def update(self, t: Any, tracker: Any, game: Any, friends: Any,
               roles: RoleResolver | None = None) -> list[AllyGankCall]:
        """New gank calls for the friends ``friends`` (champion aliases of my allies, me excluded).
        ``roles``: the gank analyser's role resolver (else one of our own). Never raises."""
        try:
            return self._update(t, tracker, game, friends, roles)
        except Exception:
            log.exception("AllyGankWatcher.update failed")
            return []

    def _update(self, t: Any, tracker: Any, game: Any, friends: Any, roles: RoleResolver | None) -> list[AllyGankCall]:
        now = _finite(t)
        if now is None or tracker is None:
            return []
        if self._last_t is not None and now < self._last_t - 1.0:
            self.reset()                          # new timeline
        self._last_t = now
        if self._first_t is None:
            self._first_t = now
        wanted = {_key(a): str(a) for a in (friends or ()) if _key(a)}
        if roles is None:
            if self._own_roles is None:
                self._own_roles = RoleResolver()
            roles = self._own_roles
            roles.update(now, tracker, game)
        if not wanted or self._opt("safe_mode", False):
            return []
        if game is not None and not bool(getattr(game, "is_summoners_rift", True)):
            return []
        roster = _Roster(game, roles)
        me = tracker.me()
        my_key = _key(getattr(me, "alias", None)) if me is not None else ""
        dead_allies = {_key(getattr(p, "champion_alias", "")) for p in (getattr(game, "allies", None) or [])
                       if bool(getattr(p, "is_dead", False))} if game is not None else set()
        team = normalize_team(getattr(getattr(game, "me", None), "team", None)) if game is not None else None
        allies = {(_key(a.alias) or a.key): a for a in tracker.allies(visible_only=False) if a.alias}
        ally_pts = [p for p in (a.raw_position() for a in allies.values() if a.visible) if p is not None]
        if me is not None and me.raw_position() is not None:
            ally_pts.append(me.raw_position())
        enemies = tracker.enemies(visible_only=False)
        dead = {_key(getattr(p, "champion_alias", "")) for p in (getattr(game, "enemies", None) or [])
                if bool(getattr(p, "is_dead", False))} - {""} if game is not None else set()
        warn, danger = self._radii()
        coarse = warn * COMPANION_RADIUS_FACTOR + ETA_FLASH
        out: list[AllyGankCall] = []
        for k, alias in wanted.items():
            if k == my_key:
                continue                          # my own ganks: gank.py (the PC voice + the bot)
            fr = self._friends.setdefault(k, _Friend())
            fr.seen_t = now
            atr = allies.get(k)
            pos = atr.position() if atr is not None else None
            if atr is None or pos is None or now - atr.last_seen > ALLY_POS_MAX_AGE_S or k in dead_allies \
                    or in_fountain(pos[0], pos[1], team):
                self._clear(fr)
                continue
            araw = atr.raw_position() or pos
            if GROUP_ALLIES and sum(1 for p in ally_pts if dist(p, araw) < GROUP_RADIUS) - 1 >= GROUP_ALLIES:
                continue                          # grouped with 2+ allies: a fight, not a gank
            avel = atr.velocity() if atr.visible else (0.0, 0.0)
            info = roles.info(atr.alias, "ally")
            arole = info.role if info is not None else None
            alane = ROLE_LANE.get(arole) if arole else None
            call = self._friend_threats(fr, atr, pos, araw, avel, alane, enemies, roster, dead, ally_pts,
                                        warn, danger, coarse, now)
            if call is None or now - fr.last_call < ALLY_REPEAT_S:
                continue
            lane = lane_at(pos, arole)
            if not lane:
                continue
            level, threats, d = call
            fr.last_call = now
            c = AllyGankCall(alias=atr.alias or alias, lane=lane, level=level, t=now, threats=threats, d=round(d, 3))
            out.append(c)
            self.calls.append(c)
            del self.calls[:-200]
        self._forget(now, wanted)
        return out

    # ------------------------------------------------------------------ one friend
    def _friend_threats(self, fr: _Friend, atr: Any, pos: tuple[float, float], araw: tuple[float, float],
                        avel: tuple[float, float], alane: str | None, enemies: list, roster: _Roster,
                        dead: set, ally_pts: list, warn: float, danger: float, coarse: float,
                        now: float) -> tuple[Level, tuple[str, ...], float] | None:
        threats: list[tuple[Level, bool, str, float]] = []
        present = set()
        for tr in enemies:
            present.add(tr.key)
            st = fr.states.get(tr.key)
            if st is None:
                st = fr.states[tr.key] = _TrackState()
            relation = roster.relation(tr)
            if relation != "enemy" or (tr.alias and _key(tr.alias) in dead) \
                    or (_finite(tr.id_score) or 0.0) < MIN_ID_SCORE:
                st.confirm, st.approaching, st.on_count, st.off_count = 0, False, 0, 0
                continue                           # anonymous / doubtful / ally / dead: never for a friend
            epos = tr.position()
            if epos is None or not tr.visible:
                st.approaching, st.on_count, st.off_count = False, 0, 0
                st.confirm, st.confirm_obs_t = 0, None
                continue
            is_jungler = roster.jungler is not None and _key(tr.alias) == roster.jungler
            if not is_jungler and self._lane_opponent(tr, roster, alane, now):
                continue                           # laning is not a gank
            d_line = dist(pos, epos)
            d = self._distance(fr, pos, epos, d_line, coarse)
            GankAnalyzer._record_distance(tr, st, dist(araw, tr.raw_position() or epos))
            GankAnalyzer._confirm(tr, st, "enemy", ally_pts)
            moving_in = GankAnalyzer._update_approach(tr, st, pos, avel, epos, now)
            popped = self._popped_close(tr, st, d, warn, now)
            st.early_n = st.early_n + 1 if is_jungler and moving_in and \
                GankAnalyzer._heading_at_me(tr, pos, avel, epos) else 0
            if d >= warn * JUNGLER_EARLY_FACTOR:
                continue
            if not is_jungler and self._in_own_lane(tr, roster, epos, alane):
                continue                           # a laner farming his lane while my friend walks by
            if not self._opt("alert_jungler_approach" if is_jungler else "alert_roam", True):
                continue
            if st.confirm < 1:
                continue
            fast = popped or (_finite(tr.id_score) or 0.0) >= FAST_ID_SCORE
            if st.confirm < CONFIRM_FRAMES and not (fast and d < danger) and not (popped and d < warn):
                continue
            level: Level | None = None
            if d < danger and (is_jungler or moving_in or st.on_count >= 1 or popped
                               or d < danger * ROAM_STILL_DANGER_FACTOR):
                level = Level.DANGER
            elif d < danger:
                level = None                       # a roamer standing still near him
            elif d < warn and (moving_in or (popped and is_jungler)):
                level = Level.WARNING
            elif is_jungler and moving_in and d < warn * JUNGLER_EARLY_FACTOR and st.early_n >= EARLY_TICKS:
                level = Level.WARNING
            if level is not None:
                threats.append((level, is_jungler, str(tr.alias), d))
        for key in [x for x in fr.states if x not in present]:
            del fr.states[key]
        if not threats:
            return None
        threats.sort(key=lambda x: (not x[1], -int(x[0]), x[3]))
        return max(x[0] for x in threats), tuple(x[2] for x in threats), min(x[3] for x in threats)

    def _distance(self, fr: _Friend, pos: tuple[float, float], epos: tuple[float, float], d_line: float,
                  coarse: float) -> float:
        """Travel distance through the walls (gank.py's ETA distance) from the friend."""
        if d_line >= coarse:
            return d_line
        geo = fr.paths.distance(pos, epos, coarse + 0.05)
        if geo is None:
            return d_line
        return geo if math.isfinite(geo) else max(d_line, coarse)

    def _popped_close(self, tr: Any, st: _TrackState, d: float, warn: float, now: float) -> bool:
        appeared = tr.appeared_at
        if appeared is None or now - appeared > FOG_POP_WINDOW_S:
            return False
        if tr.prev_hidden_s is None and (self._first_t is None or appeared - self._first_t < FOG_POP_WINDOW_S):
            return False
        if st.pop_for != appeared:
            st.pop_for, st.pop_d = appeared, d
        return st.pop_d is not None and st.pop_d < warn

    @staticmethod
    def _lane_opponent(tr: Any, roster: _Roster, alane: str | None, now: float) -> bool:
        """An enemy laner of my friend's lane (role, else >= 50 % of his visible time in that lane)."""
        if alane is None:
            return False
        try:
            info = roster.roles.info(tr.alias, "enemy")
        except Exception:
            info = None
        lane = ROLE_LANE.get(info.role) if info is not None and info.role else None
        if lane is not None and info is not None and info.source in ("riot", "smite", "observed"):
            return lane == alane
        if lane == alane:
            return True
        if tr.observed_time(LANE_WINDOW_S, now) < LANE_MIN_OBSERVED_S:
            return False
        return tr.zone_fraction(alane, LANE_WINDOW_S, now) >= LANE_MIN_FRACTION

    @staticmethod
    def _in_own_lane(tr: Any, roster: _Roster, epos: tuple[float, float], alane: str | None) -> bool:
        try:
            info = roster.roles.info(tr.alias, "enemy")
        except Exception:
            info = None
        lane = ROLE_LANE.get(info.role) if info is not None and info.role else None
        if lane is None or lane == alane:
            return False
        return lane_of(classify_zone(epos[0], epos[1])) == lane

    @staticmethod
    def _clear(fr: _Friend) -> None:
        for st in fr.states.values():
            st.approaching, st.on_count, st.off_count = False, 0, 0
            st.confirm, st.confirm_obs_t = 0, None
            st.dists.clear()

    def _forget(self, now: float, wanted: dict) -> None:
        for k in [k for k in self._friends if k not in wanted and now - self._friends[k].seen_t > 60.0]:
            del self._friends[k]
        for fr in self._friends.values():
            if len(fr.states) > STATE_MAXLEN:
                fr.states.clear()


__all__ = ["AllyGankWatcher", "AllyGankCall", "lane_at", "ALLY_REPEAT_S"]
