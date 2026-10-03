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

Per track: smoothed position (component-wise median of the last 3 observations, projected to
the newest time along the fitted velocity when the motion is consistent: no lag), velocity
(least squares over the last ~1.2 s, ``(0, 0)`` without >= 3 points spanning >= 0.25 s),
visibility (seen < :data:`HIDE_AFTER` s ago, or :data:`HIDE_FRAMES` frames at a low measured
detection rate), appearance time (first sighting, or back in
sight after >= :data:`REAPPEAR_AFTER` s hidden) and a run-length zone history for
:meth:`Track.zone_fraction`. Memory is bounded (deques with ``maxlen``, anonymous tracks
forgotten after 20 s unseen, :data:`MAX_TRACKS` tracks at most).

Robustness additions (v2, ideas and parameters adapted from DeepestLeague, MIT licence, see
THIRD_PARTY_NOTICES.md):

* **Kalman filter** - every track also runs a constant-velocity Kalman filter (per axis,
  normalized minimap units, time-based ``dt``): :meth:`Track.kf_position`,
  :meth:`Track.kf_velocity` and :meth:`Track.predict` (short-miss prediction with a damped
  velocity, used by the association gate). Innovations beyond ~5 sigma are down-weighted.
* **Impossible-jump gating** - a displacement faster than a champion can walk (> 0.15 in
  < 0.3 s, or > ``MAX_WALK_SPEED * gap + JUMP_SLACK``) is accepted at once only when it lands on
  a fountain (recall, respawn); anywhere else the observation is held back until a second
  observation within :data:`TP_CONFIRM_S` confirms it (teleport), otherwise it is dropped as a
  misdetection / wrong identity.
* **Stacked icons** - a track that stops being detected while its last position is within
  ~1.2 icon radii of another visible icon is ``stacked_with`` that track for up to
  :data:`STACK_HOLD_S` s: it stays *visible*, its position / velocity follow the occluding icon,
  so no fog circle, "a disparu" message or fog pop is produced for an icon that is simply
  drawn under another one.
* **Champion locker** - an *anonymous* track (no identity, not "self") is *tentative* until it
  was observed in >= 30 % of the recent frames (at least :data:`LOCK_MIN_OBS` observations)
  with a mean detection score >= 0.40. Tentative tracks are hidden from :meth:`Tracker.tracks`,
  :meth:`Tracker.enemies` and :meth:`Tracker.allies` (still reachable with :meth:`Tracker.get`)
  and forgotten after :data:`TENTATIVE_FORGET_S` s unseen: sporadic false detections (camp
  timers, pings) never become enemies.

Thread safety: :meth:`Tracker.update` runs on the analysis thread; every accessor returns
**snapshot copies** of the tracks, so other threads (overlay, recorder, UI) can read them
while the tracker keeps updating. Pure Python + geometry (numpy LUT), importable everywhere.
"""

from __future__ import annotations

import copy
import dataclasses
import logging
import math
import threading
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from treeaicoach.geometry import Zone, classify_zone, in_fountain, lane_of

if TYPE_CHECKING:  # pragma: no cover - typing only (avoids importing the detector at runtime)
    from treeaicoach.identifier import Identified

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------------------
# Tunables (normalized minimap units, seconds)
# --------------------------------------------------------------------------------------
HIDE_AFTER = 0.6            # a track is visible if seen less than this ago...
HIDE_FRAMES = 3.5           # ...or than this many frames at the measured detection rate
HIDE_MAX_S = 1.6            # (slow detection: 4 img/s -> 0.875 s, 2 img/s -> 1.6 s)
RATE_WINDOW = 9             # frame interval = median of the last intervals (a pause is not a rate)
REAPPEAR_AFTER = 1.5        # hidden at least this long -> a new sighting sets appeared_at
POS_MEDIAN_N = 3            # smoothed position = median of the last N observations...
POS_WINDOW_S = 1.0          # ...not older than this before the newest one
POS_CONSIST = 0.006         # projected along the velocity when they agree this well (no lag)
VEL_WINDOW_S = 1.2          # least-squares velocity window
VEL_MIN_POINTS = 3
VEL_MIN_SPAN_S = 0.25        # (latency: radial velocity available ~3 frames after a sighting at 12 fps)
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
# Kalman filter (constant velocity, per axis; adapted from DeepestLeague ``_KalmanTrack``, MIT)
KF_MEAS_STD = 0.006         # detection noise of an icon centre (normalized)
KF_ACCEL_Q = 0.004          # white-acceleration spectral density (units^2 / s^3)
KF_INIT_VEL_STD = 0.05      # velocity uncertainty of a fresh track (/ s)
KF_GATE_D2 = 25.0           # innovations beyond 5 sigma are down-weighted (soft gate)
KF_COAST_TAU_S = 0.6        # velocity decay time constant while extrapolating a missing track
# impossible jumps
TP_CONFIRM_S = 1.0          # a held-back far observation must be confirmed this fast...
TP_CONFIRM_DIST = 0.04      # ...by an observation this close to it (+ walking)
# stacked icons (DeepestLeague "occlusion-aware hold", MIT)
STACK_RADII = 1.2           # disappeared within this many icon radii of a visible icon
STACK_DEFAULT_R = 0.045     # icon radius when unknown (normalized)
STACK_HOLD_S = 5.0          # longest stacked hold (enemies)
#: my team (me + allies) is never in the fog: an allied icon that disappeared under another
#: visible icon is still under it (support glued on his ADC for minutes, LCU truth of real
#: games: median 0.5 icon diameter apart during the laning phase) -> much longer hold
STACK_HOLD_FRIEND_S = 90.0
STACK_ASSOC_PENALTY = 0.01  # the occluder's own track wins an association tie
# champion locker (DeepestLeague ``ChampionLocker``, MIT) for anonymous tracks
LOCK_MIN_OBS = 3            # observations before an anonymous track is confirmed...
LOCK_WINDOW = 12            # ...in >= LOCK_MIN_DENSITY of the last LOCK_WINDOW frames...
LOCK_MIN_DENSITY = 0.30
LOCK_MIN_CONF = 0.40        # ...with this mean detection score
TENTATIVE_FORGET_S = 2.0    # tentative (unconfirmed) tracks unseen for this long are dropped
# identity by elimination (Live Client roster, see Tracker.set_roster / _eliminate)
ELIM_ENABLED = True
ELIM_MIN_OBS = 3            # the unidentified icon was seen this often recently...
ELIM_MIN_SCORE = 0.45       # ...with this mean detection score
ELIM_ALLY_REACH = 0.12      # an ally is identified by elimination this close to his last place
#: ... and only when that place is known and recent: an ally never seen yet (hidden under
#: his support from the first frame, a custom skin) must not take the identity of an
#: unidentified ally-coloured blob anywhere on the map (det_gym bl_split, real records: an
#: ally drawn on a glyph across the map for minutes)
ELIM_ALLY_MEMORY_S = 20.0

#: Anonymous tracks are checked against the Live Client roster: when every alive champion of
#: their side is accounted for (identified and visible / stacked right now) or cannot have
#: walked to the anonymous icon since he was last seen (MAX_WALK_SPEED, fountain = respawn /
#: recall), the anonymous track is a phantom and is not reported (real records 2.1-2.4:
#: 49 anonymous phantom samples, the 2nd failure mode). False disables.
ANON_SURPLUS = True
#: ... and an anonymous track parked on one spot (all its recent observations within
#: ANON_STATIC_D of the last one over ANON_STATIC_S) is a glyph / text / ping, not a
#: champion nobody could identify (det_gym: unidentified "enemies" parked on turrets for
#: seconds; real records: enemy?N frozen at one spot for 3 s). <= 0 disables.
ANON_STATIC_S = 2.5
ANON_STATIC_D = 0.006

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
# Constant-velocity Kalman filter, one independent 2-state filter per axis.
# State per axis: position p, velocity w; covariance [[a, b], [b, c]].
# Layout of the list: [t, pu, wu, au, bu, cu, pv, wv, av, bv, cv].
# (Model and soft gating adapted from DeepestLeague, MIT licence - THIRD_PARTY_NOTICES.md.)
# --------------------------------------------------------------------------------------

def _kf_init(t: float, u: float, v: float) -> list[float]:
    r2 = KF_MEAS_STD ** 2
    w2 = KF_INIT_VEL_STD ** 2
    return [t, u, 0.0, r2, 0.0, w2, v, 0.0, r2, 0.0, w2]


def _kf_axis_predict(p: float, w: float, a: float, b: float, c: float, dt: float
                     ) -> tuple[float, float, float, float, float]:
    q = KF_ACCEL_Q
    return (p + w * dt, w,
            a + 2.0 * dt * b + dt * dt * c + q * dt ** 3 / 3.0,
            b + dt * c + q * dt * dt / 2.0,
            c + q * dt)


def _kf_step(kf: list[float], t: float, u: float, v: float) -> None:
    """Predict ``kf`` to ``t`` and correct it with the measurement ``(u, v)`` (in place)."""
    dt = max(0.0, t - kf[0])
    pu, wu, au, bu, cu = _kf_axis_predict(kf[1], kf[2], kf[3], kf[4], kf[5], dt)
    pv, wv, av, bv, cv = _kf_axis_predict(kf[6], kf[7], kf[8], kf[9], kf[10], dt)
    r2 = KF_MEAS_STD ** 2
    yu, yv = u - pu, v - pv
    d2 = yu * yu / (au + r2) + yv * yv / (av + r2)
    if d2 > KF_GATE_D2:                    # outlier / sharp turn: trust it less, never ignore it
        r2 *= (d2 / KF_GATE_D2) ** 2
    su, sv = au + r2, av + r2
    ku0, ku1 = au / su, bu / su
    kv0, kv1 = av / sv, bv / sv
    kf[:] = [t,
             pu + ku0 * yu, wu + ku1 * yu, (1.0 - ku0) * au, (1.0 - ku0) * bu, cu - ku1 * bu,
             pv + kv0 * yv, wv + kv1 * yv, (1.0 - kv0) * av, (1.0 - kv0) * bv, cv - kv1 * bv]


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
    confirmed: bool = True                # False: tentative anonymous track (champion locker)
    stacked_with: str | None = None       # key of the icon drawn over this one (stacked hold)
    stacked_since: float | None = None    # last real sighting when the stacked hold started
    stack_released_at: float | None = None  # when the last stacked hold ended (until the next sighting)
    _obs: deque = field(default_factory=lambda: deque(maxlen=OBS_MAXLEN), repr=False)
    _segs: deque = field(default_factory=lambda: deque(maxlen=ZONE_SEG_MAXLEN), repr=False)
    _last_obs_t: float | None = field(default=None, repr=False)
    _kf: list | None = field(default=None, repr=False)               # Kalman state (see _kf_init)
    _pending: tuple | None = field(default=None, repr=False)         # held-back far observation
    _stack_pos: tuple | None = field(default=None, repr=False)       # occluder position (stacked)
    _stack_vel: tuple | None = field(default=None, repr=False)
    _stack_t: float | None = field(default=None, repr=False)
    _lock_obs: deque = field(default_factory=lambda: deque(maxlen=LOCK_WINDOW), repr=False)
    _first_frame: int = field(default=0, repr=False)
    #: time of the last observation that carried the identity (an identified icon): an
    #: identity track continued by unidentified icons coasts IDENTITY_COAST_S from it
    last_id_t: float | None = None
    #: anonymous track that no alive champion of its side can be (all of them accounted for
    #: elsewhere, or too far to have walked there): a phantom (glyph, ping, a second detection
    #: of one icon), not listed by Tracker.enemies() / allies() (see ANON_SURPLUS)
    surplus: bool = False

    # -- derived quantities -----------------------------------------------------------

    def position(self) -> tuple[float, float] | None:
        """Smoothed position: component-wise median of the last 3 recent observations.

        While stacked (and after a stacked hold ended, until the next sighting) this is the
        position of the occluding icon."""
        if self._stack_pos is not None:
            return self._stack_pos
        obs = self._obs
        if not obs:
            return None
        t_last = obs[-1][0]
        recent: list[Obs] = []
        for o in reversed(obs):
            if t_last - o[0] > POS_WINDOW_S or len(recent) >= POS_MEDIAN_N:
                break
            recent.append(o)
        if len(recent) >= POS_MEDIAN_N:
            # walking: the older points are projected to the newest time with the fitted
            # velocity (a plain median trails a walking champion by one frame); only when
            # the motion is consistent (else: outlier / turn -> plain median)
            vx, vy = self.velocity()
            if vx or vy:
                pu = [o[1] + vx * (t_last - o[0]) for o in recent]
                pv = [o[2] + vy * (t_last - o[0]) for o in recent]
                mu, mv = _median3(pu), _median3(pv)
                if max(math.hypot(a - mu, b - mv) for a, b in zip(pu, pv)) <= POS_CONSIST:
                    return (mu, mv)
        return (_median3([o[1] for o in recent]), _median3([o[2] for o in recent]))

    def raw_position(self) -> tuple[float, float] | None:
        """Last observed (unsmoothed) position."""
        return (self._obs[-1][1], self._obs[-1][2]) if self._obs else None

    def velocity(self) -> tuple[float, float]:
        """Least-squares velocity (normalized units / s) over the last ~1.2 s of observations.

        ``(0, 0)`` without at least 3 points spanning 0.25 s (e.g. just after a jump). For a
        hidden track this is the velocity observed just before it disappeared; while stacked,
        the velocity of the occluding icon.
        """
        if self.stacked_with is not None and self._stack_vel is not None:
            return self._stack_vel
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

    def kf_position(self) -> tuple[float, float] | None:
        """Kalman-filtered position at the last observation (stacked: the occluder's)."""
        if self._stack_pos is not None:
            return self._stack_pos
        kf = self._kf
        return (kf[1], kf[6]) if kf is not None else None

    def kf_velocity(self) -> tuple[float, float]:
        """Kalman velocity (/ s), clamped to ``VEL_MAX``; ``(0, 0)`` before 2 observations."""
        if self.stacked_with is not None and self._stack_vel is not None:
            return self._stack_vel
        kf = self._kf
        if kf is None or len(self._obs) < 2:
            return (0.0, 0.0)
        vx, vy = kf[2], kf[7]
        sp = math.hypot(vx, vy)
        if sp > VEL_MAX:
            vx, vy = vx * VEL_MAX / sp, vy * VEL_MAX / sp
        return (vx, vy)

    def kf_speed_std(self) -> float:
        """Standard deviation of the Kalman speed estimate (large = not trusted yet)."""
        kf = self._kf
        if kf is None:
            return KF_INIT_VEL_STD
        return math.sqrt(max(0.0, 0.5 * (kf[5] + kf[10])))

    def predict(self, t: float, horizon: float = PREDICT_MAX_S) -> tuple[float, float] | None:
        """Expected position at ``t``: Kalman position + damped velocity over at most
        ``horizon`` s (short detection misses). Stacked: the occluder's position."""
        if self._stack_pos is not None:
            return self._stack_pos
        kf = self._kf
        if kf is None:
            return self.position()
        dt = min(max(0.0, float(t) - kf[0]), max(0.0, horizon))
        k = KF_COAST_TAU_S * (1.0 - math.exp(-dt / KF_COAST_TAU_S))
        vx, vy = self.kf_velocity()
        return (_clamp01(kf[1] + vx * k), _clamp01(kf[6] + vy * k))

    def recent_scores(self) -> list[float]:
        """Detection scores of the last few observations of an anonymous track (oldest first),
        recorded while the champion locker was deciding (for consumers that confirm threats)."""
        return [s for _f, s in self._lock_obs]

    def last_known(self) -> tuple[float, tuple[float, float]] | None:
        """``(time, position)`` of the last reliable knowledge of where the champion was
        (a real sighting, or the end of a stacked hold)."""
        pos = self.position()
        return (self.last_seen, pos) if pos is not None else None

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

    def _impossible(self, t: float, u: float, v: float) -> bool:
        """The observation is farther than the champion can have walked since last known."""
        ref_t = self._last_obs_t
        if ref_t is None or not self._obs:
            return False
        ref = self._obs[-1][1:]
        if self._stack_pos is not None and self._stack_t is not None and self._stack_t >= ref_t:
            ref_t, ref = self._stack_t, self._stack_pos
        gap = max(0.0, t - ref_t)
        jump = math.hypot(u - ref[0], v - ref[1])
        limit = TELEPORT_DIST if gap < TELEPORT_DT else max(TELEPORT_DIST, MAX_WALK_SPEED * gap + JUMP_SLACK)
        return jump > limit

    def observe(self, t: float, u: float, v: float, r: float = 0.0, score: float = 0.0,
                id_score: float | None = None) -> bool:
        """Add one observation at time ``t`` (non-decreasing). Used by :class:`Tracker`.

        Returns False when the observation was held back / dropped by the impossible-jump gate
        (see the module docstring): the track is then unchanged."""
        reset = False
        if self._impossible(t, u, v):
            pend = self._pending
            if in_fountain(u, v, self.team):
                log.debug("Track %s: recall / respawn to the fountain, history reset", self.key)
            elif pend is not None and 0.0 <= t - pend[0] <= TP_CONFIRM_S and math.hypot(
                    u - pend[1], v - pend[2]) <= TP_CONFIRM_DIST + MAX_WALK_SPEED * (t - pend[0]):
                log.debug("Track %s: teleport confirmed, history reset", self.key)
            else:
                self._pending = (t, u, v)
                return False
            reset = True
        self._pending = None
        prev_t = self._last_obs_t
        if self._stack_t is not None and prev_t is not None and self._stack_t > prev_t:
            # back from a stacked hold: continuous presence, not a fog reappearance
            if t - prev_t >= REAPPEAR_AFTER:
                self._obs.clear()
                self._kf = None
            prev_t = self._stack_t
        self._stack_pos = self._stack_vel = self._stack_t = None
        self.stacked_with = self.stacked_since = self.stack_released_at = None
        if prev_t is None:
            self.appeared_at = t
            self.prev_hidden_s = None
            self.first_seen = min(self.first_seen, t) if self.n_obs else t
        else:
            gap = t - prev_t
            if gap >= REAPPEAR_AFTER:
                self.appeared_at = t
                self.prev_hidden_s = gap
                reset = True               # old points say nothing about the new sighting
        if reset:
            self._obs.clear()
            self._kf = None
        if self._kf is None or not self._obs:
            self._kf = _kf_init(t, u, v)
        else:
            _kf_step(self._kf, t, u, v)
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
        return True

    def follow(self, occluder: Track, now: float) -> None:
        """Stacked hold: take the occluding icon's position / velocity (tracker thread only)."""
        pos = occluder.position()
        if pos is None:
            return
        self._stack_pos = pos
        self._stack_vel = occluder.velocity()
        self._stack_t = now

    def _kf_rebuild(self) -> None:
        """Rebuild the Kalman state from the stored observations (after a merge)."""
        self._kf = None
        for t, u, v in self._obs:
            if self._kf is None:
                self._kf = _kf_init(t, u, v)
            else:
                _kf_step(self._kf, t, u, v)

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

    def refresh(self, now: float, hide_after: float = HIDE_AFTER) -> None:
        """Update ``visible`` / ``hidden_since`` for the current time (stacked = visible)."""
        self.visible = self.stacked_with is not None or (now - self.last_seen) < hide_after
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
            self._kf = list(other._kf) if other._kf is not None else None
            self._pending = None
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
        self._stack_pos = self._stack_vel = self._stack_t = None
        self.stacked_with = self.stacked_since = self.stack_released_at = None
        self._pending = None
        self._kf_rebuild()

    def copy(self) -> Track:
        """Independent snapshot (safe to read from another thread)."""
        # (shallow copy + fresh containers: ~5x cheaper than dataclasses.replace, which
        # re-runs __init__; called for every track by every reader on every tick)
        c = copy.copy(self)
        c._obs = deque(self._obs, maxlen=OBS_MAXLEN)
        c._segs = deque(self._segs, maxlen=ZONE_SEG_MAXLEN)
        c._kf = list(self._kf) if self._kf is not None else None
        c._lock_obs = deque(self._lock_obs, maxlen=LOCK_WINDOW)
        return c


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
        self._frame = 0
        self._dts: deque = deque(maxlen=RATE_WINDOW)
        self._dt_ema: float | None = None
        #: Current hide timeout (s): HIDE_AFTER, longer when the detection rate is low
        #: (HIDE_FRAMES frames at the measured rate, at most HIDE_MAX_S).
        self.hide_after = HIDE_AFTER
        #: Champions dead right now (Live Client): no icon on the map, so their tracks are
        #: hidden at once (no stacked hold, no coasting) and an icon identified as one of
        #: them is a misidentification (kept as an unidentified icon). See :meth:`set_dead`.
        self._dead: frozenset[str] = frozenset()
        #: Live Client roster ``{alias: "self" | "ally" | "enemy"}`` (see :meth:`set_roster`):
        #: identity by elimination of an unidentified icon (ELIM_*).
        self._roster: dict[str, str] = {}

    # -- public API ---------------------------------------------------------------------

    def set_roster(self, roster: dict[str, str] | None) -> None:
        """The match's champions ``{alias: relation}`` (Live Client). Never raises."""
        try:
            ro = {str(a): str(r) for a, r in (roster or {}).items()
                  if a and r in RELATIONS}
        except Exception:
            ro = {}
        with self._lock:
            self._roster = ro

    def set_dead(self, aliases: Iterable[str] | None) -> None:
        """Champions dead right now (Live Client ``isDead`` / respawn timers). Never raises."""
        try:
            dead = frozenset(str(a) for a in (aliases or ()) if a)
        except Exception:
            dead = frozenset()
        with self._lock:
            self._dead = dead

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
            items = sorted((tr for tr in self._tracks.values() if tr.confirmed),
                           key=lambda tr: (order.get(tr.relation, 3), tr.key))
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
                    if tr.relation == "enemy" and tr.confirmed and not tr.surplus
                    and (tr.visible or not visible_only)]

    def allies(self, visible_only: bool = True) -> list[Track]:
        """Snapshots of the allied tracks (without me)."""
        with self._lock:
            return [tr.copy() for tr in sorted(self._tracks.values(), key=lambda x: x.key)
                    if tr.relation == "ally" and tr.confirmed and not tr.surplus
                    and (tr.visible or not visible_only)]

    def get(self, key: str) -> Track | None:
        """Snapshot of the track ``key`` (alias or anonymous key, tentative ones included), or None."""
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
            self._frame = 0
            self._dt_ema = None
            self._dts.clear()
            self.hide_after = HIDE_AFTER

    def forget(self, key: str) -> bool:
        """Drop the track ``key`` now (self-check: one identity seen at two places, a phantom
        enemy): its champion is tracked again from his next observation. True if it existed.
        Never raises."""
        try:
            with self._lock:
                k = str(key)
                if self._tracks.pop(k, None) is None:
                    return False
                if k == self._self_key:
                    self._self_key = None
                for tr in self._tracks.values():
                    if tr.stacked_with == k:          # nothing hides under a forgotten track
                        tr.stacked_with = None
                        tr.stacked_since = None
                return True
        except Exception:
            log.debug("Tracker.forget failed", exc_info=True)
            return False

    @property
    def frame_interval(self) -> float | None:
        """Measured time between two updates (s, median of the last ones), None before two."""
        return self._dt_ema

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
        self._frame += 1
        frame = self._frame
        if self._last_t is not None and 0.0 < now - self._last_t <= 3.0:
            self._dts.append(now - self._last_t)
            self._dt_ema = float(sorted(self._dts)[len(self._dts) // 2])
            self.hide_after = min(HIDE_MAX_S, max(HIDE_AFTER, HIDE_FRAMES * self._dt_ema))
        entries = [e for e in (_entry_from(x) for x in (identified or ())) if e is not None]
        dead = self._dead
        if dead:
            for e in entries:
                if e.alias and e.alias in dead:
                    e.alias = None        # a dead champion has no icon: wrong identity
        # I am dead (Live Client): no icon of mine on the map, no "self" claim (an icon next
        # to where I died would otherwise carry my position around until the respawn)
        my_alias = next((a for a, r in self._roster.items() if r == "self"), None)
        me_dead = my_alias is not None and my_alias in dead
        if me_dead:
            for e in entries:
                if e.relation == "self":
                    e.relation = "ally"
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
                                          first_seen=now, last_seen=now,
                                          confirmed=e.relation == "self", _first_frame=frame)
            tr = self._tracks[key]
            if tr.alias is None:
                tr.relation = self._merged_relation(tr.relation, e.relation)
            if e.team and tr.team is None:
                tr.team = e.team
            if not tr.observe(now, e.u, e.v, e.r, e.score):
                continue
            self._lock_check(tr, frame, e.score)
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
            tr.confirmed = True                    # an identity (roster) is its own confirmation
            if not tr.observe(now, e.u, e.v, e.r, e.score, e.id_score):
                continue
            tr.last_id_t = now
            updated.add(key)
            if e.relation == "self":
                self_key = key

        # 2c. identity by elimination: the only alive champion of a team not accounted for
        if self._roster and ELIM_ENABLED:
            self._eliminate(now, updated, frame)

        # 2b. icons drawn under another icon: stacked hold (not a disappearance)
        self._update_stacks(now, updated)

        # 3. at most one "self" track: the one declared self in this frame wins
        if self_key is not None:
            self._self_key = self_key
        for key, tr in self._tracks.items():
            if tr.relation == "self" and key != self._self_key:
                tr.relation = "ally"
            tr.refresh(now, self.hide_after)
            if (dead and tr.alias in dead) or (me_dead and key == self._self_key):
                # dead: hidden at once (a stacked hold or the hide timeout would keep
                # drawing him where he died)
                tr.stacked_with = tr.stacked_since = None
                tr._stack_pos = tr._stack_vel = tr._stack_t = None
                tr.visible = False
                tr.hidden_since = tr.last_seen

        if self._roster and ANON_SURPLUS:
            self._mark_surplus(now)
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
        return tr.predict(now)

    @staticmethod
    def _lock_check(tr: Track, frame: int, score: float) -> None:
        """Champion locker: confirm a tentative anonymous track once it was seen often enough
        (density over the recent frames) with a good enough mean detection score."""
        tr._lock_obs.append((frame, score))
        if tr.confirmed:
            return
        recent = [s for f, s in tr._lock_obs if frame - f < LOCK_WINDOW]
        span = min(LOCK_WINDOW, frame - tr._first_frame + 1)
        if len(recent) < LOCK_MIN_OBS or span <= 0:
            return
        if len(recent) / span >= LOCK_MIN_DENSITY and sum(recent) / len(recent) >= LOCK_MIN_CONF:
            tr.confirmed = True
            log.debug("Tracker: %s confirmed (%d obs / %d frames)", tr.key, len(recent), span)

    def _update_stacks(self, now: float, updated: set[str]) -> None:
        """Start / follow / end the stacked holds of the tracks not observed in this frame."""
        occluders = [(k, self._tracks[k]) for k in updated
                     if k in self._tracks and self._tracks[k].confirmed]
        for key, tr in self._tracks.items():
            if key in updated or not tr.confirmed or tr._last_obs_t is None:
                continue
            if tr.stacked_with is not None:
                occ = self._tracks.get(tr.stacked_with)
                occ_visible = occ is not None and occ.stacked_with is None \
                    and now - occ.last_seen < self.hide_after
                hold = STACK_HOLD_S if tr.relation == "enemy" else STACK_HOLD_FRIEND_S
                if occ_visible and now - (tr.stacked_since or now) <= hold:
                    if tr.stacked_with in updated:
                        tr.follow(occ, now)        # type: ignore[arg-type]
                    continue
                if occ is not None and not occ_visible and tr._stack_t is not None:
                    # the occluder went into the fog: both were there together until then
                    tr.last_seen = max(tr.last_seen, min(occ.last_seen, tr._stack_t))
                tr.stacked_with = None
                tr.stacked_since = None
                tr.stack_released_at = now
                continue
            if now - tr.last_seen >= self.hide_after or tr._stack_pos is not None:
                continue                           # not a fresh disappearance
            last = tr.raw_position()
            pred = tr.predict(now)
            if last is None:
                continue
            best: tuple[float, str, Track] | None = None
            for k, occ in occluders:
                if k == key:
                    continue
                op = occ.raw_position()
                if op is None:
                    continue
                reach = STACK_RADII * max(tr.radius, occ.radius, STACK_DEFAULT_R * 0.5) \
                    if (tr.radius or occ.radius) else STACK_RADII * STACK_DEFAULT_R
                d = math.hypot(op[0] - last[0], op[1] - last[1])
                if pred is not None:
                    d = min(d, math.hypot(op[0] - pred[0], op[1] - pred[1]))
                if d <= reach and (best is None or d < best[0]):
                    best = (d, k, occ)
            if best is not None:
                tr.stacked_with = best[1]
                tr.stacked_since = tr.last_seen
                tr.follow(best[2], now)
                log.debug("Tracker: %s stacked under %s", key, best[1])

    def _associate(self, anonymous: list[_Entry], now: float,
                   named_keys: set[str]) -> list[tuple[_Entry, str | None]]:
        """Greedy nearest-neighbour matching of unidentified icons to existing tracks."""
        if not anonymous:
            return []
        candidates: list[tuple[str, str, tuple[float, float]]] = []
        for key, tr in self._tracks.items():
            if key in named_keys:
                continue
            if tr.alias is not None and now - (tr.last_id_t if tr.last_id_t is not None
                                               else tr.last_seen) > max(IDENTITY_COAST_S,
                                                                        self.hide_after + 0.4):
                continue      # (from the last IDENTIFIED sighting: an unidentified icon
                #             parked on a glyph must not carry the identity for ever)
            pred = self._predicted(tr, now)
            if pred is not None:
                candidates.append((key, side_of_relation(tr.relation), pred,
                                   STACK_ASSOC_PENALTY if tr.stacked_with is not None else 0.0))
        pairs: list[tuple[float, int, str]] = []
        for i, e in enumerate(anonymous):
            for key, side, (pu, pv), penalty in candidates:
                if side != e.side:
                    continue
                d = math.hypot(e.u - pu, e.v - pv)
                if d < ASSOC_DIST:
                    pairs.append((d + penalty, i, key))
        pairs.sort(key=lambda p: (p[0], p[1], p[2]))
        taken_e: dict[int, str] = {}
        taken_k: set[str] = set()
        for _d, i, key in pairs:
            if i in taken_e or key in taken_k:
                continue
            taken_e[i] = key
            taken_k.add(key)
        return [(e, taken_e.get(i)) for i, e in enumerate(anonymous)]

    def _eliminate(self, now: float, updated: set[str], frame: int) -> None:
        """An unidentified, confirmed icon of one team while exactly one alive champion of that
        team is not accounted for (identified this frame, visible a moment ago or stacked) is
        that champion, if he can have walked there since he was last seen (LESSONS rule 13:
        identity only among that team's alive champions, one-to-one)."""
        for side in ("enemy", "ally"):
            anon = [k for k in updated if k in self._tracks and self._tracks[k].alias is None
                    and self._tracks[k].confirmed
                    and side_of_relation(self._tracks[k].relation) == side]
            if len(anon) != 1:
                continue
            a = self._tracks[anon[0]]
            recent = [s_ for f_, s_ in a._lock_obs if frame - f_ < LOCK_WINDOW]
            if len(recent) < ELIM_MIN_OBS or sum(recent) / len(recent) < ELIM_MIN_SCORE:
                continue
            cands = []
            for alias, rel in self._roster.items():
                if side_of_relation(rel) != side or alias in self._dead:
                    continue
                tr = self._tracks.get(alias)
                if tr is not None and (alias in updated or tr.stacked_with is not None
                                       or now - tr.last_seen < self.hide_after):
                    continue                     # accounted for (seen / stacked right now)
                cands.append(alias)
            if len(cands) != 1:
                continue
            alias = cands[0]
            tr = self._tracks.get(alias)
            pos = a.raw_position()
            if side == "ally" and ELIM_ALLY_MEMORY_S > 0 and (
                    tr is None or tr.raw_position() is None or now - tr.last_seen > ELIM_ALLY_MEMORY_S):
                continue
            if tr is not None and pos is not None and tr.raw_position() is not None:
                last = tr.raw_position()
                reach = MAX_WALK_SPEED * max(0.0, now - tr.last_seen) + JUMP_SLACK
                if side == "ally":
                    # an ally is never in the fog: unaccounted for, he is under an icon near
                    # where he was last seen (or back home)
                    reach = min(reach, ELIM_ALLY_REACH)
                if math.hypot(pos[0] - last[0], pos[1] - last[1]) > reach and \
                        (side == "ally" or not in_fountain(pos[0], pos[1], tr.team)):
                    continue
            rel = self._roster[alias]
            if tr is None:
                tr = Track(key=alias, alias=alias, relation=rel, team=a.team,
                           first_seen=a.first_seen, last_seen=a.last_seen)
                self._tracks[alias] = tr
            elif side_of_relation(tr.relation) != side:
                continue                         # (cross-team mirror key)
            tr.absorb(a)
            self._tracks.pop(a.key, None)
            if self._self_key == a.key:
                self._self_key = alias
            tr.relation = self._merged_relation(tr.relation, rel)
            tr.confirmed = True
            tr.last_id_t = now
            updated.discard(a.key)
            updated.add(alias)
            log.debug("Tracker: %s identified as %s by elimination", a.key, alias)

    def _mark_surplus(self, now: float) -> None:
        """Flag the anonymous tracks no alive champion of their side can be (ANON_SURPLUS)."""
        for side in ("enemy", "ally"):
            anon = [tr for k, tr in self._tracks.items() if tr.alias is None
                    and k != self._self_key and side_of_relation(tr.relation) == side]
            if not anon:
                continue
            free = []                    # alive champions of the side not accounted for
            for alias, rel in self._roster.items():
                if side_of_relation(rel) != side or alias in self._dead:
                    continue
                tr = self._tracks.get(alias)
                if rel == "self" and tr is None and self._self_key is not None:
                    continue                     # (my track under an anonymous key)
                if tr is not None and (tr.stacked_with is not None or (
                        tr.visible and now - tr.last_seen < self.hide_after)):
                    continue
                free.append(tr)
            for a in anon:
                pos = a.raw_position()
                if pos is None:
                    a.surplus = False
                    continue
                if ANON_STATIC_S > 0 and len(a._obs) >= 3 and \
                        a._obs[-1][0] - a._obs[0][0] >= ANON_STATIC_S and \
                        all(math.hypot(u - pos[0], v - pos[1]) <= ANON_STATIC_D
                            for _t, u, v in a._obs):
                    a.surplus = True
                    continue
                ok = False
                for tr in free:
                    last = tr.raw_position() if tr is not None else None
                    if last is None or in_fountain(pos[0], pos[1], tr.team):
                        ok = True
                        break
                    since = max(0.0, now - tr.last_seen)
                    if math.hypot(pos[0] - last[0], pos[1] - last[1]) <= \
                            MAX_WALK_SPEED * since + JUMP_SLACK + ASSOC_DIST:
                        ok = True
                        break
                a.surplus = not ok

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
                 if tr.alias is None and (now - tr.last_seen > ANON_FORGET_S
                                          or (not tr.confirmed and now - tr.last_seen > TENTATIVE_FORGET_S))]
        for k in stale:
            del self._tracks[k]
            if k == self._self_key:
                self._self_key = None
        if len(self._tracks) > MAX_TRACKS:
            victims = sorted((tr.last_seen, k) for k, tr in self._tracks.items() if k != self._self_key)
            for _ts, k in victims[: len(self._tracks) - MAX_TRACKS]:
                del self._tracks[k]


# validated tuning overrides (assets/model/det_params.json, written by tools/det_tune.py)
from treeaicoach import det_params as _det_params  # noqa: E402

_det_params.apply("tracker", globals())
