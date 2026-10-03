"""UI v2 (docs/DESIGN.md redesign): analysis tabs (Parties / Progrès / Replay), launcher system rows,
overlay test, update fallback. Skipped without a display (like test_ui.py)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import test_ui as tu

from treeaicoach import ui_kit
from treeaicoach import ui_common as ui

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




