"""CPU of the TreeAI Coach app when nothing happens: the real window (offscreen), the real engine and WoW mode.

PERF4 (4.0.1). The app is started as the player starts it (``run_app``: window, backend, engine autostart, WoW mode
thread), with no game running (no League, no WoW): every second spent in this process is background cost. After a
warm-up (backend + detector loading), the process CPU time (all threads: ``resource.getrusage``) is sampled over the
measured window; the result is the average % of ONE core.

    QT_QPA_PLATFORM=offscreen python tools/app_idle_cpu.py [--seconds 120] [--warmup 30] [--json out.json]
        [--wow-running]   (a fake « WowClassic.exe » in the process list: the WoW mode is ON, WoW idle)

Prints ``APP IDLE CPU: x.xx % of one core over N s (warm-up W s)`` and the threads alive at the end.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("TREEAICOACH_HOME", tempfile.mkdtemp(prefix="treeai_idle_"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("TREEAICOACH_NO_SPLASH", "1")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _cpu() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=120.0)
    ap.add_argument("--warmup", type=float, default=30.0)
    ap.add_argument("--json", default=None)
    ap.add_argument("--wow-running", action="store_true")
    args = ap.parse_args(argv)

    import treeaicoach  # noqa: F401  (single-threaded BLAS before numpy, like the app)
    from treeaicoach import ui
    from treeaicoach.config import Config

    if args.wow_running:
        from treeaicoach.wow import process

        real = process.process_names

        def fake(lister=None, *, cached=True):  # noqa: ANN001
            names = set(real(lister, cached=cached))
            names.add("wowclassic.exe")
            return frozenset(names)
        process.process_names = fake

    cfg = Config()
    marks: dict[str, float] = {}
    total = args.warmup + args.seconds

    threads: list[str] = []

    def sample(name: str) -> None:
        marks[name] = _cpu()
        marks[name + "_t"] = time.monotonic()
        if name == "b":
            threads[:] = sorted(t.name for t in threading.enumerate())
            app = app_box.get("app")
            marks["detector"] = 1.0 if getattr(app, "_detector", None) is not None else 0.0

    app_box: dict[str, object] = {}
    orig_init = ui.CoachApp.__init__

    def init(self, *a, **k):  # noqa: ANN001
        orig_init(self, *a, **k)
        app_box["app"] = self
        self.QtCore.QTimer.singleShot(int(args.warmup * 1000), self.win, lambda: sample("a"))
        self.QtCore.QTimer.singleShot(int(total * 1000), self.win, lambda: sample("b"))
    ui.CoachApp.__init__ = init
    try:
        rc = ui.run_app(cfg, smoke_seconds=total + 0.5, _save_path=Path(os.environ["TREEAICOACH_HOME"]) / "cfg.json")
    finally:
        ui.CoachApp.__init__ = orig_init
    if "a" not in marks or "b" not in marks:
        print("APP IDLE CPU: measurement incomplete (rc=%s)" % rc)
        return 2
    span = marks["b_t"] - marks["a_t"]
    pct = 100.0 * (marks["b"] - marks["a"]) / max(1e-6, span)
    print(f"APP IDLE CPU: {pct:.2f} % of one core over {span:.0f} s (warm-up {args.warmup:.0f} s)"
          f"{' — WoW mode on' if args.wow_running else ''}")
    print("threads (end of the measure): " + ", ".join(threads))
    print("LoL detector (ONNX) loaded: " + ("yes" if marks.get("detector") else "no"))
    if args.json:
        Path(args.json).write_text(json.dumps({"cpu_pct": pct, "seconds": span, "warmup": args.warmup,
                                               "wow_running": bool(args.wow_running), "threads": threads,
                                               "detector_loaded": bool(marks.get("detector"))}),
                                   encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
