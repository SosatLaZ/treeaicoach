"""Rendering of League of Legends minimaps (shared by training, demo mode and selftest).

A :class:`Scene` describes what is on the minimap (texture variant, fog of war and vision,
champions, minions, structures, camps, wards, pings, camera rectangle...) in *normalized
minimap coordinates* ``(u, v)`` (``u`` left -> right, ``v`` top -> bottom, radii normalized
by the minimap width). :class:`MinimapRenderer` turns it into a BGR ``uint8`` image that
mimics the in-game minimap (see ``docs/MINIMAP_FACTS.md``, measured on real captures):

* the whole 512 px ``2dlevelminimap_*`` texture (black margins included) scaled to the
  square, its transparent parts (walls / void) shown almost black (:data:`VOID_BGR`);
* fog of war = uniform multiplication (~0.36) of everything outside the vision circles,
  with soft edges (optionally an official ``fogofwaroverlay*`` texture instead);
* team-tinted structure icons at their official positions (:data:`STRUCTURES`), jungle
  camps (:data:`CAMPS`), the shop icon at the allied fountain, wards, minion dots, the white
  camera rectangle, the white movement path;
* champion icons: round portrait + thin dark line + coloured ring (:data:`RING_BGR`; the
  local player's ring is the *same* light blue as the allies'); cyan recall halo;
  teleport highlight; pings on top.

Every visual constant is a plain module-level value so it can be re-tuned from real
screenshots without touching the code. Images are BGR ``uint8``; icons are RGBA ``uint8``.

Pixel convention: a pixel ``i`` covers the continuous interval ``[i, i + 1)``, so the
continuous centre of an icon at ``u`` in an image of width ``W`` is ``u * W``.
"""

from __future__ import annotations

import functools
import logging
import math
import os
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import cv2
import numpy as np

log = logging.getLogger(__name__)

BGR = tuple[int, int, int]

# ======================================================================================
# Tunable visual constants (normalized sizes are fractions of the minimap width).
# Values come from docs/MINIMAP_FACTS.md (real captures, 2022-2026 clients).
# ======================================================================================

#: Colour shown where the texture is transparent (walls ~ RGB(1-3, 2-9, 2-10)).
VOID_BGR: BGR = (6, 5, 2)

#: Champion ring colours per relation. The local player has NO special ring: same light
#: blue as the allies (measured ally ~ RGB(75-81, 140-162, 200-230), enemy ~ RGB(195-204, 51, 51)).
RING_BGR: dict[str, BGR] = {
    "enemy": (51, 51, 200),
    "ally": (218, 152, 78),
    "self": (218, 152, 78),
}
#: Paler blue-lavender ally ring seen on 2024 clients (~ RGB(133-183, 143-187, 162-204)).
RING_BGR_ALLY_PALE: BGR = (183, 165, 158)
#: Ring thickness / icon radius (1.5-2 px for a 27 px icon).
RING_FRAC: float = 0.13
#: Thin dark line between the ring and the portrait (~ RGB(30-65, 37-75, 55-130)).
INNER_LINE_BGR: BGR = (95, 56, 45)
INNER_LINE_FRAC: float = 0.06       # width / icon radius
INNER_LINE_MIN_PX: float = 0.5
#: Optional dark outline outside the ring (not observed on real captures -> width 0).
OUTLINE_BGR: BGR = (12, 10, 10)
OUTLINE_FRAC: float = 0.0           # outline width / icon radius
OUTLINE_MIN_PX: float = 0.0
#: Part of the (square) portrait image covered by the visible disc: the portrait is zoomed
#: slightly so that its own anti-aliased transparent border hides under the ring.
PORTRAIT_FILL: float = 0.94
#: "Dead" / greyed icon look.
GREY_RING_BGR: BGR = (110, 110, 110)
GREY_ICON_DIM: float = 0.55
#: Default champion icon radius (demo / selftest). Real: diameter 0.088-0.10 of the width.
ICON_RADIUS_DEFAULT: float = 0.047

#: Fog of war darkening (``Scene.fog_alpha``): pixels outside vision are multiplied by
#: ``1 - fog_alpha`` (measured ~0.36 -> 0.64).
FOG_ALPHA_DEFAULT: float = 0.64
FOG_MAX_DARKEN: float = 0.95
#: When a fog overlay texture is used, its alpha is normalized by this value.
FOG_TEX_REF_ALPHA: float = 88.0
#: Gaussian feathering of the vision circle edges (sigma, normalized).
FOG_FEATHER: float = 0.012
#: Resolution of the vision mask (computed small, then upsampled).
FOG_MASK_RES: int = 96
#: Typical vision radii (normalized) - used by demo / synth to build ``Scene.vision``.
VISION_R: dict[str, float] = {"champion": 0.08, "turret": 0.09, "ward": 0.06, "minion": 0.035}

#: Team tints of structures (team-relative colours: ally blue, enemy red).
TEAM_BGR: dict[str, BGR] = {"ally": (230, 170, 90), "enemy": (70, 70, 225)}
#: Minion dots (diameter ~0.018 of the width), light blue / red with a dark contour.
MINION_R: float = 0.009
MINION_BGR: dict[str, BGR] = {"ally": (228, 165, 90), "enemy": (55, 55, 205)}
MINION_OUTLINE_BGR: BGR = (20, 16, 14)

#: Structure icons (diameter, normalized: turrets 0.04-0.045) and files.
STRUCTURE_SIZE: dict[str, float] = {"turret": 0.043, "inhibitor": 0.036, "nexus": 0.05}
STRUCTURE_ICON: dict[str, str] = {
    "turret": "icon_ui_tower_minimap.png",
    "inhibitor": "icon_ui_inhibitor_minimap_v2.png",
    "nexus": "icon_ui_nexus_minimap_v2.png",
}
#: Outer turrets show a numbered badge (remaining plates, until 14:00): dark shield with a
#: team-coloured border and digit. Official glyphs exist for 1, 3 and 5 plates; the other
#: counts are derived from the 5-plate glyph. Size relative to the plain turret glyph.
PLATE_ICON: str = "turret_{n}plate.png"
PLATE_SCALE: float = 1.2
#: Jungle camps: orange diamonds ~0.022 of the width; epic monsters: bigger glyphs.
CAMP_SIZE: dict[str, float] = {"dragon": 0.05, "baron": 0.052}
CAMP_SIZE_DEFAULT: float = 0.022
CAMP_ICON: dict[str, str] = {"dragon": "dragon.png", "baron": "baron.png"}
CAMP_ICON_DEFAULT: str = "smallcamp.png"
#: Shop icon at the allied fountain corner.
SHOP_ICON: str = "shop.png"
SHOP_SIZE: float = 0.05
#: Wards: allied glyphs ~0.036 of the width; enemy wards: small red dots ~0.02.
WARD_SIZE: float = 0.036
ENEMY_WARD_DOT: str = "enemy_dot"
ENEMY_WARD_DOT_SIZE: float = 0.02
ENEMY_WARD_DOT_BGR: BGR = (60, 45, 215)
#: Pings: symbol glyphs and pulsing rings (~0.10 of the width).
PING_SIZE: float = 0.045
PING_RING_SIZE: float = 0.10
PING_BGR: dict[str, BGR] = {
    "generic": (253, 188, 33), "enemy_vision": (67, 34, 248),
    "caution": (15, 190, 245), "assist": (128, 215, 9),
}
#: Recall: bright cyan halo (~RGB(106-113, 180, 201-214)), 2-4 px, at 1.1-1.25 x the radius.
RECALL_BGR: BGR = (208, 180, 110)
#: Recall of a visible enemy (``recallhostileoutline``: red / pink), not verified on captures.
RECALL_HOSTILE_BGR: BGR = (110, 60, 235)
RECALL_RADIUS: float = 1.17         # ring centre radius / icon radius
RECALL_WIDTH: float = 0.2           # ring width / icon radius
#: Teleport highlight (not verified on real captures), relative to the icon diameter.
TELEPORT_SCALE: float = 1.5
TELEPORT_ICON: dict[str, str] = {
    "ally": "teleporthighlight_friendly.png", "self": "teleporthighlight_friendly.png",
    "enemy": "teleporthighlight_enemy.png",
}
DUMMY_ICON: dict[str, str] = {
    "ally": "dummy_friendly_circle.png", "self": "dummy_friendly_circle.png",
    "enemy": "dummy_enemy_circle.png",
}
#: Camera rectangle: white, ~0.007 of the width thick; 0.272-0.279 x 0.151-0.158 in size.
CAMERA_BGR: BGR = (245, 245, 245)
CAMERA_THICKNESS: float = 0.007     # normalized, at least 1 px
CAMERA_SIZE: tuple[float, float] = (0.275, 0.155)
#: Movement path (white line between my icon and the clicked point).
PATH_BGR: BGR = (235, 235, 235)
#: Local-player glowing outline (teal / cyan, BGR).
SELF_GLOW_BGR: BGR = (225, 235, 110)
#: Small white texts (epic camp timers, "1:17").
TEXT_BGR: BGR = (250, 250, 250)
TEXT_HEIGHT: float = 0.03           # normalized glyph height

#: Textures.
TEXTURE_PREFIX: str = "2dlevelminimap_"
DEFAULT_TEXTURE: str = "2dlevelminimap_base_baron1.png"
DEFAULT_FOG: str = "fogofwaroverlay.png"
#: Fog overlay matching each texture variant (``2dlevelminimap_<variant>_baron<n>.png``).
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
_UNIFORM_FOG = "__uniform__"


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
#: Default plate badges (``Scene.turret_plates is None``): 5 plates on every outer turret.
DEFAULT_TURRET_PLATES: dict[str, int] = {sid: 5 for sid in STRUCTURE_IDS if sid.endswith("_outer")}

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

#: Fountains (spawn platforms, the coloured discs in the texture corners), normalized.
FOUNTAINS: dict[str, tuple[float, float]] = {"ORDER": (0.045, 0.955), "CHAOS": (0.955, 0.045)}


# ======================================================================================
# Scene description
# ======================================================================================


@dataclass
class ChampionSprite:
    """A champion icon on the minimap.

    ``relation`` is ``"self" | "ally" | "enemy"``; ``r`` is the normalized outer radius of
    the ring; ``icon`` is the round portrait (RGBA, None -> neutral placeholder);
    ``ring_bgr`` overrides the ring colour (None -> :data:`RING_BGR`); ``label_class`` is
    the class used for training labels (None -> class not labelled, e.g. random-hue ring).

    Optional extensions: ``grey`` greyed "dead" icon; ``teleport`` teleport highlight;
    ``ring_frac`` / ``ring_shading`` / ``inner_line_bgr`` / ``inner_line_frac`` /
    ``outline_frac`` override the icon style (see :func:`draw_champion_icon`); ``halo``
    (0..1) adds a soft glow of the ring colour just outside the ring; ``recall_bgr``
    overrides the recall halo colour (None -> cyan, red for enemies); ``self_glow`` (0..1)
    draws the local player's thick teal glowing outline (``glow_bgr`` overrides it).
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
    ring_shading: float = 0.0
    inner_line_bgr: tuple | None = None
    inner_line_frac: float | None = None
    outline_frac: float | None = None
    halo: float = 0.0
    recall_bgr: tuple | None = None
    #: Local-player highlight (0..1): thick bright teal / cyan glowing outline drawn
    #: around the icon (seen on real 2025+ clients); ``glow_bgr`` overrides its colour.
    self_glow: float = 0.0
    glow_bgr: tuple | None = None


@dataclass
class Sprite:
    """Any other icon (hard negatives, objectives, ping rings, badges...).

    ``icon`` is a file name (searched in ``icons/minimap`` then ``icons/pings``) or an RGBA
    array; ``size`` is the normalized diameter (largest side); ``tint`` multiplies the
    colours (BGR); ``layer`` is ``"under"`` (below minions and champions) or ``"over"``
    (above everything, like pings).
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
    """Everything drawn on one minimap. All coordinates normalized (see module doc).

    The first fields are the documented contract; the others are optional extensions whose
    defaults keep the documented behaviour.
    """

    texture: str = DEFAULT_TEXTURE                          # file name in assets/minimap
    size: int = 256                                         # output size (px)
    fog_alpha: float = FOG_ALPHA_DEFAULT                    # darkening outside vision, 0 = none
    vision: list[tuple[float, float, float]] = field(default_factory=list)  # (u, v, radius)
    champions: list[ChampionSprite] = field(default_factory=list)
    minions: list[tuple] = field(default_factory=list)      # (u, v, "ally"|"enemy"[, size factor])
    structures: bool = True                                 # turrets / inhibitors / nexus (+ shop)
    wards: list[tuple] = field(default_factory=list)        # (u, v, icon name | "enemy_dot"[, size])
    pings: list[tuple] = field(default_factory=list)        # (u, v, ping icon name[, size])
    camera: tuple[float, float, float, float] | None = None  # (u0, v0, u1, v1), white
    camps: bool = True
    # ---- optional extensions --------------------------------------------------------
    my_team: str = "ORDER"                  # local player's team (structure colours, shop)
    destroyed: frozenset = frozenset()      # STRUCTURE_IDS (or indices) not drawn
    #: Plate badges {structure id: remaining plates (0 = no badge)} on turrets;
    #: None -> DEFAULT_TURRET_PLATES (5 on the outer turrets), {} -> no badge at all.
    turret_plates: dict | None = None
    fog_texture: str | None = None          # None -> uniform darkening (real game)
    camp_icons: list[tuple] | None = None   # explicit (u, v, icon[, size]); None -> CAMPS
    sprites: list[Sprite] = field(default_factory=list)
    texts: list[tuple] = field(default_factory=list)        # (u, v, text[, height[, thick[, bgr]]])
    path: list[tuple[float, float]] | None = None           # movement path polyline (white)
    shop: bool = True                                       # shop icon at the allied fountain
    structure_scale: float = 1.0
    minion_r: float = MINION_R
    camera_bgr: tuple | None = None
    camera_px: int | None = None            # camera line width in px (None -> CAMERA_THICKNESS)
    camera_on_top: bool = False             # camera rectangle above the champion icons
    void_bgr: tuple | None = None
    #: Optional ``f(terrain_bgr) -> terrain_bgr`` applied after the fog, before any icon
    #: (training augmentation of the map background). Must keep shape and dtype.
    terrain_fn: Callable[[np.ndarray], np.ndarray] | None = None


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
    try:
        return cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    except cv2.error:
        return None


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
        return cv2.cvtColor(np.ascontiguousarray(img[:, :, 0]), cv2.COLOR_GRAY2RGBA)
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
    if src.ndim == 3 and src.shape[2] == 4 and src.dtype == np.uint8 and src.flags.c_contiguous:
        return src
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
        and dst.flags.writeable
    )


def _finite(*vals: Any) -> bool:
    try:
        for v in vals:
            if not math.isfinite(float(v)):
                return False
        return True
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
        x0 = int(math.floor(cx - w / 2.0 + 0.5))
        y0 = int(math.floor(cy - h / 2.0 + 0.5))
        H, W = dst_bgr.shape[:2]
        if x0 >= W or y0 >= H or x0 + w <= 0 or y0 + h <= 0:
            return
        rgba = _resize_rgba(rgba, w, h)
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
    xs = np.arange(x0, x1, dtype=np.float32) + np.float32(0.5 - cx)
    ys = np.arange(y0, y1, dtype=np.float32) + np.float32(0.5 - cy)
    d = np.sqrt(xs[None, :] ** 2 + ys[:, None] ** 2)
    return x0, y0, x1, y1, d


def _portrait_layer(icon_rgba: np.ndarray, r_in: float, cx: float, cy: float,
                    x0: int, y0: int, w: int, h: int) -> tuple[np.ndarray, np.ndarray]:
    """Portrait resampled onto the patch grid: (BGR float32 [h,w,3], alpha float32 [h,w])."""
    sh, sw = icon_rgba.shape[:2]
    target_r = max(0.5, r_in + 0.5)
    k = 0.5 * min(sh, sw) * PORTRAIT_FILL / target_r
    if k > 1.5:  # pre-shrink with area filtering so that the final warp is ~1:1
        nw = max(2, int(round(sw / k)))
        nh = max(2, int(round(sh / k)))
        icon_rgba = cv2.resize(icon_rgba, (nw, nh), interpolation=cv2.INTER_AREA)
        sh, sw = nh, nw
        k = 0.5 * min(sh, sw) * PORTRAIT_FILL / target_r
    # inverse map: dst pixel j (continuous centre x0 + j + 0.5) -> source pixel index
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


def _ramp(edge: float, d: np.ndarray) -> np.ndarray:
    """Anti-aliased coverage ``clip(edge - d, 0, 1)`` (new array; faster than np.clip)."""
    a = np.subtract(np.float32(edge), d)
    np.maximum(a, 0.0, out=a)
    return np.minimum(a, 1.0, out=a)


def _to_u8(patch: np.ndarray) -> np.ndarray:
    """Round a float patch to uint8 with saturation (in place on ``patch``)."""
    patch += 0.5
    np.maximum(patch, 0.0, out=patch)
    np.minimum(patch, 255.0, out=patch)
    return patch.astype(np.uint8)


def _lerp_(patch: np.ndarray, color: np.ndarray, alpha: np.ndarray) -> None:
    """In place: ``patch += (color - patch) * alpha`` (alpha [h,w])."""
    patch += (color - patch) * alpha[:, :, None]


def draw_champion_icon(dst_bgr: np.ndarray, cx: float, cy: float, radius_px: float,
                       icon_rgba: np.ndarray | None, ring_bgr: Sequence[int] | None,
                       ring_frac: float = RING_FRAC, grey: bool = False, *,
                       outline_bgr: Sequence[int] | None = OUTLINE_BGR,
                       outline_px: float | None = None, ring_shading: float = 0.0,
                       inner_line_bgr: Sequence[int] | None = INNER_LINE_BGR,
                       inner_line_px: float | None = None) -> None:
    """Draw a minimap champion icon in place: coloured ring + thin dark line + portrait.

    ``(cx, cy)`` is the continuous centre in pixels and ``radius_px`` the outer radius of
    the ring. ``ring_frac`` is the ring width / radius. Optional: a dark outline outside
    the ring (``outline_px``, default ``OUTLINE_FRAC * radius``), the dark line between ring
    and portrait (``inner_line_px``, default ``INNER_LINE_FRAC * radius``; ``inner_line_bgr``
    None disables it), ``ring_shading`` (0..1) for a subtle top-light / bottom-dark bevel.
    ``grey`` renders a greyed "dead" icon. Invalid inputs are ignored (never raises).
    """
    try:
        if not _valid_dst(dst_bgr) or not _finite(cx, cy, radius_px, ring_frac):
            return
        R = float(radius_px)
        if R < 0.5 or R > 2 * _MAX_SIZE:
            return
        rf = min(0.9, max(0.0, float(ring_frac)))
        ow = float(outline_px) if outline_px is not None and _finite(outline_px) else \
            max(OUTLINE_MIN_PX, R * OUTLINE_FRAC)
        if outline_bgr is None:
            ow = 0.0
        ow = max(0.0, ow)
        patch_info = _disc_patch(dst_bgr, cx, cy, R + ow)
        if patch_info is None:
            return
        x0, y0, x1, y1, d = patch_info
        patch = dst_bgr[y0:y1, x0:x1].astype(np.float32)

        # 1) optional outline
        if ow > 0.05:
            _lerp_(patch, np.asarray(outline_bgr, np.float32)[:3],
                   _ramp(R + ow + 0.5, d))
        # 2) ring disc
        ring = np.asarray(GREY_RING_BGR if grey else (ring_bgr if ring_bgr is not None
                                                      else RING_BGR["enemy"]), np.float32)[:3]
        a_ring = _ramp(R + 0.5, d)
        if ring_shading and _finite(ring_shading) and not grey:
            ys = (np.arange(y0, y1, dtype=np.float32) + np.float32(0.5 - cy)) / np.float32(R)
            shade = 1.0 - float(ring_shading) * np.clip(ys, -1.0, 1.0)
            ring_img = np.clip(ring[None, None, :] * shade[:, None, None], 0, 255)
            patch += (ring_img - patch) * a_ring[:, :, None]
        else:
            _lerp_(patch, ring, a_ring)
        # 3) thin dark line between ring and portrait
        r_in = R * (1.0 - rf)
        if inner_line_bgr is not None and r_in > 1.0:
            il = float(inner_line_px) if inner_line_px is not None and _finite(inner_line_px) \
                else max(INNER_LINE_MIN_PX, R * INNER_LINE_FRAC)
            il = min(max(0.0, il), 0.5 * r_in)
            if il > 0.05:
                line = np.asarray((70, 70, 70) if grey else inner_line_bgr, np.float32)[:3]
                _lerp_(patch, line, _ramp(r_in + 0.5, d))
                r_in -= il
        # 4) portrait
        rgba = _as_rgba(icon_rgba) if icon_rgba is not None else None
        if r_in >= 0.5:
            cover = _ramp(r_in + 0.5, d)
            if rgba is not None:
                p_bgr, p_a = _portrait_layer(rgba, r_in, cx, cy, x0, y0, x1 - x0, y1 - y0)
                if grey:
                    g = cv2.cvtColor(np.clip(p_bgr, 0, 255).astype(np.uint8), cv2.COLOR_BGR2GRAY)
                    p_bgr = np.repeat(g[:, :, None].astype(np.float32) * GREY_ICON_DIM, 3, axis=2)
                cover = cover * p_a
            else:  # no portrait: dark neutral disc
                p_bgr = np.full(patch.shape, 45.0, np.float32)
            patch += (p_bgr - patch) * cover[:, :, None]
        dst_bgr[y0:y1, x0:x1] = _to_u8(patch)
    except Exception:
        log.exception("draw_champion_icon failed")


def draw_ring(dst_bgr: np.ndarray, cx: float, cy: float, radius_px: float, width_px: float,
              bgr: Sequence[int], opacity: float = 1.0, glow: float = 0.0) -> None:
    """Draw an anti-aliased ring (centre radius ``radius_px``) with an optional soft glow.

    Used for recall halos and ping-like rings. Invalid inputs are ignored (never raises).
    """
    try:
        if not _valid_dst(dst_bgr) or not _finite(cx, cy, radius_px, width_px, opacity, glow):
            return
        R, wd = float(radius_px), max(0.3, float(width_px))
        if R <= 0 or R > 4 * _MAX_SIZE or opacity <= 0:
            return
        g = max(0.0, float(glow)) * wd
        info = _disc_patch(dst_bgr, cx, cy, R + wd / 2 + 2 * g + 1)
        if info is None:
            return
        x0, y0, x1, y1, d = info
        dist = np.abs(d - np.float32(R))
        a = np.clip(wd / 2 + 0.5 - dist, 0.0, 1.0)
        if g > 0:
            a = np.maximum(a, 0.45 * np.exp(-np.maximum(dist - wd / 2, 0.0) / g))
        a *= min(1.0, float(opacity))
        patch = dst_bgr[y0:y1, x0:x1].astype(np.float32)
        _lerp_(patch, np.asarray(bgr, np.float32)[:3], a)
        dst_bgr[y0:y1, x0:x1] = _to_u8(patch)
    except Exception:
        log.exception("draw_ring failed")


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


@functools.lru_cache(maxsize=1024)
def _normalize_png_name_str(name: str) -> str:
    name = os.path.basename(name.strip())
    return name if name.lower().endswith(".png") else name + ".png"


def _normalize_png_name(name: Any) -> str:
    """``"foo"`` / ``"dir/foo.png"`` -> ``"foo.png"`` (cached)."""
    return _normalize_png_name_str(str(name))


@functools.lru_cache(maxsize=256)
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
        self._pyramid_cache = _LRU(40)   # (kind, name, void) -> list of levels
        self._base_cache = _LRU(48)      # (texture, size, void) -> uint8 BGR
        self._fog_cache = _LRU(48)       # (fog, size) -> (fog colour uint8, k0 float32)
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
        """A minimap / ping icon by file name (RGBA uint8, do not modify); None if missing."""
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

    def plate_icon(self, n: int) -> str | None:
        """Icon name of a turret badge with ``n`` plates (1..9), synthesized if needed.

        The official files exist for 1, 3 and 5 plates; other digits are drawn on the
        5-plate shield. Returns None if no base glyph is available.
        """
        try:
            n = int(n)
        except (TypeError, ValueError):
            return None
        if not 1 <= n <= 9:
            return None
        name = PLATE_ICON.format(n=n)
        with self._lock:
            if name in self._raw_icons:
                return name if self._raw_icons[name] is not None else None
        try:
            exists = (self.assets_dir / "icons" / "minimap" / name).is_file()
        except OSError:
            exists = False
        if exists and self.icon(name) is not None:
            return name
        base = self.icon(PLATE_ICON.format(n=5))
        img = _redigit_badge(base, n) if base is not None else None
        with self._lock:
            self._raw_icons[name] = img
        return name if img is not None else None

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

    def _fog(self, fog_name: str, size: int) -> tuple[np.ndarray, np.ndarray]:
        """(fog colour BGR uint8 [S,S,3], relative strength k0 float32 [S,S]) at ``size``."""
        key = (fog_name, size)
        cached = self._fog_cache.get(key)
        if cached is not None:
            return cached
        levels = None if fog_name == _UNIFORM_FOG else self._pyramid("fog", fog_name, (0, 0, 0))
        if not levels:  # uniform multiplication (the real game)
            col = np.zeros((size, size, 3), np.uint8)
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
            key: tuple | None = (_normalize_png_name(icon), px, tkey)
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

    def _draw_sprite(self, img: np.ndarray, icon: str | np.ndarray, u: Any, v: Any,
                     diameter: Any, tint: tuple | None = None, opacity: Any = 1.0) -> None:
        """Draw an icon centred on (u, v) with a normalized diameter (bad input ignored)."""
        if not _finite(u, v, diameter, opacity):
            return
        u, v, diameter, opacity = float(u), float(v), float(diameter), float(opacity)
        if diameter <= 0 or opacity <= 0 or not -1.0 < u < 2.0 or not -1.0 < v < 2.0:
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
            bgr_p = (bgr_p - 0.5) * opacity + 0.5
            inv_a = 1.0 - (1.0 - inv_a) * opacity
        h, w = inv_a.shape[:2]
        x0 = int(math.floor(u * S - w / 2.0 + 0.5))
        y0 = int(math.floor(v * img.shape[0] - h / 2.0 + 0.5))
        _blit_premul(img, bgr_p, inv_a, x0, y0)

    # ---------------------------------------------------------------- layers
    def _apply_fog(self, img: np.ndarray, scene: Scene, size: int) -> np.ndarray:
        fog_alpha = float(scene.fog_alpha) if _finite(scene.fog_alpha) else 0.0
        if fog_alpha <= 0.0:
            return img
        fog_name = _normalize_png_name(scene.fog_texture) if scene.fog_texture else _UNIFORM_FOG
        col, k0 = self._fog(fog_name, size)
        g = int(min(size, FOG_MASK_RES))
        mask = np.ones((g, g), np.float32)
        for circ in scene.vision or ():
            try:
                cu, cv_, cr = (float(c) for c in circ[:3])
            except (TypeError, ValueError):
                continue
            if not _finite(cu, cv_, cr) or cr <= 0 or cr > 4:
                continue
            if not -2.0 < cu < 3.0 or not -2.0 < cv_ < 3.0:
                continue
            cv2.circle(mask, (int(round(cu * g * 16 - 8)), int(round(cv_ * g * 16 - 8))),
                       int(round(cr * g * 16)), 0.0, -1, cv2.LINE_AA, 4)
        sigma = FOG_FEATHER * g
        if sigma > 0.3:
            mask = cv2.GaussianBlur(mask, (0, 0), sigma)
        if fog_name == _UNIFORM_FOG:
            # fast path (the real game): img * (1 - w), computed in uint8 at full size
            w = np.minimum(mask * min(fog_alpha, 1.0), FOG_MAX_DARKEN)
            f8 = np.clip((1.0 - w) * 255.0 + 0.5, 0, 255).astype(np.uint8)
            if g != size:
                f8 = cv2.resize(f8, (size, size), interpolation=cv2.INTER_LINEAR)
            return cv2.multiply(img, cv2.cvtColor(f8, cv2.COLOR_GRAY2BGR), scale=1.0 / 255.0)
        if g != size:
            mask = cv2.resize(mask, (size, size), interpolation=cv2.INTER_LINEAR)
        w = cv2.multiply(k0, mask, scale=min(fog_alpha, 1.0))
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
        plates = scene.turret_plates if isinstance(scene.turret_plates, dict) \
            else DEFAULT_TURRET_PLATES
        # nexus / inhibitors first, turrets on top
        order = sorted(range(len(STRUCTURES)), key=lambda i: STRUCTURES[i][2] == "turret")
        for i in order:
            u, v, kind, team = STRUCTURES[i]
            sid = STRUCTURE_IDS[i]
            if sid in destroyed:
                continue
            rel = "ally" if team == scene.my_team else "enemy"
            size = STRUCTURE_SIZE.get(kind, 0.04) * scale
            icon = STRUCTURE_ICON.get(kind, STRUCTURE_ICON["turret"])
            if kind == "turret":
                try:
                    n = int(plates.get(sid, 0))
                except (TypeError, ValueError):
                    n = 0
                badge = self.plate_icon(n) if n > 0 else None
                if badge is not None:
                    icon, size = badge, size * PLATE_SCALE
            self._draw_sprite(img, icon, u, v, size, tint=TEAM_BGR[rel])
        if scene.shop:
            fu, fv = FOUNTAINS.get(scene.my_team, FOUNTAINS["ORDER"])
            self._draw_sprite(img, SHOP_ICON, fu, fv, SHOP_SIZE * scale)

    def _draw_camps(self, img: np.ndarray, scene: Scene) -> None:
        if scene.camp_icons is not None:
            for item in scene.camp_icons:
                try:
                    u, v, name = item[0], item[1], item[2]
                    size = item[3] if len(item) > 3 else CAMP_SIZE_DEFAULT
                except (TypeError, IndexError):
                    continue
                self._draw_sprite(img, name, u, v, size)
            return
        for u, v, name in CAMPS:
            self._draw_sprite(img, CAMP_ICON.get(name, CAMP_ICON_DEFAULT), u, v,
                              CAMP_SIZE.get(name, CAMP_SIZE_DEFAULT))

    def _draw_wards(self, img: np.ndarray, scene: Scene) -> None:
        S = img.shape[1]
        for w in scene.wards or ():
            try:
                u, v, name = w[0], w[1], str(w[2])
                size = w[3] if len(w) > 3 else None
            except (TypeError, IndexError):
                continue
            if name == ENEMY_WARD_DOT:
                if not _finite(u, v):
                    continue
                d = float(size) if size is not None and _finite(size) else ENEMY_WARD_DOT_SIZE
                self._dot(img, float(u) * S, float(v) * S, max(0.8, d * S / 2),
                          ENEMY_WARD_DOT_BGR, MINION_OUTLINE_BGR)
            else:
                self._draw_sprite(img, name, u, v, size if size is not None else WARD_SIZE)

    @staticmethod
    def _dot(img: np.ndarray, cx: float, cy: float, r: float, col: Sequence[int],
             outline: Sequence[int] | None) -> None:
        """Anti-aliased filled dot with a dark contour (sub-pixel centre)."""
        sh = 4
        mul = 1 << sh
        lim = 1 << 20
        c = (int(max(-lim, min(lim, round((cx - 0.5) * mul)))),
             int(max(-lim, min(lim, round((cy - 0.5) * mul)))))
        if outline is not None:
            cv2.circle(img, c, int(round((r + 0.6) * mul)), tuple(int(x) for x in outline[:3]),
                       -1, cv2.LINE_AA, sh)
        cv2.circle(img, c, int(round(max(0.5, r - 0.2) * mul)), tuple(int(x) for x in col[:3]),
                   -1, cv2.LINE_AA, sh)

    def _draw_minions(self, img: np.ndarray, scene: Scene) -> None:
        S = img.shape[1]
        base_r = float(scene.minion_r) if _finite(scene.minion_r) else MINION_R
        r_px = max(0.8, base_r * S)
        for item in scene.minions or ():
            try:
                u, v, team = float(item[0]), float(item[1]), str(item[2])
                fac = float(item[3]) if len(item) > 3 else 1.0
            except (TypeError, ValueError, IndexError):
                continue
            if not _finite(u, v, fac) or not -1.0 < u < 2.0 or not -1.0 < v < 2.0:
                continue
            self._dot(img, u * S, v * S, r_px * max(0.2, min(fac, 5.0)),
                      MINION_BGR.get(team, MINION_BGR["enemy"]), MINION_OUTLINE_BGR)

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
        th = scene.camera_px if scene.camera_px else int(round(CAMERA_THICKNESS * S))
        th = max(1, min(16, int(th)))
        col = tuple(int(c) for c in (scene.camera_bgr or CAMERA_BGR)[:3])
        lim = 4 * S
        xa = max(-lim, min(lim, int(round(min(u0, u1) * S))))
        ya = max(-lim, min(lim, int(round(min(v0, v1) * S))))
        xb = max(-lim, min(lim, int(round(max(u0, u1) * S))))
        yb = max(-lim, min(lim, int(round(max(v0, v1) * S))))
        if xb - xa < 2 or yb - ya < 2:
            return
        # line of width th drawn inside [xa, xb) x [ya, yb)
        for (px0, py0, px1, py1) in ((xa, ya, xb, ya + th), (xa, yb - th, xb, yb),
                                     (xa, ya, xa + th, yb), (xb - th, ya, xb, yb)):
            cx0, cy0 = max(0, px0), max(0, py0)
            cx1, cy1 = min(img.shape[1], px1), min(img.shape[0], py1)
            if cx0 < cx1 and cy0 < cy1:
                img[cy0:cy1, cx0:cx1] = col

    def _draw_path(self, img: np.ndarray, scene: Scene) -> None:
        pts = scene.path
        if not pts or len(pts) < 2:
            return
        S = img.shape[1]
        sh = 4
        mul = 1 << sh
        lim = 1 << 20
        poly = []
        for p in pts:
            try:
                u, v = float(p[0]), float(p[1])
            except (TypeError, ValueError, IndexError):
                continue
            if _finite(u, v):
                poly.append([max(-lim, min(lim, round((u * S - 0.5) * mul))),
                             max(-lim, min(lim, round((v * S - 0.5) * mul)))])
        if len(poly) >= 2:
            th = max(1, int(round(0.004 * S)))
            cv2.polylines(img, [np.asarray(poly, np.int32)], False, PATH_BGR, th, cv2.LINE_AA, sh)

    def _draw_texts(self, img: np.ndarray, scene: Scene) -> None:
        S = img.shape[1]
        for t in scene.texts or ():
            try:
                u, v, text = float(t[0]), float(t[1]), str(t[2])[:16]
                hgt = float(t[3]) if len(t) > 3 else TEXT_HEIGHT
                thick = max(1, min(4, int(t[4]))) if len(t) > 4 else 1
                col = tuple(int(c) for c in t[5][:3]) if len(t) > 5 else TEXT_BGR
            except (TypeError, ValueError, IndexError):
                continue
            if not _finite(u, v, hgt) or not text:
                continue
            scale = max(0.2, hgt * S / 22.0)
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
            org = (int(round(u * S - tw / 2)), int(round(v * S + th / 2)))
            cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 1,
                        cv2.LINE_AA)
            cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, col, thick,
                        cv2.LINE_AA)

    def _draw_champions(self, img: np.ndarray, scene: Scene) -> None:
        S = img.shape[1]
        champs = [c for c in (scene.champions or ()) if isinstance(c, ChampionSprite)]
        # greyed (dead) icons first; otherwise list order (no priority for the local player)
        champs.sort(key=lambda c: 0 if c.grey else 1)
        for c in champs:
            if not _finite(c.u, c.v, c.r) or c.r <= 0 or c.r > 1.0:
                continue
            if not -1.0 < c.u < 2.0 or not -1.0 < c.v < 2.0:
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
            shading = float(c.ring_shading) if _finite(c.ring_shading) else 0.0
            il_px = c.inner_line_frac * rp if c.inner_line_frac is not None \
                and _finite(c.inner_line_frac) else None
            ol_px = c.outline_frac * rp if c.outline_frac is not None \
                and _finite(c.outline_frac) else None
            glow = float(c.self_glow) if _finite(c.self_glow) else 0.0
            if glow > 0 and not c.grey:
                gcol = c.glow_bgr if c.glow_bgr is not None else SELF_GLOW_BGR
                gw = max(1.0, rp * (0.18 + 0.22 * min(1.0, glow)))
                draw_ring(img, cx, cy, rp + gw * 0.25, gw, gcol, opacity=min(1.0, 0.5 + glow),
                          glow=0.9 + glow)
                ring = gcol
            halo = float(c.halo) if _finite(c.halo) else 0.0
            if halo > 0 and not c.grey:
                draw_ring(img, cx, cy, rp + 0.35, 0.8, ring, opacity=min(1.0, halo),
                          glow=1.8)
            draw_champion_icon(img, cx, cy, rp, icon, ring, rf, grey=bool(c.grey),
                               ring_shading=shading, outline_px=ol_px,
                               inner_line_bgr=c.inner_line_bgr or INNER_LINE_BGR,
                               inner_line_px=il_px)
            if c.recall:
                rcol = c.recall_bgr if c.recall_bgr is not None else \
                    (RECALL_HOSTILE_BGR if rel == "enemy" else RECALL_BGR)
                draw_ring(img, cx, cy, rp * RECALL_RADIUS, max(1.0, rp * RECALL_WIDTH),
                          rcol, opacity=1.0, glow=0.6)

    # ---------------------------------------------------------------- public
    def render(self, scene: Scene) -> np.ndarray:
        """Render ``scene`` to a BGR ``uint8`` image of ``scene.size`` x ``scene.size``.

        Missing assets fall back to simpler drawings; a failing layer is skipped and logged,
        so this always returns an image.
        """
        try:
            size = int(scene.size)
        except (TypeError, ValueError, AttributeError, OverflowError):
            size = 256
        size = max(_MIN_SIZE, min(_MAX_SIZE, size))
        try:
            void = tuple(int(c) for c in (getattr(scene, "void_bgr", None) or VOID_BGR)[:3])
            if len(void) != 3:
                raise ValueError
        except (TypeError, ValueError):
            void = VOID_BGR
        texture = _normalize_png_name(getattr(scene, "texture", None) or DEFAULT_TEXTURE)
        try:
            img = self._base(texture, size, void).copy()
        except Exception:
            log.exception("Texture rendering failed")
            img = np.empty((size, size, 3), np.uint8)
            img[:] = void
        holder = [img]

        def fog() -> None:
            out = self._apply_fog(holder[0], scene, size)
            fn_t = getattr(scene, "terrain_fn", None)
            if fn_t is not None:
                t = fn_t(out)
                if isinstance(t, np.ndarray) and t.shape == out.shape and t.dtype == np.uint8:
                    out = np.ascontiguousarray(t)
            holder[0] = out

        def sprites(layer_over: bool) -> None:
            for s in scene.sprites or ():
                if isinstance(s, Sprite) and (s.layer == "over") == layer_over:
                    self._draw_sprite(holder[0], s.icon, s.u, s.v, s.size, s.tint, s.opacity)

        def pings() -> None:
            for p in scene.pings or ():
                if len(p) < 3:
                    continue
                name = _normalize_png_name(p[2])
                default = PING_RING_SIZE if name.startswith("ring") else PING_SIZE
                self._draw_sprite(holder[0], name, p[0], p[1], p[3] if len(p) > 3 else default)

        steps: list[tuple[str, Callable[[], Any]]] = [
            ("fog", fog),
            ("camps", lambda: scene.camps and self._draw_camps(holder[0], scene)),
            ("structures", lambda: scene.structures and self._draw_structures(holder[0], scene)),
            ("wards", lambda: self._draw_wards(holder[0], scene)),
            ("sprites_under", lambda: sprites(False)),
            ("texts", lambda: self._draw_texts(holder[0], scene)),
            ("minions", lambda: self._draw_minions(holder[0], scene)),
            ("path", lambda: self._draw_path(holder[0], scene)),
            ("camera", lambda: (not scene.camera_on_top) and self._draw_camera(holder[0], scene)),
            ("champions", lambda: self._draw_champions(holder[0], scene)),
            ("camera_top", lambda: scene.camera_on_top and self._draw_camera(holder[0], scene)),
            ("pings", pings),
            ("sprites_over", lambda: sprites(True)),
        ]
        for name, fn in steps:
            try:
                fn()
            except Exception:
                log.exception("Minimap layer %r failed", name)
        return holder[0]

    def clear_caches(self) -> None:
        """Drop resized assets (textures and icons stay loaded)."""
        self._pyramid_cache.clear()
        self._base_cache.clear()
        self._fog_cache.clear()
        self._sprite_cache.clear()


def _redigit_badge(base_rgba: np.ndarray, n: int) -> np.ndarray | None:
    """Replace the digit of a plate badge glyph (bright digit in a dark box) by ``n``."""
    try:
        img = np.array(base_rgba, dtype=np.uint8, copy=True)
        h, w = img.shape[:2]
        wy0, wy1, wx0, wx1 = int(0.36 * h), int(0.68 * h), int(0.3 * w), int(0.7 * w)
        win = img[wy0:wy1, wx0:wx1]
        lum = cv2.cvtColor(np.ascontiguousarray(win[:, :, :3]), cv2.COLOR_RGB2GRAY)
        bright = (lum > 170) & (win[:, :, 3] > 128)
        dark = (lum < 60) & (win[:, :, 3] > 128)
        if not bright.any() or not dark.any():
            return None
        ys, xs = np.nonzero(bright)
        fill = np.median(win[:, :, :3][dark], axis=0).astype(np.uint8)
        by0, by1 = max(0, ys.min() - 1), ys.max() + 2
        bx0, bx1 = max(0, xs.min() - 1), xs.max() + 2
        box = win[by0:by1, bx0:bx1]
        box[:, :, :3][lum[by0:by1, bx0:bx1] > 45] = fill
        dh = int(ys.max() - ys.min() + 1)
        cx = wx0 + 0.5 * (xs.min() + xs.max() + 1)
        cy = wy0 + 0.5 * (ys.min() + ys.max() + 1)
        text = str(int(n))
        scale = dh / 22.0
        thick = max(1, int(round(dh / 7.0)))
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
        org = (int(round(cx - tw / 2.0)), int(round(cy + th / 2.0)))
        rgb = np.ascontiguousarray(img[:, :, :3])
        cv2.putText(rgb, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (235, 235, 235), thick,
                    cv2.LINE_AA)
        img[:, :, :3] = rgb
        return img
    except Exception:
        log.debug("Cannot derive a %s-plate badge", n, exc_info=True)
        return None


def _procedural_map(size: int, void: tuple) -> np.ndarray:
    """Very simple stand-in map (lanes, river, bases) when no texture file is available."""
    img = np.empty((size, size, 3), np.uint8)
    img[:] = void[:3]
    s = size
    khaki = (120, 170, 165)
    t = max(2, int(0.06 * s))
    o = int(0.082 * s)
    for p, q in (((o, s - o), (o, o)), ((o, o), (s - o, o)), ((o, s - o), (s - o, s - o)),
                 ((s - o, s - o), (s - o, o)), ((o, s - o), (s - o, o))):
        cv2.line(img, p, q, khaki, t)
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
