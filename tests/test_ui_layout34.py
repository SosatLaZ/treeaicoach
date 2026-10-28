"""v3.4 launcher layout: 6 pages, closed « Avancé » folds built lazily, old page names land on Réglages,
and nothing heavy (numpy, PIL, cv2, onnxruntime, discord, edge_tts, torch) imported before the window."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import test_launcher as tl

from treeaicoach import ui
from treeaicoach import ui_widgets as W

ROOT = Path(__file__).resolve().parents[1]
home = tl.home


def test_six_pages_with_one_purpose_each() -> None:
    assert len(ui.PAGES) <= 6
    assert ui.PAGE_ALIASES["advanced"] == "settings" and ui.PAGE_ALIASES["about"] == "settings"


def test_ui_import_is_light() -> None:
    code = ("import sys; import treeaicoach.ui, treeaicoach.ui_page_home; "
            "bad = [m for m in ('numpy', 'PIL', 'cv2', 'onnxruntime', 'discord', 'edge_tts', 'torch', "
            "'treeaicoach.capture', 'treeaicoach.engine') if m in sys.modules]; print(bad); sys.exit(bool(bad))")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_folds_are_lazy_and_old_pages_open_their_block(home: Path) -> None:
    app, _v, _e = tl._build(home, start_backend=False)
    try:
        app.show_page("settings")
        view = app.page_views["settings"]
        assert isinstance(view.more, W.Fold) and not view.more.is_open()
        assert view.more._builders and "advanced" not in app.page_views      # nothing built yet
        app.show_page("advanced")                                              # old page name
        assert app._current_page == "settings" and view.more.is_open()
        assert app.page_views["advanced"] is view.advanced and not view.more._builders
        app.show_page("about")
        assert app._current_page == "settings" and app.page_views["about"] is view.about
        app.show_page("help")
        assert view.help.is_open()
        app.build_all_pages()
        assert not app._pending_folds()
        # every page keeps its rarely used rows in ONE closed fold
        for key in ("overlay", "alerts", "discord", "settings"):
            assert app.pages[key].findChildren(W.Fold), key
    finally:
        app.close()
