"""Synthetic League of Legends minimaps for training the champion-icon detector.

``generate_sample(rng, size)`` builds a random but plausible in-game situation (texture
variant, fog of war with vision circles, 0-10 champion icons with realistic team counts,
minion waves, structures, camps, wards, pings, camera rectangle, recall / teleport rings,
hard negatives...), renders it with :mod:`treeaicoach.render` at a random *native* minimap
size (170-440 px, like real screens) and then degrades it the way a screen / video capture
does (crop misalignment with the HUD frame, video downscale, JPEG, resize with a random
filter, blur, colour jitter, noise). It returns the image and one label per champion icon
whose centre lies inside the image.

All visual facts come from ``docs/MINIMAP_FACTS.md`` (measured on real captures): the
local player's ring is the same light blue as the allies' (so "self" icons are labelled
``"ally"``), rings are thin, fog multiplies by ~0.36, icons are 0.088-0.10 of the width.

Only numpy / OpenCV are used (no PIL, no torch). One process generates > 150 samples/s at
size 256 on one core (``python training/synth.py --bench``).

CLI::

    python training/synth.py --preview 16 --out previews/   # PNGs with labels + contact sheet
    python training/synth.py --bench                        # throughput on one core
    python training/synth.py --compare real_dir --out dir   # real crops vs synthetic sheet
"""

from __future__ import annotations

import argparse
import colorsys
import json
import logging
import math
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:  # allow `python training/synth.py`
    sys.path.insert(0, str(_REPO_ROOT))

from treeaicoach import render as R  # noqa: E402

log = logging.getLogger(__name__)

#: Detector classes (fixed order, see ARCHITECTURE.md §4.9).
CLASSES: tuple[str, ...] = ("enemy", "ally", "self")
#: Class used for the local player's icon: same ring as the allies (MINIMAP_FACTS.md).
SELF_LABEL: str = "ally"

#: Default folder of the training skin icons (``<Alias>/<skin>.png``, gitignored).
CHAMPION_CACHE_DIR: Path = _REPO_ROOT / "training" / "cache" / "champions"
#: Portraits are stored at this size (largest drawn portrait ~ 0.12 * 440 = 53 px).
PORTRAIT_PX: int = 64
#: Resolution of the walkable-cell grid used to place things on the map.
CELL_GRID: int = 64

try:  # lane / river centre lines measured on the texture (geometry.py)
    from treeaicoach.geometry import (BOT_LANE_POLYLINE, BOT_RIVER_POLYLINE, MID_LANE_POLYLINE,
                                      TOP_LANE_POLYLINE, TOP_RIVER_POLYLINE)
except Exception:  # pragma: no cover - geometry missing or broken: local copies
    _O = 0.082
    TOP_LANE_POLYLINE = ((_O, 1 - _O), (_O, 0.22), (0.10, 0.14), (0.14, 0.10), (0.22, _O),
                         (1 - _O, _O))
    MID_LANE_POLYLINE = ((_O, 1 - _O), (1 - _O, _O))
    BOT_LANE_POLYLINE = tuple((1 - u, 1 - v) for u, v in TOP_LANE_POLYLINE)
    TOP_RIVER_POLYLINE = ((0.15, 0.18), (0.23, 0.27), (0.29, 0.325), (0.40, 0.42), (0.5, 0.5))
    BOT_RIVER_POLYLINE = tuple((1 - u, 1 - v) for u, v in reversed(TOP_RIVER_POLYLINE))

# ======================================================================================
# Appearance ranges (RGB as measured, converted to BGR where used)
# ======================================================================================

#: Ally / self ring: vivid light blue (lossless 2022 captures) and the paler blue-lavender
#: of 2024+ clients. Each range is (low RGB, high RGB), interpolated with one parameter.
ALLY_RING_RGB_VIVID: tuple[tuple[int, int, int], tuple[int, int, int]] = ((75, 140, 200),
                                                                          (81, 162, 230))
ALLY_RING_RGB_PALE: tuple[tuple[int, int, int], tuple[int, int, int]] = ((133, 143, 162),
                                                                         (183, 187, 204))
ENEMY_RING_RGB: tuple[tuple[int, int, int], tuple[int, int, int]] = ((192, 44, 44),
                                                                     (210, 60, 60))
#: Thin dark line between ring and portrait ~ RGB(30-65, 37-75, 55-130).
INNER_LINE_RGB: tuple[tuple[int, int, int], tuple[int, int, int]] = ((30, 37, 55), (65, 75, 130))
#: Ping colours (RGB): generic / on my way, enemy vision, caution / MIA, assist,
#: colour-blind magenta and blue.
PING_RGB: tuple[tuple[int, int, int], ...] = ((33, 188, 253), (248, 34, 67), (245, 190, 15),
                                              (9, 215, 128), (255, 79, 203), (24, 117, 255))
#: HUD frame around the minimap (dark blue-green band, thin bronze line), BGR.
FRAME_BAND_BGR: tuple[int, int, int] = (22, 26, 14)
FRAME_LINE_BGR: tuple[int, int, int] = (70, 118, 150)
#: The teal "!" diamond button overlapping the top-left corner of the map (BGR).
BANG_FILL_BGR: tuple[int, int, int] = (82, 89, 33)
BANG_BORDER_BGR: tuple[int, int, int] = (85, 144, 167)
BANG_MARK_BGR: tuple[int, int, int] = (189, 233, 115)

PING_SYMBOLS: tuple[str, ...] = (
    "caution", "on_my_way_new", "enemychampsighted", "mia_new", "assist", "all_in", "bait",
    "hold", "need_ward", "push", "retreat", "target", "ping", "area_is_warded_small_red_new",
)
PING_RINGS: tuple[str, ...] = ("ring_red", "ring2_red", "ring2_yellow", "ring_danger",
                               "ring_green")
ALLY_WARDS: tuple[str, ...] = ("minimap_ward_green_full", "minimap_ward_green_full",
                               "minimap_ward_blue_full", "minimap_ward_pink_friendly",
                               "minimap_jammer_friendly")
ENEMY_WARDS: tuple[str, ...] = (R.ENEMY_WARD_DOT, R.ENEMY_WARD_DOT, "minimap_ward_green_enemy_new",
                                "minimap_ward_pink_enemy", "minimap_jammer_enemy")
#: Icons used as hard negatives (things that are NOT champions), with a size range.
HARD_NEGATIVES: tuple[tuple[str, float, float], ...] = (
    ("icon_ui_inhibitor_minimap_v2", 0.03, 0.05), ("icon_ui_nexus_minimap_v2", 0.035, 0.055),
    ("icon_ui_tower_minimap", 0.035, 0.05), ("inhibitor", 0.035, 0.06),
    ("dragon", 0.04, 0.06), ("baron", 0.04, 0.06), ("riftherald", 0.04, 0.06),
    ("grub", 0.03, 0.05), ("atakhan_r", 0.04, 0.06), ("atakhan_v", 0.04, 0.06),
    ("jungle_camp_2", 0.03, 0.05), ("jungle_camp_4", 0.03, 0.05),
    ("jungle_camp_6", 0.03, 0.05), ("jungle_camp_next", 0.03, 0.05),
    ("jungle_camp_current", 0.03, 0.05), ("tunnelicon", 0.03, 0.05), ("zzrotblue", 0.02, 0.035),
    ("zzrotred", 0.02, 0.035), ("crystalicon", 0.03, 0.05), ("questicon", 0.03, 0.05),
    ("respawnicon01", 0.025, 0.04), ("healthpack", 0.025, 0.04), ("champion_dead", 0.03, 0.05),
    ("teleporthighlight_friendly", 0.08, 0.12), ("teleporthighlight_enemy", 0.08, 0.12),
    ("recalloutline", 0.08, 0.11), ("recallhostileoutline", 0.08, 0.11),
    ("timeryellow", 0.025, 0.035), ("timergrey", 0.025, 0.035),
)
TEAMS: tuple[str, str] = ("ORDER", "CHAOS")


@dataclass
class SynthConfig:
    """Probabilities and ranges of the generator (all tunable)."""

    native_min: int = 170
    native_max: int = 440
    #: Probability to use a common screen-derived size (720p / 900p / 1080p / 1440p...).
    p_common_native: float = 0.4
    common_natives: tuple[int, ...] = (171, 204, 224, 255, 290, 339)
    #: Champion icon diameter / minimap width (real 0.088-0.10; wider for robustness).
    icon_diam: tuple[float, float] = (0.07, 0.12)
    p_empty: float = 0.05            # fully fogged / empty map, no champion
    p_self: float = 0.85             # local player's icon present
    p_random_hue: float = 0.10       # random ring colour, cls_valid=False
    p_dummy: float = 0.03            # dummy_*_circle portrait (unknown champion)
    p_teamfight: float = 0.35
    p_recall: float = 0.06
    p_teleport: float = 0.02
    p_edge: float = 0.04             # icon partly outside the map edge
    p_pale_ally: float = 0.55        # 2024+ pale blue-lavender ally ring
    p_ping: float = 0.4
    p_ping_wheel: float = 0.03
    p_bang_button: float = 0.65
    p_camera: float = 0.88
    p_path: float = 0.15
    p_hard_neg: float = 0.55
    p_fog_texture: float = 0.06
    p_no_fog: float = 0.04
    p_jitter: float = 0.35           # 1-3 px misalignment of the minimap crop
    p_video_downscale: float = 0.25
    p_jpeg: float = 0.5
    jpeg_quality: tuple[int, int] = (55, 95)
    p_blur: float = 0.3
    p_noise: float = 0.5
    noise_sigma: tuple[float, float] = (1.0, 5.0)
    p_color: float = 0.85


DEFAULT_CONFIG = SynthConfig()


# ======================================================================================
# Assets (lazy, one set per process, thread-safe)
# ======================================================================================


class _Polyline:
    """Densely resampled polyline: points, unit tangents and total length."""

    def __init__(self, pts: Sequence[tuple[float, float]], step: float = 0.004) -> None:
        p = np.asarray(pts, np.float64)
        seg = np.diff(p, axis=0)
        seg_len = np.hypot(seg[:, 0], seg[:, 1])
        cum = np.concatenate([[0.0], np.cumsum(seg_len)])
        self.length = float(cum[-1])
        n = max(2, int(self.length / step) + 1)
        s = np.linspace(0.0, self.length, n)
        self.points = np.stack([np.interp(s, cum, p[:, 0]), np.interp(s, cum, p[:, 1])], axis=1)
        tang = np.gradient(self.points, axis=0)
        tang /= np.maximum(np.hypot(tang[:, 0], tang[:, 1]), 1e-9)[:, None]
        self.tangents = tang

    def at(self, s: float) -> tuple[np.ndarray, np.ndarray]:
        """Point and tangent at the fraction ``s`` (0..1) of the length."""
        i = int(round(min(max(s, 0.0), 1.0) * (len(self.points) - 1)))
        return self.points[i], self.tangents[i]


class _MapCells:
    """Walkable cells of a texture (centres in normalized coords), split by area."""

    def __init__(self, alpha: np.ndarray | None, lanes: dict[str, _Polyline]) -> None:
        g = CELL_GRID
        if alpha is not None:
            a = cv2.resize(alpha, (g, g), interpolation=cv2.INTER_AREA)
            walk = a > 140
        else:
            walk = np.ones((g, g), bool)
        ys, xs = np.nonzero(walk)
        cells = np.stack([(xs + 0.5) / g, (ys + 0.5) / g], axis=1)
        if len(cells) == 0:
            cells = np.array([[0.5, 0.5]])
        d_lane = np.full(len(cells), 9.0)
        for pl in lanes.values():
            pts = pl.points[::3]
            d = np.hypot(cells[:, None, 0] - pts[None, :, 0], cells[:, None, 1] - pts[None, :, 1])
            d_lane = np.minimum(d_lane, d.min(axis=1))
        d_blue = np.hypot(cells[:, 0], 1.0 - cells[:, 1])
        d_red = np.hypot(1.0 - cells[:, 0], cells[:, 1])
        in_base = (d_blue < 0.36) | (d_red < 0.36)
        self.all = cells
        self.jungle = cells[(d_lane > 0.07) & ~in_base]
        if len(self.jungle) == 0:
            self.jungle = cells
        self.base = {"ORDER": cells[(d_blue < 0.3)], "CHAOS": cells[(d_red < 0.3)]}
        for k, v in self.base.items():
            if len(v) == 0:
                self.base[k] = np.array([[0.1, 0.9]]) if k == "ORDER" else np.array([[0.9, 0.1]])


class _Assets:
    """Renderer, textures, walkable cells, portraits and noise bank (per process)."""

    def __init__(self, champion_dir: Path | None = None) -> None:
        self.renderer = R.MinimapRenderer()
        self.textures = self.renderer.textures() or [R.DEFAULT_TEXTURE]
        self.base_textures = [t for t in self.textures if R.texture_variant(t) == "base"]
        self.fogs = self.renderer.fogs()
        self.lanes = {"top": _Polyline(TOP_LANE_POLYLINE), "mid": _Polyline(MID_LANE_POLYLINE),
                      "bot": _Polyline(BOT_LANE_POLYLINE)}
        self.rivers = [_Polyline(TOP_RIVER_POLYLINE), _Polyline(BOT_RIVER_POLYLINE)]
        self.icon_paths = self._find_portraits(champion_dir)
        self._lock = threading.Lock()
        self._portraits: dict[int, np.ndarray | None] = {}
        self._cells: dict[str, _MapCells] = {}
        self._rings: dict[tuple, np.ndarray] = {}
        self.dummy = {rel: self.renderer.icon(R.DUMMY_ICON[rel]) for rel in ("ally", "enemy")}
        self.noise = np.random.default_rng(20240917).standard_normal((320, 320, 3)).astype(
            np.float32)
        self.available_icons = {name for name in (
            [n for n, _, _ in HARD_NEGATIVES] + list(PING_SYMBOLS) + list(PING_RINGS)
            + [w for w in ALLY_WARDS + ENEMY_WARDS if w != R.ENEMY_WARD_DOT]
        ) if self.renderer.icon(name) is not None}

    # ---------------------------------------------------------------- portraits
    def _find_portraits(self, champion_dir: Path | None) -> list[Path]:
        """Training skins (``<dir>/<Alias>/*.png``), else the bundled 64 px base icons."""
        d = Path(champion_dir) if champion_dir is not None else CHAMPION_CACHE_DIR
        paths: list[Path] = []
        try:
            if d.is_dir():
                paths = sorted(p for p in d.glob("*/*.png") if p.is_file())
        except OSError:
            paths = []
        if not paths:
            try:
                bundled = self.renderer.assets_dir / "icons" / "champions"
                paths = sorted(p for p in bundled.glob("*.png") if p.is_file())
            except OSError:
                paths = []
        if not paths:
            log.warning("No champion portrait found: neutral discs will be drawn")
        return paths

    def portrait(self, idx: int) -> np.ndarray | None:
        """Portrait ``idx`` as RGBA uint8 (<= PORTRAIT_PX), loaded once."""
        with self._lock:
            if idx in self._portraits:
                return self._portraits[idx]
        img: np.ndarray | None = None
        try:
            img = R.load_rgba(self.icon_paths[idx])
            h, w = img.shape[:2]
            if max(h, w) > PORTRAIT_PX:
                s = PORTRAIT_PX / float(max(h, w))
                img = cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))),
                                 interpolation=cv2.INTER_AREA)
        except (OSError, ValueError, IndexError, cv2.error) as exc:
            log.debug("Cannot load portrait %s: %s", idx, exc)
            img = None
        with self._lock:
            self._portraits[idx] = img
        return img

    # ---------------------------------------------------------------- map cells
    def cells(self, texture: str) -> _MapCells:
        with self._lock:
            c = self._cells.get(texture)
        if c is None:
            rgba = self.renderer.texture_rgba(texture)
            c = _MapCells(rgba[:, :, 3] if rgba is not None else None, self.lanes)
            with self._lock:
                self._cells[texture] = c
        return c

    # ---------------------------------------------------------------- ring sprites
    def ring_sprite(self, bgr: tuple[int, int, int], width: float = 0.09) -> np.ndarray:
        """Glowing ring RGBA (64 px) of a ping colour, cached."""
        key = (tuple(int(c) for c in bgr), round(width, 3))
        with self._lock:
            spr = self._rings.get(key)
        if spr is None:
            n = 64
            yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
            d = np.hypot(xx + 0.5 - n / 2, yy + 0.5 - n / 2) / (n / 2)
            dist = np.abs(d - 0.8)
            core = np.clip((width / 2 - dist) * n / 2 + 0.5, 0, 1)
            glow = 0.55 * np.exp(-np.maximum(dist - width / 2, 0) * n / 5.0)
            a = np.maximum(core, glow) * (d < 1.0)
            col = np.asarray(bgr, np.float32)[None, None, ::-1]  # -> RGB
            rgb = col + (255 - col) * (0.45 * core)[:, :, None]
            spr = np.dstack([np.clip(rgb, 0, 255), a * 255]).astype(np.uint8)
            with self._lock:
                self._rings[key] = spr
        return spr


_ASSETS: _Assets | None = None
_ASSETS_LOCK = threading.Lock()


def get_assets() -> _Assets:
    """Process-wide assets (created on first use)."""
    global _ASSETS
    if _ASSETS is None:
        with _ASSETS_LOCK:
            if _ASSETS is None:
                _ASSETS = _Assets()
    return _ASSETS


# ======================================================================================
# Small random helpers
# ======================================================================================


def _lerp_rgb(rng: np.random.Generator, lo_hi: tuple, jitter: float = 4.0) -> tuple[int, int, int]:
    """BGR colour on the segment between two RGB endpoints, plus a little jitter."""
    lo, hi = np.asarray(lo_hi[0], np.float32), np.asarray(lo_hi[1], np.float32)
    rgb = lo + (hi - lo) * rng.random() + rng.normal(0.0, jitter, 3)
    rgb = np.clip(rgb, 0, 255)
    return int(rgb[2]), int(rgb[1]), int(rgb[0])


def _random_hue_bgr(rng: np.random.Generator) -> tuple[int, int, int]:
    h, s, v = rng.random(), rng.uniform(0.35, 1.0), rng.uniform(0.55, 1.0)
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return int(b * 255), int(g * 255), int(r * 255)


def _choice(rng: np.random.Generator, seq: Sequence[Any]) -> Any:
    return seq[int(rng.integers(len(seq)))]


def _pick_cell(rng: np.random.Generator, cells: np.ndarray) -> np.ndarray:
    c = cells[int(rng.integers(len(cells)))]
    return c + rng.uniform(-0.5, 0.5, 2) / CELL_GRID


def _clock_text(rng: np.random.Generator) -> str:
    t = int(rng.integers(3, 300))
    return f"{t // 60}:{t % 60:02d}"


# ======================================================================================
# Scene sampling
# ======================================================================================


@dataclass
class _Champ:
    u: float
    v: float
    r: float
    rel: str            # "self" | "ally" | "enemy"


class _SceneBuilder:
    """Draws one random scene (everything in normalized coordinates)."""

    def __init__(self, rng: np.random.Generator, assets: _Assets, cfg: SynthConfig,
                 native: int) -> None:
        self.rng = rng
        self.A = assets
        self.cfg = cfg
        self.native = native
        self.my_team = TEAMS[int(rng.integers(2))]
        self.enemy_team = TEAMS[1 - TEAMS.index(self.my_team)]
        self.phase = float(rng.random())                     # 0 early game .. 1 late game
        # base texture most of the time, dragon-soul variants otherwise
        tex = assets.textures
        base = assets.base_textures
        self.texture = _choice(rng, base) if base and rng.random() < 0.4 else _choice(rng, tex)
        self.variant = R.texture_variant(self.texture)
        self.cells = assets.cells(self.texture)
        self.vision: list[tuple[float, float, float]] = []
        self.sprites: list[R.Sprite] = []
        self.texts: list[tuple] = []

    # ---------------------------------------------------------------- positions
    def lane_point(self, lane: str | None = None, s: float | None = None,
                   spread: float = 0.012) -> np.ndarray:
        rng = self.rng
        pl = self.A.lanes[lane or _choice(rng, ("top", "mid", "bot"))]
        p, t = pl.at(rng.uniform(0.03, 0.97) if s is None else s)
        return p + np.array([-t[1], t[0]]) * rng.normal(0.0, spread) \
            + t * rng.normal(0.0, spread)

    def champion_point(self, rel: str) -> np.ndarray:
        rng, c = self.rng, self.cells
        x = rng.random()
        if x < 0.45:
            return self.lane_point(spread=0.014)
        if x < 0.62:
            pl = _choice(rng, self.A.rivers)
            p, t = pl.at(rng.random())
            return p + rng.normal(0.0, 0.02, 2)
        if x < 0.88:
            return _pick_cell(rng, c.jungle)
        if rel != "enemy" and x < 0.96:
            return _pick_cell(rng, c.base[self.my_team])
        return _pick_cell(rng, c.all)

    # ---------------------------------------------------------------- champions
    def champions(self, empty: bool) -> list[_Champ]:
        rng, cfg = self.rng, self.cfg
        if empty:
            return []
        n_ally = int(rng.choice(5, p=[0.06, 0.07, 0.12, 0.20, 0.55]))
        n_self = int(rng.random() < cfg.p_self)
        n_enemy = int(rng.choice(6, p=[0.22, 0.24, 0.18, 0.13, 0.11, 0.12]))
        rels = ["self"] * n_self + ["ally"] * n_ally + ["enemy"] * n_enemy
        diam = rng.uniform(*cfg.icon_diam)
        # smaller minimaps show slightly larger icons (MINIMAP_FACTS.md)
        diam *= 1.0 + 0.06 * (255 - self.native) / 255.0
        r0 = diam / 2.0
        fight_center = None
        if rng.random() < cfg.p_teamfight and len(rels) >= 2:
            fight_center = (self.lane_point(spread=0.02) if rng.random() < 0.5
                            else _pick_cell(rng, self.cells.jungle))
            fight_sigma = rng.uniform(0.02, 0.06)
        out: list[_Champ] = []
        for rel in rels:
            r = r0 * rng.uniform(0.97, 1.03)
            for _ in range(14):
                in_fight = fight_center is not None and \
                    rng.random() < (0.8 if rel == "enemy" else 0.6)
                if in_fight:
                    p = fight_center + rng.normal(0.0, fight_sigma, 2)
                else:
                    p = self.champion_point(rel)
                if rng.random() < cfg.p_edge:     # partly outside the map edge
                    k = int(rng.integers(4))
                    val = rng.uniform(0.0, 0.8 * r)
                    p = p.copy()
                    p[k % 2] = val if k < 2 else 1.0 - val
                u, v = float(p[0]), float(p[1])
                if not (0.0 <= u < 1.0 and 0.0 <= v < 1.0):
                    continue
                min_d = rng.uniform(0.55, 0.9) * r
                if all(math.hypot(u - o.u, v - o.v) >= min_d for o in out):
                    out.append(_Champ(u, v, r, rel))
                    break
        order = rng.permutation(len(out))   # no drawing priority for the local player
        return [out[i] for i in order]

    def champion_sprites(self, champs: list[_Champ]) -> tuple[list[R.ChampionSprite], list[bool]]:
        rng, cfg, A = self.rng, self.cfg, self.A
        sprites: list[R.ChampionSprite] = []
        valid: list[bool] = []
        ring_frac = rng.uniform(0.10, 0.17)
        inner_frac = rng.uniform(0.03, 0.09)
        inner_bgr = _lerp_rgb(rng, INNER_LINE_RGB, 6.0)
        outline = rng.uniform(0.03, 0.07) if rng.random() < 0.3 else 0.0
        shading = rng.uniform(0.0, 0.3)
        pale = rng.random() < cfg.p_pale_ally
        bright = rng.uniform(0.78, 1.08)
        n_icons = len(A.icon_paths)
        used: set[int] = set()
        for ch in champs:
            rel = ch.rel
            team_rel = "enemy" if rel == "enemy" else "ally"
            cls_valid = True
            if rng.random() < cfg.p_random_hue:
                ring = _random_hue_bgr(rng)
                cls_valid = False
            elif team_rel == "enemy":
                ring = _lerp_rgb(rng, ENEMY_RING_RGB, 5.0)
            else:
                p = pale if rng.random() < 0.9 else not pale
                ring = _lerp_rgb(rng, ALLY_RING_RGB_PALE if p else ALLY_RING_RGB_VIVID, 5.0)
            icon: np.ndarray | None = None
            if rng.random() < cfg.p_dummy:
                icon = A.dummy.get(team_rel)
            elif n_icons:
                for _ in range(4):
                    idx = int(rng.integers(n_icons))
                    if idx not in used:
                        break
                used.add(idx)
                icon = A.portrait(idx)
            if icon is not None:
                f = bright * rng.uniform(0.93, 1.07)
                if abs(f - 1.0) > 0.02:
                    icon = cv2.multiply(icon, (f, f, f, 1.0))   # saturating, alpha kept
            recall = rng.random() < (cfg.p_recall if rel != "enemy" else cfg.p_recall / 3)
            sprites.append(R.ChampionSprite(
                u=ch.u, v=ch.v, r=ch.r, relation=rel, icon=icon, ring_bgr=ring, recall=recall,
                label_class=SELF_LABEL if rel == "self" else team_rel,
                teleport=rng.random() < cfg.p_teleport,
                ring_frac=min(0.2, max(0.08, ring_frac + rng.normal(0, 0.008))),
                ring_shading=shading, inner_line_bgr=inner_bgr,
                inner_line_frac=inner_frac, outline_frac=outline,
                halo=(rng.uniform(0.12, 0.45) if rng.random() < (0.35 if rel == "enemy" else 0.12)
                      else 0.0),
            ))
            valid.append(cls_valid)
        return sprites, valid

    # ---------------------------------------------------------------- vision
    def add_vision(self, u: float, v: float, r: float) -> None:
        self.vision.append((float(u), float(v), float(r)))

    def in_vision(self, u: float, v: float, margin: float = 0.01) -> bool:
        return any((u - a) ** 2 + (v - b) ** 2 < (r - margin) ** 2 and r > margin
                   for a, b, r in self.vision)

    # ---------------------------------------------------------------- structures
    def structures(self) -> tuple[frozenset, dict[str, int]]:
        rng, ph = self.rng, self.phase
        destroyed: set[str] = set()
        for team in TEAMS:
            for lane in ("top", "mid", "bot"):
                k = int(rng.binomial(4, min(0.9, 0.5 * ph ** 1.5)))
                chain = [f"{team}_{lane}_outer", f"{team}_{lane}_inner", f"{team}_{lane}_base",
                         f"{team}_{lane}_inhibitor"]
                destroyed.update(chain[:k])
            if ph > 0.8 and rng.random() < 0.3:
                destroyed.update({f"{team}_nexus_turret_1", f"{team}_nexus_turret_2"})
        plates: dict[str, int] = {}
        if ph < 0.45:
            for sid in R.DEFAULT_TURRET_PLATES:
                plates[sid] = int(np.clip(5 - rng.binomial(5, ph * 1.4), 1, 5))
        return frozenset(destroyed), plates

    def structure_vision(self, destroyed: frozenset) -> None:
        rng = self.rng
        for sid, u, v, kind, team in R.iter_structures(team=self.my_team):
            if sid not in destroyed:
                self.add_vision(u, v, rng.uniform(0.075, 0.1) if kind == "turret" else 0.06)
        fu, fv = R.FOUNTAINS[self.my_team]
        self.add_vision(fu, fv, rng.uniform(0.16, 0.24))

    # ---------------------------------------------------------------- minions
    def minions(self) -> list[tuple]:
        rng = self.rng
        waves: list[tuple] = []
        for lane, pl in self.A.lanes.items():
            if rng.random() > 0.75:
                continue
            front = rng.uniform(0.25, 0.75)
            for team in TEAMS:
                n = int(rng.integers(0, 8))
                sign = -1.0 if team == "ORDER" else 1.0    # ORDER comes from s = 0
                s = front + sign * rng.uniform(0.0, 0.02)
                rel = "ally" if team == self.my_team else "enemy"
                for _ in range(n):
                    s += sign * rng.uniform(0.010, 0.022) / pl.length
                    if not 0.0 < s < 1.0:
                        break
                    p, t = pl.at(s)
                    q = p + np.array([-t[1], t[0]]) * rng.normal(0.0, 0.006)
                    waves.append((float(q[0]), float(q[1]), rel, float(rng.uniform(0.85, 1.15))))
        # ally minions give vision; enemy minions are drawn only if seen
        for u, v, rel, _ in waves:
            if rel == "ally":
                self.add_vision(u, v, rng.uniform(0.04, 0.07))
        return waves

    # ---------------------------------------------------------------- camps
    def camps(self) -> list[tuple]:
        rng = self.rng
        out: list[tuple] = []
        dragon = f"dragon_{self.variant}" if self.variant != "base" else "dragon"
        if self.A.renderer.icon(dragon) is None:
            dragon = "dragon"
        for u, v, name in R.CAMPS:
            x = rng.random()
            if name == "dragon":
                if x < 0.55:
                    icon = "dragon_elder" if rng.random() < 0.15 else dragon
                    out.append((u, v, icon, rng.uniform(0.04, 0.055)))
                elif x < 0.8:
                    out.append((u, v, "timeryellow", rng.uniform(0.024, 0.032)))
                    if rng.random() < 0.5:
                        self.texts.append((u, v + 0.035, _clock_text(rng), rng.uniform(0.022, 0.03)))
            elif name == "baron":
                if x < 0.6:
                    early = ("grub", "riftherald") if self.phase < 0.5 else ("baron", "atakhan_r")
                    out.append((u, v, _choice(rng, early), rng.uniform(0.04, 0.055)))
                elif x < 0.85:
                    out.append((u, v, "timergrey" if rng.random() < 0.4 else "timeryellow",
                                rng.uniform(0.024, 0.032)))
                    if rng.random() < 0.5:
                        self.texts.append((u, v + 0.035, _clock_text(rng), rng.uniform(0.022, 0.03)))
            elif name == "scuttle":
                if x < 0.5:
                    out.append((u, v, "camp", rng.uniform(0.022, 0.03)))
            else:
                if x < 0.55:
                    out.append((u, v, "smallcamp", rng.uniform(0.019, 0.026)))
                elif x < 0.72:
                    head = name if name in ("blue", "red") else "camp"
                    out.append((u, v, head, rng.uniform(0.026, 0.036)))
                elif x < 0.86:
                    out.append((u, v, "timeryellow", rng.uniform(0.022, 0.03)))
                elif x < 0.9:
                    out.append((u, v, _choice(rng, ("jungle_camp_current", "jungle_camp_next",
                                                     "timergrey")), rng.uniform(0.022, 0.03)))
        return out

    def plants(self) -> None:
        rng = self.rng
        for _ in range(int(rng.integers(0, 7))):
            p = _pick_cell(rng, self.cells.jungle)
            tint = _choice(rng, ((90, 205, 70), (80, 210, 170), (70, 90, 220), (200, 170, 70)))
            self.sprites.append(R.Sprite(float(p[0]), float(p[1]), rng.uniform(0.010, 0.016),
                                         "mapcircle", tint=tint))

    # ---------------------------------------------------------------- wards
    def wards(self, enemies: list[_Champ]) -> list[tuple]:
        rng = self.rng
        out: list[tuple] = []
        for _ in range(int(rng.integers(0, 8))):
            p = _pick_cell(rng, self.cells.jungle) if rng.random() < 0.7 else \
                self.lane_point(spread=0.03)
            out.append((float(p[0]), float(p[1]), _choice(rng, ALLY_WARDS),
                        R.WARD_SIZE * rng.uniform(0.85, 1.15)))
            self.add_vision(p[0], p[1], rng.uniform(0.05, 0.07))
        # enemies must be in vision: add a ward / vision source close to unseen ones
        for e in enemies:
            if self.in_vision(e.u, e.v):
                continue
            ang = rng.uniform(0, 2 * math.pi)
            d = rng.uniform(0.0, 0.045)
            cu, cv_ = e.u + d * math.cos(ang), e.v + d * math.sin(ang)
            self.add_vision(cu, cv_, max(d + 0.02, rng.uniform(0.055, 0.09)))
            if rng.random() < 0.35:
                out.append((cu, cv_, _choice(rng, ALLY_WARDS), R.WARD_SIZE * rng.uniform(0.85, 1.15)))
        if rng.random() < 0.3:
            for _ in range(int(rng.integers(1, 4))):
                p = _pick_cell(rng, self.cells.jungle)
                name = _choice(rng, ENEMY_WARDS)
                size = R.ENEMY_WARD_DOT_SIZE * rng.uniform(0.8, 1.2) if name == R.ENEMY_WARD_DOT \
                    else R.WARD_SIZE * rng.uniform(0.8, 1.1)
                out.append((float(p[0]), float(p[1]), name, size))
        return [w for w in out if w[2] in self.A.available_icons or w[2] == R.ENEMY_WARD_DOT]

    # ---------------------------------------------------------------- pings & negatives
    def pings(self, champs: list[_Champ]) -> list[tuple]:
        rng, A = self.rng, self.A
        out: list[tuple] = []
        if rng.random() > self.cfg.p_ping:
            return out
        for _ in range(int(rng.integers(1, 4))):
            if champs and rng.random() < 0.4:
                c = _choice(rng, champs)
                u, v = c.u + rng.normal(0, 0.02), c.v + rng.normal(0, 0.02)
            else:
                p = _pick_cell(rng, self.cells.all)
                u, v = float(p[0]), float(p[1])
            x = rng.random()
            if x < 0.45:     # pulsing coloured ring + symbol
                col = _choice(rng, PING_RGB)
                ring = A.ring_sprite((col[2], col[1], col[0]), rng.uniform(0.06, 0.12))
                self.sprites.append(R.Sprite(u, v, rng.uniform(0.07, 0.12), ring, layer="over",
                                             opacity=rng.uniform(0.6, 1.0)))
            elif x < 0.65:
                name = _choice(rng, PING_RINGS)
                if name in A.available_icons:
                    out.append((u, v, name, rng.uniform(0.07, 0.11)))
            sym = _choice(rng, PING_SYMBOLS)
            if sym in A.available_icons:
                out.append((u, v, sym, rng.uniform(0.04, 0.06)))
        return out

    def hard_negatives(self) -> None:
        rng, A = self.rng, self.A
        if rng.random() > self.cfg.p_hard_neg:
            return
        for _ in range(int(rng.integers(1, 5))):
            name, lo, hi = _choice(rng, HARD_NEGATIVES)
            if name not in A.available_icons:
                continue
            p = _pick_cell(rng, self.cells.all)
            tint = None
            if name.startswith("icon_ui_") or name == "inhibitor":
                tint = R.TEAM_BGR[_choice(rng, ("ally", "enemy"))]
            self.sprites.append(R.Sprite(float(p[0]), float(p[1]), rng.uniform(lo, hi), name,
                                         tint=tint, layer="under"))
        if rng.random() < 0.25:   # team-coloured dots (minion-like / ward-like)
            for _ in range(int(rng.integers(1, 4))):
                p = _pick_cell(rng, self.cells.all)
                tint = _choice(rng, (R.MINION_BGR["ally"], R.MINION_BGR["enemy"],
                                     R.RING_BGR["enemy"], R.RING_BGR["ally"]))
                self.sprites.append(R.Sprite(float(p[0]), float(p[1]), rng.uniform(0.02, 0.035),
                                             "mapcircle", tint=tint))

    # ---------------------------------------------------------------- camera / path
    def camera(self, champs: list[_Champ]) -> tuple[tuple[float, float, float, float] | None,
                                                     list[tuple[float, float]] | None]:
        rng = self.rng
        me = next((c for c in champs if c.rel == "self"), None)
        cam = None
        if rng.random() < self.cfg.p_camera:
            w = rng.uniform(0.262, 0.29)
            h = w * _choice(rng, (0.56, 0.56, 0.56, 0.6, 0.625, 0.75)) * rng.uniform(0.97, 1.03)
            if me is not None and rng.random() < 0.7:
                cx, cy = me.u + rng.normal(0, 0.02), me.v + rng.normal(0, 0.02)
            else:
                cx, cy = rng.uniform(0.08, 0.92), rng.uniform(0.06, 0.94)
            cx = float(np.clip(cx, w / 2 - 0.03, 1 - w / 2 + 0.03))
            cy = float(np.clip(cy, h / 2 - 0.03, 1 - h / 2 + 0.03))
            cam = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        path = None
        if me is not None and rng.random() < self.cfg.p_path:
            pts = [(me.u, me.v)]
            u, v = me.u, me.v
            for _ in range(int(rng.integers(1, 4))):
                ang = rng.uniform(0, 2 * math.pi)
                d = rng.uniform(0.05, 0.2)
                u = float(np.clip(u + d * math.cos(ang), 0.03, 0.97))
                v = float(np.clip(v + d * math.sin(ang), 0.03, 0.97))
                pts.append((u, v))
            path = pts
        return cam, path

    # ---------------------------------------------------------------- build
    def build(self) -> tuple[R.Scene, list[bool]]:
        rng, cfg = self.rng, self.cfg
        empty = rng.random() < cfg.p_empty
        destroyed, plates = self.structures()
        champs = self.champions(empty)
        enemies = [c for c in champs if c.rel == "enemy"]
        if not empty or rng.random() < 0.5:
            self.structure_vision(destroyed)
        for c in champs:
            if c.rel != "enemy":
                self.add_vision(c.u, c.v, rng.uniform(0.07, 0.1))
        minions = self.minions() if not empty or rng.random() < 0.5 else []
        wards = self.wards(enemies)
        if not empty:
            for _ in range(int(rng.integers(0, 3))):   # other vision sources (random blobs)
                p = _pick_cell(rng, self.cells.all)
                self.add_vision(p[0], p[1], rng.uniform(0.04, 0.09))
        minions = [m for m in minions if m[2] == "ally" or self.in_vision(m[0], m[1], 0.0)]
        camp_icons = self.camps()
        self.plants()
        pings = self.pings(champs)
        self.hard_negatives()
        cam, path = self.camera(champs)
        sprites, valid = self.champion_sprites(champs)
        fog_alpha = 1.0 - rng.uniform(0.33, 0.40)
        fog_texture = None
        x = rng.random()
        if x < cfg.p_no_fog:
            fog_alpha = 0.0
        elif x < cfg.p_no_fog + cfg.p_fog_texture and self.A.fogs:
            fog_texture = R.fog_for_texture(self.texture)
            fog_alpha = rng.uniform(0.5, 0.8)
        scene = R.Scene(
            texture=self.texture, size=self.native, fog_alpha=fog_alpha, vision=self.vision,
            champions=sprites, minions=minions, structures=True, wards=wards, pings=pings,
            camera=cam, camps=True, my_team=self.my_team, destroyed=destroyed,
            turret_plates=plates, fog_texture=fog_texture, camp_icons=camp_icons,
            sprites=self.sprites, texts=self.texts, path=path,
            structure_scale=rng.uniform(0.92, 1.12), minion_r=R.MINION_R * rng.uniform(0.85, 1.15),
            camera_px=(1 if self.native < 250 else 2) if rng.random() < 0.8 else None,
            camera_on_top=rng.random() < 0.3,
        )
        return scene, valid


# ======================================================================================
# Extra overlays drawn on the rendered native image
# ======================================================================================


def _draw_bang_button(img: np.ndarray, rng: np.random.Generator) -> None:
    """Teal "!" diamond of the HUD frame overlapping the top-left corner of the map."""
    n = img.shape[0]
    cx = n * rng.uniform(0.045, 0.062)
    cy = n * rng.uniform(0.04, 0.058)
    hd = n * rng.uniform(0.036, 0.046)
    sh = 4
    m = 1 << sh
    pts = np.array([[cx, cy - hd], [cx + hd, cy], [cx, cy + hd], [cx - hd, cy]]) * m
    pts = pts.round().astype(np.int32)
    cv2.fillConvexPoly(img, pts, BANG_BORDER_BGR, cv2.LINE_AA, sh)
    inner = np.array([[cx, cy - hd * 0.8], [cx + hd * 0.8, cy], [cx, cy + hd * 0.8],
                      [cx - hd * 0.8, cy]]) * m
    cv2.fillConvexPoly(img, inner.round().astype(np.int32), BANG_FILL_BGR, cv2.LINE_AA, sh)
    t = max(1, int(round(hd * 0.18)))
    x = int(round(cx))
    cv2.line(img, (x, int(round(cy - hd * 0.45))), (x, int(round(cy + hd * 0.12))),
             BANG_MARK_BGR, t, cv2.LINE_AA)
    cv2.circle(img, (x, int(round(cy + hd * 0.38))), max(1, t // 2 + 1), BANG_MARK_BGR, -1,
               cv2.LINE_AA)


def _draw_ping_wheel(img: np.ndarray, rng: np.random.Generator, assets: _Assets) -> None:
    """Radial ping wheel (hard negative): translucent teal disc, spokes and symbols."""
    n = img.shape[0]
    cx, cy = n * rng.uniform(0.3, 0.7), n * rng.uniform(0.3, 0.7)
    rad = n * rng.uniform(0.09, 0.13)
    ov = img.copy()
    cv2.circle(ov, (int(cx), int(cy)), int(rad), (95, 90, 20), -1, cv2.LINE_AA)
    cv2.circle(ov, (int(cx), int(cy)), int(rad), (150, 140, 60), max(1, n // 150), cv2.LINE_AA)
    k = 8
    for i in range(k):
        a = 2 * math.pi * (i + 0.5) / k
        cv2.line(ov, (int(cx + rad * math.cos(a)), int(cy + rad * math.sin(a))),
                 (int(cx + 2.3 * rad * math.cos(a)), int(cy + 2.3 * rad * math.sin(a))),
                 (60, 110, 140), 1, cv2.LINE_AA)
    cv2.addWeighted(ov, 0.75, img, 0.25, 0, dst=img)
    syms = [s for s in PING_SYMBOLS if s in assets.available_icons]
    for i in range(k):
        if not syms:
            break
        a = 2 * math.pi * i / k
        R.alpha_blit(img, assets.renderer.icon(_choice(rng, syms)),
                     cx + 1.65 * rad * math.cos(a), cy + 1.65 * rad * math.sin(a),
                     scale=n * 0.05 / 32.0)
    if "caution" in assets.available_icons:
        R.alpha_blit(img, assets.renderer.icon("caution"), cx, cy, scale=n * 0.05 / 32.0)


# ======================================================================================
# Degradation (capture / video pipeline)
# ======================================================================================


def _frame_canvas(img: np.ndarray, pad: int, rng: np.random.Generator) -> np.ndarray:
    """The minimap inside a strip of the HUD frame (dark band + bronze line)."""
    n = img.shape[0]
    band = [float(min(255.0, max(0.0, c + rng.normal(0, 4)))) for c in FRAME_BAND_BGR]
    canvas = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=band)
    line_off = int(rng.integers(2, max(3, pad)))
    cv2.rectangle(canvas, (pad - line_off, pad - line_off),
                  (pad + n - 1 + line_off, pad + n - 1 + line_off), FRAME_LINE_BGR, 1)
    return canvas


#: Resampling filters of the final resize (a capture / video scaler): -1 = "soft" (small
#: Gaussian pre-blur + bilinear, looks like INTER_AREA at a fraction of its cost for
#: non-integer ratios). Weighted by repetition.
_INTERPS = (cv2.INTER_AREA, cv2.INTER_LINEAR, cv2.INTER_LINEAR, cv2.INTER_CUBIC,
            -1, -1, -1, cv2.INTER_NEAREST)


def _resize_random(img: np.ndarray, w: int, h: int, rng: np.random.Generator,
                   choices: Sequence[int] = _INTERPS) -> np.ndarray:
    """Resize with a randomly chosen filter (see :data:`_INTERPS`)."""
    interp = _choice(rng, choices)
    if interp == -1:
        ratio = img.shape[1] / float(max(1, w))
        if ratio > 1.15:
            img = cv2.GaussianBlur(img, (0, 0), 0.42 * ratio)
        interp = cv2.INTER_LINEAR
    return cv2.resize(img, (w, h), interpolation=interp)


def _color_lut(rng: np.random.Generator) -> np.ndarray:
    """Per-channel LUT: brightness, contrast, gamma and a small colour cast."""
    x = np.arange(256, dtype=np.float32) / 255.0
    gamma = math.exp(rng.normal(0.0, 0.12))
    contrast = rng.uniform(0.85, 1.2)
    bright = rng.uniform(-0.06, 0.06)
    y = np.power(x, gamma)
    y = (y - 0.5) * contrast + 0.5 + bright
    cast = rng.uniform(0.94, 1.06, 3)
    lut = np.clip(y[:, None] * cast[None, :] * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return lut.reshape(256, 1, 3)


def _degrade(img: np.ndarray, rng: np.random.Generator, size: int, cfg: SynthConfig,
             assets: _Assets) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    """Capture-like degradation. Returns the image and the crop ``(x0, y0, w, h)`` in native
    pixels (continuous) that maps to the output square."""
    n = img.shape[0]
    x0 = y0 = 0.0
    cw = ch = float(n)
    if rng.random() < cfg.p_jitter:
        pad = 6
        canvas = _frame_canvas(img, pad, rng)
        dx, dy = (int(v) for v in rng.integers(-3, 4, 2))
        dw, dh = (int(v) for v in rng.integers(-3, 4, 2))
        cw_i, ch_i = n + dw, n + dh
        img = canvas[pad + dy:pad + dy + ch_i, pad + dx:pad + dx + cw_i]
        x0, y0, cw, ch = float(dx), float(dy), float(cw_i), float(ch_i)
    if rng.random() < cfg.p_video_downscale:
        f = rng.uniform(0.62, 0.95)
        w2 = max(8, int(round(img.shape[1] * f)))
        h2 = max(8, int(round(img.shape[0] * f)))
        img = _resize_random(img, w2, h2, rng, (cv2.INTER_LINEAR, -1))
    if rng.random() < cfg.p_jpeg:
        q = int(rng.integers(cfg.jpeg_quality[0], cfg.jpeg_quality[1] + 1))
        ok, buf = cv2.imencode(".jpg", np.ascontiguousarray(img), [cv2.IMWRITE_JPEG_QUALITY, q])
        if ok:
            dec = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if dec is not None:
                img = dec
    if img.shape[0] != size or img.shape[1] != size:
        img = _resize_random(img, size, size, rng)
    else:
        img = np.ascontiguousarray(img).copy()
    if rng.random() < cfg.p_blur:
        img = cv2.GaussianBlur(img, (0, 0), rng.uniform(0.3, 0.9))
    if rng.random() < cfg.p_color:
        img = cv2.LUT(img, _color_lut(rng))
        sat = rng.uniform(0.7, 1.2)
        if abs(sat - 1.0) > 0.03:
            grey = cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
            img = cv2.addWeighted(img, sat, grey, 1.0 - sat, 0.0)
    if rng.random() < cfg.p_noise:
        sigma = rng.uniform(*cfg.noise_sigma)
        bank = assets.noise
        if size <= bank.shape[0]:
            oy, ox = (int(v) for v in rng.integers(0, bank.shape[0] - size + 1, 2))
            noise = bank[oy:oy + size, ox:ox + size]
        else:
            noise = rng.standard_normal((size, size, 3)).astype(np.float32)
        out = img.astype(np.float32)
        cv2.scaleAdd(noise, float(sigma), out, dst=out)
        img = np.clip(out, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(img), (x0, y0, cw, ch)


# ======================================================================================
# Labels
# ======================================================================================

#: Sample points (unit disc) used to estimate the visible part of each icon.
_DISC_PTS = np.array([(0.0, 0.0)] + [(rr * math.cos(a), rr * math.sin(a))
                                    for rr, k in ((0.45, 8), (0.8, 12))
                                    for a in np.linspace(0, 2 * math.pi, k, endpoint=False)],
                     np.float32)


def _labels(sprites: list[R.ChampionSprite], valid: list[bool], native: int,
            crop: tuple[float, float, float, float]) -> list[dict]:
    """Labels of every labelled icon whose centre is inside the output image."""
    x0, y0, cw, ch = crop
    if not sprites:
        return []
    uu = np.array([(s.u * native - x0) / cw for s in sprites], np.float32)
    vv = np.array([(s.v * native - y0) / ch for s in sprites], np.float32)
    rr = np.array([s.r * native / cw for s in sprites], np.float32)
    out: list[dict] = []
    for i, s in enumerate(sprites):
        if s.label_class is None or s.grey:
            continue
        u, v, r = float(uu[i]), float(vv[i]), float(rr[i])
        if not (0.0 <= u < 1.0 and 0.0 <= v < 1.0):
            continue
        px = u + _DISC_PTS[:, 0] * r
        py = v + _DISC_PTS[:, 1] * r
        vis = (px >= 0) & (px < 1) & (py >= 0) & (py < 1)
        for j in range(i + 1, len(sprites)):   # icons drawn later cover this one
            if abs(uu[j] - u) < rr[j] + r and abs(vv[j] - v) < rr[j] + r:
                vis &= (px - uu[j]) ** 2 + (py - vv[j]) ** 2 > rr[j] ** 2
        out.append({"u": round(u, 5), "v": round(v, 5), "r": round(r, 5),
                    "cls": s.label_class, "cls_valid": bool(valid[i]),
                    "vis": round(float(vis.mean()), 3)})
    return out


# ======================================================================================
# Public API
# ======================================================================================


def sample_native_size(rng: np.random.Generator, cfg: SynthConfig = DEFAULT_CONFIG) -> int:
    """Random native minimap size in pixels (as captured on a real screen)."""
    if rng.random() < cfg.p_common_native:
        n = _choice(rng, cfg.common_natives) + int(rng.integers(-4, 5))
    else:
        n = int(rng.integers(cfg.native_min, cfg.native_max + 1))
    return int(np.clip(n, cfg.native_min, cfg.native_max))


def render_native(rng: np.random.Generator, native: int, cfg: SynthConfig = DEFAULT_CONFIG,
                  assets: _Assets | None = None
                  ) -> tuple[np.ndarray, list[R.ChampionSprite], list[bool], R.Scene]:
    """Random scene rendered at ``native`` px, before any degradation.

    Returns ``(img_bgr, champion sprites in drawing order, cls_valid flags, scene)``.
    """
    A = assets or get_assets()
    builder = _SceneBuilder(rng, A, cfg, native)
    scene, valid = builder.build()
    img = A.renderer.render(scene)
    if rng.random() < cfg.p_ping_wheel:
        _draw_ping_wheel(img, rng, A)
    if rng.random() < cfg.p_bang_button:
        _draw_bang_button(img, rng)
    return img, scene.champions, valid, scene


def generate_sample(rng: np.random.Generator, size: int = 256, *, native_size: int | None = None,
                    cfg: SynthConfig | None = None) -> tuple[np.ndarray, list[dict]]:
    """One synthetic training sample.

    Returns ``(img_bgr uint8 [size, size, 3], labels)`` with one label
    ``{"u", "v", "r", "cls", "cls_valid", "vis"}`` per champion icon whose centre lies inside
    the image (``r`` = radius / image width, ``cls`` in ``"enemy" | "ally"`` - the local
    player is labelled ``"ally"`` -, ``cls_valid`` False for random-hue rings, ``vis`` = the
    visible fraction of the icon, 0..1). Deterministic for a given ``rng`` state.
    """
    cfg = cfg or DEFAULT_CONFIG
    size = int(max(16, min(1024, size)))
    A = get_assets()
    native = int(native_size) if native_size else sample_native_size(rng, cfg)
    native = int(max(32, min(1024, native)))
    img, sprites, valid, _ = render_native(rng, native, cfg, A)
    out, crop = _degrade(img, rng, size, cfg, A)
    return out, _labels(sprites, valid, native, crop)


# ======================================================================================
# CLI: previews, contact sheet, comparison with real crops, benchmark
# ======================================================================================

_LABEL_BGR = {"enemy": (60, 60, 255), "ally": (255, 200, 60), "self": (60, 230, 255)}


def draw_labels(img: np.ndarray, labels: list[dict]) -> np.ndarray:
    """Copy of ``img`` with the labels drawn (circle per icon, magenta if cls_valid False)."""
    out = img.copy()
    s = out.shape[1]
    for lab in labels:
        col = _LABEL_BGR.get(lab["cls"], (255, 255, 255)) if lab["cls_valid"] else (255, 0, 255)
        c = (int(round(lab["u"] * s * 4)), int(round(lab["v"] * s * 4)))
        cv2.circle(out, c, int(round(lab["r"] * s * 4)), col, 1, cv2.LINE_AA, 2)
        cv2.circle(out, c, 4, col, -1, cv2.LINE_AA, 2)
    return out


def contact_sheet(images: list[np.ndarray], cols: int = 4, gap: int = 4) -> np.ndarray:
    """Grid of equally sized BGR images."""
    if not images:
        return np.zeros((8, 8, 3), np.uint8)
    h, w = images[0].shape[:2]
    rows = (len(images) + cols - 1) // cols
    sheet = np.full((rows * (h + gap) + gap, cols * (w + gap) + gap, 3), 30, np.uint8)
    for i, im in enumerate(images):
        r, c = divmod(i, cols)
        if im.shape[:2] != (h, w):
            im = cv2.resize(im, (w, h), interpolation=cv2.INTER_AREA)
        sheet[gap + r * (h + gap):gap + r * (h + gap) + h, gap + c * (w + gap):gap + c * (w + gap) + w] = im
    return sheet


def _write_png(path: Path, img: np.ndarray) -> None:
    ok, buf = cv2.imencode(".png", img)
    if ok:
        buf.tofile(str(path))


def _cmd_preview(n: int, out: Path, size: int, seed: int, native: int | None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    tiles = []
    for i in range(n):
        img, labels = generate_sample(rng, size, native_size=native)
        drawn = draw_labels(img, labels)
        _write_png(out / f"synth_{i:03d}.png", drawn)
        (out / f"synth_{i:03d}.json").write_text(json.dumps(labels), encoding="utf-8")
        tiles.append(drawn)
    _write_png(out / "contact_sheet.png", contact_sheet(tiles, cols=min(4, max(1, n))))
    print(f"{n} previews written to {out}")


def _cmd_compare(real_dir: Path, out: Path, seed: int, per_real: int) -> None:
    """Sheet: each real crop next to synthetic samples at the same native size."""
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    reals = sorted(p for p in real_dir.glob("minimap_crop_*.png"))
    rows = []
    for p in reals:
        real = cv2.imread(str(p))
        if real is None:
            continue
        n = real.shape[0]
        row = [real]
        for _ in range(per_real):
            img, _ = generate_sample(rng, n, native_size=n)
            row.append(img)
        rows.append(contact_sheet(row, cols=len(row)))
    if rows:
        w = max(r.shape[1] for r in rows)
        rows = [cv2.copyMakeBorder(r, 0, 0, 0, w - r.shape[1], cv2.BORDER_CONSTANT, value=(30, 30, 30))
                for r in rows]
        _write_png(out / "compare_real_synth.png", np.vstack(rows))
        print(f"comparison sheet written to {out / 'compare_real_synth.png'}")
    else:
        print(f"no minimap_crop_*.png in {real_dir}")


def _cmd_bench(n: int, size: int, seed: int) -> float:
    cv2.setNumThreads(1)
    rng = np.random.default_rng(seed)
    for _ in range(20):                    # warm-up (asset loading, caches)
        generate_sample(rng, size)
    t0 = time.perf_counter()
    n_labels = 0
    for _ in range(n):
        _, labels = generate_sample(rng, size)
        n_labels += len(labels)
    dt = time.perf_counter() - t0
    rate = n / dt
    print(f"{rate:.1f} samples/s at size {size} (1 OpenCV thread, {n} samples, "
          f"{n_labels / n:.2f} labels/sample)")
    return rate


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Synthetic LoL minimap generator (TreeAI Coach)")
    ap.add_argument("--preview", type=int, default=0, help="write N previews + a contact sheet")
    ap.add_argument("--out", type=Path, default=Path("synth_preview"))
    ap.add_argument("--size", type=int, default=256)
    ap.add_argument("--native", type=int, default=None, help="force the native minimap size")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--bench", action="store_true", help="measure samples/s on one core")
    ap.add_argument("--bench-n", type=int, default=400)
    ap.add_argument("--compare", type=Path, default=None,
                    help="folder with real minimap_crop_*.png: side-by-side sheet")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    if args.preview:
        _cmd_preview(args.preview, args.out, args.size, args.seed, args.native)
    if args.compare is not None:
        _cmd_compare(args.compare, args.out, args.seed, 3)
    if args.bench:
        _cmd_bench(args.bench_n, args.size, args.seed)
    if not (args.preview or args.bench or args.compare is not None):
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
