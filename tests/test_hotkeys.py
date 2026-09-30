"""Tests for treeaicoach.hotkeys (RegisterHotKey listener; a fake user32 drives the loop off Windows)."""

from __future__ import annotations

import queue
import sys
import threading
import time

import pytest

from treeaicoach import hotkeys
from treeaicoach.hotkeys import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    MOD_SHIFT,
    MOD_WIN,
    VK_F1,
    WM_HOTKEY,
    WM_QUIT,
    Hotkey,
    HotkeyListener,
    normalize_hotkey,
    parse_hotkey,
)

# --------------------------------------------------------------------------- key names


@pytest.mark.parametrize("name, expected", [
    ("F9", Hotkey("F9", VK_F1 + 8, 0)),
    ("f1", Hotkey("F1", VK_F1, 0)),
    (" ctrl + f9 ", Hotkey("Ctrl+F9", VK_F1 + 8, MOD_CONTROL)),
    ("Shift+Alt+F10", Hotkey("Alt+Shift+F10", VK_F1 + 9, MOD_ALT | MOD_SHIFT)),
    ("Maj+F12", Hotkey("Shift+F12", VK_F1 + 11, MOD_SHIFT)),
    ("Control+Win+F11", Hotkey("Ctrl+Win+F11", VK_F1 + 10, MOD_CONTROL | MOD_WIN)),
    ("F24", Hotkey("F24", 0x87, 0)),
])
def test_parse_valid_names(name, expected):
    assert parse_hotkey(name) == expected
    assert normalize_hotkey(name) == expected.name


@pytest.mark.parametrize("name", [
    "F0", "F25", "F", "A", "Ctrl+A", "Ctrl+Ctrl+F1", "F9+Ctrl", "Hyper+F1", "Ctrl+",
    "F1 F2", "Ctrl+Alt+Shift+Win+Ctrl+F1", "x" * 100, "F９",
])
def test_parse_invalid_names(name):
    assert parse_hotkey(name) is None
    assert normalize_hotkey(name) is None


@pytest.mark.parametrize("name", ["", "  ", "off", "None", "désactivé", "Aucun"])
def test_disabled_names(name):
    assert parse_hotkey(name) is None
    assert normalize_hotkey(name) == ""


@pytest.mark.parametrize("value", [None, 9, 9.0, b"F9", ["F9"], object()])
def test_non_string_names(value):
    assert parse_hotkey(value) is None and normalize_hotkey(value) is None


# --------------------------------------------------------------------------- off Windows


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows behaviour")
def test_noop_off_windows():
    called = []
    lst = HotkeyListener({"F9": lambda: called.append(1), "bad key": lambda: None, "": lambda: None})
    assert lst.keys == ["F9"]
    lst.start()
    assert lst.ok is False and not lst.is_running() and lst.registered == []
    lst.stop()
    lst.stop()
    with HotkeyListener({"F10": lambda: None}) as other:
        assert other.ok is False


# --------------------------------------------------------------------------- fake Win32


class FakeUser32:
    """Minimal user32 stand-in: a message queue per listener thread."""

    def __init__(self, taken: set[int] | None = None):
        self.q: queue.Queue = queue.Queue()
        self.taken = taken or set()
        self.registered: dict[int, tuple[int, int]] = {}
        self.unregistered: list[int] = []
        self.register_thread: int | None = None
        self.fail_getmessage = False

    def PeekMessageW(self, pmsg, hwnd, a, b, flags):
        return 0

    def RegisterHotKey(self, hwnd, hid, mods, vk):
        self.register_thread = threading.get_ident()
        if vk in self.taken:
            return 0
        self.registered[hid] = (mods, vk)
        return 1

    def UnregisterHotKey(self, hwnd, hid):
        assert threading.get_ident() == self.register_thread     # same thread as RegisterHotKey
        self.unregistered.append(hid)
        return 1

    def GetMessageW(self, pmsg, hwnd, a, b):
        if self.fail_getmessage:
            return -1
        kind, value = self.q.get(timeout=10)
        if kind == "quit":
            return 0
        msg = pmsg._obj
        msg.message = WM_HOTKEY if kind == "hotkey" else 0x0113
        msg.wParam = value
        return 1

    def PostThreadMessageW(self, tid, msg, wparam, lparam):
        if msg == WM_QUIT:
            self.q.put(("quit", 0))
        return 1

    def press(self, vk: int) -> None:
        for hid, (_mods, v) in self.registered.items():
            if v == vk:
                self.q.put(("hotkey", hid))


@pytest.fixture
def fake(monkeypatch):
    f = FakeUser32()
    monkeypatch.setattr(hotkeys, "is_supported", lambda: True)
    monkeypatch.setattr(hotkeys, "_user32", lambda: f)
    monkeypatch.setattr(hotkeys, "_current_thread_id", lambda: threading.get_ident())
    monkeypatch.setattr(hotkeys, "_post_quit", lambda tid: bool(f.PostThreadMessageW(tid, WM_QUIT, 0, 0)))
    return f


def _wait(pred, timeout=3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.005)
    return False


def test_register_dispatch_and_stop(fake):
    hits: list[str] = []
    boom = threading.Event()

    def explode():
        boom.set()
        raise RuntimeError("callback bug")

    lst = HotkeyListener({"F9": lambda: hits.append("F9"), "ctrl+f10": explode, "F11": "not callable",
                          "Ctrl+F10": lambda: hits.append("dup")})
    assert lst.keys == ["F9", "Ctrl+F10"]
    lst.start()
    assert lst.ok is True and lst.is_running()
    assert lst.registered == ["F9", "Ctrl+F10"] and lst.failed == []
    mods = sorted(m for m, _vk in fake.registered.values())
    assert mods == [MOD_NOREPEAT, MOD_CONTROL | MOD_NOREPEAT]
    lst.start()                                          # idempotent
    fake.press(VK_F1 + 9)                                # callback raising: logged, loop continues
    assert boom.wait(3)
    fake.q.put(("other", 0))                             # non-hotkey message ignored
    fake.press(VK_F1 + 8)
    assert _wait(lambda: hits == ["F9"])
    t0 = time.monotonic()
    lst.stop()
    assert time.monotonic() - t0 < 2.0
    assert not lst.is_running() and lst.ok is False
    assert sorted(fake.unregistered) == sorted(fake.registered)
    lst.stop()                                           # idempotent


def test_restart_and_set_bindings(fake):
    hits: list[str] = []
    lst = HotkeyListener({"F9": lambda: hits.append("a")})
    lst.start()
    lst.set_bindings({"F8": lambda: hits.append("b")})
    assert lst.is_running() and lst.registered == ["F8"]
    fake.press(VK_F1 + 7)
    assert _wait(lambda: hits == ["b"])
    lst.stop()
    lst.set_bindings({"F7": lambda: None})               # not running: just replaced
    assert not lst.is_running() and lst.keys == ["F7"]
    lst.start()
    assert lst.ok and lst.registered == ["F7"]
    lst.stop()


def test_partial_and_total_registration_failure(fake):
    fake.taken = {VK_F1 + 9}                             # F10 used by another program
    lst = HotkeyListener({"F9": lambda: None, "F10": lambda: None})
    lst.start()
    assert lst.ok is False and lst.failed == ["F10"] and lst.registered == ["F9"]
    assert lst.is_running()                              # F9 still works
    lst.stop()
    only = HotkeyListener({"F10": lambda: None})
    only.start()
    assert only.ok is False and only.failed == ["F10"]
    assert _wait(lambda: not only.is_running())          # nothing registered: thread ends
    only.stop()


def test_getmessage_error_and_empty_bindings(fake):
    fake.fail_getmessage = True
    lst = HotkeyListener({"F9": lambda: None})
    lst.start()
    assert _wait(lambda: not lst.is_running())
    assert lst.ok is False and fake.unregistered == list(fake.registered)
    lst.stop()
    empty = HotkeyListener({})
    empty.start()
    assert empty.ok is True and not empty.is_running()
    assert HotkeyListener(None).keys == [] and HotkeyListener("F9").keys == []   # type: ignore[arg-type]


def test_start_never_raises_when_win32_is_broken(monkeypatch):
    monkeypatch.setattr(hotkeys, "is_supported", lambda: True)

    def broken():
        raise OSError("no user32")

    monkeypatch.setattr(hotkeys, "_user32", broken)
    lst = HotkeyListener({"F9": lambda: None})
    lst.start()
    assert lst.ok is False
    assert _wait(lambda: not lst.is_running())
    lst.stop()


# --------------------------------------------------------------------------- real Windows


@pytest.mark.skipif(sys.platform != "win32", reason="RegisterHotKey is Windows-only")
def test_real_register_and_stop_on_windows():
    lst = HotkeyListener({"Ctrl+Alt+Shift+F24": lambda: None})
    t0 = time.monotonic()
    lst.start()
    assert isinstance(lst.ok, bool)                     # may be False on a headless CI session
    if lst.ok:
        assert lst.registered == ["Ctrl+Alt+Shift+F24"] and lst.is_running()
    lst.stop()
    assert not lst.is_running()
    assert time.monotonic() - t0 < 5.0
    # the same key can be registered again after stop (it was unregistered)
    again = HotkeyListener({"Ctrl+Alt+Shift+F24": lambda: None})
    again.start()
    assert again.ok == lst.ok or not lst.ok
    again.stop()
