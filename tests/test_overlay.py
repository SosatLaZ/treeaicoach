"""Tests of treeaicoach.overlay: placement math (everywhere), no-op off Windows, Win32 windows on Windows."""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass

import numpy as np
import pytest

from treeaicoach import overlay as ov

SCREEN = (0, 0, 1920, 1080)
MINIMAP = (1640, 800, 255, 255)          # bottom-right, ~0.236 x H


@dataclass
class Cfg:
    overlay_enabled: bool = True
    radar_enabled: bool = True
    radar_position: str = "above_minimap"
    radar_scale: float = 1.0
    radar_xy: list | None = None
    hud_enabled: bool = True
    hud_position: str = "top_left"
    hud_xy: list | None = None
    danger_flash: bool = True
    overlay_mode: str = "minimap"


def inside(r, screen=SCREEN) -> bool:
    return r[0] >= screen[0] and r[1] >= screen[1] and r[0] + r[2] <= screen[0] + screen[2] \
        and r[1] + r[3] <= screen[1] + screen[3]


# ---------------------------------------------------------------------------- helpers
def test_as_rect_and_overlap():
    class R:
        x, y, w, h = 1, 2, 3, 4

    assert ov.as_rect(R()) == (1, 2, 3, 4)
    assert ov.as_rect([1.4, 2.6, 10, 20]) == (1, 3, 10, 20)
    assert ov.as_rect((0, 0, 0, 5)) is None and ov.as_rect(None) is None and ov.as_rect("abc") is None
    assert ov.as_rect((0, 0, float("nan"), 5)) is None
    assert ov.rects_overlap((0, 0, 10, 10), (9, 9, 5, 5))
    assert not ov.rects_overlap((0, 0, 10, 10), (10, 0, 5, 5))
    assert ov.clamp_to_screen(-50, 2000, 100, 100, SCREEN) == (0, 980)


# ---------------------------------------------------------------------------- radar placement
def test_radar_above_minimap_default():
    x, y, s = ov.radar_geometry(MINIMAP, SCREEN)
    assert s == 255
    assert x + s == MINIMAP[0] + MINIMAP[2]                   # same right edge
    assert y + s == MINIMAP[1] - ov.RADAR_GAP                  # 8 px above the minimap top
    assert not ov.rects_overlap((x, y, s, s), MINIMAP) and inside((x, y, s, s))


def test_radar_scale_and_fallback_to_left():
    x, y, s = ov.radar_geometry(MINIMAP, SCREEN, scale=2.0)
    assert s == 510
    assert not ov.rects_overlap((x, y, s, s), MINIMAP) and inside((x, y, s, s))
    # big minimap near the top: not enough room above -> shrunk, still above and right-aligned
    mm = (1500, 300, 400, 400)
    x, y, s = ov.radar_geometry(mm, SCREEN, 1.0)
    assert s == 300 - 2 * ov.RADAR_GAP
    assert (x + s, y + s) == (mm[0] + mm[2], mm[1] - ov.RADAR_GAP)
    assert not ov.rects_overlap((x, y, s, s), mm) and inside((x, y, s, s))
    # no room at all above (< RADAR_MIN): only then to the left, bottom-aligned
    mm = (1500, 60, 400, 400)
    x, y, s = ov.radar_geometry(mm, SCREEN, 1.0)
    assert (x + s, y + s) == (mm[0] - ov.RADAR_GAP, mm[1] + mm[3])
    assert not ov.rects_overlap((x, y, s, s), mm) and inside((x, y, s, s))


def test_radar_user_screenshot_bug_dpi_virtualized_screen():
    """Game window reported in logical pixels (125 %), minimap in physical ones: the radar must stay
    above the minimap, right-aligned - not pushed to the left over the game."""
    logical = (0, 0, 1536, 864)
    mm = (1598, 760, 318, 318)
    scr = ov.effective_screen(logical, mm, (0, 0, 1920, 1080))
    assert scr == (0, 0, 1920, 1080)
    x, y, s = ov.radar_geometry(mm, scr)
    assert x + s == mm[0] + mm[2] and y + s == mm[1] - ov.RADAR_GAP
    # no monitor information: union of both rectangles
    scr = ov.effective_screen(logical, mm)
    assert scr == (0, 0, 1916, 1078)
    x, y, s = ov.radar_geometry(mm, scr)
    assert x + s == mm[0] + mm[2] and not ov.rects_overlap((x, y, s, s), mm)
    assert ov.effective_screen(SCREEN, MINIMAP, (5, 5, 10, 10)) == SCREEN
    assert ov.effective_screen(None, None) == (0, 0, 1920, 1080)


def test_resolve_overlay_mode():
    assert ov.resolve_overlay_mode("minimap", True) == "minimap"
    assert ov.resolve_overlay_mode("minimap", False) == "radar"
    assert ov.resolve_overlay_mode("radar", True) == "radar"
    assert ov.resolve_overlay_mode("off", False) == "off"
    assert ov.resolve_overlay_mode(None, True) == "minimap"
    assert ov.resolve_overlay_mode("junk", False) == "radar"


def test_radar_other_positions_and_custom():
    x, y, s = ov.radar_geometry(MINIMAP, SCREEN, 1.0, "left_of_minimap")
    assert x + s == MINIMAP[0] - ov.RADAR_GAP and y + s == MINIMAP[1] + MINIMAP[3]
    x, y, s = ov.radar_geometry(MINIMAP, SCREEN, 1.0, "top_left")
    assert (x, y) == (ov.RADAR_GAP, ov.RADAR_GAP)
    assert ov.radar_geometry(MINIMAP, SCREEN, 1.0, "custom", [100, 200])[:2] == (100, 200)
    x, y, s = ov.radar_geometry(MINIMAP, SCREEN, 1.0, "custom", [5000, -30])   # clamped
    assert inside((x, y, s, s)) and not ov.rects_overlap((x, y, s, s), MINIMAP)
    x, y, s = ov.radar_geometry(MINIMAP, SCREEN, 1.0, "custom", [1650, 810])   # over the minimap
    assert not ov.rects_overlap((x, y, s, s), MINIMAP)
    x, y, s = ov.radar_geometry(MINIMAP, SCREEN, 1.0, "custom", None)          # no xy: default
    assert (x, y, s) == ov.radar_geometry(MINIMAP, SCREEN)
    assert ov.radar_geometry(MINIMAP, SCREEN, 1.0, "nonsense") == ov.radar_geometry(MINIMAP, SCREEN)


def test_radar_never_overlaps_minimap_fuzz():
    rng = np.random.default_rng(3)
    for _ in range(300):
        sw, sh = int(rng.integers(800, 3840)), int(rng.integers(600, 2160))
        scr = (int(rng.integers(-2000, 2000)), int(rng.integers(-500, 500)), sw, sh)
        side = int(sh * rng.uniform(0.14, 0.5))
        right = rng.random() < 0.8
        mx = scr[0] + (sw - side - 10 if right else 10)
        mm = (mx, scr[1] + sh - side - 10, side, side)
        pos = str(rng.choice(ov.RADAR_POSITIONS))
        xy = [int(rng.integers(-500, 4000)), int(rng.integers(-500, 2500))]
        x, y, s = ov.radar_geometry(mm, scr, float(rng.uniform(0.3, 2.5)), pos, xy)
        assert s >= 48
        assert not ov.rects_overlap((x, y, s, s), mm), (scr, mm, pos, (x, y, s))
        assert inside((x, y, s, s), scr), (scr, mm, pos, (x, y, s))


def test_radar_placement_wrapper_and_no_minimap():
    x, y = ov.radar_placement(MINIMAP, SCREEN, 255)
    assert (x, y) == ov.radar_geometry(MINIMAP, SCREEN)[:2]
    x, y, s = ov.radar_geometry(None, SCREEN)
    assert inside((x, y, s, s))
    assert ov.radar_size(MINIMAP, 10) == 510 and ov.radar_size(MINIMAP, "x") == 255
    assert ov.radar_size((0, 0, 20, 20), 1.0) == ov.RADAR_MIN


# ---------------------------------------------------------------------------- HUD placement
def test_hud_width_and_flash_thickness():
    assert ov.hud_width(SCREEN) == ov.HUD_BASE_WIDTH == 280
    assert ov.hud_width((0, 0, 3840, 2160)) == ov.HUD_MAX_WIDTH
    assert ov.hud_width((0, 0, 1280, 720)) == ov.HUD_MIN_WIDTH
    assert ov.flash_thickness(SCREEN) == 10 and ov.flash_thickness((0, 0, 2560, 1440)) == 13
    assert ov.flash_thickness(None) == 10


def test_hud_positions():
    w, h = 340, 280
    assert ov.hud_placement(SCREEN, w, h, "top_left") == (16, 16)
    # default: just above the minimap, right edges aligned, never over it
    x, y = ov.hud_placement(SCREEN, w, h, anchor=MINIMAP, avoid=[MINIMAP])
    assert x + w == MINIMAP[0] + MINIMAP[2] and y + h == MINIMAP[1] - ov.RADAR_GAP
    # above the radar when one is shown above the minimap
    radar = (1640, 537, 255, 255)
    x, y = ov.hud_placement(SCREEN, w, h, "above_minimap", anchor=radar, avoid=[MINIMAP, radar])
    assert y + h == radar[1] - ov.RADAR_GAP and not ov.rects_overlap((x, y, w, h), radar)
    # no room above / no anchor -> top right
    assert ov.hud_placement(SCREEN, w, h, "above_minimap", anchor=(1600, 100, 300, 300)) == \
        ov.hud_placement(SCREEN, w, h, "top_right")
    assert ov.hud_placement(SCREEN, w, h) == ov.hud_placement(SCREEN, w, h, "top_right")
    x, y = ov.hud_placement(SCREEN, w, h, "top_right")
    assert x + w == 1920 - 16 and y > 50
    x, y = ov.hud_placement(SCREEN, w, h, "left_middle")
    assert x == 16 and y == (1080 - h) // 2
    assert ov.hud_placement(SCREEN, w, h, "custom", [300, 400]) == (300, 400)
    assert ov.hud_placement(SCREEN, w, h, "custom", [5000, 5000]) == (1920 - w, 1080 - h)
    # second monitor on the left
    scr2 = (-1920, 0, 1920, 1080)
    assert ov.hud_placement(scr2, w, h, "top_left") == (-1904, 16)


def test_hud_avoids_radar_and_minimap():
    radar = (1600, 500, 300, 300)
    x, y = ov.hud_placement(SCREEN, 340, 280, "custom", [1580, 450], avoid=[MINIMAP, radar])
    assert not ov.rects_overlap((x, y, 340, 280), radar)
    assert not ov.rects_overlap((x, y, 340, 280), MINIMAP)
    assert inside((x, y, 340, 280))
    # radar in the top-left corner pushes the default HUD below it
    x, y = ov.hud_placement(SCREEN, 340, 280, "top_left", avoid=[(8, 8, 255, 255)])
    assert y >= 8 + 255 and not ov.rects_overlap((x, y, 340, 280), (8, 8, 255, 255))


# ---------------------------------------------------------------------------- misc helpers
def test_quantize_flash_and_fast_refresh():
    assert ov.quantize_flash(0.84) == 0.8 and ov.quantize_flash(1.7) == 1.0
    assert ov.quantize_flash(-1) == 0.0 and ov.quantize_flash("x") == 0.0 and ov.quantize_flash(float("nan")) == 0.0

    @dataclass
    class S:
        threat_level: int = 0
        flash: float = 0.0
        last_alert: tuple | None = None
        enemies: list | None = None

    assert not ov.needs_fast_refresh(S())
    assert ov.needs_fast_refresh(S(threat_level=1))
    assert ov.needs_fast_refresh(S(last_alert=("x", 0, 1.0)))
    assert not ov.needs_fast_refresh(S(last_alert=("x", 0, 9.0)))


def test_move_mode_frame_makes_everything_grabbable():
    img = np.zeros((60, 120, 4), np.uint8)
    out = ov.move_mode_frame(img, "Radar — glisser")
    assert out.shape == img.shape and out[..., 3].min() > 0
    assert (out[..., :3].astype(int) <= out[..., 3:4].astype(int) + 1).all()
    assert not img.any()                                   # input untouched


# ---------------------------------------------------------------------------- platform behaviour
def test_win32_prototypes_build_with_fake_dlls(monkeypatch):
    """The ctypes structures / prototypes are declared without error (runs everywhere)."""
    import ctypes

    class FakeFn:
        def __call__(self, *a):
            return 1

    class FakeDll:
        def __init__(self, *a, **k):
            self._fns = {}

        def __getattr__(self, name):
            if name.startswith("_"):
                raise AttributeError(name)
            return self._fns.setdefault(name, FakeFn())

    monkeypatch.setattr(ctypes, "WinDLL", FakeDll, raising=False)
    if not hasattr(ctypes, "WINFUNCTYPE"):
        monkeypatch.setattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, raising=False)
    api = ov._Api()
    assert api.user32.UpdateLayeredWindow.restype is not None
    assert len(api.user32.CreateWindowExW.argtypes) == 12
    assert ctypes.sizeof(api.BLENDFUNCTION) == 4
    if sys.platform == "win32":      # wintypes.LONG / DWORD are 64-bit on Linux
        assert ctypes.sizeof(api.BITMAPINFOHEADER) == 40
    assert api.GetWindowLongPtr.restype is ctypes.c_ssize_t
    assert api.SetWindowDisplayAffinity is not None and api.GetWindowDisplayAffinity is not None


def test_capture_exclusion_fallback_logic(monkeypatch):
    """exclude_from_capture: verified with GetWindowDisplayAffinity, reset to WDA_NONE when refused."""
    calls = []

    class U:
        pass

    class Api:
        ctypes = __import__("ctypes")

        class wt:
            DWORD = __import__("ctypes").c_ulong

        def __init__(self, set_ok, reported):
            self.reported = reported

            def set_aff(hwnd, v):
                calls.append(v)
                return set_ok

            def get_aff(hwnd, ref):
                ref._obj.value = self.reported
                return 1

            self.SetWindowDisplayAffinity = set_aff
            self.GetWindowDisplayAffinity = get_aff

        def last_error(self):
            return 87

    win = ov.LayeredWindow.__new__(ov.LayeredWindow)
    win.name, win.hwnd = "t", 1
    monkeypatch.setattr(ov, "windows_build", lambda: 19045)
    win._api = Api(1, ov.WDA_EXCLUDEFROMCAPTURE)
    assert win.exclude_from_capture() is True and calls == [ov.WDA_EXCLUDEFROMCAPTURE]
    calls.clear()
    win._api = Api(1, 0x01)           # degraded to WDA_MONITOR (black box): refused + reset
    assert win.exclude_from_capture() is False and calls == [ov.WDA_EXCLUDEFROMCAPTURE, ov.WDA_NONE]
    calls.clear()
    win._api = Api(0, 0)
    assert win.exclude_from_capture() is False and calls[-1] == ov.WDA_NONE
    calls.clear()
    monkeypatch.setattr(ov, "windows_build", lambda: 18363)   # Windows 10 1909: not even tried
    assert win.exclude_from_capture() is False and calls == []



@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows behaviour")
def test_manager_is_a_clean_noop_off_windows():
    calls = []
    m = ov.OverlayManager(Cfg(), lambda: calls.append(1))
    assert m.ok is False
    m.start()
    m.apply_config(Cfg(radar_scale=2.0))
    m.set_move_mode(True)
    m.set_on_moved(lambda *a: None)
    assert m.move_mode and m.toggle_visible() is False
    m.stop()
    assert not m.is_running() and calls == []
    with pytest.raises(OSError):
        ov.LayeredWindow("radar")


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 layered windows")
def test_layered_window_create_update_destroy():
    win = ov.LayeredWindow("test", click_through=True)
    try:
        img = np.zeros((64, 96, 4), np.uint8)
        img[16:48, 16:80] = (0, 0, 128, 128)     # premultiplied half-transparent red
        win.update(img, -2000, -2000)            # off-screen: invisible to the user
        assert not win.failed and win.visible and (win.w, win.h) == (96, 64)
        win.update(np.zeros((32, 32, 4), np.uint8), -2000, -2000)   # new DIB size
        assert not win.failed and (win.w, win.h) == (32, 32)
        win.set_click_through(False)
        assert win.click_through is False
        win.set_click_through(True)
        win.keep_topmost()
        ov.pump_messages()
        r = win.window_rect()
        assert r is not None and r[2] == 32
        win.hide()
        assert not win.visible
    finally:
        win.destroy()
        win.destroy()                            # idempotent


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 layered windows")
def test_manager_runs_on_windows():
    from treeaicoach import overlay_render as orr

    st = orr.OverlayState(minimap_rect=(-1500, -1500, 255, 255), screen_rect=(-3000, -3000, 1920, 1080),
                          threat_level=2, flash=0.5)
    ev = threading.Event()

    def provider():
        ev.set()
        return st

    m = ov.OverlayManager(Cfg(), provider)
    assert m.ok
    m.start()
    try:
        assert ev.wait(5.0)
        time.sleep(0.4)
        assert m.ok and m.is_running()
        assert m.effective_mode in ("minimap", "radar")
        m.set_move_mode(True)
        time.sleep(0.3)
        m.set_move_mode(False)
        m.apply_config(Cfg(hud_enabled=False))
        time.sleep(0.3)
        m.apply_config(Cfg(overlay_mode="radar"))
        time.sleep(0.3)
        assert m.effective_mode == "radar"
        assert m.ok
    finally:
        m.stop()
    assert not m.is_running()
