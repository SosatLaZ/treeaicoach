"""Global hotkeys (F9 "where is the enemy jungler?", F10 mute, F11 overlay) via ``RegisterHotKey``.

Only the standard ``RegisterHotKey`` API is used (the one Discord / OBS use): no low-level
keyboard hook, no key injection, nothing sent to the game. Windows delivers ``WM_HOTKEY``
messages to the thread that registered the keys, so :class:`HotkeyListener` owns a dedicated
thread with its own message loop (``GetMessageW``); :meth:`HotkeyListener.stop` posts
``WM_QUIT`` to it (``PostThreadMessageW``) and the thread unregisters its keys before exiting.

Key names: ``"F1"`` .. ``"F24"`` with optional modifiers ``Ctrl`` / ``Alt`` / ``Shift``
(also ``Maj``) / ``Win``, e.g. ``"Ctrl+F9"``, ``"alt + shift + f10"``. ``""`` / ``"off"`` = disabled.
Callbacks run on the listener thread and must be quick (they should only enqueue work).
Off Windows the listener is a no-op (``ok`` stays False). Nothing here ever raises.
"""

from __future__ import annotations

import logging
import re
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

# Win32 constants
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000          # no WM_HOTKEY auto-repeat while the key is held down
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
PM_NOREMOVE = 0x0000
VK_F1 = 0x70                   # VK_F1..VK_F24 = 0x70..0x87
MAX_FKEY = 24
FIRST_HOTKEY_ID = 0x0A11       # application ids must be in 0x0000..0xBFFF

START_TIMEOUT_S = 2.0          # start() waits this long for the registrations
STOP_TIMEOUT_S = 2.0

#: Canonical modifier names, in display order, with their Win32 flag.
MODIFIERS: tuple[tuple[str, int], ...] = (("Ctrl", MOD_CONTROL), ("Alt", MOD_ALT),
                                          ("Shift", MOD_SHIFT), ("Win", MOD_WIN))
_MOD_ALIASES: dict[str, str] = {
    "ctrl": "Ctrl", "control": "Ctrl", "ctl": "Ctrl", "strg": "Ctrl",
    "alt": "Alt", "altgr": "Alt",
    "shift": "Shift", "maj": "Shift", "majuscule": "Shift",
    "win": "Win", "windows": "Win", "super": "Win", "meta": "Win",
}
_DISABLED_WORDS = frozenset({"", "none", "off", "aucun", "aucune", "désactivé", "desactive", "disabled"})
_FKEY_RE = re.compile(r"f([1-9]|1[0-9]|2[0-4])")
_SPLIT_RE = re.compile(r"\s*\+\s*")
MAX_NAME_LEN = 64


@dataclass(frozen=True)
class Hotkey:
    """A parsed key combination."""

    name: str          # canonical form, e.g. "Ctrl+F9"
    vk: int            # virtual-key code
    modifiers: int     # MOD_* flags (without MOD_NOREPEAT)


def parse_hotkey(name: Any) -> Hotkey | None:
    """Parse ``"F9"`` / ``"Ctrl+Shift+F10"`` (case and spaces ignored). ``None`` if invalid or disabled."""
    if not isinstance(name, str) or len(name) > MAX_NAME_LEN:
        return None
    text = name.strip()
    if text.casefold() in _DISABLED_WORDS:
        return None
    parts = [p for p in _SPLIT_RE.split(text.casefold()) if p]
    if not parts or len(parts) > 1 + len(MODIFIERS):
        return None
    m = _FKEY_RE.fullmatch(parts[-1])
    if m is None:
        return None
    number = int(m.group(1))
    mods: set[str] = set()
    for p in parts[:-1]:
        canon = _MOD_ALIASES.get(p)
        if canon is None or canon in mods:
            return None
        mods.add(canon)
    flags = 0
    labels: list[str] = []
    for label, flag in MODIFIERS:
        if label in mods:
            flags |= flag
            labels.append(label)
    labels.append(f"F{number}")
    return Hotkey(name="+".join(labels), vk=VK_F1 + number - 1, modifiers=flags)


def normalize_hotkey(name: Any) -> str | None:
    """Canonical key name (``"ctrl + f9"`` -> ``"Ctrl+F9"``), ``""`` if disabled, ``None`` if invalid."""
    if not isinstance(name, str):
        return None
    if len(name) <= MAX_NAME_LEN and name.strip().casefold() in _DISABLED_WORDS:
        return ""
    hk = parse_hotkey(name)
    return hk.name if hk is not None else None


def is_supported() -> bool:
    """True where global hotkeys can work (Windows)."""
    return sys.platform == "win32"


class HotkeyListener:
    """Registers global hotkeys on a dedicated thread and calls the bound callbacks.

    ``bindings`` maps key names to callables, e.g. ``{"F9": engine_where_is_jungler}``.
    Invalid / disabled names are ignored (logged). After :meth:`start` returns, ``ok`` is True
    if every valid key was registered; ``registered`` / ``failed`` list the key names
    (a key already taken by another application ends up in ``failed``).
    """

    def __init__(self, bindings: Mapping[str, Callable[[], None]] | None = None) -> None:
        self.ok: bool = False
        self.registered: list[str] = []
        self.failed: list[str] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._bindings: dict[int, tuple[Hotkey, Callable[[], None]]] = {}
        self._set_bindings(bindings)

    # -- public ---------------------------------------------------------------------------

    @property
    def keys(self) -> list[str]:
        """Canonical names of the valid bindings."""
        return [hk.name for hk, _cb in self._bindings.values()]

    def start(self) -> None:
        """Register the keys (Windows) and wait briefly for the result. Idempotent, never raises."""
        try:
            with self._lock:
                if self._thread is not None and self._thread.is_alive():
                    return
                self._reset_status()
                if not is_supported():
                    log.info("Global hotkeys are only available on Windows")
                    return
                if not self._bindings:
                    self.ok = True          # nothing to register, nothing failed
                    return
                self._ready.clear()
                self._thread_id = None
                self._thread = threading.Thread(target=self._run, name="TreeAI-hotkeys", daemon=True)
                self._thread.start()
                thread = self._thread
            if not self._ready.wait(START_TIMEOUT_S):
                log.warning("Hotkey thread did not report readiness in %.1f s", START_TIMEOUT_S)
            elif not thread.is_alive() and not self.ok:
                log.debug("Hotkey thread exited during start")
        except Exception:
            log.exception("Cannot start the hotkey listener")
            self.ok = False

    def stop(self) -> None:
        """Unregister the keys and end the thread. Idempotent, never raises."""
        try:
            with self._lock:
                thread, tid = self._thread, self._thread_id
                self._thread = None
            if thread is None:
                return
            if thread.is_alive():
                # The thread may still be registering: wait until its queue exists.
                self._ready.wait(START_TIMEOUT_S)
                tid = self._thread_id if tid is None else tid
                if tid is not None and not _post_quit(tid):
                    log.warning("Cannot post WM_QUIT to the hotkey thread")
                thread.join(STOP_TIMEOUT_S)
                if thread.is_alive():
                    log.warning("Hotkey thread did not stop in %.1f s", STOP_TIMEOUT_S)
            self.ok = False
            self.registered = []
        except Exception:
            log.exception("Cannot stop the hotkey listener")

    def set_bindings(self, bindings: Mapping[str, Callable[[], None]] | None) -> None:
        """Replace the bindings; restarts the listener if it was running. Never raises."""
        try:
            running = self.is_running()
            if running:
                self.stop()
            self._set_bindings(bindings)
            if running:
                self.start()
        except Exception:
            log.exception("Cannot change the hotkey bindings")

    def is_running(self) -> bool:
        """True while the listener thread is alive."""
        t = self._thread
        return t is not None and t.is_alive()

    def __enter__(self) -> HotkeyListener:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- internals ------------------------------------------------------------------------

    def _reset_status(self) -> None:
        self.ok = False
        self.registered = []
        self.failed = []

    def _set_bindings(self, bindings: Mapping[str, Callable[[], None]] | None) -> None:
        table: dict[int, tuple[Hotkey, Callable[[], None]]] = {}
        seen: set[tuple[int, int]] = set()
        try:
            items = list(bindings.items()) if isinstance(bindings, Mapping) else []
        except Exception:
            items = []
        for name, callback in items:
            hk = parse_hotkey(name)
            if hk is None:
                if normalize_hotkey(name) != "":
                    log.warning("Ignoring invalid hotkey name %r", name)
                continue
            if not callable(callback):
                log.warning("Ignoring hotkey %s: callback is not callable", hk.name)
                continue
            if (hk.vk, hk.modifiers) in seen:
                log.warning("Ignoring duplicate hotkey %s", hk.name)
                continue
            seen.add((hk.vk, hk.modifiers))
            table[FIRST_HOTKEY_ID + len(table)] = (hk, callback)
        self._bindings = table

    def _dispatch(self, hotkey_id: int) -> None:
        """Run the callback bound to ``hotkey_id`` (exceptions logged, never propagated)."""
        entry = self._bindings.get(hotkey_id)
        if entry is None:
            return
        hk, callback = entry
        try:
            callback()
        except Exception:
            log.exception("Hotkey %s callback failed", hk.name)

    def _run(self) -> None:
        """Thread body: register, pump messages until WM_QUIT, unregister."""
        registered_ids: list[int] = []
        user32 = None
        try:
            import ctypes
            from ctypes import wintypes

            user32 = _user32()
            msg = wintypes.MSG()
            # Create this thread's message queue before anyone posts WM_QUIT to it.
            user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)
            self._thread_id = _current_thread_id()
            for hid, (hk, _cb) in list(self._bindings.items()):
                if user32.RegisterHotKey(None, hid, hk.modifiers | MOD_NOREPEAT, hk.vk):
                    registered_ids.append(hid)
                    self.registered.append(hk.name)
                else:
                    err = _last_error()
                    self.failed.append(hk.name)
                    log.warning("Cannot register hotkey %s (error %d: already used by another program?)",
                                hk.name, err)
            self.ok = not self.failed
            if registered_ids:
                log.info("Hotkeys registered: %s", ", ".join(self.registered))
            self._ready.set()
            if not registered_ids:
                return
            while True:
                r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if r == 0:          # WM_QUIT
                    break
                if r == -1:
                    log.error("GetMessageW failed (error %d); hotkeys disabled", _last_error())
                    self.ok = False
                    break
                if msg.message == WM_HOTKEY:
                    self._dispatch(int(msg.wParam))
        except Exception:
            log.exception("Hotkey thread failed")
            self.ok = False
        finally:
            if user32 is not None:
                for hid in registered_ids:
                    try:
                        user32.UnregisterHotKey(None, hid)
                    except Exception:
                        pass
            self._ready.set()


_USER32: Any = None


def _last_error() -> int:
    """``GetLastError()`` of the last user32 call (0 where unavailable)."""
    try:
        import ctypes

        return int(ctypes.get_last_error())  # type: ignore[attr-defined]
    except Exception:
        return 0


def _user32() -> Any:
    """``user32`` with argtypes declared (64-bit safe). Windows only."""
    global _USER32
    if _USER32 is None:
        import ctypes
        from ctypes import wintypes

        u = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
        u.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        u.RegisterHotKey.restype = wintypes.BOOL
        u.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        u.UnregisterHotKey.restype = wintypes.BOOL
        u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        u.GetMessageW.restype = wintypes.BOOL
        u.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
                                   wintypes.UINT, wintypes.UINT]
        u.PeekMessageW.restype = wintypes.BOOL
        u.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.PostThreadMessageW.restype = wintypes.BOOL
        _USER32 = u
    return _USER32


def _current_thread_id() -> int:
    """Win32 id of the calling thread (the one ``PostThreadMessageW`` needs)."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    return int(kernel32.GetCurrentThreadId())


def _post_quit(thread_id: int) -> bool:
    """Post WM_QUIT to a thread's message queue (Windows)."""
    try:
        return bool(_user32().PostThreadMessageW(thread_id, WM_QUIT, 0, 0))
    except Exception:
        log.debug("PostThreadMessageW failed", exc_info=True)
        return False
