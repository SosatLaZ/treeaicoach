"""Minion waves read from the minimap pixels (v2): where the waves meet, who is pushing.

The minimap draws every visible minion as a small filled dot (diameter ~0.018 of the minimap
width, dark contour): **light blue** for my team, **red** for the enemy team (team-relative
colours). :func:`detect_minions` finds those dots with a colour + size blob filter, ignoring
the areas of the champion icons (their rings have the same colours), of the structures and
anything far from a lane. :func:`analyze_waves` projects the dots on the lane centre lines of
:mod:`treeaicoach.geometry` and gives, per lane, the counts, the wave fronts and the meeting
point as a fraction of the lane measured **from my base** (0 = my base, 1 = the enemy base),
hence who is pushing. :class:`WaveTracker` runs this at most once per second and smooths it.

Only pixels already shown to the player are used. numpy + OpenCV, never raises.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np

from treeaicoach import geometry

log = logging.getLogger(__name__)

MINION_DIAMETER = 0.018          # of the minimap width (MINIMAP_FACTS.md)
LANE_MAX_DIST = 0.06             # a dot farther than this from every lane centre line is ignored
ICON_EXCLUDE_R = 0.055           # champion icon areas (ring colours = minion colours)
STRUCTURE_EXCLUDE_R = 0.028
MIN_SIZE_PX = 64                 # minimap crops smaller than this are not analysed
PUSHING_S = 0.58                 # meeting point beyond this fraction of the lane = my wave pushes
PUSHED_IN_S = 0.42               # below this = the wave is on my side
ANALYSIS_PERIOD_S = 1.0
HISTORY = 6                      # analyses kept (smoothing / trend)

# HSV ranges (OpenCV: H 0..180, S/V 0..255)
ALLY_H = (92, 116)
ALLY_S_MIN, ALLY_V_MIN = 80, 140
ENEMY_H_LOW, ENEMY_H_HIGH = 9, 171
ENEMY_S_MIN, ENEMY_V_MIN = 110, 110

LANES = ("top", "mid", "bot")
_POLYS: dict[str, tuple[tuple[float, float], ...]] = {
    "top": geometry.TOP_LANE_POLYLINE,
    "mid": geometry.MID_LANE_POLYLINE,
    # the bot polyline goes from the red base to the blue base: reverse it (all lanes from blue)
    "bot": tuple(reversed(geometry.BOT_LANE_POLYLINE)),
}


def _structures() -> list[tuple[float, float]]:
    try:
        from treeaicoach.coach import TURRETS

        pts = [p for team in TURRETS.values() for p in team]
    except Exception:
        pts = []
    for team_corner in ((0.12, 0.88), (0.88, 0.12)):          # inhibitors / nexus area
        pts.append(team_corner)
    return pts


@dataclass(frozen=True)
class MinionDot:
    """One detected minion dot (or a short chain of touching dots, ``n`` minions)."""

    u: float
    v: float
    team: str            # "ally" | "enemy"
    n: int = 1
    lane: str | None = None
    s: float | None = None   # position along the lane from the BLUE base (0..1)


@dataclass
class LaneWave:
    """Wave state of one lane (``s`` values measured from MY base: 0 my base, 1 enemy base)."""

    lane: str
    ally: int = 0
    enemy: int = 0
    ally_front: float | None = None      # most advanced allied minion
    enemy_front: float | None = None     # most advanced enemy minion (smallest s)
    meet: float | None = None            # where the waves meet / the wave is
    state: str | None = None             # "pushing" | "pushed_in" | "even" | None (no minion)
    trend: float = 0.0                   # d(meet)/dt over the history (> 0 = moving towards them)

    def as_dict(self) -> dict[str, Any]:
        return {"lane": self.lane, "ally": self.ally, "enemy": self.enemy, "ally_front": self.ally_front,
                "enemy_front": self.enemy_front, "meet": self.meet, "state": self.state,
                "trend": round(self.trend, 4)}


# ------------------------------------------------------------------------------ geometry
def _project(u: float, v: float, poly: Sequence[tuple[float, float]]) -> tuple[float, float]:
    """(distance to the polyline, arc-length fraction 0..1 of the closest point)."""
    lengths = [math.hypot(x1 - x0, y1 - y0) for (x0, y0), (x1, y1) in zip(poly, poly[1:])]
    total = sum(lengths) or 1.0
    best_d, best_s, acc = math.inf, 0.0, 0.0
    for ((x0, y0), (x1, y1)), seg in zip(zip(poly, poly[1:]), lengths):
        dx, dy = x1 - x0, y1 - y0
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 <= 1e-12 else min(1.0, max(0.0, ((u - x0) * dx + (v - y0) * dy) / l2))
        d = math.hypot(u - (x0 + t * dx), v - (y0 + t * dy))
        if d < best_d:
            best_d, best_s = d, (acc + t * seg) / total
        acc += seg
    return best_d, best_s


def lane_position(u: float, v: float) -> tuple[str | None, float | None]:
    """Closest lane (within :data:`LANE_MAX_DIST`) and the fraction along it from the blue base."""
    best: tuple[float, str, float] | None = None
    for lane, poly in _POLYS.items():
        d, s = _project(u, v, poly)
        if best is None or d < best[0]:
            best = (d, lane, s)
    if best is None or best[0] > LANE_MAX_DIST:
        return None, None
    return best[1], best[2]


# ------------------------------------------------------------------------------ detection
def _components(mask: np.ndarray, team: str, size: int, exclude: list[tuple[float, float, float]]) -> list[MinionDot]:
    import cv2

    out: list[MinionDot] = []
    n, _labels, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
    r = MINION_DIAMETER * size / 2.0
    expected = max(2.0, math.pi * max(0.8, r - 0.4) ** 2)
    max_side_single = 2.0 * r + 3.0
    for i in range(1, n):
        x, y, w, h, area = (int(stats[i, k]) for k in range(5))
        if area < 0.25 * expected:
            continue
        long_side, short_side = max(w, h), min(w, h)
        if short_side > max_side_single + 1.0:
            continue                                         # too thick: icon, glyph, ping
        if long_side <= max_side_single:
            count = 1 if area <= 2.2 * expected else 2
        elif long_side <= 4.5 * max_side_single and area <= 7.0 * expected:
            count = int(min(5, max(2, round(area / expected))))   # touching dots in a chain
        else:
            continue
        cu, cv_ = float(cents[i][0]) / size, float(cents[i][1]) / size
        if any(math.hypot(cu - eu, cv_ - ev) < er for eu, ev, er in exclude):
            continue
        if geometry.is_base(geometry.classify_zone(cu, cv_)):
            continue                                         # inhibitors / nexus / fountain glyphs
        lane, s = lane_position(cu, cv_)
        if lane is None:
            continue
        out.append(MinionDot(u=cu, v=cv_, team=team, n=count, lane=lane, s=s))
    return out


def detect_minions(minimap_bgr: Any, exclude_uv: Iterable[Any] = ()) -> list[MinionDot]:
    """Minion dots of a minimap crop (BGR). ``exclude_uv`` = champion icon centres. Never raises."""
    try:
        import cv2

        img = minimap_bgr
        if not isinstance(img, np.ndarray) or img.ndim != 3 or img.shape[2] < 3:
            return []
        h, w = img.shape[:2]
        size = min(h, w)
        if size < MIN_SIZE_PX:
            return []
        if img.shape[2] == 4:
            img = img[..., :3]
        img = np.ascontiguousarray(img[:size, :size].astype(np.uint8, copy=False))
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        hh, ss, vv = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        ally = ((hh >= ALLY_H[0]) & (hh <= ALLY_H[1]) & (ss >= ALLY_S_MIN) & (vv >= ALLY_V_MIN))
        enemy = (((hh <= ENEMY_H_LOW) | (hh >= ENEMY_H_HIGH)) & (ss >= ENEMY_S_MIN) & (vv >= ENEMY_V_MIN))
        exclude: list[tuple[float, float, float]] = []
        for p in exclude_uv or ():
            try:
                exclude.append((float(p[0]), float(p[1]), ICON_EXCLUDE_R))
            except (TypeError, ValueError, IndexError):
                continue
        exclude += [(u, v, STRUCTURE_EXCLUDE_R) for u, v in _structures()]
        dots = _components(ally.astype(np.uint8), "ally", size, exclude)
        dots += _components(enemy.astype(np.uint8), "enemy", size, exclude)
        return dots
    except Exception:
        log.debug("detect_minions failed", exc_info=True)
        return []


# ------------------------------------------------------------------------------ analysis
def analyze_waves(dots: Iterable[MinionDot], my_team: str | None) -> dict[str, LaneWave]:
    """Per-lane wave state from the detected dots (``s`` converted to "from my base")."""
    flip = geometry.normalize_team(my_team) == "CHAOS"
    out = {lane: LaneWave(lane=lane) for lane in LANES}
    for d in dots or ():
        if d.lane not in out or d.s is None:
            continue
        s = 1.0 - d.s if flip else d.s
        lw = out[d.lane]
        if d.team == "ally":
            lw.ally += d.n
            lw.ally_front = s if lw.ally_front is None else max(lw.ally_front, s)
        else:
            lw.enemy += d.n
            lw.enemy_front = s if lw.enemy_front is None else min(lw.enemy_front, s)
    for lw in out.values():
        if lw.ally_front is not None and lw.enemy_front is not None:
            lw.meet = (lw.ally_front + lw.enemy_front) / 2.0
        else:
            lw.meet = lw.ally_front if lw.ally_front is not None else lw.enemy_front
        if lw.meet is None:
            lw.state = None
        elif lw.meet >= PUSHING_S:
            lw.state = "pushing"
        elif lw.meet <= PUSHED_IN_S:
            lw.state = "pushed_in"
        else:
            lw.state = "even"
        if lw.meet is not None:
            lw.meet = round(lw.meet, 3)
    return out


@dataclass
class _Snap:
    t: float
    waves: dict[str, LaneWave] = field(default_factory=dict)


class WaveTracker:
    """Runs :func:`detect_minions` + :func:`analyze_waves` at most once per second. Thread-safe."""

    def __init__(self, period_s: float = ANALYSIS_PERIOD_S) -> None:
        self._lock = threading.Lock()
        self._period = float(period_s)
        self._hist: list[_Snap] = []
        self._last_t: float | None = None

    def reset(self) -> None:
        with self._lock:
            self._hist = []
            self._last_t = None

    def update(self, t: float, minimap_bgr: Any, exclude_uv: Iterable[Any], my_team: str | None) -> bool:
        """Analyse the frame if the period elapsed. Returns True when a new analysis was made."""
        try:
            with self._lock:
                if self._last_t is not None and t < self._last_t - 1.0:
                    self._hist = []
                    self._last_t = None
                if minimap_bgr is None or (self._last_t is not None and t - self._last_t < self._period):
                    return False
                self._last_t = t
            waves = analyze_waves(detect_minions(minimap_bgr, list(exclude_uv or ())), my_team)
            with self._lock:
                self._hist.append(_Snap(t=t, waves=waves))
                del self._hist[:-HISTORY]
            return True
        except Exception:
            log.debug("WaveTracker.update failed", exc_info=True)
            return False

    def waves(self, now: float | None = None, max_age_s: float = 5.0) -> dict[str, LaneWave]:
        """Latest per-lane state, with the state confirmed on the last 2 analyses and a trend."""
        with self._lock:
            hist = list(self._hist)
        if not hist:
            return {}
        last = hist[-1]
        if now is not None and now - last.t > max_age_s:
            return {}
        out: dict[str, LaneWave] = {}
        for lane, lw in last.waves.items():
            cur = LaneWave(**{k: getattr(lw, k) for k in ("lane", "ally", "enemy", "ally_front", "enemy_front",
                                                           "meet", "state")})
            if len(hist) >= 2:
                prev = hist[-2].waves.get(lane)
                if prev is None or prev.state != lw.state:
                    cur.state = None if lw.state != "even" else "even"   # not confirmed yet
                pts = [(s.t, s.waves[lane].meet) for s in hist if lane in s.waves and s.waves[lane].meet is not None]
                if len(pts) >= 2 and pts[-1][0] > pts[0][0]:
                    cur.trend = (pts[-1][1] - pts[0][1]) / (pts[-1][0] - pts[0][0])
            else:
                cur.state = None
            out[lane] = cur
        return out


__all__ = ["MinionDot", "LaneWave", "WaveTracker", "detect_minions", "analyze_waves", "lane_position"]
