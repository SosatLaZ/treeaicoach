"""WoW page 4.0 (« compagnon ») : the « Maintenant » card, every tab's content, the tab ↔ content mapping, the
data-truth rules (freshness, quests of the log only, no unmeasured / absurd number) and the FREEZE BUDGET: with a
big fake save (thousands of AH items, a long ledger, hundreds of errors) and a ~9 MB Auctionator database, the UI
thread spends < 30 ms per refresh and per tab switch. Invented names only."""

from __future__ import annotations

import os
import random
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from treeaicoach import ui_page_wow as pw
from treeaicoach.wow import live

TESTS = Path(__file__).resolve().parent
NOW = 1762500000.0
BUDGET_MS = 30.0


# ------------------------------------------------------------------------------------------ a WoW-like writer
def _lua(v: object, ind: int = 1) -> str:
    """Python → Lua table constructor laid out like WoW's SavedVariables (tabs, one key per line)."""
    pad, end = "\t" * ind, "\t" * (ind - 1)
    if isinstance(v, dict):
        if not v:
            return "{\n" + end + "}"
        items = [f'{pad}[{k if isinstance(k, int) else chr(34) + str(k) + chr(34)}] = {_lua(x, ind + 1)},'
                 for k, x in v.items()]
        return "{\n" + "\n".join(items) + "\n" + end + "}"
    if isinstance(v, list):
        if not v:
            return "{\n" + end + "}"
        return "{\n" + "\n".join(f"{pad}{_lua(x, ind + 1)}, -- [{i}]" for i, x in enumerate(v, 1)) + "\n" + end + "}"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _summary(n_quests: int = 20) -> dict:
    """A summary in the REAL addon shape (4.0, from a session_sim save): ``quests.list[].obj`` carries the progress
    words; ``tracker.route[].done/total`` count OBJECTIVES; abandon entries carry the addon's ``verdict``; the group
    blocks carry ``updated`` (written with the save while in a group)."""
    log_list = [{"id": 1000 + i, "title": f"Quête {i}", "level": 26 + i % 3, "color": "jaune", "done": 0, "total": 1,
                 "obj": [f"Objectif Test {i % 4}/8"], "zone": "Zone Test", "complete": i == 0} for i in range(n_quests)]
    return {
        "class": "HUNTER", "className": "Chasseur", "level": 28, "xp": 20000, "xpMax": 40000, "rested": 0,
        "zone": "Zone Test", "subzone": "Camp Factice", "money": 123456, "bagsFree": 6, "bagsTotal": 64,
        "inGroup": True, "groupSize": 4, "updated": NOW - 120,
        "quests": {"count": n_quests, "complete": 1, "list": log_list, "updated": NOW - 120},
        "tracker": {"route": [{"id": 1000 + i, "title": f"Quête {i}", "ready": i == 0, "done": 0, "total": 1,
                               "dist": 100 + 10 * i, "zone": "Zone Test", "sec": 300, "tag": "Rapide",
                               "why": "Vite faite (≈ 5 min)."} for i in range(n_quests)]
                    + [{"id": 9999, "title": "Quête Rendue Hier", "ready": True}]},
        "todo": {"now": {"kind": "quest", "text": "Rends « Quête 0 »", "why": "Tout est fait : il reste à la rendre.",
                         "eta": 75}, "updated": NOW - 120},
        "compass": {"target": {"kind": "quest", "questID": 1000, "title": "Quête 0", "label": "Rendre « Quête 0 »",
                               "dist": 131, "distText": "120 m", "eta": "40 s", "zone": "Zone Test"}},
        "plan": {"v": 1, "src": "file", "made": NOW - 3600, "route": "xph", "title": "XP/h max", "step": 2,
                 "steps": 40, "label": "Rendre « Quête 0 »", "kind": "turn", "quest": 1000, "minutes": 90, "left": 75,
                 "verdict": "ok", "updated": NOW - 120},
        "abandon": {"list": [{"title": "Quête 7", "verdict": "abandon", "why": "grise : presque plus d'XP"},
                             {"title": "Quête 3", "verdict": "later", "why": "fais-la si tu passes à côté"},
                             {"title": "Quête Fantôme", "verdict": "abandon", "why": "pas dans le journal"}],
                    "count": 3, "updated": NOW - 120},
        "group": {"v": 2, "updated": NOW - 120, "size": 4, "members": [
            {"name": "Testeur", "class": "Chasseur", "level": 28, "zone": "Zone Test", "me": True, "ready": True,
             "treeai": True},
            {"name": "Alpha", "class": "Prêtre", "level": 27, "zone": "Zone Test", "dead": True, "ready": True,
             "treeai": True},
            {"name": "Bravo", "class": "Guerrier", "level": 28, "zone": "Autre Zone", "afk": True, "ready": False,
             "treeai": True},
            {"name": "Charlie", "class": "Mage", "level": 26, "zone": "Zone Test", "bags": 0, "ready": True,
             "treeai": True}]},
        "missions": {"v": 1, "updated": NOW - 120, "size": 4, "noAddon": 0,
                     "board": [{"id": 1000 + i, "title": f"Quête {i}", "holders": 4, "done": i % 4,
                                "pct": 25 * (i % 4)} for i in range(10)],
                     "next": "Ensemble : « Quête 0 » \u2014 toi 6/8, Alpha 2/8"},
        "auction": {"updated": NOW - 120, "neutral": False, "scanAge": 600,
                    "deals": [{"name": f"Objet {i}", "id": 2000 + i, "kind": "flip", "count": 20, "buy": 2000,
                               "sell": 4000, "profit": 1700, "roi": 0.85, "conf": 0.8, "deposit": 150, "sold": 12.5,
                               "stop": 85} for i in range(5)],
                    "owned": [{"name": f"Vente {i}", "id": 3000 + i, "count": 5, "unit": 300, "lowest": 250,
                               "undercut": i % 2 == 0, "advice": "annuler, reposter 2 pa 49 pc"} for i in range(10)],
                    "realized": {"today": 1200, "week": 5000, "total": 9000,
                                 "top": [{"item": "Objet 1", "profit": 4000}]}},
        "dungeon": {"key": "mortemines", "name": "Les Mortemines", "why": "Tu es dedans.", "boss": "Rhahk'Zor",
                    "next": "Sneed", "updated": NOW - 120,
                    "run": {"time": 900, "pulls": 12, "kills": 1, "wipes": 1, "deaths": 2, "xp": 9000}},
    }


def _big_db(n_items: int = 4000, n_ledger: int = 3000, n_err: int = 80, n_blocked: int = 300) -> dict:
    rnd = random.Random(7)
    items = {}
    for i in range(n_items):
        h = []
        for d in range(14):
            h += [20390 + d, 90 + d, 100 + d, 40, 3, 5, 24]
        items[2000 + i] = {"v": 13, "h": h}
    ledger = [{"type": "buy" if k % 2 == 0 else "sell", "item": f"Objet {k % 50}", "qty": 20, "money": 2000 + k,
               "cut": 100, "day": f"2026-11-{1 + k % 7:02d}"} for k in range(n_ledger)]
    diag = {"errors": [{"module": f"Mod{k % 9}", "msg": f"attempt to index a nil value (champ {k % 13})",
                        "n": 1 + rnd.randint(0, 40), "t": int(NOW) - k * 60, "combat": k % 5 == 0} for k in range(n_err)],
            "blocked": [{"func": f"Fonction{k % 17}", "kind": "UseAction", "ctx": "combat" if k % 2 else "hors combat",
                         "n": 1 + k % 4, "t": int(NOW) - k * 30} for k in range(n_blocked)],
            "selftest": {"t": int(NOW) - 300, "ok": 30, "warn": 1, "ko": 1, "items": [
                {"id": "a", "label": "Boussole", "status": "warn", "detail": "position inconnue"},
                {"id": "b", "label": "Macros", "status": "ko", "detail": "macro trop longue"}]}}
    return {"schema": 1, "version": "4.0.0", "diag": diag,
            "modules": {"Auction": {"realms": {"Royaumetest-Horde": {"n": n_items, "items": items}}}},
            "export": {"maps": [{"id": 1424, "name": "Zone Test"}]},
            "chars": {"Testeur-Royaumetest": {"summary": _summary(), "modules": {"Auction": {"ledger": ledger}}}}}


def _big_atr(n_items: int = 10000, days: int = 21) -> str:
    out = ["AUCTIONATOR_SAVEDVARS = {", '\t["LIFO_AUCTION_SORT"] = false,', "}", "AUCTIONATOR_PRICE_DATABASE = {",
           '\t["__dbversion"] = 6,']
    for realm in ("Autreroyaume Alliance", "Royaumetest Horde"):
        out.append(f'\t["{realm}"] = {{')
        for i in range(n_items):
            item, m = 2000 + i, 100 + i % 977
            if i % 3 == 0:                                      # newer Auctionator: one JSON string per item
                js = '{\\"m\\":%d,\\"h\\":{%s}}' % (m, ",".join('\\"%d\\":%d' % (2100 + d, m + d) for d in range(days)))
                out.append(f'\t\t["{item}"] = "{js}",')
            else:                                               # table entries, one key per line
                out.append(f'\t\t["{"g:" if i % 7 == 1 else ""}{item}{":0" if i % 7 == 1 else ""}"] = {{')
                out.append(f'\t\t\t["m"] = {m},')
                out.append('\t\t\t["h"] = {')
                out += [f'\t\t\t\t["{2100 + d}"] = {m + d},' for d in range(days)]
                out.append("\t\t\t},")
                out.append("\t\t},")
        out.append("\t},")
    out.append("}")
    out.append("AUCTIONATOR_POSTING_HISTORY = {")
    out.append('\t["2000"] = {\n\t\t{\n\t\t\t["price"] = 5,\n\t\t},\n\t},')
    out.append("}")
    return "\n".join(out) + "\n"


def _plan_file() -> dict:
    steps = []
    for i in range(40):
        q = 1000 + (i % 20)
        k = "turn" if i % 5 == 4 else "obj"
        steps.append({"k": k, "q": q, "map": 1424, "x": 40.0, "y": 50.0, "label": f"Étape {i + 1} de « Quête {i % 20} »",
                      "eta": i * 2})
    steps.insert(3, {"k": "obj", "q": 9999, "map": 1424, "label": "Tuer pour « Quête Rendue Hier »", "eta": 7})
    steps.insert(5, {"k": "pick", "q": 1003, "map": 1424, "label": "Prendre « Quête 3 »", "eta": 9})
    return {"v": 1, "made": int(NOW - 3600), "char": "Testeur-Royaumetest", "zone": 1424,
            "routes": [{"id": "xph", "title": "XP/h max", "xph": 28500, "minutes": 90, "verdict": "ok", "steps": steps},
                       {"id": "clear", "title": "Tout finir", "minutes": 120, "verdict": "long",
                        "reasons": ["Trop de quêtes loin"], "steps": steps[:5]}]}


@pytest.fixture(scope="module")
def big(tmp_path_factory: pytest.TempPathFactory) -> SimpleNamespace:
    root = tmp_path_factory.mktemp("bigwow")
    sv = root / "WTF" / "Account" / "TEST" / "SavedVariables"
    sv.mkdir(parents=True)
    db = _big_db()
    (sv / "TreeAI.lua").write_text("TreeAIDB = " + _lua(db) + "\n", encoding="utf-8")
    (sv / "Auctionator.lua").write_text(_big_atr(), encoding="utf-8")
    addons = root / "Interface" / "AddOns"
    (addons / "TreeAI_Plan").mkdir(parents=True)
    (addons / "TreeAI_Plan" / "Plan.lua").write_text("TreeAIPlan = " + _lua(_plan_file()) + "\n", encoding="utf-8")
    logs = root / "Logs"
    logs.mkdir()
    return SimpleNamespace(root=root, sv=sv / "TreeAI.lua", atr=sv / "Auctionator.lua", addons=addons, logs=logs,
                           db=db)


def _inst(big: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(flavor_dir=big.root, combat_log=big.logs / "WoWCombatLog.txt",
                           chat_log=big.logs / "WoWChatLog.txt", addon_dir=big.root / "nope", addons_dir=big.addons,
                           source="test")


# ------------------------------------------------------------------------------------------ data layer
def test_auctionator_index_reads_only_what_the_page_shows(big: SimpleNamespace) -> None:
    assert big.atr.stat().st_size > 8_000_000                     # a real-size database
    idx = live.AtrIndex()
    t0 = time.perf_counter()
    assert idx.load(big.atr) == 2
    assert set(idx.realm_keys()) == {"Autreroyaume Alliance", "Royaumetest Horde"}
    rows = idx.entries("Royaumetest Horde", [2000, 2001, 2002, 2003, 123456])
    dt = time.perf_counter() - t0
    assert set(rows) == {2000, 2001, 2002, 2003}                  # JSON string, g: key, table entries
    assert rows[2000]["m"] == 100 and rows[2000]["h"][2100 + live.AUCTIONATOR_DAY0] == 100
    assert rows[2001]["m"] == 101 and len(rows[2001]["h"]) == 21
    assert idx.count("Royaumetest Horde") == 10000
    assert len(idx._cache) == 5                                   # 4 entries + 1 miss parsed, nothing else
    assert dt < 3.0, dt                                           # the full parse took ~3 s on 17 MB
    # same answer as the complete parser on a small slice of the same layout
    small = _big_atr(30, 4)
    from treeaicoach.wow import savedvars

    full = live.parse_auctionator(savedvars.parse(small)[live.AUCTIONATOR_VAR])
    p = big.root / "small.lua"
    p.write_text(small, encoding="utf-8")
    idx2 = live.AtrIndex()
    idx2.load(p)
    assert idx2.entries("Royaumetest Horde", range(2000, 2030)) == full["Royaumetest Horde"]


def test_auctionator_index_falls_back_on_unknown_layout(tmp_path: Path) -> None:
    p = tmp_path / "Auctionator.lua"
    p.write_text('AUCTIONATOR_PRICE_DATABASE = { ["R A"] = { ["5"] = { ["m"] = 7 } } }', encoding="utf-8")
    idx = live.AtrIndex()
    assert idx.load(p) == 1 and idx.entries("R A", [5]) == {5: {"m": 7, "h": {}}}
    assert live.AtrIndex().load(tmp_path / "absent.lua") == 0


def test_read_db_cached_parses_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from treeaicoach.wow import savedvars

    p = tmp_path / "TreeAI.lua"
    p.write_text('TreeAIDB = { ["chars"] = {} }', encoding="utf-8")
    calls = []
    real = savedvars.parse
    monkeypatch.setattr(savedvars, "parse", lambda *a, **k: calls.append(1) or real(*a, **k))
    a = live.read_db_cached(p)
    b = live.read_db_cached(p)
    assert a is b and calls == [1]
    p.write_text('TreeAIDB = { ["chars"] = { ["x"] = 1 } }', encoding="utf-8")
    os.utime(p, (time.time() + 5, time.time() + 5))
    assert live.read_db_cached(p)["chars"] == {"x": 1} and calls == [1, 1]
    assert live.read_db_cached(None) == {} and live.read_db_cached(tmp_path / "nope.lua") == {}
    # two readers at once (the WoW mode and the page after a /reload): one parse, the other waits for it
    os.utime(p, (time.time() + 9, time.time() + 9))
    out: list = []
    ths = [threading.Thread(target=lambda: out.append(live.read_db_cached(p))) for _ in range(3)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    assert len(out) == 3 and out[0] is out[1] is out[2] and calls == [1, 1, 1]


def test_big_save_is_parsed_without_the_ah_history(big: SimpleNamespace) -> None:
    """The AH price history (~90 % of a big save) is cut before parsing; the page reads only its items."""
    assert big.sv.stat().st_size > 5_000_000
    t0 = time.perf_counter()
    db = live.read_db_cached(big.sv)
    dt = time.perf_counter() - t0
    realm = db["modules"]["Auction"]["realms"]["Royaumetest-Horde"]
    assert realm["n"] == 4000 and realm["items"] == {}             # the rest of the save is complete
    assert db["chars"]["Testeur-Royaumetest"]["summary"]["level"] == 28 and len(db["diag"]["blocked"]) == 300
    recs = live.ah_items(db, "Royaumetest-Horde", [2000, 2004, 999999])
    assert set(recs) == {2000, 2004} and len(recs[2000]["h"]) == 14 * 7
    assert live.treeai_series(db, "", 0, recs[2000])[-1] == (20403, 113)
    assert dt < 2.5, dt                                           # the full parse took > 2 s on this file
    hub = live.LiveHub(big.sv, big.atr)
    hub.poll()
    deals = live.ah_view(hub.snapshot())["deals"]
    assert len(deals) == 5 and len(deals[0]["ours"]) == 14 and len(deals[0]["atr_series"]) == 21


def test_xp_track_between_reloads() -> None:
    tr = live.XpTrack()
    tr.feed("A-R", 1000, 27, 30000, 38000)
    assert tr.rate() is None                                       # one save: nothing measured
    tr.feed("A-R", 1300, 27, 33000, 38000)
    assert tr.rate() is None                                       # 5 min: too short to say anything
    tr.feed("A-R", 1000 + 1800, 28, 2000, 40000)                   # level up in between
    r = tr.rate()
    assert r and r["xp_h"] == int((8000 + 2000) * 3600 / 1800) and r["minutes"] == 30
    assert tr.points()[-1] == (30.0, 10000.0)
    tr.feed("B-R", 5000, 10, 100, 5000)                            # another character: start again
    assert tr.rate() is None and len(tr.samples) == 1


def _snap(**over: object) -> dict:
    s = _summary()
    snap = {"now": NOW, "summary": s, "char_key": "Testeur-Royaumetest", "char": {"summary": s}, "sv_age": 100.0,
            "db": {"export": {"maps": [{"id": 1424, "name": "Zone Test"}]}}, "chat": {}, "chat_age": None}
    snap.update(over)
    return snap


def test_freshness_never_presents_old_data_as_live() -> None:
    f = live.freshness(_snap())
    assert f["text"] == "Données du /reload il y a 2 min." and f["badge"] == "/reload il y a 2 min" and not f["stale"]
    old = _snap(sv_age=4000.0)
    old["summary"] = {**old["summary"], "updated": NOW - 4000}
    f = live.freshness(old)
    assert f["stale"] and f["tone"] == "warning" and "Tape /reload" in f["text"]
    assert live.hero_view(old)["kicker"] == "Au dernier /reload"
    older = _snap(sv_age=8 * 3600.0)
    older["summary"] = {**older["summary"], "updated": NOW - 8 * 3600}
    assert live.hero_view(older)["kicker"] == "Dernière session"
    chat = {"events": 12, "xp": 9000, "duration": 1500, "deaths": 0, "xp_series": [0, 4000, 9000]}
    assert live.freshness(_snap(chat=chat, chat_age=30.0))["badge"] == "En direct"
    assert live.freshness(_snap(chat=chat, chat_age=3600.0))["badge"] != "En direct"   # a chat log 1 h old
    rows = dict(live.session_view(_snap(chat=chat, chat_age=3600.0))["rows"])
    assert not any("en direct" in k for k in rows)
    assert live.freshness({})["badge"] == "Pas de données"


def test_xp_rate_is_measured_or_absent() -> None:
    assert live.xp_rate(_snap()) is None                          # nothing measured: no number at all
    chat = {"events": 9, "xp": 12000, "duration": 1800}
    r = live.xp_rate(_snap(chat=chat, chat_age=20.0))
    assert r == {"xp_h": 24000, "src": "en direct", "minutes": 30.0, "conf": "fiable", "live": True}
    assert live.xp_rate(_snap(chat={**chat, "duration": 300}, chat_age=20.0)) is None      # 5 min: too short
    assert live.xp_rate(_snap(chat={**chat, "xp": 900000}, chat_age=20.0)) is None          # absurd: hidden
    r = live.xp_rate(_snap(xp_track={"xp_h": 18000, "minutes": 14.0, "samples": 3}))
    assert r["src"] == "entre tes /reload" and r["conf"] == "approximatif"
    lp = live.level_progress(_snap(xp_track={"xp_h": 20000, "minutes": 40.0}))
    assert lp["pct"] == 50 and lp["eta"] == "~1 h 00" and lp["conf"] == "fiable"
    assert live.level_progress(_snap())["eta"] == ""             # no measured XP/h: no time to level
    # the live chat log saw a level-up since the save: no stale percentage
    lp = live.level_progress(_snap(chat_age=10.0, chat_since={"xp": 500, "levels": [29], "deaths": 0}))
    assert lp["level"] == 29 and lp["pct"] is None


def test_hero_next_step_quests_of_the_log_only() -> None:
    h = live.hero_view(_snap())
    assert h["step"]["title"] == "Rends « Quête 0 »" and h["step"]["dist"] == "120 m"
    assert h["step"]["eta"] == "≈ 1 min" and h["step"]["why"].startswith("Tout est fait")
    assert h["who"] == "Testeur · Chasseur 28" and h["zone"] == "Zone Test · Camp Factice"
    assert ("Alpha est mort", "danger") in h["danger"] and len(h["danger"]) <= 2
    s = _summary()
    s["todo"]["now"]["text"] = "Rends « Quête Rendue Hier »"     # not in the log any more: never shown
    s["compass"]["target"] = {"kind": "quest", "questID": 9999, "title": "Quête Rendue Hier", "distText": "50 m"}
    s["plan"]["stale"] = True
    h = live.hero_view(_snap(summary=s))
    assert "Rendue Hier" not in h["step"]["title"] and h["step"]["src"] == "tracker"
    assert h["step"]["title"] == "Rends « Quête 0 »" and h["step"]["dist"] == ""
    assert live.hero_view({})["empty"]


def test_trajet_numbered_stops_from_the_plan(big: SimpleNamespace) -> None:
    from treeaicoach.wow.planner import writer

    plan = writer.read_plan(big.addons)
    assert plan and plan["made"] == int(NOW - 3600)
    t = live.trajet_view(_snap(), plan)
    assert t["source"] == "plan" and t["head"].startswith("Étape 2 sur 42")
    titles = [s["title"] for s in t["stops"]]
    assert titles[0] == "Étape 2 de « Quête 1 »" and t["stops"][0]["current"] and not t["stops"][1]["current"]
    assert [s["n"] for s in t["stops"]] == list(range(1, len(t["stops"]) + 1)) and len(t["stops"]) <= live.STOPS_MAX
    assert not any("Rendue Hier" in x or x.startswith("Prendre « Quête 3") for x in titles)   # not in log / taken
    assert t["dropped"] == 2 and "Zone Test" in t["stops"][0]["desc"]
    s = _summary()
    s["plan"]["made"] = NOW - 60                                   # a newer plan, not loaded in game yet
    assert "/reload" in live.trajet_view(_snap(summary=s), plan)["warn"]
    s = _summary()
    s["plan"] = {"src": "none"}
    t = live.trajet_view(_snap(summary=s), plan)
    assert t["source"] == "suivi" and all(st["title"] != "Quête Rendue Hier" for st in t["stops"])


def test_trajet_shows_the_in_game_route_not_the_quest_log() -> None:
    """QA-QUEST F5: the game follows its own route (the default): the tab numbers the stops the addon shows on its
    « Prochaine étape » card (summary.plan.next, flights / hearth included), never the tracker's quest list with
    « En cours » on an elite quest the duo route leaves out."""
    s = _summary()
    s["tracker"]["route"].insert(0, {"id": 1019, "title": "Hypercapaciteur bidulotron", "hard": True, "done": 0,
                                     "total": 1, "zone": "Mille pointes"})
    s["plan"] = {"v": 1, "src": "auto", "route": "auto", "title": "Trajet TreeAI", "step": 1, "steps": 14,
                 "label": "Ramasse « Huile de basilic »", "next": [
                     {"k": "obj", "q": 1001, "map": 1441, "label": "Ramasse « Huile de basilic » (0/5)", "min": 4},
                     {"k": "hs", "map": 1454, "label": "Pierre de foyer vers Orgrimmar", "min": 6},
                     {"k": "fly", "map": 1440, "label": "Vol pour Poste de Bois-brisé", "min": 9},
                     {"k": "turn", "q": 1000, "map": 1440, "label": "Rendre « Quête 0 »", "min": 12}]}
    t = live.trajet_view(_snap(summary=s), None)
    assert t["source"] == "jeu" and [x["n"] for x in t["stops"]] == [1, 2, 3, 4]
    assert [x["title"] for x in t["stops"]] == ["Ramasse « Huile de basilic » (0/5)", "Pierre de foyer vers Orgrimmar",
                                               "Vol pour Poste de Bois-brisé", "Rendre « Quête 0 »"]
    assert t["stops"][0]["current"] and not any(x["current"] for x in t["stops"][1:])
    assert "dans ≈ 12 min" in t["stops"][3]["desc"] and not any("Hypercapaciteur" in x["title"] for x in t["stops"])
    sec = {b["id"]: b for b in pw.tab_sections("trajet", _snap(summary=s))}["trajet.stops"]
    assert sec["title"] == "Ton trajet" and [r.get("num") for r in sec["rows"]] == ["1", "2", "3", "4"]
    assert sec["rows"][0]["badge"] == "En cours" and all(r.get("badge") != "En cours" for r in sec["rows"][1:])
    # an older addon (no « next »): the quest log, titled as such, without numbers nor « En cours »
    s["plan"].pop("next")
    sec = {b["id"]: b for b in pw.tab_sections("trajet", _snap(summary=s))}["trajet.stops"]
    assert sec["title"] == "Ton journal" and all("num" not in r for r in sec["rows"])
    assert all(r.get("badge") != "En cours" for r in sec["rows"])
    assert sec["rows"][0]["title"] == "Hypercapaciteur bidulotron" and sec["rows"][0].get("badge") == "À plusieurs"


def test_abandon_is_the_addons_list_only() -> None:
    s = _summary()
    assert live.abandon_advice(s) == [{"title": "Quête 7", "why": "grise : presque plus d'XP"}]
    assert live.abandon_advice({"abandon": [{"title": "X", "why": "pas rentable ici"}, "Y"]}) == \
        [{"title": "X", "why": "pas rentable ici"}, {"title": "Y", "why": "conseillé par l'addon"}]
    no_list = {**s, "abandon": None}
    assert live.abandon_advice(no_list) == []                     # no home-made guess any more


def test_hv_hides_deals_of_an_old_scan() -> None:
    s = _summary()
    assert len(live.ah_view(_snap(summary=s))["deals"]) == 5
    s["auction"] = {**s["auction"], "scanAge": 30 * 3600}
    v = live.ah_view(_snap(summary=s))
    assert v["deals"] == [] and v["stale_deals"] == 5 and v["none"][0].startswith("Pas d'achat-revente")
    s["auction"] = {k: x for k, x in s["auction"].items() if k != "scanAge"}
    assert live.ah_view(_snap(summary=s))["deals"] == []           # no known scan: no buy advice
    db = {"modules": {"Auction": {"lastScanT": NOW - 3600}}}
    assert len(live.ah_view(_snap(summary=s, db=db))["deals"]) == 5
    secs = pw.tab_sections("hv", _snap(summary={**s, "auction": {**s["auction"], "scanAge": 90000}}))
    assert secs[0]["rows"][0]["title"].startswith("Pas d'achat-revente") and secs[0]["rows"][0]["tone"] == "warning"


def test_every_tab_view_model(big: SimpleNamespace) -> None:
    from treeaicoach.wow.planner import writer

    snap = _snap(db=big.db, chat={"events": 30, "xp": 15000, "duration": 1800, "deaths": 1, "copper": 5000,
                                  "xp_series": [0, 3000, 8000, 15000], "loot": [("Peau Test", 4), ("Lin", 2)]},
                 chat_age=15.0)
    vm = pw.page_vm({"live": snap, "plan": writer.read_plan(big.addons), "errfile": None, "mode": {}})
    tabs = vm["tabs"]
    assert set(tabs) == set(pw.TAB_KEYS) and vm["visible"]["donjon"]
    ids = {k: [s["id"] for s in v] for k, v in tabs.items()}
    assert ids["trajet"][:2] == ["trajet.stops", "trajet.routes"] and "trajet.abandon" in ids["trajet"]
    assert ids["groupe"][0] == "groupe.members" and len(tabs["groupe"][0]["rows"]) == 4
    alpha = tabs["groupe"][0]["rows"][1]
    assert alpha["title"] == "Alpha" and ["Mort", "danger"] in alpha["badges"]
    assert ids["hv"] == ["hv.deals", "hv.mine", "hv.profit"]
    assert ids["session"][0] == "session.curve" and "session.loot" in ids["session"]
    assert ids["donjon"][0] == "donjon.now" and ids["erreurs"][:3] == ["erreurs.file", "erreurs.blocked", "erreurs.errors"]
    blocked = tabs["erreurs"][1]["rows"]
    assert blocked[0][0].startswith("×") and len(blocked) == 17                 # grouped by function
    counts = [int(t.split()[0][1:]) for t, _tip, _tone in tabs["erreurs"][2]["rows"]]
    assert counts == sorted(counts, reverse=True)                               # most frequent first
    assert vm["hero"]["badge"] == "En direct" and vm["hero"]["xp"]["xp_h"] == 30000
    # signatures: same data, same signature; every section carries one
    assert pw.page_vm({"live": snap, "plan": writer.read_plan(big.addons)})["tab_sig"] == vm["tab_sig"]
    # wording rules of docs/DESIGN.md: no em dash, no jargon in what the page shows
    texts = [repr(vm["hero"])] + [repr(s) for v in tabs.values() for s in v]
    assert not [t for t in texts if "\u2014" in t]
    assert all(live.is_plain(t) for t in texts)


# ------------------------------------------------------------------------------------------ the page (Qt)
def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN202
    pytest.importorskip("PySide6")
    if str(TESTS) not in sys.path:
        sys.path.insert(0, str(TESTS))
    import test_launcher as tl

    from treeaicoach import ui as app_ui

    monkeypatch.setenv("TREEAICOACH_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(app_ui, "_report_function", lambda name: (lambda n=50: []) if name == "list_games" else None)
    app, _v, _e = tl._build(tmp_path, start_backend=False)
    return app


def _pump(app: object, ms: int = 0) -> None:
    end = time.monotonic() + ms / 1000
    while True:
        app.qapp.processEvents()
        if time.monotonic() >= end:
            break
        time.sleep(0.005)


def test_tab_bar_always_matches_the_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The 3.9.7 screenshot: « HV » selected, « Session » shown. Every way of switching keeps them equal."""
    app = _app(tmp_path, monkeypatch)
    try:
        app.show_page("wow")
        view = app.page_views["wow"]
        assert view.visible_tab() == view.current_tab() == view.tabs.value() == "trajet"
        view.state = {"install": None, "live": _snap(), "rows": [], "mode": {}}
        view._render()
        expect = {"trajet": "Ton journal", "groupe": "Le groupe", "hv": "Achat-revente", "session": "Objectifs",
                  "donjon": "En ce moment", "erreurs": "Fichier d'erreurs"}
        for key in ("hv", "session", "hv", "groupe", "donjon", "erreurs", "trajet", "session"):
            view.tabs._buttons[key].click()                           # a real click on the segment
            assert view.current_tab() == view.visible_tab() == view.tabs.value() == key
            assert view.tab_views[key].section_titles()[0] == expect[key]
            others = [k for k in pw.TAB_KEYS if k != key]
            assert all(view.tab_views[k].isHidden() for k in others)
        view._show_tab("direct")                                      # 3.9 names land on « Trajet »
        assert view.visible_tab() == view.tabs.value() == "trajet"
        # a render while another tab is shown never swaps the content under the bar
        view.tabs._buttons["hv"].click()
        view.state = {"install": None, "live": _snap(sv_age=50.0), "rows": [], "mode": {}}
        view._render()
        assert view.visible_tab() == view.tabs.value() == "hv"
        # « Donjon » exists only in a dungeon; leaving it while it is shown goes back to « Trajet »
        view.tabs._buttons["donjon"].click()
        s = _summary()
        s.pop("dungeon")
        view.state = {"install": None, "live": _snap(summary=s), "rows": [], "mode": {}}
        view._render()
        assert not view.tabs.choice_visible("donjon")
        assert view.visible_tab() == view.tabs.value() == "trajet"
    finally:
        app.close()


def test_rows_are_updated_in_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(tmp_path, monkeypatch)
    try:
        app.show_page("wow")
        view = app.page_views["wow"]
        view.state = {"install": None, "live": _snap(), "rows": [], "mode": {}}
        view._render()
        sec = view.tab_views["trajet"].views["trajet.stops"]
        rows_before = list(sec.rows)
        titles = [r.title.text() for r in sec.rows if not r.isHidden()]
        assert titles[:2] == ["Quête 0", "Quête 1"] and sec.rows[0].num is None   # (QA-QUEST F5) journal: no number
        s = _summary()
        s["quests"]["list"][1]["obj"] = ["Objectif Test 7/8"]        # one quest progressed (the log's own words)
        view.state = {"install": None, "live": _snap(summary=s), "rows": [], "mode": {}}
        view._render()
        assert sec.rows == rows_before                                # same widgets, updated in place
        assert sec.rows[1].desc.text().startswith("Objectif Test 7/8")
        s["tracker"]["route"] = s["tracker"]["route"][:3]             # shorter list: extra rows hidden, kept
        view.state = {"install": None, "live": _snap(summary=s), "rows": [], "mode": {}}
        view._render()
        assert sec.rows == rows_before and sum(not r.isHidden() for r in sec.rows) == 3
        hero_title = view.hero.title.text()
        assert hero_title == "Rends « Quête 0 »" and "120 m" in view.hero.sub.text()
        qt = pw._qt_classes()
        from PySide6 import QtWidgets

        assert qt["Sparkline"].paintEvent is QtWidgets.QLabel.paintEvent     # painted once, no Python repaint
    finally:
        app.close()


def test_render_is_throttled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """States can arrive in bursts: renders stay at least RENDER_MIN_MS apart and the last state wins."""
    app = _app(tmp_path, monkeypatch)
    try:
        app.show_page("wow")
        view = app.page_views["wow"]
        view.timer.stop()
        stamps: list[float] = []
        real = view._render
        monkeypatch.setattr(view, "_render", lambda: (stamps.append(time.monotonic()), real()))
        view._render_timer.timeout.disconnect()
        view._render_timer.timeout.connect(view._render)
        for i in range(12):                                           # a burst of states, 25 ms apart
            view._on_state({"install": None, "live": _snap(sv_age=100.0 + i), "rows": [], "mode": {}})
            _pump(app, 25)
        _pump(app, 1200)
        assert 1 <= len(stamps) <= 4
        assert all(b - a >= pw.RENDER_MIN_MS / 1000 - 0.05 for a, b in zip(stamps, stamps[1:])), stamps
        assert view.hero.badge.text() == "/reload il y a 2 min"       # the last state is on screen
    finally:
        app.close()


def test_freeze_budget_big_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, big: SimpleNamespace) -> None:
    """Big save + big Auctionator: the worker does the heavy part; the UI thread stays under 30 ms per refresh
    and per tab switch (CPU time of the UI thread), and the event loop keeps turning while the worker parses."""
    from treeaicoach.wow import install

    inst = _inst(big)
    monkeypatch.setattr(install, "find_wow", lambda *_a, **_k: inst)
    monkeypatch.setattr(pw.WowPage, "on_show", lambda self: None)      # cold page: no refresh of its own
    app = _app(tmp_path, monkeypatch)
    try:
        app.show_page("wow")
        view = app.page_views["wow"]
        view.timer.stop()
        _pump(app, 300)
        # 1. the worker reads everything (cold: nothing parsed yet) while the UI thread measures its own lag
        with live._DB_LOCK:
            live._DB_CACHE.clear()
        hub = live.LiveHub()
        box: dict = {}
        gaps: list[float] = []
        th = threading.Thread(target=lambda: box.update(st=pw.read_state(SimpleNamespace(wow_path=""), hub)))
        t0 = time.perf_counter()
        th.start()
        last = time.perf_counter()
        while th.is_alive():
            app.qapp.processEvents()
            time.sleep(0.004)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now
        worker_s = time.perf_counter() - t0
        st = box["st"]
        assert st["vm"]["tabs"]["hv"] and st["live"]["atr_count"] == 10000
        assert st["vm"]["tabs"]["trajet"][0]["foot"].startswith("Étape 2 sur 42")     # the plan file was read
        assert max(gaps) < 0.25, (max(gaps), worker_s)               # never a frozen window while it parses
        # 2. the UI thread applies it: first render (builds the visible tab) and every later refresh
        view._on_state(st)
        view._render()
        first = view.render_ms[-1]
        while view._dirty - {view.current_tab()}:                    # idle warm-up of the hidden tabs
            t = time.thread_time()
            view._warm()
            assert 1000 * (time.thread_time() - t) < 4 * BUDGET_MS
        switches = {}
        for key in ("hv", "session", "groupe", "donjon", "erreurs", "trajet"):
            t = time.thread_time()
            view.tabs._buttons[key].click()
            switches[key] = 1000 * (time.thread_time() - t)
        # a new save arrives with changes in every tab: each refresh is a diff, measured with that tab on screen
        s = big.db["chars"]["Testeur-Royaumetest"]["summary"]
        a = s["auction"]
        s2 = {**s, "xp": 26000, "updated": NOW - 30, "tracker": {**s["tracker"], "route": s["tracker"]["route"][2:]},
              "plan": {**s["plan"], "step": 5},
              "group": {**s["group"], "members": [{**m, "dead": not m.get("dead")} for m in s["group"]["members"]]},
              "auction": {**a, "deals": [{**d, "sell": d["sell"] + 100} for d in a["deals"][:3]],
                          "owned": a["owned"][::-1]},
              "dungeon": {**s["dungeon"], "boss": "Sneed", "next": "Gilnid"}}
        db2 = {**st["live"]["db"], "diag": {**big.db["diag"], "blocked": big.db["diag"]["blocked"][:40]}}
        snap2 = {**st["live"], "summary": s2, "sv_age": 30.0, "db": db2, "char": {**st["live"]["char"], "summary": s2},
                 "chat": {"events": 40, "xp": 15000, "duration": 1800, "deaths": 1, "xp_series": [0, 5000, 15000],
                          "loot": [(f"Objet {k}", k) for k in range(30)]}, "chat_age": 5.0}
        st2 = {**st, "live": snap2}
        st2["vm"] = pw.page_vm(st2)
        refreshes = {}
        for key in pw.TAB_KEYS:
            view.tabs._buttons[key].click()
            for state in (st2, st):
                view._on_state(state)
                view._render()
                refreshes[key] = max(refreshes.get(key, 0.0), view.render_ms[-1])
            assert view.visible_tab() == view.tabs.value() == key
        assert first < 4 * BUDGET_MS, first
        assert max(switches.values()) < BUDGET_MS, switches
        assert max(refreshes.values()) < BUDGET_MS, refreshes
        print(f"\nworker {worker_s:.2f} s · max UI gap {1000 * max(gaps):.0f} ms · first render {first:.1f} ms · "
              f"switches {max(switches.values()):.1f} ms · refreshes {max(refreshes.values()):.1f} ms")
        # 3. nothing heavy on the next poll: the files did not change
        t = time.perf_counter()
        st3 = pw.read_state(SimpleNamespace(wow_path=""), hub)
        assert st3["events"] == [] and time.perf_counter() - t < 2.0
    finally:
        app.close()


def test_virtual_list_updates_lines_and_keeps_text_readable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lines without a tone use the palette's text colour (an empty brush painted them black on graphite)."""
    app = _app(tmp_path, monkeypatch)
    try:
        from PySide6 import QtCore

        from treeaicoach import ui_widgets as W

        vl = W.VirtualList(max_visible=3)
        vl.set_rows([("un", "", ""), ("deux", "info", "danger"), ("trois", "", "")])
        m = vl.model()
        assert vl.rows() == ["un", "deux", "trois"]
        assert m.item(0).data(QtCore.Qt.ForegroundRole) is None
        assert m.item(1).foreground().color().name().lower() == W.theme().danger.lower()
        h = vl.height()
        vl.set_rows([("un", "", ""), ("deux", "info", ""), ("trois", "", ""), ("quatre", "", ""), ("cinq", "", "")])
        assert m.item(1).data(QtCore.Qt.ForegroundRole) is None and vl.rows()[-1] == "cinq"
        assert vl.height() == h                                      # 3 lines shown, the rest scroll inside
        vl.set_rows([("un", "", "")])
        assert vl.rows() == ["un"] and vl.height() < h
    finally:
        app.close()


def test_page_strings_follow_the_design_rules() -> None:
    """docs/DESIGN.md for the WoW page and its views: no em dash, no emoji, no banned colour."""
    if str(TESTS) not in sys.path:
        sys.path.insert(0, str(TESTS))
    import test_design_rules as D

    for mod in (pw, live):
        path = Path(mod.__file__)
        bad = [(ln, s[:60]) for ln, s in D._strings(path) if D.EM_DASH in s or D._EMOJI.search(s)]
        assert not bad, (path.name, bad[:5])
        src = path.read_text(encoding="utf-8").upper()
        assert not [c for c in D.BANNED_COLORS if c in src], path.name


def test_save_cutter_keeps_valid_lua() -> None:
    """The AH-history cut leaves a parseable save; layouts it does not know are parsed in full."""
    from treeaicoach.wow import savedvars

    db = {"modules": {"Auction": {"realms": {"R-Horde": {"n": 2, "items": {7: {"v": 1, "h": [1, 2, 3, 4, 5, 6, 7]}},
                                                         "z": 1},
                                             "R-Alliance": {"items": {}, "n": 0}}}},
          "chars": {"A-R": {"summary": {"level": 3}}}}
    text = ("TreeAIDB = " + _lua(db) + "\n").encode()
    lite, cut = live._strip_ah_items(text)
    assert set(cut) == {"R-Horde", "R-Alliance"} and len(lite) < len(text)
    parsed = savedvars.parse(lite.decode())["TreeAIDB"]
    assert parsed["modules"]["Auction"]["realms"]["R-Horde"] == {"n": 2, "items": {}, "z": 1}
    assert parsed["chars"]["A-R"]["summary"]["level"] == 3
    inline = text.replace(b'["items"] = {\n\t\t\t\t\t},', b'["items"] = {},')
    lite2, cut2 = live._strip_ah_items(inline)
    assert set(cut2) == {"R-Horde"} and savedvars.parse(lite2.decode())["TreeAIDB"]["chars"]
    flat = b'TreeAIDB = { ["modules"] = { ["Auction"] = { ["realms"] = { ["R"] = { ["items"] = { [1] = {} } } } } } }'
    assert live._strip_ah_items(flat) == (flat, {})
