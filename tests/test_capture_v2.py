"""Pipeline v2 capture: backend selection / fallback (fakes for the Windows APIs), black and
frozen frame detection, DXGI off Windows, focus / occlusion helpers, DPI conversion."""

from __future__ import annotations

import ctypes
import sys
import types

import numpy as np
import pytest

from treeaicoach import capture as cap
from treeaicoach import dxgi_capture
from treeaicoach.capture import FrameHealth, Rect, SmartCapture


def live(seed: int, shape=(64, 64, 3)) -> np.ndarray:
    return np.random.default_rng(seed).integers(20, 230, shape, dtype=np.uint8)


class FakeBackend:
    def __init__(self, name: str, frames=None, fail: bool = False) -> None:
        self.name = name
        self.frames = frames
        self.fail = fail
        self.calls = 0
        self.closed = False

    def grab(self, rect, pad=True):
        self.calls += 1
        if self.fail:
            return None
        f = self.frames(self.calls) if callable(self.frames) else self.frames
        return None if f is None else f.copy()

    def close(self):
        self.closed = True


def smart(dxgi: FakeBackend, mss: FakeBackend, backend: str = "auto") -> SmartCapture:
    return SmartCapture(backend, factories={"dxgi": lambda: dxgi, "mss": lambda: mss}, platform="win32")


R = Rect(10, 20, 64, 64)


def test_frame_health_black_and_stale():
    h = FrameHealth(black_frames=3, stale_s=1.0, stale_frames=4)
    black = np.zeros((32, 32, 3), np.uint8)
    assert [h.observe(black, t) for t in (0, 0.1, 0.2)] == ["ok", "ok", "black"]
    img = live(1)
    out = [h.observe(img, 1.0 + 0.2 * i) for i in range(8)]
    assert out[:5] == ["ok"] * 5 and out[-1] == "stale"         # >= 4 same frames AND >= 1 s
    assert h.observe(live(2), 3.0) == "ok" and h.same_run == 0


def test_fingerprint_and_diff():
    a = live(3)
    assert cap.frame_fingerprint(a) == cap.frame_fingerprint(a.copy())
    assert cap.frame_fingerprint(a) != cap.frame_fingerprint(live(4))
    assert cap.frame_fingerprint(None) == -1
    assert cap.mean_abs_diff(a, a) == 0.0 and cap.mean_abs_diff(a, live(4)) > 20
    assert cap.mean_abs_diff(a, a[:10]) == float("inf")


def test_auto_prefers_dxgi_on_windows_mss_elsewhere():
    assert SmartCapture("auto", factories={"dxgi": object, "mss": object}, platform="win32").order == ["dxgi", "mss"]
    assert SmartCapture("auto", factories={"dxgi": object, "mss": object}, platform="linux").order == ["mss"]
    assert SmartCapture("mss", factories={"dxgi": object, "mss": object}, platform="win32").order == ["mss", "dxgi"]


def test_dxgi_crosscheck_keeps_matching_backend():
    img = live(5)
    d, m = FakeBackend("dxgi", img), FakeBackend("mss", img)
    c = smart(d, m)
    out = c.grab(R)
    assert out is not None and c.current == "dxgi" and c.stats["crosscheck"] == 0.0
    c.grab(R)
    assert m.calls == 1                      # cross-checked once only
    assert c.timings()["backend"] == "dxgi"


def test_dxgi_disabled_when_its_image_differs_from_mss():
    d, m = FakeBackend("dxgi", live(6)), FakeBackend("mss", live(7))
    c = smart(d, m)
    out = c.grab(R)
    assert "dxgi" in c.stats["disabled"] and c.current == "mss"
    assert out is not None and np.array_equal(out, m.frames)
    assert d.closed


def test_dxgi_init_failure_falls_back_to_mss():
    def boom():
        raise OSError("Desktop Duplication unavailable")

    m = FakeBackend("mss", live(8))
    c = SmartCapture("auto", factories={"dxgi": boom, "mss": lambda: m}, platform="win32")
    assert c.grab(R) is not None and c.current == "mss"
    assert "dxgi" in c.stats["disabled"]


def test_failed_grabs_served_by_fallback_then_switch():
    img = live(9)
    d, m = FakeBackend("dxgi", img), FakeBackend("mss", img)
    c = smart(d, m)
    c.grab(R)                                  # cross-check passes
    d.fail = True
    for i in range(cap.FAIL_SWITCH):
        assert c.grab(R) is not None           # every grab still served (mss)
    assert c.current == "mss" and c.stats["fallback_grabs"] >= cap.FAIL_SWITCH
    assert "dxgi -> mss" in c.stats["last_switch"]


def test_black_frames_switch_backend_when_other_is_live():
    black = np.zeros((64, 64, 3), np.uint8)
    d, m = FakeBackend("dxgi", None), FakeBackend("mss", black)
    c = SmartCapture("mss", factories={"dxgi": lambda: d, "mss": lambda: m}, platform="win32")
    d.frames = live(10)
    sts = [c.check(c.grab(R), 0.1 * i, R) for i in range(3)]
    assert sts[-1] == "switched" and c.current == "dxgi"


def test_black_on_both_backends_is_reported_black():
    black = np.zeros((64, 64, 3), np.uint8)
    c = smart(FakeBackend("dxgi", black), FakeBackend("mss", black))
    sts = [c.check(c.grab(R), 0.1 * i, R) for i in range(4)]
    assert sts[2:] == ["black", "black"] and c.current == "dxgi"


def test_frozen_frames_switch_only_when_the_other_backend_moves():
    frozen = live(11)
    d, m = FakeBackend("dxgi", frozen), FakeBackend("mss", lambda n: live(100 + n))
    c = smart(d, m)
    c._crosschecked.add("dxgi")                 # (cross-check would disable dxgi: different images)
    st = "ok"
    for i in range(cap.STALE_FRAMES + 2):
        st = c.check(c.grab(R), 0.5 * i, R)
        if st != "ok":
            break
    assert st == "switched" and c.current == "mss"
    # a legitimately static minimap (both identical) is not a capture failure
    same = live(12)
    c2 = smart(FakeBackend("dxgi", same), FakeBackend("mss", same))
    sts = [c2.check(c2.grab(R), 0.5 * i, R) for i in range(cap.STALE_FRAMES + 4)]
    assert "switched" not in sts and c2.current == "dxgi"
    # before the minions walk, frozen frames are not even questioned
    c3 = smart(FakeBackend("dxgi", same), FakeBackend("mss", live(13)))
    assert all(c3.check(c3.grab(R), 0.5 * i, R, allow_stale=False) == "ok" for i in range(30))


def test_dxgi_backend_is_inert_off_windows():
    if sys.platform == "win32":
        pytest.skip("Windows: covered by the real-hardware smoke")
    assert dxgi_capture.available() is False
    d = dxgi_capture.DxgiCapture()
    assert d.grab(Rect(0, 0, 10, 10)) is None
    d.close()
    assert dxgi_capture.self_test_copy()["ok"] is False


def test_game_window_helpers_off_windows():
    if sys.platform == "win32":
        pytest.skip("off-Windows behaviour")
    assert cap.game_window_info() is None
    assert cap.foreground_state() == (None, False)
    assert cap.rect_occluded(Rect(0, 0, 50, 50)) is None


def test_rect_occluded_with_fake_window_from_point():
    rect = Rect(1687, 809, 300, 300)
    pts = cap.occlusion_points(rect)
    assert len(pts) == 5 and all(rect.x <= x < rect.right and rect.y <= y < rect.bottom for x, y in pts)
    game = 0x1234
    assert cap.rect_occluded(rect, window_at=lambda x, y: game, game_hwnd=game) is False
    # League client over the left part of the minimap (real screenshot 5)
    assert cap.rect_occluded(rect, window_at=lambda x, y: 0x99 if x < 1760 else game, game_hwnd=game) is True
    # taskbar over the bottom of the minimap
    assert cap.rect_occluded(rect, window_at=lambda x, y: 0x77 if y > 1060 else game, game_hwnd=game) is True
    assert cap.rect_occluded(rect, window_at=lambda x, y: game, game_hwnd=None) is None


class _Fn:
    """ctypes-like function object (accepts restype / argtypes attributes)."""

    def __init__(self, fn) -> None:
        self.fn = fn

    def __call__(self, *a):
        return self.fn(*a)


def _fake_user32(awareness: int, dpi: int = 96):
    return types.SimpleNamespace(GetThreadDpiAwarenessContext=_Fn(lambda: 1),
                                 GetAwarenessFromDpiAwarenessContext=_Fn(lambda ctx: awareness),
                                 GetDpiForWindow=_Fn(lambda hwnd: dpi))


@pytest.mark.parametrize("dpi,expected", [(96, (100, 50, 800, 600)), (120, (125, 62, 1000, 750)),
                                          (144, (150, 75, 1200, 900))])
def test_to_physical_scales_logical_rects(monkeypatch, dpi, expected):
    """DPI-unaware thread at 125 % / 150 %: logical rects are scaled to physical pixels."""
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(user32=_fake_user32(0, dpi)), raising=False)
    r = cap._to_physical(1, Rect(100, 50, 800, 600))
    assert (r.x, r.y, r.w, r.h) == expected


def test_to_physical_keeps_rect_when_aware(monkeypatch):
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(user32=_fake_user32(2, 144)), raising=False)
    assert cap._to_physical(1, Rect(1, 2, 3, 4)) == Rect(1, 2, 3, 4)


def test_dead_backend_is_disabled_for_the_session():
    img = live(20)
    d = FakeBackend("dxgi", None)
    d.dead, d.last_error = True, "DuplicateOutput 0x80004001"
    m = FakeBackend("mss", img)
    c = smart(d, m)
    assert c.grab(R) is not None and c.current == "mss"
    assert c.stats["disabled"] == {"dxgi": "DuplicateOutput 0x80004001"}
    c.grab(R)
    assert d.calls == 1                      # never retried


@pytest.mark.skipif(sys.platform != "win32", reason="D3D11 only on Windows")
def test_dxgi_d3d11_half_on_windows():
    """CreateTexture2D / CopySubresourceRegion / Map vtable slots and struct layouts (also
    validated under Wine; Desktop Duplication itself needs a real desktop)."""
    out = dxgi_capture.self_test_copy()
    assert out["ok"] or "D3D11CreateDevice" in str(out.get("error")), out
