"""Summoner's Rift minimap geometry: zones, distances and French zone labels.

Coordinates are *normalized minimap coordinates* ``(u, v)`` in ``[0, 1]``: ``u`` grows
left -> right and ``v`` top -> bottom. The blue (``ORDER``) base is in the bottom-left
corner, the red (``CHAOS``) base in the top-right corner, the river follows ``u = v`` and
the mid lane ``u + v = 1``.

Every shape below was **measured on the official texture**
``assets/minimap/2dlevelminimap_base_baron1.png`` (512 x 512: khaki lanes, blue river,
grey-blue bases) and cross-checked with the official turret / camp coordinates
(``u = x / 14870``, ``v = 1 - y / 14980``). All of them are plain module-level constants so
they can be re-tuned (e.g. from ``docs/MINIMAP_FACTS.md``); call :func:`reset_zone_cache`
after changing one at runtime.

:func:`classify_zone` answers from a lazily built lookup table (O(1), thread-safe), so it
can be called for every tracked sample without CPU concern.
"""

from __future__ import annotations

import logging
import math
import threading
from enum import Enum
from typing import Any, Sequence

import numpy as np

log = logging.getLogger(__name__)

Point = tuple[float, float]


class Zone(str, Enum):
    """Coarse map zones used for speech and gank logic."""

    BLUE_BASE = "blue_base"
    RED_BASE = "red_base"
    TOP_LANE = "top_lane"
    MID_LANE = "mid_lane"
    BOT_LANE = "bot_lane"
    TOP_RIVER = "top_river"
    BOT_RIVER = "bot_river"
    BLUE_JUNGLE_TOP = "blue_jungle_top"
    BLUE_JUNGLE_BOT = "blue_jungle_bot"
    RED_JUNGLE_TOP = "red_jungle_top"
    RED_JUNGLE_BOT = "red_jungle_bot"


#: Fixed order of the zones (index = code stored in the lookup table).
ZONES: tuple[Zone, ...] = tuple(Zone)
_ZONE_INDEX: dict[Zone, int] = {z: i for i, z in enumerate(ZONES)}

# --------------------------------------------------------------------------------------
# Map scale
# --------------------------------------------------------------------------------------
MAP_GAME_UNITS: float = 14870.0     # map width in game units (u = x / MAP_GAME_UNITS)
MAP_GAME_HEIGHT: float = 14980.0    # map height in game units (v = 1 - y / MAP_GAME_HEIGHT)

# --------------------------------------------------------------------------------------
# Measured shapes (normalized coordinates). Texture measurements (512 px):
#   * left / top lane bands: khaki from px 25 to 59 -> centre 0.082, half-width 0.034;
#     right / bottom bands: px 452..487 -> centre 0.918 (the map is point-symmetric);
#   * mid lane: khaki band centred on u + v = 1, ~0.075 wide across;
#   * river: centre line below, ~0.06-0.08 wide across, baron / dragon pits attached;
#   * bases: grey-blue quarter discs around the corners, radius 0.37-0.41.
# --------------------------------------------------------------------------------------
SIDE_LANE_OFFSET: float = 0.082     # distance between the map edge and a side-lane centre line

_O = SIDE_LANE_OFFSET
#: Top lane: up the left edge, around the top-left bend, then along the top edge.
TOP_LANE_POLYLINE: tuple[Point, ...] = (
    (_O, 1.0 - _O), (_O, 0.22), (0.10, 0.14), (0.14, 0.10), (0.22, _O), (1.0 - _O, _O),
)
#: Mid lane: the diagonal between the two nexus.
MID_LANE_POLYLINE: tuple[Point, ...] = ((_O, 1.0 - _O), (1.0 - _O, _O))


def _rot180(poly: Sequence[Point]) -> tuple[Point, ...]:
    """Point-symmetric image of a polyline (the map is symmetric around its centre)."""
    return tuple((round(1.0 - u, 6), round(1.0 - v, 6)) for u, v in poly)


#: Bot lane: along the bottom edge, around the bottom-right bend, then up the right edge.
BOT_LANE_POLYLINE: tuple[Point, ...] = _rot180(TOP_LANE_POLYLINE)

#: Zone tolerance around the lane centre lines (painted band + icon / detection slack).
LANE_HALF_WIDTH: dict[str, float] = {"top": 0.055, "mid": 0.05, "bot": 0.055}

#: Top river centre line, from the top-lane river mouth to the map centre.
TOP_RIVER_POLYLINE: tuple[Point, ...] = (
    (0.15, 0.18), (0.18, 0.20), (0.21, 0.225), (0.23, 0.27), (0.25, 0.305), (0.29, 0.325),
    (0.325, 0.36), (0.355, 0.387), (0.40, 0.42), (0.45, 0.455), (0.50, 0.50),
)
#: Bot river centre line (symmetric of the top one), from the map centre to the bot-lane mouth.
BOT_RIVER_POLYLINE: tuple[Point, ...] = tuple(reversed(_rot180(TOP_RIVER_POLYLINE)))
RIVER_HALF_WIDTH: float = 0.05
#: Baron / dragon pits (centre u, centre v, radius): part of the top / bot river.
BARON_PIT: tuple[float, float, float] = (0.335, 0.297, 0.052)
DRAGON_PIT: tuple[float, float, float] = (0.665, 0.703, 0.052)

#: Bases: disc of radius BASE_RADIUS around the map corner.
BLUE_BASE_CORNER: Point = (0.0, 1.0)
RED_BASE_CORNER: Point = (1.0, 0.0)
BASE_RADIUS: float = 0.39
#: Fountain / spawn platform (centre, radius) - deep inside the base.
BLUE_FOUNTAIN: Point = (0.04, 0.96)
RED_FOUNTAIN: Point = (0.96, 0.04)
FOUNTAIN_RADIUS: float = 0.11

#: Resolution of the zone lookup table (cells per side).
ZONE_LUT_SIZE: int = 512

LANES: tuple[str, ...] = ("top", "mid", "bot")
_LANE_ZONE: dict[str, Zone] = {"top": Zone.TOP_LANE, "mid": Zone.MID_LANE, "bot": Zone.BOT_LANE}
_ZONE_LANE: dict[Zone, str] = {v: k for k, v in _LANE_ZONE.items()}

# --------------------------------------------------------------------------------------
# Input sanitation
# --------------------------------------------------------------------------------------


def _coord(x: Any) -> float:
    """Float in [0, 1]; NaN / invalid values become 0.5 (map centre), inf is clamped."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        log.debug("Invalid minimap coordinate %r, using 0.5", x)
        return 0.5
    if math.isnan(f):
        return 0.5
    return 0.0 if f < 0.0 else 1.0 if f > 1.0 else f


def clamp_uv(u: Any, v: Any) -> Point:
    """Clamp a position to the minimap square (invalid / NaN coordinates -> 0.5)."""
    return _coord(u), _coord(v)


# --------------------------------------------------------------------------------------
# Vectorized classification (used to build the lookup table and by classify_zone_exact)
# --------------------------------------------------------------------------------------


def _polyline_dist(u: np.ndarray, v: np.ndarray, poly: Sequence[Point]) -> np.ndarray:
    """Euclidean distance from each point to a polyline."""
    best = np.full(np.shape(u), np.inf, dtype=np.float64)
    if len(poly) == 1:
        return np.hypot(u - poly[0][0], v - poly[0][1])
    for (x0, y0), (x1, y1) in zip(poly, poly[1:]):
        dx, dy = x1 - x0, y1 - y0
        l2 = dx * dx + dy * dy
        if l2 <= 1e-18:
            t = np.zeros(np.shape(u))
        else:
            t = np.clip(((u - x0) * dx + (v - y0) * dy) / l2, 0.0, 1.0)
        np.minimum(best, np.hypot(u - (x0 + t * dx), v - (y0 + t * dy)), out=best)
    return best


def _inside_polygon(u: np.ndarray, v: np.ndarray, poly: Sequence[Point]) -> np.ndarray:
    """Even-odd point-in-polygon test (vectorized ray casting)."""
    inside = np.zeros(np.shape(u), dtype=bool)
    n = len(poly)
    if n < 3:
        return inside
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if y0 == y1:
            continue
        crosses = (y0 > v) != (y1 > v)
        x_int = x0 + (v - y0) * (x1 - x0) / (y1 - y0)
        inside ^= crosses & (u < x_int)
    return inside


def _classify_arrays(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Zone codes (indices into :data:`ZONES`) for arrays of clamped coordinates."""
    u = np.clip(np.nan_to_num(np.asarray(u, dtype=np.float64), nan=0.5), 0.0, 1.0)
    v = np.clip(np.nan_to_num(np.asarray(v, dtype=np.float64), nan=0.5), 0.0, 1.0)
    idx = _ZONE_INDEX

    # 4. jungle quadrants (default): blue side v >= u, top half u + v < 1
    blue_side = v >= u
    top_half = (u + v) < 1.0
    out = np.where(
        blue_side,
        np.where(top_half, idx[Zone.BLUE_JUNGLE_TOP], idx[Zone.BLUE_JUNGLE_BOT]),
        np.where(top_half, idx[Zone.RED_JUNGLE_TOP], idx[Zone.RED_JUNGLE_BOT]),
    ).astype(np.int16)

    # 3. river (centre-line band + pits)
    d_rt = _polyline_dist(u, v, TOP_RIVER_POLYLINE)
    d_rb = _polyline_dist(u, v, BOT_RIVER_POLYLINE)
    bx, by, br = BARON_PIT
    dx, dy, dr = DRAGON_PIT
    in_top_river = (d_rt < RIVER_HALF_WIDTH) | (np.hypot(u - bx, v - by) < br)
    in_bot_river = (d_rb < RIVER_HALF_WIDTH) | (np.hypot(u - dx, v - dy) < dr)
    both = in_top_river & in_bot_river
    out[in_top_river & ~(both & (d_rb < d_rt))] = idx[Zone.TOP_RIVER]
    out[in_bot_river & ~(both & (d_rb >= d_rt))] = idx[Zone.BOT_RIVER]

    # 2. lanes (the closest lane within its tolerance wins)
    d_top = _polyline_dist(u, v, TOP_LANE_POLYLINE)
    d_mid = _polyline_dist(u, v, MID_LANE_POLYLINE)
    d_bot = _polyline_dist(u, v, BOT_LANE_POLYLINE)
    inf = np.inf
    e_top = np.where(d_top < LANE_HALF_WIDTH.get("top", 0.055), d_top, inf)
    e_mid = np.where(d_mid < LANE_HALF_WIDTH.get("mid", 0.05), d_mid, inf)
    e_bot = np.where(d_bot < LANE_HALF_WIDTH.get("bot", 0.055), d_bot, inf)
    stack = np.stack([e_top, e_mid, e_bot])
    lane_i = np.argmin(stack, axis=0)
    in_lane = np.isfinite(np.min(stack, axis=0))
    lane_codes = np.array([idx[Zone.TOP_LANE], idx[Zone.MID_LANE], idx[Zone.BOT_LANE]], dtype=np.int16)
    out[in_lane] = lane_codes[lane_i[in_lane]]

    # 1b. outer rim (between the side lanes and the map border) -> nearest side lane
    loop = tuple(TOP_LANE_POLYLINE) + tuple(BOT_LANE_POLYLINE)
    outside = ~_inside_polygon(u, v, loop)
    out[outside & (d_top <= d_bot)] = idx[Zone.TOP_LANE]
    out[outside & (d_top > d_bot)] = idx[Zone.BOT_LANE]

    # 1a. bases have the highest priority
    d_blue = np.hypot(u - BLUE_BASE_CORNER[0], v - BLUE_BASE_CORNER[1])
    d_red = np.hypot(u - RED_BASE_CORNER[0], v - RED_BASE_CORNER[1])
    out[(d_blue < BASE_RADIUS) & (d_blue <= d_red)] = idx[Zone.BLUE_BASE]
    out[(d_red < BASE_RADIUS) & (d_red < d_blue)] = idx[Zone.RED_BASE]
    return out


# --------------------------------------------------------------------------------------
# Lookup table
# --------------------------------------------------------------------------------------
_lut_lock = threading.Lock()
_lut: np.ndarray | None = None


def _get_lut() -> np.ndarray:
    global _lut
    lut = _lut
    if lut is not None:
        return lut
    with _lut_lock:
        if _lut is None:
            n = max(16, int(ZONE_LUT_SIZE))
            c = (np.arange(n, dtype=np.float64) + 0.5) / n
            uu, vv = np.meshgrid(c, c)            # rows = v, cols = u
            _lut = _classify_arrays(uu, vv).astype(np.uint8)
        return _lut


def warm_up() -> None:
    """Build the zone lookup table now (~0.3 s once) instead of on the first classify_zone call."""
    try:
        _get_lut()
    except Exception:  # pragma: no cover - defensive
        log.exception("Cannot build the zone lookup table")


def reset_zone_cache() -> None:
    """Drop the lookup table (call after changing a shape constant at runtime)."""
    global _lut
    with _lut_lock:
        _lut = None


def zone_map(size: int = 256) -> np.ndarray:
    """``size x size`` array of zone codes (indices into :data:`ZONES`), row = v, col = u."""
    size = int(max(1, min(4096, size)))
    c = (np.arange(size, dtype=np.float64) + 0.5) / size
    uu, vv = np.meshgrid(c, c)
    return _classify_arrays(uu, vv).astype(np.uint8)


# --------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------


def classify_zone(u: float, v: float) -> Zone:
    """Zone of a minimap position (values outside [0, 1] are clamped, NaN -> centre)."""
    uu, vv = _coord(u), _coord(v)
    lut = _get_lut()
    n = lut.shape[0]
    i = min(int(vv * n), n - 1)
    j = min(int(uu * n), n - 1)
    return ZONES[int(lut[i, j])]


def classify_zone_exact(u: float, v: float) -> Zone:
    """Same as :func:`classify_zone` but computed analytically (slower, no lookup table)."""
    uu, vv = _coord(u), _coord(v)
    return ZONES[int(_classify_arrays(np.array([uu]), np.array([vv]))[0])]


def classify_zones(uv: Any) -> list[Zone]:
    """Vectorized :func:`classify_zone` for an ``(N, 2)`` array-like of positions."""
    try:
        arr = np.asarray(uv, dtype=np.float64).reshape(-1, 2)
    except (TypeError, ValueError):
        log.debug("classify_zones: invalid input %r", uv)
        return []
    if arr.size == 0:
        return []
    arr = np.clip(np.nan_to_num(arr, nan=0.5, posinf=1.0, neginf=0.0), 0.0, 1.0)
    lut = _get_lut()
    n = lut.shape[0]
    j = np.minimum((arr[:, 0] * n).astype(np.int64), n - 1)
    i = np.minimum((arr[:, 1] * n).astype(np.int64), n - 1)
    return [ZONES[int(k)] for k in lut[i, j]]


def _as_zone(zone: Any) -> Zone | None:
    """Accept a Zone, its value ("top_lane") or its name ("TOP_LANE")."""
    if isinstance(zone, Zone):
        return zone
    if isinstance(zone, str):
        key = zone.strip()
        try:
            return Zone(key.lower())
        except ValueError:
            return Zone.__members__.get(key.upper())
    return None


def lane_of(zone: Zone | None) -> str | None:
    """``"top"`` / ``"mid"`` / ``"bot"`` for lane zones, else ``None``."""
    z = _as_zone(zone)
    return _ZONE_LANE.get(z) if z is not None else None


def is_base(zone: Zone | None) -> bool:
    """True for :attr:`Zone.BLUE_BASE` and :attr:`Zone.RED_BASE`."""
    return _as_zone(zone) in (Zone.BLUE_BASE, Zone.RED_BASE)


def is_river(zone: Zone | None) -> bool:
    """True for the two river zones."""
    return _as_zone(zone) in (Zone.TOP_RIVER, Zone.BOT_RIVER)


def is_jungle(zone: Zone | None) -> bool:
    """True for the four jungle quadrants."""
    return _as_zone(zone) in (Zone.BLUE_JUNGLE_TOP, Zone.BLUE_JUNGLE_BOT,
                              Zone.RED_JUNGLE_TOP, Zone.RED_JUNGLE_BOT)


def zone_owner(zone: Zone | None) -> str | None:
    """Team owning a base / jungle zone (``"ORDER"`` or ``"CHAOS"``), else ``None``."""
    z = _as_zone(zone)
    if z in (Zone.BLUE_BASE, Zone.BLUE_JUNGLE_TOP, Zone.BLUE_JUNGLE_BOT):
        return "ORDER"
    if z in (Zone.RED_BASE, Zone.RED_JUNGLE_TOP, Zone.RED_JUNGLE_BOT):
        return "CHAOS"
    return None


def side_of(u: float, v: float) -> str:
    """``"top"`` when ``u + v < 1`` (top half of the map), else ``"bot"``."""
    uu, vv = _coord(u), _coord(v)
    return "top" if uu + vv < 1.0 else "bot"


def normalize_team(team: Any) -> str | None:
    """``"ORDER"`` / ``"CHAOS"`` from various spellings (blue/red/100/200), else ``None``."""
    if team is None:
        return None
    t = str(team).strip().upper()
    if t in ("ORDER", "BLUE", "100", "T1"):
        return "ORDER"
    if t in ("CHAOS", "RED", "200", "T2"):
        return "CHAOS"
    return None


_FIXED_LABELS: dict[Zone, str] = {
    Zone.TOP_LANE: "en haut",
    Zone.MID_LANE: "au milieu",
    Zone.BOT_LANE: "en bas",
    Zone.TOP_RIVER: "dans la rivière du haut",
    Zone.BOT_RIVER: "dans la rivière du bas",
}
_JUNGLE_PART: dict[Zone, tuple[str, str]] = {
    Zone.BLUE_JUNGLE_TOP: ("ORDER", "du haut"),
    Zone.BLUE_JUNGLE_BOT: ("ORDER", "du bas"),
    Zone.RED_JUNGLE_TOP: ("CHAOS", "du haut"),
    Zone.RED_JUNGLE_BOT: ("CHAOS", "du bas"),
}
_COLOR_FR: dict[str, str] = {"ORDER": "bleue", "CHAOS": "rouge"}


def zone_label_fr(zone: Zone | None, my_team: str | None) -> str:
    """Short French location phrase for speech, e.g. ``"dans ta jungle du haut"``.

    ``my_team`` (``"ORDER"`` / ``"CHAOS"``) turns jungles and bases into "ta" / "ennemie";
    with ``None`` (spectator / unknown) the colour is used ("dans la jungle bleue du haut").
    Unknown zones give ``""``.
    """
    z = _as_zone(zone)
    if z is None:
        return ""
    fixed = _FIXED_LABELS.get(z)
    if fixed is not None:
        return fixed
    team = normalize_team(my_team)
    if z in _JUNGLE_PART:
        owner, part = _JUNGLE_PART[z]
        if team is None:
            return f"dans la jungle {_COLOR_FR[owner]} {part}"
        if team == owner:
            return f"dans ta jungle {part}"
        return f"dans la jungle ennemie {part}"
    owner = "ORDER" if z == Zone.BLUE_BASE else "CHAOS"
    if team is None:
        return f"dans la base {_COLOR_FR[owner]}"
    return "dans ta base" if team == owner else "dans la base ennemie"


_FIXED_NAMES: dict[Zone, str] = {
    Zone.TOP_LANE: "voie du haut",
    Zone.MID_LANE: "voie du milieu",
    Zone.BOT_LANE: "voie du bas",
    Zone.TOP_RIVER: "rivière du haut",
    Zone.BOT_RIVER: "rivière du bas",
}


def zone_name_fr(zone: Zone | None, my_team: str | None) -> str:
    """Short French noun phrase for on-screen text, e.g. ``"rivière du haut"``, ``"ta jungle du bas"``.

    Same rules as :func:`zone_label_fr` but without the preposition (for HUD lines such as
    "vu il y a 23 s, rivière du haut"). Unknown zones give ``""``.
    """
    z = _as_zone(zone)
    if z is None:
        return ""
    fixed = _FIXED_NAMES.get(z)
    if fixed is not None:
        return fixed
    team = normalize_team(my_team)
    if z in _JUNGLE_PART:
        owner, part = _JUNGLE_PART[z]
        noun = "jungle"
    else:
        owner, part = ("ORDER" if z == Zone.BLUE_BASE else "CHAOS"), ""
        noun = "base"
    if team is None:
        text = f"{noun} {_COLOR_FR[owner]}"
    elif team == owner:
        text = f"ta {noun}"
    else:
        text = f"{noun} ennemie"
    return f"{text} {part}" if part else text


def dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Euclidean distance between two normalized positions (``inf`` if either is invalid)."""
    try:
        d = math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))
    except (TypeError, ValueError, IndexError):
        return math.inf
    return math.inf if math.isnan(d) else d


def to_game_units(d: float) -> float:
    """Convert a normalized distance to game units (1.0 == map width)."""
    try:
        return float(d) * MAP_GAME_UNITS
    except (TypeError, ValueError):
        return math.inf


def game_to_uv(x: float, y: float) -> Point:
    """Game coordinates (x right, y up) -> normalized minimap ``(u, v)`` (not clamped)."""
    return float(x) / MAP_GAME_UNITS, 1.0 - float(y) / MAP_GAME_HEIGHT


def uv_to_game(u: float, v: float) -> Point:
    """Normalized minimap ``(u, v)`` -> game coordinates (x right, y up)."""
    return float(u) * MAP_GAME_UNITS, (1.0 - float(v)) * MAP_GAME_HEIGHT


def distance_to_lane(u: float, v: float, lane: str) -> float:
    """Distance from a position to the centre line of ``lane`` ("top" / "mid" / "bot")."""
    poly = {"top": TOP_LANE_POLYLINE, "mid": MID_LANE_POLYLINE, "bot": BOT_LANE_POLYLINE}.get(
        str(lane).strip().lower())
    if poly is None:
        return math.inf
    uu, vv = _coord(u), _coord(v)
    return float(_polyline_dist(np.array([uu]), np.array([vv]), poly)[0])


def in_fountain(u: float, v: float, team: str | None) -> bool:
    """True if the position is on ``team``'s fountain / spawn platform (any team if None)."""
    uu, vv = _coord(u), _coord(v)
    t = normalize_team(team)
    centres = {"ORDER": [BLUE_FOUNTAIN], "CHAOS": [RED_FOUNTAIN]}.get(t or "", [BLUE_FOUNTAIN, RED_FOUNTAIN])
    return any(math.hypot(uu - cx, vv - cy) < FOUNTAIN_RADIUS for cx, cy in centres)


#: Debug colours (BGR) per zone for :func:`zone_debug_image`.
ZONE_DEBUG_BGR: dict[Zone, tuple[int, int, int]] = {
    Zone.BLUE_BASE: (200, 120, 40),
    Zone.RED_BASE: (40, 40, 200),
    Zone.TOP_LANE: (0, 200, 255),
    Zone.MID_LANE: (0, 255, 120),
    Zone.BOT_LANE: (255, 0, 255),
    Zone.TOP_RIVER: (255, 220, 0),
    Zone.BOT_RIVER: (180, 140, 0),
    Zone.BLUE_JUNGLE_TOP: (140, 90, 30),
    Zone.BLUE_JUNGLE_BOT: (100, 60, 20),
    Zone.RED_JUNGLE_TOP: (60, 60, 150),
    Zone.RED_JUNGLE_BOT: (30, 30, 100),
}


def zone_debug_image(background_bgr: np.ndarray | None = None, size: int = 512,
                     alpha: float = 0.45) -> np.ndarray:
    """BGR image of the zone partition, optionally blended over a minimap image."""
    size = int(max(8, min(4096, size)))
    codes = zone_map(size)
    palette = np.array([ZONE_DEBUG_BGR[z] for z in ZONES], dtype=np.uint8)
    colour = palette[codes]
    if background_bgr is None or not isinstance(background_bgr, np.ndarray) or background_bgr.size == 0:
        return colour
    try:
        import cv2  # local import: geometry itself only needs numpy

        bg = background_bgr
        if bg.ndim == 2:
            bg = cv2.cvtColor(bg, cv2.COLOR_GRAY2BGR)
        elif bg.shape[2] == 4:
            bg = cv2.cvtColor(bg, cv2.COLOR_BGRA2BGR)
        bg = cv2.resize(bg.astype(np.uint8), (size, size), interpolation=cv2.INTER_AREA)
        a = float(min(1.0, max(0.0, alpha)))
        return cv2.addWeighted(bg, 1.0 - a, colour, a, 0.0)
    except Exception:  # pragma: no cover - debug helper must not fail
        log.exception("zone_debug_image: cannot blend background")
        return colour
