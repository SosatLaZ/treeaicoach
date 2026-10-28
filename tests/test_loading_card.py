"""loading_card.py + the loading-screen data of player_intel / champ_select (fake LCU JSON, no real ids)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from treeaicoach import champ_select, loading_card as lc, player_intel as pi

FX = Path(__file__).parent / "fixtures" / "player_intel"
NAMES = {86: ("Garen", "Garen"), 64: ("LeeSin", "Lee Sin"), 103: ("Ahri", "Ahri"), 222: ("Jinx", "Jinx"),
         117: ("Lulu", "Lulu"), 122: ("Darius", "Darius"), 11: ("MasterYi", "Maître Yi"), 7: ("Leblanc", "LeBlanc"),
         51: ("Caitlyn", "Caitlyn"), 412: ("Thresh", "Thresh"), 875: ("Sett", "Sett")}


def load(name: str):
    return json.loads((FX / name).read_text(encoding="utf-8"))


class SlowClient:
    """Answers like the League Client, each request taking ``latency`` seconds (thread-safe)."""

    def __init__(self, latency: float = 0.0, session: str = "gameflow_gamestart.json"):
        self.latency, self.session = latency, session
        self.paths: list[str] = []
        self.lock = threading.Lock()
        self.inflight = self.max_inflight = 0

    def get(self, path: str):
        with self.lock:
            self.paths.append(path)
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            if self.latency and path != pi.SESSION_PATH:
                time.sleep(self.latency)
            if path == pi.SESSION_PATH:
                return load(self.session)
            if path == pi.ME_PATH:
                return load("current_summoner.json")
            if path.startswith("/lol-ranked/"):
                return load("ranked_plat2.json")
            if "/matches?" in path:
                return load("history_otp_darius.json" if "puuid-e1" in path else "history_win_remake.json")
            if path.startswith("/lol-collections/"):
                return load("mastery_sett_main.json") if path.endswith("/1020/champion-mastery") else []
            return None
        finally:
            with self.lock:
                self.inflight -= 1


def test_endpoints_match_the_lcu_schema_paths():
    assert pi.MASTERY_PATH.startswith("/lol-collections/v1/inventories/{sid}/champion-mastery")
    assert pi.HISTORY_PATH.startswith("/lol-match-history/v1/products/lol/{puuid}/matches")
    assert pi.RANKED_PATH == "/lol-ranked/v1/ranked-stats/{puuid}" and champ_select.SESSION_PATH == "/lol-champ-select/v1/session"


def test_loading_screen_phase_is_public_champ_select_never():
    s = pi.parse_session(load("gameflow_gamestart.json"))
    assert s is not None and len(s["players"]) == 10 and s["summoner_ids"]["puuid-e1"] == 1020
    assert pi.parse_session(load("gameflow_champselect.json")) is None


def test_parse_recent_and_mastery():
    r = pi.parse_recent(load("history_otp_darius.json"), "puuid-e1", 122)
    assert r["recent_all"] == 20 and r["champ_all"] == 14
    assert r["mains"] == ((122, 14), (875, 4), (86, 2))
    assert r["last5"] == ((122, True), (122, True), (122, False), (122, True), (122, False))
    m = pi.parse_mastery(load("mastery_sett_main.json"), 122)
    assert m == {"mastery_mains": ((875, 412000), (122, 233000), (86, 41000)), "champ_points": 233000}
    assert pi.parse_mastery(load("mastery_sett_main.json"), 7)["champ_points"] == 0
    assert pi.parse_mastery([], 7) == {} and pi.parse_mastery({"x": 1}, 7) is None


def test_bans_from_champ_select_memory_only_champions():
    bans = champ_select.parse_bans(load("champselect_bans.json"))
    assert bans == frozenset({875, 238, 157})                     # the uncompleted ban action is not counted
    champ_select.remember_bans(bans, now=100.0)
    assert champ_select.last_bans(now=200.0) == bans
    assert champ_select.last_bans(now=100.0 + champ_select.BANS_KEEP_S + 1) == frozenset()
    assert champ_select.parse_bans(None) == frozenset()


def _parallel_watcher(client):
    w = pi.PlayerIntelWatcher(client, resolve=NAMES.get)
    w.want()
    return w


def test_parallel_fetch_fast_partial_then_complete():
    client = SlowClient(latency=0.1)
    w = _parallel_watcher(client)
    try:
        t0 = time.monotonic()
        partial = None
        while time.monotonic() - t0 < 5.0 and not w.ready():
            snap = w.snapshot()
            if partial is None and snap and any(p.ranked_loaded for p in snap) and not w.ready():
                partial = lc.from_watcher(w, frozenset())
            time.sleep(0.02)
        took = time.monotonic() - t0
        assert w.ready() and took < 3.0, took
        assert 2 <= client.max_inflight <= pi.WORKERS
        assert partial is not None and not partial.complete
        hist = [p for p in client.paths if "/matches?" in p]
        assert len(hist) == len(set(hist)) == 9                   # cached per player: one history each
        assert sum(p.startswith("/lol-collections/") for p in client.paths) == 5   # enemies only
        card = lc.from_watcher(w, frozenset({875}))
        assert card.complete and len(card.enemies) == 5 and len(card.allies) == 4
        darius = next(b for b in card.enemies if b.head.startswith("Darius"))
        assert "Platine 2 · 52 % (58 parties)" in darius.head
        text = "\n".join(darius.lines)
        assert "OTP (70 % de ses parties)" in text and "Mains : Darius 14, Sett 4, Garen 2" in text
        assert "5 dernières : Darius V · Darius V · Darius D · Darius V · Darius D" in text
        assert darius.alert == "Son main Sett a été banni"
        yi = next(b for b in card.enemies if b.head.startswith("Maître Yi"))
        assert not yi.alert
    finally:
        w.stop()


def test_card_matchups_win_condition_and_privacy():
    P = pi.PlayerIntel
    ps = [P("Darius", "Darius", False, "", True, "PLATINUM", 2, 0, 30, 28, champion_id=122),
          P("MasterYi", "Maître Yi", False, "", champion_id=11),
          P("Leblanc", "LeBlanc", False, "", champion_id=7), P("Caitlyn", "Caitlyn", False, "", champion_id=51),
          P("Thresh", "Thresh", False, "", champion_id=412),
          P("LeeSin", "Lee Sin", True, "JUNGLE"), P("Ahri", "Ahri", True, "MIDDLE"),
          P("Jinx", "Jinx", True, "BOTTOM"), P("Lulu", "Lulu", True, "UTILITY")]
    card = lc.build(ps, frozenset(), ("Garen", "Garen", "TOP"), resolve=NAMES.get)
    assert [" · ".join(b.head.split(" · ")[:2]) for b in card.enemies] == [
        "Darius · top", "Maître Yi · jungle", "LeBlanc · mid", "Caitlyn · tireur", "Thresh · support"]
    assert len(card.matchups) == 5 and card.matchups[0].startswith("Top (ta voie) · Garen vs Darius : ")
    assert all(" vs " in m for m in card.matchups) and card.win_condition
    assert any(b.lines == ("chargement…",) for b in card.enemies)              # partial display
    assert card.allies[0].startswith("Lee Sin (jungle) : ")
    extra = lc.discord_extra(card)
    assert "Top (ta voie)" in extra and "Plan : " in extra
    assert "puuid" not in json.dumps(card.lines())
    assert lc.build([], frozenset()) is None


def test_first_game_and_not_played():
    P = pi.PlayerIntel
    p = P("Leblanc", "LeBlanc", False, "MIDDLE", mastery_loaded=True, champ_points=0, champion_id=7)
    assert "1re partie sur ce champion" in lc.enemy_block(p, resolve=NAMES.get).lines[0]
    p = P("Leblanc", "LeBlanc", False, "MIDDLE", history_loaded=True, recent_all=12, champ_all=0, champion_id=7)
    assert "pas joué sur ses 12 dernières" in lc.enemy_block(p, resolve=NAMES.get).lines[0]


def test_should_show_window():
    assert lc.should_show(100.0, None, 120.0) and lc.should_show(100.0, 89.0, 200.0)
    assert not lc.should_show(100.0, 91.0, 200.0) and not lc.should_show(None, None, 0.0)
    assert not lc.should_show(100.0, None, 100.0 + lc.SHOW_MAX_S + 1)


@pytest.fixture()
def qapp():
    from PySide6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_overlay_renders_offscreen(qapp, tmp_path):
    P = pi.PlayerIntel
    ps = [P("Darius", "Darius", False, "TOP", True, "PLATINUM", 2, 0, 30, 28, True, 10, -3, 0, champion_id=122,
            recent_all=20, champ_all=14, last5=((122, True), (875, False)), mains=((122, 14), (875, 4))),
          P("Lulu", "Lulu", True, "UTILITY")]
    card = lc.build(ps, frozenset({875}), ("Garen", "Garen", "TOP"), True, resolve=NAMES.get)
    win = lc.make_window()
    win.set_card(card)
    win.show()
    qapp.processEvents()
    img = win.grab().toImage()
    assert img.width() == 560 and img.height() > 100
    assert "Son main Sett a été banni" in win.box.text()
    win.close()

    class W:
        def snapshot(self):
            return tuple(ps)

        def ready(self):
            return True

        def me_pick(self):
            return (86, "TOP")

        def game_since(self):
            return 0.0

    clock = [10.0]
    ctl = lc.LoadingController(clock=lambda: clock[0])
    assert ctl.tick(W(), None) is not None and ctl.visible
    assert ctl.tick(W(), 95.0) is not None and not ctl.visible        # hidden after 1:30 of game
    clock[0] = 5.0
    ctl.tick(W(), 10.0)
    assert ctl.visible
    ctl.hide()
