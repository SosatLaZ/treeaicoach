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
    from treeaicoach.tracker import Track, Tracker

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
MODES = ("jungler", "all", "off")


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

    def distance_field(self, start_uv: tuple[float, float]) -> np.ndarray:
        """Normalized geodesic distance from ``start_uv`` (float32 ``[grid, grid]``, inf = unreachable).

        The start is snapped to the nearest walkable cell. Never raises (open-field Euclidean
        distances on failure).
        """
        g = self.grid
        try:
            sx, sy = self.snap(start_uv)
            dist = np.full((g, g), np.inf, np.float32)
            reached = np.zeros((g, g), np.uint8)
            reached[sy, sx] = 1
            dist[sy, sx] = 0.0
            step = 0
            limit = 4 * g  # longest possible walk on the grid; safety bound
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


class _Loss:
    """Internal state of one disappearance (distance field computed once)."""

    __slots__ = ("key", "alias", "name", "last_uv", "last_seen", "speed", "is_jungler",
                 "field", "_region_q", "_region")

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

    def region(self, reach: float, grid: int, walkable_u8: np.ndarray, flash_kernel: np.ndarray
               ) -> np.ndarray:
        """Reachable cells for a geodesic budget ``reach`` (cached per quarter cell)."""
        q = int(reach * grid * 4)
        if self._region is not None and q == self._region_q:
            return self._region
        c = (np.arange(grid, dtype=np.float32) + 0.5) / grid
        # straight-line bound (+ half a cell diagonal for the discretization of the start)
        disc = np.hypot(c[None, :] - self.last_uv[0], c[:, None] - self.last_uv[1]) <= reach + 0.75 / grid
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
        tex = texture_rgba if texture_rgba is not None else load_default_texture()
        if tex is None:
            log.warning("FogTracker: no minimap texture; the whole map is treated as walkable")
            mask = np.ones((WALK_GRID, WALK_GRID), bool)
        else:
            mask = walkable_mask(tex, WALK_GRID)
        self.reach = Reachability(mask)
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
        out.sort(key=lambda e: (not e.is_jungler, e.elapsed))
        return out

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
        loss = self._losses.get(key)
        if loss is not None and abs(loss.last_seen - last_seen) > 1e-6:
            loss = None          # a new disappearance of the same champion
            self._losses.pop(key, None)
        elapsed = max(0.0, t - last_seen)
        player = _player(game, alias)
        dead = bool(player is not None and getattr(player, "is_dead", False))
        expired = elapsed > self.max_s + FOG_GRACE_S
        if dead or expired:
            self._losses.pop(key, None)
            self._dismissed[key] = last_seen
            return None
        if loss is None:
            if abs(self._dismissed.get(key, math.nan) - last_seen) <= 1e-6:
                return None
            pos = _safe_position(tr)
            if pos is None:
                return None
            gt = _finite_or(getattr(game, "game_time", None), None) if game is not None else None
            gt_loss = gt - elapsed if gt is not None else None
            speed = clamp_speed(self._observed_speed(key, tr, last_seen), gt_loss)
            name = (getattr(player, "champion_name", None) or None) if player is not None else None
            loss = _Loss(key, alias, name or alias, pos, last_seen, speed, is_jungler,
                         self.reach.distance_field(pos))
            self._losses[key] = loss
            log.debug("Fog estimate started for %s at %s (speed %.4f/s)", key, pos, speed)
        loss.is_jungler = is_jungler
        reach = loss.speed * elapsed + REACH_MARGIN
        region = loss.region(reach, self.grid, self._walk_u8, self._flash_kernel)
        confidence = max(0.0, 1.0 - elapsed / self.max_s)
        return FogEstimate(
            key=key, alias=loss.alias, name=loss.name, last_uv=loss.last_uv, last_seen=loss.last_seen,
            elapsed=elapsed, speed=loss.speed, radius=reach + FLASH_MARGIN, region=region,
            confidence=confidence, is_jungler=loss.is_jungler,
        )


# ---------------------------------------------------------------------- helpers

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
