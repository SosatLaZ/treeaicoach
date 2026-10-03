"""Screenshots of every launcher page, light and dark, at 1280 x 720 and 1920 x 1080 (docs/LAUNCHER.md).

    python tools/launcher_shots.py [--out DIR] [--sizes 1280x720,1920x1080] [--themes light,dark]

Runs the real window (no engine, no voice, no hotkeys, no network): a stub engine reports a running
game so the home page shows its in-game state, and a few fictitious games fill the Analyse page.
Also prints the page build and page switch times. Headless: ``QT_QPA_PLATFORM=offscreen`` or xvfb-run.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SAMPLE_GAMES = [
    {"champion": "Garen", "role": "top", "result": "win", "start": "2026-10-03T18:06:00", "duration": 1712,
     "kills": 7, "deaths": 3, "assists": 5, "ganks": 4, "ganks_survived": 3},
    {"champion": "Swain", "role": "support", "result": "lose", "start": "2026-10-03T16:40:00", "duration": 1955,
     "kills": 2, "deaths": 6, "assists": 11, "ganks": 2, "ganks_survived": 1},
    {"champion": "Garen", "role": "top", "result": "win", "start": "2026-10-02T21:12:00", "duration": 1504,
     "kills": 9, "deaths": 2, "assists": 4, "ganks": 3, "ganks_survived": 3},
]


class StubEngine:
    def __init__(self) -> None:
        self.muted = False

    def is_running(self) -> bool:
        return True

    def get_status(self) -> SimpleNamespace:
        return SimpleNamespace(state=SimpleNamespace(value="WAITING_GAME"), message="En attente d'une partie de "
                               "League of Legends…", game_time=None, fps=0.0, enemies_visible=0, last_alert="",
                               detector="onnx", voice="neural", minimap_rect=None, locate_method=None)

    def get_overlay_state(self) -> None:
        return None

    def apply_config(self, _cfg: object) -> None:
        pass

    def stop(self) -> None:
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "release" / "shots"))
    ap.add_argument("--sizes", default="1280x720,1920x1080")
    ap.add_argument("--themes", default="light,dark")
    args = ap.parse_args(argv)
    os.environ.setdefault("TREEAICOACH_HOME", tempfile.mkdtemp(prefix="treeai-shots-"))
    from treeaicoach import ui
    from treeaicoach.config import Config

    ui._report_function = lambda name: (lambda n=50: list(SAMPLE_GAMES)) if name == "list_games" else None
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cfg = Config(ui_onboarding_done=True, ui_seen_changelog="9", check_updates_on_start=False,
                 lcu_enabled=False, download_skin_icons=False)
    for theme in args.themes.split(","):
        for size in args.sizes.split(","):
            w, h = (int(x) for x in size.split("x"))
            t0 = time.perf_counter()
            app = ui.CoachApp(cfg, hotkeys=False, start_backend=False, dark=(theme == "dark"),
                              save_path=Path(tempfile.mkdtemp()) / "c.json", voice=SimpleNamespace(
                                  backend="neural", say=lambda *a, **k: None, stop=lambda: None))
            app._no_dialogs = True
            app.engine = StubEngine()
            app._lcu_text = "Client LoL : connecté"
            app.win.resize(w, h)
            app.show()
            app.qapp.processEvents()
            build_ms = 1000 * (time.perf_counter() - t0)
            app._games = SAMPLE_GAMES
            app.build_all_pages()
            app.page_views["analysis"].on_games(SAMPLE_GAMES)
            app.page_views["home"].on_games(SAMPLE_GAMES)
            switches = []
            for key, _l, _i in ui.PAGES:
                t1 = time.perf_counter()
                app.show_page(key)
                app.stack.currentWidget().repaint()
                switches.append(1000 * (time.perf_counter() - t1))
                end = time.monotonic() + (8.0 if key == "overlay" else 0.2)
                prev = app.page_views.get("overlay")
                while time.monotonic() < end:
                    if key == "overlay" and prev is not None and prev.preview.pixmap() is not None \
                            and not prev.preview.pixmap().isNull() and time.monotonic() > end - 7.5:
                        break
                    app.qapp.processEvents()
                    app._dispatcher.drain()
                    time.sleep(0.01)
                app.toast.hide()
                img = app.win.grab()
                p = out / f"{key}_{theme}_{w}x{h}.png"
                img.save(str(p))
            print(f"{theme} {w}x{h}: window {build_ms:.0f} ms, page switch (incl. paint) max "
                  f"{max(switches):.1f} ms mean {sum(switches) / len(switches):.1f} ms")
            app.close()
    print(f"screenshots in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
