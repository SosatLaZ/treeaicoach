"""On-screen overlay windows (minimap marks, radar, HUD, danger flash) - Win32 layered windows via ctypes.

Everything is *drawn* by :mod:`treeaicoach.overlay_render` (pure numpy / PIL); this module only
puts the premultiplied BGRA images on screen with ``UpdateLayeredWindow`` (per-pixel alpha), in
separate popup windows of the TreeAI Coach process:

* styles ``WS_POPUP`` + ``WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW |
  WS_EX_NOACTIVATE | WS_EX_TOPMOST``: always on top, never activated, mouse clicks go through,
  no taskbar button. No injection, no DirectX hook: works with the game in *Borderless* or
  *Windowed* mode.
* one dedicated thread owns every window and pumps its messages with ``PeekMessageW``; it
  refreshes at ~12 Hz from ``state_provider()`` and hides everything when the state is None.
* the default "minimap" mode draws thin marks *exactly over the real minimap*
  (``state.minimap_rect``, physical px) on a transparent window. That window is captured like
  any other one (visible in the user's screenshots / streams), so it never draws champion
  portraits - only thin rings drawn *outside* the real icons, role tags, arrows, dashed
  last-seen circles with timers and fog outlines - which the detector does not mistake for
  champions. ``cfg.overlay_hide_from_capture`` (default False) additionally excludes it from
  capture (``SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)``, Windows 10 2004+); the mode
  works the same with or without it. "radar" mode shows an enlarged copy placed *above* the
  minimap (shrunk rather than moved towards the centre), never over it. Every window is
  clamped inside the screen, and the screen used for the layout always contains the minimap
  (see :func:`effective_screen`).
* "move mode" (:meth:`OverlayManager.set_move_mode`): windows stop being click-through, can be
  dragged (``WM_NCHITTEST`` -> ``HTCAPTION``) and report their new position through
  ``on_moved(name, x, y)`` so the UI can save ``cfg.radar_xy`` / ``cfg.hud_xy``.

Coordinates are physical pixels (the process is per-monitor DPI aware; the overlay thread also
asks for per-monitor-v2 awareness). Off Windows, or after any Win32 failure (logged once),
:attr:`OverlayManager.ok` is False and every method is a harmless no-op.

The placement helpers at the top (:func:`radar_geometry`, :func:`hud_placement`, ...) are pure
functions, shared with ``overlay_render.render_preview`` and unit-tested everywhere.
"""

from __future__ import annotations

import copy
import logging
import math
import sys
import threading
import time
from typing import Any, Callable, Iterable, Sequence

import numpy as np

log = logging.getLogger(__name__)

RectT = tuple[int, int, int, int]

#: Refresh rate of the overlay thread (Hz).
REFRESH_HZ = 12.0
#: Gap (px) between the radar and the minimap / screen edges.
RADAR_GAP = 8
#: Margin (px, at 1080p) between the HUD and the screen edges.
HUD_MARGIN = 16
#: Radar side limits (px).
RADAR_MIN, RADAR_MAX = 96, 1024
#: HUD width at 1080p and its limits.
HUD_BASE_WIDTH, HUD_MIN_WIDTH, HUD_MAX_WIDTH = 280, 240, 520
#: Flash intensity quantization (the full-screen image is re-rendered only when it changes).
FLASH_STEP = 0.1
#: Refresh rate (Hz) of the radar / HUD while nothing is animated (saves CPU).
CALM_HZ = 4.0
#: How often (s) visible windows are re-asserted as topmost.
TOPMOST_EVERY_S = 2.0
RADAR_POSITIONS = ("above_minimap", "left_of_minimap", "top_left", "custom")
HUD_POSITIONS = ("above_minimap", "top_left", "top_right", "left_middle", "custom")
#: ``cfg.overlay_mode``: "minimap" draws the marks *on* the real minimap (with or without capture
#: exclusion), "radar" shows an enlarged copy
#: above the minimap, "off" draws no map at all (HUD / flash keep their own switches).
OVERLAY_MODES = ("minimap", "radar", "off")
#: SetWindowDisplayAffinity values (WDA_EXCLUDEFROMCAPTURE: Windows 10 2004 / build 19041+).
WDA_NONE = 0x00
WDA_EXCLUDEFROMCAPTURE = 0x11
EXCLUDE_MIN_BUILD = 19041


# ======================================================================================
# Pure placement helpers
# ======================================================================================
def as_rect(r: Any) -> RectT | None:
    """``Rect`` / 4-sequence -> ``(x, y, w, h)`` ints, or None if invalid / empty."""
    if r is None:
        return None
    try:
        vals = (r.x, r.y, r.w, r.h) if hasattr(r, "x") else tuple(r)[:4]
        if len(vals) != 4:
            return None
        fl = [float(v) for v in vals]
        if not all(math.isfinite(v) for v in fl):
            return None
        x, y, w, h = (int(round(v)) for v in fl)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


def rects_overlap(a: Sequence[int], b: Sequence[int]) -> bool:
    """True when the two ``(x, y, w, h)`` rectangles share at least one pixel."""
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def clamp_to_screen(x: int, y: int, w: int, h: int, screen: Sequence[int]) -> tuple[int, int]:
    """Top-left corner moved so that a ``w x h`` window stays inside ``screen``."""
    sx, sy, sw, sh = screen[:4]
    x = min(max(int(x), sx), sx + max(0, sw - w))
    y = min(max(int(y), sy), sy + max(0, sh - h))
    return x, y


def _fits(x: int, y: int, w: int, h: int, screen: Sequence[int]) -> bool:
    sx, sy, sw, sh = screen[:4]
    return x >= sx and y >= sy and x + w <= sx + sw and y + h <= sy + sh


def _xy(value: Any) -> tuple[int, int] | None:
    try:
        x, y = (int(round(float(v))) for v in list(value)[:2])
        return x, y
    except (TypeError, ValueError):
        return None


def _scale_of(screen: Sequence[int] | None) -> float:
    return max(0.5, min(3.0, (screen[3] / 1080.0) if screen else 1.0))


def effective_screen(screen: Any, minimap: Any, monitor: Any = None) -> RectT:
    """Screen rectangle to lay the overlay out in, consistent with the minimap rectangle.

    The reported game window can be in another coordinate space than the (physical) minimap
    rectangle, e.g. DPI-virtualized: 1536 x 864 for a 1920 x 1080 screen at 125 %. When the
    minimap does not fit inside ``screen``, the monitor containing the minimap (``monitor``) or
    at least the union of both rectangles is used, so nothing gets pushed towards the centre.
    """
    scr, mm, mon = as_rect(screen), as_rect(minimap), as_rect(monitor)
    if mm is None:
        return scr or mon or (0, 0, 1920, 1080)
    if scr is not None and _fits(mm[0], mm[1], mm[2], mm[3], scr):
        return scr
    if mon is not None and _fits(mm[0], mm[1], mm[2], mm[3], mon):
        return mon
    base = scr or mon or (0, 0, max(1920, mm[0] + mm[2]), max(1080, mm[1] + mm[3]))
    x0, y0 = min(base[0], mm[0]), min(base[1], mm[1])
    x1, y1 = max(base[0] + base[2], mm[0] + mm[2]), max(base[1] + base[3], mm[1] + mm[3])
    return x0, y0, x1 - x0, y1 - y0


def resolve_overlay_mode(mode: Any, capture_excluded: bool = False) -> str:
    """Mode actually used: the configured one, junk -> "minimap".

    ``capture_excluded`` is kept for compatibility and ignored: the minimap layer draws no
    portraits, so it works whether or not it is hidden from screen capture.
    """
    m = str(mode or "").strip().lower()
    return m if m in OVERLAY_MODES else "minimap"


def radar_size(minimap: Any, scale: float = 1.0) -> int:
    """Radar side (px): the minimap side times ``scale`` (0.5..2.0), clamped to 96..1024."""
    mm = as_rect(minimap)
    try:
        s = float(scale)
        s = s if math.isfinite(s) else 1.0
    except (TypeError, ValueError):
        s = 1.0
    s = min(max(s, 0.5), 2.0)
    side = max(mm[2], mm[3]) if mm is not None else 256
    return int(min(max(round(side * s), RADAR_MIN), RADAR_MAX))


def _radar_candidates(mm: RectT, screen: RectT, size: int, position: str) -> list[tuple[int, int]]:
    mx, my, mw, mh = mm
    sx, sy = screen[0], screen[1]
    g = RADAR_GAP
    above = (mx + mw - size, my - g - size)
    left = (mx - g - size, my + mh - size)
    top_left = (sx + g, sy + g)
    if position == "left_of_minimap":
        return [left, above, top_left]
    if position == "top_left":
        return [top_left, above, left]
    return [above, left, top_left]


def radar_geometry(minimap: Any, screen: Any, scale: float = 1.0, position: str = "above_minimap",
                   custom_xy: Any = None) -> tuple[int, int, int]:
    """``(x, y, size)`` of the radar window, never overlapping the minimap, inside the screen.

    ``position``: "above_minimap" (bottom edge ``RADAR_GAP`` px above the minimap top, same right
    edge; *shrunk* when it does not fit, down to ``RADAR_MIN``; only a minimap with less room than
    that above it sends the radar to its left), "left_of_minimap", "top_left" or
    "custom" (``custom_xy`` = ``[x, y]``). The size shrinks if the requested one fits nowhere.
    """
    mm = as_rect(minimap)
    scr = as_rect(screen)
    size = radar_size(mm, scale)
    if scr is None:
        scr = (0, 0, 1920, 1080) if mm is None else (0, 0, max(1920, mm[0] + mm[2]), max(1080, mm[1] + mm[3]))
    size = int(min(size, scr[2], scr[3]))
    if mm is None:
        x, y = clamp_to_screen(scr[0] + scr[2] - size - RADAR_GAP, scr[1] + scr[3] - size - RADAR_GAP,
                               size, size, scr)
        return x, y, size
    position = position if position in RADAR_POSITIONS else "above_minimap"
    if position == "custom":
        xy = _xy(custom_xy)
        if xy is not None:
            x, y = clamp_to_screen(xy[0], xy[1], size, size, scr)
            if not rects_overlap((x, y, size, size), mm):
                return x, y, size
        position = "above_minimap"
    if position == "above_minimap":
        # above the minimap, right edges aligned; shrink rather than moving it to the left
        room_above = mm[1] - scr[1] - 2 * RADAR_GAP
        room_x = mm[0] + mm[2] - scr[0]
        s = int(min(size, room_above, room_x, scr[2]))
        if s >= min(size, RADAR_MIN):
            x, y = mm[0] + mm[2] - s, mm[1] - RADAR_GAP - s
            x, y = clamp_to_screen(x, y, s, s, scr)
            if not rects_overlap((x, y, s, s), mm):
                return x, y, s
    # largest size that fits above or left of the minimap
    room = max(mm[1] - scr[1] - 2 * RADAR_GAP, mm[0] - scr[0] - 2 * RADAR_GAP)
    for s in (size, max(RADAR_MIN // 2, min(size, room))):
        cands = _radar_candidates(mm, scr, s, position)
        for cx, cy in cands:
            if _fits(cx, cy, s, s, scr) and not rects_overlap((cx, cy, s, s), mm):
                return cx, cy, s
        for cx, cy in cands:
            x, y = clamp_to_screen(cx, cy, s, s, scr)
            if not rects_overlap((x, y, s, s), mm):
                return x, y, s
    # pathological (minimap fills the screen): smallest radar in the top-left corner
    s = max(RADAR_MIN // 2, min(size, 128))
    x, y = clamp_to_screen(scr[0], scr[1], s, s, scr)
    return x, y, s


def radar_placement(minimap: Any, screen: Any, size: int, position: str = "above_minimap",
                    custom_xy: Any = None) -> tuple[int, int]:
    """Top-left corner of a ``size`` radar (see :func:`radar_geometry`; the size is kept)."""
    mm, scr = as_rect(minimap), as_rect(screen)
    size = max(1, int(size))
    if mm is None or scr is None:
        x, y, _ = radar_geometry(mm, scr, 1.0, position, custom_xy)
        return clamp_to_screen(x, y, size, size, scr or (0, 0, 1920, 1080))
    if position == "custom":
        xy = _xy(custom_xy)
        if xy is not None:
            x, y = clamp_to_screen(xy[0], xy[1], size, size, scr)
            if not rects_overlap((x, y, size, size), mm):
                return x, y
        position = "above_minimap"
    cands = _radar_candidates(mm, scr, size, position if position in RADAR_POSITIONS else "above_minimap")
    for cx, cy in cands:
        if _fits(cx, cy, size, size, scr) and not rects_overlap((cx, cy, size, size), mm):
            return cx, cy
    for cx, cy in cands:
        x, y = clamp_to_screen(cx, cy, size, size, scr)
        if not rects_overlap((x, y, size, size), mm):
            return x, y
    return clamp_to_screen(cands[0][0], cands[0][1], size, size, scr)


def hud_width(screen: Any) -> int:
    """HUD width (px) for a screen: 340 px at 1080p, proportional to the height."""
    scr = as_rect(screen)
    w = HUD_BASE_WIDTH * _scale_of(scr)
    return int(min(max(round(w), HUD_MIN_WIDTH), HUD_MAX_WIDTH))


def flash_thickness(screen: Any) -> int:
    """Danger-flash frame thickness (px): 10 px at 1080p."""
    return int(max(6, round(10 * _scale_of(as_rect(screen)))))


def hud_placement(screen: Any, w: int, h: int, position: str = "above_minimap", custom_xy: Any = None,
                  avoid: Iterable[Any] = (), anchor: Any = None) -> tuple[int, int]:
    """Top-left corner of the HUD panel, inside the screen and not over the ``avoid`` rects.

    ``position``: "above_minimap" (default: bottom edge ``RADAR_GAP`` px above ``anchor`` - the
    minimap, or the radar when one is shown above it - right edges aligned; "top_right" when it
    does not fit or without anchor), "top_left", "top_right" (below the game's score bar),
    "left_middle" or "custom" (``custom_xy``). When the panel would cover an ``avoid``
    rectangle (minimap, radar) it slides vertically below (or above) it.
    """
    scr = as_rect(screen) or (0, 0, 1920, 1080)
    w, h = max(1, int(w)), max(1, int(h))
    k = _scale_of(scr)
    m = int(round(HUD_MARGIN * k))
    sx, sy, sw, sh = scr
    position = position if position in HUD_POSITIONS else "above_minimap"
    xy = _xy(custom_xy) if position == "custom" else None
    anc = as_rect(anchor)
    if position == "above_minimap":
        if anc is not None and anc[1] - RADAR_GAP - h >= sy + m and w <= sw:
            x, y = anc[0] + anc[2] - w, anc[1] - RADAR_GAP - h
            x = min(x, sx + sw - w)
            x = max(x, sx)
            blockers = [r for r in (as_rect(a) for a in avoid) if r is not None]
            if not any(rects_overlap((x, y, w, h), b) for b in blockers):
                return x, y
        position = "top_right"
    if xy is not None:
        x, y = xy
    elif position == "top_right":
        x, y = sx + sw - w - m, sy + int(round(0.075 * sh))
    elif position == "left_middle":
        x, y = sx + m, sy + (sh - h) // 2
    else:
        x, y = sx + m, sy + m
    x, y = clamp_to_screen(x, y, w, h, scr)
    blockers = [r for r in (as_rect(a) for a in avoid) if r is not None]
    for _ in range(len(blockers) + 1):
        hit = next((b for b in blockers if rects_overlap((x, y, w, h), b)), None)
        if hit is None:
            return x, y
        below = hit[1] + hit[3] + RADAR_GAP
        above = hit[1] - RADAR_GAP - h
        if below + h <= sy + sh:
            y = below
        elif above >= sy:
            y = above
        else:   # no vertical room: slide horizontally
            left = hit[0] - RADAR_GAP - w
            x = left if left >= sx else hit[0] + hit[2] + RADAR_GAP
        x, y = clamp_to_screen(x, y, w, h, scr)
    return x, y


def quantize_flash(intensity: Any) -> float:
    """Flash intensity rounded to :data:`FLASH_STEP` (0 when invalid)."""
    try:
        f = float(intensity)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(f) or f <= 0:
        return 0.0
    return round(min(1.0, round(f / FLASH_STEP) * FLASH_STEP), 3)


def needs_fast_refresh(state: Any) -> bool:
    """True while something on the radar / HUD animates (threat, visible jungler, fading alert)."""
    try:
        if int(getattr(state, "threat_level", 0) or 0) >= 1:
            return True
        if float(getattr(state, "flash", 0.0) or 0.0) > 0:
            return True
        la = getattr(state, "last_alert", None)
        if la and float(la[2]) < 4.5:
            return True
        return any(getattr(e, "visible", False) and (getattr(e, "is_jungler", False) or getattr(e, "approaching", False))
                   for e in (getattr(state, "enemies", None) or []) if e is not None)
    except Exception:
        return True


def move_mode_frame(bgra: np.ndarray, label: str = "") -> np.ndarray:
    """Copy of an overlay image with a visible grab area + teal dashed frame (move mode).

    Layered windows only receive the mouse where alpha > 0, so the whole rectangle gets a faint
    dark veil to be draggable.
    """
    img = np.array(bgra, dtype=np.uint8, copy=True)
    if img.ndim != 3 or img.shape[2] != 4 or img.shape[0] < 4 or img.shape[1] < 4:
        return img
    h, w = img.shape[:2]
    veil_a = 90.0 / 255.0
    veil = np.array([40, 20, 10], np.float32) * veil_a  # premultiplied #0A1428
    f = img.astype(np.float32)
    inv = 1.0 - f[..., 3:4] / 255.0
    f[..., :3] += veil * inv
    f[..., 3:4] += 255.0 * veil_a * inv
    img = np.clip(f + 0.5, 0, 255).astype(np.uint8)
    teal = np.array([185, 200, 10, 255], np.uint8)  # BGRA #0AC8B9 opaque
    dash = (np.arange(max(w, h)) // 8) % 2 == 0
    for t in range(2):
        img[t, dash[:w]] = teal
        img[h - 1 - t, dash[:w]] = teal
        img[dash[:h], t] = teal
        img[dash[:h], w - 1 - t] = teal
    if label:
        try:
            from treeaicoach.overlay_render import Canvas, get_font, TEAL
            cv_ = Canvas(w, h)
            cv_.paint_premul(0, 0, img)
            cv_.rrect(6, 6, min(w - 12, 150), 20, 5, (1, 10, 19), 0.9, border=TEAL, border_alpha=0.9)
            cv_.text(12, 16, label, get_font(12, "bold"), TEAL)
            img = cv_.to_bgra()
        except Exception:  # pragma: no cover - cosmetic only
            log.debug("move-mode label failed", exc_info=True)
    return img


# ======================================================================================
# Win32 (ctypes) - loaded lazily, Windows only
# ======================================================================================
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
GWL_EXSTYLE = -20
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
HWND_TOPMOST = -1
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
BI_RGB = 0
DIB_RGB_COLORS = 0
PM_REMOVE = 0x0001
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_NCHITTEST = 0x0084
WM_EXITSIZEMOVE = 0x0232
WM_MOUSEACTIVATE = 0x0021
MA_NOACTIVATE = 3
HTCAPTION = 2
HTTRANSPARENT = -1
IDC_SIZEALL = 32646
MONITOR_DEFAULTTONEAREST = 2
SM_CXSCREEN, SM_CYSCREEN = 0, 1
CLASS_NAME = "TreeAICoachOverlay"

_api: Any = None
_api_lock = threading.Lock()
_api_error: str | None = None
_windows_by_hwnd: dict[int, "LayeredWindow"] = {}


class _Api:
    """ctypes prototypes (correct 64-bit types for every handle / pointer-sized value)."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes as wt

        self.ctypes = ctypes
        self.wt = wt
        LRESULT = ctypes.c_ssize_t
        WPARAM = ctypes.c_size_t
        LPARAM = ctypes.c_ssize_t
        self.LRESULT, self.WPARAM, self.LPARAM = LRESULT, WPARAM, LPARAM
        HANDLE = ctypes.c_void_p
        self.WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, WPARAM, LPARAM)

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", self.WNDPROC),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", HANDLE),
                        ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                        ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                        ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]

        class BLENDFUNCTION(ctypes.Structure):
            _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                        ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                        ("dwFlags", wt.DWORD)]

        self.WNDCLASSEXW, self.BITMAPINFO, self.BITMAPINFOHEADER = WNDCLASSEXW, BITMAPINFO, BITMAPINFOHEADER
        self.BLENDFUNCTION, self.MONITORINFO = BLENDFUNCTION, MONITORINFO

        u = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
        g = ctypes.WinDLL("gdi32", use_last_error=True)  # type: ignore[attr-defined]
        k = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        self.user32, self.gdi32, self.kernel32 = u, g, k

        def proto(fn: Any, res: Any, *args: Any) -> None:
            fn.restype = res
            fn.argtypes = list(args)

        P = ctypes.POINTER
        proto(k.GetModuleHandleW, wt.HMODULE, wt.LPCWSTR)
        proto(u.RegisterClassExW, wt.ATOM, P(WNDCLASSEXW))
        proto(u.UnregisterClassW, wt.BOOL, wt.LPCWSTR, wt.HINSTANCE)
        proto(u.CreateWindowExW, wt.HWND, wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int,
              ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID)
        proto(u.DefWindowProcW, LRESULT, wt.HWND, wt.UINT, WPARAM, LPARAM)
        proto(u.DestroyWindow, wt.BOOL, wt.HWND)
        proto(u.ShowWindow, wt.BOOL, wt.HWND, ctypes.c_int)
        proto(u.IsWindow, wt.BOOL, wt.HWND)
        proto(u.SetWindowPos, wt.BOOL, wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
              ctypes.c_int, wt.UINT)
        proto(u.GetWindowRect, wt.BOOL, wt.HWND, P(wt.RECT))
        proto(u.UpdateLayeredWindow, wt.BOOL, wt.HWND, wt.HDC, P(wt.POINT), P(wt.SIZE), wt.HDC,
              P(wt.POINT), wt.COLORREF, P(BLENDFUNCTION), wt.DWORD)
        proto(u.GetDC, wt.HDC, wt.HWND)
        proto(u.ReleaseDC, ctypes.c_int, wt.HWND, wt.HDC)
        proto(u.PeekMessageW, wt.BOOL, P(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT)
        proto(u.TranslateMessage, wt.BOOL, P(wt.MSG))
        proto(u.DispatchMessageW, LRESULT, P(wt.MSG))
        proto(u.LoadCursorW, HANDLE, wt.HINSTANCE, ctypes.c_void_p)
        proto(u.GetSystemMetrics, ctypes.c_int, ctypes.c_int)
        proto(u.MonitorFromPoint, HANDLE, wt.POINT, wt.DWORD)
        proto(u.GetMonitorInfoW, wt.BOOL, HANDLE, P(MONITORINFO))
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.GetWindowLongPtr, self.SetWindowLongPtr = u.GetWindowLongPtrW, u.SetWindowLongPtrW
        else:  # pragma: no cover - 32-bit Python
            self.GetWindowLongPtr, self.SetWindowLongPtr = u.GetWindowLongW, u.SetWindowLongW
        proto(self.GetWindowLongPtr, ctypes.c_ssize_t, wt.HWND, ctypes.c_int)
        proto(self.SetWindowLongPtr, ctypes.c_ssize_t, wt.HWND, ctypes.c_int, ctypes.c_ssize_t)
        # display affinity (capture exclusion): may be missing on very old systems
        self.SetWindowDisplayAffinity = getattr(u, "SetWindowDisplayAffinity", None)
        if self.SetWindowDisplayAffinity is not None:
            proto(self.SetWindowDisplayAffinity, wt.BOOL, wt.HWND, wt.DWORD)
        self.GetWindowDisplayAffinity = getattr(u, "GetWindowDisplayAffinity", None)
        if self.GetWindowDisplayAffinity is not None:
            proto(self.GetWindowDisplayAffinity, wt.BOOL, wt.HWND, P(wt.DWORD))
        self.SetThreadDpiAwarenessContext = getattr(u, "SetThreadDpiAwarenessContext", None)
        if self.SetThreadDpiAwarenessContext is not None:
            proto(self.SetThreadDpiAwarenessContext, HANDLE, HANDLE)
        proto(g.CreateCompatibleDC, wt.HDC, wt.HDC)
        proto(g.DeleteDC, wt.BOOL, wt.HDC)
        proto(g.CreateDIBSection, wt.HBITMAP, wt.HDC, P(BITMAPINFO), wt.UINT, P(ctypes.c_void_p),
              wt.HANDLE, wt.DWORD)
        proto(g.SelectObject, wt.HGDIOBJ, wt.HDC, wt.HGDIOBJ)
        proto(g.DeleteObject, wt.BOOL, wt.HGDIOBJ)

        self.hinstance = k.GetModuleHandleW(None)
        self._wndproc = self.WNDPROC(_wndproc)   # keep a reference: called by Windows
        self.class_atom = 0

    def register_class(self) -> None:
        if self.class_atom:
            return
        wc = self.WNDCLASSEXW()
        wc.cbSize = self.ctypes.sizeof(self.WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = self.hinstance
        wc.hCursor = self.user32.LoadCursorW(None, self.ctypes.c_void_p(IDC_SIZEALL))
        wc.lpszClassName = CLASS_NAME
        atom = self.user32.RegisterClassExW(self.ctypes.byref(wc))
        if not atom:
            err = self.ctypes.get_last_error()
            if err != 1410:  # ERROR_CLASS_ALREADY_EXISTS
                raise OSError(f"RegisterClassExW failed ({err})")
            atom = 1
        self.class_atom = atom

    def last_error(self) -> int:
        return int(self.ctypes.get_last_error())


def _get_api() -> _Api:
    """The shared ctypes API (Windows only); raises OSError elsewhere or on failure."""
    global _api, _api_error
    with _api_lock:
        if _api is not None:
            return _api
        if sys.platform != "win32":
            raise OSError("overlay windows are only available on Windows")
        if _api_error is not None:
            raise OSError(_api_error)
        try:
            _api = _Api()
        except Exception as exc:
            _api_error = f"Win32 API unavailable: {exc}"
            raise OSError(_api_error) from exc
        return _api


def _wndproc(hwnd: Any, msg: int, wparam: int, lparam: int) -> int:
    """Window procedure shared by every overlay window. Never raises."""
    api = _api
    try:
        win = _windows_by_hwnd.get(int(hwnd or 0))
        if msg == WM_NCHITTEST:
            if win is not None and not win.click_through:
                return HTCAPTION
            return HTTRANSPARENT
        if msg == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE
        if msg == WM_EXITSIZEMOVE and win is not None:
            win._on_moved()
        if msg == WM_CLOSE:     # Alt+F4 must not destroy an overlay window
            return 0
    except Exception:  # pragma: no cover - never propagate into Windows
        log.debug("overlay wndproc error", exc_info=True)
    if api is None:  # pragma: no cover
        return 0
    return int(api.user32.DefWindowProcW(hwnd, msg, wparam, lparam))


class LayeredWindow:
    """One layered popup window. Every method must be called from the thread that created it.

    Raises ``OSError`` from the constructor when the window cannot be created (the manager then
    disables the overlay); the other methods never raise (failures are logged).
    """

    def __init__(self, name: str, click_through: bool = True,
                 on_moved: Callable[[str, int, int], None] | None = None) -> None:
        self.name = str(name)
        self.click_through = bool(click_through)
        self.on_moved = on_moved
        self.visible = False
        self.x = self.y = 0
        self.w = self.h = 0
        self.failed = False
        self._api = _get_api()
        self._hbmp: Any = None
        self._memdc: Any = None
        self._old_obj: Any = None
        self._bits: Any = None
        self._dib_size = (0, 0)
        api = self._api
        api.register_class()
        ex = WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST
        if self.click_through:
            ex |= WS_EX_TRANSPARENT
        hwnd = api.user32.CreateWindowExW(ex, CLASS_NAME, f"TreeAI Coach — {self.name}", WS_POPUP,
                                          0, 0, 1, 1, None, None, api.hinstance, None)
        if not hwnd:
            raise OSError(f"CreateWindowExW failed ({api.last_error()})")
        self.hwnd = hwnd
        _windows_by_hwnd[int(hwnd)] = self

    # ------------------------------------------------------------------ capture exclusion
    def exclude_from_capture(self) -> bool:
        """Hide this window from screen captures (``WDA_EXCLUDEFROMCAPTURE``). True on success.

        The window stays visible on the monitor but BitBlt / DXGI / Graphics Capture (hence our
        own minimap capture) do not see it. Requires Windows 10 2004+; on older systems the flag
        may be refused or degrade to ``WDA_MONITOR`` (window captured as a black box): the
        affinity is then reset to ``WDA_NONE`` and False is returned. Never raises.
        """
        try:
            api = self._api
            if api.SetWindowDisplayAffinity is None or windows_build() < EXCLUDE_MIN_BUILD:
                return False
            ok = bool(api.SetWindowDisplayAffinity(self.hwnd, WDA_EXCLUDEFROMCAPTURE))
            if ok and api.GetWindowDisplayAffinity is not None:
                cur = api.wt.DWORD(0)
                if api.GetWindowDisplayAffinity(self.hwnd, api.ctypes.byref(cur)):
                    ok = int(cur.value) == WDA_EXCLUDEFROMCAPTURE
            if not ok:
                log.info("Overlay %s: capture exclusion unavailable (error %s)", self.name, api.last_error())
                api.SetWindowDisplayAffinity(self.hwnd, WDA_NONE)
            return ok
        except Exception:
            log.debug("SetWindowDisplayAffinity failed", exc_info=True)
            return False

    def include_in_capture(self) -> None:
        """Undo :meth:`exclude_from_capture` (``WDA_NONE``). Never raises."""
        try:
            api = self._api
            if api.SetWindowDisplayAffinity is not None:
                api.SetWindowDisplayAffinity(self.hwnd, WDA_NONE)
        except Exception:
            log.debug("SetWindowDisplayAffinity(WDA_NONE) failed", exc_info=True)

    # ------------------------------------------------------------------ drawing
    def _ensure_dib(self, w: int, h: int) -> bool:
        if self._dib_size == (w, h) and self._bits:
            return True
        self._free_dib()
        api = self._api
        ctypes = api.ctypes
        screen_dc = api.user32.GetDC(None)
        if not screen_dc:
            return False
        try:
            memdc = api.gdi32.CreateCompatibleDC(screen_dc)
            if not memdc:
                return False
            bmi = api.BITMAPINFO()
            hdr = bmi.bmiHeader
            hdr.biSize = ctypes.sizeof(api.BITMAPINFOHEADER)
            hdr.biWidth = w
            hdr.biHeight = -h          # top-down
            hdr.biPlanes = 1
            hdr.biBitCount = 32
            hdr.biCompression = BI_RGB
            bits = ctypes.c_void_p()
            hbmp = api.gdi32.CreateDIBSection(screen_dc, ctypes.byref(bmi), DIB_RGB_COLORS,
                                              ctypes.byref(bits), None, 0)
            if not hbmp or not bits.value:
                api.gdi32.DeleteDC(memdc)
                return False
            self._old_obj = api.gdi32.SelectObject(memdc, hbmp)
            self._memdc, self._hbmp, self._bits = memdc, hbmp, bits
            self._dib_size = (w, h)
            return True
        finally:
            api.user32.ReleaseDC(None, screen_dc)

    def _free_dib(self) -> None:
        api = self._api
        try:
            if self._memdc:
                if self._old_obj:
                    api.gdi32.SelectObject(self._memdc, self._old_obj)
                api.gdi32.DeleteDC(self._memdc)
            if self._hbmp:
                api.gdi32.DeleteObject(self._hbmp)
        except Exception:  # pragma: no cover
            log.debug("DIB cleanup failed", exc_info=True)
        self._memdc = self._hbmp = self._old_obj = self._bits = None
        self._dib_size = (0, 0)

    def update(self, bgra_premul: np.ndarray, x: int, y: int) -> None:
        """Show ``bgra_premul`` (premultiplied BGRA uint8 ``h x w x 4``) at screen ``(x, y)``."""
        if self.failed:
            return
        try:
            img = np.ascontiguousarray(bgra_premul, dtype=np.uint8)
            if img.ndim != 3 or img.shape[2] != 4 or img.shape[0] < 1 or img.shape[1] < 1:
                return
            h, w = int(img.shape[0]), int(img.shape[1])
            api = self._api
            ctypes, wt = api.ctypes, api.wt
            if not self._ensure_dib(w, h):
                raise OSError(f"CreateDIBSection failed ({api.last_error()})")
            ctypes.memmove(self._bits, img.ctypes.data, w * h * 4)
            screen_dc = api.user32.GetDC(None)
            try:
                pt_dst = wt.POINT(int(x), int(y))
                size = wt.SIZE(w, h)
                pt_src = wt.POINT(0, 0)
                blend = api.BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
                ok = api.user32.UpdateLayeredWindow(self.hwnd, screen_dc, ctypes.byref(pt_dst),
                                                    ctypes.byref(size), self._memdc, ctypes.byref(pt_src),
                                                    0, ctypes.byref(blend), ULW_ALPHA)
            finally:
                api.user32.ReleaseDC(None, screen_dc)
            if not ok:
                raise OSError(f"UpdateLayeredWindow failed ({api.last_error()})")
            self.x, self.y, self.w, self.h = int(x), int(y), w, h
            if not self.visible:
                self.show()
        except Exception:
            self.failed = True
            log.exception("Overlay window %s: update failed", self.name)

    # ------------------------------------------------------------------ state
    def show(self) -> None:
        """Show without activating (keeps the game focused)."""
        try:
            self._api.user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
            self.visible = True
            self.keep_topmost()
        except Exception:  # pragma: no cover
            log.debug("show failed", exc_info=True)

    def hide(self) -> None:
        """Hide the window (no-op when already hidden)."""
        if not self.visible:
            return
        try:
            self._api.user32.ShowWindow(self.hwnd, SW_HIDE)
        except Exception:  # pragma: no cover
            log.debug("hide failed", exc_info=True)
        self.visible = False

    def keep_topmost(self) -> None:
        """Re-assert ``HWND_TOPMOST`` (some games push themselves on top)."""
        try:
            api = self._api
            api.user32.SetWindowPos(self.hwnd, api.ctypes.c_void_p(HWND_TOPMOST), 0, 0, 0, 0,
                                    SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE)
        except Exception:  # pragma: no cover
            log.debug("SetWindowPos failed", exc_info=True)

    def set_click_through(self, on: bool) -> None:
        """Toggle ``WS_EX_TRANSPARENT`` (off = the window can be dragged in move mode)."""
        on = bool(on)
        try:
            api = self._api
            ex = int(api.GetWindowLongPtr(self.hwnd, GWL_EXSTYLE))
            ex = (ex | WS_EX_TRANSPARENT) if on else (ex & ~WS_EX_TRANSPARENT)
            api.SetWindowLongPtr(self.hwnd, GWL_EXSTYLE, ex)
            api.user32.SetWindowPos(self.hwnd, api.ctypes.c_void_p(HWND_TOPMOST), 0, 0, 0, 0,
                                    SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE | SWP_FRAMECHANGED)
            self.click_through = on
        except Exception:
            log.exception("set_click_through failed")

    def window_rect(self) -> RectT | None:
        """Current ``(x, y, w, h)`` of the window on screen, or None."""
        try:
            api = self._api
            r = api.wt.RECT()
            if not api.user32.GetWindowRect(self.hwnd, api.ctypes.byref(r)):
                return None
            return int(r.left), int(r.top), int(r.right - r.left), int(r.bottom - r.top)
        except Exception:  # pragma: no cover
            return None

    def _on_moved(self) -> None:
        r = self.window_rect()
        if r is None:
            return
        self.x, self.y = r[0], r[1]
        cb = self.on_moved
        if cb is not None:
            try:
                cb(self.name, r[0], r[1])
            except Exception:
                log.exception("overlay on_moved callback failed")

    def destroy(self) -> None:
        """Destroy the window and free its bitmap. Idempotent."""
        hwnd = getattr(self, "hwnd", None)
        if not hwnd:
            return
        try:
            _windows_by_hwnd.pop(int(hwnd), None)
            self._free_dib()
            self._api.user32.DestroyWindow(hwnd)
        except Exception:  # pragma: no cover
            log.debug("destroy failed", exc_info=True)
        self.hwnd = None
        self.visible = False


def windows_build() -> int:
    """Windows build number (e.g. 19045), 0 off Windows / unknown."""
    try:
        gv = getattr(sys, "getwindowsversion", None)
        return int(gv().build) if gv is not None else 0
    except Exception:
        return 0


def pump_messages(max_messages: int = 200) -> int:
    """Dispatch pending messages of the calling thread (non-blocking). Returns the count."""
    api = _api
    if api is None:
        return 0
    msg = api.wt.MSG()
    n = 0
    while n < max_messages and api.user32.PeekMessageW(api.ctypes.byref(msg), None, 0, 0, PM_REMOVE):
        api.user32.TranslateMessage(api.ctypes.byref(msg))
        api.user32.DispatchMessageW(api.ctypes.byref(msg))
        n += 1
    return n


def _monitor_rect_at(api: _Api, x: int, y: int) -> RectT | None:
    try:
        hmon = api.user32.MonitorFromPoint(api.wt.POINT(int(x), int(y)), MONITOR_DEFAULTTONEAREST)
        if not hmon:
            return None
        mi = api.MONITORINFO()
        mi.cbSize = api.ctypes.sizeof(api.MONITORINFO)
        if not api.user32.GetMonitorInfoW(hmon, api.ctypes.byref(mi)):
            return None
        r = mi.rcMonitor
        return as_rect((r.left, r.top, r.right - r.left, r.bottom - r.top))
    except Exception:  # pragma: no cover
        return None


# ======================================================================================
# Manager
# ======================================================================================
class OverlayManager:
    """Owns the overlay thread and its windows (flash, radar, minimap marks, HUD).

    The window drawn over the minimap is excluded from screen capture only when
    ``cfg.overlay_hide_from_capture`` is True (:meth:`LayeredWindow.exclude_from_capture`);
    the "minimap" mode works either way (:attr:`effective_mode` = the configured mode).

    ``state_provider`` is called from the overlay thread (~12 Hz) and must be cheap and
    thread-safe (e.g. ``CoachEngine.get_overlay_state``). ``on_moved(name, x, y)`` ("radar" |
    "hud") is called from the overlay thread after the user dragged a window in move mode -
    marshal it to the UI thread (``root.after``) before touching widgets.
    """

    def __init__(self, cfg: Any, state_provider: Callable[[], Any],
                 on_moved: Callable[[str, int, int], None] | None = None) -> None:
        self._lock = threading.Lock()
        self._cfg = copy.copy(cfg)
        self._provider = state_provider
        self._on_moved_cb = on_moved
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._move_mode = False
        self._visible = True
        self._custom: dict[str, tuple[int, int]] = {}
        self._failed_logged = False
        self._demo_state: Any = None
        self.capture_excluded = False
        #: minimap-layer diagnostics (overlay thread): updates / frames drawn, rect, session logs
        self.mm_stats: dict[str, Any] = {"updates": 0, "rect": None, "no_rect": 0}
        self._mm_session = False
        self._mm_no_rect_logged = False
        self.ok = sys.platform == "win32"
        if not self.ok:
            log.info("Overlay disabled: Windows only")

    # ------------------------------------------------------------------ public API
    def start(self) -> None:
        """Start the overlay thread (no-op when unsupported or already running)."""
        if not self.ok:
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="overlay", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the thread and destroy the windows. Idempotent, never raises."""
        self._stop.set()
        th = self._thread
        if th is not None and th.is_alive() and th is not threading.current_thread():
            th.join(timeout)
        self._thread = None

    def is_running(self) -> bool:
        th = self._thread
        return bool(th is not None and th.is_alive())

    def apply_config(self, cfg: Any) -> None:
        """Use new settings from the next refresh (positions, scale, enabled parts...)."""
        with self._lock:
            self._cfg = copy.copy(cfg)
            self._custom.clear()

    def set_on_moved(self, callback: Callable[[str, int, int], None] | None) -> None:
        """Set / replace the ``on_moved(name, x, y)`` callback (called from the overlay thread)."""
        self._on_moved_cb = callback

    def set_move_mode(self, on: bool) -> None:
        """Move mode: windows become draggable (and shown with sample content out of game)."""
        with self._lock:
            self._move_mode = bool(on)

    @property
    def move_mode(self) -> bool:
        return self._move_mode

    def set_visible(self, on: bool) -> None:
        """Temporarily show / hide every overlay window (F11), independently of the config."""
        with self._lock:
            self._visible = bool(on)

    def toggle_visible(self) -> bool:
        """Flip :meth:`set_visible`; returns the new visibility."""
        with self._lock:
            self._visible = not self._visible
            return self._visible

    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def effective_mode(self) -> str:
        """Map mode really used ("minimap" | "radar" | "off"), see :func:`resolve_overlay_mode`."""
        with self._lock:
            cfg = self._cfg
        return resolve_overlay_mode(getattr(cfg, "overlay_mode", "minimap"), self.capture_excluded)

    # ------------------------------------------------------------------ thread
    def _fail(self, what: str) -> None:
        self.ok = False
        if not self._failed_logged:
            self._failed_logged = True
            log.exception("Overlay disabled (%s)", what)

    def _run(self) -> None:
        windows: dict[str, LayeredWindow] = {}
        try:
            api = _get_api()
            if api.SetThreadDpiAwarenessContext is not None:
                try:   # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
                    api.SetThreadDpiAwarenessContext(api.ctypes.c_void_p(-4))
                except Exception:  # pragma: no cover
                    pass
            # creation order = z-order among topmost windows: flash below radar / minimap / HUD
            for name in ("flash", "radar", "minimap", "hud"):
                windows[name] = LayeredWindow(name, click_through=True, on_moved=self._window_moved)
            # The minimap layer is captured by default (visible in the user's screenshots):
            # it never draws portraits, so the detector does not re-detect it. Optional
            # exclusion: cfg.overlay_hide_from_capture (applied in _sync_capture_exclusion).
            with self._lock:
                cfg0 = self._cfg
            self._sync_capture_exclusion(windows["minimap"], cfg0, force_log=True)
        except Exception:
            self._fail("window creation")
            for w in windows.values():
                w.destroy()
            return
        self._loop(api, windows)
        for w in windows.values():
            w.destroy()
        try:
            pump_messages()
        except Exception:  # pragma: no cover
            pass

    def _sync_capture_exclusion(self, mm_win: "LayeredWindow", cfg: Any, force_log: bool = False) -> None:
        """Apply ``cfg.overlay_hide_from_capture`` to the minimap window (only calls
        SetWindowDisplayAffinity when exclusion is wanted, or to undo a previous one)."""
        want = bool(getattr(cfg, "overlay_hide_from_capture", False))
        if want and not self.capture_excluded:
            self.capture_excluded = mm_win.exclude_from_capture()
            log.info("Overlay: minimap layer hidden from capture: %s",
                     "OK" if self.capture_excluded else "indisponible")
        elif not want and self.capture_excluded:
            mm_win.include_in_capture()
            self.capture_excluded = False
            log.info("Overlay: minimap layer visible in captures again")
        elif force_log:
            log.info("Overlay: mode %s, minimap layer %s", self.effective_mode,
                     "hidden from capture" if self.capture_excluded else "visible in captures")

    def _window_moved(self, name: str, x: int, y: int) -> None:
        if name not in ("radar", "hud"):
            return
        with self._lock:
            self._custom[name] = (int(x), int(y))
        cb = self._on_moved_cb
        if cb is not None:
            try:
                cb(name, int(x), int(y))
            except Exception:
                log.exception("on_moved callback failed")

    def _loop(self, api: _Api, windows: dict[str, LayeredWindow]) -> None:
        period = 1.0 / REFRESH_HZ
        last_top = 0.0
        last_draw = 0.0
        flash_key: Any = None
        click_through = True
        errors = 0
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                pump_messages()
                with self._lock:
                    cfg, move, visible = self._cfg, self._move_mode, self._visible
                    custom = dict(self._custom)
                self._sync_capture_exclusion(windows["minimap"], cfg)
                if click_through == move:     # move mode changed
                    for name in ("radar", "hud"):
                        windows[name].set_click_through(not move)
                    click_through = not move
                state = self._state(move)
                enabled = bool(getattr(cfg, "overlay_enabled", True)) and (visible or move)
                if state is None or not enabled:
                    for w in windows.values():
                        w.hide()
                    flash_key = None
                    if state is None:
                        self._end_mm_session()
                else:
                    fast = move or needs_fast_refresh(state) or not any(w.visible for w in windows.values())
                    if fast or t0 - last_draw >= 1.0 / CALM_HZ - 1e-3:
                        last_draw = t0
                        flash_key = self._refresh(api, windows, state, cfg, move, custom, flash_key)
                    if t0 - last_top >= TOPMOST_EVERY_S:
                        last_top = t0
                        for w in windows.values():
                            if w.visible:
                                w.keep_topmost()
                if any(w.failed for w in windows.values()):
                    raise OSError("a layered window stopped working")
                errors = 0
            except Exception:
                errors += 1
                if errors >= 5 or not all(w.hwnd for w in windows.values()):
                    self._fail("refresh loop")
                    return
                log.debug("overlay refresh error", exc_info=True)
            self._stop.wait(max(0.005, period - (time.monotonic() - t0)))

    def _state(self, move: bool) -> Any:
        try:
            state = self._provider() if self._provider is not None else None
        except Exception:
            log.debug("overlay state_provider failed", exc_info=True)
            state = None
        if state is None and move:
            if self._demo_state is None:
                try:
                    from treeaicoach.overlay_render import sample_states
                    self._demo_state = sample_states()["warning"]
                except Exception:
                    log.debug("no sample state", exc_info=True)
            state = self._demo_state
        return state

    def _screen_for(self, api: _Api, state: Any) -> tuple[RectT, RectT | None]:
        mm = as_rect(getattr(state, "minimap_rect", None))
        scr = as_rect(getattr(state, "screen_rect", None))
        mon = _monitor_rect_at(api, mm[0] + mm[2] // 2, mm[1] + mm[3] // 2) if mm is not None else None
        if scr is None and mon is None:
            scr = (0, 0, max(1, int(api.user32.GetSystemMetrics(SM_CXSCREEN))),
                   max(1, int(api.user32.GetSystemMetrics(SM_CYSCREEN))))
        return effective_screen(scr, mm, mon), mm

    def _note_mm_update(self, state: Any, mm: RectT, img: np.ndarray) -> None:
        """Count minimap-layer updates; log the rect once per game (and when it changes)."""
        st = self.mm_stats
        st["updates"] += 1
        if not self._mm_session or st["rect"] != mm:
            self._mm_session = True
            st["rect"] = mm
            try:
                lit = int(np.count_nonzero(img[..., 3]))
            except Exception:
                lit = -1
            n_tracks = sum(1 for e in list(getattr(state, "enemies", None) or [])
                           + list(getattr(state, "allies", None) or [])
                           if e is not None and getattr(e, "uv", None) is not None)
            log.info("Overlay minimap: layer at x=%d y=%d %dx%d px (physical), %d visible px, %d tracks, %s",
                     mm[0], mm[1], mm[2], mm[3], lit, n_tracks,
                     "hidden from capture" if self.capture_excluded else "visible in captures")

    def _end_mm_session(self) -> None:
        """End of game (state gone): log the update count once and reset the per-game logs."""
        if self._mm_session:
            log.info("Overlay minimap: end of game, %d updates on %s (no rect: %d)",
                     self.mm_stats["updates"], self.mm_stats["rect"], self.mm_stats["no_rect"])
        self._mm_session = False
        self._mm_no_rect_logged = False
        self.mm_stats.update(updates=0, rect=None, no_rect=0)

    def _refresh(self, api: _Api, windows: dict[str, LayeredWindow], state: Any, cfg: Any, move: bool,
                 custom: dict[str, tuple[int, int]], flash_key: Any) -> Any:
        from treeaicoach import overlay_render as orr

        scr, mm = self._screen_for(api, state)
        mode = resolve_overlay_mode(getattr(cfg, "overlay_mode", "minimap"), self.capture_excluded)
        # ---- marks drawn exactly over the real minimap (transparent window, same physical rect)
        mm_win = windows["minimap"]
        demo = move and state is self._demo_state
        if mode == "minimap" and mm is not None and not demo:
            img = orr.render_minimap(state, mm[2], mm[3],
                                     show_frame=bool(getattr(cfg, "overlay_show_frame", True)))
            mm_win.update(img, mm[0], mm[1])
            self._note_mm_update(state, mm, img)
        else:
            mm_win.hide()
            if mode == "minimap" and mm is None and not demo:
                self.mm_stats["no_rect"] += 1
                if not self._mm_no_rect_logged:
                    self._mm_no_rect_logged = True
                    log.info("Overlay minimap: minimap_rect inconnu (minimap pas encore localisée, "
                             "ou source de démo) - rien n'est dessiné sur la minimap")
        # ---- radar (needs the minimap position)
        radar_rect: RectT | None = None
        radar_win = windows["radar"]
        if mode == "radar" and getattr(cfg, "radar_enabled", True) and mm is not None:
            pos = getattr(cfg, "radar_position", "above_minimap")
            xy = getattr(cfg, "radar_xy", None)
            if "radar" in custom:
                pos, xy = "custom", custom["radar"]
            x, y, size = radar_geometry(mm, scr, getattr(cfg, "radar_scale", 1.0), pos, xy)
            img = orr.render_radar(state, size, None)
            if move:
                img = move_mode_frame(img, "Radar — glisser")
            radar_win.update(img, x, y)
            radar_rect = (x, y, size, size)
        else:
            radar_win.hide()
        # ---- HUD
        hud_win = windows["hud"]
        if getattr(cfg, "hud_enabled", True):
            img = orr.render_hud(state, hud_width(scr))
            if move:
                img = move_mode_frame(img, "HUD — glisser")
            pos = getattr(cfg, "hud_position", "above_minimap")
            xy = getattr(cfg, "hud_xy", None)
            if "hud" in custom:
                pos, xy = "custom", custom["hud"]
            avoid = [r for r in (mm, radar_rect) if r is not None]
            x, y = hud_placement(scr, img.shape[1], img.shape[0], pos, xy, avoid=avoid,
                                 anchor=radar_rect or mm)
            hud_win.update(img, x, y)
        else:
            hud_win.hide()
        # ---- danger flash (re-rendered only when intensity / geometry change)
        flash_win = windows["flash"]
        intensity = quantize_flash(getattr(state, "flash", 0.0)) if getattr(cfg, "danger_flash", True) else 0.0
        if intensity <= 0 or move:
            flash_win.hide()
            return None
        rel = (mm[0] - scr[0], mm[1] - scr[1], mm[2], mm[3]) if mm is not None else None
        key = (intensity, scr, rel)
        if key != flash_key or not flash_win.visible:
            img = orr.render_flash(scr[2], scr[3], intensity, rel, thickness=flash_thickness(scr))
            flash_win.update(img, scr[0], scr[1])
        return key
