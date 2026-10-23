"""League flavour + launch sequence (ui_flair): real checks, art cache, skippable splash, 8-pt grid."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")
sys.path.insert(0, str(Path(__file__).parent))

from treeaicoach import ui_flair as F  # noqa: E402
from treeaicoach import ui_widgets as W  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from treeaicoach import ui

    app = ui.ensure_qapp()
    W.apply_theme(app, True)
    return app


def test_champion_key_is_path_safe() -> None:
    assert F.champion_key("MonkeyKing") == "MonkeyKing"
    for bad in ("", None, "../x", "a b", "Garen/..", "x" * 40):
        assert F.champion_key(bad) == ""


def test_boot_checks_are_real_and_fast() -> None:
    import time

    t0 = time.perf_counter()
    rows = F.boot_checks(None)
    assert time.perf_counter() - t0 < 1.0
    assert [r[0] for r in rows] == ["détecteur", "capture", "client LoL", "voix", "overlay"]
    assert all(ok in (True, False, None) and isinstance(d, str) and d for _l, ok, d in rows)
    assert dict((l, ok) for l, ok, _d in rows)["détecteur"] is True      # bundled model + onnxruntime


def test_art_download_is_cached_and_validated(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TREEAICOACH_HOME", str(tmp_path))
    from treeaicoach import paths

    monkeypatch.setattr(paths, "cache_dir", lambda: tmp_path)
    F._art_failed.clear()
    calls = []
    monkeypatch.setattr(F, "_fetch_hook", lambda u: calls.append(u) or b"\xff\xd8\xff" + b"0" * 100)
    assert F.fetch_art("Garen") and F.has_full_art("Garen")
    assert F.fetch_art("Garen") and len(calls) == 1                      # second call: cache
    monkeypatch.setattr(F, "_fetch_hook", lambda u: b"<html>nope")
    assert not F.fetch_art("Ahri") and not F.fetch_art("Ahri")           # refused, one try per session
    assert not F.fetch_art("../etc")
    assert F.art_path("Ahri") is None or F.art_path("Ahri").suffix == ".png"   # bundled portrait fallback


def test_hero_card_art_and_fallback(qapp) -> None:
    hero = F.HeroCard()
    hero.resize(840, 184)
    hero.set_champion("")
    assert not hero.has_art
    hero.set_champion("Garen")                    # bundled 64 px portrait at least
    assert hero.has_art
    img = hero.grab()
    assert not img.isNull()


def test_splash_runs_once_skips_and_cleans_up(qapp) -> None:
    from PySide6 import QtCore, QtWidgets

    host = QtWidgets.QWidget()
    host.resize(800, 500)
    host.show()
    sp = F.BootSplash(host, "v0", F.boot_checks(None))
    fired = []
    sp.finished.connect(lambda: fired.append(1))
    sp.start()
    assert sp.isVisible()
    sp.skip()
    sp.skip()
    end = QtCore.QDeadlineTimer(1500)
    while not fired and not end.hasExpired():
        qapp.processEvents()
    assert fired == [1]
    assert F.BootSplash.FILL_MS + F.BootSplash.FADE_MS <= 1250        # spec: 1.2 s at most
    host.close()


def test_splash_abort_when_window_closes(qapp) -> None:
    from PySide6 import QtWidgets

    host = QtWidgets.QWidget()
    host.show()
    sp = F.BootSplash(host, "v0", [("a", True, "ok")])
    done = []
    sp.finished.connect(lambda: done.append(1))
    sp.start()
    sp.abort()
    sp.abort()
    from PySide6.QtCore import QAbstractAnimation

    assert done == [1] and sp._anim.state() == QAbstractAnimation.Stopped
    host.close()


def test_eight_point_grid() -> None:
    for v in (W.CONTENT_MAX, W.PAGE_PAD_X, W.PAGE_PAD_TOP, W.SECTION_GAP, W.ROW_MIN_H, W.ROW_PAD_X):
        assert v % 8 == 0, v
    assert W.GROUP_RADIUS == F.HeroCard.RADIUS and W.ROW_PAD_Y % 4 == 0
    assert W.SECTION_PX > W.BODY_PX and W.TITLE_PX >= 32


def test_sections_have_a_scanline(qapp) -> None:
    s = W.Section("Titre")
    assert s.findChild(object, "Scanline") is not None


def test_app_shows_splash_once_and_closes_cleanly(qapp, tmp_path, monkeypatch) -> None:
    import test_launcher as tl

    app, _voice, _eng = tl._build(tmp_path)
    app.win.show()
    app._boot_splash()
    sp = app._splash
    assert sp is not None
    app._boot_splash()                                    # once: a second call changes nothing
    assert app._splash is sp
    app.close()                                           # closing mid-splash: aborted, nothing left
    assert app._splash is None
