"""UI v4 (practical sweep): one Réglages page with intent tabs, no dead / unreachable setting, the ONE
status line with its fix, pre-game vs in-game dashboard, update / post-game / champion-select flows,
layout at the minimum window size. Pure parts run everywhere; Tk parts need a display."""
from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_ui as tu

from treeaicoach import ui, ui_kit
from treeaicoach.config import REMOVED_KEYS, Config, load_config, save_config
from treeaicoach.ui_page_alerts import danger_mode, danger_mode_fields

home = tu.home


# ======================================================================================
# Pure
# ======================================================================================
def test_navigation_and_tabs() -> None:
    assert [k for k, _l, _i in ui.PAGES] == ["dashboard", "analysis", "settings", "help"]
    assert ui.SETTINGS_TABS[0] == "Général" and "Avancé" == ui.SETTINGS_TABS[-1]
    for old, (page, tab) in ui.PAGE_ALIASES.items():
        assert page == "settings" and tab in ui.SETTINGS_TABS, old
    assert ui.ui_kit.SHORTCUTS[0][0] == "Ctrl + 1 … 4"


def test_status_line() -> None:
    sl = ui_kit.status_line
    assert sl("WAITING_GAME", "En attente d'une partie de League of Legends…")[:2] == (
        "En attente d'une partie", "Lance une partie : l'analyse démarre toute seule.")
    t, m, fix, act = sl("RUNNING", "Analyse de la minimap en cours.", champion="Garen", role="TOP")
    assert (t, m, fix) == ("En jeu : Garen top", "Alertes et overlay actifs.", "")
    assert sl("RUNNING", "", champion="Jinx", role="BOTTOM")[0] == "En jeu : Jinx ADC"
    assert sl("RUNNING", "")[0] == "En jeu"
    assert sl("RUNNING", "Minimap non trouvée automatiquement : position par défaut")[2:] == ("Calibrer", "calibrate")
    assert sl("LOCATING", "Recherche de la minimap…")[2:] == ("Calibrer", "calibrate")
    assert sl("LOCATING", "Jeu réduit : analyse en pause.")[2] == ""          # nothing to calibrate
    assert sl("CAPTURE_BLACK", "Capture noire : passe le jeu en Sans bordure")[3] == "help_borderless"
    assert sl("ERROR", "boom")[3] == sl("NO_ENGINE", "")[3] == "diagnostic"
    assert "Démarrer" in sl("STOPPED", "Analyse arrêtée.")[1]
    for state in ("RUNNING", "WAITING_GAME", "LOCATING", "CAPTURE_BLACK", "ERROR", "NO_ENGINE", "STOPPED",
                  "STARTING", "UNSUPPORTED_MODE", "???"):
        title, msg, fix, act = sl(state, "x")
        assert title and msg and bool(fix) == bool(act) and chr(0x2014) not in title + msg


def test_danger_mode_merges_two_settings() -> None:
    for mode, _label in ui.DANGER_MODES:
        cfg = dataclasses.replace(Config(), **danger_mode_fields(mode)).validated()
        assert danger_mode(cfg) == mode
    assert danger_mode(Config()) == "bip_voix"                 # beep-first by default (LESSONS 17)
    assert danger_mode_fields("voix") == {"beep_on_danger": False, "danger_voice": "bip_voix"}


def test_game_keys_follow_the_bindings() -> None:
    keys = dict(ui_kit.game_keys(Config()))
    assert keys["F9"] == "Où est le jungler ?" and keys["Ctrl + F8"].startswith("Diagnostic")
    assert "F6" in keys
    assert "F7" not in dict(ui_kit.game_keys(Config(hotkey_ward="")))


def test_removed_settings_load_silently_and_are_not_saved(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    p = tmp_path / "config.json"
    old = {k: True for k in REMOVED_KEYS} | {"hotkey_details": "F6", "overlay_opacity": 0.8, "ui_last_page": "alerts"}
    p.write_text(json.dumps(old), encoding="utf-8")
    with caplog.at_level(logging.INFO, logger="treeaicoach.config"):
        cfg = load_config(p)
    assert "unknown config keys" not in caplog.text
    assert cfg.overlay_show_roles and cfg.overlay_show_ghosts            # layer_roles / layer_ghosts merged
    save_config(cfg, p)
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert not REMOVED_KEYS & set(saved)
    for name in REMOVED_KEYS:
        assert not hasattr(Config(), name), name


def test_ui_text_never_names_a_removed_setting() -> None:
    root = Path(ui.__file__).parent
    src = "\n".join((root / f).read_text(encoding="utf-8") for f in (
        "ui.py", "ui_common.py", "ui_dialogs.py", "ui_kit.py", "ui_page_alerts.py", "ui_page_analysis.py",
        "ui_page_dashboard.py", "ui_page_overlay.py", "ui_page_settings.py", "ui_preview.py"))
    for name in REMOVED_KEYS:
        assert f'"{name}"' not in src, name
    assert "apply_preset" not in src and "PRESET_LABELS" not in src


# ======================================================================================
# Tk
# ======================================================================================
@tu.needs_display
def test_every_setting_has_a_control(home: Path, tmp_path: Path) -> None:
    """No setting is dead or unreachable: each Config field has a control in Réglages, or is listed in
    ui_common.HIDDEN_SETTINGS with the reason (sidebar, learned, written by another action...)."""
    app, _voice, _ = tu._build(tmp_path)
    try:
        app.build_all_pages()
        names = {f.name for f in dataclasses.fields(Config)}
        missing = sorted(n for n in names if n not in app._widgets_by_field and n not in ui.HIDDEN_SETTINGS)
        assert not missing, ("settings without a control: add one in Réglages or list them in "
                             f"ui_common.HIDDEN_SETTINGS with the reason: {missing}")
        assert not [n for n in ui.HIDDEN_SETTINGS if n in app._widgets_by_field]
        assert not [n for n in ui.HIDDEN_SETTINGS if n not in names]       # no stale entry
        # every tab shows something, also when opened from elsewhere
        page = app.pages["settings"]
        for tab in ui.SETTINGS_TABS:
            app.show_page("settings", tab)
            tu._pump(app, 0.05)
            assert page.current_tab == tab and app._settings_tab == tab
            shown = [s for s in body_sections(page) if s.winfo_manager() == "grid"]
            assert shown, tab
        app.show_page("alerts")                                             # page key of older versions
        assert app._current_page == "settings" and app._settings_tab == "Voix"
    finally:
        app.close()


def body_sections(page: object) -> list:
    sf = getattr(page, "scroll_frame", None)
    inner = sf.winfo_children()[0] if sf is not None else None
    return list(getattr(inner, "_sections", []) or [])


@tu.needs_display
def test_settings_controls_write_the_config(home: Path, tmp_path: Path) -> None:
    app, voice, (engines, _ov) = tu._build(tmp_path)
    try:
        tu._pump(app, 12.0, lambda: app.engine is not None and not app._busy)
        app.open_settings("Voix")
        tu._pump(app, 0.2)
        seg = app._danger_seg
        seg._click("  Bip seul  ")
        assert (app.cfg.beep_on_danger, app.cfg.danger_voice) == (True, "bip")
        seg._click("  Voix seule  ")
        assert app.cfg.beep_on_danger is False
        app.reset_settings()
        assert seg.get().strip() == "Bip + voix" and app.cfg.beep_on_danger
        # the radar section only exists in the radar mode
        app.open_settings("Affichage")
        tu._pump(app, 0.1)
        radar = app._radar_section.wrap
        assert radar.winfo_manager() == ""
        app.set_option("overlay_mode", "radar")
        app._refresh_radar_rows()
        tu._pump(app, 0.05)
        assert radar.winfo_manager() == "grid"
        app.open_settings("Général")
        tu._pump(app, 0.05)
        assert radar.winfo_manager() == ""                              # other tab: hidden again
        # an engine-start setting rebuilds the engine (detector kept)
        n = len(engines)
        app.set_option("hotkey_diag", "Ctrl+F7")
        tu._pump(app, 10.0, lambda: len(engines) > n and not app._busy)
        assert len(engines) == n + 1
    finally:
        app.close()


class LocatingEngine(tu.FakeEngine):
    """Running, the minimap not found yet (LOCATING): the status strip offers "Calibrer"."""

    def get_status(self) -> tu.FakeStatus:
        st = super().get_status()
        if self.is_running():
            return dataclasses.replace(st, state="locating", message="Recherche de la minimap…", game_time=None)
        return st

    def get_overlay_state(self) -> None:
        return None


@tu.needs_display
def test_status_line_fix_button_and_pregame_layout(home: Path, tmp_path: Path) -> None:
    pytest.importorskip("customtkinter")
    engines: list = []

    def factory(cfg: Config, v: object, det: object, src: object) -> LocatingEngine:
        e = LocatingEngine(cfg, v, det, src)
        engines.append(e)
        return e

    app = ui.CoachApp(Config(ui_onboarding_done=True, ui_seen_changelog=ui_kit.CHANGELOG_VERSION),
                      engine_factory=factory, overlay_factory=lambda c, p: None, voice=tu.FakeVoice(),
                      detector_factory=lambda c: None, hotkeys=False, save_path=tmp_path / "c.json")
    try:
        tu._pump(app, 12.0, lambda: app.state_title.cget("text") == "Recherche de la minimap")
        assert app.state_title.cget("text") == "Recherche de la minimap"
        tu._pump(app, 4.0, lambda: app.hero._fix_on)
        assert app.hero._fix_on and app.btn_fix.cget("text") == "Calibrer" and app._fix_action == "calibrate"
        # no game data: the radar block is out of the way
        engines[0].stop()
        tu._pump(app, 8.0, lambda: app.state_title.cget("text") == "Analyse arrêtée")
        assert not app.hero._fix_on
        assert app._live_layout is False and app.enemies_card.winfo_manager() == ""
        assert app.radar_lbl.master.winfo_manager() == ""
    finally:
        app.close()


@tu.needs_display
def test_in_game_layout_update_and_post_game(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app, _voice, (engines, _ov) = tu._build(tmp_path)
    try:
        tu._pump(app, 12.0, lambda: app._live_layout and app._radar_live)
        assert app._live_layout and app.enemies_card.winfo_manager() == "grid"
        assert app.state_title.cget("text").startswith("En jeu")
        # a new version found: one button in the sidebar, one click to the tab
        info = SimpleNamespace(version="9.9.9")
        app._update_info = info
        app._show_update_available(info)
        tu._pump(app, 0.1)
        assert app._update_side.winfo_manager() == "grid" and "9.9.9" in app._update_side.cget("text")
        app._update_side.invoke()
        tu._pump(app, 0.2)
        assert app._current_page == "settings" and app._settings_tab == "Mises à jour"
        app._show_update_available(None)
        assert app._update_side.winfo_manager() == ""
        # after a game: the new record is announced once
        toasts: list[str] = []
        monkeypatch.setattr(app, "show_toast", lambda text, level="info": toasts.append(text))
        app._post_game_watch = "old.json"
        app._show_games([{"path": "new.json", "start": "2026-09-30T21:00:00", "champion": "Garen"}])
        assert toasts and "Partie enregistrée" in toasts[-1] and app._post_game_watch is None
        app._show_games([{"path": "new.json", "start": "2026-09-30T21:00:00", "champion": "Garen"}])
        assert len(toasts) == 1
        # champion select seen from another page: a toast says where the card is
        app.show_page("help")
        app._cs_sig = None
        app._show_champ_select(SimpleNamespace(title="AHRI · MID", lines=("Face à Zed",)))
        assert "Sélection des champions" in toasts[-1]
    finally:
        app.close()


@tu.needs_display
def test_minimum_window_layout(home: Path, tmp_path: Path) -> None:
    """980 x 640: the level and the quick toggles stay in the sidebar, the setting controls stay inside
    their card, the tabs fit on one line."""
    app, _voice, _ = tu._build(tmp_path)
    try:
        app.root.geometry("980x640+0+0")
        app.build_all_pages()
        tu._pump(app, 1.0)
        for b in app._skill_btns.values():
            assert b.winfo_ismapped()
        pill_y = app.pill_text.winfo_rooty()
        assert max(b.winfo_rooty() + b.winfo_height() for b in app._skill_btns.values()) <= pill_y
        for tab in ui.SETTINGS_TABS:
            app.show_page("settings", tab)
            tu._pump(app, 0.3)
            bad = []
            for slot in app._row_slots:
                try:
                    if not slot.winfo_ismapped():
                        continue
                    card = slot.master.master.master            # slot -> row -> body -> card
                    if slot.winfo_rootx() + slot.winfo_width() > card.winfo_rootx() + card.winfo_width() + 1:
                        bad.append(slot.desc_label.cget("text")[:40] if slot.desc_label else "?")
                except Exception:
                    continue
            assert not bad, (tab, bad)
        bar = app.pages["settings"].head.grid_slaves(row=1)[0]
        assert bar.winfo_rootx() + bar.winfo_width() <= app.root.winfo_rootx() + app.root.winfo_width()
    finally:
        app.close()
