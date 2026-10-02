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
from dataclasses import dataclass, field, replace
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
    ring_every: int = 0      # roster matcher: ring proposals every N frames (0 = module default)
    stack_every: int = 0     # roster matcher: stack proposals (verifier) every N frames (0 = default)
    load: str = "normal"     # self-check load level (LOAD_LEVELS): "normal" | "allege" | "minimal"
    extras: dict[str, Any] = field(default_factory=dict)


#: Measured with ``tools/perf_budget.py`` (real engine + overlay threads, 60 s) and the detection gym
#: (``tools/det_gym.py --quick``, quality unchanged): the generic ONNX extras every 2nd frame give
#: the same detections (they also run at once when something new appears) for ~3 % less CPU per
#: frame; calm detection 5 img/s instead of 6 (-17 % analysis CPU; 4 seeds x laning / custom skin
#: after warm-up: recall, precision, identity, ghosts within noise, position p95 +0.0007), bursts at
#: ``target_fps`` unchanged; the overlay is paced by what animates (overlay.py), 20 img/s only
#: while a toast slides.
PROFILES: dict[str, PerfProfile] = {
    "normal": PerfProfile("normal", calm_fps=5.0, burst_fps=12.0, overlay_fps=20.0, heavy_hz=2.0,
                          verify_s=1.0, onnx_every=1, cv_threads=2, onnx_threads=2, lost_every=4),
    "low_end": PerfProfile("low_end", calm_fps=4.0, burst_fps=8.0, overlay_fps=15.0, heavy_hz=1.0,
                           verify_s=1.5, onnx_every=4, cv_threads=1, onnx_threads=1, lost_every=8),
}

#: Load levels decided live by the self-check (selfcheck.py, rule "perf": detection starving) on
#: top of the budget's profile: "allege" then "minimal" cut the optional per-frame work.
LOAD_LEVELS: tuple[str, ...] = ("normal", "allege", "minimal")


def degraded(p: PerfProfile, level: int) -> PerfProfile:
    """``p`` with the cost cuts of load ``level`` (0 = unchanged): generic ONNX extras rarer, ring /
    stack proposals and the whole-map search of lost champions every Nth frame, minimap
    verification less often, overlay frame rate capped (15 then 12 img/s), coaching stages
    slower (staggered further apart). The detection rates are kept: the cuts make each analysis
    tick cheaper so that the rate is reached again."""
    lvl = max(0, min(len(LOAD_LEVELS) - 1, int(level)))
    if lvl == 0:
        return p
    if lvl == 1:
        return replace(p, onnx_every=max(4, 2 * p.onnx_every), ring_every=max(4, p.ring_every),
                       stack_every=max(4, p.stack_every), lost_every=max(8, p.lost_every),
                       verify_s=max(1.5, p.verify_s), overlay_fps=min(15.0, p.overlay_fps),
                       heavy_hz=min(1.0, p.heavy_hz), load=LOAD_LEVELS[1])
    return replace(p, onnx_every=max(16, p.onnx_every), ring_every=max(8, p.ring_every),
                   stack_every=max(8, p.stack_every), lost_every=max(12, p.lost_every),
                   verify_s=max(2.0, p.verify_s), overlay_fps=min(12.0, p.overlay_fps),
                   heavy_hz=min(0.5, p.heavy_hz), cv_threads=1, load=LOAD_LEVELS[2])


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
        #: self-check load level (0 normal, 1 allégé, 2 minimal, see :func:`degraded`)
        self.load_level = 0
        self._lock = threading.Lock()

    @property
    def profile(self) -> PerfProfile:
        p = PROFILES[self.name]
        tf = max(2.0, float(self.target_fps))
        base = PerfProfile(p.name, calm_fps=min(p.calm_fps, tf), burst_fps=min(p.burst_fps, tf),
                           overlay_fps=p.overlay_fps, heavy_hz=p.heavy_hz, verify_s=p.verify_s,
                           onnx_every=p.onnx_every, cv_threads=p.cv_threads, onnx_threads=p.onnx_threads,
                           lost_every=p.lost_every, ring_every=p.ring_every, stack_every=p.stack_every)
        return degraded(base, self.load_level)

    def set_load_level(self, level: int) -> bool:
        """Self-check load level (0..2) applied on top of the profile. True when it changed."""
        try:
            lvl = max(0, min(len(LOAD_LEVELS) - 1, int(level)))
        except (TypeError, ValueError):
            return False
        with self._lock:
            if lvl == self.load_level:
                return False
            self.load_level = lvl
        log.info("Performance load level: %s", LOAD_LEVELS[lvl])
        return True

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
                "heavy_hz": p.heavy_hz, "load": p.load}


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


# ======================================================================================
# Native thread pools, GC, per-thread CPU, "Performance" summary (tools/perf_budget.py)
# ======================================================================================
def limit_blas_threads(n: int = 1) -> dict[str, Any]:
    """Force numpy's OpenBLAS to ``n`` threads at run time (``openblas_set_num_threads``).

    ``treeaicoach/__init__`` sets ``OPENBLAS_NUM_THREADS=1`` before numpy loads; when numpy was
    imported first (a launcher, a test runner, a tool) OpenBLAS starts one busy-waiting worker per
    core: measured with ``tools/perf_budget.py`` on 4 cores, 3 workers burned 67 % of a core in
    steady state for the small matrix products of the detection. Returns ``{"blas": lib name,
    "threads": n}`` or ``{}`` when no OpenBLAS was found. Never raises."""
    out: dict[str, Any] = {}
    try:
        if "numpy" not in sys.modules:
            return out
        import ctypes
        import glob

        import numpy

        base = os.path.dirname(os.path.abspath(numpy.__file__))
        cands: list[str] = []
        for d in (os.path.join(base, os.pardir, "numpy.libs"), os.path.join(base, ".libs"), base):
            cands += glob.glob(os.path.join(d, "*openblas*"))
        for path in cands:
            try:
                lib = ctypes.CDLL(path)
            except OSError:
                continue
            for sym in ("openblas_set_num_threads64_", "openblas_set_num_threads", "openblas_set_num_threads_"):
                fn = getattr(lib, sym, None)
                if fn is None:
                    continue
                fn.argtypes = [ctypes.c_int]
                fn(int(max(1, n)))
                out = {"blas": os.path.basename(path), "threads": int(max(1, n))}
                APPLIED["blas_threads"] = out["threads"]
                return out
    except Exception:
        log.debug("limit_blas_threads failed", exc_info=True)
    return out


_gc_frozen = False


def freeze_gc_once() -> bool:
    """``gc.freeze()`` once, after the game's components are loaded (champion icons, templates,
    models, data tables): those long-lived objects move to the permanent generation, so a later
    full collection (a pause of the analysis AND overlay threads, GIL held) does not walk them
    again. True the first time. Never raises."""
    global _gc_frozen
    if _gc_frozen:
        return False
    try:
        import gc

        gc.collect()
        gc.freeze()
        _gc_frozen = True
        APPLIED["gc_frozen"] = gc.get_freeze_count()
        return True
    except Exception:
        return False


def _thread_seconds_win(native_ids: list[int]) -> dict[int, float]:
    import ctypes
    from ctypes import wintypes as wt

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenThread.restype = wt.HANDLE
    k.OpenThread.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    k.GetThreadTimes.argtypes = [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4
    k.CloseHandle.argtypes = [wt.HANDLE]
    out: dict[int, float] = {}
    for tid in native_ids:
        h = k.OpenThread(0x0800, False, int(tid))          # THREAD_QUERY_LIMITED_INFORMATION
        if not h:
            continue
        try:
            c, e, kt, ut = (wt.FILETIME() for _ in range(4))
            if k.GetThreadTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut)):
                out[int(tid)] = sum(((f.dwHighDateTime << 32) | f.dwLowDateTime) for f in (kt, ut)) / 1e7
        finally:
            k.CloseHandle(h)
    return out


def _thread_seconds_linux(native_ids: list[int]) -> dict[int, float]:
    tck = float(os.sysconf("SC_CLK_TCK")) if hasattr(os, "sysconf") else 100.0
    out: dict[int, float] = {}
    for tid in native_ids:
        try:
            with open(f"/proc/self/task/{int(tid)}/stat", encoding="ascii", errors="replace") as fh:
                stat = fh.read()
            rest = stat[stat.rindex(")") + 2:].split()
            out[int(tid)] = (int(rest[11]) + int(rest[12])) / tck
        except Exception:
            continue
    return out


#: Python thread name -> short name of the "Performance" summary (the rest is grouped by name).
THREAD_GROUPS = (("TreeAICoach-analysis", "analyse"), ("overlay", "overlay"), ("TreeAICoach-fx", "badges"),
                 ("TreeAICoach-poller", "api"), ("MainThread", "interface"), ("TreeAICoach-voice", "voix"),
                 ("treeai-beep", "voix"), ("TreeAICoach-tts", "voix"), ("TreeAICoach-ai", "ia"),
                 ("champ-select", "lcu"), ("TreeAI-hotkeys", "raccourcis"), ("TreeAICoach-diag", "diagnostic"))


def thread_group(name: str) -> str:
    for prefix, short in THREAD_GROUPS:
        if str(name).startswith(prefix):
            return short
    return "autres"


class ThreadCpuMeter:
    """CPU % of one core per Python thread group between two :meth:`sample` calls (>= 0.5 s apart),
    + ``"natif"`` = the process total minus the Python threads (OpenCV / onnxruntime / BLAS pools,
    audio). Windows: ``GetThreadTimes``; Linux: ``/proc``; elsewhere only the process total.
    Cheap (one ``OpenThread`` per Python thread). Never raises."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last: tuple[float, float, dict[int, tuple[str, float]]] | None = None
        self.percent: dict[str, float] = {}

    @staticmethod
    def _now_threads() -> dict[int, tuple[str, float]]:
        ths = [(int(th.native_id), th.name) for th in threading.enumerate()
               if getattr(th, "native_id", None) is not None]
        ids = [tid for tid, _ in ths]
        if sys.platform == "win32":
            secs = _thread_seconds_win(ids)
        elif sys.platform.startswith("linux"):
            secs = _thread_seconds_linux(ids)
        else:
            secs = {}
        return {tid: (name, secs[tid]) for tid, name in ths if tid in secs}

    def sample(self) -> dict[str, float]:
        try:
            now, proc = float(self._clock()), time.process_time()
            if self._last is not None and now - self._last[0] < 0.5:
                return self.percent
            cur = self._now_threads()
            if self._last is not None:
                t0, p0, prev = self._last
                dt = max(1e-6, now - t0)
                groups: dict[str, float] = {}
                py = 0.0
                for tid, (name, sec) in cur.items():
                    d = max(0.0, sec - prev.get(tid, (name, sec))[1])
                    py += d
                    g = thread_group(name)
                    groups[g] = groups.get(g, 0.0) + d
                if cur:
                    groups["natif"] = max(0.0, (proc - p0) - py)
                self.percent = {k: round(100.0 * v / dt, 1) for k, v in
                                sorted(groups.items(), key=lambda kv: -kv[1]) if v > 0}
            self._last = (now, proc, cur)
        except Exception:
            log.debug("ThreadCpuMeter.sample failed", exc_info=True)
        return self.percent


#: "Performance" summary thresholds: total CPU (% of one core) / overlay uploads per second.
PERF_CPU_OK, PERF_CPU_HIGH = 12.0, 25.0
PERF_PUSH_OK = 8.0


def performance_summary(health: dict[str, Any] | None, threads: dict[str, float] | None = None) -> dict[str, Any]:
    """What TreeAI costs the game right now (data for the launcher's status / diagnostic):
    ``{"level": "ok" | "eleve" | "lourd", "text", "cpu_pct", "threads", "detect_fps",
    "tick_ms", "tick_p95_ms", "overlay_fps", "overlay_uploads_per_s", "overlay_kb_per_s",
    "capture_per_s", "profile", "load", "blas_threads"}`` from ``engine.health()``. Pure;
    never raises (missing fields are None)."""
    h = health or {}
    out: dict[str, Any] = {}
    try:
        ov = h.get("overlay") or {}
        cap = h.get("capture") or {}
        tick = h.get("tick_ms") or {}
        bud = h.get("budget") or {}
        rate = h.get("detect_rate") or {}
        pushes = h.get("overlay_pushes") or {}
        cpu = h.get("cpu_percent")
        out = {"cpu_pct": cpu, "threads": dict(threads or {}),
               "detect_fps": rate.get("measured_fps"), "detect_target_fps": rate.get("target_fps"),
               "tick_ms": tick.get("mean"), "tick_p95_ms": tick.get("p95"),
               "overlay_fps": ov.get("fps"), "overlay_uploads_per_s": pushes.get("pushes_per_s"),
               "overlay_kb_per_s": pushes.get("kb_per_s"),
               "capture_per_s": cap.get("fps"), "capture_backend": h.get("capture_backend"),
               "profile": bud.get("profile"), "load": bud.get("load"),
               "blas_threads": APPLIED.get("blas_threads"),
               "priority_below_normal": APPLIED.get("priority")}
        level = "ok"
        if cpu is not None and cpu > PERF_CPU_HIGH:
            level = "lourd"
        elif (cpu is not None and cpu > PERF_CPU_OK) or (
                (out["overlay_uploads_per_s"] or 0.0) > PERF_PUSH_OK):
            level = "eleve"
        out["level"] = level
        parts = []
        if cpu is not None:
            parts.append(f"{cpu:.0f} % d'un cœur")
        if out["detect_fps"]:
            parts.append(f"analyse {out['detect_fps']:.0f} img/s")
        if out["overlay_uploads_per_s"] is not None:
            parts.append(f"overlay {out['overlay_uploads_per_s']:.0f} envois/s")
        head = {"ok": "Impact sur le jeu : faible", "eleve": "Impact sur le jeu : moyen",
                "lourd": "Impact sur le jeu : élevé"}[level]
        out["text"] = head + (" (" + ", ".join(parts) + ")" if parts else "")
    except Exception:
        log.debug("performance_summary failed", exc_info=True)
    return out
