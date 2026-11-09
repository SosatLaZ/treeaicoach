"""CITYRUN (4.1): « Courses en ville » (Modules/CityRun.lua) — shopping list from every source, NPC memory learned on
visit, money cap, optimal route, focus contract, stops checked off by the game windows, one game action per click,
profession session navigation, honest options, « en trop » verdicts, « Services » map pins (with / without Questie)."""
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
MODULE = ADDON / "Modules" / "CityRun.lua"
LUA = shutil.which("lua5.1")
needs_lua = pytest.mark.skipif(not LUA, reason="lua5.1 not installed")


@needs_lua
@pytest.mark.parametrize("flavor", ["modern", "legacy", "forever"])
def test_cityrun_scenario(flavor):
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--flavor", flavor, "--class", "HUNTER",
                        "--scenario", "tests/wow/cityrun_scenario.lua", str(ADDON)],
                       capture_output=True, text=True, timeout=240, cwd=ROOT)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-3000:] + r.stderr[-2000:]
    assert "violations=0" in r.stdout and "leaks=0" in r.stdout


def test_protected_calls_only_in_click_actions():
    body = re.sub(r"--[^\n]*", "", MODULE.read_text(encoding="utf-8"))
    for api, fns in (("BuyMerchantItem", ("CR.BuyNext",)), ("RepairAllItems", ("CR.RepairNow",)),
                     ("UseContainerItem", ("CR.SellNext", "CR.WithdrawNext", "CR.BankSellNext"))):   # 4.2: bank « Sortir à vendre »
        hits = [m.start() for m in re.finditer(r"\b" + re.escape(api) + r"\b", body)]
        assert hits, api
        for h in hits:
            owner = body.rfind("function ", 0, h)
            assert any(body[owner:owner + 40].startswith("function " + fn) for fn in fns), (api, body[owner:owner + 40])
    for bad in ("TakeInboxItem", "BuyTrainerService", "SendChatMessage", "C_Timer", "PickupContainerItem", "DeleteCursorItem",
                "SplitContainerItem", "TakeTaxiNode", "CastSpell", ":Print(", "SetBinding", "PlaceAuctionBid"):
        assert bad not in body, bad
    assert "inCombat()" in body and "T:UserAction" in body
    # every action refuses combat first
    for fn in ("CR.BuyNext", "CR.SellNext", "CR.RepairNow", "CR.WithdrawNext", "CR.BankSellNext"):
        i = body.index("function " + fn)
        assert "if inCombat() then refuse(" in body[i:i + 200], fn


def test_toc_bindings_and_hooks():
    toc = (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()
    assert toc.index("Modules\\CityRun.lua") == toc.index("Modules\\ProfLevel.lua") + 1
    root = ET.parse(ADDON / "Bindings.xml").getroot()
    for name, arg in (("TREEAI_STEP_NEXT", "1"), ("TREEAI_STEP_PREV", "-1")):
        b = [x for x in root if x.get("name") == name]
        assert len(b) == 1 and b[0].get("default") is None and f"TreeAI.FocusStep({arg})" in b[0].text
    src = MODULE.read_text(encoding="utf-8")
    assert "BINDING_NAME_TREEAI_STEP_NEXT" in src and "T.FocusStep = function" in src
    # the guarded hunks in the shared modules
    pl = (ADDON / "Modules" / "ProfLevel.lua").read_text(encoding="utf-8")
    for needle in ("CR.ApplyPick", "CR.Borrow", "CR.OwnsCard", "CR.Assume", "CR.PageButtons", "CR.RenderProfOptions",
                   "CR.ExtraList", "PL.SkipFn"):
        assert needle in pl, needle
    ii = (ADDON / "Modules" / "ItemInfo.lua").read_text(encoding="utf-8")
    assert "CR.Verdict" in ii and "pcall(CR.Verdict" in ii
    hl = (ADDON / "Modules" / "Highlight.lua").read_text(encoding="utf-8")
    assert "CR.BagBadge" in hl and 'kind == "extra"' in hl
    # never a made-up coordinate table: no {x, y} literals of NPCs in the module
    assert not re.search(r"\{\s*name\s*=\s*\"[^\"]+\",\s*x\s*=\s*0\.\d+", src)


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_syntax_no_globals():
    assert subprocess.run(["luac5.1", "-p", str(MODULE)], capture_output=True).returncode == 0
    listing = subprocess.run(["luac5.1", "-l", "-p", str(MODULE)], capture_output=True, text=True).stdout
    assert "SETGLOBAL" not in listing
