"""DXGI Desktop Duplication capture backend (Windows 8+), pure ``ctypes`` - no extra dependency.

Why: ``mss`` uses GDI ``BitBlt`` on the screen DC. With DirectX 11/12 games in borderless
"independent flip" / multiplane-overlay presentation, GDI has to make DWM read the composed
desktop back synchronously (stalls the GPU queue the game uses) and can return black or *stale*
frames; with true exclusive fullscreen it returns black. Desktop Duplication
(``IDXGIOutputDuplication``) is the API OBS' "display capture" uses: the GPU copies only the
**minimap box** (``CopySubresourceRegion``) into a small CPU-readable staging texture, so a
306 x 306 grab moves ~370 KB instead of reading the whole screen.

Only reads pixels already on the user's screen (same as OBS / Discord): no hook, no
injection, nothing touches the game process.

Behaviour:

* one :class:`DxgiCapture` per thread (D3D11 immediate context is not thread-safe); objects
  are created lazily on the first :meth:`DxgiCapture.grab` and re-created after
  ``DXGI_ERROR_ACCESS_LOST`` (mode change, fullscreen switch, UAC prompt...);
* a rectangle must lie on one monitor (output); otherwise :meth:`grab` returns None and the
  caller (``capture.SmartCapture``) uses ``mss`` for that grab;
* Desktop Duplication only hands out a frame when the desktop changed: when nothing changed
  since the previous grab, the last image of the same rectangle is returned (it is still
  exact). A new rectangle on a static screen waits up to :data:`FIRST_FRAME_TIMEOUT_MS`;
* never raises: failures return None and are counted in :attr:`DxgiCapture.stats`.

Off Windows (or on Windows 7 / Remote Desktop / Wine without duplication) :func:`available`
is False and :meth:`DxgiCapture.grab` always returns None.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------- constants
S_OK = 0
DXGI_ERROR_NOT_FOUND = 0x887A0002
DXGI_ERROR_WAIT_TIMEOUT = 0x887A0027
DXGI_ERROR_ACCESS_LOST = 0x887A0026
DXGI_ERROR_INVALID_CALL = 0x887A0001
DXGI_ERROR_UNSUPPORTED = 0x887A0004
DXGI_ERROR_NOT_CURRENTLY_AVAILABLE = 0x887A0022
DXGI_ERROR_SESSION_DISCONNECTED = 0x887A0028
E_ACCESSDENIED = 0x80070005
E_NOTIMPL = 0x80004001

DXGI_FORMAT_B8G8R8A8_UNORM = 87
D3D11_USAGE_STAGING = 3
D3D11_CPU_ACCESS_READ = 0x20000
D3D11_MAP_READ = 1
D3D11_SDK_VERSION = 7
D3D_DRIVER_TYPE_UNKNOWN = 0
D3D_DRIVER_TYPE_HARDWARE = 1
DXGI_MODE_ROTATION_UNSPECIFIED = 0
DXGI_MODE_ROTATION_IDENTITY = 1

#: AcquireNextFrame timeout of a regular grab (ms): 0 = never wait for the game.
GRAB_TIMEOUT_MS = 0
#: Wait this long (ms) for a frame when no image of the requested rectangle exists yet.
FIRST_FRAME_TIMEOUT_MS = 120
#: Give up re-creating the duplication for this long after a hard failure (s).
RETRY_AFTER_S = 5.0

# vtable indices (IUnknown 0-2; IDXGIObject 3-6; ID3D11DeviceChild 3-6)
_QI, _ADDREF, _RELEASE = 0, 1, 2
_FACTORY1_ENUM_ADAPTERS1 = 12
_ADAPTER_ENUM_OUTPUTS = 7
_OUTPUT_GET_DESC = 7
_OUTPUT1_DUPLICATE_OUTPUT = 22
_DUPL_GET_DESC = 7
_DUPL_ACQUIRE_NEXT_FRAME = 8
_DUPL_RELEASE_FRAME = 14
_DEVICE_CREATE_TEXTURE2D = 5
_CTX_MAP = 14
_CTX_UNMAP = 15
_CTX_COPY_SUBRESOURCE_REGION = 46
_TEX2D_GET_DESC = 10


def _u32(hr: int) -> int:
    return int(hr) & 0xFFFFFFFF


def available() -> bool:
    """True where Desktop Duplication may work (Windows 8+ with dxgi.dll / d3d11.dll)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        ctypes.WinDLL("dxgi")
        ctypes.WinDLL("d3d11")
        return sys.getwindowsversion().major >= 6 and sys.getwindowsversion()[:2] >= (6, 2)
    except Exception:
        return False


class _Api:
    """ctypes structures, GUIDs and COM call helpers (built once, Windows only)."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes as wt

        self.ctypes, self.wt = ctypes, wt
        UINT, BOOL = ctypes.c_uint, wt.BOOL

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                        ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

        class DXGI_OUTPUT_DESC(ctypes.Structure):
            _fields_ = [("DeviceName", ctypes.c_wchar * 32), ("DesktopCoordinates", wt.RECT),
                        ("AttachedToDesktop", BOOL), ("Rotation", UINT), ("Monitor", wt.HMONITOR)]

        class DXGI_RATIONAL(ctypes.Structure):
            _fields_ = [("Numerator", UINT), ("Denominator", UINT)]

        class DXGI_MODE_DESC(ctypes.Structure):
            _fields_ = [("Width", UINT), ("Height", UINT), ("RefreshRate", DXGI_RATIONAL),
                        ("Format", UINT), ("ScanlineOrdering", UINT), ("Scaling", UINT)]

        class DXGI_OUTDUPL_DESC(ctypes.Structure):
            _fields_ = [("ModeDesc", DXGI_MODE_DESC), ("Rotation", UINT),
                        ("DesktopImageInSystemMemory", BOOL)]

        class DXGI_OUTDUPL_POINTER_POSITION(ctypes.Structure):
            _fields_ = [("Position", wt.POINT), ("Visible", BOOL)]

        class DXGI_OUTDUPL_FRAME_INFO(ctypes.Structure):
            _fields_ = [("LastPresentTime", ctypes.c_longlong), ("LastMouseUpdateTime", ctypes.c_longlong),
                        ("AccumulatedFrames", UINT), ("RectsCoalesced", BOOL),
                        ("ProtectedContentMaskedOut", BOOL), ("PointerPosition", DXGI_OUTDUPL_POINTER_POSITION),
                        ("TotalMetadataBufferSize", UINT), ("PointerShapeBufferSize", UINT)]

        class DXGI_SAMPLE_DESC(ctypes.Structure):
            _fields_ = [("Count", UINT), ("Quality", UINT)]

        class D3D11_TEXTURE2D_DESC(ctypes.Structure):
            _fields_ = [("Width", UINT), ("Height", UINT), ("MipLevels", UINT), ("ArraySize", UINT),
                        ("Format", UINT), ("SampleDesc", DXGI_SAMPLE_DESC), ("Usage", UINT),
                        ("BindFlags", UINT), ("CPUAccessFlags", UINT), ("MiscFlags", UINT)]

        class D3D11_SUBRESOURCE_DATA(ctypes.Structure):
            _fields_ = [("pSysMem", ctypes.c_void_p), ("SysMemPitch", UINT), ("SysMemSlicePitch", UINT)]

        class D3D11_MAPPED_SUBRESOURCE(ctypes.Structure):
            _fields_ = [("pData", ctypes.c_void_p), ("RowPitch", UINT), ("DepthPitch", UINT)]

        class D3D11_BOX(ctypes.Structure):
            _fields_ = [("left", UINT), ("top", UINT), ("front", UINT),
                        ("right", UINT), ("bottom", UINT), ("back", UINT)]

        self.GUID = GUID
        self.DXGI_OUTPUT_DESC = DXGI_OUTPUT_DESC
        self.DXGI_OUTDUPL_DESC = DXGI_OUTDUPL_DESC
        self.DXGI_OUTDUPL_FRAME_INFO = DXGI_OUTDUPL_FRAME_INFO
        self.D3D11_TEXTURE2D_DESC = D3D11_TEXTURE2D_DESC
        self.D3D11_SUBRESOURCE_DATA = D3D11_SUBRESOURCE_DATA
        self.D3D11_MAPPED_SUBRESOURCE = D3D11_MAPPED_SUBRESOURCE
        self.D3D11_BOX = D3D11_BOX
        self.IID_IDXGIFactory1 = self.guid("770aae78-f26f-4dba-a829-253c83d1b387")
        self.IID_IDXGIOutput1 = self.guid("00cddea8-939b-4b83-a340-a685226666cc")
        self.IID_ID3D11Texture2D = self.guid("6f15aaf2-d208-4e89-9ab4-489535d34f9c")

        dxgi = ctypes.WinDLL("dxgi")
        d3d11 = ctypes.WinDLL("d3d11")
        self.CreateDXGIFactory1 = dxgi.CreateDXGIFactory1
        self.CreateDXGIFactory1.restype = ctypes.c_long
        self.CreateDXGIFactory1.argtypes = [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
        self.D3D11CreateDevice = d3d11.D3D11CreateDevice
        self.D3D11CreateDevice.restype = ctypes.c_long
        self.D3D11CreateDevice.argtypes = [
            ctypes.c_void_p, UINT, ctypes.c_void_p, UINT, ctypes.c_void_p, UINT, UINT,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(UINT), ctypes.POINTER(ctypes.c_void_p)]
        self._protos: dict[tuple, Any] = {}

    def guid(self, text: str) -> Any:
        import uuid

        u = uuid.UUID(text)
        g = self.GUID()
        g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
        for i, b in enumerate(u.bytes[8:]):
            g.Data4[i] = b
        return g

    def call(self, obj: Any, index: int, restype: Any, argtypes: tuple, *args: Any) -> Any:
        """Call method ``index`` of the COM object ``obj`` (``c_void_p`` / int address)."""
        ctypes = self.ctypes
        key = (index, restype, argtypes)
        proto = self._protos.get(key)
        if proto is None:
            proto = self._protos[key] = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
        ptr = obj.value if isinstance(obj, ctypes.c_void_p) else obj
        vtbl = ctypes.cast(ctypes.c_void_p(ptr), ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        fn = proto(vtbl[index])
        return fn(ptr, *args)

    def hr(self, obj: Any, index: int, argtypes: tuple, *args: Any) -> int:
        return _u32(self.call(obj, index, self.ctypes.c_long, argtypes, *args))

    def release(self, obj: Any) -> None:
        try:
            if obj is not None and (obj.value if isinstance(obj, self.ctypes.c_void_p) else obj):
                self.call(obj, _RELEASE, self.ctypes.c_ulong, ())
        except Exception:  # pragma: no cover - defensive
            log.debug("COM Release failed", exc_info=True)


_api: _Api | None = None
_api_lock = threading.Lock()


def _get_api() -> _Api:
    global _api
    with _api_lock:
        if _api is None:
            _api = _Api()
        return _api


class DxgiCapture:
    """Region capture through Desktop Duplication. One instance per thread. Never raises."""

    name = "dxgi"

    def __init__(self, clock: Any = time.monotonic) -> None:
        self._clock = clock
        self._thread: int | None = None
        self._factory: Any = None
        self._adapter: Any = None
        self._output: Any = None
        self._device: Any = None
        self._ctx: Any = None
        self._dupl: Any = None
        self._out_rect: tuple[int, int, int, int] | None = None   # desktop x, y, w, h
        self._staging: Any = None
        self._staging_size: tuple[int, int] = (0, 0)
        self._spare: dict[tuple[int, int], Any] = {}
        self._last: dict[tuple[int, int, int, int], np.ndarray] = {}
        self._retry_at = -1e18
        self.last_error: str | None = None
        #: permanent failure (no Desktop Duplication here: E_NOTIMPL / UNSUPPORTED, Wine, RDP...):
        #: the caller stops using this backend instead of retrying every few seconds
        self.dead = False
        #: diagnostics: grabs, new frames, timeouts (static desktop), re-inits, failures
        self.stats: dict[str, Any] = {"grabs": 0, "frames": 0, "static": 0, "reinit": 0,
                                      "fail": 0, "adapter": None, "output": None}

    # ------------------------------------------------------------------ setup
    def _release_all(self) -> None:
        if self._api_ok():
            api = _get_api()
            for tex in list(getattr(self, "_spare", {}).values()):
                api.release(tex)
            for name in ("_staging", "_dupl", "_ctx", "_device", "_output", "_adapter", "_factory"):
                api.release(getattr(self, name))
        if hasattr(self, "_spare"):
            self._spare.clear()
        self._factory = self._adapter = self._output = self._device = None
        self._ctx = self._dupl = self._staging = None
        self._staging_size = (0, 0)
        self._out_rect = None
        self._last.clear()

    @staticmethod
    def _api_ok() -> bool:
        return sys.platform == "win32" and _api is not None

    def _contains(self, x: int, y: int, w: int, h: int) -> bool:
        o = self._out_rect
        return o is not None and x >= o[0] and y >= o[1] and x + w <= o[0] + o[2] and y + h <= o[1] + o[3]

    def _open(self, x: int, y: int, w: int, h: int) -> bool:
        """Create factory / device / duplication for the output containing the rectangle."""
        self._release_all()
        if self._clock() < self._retry_at:
            return False
        api = _get_api()
        ctypes = api.ctypes
        try:
            factory = ctypes.c_void_p()
            hr = _u32(api.CreateDXGIFactory1(ctypes.byref(api.IID_IDXGIFactory1), ctypes.byref(factory)))
            if hr != S_OK or not factory.value:
                self.dead = True
                raise OSError(f"CreateDXGIFactory1 0x{hr:08X}")
            self._factory = factory
            found = None
            for ai in range(16):
                adapter = ctypes.c_void_p()
                hr = api.hr(factory, _FACTORY1_ENUM_ADAPTERS1, (ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)),
                            ai, ctypes.byref(adapter))
                if hr == DXGI_ERROR_NOT_FOUND or not adapter.value:
                    break
                for oi in range(16):
                    output = ctypes.c_void_p()
                    hr = api.hr(adapter, _ADAPTER_ENUM_OUTPUTS, (ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)),
                                oi, ctypes.byref(output))
                    if hr == DXGI_ERROR_NOT_FOUND or not output.value:
                        break
                    desc = api.DXGI_OUTPUT_DESC()
                    api.hr(output, _OUTPUT_GET_DESC, (ctypes.POINTER(api.DXGI_OUTPUT_DESC),), ctypes.byref(desc))
                    rc = desc.DesktopCoordinates
                    orect = (int(rc.left), int(rc.top), int(rc.right - rc.left), int(rc.bottom - rc.top))
                    if desc.AttachedToDesktop and orect[0] <= x and orect[1] <= y \
                            and x + w <= orect[0] + orect[2] and y + h <= orect[1] + orect[3] and found is None:
                        found = (adapter, output, orect, desc.DeviceName, int(desc.Rotation))
                        continue
                    api.release(output)
                if found is not None and found[0] is adapter:
                    break
                api.release(adapter)
            if found is None:
                raise LookupError("rectangle not inside one monitor")
            adapter, output, orect, dev_name, rotation = found
            self._adapter, self._output, self._out_rect = adapter, output, orect
            if rotation not in (DXGI_MODE_ROTATION_UNSPECIFIED, DXGI_MODE_ROTATION_IDENTITY):
                raise OSError(f"rotated monitor ({rotation}) not supported")
            device, ctx, level = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_uint()
            hr = _u32(api.D3D11CreateDevice(adapter, D3D_DRIVER_TYPE_UNKNOWN, None, 0, None, 0,
                                            D3D11_SDK_VERSION, ctypes.byref(device), ctypes.byref(level),
                                            ctypes.byref(ctx)))
            if hr != S_OK or not device.value or not ctx.value:
                raise OSError(f"D3D11CreateDevice 0x{hr:08X}")
            self._device, self._ctx = device, ctx
            out1 = ctypes.c_void_p()
            hr = api.hr(output, _QI, (ctypes.POINTER(api.GUID), ctypes.POINTER(ctypes.c_void_p)),
                        ctypes.byref(api.IID_IDXGIOutput1), ctypes.byref(out1))
            if hr != S_OK or not out1.value:
                raise OSError(f"IDXGIOutput1 0x{hr:08X}")
            try:
                dupl = ctypes.c_void_p()
                hr = api.hr(out1, _OUTPUT1_DUPLICATE_OUTPUT, (ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)),
                            device, ctypes.byref(dupl))
            finally:
                api.release(out1)
            if hr != S_OK or not dupl.value:
                if hr in (E_NOTIMPL, DXGI_ERROR_UNSUPPORTED):
                    self.dead = True
                raise OSError(f"DuplicateOutput 0x{hr:08X}")
            self._dupl = dupl
            dd = api.DXGI_OUTDUPL_DESC()
            api.call(dupl, _DUPL_GET_DESC, None, (ctypes.POINTER(api.DXGI_OUTDUPL_DESC),), ctypes.byref(dd))
            if dd.Rotation not in (DXGI_MODE_ROTATION_UNSPECIFIED, DXGI_MODE_ROTATION_IDENTITY):
                raise OSError(f"rotated duplication ({dd.Rotation}) not supported")
            self.stats["output"] = f"{dev_name} {orect[2]}x{orect[3]}@{orect[0]},{orect[1]}"
            self.stats["reinit"] += 1
            self.last_error = None
            log.info("DXGI capture ready on %s (%dx%d, feature level 0x%X)", dev_name.strip("\x00 "),
                     dd.ModeDesc.Width, dd.ModeDesc.Height, level.value)
            return True
        except Exception as exc:
            self.last_error = str(exc)
            self.stats["fail"] += 1
            self._retry_at = self._clock() + RETRY_AFTER_S
            log.info("DXGI capture unavailable: %s", exc)
            self._release_all()
            return False

    def _ensure_staging(self, w: int, h: int) -> bool:
        """Staging texture of this size (a few sizes are kept: minimap, HUD patch, window)."""
        if self._staging is not None and self._staging_size == (w, h):
            return True
        api = _get_api()
        ctypes = api.ctypes
        if self._staging is not None:
            self._spare[self._staging_size] = self._staging
        self._staging, self._staging_size = None, (0, 0)
        hit = self._spare.pop((w, h), None)
        if hit is not None:
            self._staging, self._staging_size = hit, (w, h)
            return True
        while len(self._spare) >= 3:
            api.release(self._spare.pop(next(iter(self._spare))))
        desc = api.D3D11_TEXTURE2D_DESC()
        desc.Width, desc.Height, desc.MipLevels, desc.ArraySize = w, h, 1, 1
        desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM
        desc.SampleDesc.Count, desc.SampleDesc.Quality = 1, 0
        desc.Usage, desc.BindFlags = D3D11_USAGE_STAGING, 0
        desc.CPUAccessFlags, desc.MiscFlags = D3D11_CPU_ACCESS_READ, 0
        tex = ctypes.c_void_p()
        hr = api.hr(self._device, _DEVICE_CREATE_TEXTURE2D,
                    (ctypes.POINTER(api.D3D11_TEXTURE2D_DESC), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)),
                    ctypes.byref(desc), None, ctypes.byref(tex))
        if hr != S_OK or not tex.value:
            raise OSError(f"CreateTexture2D 0x{hr:08X}")
        self._staging, self._staging_size = tex, (w, h)
        return True

    # ------------------------------------------------------------------ grab
    def grab(self, rect: Any) -> np.ndarray | None:
        """BGR image of ``rect`` (desktop physical px, must lie on one monitor); None on failure."""
        if sys.platform != "win32":
            return None
        try:
            x, y, w, h = int(rect.x), int(rect.y), int(rect.w), int(rect.h)
        except Exception:
            return None
        if w <= 0 or h <= 0 or w * h > 8192 * 8192:
            return None
        tid = threading.get_ident()
        if self._thread != tid:
            self._release_all()
            self._thread = tid
        self.stats["grabs"] += 1
        try:
            if self._dupl is None or not self._contains(x, y, w, h):
                if not self._open(x, y, w, h):
                    return None
            key = (x, y, w, h)
            ox, oy = x - self._out_rect[0], y - self._out_rect[1]
            got = self._acquire_copy(ox, oy, w, h, GRAB_TIMEOUT_MS)
            if got is None:          # reinit needed
                return None
            if not got:
                cached = self._last.get(key)
                if cached is not None:
                    self.stats["static"] += 1
                    return cached.copy()
                got = self._acquire_copy(ox, oy, w, h, FIRST_FRAME_TIMEOUT_MS)
                if not got:
                    return None
            img = self._read_staging(w, h)
            if img is None:
                return None
            if len(self._last) > 4:
                self._last.clear()
            self._last[key] = img
            return img.copy()
        except Exception as exc:
            self.stats["fail"] += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.debug("DXGI grab failed", exc_info=True)
            self._release_all()
            self._retry_at = self._clock() + 1.0
            return None

    def _acquire_copy(self, ox: int, oy: int, w: int, h: int, timeout_ms: int) -> bool | None:
        """Acquire the next desktop frame and copy the box into the staging texture.
        True = copied, False = no new frame (timeout), None = duplication lost (re-created later)."""
        api = _get_api()
        ctypes = api.ctypes
        info = api.DXGI_OUTDUPL_FRAME_INFO()
        res = ctypes.c_void_p()
        hr = api.hr(self._dupl, _DUPL_ACQUIRE_NEXT_FRAME,
                    (ctypes.c_uint, ctypes.POINTER(api.DXGI_OUTDUPL_FRAME_INFO), ctypes.POINTER(ctypes.c_void_p)),
                    int(timeout_ms), ctypes.byref(info), ctypes.byref(res))
        if hr == DXGI_ERROR_WAIT_TIMEOUT:
            return False
        if hr != S_OK:
            self.last_error = f"AcquireNextFrame 0x{hr:08X}"
            if hr in (DXGI_ERROR_ACCESS_LOST, DXGI_ERROR_INVALID_CALL, E_ACCESSDENIED,
                      DXGI_ERROR_SESSION_DISCONNECTED):
                log.debug("DXGI duplication lost (0x%08X): re-created at the next grab", hr)
            self._release_all()
            return None
        try:
            tex = ctypes.c_void_p()
            hr = api.hr(res, _QI, (ctypes.POINTER(api.GUID), ctypes.POINTER(ctypes.c_void_p)),
                        ctypes.byref(api.IID_ID3D11Texture2D), ctypes.byref(tex))
            if hr != S_OK or not tex.value:
                raise OSError(f"QueryInterface(ID3D11Texture2D) 0x{hr:08X}")
            try:
                self._ensure_staging(w, h)
                box = api.D3D11_BOX(ox, oy, 0, ox + w, oy + h, 1)
                api.call(self._ctx, _CTX_COPY_SUBRESOURCE_REGION, None,
                         (ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
                          ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(api.D3D11_BOX)),
                         self._staging, 0, 0, 0, 0, tex, 0, ctypes.byref(box))
            finally:
                api.release(tex)
        finally:
            api.release(res)
            api.hr(self._dupl, _DUPL_RELEASE_FRAME, ())
        self.stats["frames"] += 1
        return True

    def _read_staging(self, w: int, h: int) -> np.ndarray | None:
        return read_texture(self._ctx, self._staging, w, h)

    def close(self) -> None:
        """Release every COM object (a later grab re-creates them)."""
        try:
            self._release_all()
        except Exception:  # pragma: no cover
            pass

    def __del__(self) -> None:  # pragma: no cover - best effort
        try:
            if self._thread == threading.get_ident():
                self._release_all()
        except Exception:
            pass


def read_texture(ctx: Any, staging: Any, w: int, h: int) -> np.ndarray | None:
    """Map a CPU-readable B8G8R8A8 texture and return its pixels as a BGR array (one copy)."""
    import cv2

    api = _get_api()
    ctypes = api.ctypes
    mapped = api.D3D11_MAPPED_SUBRESOURCE()
    hr = api.hr(ctx, _CTX_MAP, (ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
                                ctypes.POINTER(api.D3D11_MAPPED_SUBRESOURCE)),
                staging, 0, D3D11_MAP_READ, 0, ctypes.byref(mapped))
    if hr != S_OK or not mapped.pData:
        raise OSError(f"Map 0x{hr:08X}")
    try:
        pitch = int(mapped.RowPitch)
        if pitch < w * 4:
            raise OSError(f"bad row pitch {pitch}")
        buf = (ctypes.c_ubyte * (pitch * (h - 1) + w * 4)).from_address(mapped.pData)
        raw = np.frombuffer(buf, np.uint8)
        view = np.lib.stride_tricks.as_strided(raw, shape=(h, w, 4), strides=(pitch, 4, 1))
        return cv2.cvtColor(view, cv2.COLOR_BGRA2BGR)      # the only copy
    finally:
        api.call(ctx, _CTX_UNMAP, None, (ctypes.c_void_p, ctypes.c_uint), staging, 0)


def self_test_copy(w: int = 37, h: int = 23) -> dict[str, Any]:
    """Validate the D3D11 half of the pipeline without Desktop Duplication (works on Wine and
    in a CI VM): upload a known texture, ``CopySubresourceRegion`` a box into a staging
    texture, ``Map`` it and compare. Returns ``{"ok": bool, ...}``. Never raises."""
    out: dict[str, Any] = {"ok": False}
    if sys.platform != "win32":
        out["error"] = "not windows"
        return out
    cap = DxgiCapture()
    try:
        api = _get_api()
        ctypes = api.ctypes
        device, ctx, level = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_uint()
        hr = _u32(api.D3D11CreateDevice(None, D3D_DRIVER_TYPE_HARDWARE, None, 0, None, 0, D3D11_SDK_VERSION,
                                        ctypes.byref(device), ctypes.byref(level), ctypes.byref(ctx)))
        if hr != S_OK:
            hr = _u32(api.D3D11CreateDevice(None, 5, None, 0, None, 0, D3D11_SDK_VERSION,   # WARP
                                            ctypes.byref(device), ctypes.byref(level), ctypes.byref(ctx)))
        if hr != S_OK:
            out["error"] = f"D3D11CreateDevice 0x{hr:08X}"
            return out
        cap._device, cap._ctx = device, ctx
        W, H = w + 20, h + 10
        src = np.zeros((H, W, 4), np.uint8)
        src[..., 0] = (np.arange(W)[None, :] * 3) % 256
        src[..., 1] = (np.arange(H)[:, None] * 7) % 256
        src[..., 2] = 99
        src[..., 3] = 255
        desc = api.D3D11_TEXTURE2D_DESC()
        desc.Width, desc.Height, desc.MipLevels, desc.ArraySize = W, H, 1, 1
        desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM
        desc.SampleDesc.Count = 1
        desc.Usage, desc.BindFlags, desc.CPUAccessFlags = 0, 0x8, 0   # DEFAULT, SHADER_RESOURCE
        init = api.D3D11_SUBRESOURCE_DATA(src.ctypes.data, W * 4, 0)
        tex = ctypes.c_void_p()
        hr = api.hr(device, _DEVICE_CREATE_TEXTURE2D,
                    (ctypes.POINTER(api.D3D11_TEXTURE2D_DESC), ctypes.POINTER(api.D3D11_SUBRESOURCE_DATA),
                     ctypes.POINTER(ctypes.c_void_p)), ctypes.byref(desc), ctypes.byref(init), ctypes.byref(tex))
        if hr != S_OK:
            out["error"] = f"CreateTexture2D(src) 0x{hr:08X}"
            return out
        try:
            check = api.D3D11_TEXTURE2D_DESC()
            api.call(tex, _TEX2D_GET_DESC, None, (ctypes.POINTER(api.D3D11_TEXTURE2D_DESC),), ctypes.byref(check))
            out["desc_ok"] = (check.Width, check.Height, check.Format) == (W, H, DXGI_FORMAT_B8G8R8A8_UNORM)
            cap._ensure_staging(w, h)
            ox, oy = 11, 5
            box = api.D3D11_BOX(ox, oy, 0, ox + w, oy + h, 1)
            api.call(ctx, _CTX_COPY_SUBRESOURCE_REGION, None,
                     (ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
                      ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(api.D3D11_BOX)),
                     cap._staging, 0, 0, 0, 0, tex, 0, ctypes.byref(box))
            t0 = time.perf_counter()
            img = read_texture(ctx, cap._staging, w, h)
            out["map_ms"] = (time.perf_counter() - t0) * 1000.0
        finally:
            api.release(tex)
        want = src[oy:oy + h, ox:ox + w, :3]
        out["shape"] = None if img is None else tuple(img.shape)
        out["ok"] = bool(img is not None and img.shape == want.shape and np.array_equal(img, want)
                         and out.get("desc_ok"))
        out["feature_level"] = hex(level.value)
        return out
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    finally:
        cap.close()
