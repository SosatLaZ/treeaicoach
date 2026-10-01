"""Analysis scheduling helpers for :mod:`treeaicoach.engine` (pure, unit-tested).

* :class:`RateGovernor` - adaptive detection rate: ``calm_fps`` while nothing threatens me,
  ``burst_fps`` for a few seconds after a threat / an enemy close to me / an enemy popping out
  of the fog; slower when the game is not in the foreground; paused when it is minimized.
* :class:`HeavyScheduler` - the coaching stages (tactics macro, map coach, Tab board + hype,
  tips + items) used to run all together on one tick every 0.5 s, producing a tick 2-3x
  longer than the others. When staggering (threaded engine), at most one slot runs per tick,
  spread evenly, so no tick spikes. Synchronous use (tests) keeps the historical behaviour:
  every slot on the same tick.
* :class:`MotionSnapshot` - immutable per-tick copies of the tracks the overlay draws, used by
  the overlay thread to extrapolate positions to the render time (Kalman ``predict``) without
  touching the live tracker.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

#: Burst detection rate is kept this long after the last trigger (s).
BURST_HOLD_S = 3.0
#: An enemy within ``BURST_NEAR`` x the warn radius of me triggers the burst rate.
BURST_NEAR = 1.5
#: An enemy that (re)appeared less than this ago triggers the burst rate (s).
BURST_APPEARED_S = 2.0
#: Detection rate while the game window is not in the foreground (alt-tab, other monitor).
UNFOCUSED_FPS = 2.0
#: Game not in the foreground this long before the unfocused rate applies (s).
UNFOCUSED_AFTER_S = 3.0
#: Window check period while paused (minimized / no window), s.
PAUSED_PERIOD_S = 1.0
#: Render-time extrapolation horizon (s): positions are predicted at most this far past the
#: last observation (beyond, the champion is drawn where last seen).
PREDICT_HORIZON_S = 0.9
#: Velocity damping time constant of the render-time extrapolation (s). Gentler than the
#: tracker's association gate (0.6 s): measured on walking champions at 2-8 detections / s,
#: tau 1.5 s / horizon 0.9 s halves the median display error of tau 0.6 s / 0.6 s at 2.6 fps.
RENDER_TAU_S = 1.5
#: A visible track whose last observation is older than this is drawn as a ghost (no label).
STALE_DRAW_S = 0.45

SLOTS = ("tactics", "coach", "board", "tips")


class RateGovernor:
    """Detection period for the analysis loop."""

    def __init__(self, calm_fps: float = 6.0, burst_fps: float = 12.0) -> None:
        self.calm_fps = float(calm_fps)
        self.burst_fps = float(burst_fps)
        self.burst_until = -math.inf
        self.reason = "calm"

    def configure(self, calm_fps: float, burst_fps: float) -> None:
        self.burst_fps = max(1.0, float(burst_fps))
        self.calm_fps = max(1.0, min(float(calm_fps), self.burst_fps))

    def trigger(self, t: float, why: str = "threat") -> None:
        """Burst rate until ``t + BURST_HOLD_S``."""
        self.burst_until = max(self.burst_until, float(t) + BURST_HOLD_S)
        self.reason = why

    def bursting(self, t: float) -> bool:
        return float(t) < self.burst_until

    def fps(self, t: float, paused: str | None = None, unfocused: bool = False) -> float:
        if paused:
            return 1.0 / PAUSED_PERIOD_S
        if self.bursting(t):
            return self.burst_fps
        if unfocused:
            return min(self.calm_fps, UNFOCUSED_FPS)
        return self.calm_fps

    def period(self, t: float, paused: str | None = None, unfocused: bool = False) -> float:
        return 1.0 / max(0.5, self.fps(t, paused, unfocused))


def burst_reason(t: float, tracks: list[Any], me_uv: tuple[float, float] | None, warn_radius: float,
                 threat: int = 0, fighting: bool = False) -> str | None:
    """Why the detection should run at the burst rate now (None = calm). Pure."""
    if threat >= 1:
        return "threat"
    if fighting:
        return "fight"
    near = BURST_NEAR * max(0.01, float(warn_radius))
    for tr in tracks:
        if getattr(tr, "relation", None) != "enemy" or not getattr(tr, "visible", False):
            continue
        ap = getattr(tr, "appeared_at", None)
        if ap is not None and 0.0 <= t - ap <= BURST_APPEARED_S:
            return "appeared"
        if me_uv is not None:
            try:
                pos = tr.position()
            except Exception:
                pos = None
            if pos is not None and math.hypot(pos[0] - me_uv[0], pos[1] - me_uv[1]) <= near:
                return "near"
    return None


class HeavyScheduler:
    """Which coaching slots run on this tick (see module doc)."""

    def __init__(self, hz: float = 2.0) -> None:
        self.hz = float(hz)
        self.reset()

    def reset(self) -> None:
        self.next: dict[str, float] = {s: -math.inf for s in SLOTS}
        self._shared_next = -math.inf
        self._anchor: float | None = None
        self.runs: dict[str, int] = {s: 0 for s in SLOTS}

    @property
    def period(self) -> float:
        return 1.0 / max(0.1, self.hz)

    def plan(self, t: float, stagger: bool, busy: bool = False) -> set[str]:
        """Slots to run at ``t``. ``busy`` (staggered only): this tick already carries other
        periodic work (minimap verification): no slot, they wait for the next tick."""
        t = float(t)
        p = self.period
        if not stagger:
            # historical behaviour: one shared timer, every slot together
            if t >= self._shared_next or t < self._shared_next - 2.0 * p:
                self._shared_next = t + p
                for s in SLOTS:
                    self.runs[s] += 1
                return set(SLOTS)
            return set()
        if self._anchor is None or any(t < n - 2.0 * p for n in self.next.values() if n > -math.inf):
            # first tick / clock jumped back: slots spread over one period from now
            self._anchor = t
            for i, s in enumerate(SLOTS):
                self.next[s] = t + i * p / len(SLOTS)
        due = [s for s in SLOTS if t >= self.next[s]]
        if not due or busy:
            return set()
        s = min(due, key=lambda x: self.next[x])        # most overdue first, one per tick
        self.next[s] = max(self.next[s] + p, t + 0.5 * p)
        self.runs[s] += 1
        return {s}

    def due_soon(self, t: float) -> bool:
        """A slot will run at ``t`` (used to keep other periodic work off that tick)."""
        return any(float(t) >= n for n in self.next.values())


@dataclass(frozen=True)
class MotionItem:
    """Kalman state copy of one track for render-time prediction."""

    key: str
    track: Any            # tracker.Track snapshot (Track.copy())
    last_obs: float       # engine time of the last observation
    visible: bool
    stacked: bool


class MotionSnapshot:
    """Per-tick snapshot of the drawable tracks. ``predict(now)`` -> ``{key: (uv, age_s)}``."""

    def __init__(self, t: float, items: dict[str, MotionItem], clock_offset: float = 0.0) -> None:
        self.t = float(t)
        self.items = items
        self.clock_offset = float(clock_offset)

    @classmethod
    def from_tracks(cls, t: float, tracks: list[Any]) -> "MotionSnapshot":
        items: dict[str, MotionItem] = {}
        for tr in tracks:
            key = getattr(tr, "key", None)
            if not key:
                continue
            last = getattr(tr, "last_seen", None)
            items[key] = MotionItem(key=key, track=tr, last_obs=float(last) if last is not None else float(t),
                                    visible=bool(getattr(tr, "visible", False)),
                                    stacked=getattr(tr, "stacked_with", None) is not None)
        return cls(t, items)

    def predict(self, now: float, horizon: float = PREDICT_HORIZON_S) -> dict[str, tuple[tuple[float, float], float]]:
        """Position of every visible track extrapolated to engine time ``now`` + age of its data.

        Kalman position + gently damped Kalman velocity (read-only ``Track.kf_position`` /
        ``kf_velocity``), else ``Track.predict`` / the smoothed position. Never raises (an item failing is skipped)."""
        out: dict[str, tuple[tuple[float, float], float]] = {}
        for key, it in self.items.items():
            if not it.visible:
                continue
            try:
                age = max(0.0, float(now) - it.last_obs)
                tr = it.track
                kfp = getattr(tr, "kf_position", None)
                kf = kfp() if callable(kfp) and not it.stacked else None
                if kf is not None:
                    vx, vy = tr.kf_velocity()
                    dt = min(age, max(0.0, float(horizon)))
                    k = RENDER_TAU_S * (1.0 - math.exp(-dt / RENDER_TAU_S))
                    uv = (kf[0] + vx * k, kf[1] + vy * k)
                else:
                    pred = getattr(tr, "predict", None)
                    uv = pred(it.last_obs + min(age, horizon), horizon=horizon) \
                        if callable(pred) and not it.stacked else tr.position()
                if uv is None:
                    continue
                u, v = float(uv[0]), float(uv[1])
                if math.isfinite(u) and math.isfinite(v):
                    out[key] = ((min(1.0, max(0.0, u)), min(1.0, max(0.0, v))), age)
            except Exception:
                continue
        return out
