"""UI v2 (docs/DESIGN.md redesign): analysis tabs (Parties / Progrès / Replay), launcher system rows,
overlay test, update fallback. Skipped without a display (like test_ui.py)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import test_ui as tu

from treeaicoach import ui, ui_kit

home = tu.home


def test_subsystem_rows_pure() -> None:
    rows = ui_kit.subsystem_rows(state="CAPTURE_BLACK", running=True, detector="classic", voice_backend="print",
                                 lcu_text="Client LoL : non trouvé")
    by = {r[0]: r for r in rows}
    assert set(by) == {"game", "minimap", "lcu", "ia", "ai", "voice"}
    assert by["ia"][1] == "Détection" and by["ai"][1] == "IA conseil" and by["ai"][5] == "settings_ai"
    assert by["game"][2] == 2 and by["game"][5] == "help_borderless"
    assert by["ia"][2] == 1 and by["voice"][2] == 2 and by["lcu"][5] == "lcu_help"
    ok = {r[0]: r for r in ui_kit.subsystem_rows(state="RUNNING", running=True, minimap_found=True,
                                                 minimap_method="auto", detector="onnx", voice_backend="sapi",
                                                 lcu_text="Client LoL : connecté", ai_provider="gemini",
                                                 ai_key_set=True)}
    assert all(r[2] == 0 for r in ok.values())
    idle = ui_kit.subsystem_rows(running=False, muted=True)
    assert idle[0][5] == "start" and idle[-1][5] == "unmute"


def test_objectives_text_and_ui_text() -> None:
    class Ob:
        def __init__(self, name: str, nxt: float | None, alive: bool = False) -> None:
            self.name, self.next_spawn, self.alive = name, nxt, alive

    class Ov:
        game_time = 600.0
        objectives = [Ob("Baron", 1500.0), Ob("Dragon", 684.0), Ob("Héraut", None, True)]

    assert ui_kit.objectives_text(Ov()) == "Héraut là · Drake 1:24 · Baron 15:00"
    assert ui_kit.objectives_text(None) == ""
    assert ui.ui_text("Lee Sin " + chr(0x2014) + " vu il y a 3 s") == "Lee Sin · vu il y a 3 s"
    assert ui.ui_text(chr(0x2014)) == "-"


@tu.needs_display
def test_analysis_tabs_replay_progress(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    games_dir = home / "home" / "games"
    games_dir.mkdir(parents=True)
    src = Path(__file__).parent / "fixtures" / "game_record_sample.json"
    rec = json.loads(src.read_text(encoding="utf-8"))
    paths_ = []
    for i in range(3):
        p = games_dir / f"2026-09-{20 + i}_2100_Garen.json"
        shutil.copy(src, p)
        paths_.append(p)
    games = [{"path": str(p), "start": rec["summary"]["start"], "champion": "Garen", "result": "Win",
              "kills": 3, "deaths": 4, "assists": 5, "duration": 1690.0, "ganks": 4, "ganks_survived": 2}
             for p in paths_]
    real = ui._report_function
    monkeypatch.setattr(ui, "_report_function", lambda name: {"list_games": lambda n=50: games}.get(name)
                        or real(name))
    app, _voice, _ = tu._build(tmp_path)
    try:
        app.show_page("analysis")
        tu._pump(app, 3.0, lambda: app._games == games)
        assert app._games == games and len(app._replay_choices) == 3
        page = app.pages["analysis"]
        page.select_tab("Progrès")
        app.refresh_progress()
        tu._pump(app, 8.0, lambda: isinstance(app._progress_sig, int))
        assert app._progress_sig == 3
        app.open_replay(games[0])
        tu._pump(app, 8.0, lambda: app._replay.get("model") is not None)
        model = app._replay["model"]
        assert model is not None and model.markers
        assert "/" in app.replay_clock.cget("text")
        t0 = app._replay["t"]
        app.replay_jump(1)
        assert app._replay["t"] >= t0
        app.replay_toggle()
        tu._pump(app, 0.6)
        assert app._replay["playing"] and app._replay["t"] > t0
        app.replay_toggle()
        assert not app._replay["playing"]
        app._replay_click(10)
        assert abs(app._replay["t"] - model.start) < 60
    finally:
        app.close()


@tu.needs_display
def test_system_rows_overlay_test_and_update_fallback(home: Path, tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(ui.webbrowser, "open", lambda url: opened.append(url))
    app, _voice, (engines, overlays) = tu._build(tmp_path)
    try:
        tu._pump(app, 4.0, lambda: app.sys_rows["game"]["sig"] is not None and engines and engines[0].is_running())
        assert all(r["sig"] is not None for r in app.sys_rows.values())
        assert app.hero.timers.cget("text") is not None
        overlays[0].ok = True
        monkeypatch.setattr(app, "_in_game", lambda: False)
        app.test_overlay()
        tu._pump(app, 6.0, lambda: app._overlay_test is not None)
        assert app._overlay_test is not None
        st = overlays[0].provider()
        assert st is not None and getattr(st, "threat_level", None) == 0      # "safe" sample first
        app.open_manual_download()
        from treeaicoach import updater
        assert opened == [updater.MANUAL_DOWNLOAD_URL]
    finally:
        app.close()
