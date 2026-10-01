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
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

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

    name = "mss"
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


# ======================================================================================
# Game window details (foreground / minimized / DPI) - pause & overlay hiding
# ======================================================================================


@dataclass(frozen=True)
class GameWindowInfo:
    """State of the game window. ``rect`` is None when minimized / hidden / too small."""

    hwnd: int
    rect: Rect | None
    minimized: bool
    foreground: bool           # the game (or one of its popups) has the keyboard focus
    own_foreground: bool       # one of OUR windows is in the foreground (settings, preview...)
    dpi: int                   # 96 = 100 %, 120 = 125 %, 144 = 150 %


def game_window_info() -> GameWindowInfo | None:
    """:class:`GameWindowInfo` of the League client window; None when absent / off Windows.

    Never raises. Cheap (a handful of user32 calls): fine once or twice per second.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        import os
        from ctypes import wintypes

        set_dpi_awareness()
        user32 = _user32()
        hwnd = user32.FindWindowW(GAME_WINDOW_CLASS, None) or user32.FindWindowW(None, GAME_WINDOW_TITLE)
        if not hwnd:
            return None
        minimized = bool(user32.IsIconic(hwnd)) or not bool(user32.IsWindowVisible(hwnd))
        user32.GetForegroundWindow.restype = wintypes.HWND
        fg = user32.GetForegroundWindow()
        foreground = own = False
        if fg:
            if int(fg) == int(hwnd):
                foreground = True
            else:
                try:
                    user32.GetAncestor.restype = wintypes.HWND
                    user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
                    foreground = int(user32.GetAncestor(fg, 3) or 0) == int(hwnd)   # GA_ROOTOWNER
                except Exception:
                    pass
                pid = wintypes.DWORD(0)
                user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
                own = int(pid.value) == os.getpid()
        dpi = 96
        try:
            dpi = int(user32.GetDpiForWindow(hwnd) or 96)
        except Exception:
            pass
        rect = None if minimized else find_game_window()
        return GameWindowInfo(int(hwnd), rect, minimized, foreground, own, dpi)
    except Exception as exc:
        log.debug("game_window_info failed: %s", exc)
        return None


# ======================================================================================
# Frame health (black / frozen captures) + backend selection with automatic fallback
# ======================================================================================

#: Consecutive black frames before the backend is questioned.
BLACK_FRAMES = 3
#: A minimap frozen this long (s) AND this many frames is suspicious (only checked by the
#: engine once the game clock is past :data:`STALE_MIN_GAME_S`: before minions spawn the
#: minimap can legitimately stay still).
STALE_S = 4.0
STALE_FRAMES = 12
STALE_MIN_GAME_S = 90.0
#: Two captures of the same rect differing less than this (mean abs, 8-bit) are "the same".
SAME_FRAME_DIFF = 1.5
#: First DXGI image cross-checked against mss: more different than this -> DXGI disabled.
CROSSCHECK_MAX_DIFF = 40.0
#: Consecutive failed grabs before the backend is abandoned for the next one.
FAIL_SWITCH = 5


def frame_fingerprint(img: Any) -> int:
    """Cheap content hash of an image (subsampled CRC32); -1 for invalid input."""
    try:
        import zlib

        a = np.asarray(img)
        if a.ndim < 2 or a.size == 0:
            return -1
        step = max(1, int(min(a.shape[0], a.shape[1]) // 64))
        return zlib.crc32(np.ascontiguousarray(a[::step, ::step]).tobytes())
    except Exception:
        return -1


def mean_abs_diff(a: Any, b: Any) -> float:
    """Mean absolute difference of two same-sized images on a <= 64 px grid (inf if unusable)."""
    try:
        x, y = np.asarray(a), np.asarray(b)
        if x.shape != y.shape or x.size == 0:
            return float("inf")
        step = max(1, int(min(x.shape[0], x.shape[1]) // 64))
        xs = x[::step, ::step].astype(np.int16)
        ys = y[::step, ::step].astype(np.int16)
        return float(np.abs(xs - ys).mean())
    except Exception:
        return float("inf")


class FrameHealth:
    """Black / frozen frame detector for one capture stream (pure, testable)."""

    def __init__(self, black_frames: int = BLACK_FRAMES, stale_s: float = STALE_S,
                 stale_frames: int = STALE_FRAMES) -> None:
        self.black_frames = int(black_frames)
        self.stale_s = float(stale_s)
        self.stale_frames = int(stale_frames)
        self.reset()

    def reset(self) -> None:
        self.black_run = 0
        self.same_run = 0
        self.same_since: float | None = None
        self._fp: int | None = None

    def observe(self, img: Any, t: float) -> str:
        """``"ok"`` | ``"black"`` (``black_frames`` black frames in a row) | ``"stale"``
        (identical content for ``stale_frames`` frames and ``stale_s`` seconds)."""
        if is_black_frame(img):
            self.black_run += 1
            self.same_run, self.same_since, self._fp = 0, None, None
            return "black" if self.black_run >= self.black_frames else "ok"
        self.black_run = 0
        fp = frame_fingerprint(img)
        if fp != -1 and fp == self._fp:
            self.same_run += 1
            if self.same_since is None:
                self.same_since = float(t)
            if self.same_run >= self.stale_frames and float(t) - self.same_since >= self.stale_s:
                return "stale"
        else:
            self.same_run, self.same_since = 0, None
        self._fp = fp
        return "ok"


def _default_backend_factories() -> dict[str, Callable[[], Any]]:
    def dxgi() -> Any:
        from treeaicoach.dxgi_capture import DxgiCapture, available

        if not available():
            raise OSError("Desktop Duplication unavailable")
        return DxgiCapture()

    return {"mss": ScreenCapture, "dxgi": dxgi}


class SmartCapture:
    """Drop-in replacement of :class:`ScreenCapture` choosing the capture backend.

    ``backend``: ``"auto"`` (DXGI Desktop Duplication first on Windows 8+, ``mss`` otherwise
    and as the fallback), ``"dxgi"`` or ``"mss"`` (preferred, the other one still used if it
    fails). The first DXGI image is cross-checked against ``mss`` (DXGI disabled for the session
    when they disagree). A grab the current backend cannot do (rectangle across two monitors...)
    is served by the next one. :meth:`check` (called by the engine on every minimap frame)
    switches backend on black / frozen frames when the other backend sees a live image.

    One instance per thread, like :class:`ScreenCapture`. Never raises.
    """

    def __init__(self, backend: str = "auto", factories: dict[str, Callable[[], Any]] | None = None,
                 clock: Callable[[], float] = time.monotonic, platform: str | None = None) -> None:
        self._factories = factories if factories is not None else _default_backend_factories()
        self._clock = clock
        plat = platform or sys.platform
        pref = str(backend or "auto").lower()
        if pref == "mss":
            order = ["mss", "dxgi"]
        else:   # "auto" / "dxgi": duplication first where it exists
            order = ["dxgi", "mss"] if plat == "win32" else ["mss"]
        self.order = [n for n in order if n in self._factories]
        self._impl: dict[str, Any] = {}
        self._disabled: dict[str, str] = {}
        self._fails = 0
        self._crosschecked: set[str] = set()
        self.health = FrameHealth()
        self.errors = 0
        self._ms: deque[float] = deque(maxlen=120)
        self._grab_times: deque[float] = deque(maxlen=120)
        self.stats: dict[str, Any] = {"backend": None, "switches": 0, "last_switch": None,
                                      "black_events": 0, "stale_events": 0, "fallback_grabs": 0,
                                      "disabled": {}}
        self.current = self.order[0] if self.order else "mss"

    # ------------------------------------------------------------------ backends
    @property
    def name(self) -> str:
        return self.current

    def _get(self, name: str) -> Any:
        if name in self._disabled:
            return None
        impl = self._impl.get(name)
        if impl is None:
            try:
                impl = self._impl[name] = self._factories[name]()
            except Exception as exc:
                self.disable(name, f"init: {exc}")
                return None
        return impl

    def disable(self, name: str, reason: str) -> None:
        """Stop using backend ``name`` for this session (logged once)."""
        if name in self._disabled:
            return
        self._disabled[name] = str(reason)[:200]
        self.stats["disabled"] = dict(self._disabled)
        log.info("Capture backend %s disabled: %s", name, reason)
        impl = self._impl.pop(name, None)
        if impl is not None:
            try:
                impl.close()
            except Exception:
                pass
        if name == self.current:
            self._switch(f"{name} disabled")

    def _switch(self, reason: str, to: str | None = None) -> bool:
        cands = [n for n in self.order if n not in self._disabled and n != self.current]
        if to is not None:
            cands = [to] if to in cands else []
        if not cands:
            return False
        old, self.current = self.current, cands[0]
        self._fails = 0
        self.health.reset()
        self.stats["switches"] += 1
        self.stats["last_switch"] = f"{old} -> {self.current}: {reason}"
        log.warning("Capture: %s -> %s (%s)", old, self.current, reason)
        return True

    def other(self) -> str | None:
        """Name of the fallback backend (None if there is none)."""
        for n in self.order:
            if n != self.current and n not in self._disabled:
                return n
        return None

    # ------------------------------------------------------------------ grab
    def _grab_with(self, name: str, rect: Rect, pad: bool) -> np.ndarray | None:
        impl = self._get(name)
        if impl is None:
            return None
        try:
            if name == "mss":
                return impl.grab(rect, pad=pad)
            return impl.grab(rect)
        except Exception as exc:   # backends never raise, but be safe
            log.debug("%s grab raised: %s", name, exc)
            return None

    def grab(self, rect: Rect, pad: bool = True) -> np.ndarray | None:
        """BGR capture of ``rect`` (screen px) with the current backend, then the fallback."""
        t0 = time.perf_counter()
        name = self.current
        img = self._grab_with(name, rect, pad)
        if img is not None and name == "dxgi" and "dxgi" not in self._crosschecked:
            self._crosscheck(rect, img)
            if "dxgi" in self._disabled:
                img = None
        if img is None:
            self._fails += 1
            alt = self.other()
            if alt is not None:
                img = self._grab_with(alt, rect, pad)
                if img is not None:
                    self.stats["fallback_grabs"] += 1
                    if self._fails >= FAIL_SWITCH:
                        self._switch(f"{FAIL_SWITCH} failed grabs", to=alt)
        else:
            self._fails = 0
        self.errors = 0 if img is not None else self.errors + 1
        dt = (time.perf_counter() - t0) * 1000.0
        self._ms.append(dt)
        self._grab_times.append(self._clock())
        self.stats["backend"] = self.current
        return img

    def _crosscheck(self, rect: Rect, img: np.ndarray) -> None:
        self._crosschecked.add("dxgi")
        ref = self._grab_with("mss", rect, True)
        if ref is None or is_black_frame(ref) or ref.shape != img.shape:
            self.stats["crosscheck"] = "skipped"
            return
        d = mean_abs_diff(ref, img)
        self.stats["crosscheck"] = round(d, 2)
        if d > CROSSCHECK_MAX_DIFF:
            self.disable("dxgi", f"image differs from mss (diff {d:.1f})")

    def check(self, img: Any, t: float, rect: Rect | None = None, allow_stale: bool = True) -> str:
        """Frame health of a minimap image just grabbed: ``"ok"`` | ``"black"`` | ``"stale"``.

        On black / frozen frames the other backend grabs the same ``rect``: if it sees a live
        image (not black, different), the capture switches to it and ``"switched"`` is
        returned; if both agree, the status stands (truly black screen / static minimap).
        ``allow_stale=False`` ignores frozen frames (before minions spawn, game paused).
        """
        st = self.health.observe(img, t)
        if st == "stale" and not allow_stale:
            return "ok"
        if st == "ok":
            return "ok"
        self.stats["black_events" if st == "black" else "stale_events"] += 1
        alt = self.other()
        if alt is not None and rect is not None:
            other = self._grab_with(alt, rect, True)
            if other is not None and not is_black_frame(other) and \
                    (st == "black" or mean_abs_diff(other, img) > SAME_FRAME_DIFF):
                self._switch(f"{st} frames", to=alt)
                return "switched"
        self.health.reset()
        if st == "black":
            self.health.black_run = self.health.black_frames   # stays black until a live frame
        return st

    def timings(self) -> dict[str, Any]:
        """``{"backend", "grab_ms_p50", "grab_ms_p95", "fps"}`` + :attr:`stats` (diagnostics)."""
        ms = sorted(self._ms)
        out = dict(self.stats)
        out["backend"] = self.current
        if ms:
            out["grab_ms_p50"] = round(ms[len(ms) // 2], 2)
            out["grab_ms_p95"] = round(ms[min(len(ms) - 1, int(len(ms) * 0.95))], 2)
        ts = list(self._grab_times)
        if len(ts) >= 2 and ts[-1] > ts[0]:
            out["fps"] = round((len(ts) - 1) / (ts[-1] - ts[0]), 2)
        for name, impl in self._impl.items():
            st = getattr(impl, "stats", None)
            if isinstance(st, dict):
                out[f"{name}_stats"] = dict(st)
            err = getattr(impl, "last_error", None)
            if err:
                out[f"{name}_error"] = err
        return out

    def close(self) -> None:
        for impl in list(self._impl.values()):
            try:
                impl.close()
            except Exception:
                pass
        self._impl.clear()

    def __enter__(self) -> "SmartCapture":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# ======================================================================================
# Focus / occlusion (overlay hiding, frames discarded while another window covers the minimap)
# ======================================================================================

def _find_game_hwnd(user32: Any) -> int:
    hwnd = user32.FindWindowW(GAME_WINDOW_CLASS, None) or user32.FindWindowW(None, GAME_WINDOW_TITLE)
    return int(hwnd or 0)


def foreground_state() -> tuple[bool | None, bool]:
    """``(game_in_front, own_window_in_front)``: ``game_in_front`` is None when no game window
    exists (demo, overlay test). Cheap (3 user32 calls): fine at the overlay's frame rate.
    Never raises; off Windows ``(None, False)``."""
    if sys.platform != "win32":
        return None, False
    try:
        import ctypes
        import os
        from ctypes import wintypes

        user32 = _user32()
        game = _find_game_hwnd(user32)
        if not game:
            return None, False
        user32.GetForegroundWindow.restype = wintypes.HWND
        fg = int(user32.GetForegroundWindow() or 0)
        if not fg:
            return False, False
        if fg == game:
            return True, False
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
        if int(user32.GetAncestor(fg, 3) or 0) == game:          # GA_ROOTOWNER: a game popup
            return True, False
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(wintypes.HWND(fg), ctypes.byref(pid))
        return False, int(pid.value) == os.getpid()
    except Exception:
        return None, False


def occlusion_points(rect: Rect, inset: float = 0.12) -> list[tuple[int, int]]:
    """Sample points of ``rect`` checked for occlusion: 4 inset corners + the centre."""
    dx, dy = max(1, int(rect.w * inset)), max(1, int(rect.h * inset))
    return [(rect.x + dx, rect.y + dy), (rect.right - 1 - dx, rect.y + dy), (rect.x + dx, rect.bottom - 1 - dy),
            (rect.right - 1 - dx, rect.bottom - 1 - dy), (rect.x + rect.w // 2, rect.y + rect.h // 2)]


def rect_occluded(rect: Rect | None, window_at: Callable[[int, int], int | None] | None = None,
                  game_hwnd: int | None = None) -> bool | None:
    """True when another top-level window (League client, browser, our own UI...) covers a sample
    point of ``rect`` over the game window; None when unknown (off Windows / no game). Click-through
    layered windows (our overlay) are ignored by ``WindowFromPoint``. ``window_at(x, y)`` returns
    the root window at a point (tests). Never raises."""
    if rect is None:
        return None
    try:
        if window_at is None:
            if sys.platform != "win32":
                return None
            import ctypes
            from ctypes import wintypes

            user32 = _user32()
            game_hwnd = _find_game_hwnd(user32) if game_hwnd is None else game_hwnd
            user32.WindowFromPoint.restype = wintypes.HWND
            user32.WindowFromPoint.argtypes = [wintypes.POINT]
            user32.GetAncestor.restype = wintypes.HWND
            user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]

            def window_at(x: int, y: int) -> int | None:
                h = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
                return int(user32.GetAncestor(h, 2) or 0) if h else 0          # GA_ROOT
        if not game_hwnd:
            return None
        hits = [window_at(x, y) for x, y in occlusion_points(rect)]
        return any(h is not None and h != game_hwnd for h in hits)
    except Exception:
        return None
