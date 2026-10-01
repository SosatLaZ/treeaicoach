"""Launcher stress test: random page / tab switching from t=0 (before the backend is ready), game
start / stop, demo toggles, minimise / restore, DPI changes, settings changes and worker results
(game list, progress, replay, champion select, update check, toasts) injected at random times.

Fails on any Tk callback exception or logged UI error, on a page that is blank / partially built /
not the one asked for, and on a page switch slower than SWITCH_MAX_S. Run longer / other seeds with
``TREEAI_STRESS_STEPS=2000 TREEAI_STRESS_SEEDS=1,2,3 pytest tests/test_ui_stress.py``.
"""
from __future__ import annotations

import json
import logging
import os
import random
import shutil
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import test_ui as tu

from treeaicoach import ui
from treeaicoach.config import Config

home = tu.home
FIXTURE = Path(__file__).parent / "fixtures" / "game_record_sample.json"
SWITCH_MAX_S = float(os.environ.get("TREEAI_STRESS_SWITCH_MAX", "0.3"))
STEPS = int(os.environ.get("TREEAI_STRESS_STEPS", "260"))
SEEDS = [int(s) for s in os.environ.get("TREEAI_STRESS_SEEDS", "1,2").split(",") if s.strip()]


class _Errors(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(f"{record.name}: {record.getMessage()} {record.exc_text or ''}"[:2000])


def _games(dir_: Path, n: int) -> list[dict]:
    out = []
    for i in range(n):
        p = dir_ / f"2026-09-{10 + i:02d}_2100_Garen.json"
        if not p.exists():
            shutil.copy(FIXTURE, p)
        out.append({"path": str(p), "start": f"2026-09-{10 + i:02d}T21:00:00", "champion": "Garen",
                    "champion_name": "Garen", "result": "Win" if i % 2 else "Lose", "kills": i, "deaths": 2,
                    "assists": 3, "duration": 1500.0 + i, "ganks": 2, "ganks_survived": 1,
                    "precision": 60 + i, "position": "TOP"})
    return list(reversed(out))


def _visible_page(app: Any) -> list[str]:
    return [k for k in app._page_builders if k in app._built and dict.get(app.pages, k) is not None
            and dict.get(app.pages, k).winfo_manager() == "grid"]


def _check_page(app: Any, key: str, problems: list[str], where: str) -> None:
    shown = _visible_page(app)
    if shown != [key]:
        problems.append(f"{where}: visible pages {shown}, expected [{key}]")
        return
    page = dict.get(app.pages, key)
    if not page.winfo_ismapped():
        problems.append(f"{where}: page {key} not mapped")
    if getattr(page, "is_error_page", False):
        problems.append(f"{where}: page {key} is the error page")
    tab = getattr(page, "current_tab", None)
    if tab is not None:
        sf = getattr(page, "scroll_frame", None)
        inner = sf.winfo_children()[0] if sf is not None and sf.winfo_children() else None
        secs = [s for s in getattr(inner, "_sections", []) or [] if s.winfo_manager() == "grid"]
        if not secs:
            problems.append(f"{where}: page {key} tab {tab} shows no section")
    elif len(page.winfo_children()) < 2:
        problems.append(f"{where}: page {key} looks empty")
    btn, ind, _icon = app._nav[key]
    if str(ind.cget("bg")).upper() != ui.ACCENT.upper():
        problems.append(f"{where}: nav indicator of {key} not lit")


def run_stress(app: Any, engines: list, seed: int, steps: int, games_dir: Path) -> tuple[list[str], list[float]]:
    rng = random.Random(seed)
    problems: list[str] = []
    slow: list[float] = []
    games = _games(games_dir, 6)
    keys = [k for k, _l, _i in ui.PAGES] + list(ui.PAGE_ALIASES)
    progress_rows: list[dict] = []
    try:
        from treeaicoach import progress

        progress_rows = progress.collect(games_dir, last=20)
    except Exception:
        pass

    def act_switch() -> None:
        key = rng.choice(keys)
        tab = None
        if key == "settings" and rng.random() < 0.7:
            tab = rng.choice(ui.SETTINGS_TABS)
        elif key == "analysis" and rng.random() < 0.5:
            tab = rng.choice(("Parties", "Progrès", "Replay"))
        t0 = time.perf_counter()
        app.show_page(key, tab)
        app.root.update_idletasks()
        dt = time.perf_counter() - t0
        real = ui.PAGE_ALIASES.get(key, (key, None))[0]
        if dt > SWITCH_MAX_S:
            slow.append(dt)
            problems.append(f"slow switch to {key}/{tab}: {1000 * dt:.0f} ms")
        app.root.update()
        _check_page(app, real, problems, f"switch {key}/{tab}")

    def act_tab() -> None:
        page = dict.get(app.pages, app._current_page)
        sel = getattr(page, "select_tab", None)
        if callable(sel):
            tabs = ui.SETTINGS_TABS if app._current_page == "settings" else ("Parties", "Progrès", "Replay")
            t0 = time.perf_counter()
            sel(rng.choice(tabs))
            app.root.update_idletasks()
            if time.perf_counter() - t0 > SWITCH_MAX_S:
                problems.append(f"slow tab switch: {1000 * (time.perf_counter() - t0):.0f} ms")

    def act_game() -> None:
        if engines:
            e = engines[-1]
            r = rng.random()
            if r < 0.4:
                e.in_game = not e.in_game
            elif r < 0.6:
                app.toggle_engine()
            elif r < 0.7:
                app.toggle_demo()
            else:
                e.fixed_index = rng.choice((None, 0, 1, 2, 3))

    def act_window() -> None:
        r = rng.random()
        if r < 0.5:
            app.root.iconify()
            app.root.update()
            app.root.deiconify()
        else:
            app._on_scaling(rng.choice((1.0, 1.25, 1.0)), 1.0)

    def act_inject() -> None:
        r = rng.randrange(12)
        if r == 0:
            app._show_games(games[: rng.randint(0, len(games))])
        elif r == 1:
            app._show_champ_select(rng.choice((None, SimpleNamespace(title="AHRI · MID",
                                                                      lines=("Face à Zed", "Joue loin")))))
        elif r == 2 and progress_rows:
            app._progress_sig = None
            if "analysis" in app._built:
                app._show_progress(progress_rows)
        elif r == 3 and games:
            if "analysis" in app._built:
                app._replay_load(rng.choice(games))
        elif r == 4:
            info = rng.choice((None, SimpleNamespace(version="9.9.9")))
            app._update_info = info
            app._show_update_available(info)
            app._set_update_status(rng.choice(("", "Recherche…", "Erreur réseau")), ui.DANGER, manual=bool(info))
        elif r == 5:
            app.show_toast(rng.choice(("info", "Bonjour")), rng.choice(("info", "warning")))
        elif r == 6:
            app.set_option(rng.choice(("overlay_mode", "fog_mode", "voice_level", "hud_enabled", "sensitivity")),
                           rng.choice((("minimap", "radar", "off"), ("jungler", "all", "off"), ("minimal", "bavard"),
                                       (True, False), (0.8, 1.2)))[rng.randrange(2)])
        elif r == 7:
            app.apply_skill_level(rng.choice(("debutant", "intermediaire", "avance", "expert")))
        elif r == 8:
            app.reset_settings()
        elif r == 9:
            app._post_game_watch = "x"
            app.refresh_games()
        elif r == 10:
            rng.choice((app.show_changelog, app.show_about, lambda: app.show_onboarding(rng.randrange(3))))()
            if app._open_dialog is not None and rng.random() < 0.8:
                app._open_dialog._close()
        else:
            app.clear_journal()

    actions = [(act_switch, 0.40), (act_tab, 0.15), (act_game, 0.12), (act_window, 0.08), (act_inject, 0.25)]
    for i in range(steps):
        r, acc = rng.random(), 0.0
        for fn, w in actions:
            acc += w
            if r <= acc:
                break
        fn()
        if rng.random() < 0.35:                      # let timers / worker callbacks run in between
            tu._pump(app, rng.choice((0.0, 0.02, 0.1, 0.3)))
        else:
            app.root.update()
    tu._pump(app, 1.0)
    app.show_page("dashboard")
    app.root.update()
    _check_page(app, "dashboard", problems, "final")
    return problems, slow


@tu.needs_display
@pytest.mark.parametrize("seed", SEEDS)
def test_launcher_stress(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seed: int) -> None:
    games_dir = home / "home" / "games"
    games_dir.mkdir(parents=True, exist_ok=True)
    games = _games(games_dir, 6)
    real = ui._report_function
    monkeypatch.setattr(ui, "_report_function",
                        lambda name: {"list_games": lambda n=50: games}.get(name) or real(name))
    monkeypatch.setattr(ui.webbrowser, "open", lambda url: None)
    errors = _Errors()
    logging.getLogger("treeaicoach").addHandler(errors)
    tk_errors: list[str] = []
    try:
        app, _voice, (engines, _ov) = tu._build(tmp_path)        # no pump: actions start at t=0
        orig = app._tk_exception
        app.root.report_callback_exception = lambda et, ev, tb: (tk_errors.append(f"{et.__name__}: {ev}"),
                                                                 orig(et, ev, tb))
        try:
            problems, _slow = run_stress(app, engines, seed, STEPS, games_dir)
        finally:
            app.close()
    finally:
        logging.getLogger("treeaicoach").removeHandler(errors)
    assert not tk_errors, tk_errors[:5]
    assert not errors.records, errors.records[:5]
    assert not problems, problems[:10]
