"""Rendering of League of Legends minimaps (shared by training, demo mode and selftest).

A :class:`Scene` describes what is on the minimap (texture variant, fog of war and vision,
champions, minions, structures, camps, wards, pings, camera rectangle...) in *normalized
minimap coordinates* ``(u, v)`` (``u`` left -> right, ``v`` top -> bottom, radii normalized
by the minimap width). :class:`MinimapRenderer` turns it into a BGR ``uint8`` image that
mimics the in-game minimap:

* the square ``2dlevelminimap_*`` texture scaled to the whole minimap, its transparent
  parts (walls / void) shown as a very dark blue-grey (:data:`VOID_BGR`);
* fog of war (``fogofwaroverlay*`` textures) darkening everything outside the vision
  circles, with feathered edges;
* team-tinted structure icons at their official positions (:data:`STRUCTURES`), jungle
  camps (:data:`CAMPS`), wards, minion dots, the white camera rectangle;
* champion icons: round portrait + coloured ring (:data:`RING_BGR`) + thin dark outline,
  the local player (``"self"``) drawn on top; recall / teleport outlines; pings on top.

Every visual constant is a plain module-level value so it can be re-tuned from real
screenshots (``docs/MINIMAP_FACTS.md``) without touching the code. Images are BGR
``uint8``; icons are RGBA ``uint8`` (module convention).

Pixel convention: a pixel ``i`` covers the continuous interval ``[i, i + 1)``, so the
continuous centre of an icon at ``u`` in an image of width ``W`` is ``u * W``.
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import cv2
import numpy as np

log = logging.getLogger(__name__)

BGR = tuple[int, int, int]

# ======================================================================================
# Tunable visual constants (normalized sizes are fractions of the minimap width)
# ======================================================================================

#: Colour shown where the texture is transparent (walls, void around the map).
VOID_BGR: BGR = (20, 16, 12)

#: Default champion ring colours per relation to the local player.
RING_BGR: dict[str, BGR] = {
    "enemy": (40, 40, 215),     # red
    "ally": (215, 140, 30),     # blue
    "self": (40, 200, 235),     # yellow / gold
}
#: Ring thickness divided by the icon radius.
RING_FRAC: float = 0.14
#: Thin dark outline drawn just outside the ring.
OUTLINE_BGR: BGR = (14, 12, 12)
OUTLINE_FRAC: float = 0.06          # outline width / icon radius
OUTLINE_MIN_PX: float = 0.7         # minimum outline width in pixels
#: Part of the (square) portrait image covered by the visible disc: the portrait is zoomed
#: slightly so that its own anti-aliased transparent border hides under the ring.
PORTRAIT_FILL: float = 0.94
#: "Dead" / greyed icon look.
GREY_RING_BGR: BGR = (110, 110, 110)
GREY_ICON_DIM: float = 0.55
#: Default champion icon radius (demo / selftest); real HUD: diameter ~7-11 % of the width.
ICON_RADIUS_DEFAULT: float = 0.045

#: Fog of war: darkening strength (``Scene.fog_alpha``) is relative to the mean alpha of the
#: ``fogofwaroverlay`` textures, so ``fog_alpha = 0.55`` darkens by ~55 % on average.
FOG_ALPHA_DEFAULT: float = 0.55
FOG_TEX_REF_ALPHA: float = 88.0
FOG_MAX_DARKEN: float = 0.93
#: Gaussian feathering of the vision circle edges (sigma, normalized).
FOG_FEATHER: float = 0.010
#: Resolution of the vision mask (computed small, then upsampled).
FOG_MASK_RES: int = 96
#: Typical vision radii (normalized) - used by demo / synth to build ``Scene.vision``.
VISION_R: dict[str, float] = {"champion": 0.08, "turret": 0.09, "ward": 0.06, "minion": 0.035}

#: Team tints of structures / minions (team-relative colours: ally blue, enemy red).
TEAM_BGR: dict[str, BGR] = {"ally": (235, 165, 60), "enemy": (70, 70, 230)}
#: Minion dots.
MINION_R: float = 0.0075
MINION_BGR: dict[str, BGR] = {"ally": (225, 150, 45), "enemy": (50, 50, 215)}
MINION_OUTLINE_BGR: BGR = (22, 18, 18)

#: Structure icons (diameter, normalized) and files.
STRUCTURE_SIZE: dict[str, float] = {"turret": 0.042, "inhibitor": 0.036, "nexus": 0.052}
STRUCTURE_ICON: dict[str, str] = {
    "turret": "icon_ui_tower_minimap.png",
    "inhibitor": "icon_ui_inhibitor_minimap_v2.png",
    "nexus": "icon_ui_nexus_minimap_v2.png",
}
#: Jungle camp / objective icons (diameter, normalized) and default files.
CAMP_SIZE: dict[str, float] = {
    "blue": 0.042, "red": 0.042, "gromp": 0.03, "wolves": 0.03, "raptors": 0.03,
    "krugs": 0.03, "scuttle": 0.03, "dragon": 0.055, "baron": 0.058,
}
CAMP_SIZE_DEFAULT: float = 0.032
CAMP_ICON: dict[str, str] = {
    "blue": "blue.png", "red": "red.png", "gromp": "camp.png", "wolves": "camp.png",
    "raptors": "camp.png", "krugs": "camp.png", "scuttle": "smallcamp.png",
    "dragon": "dragon.png", "baron": "baron.png",
}
#: Ward icons (diameter, normalized).
WARD_SIZE: float = 0.028
#: Ping icons (diameter, normalized).
PING_SIZE: float = 0.05
#: Recall / teleport outlines, relative to the champion icon diameter.
RECALL_SCALE: float = 1.38
TELEPORT_SCALE: float = 1.55
RECALL_ICON: dict[str, str] = {
    "ally": "recalloutline.png", "self": "recalloutline.png", "enemy": "recallhostileoutline.png",
}
TELEPORT_ICON: dict[str, str] = {
    "ally": "teleporthighlight_friendly.png", "self": "teleporthighlight_friendly.png",
    "enemy": "teleporthighlight_enemy.png",
}
DUMMY_ICON: dict[str, str] = {
    "ally": "dummy_friendly_circle.png", "self": "dummy_friendly_circle.png",
    "enemy": "dummy_enemy_circle.png",
}
#: Camera rectangle.
CAMERA_BGR: BGR = (235, 235, 235)
CAMERA_THICKNESS: float = 0.005     # normalized, at least 1 px

#: Textures.
TEXTURE_PREFIX: str = "2dlevelminimap_"
DEFAULT_TEXTURE: str = "2dlevelminimap_base_baron1.png"
DEFAULT_FOG: str = "fogofwaroverlay.png"
#: Fog overlay used for each texture variant (``2dlevelminimap_<variant>_baron<n>.png``).
FOG_TEXTURE_BY_VARIANT: dict[str, str] = {
    "base": "fogofwaroverlay.png",
    "cloud": "fogofwaroverlay_srx_cloud.png",
    "hextech": "fogofwaroverlay_srx_hextech.png",
    "infernal": "fogofwaroverlay_srx_infernal.png",
    "mountain": "fogofwaroverlay_srx_mountain.png",
    "ocean": "fogofwaroverlay_srx_ocean.png",
    "chemtech": "fogofwaroverlay_srx_chemtech.png",
}

#: Game units -> normalized coordinates: ``u = x / MAP_W``, ``v = 1 - y / MAP_H``.
MAP_W: float = 14870.0
MAP_H: float = 14980.0

_MIN_SIZE = 8
_MAX_SIZE = 4096
#: Texture pyramid levels (px): consecutive ratios <= 1.33 so bilinear resampling is clean.
_PYRAMID: tuple[int, ...] = (512, 384, 288, 216, 162, 122, 92, 69, 52, 39, 29, 22, 16, 12, 8)
_SUBDIRS_ICONS = ("icons/minimap", "icons/pings")


def game_to_uv(x: float, y: float) -> tuple[float, float]:
    """Convert game coordinates (x right, y up) to normalized minimap coordinates."""
    return round(x / MAP_W, 4), round(1.0 - y / MAP_H, 4)


# --------------------------------------------------------------------------------------
# Official positions (game units, community data) - checked visually against the texture:
# turrets sit on the khaki lane bands, inhibitors / nexus on the grey base paths.
# --------------------------------------------------------------------------------------
_STRUCTURES_GAME: tuple[tuple[str, float, float, str, str], ...] = (
    # id, x, y, kind, team
    ("ORDER_top_outer", 981, 10441, "turret", "ORDER"),
    ("ORDER_top_inner", 1512, 6699, "turret", "ORDER"),
    ("ORDER_top_base", 1169, 4287, "turret", "ORDER"),
    ("ORDER_mid_outer", 5846, 6396, "turret", "ORDER"),
    ("ORDER_mid_inner", 5048, 4812, "turret", "ORDER"),
    ("ORDER_mid_base", 3651, 3696, "turret", "ORDER"),
    ("ORDER_bot_outer", 10504, 1029, "turret", "ORDER"),
    ("ORDER_bot_inner", 6919, 1483, "turret", "ORDER"),
    ("ORDER_bot_base", 4281, 1253, "turret", "ORDER"),
    ("ORDER_nexus_turret_1", 1748, 2270, "turret", "ORDER"),
    ("ORDER_nexus_turret_2", 2177, 1807, "turret", "ORDER"),
    ("ORDER_top_inhibitor", 1171, 3571, "inhibitor", "ORDER"),
    ("ORDER_mid_inhibitor", 3203, 3208, "inhibitor", "ORDER"),
    ("ORDER_bot_inhibitor", 3452, 1236, "inhibitor", "ORDER"),
    ("ORDER_nexus", 1551, 1659, "nexus", "ORDER"),
    ("CHAOS_top_outer", 4318, 13875, "turret", "CHAOS"),
    ("CHAOS_top_inner", 7943, 13411, "turret", "CHAOS"),
    ("CHAOS_top_base", 10481, 13650, "turret", "CHAOS"),
    ("CHAOS_mid_outer", 8955, 8510, "turret", "CHAOS"),
    ("CHAOS_mid_inner", 9767, 10113, "turret", "CHAOS"),
    ("CHAOS_mid_base", 11134, 11207, "turret", "CHAOS"),
    ("CHAOS_bot_outer", 13866, 4505, "turret", "CHAOS"),
    ("CHAOS_bot_inner", 13327, 8226, "turret", "CHAOS"),
    ("CHAOS_bot_base", 13624, 10572, "turret", "CHAOS"),
    ("CHAOS_nexus_turret_1", 12611, 13084, "turret", "CHAOS"),
    ("CHAOS_nexus_turret_2", 13052, 12612, "turret", "CHAOS"),
    ("CHAOS_top_inhibitor", 11261, 13676, "inhibitor", "CHAOS"),
    ("CHAOS_mid_inhibitor", 11598, 11667, "inhibitor", "CHAOS"),
    ("CHAOS_bot_inhibitor", 13604, 11316, "inhibitor", "CHAOS"),
    ("CHAOS_nexus", 13052, 12997, "nexus", "CHAOS"),
)

#: Structures ``(u, v, kind, team)`` with kind in ``"turret" | "inhibitor" | "nexus"``.
STRUCTURES: list[tuple[float, float, str, str]] = [
    (*game_to_uv(x, y), kind, team) for _, x, y, kind, team in _STRUCTURES_GAME
]
#: Stable identifiers of :data:`STRUCTURES` (same order), e.g. ``"ORDER_top_outer"``.
STRUCTURE_IDS: list[str] = [sid for sid, *_ in _STRUCTURES_GAME]

_CAMPS_GAME: tuple[tuple[float, float, str], ...] = (
    (3821, 8101, "blue"), (2288, 8448, "gromp"), (3783, 6495, "wolves"),
    (6823, 5508, "raptors"), (7765, 4020, "red"), (8394, 2641, "krugs"),
    (11131, 6990, "blue"), (12703, 6443, "gromp"), (11059, 8422, "wolves"),
    (7852, 9434, "raptors"), (7101, 10900, "red"), (6317, 12146, "krugs"),
    (4400, 9600, "scuttle"), (10500, 5170, "scuttle"),
    (9866, 4414, "dragon"), (5007, 10471, "baron"),
)
#: Jungle camps and epic monster pits ``(u, v, name)``.
CAMPS: list[tuple[float, float, str]] = [(*game_to_uv(x, y), name) for x, y, name in _CAMPS_GAME]

#: Fountains (spawn platforms) per team, normalized.
FOUNTAINS: dict[str, tuple[float, float]] = {"ORDER": (0.045, 0.955), "CHAOS": (0.955, 0.045)}


# ======================================================================================
# Scene description
# ======================================================================================


@dataclass
class ChampionSprite:
    """A champion icon on the minimap.

    ``relation`` is ``"self" | "ally" | "enemy"``; ``icon`` is the round portrait (RGBA);
    ``ring_bgr`` overrides the ring colour (None -> :data:`RING_BGR`); ``label_class`` is the
    class used for training labels (None -> the class is not labelled, e.g. random-hue ring).
    Extra (optional) fields: ``grey`` draws a greyed "dead" icon, ``teleport`` a teleport
    highlight around it, ``ring_frac`` overrides :data:`RING_FRAC`.
    """

    u: float
    v: float
    r: float
    relation: str
    icon: np.ndarray | None
    ring_bgr: tuple | None = None
    recall: bool = False
    label_class: str | None = None
    grey: bool = False
    teleport: bool = False
    ring_frac: float | None = None


@dataclass
class Sprite:
    """Any other icon (hard negatives, objectives, ping rings...).

    ``icon`` is a file name (searched in ``icons/minimap`` then ``icons/pings``) or an RGBA
    array; ``size`` is the normalized diameter; ``tint`` multiplies the colours (BGR);
    ``layer`` is ``"under"`` (below champions) or ``"over"`` (above everything, like pings).
    """

    u: float
    v: float
    size: float
    icon: str | np.ndarray
    tint: tuple | None = None
    opacity: float = 1.0
    layer: str = "under"


@dataclass
class Scene:
    """Everything drawn on one minimap. All coordinates normalized (see module doc)."""

    texture: str = DEFAULT_TEXTURE                          # file name in assets/minimap
    size: int = 256                                         # output size (px)
    fog_alpha: float = FOG_ALPHA_DEFAULT                    # 0 = no fog of war
    vision: list[tuple[float, float, float]] = field(default_factory=list)  # (u, v, radius)
    champions: list[ChampionSprite] = field(default_factory=list)
    minions: list[tuple[float, float, str]] = field(default_factory=list)   # (u, v, "ally"|"enemy")
    structures: bool = True
    wards: list[tuple[float, float, str]] = field(default_factory=list)     # (u, v, icon name)
    pings: list[tuple[float, float, str]] = field(default_factory=list)     # (u, v, icon name)
    camera: tuple[float, float, float, float] | None = None                 # (u0, v0, u1, v1)
    camps: bool = True
    # ---- optional extensions (defaults keep the documented behaviour) ----
    my_team: str = "ORDER"                  # team of the local player (structure colours)
    destroyed: frozenset = frozenset()      # STRUCTURE_IDS (or indices) not drawn
    fog_texture: str | None = None          # None -> matches the texture variant
    camp_icons: list[tuple[float, float, str]] | None = None  # explicit (u, v, icon); None -> CAMPS
    sprites: list[Sprite] = field(default_factory=list)
    structure_scale: float = 1.0
    minion_r: float = MINION_R
    camera_bgr: tuple | None = None
    camera_on_top: bool = False
    void_bgr: tuple | None = None


# ======================================================================================
# Image helpers
# ======================================================================================


def _read_image(path: str | os.PathLike[str]) -> np.ndarray | None:
    """Decode an image file (unicode-safe on Windows); None if unreadable."""
    try:
        data = np.fromfile(os.fspath(path), dtype=np.uint8)
    except (OSError, ValueError):
        return None
    if data.size == 0:
        return None
    img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    return img


def _to_rgba_u8(img: np.ndarray, *, bgr_order: bool) -> np.ndarray:
    """Normalize any decoded image to RGBA ``uint8`` (``bgr_order``: OpenCV channel order)."""
    if img.dtype == np.uint16:
        img = (img >> 8).astype(np.uint8)
    elif img.dtype != np.uint8:
        img = np.clip(np.nan_to_num(img.astype(np.float32)), 0, 255).astype(np.uint8)
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2RGBA)
    if img.ndim != 3:
        raise ValueError(f"unsupported image shape {img.shape}")
    ch = img.shape[2]
    if ch == 1:
        return cv2.cvtColor(img[:, :, 0], cv2.COLOR_GRAY2RGBA)
    if ch == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2RGBA if bgr_order else cv2.COLOR_RGB2RGBA)
    if ch == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2RGBA) if bgr_order else np.ascontiguousarray(img)
    raise ValueError(f"unsupported channel count {ch}")


def load_rgba(path: str | os.PathLike[str]) -> np.ndarray:
    """Load an image file as RGBA ``uint8`` (grey / RGB / 16-bit images are converted).

    Raises ``FileNotFoundError`` if the file is missing and ``ValueError`` if it cannot be
    decoded.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    img = _read_image(p)
    if img is None:
        raise ValueError(f"cannot decode image {p}")
    return _to_rgba_u8(img, bgr_order=True)


def _as_rgba(src: Any) -> np.ndarray | None:
    """Best-effort conversion of an icon array to RGBA uint8 (None if unusable)."""
    if not isinstance(src, np.ndarray) or src.size == 0 or src.ndim not in (2, 3):
        return None
    if src.shape[0] < 1 or src.shape[1] < 1:
        return None
    try:
        return _to_rgba_u8(src, bgr_order=False)
    except (ValueError, cv2.error):
        return None


def _valid_dst(dst: Any) -> bool:
    return (
        isinstance(dst, np.ndarray)
        and dst.ndim == 3
        and dst.shape[2] == 3
        and dst.dtype == np.uint8
        and dst.shape[0] > 0
        and dst.shape[1] > 0
    )


def _finite(*vals: float) -> bool:
    try:
        return all(math.isfinite(float(v)) for v in vals)
    except (TypeError, ValueError):
        return False


def _resize_rgba(src: np.ndarray, w: int, h: int) -> np.ndarray:
    """Resize RGBA with the right filter for the direction (area when shrinking)."""
    sh, sw = src.shape[:2]
    if (w, h) == (sw, sh):
        return src
    interp = cv2.INTER_AREA if (w < sw or h < sh) else cv2.INTER_LINEAR
    return cv2.resize(src, (max(1, w), max(1, h)), interpolation=interp)


def _premultiply(rgba: np.ndarray, tint: Sequence[float] | None = None,
                 opacity: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """RGBA uint8 -> blit-ready pair ``(premultiplied BGR + 0.5, 1 - alpha)`` (float32).

    The +0.5 makes the final ``astype(uint8)`` round to nearest; ``1 - alpha`` has shape
    ``[h, w, 1]``. See :func:`_blit_premul`.
    """
    a = rgba[:, :, 3:4].astype(np.float32) * (float(opacity) / 255.0)
    bgr = rgba[:, :, 2::-1].astype(np.float32)
    if tint is not None:
        bgr *= np.asarray(tint, dtype=np.float32)[:3] * (1.0 / 255.0)
    bgr *= a
    bgr += 0.5
    return bgr, 1.0 - a


def _blit_premul(dst: np.ndarray, bgr_p: np.ndarray, inv_a: np.ndarray, x0: int, y0: int) -> None:
    """Blend a prepared sprite (see :func:`_premultiply`) with top-left corner at (x0, y0).

    Clipped at the borders; values stay in [0, 255.5) so no clipping is needed.
    """
    H, W = dst.shape[:2]
    h, w = inv_a.shape[:2]
    dx0, dy0 = max(0, x0), max(0, y0)
    dx1, dy1 = min(W, x0 + w), min(H, y0 + h)
    if dx0 >= dx1 or dy0 >= dy1:
        return
    sx0, sy0 = dx0 - x0, dy0 - y0
    sx1, sy1 = sx0 + (dx1 - dx0), sy0 + (dy1 - dy0)
    region = dst[dy0:dy1, dx0:dx1].astype(np.float32)
    region *= inv_a[sy0:sy1, sx0:sx1]
    region += bgr_p[sy0:sy1, sx0:sx1]
    dst[dy0:dy1, dx0:dx1] = region.astype(np.uint8)


def alpha_blit(dst_bgr: np.ndarray, src_rgba: np.ndarray, cx: float, cy: float,
               scale: float = 1.0, opacity: float = 1.0) -> None:
    """Alpha-blend an RGBA sprite onto a BGR image, centred on ``(cx, cy)`` (pixels).

    The sprite is resized by ``scale`` first and clipped at the image borders. Invalid
    inputs (empty images, NaN positions, non-positive scale...) are ignored. In place.
    """
    try:
        if not _valid_dst(dst_bgr) or not _finite(cx, cy, scale, opacity):
            return
        rgba = _as_rgba(src_rgba)
        if rgba is None or scale <= 0 or opacity <= 0:
            return
        h0, w0 = rgba.shape[:2]
        w = max(1, int(round(w0 * scale)))
        h = max(1, int(round(h0 * scale)))
        if w > 4 * _MAX_SIZE or h > 4 * _MAX_SIZE:
            return
        rgba = _resize_rgba(rgba, w, h)
        x0 = int(math.floor(cx - w / 2.0 + 0.5))
        y0 = int(math.floor(cy - h / 2.0 + 0.5))
        H, W = dst_bgr.shape[:2]
        if x0 >= W or y0 >= H or x0 + w <= 0 or y0 + h <= 0:
            return
        bgr_p, inv_a = _premultiply(rgba, opacity=min(1.0, float(opacity)))
        _blit_premul(dst_bgr, bgr_p, inv_a, x0, y0)
    except Exception:  # never let a drawing helper crash a caller
        log.exception("alpha_blit failed")


def _disc_patch(dst: np.ndarray, cx: float, cy: float, extent: float
                ) -> tuple[int, int, int, int, np.ndarray] | None:
    """Clipped patch bounds around (cx, cy) and the distance of each pixel centre to it."""
    H, W = dst.shape[:2]
    x0 = max(0, int(math.floor(cx - extent - 1)))
    y0 = max(0, int(math.floor(cy - extent - 1)))
    x1 = min(W, int(math.ceil(cx + extent + 1)))
    y1 = min(H, int(math.ceil(cy + extent + 1)))
    if x0 >= x1 or y0 >= y1:
        return None
    xs = np.arange(x0, x1, dtype=np.float32) + (0.5 - cx)
    ys = np.arange(y0, y1, dtype=np.float32) + (0.5 - cy)
    d = np.sqrt(xs[None, :] ** 2 + ys[:, None] ** 2)
    return x0, y0, x1, y1, d


def _portrait_layer(icon_rgba: np.ndarray, r_in: float, cx: float, cy: float,
                    x0: int, y0: int, w: int, h: int) -> tuple[np.ndarray, np.ndarray]:
    """Portrait resampled onto the patch grid: (BGR float32 [h,w,3], alpha float32 [h,w])."""
    sh, sw = icon_rgba.shape[:2]
    half_src = 0.5 * min(sh, sw) * PORTRAIT_FILL
    target_r = max(0.5, r_in + 0.5)
    # pre-shrink with area filtering so that the final warp is ~1:1 (no aliasing)
    k = half_src / target_r
    if k > 1.5:
        nw = max(2, int(round(sw / k)))
        nh = max(2, int(round(sh / k)))
        icon_rgba = cv2.resize(icon_rgba, (nw, nh), interpolation=cv2.INTER_AREA)
        sh, sw = nh, nw
        half_src = 0.5 * min(sh, sw) * PORTRAIT_FILL
        k = half_src / target_r
    # inverse map: dst pixel j (centre x0 + j + 0.5) -> source index
    m = np.array(
        [[k, 0.0, k * (x0 + 0.5 - cx) + sw / 2.0 - 0.5],
         [0.0, k, k * (y0 + 0.5 - cy) + sh / 2.0 - 0.5]],
        dtype=np.float64,
    )
    warped = cv2.warpAffine(
        icon_rgba, m, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
        borderMode=cv2.BORDER_REPLICATE,
    )
    bgr = warped[:, :, 2::-1].astype(np.float32)
    a = warped[:, :, 3].astype(np.float32) * (1.0 / 255.0)
    return bgr, a


def draw_champion_icon(dst_bgr: np.ndarray, cx: float, cy: float, radius_px: float,
                       icon_rgba: np.ndarray | None, ring_bgr: Sequence[int] | None,
                       ring_frac: float = RING_FRAC, grey: bool = False, *,
                       outline_bgr: Sequence[int] | None = OUTLINE_BGR,
                       outline_px: float | None = None, ring_shading: float = 0.0) -> None:
    """Draw a minimap champion icon in place: portrait disc + coloured ring + dark outline.

    ``(cx, cy)`` is the continuous centre in pixels and ``radius_px`` the outer radius of
    the ring (the outline is drawn just outside). ``grey`` renders a greyed "dead" icon.
    ``ring_shading`` (0..1) adds a subtle top-light / bottom-dark bevel on the ring.
    Invalid inputs are ignored (never raises).
    """
    try:
        if not _valid_dst(dst_bgr) or not _finite(cx, cy, radius_px, ring_frac):
            return
        R = float(radius_px)
        if R < 0.5 or R > 2 * _MAX_SIZE:
            return
        rf = min(0.9, max(0.0, float(ring_frac)))
        ow = float(outline_px) if outline_px is not None else max(OUTLINE_MIN_PX, R * OUTLINE_FRAC)
        if outline_bgr is None:
            ow = 0.0
        patch_info = _disc_patch(dst_bgr, cx, cy, R + ow)
        if patch_info is None:
            return
        x0, y0, x1, y1, d = patch_info
        patch = dst_bgr[y0:y1, x0:x1].astype(np.float32)

        # 1) outline
        if ow > 0:
            a = np.clip(R + ow + 0.5 - d, 0.0, 1.0)[:, :, None]
            patch += (np.asarray(outline_bgr, np.float32)[:3] - patch) * a
        # 2) ring
        ring = np.asarray(GREY_RING_BGR if grey else (ring_bgr if ring_bgr is not None
                                                      else RING_BGR["enemy"]), np.float32)[:3]
        a = np.clip(R + 0.5 - d, 0.0, 1.0)[:, :, None]
        if ring_shading and not grey:
            ys = (np.arange(y0, y1, dtype=np.float32) + (0.5 - cy)) / R
            shade = 1.0 - float(ring_shading) * np.clip(ys, -1.0, 1.0)[:, None, None]
            ring_img = np.clip(ring[None, None, :] * shade, 0, 255)
            patch += (ring_img - patch) * a
        else:
            patch += (ring - patch) * a
        # 3) portrait
        r_in = R * (1.0 - rf)
        rgba = _as_rgba(icon_rgba) if icon_rgba is not None else None
        if r_in >= 0.5:
            cover = np.clip(r_in + 0.5 - d, 0.0, 1.0)
            if rgba is not None:
                p_bgr, p_a = _portrait_layer(rgba, r_in, cx, cy, x0, y0, x1 - x0, y1 - y0)
                if grey:
                    g = cv2.cvtColor(np.clip(p_bgr, 0, 255).astype(np.uint8), cv2.COLOR_BGR2GRAY)
                    p_bgr = np.repeat(g[:, :, None].astype(np.float32) * GREY_ICON_DIM, 3, axis=2)
                cover = cover * p_a
            else:  # no portrait: dark neutral disc
                p_bgr = np.full(patch.shape, 45.0, np.float32)
            patch += (p_bgr - patch) * cover[:, :, None]
        dst_bgr[y0:y1, x0:x1] = np.clip(patch + 0.5, 0, 255).astype(np.uint8)
    except Exception:
        log.exception("draw_champion_icon failed")


# ======================================================================================
# Renderer
# ======================================================================================


class _LRU:
    """Tiny thread-safe LRU cache."""

    def __init__(self, capacity: int) -> None:
        self.capacity = max(1, int(capacity))
        self._d: OrderedDict[Any, Any] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Any) -> Any:
        with self._lock:
            val = self._d.get(key)
            if val is not None:
                self._d.move_to_end(key)
            return val

    def put(self, key: Any, val: Any) -> None:
        with self._lock:
            self._d[key] = val
            self._d.move_to_end(key)
            while len(self._d) > self.capacity:
                self._d.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


def _default_assets_dir() -> Path:
    try:
        from .paths import asset_path

        return asset_path()
    except Exception:  # pragma: no cover - paths module missing / broken
        return Path(__file__).resolve().parent / "assets"


def _normalize_png_name(name: str) -> str:
    name = os.path.basename(str(name).strip())
    return name if name.lower().endswith(".png") else name + ".png"


def texture_variant(texture: str) -> str:
    """Variant of a texture name: ``"2dlevelminimap_ocean_baron2.png"`` -> ``"ocean"``."""
    m = re.match(r"^2dlevelminimap_([a-z]+)", os.path.basename(str(texture)).lower())
    return m.group(1) if m else "base"


def fog_for_texture(texture: str) -> str:
    """Fog overlay file name matching a texture variant."""
    return FOG_TEXTURE_BY_VARIANT.get(texture_variant(texture), DEFAULT_FOG)


class MinimapRenderer:
    """Render :class:`Scene` objects to BGR images. Thread-safe; caches resized assets."""

    def __init__(self, assets_dir: Path | None = None) -> None:
        self.assets_dir = Path(assets_dir) if assets_dir is not None else _default_assets_dir()
        self._lock = threading.Lock()
        self._raw_textures: dict[str, np.ndarray | None] = {}
        self._raw_icons: dict[str, np.ndarray | None] = {}
        self._pyramid_cache = _LRU(40)  # (kind, name, void) -> list of levels
        self._base_cache = _LRU(48)     # (texture, size, void) -> uint8 BGR
        self._fog_cache = _LRU(48)      # (fog, size) -> (fog colour uint8, k0 float32)
        self._sprite_cache = _LRU(1024)  # (name, px, tint) -> (bgr_p, 1 - alpha)
        self._warned: set[str] = set()

    # ---------------------------------------------------------------- asset access
    def _warn_once(self, key: str, msg: str, *args: Any) -> None:
        if key not in self._warned:
            self._warned.add(key)
            log.warning(msg, *args)

    def textures(self) -> list[str]:
        """Available minimap texture file names (sorted)."""
        try:
            d = self.assets_dir / "minimap"
            return sorted(p.name for p in d.glob(TEXTURE_PREFIX + "*.png") if p.is_file())
        except OSError:
            return []

    def fogs(self) -> list[str]:
        """Available fog-of-war overlay file names (sorted)."""
        try:
            d = self.assets_dir / "minimap"
            return sorted(p.name for p in d.glob("fogofwaroverlay*.png") if p.is_file())
        except OSError:
            return []

    def texture_rgba(self, name: str) -> np.ndarray | None:
        """Raw texture (RGBA uint8, 512x512 for official files); None if missing."""
        name = _normalize_png_name(name)
        with self._lock:
            if name in self._raw_textures:
                return self._raw_textures[name]
        img: np.ndarray | None
        try:
            img = load_rgba(self.assets_dir / "minimap" / name)
        except (OSError, ValueError, cv2.error) as exc:
            self._warn_once("tex:" + name, "Minimap texture %s unavailable: %s", name, exc)
            img = None
        with self._lock:
            self._raw_textures[name] = img
        return img

    def icon(self, name: str) -> np.ndarray | None:
        """A minimap / ping icon by file name (RGBA uint8); None if missing."""
        name = _normalize_png_name(name)
        with self._lock:
            if name in self._raw_icons:
                return self._raw_icons[name]
        img = None
        for sub in _SUBDIRS_ICONS:
            p = self.assets_dir / sub / name
            try:
                if p.is_file():
                    img = load_rgba(p)
                    break
            except (OSError, ValueError, cv2.error) as exc:
                log.debug("Cannot load icon %s: %s", p, exc)
        if img is None:
            self._warn_once("icon:" + name, "Minimap icon %s unavailable", name)
        with self._lock:
            self._raw_icons[name] = img
        return img

    def _pyramid(self, kind: str, name: str, void: tuple) -> list[np.ndarray] | None:
        """Area-downsampled levels (sizes :data:`_PYRAMID`) of a texture.

        ``kind == "base"``: texture composited over ``void`` (BGR uint8). Compositing over a
        constant colour commutes with linear resampling, so levels can be composited first.
        ``kind == "fog"``: fog overlay as BGRA. Cached per (kind, name, void).
        """
        key = (kind, name, void)
        cached = self._pyramid_cache.get(key)
        if cached is not None:
            return cached
        rgba = self.texture_rgba(name)
        fallback = DEFAULT_TEXTURE if kind == "base" else DEFAULT_FOG
        if rgba is None and name != fallback:
            rgba = self.texture_rgba(fallback)
        if rgba is None:
            return None
        if kind == "base":
            a = rgba[:, :, 3:4].astype(np.float32) * (1.0 / 255.0)
            top = rgba[:, :, 2::-1].astype(np.float32) * a
            top += np.asarray(void, np.float32)[None, None, :3] * (1.0 - a)
            top = np.clip(top + 0.5, 0, 255).astype(np.uint8)
        else:
            top = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)
        levels = [top]
        for lv in _PYRAMID[1:]:
            if lv < top.shape[0]:
                levels.append(cv2.resize(top, (lv, lv), interpolation=cv2.INTER_AREA))
        self._pyramid_cache.put(key, levels)
        return levels

    @staticmethod
    def _from_pyramid(levels: list[np.ndarray], size: int) -> np.ndarray:
        """Resize from the smallest pyramid level >= size (ratio <= 1.33: bilinear is clean)."""
        src = levels[0]
        for lv in levels[1:]:
            if lv.shape[0] >= size:
                src = lv
        if src.shape[0] == size:
            return src.copy()
        interp = cv2.INTER_LINEAR if src.shape[0] > size else cv2.INTER_CUBIC
        return cv2.resize(src, (size, size), interpolation=interp)

    def _base(self, texture: str, size: int, void: tuple) -> np.ndarray:
        """Texture composited over the void colour at ``size`` (cached, do not modify)."""
        key = (texture, size, void)
        cached = self._base_cache.get(key)
        if cached is not None:
            return cached
        levels = self._pyramid("base", texture, void)
        out = self._from_pyramid(levels, size) if levels else _procedural_map(size, void)
        self._base_cache.put(key, out)
        return out

    def _fog(self, fog_name: str, size: int) -> tuple[np.ndarray, np.ndarray] | None:
        """(fog colour BGR uint8 [S,S,3], relative strength k0 float32 [S,S]) at ``size``."""
        key = (fog_name, size)
        cached = self._fog_cache.get(key)
        if cached is not None:
            return cached
        levels = self._pyramid("fog", fog_name, (0, 0, 0))
        if not levels:
            col = np.empty((size, size, 3), np.uint8)
            col[:] = (24, 8, 6)
            k0 = np.ones((size, size), np.float32)
        else:
            small = self._from_pyramid(levels, size)
            col = cv2.cvtColor(small, cv2.COLOR_BGRA2BGR)
            k0 = cv2.extractChannel(small, 3).astype(np.float32)
            k0 *= 1.0 / FOG_TEX_REF_ALPHA
        val = (col, k0)
        self._fog_cache.put(key, val)
        return val

    def _sprite(self, icon: str | np.ndarray, px: int, tint: tuple | None
                ) -> tuple[np.ndarray, np.ndarray] | None:
        """Icon resized to ``px`` (largest side), tinted, premultiplied (cached by name)."""
        px = int(max(1, min(px, 2 * _MAX_SIZE)))
        tkey = tuple(int(c) for c in tint[:3]) if tint is not None else None
        if isinstance(icon, str):
            key = (_normalize_png_name(icon), px, tkey)
            cached = self._sprite_cache.get(key)
            if cached is not None:
                return cached
            rgba = self.icon(icon)
        else:
            key = None
            rgba = _as_rgba(icon)
        if rgba is None:
            return None
        h0, w0 = rgba.shape[:2]
        s = px / float(max(h0, w0))
        rgba = _resize_rgba(rgba, max(1, int(round(w0 * s))), max(1, int(round(h0 * s))))
        val = _premultiply(rgba, tint=tkey)
        if key is not None:
            self._sprite_cache.put(key, val)
        return val

    def _draw_sprite(self, img: np.ndarray, icon: str | np.ndarray, u: float, v: float,
                     diameter: float, tint: tuple | None = None, opacity: float = 1.0) -> None:
        """Draw an icon centred on (u, v) with a normalized diameter."""
        if not _finite(u, v, diameter, opacity) or diameter <= 0 or opacity <= 0:
            return
        S = img.shape[1]
        px = int(round(diameter * S))
        if px < 1:
            return
        spr = self._sprite(icon, px, tint)
        if spr is None:
            return
        bgr_p, inv_a = spr
        if opacity < 1.0:
            op = float(opacity)
            bgr_p = (bgr_p - 0.5) * op + 0.5
            inv_a = 1.0 - (1.0 - inv_a) * op
        h, w = inv_a.shape[:2]
        x0 = int(math.floor(u * S - w / 2.0 + 0.5))
        y0 = int(math.floor(v * img.shape[0] - h / 2.0 + 0.5))
        _blit_premul(img, bgr_p, inv_a, x0, y0)

    # ---------------------------------------------------------------- layers
    def _apply_fog(self, img: np.ndarray, scene: Scene, size: int) -> np.ndarray:
        fog_alpha = float(scene.fog_alpha) if _finite(scene.fog_alpha) else 0.0
        if fog_alpha <= 0.0:
            return img
        fog_name = _normalize_png_name(scene.fog_texture) if scene.fog_texture else \
            fog_for_texture(scene.texture)
        fog = self._fog(fog_name, size)
        if fog is None:
            return img
        col, k0 = fog
        g = int(min(size, FOG_MASK_RES))
        mask = np.ones((g, g), np.float32)
        for circ in scene.vision or ():
            try:
                cu, cv_, cr = (float(c) for c in circ[:3])
            except (TypeError, ValueError):
                continue
            if not _finite(cu, cv_, cr) or cr <= 0:
                continue
            cv2.circle(mask, (int(round(cu * g * 16 - 8)), int(round(cv_ * g * 16 - 8))),
                       int(round(cr * g * 16)), 0.0, -1, cv2.LINE_AA, 4)
        sigma = FOG_FEATHER * g
        if sigma > 0.3:
            mask = cv2.GaussianBlur(mask, (0, 0), sigma)
        if g != size:
            mask = cv2.resize(mask, (size, size), interpolation=cv2.INTER_LINEAR)
        w = cv2.multiply(k0, mask, scale=fog_alpha)
        np.minimum(w, FOG_MAX_DARKEN, out=w)
        return cv2.blendLinear(img, col, 1.0 - w, w)

    def _draw_structures(self, img: np.ndarray, scene: Scene) -> None:
        destroyed = set()
        for item in scene.destroyed or ():
            if isinstance(item, (int, np.integer)) and 0 <= int(item) < len(STRUCTURE_IDS):
                destroyed.add(STRUCTURE_IDS[int(item)])
            else:
                destroyed.add(str(item))
        scale = float(scene.structure_scale) if _finite(scene.structure_scale) else 1.0
        # nexus / inhibitors first, turrets on top
        order = sorted(range(len(STRUCTURES)), key=lambda i: STRUCTURES[i][2] == "turret")
        for i in order:
            u, v, kind, team = STRUCTURES[i]
            if STRUCTURE_IDS[i] in destroyed:
                continue
            rel = "ally" if team == scene.my_team else "enemy"
            self._draw_sprite(img, STRUCTURE_ICON.get(kind, STRUCTURE_ICON["turret"]), u, v,
                              STRUCTURE_SIZE.get(kind, 0.04) * scale, tint=TEAM_BGR[rel])

    def _draw_camps(self, img: np.ndarray, scene: Scene) -> None:
        if scene.camp_icons is not None:
            for item in scene.camp_icons:
                try:
                    u, v, name = item[0], item[1], str(item[2])
                except (TypeError, IndexError):
                    continue
                size = item[3] if len(item) > 3 else CAMP_SIZE_DEFAULT
                self._draw_sprite(img, name, u, v, float(size))
            return
        for u, v, name in CAMPS:
            self._draw_sprite(img, CAMP_ICON.get(name, "camp.png"), u, v,
                              CAMP_SIZE.get(name, CAMP_SIZE_DEFAULT))

    def _draw_minions(self, img: np.ndarray, scene: Scene) -> None:
        S = img.shape[1]
        r_px = max(0.8, float(scene.minion_r) * S) if _finite(scene.minion_r) else MINION_R * S
        sh = 4
        mul = 1 << sh
        for item in scene.minions or ():
            try:
                u, v, team = float(item[0]), float(item[1]), str(item[2])
            except (TypeError, ValueError, IndexError):
                continue
            if not _finite(u, v):
                continue
            rr = r_px * (float(item[3]) if len(item) > 3 else 1.0)
            c = (int(round((u * S - 0.5) * mul)), int(round((v * S - 0.5) * mul)))
            col = MINION_BGR.get(team, MINION_BGR["enemy"])
            cv2.circle(img, c, int(round((rr + 0.6) * mul)), MINION_OUTLINE_BGR, -1, cv2.LINE_AA, sh)
            cv2.circle(img, c, int(round(max(0.5, rr - 0.2) * mul)), col, -1, cv2.LINE_AA, sh)

    def _draw_camera(self, img: np.ndarray, scene: Scene) -> None:
        cam = scene.camera
        if cam is None:
            return
        try:
            u0, v0, u1, v1 = (float(c) for c in cam[:4])
        except (TypeError, ValueError):
            return
        if not _finite(u0, v0, u1, v1):
            return
        S = img.shape[1]
        th = max(1, int(round(CAMERA_THICKNESS * S)))
        col = tuple(int(c) for c in (scene.camera_bgr or CAMERA_BGR)[:3])
        p0 = (int(round(min(u0, u1) * S)), int(round(min(v0, v1) * S)))
        p1 = (int(round(max(u0, u1) * S)) - 1, int(round(max(v0, v1) * S)) - 1)
        lim = 4 * S
        p0 = (max(-lim, min(lim, p0[0])), max(-lim, min(lim, p0[1])))
        p1 = (max(-lim, min(lim, p1[0])), max(-lim, min(lim, p1[1])))
        cv2.rectangle(img, p0, p1, col, th, cv2.LINE_8)

    def _draw_champions(self, img: np.ndarray, scene: Scene) -> None:
        S = img.shape[1]
        champs = [c for c in (scene.champions or ()) if isinstance(c, ChampionSprite)]
        # greyed (dead) first, the local player last (on top); stable otherwise
        champs.sort(key=lambda c: (0 if c.grey else 2 if c.relation == "self" else 1))
        for c in champs:
            if not _finite(c.u, c.v, c.r) or c.r <= 0:
                continue
            cx, cy, rp = c.u * S, c.v * img.shape[0], c.r * S
            rel = c.relation if c.relation in RING_BGR else "enemy"
            if c.teleport:
                self._draw_sprite(img, TELEPORT_ICON[rel], c.u, c.v, 2 * c.r * TELEPORT_SCALE)
            icon = c.icon
            if _as_rgba(icon) is None:
                icon = self.icon(DUMMY_ICON[rel])
            ring = c.ring_bgr if c.ring_bgr is not None else RING_BGR[rel]
            rf = c.ring_frac if c.ring_frac is not None and _finite(c.ring_frac) else RING_FRAC
            draw_champion_icon(img, cx, cy, rp, icon, ring, rf, grey=bool(c.grey))
            if c.recall:
                self._draw_sprite(img, RECALL_ICON[rel], c.u, c.v, 2 * c.r * RECALL_SCALE)

    # ---------------------------------------------------------------- public
    def render(self, scene: Scene) -> np.ndarray:
        """Render ``scene`` to a BGR ``uint8`` image of ``scene.size`` x ``scene.size``.

        Missing assets fall back to simpler drawings; a failing layer is skipped and logged.
        """
        try:
            size = int(scene.size)
        except (TypeError, ValueError, AttributeError):
            size = 256
        size = max(_MIN_SIZE, min(_MAX_SIZE, size))
        void = tuple(int(c) for c in (getattr(scene, "void_bgr", None) or VOID_BGR)[:3])
        texture = _normalize_png_name(getattr(scene, "texture", None) or DEFAULT_TEXTURE)
        try:
            img = self._base(texture, size, void).copy()
        except Exception:
            log.exception("Texture rendering failed")
            img = np.empty((size, size, 3), np.uint8)
            img[:] = void
        steps: list[tuple[str, Any]] = [
            ("fog", None),
            ("camps", lambda: scene.camps and self._draw_camps(img, scene)),
            ("structures", lambda: scene.structures and self._draw_structures(img, scene)),
            ("wards", lambda: [self._draw_sprite(img, w[2], w[0], w[1], WARD_SIZE)
                               for w in (scene.wards or ()) if len(w) >= 3]),
            ("sprites_under", lambda: [self._draw_sprite(img, s.icon, s.u, s.v, s.size, s.tint,
                                                         s.opacity)
                                       for s in (scene.sprites or ()) if s.layer != "over"]),
            ("minions", lambda: self._draw_minions(img, scene)),
            ("camera", lambda: (not scene.camera_on_top) and self._draw_camera(img, scene)),
            ("champions", lambda: self._draw_champions(img, scene)),
            ("camera_top", lambda: scene.camera_on_top and self._draw_camera(img, scene)),
            ("pings", lambda: [self._draw_sprite(img, p[2], p[0], p[1], PING_SIZE)
                               for p in (scene.pings or ()) if len(p) >= 3]),
            ("sprites_over", lambda: [self._draw_sprite(img, s.icon, s.u, s.v, s.size, s.tint,
                                                        s.opacity)
                                      for s in (scene.sprites or ()) if s.layer == "over"]),
        ]
        for name, fn in steps:
            try:
                if name == "fog":
                    img = self._apply_fog(img, scene, size)
                else:
                    fn()
            except Exception:
                log.exception("Minimap layer %r failed", name)
        return img

    def clear_caches(self) -> None:
        """Drop resized assets (textures and icons stay loaded)."""
        self._pyramid_cache.clear()
        self._base_cache.clear()
        self._fog_cache.clear()
        self._sprite_cache.clear()


def _procedural_map(size: int, void: tuple) -> np.ndarray:
    """Very simple stand-in map (lanes, river, bases) when no texture file is available."""
    img = np.empty((size, size, 3), np.uint8)
    img[:] = void[:3]
    s = size
    khaki = (120, 170, 165)
    t = max(2, int(0.06 * s))
    o = int(0.082 * s)
    cv2.line(img, (o, s - o), (o, o), khaki, t)
    cv2.line(img, (o, o), (s - o, o), khaki, t)
    cv2.line(img, (o, s - o), (s - o, s - o), khaki, t)
    cv2.line(img, (s - o, s - o), (s - o, o), khaki, t)
    cv2.line(img, (o, s - o), (s - o, o), khaki, t)
    cv2.line(img, (int(0.15 * s), int(0.15 * s)), (int(0.85 * s), int(0.85 * s)),
             (160, 120, 20), max(2, int(0.05 * s)))
    cv2.circle(img, (0, s), int(0.38 * s), (140, 130, 110), -1)
    cv2.circle(img, (s, 0), int(0.38 * s), (140, 130, 110), -1)
    return img


def iter_structures(team: str | None = None, kind: str | None = None
                    ) -> Iterable[tuple[str, float, float, str, str]]:
    """``(id, u, v, kind, team)`` of the structures, optionally filtered."""
    for sid, (u, v, k, t) in zip(STRUCTURE_IDS, STRUCTURES):
        if (team is None or t == team) and (kind is None or k == kind):
            yield sid, u, v, k, t
