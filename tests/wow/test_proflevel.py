"""PROFLEVEL (4.0.2): levelling planner (treeaicoach/wow/professions.py = Prof.Engine.Session / Split in
Modules/Prof.lua), Lua <-> Python parity on fixtures, the « Session métier » scenario (Modules/ProfLevel.lua) on the
three API flavours, the recipes data (learn estimates, vendor unit prices, leather farming bands)."""
import gzip
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from treeaicoach.wow import professions as p

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "wow_addon" / "TreeAI"
LEVEL = ADDON / "Modules" / "ProfLevel.lua"
LUA = shutil.which("lua5.1")
needs_lua = pytest.mark.skipif(not LUA, reason="lua5.1 not installed")

A = [10, 0, 1, 1, 5, 10, 1, 0, "A", [900, 1], 1]
B = [11, 0, 1, 8, 20, 40, 1, 0, "B", [901, 2], 0]
C = [12, 0, 1, 15, 30, 40, 1, 777, "C", [902, 1], 0]


# ------------------------------------------------------------------ engine (pure)
def test_usable_and_chance():
    assert p.usable(A, 1, None) == (True, None) and p.usable(A, 1, set()) == (True, None)
    assert p.usable(B, 9, set()) == (True, "trainer") and p.usable(B, 7, set())[0] is False
    assert p.usable(B, 7, set(), {11: 5})[0] is True                   # requirement read at the trainer
    assert p.usable(C, 30, set())[0] is False and p.usable(C, 30, {12})[0] is True
    assert p.usable(C, 30, set(), patterns=True) == (True, "pattern")
    assert p.chance2(A, 3) == 1.0 and p.chance2(A, 7) == pytest.approx(0.6) and p.chance2(A, 10) == 0.0
    assert p.chance2(A, 7, overtime=5) == pytest.approx(0.8)
    assert [p.color2(A, s) for s in (3, 5, 8, 10)] == ["orange", "yellow", "green", "grey"]


def test_session_full_exact_and_ranks():
    r = p.session_plan([A, B, C], 1, 12, stock={900: 3}, vendor={901: 50}, price={900: 10}, known={10})
    assert r["reached"] == 12 and r["stop"] == "target"
    assert r["use"][900] == 3 and r["buy"][900] > 0 and r["buy"][901] == 4
    assert r["learn"][0]["spell"] == 11 and r["learn"][0]["kind"] == "trainer"
    assert sum(x["crafts"] for x in r["steps"]) == r["crafts"]
    ex = p.session_plan([A, B, C], 1, 300, mode="exact", stock={900: 4}, vendor={901: 50})
    assert ex["stop"] == "mats" and ex["use"][900] == 4 and 900 not in ex["buy"]
    d = [20, 0, 1, 1, 160, 200, 1, 0, "D", [903, 1], 1]
    low = p.session_plan([d], 148, 155, cap=150, level=15, vendor={903: 5})
    assert low["reached"] == 150 and low["stop"] == "level" and low["next_rank"][0] == "Expert"
    ok = p.session_plan([d], 148, 155, cap=150, level=28, vendor={903: 5})
    assert ok["reached"] == 155 and ok["train"][0]["rank"] == "Expert" and ok["train"][0]["at"] == 150
    assert [x["kind"] for x in ok["seq"]] == ["craft", "train", "craft"]


def test_split_reserve_bank_mail_margin_lot():
    res = {"use": {900: 10}, "buy": {901: 7}, "unknown": {}, "var": {901: True}}
    sp = p.split(res, {"bags": {900: 4}, "bank": {900: 5}, "mail": {900: 5}}, {900: 2}, {901: (4, 5)}, {})
    assert sp["bank"] == {900: 5} and sp["mail"] == {900: 3}
    assert sp["vendor"][0]["buy"] == 10 and sp["vendor"][0]["margin"] == 3 and sp["vendor"][0]["cost"] == 40
    res2 = {"use": {}, "buy": {950: 6, 951: 2}, "unknown": {951: 2}, "var": {}}
    sp2 = p.split(res2, {}, {}, {}, {950: 30})
    assert sp2["ah"] == [{"id": 950, "n": 6, "unit": 30, "cost": 180}] and sp2["farm"] == [{"id": 951, "n": 2}]


def test_real_leatherworking_143_to_165():
    rows = [r for r in p.recipes(165) if r[p.GREY] > 143]
    known = {3780, 7135, 3818}
    mats = p.load()["mats"]
    vendor = {int(k): v[1] for k, v in mats.items() if v[2] == 1}
    r = p.session_plan(rows, 143, 165, cap=150, level=28, stock={4234: 30, 2319: 18}, known=known,
                       vendor=vendor, price={4234: 80, 2319: 35})
    assert r["reached"] == 165 and r["stop"] == "target"
    assert r["train"] and r["train"][0]["rank"] == "Expert" and r["train"][0]["at"] == 150
    assert any(x["spell"] == 3780 for x in r["steps"])               # Heavy Armor Kit, known
    assert not any(x["learn"] == "pattern" for x in r["steps"])       # no pattern you do not have
    assert r["use"][4234] == 30                                       # your heavy leather first
    assert r["buy"].get(2321, 0) > 0 and 2321 in vendor               # fine thread from the vendor
    # never a recipe you cannot learn yet: every craft step starts at or above its learn skill
    by = {x[p.SPELL]: x for x in rows}
    for x in r["steps"]:
        row = by[x["spell"]]
        assert x["spell"] in known or row[p.MIN] <= x["from"], (x, row[p.MIN])


def test_former_contract_still_answers():
    rows = [[10, 0, 1, 1, 5, 10, 1, 0, "A", [900, 1]], [11, 0, 1, 1, 20, 40, 1, 0, "B", [901, 2]]]
    r = p.plan(0, 1, 12, lambda i: 10 if i == 900 else 50, stock={900: 3}, rows=rows)
    assert r["reached"] == 12 and r["steps"][0]["name"] == "A" and r["steps"][-1]["name"] == "B"
    assert r["used"] == {900: 3} and r["shopping"][901] == 4
    full = p.plan(129, 1, 300, lambda i: 100)
    assert full["reached"] >= 75 and full["cost"] > 0


# ------------------------------------------------------------------ data
def test_recipes_data_v402():
    d = p.load()
    rec = d["recipes"]
    lin = {r[p.NAME]: r for r in rec["129"]}["Bandage en lin"]
    assert lin[p.MIN] == 1 and lin[p.FLAGS] & p.FLAG_AUTO                # learned with First Aid
    heavy = {r[p.NAME]: r for r in rec["129"]}["Bandage épais en lin"]
    assert heavy[p.MIN] == heavy[p.YELLOW] == 50 and heavy[p.FLAGS] & p.FLAG_EST   # trainer: estimate = yellow
    pattern = {r[p.SPELL]: r for r in rec["165"]}[3765]                 # Gants noirs en cuir: pattern, real skill
    assert pattern[p.RECIPE] and pattern[p.MIN] == 95 and not pattern[p.FLAGS] & p.FLAG_EST
    assert all(len(r) == 11 for rows in rec.values() for r in rows)
    mats = d["mats"]
    assert mats["3371"][:3] == ["Fiole vide", 4, 1] and mats["3371"][4] == 5     # 5 vials for 20 c = 4 c each
    assert mats["2678"][1] == 2 and mats["2320"][1] == 10 and mats["2320"][4] == 1
    farm = d["farm"]
    assert farm["2319"][:3] == [20, 30, 100] and "Mille pointes" in farm["4234"][3]
    size = (ADDON / "Data" / "Recipes.lua").stat().st_size
    assert size <= 300 * 1024 and "farm = {" in (ADDON / "Data" / "Recipes.lua").read_text(encoding="utf-8")


def test_farm_zone_names_exist():
    from treeaicoach.wow import gamedata as g
    import unicodedata

    def fold(s):
        s = re.sub(r"\s*\(.*\)\s*$", "", s).replace("’", "'")
        return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    zones = {fold(v[0]) for v in g._load("zones").get("zones", {}).values()}
    if not zones:
        pytest.skip("zones data absent")
    for _, _, _, names in p.load()["farm"].values():
        for n in names:
            assert fold(n) in zones, n


# ------------------------------------------------------------------ Lua <-> Python parity
def _lua(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, (set, frozenset)):
        return "{" + ",".join(f"[{_lua(k)}]=true" for k in sorted(v)) + "}"
    if isinstance(v, dict):
        return "{" + ",".join(f"[{_lua(k)}]={_lua(x)}" for k, x in v.items()) + "}"
    if isinstance(v, (list, tuple)):
        return "{" + ",".join(_lua(x) for x in v) + "}"
    if v is None:
        return "nil"
    raise TypeError(type(v))


def _canon(res, sp):
    def kv(t):
        return ",".join(f"{k}={t[k]}" for k in sorted(t or {}))
    parts = [str(res["reached"]), str(res["stop"]), str(res["crafts"]), "%d" % int(res["cost"] + 0.5)]
    st = []
    for x in res["seq"]:
        if x["kind"] == "train":
            st.append(f"T{x['at']}:{x['rank']}")
        else:
            st.append(f"{x['spell']}:{x['from']}:{x['to']}:{x['crafts']}:{x['color']}:{x['color_to']}:{x['learn'] or '-'}")
    parts.append(";".join(st))
    parts.append(";".join(f"{l['spell']}:{l['at']}:{l['kind']}" for l in res["learn"]))
    parts += [kv(res["use"]), kv(res["buy"]), kv(res["unknown"]), kv(sp["bank"]), kv(sp["mail"])]
    parts.append(";".join(f"{e['id']}:{e['n']}:{e['buy']}:{e['margin']}:{e['unit']}" for e in sp["vendor"]))
    parts.append(";".join(f"{e['id']}:{e['n']}:{e['unit']}" for e in sp["ah"]))
    parts.append(";".join(f"{e['id']}:{e['n']}" for e in sp["farm"]))
    return "|".join(parts)


def _cases():
    mats = p.load()["mats"]
    vendor = {int(k): v[1] for k, v in mats.items() if v[2] == 1}
    vinfo = {int(k): (v[1], v[4]) for k, v in mats.items() if v[2] == 1}
    lw = [r for r in p.recipes(165) if r[p.GREY] > 143]
    fa = [r for r in p.recipes(129) if r[p.GREY] > 42]
    ck = [r for r in p.recipes(185) if r[p.GREY] > 92]
    out = [
        dict(name="synthetic_full", rows=[A, B, C], skill=1, target=12, stock={900: 3}, vendor={901: 50},
             price={900: 10}, known={10}),
        dict(name="synthetic_exact", rows=[A, B, C], skill=1, target=300, mode="exact", stock={900: 4},
             vendor={901: 50}),
        dict(name="synthetic_overtime_patterns", rows=[A, B, C], skill=1, target=38, stock={902: 2},
             vendor={901: 50, 902: 7}, price={900: 9}, known={10}, patterns=True, overtime=3),
        dict(name="rank_level_stop", rows=[[20, 0, 1, 1, 160, 200, 1, 0, "D", [903, 1], 1]], skill=148,
             target=155, cap=150, level=15, vendor={903: 5}),
        dict(name="lw_143_165_full", rows=lw, skill=143, target=165, cap=150, level=28,
             stock={4234: 30, 2319: 18}, known={3780, 7135, 3818}, vendor=vendor, price={4234: 80, 2319: 35},
             value={4234: 80, 2319: 35}, have={"bags": {4234: 4, 2319: 6}, "bank": {4234: 26, 2319: 12}},
             reserve={}, vinfo=vinfo),
        dict(name="lw_143_exact", rows=lw, skill=143, target=300, cap=150, level=28, mode="exact",
             stock={4234: 30, 2319: 18}, known={3780, 7135, 3818}, vendor=vendor, value={4234: 80, 2319: 35},
             have={"bags": {4234: 4, 2319: 6}, "bank": {4234: 26, 2319: 12}}, vinfo=vinfo),
        dict(name="lw_learn_at_read", rows=lw, skill=143, target=160, cap=150, level=28, stock={4234: 10},
             known={3780}, learn_at={3768: 125, 3770: 130}, vendor=vendor, price={4234: 80}, vinfo=vinfo),
        dict(name="first_aid_42", rows=fa, skill=42, target=75, cap=75, level=28, stock={2589: 30, 2592: 6},
             known={3275, 3276}, vendor=vendor, have={"bags": {2589: 20, 2592: 6}, "mail": {2589: 10}},
             reserve={2589: 5}, vinfo=vinfo),
        dict(name="cooking_92_unknown_list", rows=ck, skill=92, target=150, cap=150, level=28, vendor=vendor,
             vinfo=vinfo),
    ]
    return out


@needs_lua
def test_lua_python_parity(tmp_path):
    cases = _cases()
    lua_cases = []
    py = {}
    for c in cases:
        kw = dict(rows=c["rows"], skill=c["skill"], target=c["target"], cap=c.get("cap", 300), level=c.get("level", 60),
                  mode=c.get("mode", "full"), stock=c.get("stock"), known=c.get("known"), learn_at=c.get("learn_at"),
                  patterns=c.get("patterns", False), vendor=c.get("vendor"), price=c.get("price"), value=c.get("value"),
                  overtime=c.get("overtime", 0))
        res = p.session_plan(**kw)
        sp = p.split(res, c.get("have"), c.get("reserve"), c.get("vinfo"), c.get("price"))
        py[c["name"]] = _canon(res, sp)
        inp = {"rows": c["rows"], "skill": c["skill"], "target": c["target"], "cap": kw["cap"], "level": kw["level"],
               "mode": kw["mode"], "stock": c.get("stock") or {}, "vendor": c.get("vendor") or {},
               "price": c.get("price") or {}, "value": c.get("value") or {}, "overtime": kw["overtime"],
               "patterns": kw["patterns"]}
        if c.get("known") is not None:
            inp["known"] = set(c["known"])
        if c.get("learn_at"):
            inp["learnAt"] = c["learn_at"]
        vinfo = {k: list(v) for k, v in (c.get("vinfo") or {}).items()}
        lua_cases.append("{name=%s, inp=%s, have=%s, reserve=%s, vinfo=%s, price=%s}" % (
            _lua(c["name"]), _lua(inp), _lua(c.get("have") or {}), _lua(c.get("reserve") or {}), _lua(vinfo),
            _lua(c.get("price") or {})))
    fin, fout = tmp_path / "cases.lua", tmp_path / "out.txt"
    fin.write_text("return {\n" + ",\n".join(lua_cases) + "\n}\n", encoding="utf-8")
    env = dict(os.environ, PROFLEVEL_PARITY_IN=str(fin), PROFLEVEL_PARITY_OUT=str(fout))
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--no-storm", "--flavor", "modern", "--class", "HUNTER",
                        "--scenario", "tests/wow/proflevel_parity_scenario.lua", str(ADDON)],
                       capture_output=True, text=True, timeout=180, cwd=ROOT, env=env)
    assert "RESULT ok" in r.stdout, r.stdout[-3000:] + r.stderr[-2000:]
    lua = dict(line.split("\t", 1) for line in fout.read_text(encoding="utf-8").splitlines())
    assert set(lua) == set(py)
    for name in py:
        assert lua[name] == py[name], f"{name}\nLua: {lua[name][:600]}\nPy:  {py[name][:600]}"


# ------------------------------------------------------------------ the session in the harness
@needs_lua
@pytest.mark.parametrize("flavor", ["modern", "legacy", "forever"])
def test_proflevel_scenario(flavor):
    r = subprocess.run([LUA, "tests/wow/harness.lua", "-q", "--flavor", flavor, "--class", "HUNTER",
                        "--scenario", "tests/wow/proflevel_scenario.lua", str(ADDON)],
                       capture_output=True, text=True, timeout=180, cwd=ROOT)
    assert r.returncode == 0 and "RESULT ok" in r.stdout, r.stdout[-3000:] + r.stderr[-2000:]
    assert "violations=0" in r.stdout


# ------------------------------------------------------------------ static rules
def test_protected_calls_only_in_click_actions():
    src = LEVEL.read_text(encoding="utf-8")
    body = re.sub(r"--[^\n]*", "", src)
    # each game action lives in exactly one function, reached from a click (card button, step, page)
    for api, fn in (("BuyMerchantItem", "PL.BuyNext"), ("CraftRecipe", "PL.CraftNow"), ("DoTradeSkill", "PL.CraftNow"),
                    ("UseContainerItem", "PL.WithdrawNext"), ("StartCommoditiesPurchase", "PL.AHNext"),
                    ("ConfirmCommoditiesPurchase", "PL.AHNext"), ("SendSearchQuery", "PL.AHNext")):
        hits = [m.start() for m in re.finditer(re.escape(api), body)]
        assert hits, api
        for h in hits:
            owner = body.rfind("function ", 0, h)
            assert body[owner:owner + 40].startswith("function " + fn), (api, body[owner:owner + 40])
    for bad in ("CastSpellByName", "TakeInboxItem", "BuyTrainerService", "SendChatMessage", "OpenTradeSkill",
                "PickupContainerItem", "C_Timer"):
        assert bad not in body, bad
    assert "inCombat()" in body and "T:UserAction" in body
    toc = (ADDON / "TreeAI.toc").read_text(encoding="utf-8").splitlines()
    assert toc.index("Modules\\ProfLevel.lua") > toc.index("Modules\\Window.lua") > toc.index("Modules\\Prof.lua")


@pytest.mark.skipif(not shutil.which("luac5.1"), reason="luac5.1 absent")
def test_syntax_no_globals():
    for f in (LEVEL, ADDON / "Modules" / "Prof.lua"):
        assert subprocess.run(["luac5.1", "-p", str(f)], capture_output=True).returncode == 0
        listing = subprocess.run(["luac5.1", "-l", "-p", str(f)], capture_output=True, text=True).stdout
        assert "SETGLOBAL" not in listing, f
