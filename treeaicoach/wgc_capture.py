"""Windows.Graphics.Capture (WGC) *window* capture backend, pure ``ctypes`` - no extra dependency.

Why: ``mss`` (GDI) and DXGI Desktop Duplication read the composed desktop, so anything drawn
above the game (our overlay, Discord, a notification...) ends up in the minimap image. WGC with
a *window* target (``IGraphicsCaptureItemInterop::CreateForWindow`` on the ``RiotWindowClass``
HWND) hands out the game window's own content only, even when another window covers it.

Pipeline (raw WinRT COM through vtables, the same way :mod:`treeaicoach.dxgi_capture` drives
D3D11; the pywinrt packages are not needed, so the exe does not grow):

``D3D11CreateDevice(BGRA)`` -> ``IDXGIDevice`` -> ``CreateDirect3D11DeviceFromDXGIDevice`` ->
``Direct3D11CaptureFramePool.CreateFreeThreaded(B8G8R8A8, 2 buffers)`` (no dispatcher queue
needed) -> ``CreateCaptureSession(item)`` (cursor off, yellow border off) -> ``StartCapture``.
Each grab drains ``TryGetNextFrame`` (the newest frame is kept, the older ones closed), takes
its ``ID3D11Texture2D`` (``IDirect3DDxgiInterfaceAccess``) and ``CopySubresourceRegion``-s the
requested box (screen px -> window px through ``DWMWA_EXTENDED_FRAME_BOUNDS``) into a small
staging texture that is mapped and read. The newest frame stays held, so several rectangles of
the same frame (minimap, HUD patches) are served without waiting.

Requirements: Windows 10 1903+ (build 18362). On Windows 10 the border cannot be removed
(``IsBorderRequired`` exists from Windows 11): a yellow frame around the game would worry the
user, so the backend refuses to start there unless :data:`ALLOW_BORDER` (the caller then uses
DXGI / mss as before). Never raises: failures return None, ``dead`` marks a permanent one.
Only reads pixels of the game window like OBS' "window capture": no hook, no injection.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Any, Callable

import numpy as np

log = logging.getLogger(__name__)

GAME_WINDOW_CLASS = "RiotWindowClass"
GAME_WINDOW_TITLE = "League of Legends (TM) Client"
#: Windows 10 1903: first build with Windows.Graphics.Capture for a Win32 HWND.
MIN_BUILD = 18362
#: Accept a capture session whose yellow border cannot be turned off (Windows 10).
ALLOW_BORDER = False
#: Frame pool size (2 = one held by us, one being written by DWM).
POOL_BUFFERS = 2
#: Wait at most this long (s) for the first frame of a new session.
FIRST_FRAME_TIMEOUT_S = 0.25
#: The game window is looked up again this often (s): it changes at each game.
HWND_RECHECK_S = 2.0
#: Re-open attempts after a failure are spaced by this (s).
RETRY_AFTER_S = 3.0
#: Window bounds (DWM) and frame content may differ by this many px (rounding) before the
#: screen -> window mapping is considered unknown for this frame.
SIZE_TOLERANCE = 2

S_OK = 0
S_FALSE = 1
RPC_E_CHANGED_MODE = 0x80010106
DXGI_FORMAT_B8G8R8A8_UNORM = 87          # == DirectXPixelFormat.B8G8R8A8UIntNormalized
D3D11_CREATE_DEVICE_BGRA_SUPPORT = 0x20
D3D_DRIVER_TYPE_HARDWARE = 1
D3D11_SDK_VERSION = 7
DWMWA_EXTENDED_FRAME_BOUNDS = 9

# vtable slots (IUnknown 0-2, IInspectable 3-5, interface methods from 6)
_QI, _RELEASE = 0, 2
_INTEROP_CREATE_FOR_WINDOW = 3            # IGraphicsCaptureItemInterop (IUnknown based)
_ITEM_GET_SIZE = 7                        # IGraphicsCaptureItem: DisplayName 6, Size 7
_POOL_STATICS2_CREATE_FREE_THREADED = 6   # IDirect3D11CaptureFramePoolStatics2
_POOL_RECREATE = 6                        # IDirect3D11CaptureFramePool
_POOL_TRY_GET_NEXT_FRAME = 7
_POOL_CREATE_SESSION = 10
_FRAME_GET_SURFACE = 6                    # IDirect3D11CaptureFrame: Surface 6, Time 7, ContentSize 8
_FRAME_GET_CONTENT_SIZE = 8
_SESSION_START = 6                        # IGraphicsCaptureSession
_SESSION2_PUT_CURSOR = 7                  # IGraphicsCaptureSession2: get 6, put 7
_SESSION3_PUT_BORDER = 7                  # IGraphicsCaptureSession3: get 6, put 7
_SESSION_STATICS_IS_SUPPORTED = 6         # IGraphicsCaptureSessionStatics
_CLOSABLE_CLOSE = 6                       # IClosable
_DXGI_ACCESS_GET_INTERFACE = 3            # IDirect3DDxgiInterfaceAccess (IUnknown based)
_CTX_COPY_SUBRESOURCE_REGION = 46
_DEVICE_CREATE_TEXTURE2D = 5

IIDS = {
    "IGraphicsCaptureItemInterop": "3628e81b-3cac-4c60-b7f4-23ce0e0c3356",
    "IGraphicsCaptureItem": "79c3f95b-31f7-4ec2-a464-632ef5d30760",
    "IDirect3D11CaptureFramePoolStatics2": "589b103f-6bbc-5df5-a991-02e28b3b66d5",
    "IDirect3D11CaptureFramePool": "24eb6d22-1975-422e-82e7-780dbd8ddf24",
    "IDirect3D11CaptureFrame": "fa50c623-38da-4b32-acf3-fa9734ad800e",
    "IGraphicsCaptureSession": "814e42a9-f70f-4ad7-939b-fddcc6eb880d",
    "IGraphicsCaptureSession2": "2c39ae40-7d2e-5044-804e-8b6799d4cf9e",
    "IGraphicsCaptureSession3": "f2cdd966-22ae-5ea1-9596-3a289344c3be",
    "IGraphicsCaptureSessionStatics": "2224a540-5974-49aa-b232-0882536f4cb5",
    "IDirect3DDevice": "a37624ab-8d5f-4650-9d3e-9eae3d9bc670",
    "IDirect3DDxgiInterfaceAccess": "a9b3d012-3df2-4ee3-b8d1-8695f457d3c1",
    "IClosable": "30d5a829-7fa4-4026-83bb-d75bae4ea99e",
    "IDXGIDevice": "54ec77fa-1377-44e6-8c32-88fd5f44c84c",
    "ID3D11Texture2D": "6f15aaf2-d208-4e89-9ab4-489535d34f9c",
}
CLASS_ITEM = "Windows.Graphics.Capture.GraphicsCaptureItem"
CLASS_POOL = "Windows.Graphics.Capture.Direct3D11CaptureFramePool"
CLASS_SESSION = "Windows.Graphics.Capture.GraphicsCaptureSession"


class WgcUnsupported(OSError):
    """Permanent: WGC window capture cannot work on this PC (the backend is dropped)."""


def _u32(hr: Any) -> int:
    return int(hr or 0) & 0xFFFFFFFF


def windows_build() -> int:
    try:
        return int(sys.getwindowsversion().build)  # type: ignore[attr-defined]
    except Exception:
        return 0


def available() -> bool:
    """True where WGC window capture may work (Windows 10 1903+, combase / d3d11 present and
    ``GraphicsCaptureSession.IsSupported()``). Never raises."""
    if sys.platform != "win32" or windows_build() < MIN_BUILD:
        return False
    try:
        return bool(_ComWgc().is_supported())
    except Exception as exc:
        log.debug("WGC unavailable: %s", exc)
        return False


# ======================================================================================
# Raw WinRT / D3D11 calls (Windows only; tests replace the dlls and the COM objects)
# ======================================================================================

class _ComWgc:
    """One WGC session on one HWND. Methods raise; :class:`WgcCapture` catches."""

    def __init__(self, api: Any = None, dlls: dict[str, Any] | None = None) -> None:
        import ctypes

        if api is None:
            from treeaicoach.dxgi_capture import _get_api

            api = _get_api()
        self.api, self.ctypes = api, ctypes
        d = dlls or {}
        self.combase = d.get("combase") or ctypes.WinDLL("combase")
        self.d3d11 = d.get("d3d11") or ctypes.WinDLL("d3d11")
        self.iid = {k: api.guid(v) for k, v in IIDS.items()}

        class SizeInt32(ctypes.Structure):
            _fields_ = [("Width", ctypes.c_int32), ("Height", ctypes.c_int32)]

        self.SizeInt32 = SizeInt32
        self.device = self.ctx = self.rt_device = None
        self.item = self.pool = self.session = None
        self.pool_size: tuple[int, int] = (0, 0)
        self.border: bool | None = None
        self.cursor: bool | None = None
        self.staging: Any = None
        self.staging_size = (0, 0)

    # ---------------------------------------------------------------- helpers
    def _hr(self, obj: Any, index: int, argtypes: tuple, *args: Any) -> int:
        return self.api.hr(obj, index, argtypes, *args)

    def _qi(self, obj: Any, iid_name: str) -> Any:
        c = self.ctypes
        out = c.c_void_p()
        hr = self._hr(obj, _QI, (c.POINTER(self.api.GUID), c.POINTER(c.c_void_p)),
                      c.byref(self.iid[iid_name]), c.byref(out))
        return out if hr == S_OK and out.value else None

    def _factory(self, class_name: str, iid_name: str) -> Any:
        c = self.ctypes
        cb = self.combase
        cb.WindowsCreateString.argtypes = [c.c_wchar_p, c.c_uint32, c.POINTER(c.c_void_p)]
        cb.WindowsCreateString.restype = c.c_long
        cb.WindowsDeleteString.argtypes = [c.c_void_p]
        cb.RoGetActivationFactory.argtypes = [c.c_void_p, c.POINTER(self.api.GUID), c.POINTER(c.c_void_p)]
        cb.RoGetActivationFactory.restype = c.c_long
        hs = c.c_void_p()
        hr = _u32(cb.WindowsCreateString(class_name, len(class_name), c.byref(hs)))
        if hr != S_OK:
            raise OSError(f"WindowsCreateString 0x{hr:08X}")
        try:
            fac = c.c_void_p()
            hr = _u32(cb.RoGetActivationFactory(hs, c.byref(self.iid[iid_name]), c.byref(fac)))
            if hr != S_OK or not fac.value:
                raise WgcUnsupported(f"RoGetActivationFactory({class_name.rsplit('.', 1)[-1]}) 0x{hr:08X}")
            return fac
        finally:
            cb.WindowsDeleteString(hs)

    def ro_init(self) -> None:
        c = self.ctypes
        self.combase.RoInitialize.argtypes = [c.c_int]
        self.combase.RoInitialize.restype = c.c_long
        hr = _u32(self.combase.RoInitialize(1))          # RO_INIT_MULTITHREADED
        if hr not in (S_OK, S_FALSE, RPC_E_CHANGED_MODE):
            raise OSError(f"RoInitialize 0x{hr:08X}")

    def is_supported(self) -> bool:
        self.ro_init()
        c = self.ctypes
        st = self._factory(CLASS_SESSION, "IGraphicsCaptureSessionStatics")
        try:
            ok = c.c_ubyte(0)
            hr = self._hr(st, _SESSION_STATICS_IS_SUPPORTED, (c.POINTER(c.c_ubyte),), c.byref(ok))
            return hr == S_OK and bool(ok.value)
        finally:
            self.api.release(st)

    # ---------------------------------------------------------------- session
    def open(self, hwnd: int, allow_border: bool = ALLOW_BORDER) -> None:
        api, c = self.api, self.ctypes
        self.ro_init()
        device, ctx, level = c.c_void_p(), c.c_void_p(), c.c_uint()
        hr = _u32(api.D3D11CreateDevice(None, D3D_DRIVER_TYPE_HARDWARE, None, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                                        None, 0, D3D11_SDK_VERSION, c.byref(device), c.byref(level), c.byref(ctx)))
        if hr != S_OK or not device.value:
            raise OSError(f"D3D11CreateDevice 0x{hr:08X}")
        self.device, self.ctx = device, ctx
        dxgi_dev = self._qi(device, "IDXGIDevice")
        if dxgi_dev is None:
            raise OSError("QueryInterface(IDXGIDevice) failed")
        try:
            fn = getattr(self.d3d11, "CreateDirect3D11DeviceFromDXGIDevice", None)
            if fn is None:
                raise WgcUnsupported("d3d11.CreateDirect3D11DeviceFromDXGIDevice missing")
            fn.argtypes = [c.c_void_p, c.POINTER(c.c_void_p)]
            fn.restype = c.c_long
            insp = c.c_void_p()
            hr = _u32(fn(dxgi_dev, c.byref(insp)))
            if hr != S_OK or not insp.value:
                raise WgcUnsupported(f"CreateDirect3D11DeviceFromDXGIDevice 0x{hr:08X}")
        finally:
            api.release(dxgi_dev)
        try:
            self.rt_device = self._qi(insp, "IDirect3DDevice")
        finally:
            api.release(insp)
        if self.rt_device is None:
            raise WgcUnsupported("QueryInterface(IDirect3DDevice) failed")
        interop = self._factory(CLASS_ITEM, "IGraphicsCaptureItemInterop")
        try:
            item = c.c_void_p()
            hr = self._hr(interop, _INTEROP_CREATE_FOR_WINDOW,
                          (c.c_void_p, c.POINTER(api.GUID), c.POINTER(c.c_void_p)),
                          c.c_void_p(int(hwnd)), c.byref(self.iid["IGraphicsCaptureItem"]), c.byref(item))
            if hr != S_OK or not item.value:
                raise OSError(f"CreateForWindow 0x{hr:08X}")
            self.item = item
        finally:
            api.release(interop)
        size = self.SizeInt32()
        hr = self._hr(self.item, _ITEM_GET_SIZE, (c.POINTER(self.SizeInt32),), c.byref(size))
        if hr != S_OK or size.Width <= 0 or size.Height <= 0:
            raise OSError(f"GraphicsCaptureItem.Size 0x{hr:08X} {size.Width}x{size.Height}")
        statics = self._factory(CLASS_POOL, "IDirect3D11CaptureFramePoolStatics2")
        try:
            pool = c.c_void_p()
            hr = self._hr(statics, _POOL_STATICS2_CREATE_FREE_THREADED,
                          (c.c_void_p, c.c_int32, c.c_int32, self.SizeInt32, c.POINTER(c.c_void_p)),
                          self.rt_device, DXGI_FORMAT_B8G8R8A8_UNORM, POOL_BUFFERS, size, c.byref(pool))
            if hr != S_OK or not pool.value:
                raise OSError(f"CreateFreeThreaded 0x{hr:08X}")
            self.pool, self.pool_size = pool, (int(size.Width), int(size.Height))
        finally:
            api.release(statics)
        session = c.c_void_p()
        hr = self._hr(self.pool, _POOL_CREATE_SESSION, (c.c_void_p, c.POINTER(c.c_void_p)),
                      self.item, c.byref(session))
        if hr != S_OK or not session.value:
            raise OSError(f"CreateCaptureSession 0x{hr:08X}")
        self.session = session
        self.cursor = self._put_bool("IGraphicsCaptureSession2", _SESSION2_PUT_CURSOR, False)
        self.border = not self._put_bool("IGraphicsCaptureSession3", _SESSION3_PUT_BORDER, False)
        if self.border and not allow_border:
            raise WgcUnsupported("capture border cannot be removed (Windows 10): yellow frame around the game")
        hr = self._hr(self.session, _SESSION_START, ())
        if hr != S_OK:
            raise OSError(f"StartCapture 0x{hr:08X}")

    def _put_bool(self, iid_name: str, index: int, value: bool) -> bool:
        """``session.<prop> = value`` through an optional interface; True when applied."""
        c = self.ctypes
        s = self._qi(self.session, iid_name)
        if s is None:
            return False
        try:
            return self._hr(s, index, (c.c_ubyte,), 1 if value else 0) == S_OK
        finally:
            self.api.release(s)

    # ---------------------------------------------------------------- frames
    def try_next_frame(self) -> Any:
        c = self.ctypes
        frame = c.c_void_p()
        hr = self._hr(self.pool, _POOL_TRY_GET_NEXT_FRAME, (c.POINTER(c.c_void_p),), c.byref(frame))
        if hr != S_OK:
            raise OSError(f"TryGetNextFrame 0x{hr:08X}")
        return frame if frame.value else None

    def content_size(self, frame: Any) -> tuple[int, int]:
        c = self.ctypes
        size = self.SizeInt32()
        hr = self._hr(frame, _FRAME_GET_CONTENT_SIZE, (c.POINTER(self.SizeInt32),), c.byref(size))
        if hr != S_OK:
            raise OSError(f"ContentSize 0x{hr:08X}")
        return int(size.Width), int(size.Height)

    def recreate(self, w: int, h: int) -> None:
        c = self.ctypes
        hr = self._hr(self.pool, _POOL_RECREATE, (c.c_void_p, c.c_int32, c.c_int32, self.SizeInt32),
                      self.rt_device, DXGI_FORMAT_B8G8R8A8_UNORM, POOL_BUFFERS, self.SizeInt32(int(w), int(h)))
        if hr != S_OK:
            raise OSError(f"FramePool.Recreate 0x{hr:08X}")
        self.pool_size = (int(w), int(h))

    def copy_crop(self, frame: Any, ox: int, oy: int, w: int, h: int) -> np.ndarray | None:
        """Pixels (BGR) of the box ``(ox, oy, w, h)`` of the frame's texture."""
        from treeaicoach.dxgi_capture import read_texture

        api, c = self.api, self.ctypes
        surface = c.c_void_p()
        hr = self._hr(frame, _FRAME_GET_SURFACE, (c.POINTER(c.c_void_p),), c.byref(surface))
        if hr != S_OK or not surface.value:
            raise OSError(f"Frame.Surface 0x{hr:08X}")
        try:
            access = self._qi(surface, "IDirect3DDxgiInterfaceAccess")
            if access is None:
                raise OSError("QueryInterface(IDirect3DDxgiInterfaceAccess) failed")
            try:
                tex = c.c_void_p()
                hr = self._hr(access, _DXGI_ACCESS_GET_INTERFACE, (c.POINTER(api.GUID), c.POINTER(c.c_void_p)),
                              c.byref(self.iid["ID3D11Texture2D"]), c.byref(tex))
                if hr != S_OK or not tex.value:
                    raise OSError(f"GetInterface(ID3D11Texture2D) 0x{hr:08X}")
            finally:
                api.release(access)
        finally:
            api.release(surface)
        try:
            self._ensure_staging(w, h)
            box = api.D3D11_BOX(ox, oy, 0, ox + w, oy + h, 1)
            api.call(self.ctx, _CTX_COPY_SUBRESOURCE_REGION, None,
                     (c.c_void_p, c.c_uint, c.c_uint, c.c_uint, c.c_uint, c.c_void_p, c.c_uint,
                      c.POINTER(api.D3D11_BOX)),
                     self.staging, 0, 0, 0, 0, tex, 0, c.byref(box))
        finally:
            api.release(tex)
        return read_texture(self.ctx, self.staging, w, h, DXGI_FORMAT_B8G8R8A8_UNORM)

    def _ensure_staging(self, w: int, h: int) -> None:
        if self.staging is not None and self.staging_size == (w, h):
            return
        api, c = self.api, self.ctypes
        api.release(self.staging)
        self.staging, self.staging_size = None, (0, 0)
        desc = api.D3D11_TEXTURE2D_DESC()
        desc.Width, desc.Height, desc.MipLevels, desc.ArraySize = w, h, 1, 1
        desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM
        desc.SampleDesc.Count, desc.SampleDesc.Quality = 1, 0
        desc.Usage, desc.BindFlags, desc.CPUAccessFlags, desc.MiscFlags = 3, 0, 0x20000, 0   # STAGING, CPU read
        tex = c.c_void_p()
        hr = api.hr(self.device, _DEVICE_CREATE_TEXTURE2D,
                    (c.POINTER(api.D3D11_TEXTURE2D_DESC), c.c_void_p, c.POINTER(c.c_void_p)),
                    c.byref(desc), None, c.byref(tex))
        if hr != S_OK or not tex.value:
            raise OSError(f"CreateTexture2D 0x{hr:08X}")
        self.staging, self.staging_size = tex, (w, h)

    def close_frame(self, frame: Any) -> None:
        self._close(frame)

    def _close(self, obj: Any) -> None:
        """IClosable.Close() then Release (WinRT objects holding GPU resources)."""
        if obj is None or not getattr(obj, "value", obj):
            return
        try:
            cl = self._qi(obj, "IClosable")
            if cl is not None:
                try:
                    self._hr(cl, _CLOSABLE_CLOSE, ())
                finally:
                    self.api.release(cl)
        except Exception:
            log.debug("IClosable.Close failed", exc_info=True)
        self.api.release(obj)

    def close(self) -> None:
        self._close(self.session)
        self._close(self.pool)
        self.api.release(self.item)
        self.api.release(self.staging)
        self.api.release(self.rt_device)
        self.api.release(self.ctx)
        self.api.release(self.device)
        self.session = self.pool = self.item = self.staging = None
        self.rt_device = self.ctx = self.device = None


# ======================================================================================
# Window lookup (Windows only; injectable)
# ======================================================================================

def find_game_hwnd() -> int:
    if sys.platform != "win32":
        return 0
    try:
        import ctypes
        from ctypes import wintypes

        u = ctypes.windll.user32
        u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        u.FindWindowW.restype = wintypes.HWND
        return int(u.FindWindowW(GAME_WINDOW_CLASS, None) or u.FindWindowW(None, GAME_WINDOW_TITLE) or 0)
    except Exception:
        return 0


def window_bounds(hwnd: int) -> tuple[int, int, int, int] | None:
    """Visible bounds ``(x, y, w, h)`` of the window in physical screen px (what WGC captures:
    ``DWMWA_EXTENDED_FRAME_BOUNDS``, without the invisible resize borders); None if unknown."""
    if sys.platform != "win32" or not hwnd:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        u = ctypes.windll.user32
        u.IsWindow.argtypes = [wintypes.HWND]
        if not u.IsWindow(hwnd) or u.IsIconic(wintypes.HWND(hwnd)):
            return None
        rc = wintypes.RECT()
        dwm = ctypes.windll.dwmapi
        dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwm.DwmGetWindowAttribute.restype = ctypes.c_long
        if dwm.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rc), ctypes.sizeof(rc)) != 0:
            u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
            if not u.GetWindowRect(hwnd, ctypes.byref(rc)):
                return None
        return int(rc.left), int(rc.top), int(rc.right - rc.left), int(rc.bottom - rc.top)
    except Exception:
        return None


# ======================================================================================
# Backend
# ======================================================================================

class WgcCapture:
    """Game-window capture through Windows.Graphics.Capture. One instance per thread.

    :meth:`grab` takes a rectangle in screen px (like the other backends) and crops it from the
    newest frame of the game window. Returns None (the caller uses the next backend for this
    grab) when there is no game window (``idle`` True: not a failure), when the rectangle is
    not inside the window, or on errors. ``dead``: WGC cannot work here (old Windows, border
    impossible to remove, API missing). Never raises.
    """

    name = "wgc"
    #: frames come from the game window only: nothing drawn above it is captured
    window_only = True

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 com_factory: Callable[[], Any] | None = None,
                 find_hwnd: Callable[[], int] = find_game_hwnd,
                 bounds: Callable[[int], tuple[int, int, int, int] | None] = window_bounds,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._clock = clock
        self._com_factory = com_factory or _ComWgc
        self._find_hwnd = find_hwnd
        self._bounds = bounds
        self._sleep = sleep
        self._thread: int | None = None
        self._com: Any = None
        self._hwnd = 0
        self._hwnd_at = -1e18
        self._frame: Any = None
        self._frame_size: tuple[int, int] = (0, 0)
        self._retry_at = -1e18
        self.idle = False
        #: the last rectangle was not inside the game window (served by the next backend)
        self.outside = False
        self.dead = False
        self.last_error: str | None = None
        self.stats: dict[str, Any] = {"grabs": 0, "frames": 0, "static": 0, "reinit": 0, "fail": 0,
                                      "outside": 0, "size_mismatch": 0, "recreate": 0, "no_window": 0,
                                      "border": None, "cursor_off": None, "window": None, "last_mean": None}

    # ------------------------------------------------------------------ session
    def _release(self) -> None:
        com, frame = self._com, self._frame
        self._com, self._frame, self._frame_size = None, None, (0, 0)
        if com is not None:
            try:
                if frame is not None:
                    com.close_frame(frame)
            except Exception:
                pass
            try:
                com.close()
            except Exception:
                pass

    def _game_hwnd(self) -> int:
        now = self._clock()
        if now - self._hwnd_at >= HWND_RECHECK_S or not self._hwnd:
            self._hwnd_at = now
            try:
                hwnd = int(self._find_hwnd() or 0)
            except Exception:
                hwnd = 0
            if hwnd != self._hwnd:
                if self._com is not None:
                    log.info("WGC: game window changed, new capture session")
                self._release()
                self._hwnd = hwnd
        return self._hwnd

    def _open(self, hwnd: int) -> bool:
        if self._clock() < self._retry_at:
            return False
        com = None
        try:
            com = self._com_factory()
            com.open(hwnd)
        except WgcUnsupported as exc:
            self._fail(exc, permanent=True, com=com)
            return False
        except Exception as exc:
            self._fail(exc, com=com)
            return False
        self._com = com
        self.stats["reinit"] += 1
        self.stats["border"] = getattr(com, "border", None)
        self.stats["cursor_off"] = getattr(com, "cursor", None)
        ps = getattr(com, "pool_size", (0, 0))
        self.stats["window"] = f"{ps[0]}x{ps[1]}"
        self.last_error = None
        log.info("WGC window capture ready (%s, border %s, cursor off %s)", self.stats["window"],
                 self.stats["border"], self.stats["cursor_off"])
        return True

    def _fail(self, exc: Exception, permanent: bool = False, com: Any = None) -> None:
        self.stats["fail"] += 1
        self.last_error = f"{type(exc).__name__}: {exc}"[:300]
        if com is not None and com is not self._com:
            try:
                com.close()
            except Exception:
                pass
        self._release()
        self._retry_at = self._clock() + RETRY_AFTER_S
        if permanent:
            self.dead = True
            log.info("WGC window capture unavailable: %s", self.last_error)
        else:
            log.debug("WGC failure: %s", self.last_error)

    def _newest_frame(self, wait_s: float) -> bool:
        """Drain the pool keeping the newest frame; True when a new one arrived."""
        com = self._com
        got = False
        deadline = self._clock() + wait_s
        while True:
            for _ in range(8):                      # pool holds <= POOL_BUFFERS frames
                fr = com.try_next_frame()
                if fr is None:
                    break
                if self._frame is not None:
                    com.close_frame(self._frame)
                self._frame, got = fr, True
                self.stats["frames"] += 1
            if got or self._clock() >= deadline:
                break
            self._sleep(0.005)
        if got:
            self._frame_size = tuple(com.content_size(self._frame))   # type: ignore[assignment]
            pw, ph = getattr(com, "pool_size", self._frame_size)
            fw, fh = self._frame_size
            if (fw, fh) != (pw, ph) and fw > 0 and fh > 0:
                com.recreate(fw, fh)                # window resized: next frames at the new size
                self.stats["recreate"] += 1
        return got

    # ------------------------------------------------------------------ grab
    def grab(self, rect: Any) -> np.ndarray | None:
        """BGR image of ``rect`` (screen physical px) taken from the game window only."""
        if self.dead:
            return None
        try:
            x, y, w, h = int(rect.x), int(rect.y), int(rect.w), int(rect.h)
        except Exception:
            return None
        if w <= 0 or h <= 0 or w * h > 8192 * 8192:
            return None
        tid = threading.get_ident()
        if self._thread != tid:
            self._release()
            self._thread = tid
        self.stats["grabs"] += 1
        self.outside = False
        try:
            hwnd = self._game_hwnd()
            if not hwnd:
                self.idle = True
                self.stats["no_window"] += 1
                return None
            self.idle = False
            if self._com is None and not self._open(hwnd):
                return None
            new = self._newest_frame(FIRST_FRAME_TIMEOUT_S if self._frame is None else 0.0)
            if self._frame is None:
                return None
            if not new:
                self.stats["static"] += 1
            b = self._bounds(hwnd)
            if b is None:
                self.idle = True                    # minimized / gone: other backends decide
                return None
            bx, by, bw, bh = b
            fw, fh = self._frame_size
            if abs(bw - fw) > SIZE_TOLERANCE or abs(bh - fh) > SIZE_TOLERANCE:
                self.stats["size_mismatch"] += 1    # resizing / DPI virtualised window
                return None
            ox, oy = x - bx, y - by
            if ox < 0 or oy < 0 or ox + w > fw or oy + h > fh:
                self.stats["outside"] += 1          # not on the game window: next backend
                self.outside = True
                return None
            img = self._com.copy_crop(self._frame, ox, oy, w, h)
            if img is None:
                return None
            self.stats["last_mean"] = round(float(np.asarray(img).mean()), 1)
            return img
        except Exception as exc:
            self._fail(exc)
            return None

    def describe(self) -> str:
        w = self.stats.get("window")
        return f"game window {w}" if w else ""

    def close(self) -> None:
        try:
            self._release()
        except Exception:  # pragma: no cover
            pass

    def __del__(self) -> None:  # pragma: no cover - best effort
        try:
            if self._thread in (None, threading.get_ident()):
                self._release()
        except Exception:
            pass
