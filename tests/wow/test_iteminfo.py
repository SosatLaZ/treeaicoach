"""Modules/ItemInfo.lua (agent ITEMINFO, 4.0.2): TRUE lines under item tooltips — upgrade vs what you wear (Gear's
engine, ≈ % ranged damage for the hunter), group Need / Greed, value (vendor stack, AH only when the liquidity model
says it sells), quest item (journal rules), professions (known recipes, how many), hunter pet food, measured sources.
TOC / Core contracts, one owner for item tooltip lines (Gear / Highlight / Quests stand down), taint rules, syntax,
the harness scenario on every flavour (forever = secret values in combat), the session_sim cap."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
SRC = (ADDON / "Modules" / "ItemInfo.lua").read_text(encoding="utf-8")
CODE = re.sub(r"--[^\n]*", "", SRC)


def _toc():
    return [ln.strip() for ln in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()]


def test_toc_after_the_modules_it_reads():
    lines = _toc()
    me = lines.index("Modules\\ItemInfo.lua")
    for dep in ("Gear", "Auction", "Stats", "Highlight", "Dungeon", "Prof", "Hunter", "Quests", "Group", "Compass"):
        assert lines.index("Modules\\%s.lua" % dep) < me, dep
    assert me < lines.index("Modules\\Tour.lua")   # Tour stays last


def test_core_contracts_and_single_owner():
    core = (ADDON / "Core.lua").read_text(encoding="utf-8")
    assert re.search(r"SAFE_MODULES = TreeAI\.SAFE_MODULES or \{.*?\bItemInfo = true,", core, re.S), "mode sûr"
    assert "ItemInfo = { lite = true," in core
    assert '"ItemInfo"' in (ADDON / "Options.lua").read_text(encoding="utf-8")   # options family « Sacs, or, métiers »
    # one owner for item tooltip lines: the old writers stand down while ItemInfo is on (guarded one-liners)
    assert (ADDON / "Modules" / "Gear.lua").read_text(encoding="utf-8").count("T.ItemInfo.Owns()") == 2
    hl = (ADDON / "Modules" / "Highlight.lua").read_text(encoding="utf-8")
    assert "function M.ItemInfoOwns()" in hl and hl.count("not M.ItemInfoOwns()") == 2
    qs = (ADDON / "Modules" / "Quests.lua").read_text(encoding="utf-8")
    assert qs.count("not itemInfoOwns()") == 2
    for k in ("upgrade", "quest", "group", "pet", "prof", "value", "source", "shift"):
        assert 'key = "%s", type = "toggle"' % k in SRC, k   # one toggle per section
    assert 'section = "Infobulles d\'objets"' in SRC


def test_taint_rules():
    # hooks: TooltipDataProcessor post-call, HookScript fallback; never a replaced method, never a forced refresh
    # (the item comparison is protected on Forever: C_TooltipComparison.CompareItem)
    assert "AddTooltipPostCall" in CODE and '"OnTooltipSetItem"' in CODE and "HookScript" in CODE
    assert not re.search(r"\b(GameTooltip|ItemRefTooltip|tt|tip)\s*\.\s*[A-Z]\w*\s*=[^=]", CODE), "tooltip method replaced"
    assert ":SetScript(" not in CODE and ".SetScript" not in CODE
    for bad in ("RefreshData", "SetHyperlink", "SetBagItem", "SetInventoryItem", "CompareItem", "ShowCompareItem",
                "UseContainerItem", "PickupContainerItem", "SendChatMessage", "SetParent", "C_Timer", "CreateFrame"):
        assert bad not in CODE, bad
    assert '"GameTooltip"' in CODE and '"ItemRefTooltip"' in CODE   # only these two (no comparison tooltips)


def test_only_public_apis_of_other_modules():
    # read-only: no write into another module's table or data
    assert not re.search(r"\b(Gm|E|A|Au|P|Hm|H|Q|Dg|Stt|Gear|Auction|Prof|Hunter|Highlight|Quests|Dungeon|Stats)\s*\.\s*\w+\s*=[^=]",
                         CODE), "writes into another module"
    assert "T.Data.Recipes.r[" not in CODE.replace(" ", "")


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_syntax_no_globals():
    path = ADDON / "Modules" / "ItemInfo.lua"
    assert subprocess.run(["luac5.1", "-p", str(path)], capture_output=True).returncode == 0
    listing = subprocess.run(["luac5.1", "-l", "-p", str(path)], capture_output=True, text=True).stdout
    assert "SETGLOBAL" not in listing


def test_session_sim_caps():
    sim = (ROOT / "tests" / "wow" / "session_sim.lua").read_text(encoding="utf-8")
    assert "ItemInfo = 10" in sim and "ItemInfo = 8" in sim and "ItemInfo = 1 }" in sim
    assert "hoverBags(main" in sim


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["modern", "legacy", "forever"])
def test_iteminfo_scenario(flavor):
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", "HUNTER",
                        "--scenario", str(ROOT / "tests/wow/iteminfo_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=180, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
    assert "RESULT ok" in r.stdout


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
def test_iteminfo_scenario_safe_mode():
    """What every player runs (mode sûr, no storm seeding): ItemInfo on, Gear off — the lines still come."""
    r = subprocess.run(["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--no-storm", "--profile", "sur",
                        "--flavor", "forever", "--class", "HUNTER",
                        "--scenario", str(ROOT / "tests/wow/iteminfo_scenario.lua"), str(ADDON)],
                       capture_output=True, text=True, timeout=180, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
    assert "RESULT ok" in r.stdout
