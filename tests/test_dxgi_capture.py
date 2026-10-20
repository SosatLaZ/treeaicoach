"""DXGI Desktop Duplication backend with a fake COM layer (no Windows needed): adapter x
output enumeration and ranking (the GPU driving the display first), black outputs dropped for
the next candidate, desktop formats (BGRA8 / RGBA8 / 10-bit / HDR float), DuplicateOutput1
fallback, first-frame retries, stats; SmartCapture's use of it (cross-check -> mark_bad,
reasons naming the output); capture_why texts; the "Capture d'écran" row of the Système panel.
"""

from __future__ import annotations

import ctypes
import sys

import numpy as np
import pytest

from treeaicoach import capture as cap
from treeaicoach import dxgi_capture as dx
from treeaicoach.capture import Rect, SmartCapture

S_OK, NOT_FOUND, E_NOTIMPL, WAIT_TIMEOUT = 0, dx.DXGI_ERROR_NOT_FOUND, dx.E_NOTIMPL, dx.DXGI_ERROR_WAIT_TIMEOUT
RECT_1080 = (0, 0, 1920, 1080)


def desktop(seed: int, w: int = 1920, h: int = 1080) -> np.ndarray:
    """A fake desktop, BGRA uint8 (h, w, 4)."""
    img = np.random.default_rng(seed).integers(20, 230, (h, w, 4), dtype=np.uint8)
    img[..., 3] = 255
    return img


class FakeApi(dx._Api):
    """``_Api`` with the real ctypes structures and faked COM calls.

    ``adapters``: list of (name, [outputs]) ; an output = dict(name, rect, desktop (BGRA array or
    None = black frames), fmt (DXGI format of the duplication), out5 (DuplicateOutput1 works)).
    """

    def __init__(self, adapters: list[tuple[str, list[dict]]]) -> None:
        super().__init__(load_dlls=False)
        self.adapters = adapters
        self.calls: list[tuple] = []
        self.released: list[int] = []
        self.staging: dict[int, tuple[int, int, int]] = {}
        self.staging_buf: dict[int, ctypes.Array] = {}
        self.cur_output: dict | None = None
        self.acquires = 0
        self.timeouts_first = 0            # AcquireNextFrame timeouts before the first frame
        self.frame_fmt: int | None = None  # format of the acquired texture (None = duplication's)
        self.protected = False

    # entry points
    def CreateDXGIFactory1(self, _iid, pfactory):
        pfactory._obj.value = 1
        return S_OK

    def D3D11CreateDevice(self, adapter, *args):
        pdev, plevel, pctx = args[-3], args[-2], args[-1]
        pdev._obj.value, plevel._obj.value, pctx._obj.value = 300, 0xB000, 301
        self.calls.append(("D3D11CreateDevice", int(adapter.value if hasattr(adapter, "value") else adapter)))
        return S_OK

    def release(self, obj):
        v = obj.value if isinstance(obj, ctypes.c_void_p) else obj
        if v:
            self.released.append(int(v))

    def _ptr(self, obj) -> int:
        return int(obj.value if isinstance(obj, ctypes.c_void_p) else (obj or 0))

    def hr(self, obj, index, argtypes, *args):
        return dx._u32(self.call(obj, index, ctypes.c_long, argtypes, *args))

    def call(self, obj, index, restype, argtypes, *args):
        p = self._ptr(obj)
        self.calls.append((p, index))
        if p == 1 and index == dx._FACTORY1_ENUM_ADAPTERS1:
            ai, pad = int(args[0]), args[1]
            if ai >= len(self.adapters):
                return NOT_FOUND
            pad._obj.value = 100 + ai
            return S_OK
        if 100 <= p < 200:
            ai = p - 100
            if index == dx._ADAPTER_GET_DESC:
                args[0]._obj.Description = self.adapters[ai][0]
                return S_OK
            if index == dx._ADAPTER_ENUM_OUTPUTS:
                oi, pout = int(args[0]), args[1]
                if oi >= len(self.adapters[ai][1]):
                    return NOT_FOUND
                pout._obj.value = 200 + ai * 10 + oi
                return S_OK
        if 200 <= p < 300:
            ai, oi = (p - 200) // 10, (p - 200) % 10
            out = self.adapters[ai][1][oi]
            if index == dx._OUTPUT_GET_DESC:
                d = args[0]._obj
                d.DeviceName = out["name"]
                r = out.get("rect", RECT_1080)
                d.DesktopCoordinates.left, d.DesktopCoordinates.top = r[0], r[1]
                d.DesktopCoordinates.right, d.DesktopCoordinates.bottom = r[0] + r[2], r[1] + r[3]
                d.AttachedToDesktop = 1
                d.Rotation = dx.DXGI_MODE_ROTATION_IDENTITY
                d.Monitor = out.get("monitor", 7)
                return S_OK
            if index == dx._QI:
                args[1]._obj.value = p
                return S_OK
            if index == dx._OUTPUT5_DUPLICATE_OUTPUT1:
                if not out.get("out5", True):
                    return E_NOTIMPL
                self.cur_output = out
                args[4]._obj.value = 400 + ai * 10 + oi
                return S_OK
            if index == dx._OUTPUT1_DUPLICATE_OUTPUT:
                self.cur_output = out
                args[1]._obj.value = 400 + ai * 10 + oi
                return S_OK
        if 400 <= p < 500:
            if index == dx._DUPL_GET_DESC:
                d = args[0]._obj
                d.ModeDesc.Width, d.ModeDesc.Height = 1920, 1080
                d.ModeDesc.Format = self.cur_output.get("fmt", dx.DXGI_FORMAT_B8G8R8A8_UNORM)
                d.Rotation = dx.DXGI_MODE_ROTATION_IDENTITY
                return None
            if index == dx._DUPL_ACQUIRE_NEXT_FRAME:
                self.acquires += 1
                if self.timeouts_first > 0:
                    self.timeouts_first -= 1
                    return WAIT_TIMEOUT
                args[1]._obj.ProtectedContentMaskedOut = 1 if self.protected else 0
                args[2]._obj.value = 500
                return S_OK
            if index == dx._DUPL_RELEASE_FRAME:
                return S_OK
        if p == 500 and index == dx._QI:
            args[1]._obj.value = 501
            return S_OK
        if p == 501 and index == dx._TEX2D_GET_DESC:
            args[0]._obj.Format = self.frame_fmt or self.cur_output.get("fmt", dx.DXGI_FORMAT_B8G8R8A8_UNORM)
            return None
        if p == 300 and index == dx._DEVICE_CREATE_TEXTURE2D:
            d = args[0]._obj
            sid = 600 + len(self.staging)
            self.staging[sid] = (int(d.Width), int(d.Height), int(d.Format))
            args[2]._obj.value = sid
            return S_OK
        if p == 301 and index == dx._CTX_COPY_SUBRESOURCE_REGION:
            dst, box = self._ptr(args[0]), args[7]._obj
            w, h, fmt = self.staging[dst]
            bpp = dx.FORMAT_BYTES[fmt]
            pitch = w * bpp + 32
            buf = (ctypes.c_ubyte * (pitch * h))()
            img = self.cur_output.get("desktop")
            if img is not None:
                crop = img[box.top:box.bottom, box.left:box.right]
                rows = np.frombuffer(buf, np.uint8).reshape(h, pitch)
                if fmt == 10:   # HDR: scRGB linear float16, SDR white at 2.0 (= 160 nits)
                    lin = (crop[..., [2, 1, 0, 3]].astype(np.float32) / 255.0) ** 2.2 * 2.0
                    lin[..., 3] = 1.0
                    rows[:, :w * 8] = np.ascontiguousarray(lin.astype(np.float16)).view(np.uint8).reshape(h, w * 8)
                elif fmt == 24:
                    r, g, b = (crop[..., 2].astype(np.uint32) << 2, crop[..., 1].astype(np.uint32) << 2,
                               crop[..., 0].astype(np.uint32) << 2)
                    words = r | (g << 10) | (b << 20) | (3 << 30)
                    rows[:, :w * 4] = np.ascontiguousarray(words.astype(np.uint32)).view(np.uint8).reshape(h, w * 4)
                elif fmt in (28, 29):
                    rows[:, :w * 4] = np.ascontiguousarray(crop[..., [2, 1, 0, 3]]).reshape(h, w * 4)
                else:
                    rows[:, :w * 4] = np.ascontiguousarray(crop).reshape(h, w * 4)
            self.staging_buf[dst] = buf
            self.calls.append(("copy", self.cur_output["name"], (box.left, box.top, box.right, box.bottom)))
            return None
        if p == 301 and index == dx._CTX_MAP:
            sid = self._ptr(args[0])
            w, h, fmt = self.staging[sid]
            m = args[4]._obj
            m.pData = ctypes.addressof(self.staging_buf[sid])
            m.RowPitch = w * dx.FORMAT_BYTES[fmt] + 32
            return S_OK
        if p == 301 and index == dx._CTX_UNMAP:
            return None
        raise AssertionError(f"unexpected COM call object {p} index {index}")


@pytest.fixture
def win(monkeypatch):
    """Pretend to be on Windows for the backend (the fake API replaces every DLL call)."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(dx.DxgiCapture, "_monitor_of", staticmethod(lambda x, y, w, h: 7))
    monkeypatch.setattr(dx.DxgiCapture, "_display_owners", staticmethod(lambda: {}))
    yield monkeypatch
    monkeypatch.setattr(dx, "_api", None)


def install(monkeypatch, adapters) -> FakeApi:
    api = FakeApi(adapters)
    monkeypatch.setattr(dx, "_api", api)
    return api


def expected(img: np.ndarray, r: Rect) -> np.ndarray:
    return np.ascontiguousarray(img[r.y:r.y + r.h, r.x:r.x + r.w, :3])


R = Rect(1500, 700, 300, 300)


# ---------------------------------------------------------------------------------- decoders
def test_convert_pixels_every_format():
    w, h = 7, 3
    pitch = w * 4 + 8
    raw = np.zeros(pitch * h, np.uint8)
    v = np.lib.stride_tricks.as_strided(raw, (h, w, 4), (pitch, 4, 1))
    v[..., 0], v[..., 1], v[..., 2], v[..., 3] = 10, 20, 30, 255
    assert tuple(dx.convert_pixels(raw, w, h, pitch, 87)[1, 1]) == (10, 20, 30)
    assert tuple(dx.convert_pixels(raw, w, h, pitch, 91)[1, 1]) == (10, 20, 30)
    assert tuple(dx.convert_pixels(raw, w, h, pitch, 28)[1, 1]) == (30, 20, 10)
    words = np.full((h, w), (1023 << 20) | (512 << 10) | 4, np.uint32)
    raw10 = np.zeros(pitch * h, np.uint8)
    np.lib.stride_tricks.as_strided(raw10, (h, w, 4), (pitch, 4, 1))[...] = words.view(np.uint8).reshape(h, w, 4)
    assert tuple(dx.convert_pixels(raw10, w, h, pitch, 24)[0, 0]) == (255, 128, 1)
    pitch16 = w * 8 + 16
    raw16 = np.zeros(pitch16 * h, np.uint8)
    f16 = raw16.view(np.float16).reshape(h, pitch16 // 2)[:, :w * 4].reshape(h, w, 4)
    f16[..., 0], f16[..., 1], f16[..., 2], f16[..., 3] = 2.0, 0.214 * 2.0, 0.0, 1.0     # SDR white = 2.0
    out = dx.convert_pixels(raw16, w, h, pitch16, 10)
    assert out.shape == (h, w, 3) and out.dtype == np.uint8
    assert out[0, 0][2] == 255 and abs(int(out[0, 0][1]) - 128) <= 2 and out[0, 0][0] == 0
    with pytest.raises(OSError):
        dx.convert_pixels(raw, w, h, 3, 87)


def test_frame_mean_and_helpers():
    assert dx.frame_mean(np.zeros((50, 50, 3), np.uint8)) == 0.0
    assert dx.frame_mean(np.full((50, 50, 3), 200, np.uint8)) == 200.0
    assert dx.frame_mean(None) == float("inf")
    assert dx._same_gpu("NVIDIA GeForce GTX 1650", "NVIDIA GeForce GTX 1650")
    assert dx._same_gpu("Intel(R) UHD Graphics", "Intel(R) UHD Graphics 630")
    assert not dx._same_gpu("Intel(R) UHD Graphics", "NVIDIA GeForce GTX 1650")
    txt = dx.candidate_text({"ai": 1, "oi": 0, "name": r"\\.\DISPLAY1", "rect": RECT_1080, "adapter": "Intel",
                             "shows": True, "owner": True, "inside": True, "attached": True})
    assert txt == r"a1/o0 \\.\DISPLAY1 1920x1080@0,0 Intel [shows, owner, inside]"
    assert dx.enumerate_displays() == [] if sys.platform != "win32" else True


# ---------------------------------------------------------------------------------- backend
def test_grab_on_a_single_output(win):
    img = desktop(1)
    api = install(win, [("NVIDIA GeForce GTX 1650", [{"name": r"\\.\DISPLAY1", "desktop": img}])])
    c = dx.DxgiCapture()
    out = c.grab(R)
    assert out is not None and np.array_equal(out, expected(img, R))
    assert c.stats["format"] == "B8G8R8A8_UNORM" and c.stats["hdr"] is False
    assert c.stats["adapter"] == "NVIDIA GeForce GTX 1650" and c.stats["output"].startswith(r"\\.\DISPLAY1 1920x1080@0,0")
    assert "DuplicateOutput1" in c.stats["chosen"] and "[shows, inside]" in c.stats["chosen"]
    assert c.stats["frames"] == 1 and c.stats["black_frames"] == 0 and c.stats["last_mean"] > 20
    assert ("D3D11CreateDevice", 100) in api.calls        # the device is made on the output's adapter
    assert api.calls[-1][0] == "copy" or ("copy", r"\\.\DISPLAY1", (1500, 700, 1800, 1000)) in api.calls
    assert c.describe().startswith(r"\\.\DISPLAY1 1920x1080@0,0 / NVIDIA")
    # static desktop: the cached image of the same rectangle is returned
    api.timeouts_first = 1
    again = c.grab(R)
    assert again is not None and np.array_equal(again, out) and c.stats["static"] == 1
    c.close()


def test_hybrid_laptop_prefers_the_gpu_driving_the_display(win):
    """Optimus: both adapters list the panel; the duplication must be made on the iGPU that
    owns it (EnumDisplayDevices), not on the GeForce listed first."""
    img = desktop(2)
    win.setattr(dx.DxgiCapture, "_display_owners", staticmethod(lambda: {r"\\.\DISPLAY1": "Intel(R) UHD Graphics 630"}))
    api = install(win, [("NVIDIA GeForce GTX 1650", [{"name": r"\\.\DISPLAY1", "desktop": None}]),
                        ("Intel(R) UHD Graphics 630", [{"name": r"\\.\DISPLAY1", "desktop": img}])])
    c = dx.DxgiCapture()
    out = c.grab(R)
    assert out is not None and np.array_equal(out, expected(img, R))
    assert c.stats["adapter"] == "Intel(R) UHD Graphics 630" and "owner" in c.stats["chosen"]
    assert ("D3D11CreateDevice", 101) in api.calls and ("D3D11CreateDevice", 100) not in api.calls
    assert len(c.stats["candidates"]) == 2 and all(r"\\.\DISPLAY1" in t for t in c.stats["candidates"])


def test_black_output_is_dropped_for_the_next_candidate(win):
    """Without the owner hint the GeForce comes first and gives black frames: the first frame
    is re-acquired a few times, then the caller's mark_bad() moves to the Intel output."""
    img = desktop(3)
    api = install(win, [("NVIDIA GeForce GTX 1650", [{"name": r"\\.\DISPLAY1", "desktop": None}]),
                        ("Intel(R) UHD Graphics 630", [{"name": r"\\.\DISPLAY1", "desktop": img}])])
    c = dx.DxgiCapture()
    out = c.grab(R)
    assert out is not None and float(out.mean()) == 0.0
    assert c.stats["adapter"].startswith("NVIDIA") and c.stats["black_frames"] == 1
    assert c.stats["first_black"] == dx.FIRST_BLACK_RETRIES and api.acquires == 1 + dx.FIRST_BLACK_RETRIES
    assert c.mark_bad("black") is True and c.stats["rotations"] == 1
    out = c.grab(R)
    assert out is not None and np.array_equal(out, expected(img, R))
    assert c.stats["adapter"].startswith("Intel") and c.stats["reinit"] == 2
    # the dropped output stays dropped across a close() (suspension) ...
    c.close()
    assert c.grab(R) is not None and c.stats["adapter"].startswith("Intel")
    # ... and when every candidate failed they are cycled again
    assert c.mark_bad("black") is True
    assert c.grab(R) is not None and c.stats["adapter"].startswith("NVIDIA")


def test_single_candidate_mark_bad_reports_no_alternative(win):
    install(win, [("GPU", [{"name": r"\\.\DISPLAY1", "desktop": None}])])
    c = dx.DxgiCapture()
    assert c.grab(R) is not None
    assert c.mark_bad("black") is False and c.stats["rotations"] == 1
    assert c.grab(R) is not None and c.stats["reinit"] == 2     # re-opened on the same output
    assert dx.DxgiCapture().mark_bad() is False                 # never opened


@pytest.mark.parametrize("fmt, name", [(10, "R16G16B16A16_FLOAT"), (24, "R10G10B10A2_UNORM"), (28, "R8G8B8A8_UNORM")])
def test_desktop_formats_are_decoded_with_a_matching_staging(win, fmt, name):
    img = desktop(4)
    api = install(win, [("GPU", [{"name": r"\\.\DISPLAY1", "desktop": img, "fmt": fmt}])])
    c = dx.DxgiCapture()
    out = c.grab(R)
    assert out is not None and out.shape == (300, 300, 3)
    want = expected(img, R).astype(np.int16)
    if fmt == 10:     # HDR: the SDR white is estimated from the crop (tone offset), the picture is the same
        assert np.abs(out.astype(np.int16) - want).mean() < 25
        assert np.corrcoef(out.ravel().astype(float), want.ravel().astype(float))[0, 1] > 0.99
    else:
        assert np.abs(out.astype(np.int16) - want).mean() <= 1
    assert c.stats["format"] == name and c.stats["hdr"] is (fmt == 10)
    assert list(api.staging.values()) == [(300, 300, fmt)]


def test_frame_format_overrides_the_duplication_desc(win):
    """The acquired texture says HDR although GetDesc said BGRA8: the staging follows the frame
    (a copy between formats is ignored by D3D11 and leaves the staging black)."""
    img = desktop(5)
    api = install(win, [("GPU", [{"name": r"\\.\DISPLAY1", "desktop": img}])])
    api.frame_fmt = 10
    c = dx.DxgiCapture()
    out = c.grab(R)
    assert out is not None and c.stats["format"] == "R16G16B16A16_FLOAT" and c.stats["hdr"] is True
    assert list(api.staging.values()) == [(300, 300, 10)]
    assert np.corrcoef(out.ravel().astype(float), expected(img, R).ravel().astype(float))[0, 1] > 0.99


def test_unsupported_desktop_format_fails_cleanly(win):
    install(win, [("GPU", [{"name": r"\\.\DISPLAY1", "desktop": desktop(6), "fmt": 2}])])
    c = dx.DxgiCapture()
    assert c.grab(R) is None and "format 2 not supported" in str(c.last_error) and c.stats["fail"] == 1


def test_no_duplication_on_any_output_marks_the_backend_dead(win):
    """Wine / Remote Desktop: DuplicateOutput E_NOTIMPL on every candidate -> dead (never retried
    by SmartCapture); one candidate failing for another reason keeps it alive."""
    api = install(win, [("A", [{"name": r"\.\DISPLAY1", "desktop": None, "out5": False}]),
                        ("B", [{"name": r"\.\DISPLAY1", "desktop": None, "out5": False}])])
    real_call = api.call

    def call(obj, index, restype, argtypes, *args):
        if index == dx._OUTPUT1_DUPLICATE_OUTPUT:
            return E_NOTIMPL
        return real_call(obj, index, restype, argtypes, *args)

    api.call = call
    c = dx.DxgiCapture()
    assert c.grab(R) is None and c.dead is True and "DuplicateOutput 0x80004001" in str(c.last_error)

    def call2(obj, index, restype, argtypes, *args):
        if index == dx._OUTPUT1_DUPLICATE_OUTPUT and api._ptr(obj) == 200:
            return E_NOTIMPL
        return real_call(obj, index, restype, argtypes, *args)

    api.call = call2
    c2 = dx.DxgiCapture()
    assert c2.grab(R) is not None and c2.dead is False and c2.stats["adapter"] == "B"


def test_duplicate_output1_falls_back_to_duplicate_output(win):
    img = desktop(7)
    install(win, [("GPU", [{"name": r"\\.\DISPLAY1", "desktop": img, "out5": False}])])
    c = dx.DxgiCapture()
    out = c.grab(R)
    assert out is not None and np.array_equal(out, expected(img, R))
    assert c.stats["chosen"].endswith(" DuplicateOutput")


def test_rectangle_on_another_monitor_or_outside(win):
    img = desktop(8)
    install(win, [("GPU", [{"name": r"\\.\DISPLAY1", "desktop": img},
                           {"name": r"\\.\DISPLAY2", "rect": (1920, 0, 1920, 1080), "desktop": img, "monitor": 9}])])
    c = dx.DxgiCapture()
    assert c.grab(Rect(1900, 0, 100, 100)) is None and "not inside one monitor" in str(c.last_error)
    c._retry_at = -1e18
    out = c.grab(Rect(2000, 50, 64, 64))
    assert out is not None and np.array_equal(out, img[50:114, 80:144, :3])
    assert c.stats["output"].startswith(r"\\.\DISPLAY2 1920x1080@1920,0")
    assert len(c.stats["candidates"]) == 2


def test_protected_content_flag_is_recorded(win):
    api = install(win, [("GPU", [{"name": r"\\.\DISPLAY1", "desktop": desktop(9)}])])
    api.protected = True
    c = dx.DxgiCapture()
    assert c.grab(R) is not None and c.stats.get("protected") is True


def test_enumerate_displays_lists_every_adapter_output(win):
    install(win, [("NVIDIA", [{"name": r"\\.\DISPLAY1", "desktop": None}]),
                  ("Intel", [{"name": r"\\.\DISPLAY1", "desktop": None}])])
    win.setattr(dx, "available", lambda: True)
    rows = dx.enumerate_displays()
    assert len(rows) == 2 and rows[0].startswith("a0/o0") and rows[1].startswith("a1/o0 ")
    assert dx._api is not None


# ---------------------------------------------------------------------------------- SmartCapture glue
def live(seed: int, shape=(64, 64, 3)) -> np.ndarray:
    return np.random.default_rng(seed).integers(20, 230, shape, dtype=np.uint8)


class FakeDxgi:
    """A backend like DxgiCapture seen from SmartCapture (frames per call, mark_bad, describe)."""

    def __init__(self, frames, alternatives: int = 0) -> None:
        self.frames, self.alternatives, self.calls, self.marked, self.closed = frames, alternatives, 0, [], 0
        self.stats = {"output": r"\\.\DISPLAY1 1920x1080@0,0", "adapter": "NVIDIA GeForce GTX 1650",
                      "format": "B8G8R8A8_UNORM", "hdr": False}

    def grab(self, rect):
        self.calls += 1
        f = self.frames(self.calls)
        return None if f is None else f.copy()

    def mark_bad(self, reason=""):
        self.marked.append(reason)
        if self.alternatives > 0:
            self.alternatives -= 1
            return True
        return False

    def describe(self):
        return f"{self.stats['output']} / {self.stats['adapter']} / {self.stats['format']}"

    def close(self):
        self.closed += 1


class FakeMss:
    name = "mss"

    def __init__(self, img) -> None:
        self.img = img

    def grab(self, rect, pad=True):
        return self.img.copy()

    def close(self):
        pass


class _Clock:
    t = 0.0

    def __call__(self):
        return self.t


R64 = Rect(10, 20, 64, 64)


def test_crosscheck_black_frame_drops_the_output_then_keeps_the_good_one():
    img = live(40)
    black = np.zeros_like(img)
    d = FakeDxgi(lambda n: black if n == 1 else img, alternatives=1)
    clk = _Clock()
    c = SmartCapture("auto", factories={"dxgi": lambda: d, "mss": lambda: FakeMss(img)}, platform="win32", clock=clk)
    out = c.grab(R64)
    assert out is not None and c.current == "mss"
    assert d.marked == ["black / partial DXGI frame"] and c.stats["dxgi_rotations"] == 1
    reason = c.stats["disabled"]["dxgi"]
    assert "on " + r"\\.\DISPLAY1" in reason and "NVIDIA GeForce GTX 1650" in reason and "next output tried" in reason
    assert "overlay plane" not in reason
    assert any("dxgi output dropped" in e for e in c.stats["events"])
    assert c._impl["dxgi"] is d and d.closed == 1        # kept: remembers the dropped output
    clk.t = cap.CROSSCHECK_RETRY_S[0] + 0.1
    assert c.grab(R64) is not None and c.current == "dxgi" and d.calls == 2
    assert c.why().startswith("rapide (Desktop Duplication) · sur " + r"\\.\DISPLAY1" + " / NVIDIA")


def test_crosscheck_black_frame_single_output_names_the_overlay_plane():
    img = live(41)
    d = FakeDxgi(lambda n: np.zeros_like(img), alternatives=0)
    c = SmartCapture("auto", factories={"dxgi": lambda: d, "mss": lambda: FakeMss(img)}, platform="win32")
    c.grab(R64)
    reason = c.stats["disabled"]["dxgi"]
    assert "single output" in reason and "MPO" in reason and r"\\.\DISPLAY1" in reason
    assert "dxgi -> mss" in c.stats["last_switch"] and "MPO" in c.stats["last_switch"]
    why = c.why()
    assert why.startswith("classique (GDI)") and "pause" in why


def test_capture_why_texts():
    assert cap.capture_why({}) == "" and cap.capture_why(None) == "" and cap.capture_why({"backend": ""}) == ""
    t = {"backend": "dxgi", "fps": 29.6, "dxgi_stats": {"output": r"\\.\DISPLAY1 1920x1080@0,0", "adapter": "Intel",
                                                        "format": "R16G16B16A16_FLOAT", "hdr": True}}
    assert cap.capture_why(t) == "rapide (Desktop Duplication) · sur " + r"\\.\DISPLAY1" + " / Intel · bureau HDR converti · 30 img/s"
    t = {"backend": "mss", "disabled": {"dxgi": "init: Desktop Duplication unavailable"}}
    assert cap.capture_why(t) == "classique (GDI) · capture rapide indisponible sur ce PC"
    t = {"backend": "mss", "disabled": {"dxgi": "black / partial DXGI frame (mean 48.8, black 0.685) on x; single output: "
                                                "an overlay plane (MPO / flip) probably hides the game from the duplication"},
         "dxgi_stats": {"output": r"\\.\DISPLAY1 1920x1080@0,0"}}
    assert cap.capture_why(t) == ("classique (GDI) · capture rapide écartée : image noire sur " + r"\\.\DISPLAY1"
                                  + " (jeu dessiné hors du bureau : MPO / flip)")
    t = {"backend": "mss", "disabled": {"dxgi": "image differs from mss (diff 40); 7 cross-checks failed"}}
    assert "image différente" in cap.capture_why(t)
    t = {"backend": "mss", "disabled": {"dxgi": "self-check: black / frozen frames"}}
    assert "auto-diagnostic" in cap.capture_why(t)
    t = {"backend": "mss", "disabled": {"dxgi": "suspended 2 s: black ..."}, "suspended": {"dxgi": 3.0}}
    assert "pause" in cap.capture_why(t)


def test_system_panel_capture_row():
    from treeaicoach.ui_kit import subsystem_rows

    rows = {r[0]: r for r in subsystem_rows(state="RUNNING", running=True, capture="rapide (Desktop Duplication) · 30 img/s")}
    assert rows["capture"][1] == "Capture d'écran" and rows["capture"][2] == 0
    rows = {r[0]: r for r in subsystem_rows(state="RUNNING", running=True,
                                            capture="classique (GDI) · capture rapide écartée : image noire")}
    assert rows["capture"][2] == 1 and "noire" in rows["capture"][3]
    assert "capture" not in {r[0] for r in subsystem_rows(state="RUNNING", running=True)}
    assert "capture" not in {r[0] for r in subsystem_rows(state="RUNNING", running=False, capture="classique (GDI)")}


def test_selfcheck_snapshot_and_note_carry_capture_why():
    from treeaicoach import selfcheck as sc

    class Cap:
        current = "mss"
        stats = {"switches": 0, "last_switch": None}

        def other(self):
            return None

        def why(self):
            return "classique (GDI) · capture rapide indisponible sur ce PC"

    s = sc.Snapshot(t=1.0)
    s.capture_backend, s.capture_why, s.live, s.in_game = "mss", Cap().why(), True, True
    chk = sc.SelfCheck(clock=lambda: 1.0)
    chk._r_adapt(s)
    assert any("Capture d'écran : classique (GDI)" in str(v) for v in chk._adapt.notes.values())
