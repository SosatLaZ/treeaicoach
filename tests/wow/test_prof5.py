"""PROF 5.0 — « métiers sans erreur »: the four real complaints as regressions, property checks of the planner over every
profession and skill 1..299 (Lua engine in tests/wow/prof5_scenario.lua, Python mirror here), the trainer window read
with both client signatures, « tout ce que tu peux apprendre d'utile », the passive hint, the BAGS contracts, the page,
the data audit (tools/wow_audit_recipes.py), static safety of Modules/ProfGuide.lua."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from treeaicoach.wow import professions as p

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
GUIDE = ADDON / "Modules" / "ProfGuide.lua"
LUA = shutil.which("lua5.1")
needs_lua = pytest.mark.skipif(not LUA, reason="lua5.1 not installed")


@needs_lua
@pytest.mark.parametrize("flavor", ["modern", "legacy", "forever"])
def test_prof5_scenario(flavor):
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--flavor", flavor, "--class", "HUNTER",
                        "--scenario", "tests/wow/prof5_scenario.lua", str(ADDON)],
                       capture_output=True, text=True, timeout=300, cwd=ROOT)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-3000:] + r.stderr[-2000:]
    assert "violations=0" in r.stdout and "leaks=0" in r.stdout


@needs_lua
def test_what_the_player_sees_dump():
    """« CE QUE LE JOUEUR VOIT » (PROF5_SEE=1): the dump exists, is French, and never shows the old silly lines."""
    env = dict(os.environ, PROF5_SEE="1")
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--flavor", "forever", "--class", "HUNTER",
                        "--scenario", "tests/wow/prof5_scenario.lua", str(ADDON)],
                       capture_output=True, text=True, timeout=300, cwd=ROOT, env=env)
    seen = r.stderr
    assert "Va d'abord chez ton maître" in seen and "Apprends Peau moyenne traitée chez ton maître" in seen
    assert "Tu passes devant tes maîtres" in seen and "Avec ton or : 92 à" in seen
    assert not re.search(r"\b\d+/\d+ (Cuir|Etoffe|Peau)", seen)          # « 28/271 Cuir moyen »
    assert not re.search(r"Apprendre [^\n]* à \d+", seen)                 # « Apprendre X à 67 »
    assert "objet " not in seen and "nil" not in seen.replace("| nil |", "")   # no raw id, no nil leaking in a line
    for english in ("Learn ", "Buy ", "Craft ", "Trainer", "Vendor"):
        assert english not in seen, english


# ------------------------------------------------------------------ Python mirror: property checks
def _vendor_price():
    d = p.load()
    mats = {int(k): v for k, v in d["mats"].items()}
    vendor = {k: v[1] for k, v in mats.items() if v[2] == 1}
    price = {k: 300 + (k % 7) * 100 for k in mats if k not in vendor and k % 5}
    return vendor, price


@pytest.mark.parametrize("line", [129, 185, 165, 197, 164, 171, 202, 333, 186])
def test_planner_properties_every_skill(line):
    """No step below the skill, no quantity above what the range needs, no recipe not learnable, costs that add up —
    for every skill 1..299, both modes, three « known » states, with and without stock."""
    vendor, price = _vendor_price()
    rows = p.recipes(line)
    plans = 0
    for skill in range(1, 300):
        for cap in (75, 150, 225, 300):
            if skill >= cap or skill < cap - 75:
                continue
            target = min(cap, skill + 25)
            rel = [r for r in rows if r[p.GREY] > skill and r[p.MIN] <= target]
            for known in (None, set(), {r[0] for r in rel if r[p.YELLOW] <= skill}):
                for mode in ("full", "exact"):
                    stock = {} if skill % 3 else {m: (skill * 7 + m) % 40 for r in rel for m in r[p.MATS][0::2]}
                    kw = dict(skill=skill, target=target, cap=cap, level=60, mode=mode, stock=stock, known=known,
                              vendor=vendor, price=price)
                    res = p.session_plan(rel, **kw)
                    bad = p.audit(res, **kw)
                    assert not bad, (line, skill, cap, mode, bad[:3])
                    plans += 1
                    for x in res["steps"]:
                        assert x["from"] >= skill and x["lo"] <= x["crafts"] <= x["hi"]
                        if x["min_c"] >= 1:
                            assert x["lo"] == x["hi"] == x["crafts"]       # orange = an exact count
                    # a deep-green craft only when nothing better was usable at that skill
                    for x in res["steps"]:
                        if x["min_c"] < p.MIN_CHANCE and mode == "full" and x["from"] == skill:
                            better = [r for r in rel if p.usable(r, skill, known)[0]
                                      and p.chance2(r, skill) >= p.MIN_CHANCE]
                            assert not better or p.chance2(x["row"], skill) >= p.MIN_CHANCE, (line, skill, x["name"])
    assert plans > 1000


def test_a_lower_target_is_a_prefix_of_the_full_plan():
    """PL.Afford relies on it: the engine decides craft by craft, so the plan to T is the start of the plan to the cap
    (same recipes, cumulative cost read on the full plan)."""
    vendor, price = _vendor_price()
    rows = p.recipes(165)
    full = p.session_plan(rows, 92, 150, cap=150, level=30, known=set(), vendor=vendor, price=price)
    low = p.session_plan(rows, 92, 120, cap=150, level=30, known=set(), vendor=vendor, price=price)
    assert low["reached"] == 120 and low["cost"] <= full["cost"]
    assert [x["spell"] for x in low["steps"]] == [x["spell"] for x in full["steps"]][:len(low["steps"])]
    for a, b in zip(low["steps"][:-1], full["steps"]):
        assert (a["crafts"], a["from"], a["to"]) == (b["crafts"], b["from"], b["to"])
    assert all(x["cost0"] <= x["cost1"] for x in full["steps"]) and full["steps"][-1]["cost1"] == pytest.approx(full["cost"])


def test_range_model():
    assert p.craft_range(7, 0.0, 7) == (7, 7)                      # orange: exact
    lo, hi = p.craft_range(10, 6.0, 5)
    assert 5 <= lo < 10 < hi <= 14                                  # ± 1.28 σ, never under the points gained
    assert p.craft_range(3, 50.0, 1)[0] >= 1


# ------------------------------------------------------------------ data
def test_data_requirements_are_flagged_honestly():
    d = p.load()
    for line, rows in d["recipes"].items():
        for r in rows:
            flags = r[p.FLAGS]
            if flags & p.FLAG_EST:
                assert r[p.MIN] == max(1, r[p.YELLOW]) and not r[p.RECIPE], (line, r[:9])
            # 5.0 audit: a table requirement above the yellow threshold was a leftover → estimated, never kept
            if not flags & p.FLAG_AUTO and not r[p.RECIPE] and r[p.YELLOW] > 0:
                assert r[p.MIN] <= r[p.YELLOW], (line, r[:9])
    by = {r[0]: r for r in d["recipes"]["164"]}
    assert by[2665][p.MIN] == 40 and by[2665][p.FLAGS] & p.FLAG_EST and by[2674][p.MIN] == 100
    # rank caps: the planner's rank table = 75 × tier (client: rank spells, effect 118)
    assert [r[3] for r in p.RANKS] == [75, 150, 225, 300]


def test_audit_tool_offline(tmp_path):
    cache = os.environ.get("TREEAI_WOWF_DB2")
    if not cache or not os.path.isfile(os.path.join(cache, "SkillLineAbility.csv")):
        pytest.skip("client tables not cached (TREEAI_WOWF_DB2)")
    r = subprocess.run(["python3", "tools/wow_audit_recipes.py", "--cache", cache], capture_output=True, text=True, cwd=ROOT)
    assert "fichier livré : **oui**" in r.stdout, r.stdout[:400] + r.stderr[-400:]


# ------------------------------------------------------------------ static safety of the new file
def test_guide_static_rules():
    src = GUIDE.read_text(encoding="utf-8")
    body = re.sub(r"--[^\n]*", "", src)
    # the only game action of the file: BuyTrainerService, through T:ShopCall, inside PL.LearnNext, combat refused first
    hits = [m.start() for m in re.finditer(r"BuyTrainerService", body)]
    assert hits
    for h in hits:
        owner = body.rfind("\nfunction ", 0, h)
        assert body[owner:owner + 30].startswith("\nfunction PL.LearnNext"), body[owner:owner + 40]
    i = body.index("function PL.LearnNext")
    assert "if inCombat() then refuse(" in body[i:i + 120] and "T:ShopCall(\"BuyTrainerService\"" in body
    assert not re.search(r"\bbuy\(", body)                       # never called directly
    for bad in ("SendChatMessage", ":Print(", "print(", "BuyMerchantItem", "UseContainerItem", "CraftRecipe", "C_Timer",
                "SetScript(\"OnUpdate\"", "SelectTrainerService", "SetTrainerServiceTypeFilter", "PickupContainerItem"):
        assert bad not in body, bad
    # guarded cross-module contracts
    assert 'type(B.RegisterKeeper) == "function"' in body and 'type(T.OnBags) == "function"' in body
    toc = (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()
    assert toc.index("Modules\\ProfGuide.lua") == toc.index("Modules\\CityRun.lua") + 1
    cr = (ADDON / "Modules" / "CityRun.lua").read_text(encoding="utf-8")
    assert 'type(B.Shopping) == "function"' in cr and 'type(B.Verdict) == "function"' in cr


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_guide_syntax_no_globals():
    assert subprocess.run(["luac5.1", "-p", str(GUIDE)], capture_output=True).returncode == 0
    listing = subprocess.run(["luac5.1", "-l", "-p", str(GUIDE)], capture_output=True, text=True).stdout
    assert "SETGLOBAL" not in listing
