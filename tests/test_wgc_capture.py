"""Windows.Graphics.Capture window backend: session logic with a fake COM layer, the real ctypes
vtable plumbing against fake COM objects (any OS), and its integration in SmartCapture."""

from __future__ import annotations

import ctypes
import sys
import uuid

import numpy as np
import pytest

from treeaicoach import capture as cap
from treeaicoach import dxgi_capture as dx
from treeaicoach import wgc_capture as wgc
from treeaicoach.capture import Rect, SmartCapture, capture_why


# ======================================================================================
# WgcCapture logic with a fake session
# ======================================================================================

class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


class FakeSession:
    """Stands for _ComWgc: frames are numpy images of the whole window."""

    instances: list["FakeSession"] = []

    def __init__(self, frames=None, fail_open=None, size=(800, 600)) -> None:
        self.queue = list(frames or [])
        self.fail_open = fail_open
        self.pool_size = size
        self.border, self.cursor = False, True
        self.closed_frames: list = []
        self.recreated: list = []
        self.closed = False
        self.opened_hwnd = None
        FakeSession.instances.append(self)

    def open(self, hwnd):
        if self.fail_open is not None:
            raise self.fail_open
        self.opened_hwnd = hwnd

    def try_next_frame(self):
        return self.queue.pop(0) if self.queue else None

    def content_size(self, frame):
        return frame.shape[1], frame.shape[0]

    def recreate(self, w, h):
        self.recreated.append((w, h))
        self.pool_size = (w, h)

    def copy_crop(self, frame, ox, oy, w, h):
        return frame[oy:oy + h, ox:ox + w].copy()

    def close_frame(self, frame):
        self.closed_frames.append(frame)

    def close(self):
        self.closed = True


def _img(w=800, h=600, v=0):
    a = np.zeros((h, w, 3), np.uint8)
    a[..., 0] = (np.arange(w)[None, :] + v) % 256
    a[..., 1] = (np.arange(h)[:, None] * 3) % 256
    a[..., 2] = 50 + v
    return a


def _wgc(sessions, hwnd=0x1234, bounds=(100, 50, 800, 600), clock=None):
    it = iter(sessions)
    state = {"hwnd": hwnd, "bounds": bounds, "opened": 0}

    def factory():
        state["opened"] += 1
        return next(it)
    c = wgc.WgcCapture(clock=clock or Clock(), com_factory=factory,
                       find_hwnd=lambda: state["hwnd"], bounds=lambda h: state["bounds"],
                       sleep=lambda s: None)
    return c, state


def test_crop_maps_screen_to_window_and_keeps_newest_frame():
    f1, f2 = _img(v=1), _img(v=2)
    s = FakeSession([f1, f2])
    c, _ = _wgc([s])
    out = c.grab(Rect(100 + 500, 50 + 300, 120, 100))
    assert out is not None and out.shape == (100, 120, 3)
    assert np.array_equal(out, f2[300:400, 500:620])          # newest frame, window coords
    assert len(s.closed_frames) == 1 and s.closed_frames[0] is f1
    # no new frame: same held frame serves another rectangle at once
    out2 = c.grab(Rect(110, 60, 10, 10))
    assert np.array_equal(out2, f2[10:20, 10:20])
    assert c.stats["static"] == 1 and c.stats["frames"] == 2 and c.window_only


def test_rect_outside_window_and_no_window_are_not_failures():
    s1 = FakeSession([_img()])
    c, state = _wgc([s1])
    assert c.grab(Rect(0, 0, 50, 50)) is None and c.outside and not c.dead
    assert c.stats["outside"] == 1 and c.stats["fail"] == 0
    state["hwnd"] = 0
    c._hwnd_at = -1e18
    assert c.grab(Rect(200, 200, 10, 10)) is None and c.idle
    assert s1.closed                    # session released with the window


def test_window_change_opens_new_session_and_resize_recreates_pool():
    clock = Clock()
    s1, s2 = FakeSession([_img()]), FakeSession([_img(900, 700)], size=(800, 600))
    c, state = _wgc([s1, s2], clock=clock)
    assert c.grab(Rect(150, 60, 10, 10)) is not None and s1.opened_hwnd == 0x1234
    state["hwnd"], state["bounds"] = 0x9999, (100, 50, 900, 700)
    clock.t += wgc.HWND_RECHECK_S + 0.1
    assert c.grab(Rect(150, 60, 10, 10)) is not None
    assert s1.closed and s2.opened_hwnd == 0x9999 and s2.recreated == [(900, 700)]


def test_size_mismatch_and_border_and_transient_failure():
    c, state = _wgc([FakeSession([_img()])], bounds=(0, 0, 1000, 600))
    assert c.grab(Rect(10, 10, 10, 10)) is None and c.stats["size_mismatch"] == 1
    # Windows 10: yellow border cannot be removed -> permanent, backend dropped
    c, _ = _wgc([FakeSession(fail_open=wgc.WgcUnsupported("border"))])
    assert c.grab(Rect(110, 60, 10, 10)) is None and c.dead and "border" in c.last_error
    # transient error: retried after RETRY_AFTER_S only
    clock = Clock()
    c, st = _wgc([FakeSession(fail_open=OSError("x")), FakeSession([_img()])], clock=clock)
    assert c.grab(Rect(110, 60, 10, 10)) is None and not c.dead
    assert c.grab(Rect(110, 60, 10, 10)) is None and st["opened"] == 1
    clock.t += wgc.RETRY_AFTER_S + 0.1
    assert c.grab(Rect(110, 60, 10, 10)) is not None


def test_no_first_frame_times_out_and_bad_rect():
    clock = Clock()
    c = wgc.WgcCapture(clock=clock, com_factory=lambda: FakeSession([]), find_hwnd=lambda: 1,
                       bounds=lambda h: (0, 0, 800, 600), sleep=lambda s: setattr(clock, "t", clock.t + s))
    assert c.grab(Rect(10, 10, 10, 10)) is None and not c.dead
    assert c.grab(object()) is None and c.grab(Rect(0, 0, 0, 5)) is None


def test_available_false_off_windows():
    if sys.platform != "win32":
        assert wgc.available() is False


# ======================================================================================
# Real _ComWgc ctypes plumbing against fake COM objects
# ======================================================================================

_PROTO = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)


class ComWorld:
    """Fake COM objects with real vtables; records the calls the code makes."""

    def __init__(self, api, frames) -> None:
        self.api, self.keep, self.calls = api, [], []
        self.released: dict[int, int] = {}
        self.frames = list(frames)
        self.copies: list = []
        self.mapped = None
        self.tex_img: dict[int, np.ndarray] = {}
        self.objs: dict[str, int] = {}

    def obj(self, name, methods, qi=None):
        """methods: slot -> (argtypes, func(*args) -> hresult) (restype c_long unless given)."""
        vt = (ctypes.c_void_p * 60)()
        qi = dict(qi or {})

        def _qi(this, riid, out):
            g = ctypes.cast(riid, ctypes.POINTER(self.api.GUID))[0]
            d4 = bytes(g.Data4)       # (fields: c_ulong is 8 bytes off Windows)
            key = str(uuid.UUID(fields=(g.Data1, g.Data2, g.Data3, d4[0], d4[1], int.from_bytes(d4[2:], "big"))))
            target = next((v for k, v in qi.items() if wgc.IIDS[k] == key), None)
            if target is None:
                ctypes.cast(out, ctypes.POINTER(ctypes.c_void_p))[0] = None
                return 0x80004002 - (1 << 32)
            ctypes.cast(out, ctypes.POINTER(ctypes.c_void_p))[0] = self.objs[target]
            return 0

        def _release(this):
            self.released[name] = self.released.get(name, 0) + 1
            return 0

        base = {0: (ctypes.c_long, (ctypes.c_void_p, ctypes.c_void_p), _qi),
                1: (ctypes.c_ulong, (), lambda this: 1), 2: (ctypes.c_ulong, (), _release)}
        for slot, spec in methods.items():
            argtypes, fn = spec[0], spec[1]
            restype = spec[2] if len(spec) > 2 else ctypes.c_long

            def wrap(fn=fn, slot=slot):
                def f(this, *a):
                    self.calls.append((name, slot))
                    r = fn(*a)
                    return 0 if r is None else r
                return f
            base[slot] = (restype, argtypes, wrap())
        for slot, (restype, argtypes, fn) in base.items():
            cb = _PROTO(restype, ctypes.c_void_p, *argtypes)(fn)
            self.keep.append(cb)
            vt[slot] = ctypes.cast(cb, ctypes.c_void_p)
        holder = (ctypes.c_void_p * 1)(ctypes.addressof(vt))
        self.keep += [vt, holder]
        self.objs[name] = ctypes.addressof(holder)
        return self.objs[name]


def _out(ptr, value):
    ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0] = value


def _build(monkeypatch, frames, border_ok=True):
    if not hasattr(ctypes, "WINFUNCTYPE"):
        monkeypatch.setattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, raising=False)
    api = dx._Api(load_dlls=False)
    w = ComWorld(api, frames)
    VP, U = ctypes.c_void_p, ctypes.c_uint
    com = None

    class Size(ctypes.Structure):
        _fields_ = [("Width", ctypes.c_int32), ("Height", ctypes.c_int32)]

    def set_size(p, wh):
        s = ctypes.cast(p, ctypes.POINTER(Size))[0]
        s.Width, s.Height = wh

    def create_tex(desc, init, out):
        d = ctypes.cast(desc, ctypes.POINTER(api.D3D11_TEXTURE2D_DESC))[0]
        w.staging_desc = (d.Width, d.Height, d.Format, d.Usage, d.CPUAccessFlags)
        _out(out, w.objs["staging"])

    def copy(dst, dsub, x, y, z, src, ssub, box):
        b = ctypes.cast(box, ctypes.POINTER(api.D3D11_BOX))[0]
        w.copies.append((dst, src, b.left, b.top, b.right, b.bottom))
        img = w.tex_img[src]
        crop = img[b.top:b.bottom, b.left:b.right]
        bgra = np.dstack([crop, np.full(crop.shape[:2], 255, np.uint8)])
        w.mapped = np.ascontiguousarray(bgra)

    def map_(res, sub, mt, fl, out):
        m = ctypes.cast(out, ctypes.POINTER(api.D3D11_MAPPED_SUBRESOURCE))[0]
        m.pData = w.mapped.ctypes.data
        m.RowPitch = w.mapped.shape[1] * 4

    w.obj("staging", {})
    w.obj("dxgidev", {})
    w.obj("device", {5: ((VP, VP, VP), create_tex)}, qi={"IDXGIDevice": "dxgidev"})
    w.obj("ctx", {46: ((VP, U, U, U, U, VP, U, VP), copy, None), 14: ((VP, U, U, U, VP), map_),
                  15: ((VP, U), lambda r, s: None, None)})
    w.obj("rtdev", {})
    w.obj("insp", {}, qi={"IDirect3DDevice": "rtdev"})
    w.obj("closable", {6: ((), lambda: None)})
    w.obj("session2", {7: ((ctypes.c_ubyte,), lambda v: w.calls.append(("cursor", v)) and None)})
    w.obj("session3", {7: ((ctypes.c_ubyte,), lambda v: 0 if border_ok else 0x80070005 - (1 << 32))})
    w.obj("session", {6: ((), lambda: None)},
          qi={"IGraphicsCaptureSession2": "session2", "IGraphicsCaptureSession3": "session3",
              "IClosable": "closable"})
    w.obj("item", {7: ((VP,), lambda p: set_size(p, (800, 600)))})
    w.obj("interop", {3: ((VP, VP, VP), lambda h, iid, out: (w.calls.append(("hwnd", h)), _out(out, w.objs["item"]))[-1])})
    # frames: texture -> image
    for i, img in enumerate(frames):
        tex = w.obj(f"tex{i}", {})
        w.tex_img[tex] = img
        w.obj(f"access{i}", {3: ((VP, VP), lambda iid, out, t=tex: _out(out, t))})
        w.obj(f"surface{i}", {}, qi={"IDirect3DDxgiInterfaceAccess": f"access{i}"})
        w.obj(f"frame{i}", {6: ((VP,), lambda out, i=i: _out(out, w.objs[f"surface{i}"])),
                            8: ((VP,), lambda p, im=img: set_size(p, (im.shape[1], im.shape[0])))},
              qi={"IClosable": "closable"})
    pending = [f"frame{i}" for i in range(len(frames))]

    def next_frame(out):
        _out(out, w.objs[pending.pop(0)] if pending else None)

    w.obj("pool", {6: ((VP, ctypes.c_int32, ctypes.c_int32, Size), lambda d, f, n, s: w.calls.append(("recreate", s.Width, s.Height)) and None),
                   7: ((VP,), next_frame),
                   10: ((VP, VP), lambda item, out: _out(out, w.objs["session"]))}, qi={"IClosable": "closable"})

    def free_threaded(dev, fmt, n, size, out):
        w.calls.append(("pool", dev, fmt, n, size.Width, size.Height))
        _out(out, w.objs["pool"])

    w.obj("statics2", {6: ((VP, ctypes.c_int32, ctypes.c_int32, Size, VP), free_threaded)})
    w.obj("sstatics", {6: ((VP,), lambda p: ctypes.cast(p, ctypes.POINTER(ctypes.c_ubyte)).__setitem__(0, 1))})

    def d3d_create(adapter, dtype, soft, flags, levels, nlev, sdk, dev, lvl, ctx):
        w.calls.append(("D3D11CreateDevice", dtype, flags))
        dev._obj.value, ctx._obj.value = w.objs["device"], w.objs["ctx"]
        return 0

    api.D3D11CreateDevice = d3d_create

    class Fn:
        def __init__(self, f):
            self.f = f

        def __call__(self, *a):
            return self.f(*a)

    strings: dict[int, str] = {}

    def create_string(s, n, out):
        strings[id(s)] = s
        out._obj.value = 1000 + len(strings)
        strings[out._obj.value] = s
        return 0

    classes = {wgc.CLASS_ITEM: "interop", wgc.CLASS_POOL: "statics2", wgc.CLASS_SESSION: "sstatics"}

    def get_factory(hs, iid, out):
        out._obj.value = w.objs[classes[strings[hs.value]]]
        return 0

    combase = type("C", (), {})()
    combase.RoInitialize = Fn(lambda m: 1)
    combase.WindowsCreateString = Fn(create_string)
    combase.WindowsDeleteString = Fn(lambda h: 0)
    combase.RoGetActivationFactory = Fn(get_factory)

    def from_dxgi(d, out):
        assert d.value == w.objs["dxgidev"]
        out._obj.value = w.objs["insp"]
        return 0

    d3d11 = type("D", (), {})()
    d3d11.CreateDirect3D11DeviceFromDXGIDevice = Fn(from_dxgi)
    com = wgc._ComWgc(api=api, dlls={"combase": combase, "d3d11": d3d11})
    monkeypatch.setattr(dx, "_api", api)
    monkeypatch.setattr(dx, "_get_api", lambda: api)
    return w, com


def test_ctypes_session_pipeline_on_fake_com(monkeypatch):
    f1, f2 = _img(v=3), _img(v=9)
    w, com = _build(monkeypatch, [f1, f2])
    assert com.is_supported() is True
    com.open(0x4242)
    assert ("hwnd", 0x4242) in w.calls
    assert ("pool", w.objs["rtdev"], 87, wgc.POOL_BUFFERS, 800, 600) in w.calls
    assert ("D3D11CreateDevice", 1, wgc.D3D11_CREATE_DEVICE_BGRA_SUPPORT) in w.calls
    assert com.border is False and com.cursor is True and ("cursor", 0) in w.calls
    assert ("session", 6) in w.calls                            # StartCapture
    c = wgc.WgcCapture(com_factory=lambda: com, find_hwnd=lambda: 0x4242,
                       bounds=lambda h: (10, 20, 800, 600), sleep=lambda s: None)
    c._com = com                                                # already opened above
    out = c.grab(Rect(10 + 30, 20 + 40, 64, 48))
    assert out is not None and np.array_equal(out, f2[40:88, 30:94])
    assert w.staging_desc == (64, 48, 87, 3, 0x20000)
    assert w.released.get("frame0", 0) >= 1 and ("closable", 6) in w.calls   # older frame closed
    com.recreate(1024, 768)
    assert ("recreate", 1024, 768) in w.calls and com.pool_size == (1024, 768)
    c.close()
    for name in ("session", "pool", "item", "staging", "rtdev", "ctx", "device", "frame1"):
        assert w.released.get(name, 0) >= 1, name


def test_ctypes_border_refused_on_windows10(monkeypatch):
    w, com = _build(monkeypatch, [], border_ok=False)
    with pytest.raises(wgc.WgcUnsupported):
        com.open(1)
    assert ("session", 6) not in w.calls                        # never started: no yellow frame
    w2, com2 = _build(monkeypatch, [], border_ok=False)
    com2.open(1, allow_border=True)
    assert com2.border is True


# ======================================================================================
# SmartCapture integration
# ======================================================================================

class Backend:
    def __init__(self, img=None, idle=False):
        self.img, self.idle, self.outside, self.dead = img, idle, False, False
        self.calls = 0
        self.stats = {}

    def grab(self, rect, pad=True):
        self.calls += 1
        return None if self.img is None else self.img.copy()

    def close(self):
        pass


def _smart(wgc_b, dxgi_b, mss_b, backend="auto"):
    f = {"wgc": lambda: wgc_b, "dxgi": lambda: dxgi_b, "mss": lambda: mss_b}
    return SmartCapture(backend, factories=f, clock=Clock(), platform="win32")


def test_smart_capture_prefers_wgc_and_reports_window_only():
    img = _img(64, 64)
    s = _smart(Backend(img), Backend(img), Backend(img))
    assert s.order == ["wgc", "dxgi", "mss"]
    assert s.grab(Rect(0, 0, 64, 64)) is not None
    assert s.current == "wgc" and s.window_only and s.timings()["window_only"] is True
    assert capture_why(s.timings()).startswith("rapide (fenêtre du jeu)")
    assert _smart(Backend(img), Backend(img), Backend(img), "dxgi").order == ["dxgi", "wgc", "mss"]
    assert _smart(Backend(img), Backend(img), Backend(img), "mss").order == ["mss", "dxgi"]
    assert SmartCapture("auto", factories={"wgc": Backend, "dxgi": Backend, "mss": Backend},
                        platform="linux").order == ["mss"]


def test_smart_capture_wgc_mismatch_suspends_then_dxgi(monkeypatch):
    monkeypatch.setattr(cap, "rect_occluded", lambda r: False)
    rng = np.random.default_rng(1)
    a = rng.integers(30, 255, (64, 64, 3), dtype=np.uint8)
    b = rng.integers(30, 255, (64, 64, 3), dtype=np.uint8)
    s = _smart(Backend(b), Backend(a), Backend(a))
    out = s.grab(Rect(0, 0, 64, 64))
    assert np.array_equal(out, a) and "wgc" in s.stats["suspended"]
    assert not s.window_only and s.current == "dxgi"


def test_smart_capture_wgc_mismatch_under_covering_window_not_judged(monkeypatch):
    monkeypatch.setattr(cap, "rect_occluded", lambda r: True)
    rng = np.random.default_rng(2)
    a = rng.integers(30, 255, (64, 64, 3), dtype=np.uint8)
    b = rng.integers(30, 255, (64, 64, 3), dtype=np.uint8)
    s = _smart(Backend(b), Backend(a), Backend(a))
    out = s.grab(Rect(0, 0, 64, 64))
    assert np.array_equal(out, b) and s.current == "wgc" and not s.stats.get("suspended")
    assert not s.window_only                                   # not validated yet


def test_smart_capture_idle_wgc_falls_back_without_switching():
    img = _img(64, 64)
    w = Backend(None, idle=True)
    s = _smart(w, Backend(img), Backend(img))
    for _ in range(cap.FAIL_SWITCH + 3):
        assert s.grab(Rect(0, 0, 64, 64)) is not None
    assert s.current == "wgc" and s.stats["fallback_grabs"] == cap.FAIL_SWITCH + 3
    w.idle, w.img = False, img                                  # game window appears
    s.grab(Rect(0, 0, 64, 64))
    assert s.window_only


def test_smart_capture_dead_wgc_is_disabled():
    img = _img(64, 64)
    w = Backend(None)
    w.dead, w.last_error = True, "WgcUnsupported: border"
    s = _smart(w, Backend(img), Backend(img))
    assert s.grab(Rect(0, 0, 64, 64)) is not None
    assert "wgc" in s.stats["disabled"] and s.current == "dxgi"
    assert capture_why(s.timings()).startswith("rapide (Desktop Duplication)")


def test_border_option_passed_to_session():
    """Option capture_wgc_border (Windows 10): the session is opened accepting the yellow frame;
    off by default (the session then refuses it and DXGI is used)."""
    seen = []

    class Com:
        border = True
        pool_size = (10, 10)

        def open(self, hwnd, allow_border=False):
            seen.append(allow_border)

    c = wgc.WgcCapture(com_factory=Com, find_hwnd=lambda: 7)
    assert c.allow_border is False
    assert c._open(7) and seen == [False]     # default call: open(hwnd), border refused inside
    c2 = wgc.WgcCapture(com_factory=Com, find_hwnd=lambda: 7, allow_border=True)
    assert c2._open(7) and seen == [False, True]
