"""Qt launcher (docs/LAUNCHER.md): pages, settings coverage, speed, threading, close, calibration.

Runs headless with ``QT_QPA_PLATFORM=offscreen`` (set below when no display is given)."""
from __future__ import annotations

import dataclasses
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from treeaicoach import ui  # noqa: E402
from treeaicoach.config import Config, load_config  # noqa: E402


class FakeVoice:
    backend = "fake"

    def __init__(self) -> None:
        self.said: list[str] = []
        self.params: dict = {}
        self.stopped = False

    def say(self, text: str, prio: int = 0, **_kw: object) -> None:
        self.said.append(text)

    def preview(self, text: str = "") -> None:
        self.said.append("preview:" + text)

    def set_params(self, **kw: object) -> None:
        self.params = kw

    def list_voices(self) -> list[str]:
        return ["Microsoft Hortense"]

    def stop(self) -> None:
        self.stopped = True


class FakeEngine:
    def __init__(self, *a: object, slow_stop: float = 0.0) -> None:
        self.running = False
        self.applied = 0
        self.stopped = 0
        self.muted = False
        self._slow = slow_stop

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        time.sleep(self._slow)
        self.running = False
        self.stopped += 1

    def is_running(self) -> bool:
        return self.running

    def apply_config(self, cfg: Config) -> None:
        self.applied += 1

    def get_status(self) -> SimpleNamespace:
        return SimpleNamespace(state=SimpleNamespace(value="RUNNING"), message="Analyse en cours", game_time=95.0,
                               fps=12.0, enemies_visible=2, last_alert="", detector="onnx", voice="fake",
                               minimap_rect=(1, 2, 3, 4), locate_method="auto")

    def get_overlay_state(self) -> None:
        return None

    def request_relocate(self) -> None:
        pass

    def mute(self, on: bool) -> None:
        self.muted = on


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TREEAICOACH_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(ui, "_report_function", lambda name: (lambda n=50: []) if name == "list_games" else None)
    return tmp_path


def _pump(app: ui.CoachApp, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.qapp.processEvents()
        app._dispatcher.drain()
        time.sleep(0.005)


def _build(tmp_path: Path, **kw: object) -> tuple[ui.CoachApp, FakeVoice, list[FakeEngine]]:
    engines: list[FakeEngine] = []

    def factory(cfg: Config, voice: object, det: object, src: object) -> FakeEngine:
        e = FakeEngine()
        engines.append(e)
        return e

    voice = FakeVoice()
    cfg = Config(ui_onboarding_done=True, ui_seen_changelog="99", lcu_enabled=False)
    app = ui.CoachApp(cfg, engine_factory=factory, overlay_factory=lambda c, p: None, voice=voice,
                      detector_factory=lambda c: None, hotkeys=False, save_path=tmp_path / "config.json", **kw)
    app._no_dialogs = True
    return app, voice, engines


def test_geometry_helpers() -> None:
    assert ui.parse_geometry("1200x800+10+-5") == (1200, 800, 10, -5)
    assert ui.parse_geometry("1200x800") == (1200, 800, None, None)
    assert ui.parse_geometry("nope") is None
    scr = (0, 0, 1280, 720)
    # too big for a 1280 x 720 screen: shrunk and kept on screen
    assert ui.fit_geometry((1920, 1080, 0, 0), scr) == (0, 0, 1280, 720)
    # off-screen position (monitor unplugged): centred
    x, y, w, h = ui.fit_geometry((1000, 640, 3000, 100), scr)
    assert (w, h) == (1000, 640) and 0 <= x and x + w <= 1280
    assert ui.fit_geometry(None, scr)[2:] == (ui.DEFAULT_W, 720)


def test_every_setting_is_reachable(home: Path) -> None:
    """Each Config field has a control, or is listed in HIDDEN_SETTINGS with the reason."""
    app, _v, _e = _build(home)
    try:
        app.build_all_pages()
        assert not app._failed_pages
        names = {f.name for f in dataclasses.fields(Config)}
        missing = sorted(n for n in names if n not in app._refreshers and n not in ui.HIDDEN_SETTINGS)
        assert not missing, missing
        assert not [n for n in ui.HIDDEN_SETTINGS if n not in names]
    finally:
        app.close()


def test_pages_switch_fast_and_status_updates(home: Path) -> None:
    app, voice, engines = _build(home)
    try:
        app.show()
        _pump(app, 0.5)                           # backend started on a worker, pages prebuilt
        assert engines and app.engine is engines[0] and engines[0].running
        _pump(app, 1.0)
        assert set(app.pages) == {k for k, _l, _i in ui.PAGES}
        app._switch_ms.clear()
        for _ in range(3):
            for key, _l, _i in ui.PAGES:
                app.show_page(key)
                app.qapp.processEvents()
        assert max(app._switch_ms) < 50, app._switch_ms
        app.show_page("home")
        app._refresh_status()
        view = app.page_views["home"]
        assert view.title.text().startswith("En jeu") and "1:35" in view.live.text()
        app.show_page("dashboard")                 # old page names still land somewhere sensible
        assert app._current_page == "home"
        app.show_page("settings", "Voix")
        assert app._current_page == "alerts"
    finally:
        app.close()


def test_setting_change_applies_live_and_saves(home: Path) -> None:
    app, voice, engines = _build(home)
    try:
        _pump(app, 0.5)
        app.build_all_pages()
        app.set_option("voice_volume", 40)
        assert voice.params.get("volume") == 40
        app.set_option("overlay_mode", "radar")
        assert app.page_views["overlay"].radar.isVisibleTo(app.page_views["overlay"].page)
        _pump(app, 0.7)                           # debounced save
        assert load_config(home / "config.json").voice_volume == 40
        app.test_voice()                          # the real chosen voice (voice.preview), never say()
        assert voice.said == ["preview:"]
        app.apply_skill_level("debutant")
        assert app.cfg.skill_level == "debutant"
        assert app.page_views["home"].level.value() == "debutant"
    finally:
        app.close()


def test_callbacks_after_close_are_dropped(home: Path) -> None:
    app, voice, engines = _build(home)
    _pump(app, 0.3)
    hit: list[int] = []
    gate = threading.Event()
    app.run_job(lambda: gate.wait(2), lambda _r: hit.append(1))
    app.close()
    gate.set()
    time.sleep(0.1)
    app._dispatcher.drain()
    assert not hit
    assert all(not e.running for e in engines)
    app.later(0, lambda: hit.append(2))           # nothing scheduled after close runs
    app.show_page("about")                        # no page built after close
    app.qapp.processEvents()
    assert not hit


def test_engine_failure_keeps_window_open(home: Path) -> None:
    def boom(*_a: object) -> object:
        raise RuntimeError("no engine")

    app = ui.CoachApp(Config(ui_onboarding_done=True), engine_factory=boom, overlay_factory=lambda c, p: None,
                      voice=FakeVoice(), detector_factory=lambda c: None, hotkeys=False,
                      save_path=home / "c.json")
    try:
        _pump(app, 0.5)
        assert app.engine is None and "moteur" in (app.engine_error or "")
        assert app.status_snapshot()["key"] == "NO_ENGINE"
        app.toggle_engine()                      # retry: no crash
        _pump(app, 0.3)
    finally:
        app.close()


def test_run_app_smoke(home: Path) -> None:
    rc = ui.run_app(Config(ui_onboarding_done=True), smoke_seconds=0.5,
                    _engine_factory=lambda *a: FakeEngine(), _overlay_factory=lambda c, p: None, _voice=FakeVoice(),
                    _detector_factory=lambda c: None, _hotkeys=False, _save_path=home / "c.json")
    assert rc == 0


def test_calibration_dialog_validates(home: Path) -> None:
    from treeaicoach import calibration as cal

    ui.ensure_qapp()
    img = np.zeros((1080, 1920, 3), np.uint8)
    origin = SimpleNamespace(x=0, y=0, w=1920, h=1080)
    dlg = cal.CalibrationDialog(None, Config(), img, origin)
    d = dlg.dialog
    d.press(1600, 780)
    d.motion(1880, 1060)
    d.release()
    d.validate()
    assert d.result_rect == {"screen_w": 1920, "screen_h": 1080, "x": 1600, "y": 780, "w": 280, "h": 280}


def test_export_button_builds_one_zip(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Réglages → Maintenance et support → "Créer le zip": captures + games in one file, shown in
    the file manager (users were sending dozens of files one by one)."""
    from treeaicoach import paths, ui_common

    opened: list[Path] = []
    monkeypatch.setattr(ui_common, "open_path", lambda p, select=False: opened.append(Path(p)) or True)
    paths._reset_cache()
    base = paths.user_data_dir()
    (base / "collect").mkdir(parents=True, exist_ok=True)
    (base / "collect" / "20261009_200000_0000.png").write_bytes(b"\x89PNG")
    (base / "games").mkdir(parents=True, exist_ok=True)
    (base / "games" / "2026-10-09_2006_Swain.json").write_text("{}", encoding="utf-8")
    app, _v, _e = _build(home)
    try:
        app.show_page("advanced")
        _pump(app, 0.3)
        texts = [b.text() for b in app.win.findChildren(app.QtWidgets.QPushButton)]
        assert "Créer le zip" in texts
        app.export_for_treeai()
        end = time.monotonic() + 10
        while not opened and time.monotonic() < end:
            _pump(app, 0.05)
        assert opened and opened[0].suffix == ".zip" and opened[0].parent == base / "exports"
    finally:
        app.close()
        paths._reset_cache()


DISCORD_SNAP = {
    "state": "connected", "status": "Connecté à #Général.", "ok": True, "error": "", "bot_name": "TreeAI",
    "latency_ms": 48, "in_game": False, "user_id": "123456789012345678",
    "guilds": [{"id": "10", "name": "Les potes", "members": 12, "voice_channels": [
        {"id": "101", "name": "Général", "members": 2, "people": ["Ami", "Dexter"], "can_speak": True, "missing": [],
         "me_here": True, "bot_here": True},
        {"id": "102", "name": "Muet", "members": 0, "people": [], "can_speak": False, "missing": ["parler"],
         "me_here": False, "bot_here": False}]},
        {"id": "20", "name": "Clan", "members": 5, "voice_channels": []}],
    "user": {"id": "123456789012345678", "found": True, "name": "Dexter", "channel_id": "101", "channel": "Général",
             "guild": "Les potes", "guild_id": "10"},
    "channel": {"id": "101", "name": "Général", "guild": "Les potes", "guild_id": "10"},
    "voice_source": "edge", "voice_checked": True,
    "events": [{"t": "21:04:10", "text": "A dit « Bot gank »", "channel": "Général", "ok": True},
               {"t": "21:03:58", "text": "A rejoint #Général", "channel": "Général", "ok": True}],
}


def test_discord_page_shows_the_bot(home: Path) -> None:
    from treeaicoach import ui_page_discord as pd

    app, _v, _e = _build(home)
    try:
        app.show()
        app.show_page("discord")
        view = app.page_views["discord"]
        assert view.big.text() == "Bot désactivé"                     # no bot yet: offline card, no crash
        view.snapshot_fn = lambda: dict(DISCORD_SNAP)                  # before enabling: no real bot in tests
        app.set_options(discord_enabled=True, discord_token="x" * 30 + ".abcd." + "y" * 27,
                        discord_user_id="123456789012345678")
        view.tick()
        assert view.big.text() == "Connecté en tant que TreeAI"
        assert "#Général (Les potes)" in view.detail.text() and "48 ms" in view.detail.text()
        assert "Voix : naturelle" in view.detail.text() and view.voice_row.title.text() == "Voix : naturelle"
        view.snapshot_fn = lambda: dict(DISCORD_SNAP, voice_source="", voice_checked=True,
                                        voice_failures={"edge": "403"})
        view.tick()
        assert view.voice_row.title.text().startswith("Voix : aucune") and "403" in view.voice_row.desc.text()
        view.snapshot_fn = lambda: dict(DISCORD_SNAP)
        view.tick()
        titles = [view.steps[k].title.text() for k in ("token", "guilds", "me", "perms", "test")]
        assert all(t.startswith(pd.OK_MARK) for t in titles), titles
        assert "Les potes (12 membres)" in view.steps["guilds"].desc.text()
        assert "Dexter : dans #Général" in view.steps["me"].desc.text()
        assert view.server.count() == 2 and view.server.value() == "10"
        rows = view._chan_group._rows
        assert len(rows) == 2 and "tu es ici" in rows[0].desc.text() and "il manque : parler" in rows[1].desc.text()
        assert not rows[1].control.isEnabled() and not rows[0].control.isEnabled()   # bot already there / no right
        assert len(view._log_group._rows) == 2 and view.leave_btn.isEnabled()
        view._pick_server("20")
        assert view._chan_group._rows[0].title.text() == "Aucun salon vocal"
        # an error: the real reason is on the card, the checklist says what is left
        err = dict(pd.offline_snapshot(app.cfg), state="error", error="ClientConnectorError : Cannot connect",
                   status="Discord ne répond pas, nouvel essai dans 5 s.")
        view.snapshot_fn = lambda: err
        view.tick()
        assert view.big.text() == "Erreur de connexion" and "Cannot connect" in view.detail.text()
        assert view.steps["guilds"].title.text().startswith(pd.WAIT_MARK)
        assert not view.say_gank.isEnabled()
        app.show_page("home")
        view.tick()
        assert not view.timer.isActive()                               # polls only while shown
    finally:
        app.close()


def test_players_card_on_home_only_when_on(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Accueil "Joueurs" card (player_intel.py): hidden while the switch is off, by champion when on."""
    from treeaicoach import player_intel as pi

    w = pi.PlayerIntelWatcher(client=None, resolve=lambda c: None)
    with w._lock:
        w._players = {"k1": pi.PlayerIntel("LeeSin", "Lee Sin", False, "JUNGLE", ranked_loaded=True, tier="PLATINUM",
                                           division=2, lp=45, wins=30, losses=28, history_loaded=True, games=10,
                                           streak=-3, champ_games=2),
                      "k2": pi.PlayerIntel("Lulu", "Lulu", True, "UTILITY")}
    monkeypatch.setattr(pi, "get_default_watcher", lambda: w)
    app, _v, _e = _build(home)
    try:
        app.show()
        _pump(app, 0.5)
        app.show_page("home")
        view = app.page_views["home"]
        app._refresh_status()
        assert view.pl.isHidden()                          # off by default
        app.cfg.player_intel = True
        app.cfg.lcu_enabled = True
        app._refresh_status()
        assert not view.pl.isHidden()
        txt = view.pl_enemies.text()
        assert txt.startswith("Lee Sin (jungler) · Platine 2 · 45 LP") and "3 défaites d'affilée" in txt
        assert "Lulu (support)" in view.pl_allies.text()
        app.cfg.player_intel = False
        app._refresh_status()
        assert view.pl.isHidden()
    finally:
        app.close()
