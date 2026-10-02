"""Performance budget: what TreeAI costs the game, measured on the real pipeline.

Runs the REAL engine threads (analysis + Live Client poller, ``CoachEngine.start``) on a real
in-game screenshot (``tests/fixtures/ingame4_2000x1125.jpg``, game g1 of the real ground truth:
the minimap, the HUD portrait and the ability bar are cropped from it by a fake capture that counts
every grab) with a fake Live Client (the real ``allgamedata`` sample, game clock running), plus the
REAL overlay loop (``overlay.OverlayManager``: layout, renders, dirty tracking, pacing) drawing into
fake layered windows (real ``UpdateLayeredWindow`` windows with ``--real-windows`` on Windows / Wine),
and a simulated launcher polling ``engine.get_status()`` like the UI. After a warm-up it reports, for
the steady state:

* CPU % of ONE core per thread (Python threads by name + native pools: OpenBLAS, onnxruntime,
  OpenCV...) and for the whole process;
* analysis ticks: count, rate, mean / p50 / p95 / max ms;
* overlay: loop frames / s, window pixel uploads / s and KB / s (per layer), alpha-only updates,
  topmost re-assertions;
* capture: grabs / s and kpx / s (minimap / HUD patches / full window);
* garbage collector: gen-2 collections and the longest pause.

``--scenario demo`` replaces the screenshot by the demo game (moving champions, a gank, alerts,
toasts: the overlay's busy case); the cost of rendering the synthetic minimap is subtracted from the
analysis thread.

    python tools/perf_budget.py [--seconds 60] [--warmup 15] [--scenario real|demo] [--json out.json]
                                [--ui-hz 1] [--real-windows] [--perf-mode auto|normal|low_end]

Targets (steady state, calm): total <= ~10 % of one core, overlay uploads <= 5 / s.
"""

from __future__ import annotations

import argparse
import gc
import json
import math
import os
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("TREEAICOACH_HOME", tempfile.mkdtemp(prefix="treeai_perf_"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import treeaicoach  # noqa: E402,F401  (first: single-threaded BLAS before numpy loads, like the app)

FIX = ROOT / "tests" / "fixtures"
SCREEN = FIX / "ingame4_2000x1125.jpg"
#: game g1 of tests/fixtures/real/ground_truth.json (the screenshot's game): me = Garen (ORDER)
ROSTER = {"ORDER": ["Garen", "MasterYi", "Mel", "Ezreal", "Brand"],
          "CHAOS": ["Vladimir", "Kindred", "Ekko", "Seraphine", "Thresh"]}
GAME_T0 = 925.0          # 15:25 (the screenshot)
TARGET_CPU_PCT = 10.0
TARGET_PUSHES = 5.0


# ------------------------------------------------------------------------------ per-thread CPU
class ThreadCpu:
    """CPU seconds of every thread of this process: ``{tid: (name, seconds)}``.

    Linux: ``/proc/self/task/<tid>/stat`` (utime + stime). Windows (and Wine):
    ``CreateToolhelp32Snapshot`` + ``GetThreadTimes``. Python threads are named after
    ``threading.Thread.name``, the others after the OS name (``comm``) or "native"."""

    def __init__(self) -> None:
        self.win = sys.platform == "win32"
        self._names: dict[int, str] = {}
        if self.win:
            self._init_win()
        else:
            self._tck = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

    def _py_names(self) -> dict[int, str]:
        out = {}
        for th in threading.enumerate():
            nid = getattr(th, "native_id", None)
            if nid is not None:
                out[int(nid)] = th.name
        return out

    def sample(self) -> dict[int, tuple[str, float]]:
        py = self._py_names()
        self._names.update(py)
        try:
            raw = self._sample_win() if self.win else self._sample_linux()
        except Exception:
            raw = {}
        return {tid: (self._names.get(tid) or name, sec) for tid, (name, sec) in raw.items()}

    # linux
    def _sample_linux(self) -> dict[int, tuple[str, float]]:
        out = {}
        base = Path("/proc/self/task")
        for d in base.iterdir():
            try:
                tid = int(d.name)
                stat = (d / "stat").read_text()
                rest = stat[stat.rindex(")") + 2:].split()
                ut, st = int(rest[11]), int(rest[12])
                comm = (d / "comm").read_text().strip()
                out[tid] = (f"native:{comm}", (ut + st) / float(self._tck))
            except Exception:
                continue
        return out

    # windows
    def _init_win(self) -> None:
        import ctypes
        from ctypes import wintypes as wt

        class THREADENTRY32(ctypes.Structure):
            _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ThreadID", wt.DWORD),
                        ("th32OwnerProcessID", wt.DWORD), ("tpBasePri", wt.LONG),
                        ("tpDeltaPri", wt.LONG), ("dwFlags", wt.DWORD)]

        self._ct, self._wt, self._TE = ctypes, wt, THREADENTRY32
        k = self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateToolhelp32Snapshot.restype = wt.HANDLE
        k.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
        k.Thread32First.argtypes = k.Thread32Next.argtypes = [wt.HANDLE, ctypes.POINTER(THREADENTRY32)]
        k.OpenThread.restype = wt.HANDLE
        k.OpenThread.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k.GetThreadTimes.argtypes = [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4
        k.CloseHandle.argtypes = [wt.HANDLE]

    def _sample_win(self) -> dict[int, tuple[str, float]]:
        ct, wt, k = self._ct, self._wt, self._k32
        pid = os.getpid()
        snap = k.CreateToolhelp32Snapshot(0x4, 0)
        tids = []
        try:
            te = self._TE()
            te.dwSize = ct.sizeof(self._TE)
            ok = k.Thread32First(snap, ct.byref(te))
            while ok:
                if te.th32OwnerProcessID == pid:
                    tids.append(int(te.th32ThreadID))
                ok = k.Thread32Next(snap, ct.byref(te))
        finally:
            k.CloseHandle(snap)
        out = {}
        for tid in tids:
            h = k.OpenThread(0x0800, False, tid)       # THREAD_QUERY_LIMITED_INFORMATION
            if not h:
                continue
            try:
                c, e, kt, ut = (wt.FILETIME() for _ in range(4))
                if k.GetThreadTimes(h, ct.byref(c), ct.byref(e), ct.byref(kt), ct.byref(ut)):
                    sec = sum(((f.dwHighDateTime << 32) | f.dwLowDateTime) for f in (kt, ut)) / 1e7
                    out[tid] = ("native", sec)
            finally:
                k.CloseHandle(h)
        return out


def _group(name: str) -> str:
    """Thread name -> report group (pools of native threads summed)."""
    if name.startswith("native:"):
        comm = name[7:]
        if comm.startswith(("python", "pytest")):
            return "main"
        return f"native:{comm.rstrip('0123456789-_ ')}" or "native"
    if name == "MainThread":
        return "main"
    return name.rstrip("0123456789-_ ") if name.startswith(("Thread-", "skin-", "TreeAICoach-tts-")) else name


# ------------------------------------------------------------------------------ fakes
class CountingCapture:
    """Crops of the screenshot, every grab counted (calls, pixels, kind)."""

    def __init__(self, img: Any, mm_side: int = 300) -> None:
        self.img = img
        self.mm_side = mm_side
        self.calls: Counter = Counter()
        self.pixels: Counter = Counter()
        self.lock = threading.Lock()

    def grab(self, r: Any) -> Any:
        x0, y0 = max(0, int(r.x)), max(0, int(r.y))
        out = self.img[y0:int(r.y) + int(r.h), x0:int(r.x) + int(r.w)].copy()
        px = int(r.w) * int(r.h)
        kind = "full" if px >= 0.5 * self.img.shape[0] * self.img.shape[1] else (
            "minimap" if abs(int(r.w) - self.mm_side) <= 0.25 * self.mm_side and abs(int(r.w) - int(r.h)) <= 4
            else "hud_patch")
        with self.lock:
            self.calls[kind] += 1
            self.pixels[kind] += px
        return out

    def snapshot(self) -> tuple[Counter, Counter]:
        with self.lock:
            return Counter(self.calls), Counter(self.pixels)

    def close(self) -> None:
        pass


class FakeLiveClient:
    """The real allgamedata sample with the screenshot's roster; the game clock runs."""

    def __init__(self) -> None:
        d = json.loads((FIX / "allgamedata_sample.json").read_text(encoding="utf-8"))
        cnt = {"ORDER": 0, "CHAOS": 0}
        for p in d["allPlayers"]:
            team = p["team"]
            name = ROSTER[team][cnt[team] % 5]
            cnt[team] += 1
            p["rawChampionName"] = "game_character_displayname_" + name
            p["championName"] = name
            p["skinID"] = 0
            p["isDead"] = False
        me = [p for p in d["allPlayers"] if p["team"] == "ORDER"][0]
        d["activePlayer"]["riotId"] = me["riotId"]
        d["activePlayer"]["summonerName"] = me.get("summonerName", "")
        self.data = d
        self.t0 = time.monotonic()
        self.calls = 0

    def fetch(self) -> Any:
        from treeaicoach.live_client import parse_allgamedata

        self.calls += 1
        d = json.loads(json.dumps(self.data))
        d["gameData"]["gameTime"] = GAME_T0 + (time.monotonic() - self.t0)
        return parse_allgamedata(d, now=time.monotonic())


class QuietVoice:
    backend = "bench"
    danger_voice = "voice"

    def __init__(self) -> None:
        self.said = 0

    def say(self, *a: Any, **k: Any) -> None:
        self.said += 1

    def __getattr__(self, name: str) -> Any:        # start / stop / alert_beep / prefetch...
        return lambda *a, **k: None


class FakeWindow:
    """Same interface as overlay.LayeredWindow; accounts its uploads like the real one."""

    def __init__(self, name: str) -> None:
        self.name, self.visible, self.failed, self.hwnd = name, False, False, 1
        self.click_through = True
        self.alpha = 255
        self.x = self.y = self.w = self.h = 0

    def update(self, img: Any, x: int, y: int, alpha: int = 255) -> None:
        from treeaicoach import overlay as ov

        self.w, self.h = int(img.shape[1]), int(img.shape[0])
        self.x, self.y = int(x), int(y)
        _ = bytes(memoryview(img.reshape(-1))[: self.w * self.h * 4])   # the memmove into the DIB
        ov.note_push(self.name, self.w * self.h * 4)
        self.alpha = int(alpha)
        if not self.visible:
            self.show()

    def set_alpha(self, a: int) -> None:
        from treeaicoach import overlay as ov

        if self.visible and int(a) != self.alpha:
            self.alpha = int(a)
            ov.note_push(self.name, 0)

    def show(self) -> None:
        self.visible = True
        self.keep_topmost()

    def hide(self) -> None:
        self.visible = False

    def keep_topmost(self) -> None:
        from treeaicoach import overlay as ov

        ov.note_push(self.name, -1)

    def set_click_through(self, on: bool) -> None:
        self.click_through = bool(on)

    def exclude_from_capture(self) -> bool:
        return True

    def include_in_capture(self) -> None:
        pass

    def destroy(self) -> None:
        self.visible = False


# ------------------------------------------------------------------------------ run
def _pct(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    return s[min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))]


def run(seconds: float = 60.0, warmup: float = 15.0, scenario: str = "real", ui_hz: float = 1.0,
        real_windows: bool = False, perf_mode: str = "auto", cfg_changes: dict | None = None,
        quiet: bool = False) -> dict[str, Any]:
    """Run the pipeline for ``warmup + seconds`` (wall clock) and return the measurements."""
    import dataclasses
    import logging

    import cv2

    logging.basicConfig(level=logging.WARNING)
    from treeaicoach import overlay as ov
    from treeaicoach.capture import Rect
    from treeaicoach.config import Config
    from treeaicoach.engine import CoachEngine

    cfg = Config()
    changes = {"perf_mode": perf_mode, "ai_enabled": False}
    changes.update(cfg_changes or {})
    changes = {k: v for k, v in changes.items() if hasattr(cfg, k)}
    cfg = dataclasses.replace(cfg, **changes).validated()

    cap = None
    src = None
    src_cpu = [0.0]
    if scenario == "demo":
        from treeaicoach.demo import DemoSource

        src = DemoSource(size=300)
        orig_next = src.next

        def timed_next(t: float) -> Any:
            c0 = time.thread_time()
            try:
                return orig_next(t)
            finally:
                src_cpu[0] += time.thread_time() - c0

        src.next = timed_next          # type: ignore[method-assign]
        eng = CoachEngine(cfg, QuietVoice(), frame_source=src, enable_hotkeys=False, manage_overlay=False)
    else:
        img = cv2.imread(str(SCREEN))
        if img is None:
            raise SystemExit(f"missing fixture {SCREEN}")
        cap = CountingCapture(img)
        eng = CoachEngine(cfg, QuietVoice(), live_client=FakeLiveClient(),
                          window_finder=lambda: Rect(0, 0, img.shape[1], img.shape[0]),
                          screen_capture=cap, enable_hotkeys=False, manage_overlay=False)

    ticks: list[tuple[float, float]] = []
    orig_step = eng.step

    def timed_step(t: float) -> Any:
        c0 = time.perf_counter()
        try:
            return orig_step(t)
        finally:
            ticks.append((time.monotonic(), (time.perf_counter() - c0) * 1000.0))

    eng.step = timed_step              # type: ignore[method-assign]

    factory = None if (real_windows and sys.platform == "win32") else FakeWindow
    mgr = ov.OverlayManager(cfg, eng.get_overlay_state, window_factory=factory)
    mgr._foreground = lambda: (True, False)          # the game is in front

    gc_pauses: list[float] = []
    gc_t0: dict[str, float] = {}

    def gc_cb(phase: str, info: dict) -> None:
        if info.get("generation") != 2:
            return
        if phase == "start":
            gc_t0["t"] = time.perf_counter()
        elif "t" in gc_t0:
            gc_pauses.append((time.perf_counter() - gc_t0.pop("t")) * 1000.0)

    gc.callbacks.append(gc_cb)
    stop = threading.Event()
    ui_calls = [0]

    def ui_poll() -> None:          # the launcher's status refresh (ui.py: 1 Hz in game, 4 Hz on the dashboard)
        while not stop.wait(1.0 / max(0.1, ui_hz)):
            try:
                eng.get_status()
                ui_calls[0] += 1
            except Exception:
                pass

    tc = ThreadCpu()
    eng.start()
    mgr.start()
    ui_th = None
    if ui_hz > 0:
        ui_th = threading.Thread(target=ui_poll, name="ui-sim", daemon=True)
        ui_th.start()
    try:
        t_end_warm = time.monotonic() + warmup
        while time.monotonic() < t_end_warm:
            time.sleep(0.2)
        # ---- steady state
        s0 = tc.sample()
        p0 = time.process_time()
        w0 = time.monotonic()
        src0 = src_cpu[0]
        ov_frames0 = mgr.stats.get("frames", 0)
        ov.reset_push_stats()
        cap0 = cap.snapshot() if cap is not None else (Counter(), Counter())
        n_ticks0 = len(ticks)
        gc_n0 = len(gc_pauses)
        while time.monotonic() < w0 + seconds:
            time.sleep(0.25)
        s1 = tc.sample()
        p1 = time.process_time()
        w1 = time.monotonic()
        src1 = src_cpu[0]
        ov_stats = mgr.stats
        pushes = ov.push_stats()
        cap1 = cap.snapshot() if cap is not None else (Counter(), Counter())
        health = eng.health()
        status = eng.get_status()
    finally:
        stop.set()
        mgr.stop()
        eng.stop()
        try:
            gc.callbacks.remove(gc_cb)
        except ValueError:
            pass
    wall = w1 - w0
    per: dict[str, float] = defaultdict(float)
    for tid, (name, sec) in s1.items():
        before = s0.get(tid, (name, 0.0))[1]
        per[_group(name)] += max(0.0, sec - before)
    src_cost = src1 - src0
    if src_cost > 0:
        per["TreeAICoach-analysis"] = max(0.0, per.get("TreeAICoach-analysis", 0.0) - src_cost)
    threads = {k: round(100.0 * v / wall, 2) for k, v in sorted(per.items(), key=lambda kv: -kv[1]) if v > 0}
    proc = 100.0 * (p1 - p0 - src_cost) / wall
    win_ticks = [ms for (t, ms) in ticks[n_ticks0:]]
    calls = {k: round((cap1[0][k] - cap0[0][k]) / wall, 2) for k in set(cap1[0]) | set(cap0[0])}
    kpx = {k: round((cap1[1][k] - cap0[1][k]) / wall / 1000.0, 1) for k in set(cap1[1]) | set(cap0[1])}
    layers = {n: {"per_s": round(v[0] / wall, 2), "kb_per_s": round(v[1] / 1024.0 / wall, 1)}
              for n, v in pushes.get("layers", {}).items()}
    res = {
        "scenario": scenario, "seconds": round(wall, 1), "warmup": warmup, "platform": sys.platform,
        "cores": os.cpu_count(),
        "process_cpu_pct": round(proc, 2),
        "threads_cpu_pct": threads,
        "ticks": {"n": len(win_ticks), "per_s": round(len(win_ticks) / wall, 2),
                  "mean_ms": round(sum(win_ticks) / len(win_ticks), 2) if win_ticks else None,
                  "p50_ms": round(_pct(win_ticks, 0.5) or 0, 2), "p95_ms": round(_pct(win_ticks, 0.95) or 0, 2),
                  "max_ms": round(max(win_ticks), 2) if win_ticks else None},
        "overlay": {"loop_fps": round((ov_stats.get("frames", 0) - ov_frames0) / wall, 2),
                    "pushes_per_s": round(pushes.get("pushes", 0) / wall, 2),
                    "kb_per_s": round(pushes.get("bytes", 0) / 1024.0 / wall, 1),
                    "alpha_per_s": round(pushes.get("alpha", 0) / wall, 2),
                    "topmost_per_s": round(pushes.get("topmost", 0) / wall, 2),
                    "layers": layers,
                    "loop_ms": ov_stats.get("loop"),
                    "render_ms": {k[7:]: v.get("mean") for k, v in ov_stats.items()
                                  if k.startswith("render_") and isinstance(v, dict)}},
        "capture": {"calls_per_s": calls, "kpx_per_s": kpx, "total_calls_per_s": round(sum(calls.values()), 2)},
        "gc": {"gen2": len(gc_pauses) - gc_n0, "max_pause_ms": round(max(gc_pauses[gc_n0:], default=0.0), 2)},
        "ui_polls_per_s": round(ui_calls[0] / max(1e-6, wall + warmup), 2),
        "engine": {"state": getattr(getattr(status, "state", None), "value", None),
                   "budget": health.get("budget"), "detect_rate": health.get("detect_rate"),
                   "champions_seen": health.get("champions_seen"),
                   "champions_expected": health.get("champions_expected"),
                   "performance": health.get("performance")},
    }
    res["verdict"] = {"cpu_ok": proc <= TARGET_CPU_PCT,
                      "pushes_ok": res["overlay"]["pushes_per_s"] <= TARGET_PUSHES}
    if not quiet:
        print_report(res)
    return res


def print_report(r: dict[str, Any]) -> None:
    print(f"TreeAI performance budget - scenario {r['scenario']}, {r['seconds']} s steady state "
          f"(after {r['warmup']} s warm-up), {r['platform']}, {r['cores']} CPU")
    print(f"  process CPU          {r['process_cpu_pct']:6.2f} % of one core   (target <= {TARGET_CPU_PCT:.0f} %)")
    for k, v in r["threads_cpu_pct"].items():
        if v >= 0.05:
            print(f"    {k:<28} {v:6.2f} %")
    t = r["ticks"]
    print(f"  analysis ticks       {t['per_s']:.2f} /s, mean {t['mean_ms']} ms, p50 {t['p50_ms']} ms, "
          f"p95 {t['p95_ms']} ms, max {t['max_ms']} ms")
    o = r["overlay"]
    print(f"  overlay              loop {o['loop_fps']} fps, uploads {o['pushes_per_s']} /s "
          f"({o['kb_per_s']} KB/s; target <= {TARGET_PUSHES:.0f} /s), alpha {o['alpha_per_s']} /s, "
          f"topmost {o['topmost_per_s']} /s")
    for n, v in sorted(o["layers"].items()):
        print(f"    {n:<12} {v['per_s']:6.2f} /s  {v['kb_per_s']:8.1f} KB/s")
    if o.get("render_ms"):
        print("    render ms (mean): " + ", ".join(f"{k} {v}" for k, v in sorted(o["render_ms"].items())))
    c = r["capture"]
    print(f"  capture              {c['total_calls_per_s']} grabs/s " + ", ".join(
        f"{k} {v}/s ({c['kpx_per_s'].get(k, 0)} kpx/s)" for k, v in sorted(c["calls_per_s"].items())))
    g = r["gc"]
    print(f"  gc                   {g['gen2']} gen-2 collections, longest {g['max_pause_ms']} ms")
    e = r["engine"]
    print(f"  engine               {e['state']}, budget {(e['budget'] or {}).get('profile')} / "
          f"{(e['budget'] or {}).get('load')}, seen {e['champions_seen']}/{e['champions_expected']}")
    v = r["verdict"]
    print(f"  verdict              CPU {'OK' if v['cpu_ok'] else 'OVER'}, overlay {'OK' if v['pushes_ok'] else 'OVER'}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--warmup", type=float, default=15.0)
    ap.add_argument("--scenario", choices=("real", "demo"), default="real")
    ap.add_argument("--ui-hz", type=float, default=1.0, help="simulated launcher status polls / s (0 = none)")
    ap.add_argument("--real-windows", action="store_true", help="real layered windows (Windows / Wine)")
    ap.add_argument("--perf-mode", choices=("auto", "normal", "low_end"), default="auto")
    ap.add_argument("--json", default=None, help="write the measurements to this file")
    a = ap.parse_args(argv)
    res = run(a.seconds, a.warmup, a.scenario, a.ui_hz, a.real_windows, a.perf_mode)
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
