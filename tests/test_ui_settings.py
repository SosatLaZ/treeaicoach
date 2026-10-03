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

from treeaicoach import ui_kit
from treeaicoach import ui_common as ui
from treeaicoach.config import REMOVED_KEYS, Config, load_config, save_config
from treeaicoach.ui_page_alerts import danger_mode, danger_mode_fields

home = tu.home


# ======================================================================================
# Pure
# ======================================================================================
def test_navigation() -> None:
    assert [k for k, _l, _i in ui.PAGES] == ["home", "overlay", "alerts", "analysis", "settings", "about"]
    for old, page in ui.PAGE_ALIASES.items():
        assert page in {k for k, _l, _i in ui.PAGES}, old
    assert ui_kit.SHORTCUTS[0][0] == "Ctrl + 1 … 6"

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
        "ui.py", "ui_common.py", "ui_dialogs.py", "ui_kit.py", "ui_widgets.py", "ui_page_home.py", "ui_page_alerts.py",
        "ui_page_analysis.py", "ui_page_overlay.py", "ui_page_settings.py", "ui_page_about.py", "ui_preview.py"))
    for name in REMOVED_KEYS:
        assert f'"{name}"' not in src, name
    assert "apply_preset" not in src and "PRESET_LABELS" not in src


# ======================================================================================
# Tk
# ======================================================================================


def body_sections(page: object) -> list:
    sf = getattr(page, "scroll_frame", None)
    inner = sf.winfo_children()[0] if sf is not None else None
    return list(getattr(inner, "_sections", []) or [])




class LocatingEngine(tu.FakeEngine):
    """Running, the minimap not found yet (LOCATING): the status strip offers "Calibrer"."""

    def get_status(self) -> tu.FakeStatus:
        st = super().get_status()
        if self.is_running():
            return dataclasses.replace(st, state="locating", message="Recherche de la minimap…", game_time=None)
        return st

    def get_overlay_state(self) -> None:
        return None






