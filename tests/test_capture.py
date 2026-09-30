"""Tests for treeaicoach.capture (Rect, DPI, game window, mss capture, black frames).

The capture logic is exercised with a fake ``mss`` object (no display needed); a real
``mss`` smoke test only checks that nothing raises (it may return None without a display).
"""

from __future__ import annotations

import sys
import threading
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

import treeaicoach.capture as capture
from treeaicoach.capture import (
    Rect,
    ScreenCapture,
    find_game_window,
    is_black_frame,
    monitor_rects,
    set_dpi_awareness,
)


# ======================================================================================
# Fake mss
# ======================================================================================


class FakeShot:
    def __init__(self, raw: bytes, w: int, h: int) -> None:
        self.raw, self.width, self.height = raw, w, h


class FakeMss:
    """Virtual screen made of two monitors: [0, 1920) x [0, 1080) and [1920, 3200) x [0, 1024)."""

    instances: list["FakeMss"] = []

    def __init__(self, fail: bool = False) -> None:
        self.monitors = [
            {"left": 0, "top": 0, "width": 3200, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 1920, "top": 0, "width": 1280, "height": 1024},
        ]
        self.fail = fail
        self.closed = False
        self.grabs: list[dict] = []
        self.thread = threading.get_ident()
        FakeMss.instances.append(self)

    def grab(self, mon: dict) -> FakeShot:
        if self.fail:
            raise RuntimeError("boom")
        self.grabs.append(dict(mon))
        w, h = mon["width"], mon["height"]
        # pixel (x, y) of the screen = (B, G, R, A) = (x % 256, y % 256, 77, 255)
        xs = (np.arange(mon["left"], mon["left"] + w) % 256).astype(np.uint8)
        ys = (np.arange(mon["top"], mon["top"] + h) % 256).astype(np.uint8)
        img = np.empty((h, w, 4), np.uint8)
        img[:, :, 0] = xs[None, :]
        img[:, :, 1] = ys[:, None]
        img[:, :, 2] = 77
        img[:, :, 3] = 255
        return FakeShot(img.tobytes(), w, h)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_mss(monkeypatch: pytest.MonkeyPatch) -> type[FakeMss]:
    FakeMss.instances = []
    monkeypatch.setattr(capture, "_new_mss", lambda: FakeMss())
    return FakeMss


# ======================================================================================
# Rect
# ======================================================================================


def test_rect_basics() -> None:
    r = Rect(10, 20, 30, 40)
    assert (r.right, r.bottom, r.area) == (40, 60, 1200)
    assert not r.is_empty() and Rect(0, 0, 0, 5).is_empty()
    assert r.offset(5, -5) == Rect(15, 15, 30, 40)
    assert r.to_dict() == {"x": 10, "y": 20, "w": 30, "h": 40}
    assert r.intersect(Rect(30, 50, 100, 100)) == Rect(30, 50, 10, 10)
    assert r.intersect(Rect(100, 100, 5, 5)) is None
    # floats / numpy scalars are coerced to int (hashable, JSON friendly)
    f = Rect(1.6, np.int64(2), np.float32(3.2), 4)  # type: ignore[arg-type]
    assert f == Rect(2, 2, 3, 4) and all(type(v) is int for v in (f.x, f.y, f.w, f.h))
    assert hash(f) == hash(Rect(2, 2, 3, 4))
    with pytest.raises(Exception):
        r.x = 5  # type: ignore[misc]  # frozen


# ======================================================================================
# DPI / game window / monitors
# ======================================================================================


def test_set_dpi_awareness_idempotent() -> None:
    set_dpi_awareness()
    set_dpi_awareness()  # second call: no-op, never raises


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows behaviour")
def test_find_game_window_off_windows() -> None:
    assert find_game_window() is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_find_game_window_windows_smoke() -> None:
    r = find_game_window()  # no game on CI -> None; must not raise either way
    assert r is None or (r.w >= capture.MIN_WINDOW_SIZE and r.h >= capture.MIN_WINDOW_SIZE)


def test_monitor_rects(fake_mss: type[FakeMss]) -> None:
    mons = monitor_rects()
    assert mons == [Rect(0, 0, 1920, 1080), Rect(1920, 0, 1280, 1024)]
    assert all(m.closed for m in fake_mss.instances)
    assert capture.virtual_screen_rect() == Rect(0, 0, 3200, 1080)


def test_monitor_rects_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> Any:
        raise OSError("no display")

    monkeypatch.setattr(capture, "_new_mss", broken)
    assert monitor_rects() == []
    assert capture.virtual_screen_rect() is None


# ======================================================================================
# ScreenCapture
# ======================================================================================


def _expected(rect: Rect) -> np.ndarray:
    xs = (np.arange(rect.x, rect.right) % 256).astype(np.uint8)
    ys = (np.arange(rect.y, rect.bottom) % 256).astype(np.uint8)
    out = np.empty((rect.h, rect.w, 3), np.uint8)
    out[:, :, 0] = xs[None, :]
    out[:, :, 1] = ys[:, None]
    out[:, :, 2] = 77
    return out


def test_grab_inside_screen(fake_mss: type[FakeMss]) -> None:
    cap = ScreenCapture()
    r = Rect(1500, 800, 300, 200)
    img = cap.grab(r)
    assert img is not None and img.shape == (200, 300, 3) and img.dtype == np.uint8
    assert img.flags["C_CONTIGUOUS"]
    np.testing.assert_array_equal(img, _expected(r))
    # lazily created once, reused
    cap.grab(r)
    assert len(fake_mss.instances) == 1 and len(fake_mss.instances[0].grabs) == 2
    cap.close()
    assert fake_mss.instances[0].closed


def test_grab_clips_to_virtual_screen(fake_mss: type[FakeMss]) -> None:
    cap = ScreenCapture()
    r = Rect(3100, 1000, 200, 150)            # crosses the right and bottom edges
    img = cap.grab(r)
    assert img is not None and img.shape == (150, 200, 3)
    vis = Rect(3100, 1000, 100, 80)
    np.testing.assert_array_equal(img[:80, :100], _expected(vis))
    assert not img[80:].any() and not img[:, 100:].any()   # padded black
    assert fake_mss.instances[-1].grabs[-1] == {"left": 3100, "top": 1000, "width": 100, "height": 80}
    img2 = cap.grab(Rect(-50, -20, 100, 60), pad=False)   # negative origin, no padding
    assert img2 is not None and img2.shape == (40, 50, 3)
    np.testing.assert_array_equal(img2, _expected(Rect(0, 0, 50, 40)))
    assert cap.grab(Rect(5000, 5000, 10, 10)) is None      # entirely off screen
    cap.close()


def test_grab_invalid_rects(fake_mss: type[FakeMss]) -> None:
    cap = ScreenCapture()
    assert cap.grab(Rect(0, 0, 0, 10)) is None
    assert cap.grab(Rect(0, 0, -5, 10)) is None
    assert cap.grab(Rect(0, 0, 100000, 100000)) is None
    assert cap.grab(None) is None  # type: ignore[arg-type]
    assert cap.grab(SimpleNamespace(x="a", y=0, w=1, h=1)) is None  # type: ignore[arg-type]


def test_grab_error_returns_none_and_recreates(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[FakeMss] = []

    def factory() -> FakeMss:
        m = FakeMss(fail=not made)   # the first instance fails, the next ones work
        made.append(m)
        return m

    monkeypatch.setattr(capture, "_new_mss", factory)
    cap = ScreenCapture()
    assert cap.grab(Rect(0, 0, 10, 10)) is None
    assert cap.errors == 1 and made[0].closed
    img = cap.grab(Rect(0, 0, 10, 10))
    assert img is not None and cap.errors == 0 and len(made) == 2


def test_grab_factory_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> Any:
        raise OSError("no display")

    monkeypatch.setattr(capture, "_new_mss", broken)
    cap = ScreenCapture()
    assert cap.grab(Rect(0, 0, 10, 10)) is None  # never raises
    cap.close()


def test_grab_recreates_mss_in_other_thread(fake_mss: type[FakeMss]) -> None:
    cap = ScreenCapture()
    assert cap.grab(Rect(0, 0, 8, 8)) is not None
    out: list[Any] = []
    th = threading.Thread(target=lambda: out.append(cap.grab(Rect(0, 0, 8, 8))))
    th.start()
    th.join(10)
    assert out and out[0] is not None
    assert len(fake_mss.instances) == 2 and fake_mss.instances[0].closed
    assert fake_mss.instances[1].thread != fake_mss.instances[0].thread


def test_context_manager(fake_mss: type[FakeMss]) -> None:
    with ScreenCapture() as cap:
        assert cap.grab(Rect(0, 0, 4, 4)) is not None
    assert fake_mss.instances[0].closed


def test_real_mss_smoke() -> None:
    pytest.importorskip("mss")
    cap = ScreenCapture()
    try:
        img = cap.grab(Rect(0, 0, 64, 48))   # None without a display; never raises
        assert img is None or (img.shape == (48, 64, 3) and img.dtype == np.uint8)
    finally:
        cap.close()
    assert isinstance(monitor_rects(), list)


# ======================================================================================
# Black frames
# ======================================================================================


def test_is_black_frame() -> None:
    assert is_black_frame(np.zeros((1080, 1920, 3), np.uint8))
    assert is_black_frame(np.full((100, 100, 3), 5, np.uint8))
    assert is_black_frame(np.zeros((100, 100, 4), np.uint8))          # BGRA
    assert is_black_frame(np.zeros((100, 100), np.uint8))             # gray
    assert not is_black_frame(np.full((100, 100, 3), 10, np.uint8))   # mean too high
    noisy = np.zeros((200, 200, 3), np.uint8)
    noisy[::2] = 12                                                   # mean 6 / std 6
    assert not is_black_frame(noisy)
    dark_hud = np.zeros((720, 1280, 3), np.uint8)
    dark_hud[600:, 1000:] = 120                                       # a lit minimap corner
    assert not is_black_frame(dark_hud)
    rng = np.random.default_rng(0)
    assert not is_black_frame(rng.integers(0, 255, (720, 1280, 3), dtype=np.uint8))
    for bad in (None, "x", np.zeros((0, 0, 3), np.uint8), np.zeros((2, 2, 2, 2), np.uint8)):
        assert is_black_frame(bad) is False
