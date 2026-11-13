"""5.0 BAGS: Modules/Bags.lua = the single brain for every item decision (T.Bags.Verdict + RegisterKeeper).

The potion incident as a regression (the best healing potions are never sold; a lower rank is surplus only when the
better ones cover the stock), every keeper, AH vs vendor, the merchant card (one click, re-checked batches, at most 12
non-grey, never in combat), the bank flow, the full-bag warning, overlays on the game's bag buttons, the tooltip line,
« Il te manque » / Shopping(), the page « Sacs », nothing computed while looting with the bags closed, nothing in the
chat, zero blocked action. Static rules + the harness scenario (tests/wow/bags_scenario.lua) on forever, modern, legacy."""
# (5.1 CLEAN) harness default = the player's profile (« sur ») ; this file tests modules that are off there: « tout »
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
MODS = ADDON / "Modules"
LUA = shutil.which("lua5.1")


def code(path):
    return "\n".join(line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())


def test_module_registered_early_and_safe():
    toc = (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()
    files = [line.strip() for line in toc if line.strip().endswith(".lua")]
    at = files.index("Modules\\Bags.lua")
    # before the modules that register keepers from their own file (Gear, Prof) and before the consumers
    for later in ("Modules\\Auction.lua", "Modules\\Prof.lua", "Modules\\Gear.lua", "Modules\\CityRun.lua", "Modules\\QoL.lua"):
        assert at < files.index(later), later
    src = code(MODS / "Bags.lua")
    assert "T.SAFE_MODULES.Bags = true" in src
    assert "Bags = { lite = true" in code(ADDON / "Core.lua")


def test_no_bag_event_work_and_no_onupdate():
    src = code(MODS / "Bags.lua")
    # nothing heavy on a bag event: no BAG_UPDATE handler (only the coalesced one), no per-frame script, no ticker
    assert '"BAG_UPDATE"' not in src
    assert "OnUpdate" not in src and "NewTicker" not in src
    assert 'type(T.OnBags) == "function"' in src and 'RegisterEvent("BAG_UPDATE_DELAYED"' in src
    # the big recompute is sliced, one chunk per frame (5.0 INTEG: at the head of the queue when a window is open)
    assert "spread(step, \"Bags:avis\")" in src and "T:Spread(M, fn" in src and "T:SpreadFirst(M, fn" in src
    assert "if inCombat() then M._afterCombat = true return end" in src


def test_never_destroys_never_chats_one_sale_path():
    src = code(MODS / "Bags.lua")
    for bad in ("DeleteCursorItem", "PickupContainerItem", "SendChatMessage", "DEFAULT_CHAT_FRAME", "ClearCursor"):
        assert bad not in src, bad
    # selling / depositing / repairing in a click goes through T:ShopCall; continuation batches are guarded
    assert 'T:ShopCall("UseContainerItem", fn, bag, slot)' in src and 'T:ShopCall("RepairAllItems", fn)' in src
    assert "if inCombat() or not merchantShown() then saleDone() return 0 end" in src
    # the game's sort only inside a click
    assert "if not (T.InUserAction and T:InUserAction()) then return false end" in src


def test_best_consumable_is_never_surplus():
    src = code(MODS / "Bags.lua")
    assert "elseif i == 1 then" in src and "p.best, p.keep[id] = id, huge" in src
    assert "elseif better >= tg.hold then" in src
    # a keeper in error claims everything; without an explicit « en trop » the item is kept
    assert "Q.all, Q.why, Q.keeper = true" in src and "if surplus <= 0 or not opinion then" in src
    # an unclaimed trade good is never vendored: AH only, and only with a known, clearly better price
    assert 'if market and (action ~= "ah" or d.unknown) then' in src


def test_other_modules_use_the_brain():
    inv = code(MODS / "Inventory.lua")
    assert "B.GreyItems" in inv and "function M.GreyItems()" in inv
    qol = code(MODS / "QoL.lua")
    assert "B.SellGreys()" in qol and "B.Warm(function()" in qol
    ah = code(MODS / "Auction.lua")
    assert "T.Bags" in ah and "B.Verdict" in ah and "function M.RenderNeed(Ly, W, m)" in ah
    # the profession quick sale (the incident path) never lists a stack a keeper claims; the tooltip says it once
    assert "BG.Verdict, id, bag, slot" in code(MODS / "CityRun.lua")
    assert "BG.HasTip" in code(MODS / "ItemInfo.lua")


def _run(flavor, cls="HUNTER"):
    return subprocess.run([LUA, str(ROOT / "tests/wow/harness.lua"), "-q", "--profile", "tout", "--no-storm", "--flavor", flavor, "--class", cls,
                           "--level", "30", "--scenario", str(ROOT / "tests/wow/bags_scenario.lua"), str(ADDON)],
                          capture_output=True, text=True, timeout=300, cwd=ROOT)


@pytest.mark.skipif(not LUA, reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["forever", "modern", "legacy"])
def test_bags_scenario(flavor):
    r = _run(flavor)
    out = r.stdout
    assert r.returncode == 0 and "RESULT ok" in out, out[-6000:] + r.stderr[-2000:]
    # the incident, in the player's words
    assert "BAGS incident : Potion de soins ×5 → Garde : ta meilleure potion de soins (tu en as 5)" in out
    assert "BAGS incident : Potion de soins ×3 → Garde : 5 potions de soins pour toi (tu en as 3)" in out
    assert re.search(r"BAGS incident : Potion de soins inférieure ×4 → Vends au marchand : moins bien que Potion de soins", out)
    assert "BAGS incident : Potion de soins inférieure ×4 → Garde : 5 potions de soins pour toi (tu n'en as que 3 de mieux)" in out
    assert "BAGS sacs pleins : 2 places : vends / jette Plume sans valeur ×2 (0 pc)" in out
    assert re.search(r"BAGS carte marchand : Vendre 9 objets · .* \+ réparer .*Annulable : onglet « Rachat »", out)
    assert "(groupe de 4) Plastron en plaques de test ×1 → Garde" in out
    assert "→ À utiliser : à apprendre maintenant (Travail du cuir" in out
    assert "BAGS butin sacs fermés : 0 avis calculés pour 4 objets ramassés" in out
    assert "BAGS stock : Il te manque : 2 potions de soins" in out
    assert "chat:" not in out and "violation:" not in out


@pytest.mark.skipif(not LUA, reason="lua5.1 absent")
def test_incident_cause_is_shown_on_forever():
    out = _run("forever").stdout
    # evidence first: the profession verdict that made the quick sale sell the potions
    assert "BAGS AVANT (cause) : Courses en ville sur Potion de soins : « Pas besoin : sert en Couture (pas ton métier) », en trop = 5" in out
