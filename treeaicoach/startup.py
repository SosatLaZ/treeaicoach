"""Startup timing: seconds since the process was created (Windows: GetProcessTimes, so the PyInstaller
bootloader and the bootstrapper's extraction are included) and named marks written to the log.

``mark("name")`` is cheap and never raises; :func:`summary` gives "name=1.23s ..." for the log line
"Startup: ..." written once the main window has painted (ui.CoachApp.run)."""

from __future__ import annotations

import logging
import os
import sys
import time

log = logging.getLogger(__name__)

_T_IMPORT = time.perf_counter()
_MARKS: list[tuple[str, float]] = []


def _windows_age() -> float | None:
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32
        ft = [wintypes.FILETIME() for _ in range(4)]
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        if not k32.GetProcessTimes(k32.GetCurrentProcess(), *[ctypes.byref(f) for f in ft]):
            return None
        created = (ft[0].dwHighDateTime << 32) | ft[0].dwLowDateTime        # 100 ns since 1601
        now = wintypes.FILETIME()
        k32.GetSystemTimeAsFileTime(ctypes.byref(now))
        cur = (now.dwHighDateTime << 32) | now.dwLowDateTime
        age = (cur - created) / 1e7
        return age if 0.0 <= age < 86400.0 * 30 else None
    except Exception:
        return None


def process_age() -> float:
    """Seconds since this process started (since this module was imported when unknown)."""
    if sys.platform == "win32":
        age = _windows_age()
        if age is not None:
            return age
    return time.perf_counter() - _T_IMPORT


def launcher_age() -> float | None:
    """Seconds since the bootstrapper (TreeAICoach.exe stub) started, when it passed its start time."""
    try:
        t0 = float(os.environ.get("TREEAICOACH_LAUNCH_T0", ""))
    except ValueError:
        return None
    age = time.time() - t0
    return age if 0.0 <= age < 3600.0 else None


def mark(name: str) -> float:
    """Record ``name`` at the current process age; returns it. Never raises."""
    try:
        t = process_age()
        _MARKS.append((str(name), t))
        return t
    except Exception:
        return 0.0


def summary() -> str:
    parts = [f"{n}={t:.2f}s" for n, t in _MARKS]
    la = launcher_age()
    if la is not None:
        parts.append(f"since_launcher={la:.2f}s")
    return " ".join(parts)


def log_summary(final: str = "first_paint") -> None:
    mark(final)
    log.info("Startup: %s", summary())
