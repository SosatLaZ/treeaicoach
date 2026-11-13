"""5.1 AH — the buy-and-resell desk (wow_addon/TreeAI/Modules/AHMath.lua + AHDesk.lua).

« refais entièrement tout sur l'HV … que je puisse faire de l'achat-revente en masse … sans te gourer »:
  * ah5_scenario.lua: property tests over generated markets (profit exact to the copper, no « sûr » deal ever loses
    money, thin evidence = no deal, capital share, floor), the engine on a fake market (own auctions never bought, kept
    items never posted), the mass workflow (one press = at most one protected call, live re-check, changed listing
    skipped, nothing in combat / AH closed / chat), the ledger (purchases, sale and expired mails, vendor resale).
  * the AH session as a simulation (session_sim --crowd --phases hv): open, full scan of ~2 500 listings, 30 purchases,
    30 posts, a competitor undercuts, 10 cancels, close — no image over 4 ms of TreeAI work, no work once the AH is closed.
  * slice 2 (ah5b_scenario.lua): « réguler » a thin cheap market only with explicit thresholds and the risk in one line,
    the cancel / repost queue (one CancelAuction per press, deposit lost counted once), crafting spreads, sale-time
    patterns, the HV page as one dashboard (the superseded tiles are gone).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
ADDON = ROOT / "wow_addon" / "TreeAI"
LUA = shutil.which("lua5.1")
pytestmark = pytest.mark.skipif(not LUA, reason="lua5.1 not installed")


@pytest.mark.parametrize("flavor", ["forever", "modern", "legacy"])
def test_desk_scenario(flavor):
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--flavor", flavor, "--scenario", "tests/wow/ah5_scenario.lua",
                        str(ADDON)], cwd=ROOT, capture_output=True, text=True, timeout=240)
    assert r.returncode == 0, r.stdout[-6000:] + r.stderr
    assert "RESULT ok" in r.stdout and "violations=0" in r.stdout
    if flavor != "legacy":   # what the player sees, in plain French
        assert "VOIT (affaire): Acheter Cuir-test ×40 pour 2 po 44 pa → revendre à l'HV 9 pa 89 pc l'unité." in r.stdout
        assert "Gain probable : +" in r.stdout and "5 j de preuves" in r.stdout
        assert "VOIT (poster): Poster Cuir-test ×20 à 9 pa 89 pc l'unité (juste sous le moins cher). Plancher " in r.stdout
        assert "Objectif non atteignable sur ce marché pour l'instant" in r.stdout


@pytest.mark.parametrize("flavor", ["forever", "modern", "legacy"])
def test_desk_slice2_scenario(flavor):
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--flavor", flavor, "--scenario", "tests/wow/ah5b_scenario.lua",
                        str(ADDON)], cwd=ROOT, capture_output=True, text=True, timeout=240)
    assert r.returncode == 0, r.stdout[-6000:] + r.stderr
    assert "RESULT ok" in r.stdout and "violations=0" in r.stdout
    if flavor != "legacy":   # what the player sees, in plain French
        assert ("VOIT (réguler): Réguler Chiffon-test : racheter tout le mur bas (25 unités, 2 annonces, 2 vendeurs) pour 10 pa 75 pc"
                " → reposter à 97 pc l'unité. Gain probable : +") in r.stdout
        assert "Risque : un autre vendeur peut reposter sous ton prix ; au pire, revendu au marchand : perte 9 pa 50 pc." in r.stdout
        assert ("VOIT (annuler): Annuler Laine-test ×20 (4 pa 95 pc l'unité, dépassée à 4 pa 50 pc) : dépôt perdu 60 pc, "
                "reposter à 4 pa 49 pc → +") in r.stdout
        assert "tile:Acheter (" in r.stdout and "tile:Annuler (" in r.stdout and "Vendre tes sacs" not in r.stdout


@pytest.mark.parametrize("profile", ["sur", "tout"])
def test_ah_session_sim(profile):
    r = subprocess.run([LUA, str(HERE / "session_sim.lua"), "--crowd", "--class", "HUNTER", "--flavor", "forever",
                        "--profile", profile, "--phases", "hv", "--addon", str(ADDON)],
                       cwd=ROOT, capture_output=True, text=True, timeout=600)
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "RESULT ok" in out, out[-6000:]
    m = re.search(r"^phase hv\s+\d+ s\s+images\s+\d+\s+worst\s+(\d+) kinstr .*?over 4 ms: (\d+)", out, re.M)
    assert m, out[:3000]
    assert int(m.group(1)) <= 200 and int(m.group(2)) == 0, m.group(0)
    assert re.search(r"hv session: queue 30, buys 30, posts 30, cancels 10 \(queue \d+\)", out), out[:3000]
    assert re.search(r"AH modules' work in the \d+ s that follow: 0 kinstr", out), out[:3000]
    assert re.search(r"spikes recorded by the addon: 0\b", out), out[:3000]


def test_files_and_registration():
    toc = (ADDON / "TreeAI.toc").read_text(encoding="utf-8")
    assert (ROOT / "treeaicoach" / "wow" / "ahmath.py").exists()   # the Python twin (tests/wow/test_ahmath_parity.py)
    assert toc.index("Modules\\Auction.lua") < toc.index("Modules\\AHMath.lua") < toc.index("Modules\\AHDesk.lua")
    core = (ADDON / "Core.lua").read_text(encoding="utf-8")
    assert re.search(r"^  AHDesk = \{ lite = true, desc = ", core, re.M)
    for name in ("AHMath.lua", "AHDesk.lua"):
        src = (ADDON / "Modules" / name).read_text(encoding="utf-8")
        assert src.splitlines()[0].startswith("local tonumber = function(v) return _G.tonumber(v) end")
    desk = (ADDON / "Modules" / "AHDesk.lua").read_text(encoding="utf-8")
    # every protected AH call sits in a click handler path and nowhere else
    for call in ("PlaceBid", "StartCommoditiesPurchase", "ConfirmCommoditiesPurchase", "PostCommodity", "PostItem", "CancelAuction"):
        assert len(re.findall(r"try\(a\." + call + r"\b", desk)) == 1, call
    assert "SendChatMessage" not in desk and ":Print(" not in desk
