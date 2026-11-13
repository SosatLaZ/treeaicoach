"""5.1 BAGS: « sans te gourer sur ce dont j'ai besoin, faut tout prédire » — proof, not hope.

* the AUDIT (tests/wow/bags_audit_scenario.lua), once per class on the faithful WoW: Forever mock: the whole item
  universe TreeAI knows (every recipe reagent and recipe item of Data/Recipes, every consumable tier, ammunition, pet
  food, class reagents, + a QuestieDB of real schema asking for those items) × levels 10–60 × profession pairs × quests
  done or not. An oracle written from the raw tables asserts: an item with a live predicted use is never « sell » /
  « AH » / « to confirm »; an item with no possible use is never kept without a stated reason; and the audit proves
  it can fail (three predictors broken on purpose are caught);
* the REGRET REPLAY (tests/wow/bags_replay_scenario.lua): a hunter levels 30 → 45 with the real keepers, the clean-up
  clicked at every merchant visit (and the « HV » advice followed): nothing sold is needed later by a quest, a
  consumable need or his profession plan — and the same replay with a broken predictor does find regrets;
* static rules of Modules/BagsPredict.lua (no chat, no protected call, sliced work, the full Questie read only on a click).
The potion incident itself stays in tests/wow/test_bags.py."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
MODS = ADDON / "Modules"
LUA = shutil.which("lua5.1")
ALLOW = ["LibQuestieDB", "QuestDB", "NpcDB", "ObjectDB", "ItemDB", "IsQuestFlaggedCompleted", "Auctionator"]
CLASSES = ["WARRIOR", "PALADIN", "HUNTER", "ROGUE", "PRIEST", "SHAMAN", "MAGE", "WARLOCK", "DRUID"]


def code(path):
    return "\n".join(line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())


def run(scenario, cls="HUNTER", flavor="forever", env=None):
    cmd = [LUA, str(ROOT / "tests/wow/harness.lua"), "-q", "--no-storm", "--flavor", flavor, "--class", cls, "--level", "30"]
    for name in ALLOW:
        cmd += ["--allow", name]
    cmd += ["--scenario", str(ROOT / "tests/wow" / scenario), str(ADDON)]
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=ROOT, env=e)


def test_predictor_file_is_loaded_right_after_bags_and_is_not_a_new_module():
    toc = [line.strip() for line in (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines() if line.strip().endswith(".lua")]
    assert toc.index("Modules\\BagsPredict.lua") == toc.index("Modules\\Bags.lua") + 1
    src = (MODS / "BagsPredict.lua").read_text(encoding="utf-8")
    assert "local tonumber = function(v) return _G.tonumber(v) end" in src
    assert "RegisterModule" not in code(MODS / "BagsPredict.lua")


def test_predictors_never_act_and_slice_their_work():
    src = code(MODS / "BagsPredict.lua")
    for bad in ("SendChatMessage", "DEFAULT_CHAT_FRAME", "UseContainerItem", "DeleteCursorItem", "PickupContainerItem", "OnUpdate",
                "NewTicker", "RegisterEvent", "BuyMerchantItem", "SendMail"):
        assert bad not in src, bad
    # the recipe index and the full Questie read are sliced (one piece per image), the read never runs in combat
    assert 'return spread(step, "Bags:recettes")' in src and "local SCAN_CHUNK = 20" in src
    assert "if I.inCombat() then I.after(2, function() spread(step) end) return end" in src
    # the full read starts on the player's click only: one caller, the « verify » row of the merchant card
    bags = code(MODS / "Bags.lua")
    assert bags.count("Pd.QuestScan()") == 1 and "if e.verify then" in bags
    assert src.count("P.QuestScan()") == 0 or "function P.QuestScan()" in src
    # Questie's own item → quests link is trusted only once verified on real quests
    assert "elseif okN >= 3 then QF.cal" in src and 'return "unverified"' in src


def test_sell_is_never_said_before_the_veto_and_doubt_is_never_one_click():
    bags = code(MODS / "Bags.lua")
    veto = bags.index("pcall(Pd.Veto, id, it, have, Q, deps, forced)")
    assert veto < bags.index("local action, value, d = venue(id, it, picked, boundAny)")
    # a doubt = « keep » for the rest of the addon + a « confirm » entry listed apart; a big value = its own click
    assert 'v.keep, v.surplus, v.action, v.claimed = have, 0, "keep", true' in bags
    assert "if cf and cf.sel[key] then" in bags and "if v.action == \"sell\" and M.IsBig(v, e.value, grey) then" in bags
    assert "ok = v.action == \"sell\" and not (sale.greyOnly and not e.grey) and not M.IsBig(v, e.value, e.grey)" in bags
    # learning from a buy-back needs gold spent and the item back while the merchant is open
    assert "if not e.back and paid and cur > (e.left or 0) then" in bags
    # verdicts are dropped by cause, not by age (age only for the keepers of other modules)
    assert "if not d or d[cause] or d.all then" in bags
    assert "if not v or v.gen ~= C.gen or (v.ttl and (t - v.t > v.ttl or t < v.t)) then" in bags


@pytest.mark.skipif(not LUA, reason="lua5.1 absent")
@pytest.mark.parametrize("cls", CLASSES)
def test_audit_whole_universe(cls):
    r = run("bags_audit_scenario.lua", cls)
    out = r.stdout
    assert r.returncode == 0 and "RESULT ok" in out, out[-6000:] + r.stderr[-2000:]
    m = re.search(r"AUDIT univers : (\d+) objets \((\d+) composants, (\d+) recettes-objets, (\d+) consommables, (\d+) munitions, "
                  r"(\d+) nourritures de familier, (\d+) composants de classe\) ; (\d+) recettes ; (\d+) quêtes", out)
    assert m and int(m.group(1)) > 2500 and int(m.group(8)) > 2000, out[:800]
    m = re.search(rf"AUDIT {cls} : (\d+) avis .*; (\d+) personnages différents", out)
    assert m and int(m.group(1)) > 250000 and int(m.group(2)) >= 220, out[:1200]
    m = re.search(r"AUDIT A\. usage prévu vivant : (\d+) cas .* vendus / à confirmer : (\d+)", out)
    assert m and int(m.group(1)) > 50000 and int(m.group(2)) == 0, out[:1500]
    m = re.search(r"AUDIT B\. aucun usage possible : (\d+) cas .* gardés sans raison : (\d+)", out)
    assert m and int(m.group(1)) > 50000 and int(m.group(2)) == 0, out[:1500]
    assert re.search(r"AUDIT C\. avis mal formés : 0 ;", out)
    assert re.search(r"AUDIT D\. base relue en entier \(\d+ quêtes, \d+ images\) : \d+ usages vivants de plus contrôlés, 0 violation", out)
    assert re.search(r"AUDIT D\. sans Questie : 0 objet\(s\) non gris dans le clic unique", out)
    # the audit is able to fail: three predictors broken on purpose, three times caught
    m = re.search(r"AUDIT E\. .* : veto des quêtes coupé (\d+) ; prédicteur de métier muet (\d+) ; plan des consommables coupé (\d+)", out)
    assert m and all(int(g) > 0 for g in m.groups()), out[-1500:]


@pytest.mark.skipif(not LUA, reason="lua5.1 absent")
@pytest.mark.parametrize("flavor", ["forever", "modern"])
def test_regret_replay_30_to_45(flavor):
    r = run("bags_replay_scenario.lua", flavor=flavor)
    out = r.stdout
    assert r.returncode == 0 and "RESULT ok" in out, out[-6000:] + r.stderr[-2000:]
    m = re.search(r"REPLAY niveaux 30 → 45 : 16 visites chez le marchand, (\d+) clics, (\d+) piles vendues au marchand pour .* \+ (\d+) piles "
                  r"mises à l'HV \((\d+) objets différents\) ; \d+ dépôts / retraits à la banque ; (\d+) quêtes prises", out)
    assert m and int(m.group(2)) >= 100 and int(m.group(4)) >= 20 and int(m.group(5)) >= 16, out[:1500]
    assert "REPLAY REGRETS : 0" in out
    # what the player reads when it sells: the profession indicator, the stock sentence
    assert "« Pas besoin : sert en Couture (pas ton métier) → vends au marchand" in out
    assert re.search(r"« Garde : \d+ pour Travail du cuir \(\d+→\d+\) · \d+ en trop → ", out)
    assert "chat:" not in out and "violation:" not in out


@pytest.mark.skipif(not LUA, reason="lua5.1 absent")
def test_regret_replay_with_his_secondary_professions():
    # like the player: First Aid and Cooking level with him — silk (his First Aid plan) and mageweave (bandages from
    # 210, after the plan's cap) are never sold; nothing sold is regretted
    r = run("bags_replay_scenario.lua", env={"BAGS_PROFS": "player"})
    out = r.stdout
    assert r.returncode == 0 and "RESULT ok" in out, out[-6000:] + r.stderr[-2000:]
    assert "REPLAY REGRETS : 0" in out
    vendu = next(line for line in out.splitlines() if line.startswith("REPLAY vendu : "))
    assert "Étoffe de soie" not in vendu and "Étoffe de tisse-mage" not in vendu, vendu


@pytest.mark.skipif(not LUA, reason="lua5.1 absent")
@pytest.mark.parametrize("mutation", ["veto", "tiers"])
def test_regret_replay_catches_a_broken_predictor(mutation):
    r = run("bags_replay_scenario.lua", env={"BAGS_MUTATION": mutation})
    out = r.stdout
    assert r.returncode == 0 and "RESULT ok" in out, out[-4000:] + r.stderr[-2000:]
    m = re.search(rf"REPLAY MUTATION {mutation} : (\d+) regret", out)
    assert m and int(m.group(1)) > 0
    if mutation == "tiers":
        # the 5.0 incident, replayed: without the consumable predictor the healing potions go « because Tailoring »
        assert "regret : potion : Potion de soins" in out
