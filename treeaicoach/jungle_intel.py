"""Enemy jungler intel from the public scoreboard (Tab) data of the Live Client Data API.

Everything here is what the player can read on Tab at any time (levels, creep score, items):
nothing about enemy cooldowns or summoner spells. Two legal, cheap signals:

* **Purchase = he is in his fountain.** Items can only be bought in the fountain while alive
  (while dead the respawn anchor of fog_tracker.py already covers it). A new item in an
  enemy's inventory (auto-upgrades and items granted by runes / objectives are ignored, see
  :data:`NOT_BOUGHT`) re-anchors his fog region at his fountain at that moment
  (``fog_tracker.FogTracker.anchor``, reason ``"recall"``) and sets ``recalled_t``.
* **Creep score went up = he is farming** (a jungle camp, the scuttle crab or a lane wave).
  For the enemy jungler hidden in the fog, the camps / lane points he could have reached
  since his last sighting (geodesic distance on the walkable mask at the upper speed bound)
  are the only places he can have been at that moment: the fog region is re-anchored on
  those points (multi-point anchor), and when they are all on one half of the map the side
  is reported ("farm côté haut"). The API's creep score of other players may only move in
  steps (it is reported in tens in some clients): we never use the size of the step.

:class:`JungleIntelTracker.update` runs on every engine tick (work only when a new API
snapshot arrives, ~1 Hz; < 5 ms then). :meth:`JungleIntelTracker.state` returns an immutable
:class:`JungleIntel` for the HUD / coach (``text`` is a short French line or None), and
:meth:`JungleIntel.earliest_eta` the earliest time (s) he can be on a given point from the
last fact. Never raises.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Any

from treeaicoach import geometry

log = logging.getLogger(__name__)

#: Items never bought in the shop: automatic upgrades (Tear / quest / Stopwatch...), rune
#: rewards (biscuits, free boots, stopwatch), objective items, champion-specific starters.
NOT_BOUGHT: frozenset[int] = frozenset({
    3042, 3040, 3121,                       # Muramana, Seraph's Embrace, Fimbulwinter
    3866, 3867, 3869, 3870, 3871, 3876, 3877,   # support quest upgrades
    2010, 2419, 2420, 2421, 2422, 2423, 2424,   # biscuits, stopwatches, magical footwear
    3513, 3599, 3600,                       # Eye of the Herald, Kalista's spear
    1104, 1105, 1106, 1107,                 # (evolved jungle companions, if reported)
})
#: Fog re-anchoring and "recent" windows (s).
RECALL_RECENT_S = 30.0
FARM_RECENT_S = 20.0
#: Ignore Tab changes in the first seconds (game start, everyone buys in the fountain).
START_GRACE_GT = 75.0
#: Lane points used as "farming" candidates (a jungler can take a lane wave), spacing.
_LANE_STEP = 0.05
_EPIC = ("dragon", "baron")
_DIAG_NEUTRAL = 0.06
#: Margin of a fog region's start (detection error + Flash), see fog_tracker.
_FOG_MARGIN = 0.015 + 0.027


def _lane_points() -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for poly in (geometry.TOP_LANE_POLYLINE, geometry.MID_LANE_POLYLINE, geometry.BOT_LANE_POLYLINE):
        for a, b in zip(poly, poly[1:]):
            n = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) / _LANE_STEP))
            for k in range(n + 1):
                pts.append((a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n))
    return pts


def _camps() -> list[tuple[float, float, str, str | None]]:
    """(u, v, name, owner team) of the camps and pits (owner None: river / pits)."""
    try:
        from treeaicoach.render import CAMPS
    except Exception:
        return []
    out = []
    for u, v, name in CAMPS:        # (epic pits too: grubs / dragon give creep score)
        owner = None if name in ("scuttle",) + _EPIC else ("ORDER" if v > u else "CHAOS")
        out.append((float(u), float(v), str(name), owner))
    return out


@dataclass(frozen=True)
class JungleIntel:
    """What the Tab data says about the enemy jungler (engine clock ``t`` for times)."""

    alias: str | None = None
    name: str | None = None
    level: int = 0
    cs: int = 0
    farming: bool = False                 # creep score went up in the last FARM_RECENT_S
    last_farm_t: float | None = None
    farm_side: str | None = None          # "top" | "bot" when all candidate places agree
    farm_owner: str | None = None         # "ORDER" | "CHAOS" jungle when they all agree
    farm_points: tuple[tuple[float, float], ...] = ()
    recalled: bool = False                # bought something in the last RECALL_RECENT_S
    recalled_t: float | None = None
    dead: bool = False
    text: str | None = None               # short French line for the HUD / coach
    speed: float = 0.0                    # normalized units / s used for the ETAs
    fact_t: float | None = None           # time of the last fact (farm / recall)
    fact_points: tuple[tuple[float, float], ...] = ()

    def earliest_eta(self, uv: tuple[float, float] | None, now: float) -> float | None:
        """Earliest time (s from ``now``) he can stand on ``uv`` given the last Tab fact
        (straight line, upper speed bound: a lower bound of the real time). None without a
        recent fact."""
        try:
            if uv is None or self.fact_t is None or not self.fact_points or self.speed <= 0:
                return None
            d = min(math.hypot(uv[0] - a, uv[1] - b) for a, b in self.fact_points)
            return max(0.0, d / self.speed - max(0.0, now - self.fact_t))
        except Exception:
            return None


class JungleIntelTracker:
    """Tab-data watcher of the enemies (purchases) and of the enemy jungler (creep score)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._camps = _camps()
        self._lanes = _lane_points()
        #: Early clear model (jungle_path.py): heat map of the jungler in his fog region.
        self.path: Any = None
        try:
            from treeaicoach.jungle_path import JunglePathModel

            self.path = JunglePathModel()
        except Exception:
            log.exception("Jungle path model unavailable")
        self.reset()

    def reset(self) -> None:
        with getattr(self, "_lock", threading.Lock()):
            self._snap_key: Any = None
            self._snap_t: float | None = None
            self._items: dict[str, dict[int, int]] = {}
            self._cs: dict[str, int] = {}
            self._recall: dict[str, float] = {}
            self._farm: tuple[float, tuple, str | None, str | None] | None = None
            self._state = JungleIntel()
            self._jg_key = ""
            self._game: Any = None
            self._start_anchored = False
            self._last_obs_t = -math.inf
            if getattr(self, "path", None) is not None:
                self.path.reset()

    # ---------------------------------------------------------------- queries
    def state(self) -> JungleIntel:
        with self._lock:
            return self._state

    def recalled_at(self, alias: str) -> float | None:
        """Engine time of the last purchase seen for ``alias`` (any enemy)."""
        with self._lock:
            return self._recall.get(alias)

    # ---------------------------------------------------------------- update
    def update(self, t: float, game: Any, tracker: Any = None, fog: Any = None) -> JungleIntel:
        """New tick (engine time ``t``). Never raises."""
        try:
            with self._lock:
                self._update(float(t), game, tracker, fog)
                return self._state
        except Exception:
            log.exception("JungleIntelTracker.update failed")
            return JungleIntel()

    def _update(self, t: float, game: Any, tracker: Any, fog: Any) -> None:
        if game is None:
            return
        key = (getattr(game, "fetched_at", None), getattr(game, "game_time", None))
        enemies = list(getattr(game, "enemies", None) or [])
        jg = game.enemy_jungler() if hasattr(game, "enemy_jungler") else None
        gt = _f(getattr(game, "game_time", None))
        speed = _speed(gt)
        self._path_tick(t, game, jg, tracker, fog)
        if key != self._snap_key:
            prev_t = self._snap_t
            self._snap_key, self._snap_t = key, t
            since = prev_t if prev_t is not None and t - prev_t <= 3.0 else t - 1.0
            for p in enemies:
                self._tab_changes(p, t, since, gt, fog, jg is p, tracker, speed)
        self._state = self._make_state(t, jg, speed)

    def _tab_changes(self, p: Any, t: float, since: float, gt: float | None, fog: Any,
                     is_jg: bool, tracker: Any, speed: float) -> None:
        alias = str(getattr(p, "champion_alias", "") or "")
        if not alias:
            return
        items: dict[int, int] = {}
        for it in getattr(p, "items", None) or []:
            try:
                i = int(it)
            except (TypeError, ValueError):
                continue
            if i > 0:
                items[i] = items.get(i, 0) + 1
        cs = int(_f(getattr(p, "scores", {}).get("creepScore", 0)) or 0) \
            if isinstance(getattr(p, "scores", None), dict) else 0
        old_items, old_cs = self._items.get(alias), self._cs.get(alias)
        self._items[alias], self._cs[alias] = items, cs
        dead = bool(getattr(p, "is_dead", False))
        if is_jg and dead and self.path is not None:
            self.path.observe_left_route("dead")
        if old_items is None or dead or (gt is not None and gt < START_GRACE_GT):
            return
        bought = [i for i, n in items.items() if n > old_items.get(i, 0) and i not in NOT_BOUGHT]
        if bought:
            if is_jg and self.path is not None:
                self.path.observe_left_route("recall")
            self._recall[alias] = since
            team = geometry.normalize_team(getattr(p, "team", None))
            fountain = geometry.RED_FOUNTAIN if team == "CHAOS" else geometry.BLUE_FOUNTAIN
            if team is not None and fog is not None:
                fog.anchor(alias, fountain, since, "recall")
            if is_jg:
                self._farm = None                  # he is in base, not farming any more
            log.debug("Tab: %s bought %s -> in the fountain", alias, bought)
        elif is_jg and old_cs is not None and cs > old_cs:
            self._on_farm(alias, since, t, tracker, fog, speed)

    def _on_farm(self, alias: str, since: float, t: float, tracker: Any, fog: Any,
                 speed: float) -> None:
        """The jungler's creep score went up between ``since`` and ``t``."""
        pts: list[tuple[float, float, str | None]] = []
        tr = _track(tracker, alias)
        visible = tr is not None and bool(getattr(tr, "visible", False))
        pos = _pos(tr)
        if visible and pos is not None:
            pts = [(pos[0], pos[1], _owner_of(pos))]
        else:
            seeds: list[tuple[float, float]] = []
            t0, margin, spd = None, 0.0, speed
            est = fog.estimate_for(alias) if fog is not None else None
            if est is not None:
                seeds = [tuple(p) for p in (getattr(est, "seeds", None) or ())] or [est.last_uv]
                t0 = est.last_seen
                margin = _FOG_MARGIN
                spd = float(est.speed) if est.speed > 0 else speed   # the fog's own speed
            elif pos is not None and tr is not None:
                seeds, t0, margin = [pos], _f(getattr(tr, "last_seen", None)), 0.03
            cands = [(u, v, o) for u, v, _n, o in self._camps] + \
                [(u, v, None) for u, v in self._lanes]
            if seeds and t0 is not None:
                budget = spd * max(0.0, t - t0) + margin + 0.03
                dist = _geodesic(seeds)
                pts = [(u, v, o) for u, v, o in cands
                       if _lookup(dist, u, v, min(math.hypot(u - a, v - b) for a, b in seeds))
                       <= budget]
            else:
                pts = list(cands)                  # never seen: anywhere he can farm
        if self.path is not None:
            gtn = _gt_now(self._game, t)
            if gtn is not None:
                self.path.observe_farm(gtn - (t - since), gtn)
        if not pts:
            return
        # mid lane / centre points (on the diagonal) belong to no half
        sides = {geometry.side_of(u, v) for u, v, _o in pts if abs(u + v - 1.0) > _DIAG_NEUTRAL}
        owners = {o for _u, _v, o in pts}
        side = sides.pop() if len(sides) == 1 else None
        owner = owners.pop() if len(owners) == 1 else None
        uvs = tuple((round(u, 4), round(v, 4)) for u, v, _o in pts)
        self._farm = (since, uvs, side, owner)
        if fog is not None and not visible:
            fog.anchor(alias, None, since, "farm", points=uvs)
        if fog is not None and not visible and hasattr(fog, "observe_farm"):
            fog.observe_farm(alias)
        log.debug("Tab: jungler %s farming (%d candidate places, side %s)", alias, len(uvs), side)

    # ---------------------------------------------------------------- early clear model
    def _path_tick(self, t: float, game: Any, jg: Any, tracker: Any, fog: Any) -> None:
        """Feeds the early clear model (jungle_path.py): team, sightings; anchors the
        jungler at his fountain at the start of a fresh game; plugs the model into the fog
        tracker (``FogTracker.heat_source``)."""
        self._game = game
        path = self.path
        if path is None or jg is None:
            return
        alias = str(getattr(jg, "champion_alias", "") or "")
        self._jg_key = _norm(alias)
        team = geometry.normalize_team(getattr(jg, "team", None))
        path.set_team(team)
        if fog is not None and hasattr(fog, "heat_source") and fog.heat_source is not self:
            fog.heat_source = self
        gtn = _gt_now(game, t)
        if gtn is None:
            return
        tr = _track(tracker, alias)
        visible = tr is not None and bool(getattr(tr, "visible", False))
        if visible and t - self._last_obs_t >= 0.5:
            pos = _pos(tr)
            if pos is not None:
                self._last_obs_t = t
                path.observe_seen(gtn, pos)
        if not self._start_anchored and fog is not None and team is not None:
            self._start_anchored = True
            if gtn < 80.0 and not visible and tr is None:
                from treeaicoach.jungle_path import LEAVE_FOUNTAIN_GT

                fountain = geometry.RED_FOUNTAIN if team == "CHAOS" else geometry.BLUE_FOUNTAIN
                fog.anchor(alias, fountain, min(t, t - (gtn - LEAVE_FOUNTAIN_GT)), "start")

    def fog_active(self, alias: Any, t: float, game: Any) -> bool:
        """FogTracker hook: keep the jungler's estimate alive while the model is informative."""
        path = self.path
        if path is None or not self._jg_key or _norm(alias) != self._jg_key:
            return False
        return path.active(_gt_now(game, t))

    def fog_heat(self, alias: Any, t: float, game: Any, region: Any) -> Any:
        """FogTracker hook: heat map of the jungler inside ``region`` (or None)."""
        path = self.path
        if path is None or not self._jg_key or _norm(alias) != self._jg_key:
            return None
        return path.heat(_gt_now(game, t), region, grid=int(region.shape[0]))

    def _make_state(self, t: float, jg: Any, speed: float) -> JungleIntel:
        if jg is None:
            return JungleIntel()
        alias = str(getattr(jg, "champion_alias", "") or "") or None
        name = str(getattr(jg, "champion_name", "") or "") or alias
        rec = self._recall.get(alias or "")
        recalled = rec is not None and 0.0 <= t - rec <= RECALL_RECENT_S
        farm = self._farm
        farming = farm is not None and 0.0 <= t - farm[0] <= FARM_RECENT_S
        dead = bool(getattr(jg, "is_dead", False))
        text = None
        fact_t, fact_pts = None, ()
        if dead:
            pass
        elif recalled and (farm is None or rec >= farm[0]):
            text = f"{name} a rappelé (achat il y a {int(t - rec)} s)"
            team = geometry.normalize_team(getattr(jg, "team", None))
            fact_t = rec
            fact_pts = ((geometry.RED_FOUNTAIN if team == "CHAOS" else geometry.BLUE_FOUNTAIN),)
        elif farming:
            where = {"top": "côté haut", "bot": "côté bas"}.get(farm[2] or "", "")
            text = f"{name} farme" + (f" {where}" if where else "") + f" (il y a {int(t - farm[0])} s)"
            fact_t, fact_pts = farm[0], farm[1]
        return JungleIntel(
            alias=alias, name=name, level=int(getattr(jg, "level", 0) or 0),
            cs=self._cs.get(alias or "", 0), farming=farming,
            last_farm_t=farm[0] if farm is not None else None,
            farm_side=farm[2] if farming else None, farm_owner=farm[3] if farming else None,
            farm_points=farm[1] if farming else (), recalled=recalled, recalled_t=rec,
            dead=dead, text=text, speed=speed, fact_t=fact_t, fact_points=tuple(fact_pts))


# ---------------------------------------------------------------------- helpers
def _f(x: Any) -> float | None:
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _speed(gt: float | None) -> float:
    try:
        from treeaicoach.fog_tracker import SPEED_FACTOR_MAX, nominal_speed

        return SPEED_FACTOR_MAX * nominal_speed(gt)
    except Exception:
        return 0.035


def _norm(alias: Any) -> str:
    return "".join(ch for ch in str(alias or "").lower() if ch.isalnum())


def _gt_now(game: Any, t: float) -> float | None:
    """Game time at engine time ``t`` (the poll's game time + the time since the poll)."""
    gt = _f(getattr(game, "game_time", None)) if game is not None else None
    if gt is None:
        return None
    fetched = _f(getattr(game, "fetched_at", None))
    return gt + (min(max(0.0, t - fetched), 3.0) if fetched is not None else 0.0)


def _track(tracker: Any, alias: str) -> Any:
    try:
        return tracker.get(alias) if tracker is not None else None
    except Exception:
        return None


def _pos(tr: Any) -> tuple[float, float] | None:
    try:
        p = tr.position() if tr is not None else None
        return (float(p[0]), float(p[1])) if p is not None else None
    except Exception:
        return None


def _owner_of(p: tuple[float, float]) -> str | None:
    try:
        z = geometry.classify_zone(p[0], p[1])
        return geometry.zone_owner(z) if geometry.is_jungle(z) else None
    except Exception:
        return None


def _geodesic(starts: list[tuple[float, float]]) -> Any:
    try:
        from treeaicoach.fog_tracker import shared_reachability

        return shared_reachability().distance_field_multi(starts)
    except Exception:
        return None


def _lookup(dist: Any, u: float, v: float, fallback: float) -> float:
    """Geodesic distance of the grid cell of ``(u, v)`` (straight line when unknown)."""
    try:
        if dist is None:
            return fallback
        g = dist.shape[0]
        x, y = min(g - 1, max(0, int(u * g))), min(g - 1, max(0, int(v * g)))
        d = float(dist[max(0, y - 1):y + 2, max(0, x - 1):x + 2].min())   # icon on a wall edge
        return d + 1.5 / g if math.isfinite(d) else fallback + 0.05
    except Exception:
        return fallback


__all__ = ["JungleIntel", "JungleIntelTracker", "NOT_BOUGHT"]
