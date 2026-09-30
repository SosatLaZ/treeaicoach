"""Screen capture (mss), League of Legends game window lookup and DPI awareness.

Everything here only reads pixels that are already on the player's screen (like OBS or
Discord do): no access to the game process, no hooks.

* :func:`set_dpi_awareness` makes the process per-monitor DPI aware (Windows) so that window
  and capture coordinates are *physical* pixels. Call it once at startup, before any window
  is created; it is idempotent and a no-op elsewhere.
* :func:`find_game_window` returns the client area of the game window in screen pixels
  (``ctypes`` / ``user32`` only, no pywin32 needed).
* :class:`ScreenCapture` grabs a rectangle of the virtual screen as a BGR image. One
  instance per thread (``mss`` is not thread-safe); the ``mss`` object is created lazily in
  the calling thread and re-created after an error.

None of the public functions raises.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

#: Window class and title of the in-game client (not the lobby "LeagueClientUx").
GAME_WINDOW_CLASS = "RiotWindowClass"
GAME_WINDOW_TITLE = "League of Legends (TM) Client"
#: Smallest client area accepted as the game window (px, both sides).
MIN_WINDOW_SIZE = 200
#: :func:`is_black_frame` thresholds (8-bit intensity).
BLACK_MEAN_MAX = 6.0
BLACK_STD_MAX = 4.0
#: Refuse absurd capture requests (pixels).
MAX_CAPTURE_PIXELS = 16384 * 16384

_E_ACCESSDENIED = -2147024891  # HRESULT 0x80070005: awareness already set (manifest / earlier call)


@dataclass(frozen=True)
class Rect:
    """Axis-aligned rectangle in physical screen pixels (``x``, ``y`` = top-left corner).

    Values are coerced to ``int`` so a Rect built from floats / numpy scalars is still
    hashable and JSON friendly.
    """

    x: int
    y: int
    w: int
    h: int

    def __post_init__(self) -> None:
        for name in ("x", "y", "w", "h"):
            val = getattr(self, name)
            if not isinstance(val, int) or isinstance(val, bool):
                object.__setattr__(self, name, int(round(float(val))))

    @property
    def right(self) -> int:
        """Exclusive right edge (``x + w``)."""
        return self.x + self.w

    @property
    def bottom(self) -> int:
        """Exclusive bottom edge (``y + h``)."""
        return self.y + self.h

    @property
    def area(self) -> int:
        """Area in pixels (0 when empty)."""
        return max(0, self.w) * max(0, self.h)

    def is_empty(self) -> bool:
        """True when the rectangle has no pixel."""
        return self.w <= 0 or self.h <= 0

    def intersect(self, other: "Rect") -> "Rect | None":
        """Intersection with ``other``; None when they do not overlap."""
        x0, y0 = max(self.x, other.x), max(self.y, other.y)
        x1, y1 = min(self.right, other.right), min(self.bottom, other.bottom)
        if x1 <= x0 or y1 <= y0:
            return None
        return Rect(x0, y0, x1 - x0, y1 - y0)

    def offset(self, dx: int, dy: int) -> "Rect":
        """Same rectangle moved by ``(dx, dy)``."""
        return Rect(self.x + dx, self.y + dy, self.w, self.h)

    def to_dict(self) -> dict[str, int]:
        """``{"x", "y", "w", "h"}`` (JSON friendly)."""
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


# ======================================================================================
# DPI awareness
# ======================================================================================

_dpi_lock = threading.Lock()
_dpi_done = False


def set_dpi_awareness() -> None:
    """Make the process per-monitor DPI aware (Windows); idempotent, no-op elsewhere.

    Tries ``shcore.SetProcessDpiAwareness(2)`` (Windows 8.1+), then falls back to
    ``user32.SetProcessDPIAware()`` (Vista+). "Already set" is not an error.
    """
    global _dpi_done
    if sys.platform != "win32":
        return
    with _dpi_lock:
        if _dpi_done:
            return
        _dpi_done = True
        try:
            import ctypes

            try:  # Windows 10 1703+: per-monitor v2 (most reliable with Tk + layered windows)
                if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                    log.debug("DPI awareness: per-monitor v2")
                    return
            except (AttributeError, OSError):
                pass
            try:
                hr = ctypes.windll.shcore.SetProcessDpiAwareness(2)
                if hr in (0, _E_ACCESSDENIED):
                    log.debug("DPI awareness: per-monitor (hr=%s)", hr)
                    return
                log.debug("SetProcessDpiAwareness(2) returned %s, trying fallback", hr)
            except (AttributeError, OSError) as exc:
                log.debug("shcore.SetProcessDpiAwareness unavailable: %s", exc)
            try:
                ctypes.windll.user32.SetProcessDPIAware()
                log.debug("DPI awareness: system aware (SetProcessDPIAware)")
            except (AttributeError, OSError) as exc:
                log.warning("Unable to set DPI awareness: %s", exc)
        except Exception:  # pragma: no cover - defensive
            log.exception("set_dpi_awareness failed")


# ======================================================================================
# Game window
# ======================================================================================


def _user32() -> Any:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.ClientToScreen.restype = wintypes.BOOL
    return user32


def find_game_window() -> Rect | None:
    """Client area of the game window in physical screen pixels.

    Looks the window up by class (``RiotWindowClass``) then by title. Returns None when
    the game is not running, minimized, hidden, smaller than :data:`MIN_WINDOW_SIZE`, or
    off Windows. Never raises.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        set_dpi_awareness()  # coordinates must be physical pixels (no-op if already done)
        user32 = _user32()
        hwnd = user32.FindWindowW(GAME_WINDOW_CLASS, None)
        if not hwnd:
            hwnd = user32.FindWindowW(None, GAME_WINDOW_TITLE)
        if not hwnd:
            return None
        if user32.IsIconic(hwnd) or not user32.IsWindowVisible(hwnd):
            return None
        rc = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rc)):
            return None
        w, h = int(rc.right - rc.left), int(rc.bottom - rc.top)
        if w < MIN_WINDOW_SIZE or h < MIN_WINDOW_SIZE:
            return None
        pt = wintypes.POINT(0, 0)
        if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
            return None
        return _to_physical(hwnd, Rect(int(pt.x), int(pt.y), w, h))
    except Exception as exc:
        log.debug("find_game_window failed: %s", exc)
        return None


# ======================================================================================
# Monitors / capture
# ======================================================================================


def _to_physical(hwnd: Any, rect: Rect) -> Rect:
    """Convert a window rect to physical pixels if this thread is not DPI aware.

    When DPI awareness could not be set (or was reset by a library), Windows reports
    scaled "logical" coordinates (e.g. 1600x900 on a 2000x1125 screen at 125 %), which
    would misplace every capture and overlay. Never raises.
    """
    try:
        import ctypes

        user32 = ctypes.windll.user32
        user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        ctx = user32.GetThreadDpiAwarenessContext()
        if user32.GetAwarenessFromDpiAwarenessContext(ctx) != 0:   # 0 = DPI unaware
            return rect
        dpi = int(user32.GetDpiForWindow(hwnd) or 96)
        if dpi <= 96:
            return rect
        k = dpi / 96.0
        return Rect(int(round(rect.x * k)), int(round(rect.y * k)),
                    int(round(rect.w * k)), int(round(rect.h * k)))
    except Exception:
        return rect


def _new_mss() -> Any:
    import mss  # lazy: keeps import of this module cheap and safe

    return mss.mss()


def _rect_from_monitor(mon: dict) -> Rect:
    return Rect(int(mon["left"]), int(mon["top"]), int(mon["width"]), int(mon["height"]))


def monitor_rects() -> list[Rect]:
    """Physical monitors (``mss`` order, primary first on Windows); [] on failure."""
    try:
        sct = _new_mss()
        try:
            return [_rect_from_monitor(m) for m in sct.monitors[1:]]
        finally:
            sct.close()
    except Exception as exc:
        log.warning("Cannot enumerate monitors: %s", exc)
        return []


def virtual_screen_rect() -> Rect | None:
    """Bounding box of all monitors (``mss`` monitor 0); None on failure."""
    try:
        sct = _new_mss()
        try:
            return _rect_from_monitor(sct.monitors[0])
        finally:
            sct.close()
    except Exception as exc:
        log.warning("Cannot read the virtual screen size: %s", exc)
        return None


class ScreenCapture:
    """Grabs screen rectangles as BGR images. Use one instance per thread.

    The ``mss`` object is created lazily by the first :meth:`grab` in the calling thread
    (re-created if another thread calls, or after any capture error).
    """

    #: Minimum delay between two logged capture errors (s).
    ERROR_LOG_INTERVAL = 30.0
    #: Min age (s) of the cached monitor layout before a clipped request re-reads it.
    LAYOUT_REFRESH_S = 5.0

    def __init__(self) -> None:
        self._sct: Any = None
        self._thread: int | None = None
        self._virtual: Rect | None = None
        self._created = 0.0
        self._last_error_log = -1e9
        self.errors = 0          # consecutive failures (diagnostics)

    def _ensure(self) -> Any:
        tid = threading.get_ident()
        if self._sct is not None and self._thread != tid:
            log.debug("ScreenCapture used from another thread: re-creating mss")
            self.close()
        if self._sct is None:
            self._sct = _new_mss()
            self._thread = tid
            self._created = time.monotonic()
            self._virtual = _rect_from_monitor(self._sct.monitors[0])
        return self._sct

    def _log_error(self, msg: str, *args: Any) -> None:
        now = time.monotonic()
        if now - self._last_error_log >= self.ERROR_LOG_INTERVAL:
            self._last_error_log = now
            log.warning(msg, *args)
        else:
            log.debug(msg, *args)

    def grab(self, rect: Rect, pad: bool = True) -> np.ndarray | None:
        """Capture ``rect`` (screen pixels) as a contiguous BGR ``uint8`` image.

        The rectangle is clipped to the virtual screen. With ``pad`` (default) the parts
        outside the screen are black so the image always has shape ``(rect.h, rect.w, 3)``
        and pixel ``(0, 0)`` stays at ``(rect.x, rect.y)``; without it, only the visible
        part is returned. None if the rectangle is invalid / off screen or the capture
        fails. Never raises.
        """
        try:
            want = Rect(rect.x, rect.y, rect.w, rect.h)
            if want.is_empty() or want.area > MAX_CAPTURE_PIXELS:
                return None
        except Exception:
            return None
        try:
            sct = self._ensure()
            vis = want.intersect(self._virtual) if self._virtual is not None else want
            if vis != want and time.monotonic() - self._created > self.LAYOUT_REFRESH_S:
                # the monitor layout may have changed (screen plugged in): refresh it
                self.close()
                sct = self._ensure()
                vis = want.intersect(self._virtual) if self._virtual is not None else want
            if vis is None:
                return None
            shot = sct.grab({"left": vis.x, "top": vis.y, "width": vis.w, "height": vis.h})
            h, w = int(shot.height), int(shot.width)
            raw = np.frombuffer(shot.raw, dtype=np.uint8)
            if raw.size < h * w * 4:
                raise ValueError(f"short capture buffer ({raw.size} < {h * w * 4})")
            bgra = raw[: h * w * 4].reshape(h, w, 4)
            self.errors = 0
            if not pad or vis == want:
                return np.ascontiguousarray(bgra[:, :, :3])
            out = np.zeros((want.h, want.w, 3), np.uint8)
            ox, oy = vis.x - want.x, vis.y - want.y
            hh, ww = min(h, want.h - oy), min(w, want.w - ox)
            out[oy:oy + hh, ox:ox + ww] = bgra[:hh, :ww, :3]
            return out
        except Exception as exc:
            self.errors += 1
            self._log_error("Screen capture failed (%s): %s", type(exc).__name__, exc)
            self.close()
            return None

    def close(self) -> None:
        """Release the ``mss`` resources (a later :meth:`grab` re-creates them)."""
        sct, self._sct = self._sct, None
        self._thread = None
        self._virtual = None
        if sct is not None:
            try:
                sct.close()
            except Exception as exc:  # pragma: no cover - defensive
                log.debug("mss close failed: %s", exc)

    def __enter__(self) -> "ScreenCapture":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - best effort
        try:
            if self._sct is not None and self._thread == threading.get_ident():
                self._sct.close()
        except Exception:
            pass


def is_black_frame(img: Any) -> bool:
    """True if the capture is (almost) uniformly black: mean < 6 and std < 4.

    Typical of exclusive full-screen mode (advise "Sans bordure"). Invalid input -> False.
    """
    try:
        a = np.asarray(img)
        if a.ndim not in (2, 3) or a.size == 0:
            return False
        step = max(1, int(round((a.shape[0] * a.shape[1] / 250_000.0) ** 0.5)))
        sub = a[::step, ::step]
        if sub.ndim == 3 and sub.shape[2] == 4:
            sub = sub[:, :, :3]
        sub = sub.astype(np.float32)
        return bool(float(sub.mean()) < BLACK_MEAN_MAX and float(sub.std()) < BLACK_STD_MAX)
    except Exception:
        return False
