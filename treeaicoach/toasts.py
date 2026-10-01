"""On-screen toasts / banners ("coups de génie", warnings, insights) - pure renderers + queue.

A toast is a small hextech banner shown for ~3 s at the top-centre of the screen, just under
League's top area: it never covers the minimap (bottom-right), the champion (screen centre)
or the Tab scoreboard / KDA block (top-right). Kinds and colours:

* ``praise``  gold with a teal glow and a light sweep (a good play: "SOLO KILL"),
* ``insight`` blue (Tab analysis: "ALLIÉ EN DIFFICULTÉ"),
* ``warning`` orange ("ENNEMI AVANCÉ", "PIC DE PUISSANCE"),
* ``danger``  red.

:func:`render_toast` draws one toast (premultiplied BGRA uint8, the ``UpdateLayeredWindow``
format) at a given age (slide-in, hold, fade-out). :class:`ToastQueue` is the thread-safe
queue the engine pushes to; :meth:`ToastQueue.active` gives the visible :class:`ToastView`
list put in ``OverlayState.toasts``; :func:`render_toast_layer` + :func:`toast_layer_rect`
compose them for the overlay's "toasts" window.

Pure numpy / PIL (via :mod:`treeaicoach.overlay_render`), never raises from the public API.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

from treeaicoach import overlay_render as orr

log = logging.getLogger(__name__)

KINDS = ("praise", "insight", "warning", "danger")
#: Big banner styles (tactics.Banner): live fight decision / key macro call.
BANNER_KINDS = ("engage", "retreat", "call")
#: (accent, glow, title colour) per kind (RGB).
STYLE: dict[str, tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]] = {
    "praise": (orr.GOLD, orr.TEAL, (240, 210, 140)),
    "insight": ((90, 170, 255), (40, 120, 230), (150, 200, 255)),
    "warning": (orr.WARNING, (230, 120, 20), (255, 190, 100)),
    "danger": (orr.DANGER, (220, 30, 50), (255, 130, 140)),
    "engage": (orr.SAFE, (20, 200, 90), (150, 255, 180)),
    "retreat": (orr.DANGER, (230, 40, 60), (255, 150, 160)),
    "call": (orr.GOLD, (230, 170, 60), (255, 225, 150)),
}
BANNER_H = 84                   # big banner height at 1080p (same width as a toast)
PULSE_S = 1.2                   # subtle pulse period of the banner glow
DURATION_S = 3.2
SLIDE_IN_S = 0.28
FADE_OUT_S = 0.6
MAX_VISIBLE = 2
MAX_QUEUED = 6
DEDUPE_S = 20.0                 # same key not shown again for this long
MAX_WAIT_S = 10.0               # a queued toast not shown within this delay is dropped (stale)
BASE_W, BASE_H = 440, 68        # at 1080p
GAP = 8                         # between stacked toasts (1080p px)
TOP_FRAC = 0.045                # top of the layer: 4.5 % of the screen height (top-centre is free in LoL)
_SHINE_S = (0.15, 1.0)          # praise light sweep window (age, s)

_base_cache = orr._LRU(24)


@dataclass(frozen=True)
class Toast:
    kind: str
    title: str
    subtitle: str = ""
    icon: np.ndarray | None = field(default=None, compare=False, repr=False)   # RGBA champion icon
    key: str = ""
    created: float = 0.0
    duration: float = DURATION_S


@dataclass(frozen=True)
class ToastView:
    toast: Toast
    age: float


def _clamp01(x: float) -> float:
    return 0.0 if not math.isfinite(x) else min(1.0, max(0.0, x))


def toast_anim(age: float, duration: float = DURATION_S) -> tuple[float, float]:
    """(opacity 0..1, vertical offset in units of the toast height) at ``age`` seconds."""
    if not math.isfinite(age) or age < 0 or age >= duration:
        return 0.0, 0.0
    if age < SLIDE_IN_S:
        p = age / SLIDE_IN_S
        e = 1 - (1 - p) ** 3                      # ease-out cubic
        return e, -(1 - e) * 0.35
    out_t = duration - age
    if out_t < FADE_OUT_S:
        p = out_t / FADE_OUT_S
        return p, -(1 - p) * 0.15
    return 1.0, 0.0


def toast_size(scale: float = 1.0) -> tuple[int, int]:
    k = max(0.5, min(3.0, float(scale) if math.isfinite(scale) else 1.0))
    return int(round(BASE_W * k)), int(round(BASE_H * k))


# ------------------------------------------------------------------------------ drawing
def _sparkle(cv_: orr.Canvas, cx: float, cy: float, r: float, rgb: Any, alpha: float = 1.0) -> None:
    """4-point hextech sparkle (two thin convex diamonds)."""
    w = r * 0.28
    cv_.polygon([(cx, cy - r), (cx + w, cy), (cx, cy + r), (cx - w, cy)], rgb, alpha)
    cv_.polygon([(cx - r, cy), (cx, cy - w), (cx + r, cy), (cx, cy + w)], rgb, alpha)


def _glyph(cv_: orr.Canvas, kind: str, cx: float, cy: float, r: float, accent: Any, glow: Any) -> None:
    """Badge drawn when there is no champion icon."""
    cv_.glow(cx, cy, r * 0.6, r * 1.25, glow, 0.35)
    cv_.disc(cx, cy, r, orr.PANEL_DEEP, 1.0)
    cv_.ring(cx, cy, r - 1.2, 2.2, accent, 1.0)
    if kind == "praise":
        _sparkle(cv_, cx, cy, r * 0.62, accent, 1.0)
        _sparkle(cv_, cx + r * 0.42, cy - r * 0.42, r * 0.2, orr.GOLD_LIGHT, 0.9)
    elif kind == "warning":
        h = r * 1.05
        cv_.polygon([(cx, cy - h * 0.62), (cx + h * 0.6, cy + h * 0.42), (cx - h * 0.6, cy + h * 0.42)],
                    accent, 1.0)
        f = orr.get_font(max(7, int(r * 0.85)), "bold")
        cv_.text(cx, cy + r * 0.08, "!", f, orr.PANEL_DEEP, 1.0, anchor="m", shadow=0)
    elif kind == "danger":
        cv_.disc(cx, cy, r * 0.62, accent, 1.0)
        f = orr.get_font(max(7, int(r * 0.95)), "bold")
        cv_.text(cx, cy, "!", f, orr.WHITE, 1.0, anchor="m", shadow=0)
    else:
        d = r * 0.62
        cv_.polygon([(cx, cy - d), (cx + d, cy), (cx, cy + d), (cx - d, cy)], accent, 1.0)
        f = orr.get_font(max(7, int(r * 0.8)), "bold")
        cv_.text(cx, cy, "i", f, orr.PANEL_DEEP, 1.0, anchor="m", shadow=0)


def banner_size(scale: float = 1.0) -> tuple[int, int]:
    k = max(0.5, min(3.0, float(scale) if math.isfinite(scale) else 1.0))
    return int(round(BASE_W * k)), int(round(BANNER_H * k))


def _chevrons(cv_: orr.Canvas, cx: float, cy: float, h: float, direction: int, rgb: Any, alpha: float) -> None:
    """Two chevrons pointing right (direction 1) or left (-1)."""
    w = h * 0.42
    th = h * 0.16
    for i in range(2):
        x = cx + direction * i * w * 0.85
        tip = x + direction * w * 0.5
        back = x - direction * w * 0.5
        cv_.polygon([(back, cy - h / 2), (back + direction * th, cy - h / 2), (tip + direction * th, cy),
                     (back + direction * th, cy + h / 2), (back, cy + h / 2), (tip, cy)], rgb, alpha * (1.0 - 0.3 * i))


def _render_banner(kind: str, title: str, subtitle: str, scale: float, pct: int | None) -> np.ndarray:
    """Big ENGAGE / RECULE / call banner (premultiplied RGBA float canvas pixels)."""
    accent, glow, title_rgb = STYLE.get(kind, STYLE["call"])
    W, H = banner_size(scale)
    k = H / BANNER_H
    pad = int(round(14 * k))
    cv_ = orr.Canvas(W + 2 * pad, H + 2 * pad)
    x0, y0 = float(pad), float(pad)
    rad = 9 * k
    for i in range(7, 0, -1):
        g = i * 2.0 * k
        cv_.rrect(x0 - g, y0 - g, W + 2 * g, H + 2 * g, rad + g, glow, 0.05)
    grad = np.linspace(0, 1, 32, dtype=np.float32)[:, None, None]
    top, bot = orr._rgb(orr.PANEL), orr._rgb(orr.PANEL_DEEP)
    col = (top * (1 - grad) + bot * grad).reshape(32, 1, 3)
    cv_.rrect(x0, y0, W, H, rad, col, 0.94, border=accent, border_alpha=1.0, border_w=2.6 * k)
    cv_.glow(x0 + W / 2, y0 + H / 2, 10 * k, W * 0.45, glow, 0.14)
    cy = y0 + H * (0.42 if subtitle else 0.5)
    if kind in ("engage", "retreat"):
        d = 1 if kind == "engage" else -1
        ch = H * 0.46
        _chevrons(cv_, x0 + 30 * k, cy, ch, d, accent, 0.95)
        _chevrons(cv_, x0 + W - 30 * k - 0.85 * ch * 0.42, cy, ch, d, accent, 0.95)
    else:
        for sx in (x0 + 30 * k, x0 + W - 30 * k):
            dd = 8 * k
            cv_.polygon([(sx, cy - dd), (sx + dd, cy), (sx, cy + dd), (sx - dd, cy)], accent, 1.0)
    max_w = W - 120 * k
    size = 38 if len(title or "") <= 14 else 30
    ft = orr.get_font(max(10, int(round(size * k))), "bold")
    cv_.text(x0 + W / 2, cy, orr.fit_text((title or "").upper(), ft, max_w), ft, title_rgb, 1.0, anchor="m",
             shadow=0.8)
    if subtitle:
        fs = orr.get_font(max(8, int(round(14 * k))), "semibold")
        cv_.text(x0 + W / 2, y0 + H * 0.80, orr.fit_text(subtitle, fs, W - 40 * k), fs, orr.GOLD_LIGHT, 0.95,
                 anchor="m", shadow=0.6)
    if pct is not None and kind in ("engage", "retreat"):
        f = max(0.0, min(1.0, pct / 100.0))
        yb = y0 + H - 4.0 * k
        cv_.capsule(x0 + 14 * k, yb, x0 + W - 14 * k, yb, 2.2 * k, orr.GREY, 0.7)
        cv_.capsule(x0 + 14 * k, yb, x0 + 14 * k + (W - 28 * k) * f, yb, 2.2 * k, accent, 1.0)
    return cv_.px


def render_banner(kind: str, title: str, subtitle: str = "", scale: float = 1.0, age: float | None = None,
                  pct: int | None = None) -> np.ndarray:
    """One big banner as premultiplied BGRA uint8 (:func:`banner_size` + glow margin); ``age``
    animates a subtle pulse of the border glow. Never raises."""
    try:
        kind = kind if kind in BANNER_KINDS else "call"
        key = ("banner", kind, str(title), str(subtitle), round(float(scale), 3), pct)
        base = _base_cache.get(key)
        if base is None:
            base = _render_banner(kind, str(title or ""), str(subtitle or ""), float(scale), pct)
            base.setflags(write=False)
            _base_cache.put(key, base)
        px = base
        if age is not None and math.isfinite(age):
            px = base.copy()
            W, H = banner_size(scale)
            k = H / BANNER_H
            pad = (px.shape[1] - W) / 2.0
            accent = STYLE[kind][0]
            pulse = 0.5 + 0.5 * math.sin(2 * math.pi * (age % PULSE_S) / PULSE_S)
            cv_ = orr.Canvas(1, 1)
            cv_.px, cv_.h, cv_.w = px, px.shape[0], px.shape[1]
            cv_.rrect(pad - 2 * k, pad - 2 * k, W + 4 * k, H + 4 * k, 11 * k, None, 0.0, border=accent,
                      border_alpha=0.15 + 0.35 * pulse, border_w=2.0 * k)
        out = np.empty(px.shape[:2] + (4,), np.uint8)
        v = np.clip(px, 0.0, 1.0) * np.float32(255.0) + np.float32(0.5)
        out[..., 0], out[..., 1], out[..., 2], out[..., 3] = v[..., 2], v[..., 1], v[..., 0], v[..., 3]
        return out
    except Exception:
        log.exception("render_banner failed")
        return np.zeros((2, 2, 4), np.uint8)


def banner_view(banner: Any, now: float) -> "ToastView | None":
    """:class:`ToastView` of a :class:`treeaicoach.tactics.Banner` (shown first in the toast layer)."""
    try:
        since = float(getattr(banner, "since", now))
        until = float(getattr(banner, "until", math.inf))
        dur = (until - since) if math.isfinite(until) else 3600.0
        pct = getattr(banner, "pct", None)
        t = Toast(str(getattr(banner, "style", "call")), str(getattr(banner, "title", "")),
                  str(getattr(banner, "subtitle", "") or ""), None, f"banner:{pct if pct is not None else ''}",
                  since, max(0.5, dur))
        return ToastView(t, max(0.0, float(now) - since))
    except Exception:
        return None


def _render_base(kind: str, title: str, subtitle: str, icon: np.ndarray | None, scale: float) -> np.ndarray:
    accent, glow, title_rgb = STYLE.get(kind, STYLE["insight"])
    W, H = toast_size(scale)
    k = H / BASE_H
    pad = int(round(14 * k))                     # room for the outer glow
    cv_ = orr.Canvas(W + 2 * pad, H + 2 * pad)
    x0, y0 = float(pad), float(pad)
    rad = 7 * k
    # outer glow (a few expanding translucent rounded rects)
    for i in range(6, 0, -1):
        g = i * 2.0 * k
        cv_.rrect(x0 - g, y0 - g, W + 2 * g, H + 2 * g, rad + g, glow, 0.045)
    # panel: vertical gradient
    grad = np.linspace(0, 1, 32, dtype=np.float32)[:, None, None]
    top, bot = orr._rgb(orr.PANEL), orr._rgb(orr.PANEL_DEEP)
    col = (top * (1 - grad) + bot * grad).reshape(32, 1, 3)
    cv_.rrect(x0, y0, W, H, rad, col, 0.92, border=accent, border_alpha=0.9, border_w=1.4 * k)
    # inner accent wash on the left
    cv_.glow(x0 + 40 * k, y0 + H / 2, 4 * k, 70 * k, glow, 0.16)
    # left accent bar
    cv_.capsule(x0 + 5 * k, y0 + 12 * k, x0 + 5 * k, y0 + H - 12 * k, 3.0 * k, accent, 0.95)
    # top ornament: small diamond in the middle of the top border + thin lines
    cx = x0 + W / 2
    d = 5 * k
    cv_.capsule(cx - 70 * k, y0, cx - 10 * k, y0, 1.6 * k, accent, 0.9)
    cv_.capsule(cx + 10 * k, y0, cx + 70 * k, y0, 1.6 * k, accent, 0.9)
    cv_.polygon([(cx, y0 - d), (cx + d, y0), (cx, y0 + d), (cx - d, y0)], orr.PANEL_DEEP, 1.0, grow=1.2 * k)
    cv_.polygon([(cx, y0 - d * 0.7), (cx + d * 0.7, y0), (cx, y0 + d * 0.7), (cx - d * 0.7, y0)], accent, 1.0)
    # icon / badge
    ir = 23 * k
    icx, icy = x0 + 16 * k + ir, y0 + H / 2
    if icon is not None:
        cv_.glow(icx, icy, ir * 0.8, ir * 1.35, glow, 0.45)
        patch = orr.round_icon_patch(icon, 2 * ir, accent, ring_w=2.4 * k)
        cv_.image(icx, icy, patch)
    else:
        _glyph(cv_, kind, icx, icy, ir, accent, glow)
    # texts
    tx = icx + ir + 14 * k
    max_w = x0 + W - 14 * k - tx
    ft = orr.get_font(max(8, int(round(13 * k))), "bold")
    fs = orr.get_font(max(8, int(round(17 * k))), "semibold")
    t_txt = orr.fit_text((title or "").upper(), ft, max_w)
    if subtitle:
        cv_.text(tx, y0 + H * 0.33, t_txt, ft, title_rgb, 1.0, shadow=0.6)
        cv_.text(tx, y0 + H * 0.66, orr.fit_text(subtitle, fs, max_w), fs, orr.GOLD_LIGHT, 1.0, shadow=0.7)
    else:
        fb = orr.get_font(max(8, int(round(19 * k))), "bold")
        cv_.text(tx, y0 + H / 2, orr.fit_text((title or "").upper(), fb, max_w), fb, title_rgb, 1.0)
    return cv_.px


def _rrect_mask(w: int, h: int, x0: float, y0: float, W: float, H: float, rad: float) -> np.ndarray:
    xs = np.arange(w, dtype=np.float32)[None, :] + 0.5
    ys = np.arange(h, dtype=np.float32)[:, None] + 0.5
    qx = np.abs(xs - (x0 + W / 2)) - (W / 2 - rad)
    qy = np.abs(ys - (y0 + H / 2)) - (H / 2 - rad)
    sdf = np.sqrt(np.maximum(qx, 0) ** 2 + np.maximum(qy, 0) ** 2) + np.minimum(np.maximum(qx, qy), 0) - rad
    return np.clip(0.5 - sdf, 0.0, 1.0)


def render_toast(kind: str, title: str, subtitle: str = "", icon: np.ndarray | None = None,
                 scale: float = 1.0, age: float | None = None, duration: float = DURATION_S) -> np.ndarray:
    """One toast as premultiplied BGRA uint8 (size :func:`toast_size` + glow margin).

    ``age`` (s) animates the light sweep (praise) and the remaining-time line; the slide / fade
    are applied by :func:`render_toast_layer` (``age=None``: static, fully drawn). Never raises.
    """
    try:
        kind = kind if kind in STYLE else "insight"
        key = (kind, str(title), str(subtitle), orr._icon_fingerprint(icon) if icon is not None else None,
               round(float(scale), 3))
        base = _base_cache.get(key)
        if base is None:
            base = _render_base(kind, str(title or ""), str(subtitle or ""), icon, float(scale))
            base.setflags(write=False)
            _base_cache.put(key, base)
        if age is None:
            px = base
        else:
            px = base.copy()
            cv_ = orr.Canvas(1, 1)
            cv_.px, cv_.h, cv_.w = px, px.shape[0], px.shape[1]
            W, H = toast_size(scale)
            k = H / BASE_H
            pad = (px.shape[1] - W) / 2.0
            accent, glow, _t = STYLE[kind]
            if kind == "praise" and _SHINE_S[0] <= age <= _SHINE_S[1]:
                p = (age - _SHINE_S[0]) / (_SHINE_S[1] - _SHINE_S[0])
                pos = pad - 60 * k + p * (W + 120 * k)
                xs = np.arange(px.shape[1], dtype=np.float32)[None, :]
                ys = np.arange(px.shape[0], dtype=np.float32)[:, None]
                band = np.clip(1.0 - np.abs(xs - pos + (ys - px.shape[0] / 2) * 0.45) / (26 * k), 0.0, 1.0)
                mask = _rrect_mask(px.shape[1], px.shape[0], pad, pad, W, H, 7 * k)
                cv_.paint(0, 0, band * band * mask, orr.GOLD_LIGHT, 0.22)
            remain = _clamp01(1.0 - age / max(0.1, duration))
            if remain > 0:
                y = pad + H - 3.0 * k
                x_a = pad + 12 * k
                cv_.capsule(x_a, y, x_a + (W - 24 * k) * remain, y, 1.6 * k, accent, 0.75)
        out = np.empty(px.shape[:2] + (4,), np.uint8)
        v = np.clip(px, 0.0, 1.0) * np.float32(255.0) + np.float32(0.5)
        out[..., 0], out[..., 1], out[..., 2], out[..., 3] = v[..., 2], v[..., 1], v[..., 0], v[..., 3]
        return out
    except Exception:
        log.exception("render_toast failed")
        return np.zeros((2, 2, 4), np.uint8)


# ------------------------------------------------------------------------------ layer
def layer_size(scale: float = 1.0, max_visible: int = MAX_VISIBLE) -> tuple[int, int]:
    """Size of the toast layer window (all stacked slots + glow margins)."""
    W, H = toast_size(scale)
    k = H / BASE_H
    pad = int(round(14 * k))
    extra = int(round((BANNER_H - BASE_H) * k))          # room for a big banner in the first slot
    return W + 2 * pad, max_visible * (H + int(round(GAP * k))) + 2 * pad + extra


def scale_for_screen(screen: Any) -> float:
    try:
        h = float(screen[3])
    except (TypeError, ValueError, IndexError):
        return 1.0
    return max(0.6, min(2.2, h / 1080.0)) if math.isfinite(h) and h > 0 else 1.0


def toast_layer_rect(screen: Sequence[int], minimap: Sequence[int] | None = None) -> tuple[int, int, int, int]:
    """(x, y, w, h) of the toast layer: top-centre of ``screen``, under LoL's top area.

    Never over the minimap (moved left if it would overlap, e.g. an exotic HUD scale) nor over
    the Tab / KDA block at the top-right; stays in the top ~25 % (champion at the centre)."""
    sx, sy, sw, sh = (int(v) for v in screen[:4])
    s = scale_for_screen(screen)
    w, h = layer_size(s)
    w, h = min(w, max(1, sw)), min(h, max(1, sh))
    x = sx + (sw - w) // 2
    y = sy + int(round(sh * TOP_FRAC)) - int(round(14 * s))
    if minimap is not None:
        mx, my, mw, mh = (int(v) for v in minimap[:4])
        if x < mx + mw and mx < x + w and y < my + mh and my < y + h:
            x = max(sx, mx - w - 8)
    x = min(max(sx, x), sx + sw - w)
    y = min(max(sy, y), sy + sh - h)
    return x, y, w, h


def render_toast_layer(views: Sequence[ToastView], scale: float = 1.0) -> np.ndarray:
    """All visible toasts stacked top-down in one premultiplied BGRA image (:func:`layer_size`)."""
    LW, LH = layer_size(scale)
    out = np.zeros((LH, LW, 4), np.uint8)
    try:
        W, H = toast_size(scale)
        k = H / BASE_H
        gap = int(round(GAP * k))
        y0 = 0
        for v in list(views)[:MAX_VISIBLE]:
            op, dy = toast_anim(v.age, v.toast.duration)
            big = v.toast.kind in BANNER_KINDS
            h = banner_size(scale)[1] if big else H
            if op > 0.0:
                if big:
                    pct = None
                    if v.toast.key.startswith("banner:") and v.toast.key[7:].isdigit():
                        pct = int(v.toast.key[7:])
                    img = render_banner(v.toast.kind, v.toast.title, v.toast.subtitle, scale, age=v.age, pct=pct)
                else:
                    img = render_toast(v.toast.kind, v.toast.title, v.toast.subtitle, v.toast.icon, scale,
                                       age=v.age, duration=v.toast.duration)
                _blend_premul(out, img, 0, y0 + int(round(dy * h)), op)
            y0 += h + gap
    except Exception:
        log.exception("render_toast_layer failed")
    return out


def _blend_premul(dst: np.ndarray, src: np.ndarray, x: int, y: int, opacity: float) -> None:
    H, W = dst.shape[:2]
    h, w = src.shape[:2]
    X0, Y0, X1, Y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if X0 >= X1 or Y0 >= Y1:
        return
    s = src[Y0 - y:Y1 - y, X0 - x:X1 - x].astype(np.float32) * np.float32(opacity)
    d = dst[Y0:Y1, X0:X1].astype(np.float32)
    d = s + d * (1.0 - s[..., 3:4] / 255.0)
    dst[Y0:Y1, X0:X1] = np.clip(d + 0.5, 0, 255).astype(np.uint8)


# ------------------------------------------------------------------------------ queue
class ToastQueue:
    """Thread-safe toast queue: at most :data:`MAX_VISIBLE` on screen, the rest wait."""

    def __init__(self, clock: Any = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._shown: list[Toast] = []
        self._waiting: list[Toast] = []
        self._recent: dict[str, float] = {}

    def reset(self) -> None:
        with self._lock:
            self._shown.clear()
            self._waiting.clear()
            self._recent.clear()

    def push(self, kind: str, title: str, subtitle: str = "", icon: np.ndarray | None = None,
             key: str | None = None, t: float | None = None, duration: float = DURATION_S) -> bool:
        """Queue a toast; False if dropped (same ``key`` shown recently, queue full). Never raises."""
        try:
            now = float(self._clock() if t is None else t)
            k = key or f"{kind}:{title}:{subtitle}"
            with self._lock:
                last = self._recent.get(k)
                if last is not None and 0.0 <= now - last < DEDUPE_S:
                    return False
                if len(self._waiting) >= MAX_QUEUED:
                    return False
                self._recent[k] = now
                if len(self._recent) > 64:
                    for old in sorted(self._recent, key=self._recent.get)[:32]:
                        self._recent.pop(old, None)
                self._waiting.append(Toast(kind if kind in STYLE else "insight", str(title or ""),
                                           str(subtitle or ""), icon, k, now, float(duration)))
            return True
        except Exception:
            log.exception("ToastQueue.push failed")
            return False

    def active(self, now: float | None = None) -> list[ToastView]:
        """Visible toasts (oldest on top) with their age; promotes waiting ones. Never raises."""
        try:
            t = float(self._clock() if now is None else now)
            with self._lock:
                self._shown = [s for s in self._shown if t - s.created < s.duration]
                self._waiting = [w for w in self._waiting if t - w.created <= MAX_WAIT_S]
                while self._waiting and len(self._shown) < MAX_VISIBLE:
                    w = self._waiting.pop(0)
                    # its display starts now (it may have waited)
                    self._shown.append(Toast(w.kind, w.title, w.subtitle, w.icon, w.key, max(t, w.created),
                                             w.duration))
                return [ToastView(s, max(0.0, t - s.created)) for s in self._shown]
        except Exception:
            log.exception("ToastQueue.active failed")
            return []

    def __len__(self) -> int:
        with self._lock:
            return len(self._shown) + len(self._waiting)
