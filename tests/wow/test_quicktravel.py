"""QUICK3: module QuickTravel (best flight + « Prendre ce vol », bind-here at innkeepers, batched errands, group
« Tout le monde prêt ? » window + leader buttons, meeting point) — scenario on the real addon + static ToS checks."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
SRC = ADDON / "Modules" / "QuickTravel.lua"


def _code():
    return "\n".join(re.sub(r"--.*$", "", ln) for ln in SRC.read_text(encoding="utf-8").splitlines())


def test_toc_after_route_and_detect():
    toc = [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]
    q = toc.index("Modules\\QuickTravel.lua")
    for dep in ("Modules\\Route.lua", "Modules\\Detect.lua", "Modules\\Group.lua",
                "Modules\\Compass.lua", "Data\\Travel.lua", "Data\\Consumables.lua"):
        assert toc.index(dep) < q, dep


def test_protected_calls_only_from_clicks():
    code = _code()
    # never acts by itself: no chat, no auto confirm, no buying / repairing / mail taking
    for bad in ("SendChatMessage", "ConfirmBinder", "ConfirmReadyCheck", "BuyMerchantItem", "RepairAllItems",
                "TakeInboxMoney", "TakeInboxItem", "StaticPopup_OnClick", "AcceptGroup"):
        assert bad not in code, bad
    # each game action lives in one function, reached only from a button OnClick / tab control
    owners = {"TakeTaxiNode": {"Q.TakeFlight"}, "SelectOption": {"Q.Bind"}, "DoReadyCheck": {"Q.ReadyCheck"},
              "InviteUnit": {"Q.InviteFriends"}, "ConvertToRaid": {"Q.Convert"},
              "SetLootMethod": {"Q.CycleLoot", "Q.CanSetLoot"}}
    starts = [(m.start(), m.group(1)) for m in re.finditer(r"\nfunction ([\w.:]+)", code)]
    for fn, allowed in owners.items():
        pos = [m.start() for m in re.finditer(re.escape(fn), code)]
        assert pos, fn
        for p in pos:
            owner = max((s for s in starts if s[0] < p), default=(0, "?"))[1]
            assert owner in allowed, (fn, owner)
    for holder in ("Q.TakeFlight()", "Q.Bind(optn)"):
        assert holder in code
    for fn in ("TakeFlight", "Bind", "ReadyCheck", "InviteFriends", "Convert", "CycleLoot"):
        body = code[code.index("function Q." + fn + "("):]
        body = body[:body.index("\nend")]
        assert "inCombat()" in body, fn


def test_options_have_sections():
    for ln in SRC.read_text(encoding="utf-8").splitlines():
        if 'type = "toggle"' in ln or 'type = "range"' in ln:
            assert "section =" in ln, ln


def test_session_sim_cap():
    assert "QuickTravel = 80" in (ROOT / "tests/wow/session_sim.lua").read_text(encoding="utf-8")


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_syntax_no_globals():
    assert subprocess.run(["luac5.1", "-p", str(SRC)], capture_output=True).returncode == 0
    assert "SETGLOBAL" not in subprocess.run(["luac5.1", "-l", "-p", str(SRC)], capture_output=True, text=True).stdout


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor,cls", [("forever", "HUNTER"), ("modern", "HUNTER"), ("legacy", "MAGE"), ("forever", "WARRIOR")])
def test_quick_scenario(flavor, cls):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", cls,
                        "--scenario", str(ROOT / "tests/wow/quicktravel_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT ok" in r.stdout
