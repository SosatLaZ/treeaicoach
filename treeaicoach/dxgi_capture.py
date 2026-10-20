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
DXGI_ERROR_SESSION_DISCONNECTED = 0x887A0028
E_ACCESSDENIED = 0x80070005
E_NOTIMPL = 0x80004001

DXGI_FORMAT_B8G8R8A8_UNORM = 87
#: Desktop formats Desktop Duplication can hand out and that :func:`convert_pixels` decodes.
#: HDR desktops use R16G16B16A16_FLOAT (scRGB linear), some 10-bit SDR desktops
#: R10G10B10A2_UNORM: copying such a frame into a B8G8R8A8 staging texture silently does
#: nothing (CopySubresourceRegion needs identical formats) and the staging stays black.
DXGI_FORMAT_NAMES: dict[int, str] = {
    10: "R16G16B16A16_FLOAT", 24: "R10G10B10A2_UNORM", 28: "R8G8B8A8_UNORM", 29: "R8G8B8A8_UNORM_SRGB",
    87: "B8G8R8A8_UNORM", 88: "B8G8R8X8_UNORM", 91: "B8G8R8A8_UNORM_SRGB", 93: "B8G8R8X8_UNORM_SRGB",
}
FORMAT_BYTES: dict[int, int] = {10: 8, 24: 4, 28: 4, 29: 4, 87: 4, 88: 4, 91: 4, 93: 4}
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
#: A (nearly) black first frame of a new duplication is acquired again this many times (each
#: waiting FIRST_FRAME_TIMEOUT_MS) before it is handed out: the desktop image is sometimes not
#: composed yet right after DuplicateOutput.
FIRST_BLACK_RETRIES = 3
#: Mean 8-bit intensity under which a frame counts as black (capture.BLACK_MEAN_MAX).
BLACK_MEAN = 6.0
#: Candidates (adapter x output showing the rectangle) tried in turn; see :meth:`DxgiCapture.mark_bad`.
MAX_ADAPTERS = 8
MAX_OUTPUTS = 8

# vtable indices (IUnknown 0-2; IDXGIObject 3-6; ID3D11DeviceChild 3-6)
_QI, _ADDREF, _RELEASE = 0, 1, 2
_FACTORY1_ENUM_ADAPTERS1 = 12
_ADAPTER_ENUM_OUTPUTS = 7
_ADAPTER_GET_DESC = 8
_OUTPUT_GET_DESC = 7
_OUTPUT1_DUPLICATE_OUTPUT = 22
_OUTPUT5_DUPLICATE_OUTPUT1 = 26
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
    """ctypes structures, GUIDs and COM call helpers (built once, Windows only; the structures
    alone, ``load_dlls=False``, also build elsewhere: tests fake the COM calls)."""

    def __init__(self, load_dlls: bool = True) -> None:
        import ctypes
        from ctypes import wintypes as wt

        self.ctypes, self.wt = ctypes, wt
        UINT, BOOL = ctypes.c_uint, wt.BOOL

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                        ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

        class LUID(ctypes.Structure):
            _fields_ = [("LowPart", ctypes.c_ulong), ("HighPart", ctypes.c_long)]

        class DXGI_ADAPTER_DESC(ctypes.Structure):
            _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", UINT), ("DeviceId", UINT),
                        ("SubSysId", UINT), ("Revision", UINT), ("DedicatedVideoMemory", ctypes.c_size_t),
                        ("DedicatedSystemMemory", ctypes.c_size_t), ("SharedSystemMemory", ctypes.c_size_t),
                        ("AdapterLuid", LUID)]

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
        self.DXGI_ADAPTER_DESC = DXGI_ADAPTER_DESC
        self.DXGI_OUTPUT_DESC = DXGI_OUTPUT_DESC
        self.DXGI_OUTDUPL_DESC = DXGI_OUTDUPL_DESC
        self.DXGI_OUTDUPL_FRAME_INFO = DXGI_OUTDUPL_FRAME_INFO
        self.D3D11_TEXTURE2D_DESC = D3D11_TEXTURE2D_DESC
        self.D3D11_SUBRESOURCE_DATA = D3D11_SUBRESOURCE_DATA
        self.D3D11_MAPPED_SUBRESOURCE = D3D11_MAPPED_SUBRESOURCE
        self.D3D11_BOX = D3D11_BOX
        self.IID_IDXGIFactory1 = self.guid("770aae78-f26f-4dba-a829-253c83d1b387")
        self.IID_IDXGIOutput1 = self.guid("00cddea8-939b-4b83-a340-a685226666cc")
        self.IID_IDXGIOutput5 = self.guid("80a07424-ab52-42eb-833c-0c42fd282d98")
        self.IID_ID3D11Texture2D = self.guid("6f15aaf2-d208-4e89-9ab4-489535d34f9c")

        self._protos: dict[tuple, Any] = {}
        if not load_dlls:
            return
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
    """Region capture through Desktop Duplication. One instance per thread. Never raises.

    Output choice: every adapter x output is enumerated; the *candidates* are the outputs whose
    desktop rectangle contains the requested one, the output of the monitor showing the
    rectangle's centre (``MonitorFromPoint``) first. A duplication whose frames come out black
    (hybrid laptop: the output claimed by the GPU that does not drive it; a cloned TV; a monitor
    showing another desktop) is dropped with :meth:`mark_bad` and the next candidate is used
    (cycling when all failed). ``stats`` tells which adapter / output / format is in use.
    """

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
        self._fmt: int = DXGI_FORMAT_B8G8R8A8_UNORM                # desktop image format
        self._staging: Any = None
        self._staging_size: tuple[int, int, int] = (0, 0, 0)       # w, h, format
        self._spare: dict[tuple[int, int, int], Any] = {}
        self._last: dict[tuple[int, int, int, int], np.ndarray] = {}
        self._retry_at = -1e18
        self._candidates: list[dict[str, Any]] = []
        self._cand: dict[str, Any] | None = None
        self._bad: set[tuple[int, int]] = set()
        self._since_open = 0
        self.last_error: str | None = None
        #: permanent failure (no Desktop Duplication here: E_NOTIMPL / UNSUPPORTED, Wine, RDP...):
        #: the caller stops using this backend instead of retrying every few seconds
        self.dead = False
        #: diagnostics: grabs, new frames, timeouts (static desktop), re-inits, failures, the
        #: chosen adapter / output / format, all candidates, black frames and the last frame mean
        self.stats: dict[str, Any] = {"grabs": 0, "frames": 0, "static": 0, "reinit": 0, "fail": 0,
                                      "adapter": None, "output": None, "format": None, "hdr": None,
                                      "candidates": [], "chosen": None, "rotations": 0,
                                      "black_frames": 0, "first_black": 0, "last_mean": None}

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
        self._staging_size = (0, 0, 0)
        self._out_rect = None
        self._last.clear()

    @staticmethod
    def _api_ok() -> bool:
        return sys.platform == "win32" and _api is not None

    def _contains(self, x: int, y: int, w: int, h: int) -> bool:
        o = self._out_rect
        return o is not None and x >= o[0] and y >= o[1] and x + w <= o[0] + o[2] and y + h <= o[1] + o[3]

    @staticmethod
    def _monitor_of(x: int, y: int, w: int, h: int) -> int:
        """HMONITOR of the monitor showing the rectangle's centre (0 when unknown)."""
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            user32.MonitorFromPoint.restype = ctypes.c_void_p
            user32.MonitorFromPoint.argtypes = [wintypes.POINT, ctypes.c_uint]
            return int(user32.MonitorFromPoint(wintypes.POINT(x + w // 2, y + h // 2), 0) or 0)   # DEFAULTTONULL
        except Exception:
            return 0

    @staticmethod
    def _display_owners() -> dict[str, str]:
        """``{"\\\\.\\DISPLAY1": "Intel(R) Iris Xe Graphics"}``: the GPU that drives each GDI display
        (``EnumDisplayDevices``). On a hybrid laptop (Optimus: iGPU drives the panel, the GeForce
        only renders) both DXGI adapters may list the panel; the duplication must be created on
        the one that owns it, the other gives black frames."""
        out: dict[str, str] = {}
        try:
            import ctypes

            class DISPLAY_DEVICEW(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_ulong), ("DeviceName", ctypes.c_wchar * 32),
                            ("DeviceString", ctypes.c_wchar * 128), ("StateFlags", ctypes.c_ulong),
                            ("DeviceID", ctypes.c_wchar * 128), ("DeviceKey", ctypes.c_wchar * 128)]

            user32 = ctypes.windll.user32
            for i in range(32):
                dd = DISPLAY_DEVICEW()
                dd.cb = ctypes.sizeof(dd)
                if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
                    break
                if dd.StateFlags & 0x1:            # DISPLAY_DEVICE_ATTACHED_TO_DESKTOP
                    out[str(dd.DeviceName).strip("\x00 ")] = str(dd.DeviceString).strip("\x00 ")
        except Exception:
            pass
        return out

    def _enumerate(self, factory: Any, x: int, y: int, w: int, h: int) -> list[dict[str, Any]]:
        """Every adapter x output (COM objects released): ``stats["candidates"]`` lists them all,
        the returned ones contain the rectangle; first the output of the monitor showing it,
        on the adapter that drives that display."""
        api = _get_api()
        ctypes = api.ctypes
        mon = self._monitor_of(x, y, w, h)
        owners = self._display_owners()
        seen: list[dict[str, Any]] = []
        for ai in range(MAX_ADAPTERS):
            adapter = ctypes.c_void_p()
            hr = api.hr(factory, _FACTORY1_ENUM_ADAPTERS1, (ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)),
                        ai, ctypes.byref(adapter))
            if hr == DXGI_ERROR_NOT_FOUND or not adapter.value:
                break
            try:
                ad = api.DXGI_ADAPTER_DESC()
                try:
                    api.hr(adapter, _ADAPTER_GET_DESC, (ctypes.POINTER(api.DXGI_ADAPTER_DESC),), ctypes.byref(ad))
                    aname = str(ad.Description).strip("\x00 ")
                except Exception:
                    aname = f"adapter {ai}"
                for oi in range(MAX_OUTPUTS):
                    output = ctypes.c_void_p()
                    hr = api.hr(adapter, _ADAPTER_ENUM_OUTPUTS, (ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)),
                                oi, ctypes.byref(output))
                    if hr == DXGI_ERROR_NOT_FOUND or not output.value:
                        break
                    try:
                        desc = api.DXGI_OUTPUT_DESC()
                        api.hr(output, _OUTPUT_GET_DESC, (ctypes.POINTER(api.DXGI_OUTPUT_DESC),), ctypes.byref(desc))
                        rc = desc.DesktopCoordinates
                        orect = (int(rc.left), int(rc.top), int(rc.right - rc.left), int(rc.bottom - rc.top))
                        hmon = int(desc.Monitor or 0)
                        inside = bool(desc.AttachedToDesktop) and orect[0] <= x and orect[1] <= y \
                            and x + w <= orect[0] + orect[2] and y + h <= orect[1] + orect[3]
                        oname = str(desc.DeviceName).strip("\x00 ")
                        owner = owners.get(oname)
                        seen.append({"ai": ai, "oi": oi, "adapter": aname, "name": oname,
                                     "rect": orect, "rotation": int(desc.Rotation), "attached": bool(desc.AttachedToDesktop),
                                     "monitor": hmon, "inside": inside, "shows": bool(mon and hmon == mon),
                                     "owner": None if owner is None else _same_gpu(owner, aname)})
                    finally:
                        api.release(output)
            finally:
                api.release(adapter)
        self.stats["candidates"] = [candidate_text(c) for c in seen]
        cands = [c for c in seen if c["inside"]]
        cands.sort(key=lambda c: (not c["shows"], c["owner"] is False, c["ai"], c["oi"]))
        return cands

    def mark_bad(self, reason: str = "") -> bool:
        """The caller saw that the frames of the current output are wrong (black where GDI sees
        the game): drop this adapter x output and use the next candidate at the next grab.
        Returns True when another candidate exists (all tried -> they are cycled again)."""
        try:
            cur = self._cand
            if cur is None:
                return False
            self._bad.add((cur["ai"], cur["oi"]))
            others = [c for c in self._candidates if (c["ai"], c["oi"]) not in self._bad]
            if not others:
                self._bad.clear()
                others = [c for c in self._candidates if (c["ai"], c["oi"]) != (cur["ai"], cur["oi"])]
            self.stats["rotations"] += 1
            self._release_all()
            self._retry_at = -1e18
            log.info("DXGI output %s dropped (%s): %d other candidate(s)", candidate_text(cur), reason, len(others))
            return bool(others)
        except Exception:
            return False

    def _open(self, x: int, y: int, w: int, h: int) -> bool:
        """Create factory / device / duplication for an output showing the rectangle."""
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
            self._candidates = self._enumerate(factory, x, y, w, h)
            if not self._candidates:
                raise LookupError("rectangle not inside one monitor")
            order = [c for c in self._candidates if (c["ai"], c["oi"]) not in self._bad] or self._candidates
            errors: list[str] = []
            for cand in order:
                try:
                    self._open_candidate(cand)
                    return True
                except Exception as exc:
                    errors.append(f"{candidate_text(cand)}: {exc}")
                    for name in ("_staging", "_dupl", "_ctx", "_device", "_output", "_adapter"):
                        api.release(getattr(self, name))
                        setattr(self, name, None)
                    if self.dead and len(order) == 1:
                        raise
                    self.dead = False
            raise OSError("; ".join(errors)[:300])
        except Exception as exc:
            self.last_error = str(exc)
            self.stats["fail"] += 1
            self._retry_at = self._clock() + RETRY_AFTER_S
            log.info("DXGI capture unavailable: %s", exc)
            self._release_all()
            return False

    def _open_candidate(self, cand: dict[str, Any]) -> None:
        api = _get_api()
        ctypes = api.ctypes
        factory = self._factory
        adapter = ctypes.c_void_p()
        hr = api.hr(factory, _FACTORY1_ENUM_ADAPTERS1, (ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)),
                    cand["ai"], ctypes.byref(adapter))
        if hr != S_OK or not adapter.value:
            raise OSError(f"EnumAdapters1 0x{hr:08X}")
        self._adapter = adapter
        output = ctypes.c_void_p()
        hr = api.hr(adapter, _ADAPTER_ENUM_OUTPUTS, (ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)),
                    cand["oi"], ctypes.byref(output))
        if hr != S_OK or not output.value:
            raise OSError(f"EnumOutputs 0x{hr:08X}")
        self._output = output
        if cand["rotation"] not in (DXGI_MODE_ROTATION_UNSPECIFIED, DXGI_MODE_ROTATION_IDENTITY):
            raise OSError(f"rotated monitor ({cand['rotation']}) not supported")
        device, ctx, level = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_uint()
        hr = _u32(api.D3D11CreateDevice(adapter, D3D_DRIVER_TYPE_UNKNOWN, None, 0, None, 0,
                                        D3D11_SDK_VERSION, ctypes.byref(device), ctypes.byref(level),
                                        ctypes.byref(ctx)))
        if hr != S_OK or not device.value or not ctx.value:
            raise OSError(f"D3D11CreateDevice 0x{hr:08X}")
        self._device, self._ctx = device, ctx
        dupl, how = self._duplicate(output, device)
        self._dupl = dupl
        dd = api.DXGI_OUTDUPL_DESC()
        api.call(dupl, _DUPL_GET_DESC, None, (ctypes.POINTER(api.DXGI_OUTDUPL_DESC),), ctypes.byref(dd))
        if dd.Rotation not in (DXGI_MODE_ROTATION_UNSPECIFIED, DXGI_MODE_ROTATION_IDENTITY):
            raise OSError(f"rotated duplication ({dd.Rotation}) not supported")
        fmt = int(dd.ModeDesc.Format)
        if fmt not in FORMAT_BYTES:
            raise OSError(f"desktop format {fmt} not supported")
        self._fmt = fmt
        self._cand, self._out_rect = cand, cand["rect"]
        self._since_open = 0
        self.stats["adapter"] = cand["adapter"]
        self.stats["output"] = f"{cand['name']} {cand['rect'][2]}x{cand['rect'][3]}@{cand['rect'][0]},{cand['rect'][1]}"
        self.stats["format"] = DXGI_FORMAT_NAMES.get(fmt, str(fmt))
        self.stats["hdr"] = fmt == 10
        self.stats["chosen"] = f"{candidate_text(cand)} {how}"
        self.stats["reinit"] += 1
        self.last_error = None
        log.info("DXGI capture ready on %s (%s, %dx%d, %s, feature level 0x%X, %d candidate(s))",
                 cand["name"], cand["adapter"], dd.ModeDesc.Width, dd.ModeDesc.Height, self.stats["format"],
                 level.value, len(self._candidates))

    def _duplicate(self, output: Any, device: Any) -> tuple[Any, str]:
        """``IDXGIOutput5::DuplicateOutput1`` (asks for the 8-bit, 10-bit and HDR formats we decode:
        Windows 10 1703+), else ``IDXGIOutput1::DuplicateOutput``."""
        api = _get_api()
        ctypes = api.ctypes
        out5 = ctypes.c_void_p()
        hr = api.hr(output, _QI, (ctypes.POINTER(api.GUID), ctypes.POINTER(ctypes.c_void_p)),
                    ctypes.byref(api.IID_IDXGIOutput5), ctypes.byref(out5))
        if hr == S_OK and out5.value:
            try:
                fmts = (ctypes.c_uint * 3)(DXGI_FORMAT_B8G8R8A8_UNORM, 10, 24)
                dupl = ctypes.c_void_p()
                hr = api.hr(out5, _OUTPUT5_DUPLICATE_OUTPUT1,
                            (ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint),
                             ctypes.POINTER(ctypes.c_void_p)), device, 0, 3, fmts, ctypes.byref(dupl))
                if hr == S_OK and dupl.value:
                    return dupl, "DuplicateOutput1"
                log.debug("DuplicateOutput1 0x%08X: falling back to DuplicateOutput", hr)
            finally:
                api.release(out5)
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
        return dupl, "DuplicateOutput"

    def _ensure_staging(self, w: int, h: int, fmt: int | None = None) -> bool:
        """Staging texture of this size and format (a few sizes are kept: minimap, HUD patch,
        window). The format must be the desktop image's: a copy between formats is ignored."""
        fmt = self._fmt if fmt is None else int(fmt)
        key = (w, h, fmt)
        if self._staging is not None and self._staging_size == key:
            return True
        api = _get_api()
        ctypes = api.ctypes
        if self._staging is not None:
            self._spare[self._staging_size] = self._staging
        self._staging, self._staging_size = None, (0, 0, 0)
        hit = self._spare.pop(key, None)
        if hit is not None:
            self._staging, self._staging_size = hit, key
            return True
        while len(self._spare) >= 3:
            api.release(self._spare.pop(next(iter(self._spare))))
        desc = api.D3D11_TEXTURE2D_DESC()
        desc.Width, desc.Height, desc.MipLevels, desc.ArraySize = w, h, 1, 1
        desc.Format = fmt
        desc.SampleDesc.Count, desc.SampleDesc.Quality = 1, 0
        desc.Usage, desc.BindFlags = D3D11_USAGE_STAGING, 0
        desc.CPUAccessFlags, desc.MiscFlags = D3D11_CPU_ACCESS_READ, 0
        tex = ctypes.c_void_p()
        hr = api.hr(self._device, _DEVICE_CREATE_TEXTURE2D,
                    (ctypes.POINTER(api.D3D11_TEXTURE2D_DESC), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)),
                    ctypes.byref(desc), None, ctypes.byref(tex))
        if hr != S_OK or not tex.value:
            raise OSError(f"CreateTexture2D 0x{hr:08X}")
        self._staging, self._staging_size = tex, key
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
            first = self._since_open == 0
            got = self._acquire_copy(ox, oy, w, h, FIRST_FRAME_TIMEOUT_MS if first else GRAB_TIMEOUT_MS)
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
            mean = frame_mean(img)
            if first:
                # the desktop image of a brand-new duplication is sometimes not composed yet
                for _ in range(FIRST_BLACK_RETRIES):
                    if mean >= BLACK_MEAN:
                        break
                    self.stats["first_black"] += 1
                    if not self._acquire_copy(ox, oy, w, h, FIRST_FRAME_TIMEOUT_MS):
                        continue
                    again = self._read_staging(w, h)
                    if again is not None:
                        img, mean = again, frame_mean(again)
            self._since_open += 1
            self.stats["last_mean"] = round(mean, 1)
            if mean < BLACK_MEAN:
                self.stats["black_frames"] += 1
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
                if self._since_open == 0:
                    # the frame's real format decides the staging format (a copy between
                    # formats is silently ignored by D3D11 -> black staging)
                    td = api.D3D11_TEXTURE2D_DESC()
                    api.call(tex, _TEX2D_GET_DESC, None, (ctypes.POINTER(api.D3D11_TEXTURE2D_DESC),), ctypes.byref(td))
                    tf = int(td.Format)
                    if tf and tf != self._fmt:
                        if tf not in FORMAT_BYTES:
                            raise OSError(f"frame format {tf} not supported")
                        log.info("DXGI frame format %s (duplication said %s)", DXGI_FORMAT_NAMES.get(tf, tf),
                                 self.stats.get("format"))
                        self._fmt = tf
                        self.stats["format"] = DXGI_FORMAT_NAMES.get(tf, str(tf))
                        self.stats["hdr"] = tf == 10
                    if bool(info.ProtectedContentMaskedOut):
                        self.stats["protected"] = True
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
        return read_texture(self._ctx, self._staging, w, h, self._fmt)

    def describe(self) -> str:
        """One line for logs / reasons: output, adapter, format (``""`` before the first open)."""
        try:
            if not self.stats.get("output"):
                return ""
            return f"{self.stats['output']} / {self.stats.get('adapter') or '?'} / {self.stats.get('format') or '?'}"
        except Exception:
            return ""

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


def enumerate_displays() -> list[str]:
    """Every DXGI adapter x output of this PC as text (diagnostic bundle), e.g.
    ``a0/o0 \\\\.\\DISPLAY1 1920x1080@0,0 Intel(R) Iris Xe Graphics [owner]``; [] off Windows /
    on failure. Never raises."""
    if not available():
        return []
    cap = DxgiCapture()
    try:
        api = _get_api()
        ctypes = api.ctypes
        factory = ctypes.c_void_p()
        hr = _u32(api.CreateDXGIFactory1(ctypes.byref(api.IID_IDXGIFactory1), ctypes.byref(factory)))
        if hr != S_OK or not factory.value:
            return [f"CreateDXGIFactory1 0x{hr:08X}"]
        try:
            cap._enumerate(factory, 0, 0, 0, 0)
        finally:
            api.release(factory)
        return list(cap.stats.get("candidates") or [])
    except Exception as exc:
        return [f"{type(exc).__name__}: {exc}"]
    finally:
        cap.close()


def _same_gpu(a: str, b: str) -> bool:
    """Two GPU names (GDI DeviceString / DXGI Description) designate the same adapter."""
    norm = lambda t: "".join(ch for ch in str(t).lower() if ch.isalnum())   # noqa: E731
    na, nb = norm(a), norm(b)
    return bool(na and nb and (na == nb or na in nb or nb in na))


def candidate_text(c: dict[str, Any]) -> str:
    """``a0/o1 \\.\DISPLAY2 1920x1080@1920,0 NVIDIA GeForce RTX 3060 [shows the game]``."""
    try:
        r = c.get("rect") or (0, 0, 0, 0)
        tags = [t for t, on in (("shows", c.get("shows")), ("owner", c.get("owner")), ("inside", c.get("inside")),
                                ("detached", not c.get("attached", True))) if on]
        return f"a{c.get('ai')}/o{c.get('oi')} {c.get('name')} {r[2]}x{r[3]}@{r[0]},{r[1]} {c.get('adapter')}" \
            + (f" [{', '.join(tags)}]" if tags else "")
    except Exception:
        return str(c)


def frame_mean(img: Any) -> float:
    """Mean intensity of an image on a coarse grid (cheap; inf for invalid input)."""
    try:
        a = np.asarray(img)
        if a.ndim < 2 or a.size == 0:
            return float("inf")
        step = max(1, int(min(a.shape[0], a.shape[1]) // 48))
        return float(a[::step, ::step].mean())
    except Exception:
        return float("inf")


_SRGB_LUT: np.ndarray | None = None


def _srgb_lut() -> np.ndarray:
    """Linear [0, 1] in 4096 steps -> 8-bit sRGB."""
    global _SRGB_LUT
    if _SRGB_LUT is None:
        x = np.linspace(0.0, 1.0, 4096, dtype=np.float32)
        y = np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(np.maximum(x, 1e-6), 1 / 2.4) - 0.055)
        _SRGB_LUT = np.clip(np.round(y * 255.0), 0, 255).astype(np.uint8)
    return _SRGB_LUT


def convert_pixels(raw: np.ndarray, w: int, h: int, pitch: int, fmt: int) -> np.ndarray:
    """Decode one mapped desktop texture (``raw``: bytes of ``h`` rows of ``pitch`` bytes) of
    DXGI format ``fmt`` into a contiguous BGR uint8 image. Pure numpy / cv2, testable anywhere.

    * B8G8R8A8 / B8G8R8X8 (87, 88, 91, 93): drop the alpha;
    * R8G8B8A8 (28, 29): swap red / blue;
    * R10G10B10A2_UNORM (24): 10-bit SDR desktop, top 8 bits of each channel;
    * R16G16B16A16_FLOAT (10): HDR desktop (scRGB linear, 1.0 = SDR white of 80 nits). SDR
      windows (the game) are composed at the Windows "SDR content brightness": the brightest
      sample of the crop is taken as that white, then sRGB-encoded (tone curve close enough for
      the icons; the mss cross-check tolerates a tone-curve difference).
    """
    import cv2

    bpp = FORMAT_BYTES.get(int(fmt), 4)
    if pitch < w * bpp:
        raise OSError(f"bad row pitch {pitch}")
    if fmt == 10:
        view = np.lib.stride_tricks.as_strided(raw, shape=(h, w, 8), strides=(pitch, 8, 1))
        lin = np.ascontiguousarray(view).view(np.float16)[:, :, :3].astype(np.float32)   # R, G, B
        lin = np.nan_to_num(lin, nan=0.0, posinf=1.0, neginf=0.0)
        white = float(np.percentile(lin[::4, ::4], 99.5)) if lin.size else 1.0
        scale = min(12.5, max(1.0, white))
        idx = np.clip(lin * (4095.0 / scale), 0, 4095).astype(np.uint16)
        rgb = _srgb_lut()[idx]
        return np.ascontiguousarray(rgb[:, :, ::-1])
    view = np.lib.stride_tricks.as_strided(raw, shape=(h, w, 4), strides=(pitch, 4, 1))
    if fmt == 24:
        words = np.ascontiguousarray(view).view(np.uint32).reshape(h, w)
        out = np.empty((h, w, 3), np.uint8)
        out[:, :, 2] = (words & 0x3FF) >> 2                 # R
        out[:, :, 1] = ((words >> 10) & 0x3FF) >> 2         # G
        out[:, :, 0] = ((words >> 20) & 0x3FF) >> 2         # B
        return out
    if fmt in (28, 29):
        return cv2.cvtColor(view, cv2.COLOR_RGBA2BGR)
    return cv2.cvtColor(view, cv2.COLOR_BGRA2BGR)      # the only copy


def read_texture(ctx: Any, staging: Any, w: int, h: int, fmt: int = DXGI_FORMAT_B8G8R8A8_UNORM) -> np.ndarray | None:
    """Map a CPU-readable texture of DXGI format ``fmt`` and return its pixels as a BGR array."""
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
        bpp = FORMAT_BYTES.get(int(fmt), 4)
        if pitch < w * bpp:
            raise OSError(f"bad row pitch {pitch}")
        buf = (ctypes.c_ubyte * (pitch * (h - 1) + w * bpp)).from_address(mapped.pData)
        raw = np.frombuffer(buf, np.uint8)
        return convert_pixels(raw, w, h, pitch, int(fmt))
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
