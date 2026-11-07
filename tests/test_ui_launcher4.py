"""v4 launcher design system: dark + light tokens, the Accueil dashboard (one card per game, last session,
empty states), the responsive two-card row, the live theme switch."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

import test_launcher as tl  # noqa: E402

from treeaicoach import ui  # noqa: E402
from treeaicoach import ui_page_home as ph  # noqa: E402
from treeaicoach import ui_widgets as W  # noqa: E402

home = tl.home


def test_tokens_are_one_scale() -> None:
    assert {t.name for t in W.THEMES} == {"dark", "light"}
    assert W.SPACING == tuple(sorted(W.SPACING)) and all(x % 4 == 0 for x in W.SPACING)
    for v in (W.PAGE_PAD_X, W.PAGE_PAD_TOP, W.CARD_PAD, W.SECTION_GAP, W.ROW_PAD_X, W.ROW_PAD_Y):
        assert v in W.SPACING, v
    assert list(W.TYPE_SCALE) == sorted(W.TYPE_SCALE, reverse=True) and min(W.TYPE_SCALE) >= 12
    light, dark = W.style_sheet(W.LIGHT, "Inter", "Inter"), W.style_sheet(W.DARK, "Inter", "Inter")
    assert W.LIGHT.window in light and W.DARK.window in dark and W.DARK.window not in light


def test_lol_badge_and_subtitle() -> None:
    assert ph.lol_badge("RUNNING") == ("En partie", "ok")
    assert ph.lol_badge("RUNNING", demo=True)[0] == "Démo"
    assert ph.lol_badge("WAITING_GAME")[1] == "accent" and ph.lol_badge("NO_ENGINE")[1] == "danger"
    assert ph.now_line("RUNNING", True).startswith("Partie de League")
    assert ph.now_line("STOPPED", True) == "World of Warcraft est lancé."
    assert ph.now_line("STOPPED", False) == "Ce qui compte maintenant."


def test_session_line_counts_today_only() -> None:
    now = dt.datetime.now()
    games = [{"champion": "Ahri", "result": "win", "date": now.isoformat()},
             {"champion": "Lux", "result": "lose", "date": now.isoformat()}]
    assert ph.session_line(games) == "bilan du jour : 2 parties, 1 victoire"
    old = [{"champion": "Ahri", "result": "win", "date": (now - dt.timedelta(days=3)).isoformat()}]
    assert ph.session_line(old) == "" and ph.session_line([]) == ""


def test_wow_card_states() -> None:
    inst = SimpleNamespace(flavor_dir="X:/WoW/_classic_era_")
    assert ph.wow_card({})["title"] == "Recherche de WoW…"
    c = ph.wow_card({"wow_exe": None, "read": True, "install": None})
    assert c["badge"] == "Hors jeu" and c["button"] == "Choisir le dossier" and c["primary"]
    c = ph.wow_card({"wow_exe": "Wow.exe", "read": True, "install": inst, "installed": False, "has_src": True})
    assert c["badge"] == "Lancé" and c["button"] == "Installer l'addon" and c["running"]
    rows = [("Personnage", "Chasseur niveau 27"), ("Zone", "Les Paluns")]
    c = ph.wow_card({"wow_exe": "Wow.exe", "mode": {"active": True}, "read": True, "install": inst,
                     "installed": True, "rows": rows, "sv_age": 300})
    assert c["title"] == "Coach WoW actif" and c["tone"] == "ok" and not c["primary"]
    assert c["char"] == "Chasseur niveau 27 · Les Paluns" and c["char_desc"] == "Les Paluns · sauvegardé il y a 5 min"
    assert ph.wow_card({"wow_exe": "Wow.exe"}, enabled=False)["badge"] == "Désactivé"
    assert not ph.wow_card({"wow_exe": "Wow.exe"}, enabled=False)["running"]


def test_read_wow_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from treeaicoach import wow

    monkeypatch.setattr(wow, "optional", lambda name: (_ for _ in ()).throw(RuntimeError("x")))
    assert ph.read_wow(SimpleNamespace(), {"wow_exe": "a"}, True) == {"wow_exe": "a"}


def test_dashboard_cards_session_and_theme(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ph, "read_wow", lambda cfg, prev=None, full=True: {
        "wow_exe": "Wow.exe", "mode": {"active": True}, "read": True, "install": object(), "installed": True,
        "rows": [("Personnage", "Mage niveau 12")], "sv_age": 30})
    app, _v, _e = tl._build(home, start_backend=False)
    try:
        app.win.resize(1100, 720)
        app.show()
        tl._pump(app, 0.3)
        view = app.page_views["home"]
        app.show_page("home")
        app._refresh_status()
        tl._pump(app, 0.3)
        assert view.wow_title.text() == "Coach WoW actif" and view.wow_badge.text() == "En jeu"
        assert view.now.is_wide()                                     # two cards side by side at 1100
        assert view.now._lay.itemAt(0).widget() is view.wow_card      # the running game first
        assert not view.wow_row.isHidden() and view.session_empty.isHidden()
        assert view.page.subtitle.text() == "World of Warcraft est lancé."
        view.on_games([])
        assert view.session_empty.isHidden()                          # the WoW character is a session
        # the analysis page shows one empty state, not empty tiles
        app.show_page("analysis")
        an = app.page_views["analysis"]
        an.on_games([])
        assert not an.empty.isHidden() and an.summary.isHidden() and an.edge.isHidden()
        # live theme switch: one style sheet swap, no rebuild
        before = len(app.pages)
        app.set_option("ui_theme", "light")
        assert W.theme().name == "light" and app.dark is False and len(app.pages) == before
        app.set_option("ui_theme", "dark")
        assert W.theme().name == "dark"
    finally:
        app.close()
        W.apply_theme(app.qapp, True)


def test_columns_stack_when_narrow(home: Path) -> None:
    app, _v, _e = tl._build(home, start_backend=False)
    try:
        cols = W.Columns(min_each=300)
        a, b = cols.add(W.Card()), cols.add(W.Card())
        cols.resize(640, 200)
        cols.show()
        app.qapp.processEvents()
        assert cols.is_wide()
        cols.resize(500, 200)
        app.qapp.processEvents()
        assert not cols.is_wide()
        cols.set_order([b])
        assert cols._lay.itemAt(0).widget() is b
        cols.hide()
    finally:
        app.close()


def test_sidebar_live_dot_keeps_the_label(home: Path) -> None:
    app, _v, _e = tl._build(home, start_backend=False)
    try:
        sb = app.sidebar
        sb.set_live("wow", True)
        assert sb.buttons["wow"].property("live") is True and sb.buttons["wow"].text() == "WoW Forever"
        sb.set_live("wow", False)
        assert not sb.buttons["wow"].property("live")
        assert sb.brand_sub.text().startswith("Version ")
        assert ui.PAGES and isinstance(sb.buttons["home"], W.NavButton)
    finally:
        app.close()
