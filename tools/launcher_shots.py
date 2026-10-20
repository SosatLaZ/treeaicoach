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


#: the Discord page: a connected bot on two servers, the player in voice with it (fictitious names)
DISCORD_SNAPSHOT = {
    "state": "connected", "status": "Connecté à #Général.", "ok": True, "error": "", "bot_name": "TreeAI Coach",
    "latency_ms": 46, "in_game": False, "user_id": "123456789012345678",
    "guilds": [{"id": "10", "name": "Les potes du soir", "members": 14, "voice_channels": [
        {"id": "101", "name": "Général", "members": 3, "people": ["Leo", "Nina", "Toi"], "can_speak": True,
         "missing": [], "me_here": True, "bot_here": True},
        {"id": "102", "name": "Flex 5", "members": 2, "people": ["Max", "Sam"], "can_speak": True, "missing": [],
         "me_here": False, "bot_here": False},
        {"id": "103", "name": "AFK", "members": 0, "people": [], "can_speak": False, "missing": ["parler"],
         "me_here": False, "bot_here": False}]},
        {"id": "20", "name": "Clan Garen", "members": 6, "voice_channels": [
            {"id": "201", "name": "Ranked", "members": 0, "people": [], "can_speak": True, "missing": [],
             "me_here": False, "bot_here": False}]}],
    "user": {"id": "123456789012345678", "found": True, "name": "Toi", "channel_id": "101", "channel": "Général",
             "guild": "Les potes du soir", "guild_id": "10"},
    "channel": {"id": "101", "name": "Général", "guild": "Les potes du soir", "guild_id": "10"},
    "events": [{"t": "21:04:12", "text": "A dit « Garen contre Darius : défavorable, joue safe jusqu'à 8 minutes. »",
                "channel": "Général", "ok": True},
               {"t": "21:03:40", "text": "A dit « Bot gank »", "channel": "Général", "ok": True},
               {"t": "21:03:31", "text": "A rejoint #Général", "channel": "Général", "ok": True},
               {"t": "21:03:30", "text": "Connecté en tant que TreeAI Coach", "channel": "", "ok": True}],
}


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
    ap.add_argument("--pages", default="", help="only these pages (comma-separated keys)")
    args = ap.parse_args(argv)
    os.environ.setdefault("TREEAICOACH_HOME", tempfile.mkdtemp(prefix="treeai-shots-"))
    from treeaicoach import ui
    from treeaicoach.config import Config

    ui._report_function = lambda name: (lambda n=50: list(SAMPLE_GAMES)) if name == "list_games" else None
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cfg = Config(ui_onboarding_done=True, ui_seen_changelog="9", check_updates_on_start=False,
                 lcu_enabled=False, download_skin_icons=False, discord_user_id="123456789012345678",
                 discord_enabled=True, discord_token=".".join(["MTIzNDU2Nzg5MDEyMzQ1Njc4OQ", "Gabc", "x" * 27]))
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
            dc = app.page_views.get("discord")
            if dc is not None:
                dc.snapshot_fn = lambda: dict(DISCORD_SNAPSHOT)
                dc.update(dc._read())
            switches = []
            for key, _l, _i in ui.PAGES:
                if args.pages and key not in args.pages.split(","):
                    continue
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
            from PySide6 import QtCore

            app.qapp.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)   # the next window starts clean
    print(f"screenshots in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
