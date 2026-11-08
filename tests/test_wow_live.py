"""WoW « en direct » : file watchers, chat log parser, Auctionator reader, tab views and the page's tabs.
Fake SavedVariables only (tests/fixtures/wow_live, invented names)."""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from treeaicoach import ui_page_wow as pw
from treeaicoach.wow import live

FIX = Path(__file__).resolve().parent / "fixtures" / "wow_live"
TESTS = Path(__file__).resolve().parent
NOW = 1762500000.0 + 600


def _chat_lines(t0: float, lines: list[str]) -> str:
    out = []
    for i, text in enumerate(lines):
        lt = time.localtime(t0 + 60 * i)
        out.append(f"{lt.tm_mon}/{lt.tm_mday} {lt.tm_hour:02d}:{lt.tm_min:02d}:{lt.tm_sec:02d}.123  {text}")
    return "\n".join(out) + "\n"


CHAT = [
    "Canal changé : [1. Général - Forêt Factice]",
    "Loup Test meurt, vous gagnez 120 points d'expérience. (+60 exp Bonus reposé)",
    "Vous ramassez 1 Argent, 20 Cuivre",
    "Vous recevez le butin : [Peau Test]x2.",
    "[Alpha] dit : je gagne 999 points d'expérience lol",
    "Vous gagnez 1 500 points d'expérience.",
    "Votre part du butin : 3 Argent",
    "Félicitations, vous avez atteint le niveau 27 !",
    "Vous êtes mort.",
    "You loot 2 Silver, 5 Copper",
]


@pytest.fixture()
def wowdir(tmp_path: Path) -> SimpleNamespace:
    sv = tmp_path / "WTF" / "Account" / "TEST" / "SavedVariables"
    sv.mkdir(parents=True)
    shutil.copy(FIX / "TreeAI.lua", sv / "TreeAI.lua")
    shutil.copy(FIX / "Auctionator.lua", sv / "Auctionator.lua")
    logs = tmp_path / "Logs"
    logs.mkdir()
    return SimpleNamespace(flavor_dir=tmp_path, sv=sv / "TreeAI.lua", atr=sv / "Auctionator.lua",
                           combat_log=logs / "WoWCombatLog.txt", chat_log=logs / "WoWChatLog.txt")


# ------------------------------------------------------------------------------------------ parsers
def test_parse_chat_lines() -> None:
    t0 = time.mktime((2026, 11, 7, 20, 0, 0, 0, 0, -1))
    evs = [live.parse_chat_line(ln, 2026) for ln in _chat_lines(t0, CHAT).splitlines()]
    kinds = [e and e["kind"] for e in evs]
    assert kinds == ["zone", "xp", "money", "loot", None, "xp", "money", "level", "death", "money"]
    assert evs[0]["zone"] == "Forêt Factice"
    assert evs[1]["xp"] == 120 and evs[1]["kill"] is True
    assert evs[2]["copper"] == 120 and evs[6]["copper"] == 300 and evs[9]["copper"] == 205
    assert evs[3] == {**evs[3], "item": "Peau Test", "count": 2}
    assert evs[5]["xp"] == 1500 and evs[7]["level"] == 27
    assert abs(evs[1]["t"] - (t0 + 60)) < 1
    assert live.parse_chat_line("pas un horodatage") is None
    assert live.parse_money("1 Or, 2 Argent, 3 Cuivre") == 10203


def test_chat_session_rates_and_gap() -> None:
    t0 = time.mktime((2026, 11, 7, 20, 0, 0, 0, 0, -1))
    evs = [e for e in (live.parse_chat_line(ln, 2026) for ln in _chat_lines(t0, CHAT).splitlines()) if e]
    cs = live.ChatSession()
    assert cs.feed(evs) == len(evs)
    d = cs.to_dict()
    assert d["xp"] == 1620 and d["copper"] == 625 and d["kills"] == 1 and d["deaths"] == 1
    assert d["levels"] == [27] and d["zone"] == "Forêt Factice" and d["loot"] == [("Peau Test", 2)]
    assert d["xp_h"] == int(1620 * 3600 / 540)       # 9 min of events (≥ 5 min floor)
    assert d["xp_series"][-1] == 1620.0 and d["gold_series"][-1] == 625.0
    cs.feed([{"t": t0 + 3 * 3600, "kind": "xp", "xp": 10}])    # 3 h later: a new session
    assert cs.xp == 10 and cs.zone == "Forêt Factice"


def test_auctionator_reader() -> None:
    from treeaicoach.wow import savedvars

    adb = live.parse_auctionator(savedvars.load(FIX / "Auctionator.lua")[live.AUCTIONATOR_VAR])
    assert set(adb) == {"Autreroyaume Horde", "Royaumetest Alliance"}
    rows = adb["Royaumetest Alliance"]
    assert set(rows) == {2589, 4234, 2318}                     # "broken" key skipped, JSON string read
    assert rows[2589]["m"] == 180 and rows[2318]["m"] == 330
    assert live.atr_series(adb, "Royaumetest Alliance", 2589) == [(20398, 170), (20399, 185), (20400, 175)]
    assert live.pick_realm(adb, "Royaumetest", "Alliance") == "Royaumetest Alliance"
    assert live.parse_auctionator(None) == {} and live.parse_auctionator({"x": 3}) == {}


def test_file_watch(tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    w = live.FileWatch(p)
    assert w.check() is False and w.age() is None
    p.write_text("x")
    assert w.check() is True and w.check() is False
    os.utime(p, (time.time() + 5, time.time() + 5))
    assert w.check() is True
    p.unlink()
    assert w.check() is True and w.age() is None
    assert live.FileWatch(None).check() is False


# ------------------------------------------------------------------------------------------ hub
def test_hub_polls_only_changes(wowdir: SimpleNamespace) -> None:
    t0 = time.time() - 600
    wowdir.chat_log.write_text(_chat_lines(t0, CHAT[:4]), encoding="utf-8")
    hub = live.LiveHub(wowdir.sv, wowdir.atr, wowdir.combat_log, wowdir.chat_log)
    seen: list = []
    hub.subscribe(seen.append)
    kinds = {e.kind for e in hub.poll()}
    assert kinds == {"savedvars", "auctionator", "chat"} and seen
    snap = hub.snapshot()
    assert snap["char_key"] == "Testeur-Royaumetest" and snap["summary"]["level"] == 26
    assert snap["has_sv"] and snap["sv_age"] is not None and snap["chat"]["xp"] == 120
    assert hub.poll() == []                                       # nothing changed: nothing re-read
    with open(wowdir.chat_log, "a", encoding="utf-8") as fh:
        fh.write(_chat_lines(t0 + 400, ["Vous gagnez 300 points d'expérience."]))
    ev = hub.poll()
    assert [e.kind for e in ev] == ["chat"] and hub.snapshot()["chat"]["xp"] == 420
    hub.set_paths()                                                # no WoW: empty, never raises
    assert hub.poll() == [] and hub.snapshot()["char_key"] == ""


def test_hub_attach_install(wowdir: SimpleNamespace) -> None:
    hub = live.LiveHub()
    hub.attach_install(wowdir)
    hub.poll()
    assert hub.snapshot()["atr"] and hub.snapshot()["summary"]["className"] == "Chasseur"
    hub.attach_install(None)
    hub.poll()
    assert hub.snapshot()["db"] == {}


def test_freshness_is_honest() -> None:
    assert "/reload" in live.freshness_text(None)
    assert live.freshness_text(30) == "Données de ton dernier /reload, à l'instant."
    assert live.freshness_text(17 * 60).startswith("Données de ton dernier /reload il y a 17 min")


# ------------------------------------------------------------------------------------------ views
@pytest.fixture()
def snap(wowdir: SimpleNamespace) -> dict:
    hub = live.LiveHub(wowdir.sv, wowdir.atr, clock=lambda: NOW)
    hub.poll()
    return hub.snapshot()


def test_quests_view_and_abandon_reasons(snap: dict) -> None:
    v = live.quests_view(snap)
    assert v["route"][0][:2] == ("Quête Une", "Prête à rendre")
    assert all(tag in live.QUEST_TAGS for _, tag, _ in v["route"])
    assert "4 quêtes, 1 prête à rendre" in v["head"] and "1 h 15" in v["head"]
    # robustness: nothing in the main quest UI speaks jargon (XP/min, score, %, λ)
    shown = [v["head"], v["now"], v["why"]] + [f"{t} {tag} {w}" for t, tag, w in v["route"]]
    shown += [f"{a['title']} {a['why']}" for a in v["abandon"]] + v["tips"]
    assert all(live.is_plain(x) for x in shown), [x for x in shown if not live.is_plain(x)]
    assert v["now"].startswith(("Rends", "Fais", "Va")) and v["why"]
    assert any("Quête En Chemin" in t and "PNJ Factice" in t for t in v["tips"])
    assert v["plan"] and "plan TreeAI" in v["plan"][0]
    assert v["abandon"] == []                     # (4.0) no home-made guess: the addon's list only
    s = {**snap["summary"], "abandon": {"list": [{"title": "Vieille Quête", "why": "grise : presque plus d'XP"},
                                                 {"title": "Quête Disparue", "why": "plus dans le journal"}]}}
    assert live.quests_view({**snap, "summary": s})["abandon"] == [{"title": "Vieille Quête",
                                                                    "why": "grise : presque plus d'XP"}]
    own = live.abandon_advice({"abandon": [{"title": "X", "why": "pas rentable ici"}, "Y"]})
    assert own == [{"title": "X", "why": "pas rentable ici"}, {"title": "Y", "why": "conseillé par l'addon"}]


def test_group_view(snap: dict) -> None:
    v = live.group_view(snap)
    assert len(v["members"]) == 4 and v["members"][1] == "Alpha (Prêtre 25)"
    assert v["board"][0]["state"].startswith("Charlie") and v["board"][1]["state"] == "tous prêts à rendre"
    assert v["board"][1]["pct"] == 100 and v["claims"] == ["Bravo s'occupe de « Quête Normale »"]
    assert live.group_view({})["empty"]


def test_ah_view_net_profit_and_history(snap: dict) -> None:
    v = live.ah_view(snap)
    d = v["deals"][0]
    assert d["cut"] == 200 and d["deposit"] == 156 and d["net"] == 4000 - 200 - 2000 and d["expected"] == 1700
    assert d["atr"] == 180                                         # Auctionator, same realm + faction
    assert d["ours"] == [(20398, 100), (20399, 110), (20400, 120)]
    assert d["atr_series"][0] == (20398, 170)
    assert v["deals"][1]["kind"] == "vendor" and v["deals"][1]["net"] == 5
    assert v["mine"][0]["undercut"] and v["mine_known"]
    assert v["realized"]["total"] == 9000 and v["realized"]["series"] == [1800.0, 475.0]
    assert v["recent"][0] == ("Lin Test", 475, "7 nov. 2026")           # French date, never ISO
    assert "commission 5 %" in v["info"] and "Auctionator" in v["info"]


def test_session_view(snap: dict) -> None:
    v = live.session_view(snap)
    rows = dict(v["rows"])
    assert "7 quêtes rendues" in rows["Session du groupe"] and rows["Alpha"] == "niveau 24 → 25"
    assert rows["Durée (au /reload)"] == "42 min"
    assert v["goals"][0] == {"text": "Niveau 30 pour tous · ~2 h 00", "pct": 35}
    assert v["goals"][1]["text"].startswith("Niveau 27") and v["leaderboard"] == [("Bravo", 30000)]   # 4.0: session XP (Group)


def test_tab_blocks_and_empty_states(snap: dict) -> None:
    for key, _t in pw.TABS:
        empty = pw.tab_blocks(key, {})
        assert [b["id"] for b in empty] == {"trajet": ["trajet.routes"], "erreurs": ["erreurs.file"]}.get(key, []), key
        blocks = pw.tab_blocks(key, snap)
        assert all(b["rows"] for b in blocks), key
        assert blocks or key == "donjon", key                          # no dungeon in the fixture
    hv = pw.tab_blocks("hv", snap)
    assert hv[0]["title"] == "Achat-revente" and hv[0]["rows"][0]["value"] == "+18 pa net"
    assert hv[0]["rows"][0]["spark"] and "commission 5 %" in hv[0]["foot"].lower()   # foot = a sentence (capitalised)
    t = {b["id"]: b for b in pw.tab_blocks("trajet", snap)}
    stops = t["trajet.stops"]["rows"]
    assert [r["num"] for r in stops] == ["1", "2", "3", "4"] and stops[0]["badge"] == "En cours"
    assert pw.tab_blocks("quetes", snap) == pw.tab_blocks("trajet", snap)     # 3.9 name


def test_read_state_with_hub(wowdir: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    from treeaicoach.wow import install

    inst = SimpleNamespace(flavor_dir=wowdir.flavor_dir, combat_log=wowdir.combat_log, chat_log=wowdir.chat_log,
                           addon_dir=wowdir.flavor_dir / "nope")
    monkeypatch.setattr(install, "find_wow", lambda *_a, **_k: inst)
    hub = live.LiveHub()
    st = pw.read_state(SimpleNamespace(wow_path=""), hub)
    assert st["live"]["char_key"] == "Testeur-Royaumetest" and st["rows"] and st["sv_age"] is not None
    assert {e.kind for e in st["events"]} >= {"savedvars", "auctionator"}
    assert pw.read_state(SimpleNamespace(wow_path=""), hub)["events"] == []


def test_live_toasts() -> None:
    ev = live.LiveEvent("chat", "", [{"kind": "level", "level": 27}, {"kind": "xp", "xp": 5}])
    assert pw.live_toasts([ev]) == ["Niveau 27 ! Bravo."]


# ------------------------------------------------------------------------------------------ page (Qt)
def test_page_tabs_render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, snap: dict) -> None:
    pytest.importorskip("PySide6")
    if str(TESTS) not in sys.path:
        sys.path.insert(0, str(TESTS))
    import test_launcher as tl

    from treeaicoach import ui as app_ui

    monkeypatch.setenv("TREEAICOACH_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(app_ui, "_report_function", lambda name: (lambda n=50: []) if name == "list_games" else None)
    app, _v, _e = tl._build(tmp_path, start_backend=False)
    try:
        app.show_page("wow")
        view = app.page_views["wow"]
        assert [k for k, _t in pw.TABS] == ["trajet", "groupe", "hv", "session", "donjon", "erreurs"]
        view.state = {"install": None, "live": {**snap, "sv_age": 17 * 60.0}, "sv_age": 17 * 60, "rows": [], "mode": {}}
        view._render()
        assert view.fresh.text().startswith("Données du /reload il y a")
        assert view.hero.title.text() == "Rends « Quête Une »"
        view.tabs.changed.emit("hv")
        assert view.current_tab() == view.visible_tab() == "hv"
        qt = pw._qt_classes()
        sparks = view.tab_views["hv"].findChildren(qt["Sparkline"])
        assert sparks and sparks[0].series and not sparks[0].pixmap().isNull()
        sparks[0].repaint()                                          # paint path runs offscreen (C++ only)
        view.tabs.changed.emit("groupe")
        from PySide6 import QtWidgets

        bars = [b for b in view.tab_views["groupe"].findChildren(QtWidgets.QProgressBar) if not b.isHidden()]
        assert sorted(b.value() for b in bars) == [40, 100]
        cards = [c for c in view.tab_views["groupe"].findChildren(qt["MemberCard"]) if not c.isHidden()]
        assert [c.name.text() for c in cards] == ["Testeur (toi)", "Alpha", "Bravo", "Charlie"]
        hv = view.tab_views["hv"]
        rows = list(hv.views["hv.deals"].rows)
        view._render()                                               # same data: nothing rebuilt
        assert hv.views["hv.deals"].rows == rows
        view.state = {"install": None, "live": {}, "rows": [], "mode": {}}
        view._render()
        view.tabs.changed.emit("hv")                                 # nothing for the HV: the empty state says why
        assert not hv.empty.isHidden() and "hôtel des ventes" in hv.empty.title.text()
        assert all(v.isHidden() for v in hv.views.values())
    finally:
        app.close()
