"""Agent SIMPLE: quest guidance in plain French (Modules/Plain.lua) + robustness: no jargon in the main UI."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
SRC = ADDON / "Modules" / "Plain.lua"
# modules whose strings reach the main quest UI (Abandon/Tracker keep their numbers only for /tai … detail)
UI_MODULES = ("Todo.lua", "Planner.lua", "Pro.lua")


def test_toc_loads_plain_before_the_quest_modules():
    lines = [x.strip() for x in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    i = lines.index("Modules\\Plain.lua")
    for m in ("Quests", "Tracker", "Todo", "Planner", "Pro", "Abandon"):
        assert lines.index(f"Modules\\{m}.lua") > i


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
def test_plain_spec():
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/plain_spec.lua"), str(SRC)],
                       capture_output=True, text=True, timeout=30, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0 failures" in r.stdout


def test_no_jargon_strings_in_ui_modules():
    bad = []
    for name in UI_MODULES:
        for n, line in enumerate((ADDON / "Modules" / name).read_text(encoding="utf-8").splitlines(), 1):
            code = re.sub(r"--.*$", "", line)
            for s in re.findall(r'"([^"]*)"', code):
                low = s.lower()
                # only real internal jargon: XP/min, XP/h, % and minutes are wanted by the player
                if any(w in low for w in ("/100", "λ", "coefficient")) or re.search(r"\bscore\b", low):
                    bad.append(f"{name}:{n}: {s}")
    assert not bad, bad


def test_app_quest_lines_are_plain():
    from treeaicoach.wow import live

    assert live.is_plain("Va tuer 6 Loups au nord (≈ 3 min)")
    assert live.is_plain("Les loups · 400 XP/min") and not live.is_plain("score 74")
    assert live.is_plain("Récompense +12 %") and live.is_plain("Niveau 27 : 62 %") and not live.is_plain("λ = 0,3")
