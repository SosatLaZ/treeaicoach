"""Pure renderers of the visual overlay: radar, HUD panel and danger screen-edge flash.

Everything here is numpy + PIL (fonts only) + OpenCV (resizing): no window, no Win32, so it is
testable on Linux and reused by the UI for its live / static previews. ``overlay.py`` only
puts the returned images on screen with ``UpdateLayeredWindow``.

Rendering model
    A small :class:`Canvas` holds a *premultiplied* RGBA float32 image. Shapes are drawn with
    analytic anti-aliasing (signed distance fields: discs, rings, dashed rings, rounded
    rectangles, capsules, convex polygons), text with FreeType (PIL) and icons with area
    resampling, then the canvas is converted to premultiplied **BGRA uint8** - the pixel format
    ``UpdateLayeredWindow`` expects (``AC_SRC_ALPHA``).

Palette (TreeAI, deliberately not the game's gold / navy): slate ``#94A3B8``, text ``#F1F5F9``,
sky ``#38BDF8``, danger ``#F85149``, warning ``#FBBF24``, safe ``#34D399``, graphite panels
``#0F1218`` (~85 % opacity, 1 px slate border, rounded corners).

Fonts: Segoe UI / Segoe UI Bold (Windows), DejaVu Sans (Linux), PIL's default font as the last
resort; cached. French text with accents is rendered as is (UTF-8).

``python -m treeaicoach.overlay_render --demo OUTDIR`` writes sample renders (safe, warning,
danger with the jungler's fog region, late game).
"""

from __future__ import annotations

import argparse
import copy
import logging
import math
import os
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from treeaicoach.fog_tracker import FogEstimate

if TYPE_CHECKING:  # pragma: no cover - typing only
    from treeaicoach.capture import Rect
    from treeaicoach.objectives import ObjectiveState

log = logging.getLogger(__name__)

# ======================================================================================
# Palette (RGB 0..255) and tunable constants
# ======================================================================================
# Legacy names kept for callers, remapped onto the TreeAI palette (no Riot gold / navy look on
# the minimap layer or the radar either).
GOLD = (148, 163, 184)          # #94A3B8 slate (borders, neutral marks)
GOLD_LIGHT = (241, 245, 249)    # #F1F5F9 text
GOLD_DARK = (71, 85, 105)       # #475569
TEAL = (56, 189, 248)           # #38BDF8 sky
DANGER = (248, 81, 73)          # #F85149
WARNING = (251, 191, 36)        # #FBBF24
SAFE = (52, 211, 153)           # #34D399
PANEL = (15, 18, 24)            # #0F1218 graphite
PANEL_DEEP = (8, 10, 14)        # #080A0E
MUTED = (148, 163, 184)         # #94A3B8
GREY = (100, 110, 125)          # #646E7D
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
ALLY_BLUE = (78, 152, 218)

# TreeAI in-game identity (HUD card, toasts): graphite panels, sky/cyan accents, our own
# warning / danger colours -- deliberately NOT Riot's gold / navy client look (third-party policy:
# an overlay must not mimic the game UI) and branded "TreeAI".
TAI_PANEL = (15, 18, 24)        # #0F1218 graphite
TAI_PANEL_TOP = (30, 35, 45)    # #1E232D
TAI_EDGE = (71, 85, 105)        # #475569 slate hairline
TAI_TEXT = (241, 245, 249)      # #F1F5F9
TAI_MUTED = (148, 163, 184)     # #94A3B8
TAI_INFO = (56, 189, 248)       # #38BDF8 sky (calm accent)
TAI_GO = (52, 211, 153)         # #34D399 emerald
TAI_GO_SOFT = (134, 239, 172)   # #86EFAC
TAI_WARN = (251, 191, 36)       # #FBBF24 amber
TAI_DANGER = (248, 81, 73)      # #F85149
TAI_BRAND = (132, 225, 100)     # #84E164 TreeAI leaf
TAI_THREAT = {0: TAI_GO, 1: TAI_WARN, 2: TAI_DANGER}
PANEL_ALPHA = 0.86
BORDER_ALPHA = 0.85
THREAT_COLORS = {0: SAFE, 1: WARNING, 2: DANGER}
THREAT_DEFAULT_TEXT = {0: "SÛR", 1: "ATTENTION", 2: "DANGER"}
#: Stance pill (coach.StanceAdvisor): key -> (label, colour)
STANCE_STYLE: dict[str, tuple[str, tuple[int, int, int]]] = {
    "prudent": ("PRUDENT", DANGER), "equilibre": ("ÉQUILIBRÉ", WARNING), "agressif": ("AGRESSIF", SAFE),
}
LAST_SEEN_MAX_S = 60.0          # invisible enemies are drawn at their last position this long
ALERT_FADE_S = 4.0              # the "last alert" line fades out over this duration
HALO_PERIOD_S = 1.1             # jungler halo pulse period
MAX_MAP_ELEMENTS = 6            # minimap layer: at most this many guides + approach arrows
GUIDE_RGB = {"gold": (251, 191, 36), "danger": (248, 81, 73), "safe": (52, 211, 153), "teal": (56, 189, 248)}
GUIDE_MAX_LEN = 0.30            # guide arrows are at most this long (fraction of the minimap)
WARD_ICON = "minimap_ward_green_full.png"
GUIDE_CLEAR_R = 0.055           # nothing of a guide is drawn this close to a champion icon centre

#: Objective display name (FR) -> minimap icon file (assets/icons/minimap).
OBJECTIVE_ICONS: dict[str, str] = {
    "dragon": "dragon.png",
    "dragon ancestral": "dragon_elder.png",
    "baron": "baron.png",
    "héraut": "riftherald.png",
    "heraut": "riftherald.png",
    "larves": "grub.png",
    "atakhan": "atakhan_r.png",
}
#: Short labels for the HUD objectives row.
OBJECTIVE_SHORT: dict[str, str] = {"dragon ancestral": "Ancestral"}

_MAX_SIDE = 8192


# ======================================================================================
# Public data (ARCHITECTURE.md §7.2)
# ======================================================================================
@dataclass
class EnemyView:
    """One enemy champion as shown by the overlay."""

    key: str
    alias: str | None = None
    name: str = ""
    visible: bool = False
    uv: tuple[float, float] | None = None       # current (visible) or last seen position
    last_seen_ago: float | None = None          # seconds since last seen (None = never seen)
    is_jungler: bool = False
    approaching: bool = False
    icon: np.ndarray | None = None              # RGBA uint8 (ChampionDB.load_icon)
    velocity: tuple[float, float] | None = None  # normalized / s (optional, for the arrow)
    relation: str = "enemy"                     # "enemy" | "ally"
    role: str | None = None                     # "TOP" | "JUNGLE" | "MIDDLE" | "BOTTOM" | "UTILITY" | None
    # pipeline v2: freshness of ``uv`` (render-time prediction, stale ghosts)
    age: float | None = None                    # s since the observation behind ``uv`` (None = unknown)
    stacked: bool = False                       # hidden under another icon: ``uv`` is the occluder's
    confidence: float = 1.0                     # identity confidence (anonymous track: < 1)


@dataclass
class OverlayState:
    """Immutable-by-convention snapshot of everything the overlay shows."""

    minimap_rect: "Rect | None" = None
    screen_rect: "Rect | None" = None
    me_uv: tuple[float, float] | None = None
    my_team: str | None = None
    enemies: list[EnemyView] = field(default_factory=list)
    fogs: list[FogEstimate] = field(default_factory=list)
    threat_level: int = 0                     # 0 safe, 1 warning, 2 danger
    threat_text: str = "SÛR"
    last_alert: tuple[str, int, float] | None = None   # (text, level, age s)
    objectives: list["ObjectiveState"] = field(default_factory=list)
    game_time: float | None = None
    warn_radius: float = 0.22
    danger_radius: float = 0.12
    flash: float = 0.0                        # 0..1 danger flash intensity
    jungler_line: str | None = None
    hint: str | None = None
    me_icon: np.ndarray | None = None         # my champion icon (RGBA), optional
    allies: list[EnemyView] = field(default_factory=list)   # allied champions (relation "ally"), without me
    roles: dict[str, str] = field(default_factory=dict)     # champion key / alias -> role (API position)
    insight: str | None = None                # most relevant live macro insight (coach.MapCoach), one HUD line
    toasts: list = field(default_factory=list)  # toasts.ToastView list (top-centre banners), optional
    # v2 stance + written tips (coach.StanceAdvisor / tips.TipRotator)
    stance: str | None = None                 # "prudent" | "equilibre" | "agressif" | None
    stance_reason: str | None = None          # short reason ("+1 niveau sur Darius, jungler vu en bas")
    tip: str | None = None                    # the one written tip line of the HUD (rotating, never spoken)
    # decluttered minimap layer (config toggles, all off by default)
    show_allies: bool = False                 # rings on allies + me
    show_roles: bool = False                  # role tags on enemies (the jungler always gets "JGL")
    show_ghosts: bool = False                 # last seen marks / fog zones of every hidden enemy
    show_last_seen: bool = True               # dashed mark + "12 s" at the last point an enemy was seen
    hud_detailed: bool = False                # HUD: jungler line + 5 enemy portraits
    # v3 visual guides (tactics.TacticalDirector): arrows / ward spots on the minimap layer
    guides: list = field(default_factory=list)  # tactics.MapGuide list, highest priority first
    phase: str | None = None                  # "laning" | "mid" | "late" | "end" (phase.py)
    role_notice: str | None = None            # "Rôle détecté : MID (échange de voie)" (roles.RoleResolver)
    # ward guide in the game view (ward_guide.WorldMarker list, screen px): ground marker / edge arrow
    world: list = field(default_factory=list)
    # HUD v3 card: "jouer plus fort ou non" gauge + ONE advice line (fade) + 2 chips
    gauge: int | None = None                  # -2 SAFE .. 2 ATTAQUE (coach.PlayGauge); None: from ``stance``
    gauge_reason: str | None = None           # short reason ("combat gagnable (68 % de chances)")
    gauge_since: float | None = None          # monotonic time the step was shown (fade)
    tip_tone: str | None = None               # "danger" | "warning" | "go" | "info": accent colour
    tip_since: float | None = None            # monotonic time the advice line appeared (fade)
    item_hint: str | None = None              # "Achète Zhonya (3 250 or)": chip, in base only
    in_base: bool = False
    ai_counter: str | None = None             # "IA 3/5": chip when nothing more useful
    # pipeline v2: render-time prediction (engine.predict_positions): callable() ->
    # {track key: ((u, v) now, age s)}; the overlay thread re-positions the views at each frame
    predict: Any = None
    me_key: str | None = None                 # track key of my champion (for ``predict``)
    me_dead: bool = False                     # I am dead: the HUD shows the respawn countdown, no threat
    respawn_s: float | None = None            # seconds before I respawn (Live Client), None = unknown


#: A visible champion whose data is older than this (s) - or stacked under another icon, or not
#: identified - is drawn as a faint dashed ghost WITHOUT role label (never a confident ring on a
#: position that may be wrong).
GHOST_AGE_S = 0.7
#: At most this many text labels on the minimap layer at once (priority: visible jungler, jungler
#: last seen / fog timer, enemy roles, ally roles).
MM_MAX_LABELS = 4
GHOST_MIN_CONFIDENCE = 0.5


def is_ghost(view: Any) -> bool:
    """True when ``view`` must be drawn as an uncertain ghost (see :data:`GHOST_AGE_S`)."""
    try:
        if bool(getattr(view, "stacked", False)):
            return True
        age = getattr(view, "age", None)
        if age is not None and _finite(age) and float(age) > GHOST_AGE_S:
            return True
        conf = getattr(view, "confidence", 1.0)
        return conf is not None and _finite(conf) and float(conf) < GHOST_MIN_CONFIDENCE
    except Exception:
        return False


def with_predicted(state: Any, positions: dict | None = None) -> Any:
    """Copy of ``state`` whose visible champions (and ``me_uv``) are moved to the positions
    predicted for *now* (``state.predict()`` unless ``positions`` is given) - the minimap layer
    then follows the real icons between two detections instead of trailing them. Views without a
    prediction are unchanged. Never raises (returns ``state`` on error)."""
    try:
        if positions is None:
            fn = getattr(state, "predict", None)
            if not callable(fn):
                return state
            positions = fn()
        if not positions:
            return state

        def move(views: Any) -> list:
            out = []
            for v in list(views or []):
                p = positions.get(getattr(v, "key", None)) if v is not None and getattr(v, "visible", False) else None
                if p is None:
                    out.append(v)
                    continue
                (u, vv), age = p
                nv = copy.copy(v)
                nv.uv, nv.age = (float(u), float(vv)), float(age)
                out.append(nv)
            return out

        new = copy.copy(state)
        new.enemies = move(getattr(state, "enemies", None))
        new.allies = move(getattr(state, "allies", None))
        mk = getattr(state, "me_key", None)
        if mk and mk in positions and getattr(state, "me_uv", None) is not None:
            new.me_uv = positions[mk][0]
        return new
    except Exception:
        log.debug("with_predicted failed", exc_info=True)
        return state


# ======================================================================================
# Small helpers
# ======================================================================================
def _rgb(c: Sequence[float]) -> np.ndarray:
    return np.asarray(c[:3], dtype=np.float32) / 255.0


def _mix(a: Sequence[float], b: Sequence[float], t: float) -> tuple[float, float, float]:
    return tuple(float(a[i]) * (1.0 - t) + float(b[i]) * t for i in range(3))  # type: ignore[return-value]


def _finite(*vals: Any) -> bool:
    try:
        return all(math.isfinite(float(v)) for v in vals)
    except (TypeError, ValueError):
        return False


def _clamp01(x: Any) -> float:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(f):
        return 0.0
    return 0.0 if f < 0 else 1.0 if f > 1 else f


def _uv_ok(uv: Any) -> tuple[float, float] | None:
    try:
        u, v = float(uv[0]), float(uv[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    if not (math.isfinite(u) and math.isfinite(v)):
        return None
    return min(max(u, 0.0), 1.0), min(max(v, 0.0), 1.0)


def fmt_clock(seconds: float | None) -> str:
    """``m:ss`` (``h:mm:ss`` over an hour); ``--:--`` when unknown."""
    if seconds is None or not _finite(seconds):
        return "--:--"
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def fmt_seconds(seconds: float | None) -> str:
    """``23 s`` below a minute, ``1:05`` above."""
    if seconds is None or not _finite(seconds):
        return "?"
    s = max(0, int(seconds))
    return f"{s} s" if s < 60 else fmt_clock(s)


class _LRU:
    """Tiny thread-safe LRU cache."""

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
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


# ======================================================================================
# Fonts
# ======================================================================================
def _windows_font_dir() -> Path:
    return Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


_FONT_FILES: dict[str, tuple[str, ...]] = {
    "regular": ("segoeui.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf", "arial.ttf"),
    "semibold": ("seguisb.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf",
                 "arialbd.ttf"),
    "bold": ("segoeuib.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "arialbd.ttf"),
}
_FONT_DIRS: tuple[Path, ...] = (
    _windows_font_dir(),
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/dejavu"),
    Path("/usr/share/fonts/TTF"),
    Path("/usr/share/fonts/truetype/liberation"),
    Path("/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
)
_font_cache: dict[tuple[str, int], Any] = {}
_font_lock = threading.Lock()
_font_warned = False


def get_font(size: int, weight: str = "regular") -> Any:
    """Cached PIL font (``weight`` = "regular" | "semibold" | "bold"). Never raises."""
    global _font_warned
    size = int(min(max(int(size), 6), 200))
    weight = weight if weight in _FONT_FILES else "regular"
    key = (weight, size)
    with _font_lock:
        cached = _font_cache.get(key)
        if cached is not None:
            return cached
        font = None
        for name in _FONT_FILES[weight]:
            candidates = [d / name for d in _FONT_DIRS] + [Path(name)]
            for cand in candidates:
                try:
                    if cand.is_absolute() and not cand.is_file():
                        continue
                    font = ImageFont.truetype(str(cand), size)
                    break
                except (OSError, ValueError):
                    continue
            if font is not None:
                break
        if font is None:
            if not _font_warned:
                _font_warned = True
                log.warning("No TrueType font found; using PIL's default font")
            try:
                font = ImageFont.load_default(size=size)
            except TypeError:  # Pillow < 10.1
                font = ImageFont.load_default()
        _font_cache[key] = font
        return font


def _cap_height(font: Any) -> float:
    try:
        bb = font.getbbox("H", anchor="ls")
        return float(-bb[1])
    except Exception:
        return float(getattr(font, "size", 10)) * 0.7


def text_width(text: str, font: Any) -> float:
    """Advance width of ``text`` in pixels."""
    try:
        return float(font.getlength(text))
    except Exception:
        try:
            bb = font.getbbox(text)
            return float(bb[2] - bb[0])
        except Exception:
            return 7.0 * len(text)


def fit_text(text: str, font: Any, max_w: float) -> str:
    """``text`` shortened with an ellipsis so that it fits in ``max_w`` pixels."""
    if text_width(text, font) <= max_w:
        return text
    ell = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if text_width(text[:mid].rstrip() + ell, font) <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return (text[:lo].rstrip(" ,;:—-") + ell) if lo > 0 else ell


def wrap_text(text: str, font: Any, max_w: float, max_lines: int = 2) -> list[str]:
    """Greedy word wrap into at most ``max_lines`` lines (the last one ellipsized)."""
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    cur = ""
    for i, w in enumerate(words):
        cand = f"{cur} {w}".strip()
        if text_width(cand, font) <= max_w or not cur:
            cur = cand
            continue
        lines.append(cur)
        cur = w
        if len(lines) == max_lines - 1:
            cur = " ".join(words[i:])
            break
    lines.append(cur)
    return [fit_text(line, font, max_w) for line in lines[:max_lines]]


_text_cache = _LRU(768)


def _text_mask(text: str, font: Any, stroke: int = 0) -> tuple[np.ndarray, int, int]:
    """Coverage mask (float32 0..1) of ``text`` + offset of its top-left from the baseline origin."""
    key = (text, id(font), stroke)
    hit = _text_cache.get(key)
    if hit is not None:
        return hit
    try:
        l, t, r, b = font.getbbox(text, anchor="ls", stroke_width=stroke)
    except TypeError:
        l, t, r, b = font.getbbox(text)
    w, h = max(1, int(r - l) + 2), max(1, int(b - t) + 2)
    img = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(img)
    try:
        draw.text((-l + 1, -t + 1), text, font=font, fill=255, anchor="ls",
                  stroke_width=stroke, stroke_fill=255)
    except TypeError:
        draw.text((-l + 1, -t + 1), text, font=font, fill=255)
    mask = np.asarray(img, dtype=np.float32) * (1.0 / 255.0)
    mask.setflags(write=False)
    res = (mask, int(l) - 1, int(t) - 1)
    _text_cache.put(key, res)
    return res


# ======================================================================================
# Canvas with analytic anti-aliasing (premultiplied RGBA float32)
# ======================================================================================
def _span(c0: float, c1: float, limit: int) -> tuple[int, int]:
    return max(0, int(math.floor(c0)) - 1), min(limit, int(math.ceil(c1)) + 1)


class Canvas:
    """Premultiplied RGBA float32 drawing surface (origin top-left, pixel centres at +0.5)."""

    def __init__(self, width: int, height: int) -> None:
        self.w = int(min(max(1, width), _MAX_SIDE))
        self.h = int(min(max(1, height), _MAX_SIDE))
        self.px = np.zeros((self.h, self.w, 4), np.float32)

    # ------------------------------------------------------------------ compositing
    def paint(self, x0: int, y0: int, cov: np.ndarray, rgb: Any, alpha: float = 1.0) -> None:
        """Composite colour ``rgb`` (0..255 tuple or float array 0..1) with coverage ``cov`` at (x0, y0)."""
        if alpha <= 0.0 or cov.size == 0:
            return
        h, w = cov.shape[:2]
        X0, Y0, X1, Y1 = max(x0, 0), max(y0, 0), min(x0 + w, self.w), min(y0 + h, self.h)
        if X0 >= X1 or Y0 >= Y1:
            return
        if (Y1 - Y0) * (X1 - X0) > 4096:
            # skip fully transparent rows / columns (borders, sparse layers): big CPU saving
            sub = cov[Y0 - y0:Y1 - y0, X0 - x0:X1 - x0] > 0
            rows = np.flatnonzero(sub.any(axis=1))
            if rows.size == 0:
                return
            cols = np.flatnonzero(sub[rows[0]:rows[-1] + 1].any(axis=0))
            Y0, Y1 = Y0 + int(rows[0]), Y0 + int(rows[-1]) + 1
            X0, X1 = X0 + int(cols[0]), X0 + int(cols[-1]) + 1
        sy, sx = slice(Y0 - y0, Y1 - y0), slice(X0 - x0, X1 - x0)
        a = cov[sy, sx] * np.float32(alpha)
        if isinstance(rgb, np.ndarray) and rgb.ndim == 3:
            col = rgb[sy, sx if rgb.shape[1] > 1 else slice(None)]
        else:
            col = rgb if isinstance(rgb, np.ndarray) else _rgb(rgb)
        dst = self.px[Y0:Y1, X0:X1]
        a4 = a[..., None]
        dst *= 1.0 - a4
        if isinstance(col, np.ndarray) and col.ndim == 3:
            dst[..., :3] += col * a4
            dst[..., 3] += a
        else:   # uniform colour: one fused 4-channel multiply-add
            dst += a4 * np.append(np.asarray(col, np.float32).reshape(-1)[:3], np.float32(1.0))

    def paint_premul(self, x0: int, y0: int, patch: np.ndarray, opacity: float = 1.0) -> None:
        """Composite a premultiplied RGBA float32 patch with its top-left at (x0, y0)."""
        if opacity <= 0.0:
            return
        h, w = patch.shape[:2]
        X0, Y0, X1, Y1 = max(x0, 0), max(y0, 0), min(x0 + w, self.w), min(y0 + h, self.h)
        if X0 >= X1 or Y0 >= Y1:
            return
        src = patch[Y0 - y0:Y1 - y0, X0 - x0:X1 - x0]
        if opacity < 1.0:
            src = src * np.float32(opacity)
        dst = self.px[Y0:Y1, X0:X1]
        dst *= (1.0 - src[..., 3:4])
        dst += src

    # ------------------------------------------------------------------ primitives
    def _grid(self, x0: float, y0: float, x1: float, y1: float
              ) -> tuple[int, int, np.ndarray, np.ndarray] | None:
        X0, X1 = _span(x0, x1, self.w)
        Y0, Y1 = _span(y0, y1, self.h)
        if X0 >= X1 or Y0 >= Y1:
            return None
        xs = np.arange(X0, X1, dtype=np.float32) + np.float32(0.5)
        ys = np.arange(Y0, Y1, dtype=np.float32) + np.float32(0.5)
        return X0, Y0, xs[None, :], ys[:, None]

    def disc(self, cx: float, cy: float, r: float, rgb: Any, alpha: float = 1.0) -> None:
        """Filled anti-aliased disc."""
        if not _finite(cx, cy, r) or r <= 0:
            return
        g = self._grid(cx - r, cy - r, cx + r, cy + r)
        if g is None:
            return
        X0, Y0, xs, ys = g
        d = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
        self.paint(X0, Y0, np.clip(r - d + 0.5, 0.0, 1.0), rgb, alpha)

    def glow(self, cx: float, cy: float, r0: float, r1: float, rgb: Any, alpha: float = 1.0) -> None:
        """Soft radial glow: full ``alpha`` inside ``r0`` fading (quadratically) to 0 at ``r1``."""
        if not _finite(cx, cy, r0, r1) or r1 <= 0:
            return
        g = self._grid(cx - r1, cy - r1, cx + r1, cy + r1)
        if g is None:
            return
        X0, Y0, xs, ys = g
        d = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)
        t = np.clip((r1 - d) / max(1e-3, r1 - r0), 0.0, 1.0)
        self.paint(X0, Y0, t * t, rgb, alpha)

    def ring(self, cx: float, cy: float, r: float, width: float, rgb: Any, alpha: float = 1.0,
             dash: tuple[float, float] | None = None, phase: float = 0.0) -> None:
        """Anti-aliased circle outline (optionally dashed: ``dash = (on_px, off_px)``)."""
        if not _finite(cx, cy, r, width) or r <= 0 or width <= 0:
            return
        ext = r + width / 2 + 1
        g = self._grid(cx - ext, cy - ext, cx + ext, cy + ext)
        if g is None:
            return
        X0, Y0, xs, ys = g
        dx, dy = xs - cx, ys - cy
        d = np.sqrt(dx * dx + dy * dy)
        cov = np.clip(width * 0.5 - np.abs(d - r) + 0.5, 0.0, 1.0)
        if width < 1.0:
            cov *= width
        if dash is not None:
            on, off = float(dash[0]), float(dash[1])
            circ = 2.0 * math.pi * r
            n = max(3, int(round(circ / max(1.0, on + off))))
            period = circ / n
            on = period * on / (on + off)
            s = (np.arctan2(dy, dx) + math.pi) * r + phase * period
            m = np.mod(s, period)
            cov *= np.clip(np.minimum(m, on - m) + 0.5, 0.0, 1.0)
        self.paint(X0, Y0, cov, rgb, alpha)

    def rrect(self, x: float, y: float, w: float, h: float, radius: float, rgb: Any,
              alpha: float = 1.0, border: Any = None, border_alpha: float = 1.0,
              border_w: float = 1.0) -> None:
        """Rounded rectangle, filled with ``rgb`` (tuple or vertical gradient array) + optional inner border."""
        if w <= 0 or h <= 0 or not _finite(x, y, w, h, radius):
            return
        g = self._grid(x, y, x + w, y + h)
        if g is None:
            return
        X0, Y0, xs, ys = g
        rad = float(min(max(radius, 0.0), w / 2, h / 2))
        qx = np.abs(xs - (x + w / 2)) - (w / 2 - rad)
        qy = np.abs(ys - (y + h / 2)) - (h / 2 - rad)
        sdf = (np.sqrt(np.maximum(qx, 0) ** 2 + np.maximum(qy, 0) ** 2)
               + np.minimum(np.maximum(qx, qy), 0) - rad)
        fill = np.clip(0.5 - sdf, 0.0, 1.0)
        if rgb is not None:
            col = rgb
            if isinstance(rgb, np.ndarray) and rgb.ndim == 3 and rgb.shape[0] != fill.shape[0]:
                # gradient given for [y, y+h): resample to the grid rows
                rows = np.clip(((ys[:, 0] - y) / max(h, 1e-3) * rgb.shape[0]).astype(int), 0, rgb.shape[0] - 1)
                col = rgb[rows]
            self.paint(X0, Y0, fill, col, alpha)
        if border is not None and border_alpha > 0 and border_w > 0:
            ring = fill - np.clip(0.5 - (sdf + border_w), 0.0, 1.0)
            gh, gw = ring.shape
            band = int(math.ceil(rad + border_w + 2))
            if gh > 2 * band + 4 and gw > 2 * band + 4:
                # only the four edge strips carry the border: paint them, not the whole interior
                for (r0, r1, c0, c1) in ((0, band, 0, gw), (gh - band, gh, 0, gw),
                                         (band, gh - band, 0, band), (band, gh - band, gw - band, gw)):
                    self.paint(X0 + c0, Y0 + r0, ring[r0:r1, c0:c1], border, border_alpha)
            else:
                self.paint(X0, Y0, ring, border, border_alpha)

    def capsule(self, x0: float, y0: float, x1: float, y1: float, width: float, rgb: Any,
                alpha: float = 1.0) -> None:
        """Thick anti-aliased segment with round caps."""
        if not _finite(x0, y0, x1, y1, width) or width <= 0:
            return
        ext = width / 2 + 1
        g = self._grid(min(x0, x1) - ext, min(y0, y1) - ext, max(x0, x1) + ext, max(y0, y1) + ext)
        if g is None:
            return
        X0, Y0, xs, ys = g
        vx, vy = x1 - x0, y1 - y0
        ll = vx * vx + vy * vy
        px, py = xs - x0, ys - y0
        if ll < 1e-9:
            d = np.sqrt(px * px + py * py)
        else:
            tt = np.clip((px * vx + py * vy) / ll, 0.0, 1.0)
            d = np.sqrt((px - tt * vx) ** 2 + (py - tt * vy) ** 2)
        cov = np.clip(width * 0.5 - d + 0.5, 0.0, 1.0)
        self.paint(X0, Y0, cov, rgb, alpha)

    def polygon(self, pts: Sequence[tuple[float, float]], rgb: Any, alpha: float = 1.0,
                grow: float = 0.0) -> None:
        """Convex polygon (any winding), optionally grown by ``grow`` px (outline halo)."""
        if len(pts) < 3:
            return
        p = np.asarray(pts, dtype=np.float32)
        if not np.all(np.isfinite(p)):
            return
        area = 0.0
        for i in range(len(p)):
            x_a, y_a = p[i]
            x_b, y_b = p[(i + 1) % len(p)]
            area += float(x_a * y_b - x_b * y_a)
        if abs(area) < 1e-6:
            return
        sign = 1.0 if area > 0 else -1.0
        ext = grow + 1
        g = self._grid(p[:, 0].min() - ext, p[:, 1].min() - ext, p[:, 0].max() + ext, p[:, 1].max() + ext)
        if g is None:
            return
        X0, Y0, xs, ys = g
        sdf = None
        for i in range(len(p)):
            ax, ay = p[i]
            bx, by = p[(i + 1) % len(p)]
            ex, ey = bx - ax, by - ay
            ln = math.hypot(ex, ey)
            if ln < 1e-6:
                continue
            # outward normal for this winding
            nx, ny = sign * ey / ln, -sign * ex / ln
            dist = (xs - ax) * nx + (ys - ay) * ny
            sdf = dist if sdf is None else np.maximum(sdf, dist)
        if sdf is None:
            return
        self.paint(X0, Y0, np.clip(0.5 - (sdf - grow), 0.0, 1.0), rgb, alpha)

    def text(self, x: float, y: float, text: str, font: Any, rgb: Any, alpha: float = 1.0,
             anchor: str = "l", shadow: float = 0.55, outline: int = 0,
             outline_rgb: Any = BLACK, outline_alpha: float = 0.75) -> float:
        """Draw one line of text vertically centred (cap height) on ``y``; returns its width.

        ``anchor``: "l" (x = left), "m" (x = centre) or "r" (x = right).
        """
        if not text or alpha <= 0:
            return 0.0
        tw = text_width(text, font)
        if anchor == "m":
            ox = x - tw / 2
        elif anchor == "r":
            ox = x - tw
        else:
            ox = x
        base = y + _cap_height(font) / 2
        bx, by = int(round(ox)), int(round(base))
        if outline > 0:
            m, dx, dy = _text_mask(text, font, outline)
            self.paint(bx + dx, by + dy, m, outline_rgb, alpha * outline_alpha)
        m, dx, dy = _text_mask(text, font, 0)
        if shadow > 0 and outline <= 0:
            self.paint(bx + dx, by + dy + 1, m, BLACK, alpha * shadow)
        self.paint(bx + dx, by + dy, m, rgb, alpha)
        return tw

    def image(self, cx: float, cy: float, patch: np.ndarray, opacity: float = 1.0) -> None:
        """Composite a premultiplied patch centred on (cx, cy)."""
        h, w = patch.shape[:2]
        self.paint_premul(int(round(cx - w / 2)), int(round(cy - h / 2)), patch, opacity)

    # ------------------------------------------------------------------ output
    def to_bgra(self) -> np.ndarray:
        """Premultiplied BGRA uint8."""
        out = np.empty((self.h, self.w, 4), np.uint8)
        px = np.clip(self.px, 0.0, 1.0) * np.float32(255.0)
        px += np.float32(0.5)
        out[..., 0] = px[..., 2]
        out[..., 1] = px[..., 1]
        out[..., 2] = px[..., 0]
        out[..., 3] = px[..., 3]
        return out


# ======================================================================================
# Icons
# ======================================================================================
_icon_cache = _LRU(160)
_asset_icon_cache: dict[str, np.ndarray | None] = {}
_asset_lock = threading.Lock()


def _icon_fingerprint(icon: np.ndarray) -> tuple:
    try:
        return (icon.shape, hash(np.ascontiguousarray(icon).tobytes()))
    except Exception:
        return (id(icon),)


def _as_rgba_icon(icon: Any) -> np.ndarray | None:
    try:
        arr = np.asarray(icon)
    except Exception:
        return None
    if arr.ndim != 3 or arr.shape[0] < 4 or arr.shape[1] < 4 or arr.dtype != np.uint8:
        return None
    if arr.shape[2] == 4:
        return arr
    if arr.shape[2] == 3:
        return np.dstack([arr, np.full(arr.shape[:2], 255, np.uint8)])
    return None


def round_icon_patch(icon: np.ndarray | None, diameter: float, ring_rgb: Sequence[int] | None,
                     ring_w: float = 2.0, grey: bool = False, fallback_rgb: Sequence[int] = GREY,
                     letter: str = "") -> np.ndarray:
    """Round champion portrait with a coloured ring as a premultiplied RGBA float32 patch (cached).

    ``icon`` is RGBA uint8 (None -> a plain disc with ``letter``). ``grey`` desaturates and
    darkens the portrait (champion not visible).
    """
    diameter = float(min(max(diameter, 6.0), 256.0))
    rgba = _as_rgba_icon(icon) if icon is not None else None
    fp = _icon_fingerprint(rgba) if rgba is not None else ("none", letter)
    key = (fp, round(diameter * 4), tuple(ring_rgb) if ring_rgb else None, round(ring_w * 4), grey,
           tuple(fallback_rgb))
    hit = _icon_cache.get(key)
    if hit is not None:
        return hit
    size = int(math.ceil(diameter)) + 2
    c = size / 2.0
    r = diameter / 2.0
    cv_ = Canvas(size, size)
    rw = ring_w if ring_rgb is not None else 0.0
    r_in = r - rw - (0.6 if rw > 0 else 0.0)
    # dark base (shows through the separator line)
    cv_.disc(c, c, r, PANEL_DEEP, 1.0)
    if rgba is not None and r_in > 1:
        d_in = max(2, int(round(2 * r_in)))
        h0, w0 = rgba.shape[:2]
        crop = 0.06   # drop the icon's own outer border
        y0, y1 = int(h0 * crop), int(round(h0 * (1 - crop)))
        x0, x1 = int(w0 * crop), int(round(w0 * (1 - crop)))
        src = rgba[y0:y1, x0:x1]
        interp = cv2.INTER_AREA if src.shape[0] > d_in else cv2.INTER_CUBIC
        img = cv2.resize(src, (d_in, d_in), interpolation=interp).astype(np.float32) / 255.0
        col = img[..., :3]
        a = img[..., 3]
        if grey:
            lum = col @ np.array([0.299, 0.587, 0.114], np.float32)
            col = np.repeat((lum * 0.62 + 0.04)[..., None], 3, axis=2)
            col = col * np.array([0.95, 0.98, 1.08], np.float32)
        # bake alpha over the dark base colour
        base = _rgb(PANEL_DEEP)
        col = col * a[..., None] + base * (1 - a[..., None])
        off = (size - d_in) / 2.0
        xs = np.arange(d_in, dtype=np.float32) + 0.5 + off - c
        dmap = np.sqrt(xs[None, :] ** 2 + xs[:, None] ** 2)
        cov = np.clip(r_in - dmap + 0.5, 0.0, 1.0)
        io = int(round(off))
        cv_.paint(io, io, cov, np.clip(col, 0, 1), 1.0)
    elif r_in > 1:
        cv_.disc(c, c, r_in, fallback_rgb, 1.0)
        if letter:
            f = get_font(max(7, int(r_in * 1.1)), "bold")
            cv_.text(c, c, letter[:1].upper(), f, GOLD_LIGHT, 1.0, anchor="m", shadow=0)
    if rw > 0 and ring_rgb is not None:
        ring_col = _mix(ring_rgb, GREY, 0.55) if grey else ring_rgb
        cv_.ring(c, c, r - rw / 2, rw, ring_col, 1.0)
    patch = cv_.px
    patch.setflags(write=False)
    _icon_cache.put(key, patch)
    return patch


def load_asset_icon(name: str) -> np.ndarray | None:
    """RGBA uint8 icon from ``assets/icons/minimap`` (cached, None if missing)."""
    with _asset_lock:
        if name in _asset_icon_cache:
            return _asset_icon_cache[name]
    img: np.ndarray | None = None
    try:
        from treeaicoach import paths

        p = paths.asset_path("icons", "minimap", name)
        data = np.fromfile(str(p), dtype=np.uint8)
        raw = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
        if raw is not None and raw.ndim == 3 and raw.shape[2] == 4:
            img = cv2.cvtColor(raw, cv2.COLOR_BGRA2RGBA)
        elif raw is not None and raw.ndim == 3:
            img = cv2.cvtColor(raw, cv2.COLOR_BGR2RGBA)
    except Exception as exc:
        log.debug("Cannot load icon %s: %s", name, exc)
    if img is not None:
        img.setflags(write=False)
    with _asset_lock:
        _asset_icon_cache[name] = img
    return img


def sprite_patch(rgba: np.ndarray, size: float, opacity: float = 1.0, grey: bool = False) -> np.ndarray:
    """Plain RGBA sprite resized to ``size`` px as a premultiplied float32 patch (cached)."""
    key = ("sprite", _icon_fingerprint(rgba), round(size * 2), round(opacity * 100), grey)
    hit = _icon_cache.get(key)
    if hit is not None:
        return hit
    s = max(2, int(round(size)))
    img = cv2.resize(rgba, (s, s), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    col, a = img[..., :3], img[..., 3:4] * np.float32(opacity)
    if grey:
        lum = col @ np.array([0.299, 0.587, 0.114], np.float32)
        col = np.repeat(lum[..., None] * 0.7, 3, axis=2)
    patch = np.concatenate([col * a, a], axis=2).astype(np.float32)
    patch.setflags(write=False)
    _icon_cache.put(key, patch)
    return patch


def objective_icon(name: str) -> np.ndarray | None:
    """Minimap icon for an objective display name ("Dragon", "Baron", "Héraut"...)."""
    fname = OBJECTIVE_ICONS.get(str(name or "").strip().lower())
    return load_asset_icon(fname) if fname else None


# ======================================================================================
# Radar
# ======================================================================================
_radar_bg_cache = _LRU(6)
_region_cache = _LRU(24)
_default_texture: np.ndarray | None = None
_default_texture_lock = threading.Lock()


def default_radar_texture() -> np.ndarray:
    """The official minimap texture as BGR (transparent parts = near-black walls). Cached."""
    global _default_texture
    with _default_texture_lock:
        if _default_texture is not None:
            return _default_texture
        img = None
        try:
            from treeaicoach.fog_tracker import load_default_texture

            bgra = load_default_texture()
            if bgra is not None:
                a = bgra[..., 3:4].astype(np.float32) / 255.0
                void = np.array([10, 7, 3], np.float32)
                img = (bgra[..., :3].astype(np.float32) * a + void * (1 - a)).astype(np.uint8)
        except Exception as exc:
            log.warning("Cannot load the radar texture: %s", exc)
        if img is None:
            img = np.full((512, 512, 3), (40, 34, 22), np.uint8)
        img.setflags(write=False)
        _default_texture = img
        return img


def _radar_background(texture_bgr: np.ndarray, size: int) -> np.ndarray:
    """Darkened, slightly desaturated map inside a rounded panel (premultiplied RGBA, cached)."""
    key = (_icon_fingerprint(texture_bgr[::8, ::8]), texture_bgr.shape, size)
    hit = _radar_bg_cache.get(key)
    if hit is not None:
        return hit
    tex = np.asarray(texture_bgr)
    if tex.ndim == 2:
        tex = cv2.cvtColor(tex, cv2.COLOR_GRAY2BGR)
    elif tex.shape[2] == 4:
        a = tex[..., 3:4].astype(np.float32) / 255.0
        tex = (tex[..., :3].astype(np.float32) * a + np.array([10, 7, 3], np.float32) * (1 - a)).astype(np.uint8)
    border = max(2, int(round(size * 0.012)))
    inner = size - 2 * border
    img = cv2.resize(tex[..., :3], (inner, inner), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    rgb = img[..., ::-1]
    lum = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
    rgb = rgb * 0.62 + lum[..., None] * 0.38          # desaturate
    rgb = rgb * 0.60                                   # darken so overlays pop
    rgb = rgb * np.array([0.92, 0.98, 1.10], np.float32) + np.array([0.0, 0.01, 0.025], np.float32)
    # soft vignette
    yy, xx = np.mgrid[0:inner, 0:inner].astype(np.float32)
    r = np.sqrt((xx / inner - 0.5) ** 2 + (yy / inner - 0.5) ** 2) / 0.7071
    rgb = rgb * (1.0 - 0.35 * r[..., None] ** 2)
    cv_ = Canvas(size, size)
    radius = size * 0.045
    cv_.rrect(0, 0, size, size, radius, PANEL, 0.94)
    # map, clipped to an inner rounded rect
    gx = np.arange(inner, dtype=np.float32) + 0.5
    qx = np.abs(gx[None, :] - inner / 2) - (inner / 2 - radius * 0.8)
    qy = np.abs(gx[:, None] - inner / 2) - (inner / 2 - radius * 0.8)
    sdf = (np.sqrt(np.maximum(qx, 0) ** 2 + np.maximum(qy, 0) ** 2)
           + np.minimum(np.maximum(qx, qy), 0) - radius * 0.8)
    cov = np.clip(0.5 - sdf, 0.0, 1.0)
    cv_.paint(border, border, cov, np.clip(rgb, 0, 1), 1.0)
    patch = cv_.px
    patch.setflags(write=False)
    _radar_bg_cache.put(key, patch)
    return patch


def _region_layers(region: np.ndarray, size: int) -> tuple[np.ndarray, np.ndarray] | None:
    """(fill coverage, contour coverage) of a fog region upsampled to ``size`` (cached)."""
    key = (id(region), region.shape, size)
    hit = _region_cache.get(key)
    if hit is not None and hit[0] is region:
        return hit[1], hit[2]
    m = region.astype(np.float32)
    if not m.any():
        return None
    m = cv2.GaussianBlur(m, (0, 0), 0.8)
    up = cv2.resize(m, (size, size), interpolation=cv2.INTER_LINEAR)
    cell = size / float(region.shape[0])
    k = max(1.0, cell * 0.9)
    fill = np.clip((up - 0.42) * k + 0.5, 0.0, 1.0)
    # contour: distance to the iso-line in pixels ~ |up - 0.42| / |grad|
    gx = cv2.Sobel(up, cv2.CV_32F, 1, 0, ksize=3) * 0.125
    gy = cv2.Sobel(up, cv2.CV_32F, 0, 1, ksize=3) * 0.125
    grad = np.sqrt(gx * gx + gy * gy) + 1e-4
    dist = np.abs(up - 0.42) / grad
    edge = np.clip(1.6 * 0.5 - dist + 0.5, 0.0, 1.0)
    edge[grad < 0.004] = 0.0
    _region_cache.put(key, (region, fill, edge))
    return fill, edge


def _heat_layer(heat: Any, W: int, H: int) -> np.ndarray | None:
    """Coverage 0..1 of a fog heat map (jungle_path.py) upsampled to ``W x H`` (cached)."""
    if not isinstance(heat, np.ndarray) or heat.ndim != 2 or heat.shape[0] < 4:
        return None
    key = ("heat", id(heat), W, H)
    hit = _region_cache.get(key)
    if hit is not None and hit[0] is heat:
        return hit[1]
    mx = float(heat.max())
    if not mx > 0:
        return None
    m = np.sqrt(np.clip(heat / mx, 0.0, 1.0)).astype(np.float32)      # soft: sqrt contrast
    up = cv2.resize(cv2.GaussianBlur(m, (0, 0), 1.0), (W, H), interpolation=cv2.INTER_LINEAR)
    up[up < 0.12] = 0.0
    _region_cache.put(key, (heat, up))
    return up


def _label_pill(cv_: Canvas, cx: float, cy: float, text: str, font: Any, fg: Any, border: Any,
                alpha: float = 1.0, bg_alpha: float = 0.88) -> tuple[float, float]:
    """Small rounded label centred on (cx, cy); returns its (w, h)."""
    tw = text_width(text, font)
    h = _cap_height(font) + 8
    w = tw + 10
    x = min(max(cx - w / 2, 1), cv_.w - w - 1)
    y = min(max(cy - h / 2, 1), cv_.h - h - 1)
    cv_.rrect(x, y, w, h, h / 2, PANEL_DEEP, bg_alpha * alpha, border=border, border_alpha=0.9 * alpha)
    cv_.text(x + w / 2, y + h / 2, text, font, fg, alpha, anchor="m", shadow=0)
    return w, h


def _arrow(cv_: Canvas, x0: float, y0: float, x1: float, y1: float, width: float, rgb: Any,
           alpha: float = 1.0) -> None:
    """Arrow from (x0, y0) to (x1, y1) with a dark outline."""
    L = math.hypot(x1 - x0, y1 - y0)
    if L < 3:
        return
    ux, uy = (x1 - x0) / L, (y1 - y0) / L
    head = min(L * 0.55, width * 3.2 + 3)
    hw = width * 1.9 + 1.5
    bx, by = x1 - ux * head, y1 - uy * head
    tri = [(x1, y1), (bx - uy * hw, by + ux * hw), (bx + uy * hw, by - ux * hw)]
    cv_.capsule(x0, y0, bx + ux * 1.0, by + uy * 1.0, width + 2.4, BLACK, 0.55 * alpha)
    cv_.polygon(tri, BLACK, 0.55 * alpha, grow=1.2)
    cv_.capsule(x0, y0, bx + ux * 1.0, by + uy * 1.0, width, rgb, alpha)
    cv_.polygon(tri, rgb, alpha)


def _enemy_by_key(state: OverlayState) -> dict[str, EnemyView]:
    out: dict[str, EnemyView] = {}
    for e in state.enemies or []:
        if e is not None and getattr(e, "key", None):
            out[e.key] = e
    return out


def render_radar(state: OverlayState, size: int, texture_bgr: np.ndarray | None = None,
                 now: float | None = None) -> np.ndarray:
    """Radar (enlarged minimap copy with threats) as premultiplied BGRA ``size x size``.

    Never raises: on an internal error an empty (transparent) image is returned and logged.
    ``now`` (s) drives the pulse animations (default ``time.monotonic()``).
    """
    size = int(min(max(int(size) if _finite(size) else 256, 64), 2048))
    try:
        return _render_radar(state, size, texture_bgr, time.monotonic() if now is None else float(now))
    except Exception:
        log.exception("render_radar failed")
        return np.zeros((size, size, 4), np.uint8)


def _render_radar(state: OverlayState, size: int, texture_bgr: np.ndarray | None, now: float) -> np.ndarray:
    tex = texture_bgr if isinstance(texture_bgr, np.ndarray) and texture_bgr.ndim >= 2 \
        and min(texture_bgr.shape[:2]) >= 8 else default_radar_texture()
    cv_ = Canvas(size, size)
    cv_.paint_premul(0, 0, _radar_background(tex, size))
    S = float(size)
    icon_d = max(18.0, S * 0.082)
    ring_w = max(1.6, icon_d * 0.09)
    f_tiny = get_font(max(8, int(S * 0.032)), "bold")
    phase = (now % HALO_PERIOD_S) / HALO_PERIOD_S
    enemies = _enemy_by_key(state)
    me = _uv_ok(state.me_uv) if state.me_uv is not None else None

    # ---- fog regions (reachable zones of hidden enemies), most confident on top
    fogs = [f for f in (state.fogs or []) if f is not None]
    fog_keys = {f.key for f in fogs}
    for fog in sorted(fogs, key=lambda f: f.confidence):
        _draw_fog(cv_, fog, S, phase)

    # ---- warn / danger radius around me
    if me is not None:
        mx, my = me[0] * S, me[1] * S
        warn_r = max(0.0, float(state.warn_radius)) * S if _finite(state.warn_radius) else 0.0
        dang_r = max(0.0, float(state.danger_radius)) * S if _finite(state.danger_radius) else 0.0
        if warn_r > 2:
            cv_.glow(mx, my, warn_r * 0.0, warn_r, WARNING, 0.06)
            cv_.ring(mx, my, warn_r, max(1.2, S * 0.005), WARNING, 0.75,
                     dash=(max(4.0, S * 0.025), max(3.0, S * 0.015)), phase=now * 0.15)
        if dang_r > 2:
            lvl = int(state.threat_level or 0)
            cv_.disc(mx, my, dang_r, DANGER, 0.10 if lvl < 2 else 0.16 + 0.06 * math.sin(phase * 2 * math.pi))
            cv_.ring(mx, my, dang_r, max(1.5, S * 0.006), DANGER, 0.85)

    # ---- last known positions of invisible enemies (without a fog estimate)
    for e in enemies.values():
        if e.visible or e.key in fog_keys:
            continue
        uv = _uv_ok(e.uv) if e.uv is not None else None
        ago = e.last_seen_ago
        if uv is None or ago is None or not _finite(ago) or ago > LAST_SEEN_MAX_S:
            continue
        fade = 1.0 - 0.55 * _clamp01(ago / LAST_SEEN_MAX_S)
        x, y = uv[0] * S, uv[1] * S
        patch = round_icon_patch(e.icon, icon_d * 0.9, DANGER, ring_w, grey=True, letter=e.name or e.alias or "?")
        cv_.image(x, y, patch, 0.9 * fade)
        br = icon_d * 0.2
        bx, by = x + icon_d * 0.36, y - icon_d * 0.36
        cv_.disc(bx, by, br + 1, PANEL_DEEP, 0.9 * fade)
        cv_.disc(bx, by, br, WARNING, fade)
        cv_.text(bx, by, "?", get_font(max(7, int(br * 1.6)), "bold"), PANEL_DEEP, fade, anchor="m", shadow=0)
        _label_pill(cv_, x, y + icon_d * 0.45 + _cap_height(f_tiny) / 2 + 5, fmt_seconds(ago), f_tiny,
                    GOLD_LIGHT, WARNING, alpha=fade)

    # ---- last seen point of each fog estimate: greyed icon + elapsed time
    for fog in sorted(fogs, key=lambda f: f.confidence):
        ev = enemies.get(fog.key)
        _draw_fog_label(cv_, fog, ev.icon if ev is not None else None, S)

    # ---- visible allies (small portraits, blue ring)
    for al in getattr(state, "allies", None) or []:
        uv = _uv_ok(al.uv) if (al is not None and al.visible and al.uv is not None) else None
        if uv is None:
            continue
        cv_.image(uv[0] * S, uv[1] * S, round_icon_patch(al.icon, icon_d * 0.78, ALLY_BLUE, ring_w,
                                                         letter=al.name or al.alias or "?"), 0.85)

    # ---- me
    if me is not None:
        mx, my = me[0] * S, me[1] * S
        cv_.glow(mx, my, icon_d * 0.5, icon_d * 0.95, TEAL, 0.35)
        if state.me_icon is not None:
            cv_.image(mx, my, round_icon_patch(state.me_icon, icon_d, TEAL, ring_w))
        else:
            cv_.disc(mx, my, icon_d * 0.28, WHITE, 1.0)
            cv_.disc(mx, my, icon_d * 0.22, TEAL, 1.0)

    # ---- visible enemies (jungler halo, approach arrows)
    visible = [e for e in enemies.values() if e.visible and e.uv is not None and _uv_ok(e.uv) is not None]
    visible.sort(key=lambda e: (e.is_jungler, e.approaching))
    for e in visible:
        uv = _uv_ok(e.uv)
        assert uv is not None
        x, y = uv[0] * S, uv[1] * S
        if e.is_jungler:
            pr = icon_d * (0.55 + 0.45 * phase)
            cv_.glow(x, y, icon_d * 0.45, icon_d * 0.85, DANGER, 0.45)
            cv_.ring(x, y, pr, max(1.5, icon_d * 0.1), DANGER, 0.9 * (1 - phase) ** 1.4)
        if e.approaching:
            vx, vy = (e.velocity if e.velocity is not None and _finite(*e.velocity) else (0.0, 0.0))
            if math.hypot(vx, vy) < 1e-4 and me is not None:
                vx, vy = me[0] - uv[0], me[1] - uv[1]
            n = math.hypot(vx, vy)
            if n > 1e-6:
                ux, uy = vx / n, vy / n
                speed_len = (n * S * 1.6) if e.velocity is not None else 0.0
                L = min(max(speed_len, icon_d * 0.8), icon_d * 1.6)
                sx, sy = x + ux * icon_d * 0.58, y + uy * icon_d * 0.58
                _arrow(cv_, sx, sy, sx + ux * L, sy + uy * L, max(2.0, icon_d * 0.11), DANGER, 0.95)
        cv_.image(x, y, round_icon_patch(e.icon, icon_d, DANGER, ring_w, letter=e.name or e.alias or "?"))

    # ---- frame (colour follows the threat level)
    lvl = int(state.threat_level or 0) if _finite(state.threat_level or 0) else 0
    radius = S * 0.045
    if lvl >= 2:
        pulse = 0.55 + 0.45 * math.sin(phase * 2 * math.pi)
        cv_.rrect(0.5, 0.5, S - 1, S - 1, radius, None, border=DANGER, border_alpha=0.9, border_w=2.5)
        cv_.rrect(2.5, 2.5, S - 5, S - 5, radius - 2, None, border=DANGER, border_alpha=0.35 * pulse,
                  border_w=3.0)
    elif lvl == 1:
        cv_.rrect(0.5, 0.5, S - 1, S - 1, radius, None, border=WARNING, border_alpha=0.85, border_w=1.8)
    else:
        cv_.rrect(0, 0, S, S, radius, None, border=GOLD, border_alpha=BORDER_ALPHA, border_w=1.2)
    return cv_.to_bgra()


def _draw_fog(cv_: Canvas, fog: FogEstimate, S: float, phase: float) -> None:
    """Reachable region (translucent fill + contour) and the dashed straight-line bound circle."""
    uv = _uv_ok(fog.last_uv)
    if uv is None:
        return
    conf = _clamp01(fog.confidence)
    vis = 0.35 + 0.65 * math.sqrt(conf) if conf > 0 else 0.0
    # the jungler's zone is the headline; other hidden enemies get a discreet version
    main = bool(getattr(fog, "is_jungler", False))
    x, y = uv[0] * S, uv[1] * S
    region = fog.region
    if vis > 0 and isinstance(region, np.ndarray) and region.ndim == 2 and region.shape[0] >= 4:
        layers = _region_layers(region, int(S))
        if layers is not None:
            fill, edge = layers
            heat = _heat_layer(getattr(fog, "heat", None), int(S), int(S))
            cv_.paint(0, 0, fill, DANGER, (0.24 if main else 0.10) * vis * (0.4 if heat is not None else 1.0))
            if heat is not None:          # where he probably is (early clear model)
                cv_.paint(0, 0, heat, DANGER, 0.95 * vis)
            cv_.paint(0, 0, edge, _mix(DANGER, WHITE, 0.15), (0.85 if main else 0.45) * vis)
    if main and vis > 0 and _finite(fog.radius) and fog.radius > 0:
        rr = float(fog.radius) * S
        cv_.ring(x, y, rr, max(1.2, S * 0.0045), _mix(DANGER, WHITE, 0.35), 0.7 * vis,
                 dash=(max(5.0, S * 0.022), max(4.0, S * 0.018)))


def _draw_fog_label(cv_: Canvas, fog: FogEstimate, icon: np.ndarray | None, S: float) -> None:
    """Greyed champion icon + elapsed-time pill at the last seen point of a fog estimate."""
    uv = _uv_ok(fog.last_uv)
    if uv is None:
        return
    conf = _clamp01(fog.confidence)
    alpha = 0.6 + 0.4 * conf
    x, y = uv[0] * S, uv[1] * S
    icon_d = max(18.0, S * 0.082)
    f_tiny = get_font(max(8, int(S * 0.034)), "bold")
    cv_.image(x, y, round_icon_patch(icon, icon_d * 0.92, DANGER, max(1.6, icon_d * 0.09), grey=True,
                                     letter=fog.name or fog.alias or "?"), alpha)
    _label_pill(cv_, x, y + icon_d * 0.46 + _cap_height(f_tiny) / 2 + 5, fmt_seconds(fog.elapsed), f_tiny,
                WHITE, DANGER, alpha=max(alpha, 0.8))


# ======================================================================================
# Minimap overlay (drawn exactly over the real minimap, transparent background)
# ======================================================================================
#: API position / common spellings -> short role tag.
ROLE_TAGS: dict[str, str] = {
    "TOP": "TOP", "JUNGLE": "JGL", "JUNGLER": "JGL", "JGL": "JGL", "JG": "JGL",
    "MIDDLE": "MID", "MID": "MID", "BOTTOM": "ADC", "BOT": "ADC", "ADC": "ADC", "CARRY": "ADC",
    "UTILITY": "SUP", "SUPPORT": "SUP", "SUP": "SUP",
}
ENEMY_TAG_RGB = (255, 150, 160)
ALLY_TAG_RGB = (150, 205, 255)
#: Ring radius around a champion icon, as a fraction of the minimap side. Real icons have a
#: radius of 0.044-0.050 (docs/MINIMAP_FACTS.md): ~1.25x keeps our stroke OUTSIDE the portrait,
#: which stays untouched (the minimap layer is captured by our own detector).
MM_MARKER_R = 0.06


def role_tag(view: Any, roles: dict[str, str] | None = None) -> str:
    """Short tag of a champion: its role (TOP/JGL/MID/ADC/SUP) or a short champion name."""
    role = getattr(view, "role", None)
    if not role and roles:
        for k in (getattr(view, "key", None), getattr(view, "alias", None), getattr(view, "name", None)):
            if k and k in roles:
                role = roles[k]
                break
    tag = ROLE_TAGS.get(str(role or "").strip().upper())
    if tag:
        return tag
    if getattr(view, "is_jungler", False):
        return "JGL"
    name = str(getattr(view, "name", "") or getattr(view, "alias", "") or "").strip()
    if not name or name.startswith(("enemy?", "ally?")):
        return "?"
    word = name.replace("'", "").replace(".", " ").split()[0] if name.split() else name
    return word[:4].upper()


def _rects_hit(r: tuple[float, float, float, float], others: list[tuple[float, float, float, float]]) -> bool:
    return any(r[0] < o[0] + o[2] and o[0] < r[0] + r[2] and r[1] < o[1] + o[3] and o[1] < r[1] + r[3]
               for o in others)


def _place_tag(cv_: Canvas, x: float, y: float, off: float, tw: float, th: float,
               taken: list[tuple[float, float, float, float]], drop: bool = False) -> tuple[float, float] | None:
    """Top-left of a ``tw x th`` tag next to a marker at (x, y), avoiding ``taken`` rects
    (``drop``: None when every candidate collides, instead of overlapping)."""
    cands = [(x - tw / 2, y - off - th), (x + off * 0.8, y - off * 0.8 - th / 2), (x - tw / 2, y + off),
             (x - off * 0.8 - tw, y - off * 0.8 - th / 2), (x + off * 0.8, y + off * 0.3),
             (x - off * 0.8 - tw, y + off * 0.3)]
    best = None
    for cx, cy in cands:
        cx = min(max(cx, 1.0), cv_.w - tw - 1.0)
        cy = min(max(cy, 1.0), cv_.h - th - 1.0)
        r = (cx, cy, tw, th)
        if best is None:
            best = r
        if not _rects_hit(r, taken):
            taken.append(r)
            return r[0], r[1]
    if drop:
        return None
    assert best is not None
    taken.append(best)
    return best[0], best[1]


def _tag(cv_: Canvas, x: float, y: float, off: float, text: str, font: Any, fg: Any,
         taken: list[tuple[float, float, float, float]], alpha: float = 1.0, drop: bool = False) -> bool:
    """Tiny dark pill with ``text`` next to a marker (collision-avoiding; ``drop``: not drawn
    when no free spot). True when drawn."""
    if not text:
        return False
    tw = text_width(text, font) + 6
    th = _cap_height(font) + 5
    pos = _place_tag(cv_, x, y, off, tw, th, taken, drop=drop)
    if pos is None:
        return False
    tx, ty = pos
    cv_.rrect(tx, ty, tw, th, th / 2, PANEL_DEEP, 0.62 * alpha)
    cv_.text(tx + tw / 2, ty + th / 2, text, font, fg, alpha, anchor="m", shadow=0)
    return True


def render_minimap(state: OverlayState, width: int, height: int | None = None,
                   now: float | None = None, show_frame: bool = False) -> np.ndarray:
    """Transparent overlay drawn *over the real minimap* (premultiplied BGRA ``height x width``).

    The layer is visible to screen capture, so it NEVER draws champion portraits / icons (they
    would be re-detected) and stays sparse: thin rings drawn outside the real icons
    (``MM_MARKER_R``) on visible enemies ("JGL" tag on the jungler only), the enemy jungler's fog
    zone as a soft outline with a small timer, one arrow per enemy approaching me and a danger
    ring around me at DANGER only. ``state.show_allies`` / ``show_roles`` / ``show_ghosts``
    (config ``overlay_show_*``) add ally rings, role tags and every hidden enemy's last seen mark.
    ``show_frame`` adds a tiny "TreeAI" label (the layer is alive, even with nothing tracked).
    Never raises (transparent image on error).
    """
    try:
        w = int(min(max(int(width), 16), 2048))
        h = int(min(max(int(height if height is not None else width), 16), 2048))
    except (TypeError, ValueError):
        w = h = 256
    try:
        img = _render_minimap(state, w, h, time.monotonic() if now is None else float(now))
        if show_frame:
            img = _minimap_frame(img)
        return img
    except Exception:
        log.exception("render_minimap failed")
        return np.zeros((h, w, 4), np.uint8)


def _minimap_frame(img: np.ndarray) -> np.ndarray:
    """Tiny "TreeAI" label (the layer is alive) over a minimap layer (premultiplied BGRA)."""
    H, W = img.shape[:2]
    cv_ = Canvas(W, H)
    cv_.px[:] = img[..., (2, 1, 0, 3)].astype(np.float32) / np.float32(255.0)  # BGRA u8 -> RGBA f32
    k = min(W, H) / 256.0
    f = get_font(max(7, int(round(7 * k))), "bold")      # tiny "alive" marker, top-right corner
    tw, th = text_width("TreeAI", f) + 4, _cap_height(f) + 4
    tx, ty = W - tw - 2.0, 2.0
    cv_.rrect(tx, ty, tw, th, th / 2, PANEL_DEEP, 0.45)
    cv_.text(tx + tw / 2, ty + th / 2, "TreeAI", f, TEAL, 0.7, anchor="m", shadow=0)
    return cv_.to_bgra()


def _render_minimap(state: OverlayState, W: int, H: int, now: float) -> np.ndarray:
    """Decluttered layer. Default: thin rings on visible enemies ("JGL" on the jungler only), the
    enemy jungler's fog zone as a soft outline + small timer, one arrow per approaching enemy and
    a danger ring around me at DANGER. ``state.show_allies / show_roles / show_ghosts`` add ally
    rings (+ me), role tags and the last seen marks / fog zones of every hidden enemy."""
    cv_ = Canvas(W, H)
    S = float(min(W, H))
    k = S / 256.0
    phase = (now % HALO_PERIOD_S) / HALO_PERIOD_S
    pulse = 0.5 + 0.5 * math.sin(phase * 2 * math.pi)
    mr = MM_MARKER_R * S
    lw = max(1.1, 1.2 * k)
    f_tag = get_font(max(8, int(round(8.5 * k))), "bold")
    f_time = get_font(max(8, int(round(8.5 * k))), "bold")
    roles = state.roles if isinstance(state.roles, dict) else {}
    show_allies = bool(getattr(state, "show_allies", False))
    show_roles = bool(getattr(state, "show_roles", False))
    show_ghosts = bool(getattr(state, "show_ghosts", False))
    taken: list[tuple[float, float, float, float]] = []

    def px(uv: tuple[float, float]) -> tuple[float, float]:
        return uv[0] * W, uv[1] * H

    def tag_of(e: Any) -> str:
        if e is None:
            return ""
        if getattr(e, "is_jungler", False):
            return "JGL"
        if not show_roles:
            return ""
        t = role_tag(e, roles)
        return "" if t == "?" else t

    lvl = int(state.threat_level or 0) if _finite(state.threat_level or 0) else 0
    lvl = min(max(lvl, 0), 2)
    me = _uv_ok(state.me_uv) if state.me_uv is not None else None
    enemies = [e for e in (state.enemies or []) if e is not None and getattr(e, "key", None)]
    allies = [a for a in (getattr(state, "allies", None) or []) if a is not None and getattr(a, "key", None)]
    by_key = {e.key: e for e in enemies}

    # live icons (after render-time prediction): labels and ghosts never cover them
    live: list[tuple[float, float]] = []
    for v in enemies + allies:
        if getattr(v, "visible", False) and v.uv is not None and _uv_ok(v.uv) is not None and not is_ghost(v):
            live.append(px(_uv_ok(v.uv)))
    if me is not None:
        live.append(px(me))
    icon_r = mr * 0.95
    for lx, ly in live:
        taken.append((lx - icon_r, ly - icon_r, 2 * icon_r, 2 * icon_r))
    labels: list[tuple[int, float, float, float, str, Any, Any, float]] = []   # placed last, by priority

    def near_live(x: float, y: float, d: float = 1.6) -> bool:
        return any(math.hypot(x - lx, y - ly) < d * mr for lx, ly in live)

    # ---- fog zones: the enemy jungler's only, every one with show_ghosts. One clean style: the
    #      probability heat when the jungle model is informative, else a soft fill + outline
    fogs = [f for f in (state.fogs or []) if f is not None
            and (show_ghosts or bool(getattr(f, "is_jungler", False)))]
    for fog in sorted(fogs, key=lambda f: (bool(getattr(f, "is_jungler", False)), f.confidence)):
        uv = _uv_ok(fog.last_uv)
        if uv is None:
            continue
        conf = _clamp01(fog.confidence)
        vis = 0.35 + 0.65 * math.sqrt(conf) if conf > 0 else 0.0
        main = bool(getattr(fog, "is_jungler", False))
        region = fog.region
        if vis > 0 and isinstance(region, np.ndarray) and region.ndim == 2 and region.shape[0] >= 4:
            heat = _heat_layer(getattr(fog, "heat", None), W, H)
            if heat is not None:          # where he probably is (early clear model): heat only
                cv_.paint(0, 0, heat, DANGER, 0.5 * vis)
                continue
            layers = _region_layers(region, int(S))
            if layers is not None:
                fill, edge = layers
                if (W, H) != (int(S), int(S)):
                    fill = cv2.resize(fill, (W, H), interpolation=cv2.INTER_LINEAR)
                    edge = cv2.resize(edge, (W, H), interpolation=cv2.INTER_LINEAR)
                cv_.paint(0, 0, fill, DANGER, (0.04 if main else 0.025) * vis)
                cv_.paint(0, 0, edge, _mix(DANGER, WHITE, 0.25), (0.42 if main else 0.2) * vis)

    # ---- danger ring around me: only at DANGER
    if me is not None and lvl >= 2:
        mx, my = px(me)
        dang_r = max(0.0, float(state.danger_radius)) * S if _finite(state.danger_radius) else 0.0
        if dang_r > 2:
            cv_.disc(mx, my, dang_r, DANGER, 0.05 + 0.04 * pulse)
            cv_.ring(mx, my, dang_r, lw * 1.3, DANGER, 0.6 + 0.3 * pulse)

    # ---- hidden enemies: faded dashed mark at the last seen point (before the fog); text ("JGL
    #      12 s") for the enemy jungler only; never on / next to a live icon (that icon is most
    #      likely the same champion, not identified yet)
    ghost_drawn: set[str] = set()
    if show_ghosts or bool(getattr(state, "show_last_seen", True)):
        for e in enemies:
            if e.visible:
                continue
            uv = _uv_ok(e.uv) if e.uv is not None else None
            ago = e.last_seen_ago
            if uv is None or ago is None or not _finite(ago) or ago > LAST_SEEN_MAX_S:
                continue
            if not show_ghosts and not e.is_jungler and ago > LAST_SEEN_MAX_S / 2:
                continue
            x, y = px(uv)
            if near_live(x, y):
                continue
            ghost_drawn.add(e.key)
            fade = 1.0 - 0.6 * _clamp01(ago / LAST_SEEN_MAX_S)
            cv_.ring(x, y, mr, lw * 0.9, DANGER, (0.6 if e.is_jungler else 0.35) * fade,
                     dash=(3.0 * k + 1, 2.5 * k + 1))
            cv_.disc(x, y, max(1.5, 1.6 * k), DANGER, 0.6 * fade)
            if e.is_jungler:
                labels.append((1, x, y, mr * 1.05, f"JGL {fmt_seconds(ago)}", f_time, GOLD_LIGHT, max(0.7, fade)))

    # ---- (option) allies + me
    if show_allies:
        for a in allies:
            uv = _uv_ok(a.uv) if (a.visible and a.uv is not None) else None
            if uv is None:
                continue
            x, y = px(uv)
            if is_ghost(a):      # stale / stacked / unsure: faint dashed ring, no label
                cv_.ring(x, y, mr, lw * 0.9, ALLY_BLUE, 0.3, dash=(3.0 * k + 1, 2.5 * k + 1))
                continue
            cv_.ring(x, y, mr, lw, ALLY_BLUE, 0.7)
            if show_roles:
                labels.append((3, x, y, mr + 1, role_tag(a, roles), f_tag, ALLY_TAG_RGB, 0.9))
        if me is not None:
            x, y = px(me)
            cv_.ring(x, y, mr * 1.03, lw * 1.3, TEAL, 0.9)

    # ---- visible enemies: thin rings, one arrow per approaching enemy, "JGL" on the jungler
    visible = [e for e in enemies if e.visible and e.uv is not None and _uv_ok(e.uv) is not None]
    visible.sort(key=lambda e: (e.is_jungler, e.approaching))
    for e in visible:
        uv = _uv_ok(e.uv)
        assert uv is not None
        x, y = px(uv)
        if is_ghost(e):          # stale / stacked / unsure: faint dashed ring, no label, no arrow
            cv_.ring(x, y, mr, lw, DANGER, 0.4, dash=(3.0 * k + 1, 2.5 * k + 1))
            continue
        if e.approaching:
            vx, vy = (e.velocity if e.velocity is not None and _finite(*e.velocity) else (0.0, 0.0))
            if math.hypot(vx, vy) < 1e-4 and me is not None:
                vx, vy = me[0] - uv[0], me[1] - uv[1]
            n = math.hypot(vx, vy)
            if n > 1e-6:
                ux, uy = vx / n, vy / n
                L = max(mr * 1.1, min(mr * 2.2, n * S * 1.5))
                sx, sy = x + ux * (mr + lw), y + uy * (mr + lw)
                _arrow(cv_, sx, sy, sx + ux * L, sy + uy * L, max(1.5, 1.6 * k), DANGER, 0.9)
        if e.is_jungler:
            cv_.ring(x, y, mr * 1.02, lw * 1.6, DANGER, 0.9)
        else:
            cv_.ring(x, y, mr, lw, DANGER, 0.8)
        tag = tag_of(e)
        if tag:
            labels.append((0 if e.is_jungler else 2, x, y, mr + 1, tag, f_tag,
                           WHITE if e.is_jungler else ENEMY_TAG_RGB, 1.0))

    # ---- fog timer: small "JGL 12 s" at the last seen point of the jungler's fog zone
    for fog in fogs:
        if fog.key in ghost_drawn or not bool(getattr(fog, "is_jungler", False)):
            continue
        uv = _uv_ok(fog.last_uv)
        if uv is None:
            continue
        x, y = px(uv)
        if near_live(x, y):
            continue
        ev = by_key.get(fog.key)
        if ev is not None and ev.visible:
            continue
        cv_.disc(x, y, max(1.5, 1.6 * k), DANGER, 0.7)
        labels.append((1, x, y, max(3.0, 3 * k), f"JGL {fmt_seconds(fog.elapsed)}", f_time, GOLD_LIGHT, 0.9))

    # ---- labels: by priority, never over a live icon or another label, at most MM_MAX_LABELS
    placed = 0
    for _prio, x, y, off, text, font, fg, alpha in sorted(labels, key=lambda l: l[0]):
        if placed >= MM_MAX_LABELS:
            break
        if _tag(cv_, x, y, off, text, font, fg, taken, alpha, drop=True):
            placed += 1

    # ---- v3 guides: retreat / objective / regroup arrows and ward spots (priority ranked, capped)
    n_arrows = sum(1 for e in visible if e.approaching)
    icons = [p for p in ([me] if me is not None else []) + [_uv_ok(e.uv) for e in visible]
             + [_uv_ok(a.uv) for a in allies if a.visible and a.uv is not None] if p is not None]
    _draw_guides(cv_, list(getattr(state, "guides", None) or []), me, icons, W, H, now,
                 max(1, MAX_MAP_ELEMENTS - n_arrows), taken)
    return cv_.to_bgra()


def _clear_of(uv: tuple[float, float], icons: list[tuple[float, float]], r: float = GUIDE_CLEAR_R) -> bool:
    return all(math.hypot(uv[0] - p[0], uv[1] - p[1]) >= r for p in icons)


def _draw_guides(cv_: Canvas, guides: list[Any], me: tuple[float, float] | None,
                 icons: list[tuple[float, float]], W: int, H: int, now: float, cap: int,
                 taken: list[tuple[float, float, float, float]]) -> None:
    """Guides of the minimap layer: big arrows from me to a target (retreat / objective / regroup)
    and pulsing ward spots. Never over a champion portrait (:data:`GUIDE_CLEAR_R`)."""
    S = float(min(W, H))
    k = S / 256.0
    pulse = 0.5 + 0.5 * math.sin(2 * math.pi * (now % 1.2) / 1.2)
    f_lab = get_font(max(8, int(round(8.5 * k))), "bold")
    drawn = 0
    labelled = False
    ward = load_asset_icon(WARD_ICON)
    for g in sorted(guides, key=lambda g: -float(getattr(g, "priority", 0) or 0)):
        if drawn >= cap:
            break
        uv = _uv_ok(getattr(g, "uv", None))
        if uv is None:
            continue
        rgb = GUIDE_RGB.get(str(getattr(g, "color", "gold")), GUIDE_RGB["gold"])
        x, y = uv[0] * W, uv[1] * H
        if not bool(getattr(g, "arrow", True)):
            # ward spot: dashed pulsing ring + small ward sprite (not over a champion icon)
            if not _clear_of(uv, icons):
                continue
            r = (0.024 + 0.004 * pulse) * S
            cv_.disc(x, y, r, PANEL_DEEP, 0.35)
            cv_.ring(x, y, r, max(1.2, 1.3 * k), rgb, 0.65 + 0.3 * pulse, dash=(2.6 * k + 1, 2.0 * k + 1))
            if bool(getattr(g, "done", False)):          # ward placed (ward_guide): small check mark
                _check_glyph(cv_, x, y, max(5.0, 0.03 * S), BLACK, 0.6)
                _check_glyph(cv_, x, y - 0.5, max(4.5, 0.027 * S), rgb, 1.0)
            elif ward is not None:
                cv_.image(x, y, sprite_patch(ward, max(6.0, 0.032 * S), 0.9))
            drawn += 1
            continue
        if me is not None:
            dx, dy = uv[0] - me[0], uv[1] - me[1]
            d = math.hypot(dx, dy)
            if d > 0.03:
                ux, uy = dx / d, dy / d
                start = 0.065                                     # leave my icon readable
                L = min(d - 0.01, GUIDE_MAX_LEN)
                if L > start + 0.02:
                    sx, sy = (me[0] + ux * start) * W, (me[1] + uy * start) * H
                    ex, ey = (me[0] + ux * L) * W, (me[1] + uy * L) * H
                    _arrow(cv_, sx, sy, ex, ey, max(2.4, 2.6 * k), rgb, 0.78 + 0.2 * pulse)
        if _clear_of(uv, icons):
            r = (0.03 + 0.006 * pulse) * S
            cv_.ring(x, y, r, max(1.5, 1.8 * k), rgb, 0.7 + 0.25 * pulse)
            cv_.disc(x, y, max(1.8, 2.0 * k), rgb, 0.9)
            label = str(getattr(g, "label", "") or "")
            if label and not labelled:
                _tag(cv_, x, y, r + 1, label[:16], f_lab, rgb, taken, 1.0)
                labelled = True
        drawn += 1


# ======================================================================================
# HUD panel
# ======================================================================================
def _objective_rows(state: OverlayState) -> list[tuple[str, np.ndarray | None, str, tuple]]:
    """(label, icon, text, colour) for the objectives worth showing, soonest first."""
    gt = state.game_time if state.game_time is not None and _finite(state.game_time) else None
    items: list[tuple[float, str, np.ndarray | None, str, tuple]] = []
    for ob in state.objectives or []:
        try:
            name = str(getattr(ob, "name", "") or "")
            alive = bool(getattr(ob, "alive", False))
            nxt = getattr(ob, "next_spawn", None)
        except Exception:
            continue
        if not name:
            continue
        icon = objective_icon(name)
        label = OBJECTIVE_SHORT.get(name.lower(), name)
        if alive:
            items.append((-1.0, label, icon, "dispo", SAFE))
            continue
        if nxt is None or not _finite(nxt) or gt is None:
            continue
        remaining = float(nxt) - gt
        if remaining < -1:
            continue
        colour = WARNING if remaining <= 60 else GOLD_LIGHT
        items.append((remaining, label, icon, fmt_clock(max(0.0, remaining)), colour))
    items.sort(key=lambda it: it[0])
    return [(label, icon, text, colour) for _, label, icon, text, colour in items]


def _threat_glyph(cv_: Canvas, cx: float, cy: float, r: float, level: int, base_rgb: Sequence[int]) -> None:
    """Round badge with a check (safe), "!" (warning) or double "!" (danger)."""
    cv_.disc(cx, cy, r, _mix(base_rgb, BLACK, 0.35), 0.9)
    cv_.ring(cx, cy, r - 0.6, 1.2, WHITE, 0.55)
    w = max(1.6, r * 0.2)
    if level <= 0:
        cv_.capsule(cx - r * 0.42, cy + r * 0.02, cx - r * 0.1, cy + r * 0.34, w, WHITE)
        cv_.capsule(cx - r * 0.1, cy + r * 0.34, cx + r * 0.45, cy - r * 0.32, w, WHITE)
    else:
        xs = (cx,) if level == 1 else (cx - r * 0.24, cx + r * 0.24)
        for x in xs:
            cv_.capsule(x, cy - r * 0.45, x, cy + r * 0.12, w, WHITE)
            cv_.disc(x, cy + r * 0.42, w * 0.62, WHITE)


def _coin(cv_: Canvas, cx: float, cy: float, r: float) -> None:
    cv_.disc(cx, cy, r, (164, 124, 48))
    cv_.disc(cx, cy - r * 0.08, r * 0.8, (236, 200, 110))
    cv_.ring(cx, cy - r * 0.08, r * 0.52, max(1.0, r * 0.16), (190, 145, 60), 0.9)


#: Reference HUD width (px at 1080p); every HUD dimension scales with ``width / HUD_REF_W``.
HUD_REF_W = 280.0


def _jungler_text(state: OverlayState) -> str:
    """"Jungler : Lee Sin — vu il y a 14 s, rivière" -> "Lee Sin · vu il y a 14 s, rivière"."""
    text = (state.jungler_line or "").strip()
    for prefix in ("Jungler :", "Jungler:", "Jungler ennemi :"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    return text.replace(" — ", " · ")


#: "Jouer plus fort ou non" gauge (coach.PlayGauge): step -> (word, colour). 5 bars: more bars = play harder.
GAUGE_STYLE: dict[int, tuple[str, tuple[int, int, int]]] = {
    2: ("ATTAQUE", TAI_GO), 1: ("PLUS FORT", TAI_GO_SOFT), 0: ("NORMAL", (203, 213, 225)),
    -1: ("PRUDENT", TAI_WARN), -2: ("SAFE", TAI_DANGER),
}
STANCE_TO_GAUGE: dict[str, int] = {"agressif": 1, "equilibre": 0, "prudent": -1}
#: accent bar / advice tone colours (calm = sky blue)
TONE_RGB: dict[str, tuple[int, int, int]] = {"danger": TAI_DANGER, "warning": TAI_WARN, "go": TAI_GO,
                                             "info": TAI_INFO}
HUD_FADE_S = 0.25               # fade-in of a new advice line / gauge step
CARD_BG = TAI_PANEL
CARD_BG_TOP = TAI_PANEL_TOP
ADVICE_RGB = TAI_TEXT
#: objective chip colours (overlay_render's map colours -> TreeAI palette)
_CHIP_RGB = {WARNING: TAI_WARN, SAFE: TAI_GO}


def _gauge_step(state: OverlayState) -> int | None:
    g = getattr(state, "gauge", None)
    try:
        if g is not None and _finite(g):
            return int(min(max(int(g), -2), 2))
    except (TypeError, ValueError):
        pass
    st = getattr(state, "stance", None)
    return STANCE_TO_GAUGE.get(st) if isinstance(st, str) else None


def _fade(since: Any, now: float) -> float:
    """0..1 smooth fade-in over :data:`HUD_FADE_S` after ``since`` (1 when unknown)."""
    try:
        if since is None or not _finite(since, now):
            return 1.0
        d = float(now) - float(since)
        if d < -0.05:                       # other clock / in the future: no fade
            return 1.0
        a = min(max(d / HUD_FADE_S, 0.0), 1.0)
    except (TypeError, ValueError):
        return 1.0
    return a * a * (3.0 - 2.0 * a)


def _short_role_notice(text: str) -> str:
    """"Rôle détecté : MID (échange de voie)" -> "Rôle : MID (échange de voie)"."""
    return text.replace("Rôle détecté :", "Rôle :").strip()


_CHIP_PREFIXES = ("pense à acheter ", "pense à la ", "pense au ", "pense aux ", "pense à l'", "pense à ",
                  "n'oublie pas la ", "n'oublie pas le ", "n'oublie pas les ", "n'oublie pas ", "achète une ",
                  "achète un ")
_CHIP_WORDS = {"balise de contrôle": "Balise de contrôle", "balises de contrôle": "Balises de contrôle"}
CHIP_MAX_CHARS = 24


def short_chip_text(text: str, max_chars: int = CHIP_MAX_CHARS) -> str:
    """Chip-sized wording of a reminder: "Pense à la balise de contrôle (75 or)" -> "Balise de
    contrôle (75 or)"; cut at a word boundary (no mid-word "…") beyond ``max_chars``."""
    t = " ".join(str(text or "").split())
    if " — " in t:            # "1 450 PO — pense à rentrer" -> "Rentrer · 1 450 PO"
        head, tail = t.split(" — ", 1)
        tail = short_chip_text(tail, max_chars)
        t = f"{tail} · {head}" if tail and head and len(tail) + len(head) + 3 <= max_chars else (tail or head)
    low = t.lower()
    for p in _CHIP_PREFIXES:
        if low.startswith(p) and len(t) > len(p):
            t = t[len(p)].upper() + t[len(p) + 1:]
            break
    for k_, v in _CHIP_WORDS.items():
        if t.lower().startswith(k_):
            t = v + t[len(k_):]
    if len(t) > max_chars:
        cut = t[:max_chars].rsplit(" ", 1)[0].rstrip(",;:·-")
        t = cut if len(cut) >= 6 else t[:max_chars]
    return t


def _hud_chips(state: OverlayState) -> list[tuple[str, str, tuple[int, int, int], Any]]:
    """At most 2 small chips ``(kind, text, colour, icon)``: next objective, then the most useful
    of item to buy (in base) / role notice / reminder / AI counter."""
    chips: list[tuple[str, str, tuple[int, int, int], Any]] = []
    objs = _objective_rows(state)
    if objs:
        label, icon, text, colour = objs[0]
        chips.append(("objective", f"{label} {text}", _CHIP_RGB.get(tuple(colour), TAI_TEXT), icon))
    extra: list[tuple[str, str, tuple[int, int, int], Any]] = []
    item = str(getattr(state, "item_hint", "") or "").strip()
    if item and getattr(state, "in_base", False):
        extra.append(("item", item, TAI_TEXT, None))
    notice = str(getattr(state, "role_notice", "") or "").strip()
    if notice:
        extra.append(("role", _short_role_notice(notice), _mix(TAI_INFO, WHITE, 0.4), None))
    hint = short_chip_text(str(getattr(state, "hint", "") or ""))
    if hint:
        extra.append(("hint", hint, TAI_TEXT, None))
    ai = str(getattr(state, "ai_counter", "") or "").strip()
    if ai:
        extra.append(("ai", ai, _mix(TAI_INFO, WHITE, 0.3), None))
    return (chips + extra)[:2]


def _hud_layout(state: OverlayState, width: int, k: float) -> dict[str, Any]:
    ms, mt, mb = round(5 * k), round(3 * k), round(8 * k)          # soft shadow margins
    cx0, cw = float(ms), float(width - 2 * ms)
    left = cx0 + 15 * k                                             # after the accent bar
    right = cx0 + cw - 11 * k
    inner = right - left
    fonts = {
        "head": get_font(round(11.5 * k), "bold"),
        "reason": get_font(max(7, round(9.5 * k)), "regular"),
        "clock": get_font(max(7, round(9.5 * k)), "semibold"),
        "advice": get_font(round(12.5 * k), "semibold"),
        "chip": get_font(max(7, round(9.5 * k)), "semibold"),
        "body": get_font(round(11 * k), "regular"),
        "tag": get_font(max(7, round(8 * k)), "bold"),
        "small": get_font(max(7, round(9 * k)), "semibold"),
    }
    lvl = 0
    try:
        if _finite(state.threat_level or 0):
            lvl = int(min(max(int(state.threat_level or 0), 0), 2))
    except (TypeError, ValueError):
        lvl = 0
    rows: list[tuple[str, float]] = [("header", 18 * k)]
    gaps: list[float] = []
    tip = getattr(state, "tip", None)
    advice = tip if isinstance(tip, str) and tip.strip() else getattr(state, "insight", None)
    advice = " ".join(advice.split()) if isinstance(advice, str) else ""
    lines = wrap_text(advice, fonts["advice"], inner, 2) if advice else []
    if lines and lines[-1].endswith("…"):                         # too long: one size smaller first
        small = get_font(round(11.5 * k), "semibold")
        alt = wrap_text(advice, small, inner, 2)
        if not alt[-1].endswith("…"):
            lines, fonts["advice"] = alt, small
    if lines:
        gaps.append(7 * k)
        rows.append(("advice", 16 * k * len(lines) - 1 * k))
    chips = _hud_chips(state)
    if chips:
        gaps.append(8 * k)
        rows.append(("chips", 18 * k))
    detailed = bool(getattr(state, "hud_detailed", False))
    jl = _jungler_text(state) if detailed else ""
    slot_w = (cw - 20 * k) / 5.0
    icon_d = min(30 * k, slot_w - 12 * k)
    if jl:
        gaps.append(8 * k)
        rows.append(("jungler", 18 * k))
    if detailed:
        gaps.append(7 * k)
        rows.append(("enemies", icon_d + 16 * k))
    pad_t, pad_b = 8 * k, 9 * k
    card_h = pad_t + sum(h for _, h in rows) + sum(gaps) + pad_b
    height = int(math.ceil(mt + card_h + mb))
    return {"ms": ms, "mt": mt, "cx0": cx0, "cw": cw, "ch": card_h, "left": left, "right": right,
            "inner": inner, "fonts": fonts, "rows": rows, "gaps": gaps, "pad_t": pad_t, "height": height,
            "lvl": lvl, "lines": lines, "chips": chips, "jl": jl, "slot_w": slot_w, "icon_d": icon_d,
            "step": _gauge_step(state), "detailed": detailed}


def hud_size(state: OverlayState, width: int = 280) -> tuple[int, int]:
    """(width, height) that :func:`render_hud` will produce for ``state``."""
    width = int(min(max(int(width) if _finite(width) else 280, 200), 1200))
    return width, _hud_layout(state, width, width / HUD_REF_W)["height"]


def render_hud(state: OverlayState, width: int = 280, now: float | None = None) -> np.ndarray:
    """Compact HUD card - premultiplied BGRA: accent bar coloured by urgency, the "jouer plus
    fort ou non" gauge (or the gank threat), ONE advice line (max 2 lines, fades in) and at
    most 2 chips (next objective, item / role / AI counter). ``state.hud_detailed``: also the
    jungler line and the 5 enemy portraits. ``width`` includes the soft shadow margin.

    Never raises (an empty 1-row image on internal error, logged).
    """
    width = int(min(max(int(width) if _finite(width) else 280, 200), 1200))
    try:
        return _render_hud(state, width, time.monotonic() if now is None else float(now))
    except Exception:
        log.exception("render_hud failed")
        return np.zeros((1, width, 4), np.uint8)


def _bars(cv_: Canvas, x: float, cy: float, k: float, step: int, rgb: Any, alpha: float) -> float:
    """5 ascending bars, ``step + 3`` of them lit (SAFE 1 ... ATTAQUE 5). Returns the width."""
    bw, gap = 3.0 * k, 1.6 * k
    hmax = 12.0 * k
    lit = step + 3
    for i in range(5):
        h = hmax * (0.36 + 0.16 * i)
        bx = x + i * (bw + gap)
        on = i < lit
        cv_.rrect(bx, cy + hmax / 2 - h, bw, h, bw * 0.45, rgb if on else (120, 120, 128),
                  (0.95 * alpha) if on else 0.28)
    return 5 * bw + 4 * gap


def _arrows(cv_: Canvas, x: float, cy: float, k: float, n: int, up: bool, rgb: Any, alpha: float) -> float:
    s = 4.2 * k
    for i in range(n):
        ax = x + i * (s * 1.9) + s
        pts = ([(ax - s, cy + s * 0.6), (ax + s, cy + s * 0.6), (ax, cy - s * 0.75)] if up else
               [(ax - s, cy - s * 0.6), (ax + s, cy - s * 0.6), (ax, cy + s * 0.75)])
        cv_.polygon(pts, rgb, alpha)
    return n * s * 1.9 + (s * 0.2 if n else 0.0)


def _chip_icon(cv_: Canvas, kind: str, icon: Any, cx: float, cy: float, k: float, colour: Any) -> None:
    if kind == "objective" and icon is not None:
        cv_.image(cx, cy, sprite_patch(icon, 14 * k))
    elif kind in ("item", "hint"):
        _coin(cv_, cx, cy, 4.6 * k)
    elif kind == "role":
        cv_.disc(cx, cy, 3.4 * k, TAI_INFO, 0.95)
    elif kind == "ai":
        r = 4.2 * k                                             # four-point spark
        cv_.polygon([(cx, cy - r), (cx + r * 0.3, cy), (cx, cy + r), (cx - r * 0.3, cy)], TAI_INFO, 0.95)
        cv_.polygon([(cx - r, cy), (cx, cy - r * 0.3), (cx + r, cy), (cx, cy + r * 0.3)], TAI_INFO, 0.95)
    else:
        cv_.disc(cx, cy, 3.0 * k, colour, 0.9)


def _brand(cv_: Canvas, right: float, cy: float, k: float) -> float:
    """Tiny "TreeAI" mark (leaf + name) right-aligned on ``right``; returns its width."""
    f = get_font(max(7, round(8.5 * k)), "bold")
    tw = text_width("TreeAI", f)
    cv_.text(right, cy, "TreeAI", f, TAI_MUTED, 0.85, anchor="r", shadow=0.3)
    r = 3.6 * k
    lx = right - tw - 4 * k - r
    cv_.polygon([(lx, cy - r * 1.25), (lx + r * 0.8, cy), (lx, cy + r * 1.25), (lx - r * 0.8, cy)], TAI_BRAND, 0.9)
    return tw + 4 * k + 2 * r


def _render_hud(state: OverlayState, width: int, now: float) -> np.ndarray:
    k = width / HUD_REF_W
    lay = _hud_layout(state, width, k)
    W, H = width, lay["height"]
    fonts, lvl, step = lay["fonts"], lay["lvl"], lay["step"]
    x0, y0, cw, ch = lay["cx0"], float(lay["mt"]), lay["cw"], lay["ch"]
    left, right = lay["left"], lay["right"]
    cv_ = Canvas(W, H)
    phase = (now % HALO_PERIOD_S) / HALO_PERIOD_S
    # ---- urgency colour: gank threat, else the advice tone, else calm sky blue
    tone = str(getattr(state, "tip_tone", "") or "").lower()
    if lvl >= 2:
        accent = TAI_DANGER
    elif lvl == 1:
        accent = TAI_WARN
    elif lay["lines"] and tone in TONE_RGB:
        accent = TONE_RGB[tone]
    elif step is not None and step != 0:
        accent = GAUGE_STYLE[step][1]
    else:
        accent = TAI_INFO
    # ---- soft shadow, graphite card, slate hairline (TreeAI identity, not the game's UI)
    rad = 8 * k
    for i, a in enumerate((0.16, 0.11, 0.07, 0.04)):
        g = (i + 1) * 1.3 * k
        cv_.rrect(x0 - g, y0 - g * 0.6 + 2 * k, cw + 2 * g, ch + 2 * g, rad + g, BLACK, a)
    grad = np.linspace(np.asarray(CARD_BG_TOP, np.float32), np.asarray(CARD_BG, np.float32), 24)
    cv_.rrect(x0, y0, cw, ch, rad, (grad / 255.0)[:, None, :], 0.93,
              border=TAI_EDGE, border_alpha=0.85, border_w=max(1.0, 0.9 * k))
    cv_.capsule(x0 + rad, y0 + 1.2 * k, x0 + cw - rad, y0 + 1.2 * k, max(0.8, 0.7 * k), WHITE, 0.07)
    # accent bar (pulses on a gank)
    a_acc = 0.95 if lvl < 2 else 0.75 + 0.25 * math.sin(phase * 2 * math.pi)
    cv_.capsule(x0 + 6 * k, y0 + 8 * k, x0 + 6 * k, y0 + ch - 8 * k, 3.2 * k, accent, a_acc)
    cv_.glow(x0 + 6 * k, y0 + ch / 2, 2 * k, 10 * k, accent, 0.10)
    y = y0 + lay["pad_t"]
    gaps = list(lay["gaps"])
    for idx, (name, h) in enumerate(lay["rows"]):
        cy = y + h / 2
        if name == "header":
            avail_r = right - _brand(cv_, right, cy, k) - 8 * k
            if bool(getattr(state, "me_dead", False)):      # dead: no "SÛR" / gauge, the respawn timer
                gr = 6.5 * k
                cv_.disc(left + gr, cy, gr, TAI_MUTED, 0.9)
                rs = getattr(state, "respawn_s", None)
                text = "MORT" + (f" · réapparition dans {int(math.ceil(float(rs)))} s"
                                 if rs is not None and _finite(rs) and float(rs) > 0 else "")
                tx = left + 2 * gr + 6 * k
                cv_.text(tx, cy, fit_text(text, fonts["head"], avail_r - tx), fonts["head"],
                         _mix(TAI_MUTED, WHITE, 0.35), shadow=0.6)
            elif lvl >= 1 or step is None:
                col = TAI_THREAT[lvl]
                gr = 6.5 * k
                _threat_glyph(cv_, left + gr, cy, gr, lvl, col)
                text = (state.threat_text or "").strip() or THREAT_DEFAULT_TEXT[lvl]
                for prefix in ("ATTENTION — ", "DANGER — "):          # the glyph + colour already say it
                    if text.upper().startswith(prefix) and len(text) > len(prefix):
                        text = text[len(prefix):]
                tx = left + 2 * gr + 6 * k
                hcol = _mix(col, WHITE, 0.25) if lvl >= 1 else _mix(col, WHITE, 0.2)
                cv_.text(tx, cy, fit_text(text, fonts["head"], avail_r - tx), fonts["head"], hcol, shadow=0.6)
            else:
                word, col = GAUGE_STYLE[step]
                fa = _fade(getattr(state, "gauge_since", None), now)
                bw = _bars(cv_, left, cy, k, step, col, max(0.35, fa))
                tx = left + bw + 7 * k
                tw = cv_.text(tx, cy, word, fonts["head"], col, max(0.25, fa), shadow=0.6)
                tx += tw + 4 * k
                if step:
                    tx += _arrows(cv_, tx, cy, k, abs(step), step > 0, col, max(0.25, fa)) + 3 * k
                reason = " ".join(str(getattr(state, "gauge_reason", "") or getattr(state, "stance_reason", "")
                                      or "").split())
                if reason and text_width("· " + reason, fonts["reason"]) <= avail_r - tx - 3 * k:
                    cv_.text(tx + 3 * k, cy, "· " + reason, fonts["reason"], TAI_MUTED, 0.95 * fa, shadow=0.3)
        elif name == "advice":
            fa = _fade(getattr(state, "tip_since", None), now)
            lh = 16 * k
            for i, line in enumerate(lay["lines"]):
                cv_.text(left, y + lh * i + lh / 2 - 0.5 * k, line, fonts["advice"], ADVICE_RGB, fa,
                         shadow=0.0, outline=1, outline_alpha=0.55)
        elif name == "chips":
            x = left
            f = fonts["chip"]
            for i, (kind, text, colour, icon) in enumerate(lay["chips"]):
                isz = 14 * k if kind == "objective" else 10 * k
                need = 7 * k + isz + 5 * k + text_width(text, f) + 8 * k
                avail = right + 4 * k - x
                if avail < 40 * k:
                    break
                if need > avail and "(" in text:
                    text = text.split("(", 1)[0].strip()
                    need = 7 * k + isz + 5 * k + text_width(text, f) + 8 * k
                cwid = min(need, avail)
                cv_.rrect(x, y, cwid, h, h / 2, (255, 255, 255), 0.05, border=TAI_EDGE, border_alpha=0.8,
                          border_w=max(0.8, 0.8 * k))
                _chip_icon(cv_, kind, icon, x + 7 * k + isz / 2, cy, k, colour)
                tx = x + 7 * k + isz + 5 * k
                cv_.text(tx, cy, fit_text(text, f, x + cwid - 8 * k - tx + 1.5), f, colour, shadow=0.4)
                x += cwid + 6 * k
        elif name == "jungler":
            enemies = list(state.enemies or [])[:5]
            jg = next((e for e in enemies if e is not None and getattr(e, "is_jungler", False)), None)
            d = 16 * k
            icx = left + d / 2
            if jg is not None:
                cv_.image(icx, cy, round_icon_patch(jg.icon, d, TAI_DANGER, max(1.2, 1.4 * k), grey=not jg.visible,
                                                    letter=jg.name or jg.alias or "J"))
            else:
                cv_.image(icx, cy, round_icon_patch(None, d, TAI_EDGE, max(1.2, 1.4 * k), letter="J"))
            tx = left + d + 6 * k
            tw = cv_.text(tx, cy, "JGL", fonts["tag"], TAI_INFO, shadow=0)
            tx += tw + 5 * k
            colour = _mix(TAI_DANGER, WHITE, 0.35) if (jg is not None and jg.visible) else TAI_TEXT
            cv_.text(tx, cy, fit_text(lay["jl"], fonts["small"], right - tx), fonts["small"], colour)
        elif name == "enemies":
            roles = state.roles if isinstance(state.roles, dict) else {}
            _draw_enemy_slots(cv_, list(state.enemies or [])[:5], x0 + 10 * k, y, lay["slot_w"], lay["icon_d"],
                              k, fonts, phase, roles)
        y += h + (gaps[idx] if idx < len(gaps) else 0.0)
    return cv_.to_bgra()


def _draw_enemy_slots(cv_: Canvas, enemies: list[EnemyView], x0: float, y0: float, slot_w: float,
                      icon_d: float, k: float, fonts: dict[str, Any], phase: float,
                      roles: dict[str, str] | None = None) -> None:
    """Five enemy slots: portrait (grey when hidden) + role badge + status line."""
    ring_w = max(1.4, 1.7 * k)
    font, f_tag = fonts["small"], fonts["tag"]
    for i in range(5):
        cx = x0 + slot_w * (i + 0.5)
        cy = y0 + icon_d / 2 + 1
        ly = y0 + icon_d + 10.5 * k
        if i >= len(enemies) or enemies[i] is None:
            cv_.ring(cx, cy, icon_d / 2 - 1, 1.0, GREY, 0.7, dash=(3, 3))
            cv_.text(cx, ly, "—", font, GREY, anchor="m", shadow=0)
            continue
        e = enemies[i]
        ago = e.last_seen_ago if e.last_seen_ago is not None and _finite(e.last_seen_ago) else None
        if e.visible and e.approaching:
            cv_.glow(cx, cy, icon_d * 0.45, icon_d * 0.7, TAI_DANGER, 0.3 + 0.25 * math.sin(phase * 2 * math.pi))
        ring = TAI_DANGER if (e.visible or e.is_jungler) else _mix(TAI_DANGER, GREY, 0.4)
        cv_.image(cx, cy, round_icon_patch(e.icon, icon_d, ring, ring_w * (1.3 if e.is_jungler else 1.0),
                                           grey=not e.visible, letter=e.name or e.alias or "?"))
        tag = role_tag(e, roles)
        if tag and tag != "?":
            tw = text_width(tag, f_tag) + 5 * k
            th = _cap_height(f_tag) + 4 * k
            bx, by = cx - tw / 2, cy + icon_d / 2 - th * 0.55
            bg = TAI_INFO if e.is_jungler else TAI_PANEL
            cv_.rrect(bx, by, tw, th, th / 2, bg, 0.95, border=TAI_INFO if not e.is_jungler else None,
                      border_alpha=0.6)
            cv_.text(cx, by + th / 2, tag, f_tag, TAI_PANEL if e.is_jungler else TAI_TEXT, anchor="m",
                     shadow=0)
        if e.visible:
            text, colour = ("approche", TAI_DANGER) if e.approaching else ("visible", TAI_GO)
        elif ago is None:
            text, colour = "non vu", GREY
        elif ago < LAST_SEEN_MAX_S:
            text, colour = fmt_seconds(ago), TAI_WARN
        else:
            text, colour = f"vu {fmt_clock(ago)}", TAI_MUTED
        cv_.text(cx, ly + 2 * k, fit_text(text, font, slot_w - 2), font, colour, anchor="m", shadow=0.4)


# ======================================================================================
# Danger flash (screen edges)
# ======================================================================================
def _rect_tuple(r: Any) -> tuple[int, int, int, int] | None:
    if r is None:
        return None
    try:
        if hasattr(r, "x"):
            vals = (r.x, r.y, r.w, r.h)
        else:
            vals = tuple(r)[:4]
        x, y, w, h = (int(round(float(v))) for v in vals)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


def flash_profile(thickness: int) -> np.ndarray:
    """Alpha (0..1) as a function of the distance to the screen edge, length ``3 * thickness``."""
    th = max(1, int(thickness))
    d = np.arange(3 * th, dtype=np.float32)
    core = 0.92 - 0.22 * (d / th)
    glow = 0.70 * np.clip(1.0 - (d - th) / (2.0 * th), 0.0, 1.0) ** 2
    return np.where(d < th, core, glow).astype(np.float32)


def render_flash(w: int, h: int, intensity: float, exclude: "Rect | None", thickness: int = 10) -> np.ndarray:
    """Red frame on the screen edges (premultiplied BGRA ``h x w``), never covering ``exclude``.

    ``exclude`` is the minimap rectangle relative to the flash image (x, y, w, h). Never raises.
    """
    try:
        w = int(min(max(int(w), 1), _MAX_SIDE))
        h = int(min(max(int(h), 1), _MAX_SIDE))
    except (TypeError, ValueError):
        return np.zeros((1, 1, 4), np.uint8)
    out = np.zeros((h, w, 4), np.uint8)
    try:
        a = _clamp01(intensity)
        if a <= 0:
            return out
        prof = flash_profile(thickness) * np.float32(a)
        D = int(min(len(prof), max(1, w // 2), max(1, h // 2)))
        prof = prof[:D]
        dx = np.minimum(np.arange(w), np.arange(w)[::-1])
        dy = np.minimum(np.arange(h), np.arange(h)[::-1])
        col = np.array([DANGER[2], DANGER[1], DANGER[0]], np.float32)

        def fill(ys: slice, xs: slice) -> None:
            d = np.minimum(dy[ys][:, None], dx[xs][None, :])
            alpha = np.where(d < D, prof[np.minimum(d, D - 1)], np.float32(0.0))
            out[ys, xs, :3] = (alpha[..., None] * col + 0.5).astype(np.uint8)
            out[ys, xs, 3] = (alpha * 255.0 + 0.5).astype(np.uint8)

        fill(slice(0, D), slice(0, w))
        fill(slice(max(D, h - D), h), slice(0, w))
        if h - 2 * D > 0:
            fill(slice(D, h - D), slice(0, D))
            fill(slice(D, h - D), slice(max(D, w - D), w))
        ex = _rect_tuple(exclude)
        if ex is not None:
            m = 2
            x0, y0 = max(0, ex[0] - m), max(0, ex[1] - m)
            x1, y1 = min(w, ex[0] + ex[2] + m), min(h, ex[1] + ex[3] + m)
            if x0 < x1 and y0 < y1:
                out[y0:y1, x0:x1] = 0
        return out
    except Exception:
        log.exception("render_flash failed")
        return np.zeros((h, w, 4), np.uint8)


# ======================================================================================
# Game-view ward guides (ward_guide.WorldMarker): ground marker / edge arrow / check mark
# ======================================================================================
WORLD_RGB = GUIDE_RGB["gold"]
WORLD_DONE_RGB = GUIDE_RGB["safe"]
WORLD_FADE_IN_S = 0.35
WORLD_FADE_OUT_S = 0.8
WORLD_PULSE_S = 1.4


def world_scale(screen: Any) -> float:
    """Size factor of the game-view markers (1.0 at 1080 px of screen height)."""
    try:
        return float(min(2.0, max(0.6, float(screen[3]) / 1080.0)))
    except Exception:
        return 1.0


def _ellipse_ring(cv_: Canvas, cx: float, cy: float, rx: float, ry: float, width: float, rgb: Any,
                  alpha: float = 1.0, fill_alpha: float = 0.0) -> None:
    """Anti-aliased ellipse outline (+ optional soft fill): a ring lying on the ground."""
    if not _finite(cx, cy, rx, ry, width) or rx <= 1 or ry <= 1 or alpha <= 0:
        return
    ext = width / 2 + 1
    g = cv_._grid(cx - rx - ext, cy - ry - ext, cx + rx + ext, cy + ry + ext)
    if g is None:
        return
    X0, Y0, xs, ys = g
    nx, ny = (xs - cx) / rx, (ys - cy) / ry
    q = np.sqrt(nx * nx + ny * ny) + 1e-6
    grad = np.sqrt((nx / rx) ** 2 + (ny / ry) ** 2) / q + 1e-6
    d = (q - 1.0) / grad                                   # ~ signed distance to the ellipse (px)
    cov = np.clip(width * 0.5 - np.abs(d) + 0.5, 0.0, 1.0)
    if fill_alpha > 0:
        inner = np.clip(0.5 - d, 0.0, 1.0) * (0.35 + 0.65 * np.clip(q, 0.0, 1.0) ** 2)
        cv_.paint(X0, Y0, inner, rgb, alpha * fill_alpha)
    cv_.paint(X0, Y0, cov, rgb, alpha)


def _eye_glyph(cv_: Canvas, cx: float, cy: float, r: float, rgb: Any, alpha: float = 1.0) -> None:
    """Ward icon: a vision "eye" (lens + pupil), ``r`` = half width."""
    n = 12
    top = [(cx + r * math.cos(math.pi * i / n), cy - 0.58 * r * math.sin(math.pi * i / n)) for i in range(n + 1)]
    bot = [(cx - r * math.cos(math.pi * i / n), cy + 0.58 * r * math.sin(math.pi * i / n)) for i in range(1, n)]
    cv_.polygon(top + bot, rgb, alpha)
    inner = [(cx + (x - cx) * 0.78, cy + (y - cy) * 0.66) for x, y in top + bot]
    cv_.polygon(inner, PANEL_DEEP, alpha)
    cv_.disc(cx, cy, 0.34 * r, rgb, alpha)
    cv_.disc(cx - 0.1 * r, cy - 0.1 * r, 0.1 * r, WHITE, alpha * 0.8)


def _check_glyph(cv_: Canvas, cx: float, cy: float, s: float, rgb: Any, alpha: float = 1.0) -> None:
    """Check mark ("✓") of size ``s`` centred on (cx, cy)."""
    w = max(1.6, 0.2 * s)
    cv_.capsule(cx - 0.42 * s, cy + 0.02 * s, cx - 0.12 * s, cy + 0.3 * s, w, rgb, alpha)
    cv_.capsule(cx - 0.12 * s, cy + 0.3 * s, cx + 0.45 * s, cy - 0.3 * s, w, rgb, alpha)


def _world_badge(cv_: Canvas, cx: float, cy: float, r: float, rgb: Any, alpha: float, done: bool = False) -> None:
    for i, a in enumerate((0.18, 0.1, 0.05)):
        cv_.disc(cx, cy + 1.5, r + (i + 1) * 1.6, BLACK, a * alpha)
    cv_.disc(cx, cy, r, PANEL_DEEP, 0.92 * alpha)
    cv_.ring(cx, cy, r - 0.6, max(1.4, 0.11 * r), rgb, 0.95 * alpha)
    if done:
        _check_glyph(cv_, cx, cy, 1.05 * r, rgb, alpha)
    else:
        _eye_glyph(cv_, cx, cy + 0.04 * r, 0.62 * r, rgb, alpha)


def _world_alpha(m: Any) -> float:
    age = float(getattr(m, "age", 1.0) or 0.0)
    left = float(getattr(m, "left", 99.0) or 0.0)
    a = min(1.0, max(0.0, age / WORLD_FADE_IN_S)) if age < WORLD_FADE_IN_S else 1.0
    return float(max(0.0, min(a, left / WORLD_FADE_OUT_S if left < WORLD_FADE_OUT_S else 1.0)))


def _world_layout(m: Any, k: float) -> dict[str, Any]:
    """Geometry of one marker relative to its anchor (the ground point / screen-edge point)."""
    kind = str(getattr(m, "kind", "ground"))
    f_lab = get_font(max(10, int(round(15 * k))), "bold")
    f_sub = get_font(max(9, int(round(12 * k))), "regular")
    label = str(getattr(m, "label", "") or "")
    sub = str(getattr(m, "sub", "") or "") if kind == "edge" else ""
    hint = str(getattr(m, "hint", "") or "")
    L: dict[str, Any] = {"kind": kind, "f_lab": f_lab, "f_sub": f_sub, "label": label, "sub": sub, "hint": hint}
    pad = 6 * k
    if kind == "edge":
        br = 19 * k
        tw = text_width(label, f_lab) + (text_width("  " + sub, f_sub) if sub else 0.0)
        hw = text_width(hint, f_sub) if hint else 0.0
        ph = (_cap_height(f_lab) + 12 * k) + ((_cap_height(f_sub) + 7 * k) if hint else 0.0)
        pw = max(tw, hw) + 22 * k
        dx, dy = float(getattr(m, "dx", 0.0) or 0.0), float(getattr(m, "dy", -1.0) or 0.0)
        n = math.hypot(dx, dy) or 1.0
        dx, dy = dx / n, dy / n
        if abs(dx) >= abs(dy):          # left / right border: the pill goes inwards horizontally
            pcx, pcy = -math.copysign(br + 8 * k + pw / 2, dx), 0.0
        else:
            pcx, pcy = 0.0, -math.copysign(br + 8 * k + ph / 2, dy)
        tip = br + 19 * k
        pts = [(-br - pad, -br - pad), (br + pad, br + pad), (dx * tip - pad, dy * tip - pad),
               (dx * tip + pad, dy * tip + pad), (pcx - pw / 2 - pad, pcy - ph / 2 - pad),
               (pcx + pw / 2 + pad, pcy + ph / 2 + pad)]
        L.update(br=br, pw=pw, ph=ph, pc=(pcx, pcy), dir=(dx, dy), tip=tip)
    else:
        R = 40 * k
        br = 15 * k
        stem = 40 * k
        bc = (0.0, -stem - br)
        ph = _cap_height(f_lab) + 12 * k
        pw = text_width(label, f_lab) + 22 * k
        pc = (0.0, bc[1] - br - 6 * k - ph / 2)
        hw = text_width(hint, f_sub) + 14 * k if hint else 0.0
        hc = (0.0, 0.42 * R + 9 * k + _cap_height(f_sub) / 2)
        pts = [(-1.4 * R - pad, -0.62 * R - pad), (1.4 * R + pad, 0.62 * R + pad),
               (-pw / 2 - pad, pc[1] - ph / 2 - pad), (pw / 2 + pad, pc[1] + ph / 2 + pad)]
        if hint:
            pts += [(-hw / 2 - pad, hc[1] - 10 * k), (hw / 2 + pad, hc[1] + 10 * k)]
        L.update(R=R, br=br, stem=stem, bc=bc, pw=pw, ph=ph, pc=pc, hw=hw, hc=hc)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    L["box"] = (math.floor(min(xs)), math.floor(min(ys)), math.ceil(max(xs)), math.ceil(max(ys)))
    return L


def render_world_marker(m: Any, k: float = 1.0, now: float | None = None) -> tuple[np.ndarray, int, int]:
    """One game-view marker as a premultiplied BGRA patch + the anchor position inside it.
    Never raises (empty 1x1 patch on error)."""
    try:
        now = time.monotonic() if now is None else float(now)
        L = _world_layout(m, k)
        bx0, by0, bx1, by1 = L["box"]
        cv_ = Canvas(bx1 - bx0, by1 - by0)
        ax, ay = -bx0, -by0
        alpha = 1.0                       # drawn opaque, faded as a whole at the end (no layer build-up)
        fade = _world_alpha(m)
        kind = L["kind"]
        done = kind == "done"
        rgb = WORLD_DONE_RGB if done else WORLD_RGB
        phase = (now % WORLD_PULSE_S) / WORLD_PULSE_S
        if kind == "edge":
            br = L["br"]
            dx, dy = L["dir"]
            tip = L["tip"]
            # arrow head towards the spot (outlined), then the badge
            px, py = -dy, dx
            base = br + 3 * k
            hw = 10 * k
            tri = [(ax + dx * tip, ay + dy * tip), (ax + dx * base + px * hw, ay + dy * base + py * hw),
                   (ax + dx * base - px * hw, ay + dy * base - py * hw)]
            cv_.polygon(tri, BLACK, 0.55 * alpha, grow=1.6 * k)
            cv_.polygon(tri, rgb, (0.8 + 0.2 * math.sin(2 * math.pi * phase)) * alpha)
            _world_badge(cv_, ax, ay, br, rgb, alpha)
            pcx, pcy = L["pc"]
            pw, ph = L["pw"], L["ph"]
            x, y = ax + pcx - pw / 2, ay + pcy - ph / 2
            cv_.rrect(x, y, pw, ph, min(ph / 2, 11 * k), PANEL_DEEP, 0.86 * alpha, border=rgb,
                      border_alpha=0.75 * alpha, border_w=max(1.0, 1.1 * k))
            line_h = _cap_height(L["f_lab"]) + 12 * k
            ty = y + line_h / 2
            tx = x + 11 * k
            tx += cv_.text(tx, ty, L["label"], L["f_lab"], GOLD_LIGHT, alpha, shadow=0)
            if L["sub"]:
                cv_.text(tx, ty, "  " + L["sub"], L["f_sub"], MUTED, alpha, shadow=0)
            if L["hint"]:
                cv_.text(x + 11 * k, y + line_h + (_cap_height(L["f_sub"]) + 7 * k) / 2 - 3 * k, L["hint"],
                         L["f_sub"], rgb, alpha, shadow=0)
        else:
            R, br = L["R"], L["br"]
            ry = 0.4 * R
            if not done:                                   # expanding "sonar" wave
                _ellipse_ring(cv_, ax, ay, R * (1.0 + 0.38 * phase), ry * (1.0 + 0.38 * phase),
                              max(1.2, 1.6 * k), rgb, 0.55 * (1.0 - phase) * alpha)
            _ellipse_ring(cv_, ax, ay + 1.5 * k, R, ry, max(2.0, 3.2 * k), BLACK, 0.35 * alpha)
            _ellipse_ring(cv_, ax, ay, R, ry, max(1.6, 2.4 * k), rgb, 0.92 * alpha, fill_alpha=0.16)
            cv_.disc(ax, ay, max(1.5, 2.6 * k), rgb, 0.9 * alpha)
            bcx, bcy = ax + L["bc"][0], ay + L["bc"][1]
            cv_.capsule(ax, ay - 2 * k, bcx, bcy + br - 1, max(1.4, 1.8 * k), BLACK, 0.35 * alpha)
            cv_.capsule(ax, ay - 2 * k, bcx, bcy + br - 1, max(1.0, 1.3 * k), rgb, 0.85 * alpha)
            bob = 0.0 if done else 2.0 * k * math.sin(2 * math.pi * phase)
            _world_badge(cv_, bcx, bcy + bob, br, rgb, alpha, done=done)
            pw, ph = L["pw"], L["ph"]
            pcx, pcy = ax + L["pc"][0], ay + L["pc"][1] + bob
            cv_.rrect(pcx - pw / 2, pcy - ph / 2, pw, ph, ph / 2, PANEL_DEEP, 0.86 * alpha, border=rgb,
                      border_alpha=0.75 * alpha, border_w=max(1.0, 1.1 * k))
            cv_.text(pcx, pcy, L["label"], L["f_lab"], GOLD_LIGHT if not done else WHITE, alpha, anchor="m", shadow=0)
            if L["hint"] and not done:
                hcx, hcy = ax + L["hc"][0], ay + L["hc"][1]
                cv_.text(hcx, hcy, L["hint"], L["f_sub"], GOLD_LIGHT, alpha, anchor="m", outline=max(1, int(round(2 * k))))
        if fade < 1.0:
            cv_.px *= np.float32(fade)
        return cv_.to_bgra(), int(round(ax)), int(round(ay))
    except Exception:
        log.exception("render_world_marker failed")
        return np.zeros((1, 1, 4), np.uint8), 0, 0


def place_world_patch(x: int, y: int, w: int, h: int, screen: Any,
                      avoid: Sequence[Any] = ()) -> tuple[int, int]:
    """Top-left of a ``w`` x ``h`` patch wanted at (x, y): inside ``screen`` and never over the
    ``avoid`` rectangles (minimap, bottom HUD bar): moved above (or left of) them."""
    try:
        sx, sy, sw, sh = (int(c) for c in screen[:4])
    except Exception:
        return int(x), int(y)
    x = min(max(int(x), sx), sx + sw - w)
    y = min(max(int(y), sy), sy + sh - h)
    for _ in range(3):
        hit = False
        for r in avoid or ():
            try:
                rx, ry, rw, rh = (int(c) for c in r[:4])
            except Exception:
                continue
            if x < rx + rw and rx < x + w and y < ry + rh and ry < y + h:
                hit = True
                up = (x, ry - h - 2)
                left = (rx - w - 2, y)
                cands = [c for c in (up, left) if c[0] >= sx and c[1] >= sy]
                x, y = min(cands, key=lambda c: abs(c[0] - x) + abs(c[1] - y)) if cands else up
        if not hit:
            break
    return int(x), int(y)


def render_world_guides(markers: Sequence[Any], screen: Any, now: float | None = None,
                        avoid: Sequence[Any] = ()) -> list[tuple[np.ndarray, int, int]]:
    """Patches ``(premultiplied BGRA, x, y)`` (absolute screen px) of the game-view ward markers,
    at most 2, kept inside ``screen`` and off the ``avoid`` rectangles. Never raises."""
    out: list[tuple[np.ndarray, int, int]] = []
    try:
        k = world_scale(screen)
        for m in list(markers or [])[:2]:
            if not _finite(getattr(m, "x", None), getattr(m, "y", None)) or _world_alpha(m) <= 0.01:
                continue
            img, ax, ay = render_world_marker(m, k, now)
            h, w = img.shape[:2]
            if w < 2 or h < 2:
                continue
            x, y = place_world_patch(int(round(float(m.x))) - ax, int(round(float(m.y))) - ay, w, h, screen, avoid)
            out.append((img, x, y))
    except Exception:
        log.exception("render_world_guides failed")
    return out


# ======================================================================================
# Pixel format helpers
# ======================================================================================
def to_premultiplied_bgra(rgba: np.ndarray) -> np.ndarray:
    """Straight RGBA uint8 -> premultiplied BGRA uint8 (``UpdateLayeredWindow`` format)."""
    arr = np.asarray(rgba)
    if arr.ndim != 3 or arr.shape[2] not in (3, 4):
        raise ValueError(f"expected an RGBA image, got shape {arr.shape}")
    if arr.shape[2] == 3:
        arr = np.dstack([arr, np.full(arr.shape[:2], 255, np.uint8)])
    a = arr[..., 3:4].astype(np.uint16)
    rgb = (arr[..., :3].astype(np.uint16) * a + 127) // 255
    out = np.empty(arr.shape[:2] + (4,), np.uint8)
    out[..., 0] = rgb[..., 2]
    out[..., 1] = rgb[..., 1]
    out[..., 2] = rgb[..., 0]
    out[..., 3] = arr[..., 3]
    return out


def premultiplied_to_rgba(bgra: np.ndarray) -> np.ndarray:
    """Premultiplied BGRA uint8 -> straight RGBA uint8 (for PIL / Tk previews)."""
    arr = np.asarray(bgra)
    a = arr[..., 3:4].astype(np.float32)
    safe = np.where(a > 0, a, 1.0)
    rgb = np.clip(arr[..., :3].astype(np.float32) * 255.0 / safe + 0.5, 0, 255).astype(np.uint8)
    out = np.empty(arr.shape[:2] + (4,), np.uint8)
    out[..., 0] = rgb[..., 2]
    out[..., 1] = rgb[..., 1]
    out[..., 2] = rgb[..., 0]
    out[..., 3] = arr[..., 3]
    out[..., :3][arr[..., 3] == 0] = 0
    return out


def composite_over(dst_rgb: np.ndarray, bgra_premul: np.ndarray, x: int, y: int) -> np.ndarray:
    """Blend a premultiplied BGRA image onto an RGB uint8 image at (x, y) (in place, clipped)."""
    H, W = dst_rgb.shape[:2]
    h, w = bgra_premul.shape[:2]
    X0, Y0, X1, Y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if X0 >= X1 or Y0 >= Y1:
        return dst_rgb
    src = bgra_premul[Y0 - y:Y1 - y, X0 - x:X1 - x].astype(np.float32)
    inv = 1.0 - src[..., 3:4] / 255.0
    dst = dst_rgb[Y0:Y1, X0:X1].astype(np.float32)
    dst = dst * inv + src[..., 2::-1]
    dst_rgb[Y0:Y1, X0:X1] = np.clip(dst + 0.5, 0, 255).astype(np.uint8)
    return dst_rgb


# ======================================================================================
# Previews (UI / tests / demo)
# ======================================================================================
def default_minimap_rect(screen_w: int, screen_h: int) -> tuple[int, int, int, int]:
    """Typical in-game minimap rectangle (bottom-right square, ~0.236 x screen height)."""
    side = int(round(screen_h * 0.236))
    margin = max(4, int(round(screen_h * 0.009)))
    return screen_w - side - margin, screen_h - side - margin, side, side


def game_background(w: int, h: int, minimap: tuple[int, int, int, int] | None = None,
                    seed: int = 7) -> np.ndarray:
    """A game-like RGB backdrop (terrain + bottom HUD + fogged minimap) for previews."""
    rng = np.random.default_rng(seed)
    small = rng.random((max(2, h // 40), max(2, w // 40), 3)).astype(np.float32)
    noise = cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)
    base = np.array([46, 58, 38], np.float32) / 255.0
    img = base * (0.75 + 0.5 * noise)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    lane = np.exp(-(((xx / w) - (yy / h) * 0.9 - 0.05) ** 2) / 0.004)
    img = img * (1 - 0.35 * lane[..., None]) + np.array([0.42, 0.37, 0.26], np.float32) * 0.35 * lane[..., None]
    vign = 1.0 - 0.45 * (((xx / w) - 0.5) ** 2 + ((yy / h) - 0.5) ** 2)
    rgb = np.clip(img * vign[..., None] * 255, 0, 255).astype(np.uint8)
    # bottom HUD (ability bar)
    cv_ = Canvas(w, h)
    bw, bh = int(w * 0.36), int(h * 0.13)
    cv_.rrect((w - bw) / 2, h - bh - 4, bw, bh, 8, (8, 16, 26), 0.95, border=GOLD_DARK, border_alpha=0.9,
              border_w=2)
    for i in range(6):
        sx = (w - bw) / 2 + bw * 0.12 + i * bw * 0.13
        cv_.rrect(sx, h - bh + 8, bw * 0.105, bh * 0.5, 4, (22, 40, 58), 1.0, border=GOLD, border_alpha=0.6)
    composite_over(rgb, cv_.to_bgra(), 0, 0)
    if minimap is not None:
        mx, my, mw, mh = minimap
        tex = default_radar_texture()
        mm = cv2.resize(tex, (mw, mh), interpolation=cv2.INTER_AREA)[..., ::-1].astype(np.float32)
        fog = np.full((mh, mw), 0.36, np.float32)
        for (u, v, r) in ((0.12, 0.88, 0.25), (0.1, 0.35, 0.12), (0.5, 0.5, 0.1)):
            cv2.circle(fog, (int(u * mw), int(v * mh)), int(r * mw), 1.0, -1, cv2.LINE_AA)
        fog = cv2.GaussianBlur(fog, (0, 0), 2)
        mm = (mm * fog[..., None]).astype(np.uint8)
        x0, y0 = max(0, mx), max(0, my)
        x1, y1 = min(w, mx + mw), min(h, my + mh)
        if x0 < x1 and y0 < y1:
            rgb[y0:y1, x0:x1] = mm[y0 - my:y1 - my, x0 - mx:x1 - mx]
            fr = Canvas(w, h)
            fr.rrect(mx - 5, my - 5, mw + 10, mh + 10, 3, None, border=(30, 50, 55), border_alpha=1, border_w=4)
            fr.rrect(mx - 6, my - 6, mw + 12, mh + 12, 3, None, border=(160, 130, 70), border_alpha=1)
            composite_over(rgb, fr.to_bgra(), 0, 0)
    return rgb


def render_preview(state: OverlayState, width: int = 1280, texture_bgr: np.ndarray | None = None,
                   now: float | None = None, cfg: Any = None, mode: str | None = None,
                   background: np.ndarray | None = None) -> np.ndarray:
    """Straight RGBA preview of the whole overlay (minimap marks or radar, HUD, flash) over a game-like screen.

    The overlay is laid out at the state's screen resolution (1920 x 1080 by default) with the
    same placement rules as ``overlay.py`` then scaled to ``width``. ``mode`` overrides
    ``cfg.overlay_mode`` ("minimap" by default, "radar", "off"); ``background`` (RGB, screen
    size) replaces the synthetic game screen. Never raises.
    """
    try:
        return _render_preview(state, width, texture_bgr, now, cfg, mode, background)
    except Exception:
        log.exception("render_preview failed")
        return np.zeros((max(1, int(width) * 9 // 16), max(1, int(width)), 4), np.uint8)


def _render_preview(state: OverlayState, width: int, texture_bgr: np.ndarray | None, now: float | None,
                    cfg: Any, mode: str | None = None, background: np.ndarray | None = None) -> np.ndarray:
    from treeaicoach import overlay as _ov   # lazy: overlay imports this module

    scr = _rect_tuple(state.screen_rect) or (0, 0, 1920, 1080)
    mm = _rect_tuple(state.minimap_rect) or tuple(
        a + b for a, b in zip(default_minimap_rect(scr[2], scr[3]), (scr[0], scr[1], 0, 0)))
    scr = _ov.effective_screen(scr, mm)
    sx, sy, sw, sh = scr
    if isinstance(background, np.ndarray) and background.ndim == 3 and background.shape[2] >= 3:
        bg = cv2.resize(np.ascontiguousarray(background[..., :3]), (sw, sh), interpolation=cv2.INTER_AREA)
    else:
        bg = game_background(sw, sh, (mm[0] - sx, mm[1] - sy, mm[2], mm[3]))
    mode = _ov.resolve_overlay_mode(mode if mode is not None else getattr(cfg, "overlay_mode", "minimap"), True)
    radar_rect = None
    if mode == "minimap":
        composite_over(bg, render_minimap(state, mm[2], mm[3], now), mm[0] - sx, mm[1] - sy)
    elif mode == "radar" and (cfg is None or getattr(cfg, "radar_enabled", True)):
        scale = float(getattr(cfg, "radar_scale", 1.0) or 1.0) if cfg is not None else 1.0
        rx, ry, rsize = _ov.radar_geometry(mm, scr, scale, getattr(cfg, "radar_position", "above_minimap"),
                                           getattr(cfg, "radar_xy", None))
        composite_over(bg, render_radar(state, rsize, texture_bgr, now), rx - sx, ry - sy)
        radar_rect = (rx, ry, rsize, rsize)
    if cfg is None or getattr(cfg, "hud_enabled", True):
        hud = render_hud(state, _ov.hud_width(scr), now)
        avoid = [r for r in (mm, radar_rect) if r is not None]
        hx, hy = _ov.hud_placement(scr, hud.shape[1], hud.shape[0], getattr(cfg, "hud_position", "left_of_minimap"),
                                   getattr(cfg, "hud_xy", None), avoid=avoid, anchor=radar_rect or mm, minimap=mm)
        composite_over(bg, hud, hx - sx, hy - sy)
    if _clamp01(state.flash) > 0 and (cfg is None or getattr(cfg, "danger_flash", True)):
        fl = render_flash(sw, sh, state.flash, (mm[0] - sx, mm[1] - sy, mm[2], mm[3]),
                          thickness=_ov.flash_thickness(scr))
        composite_over(bg, fl, 0, 0)
    width = int(min(max(int(width), 64), 4096))
    out = cv2.resize(bg, (width, max(1, int(round(sh * width / sw)))), interpolation=cv2.INTER_AREA)
    return np.dstack([out, np.full(out.shape[:2], 255, np.uint8)])


def _encode_png(rgba: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))
    if not ok:
        raise ValueError("PNG encoding failed")
    return buf.tobytes()


def render_preview_png(state: OverlayState, path: str | os.PathLike[str] | None = None,
                       width: int = 1280, texture_bgr: np.ndarray | None = None,
                       now: float | None = None, cfg: Any = None) -> bytes:
    """PNG bytes of :func:`render_preview` (also written to ``path`` when given)."""
    data = _encode_png(render_preview(state, width, texture_bgr, now, cfg))
    if path is not None:
        Path(path).write_bytes(data)
    return data


def radar_preview_rgba(state: OverlayState, size: int = 256, texture_bgr: np.ndarray | None = None,
                       now: float | None = None) -> np.ndarray:
    """Straight RGBA radar image (for Tk / CustomTkinter previews)."""
    return premultiplied_to_rgba(render_radar(state, size, texture_bgr, now))


def minimap_preview_rgba(state: OverlayState, size: int = 256, now: float | None = None,
                         background: np.ndarray | None = None) -> np.ndarray:
    """Straight RGBA "minimap" overlay, over ``background`` (RGB) or the default minimap texture."""
    size = int(min(max(int(size), 16), 2048))
    if isinstance(background, np.ndarray) and background.ndim == 3:
        bg = cv2.resize(np.ascontiguousarray(background[..., :3]), (size, size), interpolation=cv2.INTER_AREA)
    else:
        bg = cv2.resize(default_radar_texture(), (size, size), interpolation=cv2.INTER_AREA)[..., ::-1].copy()
    composite_over(bg, render_minimap(state, size, size, now), 0, 0)
    return np.dstack([bg, np.full(bg.shape[:2], 255, np.uint8)])


def hud_preview_rgba(state: OverlayState, width: int = 280, now: float | None = None) -> np.ndarray:
    """Straight RGBA HUD image (for Tk / CustomTkinter previews)."""
    return premultiplied_to_rgba(render_hud(state, width, now))


# ======================================================================================
# Sample states (demo, UI static preview, tests)
# ======================================================================================
@dataclass
class _DemoObjective:
    name: str
    next_spawn: float | None
    alive: bool = False
    source: str = "schedule"


def _demo_icon(db: Any, alias: str) -> np.ndarray | None:
    try:
        return db.load_icon(alias) if db is not None else None
    except Exception:
        return None


def sample_states(db: Any = None) -> dict[str, OverlayState]:
    """A few representative states: "safe", "warning", "danger" (jungler in fog), "late"."""
    from treeaicoach.fog_tracker import FogTracker

    if db is None:
        try:
            from treeaicoach.champions import ChampionDB

            db = ChampionDB()
        except Exception:
            db = None
    ic = {a: _demo_icon(db, a) for a in ("LeeSin", "Darius", "Ahri", "Jinx", "Thresh", "Garen",
                                         "Vi", "Lux", "Caitlyn", "Nautilus")}
    enemy_roles = {"Darius": "TOP", "LeeSin": "JUNGLE", "Ahri": "MIDDLE", "Jinx": "BOTTOM", "Thresh": "UTILITY"}
    ally_roles = {"Vi": "JUNGLE", "Lux": "MIDDLE", "Caitlyn": "BOTTOM", "Nautilus": "UTILITY"}
    roles = {**enemy_roles, **ally_roles, "Garen": "TOP"}

    def allies(spec: dict[str, tuple[float, float] | None]) -> list[EnemyView]:
        return [EnemyView(key=a, alias=a, name=a, visible=spec.get(a) is not None, uv=spec.get(a),
                          last_seen_ago=0.0 if spec.get(a) is not None else None, icon=ic[a],
                          relation="ally", role=ally_roles[a]) for a in ally_roles]

    ally_bot = {"Vi": (0.3, 0.62), "Lux": (0.45, 0.55), "Caitlyn": (0.78, 0.9), "Nautilus": (0.74, 0.93)}
    fog = FogTracker()
    scr = (0, 0, 1920, 1080)
    mm = default_minimap_rect(1920, 1080)

    def enemies(spec: dict[str, tuple]) -> list[EnemyView]:
        names = {"LeeSin": "Lee Sin", "Darius": "Darius", "Ahri": "Ahri", "Jinx": "Jinx", "Thresh": "Thresh"}
        out = []
        for alias in ("Darius", "LeeSin", "Ahri", "Jinx", "Thresh"):
            vis, uv, ago, appr, vel = spec.get(alias, (False, None, None, False, None))
            out.append(EnemyView(key=alias, alias=alias, name=names[alias], visible=vis, uv=uv,
                                 last_seen_ago=ago, is_jungler=alias == "LeeSin", approaching=appr,
                                 icon=ic[alias], velocity=vel, role=enemy_roles[alias]))
        return out

    obj_early = [_DemoObjective("Dragon", 300.0), _DemoObjective("Larves", 360.0),
                 _DemoObjective("Héraut", 900.0), _DemoObjective("Baron", 1500.0)]
    states: dict[str, OverlayState] = {}
    states["safe"] = OverlayState(
        minimap_rect=mm, screen_rect=scr, me_uv=(0.085, 0.36), my_team="ORDER",
        enemies=enemies({"Darius": (True, (0.09, 0.24), 0.0, False, None),
                         "LeeSin": (False, (0.74, 0.66), 65.0, False, None),
                         "Ahri": (True, (0.52, 0.47), 0.0, False, None),
                         "Jinx": (True, (0.86, 0.9), 0.0, False, None),
                         "Thresh": (False, (0.88, 0.84), 12.0, False, None)}),
        threat_level=0, threat_text="SÛR", last_alert=None, objectives=obj_early, game_time=192.0,
        jungler_line="Jungler : Lee Sin — vu il y a 1:05, jungle ennemie du bas",
        hint=None, me_icon=ic["Garen"],
        allies=allies(ally_bot), roles=roles,
    )
    states["warning"] = OverlayState(
        minimap_rect=mm, screen_rect=scr, me_uv=(0.085, 0.36), my_team="ORDER",
        enemies=enemies({"Darius": (True, (0.09, 0.25), 0.0, False, None),
                         "LeeSin": (True, (0.24, 0.3), 0.0, True, (-0.03, 0.012)),
                         "Ahri": (True, (0.5, 0.5), 0.0, False, None),
                         "Jinx": (True, (0.86, 0.9), 0.0, False, None),
                         "Thresh": (True, (0.84, 0.92), 0.0, False, None)}),
        threat_level=1, threat_text="ATTENTION — Lee Sin approche",
        last_alert=("Attention, Lee Sin approche.", 1, 1.3), objectives=obj_early, game_time=251.0,
        jungler_line="Jungler : Lee Sin — visible, rivière du haut", hint=None, me_icon=ic["Garen"],
        allies=allies(ally_bot), roles=roles,
    )
    lee_fog = fog.simulate("LeeSin", "LeeSin", "Lee Sin", (0.27, 0.23), 14.0, is_jungler=True, game_time=426)
    states["danger"] = OverlayState(
        minimap_rect=mm, screen_rect=scr, me_uv=(0.1, 0.3), my_team="ORDER",
        enemies=enemies({"Darius": (True, (0.1, 0.19), 0.0, True, (0.0, 0.03)),
                         "LeeSin": (False, (0.27, 0.23), 14.0, False, None),
                         "Ahri": (True, (0.2, 0.33), 0.0, True, (-0.035, -0.01)),
                         "Jinx": (False, (0.88, 0.86), 31.0, False, None),
                         "Thresh": (True, (0.86, 0.9), 0.0, False, None)}),
        fogs=[lee_fog], threat_level=2, threat_text="DANGER — 2 ennemis arrivent !",
        last_alert=("Danger, 2 ennemis arrivent, recule !", 2, 0.4),
        objectives=[_DemoObjective("Dragon", 540.0), _DemoObjective("Héraut", 900.0),
                    _DemoObjective("Larves", None, alive=True)],
        game_time=440.0, flash=0.85,
        jungler_line="Jungler : Lee Sin — vu il y a 14 s, rivière du haut",
        hint="Rentre : 1 450 or", me_icon=ic["Garen"],
        allies=allies(ally_bot), roles=roles,
    )
    f1 = fog.simulate("LeeSin", "LeeSin", "Lee Sin", (0.62, 0.72), 21.0, is_jungler=True, game_time=1660)
    f2 = fog.simulate("Thresh", "Thresh", "Thresh", (0.8, 0.8), 38.0, game_time=1660)
    states["late"] = OverlayState(
        minimap_rect=mm, screen_rect=scr, me_uv=(0.47, 0.53), my_team="ORDER",
        enemies=enemies({"Darius": (True, (0.3, 0.2), 0.0, False, None),
                         "LeeSin": (False, (0.62, 0.72), 21.0, False, None),
                         "Ahri": (False, (0.6, 0.38), 6.0, False, None),
                         "Jinx": (True, (0.7, 0.4), 0.0, True, (-0.02, 0.012)),
                         "Thresh": (False, (0.8, 0.8), 38.0, False, None)}),
        fogs=[f1, f2], threat_level=1, threat_text="ATTENTION — Jinx approche",
        last_alert=("Le Baron est disponible.", 0, 2.6),
        objectives=[_DemoObjective("Baron", None, alive=True), _DemoObjective("Dragon ancestral", 1790.0),
                    _DemoObjective("Atakhan", None)],
        game_time=1660.0, jungler_line="Jungler : Lee Sin — vu il y a 21 s, rivière du bas",
        hint="Balise de contrôle : aucune dans l'inventaire", me_icon=ic["Garen"],
        allies=allies(ally_bot), roles=roles,
    )
    return states


def write_demo(outdir: str | os.PathLike[str], now: float = 0.3) -> list[Path]:
    """Write radar / HUD / full-screen previews of :func:`sample_states` into ``outdir``."""
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, st in sample_states().items():
        radar = radar_preview_rgba(st, 300, now=now)
        hud = hud_preview_rgba(st, 280, now=now)
        mini = minimap_preview_rgba(st, 300, now=now)
        for label, img in (("radar", radar), ("hud", hud), ("minimap", mini)):
            p = out / f"{label}_{name}.png"
            p.write_bytes(_encode_png(img))
            written.append(p)
        p = out / f"preview_{name}.png"
        render_preview_png(st, p, width=1600, now=now)
        written.append(p)
        p = out / f"preview_radar_{name}.png"
        p.write_bytes(_encode_png(render_preview(st, 1600, now=now, mode="radar")))
        written.append(p)
    return written


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m treeaicoach.overlay_render --demo OUTDIR``."""
    parser = argparse.ArgumentParser(prog="python -m treeaicoach.overlay_render",
                                     description="Rendus d'exemple de l'overlay TreeAI Coach.")
    parser.add_argument("--demo", metavar="OUTDIR", required=True, help="dossier de sortie des PNG")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        files = write_demo(args.demo)
    except Exception:
        log.exception("Demo rendering failed")
        return 1
    if sys.stdout is not None:
        for f in files:
            sys.stdout.write(f"{f}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
