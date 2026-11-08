"""GEARTRADE 4.1: Modules/GroupGear.lua — « Équipement du groupe ». See what each group member wears to make good trades:
TreeAI friends share their gear and tradeable bag items over the group channel (GGE / GGB / GGQ, compact item ids with
the random suffix, no enchant, only on change, throttled); the others are inspected by the game (NotifyInspect out of
combat and out of boss fights, one at a time, ≥ 2 s apart, in range only, retried, ClearInspectPlayer after), cached with
their age. Trade suggestions for every tradeable item (unbound, or bound but still tradeable per the tooltip line):
who gains most (class, armor / weapon skill, level, Gear stat weights vs what they wear) → « ≈ +12 % pour Bravo (main
droite) ». « Groupe » page section, item tooltip line, loot roll line; the trade window opens on a click only."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
SRC = (ADDON / "Modules" / "GroupGear.lua").read_text(encoding="utf-8")
ALLOW = ["InitiateTrade", "C_TooltipInfo", "GetLootRollItemLink", "BIND_TRADE_TIME_REMAINING", "RollOnLoot"]


@pytest.mark.skipif(not shutil.which("lua5.1"), reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["forever", "modern", "legacy"])
def test_groupgear_scenario(flavor):
    cmd = ["lua5.1", str(ROOT / "tests/wow/harness.lua"), "-q", "--flavor", flavor, "--class", "HUNTER", "--level", "29"]
    for a in ALLOW:
        cmd += ["--allow", a]
    cmd += ["--scenario", str(ROOT / "tests/wow/groupgear_scenario.lua"), str(ADDON)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "RESULT ok" in r.stdout
    m = re.search(r"GROUPGEAR inspections=(\d+)", r.stdout)
    assert m and 3 <= int(m.group(1)) <= 8, r.stdout[-800:]     # never a burst of inspections


def test_registered_safe_gated_and_in_toc():
    toc = (ADDON / "TreeAI.toc").read_text(encoding="utf-8").split()
    assert "Modules\\GroupGear.lua" in toc
    for dep in ("Modules\\Group.lua", "Modules\\Gear.lua", "Modules\\ItemInfo.lua", "Modules\\Window.lua"):
        assert toc.index("Modules\\GroupGear.lua") > toc.index(dep), dep
    assert 'if type(T.SAFE_MODULES) == "table" then T.SAFE_MODULES.GroupGear = true end' in SRC
    assert 'gate = "group"' in SRC.split("T.MODULE_INFO.GroupGear = {", 1)[1].split("}", 1)[0]


def test_one_inspector_and_the_rules_of_inspection():
    group = (ADDON / "Modules" / "Group.lua").read_text(encoding="utf-8")
    assert "GG.OwnsInspect()" in group.split("function M:TryInspect(t)", 1)[1].split("\nend", 1)[0]
    body = SRC.split("function GG:TryInspect(t)", 1)[1].split("\nend", 1)[0]
    for need in ("inCombat()", "bossFight()", "GG.INSPECT_GAP", "CanInspect", "CheckInteractDistance", "S.pend"):
        assert need in body, need
    assert "GG.INSPECT_GAP = 2" in SRC
    assert '"ClearInspectPlayer"' in SRC


def test_trade_only_on_a_click_and_no_automation():
    body = SRC.split("function GG.OpenTrade(name)", 1)[1].split("\nend", 1)[0]
    assert body.index("T:InUserAction()") < body.index('g("InitiateTrade")')
    assert "inCombat()" in body and "CheckInteractDistance" in body
    assert SRC.count('"InitiateTrade"') == 2          # the check and the call, both in OpenTrade
    for bad in ("RollOnLoot", "ConfirmLootRoll", "PickupContainerItem", "PickupInventoryItem", "ClickTradeButton",
                "AcceptTrade", "UseContainerItem", "EquipItemByName", "SendChatMessage", ":Print(", "PlaySound",
                "UIErrorsFrame", "SetItemRef"):
        assert bad not in SRC, bad


def test_no_real_player_names():
    scen = (ROOT / "tests/wow/groupgear_scenario.lua").read_text(encoding="utf-8")
    assert "Faressit" not in SRC and "Faressit" not in scen
    code = "\n".join(l.split("--", 1)[0] for l in SRC.splitlines())     # code only (comments may show fake names)
    for nm in ("Bravo", "Charlie", "Delta"):
        assert nm not in code, nm


def test_wire_is_compact_and_validated():
    assert 'T:Send(kind, p, "PARTY")' in SRC and 'T:Send("GGQ", "1", "PARTY")' in SRC
    assert "GG.SEND_GAP = 10" in SRC and "GG.REQ_GAP = 30" in SRC
    assert '#payload > 600 or not allow(m.name)' in SRC
    assert 'if ver ~= "1" then return end' in SRC
