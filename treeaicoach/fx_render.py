"""Play-rating badges (:mod:`treeaicoach.plays`): pure renderer of the chess.com-style animation.

One badge = a hard-edged graphite plate with a coloured square icon carrying the chess mark
("!!" teal, "!" green, "?!" amber, "?" orange, "??" red; a drawn star / check / cross for
best / good / missed), the class title in condensed type and a one-line French reason.

Animation (:func:`anim_state`), ~2.2 s for a big badge, ~1.3 s for a small one:

1. scale-in from 45 % with an overshoot (ease-out-back, peak ~112 %) + quick fade-in;
2. one subtle light sweep across the plate;
3. hold, then a short slide up + fade out.

:func:`render_frame` draws one frame on a fixed-size transparent canvas (premultiplied BGRA
uint8, the ``UpdateLayeredWindow`` format); the plate itself is rendered once and cached, a
frame only resizes / composites it (~1 ms). :func:`fx_layer_rect` places the layer at the
top-centre (under the toast area) or next to the minimap. Visual rules: docs/DESIGN.md (no
glow, no gradient, 4 px corners, Bahnschrift, no emoji / em dash).

Pure numpy / OpenCV / PIL, importable everywhere, never raises from the public API.
"""

from __future__ import annotations

import logging
import math
import os
import threading
from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np

from treeaicoach import overlay_render as orr

log = logging.getLogger(__name__)

# DESIGN.md tokens + the class colours of the rating scale (RGB)
SURFACE = (18, 21, 19)          # #121513
LINE_STRONG = (47, 53, 50)      # #2F3532
TEXT = (228, 232, 229)          # #E4E8E5
MUTED = (139, 148, 143)         # #8B948F
ON_ACCENT = (12, 14, 13)        # #0C0E0D
CLASS_RGB: dict[str, tuple[int, int, int]] = {
    "brilliant": (38, 166, 154),    # #26A69A teal
    "great": (155, 216, 74),        # #9BD84A TreeAI green (ACCENT)
    "best": (111, 168, 58),         # #6FA83A
    "good": (126, 156, 106),        # #7E9C6A
    "inaccuracy": (227, 192, 75),   # #E3C04B amber
    "mistake": (232, 135, 58),      # #E8873A orange
    "blunder": (229, 72, 77),       # #E5484D red (DANGER)
    "miss": (139, 148, 143),        # #8B948F neutral
}
SYMBOL: dict[str, str] = {"brilliant": "!!", "great": "!", "inaccuracy": "?!", "mistake": "?", "blunder": "??"}

BASE_W, BASE_H = 400, 74        # big badge plate at 1080p (minimum width)
MAX_W = 620                     # the plate grows with the reason up to this width
SMALL_H = 46                    # small badge (during a fight): icon + title only
RADIUS = 4.0
DURATION: dict[str, float] = {"big": 2.2, "small": 1.3}
SCALE_IN_S = 0.30
FADE_IN_S = 0.10
SHINE_S = (0.30, 0.85)
OUT_S = 0.38
OVERSHOOT = 1.70158             # ease-out-back constant (peak ~ +10 %)
START_SCALE = 0.45
FPS = 30.0
TOP_FRAC = 0.205                # top-centre anchor: 20.5 % of the screen height (below the toasts)
POSITIONS = ("top_center", "minimap")

_cache = orr._LRU(16)
_font_cache: dict[tuple[str, int], Any] = {}
_font_lock = threading.Lock()


# ------------------------------------------------------------------------------ fonts
def _font_candidates(weight: str) -> list[tuple[Path, str | None]]:
    win = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    var = "Bold Condensed" if weight == "bold" else "SemiBold Condensed"
    out: list[tuple[Path, str | None]] = [(win / "bahnschrift.ttf", var)]
    for d in (Path("/usr/share/fonts/truetype/dejavu"), Path("/usr/share/fonts/dejavu"),
              Path("/usr/share/fonts/TTF")):
        out.append((d / ("DejaVuSansCondensed-Bold.ttf" if weight == "bold" else "DejaVuSansCondensed.ttf"), None))
    return out


def get_font(size: int, weight: str = "bold") -> Any:
    """Condensed display font: Bahnschrift (Windows), DejaVu Sans Condensed, else the overlay font."""
    size = int(min(max(int(size), 6), 160))
    key = (weight, size)
    with _font_lock:
        f = _font_cache.get(key)
        if f is not None:
            return f
        from PIL import ImageFont

        for path, variation in _font_candidates(weight):
            try:
                if not path.is_file():
                    continue
                f = ImageFont.truetype(str(path), size)
                if variation:
                    try:
                        f.set_variation_by_name(variation)
                    except Exception:
                        pass
                break
            except Exception:
                f = None
        if f is None:
            f = orr.get_font(size, "bold" if weight == "bold" else "semibold")
        _font_cache[key] = f
        return f


# ------------------------------------------------------------------------------ geometry
def scale_for_screen(screen: Any) -> float:
    """Play badge scale: :func:`layout.overlay_scale` ("badge": compact, text >= 12 px)."""
    try:
        from treeaicoach import layout as _lay

        return _lay.overlay_scale(screen, "badge")
    except Exception:
        return 1.0


def plate_size(size: str = "big", scale: float = 1.0, title: str = "", reason: str = "") -> tuple[int, int]:
    k = max(0.4, min(3.0, float(scale) if math.isfinite(scale) else 1.0))
    if size == "small":
        h = SMALL_H * k
        f = get_font(int(round(20 * k)), "bold")
        w = h + 14 * k + orr.text_width(title.upper(), f) + 14 * k
        return int(round(w)), int(round(h))
    h = BASE_H * k
    need = h + 14 * k + 12 * k + max(orr.text_width((title or "").upper(), get_font(int(round(25 * k)), "bold")),
                                     orr.text_width(reason or "", get_font(int(round(17 * k)), "semibold")))
    need += 2.0 * k            # rounding slack: the text must not be cut by fit_text at exactly its width
    return int(round(min(max(need, BASE_W * k), MAX_W * k))), int(round(h))


def layer_size(size: str = "big", scale: float = 1.0) -> tuple[int, int]:
    """Fixed size of the animation layer (room for the overshoot and the slide)."""
    k = max(0.4, min(3.0, float(scale) if math.isfinite(scale) else 1.0))
    w = (MAX_W if size == "big" else BASE_W * 0.6) * k * 1.16
    h = (BASE_H if size == "big" else SMALL_H) * k * 1.8
    return int(math.ceil(w)), int(math.ceil(h))


def layer_envelope(size: str = "big", scale: float = 1.0) -> tuple[int, int, int, int]:
    """Where pixels can appear inside the animation layer (x, y, w, h): the whole width (the plate
    width depends on the text, up to :data:`MAX_W` + the overshoot) and, vertically, from the
    layer's top (the fade-out slides the plate UP by up to 0.7 plate heights) down to the bottom of
    the overshooting plate (+12 % around the centre)."""
    LW, LH = layer_size(size, scale)
    k = max(0.4, min(3.0, float(scale) if math.isfinite(scale) else 1.0))
    ph = (BASE_H if size == "big" else SMALL_H) * k
    bottom = int(math.ceil(min(LH, LH / 2.0 + ph * 0.58)))
    return 0, 0, LW, bottom


def fx_layer_rect(screen: Sequence[int] | None, minimap: Sequence[int] | None, position: str = "top_center",
                  size: str = "big", scale: float | None = None, cfg: Any = None) -> tuple[int, int, int, int]:
    """``(x, y, w, h)`` of the animation layer in screen pixels: the layout's "badge_big" /
    "badge_small" slot (:mod:`treeaicoach.layout`, the one the overlay uses when it runs): big
    badges under the toasts at the top centre (``position`` "minimap": next to the minimap),
    small badges next to the minimap (out of the way during a fight); never over the minimap,
    the HUD card, the timers, the toasts or League's own UI. Clamped to the screen."""
    try:
        scr = tuple(int(v) for v in screen) if screen is not None else (0, 0, 1920, 1080)
        k = scale if scale is not None else scale_for_screen(scr)
        w, h = layer_size(size, k)
        try:
            from treeaicoach import layout as lay

            pub = lay.published(scr, minimap)
            prefs = pub.key[2] if pub is not None and len(pub.key) > 2 else None
            if pub is not None and getattr(prefs, "plays_position", position) != position:
                pub = None                       # the overlay's layout was solved for another position
            if pub is None:
                pub = lay.layout_for(scr, minimap, _PositionCfg(cfg, position))
            slot = pub.slot("badge_small" if size == "small" else "badge_big")
            if slot is not None and slot.anchor == "as_small":
                return slot.rect                 # big play shown small (see badge_size)
            if slot is not None and slot.rect[2] == w and slot.rect[3] == h:
                return slot.rect
        except Exception:
            log.debug("badge slot unavailable", exc_info=True)
        sx, sy, sw, sh = scr
        mm = tuple(int(v) for v in minimap) if minimap is not None else None
        if (position == "minimap" or size == "small") and mm is not None:
            x = mm[0] - w - int(12 * k)
            y = mm[1] + mm[3] - h
            if x < sx:                                   # minimap on the left side
                x = mm[0] + mm[2] + int(12 * k)
        else:
            x = sx + (sw - w) // 2
            y = sy + int(round(sh * TOP_FRAC))
        x = int(min(max(x, sx), sx + sw - w))
        y = int(min(max(y, sy), sy + sh - h))
        return x, y, w, h
    except Exception:
        return 0, 0, 2, 2


def badge_size(screen: Sequence[int] | None, minimap: Sequence[int] | None, size: str = "big",
               position: str = "top_center", cfg: Any = None) -> str:
    """Size a badge is really drawn at: "small" when the layout found no clean place for a big
    one (narrow or tall screens) and gave it the small badge's slot. Never raises."""
    try:
        if size != "big":
            return size
        from treeaicoach import layout as lay

        scr = tuple(int(v) for v in screen) if screen is not None else (0, 0, 1920, 1080)
        pub = lay.published(scr, minimap)
        prefs = pub.key[2] if pub is not None and len(pub.key) > 2 else None
        if pub is None or getattr(prefs, "plays_position", position) != position:
            pub = lay.layout_for(scr, minimap, _PositionCfg(cfg, position))
        slot = pub.slot("badge_big")
        return "small" if slot is not None and slot.anchor == "as_small" else size
    except Exception:
        return size


class _PositionCfg:
    """A config view whose ``plays_position`` is forced (layout_for reads the rest from ``base``)."""

    def __init__(self, base: Any, position: str) -> None:
        self._base = base
        self.plays_position = position if position in POSITIONS else "top_center"

    def __getattr__(self, name: str) -> Any:
        if self._base is None:
            raise AttributeError(name)
        return getattr(self._base, name)


# ------------------------------------------------------------------------------ easing
def _ease_out_back(p: float) -> float:
    p = min(max(p, 0.0), 1.0) - 1.0
    return 1.0 + (OVERSHOOT + 1.0) * p ** 3 + OVERSHOOT * p ** 2


def anim_state(age: float, size: str = "big") -> dict[str, float] | None:
    """``{"scale", "opacity", "dy", "shine"}`` at ``age`` s (dy in plate heights, shine 0..1 or -1);
    None once the animation is over."""
    dur = DURATION.get(size, DURATION["big"])
    if not math.isfinite(age) or age < 0 or age >= dur:
        return None
    p = age / SCALE_IN_S
    scale = START_SCALE + (1.0 - START_SCALE) * _ease_out_back(p) if p < 1.0 else 1.0
    opacity = min(1.0, age / FADE_IN_S)
    dy = 0.0
    out = dur - age
    if out < OUT_S:
        q = 1.0 - out / OUT_S
        opacity *= 1.0 - q
        dy = -0.7 * q * q
    shine = -1.0
    if size == "big" and SHINE_S[0] <= age <= SHINE_S[1]:
        shine = (age - SHINE_S[0]) / (SHINE_S[1] - SHINE_S[0])
    return {"scale": float(scale), "opacity": float(max(0.0, min(1.0, opacity))), "dy": float(dy),
            "shine": float(shine)}


def frame_times(size: str = "big", fps: float = FPS) -> list[float]:
    n = int(math.ceil(DURATION.get(size, 2.2) * fps))
    return [i / fps for i in range(n)]


# ------------------------------------------------------------------------------ drawing
def _glyph(cv_: orr.Canvas, cls: str, cx: float, cy: float, s: float) -> None:
    """Chess mark inside the icon square (side ``s``), dark on the class colour."""
    sym = SYMBOL.get(cls)
    if sym:
        f = get_font(int(round(s * (0.62 if len(sym) == 1 else 0.56))), "bold")
        cv_.text(cx, cy, sym, f, ON_ACCENT, 1.0, anchor="m", shadow=0)
        return
    if cls == "best":          # five-point star
        r0, r1 = s * 0.34, s * 0.14
        pts = []
        for i in range(10):
            a = -math.pi / 2 + i * math.pi / 5
            r = r0 if i % 2 == 0 else r1
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        for i in range(5):     # concave star = 5 triangles + the centre pentagon
            cv_.polygon([pts[(2 * i - 1) % 10], pts[2 * i], pts[(2 * i + 1) % 10]], ON_ACCENT, 1.0, grow=0.5)
        cv_.polygon([pts[j] for j in (1, 3, 5, 7, 9)], ON_ACCENT, 1.0, grow=0.5)
    elif cls == "good":        # check mark
        w = max(2.0, s * 0.11)
        cv_.capsule(cx - s * 0.22, cy + s * 0.01, cx - s * 0.06, cy + s * 0.17, w, ON_ACCENT, 1.0)
        cv_.capsule(cx - s * 0.06, cy + s * 0.17, cx + s * 0.24, cy - s * 0.17, w, ON_ACCENT, 1.0)
    else:                      # miss: cross
        w = max(2.0, s * 0.11)
        d = s * 0.2
        cv_.capsule(cx - d, cy - d, cx + d, cy + d, w, ON_ACCENT, 1.0)
        cv_.capsule(cx - d, cy + d, cx + d, cy - d, w, ON_ACCENT, 1.0)


def _render_plate(cls: str, title: str, reason: str, size: str, scale: float) -> np.ndarray:
    """The static badge (premultiplied RGBA float32), plate only."""
    rgb = CLASS_RGB.get(cls, CLASS_RGB["good"])
    W, H = plate_size(size, scale, title, reason)
    k = H / (SMALL_H if size == "small" else BASE_H)
    cv_ = orr.Canvas(W, H)
    rad = RADIUS * k
    cv_.rrect(0, 0, W, H, rad, SURFACE, 0.96, border=LINE_STRONG, border_alpha=1.0, border_w=max(1.0, k))
    # icon block: a full-height square in the class colour, hard edge on the inside
    s = float(H)
    cv_.rrect(0, 0, s, H, rad, rgb, 1.0)
    cv_.rrect(s * 0.5, 0, s * 0.5, H, 0.0, rgb, 1.0)
    _glyph(cv_, cls, s / 2, H / 2, s)
    tx = s + 14 * k
    max_w = W - tx - 12 * k
    title_u = (title or "").upper()
    if size == "small":
        ft = get_font(int(round(20 * k)), "bold")
        cv_.text(tx, H / 2, orr.fit_text(title_u, ft, max_w), ft, rgb, 1.0, shadow=0)
        return cv_.px
    ft = get_font(int(round(25 * k)), "bold")
    fr = get_font(int(round(17 * k)), "semibold")
    cv_.text(tx, H * 0.34, orr.fit_text(title_u, ft, max_w), ft, rgb, 1.0, shadow=0)
    # thin rule under the title (1 px separator, DESIGN.md)
    cv_.rrect(tx, H * 0.56, max_w, max(1.0, k), 0.0, LINE_STRONG, 1.0)
    cv_.text(tx, H * 0.76, orr.fit_text(reason or "", fr, max_w), fr, TEXT, 1.0, shadow=0)
    return cv_.px


def plate(cls: str, title: str, reason: str = "", size: str = "big", scale: float = 1.0) -> np.ndarray:
    key = (cls, title, reason, size, round(float(scale), 3))
    px = _cache.get(key)
    if px is None:
        px = _render_plate(cls, title, reason, size, float(scale))
        px.setflags(write=False)
        _cache.put(key, px)
    return px


def _shine(px: np.ndarray, pos: float) -> np.ndarray:
    """A diagonal light band sweeping the plate (``pos`` 0..1), kept inside the plate's alpha."""
    h, w = px.shape[:2]
    out = px.copy()
    band = max(6.0, w * 0.11)
    centre = -band + pos * (w + 2 * band + h * 0.5)
    xs = np.arange(w, dtype=np.float32)[None, :]
    ys = np.arange(h, dtype=np.float32)[:, None]
    d = np.abs(xs + ys * 0.5 - centre) / band
    c = np.clip(1.0 - d, 0.0, 1.0) ** 2 * np.float32(0.22)
    a = out[..., 3]
    c = c * np.minimum(1.0, a)
    out[..., :3] = out[..., :3] * (1.0 - c[..., None]) + (c * a)[..., None]
    return out


def render_frame(cls: str, title: str, reason: str = "", age: float = 1.0, *, size: str = "big",
                 scale: float = 1.0) -> np.ndarray | None:
    """One animation frame on the fixed :func:`layer_size` canvas (premultiplied BGRA uint8),
    None when the animation is over. Never raises."""
    try:
        st = anim_state(float(age), size)
        if st is None:
            return None
        LW, LH = layer_size(size, scale)
        out = np.zeros((LH, LW, 4), np.float32)
        base = plate(cls, title, reason, size, scale)
        if st["shine"] >= 0.0:
            base = _shine(base, st["shine"])
        s = st["scale"]
        ph, pw = base.shape[:2]
        tw, th = max(1, int(round(pw * s))), max(1, int(round(ph * s)))
        img = base if (tw, th) == (pw, ph) else cv2.resize(
            base, (tw, th), interpolation=cv2.INTER_AREA if s < 1.0 else cv2.INTER_LINEAR)
        cx = LW / 2.0
        cy = LH / 2.0 + st["dy"] * ph
        x0, y0 = int(round(cx - tw / 2)), int(round(cy - th / 2))
        X0, Y0, X1, Y1 = max(0, x0), max(0, y0), min(LW, x0 + tw), min(LH, y0 + th)
        if X1 > X0 and Y1 > Y0:
            out[Y0:Y1, X0:X1] = img[Y0 - y0:Y1 - y0, X0 - x0:X1 - x0] * np.float32(st["opacity"])
        bgra = np.empty((LH, LW, 4), np.uint8)
        v = np.clip(out, 0.0, 1.0) * np.float32(255.0) + np.float32(0.5)
        bgra[..., 0], bgra[..., 1], bgra[..., 2], bgra[..., 3] = v[..., 2], v[..., 1], v[..., 0], v[..., 3]
        return bgra
    except Exception:
        log.exception("fx render_frame failed")
        return None


def render_play_frame(play: Any, age: float, scale: float = 1.0) -> np.ndarray | None:
    """:func:`render_frame` for a :class:`treeaicoach.plays.Play`."""
    return render_frame(str(getattr(play, "cls", "good")), str(getattr(play, "title", "")),
                        str(getattr(play, "reason", "")), age, size=str(getattr(play, "size", "big")),
                        scale=scale)


__all__ = ["CLASS_RGB", "DURATION", "FPS", "POSITIONS", "anim_state", "badge_size", "frame_times", "fx_layer_rect",
           "layer_size", "plate", "plate_size", "render_frame", "render_play_frame", "scale_for_screen"]
