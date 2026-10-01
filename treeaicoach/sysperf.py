"""Process resources and performance budget: the game must always win.

* :func:`lower_process_priority` - ``BELOW_NORMAL_PRIORITY_CLASS`` and (Windows 11) the EcoQoS
  "power throttling" hint, so the OS schedules League first when the CPU is busy;
* :func:`hardware_profile` / :class:`PerfBudget` - decides between the ``"normal"`` and the
  ``"low_end"`` budget (few CPU cores, or analysis ticks measured too slow during the first
  30 s of a game) and holds the derived knobs (detection rates, overlay fps, thread counts...);
* :class:`RollingStats` (p50 / p95 of recent samples) and :class:`CpuMeter` (CPU % of one core
  used by this process) for the health monitor.

Pure Python + ctypes; importable everywhere; nothing raises.
"""

from __future__ import annotations

import logging
import math
import os
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1
PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1
ProcessPowerThrottling = 4          # PROCESS_INFORMATION_CLASS

#: Logical CPUs at or below this count -> low-end budget from the start.
LOW_END_CORES = 4
#: Measured over the first :data:`MEASURE_S` seconds of analysis: a mean tick above this (ms)
#: (or a p95 above :data:`LOW_END_TICK_P95_MS`) switches to the low-end budget.
LOW_END_TICK_MS = 30.0
LOW_END_TICK_P95_MS = 60.0
MEASURE_S = 30.0
MEASURE_MIN_TICKS = 40
#: What :func:`lower_process_priority` applied (diagnostic bundles).
APPLIED: dict[str, Any] = {}


def lower_process_priority(eco_qos: bool = True) -> dict[str, Any]:
    """Below-normal priority (+ EcoQoS when ``eco_qos``) for the whole process. Windows only.

    Returns what was applied ``{"priority": bool, "eco_qos": bool}``. Never raises.
    """
    out = {"priority": False, "eco_qos": False}
    if sys.platform != "win32":
        return out
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        proc = k32.GetCurrentProcess()
        k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        out["priority"] = bool(k32.SetPriorityClass(proc, BELOW_NORMAL_PRIORITY_CLASS))
        if eco_qos:
            class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
                _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG),
                            ("StateMask", wintypes.ULONG)]

            st = PROCESS_POWER_THROTTLING_STATE(PROCESS_POWER_THROTTLING_CURRENT_VERSION,
                                                PROCESS_POWER_THROTTLING_EXECUTION_SPEED,
                                                PROCESS_POWER_THROTTLING_EXECUTION_SPEED)
            fn = getattr(k32, "SetProcessInformation", None)     # Windows 8+
            if fn is not None:
                fn.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
                out["eco_qos"] = bool(fn(proc, ProcessPowerThrottling, ctypes.byref(st), ctypes.sizeof(st)))
        log.info("Process priority: below normal %s, EcoQoS %s", out["priority"], out["eco_qos"])
    except Exception:
        log.debug("lower_process_priority failed", exc_info=True)
    APPLIED.update(out)
    return out


def cpu_count() -> int:
    try:
        return max(1, int(os.cpu_count() or 1))
    except Exception:
        return 1


def cpu_name() -> str:
    """CPU model string (registry on Windows, /proc/cpuinfo on Linux); "" when unknown."""
    try:
        if sys.platform == "win32":
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    try:
        import platform

        return platform.processor() or ""
    except Exception:
        return ""


def hardware_profile() -> dict[str, Any]:
    """``{"cores", "cpu", "low_end_hint"}`` (the core-count heuristic)."""
    n = cpu_count()
    return {"cores": n, "cpu": cpu_name(), "low_end_hint": n <= LOW_END_CORES}


class RollingStats:
    """Recent samples (bounded) with mean / p50 / p95. Thread-safe enough (deque appends)."""

    def __init__(self, maxlen: int = 240) -> None:
        self._d: deque[float] = deque(maxlen=int(maxlen))

    def add(self, x: float) -> None:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return
        if math.isfinite(v):
            self._d.append(v)

    def __len__(self) -> int:
        return len(self._d)

    def clear(self) -> None:
        self._d.clear()

    def pct(self, q: float) -> float | None:
        vals = sorted(self._d)
        if not vals:
            return None
        i = min(len(vals) - 1, max(0, int(round(q * (len(vals) - 1)))))
        return vals[i]

    def mean(self) -> float | None:
        vals = list(self._d)
        return sum(vals) / len(vals) if vals else None

    def summary(self, digits: int = 1) -> dict[str, float | None]:
        def r(x: float | None) -> float | None:
            return None if x is None else round(x, digits)

        return {"mean": r(self.mean()), "p50": r(self.pct(0.5)), "p95": r(self.pct(0.95)),
                "max": r(max(self._d) if self._d else None), "n": len(self._d)}


class RateMeter:
    """Events per second over a sliding window of timestamps."""

    def __init__(self, maxlen: int = 120) -> None:
        self._t: deque[float] = deque(maxlen=int(maxlen))

    def tick(self, t: float) -> None:
        self._t.append(float(t))

    def rate(self, now: float | None = None, window: float = 5.0) -> float:
        ts = [x for x in self._t if now is None or now - x <= window]
        if len(ts) < 2 or ts[-1] <= ts[0]:
            return 0.0
        return (len(ts) - 1) / (ts[-1] - ts[0])


class CpuMeter:
    """CPU used by this process, in % of ONE core, between two :meth:`sample` calls."""

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 cpu_time: Callable[[], float] = time.process_time) -> None:
        self._clock, self._cpu = clock, cpu_time
        self._last: tuple[float, float] | None = None
        self.percent: float | None = None

    def sample(self) -> float | None:
        try:
            now, cpu = float(self._clock()), float(self._cpu())
        except Exception:
            return self.percent
        if self._last is not None and now - self._last[0] >= 0.5:
            self.percent = round(100.0 * max(0.0, cpu - self._last[1]) / (now - self._last[0]), 1)
            self._last = (now, cpu)
        elif self._last is None:
            self._last = (now, cpu)
        return self.percent


@dataclass
class PerfProfile:
    """Knobs of one budget (see :data:`PROFILES`)."""

    name: str
    calm_fps: float          # detection rate when nothing threatens me
    burst_fps: float         # ... when an enemy is near / a threat is active
    overlay_fps: float       # minimap layer render rate (predicted positions)
    heavy_hz: float          # coaching stages (coach, Tab, tips, items...) per second
    verify_s: float          # minimap re-verification period
    onnx_every: int          # generic ONNX/classic detector run every N frames (HybridDetector)
    cv_threads: int          # cv2.setNumThreads
    onnx_threads: int        # onnxruntime intra-op threads (applies to detectors created after)
    lost_every: int = 4      # roster matcher: whole-map search of lost champions every N frames
    extras: dict[str, Any] = field(default_factory=dict)


PROFILES: dict[str, PerfProfile] = {
    "normal": PerfProfile("normal", calm_fps=6.0, burst_fps=12.0, overlay_fps=30.0, heavy_hz=2.0,
                          verify_s=1.0, onnx_every=1, cv_threads=2, onnx_threads=2, lost_every=4),
    "low_end": PerfProfile("low_end", calm_fps=4.0, burst_fps=8.0, overlay_fps=15.0, heavy_hz=1.0,
                           verify_s=1.5, onnx_every=4, cv_threads=1, onnx_threads=1, lost_every=8),
}


class PerfBudget:
    """Chooses and holds the active :class:`PerfProfile`.

    ``mode``: ``"normal"`` / ``"low_end"`` (forced) or ``"auto"``: low-end when the machine has
    at most :data:`LOW_END_CORES` logical CPUs, or when the analysis ticks measured during the
    first :data:`MEASURE_S` s of detection are too slow (mean > :data:`LOW_END_TICK_MS` ms or
    p95 > :data:`LOW_END_TICK_P95_MS` ms). Once low-end, it stays low-end for the session.
    ``burst_fps`` never exceeds the user's ``target_fps``.
    """

    def __init__(self, mode: str = "auto", cores: int | None = None, target_fps: float = 12.0) -> None:
        self.mode = mode if mode in ("auto", "normal", "low_end") else "auto"
        self.cores = cpu_count() if cores is None else max(1, int(cores))
        self.target_fps = float(target_fps)
        self.reason = ""
        self._ticks = RollingStats(maxlen=600)
        self._t0: float | None = None
        self._decided = self.mode != "auto"
        if self.mode == "low_end":
            self.name, self.reason = "low_end", "forced"
        elif self.mode == "normal":
            self.name, self.reason = "normal", "forced"
        elif self.cores <= LOW_END_CORES:
            self.name, self.reason, self._decided = "low_end", f"{self.cores} CPU logiques", True
        else:
            self.name = "normal"
        self._lock = threading.Lock()

    @property
    def profile(self) -> PerfProfile:
        p = PROFILES[self.name]
        tf = max(2.0, float(self.target_fps))
        return PerfProfile(p.name, calm_fps=min(p.calm_fps, tf), burst_fps=min(p.burst_fps, tf),
                           overlay_fps=p.overlay_fps, heavy_hz=p.heavy_hz, verify_s=p.verify_s,
                           onnx_every=p.onnx_every, cv_threads=p.cv_threads, onnx_threads=p.onnx_threads,
                           lost_every=p.lost_every)

    def observe_tick(self, t: float, ms: float) -> bool:
        """Feed one analysis tick (engine time ``t``, cost ``ms``). True when the budget just
        switched to low-end."""
        with self._lock:
            if self._decided:
                return False
            if self._t0 is None:
                self._t0 = float(t)
            self._ticks.add(ms)
            if float(t) - self._t0 < MEASURE_S or len(self._ticks) < MEASURE_MIN_TICKS:
                return False
            self._decided = True
            mean, p95 = self._ticks.mean() or 0.0, self._ticks.pct(0.95) or 0.0
            if mean > LOW_END_TICK_MS or p95 > LOW_END_TICK_P95_MS:
                self.name = "low_end"
                self.reason = f"analyse lente ({mean:.0f} ms en moyenne, p95 {p95:.0f} ms)"
                log.info("Performance budget: low-end (%s)", self.reason)
                return True
            self.reason = f"mesuré {mean:.0f} ms / tick"
            return False

    def describe(self) -> dict[str, Any]:
        p = self.profile
        return {"mode": self.mode, "profile": p.name, "reason": self.reason, "cores": self.cores,
                "calm_fps": p.calm_fps, "burst_fps": p.burst_fps, "overlay_fps": p.overlay_fps,
                "heavy_hz": p.heavy_hz}


def precise_sleep(seconds: float, stop: threading.Event | None = None, chunk: float = 0.05) -> bool:
    """Sleep ``seconds`` with ``time.sleep`` (high-resolution waitable timer on Windows with
    Python 3.11, unlike ``Event.wait`` which is rounded to the 15.6 ms system tick), waking up
    every ``chunk`` s to honour ``stop``. Returns True if ``stop`` was set."""
    end = time.perf_counter() + max(0.0, float(seconds))
    while True:
        if stop is not None and stop.is_set():
            return True
        left = end - time.perf_counter()
        if left <= 0:
            return False
        time.sleep(min(left, chunk))
