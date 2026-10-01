"""Possible position of an enemy hidden in the fog of war ("zone où il peut être").

When a tracked enemy (the enemy jungler by default) disappears from the minimap, the only
honest statement we can make is *where he could be by now*: every walkable point he can
reach from his last seen position at his movement speed (plus a Flash). This module
computes that region; it never predicts a single position.

* :func:`walkable_mask` turns the alpha channel of the official minimap texture (alpha > 0
  = walkable ground, alpha 0 = walls / void) into a ``WALK_GRID x WALK_GRID`` boolean grid
  (a cell is walkable when at least half of its texture pixels are).
* :class:`Reachability` computes a geodesic distance field on that grid by *constrained
  iterative dilation*: starting from the last seen cell, the reached set is dilated one
  step at a time (alternating a 3x3 cross and a 3x3 square kernel, which gives an
  octagonal metric: 89-100 % of the Euclidean distance, so it never over-estimates distances)
  and intersected with the walkable mask. Step ``i`` reaches cells at distance
  ``i / grid`` (normalized minimap units). About 1-4 ms on a 128 x 128 grid.
  Since the octagonal ball bulges up to ~12 % past the Euclidean circle, the region is also
  clipped to the straight-line disc (true geodesic >= straight line, so the clipped region
  still contains every reachable point and never exceeds the drawn circle).
* :class:`FogTracker` keeps one :class:`FogEstimate` per hidden enemy: the distance field is
  computed once per disappearance, the region for the current elapsed time is a cheap
  threshold + Flash dilation of that cached field.
* **Re-anchoring from visible facts** (Live Client Data API, i.e. the kill feed / announcer the
  player sees): the region restarts from a new "last known place / time" when
  (a) a dead enemy respawns (respawn timer) -> his fountain; (b) a ``ChampionKill`` names him as
  killer or assister -> where the victim (one of us, always tracked) was at that moment;
  (c) a ``DragonKill`` / ``HeraldKill`` / ``BaronKill`` / ``HordeKill`` names him as killer ->
  the pit. :meth:`FogTracker.anchor` adds such a fact by hand. An anchor older than the
  track's last sighting is ignored; an anchored enemy never seen yet still gets a region.
  Tracks in a stacked hold (``tracker.Track.stacked_with``) are visible: no region for them.

Speeds: nominal movement speed 345 units/s before 2:30 of game time, 390 after (boots),
converted to normalized units per second with ``geometry.MAP_GAME_UNITS``; the speed
retained is the one observed during the last ~1.5 s of visibility, clamped to
``[0.85, 1.35] x nominal``.

Thread safety: :meth:`FogTracker.update` may be called from the analysis thread while other
threads read :meth:`FogTracker.estimates`; the returned :class:`FogEstimate` objects are fresh
snapshots whose ``region`` arrays are read-only. Pure numpy / OpenCV, importable everywhere.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

import cv2
import numpy as np

from treeaicoach import geometry

if TYPE_CHECKING:  # pragma: no cover - typing only
    from treeaicoach.live_client import GameInfo
    from treeaicoach.tracker import Tracker

log = logging.getLogger(__name__)

#: Computation grid (cells per side of the minimap).
WALK_GRID = 128
#: Default texture whose alpha channel gives the walkable ground.
DEFAULT_TEXTURE = "2dlevelminimap_base_baron1.png"
#: A grid cell is walkable when at least this fraction of its texture pixels has alpha > 0.
WALKABLE_FRACTION = 0.5

#: Flash range (~400 game units) in normalized minimap units: the region is dilated by it.
FLASH_MARGIN = 0.027
#: Uncertainty of the last seen position (detection error, smoothing lag, grid cell).
REACH_MARGIN = 0.015
#: Nominal movement speeds (game units / s) and the game time (s) when boots are assumed.
NOMINAL_SPEED_EARLY = 345.0
NOMINAL_SPEED_LATE = 390.0
BOOTS_GAME_TIME_S = 150.0
#: Observed speed is clamped to this range, as a factor of the nominal speed.
SPEED_FACTOR_MIN = 0.85
SPEED_FACTOR_MAX = 1.35
#: Velocity samples older than this (s before the last sighting) are not trusted.
VELOCITY_WINDOW_S = 1.5
#: Default duration after which the region is too large to be useful (confidence 0).
FOG_MAX_S = 60.0
#: Extra time an estimate is kept (faded, confidence 0) after the maximum duration.
FOG_GRACE_S = 10.0
#: Confidence of the jungler's estimate kept past max_s by the early clear model.
HELD_CONFIDENCE = 0.2
MODES = ("jungler", "all", "off")
#: A victim's track must have been seen this close (s) to the kill to anchor the killer there.
ANCHOR_VICTIM_MAX_DT = 3.0
#: An observed respawn this much later than the predicted respawn time is anchored "now".
RESPAWN_MAX_LATE_S = 10.0
#: Epic monster kill events -> pit (normalized centre).
_PIT_EVENTS = {
    "DragonKill": (geometry.DRAGON_PIT[0], geometry.DRAGON_PIT[1]),
    "HeraldKill": (geometry.BARON_PIT[0], geometry.BARON_PIT[1]),
    "BaronKill": (geometry.BARON_PIT[0], geometry.BARON_PIT[1]),
    "HordeKill": (geometry.BARON_PIT[0], geometry.BARON_PIT[1]),
}


def nominal_speed(game_time: float | None) -> float:
    """Nominal movement speed in normalized minimap units per second."""
    units = NOMINAL_SPEED_LATE
    if game_time is not None and math.isfinite(game_time) and game_time < BOOTS_GAME_TIME_S:
        units = NOMINAL_SPEED_EARLY
    return units / geometry.MAP_GAME_UNITS


def clamp_speed(observed: float, game_time: float | None) -> float:
    """Observed speed (normalized / s) clamped to ``[0.85, 1.35] x nominal``."""
    nom = nominal_speed(game_time)
    if not math.isfinite(observed) or observed < 0:
        observed = 0.0
    return float(min(max(observed, SPEED_FACTOR_MIN * nom), SPEED_FACTOR_MAX * nom))


def walkable_mask(texture_rgba: np.ndarray, grid: int = WALK_GRID) -> np.ndarray:
    """Boolean ``[grid, grid]`` mask of the walkable ground (texture alpha > 0).

    Accepts RGBA or BGRA (only the alpha channel is used). A 3-channel or grey image is
    interpreted as "not black = walkable". Invalid input -> everything walkable (logged).
    """
    try:
        grid = int(grid)
        if grid < 8:
            raise ValueError(f"grid too small: {grid}")
        img = np.asarray(texture_rgba)
        if img.ndim == 3 and img.shape[2] == 4:
            walk = img[:, :, 3] > 0
        elif img.ndim == 3 and img.shape[2] >= 3:
            walk = img[:, :, :3].astype(np.int32).sum(axis=2) > 24
        elif img.ndim == 2:
            walk = img > 8
        else:
            raise ValueError(f"unsupported texture shape {img.shape}")
        if walk.shape[0] < 2 or walk.shape[1] < 2:
            raise ValueError(f"texture too small {walk.shape}")
        frac = cv2.resize(walk.astype(np.float32), (grid, grid), interpolation=cv2.INTER_AREA)
        mask = frac >= WALKABLE_FRACTION
        if not mask.any():
            raise ValueError("texture has no walkable pixel")
        return mask
    except Exception as exc:
        log.warning("walkable_mask: %s; treating the whole map as walkable", exc)
        g = grid if isinstance(grid, int) and grid >= 8 else WALK_GRID
        return np.ones((g, g), bool)


def load_default_texture() -> np.ndarray | None:
    """The default minimap texture (BGRA uint8) from the assets, or None."""
    try:
        from treeaicoach import paths

        path: Path = paths.asset_path("minimap", DEFAULT_TEXTURE)
        data = np.fromfile(str(path), dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
        if img is None or img.ndim != 3 or img.shape[2] != 4:
            log.warning("Minimap texture %s missing or without alpha", path)
            return None
        return img
    except Exception as exc:
        log.warning("Cannot load the minimap texture: %s", exc)
        return None


_SHARED_LOCK = threading.Lock()
_SHARED: dict[str, Any] = {}


def shared_reachability() -> "Reachability":
    """One :class:`Reachability` on the default minimap texture, built on first use and
    shared by every analyser (fog tracker, gank travel times). Never raises."""
    with _SHARED_LOCK:
        reach = _SHARED.get("reach")
        if reach is None:
            tex = load_default_texture()
            mask = walkable_mask(tex, WALK_GRID) if tex is not None else np.ones((WALK_GRID, WALK_GRID), bool)
            reach = _SHARED["reach"] = Reachability(mask)
        return reach


class Reachability:
    """Geodesic distances on a walkable grid (constrained iterative dilation)."""

    _CROSS = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    _SQUARE = np.ones((3, 3), np.uint8)

    def __init__(self, walkable: np.ndarray):
        mask = np.asarray(walkable, dtype=bool)
        if mask.ndim != 2 or mask.shape[0] != mask.shape[1] or mask.shape[0] < 2 or not mask.any():
            log.warning("Reachability: invalid walkable mask %s; using an open grid",
                        getattr(mask, "shape", None))
            mask = np.ones((WALK_GRID, WALK_GRID), bool)
        self.walkable = mask.copy()
        self.walkable.setflags(write=False)
        self.grid = int(mask.shape[0])
        self._mask_u8 = mask.astype(np.uint8)
        ys, xs = np.nonzero(mask)
        self._walk_xy = np.stack([xs, ys], axis=1).astype(np.float32)

    def cell_of(self, uv: tuple[float, float]) -> tuple[int, int]:
        """Grid cell ``(col, row)`` containing ``uv`` (clamped to the grid)."""
        u, v = geometry.clamp_uv(uv[0], uv[1])
        g = self.grid
        return min(g - 1, max(0, int(u * g))), min(g - 1, max(0, int(v * g)))

    def snap(self, uv: tuple[float, float]) -> tuple[int, int]:
        """Nearest walkable cell ``(col, row)`` of ``uv``."""
        cx, cy = self.cell_of(uv)
        if self.walkable[cy, cx]:
            return cx, cy
        d2 = (self._walk_xy[:, 0] - cx) ** 2 + (self._walk_xy[:, 1] - cy) ** 2
        i = int(np.argmin(d2))
        return int(self._walk_xy[i, 0]), int(self._walk_xy[i, 1])

    def distance_field_multi(self, starts: Iterable[tuple[float, float]],
                             max_dist: float | None = None) -> np.ndarray:
        """Geodesic distance to the nearest of several start points (same method as
        :meth:`distance_field`). Never raises."""
        pts = [tuple(p) for p in starts]
        if len(pts) <= 1:
            return self.distance_field(pts[0] if pts else (0.5, 0.5), max_dist)
        return self.distance_field(pts[0], max_dist, _extra=pts[1:])

    def distance_field(self, start_uv: tuple[float, float], max_dist: float | None = None,
                       _extra: Iterable[tuple[float, float]] = ()) -> np.ndarray:
        """Normalized geodesic distance from ``start_uv`` (float32 ``[grid, grid]``, inf = unreachable).

        The start is snapped to the nearest walkable cell. With ``max_dist`` the dilation stops
        there (cells farther away are inf): much cheaper for short-range questions such as
        the gank travel times. Never raises (open-field Euclidean distances on failure).
        """
        g = self.grid
        try:
            dist = np.full((g, g), np.inf, np.float32)
            reached = np.zeros((g, g), np.uint8)
            for p in [start_uv, *_extra]:
                sx, sy = self.snap(p)
                reached[sy, sx] = 1
                dist[sy, sx] = 0.0
            step = 0
            limit = 4 * g  # longest possible walk on the grid; safety bound
            if max_dist is not None and math.isfinite(max_dist) and max_dist >= 0:
                limit = min(limit, int(math.ceil(max_dist * g)) + 1)
            while step < limit:
                step += 1
                kernel = self._CROSS if step % 2 else self._SQUARE
                grown = cv2.dilate(reached, kernel)
                np.bitwise_and(grown, self._mask_u8, out=grown)
                new = grown > reached
                if not new.any():
                    break
                dist[new] = step / g
                reached = grown
            return dist
        except Exception:
            log.exception("distance_field failed; using straight-line distances")
            u, v = geometry.clamp_uv(start_uv[0], start_uv[1])
            c = (np.arange(g, dtype=np.float32) + 0.5) / g
            return np.hypot(c[None, :] - u, c[:, None] - v).astype(np.float32)


@dataclass
class FogEstimate:
    """Where a hidden enemy can be (see module docstring). A snapshot: do not mutate."""

    key: str
    alias: str | None
    name: str | None
    last_uv: tuple[float, float]
    last_seen: float                   # t (engine clock) of the last sighting
    elapsed: float                     # seconds since the last sighting
    speed: float                       # retained speed (normalized units / s)
    radius: float                      # straight-line bound (normalized), speed * elapsed + margins
    region: np.ndarray | None          # bool [grid, grid] reachable cells (read-only)
    confidence: float                  # 1 -> 0 when elapsed -> max duration
    is_jungler: bool = False
    #: Start points of the region (one: ``last_uv``; several after a multi-point anchor).
    seeds: tuple = ()
    #: Probability of each cell (float32 [grid, grid], sums to 1 over ``region``) from the
    #: early jungle clear model (jungle_path.py), or None (uniform over the region).
    heat: np.ndarray | None = None


class _Loss:
    """Internal state of one disappearance (distance field computed once)."""

    __slots__ = ("key", "alias", "name", "last_uv", "last_seen", "speed", "is_jungler",
                 "field", "_region_q", "_region", "seeds", "spread", "parent", "_edist")

    def __init__(self, key: str, alias: str | None, name: str | None, last_uv: tuple[float, float],
                 last_seen: float, speed: float, is_jungler: bool, field: np.ndarray) -> None:
        self.key = key
        self.alias = alias
        self.name = name
        self.last_uv = last_uv
        self.last_seen = last_seen
        self.speed = speed
        self.is_jungler = is_jungler
        self.field = field
        self._region_q: int | None = None
        self._region: np.ndarray | None = None
        #: Several possible start points (multi-point anchor, e.g. the camps where a CS
        #: tick could have happened); ``last_uv`` is then their centroid.
        self.seeds: tuple[tuple[float, float], ...] = (last_uv,)
        self.spread = 0.0
        #: The previous disappearance this one refines (multi-point anchor): the region is
        #: the intersection of both (each is a true bound of where he can be).
        self.parent: _Loss | None = None
        self._edist: np.ndarray | None = None      # straight-line distance to the seeds

    def region(self, reach: float, grid: int, walkable_u8: np.ndarray, flash_kernel: np.ndarray
               ) -> np.ndarray:
        """Reachable cells for a geodesic budget ``reach`` (cached per quarter cell)."""
        q = int(reach * grid * 4)
        if self._region is not None and q == self._region_q:
            return self._region
        if self._edist is None or self._edist.shape != (grid, grid):
            c = (np.arange(grid, dtype=np.float32) + 0.5) / grid
            ed = np.full((grid, grid), np.inf, np.float32)
            for su, sv in self.seeds:
                np.minimum(ed, np.hypot(c[None, :] - su, c[:, None] - sv), out=ed)
            self._edist = ed
        # straight-line bound (+ half a cell diagonal for the discretization of the start)
        disc = self._edist <= reach + 0.75 / grid
        core = ((self.field <= reach) & disc).astype(np.uint8)
        grown = cv2.dilate(core, flash_kernel)
        np.bitwise_and(grown, walkable_u8, out=grown)
        region = grown.astype(bool)
        region.setflags(write=False)
        self._region, self._region_q = region, q
        return region


def _norm_alias(alias: Any) -> str:
    return "".join(ch for ch in str(alias or "").lower() if ch.isalnum())


class FogTracker:
    """Keeps one :class:`FogEstimate` per hidden enemy (jungler only by default)."""

    FOG_MAX_S = FOG_MAX_S

    def __init__(self, texture_rgba: np.ndarray | None = None, max_s: float | None = None):
        if texture_rgba is None:
            self.reach = shared_reachability()
        else:
            self.reach = Reachability(walkable_mask(texture_rgba, WALK_GRID))
        self.grid = self.reach.grid
        self._walk_u8 = self.reach.walkable.astype(np.uint8)
        k = int(round(FLASH_MARGIN * self.grid))
        self._flash_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1))
        self.max_s = FOG_MAX_S
        self.set_max_s(max_s if max_s is not None else FOG_MAX_S)
        self._lock = threading.Lock()
        self._losses: dict[str, _Loss] = {}
        self._dismissed: dict[str, float] = {}       # key -> last_seen of a closed disappearance
        self._vel: dict[str, tuple[float, float, float]] = {}   # key -> (t, vx, vy) while visible
        self._estimates: list[FogEstimate] = []
        # re-anchoring (normalized alias -> (t, uv, reason)), death watch, events consumed
        self._anchors: dict[str, tuple[float, tuple[float, float], str]] = {}
        #: Multi-point anchors: normalized alias -> the possible points (centroid in _anchors).
        self._anchor_pts: dict[str, tuple[tuple[float, float], ...]] = {}
        self._dead: dict[str, float | None] = {}
        self._events_seen = 0
        #: Optional heat-map source (``jungle_intel.JungleIntelTracker``): methods
        #: ``fog_active(alias, t, game) -> bool`` (keeps the jungler's estimate alive past
        #: ``max_s`` while the early clear model is informative) and
        #: ``fog_heat(alias, t, game, region) -> ndarray | None``.
        self.heat_source: Any = None

    def _held(self, alias: Any, t: float, game: Any) -> bool:
        src = self.heat_source
        if src is None or not alias:
            return False
        try:
            return bool(src.fog_active(alias, t, game))
        except Exception:
            log.debug("Fog heat source failed", exc_info=True)
            return False

    def _heat(self, alias: Any, t: float, game: Any, region: np.ndarray) -> np.ndarray | None:
        src = self.heat_source
        if src is None or not alias:
            return None
        try:
            h = src.fog_heat(alias, t, game, region)
            return h if isinstance(h, np.ndarray) and h.shape == region.shape else None
        except Exception:
            log.debug("Fog heat source failed", exc_info=True)
            return None

    # ------------------------------------------------------------------ configuration
    def set_max_s(self, max_s: Any) -> None:
        """Duration (s) after which confidence reaches 0 (clamped to 10..300)."""
        try:
            val = float(max_s)
            if not math.isfinite(val):
                raise ValueError
        except (TypeError, ValueError):
            val = FOG_MAX_S
        self.max_s = float(min(max(val, 10.0), 300.0))

    def apply_config(self, cfg: Any) -> None:
        """Read ``cfg.fog_max_s`` (default 60 s)."""
        self.set_max_s(getattr(cfg, "fog_max_s", FOG_MAX_S))

    def reset(self) -> None:
        """Forget every estimate (new game)."""
        with self._lock:
            self._losses.clear()
            self._dismissed.clear()
            self._vel.clear()
            self._estimates = []
            self._anchors.clear()
            self._anchor_pts.clear()
            self._dead.clear()
            self._events_seen = 0

    def anchor(self, alias: str, uv: tuple[float, float] | None, t: float, reason: str = "",
               points: Iterable[tuple[float, float]] | None = None) -> None:
        """New "last known place / time" of the enemy ``alias`` (a visible fact: kill feed,
        objective, respawn, purchase). ``points``: several possible places instead of one
        (he was at ONE of them at ``t``; ``uv`` may then be None). Ignored when older than
        his last sighting. Never raises."""
        try:
            key = _norm_alias(alias)
            tt = float(t)
            if not key or not math.isfinite(tt):
                return
            pts = tuple(geometry.clamp_uv(p[0], p[1]) for p in (points or ()))
            if len(pts) == 1:
                uv, pts = pts[0], ()
            elif pts:
                uv = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
            if uv is None:
                return
            u, v = geometry.clamp_uv(uv[0], uv[1])
            with self._lock:
                cur = self._anchors.get(key)
                if cur is None or tt >= cur[0]:
                    self._anchors[key] = (tt, (u, v), str(reason))
                    if pts:
                        self._anchor_pts[key] = pts
                    else:
                        self._anchor_pts.pop(key, None)
                    log.debug("Fog anchor %s at (%.3f, %.3f) t=%.1f (%s)", alias, u, v, tt, reason)
        except Exception:
            log.debug("FogTracker.anchor failed", exc_info=True)

    def anchors(self) -> dict[str, tuple[float, tuple[float, float], str]]:
        """Current anchors ``normalized alias -> (t, (u, v), reason)`` (snapshot)."""
        with self._lock:
            return dict(self._anchors)

    # ------------------------------------------------------------------ queries
    def estimates(self) -> list[FogEstimate]:
        """Estimates of the last :meth:`update` (snapshot list)."""
        with self._lock:
            return list(self._estimates)

    def estimate_for(self, key_or_alias: str) -> FogEstimate | None:
        """Estimate of a track key or champion alias (case-insensitive), if any."""
        want = _norm_alias(key_or_alias)
        with self._lock:
            for e in self._estimates:
                if e.key == key_or_alias or (want and _norm_alias(e.alias) == want):
                    return e
        return None

    def simulate(self, key: str, alias: str | None, name: str | None, last_uv: tuple[float, float],
                 elapsed: float, speed: float | None = None, is_jungler: bool = False,
                 game_time: float | None = None) -> FogEstimate:
        """Stand-alone estimate (no tracking state change): demos, previews and tests."""
        uv = geometry.clamp_uv(last_uv[0], last_uv[1])
        elapsed = max(0.0, float(elapsed))
        gt_loss = game_time - elapsed if game_time is not None else None
        spd = clamp_speed(speed if speed is not None else 0.0, gt_loss)
        loss = _Loss(key, alias, name or alias, uv, -elapsed, spd, is_jungler, self.reach.distance_field(uv))
        reach = spd * elapsed + REACH_MARGIN
        region = loss.region(reach, self.grid, self._walk_u8, self._flash_kernel)
        return FogEstimate(key=key, alias=alias, name=name or alias, last_uv=uv, last_seen=-elapsed,
                           elapsed=elapsed, speed=spd, radius=reach + FLASH_MARGIN, region=region,
                           confidence=max(0.0, 1.0 - elapsed / self.max_s), is_jungler=is_jungler)

    # ------------------------------------------------------------------ update
    def update(self, t: float, tracker: "Tracker", game: "GameInfo | None",
               mode: str = "jungler") -> list[FogEstimate]:
        """Advance to time ``t``; returns the current estimates. Never raises."""
        try:
            with self._lock:
                self._estimates = self._update_locked(float(t), tracker, game, mode)
                return list(self._estimates)
        except Exception:
            log.exception("FogTracker.update failed")
            with self._lock:
                self._losses.clear()
                self._estimates = []
            return []

    def _update_locked(self, t: float, tracker: Any, game: Any, mode: str) -> list[FogEstimate]:
        mode = str(mode or "jungler").strip().lower()
        if mode not in MODES:
            mode = "jungler"
        if mode == "off" or tracker is None:
            self._losses.clear()
            self._vel.clear()
            return []

        tracks = self._enemy_tracks(tracker)
        jungler_alias = _jungler_alias(game)
        try:
            self._ingest_facts(t, tracker, game)
        except Exception:
            log.debug("Fog anchors from the Live Client data failed", exc_info=True)
        seen_aliases = {_norm_alias(getattr(tr, "alias", None)) for tr in tracks} - {""}
        for akey, (at, auv, _why) in list(self._anchors.items()):
            if akey in seen_aliases or (t - at > self.max_s + FOG_GRACE_S
                                        and not self._held(akey, t, game)):
                continue
            if mode == "jungler" and akey != jungler_alias:
                continue
            player = _player(game, akey)
            tracks.append(_AnchorTrack(getattr(player, "champion_alias", None) or akey, at, auv))
        alive_keys: set[str] = set()
        out: list[FogEstimate] = []
        for tr in tracks:
            key = str(getattr(tr, "key", "") or "")
            if not key:
                continue
            alias = getattr(tr, "alias", None)
            if jungler_alias:
                is_jungler = bool(alias and _norm_alias(alias) == jungler_alias)
            else:   # no roster: a track that knows its champion has Smite
                is_jungler = bool(getattr(tr, "has_smite", False))
            if mode == "jungler" and not is_jungler:
                continue
            if mode == "all" and not alias:
                continue
            alive_keys.add(key)
            if bool(getattr(tr, "visible", False)):
                self._losses.pop(key, None)
                self._dismissed.pop(key, None)
                self._remember_velocity(key, tr, t)
                anc = self._anchors.get(_norm_alias(alias))
                if anc is not None and anc[0] <= t:
                    self._anchors.pop(_norm_alias(alias), None)   # seen since: obsolete
                    self._anchor_pts.pop(_norm_alias(alias), None)
                continue
            est = self._estimate_hidden(t, key, tr, alias, is_jungler, game)
            if est is not None:
                out.append(est)

        for key in list(self._losses):
            if key not in alive_keys:
                del self._losses[key]
        for key in list(self._vel):
            if key not in alive_keys:
                del self._vel[key]
        for key in list(self._dismissed):
            if key not in alive_keys:
                del self._dismissed[key]
        for akey in [k for k, a in self._anchors.items() if t - a[0] > self.max_s + FOG_GRACE_S
                     and not self._held(k, t, game)]:
            del self._anchors[akey]
            self._anchor_pts.pop(akey, None)
        out.sort(key=lambda e: (not e.is_jungler, e.elapsed))
        return out

    # ------------------------------------------------------------------ Live Client facts
    def _ingest_facts(self, t: float, tracker: Any, game: Any) -> None:
        """Respawns, kills and epic monsters from the Live Client data -> anchors."""
        if game is None:
            return
        enemies = list(getattr(game, "enemies", None) or [])
        my_team = geometry.normalize_team(getattr(getattr(game, "me", None), "team", None))
        # (a) respawn at the fountain (the respawn timer tells when)
        for p in enemies:
            key = _norm_alias(getattr(p, "champion_alias", ""))
            if not key:
                continue
            if bool(getattr(p, "is_dead", False)):
                timer = _finite_or(getattr(p, "respawn_timer", None), None)
                self._dead[key] = t + max(0.0, timer) if timer is not None and timer > 0 else self._dead.get(key)
                continue
            if key in self._dead:
                due = self._dead.pop(key)
                when = due if due is not None and due <= t and t - due <= RESPAWN_MAX_LATE_S else t
                team = geometry.normalize_team(getattr(p, "team", None))
                if team is None and my_team is not None:
                    team = "CHAOS" if my_team == "ORDER" else "ORDER"
                if team is not None:
                    fountain = geometry.RED_FOUNTAIN if team == "CHAOS" else geometry.BLUE_FOUNTAIN
                    self._set_anchor(key, fountain, when, "respawn")
        # (b) / (c) new events
        events = getattr(game, "events", None)
        if not isinstance(events, list):
            return
        if len(events) < self._events_seen:
            self._events_seen = 0                          # new game / API restarted
        new = events[self._events_seen:]
        self._events_seen = len(events)
        if not new or not enemies:
            return
        names = _name_lookup(game)
        enemy_keys = {_norm_alias(getattr(p, "champion_alias", "")) for p in enemies} - {""}
        gt_now = _game_time_now(game, t)
        for ev in new:
            if not isinstance(ev, dict):
                continue
            name = ev.get("EventName")
            if name != "ChampionKill" and name not in _PIT_EVENTS:
                continue
            et = _finite_or(ev.get("EventTime"), None)
            t_ev = t - max(0.0, gt_now - et) if (gt_now is not None and et is not None) else t
            killer = names.get(str(ev.get("KillerName") or "").casefold())
            if name in _PIT_EVENTS:
                k = _norm_alias(getattr(killer, "champion_alias", "")) if killer is not None else ""
                if k in enemy_keys:
                    self._set_anchor(k, _PIT_EVENTS[name], t_ev, name)
                continue
            victim = names.get(str(ev.get("VictimName") or "").casefold())
            pos = _victim_position(tracker, game, victim, t_ev)
            if pos is None:
                continue
            actors = [killer] + [names.get(str(a or "").casefold()) for a in (ev.get("Assisters") or [])]
            for a in actors:
                k = _norm_alias(getattr(a, "champion_alias", "")) if a is not None else ""
                if k in enemy_keys:
                    self._set_anchor(k, pos, t_ev, "kill")

    def _set_anchor(self, key: str, uv: tuple[float, float], t: float, reason: str) -> None:
        cur = self._anchors.get(key)
        if cur is None or t >= cur[0]:
            self._anchors[key] = (float(t), geometry.clamp_uv(uv[0], uv[1]), reason)
            self._anchor_pts.pop(key, None)
            log.debug("Fog anchor %s at %s t=%.1f (%s)", key, uv, t, reason)

    @staticmethod
    def _enemy_tracks(tracker: Any) -> list[Any]:
        try:
            tracks = tracker.enemies(visible_only=False)
        except TypeError:
            tracks = [tr for tr in tracker.tracks() if getattr(tr, "relation", None) == "enemy"]
        return [tr for tr in (tracks or []) if getattr(tr, "relation", "enemy") == "enemy"]

    def _remember_velocity(self, key: str, tr: Any, t: float) -> None:
        vel = _safe_velocity(tr)
        if vel is not None:
            self._vel[key] = (t, vel[0], vel[1])

    def _observed_speed(self, key: str, tr: Any, last_seen: float) -> float:
        rec = self._vel.get(key)
        if rec is not None and last_seen - rec[0] <= VELOCITY_WINDOW_S:
            return math.hypot(rec[1], rec[2])
        vel = _safe_velocity(tr)
        return math.hypot(*vel) if vel is not None else 0.0

    def _estimate_hidden(self, t: float, key: str, tr: Any, alias: str | None, is_jungler: bool,
                         game: Any) -> FogEstimate | None:
        last_seen = _finite_or(getattr(tr, "last_seen", None), None)
        if last_seen is None:
            return None
        anc = self._anchors.get(_norm_alias(alias)) if alias else None
        anchor_uv: tuple[float, float] | None = None
        seeds: tuple[tuple[float, float], ...] = ()
        if anc is not None and last_seen + 1e-6 < anc[0] <= t + 1.0:
            last_seen, anchor_uv = anc[0], anc[1]     # a newer visible fact than the last sighting
            seeds = self._anchor_pts.get(_norm_alias(alias), ())
        loss = self._losses.get(key)
        parent: _Loss | None = None
        if loss is not None and abs(loss.last_seen - last_seen) > 1e-6:
            if len(seeds) > 1 and loss.last_seen < last_seen:
                parent = loss    # a multi-point fact refines the current region, not replaces it
            loss = None          # a new disappearance of the same champion
            self._losses.pop(key, None)
        elapsed = max(0.0, t - last_seen)
        player = _player(game, alias)
        dead = bool(player is not None and getattr(player, "is_dead", False))
        held = is_jungler and elapsed > self.max_s and self._held(alias, t, game)
        expired = elapsed > self.max_s + FOG_GRACE_S and not held
        if dead or expired:
            self._losses.pop(key, None)
            self._dismissed[key] = last_seen
            return None
        if loss is None:
            if abs(self._dismissed.get(key, math.nan) - last_seen) <= 1e-6:
                return None
            pos = anchor_uv if anchor_uv is not None else _safe_position(tr)
            if pos is None:
                return None
            gt = _finite_or(getattr(game, "game_time", None), None) if game is not None else None
            gt_loss = gt - elapsed if gt is not None else None
            speed = clamp_speed(self._observed_speed(key, tr, last_seen), gt_loss)
            name = (getattr(player, "champion_name", None) or None) if player is not None else None
            field = self.reach.distance_field_multi(seeds) if len(seeds) > 1 \
                else self.reach.distance_field(pos)
            loss = _Loss(key, alias, name or alias, pos, last_seen, speed, is_jungler, field)
            if len(seeds) > 1:
                loss.seeds = tuple(seeds)
                loss.spread = max(math.hypot(a - pos[0], b - pos[1]) for a, b in seeds)
                if parent is not None and parent.parent is not None and \
                        parent.parent.parent is not None:
                    parent.parent.parent = None        # bounded chain
                loss.parent = parent
            self._losses[key] = loss
            log.debug("Fog estimate started for %s at %s (speed %.4f/s)", key, pos, speed)
        loss.is_jungler = is_jungler
        reach = loss.speed * elapsed + REACH_MARGIN
        region = loss.region(reach, self.grid, self._walk_u8, self._flash_kernel)
        confidence = max(0.0, 1.0 - elapsed / self.max_s)
        if held:
            confidence = max(confidence, HELD_CONFIDENCE)
        shown = loss            # display circle: the latest single-point fact of the chain
        node = loss.parent
        while node is not None and t - node.last_seen <= self.max_s + FOG_GRACE_S:
            p_reach = node.speed * max(0.0, t - node.last_seen) + REACH_MARGIN
            region = region & node.region(p_reach, self.grid, self._walk_u8, self._flash_kernel)
            if shown.spread > 0.0 and node.spread == 0.0:
                shown = node
            node = node.parent
        s_reach = shown.speed * max(0.0, t - shown.last_seen) + REACH_MARGIN
        last_uv, shown_seen, radius = shown.last_uv, shown.last_seen, s_reach + FLASH_MARGIN + shown.spread
        if region is not loss._region:
            region.setflags(write=False)
        return FogEstimate(
            key=key, alias=loss.alias, name=loss.name, last_uv=last_uv, last_seen=shown_seen,
            elapsed=max(0.0, t - shown_seen), speed=loss.speed, radius=radius,
            region=region, confidence=confidence, is_jungler=loss.is_jungler, seeds=loss.seeds,
            heat=self._heat(alias, t, game, region) if loss.is_jungler else None,
        )


# ---------------------------------------------------------------------- helpers

class _AnchorTrack:
    """Stand-in track for an enemy known only from an anchor (never seen on the minimap)."""

    relation = "enemy"
    visible = False
    has_smite = False

    def __init__(self, alias: str, t: float, uv: tuple[float, float]) -> None:
        self.key = alias
        self.alias = alias
        self.last_seen = t
        self._uv = uv

    def position(self) -> tuple[float, float]:
        return self._uv

    def velocity(self) -> tuple[float, float]:
        return (0.0, 0.0)


def _name_lookup(game: Any) -> dict[str, Any]:
    """casefolded Riot ID / game name / summoner name -> player."""
    out: dict[str, Any] = {}
    try:
        players = list(game.all_players())
    except Exception:
        me = getattr(game, "me", None)
        players = ([me] if me is not None else []) + list(getattr(game, "allies", None) or []) \
            + list(getattr(game, "enemies", None) or [])
    for p in players:
        for n in (getattr(p, "riot_id", ""), getattr(p, "summoner_name", "")):
            if isinstance(n, str) and n.strip():
                out.setdefault(n.strip().casefold(), p)
                out.setdefault(n.split("#", 1)[0].strip().casefold(), p)
    return out


def _game_time_now(game: Any, t: float) -> float | None:
    gt = _finite_or(getattr(game, "game_time", None), None)
    if gt is None:
        return None
    fetched = _finite_or(getattr(game, "fetched_at", None), None)
    return gt + (min(max(0.0, t - fetched), 3.0) if fetched is not None else 0.0)


def _victim_position(tracker: Any, game: Any, victim: Any, t_ev: float) -> tuple[float, float] | None:
    """Where the victim (tracked: one of us) was when the kill happened, or None."""
    if victim is None or tracker is None:
        return None
    alias = getattr(victim, "champion_alias", "") or ""
    tr = None
    try:
        if victim is getattr(game, "me", None) and hasattr(tracker, "me"):
            tr = tracker.me()
        if tr is None and alias and hasattr(tracker, "get"):
            tr = tracker.get(alias)
    except Exception:
        tr = None
    if tr is None:
        return None
    seen = _finite_or(getattr(tr, "last_seen", None), None)
    if seen is None or abs(seen - t_ev) > ANCHOR_VICTIM_MAX_DT:
        return None
    return _safe_position(tr)


def _finite_or(x: Any, default: float | None) -> float | None:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _safe_position(tr: Any) -> tuple[float, float] | None:
    try:
        pos = tr.position()
    except Exception:
        return None
    if not pos:
        return None
    u, v = _finite_or(pos[0], None), _finite_or(pos[1], None)
    if u is None or v is None:
        return None
    return geometry.clamp_uv(u, v)


def _safe_velocity(tr: Any) -> tuple[float, float] | None:
    try:
        vel = tr.velocity()
    except Exception:
        return None
    if not vel:
        return None
    vx, vy = _finite_or(vel[0], None), _finite_or(vel[1], None)
    if vx is None or vy is None:
        return None
    return vx, vy


def _jungler_alias(game: Any) -> str:
    """Normalized alias of the enemy jungler (Smite, else position JUNGLE), or ""."""
    if game is None:
        return ""
    try:
        p = game.enemy_jungler()
    except Exception:
        return ""
    return _norm_alias(getattr(p, "champion_alias", "")) if p is not None else ""


def _player(game: Any, alias: str | None) -> Any:
    if game is None or not alias:
        return None
    try:
        return game.player_by_alias(alias)
    except Exception:
        want = _norm_alias(alias)
        for p in getattr(game, "enemies", []) or []:
            if _norm_alias(getattr(p, "champion_alias", "")) == want:
                return p
    return None


def region_outline_points(estimate: FogEstimate) -> list[np.ndarray]:
    """Contours of an estimate's region in normalized coordinates (for UIs / debugging)."""
    if estimate.region is None:
        return []
    mask = estimate.region.astype(np.uint8)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    g = float(mask.shape[0])
    return [(c.reshape(-1, 2).astype(np.float32) + 0.5) / g for c in contours]


def union_region(estimates: Iterable[FogEstimate]) -> np.ndarray | None:
    """Union of the regions (bool grid) of several estimates, or None."""
    out: np.ndarray | None = None
    for e in estimates:
        if e.region is None:
            continue
        out = e.region.copy() if out is None else (out | e.region)
    return out
