"""4.1 STRIP: the « boutons rapides » strip (Modules/Shortcuts.lua) that replaces the « Prochaine étape » card at the
top left — Rendre · À prendre · Suivant · Zone · Groupe · Journal · TreeAI, one key binding each (Bindings.xml, no
default key) + « mode suivant », « Raccourcis conseillés » through Binds.lua (free keys only, never Ctrl-1…, the game's
pet bar). Static rules + the harness scenario on the real-client geometry (forever + modern): labels, counts, keys,
no overlap with the party frames in a group of 4, clicks call T.QuestBrain.SetMode, fallback without QuestBrain,
nothing in the chat, zero blocked action."""
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
SRC = ADDON / "Modules" / "Shortcuts.lua"
LUA = shutil.which("lua5.1")


def code(path):
    return "\n".join(line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())


def test_bindings_one_per_button_no_default_key():
    root = ET.parse(ADDON / "Bindings.xml").getroot()
    by = {b.get("name"): b for b in root}
    for name, arg in (("TREEAI_QB_TURNIN", "rendre"), ("TREEAI_QB_PICKUP", "prendre"),
                      ("TREEAI_STRIP_SUIVANT", "suivant"), ("TREEAI_QB_ZONE", "zone"),
                      ("TREEAI_QB_GROUP", "groupe"), ("TREEAI_STRIP_JOURNAL", "journal"),
                      ("TREEAI_MODE_NEXT", "mode")):
        b = by.get(name)
        assert b is not None, name
        assert b.get("header") == "TREEAI" and b.get("category") == "ADDONS" and b.get("default") is None, name
        assert 'TreeAI.StripAction("%s")' % arg in b.text, name
        assert "BINDING_NAME_" + name in SRC.read_text(encoding="utf-8"), name
    assert "TREEAI_MAIN" in by   # « TreeAI » button = the window binding
    src = SRC.read_text(encoding="utf-8")
    assert "T.StripAction = function" in src


def test_suggested_keys_never_game_defaults():
    src = code(SRC)
    keys = re.findall(r'"((?:CTRL|ALT|SHIFT)[A-Z-]*-\d)"', src)
    assert keys, "suggested keys"
    for k in keys:
        assert not re.fullmatch(r"CTRL-\d", k), k      # pet / stance bar (BONUSACTIONBUTTON1-10)
        assert not re.fullmatch(r"SHIFT-[1-6]", k), k  # action bar pages
    binds = (ADDON / "Modules" / "Binds.lua").read_text(encoding="utf-8")
    assert "function M.ApplyKeys(list)" in binds and "function M.FreeKey(keys)" in binds


def test_no_protected_calls_no_chat():
    c = code(SRC)
    for bad in ("SetBinding", "SaveBindings", "CastSpell", "UseAction", "TargetUnit", "RunMacroText", "SendChatMessage",
                "SecureActionButtonTemplate", ":Print(", "print(", "C_Timer", "OnUpdate", "AbandonQuest"):
        assert bad not in c, bad


def test_card_off_by_default():
    w = (ADDON / "Modules" / "Window.lua").read_text(encoding="utf-8")
    assert "defaults = { enabled = true, card = false," in w
    assert "W.db.cardOff41" in w


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_syntax_no_globals():
    for p in (SRC, ADDON / "Modules" / "Binds.lua", ADDON / "Modules" / "Window.lua"):
        assert subprocess.run(["luac5.1", "-p", str(p)], capture_output=True).returncode == 0, p
        listing = subprocess.run(["luac5.1", "-l", "-p", str(p)], capture_output=True, text=True).stdout
        assert "SETGLOBAL" not in listing, p


@pytest.mark.skipif(not LUA, reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["forever", "modern"])
def test_strip_scenario(flavor):
    r = subprocess.run([LUA, str(ROOT / "tests/wow/harness.lua"), "-q", "--no-storm", "--geometry", "ready=1",
                        "--flavor", flavor, "--class", "HUNTER",
                        "--scenario", str(ROOT / "tests/wow/strip_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=300, cwd=ROOT)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-6000:] + r.stderr[-2000:]
    out = r.stdout
    assert "STRIP avec QuestBrain : Rendre (4) · À prendre (3) · Suivant · Zone (9) *" in out
    assert "STRIP sans QuestBrain : Rendre (2) · À prendre · Suivant · Zone · Groupe · Journal · TreeAI" in out
    assert "[Alt-1]" in out and "chat:" not in out and "violation:" not in out
