"""5.1 CLEAN — audit B10 « macros : un piège, des défauts que personne n'a demandés, des descriptions qui mentent ».

tests/wow/macro51_scenario.lua, default module profile (« sur »), on the faithful Forever mock:
« TAI Monture » never holds the Hearthstone (not created without a mount item), no macro prints to the chat, old
harmful TreeAI macros are neutralised by the player's click, « Sert à » is rebuilt from the rendered body and checked
by an independent parse of that body (modifiers, spells, items) for every macro × preset, a new hunter gets the 5
essentials (an installed set keeps « Complet »), « TAI Mana » is off for a hunter, no aspect guard by default (one GCD
action per press), no suggested key holds a modifier its macro tests, and the tutorial is one click from the page.
Static part: the generator's lint (treeaicoach/wow/macros/generator.py lint_key) and the Lua module."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
SRC = (ADDON / "Modules" / "Macros.lua").read_text(encoding="utf-8")
DATA = (ADDON / "Data" / "Macros.lua").read_text(encoding="utf-8")


def test_no_chat_line_and_no_hearthstone_in_any_macro_text():
    code = re.sub(r"--[^\n]*", "", SRC)
    assert "/run print" not in code.replace('find("^%s*/run print%(")', "")   # only the detector of the OLD body
    assert "{item:hearth}" not in DATA and "print(" not in DATA
    # the data file is the generator's output
    r = subprocess.run(["python3", str(ROOT / "tools" / "wow_build_macros.py"), "--check"], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr


def test_describe_is_used_everywhere_a_purpose_is_shown():
    assert "function M.Describe(def, body)" in SRC and "function M.TutoLines(st)" in SRC
    assert "M.Purpose(def, st.expected)" in SRC                       # the page row
    assert '"Sert à : " .. (def.d' not in SRC                        # the old static sentence
    assert "RenderHowTo" not in SRC and "function M:RenderTuto" in SRC
    # the aspect guard is opt-in
    assert re.search(r'if key == "guard" then return false end', SRC)


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor,cls", [("forever", "HUNTER"), ("modern", "HUNTER"), ("legacy", "HUNTER"),
                                         ("forever", "PRIEST"), ("forever", "WARRIOR"), ("forever", "DRUID"),
                                         ("forever", "ROGUE"), ("forever", "MAGE"), ("forever", "PALADIN"),
                                         ("forever", "SHAMAN"), ("forever", "WARLOCK")])
def test_macro51_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--no-storm", "--profile", "sur",
                        "--flavor", flavor, "--class", cls, "--level", "40",
                        "--scenario", str(ROOT / "tests/wow/macro51_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=300, cwd=ROOT)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-4000:] + r.stderr[-2000:]
