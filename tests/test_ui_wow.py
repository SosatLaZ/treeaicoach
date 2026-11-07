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
