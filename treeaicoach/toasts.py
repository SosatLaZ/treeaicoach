"""On-screen toasts / banners ("coups de génie", warnings, insights) - pure renderers + queue.

A toast is a small banner shown for ~3 s at the top-centre of the screen, right UNDER League's
kill / objective announcer (layout slot "toasts", :mod:`treeaicoach.layout`): it never covers
the announcer, the minimap (bottom-right), the champion (screen centre) or the scoreboard /
kill feed (top-right). Kinds and colours:

* ``praise``  gold with a teal glow (a good play: "SOLO KILL"),
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
    # TreeAI identity palette (overlay_render.TAI_*): not the game's gold / navy UI
    "praise": (orr.TAI_BRAND, (70, 190, 90), (196, 245, 170)),
    "insight": (orr.TAI_INFO, (30, 140, 220), (170, 222, 255)),
    "warning": (orr.TAI_WARN, (220, 150, 20), (255, 216, 120)),
    "danger": (orr.TAI_DANGER, (220, 40, 40), (255, 150, 145)),
    "engage": (orr.TAI_GO, (20, 190, 120), (160, 245, 205)),
    "retreat": (orr.TAI_DANGER, (225, 45, 45), (255, 155, 150)),
    "call": (orr.TAI_INFO, (30, 140, 220), (175, 225, 255)),
}
#: docs/DESIGN.md tokens used by the flat toasts / banners
DS_SURFACE = (18, 21, 19)       # #121513
DS_RAISED = (25, 29, 27)        # #191D1B
DS_LINE_STRONG = (47, 53, 50)   # #2F3532
DS_TEXT = (228, 232, 229)       # #E4E8E5
DS_ACCENT = (155, 216, 74)      # #9BD84A (the only accent)
DS_WARNING = (232, 162, 58)     # #E8A23A
DS_DANGER = (229, 72, 77)       # #E5484D
STYLE.update({
    "praise": (DS_ACCENT, (70, 190, 90), DS_TEXT),
    "insight": (DS_ACCENT, (30, 140, 220), DS_TEXT),
    "warning": (DS_WARNING, (220, 150, 20), DS_TEXT),
    "danger": (DS_DANGER, (220, 40, 40), DS_TEXT),
    "engage": (DS_ACCENT, (20, 190, 120), DS_TEXT),
    "retreat": (DS_DANGER, (225, 45, 45), DS_TEXT),
    "call": (DS_ACCENT, (30, 140, 220), DS_TEXT),
})
BANNER_H = 84                   # big banner height at 1080p (same width as a toast)
PULSE_S = 1.2                   # subtle pulse period of the banner glow
DURATION_S = 4.0                # auto-hide (declutter: never longer than MAX_DURATION_S)
MAX_DURATION_S = 4.0
SLIDE_IN_S = 0.28
FADE_OUT_S = 0.6
MAX_VISIBLE = 1                 # declutter: one toast at a time
MAX_QUEUED = 6
DEDUPE_S = 20.0                 # same key not shown again for this long
INSIGHT_GAP_S = 25.0            # low-priority "insight" toasts: at most one every 25 s (anti-spam)
MAX_WAIT_S = 10.0               # a queued toast not shown within this delay is dropped (stale)
BASE_W, BASE_H = 440, 68        # at 1080p
GAP = 8                         # between stacked toasts (1080p px)
TOP_FRAC = 0.045                # fallback only (no layout): the layout slot sits under the kill announcer

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
    """Badge drawn when there is no champion icon (``glow`` None: flat, docs/DESIGN.md)."""
    if glow is not None:
        cv_.glow(cx, cy, r * 0.6, r * 1.25, glow, 0.35)
    cv_.disc(cx, cy, r, DS_RAISED if glow is None else orr.TAI_PANEL, 1.0)
    cv_.ring(cx, cy, r - 1.2, 2.2, accent, 1.0)
    if kind == "praise":
        _sparkle(cv_, cx, cy, r * 0.62, accent, 1.0)
        _sparkle(cv_, cx + r * 0.42, cy - r * 0.42, r * 0.2, orr.WHITE, 0.9)
    elif kind == "warning":
        h = r * 1.05
        cv_.polygon([(cx, cy - h * 0.62), (cx + h * 0.6, cy + h * 0.42), (cx - h * 0.6, cy + h * 0.42)],
                    accent, 1.0)
        f = orr.get_font(max(7, int(r * 0.85)), "bold")
        cv_.text(cx, cy + r * 0.08, "!", f, orr.TAI_PANEL, 1.0, anchor="m", shadow=0)
    elif kind == "danger":
        cv_.disc(cx, cy, r * 0.62, accent, 1.0)
        f = orr.get_font(max(7, int(r * 0.95)), "bold")
        cv_.text(cx, cy, "!", f, orr.WHITE, 1.0, anchor="m", shadow=0)
    else:
        d = r * 0.62
        cv_.polygon([(cx, cy - d), (cx + d, cy), (cx, cy + d), (cx - d, cy)], accent, 1.0)
        f = orr.get_font(max(7, int(r * 0.8)), "bold")
        cv_.text(cx, cy, "i", f, orr.TAI_PANEL, 1.0, anchor="m", shadow=0)


def banner_size(scale: float = 1.0) -> tuple[int, int]:
    k = max(0.5, min(3.0, float(scale) if math.isfinite(scale) else 1.0))
    return int(round(BASE_W * k)), int(round(BANNER_H * k))


def _chevrons(cv_: orr.Canvas, cx: float, cy: float, h: float, direction: int, rgb: Any, alpha: float) -> None:
    """Two chevrons (two thick strokes each) pointing right (direction 1) or left (-1)."""
    w = h * 0.36
    th = max(1.5, h * 0.13)
    for i in range(2):
        x = cx + direction * i * w * 0.9
        tip = x + direction * w * 0.5
        back = x - direction * w * 0.5
        a = alpha * (1.0 - 0.35 * i)
        cv_.capsule(back, cy - h / 2, tip, cy, th, rgb, a)
        cv_.capsule(tip, cy, back, cy + h / 2, th, rgb, a)


def _render_banner(kind: str, title: str, subtitle: str, scale: float, pct: int | None) -> np.ndarray:
    """Big ENGAGE / RECULE / call banner (premultiplied RGBA float canvas pixels)."""
    accent, glow, title_rgb = STYLE.get(kind, STYLE["call"])
    W, H = banner_size(scale)
    k = H / BANNER_H
    pad = int(round(14 * k))
    cv_ = orr.Canvas(W + 2 * pad, H + 2 * pad)
    x0, y0 = float(pad), float(pad)
    title, subtitle = no_em_dash(title), no_em_dash(subtitle)
    rad = 6 * k
    cv_.rrect(x0, y0, W, H, rad, DS_SURFACE, 0.95, border=DS_LINE_STRONG, border_alpha=1.0,
              border_w=max(1.0, 1.0 * k))
    cv_.rrect(x0, y0, 4 * k, H, min(rad, 2 * k), accent, 1.0)       # accent: left bar only
    two = bool(subtitle) and orr.text_width(subtitle, orr.get_font(max(11, int(round(14 * k))), "semibold")) > W - 40 * k
    cy = y0 + H * (0.36 if two else 0.42 if subtitle else 0.5)
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
    size = 31 if len(title or "") <= 14 else 25
    ft = orr.get_font(max(12, int(round(size * k))), "display")
    cv_.text(x0 + W / 2, cy, orr.fit_text((title or "").upper(), ft, max_w), ft, accent, 1.0, anchor="m",
             shadow=0.0)
    if subtitle:
        fs = orr.get_font(max(11, int(round(14 * k))), "semibold")
        if orr.text_width(subtitle, fs) <= W - 40 * k:
            cv_.text(x0 + W / 2, y0 + H * 0.80, orr.fit_text(subtitle, fs, W - 40 * k), fs, orr.TAI_TEXT, 0.95,
                     anchor="m", shadow=0.6)
        else:                                       # V2: the WHY on two lines rather than cut
            f2 = orr.get_font(max(11, int(round(12 * k))), "semibold")
            for i, ln in enumerate(orr.wrap_text(subtitle, f2, W - 40 * k, 2)):
                cv_.text(x0 + W / 2, y0 + H * (0.72 + 0.16 * i), ln, f2, orr.TAI_TEXT, 0.95, anchor="m", shadow=0.6)
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
            cv_.rrect(pad, pad, W, H, 6 * k, None, 0.0, border=accent,          # 1 px pulse (meaningful)
                      border_alpha=0.25 + 0.45 * pulse, border_w=max(1.0, 1.0 * k))
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


def no_em_dash(text: str) -> str:
    """docs/DESIGN.md: no em dash in the interface ("Thresh — Objet" -> "Thresh · Objet")."""
    return " ".join(str(text or "").replace(" — ", " · ").replace("—", " · ").replace(" – ", " · ").split())


def _render_base(kind: str, title: str, subtitle: str, icon: np.ndarray | None, scale: float) -> np.ndarray:
    """docs/DESIGN.md toast: flat graphite plate, 1 px hairline, 4 px radius, the accent only as
    a left bar and the icon ring, Bahnschrift caption title, no glow / gradient / sweep."""
    accent, _glow, _title_rgb = STYLE.get(kind, STYLE["insight"])
    title, subtitle = no_em_dash(title), no_em_dash(subtitle)
    W, H = toast_size(scale)
    k = H / BASE_H
    pad = int(round(14 * k))                     # layer margin (kept: layer geometry unchanged)
    cv_ = orr.Canvas(W + 2 * pad, H + 2 * pad)
    x0, y0 = float(pad), float(pad)
    rad = 4 * k
    cv_.rrect(x0, y0, W, H, rad, DS_SURFACE, 0.95, border=DS_LINE_STRONG, border_alpha=1.0,
              border_w=max(1.0, 1.0 * k))
    # accent: left bar only
    cv_.rrect(x0, y0, 3 * k, H, min(rad, 1.5 * k), accent, 1.0)
    # icon / badge (no halo)
    ir = 21 * k
    icx, icy = x0 + 14 * k + ir, y0 + H / 2
    if icon is not None:
        patch = orr.round_icon_patch(icon, 2 * ir, accent, ring_w=2.0 * k)
        cv_.image(icx, icy, patch)
    else:
        _glyph(cv_, kind, icx, icy, ir, accent, None)
    tx = icx + ir + 12 * k
    max_w = x0 + W - 12 * k - tx
    ft = orr.get_font(max(12, int(round(13 * k))), "display")
    fs = orr.get_font(max(12, int(round(16 * k))), "semibold")
    t_txt = orr.fit_text((title or "").upper(), ft, max_w)
    if subtitle and orr.text_width(subtitle, fs) > max_w:
        # at most 2 lines: the subtitle (the useful part) on two lines, the caption title dropped
        f2 = orr.get_font(max(12, int(round(14 * k))), "semibold")
        lines = orr.wrap_text(subtitle, f2, max_w, 2)
        for i, ln in enumerate(lines):
            cv_.text(tx, y0 + H * (0.34 + 0.32 * i), ln, f2, DS_TEXT, 1.0, shadow=0.0)
    elif subtitle:
        cv_.text(tx, y0 + H * 0.32, t_txt, ft, accent, 1.0, shadow=0.0)
        cv_.text(tx, y0 + H * 0.66, orr.fit_text(subtitle, fs, max_w), fs, DS_TEXT, 1.0, shadow=0.0)
    else:
        fb = orr.get_font(max(12, int(round(18 * k))), "display")
        cv_.text(tx, y0 + H / 2, orr.fit_text((title or "").upper(), fb, max_w), fb, DS_TEXT, 1.0, shadow=0.0)
    return cv_.px


def render_toast(kind: str, title: str, subtitle: str = "", icon: np.ndarray | None = None,
                 scale: float = 1.0, age: float | None = None, duration: float = DURATION_S) -> np.ndarray:
    """One toast as premultiplied BGRA uint8 (size :func:`toast_size` + glow margin).

    ``age`` (s) animates the remaining-time line; the slide / fade
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
            accent = STYLE[kind][0]
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
    """Toast / banner scale: :func:`layout.overlay_scale` ("toasts": compact, text >= 12 px)."""
    try:
        from treeaicoach import layout as _lay

        return _lay.overlay_scale(screen, "toasts")
    except Exception:
        return 1.0


def layer_envelope(scale: float = 1.0) -> tuple[int, int, int, int]:
    """Where pixels can appear inside the toast layer (x, y, w, h): the plate of a toast or of a
    big banner, from the layer's top (the slide-in comes from above and the fade-out moves up,
    both clipped by the layer). Used by the layout solver."""
    W, H = toast_size(scale)
    k = H / BASE_H
    pad = int(round(14 * k))
    return pad, 0, W, pad + max(H, banner_size(scale)[1])


def toast_layer_rect(screen: Sequence[int], minimap: Sequence[int] | None = None,
                     cfg: Any = None) -> tuple[int, int, int, int]:
    """(x, y, w, h) of the toast layer: the layout's "toasts" slot (:mod:`treeaicoach.layout`):
    top-centre, right UNDER League's kill / objective announcer (never over it), off the
    scoreboard, the kill feed and the minimap; stays in the top ~30 % (champion at the centre)."""
    sx, sy, sw, sh = (int(v) for v in screen[:4])
    s = scale_for_screen(screen)
    w, h = layer_size(s)
    w, h = min(w, max(1, sw)), min(h, max(1, sh))
    try:
        from treeaicoach import layout as lay

        slot = (lay.published(screen, minimap) or lay.layout_for(screen, minimap, cfg)).slot("toasts")
        if slot is not None and slot.rect[2] == w and slot.rect[3] == h:
            return slot.rect
    except Exception:
        log.debug("toast slot unavailable", exc_info=True)
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
        self._last_insight = -1e9

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
                if kind not in ("danger", "warning", "praise"):
                    if 0.0 <= now - self._last_insight < INSIGHT_GAP_S:
                        return False
                    self._last_insight = now
                self._recent[k] = now
                if len(self._recent) > 64:
                    for old in sorted(self._recent, key=self._recent.get)[:32]:
                        self._recent.pop(old, None)
                dur = min(float(duration), MAX_DURATION_S) if math.isfinite(float(duration)) else DURATION_S
                self._waiting.append(Toast(kind if kind in STYLE else "insight", str(title or ""),
                                           str(subtitle or ""), icon, k, now, max(0.5, dur)))
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


# ------------------------------------------------------------------------------ declutter policy
#: Toast kinds allowed per player level outside a fight (danger / retreat always pass).
#: "insight" toasts mostly repeat the HUD action line: beginners only.
LEVEL_KINDS: dict[str, frozenset[str]] = {
    "debutant": frozenset({"danger", "retreat", "warning", "call", "engage", "praise", "insight"}),
    "intermediaire": frozenset({"danger", "retreat", "warning", "call", "engage", "praise"}),
    "avance": frozenset({"danger", "retreat", "warning", "call"}),
    "expert": frozenset({"danger", "retreat"}),
}
DANGER_KINDS = frozenset({"danger", "retreat"})
_PRIORITY = {"danger": 0, "retreat": 0, "warning": 1, "call": 2, "engage": 2, "praise": 3, "insight": 4}
#: a visible enemy this close to me (normalized minimap units) = fight / skirmish
FIGHT_NEAR_UV = 0.07


def in_fight(state: Any, views: Sequence[ToastView] = ()) -> bool:
    """Heuristic fight flag from the overlay state: a live fight banner, a gank threat, or a
    visible (fresh) enemy right next to me. Never raises."""
    try:
        if any(v.toast.kind in ("engage", "retreat") for v in views):
            return True
        if int(getattr(state, "threat_level", 0) or 0) >= 1:
            return True
        me = getattr(state, "me_uv", None)
        if me is None:
            return False
        for e in getattr(state, "enemies", None) or []:
            if e is None or not getattr(e, "visible", False) or getattr(e, "uv", None) is None:
                continue
            if orr.is_ghost(e):
                continue
            if math.hypot(float(e.uv[0]) - float(me[0]), float(e.uv[1]) - float(me[1])) <= FIGHT_NEAR_UV:
                return True
        return False
    except Exception:
        return False


def select_views(views: Sequence[ToastView], state: Any = None, level: Any = None) -> list[ToastView]:
    """At most ONE toast: danger first; during a fight only danger / retreat; outside a fight the
    kinds of :data:`LEVEL_KINDS` for the player's level; a toast that only repeats the HUD action
    line is dropped; nothing older than :data:`MAX_DURATION_S` unless it is a danger banner."""
    try:
        lvl = str(level or getattr(state, "skill_level", "") or "intermediaire").lower()
        allowed = LEVEL_KINDS.get(lvl, LEVEL_KINDS["intermediaire"])
        fight = in_fight(state, views) if state is not None else False
        hud_line = ""
        if state is not None:
            tip = getattr(state, "tip", None) or getattr(state, "insight", None)
            hud_line = " ".join(str(tip or "").split()).lower()
        out = []
        for v in views:
            kind = v.toast.kind
            if fight and kind not in DANGER_KINDS:
                continue
            if kind not in allowed and kind not in DANGER_KINDS:
                continue
            if kind not in DANGER_KINDS and v.age > MAX_DURATION_S:
                continue
            sub = " ".join(str(v.toast.subtitle or "").split()).lower()
            if kind not in DANGER_KINDS and hud_line and sub and (sub in hud_line or hud_line in sub):
                continue
            out.append(v)
        out.sort(key=lambda v: _PRIORITY.get(v.toast.kind, 5))
        return out[:1]
    except Exception:
        log.debug("select_views failed", exc_info=True)
        return list(views)[:1]
