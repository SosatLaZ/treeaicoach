"""Is World of Warcraft (Forever / Classic / retail) running? Is League of Legends running?

Only the list of process NAMES is read (Toolhelp32 snapshot on Windows, ``/proc`` elsewhere for tests):
no handle is opened on the game, nothing is read from its memory. Never raises.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from typing import Callable, Iterable

log = logging.getLogger(__name__)

#: WoW clients: Wow.exe (retail / some launchers), WowClassic.exe (Classic family, Forever uses the
#: Classic client), test / beta builds (WowT, WowB, WowClassicT, WowClassicB), a possible WowForever.exe,
#: and the old "-64" names.
WOW_RE = re.compile(r"^wow(classic|forever|era)?(t|b|ptr|beta)?(-64)?\.exe$", re.IGNORECASE)
#: The League game itself (not the client: the client alone is not a game in progress).
LOL_GAME_NAMES = frozenset({"league of legends.exe"})
LOL_CLIENT_NAMES = frozenset({"leagueclient.exe", "leagueclientux.exe"})
CACHE_S = 2.0

_cache: tuple[float, frozenset[str]] | None = None


def _windows_names() -> list[str]:
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)       # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        return []
    names: list[str] = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            names.append(entry.szExeFile)
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return names


def _proc_names() -> list[str]:
    names: list[str] = []
    try:
        for pid in os.listdir("/proc"):
            if pid.isdigit():
                try:
                    with open(f"/proc/{pid}/comm", encoding="utf-8", errors="replace") as fh:
                        names.append(fh.read().strip())
                except OSError:
                    pass
    except OSError:
        pass
    return names


def process_names(lister: Callable[[], Iterable[str]] | None = None, *, cached: bool = True) -> frozenset[str]:
    """Lower-cased names of the running processes (cached ``CACHE_S``). Never raises."""
    global _cache
    now = time.monotonic()
    if lister is None and cached and _cache is not None and now - _cache[0] < CACHE_S:
        return _cache[1]
    try:
        raw = lister() if lister is not None else (_windows_names() if sys.platform == "win32" else _proc_names())
        names = frozenset(str(n).strip().lower() for n in raw if n)
    except Exception:
        log.debug("process list failed", exc_info=True)
        names = frozenset()
    if lister is None:
        _cache = (now, names)
    return names


def wow_process(names: Iterable[str] | None = None) -> str | None:
    """The running WoW client's exe name (lower case), or None."""
    for n in sorted(process_names() if names is None else (str(x).lower() for x in names)):
        if WOW_RE.match(n):
            return n
    return None


def is_wow_running(names: Iterable[str] | None = None) -> bool:
    return wow_process(names) is not None


def is_lol_running(names: Iterable[str] | None = None) -> bool:
    """True while a League GAME runs (the client alone does not count)."""
    ns = process_names() if names is None else {str(x).lower() for x in names}
    return bool(LOL_GAME_NAMES & set(ns))


def is_lol_client_running(names: Iterable[str] | None = None) -> bool:
    ns = process_names() if names is None else {str(x).lower() for x in names}
    return bool((LOL_CLIENT_NAMES | LOL_GAME_NAMES) & set(ns))


def wow_mode_wanted(names: Iterable[str] | None = None, *, lol_in_game: bool = False) -> bool:
    """WoW mode: WoW runs and no League game is running (the League client alone may stay open)."""
    ns = list(process_names() if names is None else names)
    return is_wow_running(ns) and not lol_in_game and not is_lol_running(ns)
