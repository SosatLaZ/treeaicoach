"""Launcher timings (docs/LAUNCHER.md): window creation, first paint, each page build, page switches.

    python tools/launcher_bench.py [--profile]

No engine, no voice, no hotkeys, no network. ``--profile`` prints the top cProfile entries of the
window creation (to find what is slow, e.g. under Wine).
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", action="store_true")
    args = ap.parse_args(argv)
    os.environ.setdefault("TREEAICOACH_HOME", tempfile.mkdtemp(prefix="treeai-bench-"))
    t0 = time.perf_counter()
    from treeaicoach import ui
    from treeaicoach.config import Config

    t_import = time.perf_counter() - t0
    prof = None
    if args.profile:
        import cProfile

        prof = cProfile.Profile()
        prof.enable()
    t1 = time.perf_counter()
    app = ui.CoachApp(Config(ui_onboarding_done=True, check_updates_on_start=False), start_backend=False,
                      hotkeys=False, voice=None, save_path=Path(tempfile.mkdtemp()) / "c.json")
    app._no_dialogs = True
    t_init = time.perf_counter() - t1
    if prof is not None:
        prof.disable()
        import pstats

        pstats.Stats(prof).sort_stats("cumulative").print_stats(25)
    painted = []
    app.win.show()
    t2 = time.perf_counter()
    while not painted and time.perf_counter() - t2 < 10:
        app.qapp.processEvents()
        if app.win.isVisible() and app.stack.currentWidget().isVisible():
            app.stack.currentWidget().repaint()
            painted.append(time.perf_counter())
    t_paint = painted[0] - t2 if painted else float("nan")
    builds = {}
    for key, _l, _i in ui.PAGES:
        if key in app.pages:
            continue
        t = time.perf_counter()
        app.ensure_page(key)
        builds[key] = 1000 * (time.perf_counter() - t)
    sw = []
    for _ in range(3):
        for key, _l, _i in ui.PAGES:
            t = time.perf_counter()
            app.show_page(key)
            app.stack.currentWidget().repaint()
            app.qapp.processEvents()
            sw.append(1000 * (time.perf_counter() - t))
    rss = ""
    try:
        import psutil

        rss = f", RSS {psutil.Process().memory_info().rss / 1e6:.0f} MB"
    except Exception:
        pass
    print(f"import {1000 * t_import:.0f} ms, window+home {1000 * t_init:.0f} ms, first paint {1000 * t_paint:.0f} ms, "
          f"page builds {', '.join(f'{k} {v:.0f}' for k, v in builds.items())} ms, switches (incl. paint) max "
          f"{max(sw):.1f} mean {sum(sw) / len(sw):.1f} ms{rss}")
    app.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
