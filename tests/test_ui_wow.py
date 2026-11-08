"""WoW Forever page and the per-game sidebar (3.8.3 tidy-up)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from treeaicoach import ui_common as ui
from treeaicoach import ui_page_wow as pw

TESTS = Path(__file__).resolve().parent
INST = SimpleNamespace(flavor_dir="X:/Jeux/World of Warcraft/_classic_era_", source="registre")


def test_sidebar_groups_one_per_game() -> None:
    caps = [c for c, _k in ui.NAV_GROUPS]
    assert caps == ["League of Legends", "World of Warcraft", "Configuration"]
    assert dict(ui.NAV_GROUPS)["World of Warcraft"] == ("wow",)


def test_overview_verdicts() -> None:
    assert pw.overview({})["headline"] == "WoW Forever" and not pw.overview({})["can_install"]
    ov = pw.overview({"install": None, "log": ("", "WoW introuvable.")})
    assert ov["headline"] == "WoW introuvable" and ov["tone"] == "danger" and not ov["can_install"]
    assert ov["steps"][0][:2] == ("folder", False) and not ov["found"]
    base = {"install": INST, "has_src": True, "log": ("ok", "Journal de combat actif (3 Ko).")}
    ov = pw.overview({**base, "installed": False})
    assert ov["headline"] == "Addon à installer" and ov["button"] == "Installer l'addon" and ov["can_install"]
    ov = pw.overview({**base, "installed": True, "needs_update": True, "inst_version": "1.0", "src_version": "1.1"})
    assert ov["button"] == "Mettre à jour l'addon" and "1.0 → 1.1" in ov["steps"][1][2]
    ov = pw.overview({**base, "installed": True, "inst_version": "1.1"})
    assert ov["headline"] == "Tout est prêt" and ov["button"] == "Réinstaller l'addon"
    assert [k for k, _d, _t in ov["steps"]] == ["folder", "addon", "log"]
    ov = pw.overview({**base, "installed": True, "wow_exe": "wow.exe", "log": ("warning", "Tape /combatlog.")})
    assert ov["headline"].startswith("Presque prêt") and ov["detail"].startswith("WoW lancé")
    assert pw.overview({**base, "installed": True}, enabled=False)["headline"] == "Mode WoW désactivé"


def test_wow_mode_puts_wow_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("PySide6")
    if str(TESTS) not in sys.path:
        sys.path.insert(0, str(TESTS))
    import test_launcher as tl

    from treeaicoach import ui as app_ui
    from treeaicoach import wow
    from treeaicoach.wow import mode  # noqa: F401 - imported: the sidebar only looks at a loaded module

    monkeypatch.setenv("TREEAICOACH_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(app_ui, "_report_function", lambda name: (lambda n=50: []) if name == "list_games" else None)
    app, _v, _e = tl._build(tmp_path, start_backend=False)
    try:
        sb = app.sidebar
        assert sb.group_order()[0] == "home"
        monkeypatch.setattr(wow, "wow_mode_active", lambda: True)
        app._refresh_status()
        assert sb.group_order()[0] == "wow" and sb.group_order()[-1] == "settings"
        assert sb.buttons["wow"].property("live") is True
        assert sb.status.text() == "WoW Forever : coach actif"
        monkeypatch.setattr(wow, "wow_mode_active", lambda: False)
        app._refresh_status()
        assert sb.group_order() == [k for _c, keys in ui.NAV_GROUPS for k in keys]
        assert not sb.buttons["wow"].property("live") and sb.buttons["wow"].text() == "WoW Forever"
        app.show_page("wow")
        view = app.page_views["wow"]
        assert view.more.is_open() is False                 # dossier / mise à jour auto / règles: « Avancé »
        view.state = {"install": INST, "has_src": True, "installed": True, "needs_update": True,
                      "log": ("ok", "x"), "rows": [("Personnage", "Chasseur niveau 26"), ("Zone", "Zone test"),
                                                   ("Or", "1 po 0 pa 0 pc")], "mode": {}}
        view._render()
        assert view.install_btn.isEnabled() and view.install_btn.text() == "Mettre à jour l'addon"
        assert view.state_row.title.text() == "Mise à jour de l'addon"
        assert view.pick_btn.isHidden()
    finally:
        app.close()


def test_wow_errors_buttons(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """« Erreurs WoW : ouvrir le fichier » + « Copier » (TreeAI-erreurs.txt, written from the addon save)."""
    pytest.importorskip("PySide6")
    if str(TESTS) not in sys.path:
        sys.path.insert(0, str(TESTS))
    import test_launcher as tl

    from treeaicoach import paths
    from treeaicoach import ui as app_ui
    from treeaicoach.wow import errlog

    monkeypatch.setenv("TREEAICOACH_HOME", str(tmp_path / "home"))
    paths._reset_cache()
    errlog._reset()
    monkeypatch.setattr(app_ui, "_report_function", lambda name: (lambda n=50: []) if name == "list_games" else None)
    app, _v, _e = tl._build(tmp_path, start_backend=False)
    try:
        app.show_page("wow")
        view = app.page_views["wow"]
        assert view.err_open_btn.text() == "Ouvrir" and view.err_copy_btn.text() == "Copier"
        toasts: list[str] = []
        copied: list[str] = []
        opened: list = []
        monkeypatch.setattr(app, "show_toast", lambda t, level="info": toasts.append(t))
        monkeypatch.setattr(app, "copy_text", lambda t: copied.append(t))
        monkeypatch.setattr(errlog, "open_file", lambda p: opened.append(p) or True)
        view._snap = {}
        view._open_errors()
        assert toasts[-1] == pw.ERR_EMPTY and not opened
        view._snap = {"db": {"diag": {"errors": [{"msg": "boom-fake", "module": "Quests", "t": 1800000000}]}}}
        view._copy_errors()
        assert copied and "boom-fake" in copied[-1]
        view._open_errors()
        assert opened and Path(opened[-1]).name == "TreeAI-erreurs.txt"
        info = pw._errfile_info()
        assert info and info["path"].endswith("TreeAI-erreurs.txt")
        view.state = {"install": None, "live": {}, "rows": [], "mode": {}, "errfile": info}
        view._render()
        view.tabs._buttons["erreurs"].click()
        sec = view.tab_views["erreurs"].views["erreurs.file"]
        assert view.err_open_btn.parent() is not None and "TreeAI-erreurs.txt" in sec.rows[0].desc.text()
    finally:
        app.close()
        paths._reset_cache()
