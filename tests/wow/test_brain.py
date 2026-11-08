"""BRAIN 4.0.2 — « Pilote auto » (Modules/Brain.lua + the window page Modules/BrainPage.lua): TOC, safe-mode module,
nothing protected / nothing in the chat / no timer of its own, the pure core spec (tests/wow/brain_spec.lua), the guarded
hooks (Planner errand stops, Quests page « En pause »), the documented contracts (README) and the harness scenario on every
API flavour (Forever first)."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
SRC = ADDON / "Modules" / "Brain.lua"
PAGE = ADDON / "Modules" / "BrainPage.lua"


def _code(path):
    return "\n".join(re.sub(r"--.*$", "", ln) for ln in path.read_text(encoding="utf-8").splitlines())


def test_toc_order():
    lines = [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    brain, page = lines.index("Modules\\Brain.lua"), lines.index("Modules\\BrainPage.lua")
    assert page == brain + 1
    for dep in ("Abandon", "ZonePlan", "Planner", "Route", "Window", "WindowPages", "JournalPage", "Detect"):
        assert lines.index("Modules\\%s.lua" % dep) < brain, dep


def test_safe_module_and_rules():
    code = _code(SRC)
    assert "T.SAFE_MODULES.Brain = true" in code
    for key in ("park", "lowValue", "zone", "style", "errands", "hearth", "follow", "notice"):
        assert 'key = "%s"' % key in code, key
    assert "follow = false" in code          # « Suivre le groupe » : opt-in


def test_nothing_protected_no_chat_no_timer():
    for path in (SRC, PAGE):
        code = _code(path)
        for bad in ("AbandonQuest(", "SetAbandonQuest", "SetSelectedQuest", "CastSpell", "UseItem", "TargetUnit",
                    "PickupContainerItem", "UseContainerItem", "TakeTaxiNode", "SendChatMessage", "C_Timer", ":Print(",
                    "RunMacro", "SetBinding", "AcceptQuest", "CompleteQuest", "GetQuestReward", "StaticPopup",
                    "ShowUIPanel", "hooksecurefunc", "NewTicker", "SetRaidTarget", "InviteUnit"):
            assert bad not in code, (path.name, bad)
    code = _code(SRC)
    # the only game calls that change anything: the quest watch list (not protected), out of combat
    assert "AddQuestWatch" in code and "RemoveQuestWatch" in code
    assert "if not api or inCombat() then return false end" in code
    # the page acts through the module, inside the player's click
    page = _code(PAGE)
    assert "T:UserAction(" in page and "Bn.Resume(" in page and "Bn.Undo(" in page


def test_hooks_are_tiny_and_guarded():
    planner = _code(ADDON / "Modules" / "Planner.lua")
    assert "if Bn and Bn.Inject and Bn.Inject(steps, cursor) then" in planner
    assert "if s.brain then local Bn = T.Brain return (Bn and Bn.StopDone and Bn.StopDone(s)) and true or false end" in planner
    pages = _code(ADDON / "Modules" / "WindowPages.lua")
    assert "if Bp and Bp.QuestsSection then Bp.QuestsSection(Ly) end" in pages


def test_readme_contracts():
    readme = (ADDON.parent / "README.md").read_text(encoding="utf-8")
    for s in ("T.Brain.Context()", "T.Brain.Parked(", "Group.Intent()", "Z.BRAIN_AWARE", "BrainPage", "GROUP_INTENT",
              "styleBy", "Brain.Inject("):
        assert s in readme, s


def test_session_budget_cap():
    sim = (ROOT / "tests" / "wow" / "session_sim.lua").read_text(encoding="utf-8")
    assert re.search(r"Brain = 40 }", sim)
    assert "Bugs = 1, Brain = 1 }" in sim       # idle: nothing at all


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
def test_core_spec_exact_decisions():
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/brain_spec.lua"), str(SRC)], capture_output=True, text=True,
                       timeout=60, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert " 0 failures" in r.stdout


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
@pytest.mark.parametrize("path", [SRC, PAGE])
def test_syntax_no_globals(path):
    assert subprocess.run(["luac5.1", "-p", str(path)], capture_output=True).returncode == 0
    listing = subprocess.run(["luac5.1", "-l", "-p", str(path)], capture_output=True, text=True).stdout
    assert "SETGLOBAL" not in listing


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["forever", "modern", "legacy"])
def test_brain_scenario(flavor):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--no-storm", "--flavor", flavor, "--class",
                        "HUNTER", "--scenario", str(ROOT / "tests/wow/brain_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=180, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
def test_safe_profile_storm():
    """What a new player gets (« mode sûr »): the pilot is on, the generic storm stays clean."""
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", "forever", "--class", "HUNTER",
                        "--profile", "sur", str(ADDON)], capture_output=True, text=True, timeout=180, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
