"""Temporal tracking of the champion icons seen on the minimap (ARCHITECTURE.md §4.11).

The identifier gives, for every analysed frame, a list of ``Identified`` icons (position,
relation to the player, champion alias when recognised). :class:`Tracker` turns those
per-frame observations into :class:`Track` objects that live across frames:

* **identity tracks** are keyed by the champion alias (``"LeeSin"``); an identified icon
  always updates its own track, wherever it is;
* **anonymous tracks** (``"enemy?1"``, ``"ally?2"``) hold icons whose champion is not known.
  An unidentified icon continues the nearest track of the same side (enemy / friendly) within
  :data:`ASSOC_DIST` of its predicted position: an anonymous track, or an identity track that
  was not identified in this frame and was seen less than :data:`IDENTITY_COAST_S` ago (so a
  single-frame identification miss does not spawn a fake "new" enemy). When an anonymous
  track is later identified nearby it is merged into the identity track (history kept);
* the local player (relation ``"self"``) is at most one track, returned by :meth:`Tracker.me`.

Per track: smoothed position (component-wise median of the last 3 observations), velocity
(least squares over the last ~1.2 s, ``(0, 0)`` without >= 3 points spanning >= 0.35 s),
jump detection (recall / teleport: > 0.15 in < 0.3 s, or any displacement faster than a
champion can walk, resets the history), visibility (seen < :data:`HIDE_AFTER` s ago),
appearance time (first sighting, or back in sight after >= :data:`REAPPEAR_AFTER` s hidden)
and a run-length zone history for :meth:`Track.zone_fraction`. Memory is bounded (deques with
``maxlen``, anonymous tracks forgotten after 20 s unseen, :data:`MAX_TRACKS` tracks at most).

Thread safety: :meth:`Tracker.update` runs on the analysis thread; every accessor returns
**snapshot copies** of the tracks, so other threads (overlay, recorder, UI) can read them
while the tracker keeps updating. Pure Python + geometry (numpy LUT), importable everywhere.
"""

from __future__ import annotations

import logging
import math
import threading
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from treeaicoach.geometry import Zone, classify_zone, lane_of

if TYPE_CHECKING:  # pragma: no cover - typing only (avoids importing the detector at runtime)
    from treeaicoach.identifier import Identified

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------------------
# Tunables (normalized minimap units, seconds)
# --------------------------------------------------------------------------------------
HIDE_AFTER = 0.6            # a track is visible if seen less than this ago
REAPPEAR_AFTER = 1.5        # hidden at least this long -> a new sighting sets appeared_at
POS_MEDIAN_N = 3            # smoothed position = median of the last N observations...
POS_WINDOW_S = 1.0          # ...not older than this before the newest one
VEL_WINDOW_S = 1.2          # least-squares velocity window
VEL_MIN_POINTS = 3
VEL_MIN_SPAN_S = 0.35
VEL_MAX = 0.2               # clamp of the velocity magnitude (champions walk <= ~0.06 / s)
TELEPORT_DIST = 0.15        # jump > 0.15 in < 0.3 s = recall / teleport -> history reset
TELEPORT_DT = 0.3
MAX_WALK_SPEED = 0.06       # ~900 units/s: any faster displacement is a jump...
JUMP_SLACK = 0.05           # ...beyond this slack (detection noise, Flash)
ASSOC_DIST = 0.08           # nearest-neighbour gate for unidentified icons
IDENTITY_COAST_S = 1.0      # an identity track may take unidentified icons for this long
MERGE_OVERLAP_S = 0.25      # an anonymous track seen together with an identity track is another champion
ANON_FORGET_S = 20.0        # anonymous tracks unseen for this long are dropped
PREDICT_MAX_S = 0.5         # extrapolation horizon of the association gate
ZONE_SEG_MAX_GAP = 1.0      # observation gaps above this are not counted as visible time
ZONE_HISTORY_S = 300.0      # zone history kept (s)
OBS_MAXLEN = 48             # observations kept per track (>= 1.2 s at 20 fps)
ZONE_SEG_MAXLEN = 512       # zone segments kept per track
MAX_TRACKS = 40             # hard cap on the number of tracks
SELF_STICKY_S = 3.0         # an identified "self" seen this recently ignores fallback "self" claims
BACKWARD_RESET_S = 1.0      # clock going back more than this -> new timeline (reset)

RELATIONS = ("self", "ally", "enemy")
_CLASSES = ("enemy", "ally", "self")   # detector class order (ARCHITECTURE.md §3)

_LANE_WORDS: dict[str, str] = {
    "top": "top", "mid": "mid", "middle": "mid", "bot": "bot", "bottom": "bot",
    "utility": "bot", "support": "bot", "adc": "bot",
}

Obs = tuple[float, float, float]        # (t, u, v)
ZoneSeg = tuple[float, float, Zone]     # (start, end, zone): continuous visible time in a zone


def _finite(x: Any) -> float | None:
    """Float if finite, else None (never raises)."""
    if x is None or isinstance(x, bool):
        return None
    try:
        f = float(x)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def side_of_relation(relation: str) -> str:
    """``"enemy"`` for enemies, ``"ally"`` for allies and the local player."""
    return "enemy" if relation == "enemy" else "ally"


def _zone_matcher(lane: Any) -> Callable[[Zone], bool] | None:
    """Predicate for :meth:`Track.zone_fraction`: a lane name ("top", "MIDDLE"...) or a zone."""
    if isinstance(lane, Zone):
        return lambda z: z == lane
    if not isinstance(lane, str):
        return None
    s = lane.strip().lower()
    word = _LANE_WORDS.get(s)
    if word is not None:
        return lambda z: lane_of(z) == word
    try:
        zone = Zone(s)
    except ValueError:
        zone = Zone.__members__.get(s.upper())
    if zone is None:
        return None
    return lambda z: z == zone


def _median3(values: list[float]) -> float:
    vs = sorted(values)
    n = len(vs)
    mid = n // 2
    return vs[mid] if n % 2 else 0.5 * (vs[mid - 1] + vs[mid])


# --------------------------------------------------------------------------------------
# Track
# --------------------------------------------------------------------------------------


@dataclass(eq=False)
class Track:
    """One champion followed over time. Instances returned by :class:`Tracker` are snapshots.

    ``hidden_since`` is the time of the last sighting while the track is hidden (``None`` while
    visible); ``prev_hidden_s`` is the length of the hidden period that ended at
    ``appeared_at`` (``None`` for the very first sighting).
    """

    key: str                              # alias, or "enemy?1" / "ally?1" (anonymous)
    alias: str | None
    relation: str                         # "self" | "ally" | "enemy"
    team: str | None
    first_seen: float
    last_seen: float
    visible: bool = True                  # seen less than HIDE_AFTER s ago
    appeared_at: float | None = None      # when it (re)became visible
    hidden_since: float | None = None     # last sighting time while hidden, else None
    prev_hidden_s: float | None = None    # duration of the hidden period before appeared_at
    score: float = 0.0                    # detection confidence of the last observation
    id_score: float = 0.0                 # identification score of the last identified observation
    radius: float = 0.0                   # icon radius (normalized) of the last observation
    n_obs: int = 0                        # total number of observations (merges included)
    _obs: deque = field(default_factory=lambda: deque(maxlen=OBS_MAXLEN), repr=False)
    _segs: deque = field(default_factory=lambda: deque(maxlen=ZONE_SEG_MAXLEN), repr=False)
    _last_obs_t: float | None = field(default=None, repr=False)

    # -- derived quantities -----------------------------------------------------------

    def position(self) -> tuple[float, float] | None:
        """Smoothed position: component-wise median of the last 3 recent observations."""
        obs = self._obs
        if not obs:
            return None
        t_last = obs[-1][0]
        recent: list[Obs] = []
        for o in reversed(obs):
            if t_last - o[0] > POS_WINDOW_S or len(recent) >= POS_MEDIAN_N:
                break
            recent.append(o)
        return (_median3([o[1] for o in recent]), _median3([o[2] for o in recent]))

    def raw_position(self) -> tuple[float, float] | None:
        """Last observed (unsmoothed) position."""
        return (self._obs[-1][1], self._obs[-1][2]) if self._obs else None

    def velocity(self) -> tuple[float, float]:
        """Least-squares velocity (normalized units / s) over the last ~1.2 s of observations.

        ``(0, 0)`` without at least 3 points spanning 0.35 s (e.g. just after a jump). For a
        hidden track this is the velocity observed just before it disappeared.
        """
        obs = self._obs
        if len(obs) < VEL_MIN_POINTS:
            return (0.0, 0.0)
        t_last = obs[-1][0]
        pts: list[Obs] = []
        for o in reversed(obs):
            if t_last - o[0] > VEL_WINDOW_S:
                break
            pts.append(o)
        n = len(pts)
        if n < VEL_MIN_POINTS or pts[0][0] - pts[-1][0] < VEL_MIN_SPAN_S:
            return (0.0, 0.0)
        tm = sum(o[0] for o in pts) / n
        um = sum(o[1] for o in pts) / n
        vm = sum(o[2] for o in pts) / n
        stt = sum((o[0] - tm) ** 2 for o in pts)
        if stt <= 1e-12:
            return (0.0, 0.0)
        vx = sum((o[0] - tm) * (o[1] - um) for o in pts) / stt
        vy = sum((o[0] - tm) * (o[2] - vm) for o in pts) / stt
        speed = math.hypot(vx, vy)
        if speed > VEL_MAX:
            k = VEL_MAX / speed
            vx, vy = vx * k, vy * k
        return (vx, vy)

    def speed(self) -> float:
        """Magnitude of :meth:`velocity`."""
        vx, vy = self.velocity()
        return math.hypot(vx, vy)

    def zone(self) -> Zone | None:
        """Map zone of the smoothed position (``None`` if never seen)."""
        pos = self.position()
        return classify_zone(pos[0], pos[1]) if pos is not None else None

    def hidden_for(self, now: float) -> float:
        """Seconds since the last sighting (0 while visible)."""
        if self.visible:
            return 0.0
        return max(0.0, float(now) - self.last_seen)

    def observed_time(self, window_s: float, now: float) -> float:
        """Visible time (s) recorded in the zone history during ``[now - window_s, now]``."""
        total, _hit = self._zone_time(None, window_s, now)
        return total

    def zone_time(self, lane: str | Zone, window_s: float, now: float) -> float:
        """Visible time (s) spent in ``lane`` (lane name or :class:`Zone`) during the window."""
        _total, hit = self._zone_time(_zone_matcher(lane), window_s, now)
        return hit

    def zone_fraction(self, lane: str | Zone, window_s: float, now: float) -> float:
        """Share (0..1) of the visible time of the last ``window_s`` seconds spent in ``lane``.

        ``lane`` is ``"top"`` / ``"mid"`` / ``"bot"`` (Riot positions such as ``"MIDDLE"`` or
        ``"UTILITY"`` are accepted) or a :class:`Zone`. 0 when nothing was observed.
        """
        match = _zone_matcher(lane)
        if match is None:
            return 0.0
        total, hit = self._zone_time(match, window_s, now)
        return hit / total if total > 1e-9 else 0.0

    def _zone_time(self, match: Callable[[Zone], bool] | None, window_s: float,
                   now: float) -> tuple[float, float]:
        w, n = _finite(window_s), _finite(now)
        if w is None or n is None or w <= 0:
            return 0.0, 0.0
        lo = n - w
        total = hit = 0.0
        for start, end, zone in reversed(self._segs):
            if end <= lo:
                break
            dur = min(end, n) - max(start, lo)
            if dur <= 0:
                continue
            total += dur
            if match is not None and match(zone):
                hit += dur
        return total, hit

    def points(self) -> list[tuple[float, float, float]]:
        """Recent raw observations ``(t, u, v)`` since the last history reset (oldest first)."""
        return list(self._obs)

    # -- mutation (tracker thread only) -------------------------------------------------

    def observe(self, t: float, u: float, v: float, r: float = 0.0, score: float = 0.0,
                id_score: float | None = None) -> None:
        """Add one observation at time ``t`` (non-decreasing). Used by :class:`Tracker`."""
        prev_t = self._last_obs_t
        if prev_t is None:
            self.appeared_at = t
            self.prev_hidden_s = None
            self.first_seen = min(self.first_seen, t) if self.n_obs else t
        else:
            gap = t - prev_t
            if gap >= REAPPEAR_AFTER:
                self.appeared_at = t
                self.prev_hidden_s = gap
                self._obs.clear()          # old points say nothing about the new sighting
            elif self._obs:
                _t0, u0, v0 = self._obs[-1]
                jump = math.hypot(u - u0, v - v0)
                limit = TELEPORT_DIST if gap < TELEPORT_DT else max(
                    TELEPORT_DIST, MAX_WALK_SPEED * gap + JUMP_SLACK)
                if jump > limit:
                    log.debug("Track %s: jump of %.3f in %.2f s, history reset", self.key, jump, gap)
                    self._obs.clear()
        self._obs.append((t, u, v))
        self._last_obs_t = t
        self.last_seen = t
        self.visible = True
        self.hidden_since = None
        self.n_obs += 1
        self.score = score
        self.radius = r
        if id_score is not None:
            self.id_score = id_score
        self._record_zone(prev_t, t)

    def _record_zone(self, prev_t: float | None, t: float) -> None:
        pos = self.position()
        if pos is None:
            return
        zone = classify_zone(pos[0], pos[1])
        segs = self._segs
        if prev_t is not None and 0.0 < t - prev_t <= ZONE_SEG_MAX_GAP:
            if segs and segs[-1][2] == zone and abs(segs[-1][1] - prev_t) < 1e-9:
                segs[-1] = (segs[-1][0], t, zone)
            else:
                segs.append((prev_t, t, zone))
        while segs and segs[0][1] < t - ZONE_HISTORY_S:
            segs.popleft()

    def refresh(self, now: float) -> None:
        """Update ``visible`` / ``hidden_since`` for the current time."""
        self.visible = (now - self.last_seen) < HIDE_AFTER
        self.hidden_since = None if self.visible else self.last_seen

    def absorb(self, other: Track) -> None:
        """Merge the history of ``other`` (same champion, e.g. an anonymous track) into this one."""
        if other.n_obs == 0:
            return
        if self.n_obs == 0:
            self.first_seen, self.last_seen = other.first_seen, other.last_seen
            self.appeared_at, self.prev_hidden_s = other.appeared_at, other.prev_hidden_s
            self._obs = deque(other._obs, maxlen=OBS_MAXLEN)
            self._segs = deque(other._segs, maxlen=ZONE_SEG_MAXLEN)
            self._last_obs_t = other._last_obs_t
            self.n_obs = other.n_obs
            self.score, self.radius = other.score, other.radius
            self.visible, self.hidden_since = other.visible, other.hidden_since
            return
        early, late = (self, other) if self.last_seen <= other.last_seen else (other, self)
        gap = late.first_seen - early.last_seen
        late_reappeared = (late.appeared_at is not None
                           and late.appeared_at > late.first_seen + 1e-9)
        if late_reappeared:
            appeared_at, prev_hidden = late.appeared_at, late.prev_hidden_s
        elif gap >= REAPPEAR_AFTER:
            appeared_at, prev_hidden = late.first_seen, gap
        else:
            appeared_at, prev_hidden = early.appeared_at, early.prev_hidden_s
        # observations: keep both only when they are contiguous in time
        if gap >= REAPPEAR_AFTER:
            obs = list(late._obs)
        else:
            obs = sorted(set(early._obs) | set(late._obs))
        # zone history: early segments cut where the late history starts
        cut = late._segs[0][0] if late._segs else late.first_seen
        segs = [(s, min(e, cut), z) for s, e, z in early._segs if s < cut]
        segs.extend(late._segs)
        self.first_seen = min(self.first_seen, other.first_seen)
        self.last_seen = late.last_seen
        self._last_obs_t = late._last_obs_t
        self.appeared_at, self.prev_hidden_s = appeared_at, prev_hidden
        self._obs = deque(obs, maxlen=OBS_MAXLEN)
        self._segs = deque(segs, maxlen=ZONE_SEG_MAXLEN)
        self.n_obs = self.n_obs + other.n_obs
        self.score, self.radius = late.score, late.radius
        self.visible, self.hidden_since = late.visible, late.hidden_since

    def copy(self) -> Track:
        """Independent snapshot (safe to read from another thread)."""
        return Track(
            key=self.key, alias=self.alias, relation=self.relation, team=self.team,
            first_seen=self.first_seen, last_seen=self.last_seen, visible=self.visible,
            appeared_at=self.appeared_at, hidden_since=self.hidden_since,
            prev_hidden_s=self.prev_hidden_s, score=self.score, id_score=self.id_score,
            radius=self.radius, n_obs=self.n_obs,
            _obs=deque(self._obs, maxlen=OBS_MAXLEN),
            _segs=deque(self._segs, maxlen=ZONE_SEG_MAXLEN),
            _last_obs_t=self._last_obs_t,
        )


# --------------------------------------------------------------------------------------
# Tracker
# --------------------------------------------------------------------------------------


@dataclass
class _Entry:
    """Sanitised identifier output for one icon."""

    u: float
    v: float
    r: float
    score: float
    alias: str | None
    relation: str
    team: str | None
    id_score: float

    @property
    def side(self) -> str:
        return side_of_relation(self.relation)


def _entry_from(item: Any) -> _Entry | None:
    """Duck-typed conversion of an ``Identified`` (or a bare ``Detection``); None if unusable."""
    det = getattr(item, "det", item)
    u, v = _finite(getattr(det, "u", None)), _finite(getattr(det, "v", None))
    if u is None or v is None:
        return None
    r = _finite(getattr(det, "r", 0.0)) or 0.0
    score = _finite(getattr(det, "score", 1.0))
    relation = getattr(item, "relation", None)
    if relation not in RELATIONS:
        relation = getattr(det, "cls", None)
        if relation not in _CLASSES:
            return None
    alias = getattr(item, "alias", None)
    alias = alias.strip() if isinstance(alias, str) else None
    team = getattr(item, "team", None)
    team = team if isinstance(team, str) and team else None
    id_score = _finite(getattr(item, "id_score", 0.0))
    return _Entry(u=_clamp01(u), v=_clamp01(v), r=max(0.0, r),
                  score=1.0 if score is None else score, alias=alias or None,
                  relation=relation, team=team, id_score=0.0 if id_score is None else id_score)


class Tracker:
    """Follows the champions over time from the identifier output. See the module docstring."""

    HIDE_AFTER = HIDE_AFTER

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tracks: dict[str, Track] = {}
        self._anon_counter: dict[str, int] = {"enemy": 0, "ally": 0}
        self._self_key: str | None = None
        self._last_t: float | None = None

    # -- public API ---------------------------------------------------------------------

    def update(self, t: float, identified: Iterable[Identified] | None) -> None:
        """Integrate the icons identified in the frame captured at time ``t``. Never raises."""
        try:
            with self._lock:
                self._update_locked(t, identified)
        except Exception:
            log.exception("Tracker.update failed")

    def tracks(self) -> list[Track]:
        """Snapshots of all tracks (self, allies, enemies; then by key)."""
        with self._lock:
            order = {"self": 0, "ally": 1, "enemy": 2}
            items = sorted(self._tracks.values(), key=lambda tr: (order.get(tr.relation, 3), tr.key))
            return [tr.copy() for tr in items]

    def me(self) -> Track | None:
        """Snapshot of the local player's track (at most one), or None."""
        with self._lock:
            tr = self._tracks.get(self._self_key) if self._self_key else None
            return tr.copy() if tr is not None and tr.relation == "self" else None

    def enemies(self, visible_only: bool = True) -> list[Track]:
        """Snapshots of the enemy tracks (only the visible ones by default)."""
        with self._lock:
            return [tr.copy() for tr in sorted(self._tracks.values(), key=lambda x: x.key)
                    if tr.relation == "enemy" and (tr.visible or not visible_only)]

    def allies(self, visible_only: bool = True) -> list[Track]:
        """Snapshots of the allied tracks (without me)."""
        with self._lock:
            return [tr.copy() for tr in sorted(self._tracks.values(), key=lambda x: x.key)
                    if tr.relation == "ally" and (tr.visible or not visible_only)]

    def get(self, key: str) -> Track | None:
        """Snapshot of the track ``key`` (alias or anonymous key), or None."""
        with self._lock:
            tr = self._tracks.get(key) if isinstance(key, str) else None
            return tr.copy() if tr is not None else None

    def reset(self) -> None:
        """Forget everything (new game)."""
        with self._lock:
            self._tracks.clear()
            self._anon_counter = {"enemy": 0, "ally": 0}
            self._self_key = None
            self._last_t = None

    @property
    def last_update(self) -> float | None:
        """Time of the last :meth:`update` (None before the first one / after a reset)."""
        return self._last_t

    def __len__(self) -> int:
        with self._lock:
            return len(self._tracks)

    # -- internals ----------------------------------------------------------------------

    def _now(self, t: Any) -> float | None:
        now = _finite(t)
        if now is None:
            log.debug("Tracker.update: invalid time %r ignored", t)
            return None
        last = self._last_t
        if last is not None and now < last:
            if last - now > BACKWARD_RESET_S:
                log.info("Tracker: clock went back by %.1f s, tracks reset", last - now)
                self.reset()
            else:
                now = last
        return now

    def _update_locked(self, t: float, identified: Iterable[Any] | None) -> None:
        now = self._now(t)
        if now is None:
            return
        entries = [e for e in (_entry_from(x) for x in (identified or ())) if e is not None]
        entries = self._dedupe(entries)
        # An identity-based "self" seen recently outranks the identifier's camera fallback.
        cur_self = self._tracks.get(self._self_key) if self._self_key else None
        if cur_self is not None and cur_self.alias and now - cur_self.last_seen <= SELF_STICKY_S:
            for e in entries:
                if e.relation == "self" and not e.alias:
                    e.relation = "ally"
        named = [e for e in entries if e.alias]
        anonymous = [e for e in entries if not e.alias]
        reserved: dict[str, str] = {}
        named_keys = [self._identity_key(e.alias, e.side, reserved) for e in named]  # type: ignore[arg-type]
        updated: set[str] = set()
        self_key: str | None = None

        # 1. unidentified icons continue the nearest compatible track (global greedy matching)
        for e, key in self._associate(anonymous, now, set(named_keys)):
            if key is None:
                key = self._new_anon_key(e.side)
                self._tracks[key] = Track(key=key, alias=None, relation=e.relation, team=e.team,
                                          first_seen=now, last_seen=now)
            tr = self._tracks[key]
            if tr.alias is None:
                tr.relation = self._merged_relation(tr.relation, e.relation)
            if e.team and tr.team is None:
                tr.team = e.team
            tr.observe(now, e.u, e.v, e.r, e.score)
            updated.add(key)
            if e.relation == "self" and tr.relation == "self":
                self_key = key

        # 2. identified icons update their identity track (absorbing a matching anonymous one)
        for e, key in zip(named, named_keys):
            tr = self._tracks.get(key)
            anon = self._merge_candidate(e, tr, updated)
            if tr is None:
                tr = Track(key=key, alias=e.alias, relation=e.relation, team=e.team,
                           first_seen=now, last_seen=now)
                self._tracks[key] = tr
            if anon is not None:
                log.debug("Tracker: %s identified as %s", anon.key, key)
                tr.absorb(anon)
                self._tracks.pop(anon.key, None)
                if self._self_key == anon.key:
                    self._self_key = key
            tr.relation = self._merged_relation(tr.relation, e.relation)
            if e.team:
                tr.team = e.team
            tr.observe(now, e.u, e.v, e.r, e.score, e.id_score)
            updated.add(key)
            if e.relation == "self":
                self_key = key

        # 3. at most one "self" track: the one declared self in this frame wins
        if self_key is not None:
            self._self_key = self_key
        for key, tr in self._tracks.items():
            if tr.relation == "self" and key != self._self_key:
                tr.relation = "ally"
            tr.refresh(now)

        self._forget(now)
        self._last_t = now

    @staticmethod
    def _dedupe(entries: list[_Entry]) -> list[_Entry]:
        """One "self" per frame (highest score), one icon per (alias, side)."""
        selfs = [e for e in entries if e.relation == "self"]
        if len(selfs) > 1:
            best = max(selfs, key=lambda e: (e.id_score, e.score, e.alias is not None))
            for e in selfs:
                if e is not best:
                    e.relation = "ally"
        best_by_alias: dict[tuple[str, str], _Entry] = {}
        for e in entries:
            if not e.alias:
                continue
            k = (e.alias.casefold(), e.side)
            cur = best_by_alias.get(k)
            if cur is None or (e.id_score, e.score) > (cur.id_score, cur.score):
                best_by_alias[k] = e
        for e in entries:
            if e.alias and best_by_alias.get((e.alias.casefold(), e.side)) is not e:
                e.alias = None       # duplicate identity: keep the icon as unidentified
        return entries

    @staticmethod
    def _merged_relation(current: str, observed: str) -> str:
        """New relation of a track: "self" is sticky against "ally" (same side)."""
        if observed == "ally" and current == "self":
            return "self"
        return observed

    def _identity_key(self, alias: str, side: str, reserved: dict[str, str]) -> str:
        """Track key of an identified champion: its alias (suffixed in a cross-team mirror)."""
        existing = self._tracks.get(alias)
        owner = reserved.get(alias) or (side_of_relation(existing.relation) if existing else None)
        key = alias if owner is None or owner == side else f"{alias}~{side}"
        reserved.setdefault(key, side)
        return key

    def _new_anon_key(self, side: str) -> str:
        self._anon_counter[side] = self._anon_counter.get(side, 0) + 1
        key = f"{side}?{self._anon_counter[side]}"
        while key in self._tracks:
            self._anon_counter[side] += 1
            key = f"{side}?{self._anon_counter[side]}"
        return key

    @staticmethod
    def _predicted(tr: Track, now: float) -> tuple[float, float] | None:
        pos = tr.position()
        if pos is None:
            return None
        vx, vy = tr.velocity()
        dt = min(max(0.0, now - tr.last_seen), PREDICT_MAX_S)
        return (pos[0] + vx * dt, pos[1] + vy * dt)

    def _associate(self, anonymous: list[_Entry], now: float,
                   named_keys: set[str]) -> list[tuple[_Entry, str | None]]:
        """Greedy nearest-neighbour matching of unidentified icons to existing tracks."""
        if not anonymous:
            return []
        candidates: list[tuple[str, str, tuple[float, float]]] = []
        for key, tr in self._tracks.items():
            if key in named_keys:
                continue
            if tr.alias is not None and now - tr.last_seen > IDENTITY_COAST_S:
                continue
            pred = self._predicted(tr, now)
            if pred is not None:
                candidates.append((key, side_of_relation(tr.relation), pred))
        pairs: list[tuple[float, int, str]] = []
        for i, e in enumerate(anonymous):
            for key, side, (pu, pv) in candidates:
                if side != e.side:
                    continue
                d = math.hypot(e.u - pu, e.v - pv)
                if d < ASSOC_DIST:
                    pairs.append((d, i, key))
        pairs.sort(key=lambda p: (p[0], p[1], p[2]))
        taken_e: dict[int, str] = {}
        taken_k: set[str] = set()
        for _d, i, key in pairs:
            if i in taken_e or key in taken_k:
                continue
            taken_e[i] = key
            taken_k.add(key)
        return [(e, taken_e.get(i)) for i, e in enumerate(anonymous)]

    def _merge_candidate(self, e: _Entry, tr: Track | None, updated: set[str]) -> Track | None:
        """Nearest anonymous track (same side, not updated this frame) that was this champion."""
        best: tuple[float, Track] | None = None
        for key, anon in self._tracks.items():
            if anon.alias is not None or key in updated:
                continue
            if side_of_relation(anon.relation) != e.side:
                continue
            if tr is not None and tr.n_obs and tr.last_seen > anon.first_seen + MERGE_OVERLAP_S:
                continue        # both seen at the same time: two different champions
            pos = anon.raw_position()
            if pos is None:
                continue
            d = math.hypot(e.u - pos[0], e.v - pos[1])
            if d < ASSOC_DIST and (best is None or d < best[0]):
                best = (d, anon)
        return best[1] if best is not None else None

    def _forget(self, now: float) -> None:
        stale = [k for k, tr in self._tracks.items()
                 if tr.alias is None and now - tr.last_seen > ANON_FORGET_S]
        for k in stale:
            del self._tracks[k]
            if k == self._self_key:
                self._self_key = None
        if len(self._tracks) > MAX_TRACKS:
            victims = sorted((tr.last_seen, k) for k, tr in self._tracks.items() if k != self._self_key)
            for _ts, k in victims[: len(self._tracks) - MAX_TRACKS]:
                del self._tracks[k]
