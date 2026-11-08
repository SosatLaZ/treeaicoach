"""4.0 final QA of the WoW app side (research/qa_app.md, QA-APP): what the player sees from the REAL addon summary.
Each test names the finding it locks (APP-n). Invented names only."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from treeaicoach import ui_page_wow as pw
from treeaicoach.wow import ai_context as ac
from treeaicoach.wow import ai_facts as F
from treeaicoach.wow import errlog, live

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))
from test_wow_page import NOW, _snap, _summary  # noqa: E402

KEY = "Testeur-Royaumetest"


def _db(s: dict, **top: object) -> dict:
    return {"chars": {KEY: {"summary": s}}, **top}


# ------------------------------------------------------------------ APP-1: « later » is not « abandon »
def test_only_abandon_verdicts_become_retire() -> None:
    s = _summary()
    assert [a["title"] for a in live.abandon_advice(s)] == ["Quête 7"]          # « Quête 3 » is « later »
    rows = {x["id"]: x for x in pw.tab_sections("trajet", _snap())}["trajet.abandon"]["rows"]
    assert [r["title"] for r in rows] == ["Retire « Quête 7 »"]
    later = {**s, "abandon": {"list": [{"title": "Quête 3", "verdict": "later", "why": "fais-la si tu passes à côté"}]}}
    assert live.abandon_advice(later) == [] and live.quests_view({**_snap(), "summary": later})["abandon"] == []
    q = F.quests(F.make_snap(_db(later), now=NOW)).data
    assert "a_abandonner" not in q and q["en_passant_seulement"][0].startswith("Quête 3")
    ctx = ac.question_context("Quelle quête je peux abandonner ?", db=_db(later), live={}, plan=None, facts=[], now=NOW)
    say = ac.local_answer("Quelle quête je peux abandonner ?", ctx)
    assert say.startswith("Aucune quête à abandonner") and "Abandonne" not in say and "Quête 3" in say


# ------------------------------------------------------------------ APP-4: blocks of modules that are off / stopped
def test_blocks_of_stopped_modules_are_dropped() -> None:
    s = _summary()
    s["stats"] = {"v": 1, "t": NOW - 3 * 86400, "xph": {"recent": 36231, "recentMin": 57}}
    s["auction"] = {**s["auction"], "updated": NOW - 3 * 86400}
    s["fight"] = {"deaths": 2, "lastDeath": {"killer": "Ogre Test", "time": NOW - 3 * 86400}}
    s["leveling"] = {"trainPending": 20}
    s["hunter"] = {"t": NOW - 120, "pet": {"family": "Loup"}}
    db = _db(s, modules={"Leveling": {"enabled": False}, "Quests": {"enabled": True}})
    t = live.trusted_summary(db, db["chars"][KEY], NOW)
    assert "stats" not in t and "auction" not in t and "leveling" not in t                # 3 days old / module off
    assert "lastDeath" not in t["fight"] and t["fight"]["deaths"] == 2                     # a death of 3 days ago
    assert t["hunter"] and t["quests"] and t["group"]                                    # written with the save
    assert s["stats"] and s["fight"]["lastDeath"]                                         # the parsed save is untouched
    per_char = {"chars": {KEY: {"summary": s, "profile": "char", "moduleSettings": {"Hunter": {"enabled": False}}}},
                "modules": {"Hunter": {"enabled": True}}}
    assert "hunter" not in live.trusted_summary(per_char, per_char["chars"][KEY], NOW)   # per-character profile wins
    off = {"chars": {KEY: {"summary": s}}, "settings": {"enabled": False}}
    assert "quests" not in live.trusted_summary(off, off["chars"][KEY], NOW)            # all of TreeAI off
    # the « Hey coach » snapshot uses the same rules
    snap = F.make_snap(db, now=NOW)
    assert "stats" not in snap.s and "auction" not in snap.s and "lastDeath" not in snap.s["fight"]


def test_hub_shows_no_stale_block(tmp_path: Path) -> None:
    from test_wow_page import _lua

    s = _summary()
    s["updated"] = time.time() - 60
    s["stats"] = {"v": 1, "t": s["updated"] - 3 * 86400, "xph": {"recent": 36231, "recentMin": 57}}
    s["auction"] = {**s["auction"], "updated": s["updated"] - 3 * 86400}
    s["group"]["updated"] = s["missions"]["updated"] = s["updated"]
    p = tmp_path / "TreeAI.lua"
    p.write_text("TreeAIDB = " + _lua(_db(s)) + "\n", encoding="utf-8")
    hub = live.LiveHub(p)
    hub.poll()
    snap = hub.snapshot()
    assert "stats" not in snap["summary"] and "auction" not in snap["summary"]
    assert live.xp_rate(snap) is None or live.xp_rate(snap)["src"] != "mesurée par l'addon"
    assert live.ah_view(snap)["deals"] == [] and live.ah_view(snap)["mine"] == []


# ------------------------------------------------------------------ APP-5: yesterday's group in today's solo save
def test_group_blocks_only_for_the_current_group() -> None:
    s = {**_summary(), "inGroup": False, "groupSize": 0}
    snap = _snap(summary=s, sv_age=60.0)
    snap["summary"]["updated"] = NOW - 60
    assert live.members_view(snap) == [] and live.group_view(snap)["empty"]
    assert not any("est mort" in t for t, _c in live.danger_facts(snap))
    assert "Session du groupe" not in dict(live.session_view(snap)["rows"])
    assert pw.tab_sections("groupe", snap) == []
    old = _summary()
    old["group"] = {**old["group"], "updated": NOW - 3600}          # in a group, but the block is an hour older
    old["missions"] = {**old["missions"], "updated": NOW - 3600}
    assert not live.group_ok(old) and live.group_ok(_summary())
    assert ac.local_answer("Où sont mes potes ?", ac.question_context(
        "Où sont mes potes ?", db=_db(s), live={}, plan=None, facts=[], now=NOW)) == "Tu joues seul (au dernier /reload)."


# ------------------------------------------------------------------ APP-6 / 13: « En direct » only when it is
def test_live_badge_and_eta_only_on_a_recent_save() -> None:
    chat = {"events": 20, "xp": 9000, "duration": 1500, "deaths": 0}
    s = _summary()
    s["updated"] = NOW - 7 * 3600
    s["todo"]["now"] = {"text": "Tue « Ours Test » (≈ 1 min)", "why": "C'est la prochaine étape de ton trajet.", "eta": 60}
    old = _snap(summary=s, sv_age=7 * 3600.0, chat=chat, chat_age=20.0)
    f = live.freshness(old)
    assert f["badge"] == "/reload il y a 7 h" and f["tone"] == "warning" and "en direct" in f["text"]
    h = live.hero_view(old)
    assert h["kicker"] == "Dernière session" and h["badge"] != "En direct"
    assert h["step"]["title"] == "Tue « Ours Test »" and h["step"]["eta"] == "" and h["step"]["dist"] == ""
    assert not any(st["current"] for st in live.trajet_view(old)["stops"])
    s2 = _summary()
    s2["todo"]["now"] = dict(s["todo"]["now"])
    h2 = live.hero_view(_snap(summary=s2, chat=chat, chat_age=20.0))
    assert h2["badge"] == "En direct" and h2["step"]["title"].endswith("(≈ 1 min)") and h2["step"]["eta"] == ""


# ------------------------------------------------------------------ APP-7 / 8: the stops of the game, the log's words
def test_trajet_follows_the_in_game_route() -> None:
    s = _summary()
    s["plan"] = {"v": 1, "src": "auto", "route": "auto", "step": 1, "steps": 6, "label": "Tue « Ours Test »",
                 "kind": "obj", "quest": 1005, "left": 10, "updated": NOW - 120}
    t = live.trajet_view(_snap(summary=s))
    assert t["source"] == "suivi" and t["stops"][0]["title"] == "Quête 5" and t["stops"][0]["current"]
    assert all(st["n"] == 0 for st in t["stops"])          # the quest log is never numbered as stops (QA-QUEST F5)
    assert sum(st["current"] for st in t["stops"]) == 1 and t["head"] == "Trajet en jeu : Tue « Ours Test »"
    assert t["stops"][0]["desc"].startswith("Objectif Test 1/8")                       # never « 0/1 »
    assert not any(st["desc"].startswith("0/1") for st in t["stops"])
    # the Planner's own next stops (summary.plan.next) are numbered as the game shows them
    s["plan"]["next"] = [{"k": "obj", "q": 1005, "map": 1424, "label": "Tue « Ours Test » 4/10", "min": 0},
                         {"k": "fly", "map": 1424, "label": "Vol vers Camp Test", "min": 6},
                         {"k": "turn", "q": 4242, "map": 1424, "label": "Rendre « Quête Partie »"}]
    t = live.trajet_view(_snap(summary=s))
    assert t["source"] == "jeu" and t["head"] == "Trajet en jeu : étape 1 sur 6" and t["minutes"] == 10
    assert [st["title"] for st in t["stops"]] == ["Tue « Ours Test » 4/10", "Vol vers Camp Test"]
    assert t["stops"][0]["current"] and t["dropped"] == 1 and "Zone Test" in t["stops"][0]["desc"]
    assert "dans ≈ 6 min" in t["stops"][1]["desc"]
    s["plan"]["stale"] = True                                         # a stale in-game route: the tracker again
    assert live.trajet_view(_snap(summary=s))["source"] == "suivi"


# ------------------------------------------------------------------ APP-9: the real blocked-action counters
def test_blocked_actions_use_the_real_counters() -> None:
    d = {"blocked": [{"func": "UseAction", "kind": "en combat", "ctx": "Quick:clic", "t": int(NOW)}] * 20,
         "blockedTop": {"UseAction|Quick:clic": {"func": "UseAction", "ctx": "Quick:clic", "kind": "en combat", "n": 200,
                                                 "combat": 200, "last": int(NOW) - 30},
                        "CastSpellByName|Quick:clic": {"func": "CastSpellByName", "ctx": "Quick:clic",
                                                       "kind": "en combat", "n": 100, "combat": 0, "last": int(NOW)}},
         "blockedTotal": 300}
    lists = errlog.diag_lists({"diag": d})
    assert lists["counts"]["blocked"] == 300 and lists["blocked"][0][2] == 200
    assert lists["blocked"][0][0].startswith("×200  UseAction") and lists["blocked"][1][0].startswith("×100")
    assert "Actions bloquées par le jeu : 300" in errlog.build_text({"diag": d}, now=NOW)
    assert errlog.diag_lists({"diag": {"blocked": d["blocked"]}})["counts"]["blocked"] == 20     # older saves


# ------------------------------------------------------------------ APP-10: « affaire sûre » as fresh as the page
def test_ai_offers_no_deal_without_a_recent_scan() -> None:
    s = _summary()
    s["auction"] = {k: v for k, v in s["auction"].items() if k != "scanAge"}
    h = F.auction_house(F.make_snap(_db(s), now=NOW))
    assert "affaires_sures" not in h.data and h.volatile and any("scan" in m for m in h.data["manque"])
    db = _db(s, modules={"Auction": {"lastScanT": NOW - 3600}})            # a « Scan ciblé » an hour ago
    assert "affaires_sures" in F.auction_house(F.make_snap(db, now=NOW)).data


# ------------------------------------------------------------------ APP-11: « Calculer » when no route is possible
def test_calculer_says_when_every_route_is_impossible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from treeaicoach.wow import planner
    from treeaicoach.wow.planner import routes

    plan = {"v": 1, "routes": [{"id": i, "verdict": "impossible", "steps": [],
                                "reasons": ["Aucune quête faisable ici avec les données actuelles."]}
                               for i in ("xph", "clear", "squad", "safe")]}
    monkeypatch.setattr(routes, "compute_plan", lambda *a, **k: plan)
    db = {"export": {"char": KEY, "player": {"level": 28}, "src": "log"}}
    res = planner.compute_and_write(SimpleNamespace(addons_dir=tmp_path), db=db)
    assert res["ok"] is False and not list(tmp_path.iterdir())                       # nothing written
    assert res["message"] == ("Aucun trajet possible : aucune quête faisable ici avec les données actuelles. "
                              "Installe Questie (positions des quêtes), puis /reload.")
    assert pw.plan_rows(plan)[0][0] == "Le plus rapide"                               # APP-21: no « (≈ 1 min) »


# ------------------------------------------------------------------ APP-12: the spoken answer says when
def test_local_answer_of_an_old_save_says_so() -> None:
    s = _summary()
    s["updated"] = NOW - 7 * 3600
    s["todo"]["now"] = {"text": "Tue « Ours Test » (≈ 1 min)", "why": "C'est la prochaine étape de ton trajet."}
    ctx = ac.question_context("On fait quoi maintenant ?", db=_db(s), live={}, plan=None, facts=[], now=NOW)
    say = ac.local_answer("On fait quoi maintenant ?", ctx)
    assert say.startswith("Au dernier /reload, il y a 7 h : tue « Ours Test »") and "1 min" not in say


# ------------------------------------------------------------------ APP-14..18, 22..25, 27: smaller truths
def test_level_up_imminent_and_hyperlinked_level_line() -> None:
    snap = _snap(chat_age=10.0, chat_since={"xp": 25000, "levels": [], "deaths": 0})
    lp = live.level_progress(snap)
    assert lp["text"] == "Niveau 29 imminent" and lp["pct"] == 99 and lp["eta"] == "imminent"
    line = "11/7 20:15:03.456  Félicitations, vous avez atteint le |cffffd200|Hlevelup:28:LEVEL_UP_TYPE_CHARACTER|h[niveau 28]|h|r !"
    assert live.parse_chat_line(line, 2026)["level"] == 28


def test_member_badges_session_words_and_dates() -> None:
    cards = {m["name"]: m for m in live.members_view(_snap())}
    assert ("Pas prêt", "warning") in cards["Bravo"]["badges"]
    assert not any(b[0] == "Prêt" for m in cards.values() for b in m["badges"])
    s = _summary()
    s["missions"]["session"] = {"duration": 3540, "turned": 4, "missions": 0, "deaths": 1}
    s["quests"]["questsToLevel"] = 1
    v = live.session_view(_snap(summary=s))
    assert dict(v["rows"])["Session du groupe"] == "59 min · 4 quêtes rendues · 1 mort"
    assert any(g["text"] == "~1 quête pour le niveau suivant" for g in v["goals"])
    assert live.freshness(_snap(sv_age=30.0, summary={**_summary(), "updated": NOW - 30}))["badge"] == "/reload à l'instant"
    assert live.day_text("2025-11-07", NOW) == "aujourd'hui" and live.day_text("2025-11-06", NOW) == "hier"
    assert live.day_text("2025-11-02", NOW) == "2 nov." and live.day_text("2024-12-25", NOW) == "25 déc. 2024"
    assert live.group_view(_snap())["next"] == "Ensemble : « Quête 0 » : toi 6/8, Alpha 2/8"      # no em dash


def test_state_card_dungeon_and_realm_faction() -> None:
    st = {"install": SimpleNamespace(flavor_dir="X", source="auto"), "installed": True, "needs_update": True,
          "src_version": "4.0.0", "inst_version": "4.0.0", "log": ("ok", "")}
    assert ("addon", None, "Mise à jour disponible.") in pw.overview(st)["steps"]
    st["inst_version"] = "3.9.9"
    assert ("addon", None, "Mise à jour disponible : 3.9.9 → 4.0.0.") in pw.overview(st)["steps"]
    s = _summary()
    s["updated"] = NOW - 3 * 3600
    secs = {x["id"]: x for x in pw.tab_sections("donjon", _snap(summary=s, sv_age=3 * 3600.0))}
    assert secs["donjon.now"]["title"] == "Au dernier /reload"
    assert [r["title"] for r in secs["donjon.now"]["rows"]] == ["Les Mortemines"]          # no « boss en cours »
    fresh = {x["id"]: x for x in pw.tab_sections("donjon", _snap())}["donjon.now"]
    assert fresh["title"] == "En ce moment" and "page « Donjons »" in fresh["foot"]
    db = {"modules": {"Auction": {"realms": {"Arak-arahm-Horde": {"n": 3}}}}}
    adb = {"Arak-arahm Alliance": {2000: {"m": 1}}, "Arak-arahm Horde": {2000: {"m": 2}}}
    v = live.ah_view({"summary": _summary(), "char_key": "Testeur-Arak-arahm", "db": db, "atr": adb, "now": NOW,
                      "sv_age": 100.0})
    assert "(Arak-arahm Horde)" in v["info"]                                             # the faction after the LAST « - »


def test_leftover_swaps_are_cleaned(tmp_path: Path) -> None:
    from treeaicoach.wow import install
    from treeaicoach.wow.planner import writer

    src = tmp_path / "src" / "TreeAI"
    src.mkdir(parents=True)
    (src / "TreeAI.toc").write_text("## Version: 4.0.0\n", encoding="utf-8")
    addons = tmp_path / "AddOns"
    (addons / ".TreeAI.old_1_2" / "Modules").mkdir(parents=True)
    (addons / ".TreeAI_Plan.old_3_4").mkdir(parents=True)
    inst = SimpleNamespace(addons_dir=addons, addon_dir=addons / "TreeAI")
    assert install.install_addon(inst, src).ok
    assert writer.write_plan(addons, {"v": 1, "made": int(NOW), "routes": []})[0]
    assert sorted(p.name for p in addons.iterdir()) == ["TreeAI", "TreeAI_Plan"]


# ------------------------------------------------------------------ FIX-SYSTEMS hand-offs: talents, stats words
def test_unreadable_talents_never_give_a_next_talent_as_fact() -> None:
    from treeaicoach.wow import stats, talents

    forever = {"readable": False, "points": "0/0/0", "ranks": "", "build": "HUNTER_BM_SOLO",
               "next": {"name": "Aspects mortels", "rank": 1, "max": 5, "why": "plus de DPS"}}
    for t in (forever, {"points": "0/0/0"}, {}):                  # the flag, an older save, no block at all
        tal = talents.talents_context("HUNTER", 28, t)
        assert "prochain_point" not in tal and not any(k.startswith("prochains_") for k in tal), t
        assert "lire tes talents" in tal["lecture"] and tal["guide_solo"][0].startswith("niveau 29 : ")
        assert "points" not in tal
    readable = talents.talents_context("HUNTER", 28, {"points": "19/0/0", "ranks": "1:1:2=5"})
    assert "lecture" not in readable and readable["prochains_solo"]
    s = {**_summary(), "talents": forever}
    ctx = ac.question_context("Quels talents je prends ?", db=_db(s), live={}, plan=None, facts=[], now=NOW)
    say = ac.local_answer("Quels talents je prends ?", ctx)
    assert say.startswith("Je ne peux pas lire tes talents sur WoW Forever. D'après le guide, niveau 29 : ")
    rows = dict(stats.stats_rows({"stats": {"v": 1, "deathTop": "Ours enragé", "downSec": 9}}))
    assert rows["Repos et morts"] == "repos 9 s / combat · tué surtout par Ours enragé"
