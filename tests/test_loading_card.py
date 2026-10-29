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


def test_should_show_window_loading_screen_only():
    assert lc.should_show(100.0, None, 120.0) and lc.should_show(100.0, 0.0, 120.0)
    assert lc.should_show(100.0, 3.0, 120.0)                   # frozen 0:00 extrapolated by the engine
    assert not lc.should_show(100.0, 5.0, 120.0)               # the game started: gone
    assert not lc.should_show(None, None, 0.0)
    assert not lc.should_show(100.0, None, 100.0 + lc.SHOW_MAX_S + 1)
    assert lc.should_show(100.0, None, 120.0, started_at=110.0)
    assert not lc.should_show(100.0, None, 126.0, started_at=110.0)   # minimap seen: 15 s at most


def test_verdict_focus_overlay_and_z_order():
    v = lc.overlay_verdict
    assert v(since=0.0, now=1.0, focus=True) == "show"
    assert v(since=0.0, now=1.0, focus=False) == "hide"        # alt-tab / minimised: not over the client
    assert v(since=0.0, now=1.0, focus=None, win32=True) == "hide"   # no game window: the LoL client
    assert v(since=0.0, now=1.0, focus=None, win32=False) == "show"
    assert v(since=0.0, now=1.0, overlay_on=False) == "done"   # overlay hotkey
    assert v(since=0.0, now=1.0, has_card=False) == "hide"
    assert v(since=None, now=1.0) == "hide"


P = pi.PlayerIntel


def _ten(**darius):
    d = dict(champion_id=122, ranked_loaded=True, tier="PLATINUM", division=2, wins=30, losses=28)
    d.update(darius)
    ps = [P("Darius", "Darius", False, "", **d),
          P("MasterYi", "Maître Yi", False, "", champion_id=11),
          P("Leblanc", "LeBlanc", False, "", champion_id=7), P("Caitlyn", "Caitlyn", False, "", champion_id=51),
          P("Thresh", "Thresh", False, "", champion_id=412),
          P("LeeSin", "Lee Sin", True, "JUNGLE"), P("Ahri", "Ahri", True, "MIDDLE"),
          P("Jinx", "Jinx", True, "BOTTOM"), P("Lulu", "Lulu", True, "UTILITY")]
    return _slotted(ps)


def _slotted(ps):
    """Loading-screen order: my team (team 1, me on card 0) on top, the enemies (team 2) below."""
    from dataclasses import replace

    out, n = [], {True: 1, False: 0}
    for p in ps:
        out.append(replace(p, team=1 if p.ally else 2, slot=n[p.ally]))
        n[p.ally] += 1
    return out


class FakeW:
    def __init__(self, players, since=0.0, ready=True, me=(86, "TOP")):
        self.players, self.since, self._ready, self.me = tuple(players), since, ready, me

    def snapshot(self):
        return self.players

    def ready(self):
        return self._ready

    def me_pick(self):
        return self.me

    def me_slot(self):
        return (1, 0)

    def game_since(self):
        return self.since if self.players else None


class FakeWin:
    def __init__(self):
        self.shown, self.cards = False, []

    def set_card(self, c):
        self.cards.append(c)

    def show(self):
        self.shown = True

    def hide(self):
        self.shown = False

    def isVisible(self):
        return self.shown


def _ctl(clock, focus=lambda: True):
    win = FakeWin()
    return lc.LoadingController(window_factory=lambda: win, clock=lambda: clock[0], focus=focus, win32=True), win


def test_lifetime_gone_at_game_start_and_never_back():
    clock = [10.0]
    ctl, win = _ctl(clock)
    w = FakeW(_ten(), since=10.0)
    assert ctl.tick(w, None) is not None and win.shown
    clock[0] = 40.0
    ctl.tick(w, 2.5)                         # loading screen: frozen 0:00 + extrapolation
    assert win.shown
    ctl.tick(w, 6.0)                         # the game started
    assert not win.shown and ctl.reason == "done"
    assert ctl.tick(w, None) is not None and not win.shown    # Live Client gap: never re-appears
    w.players = ()
    ctl.tick(w, None)
    w.players = _ten()                                         # same game read again after a gap
    ctl.tick(w, None)
    assert not win.shown
    w.since = 500.0                                           # the next game: shown again
    clock[0] = 501.0
    ctl.tick(w, None)
    assert win.shown


def test_lifetime_minimap_started_15s_max_and_game_end():
    clock = [0.0]
    ctl, win = _ctl(clock)
    w = FakeW(_ten())
    ctl.tick(w, None, started=True)
    assert win.shown
    clock[0] = 16.0
    ctl.tick(w, None, started=True)
    assert not win.shown
    clock[0] = 0.0
    ctl2, win2 = _ctl(clock)
    w2 = FakeW(_ten())
    ctl2.tick(w2, None)
    assert win2.shown
    w2.players = ()                                           # the game ended (watcher reset)
    ctl2.tick(w2, None)
    assert not win2.shown
    clock[0] = lc.SHOW_MAX_S + 1                              # endless loading: capped
    w2.players = _ten()
    ctl2.tick(w2, None)
    assert not win2.shown


def test_overlay_hotkey_and_focus():
    clock, front = [0.0], [True]
    ctl, win = _ctl(clock, focus=lambda: front[0])
    w = FakeW(_ten())
    ctl.tick(w, None)
    assert win.shown
    front[0] = False                                          # alt-tab / minimised
    ctl.tick(w, None)
    assert not win.shown
    front[0] = True
    ctl.tick(w, None)
    assert win.shown                                          # back in front during the loading screen
    ctl.tick(w, None, overlay=False)                          # overlay hotkey: gone for this game
    ctl.tick(w, None, overlay=True)
    assert not win.shown
    ctl2, win2 = _ctl(clock, focus=lambda: None)              # Windows without a game window (LoL client)
    ctl2.tick(FakeW(_ten()), None)
    assert not win2.shown


def test_failed_requests_never_stay_loading():
    ps = _ten(ranked_loaded=False)
    card = lc.build(ps, frozenset(), ("Garen", "Garen", "TOP"), complete=False, resolve=NAMES.get)
    yi = next(r for r in card.enemy_rows if r.alias == "MasterYi")
    assert yi.state == "loading" and "chargement…" in card.enemies[1].lines
    card = lc.build(ps, frozenset(), ("Garen", "Garen", "TOP"), complete=True, resolve=NAMES.get)
    yi = next(r for r in card.enemy_rows if r.alias == "MasterYi")
    assert yi.state == "none" and card.enemies[1].lines == (lc.NO_INFO,)
    assert all("chargement" not in a for a in card.allies)


def test_teams_duplicates_and_lanes():
    ps = _ten()
    ps.append(P("Garen", "Garen", True, "TOP"))              # me, my puuid unknown: not twice
    ps.append(P("Darius", "Darius", False, ""))              # the same enemy twice
    card = lc.build(ps, frozenset(), ("Garen", "Garen", "TOP"), True, resolve=NAMES.get)
    assert len(card.enemy_rows) == 5 and len(card.ally_rows) == 5
    assert [r.alias for r in card.ally_rows] == ["Garen", "LeeSin", "Ahri", "Jinx", "Lulu"]
    assert card.ally_rows[0].tag == "Toi" and [r.lane for r in card.enemy_rows] == [
        "Top", "Jungle", "Mid", "Tireur", "Support"]
    assert len(card.duels) == 5 and sum("Garen vs Darius" in m for m in card.matchups) == 1
    # my team unknown: nine "enemies" -> no overlay, never nine enemy rows
    allen = [P(p.alias, p.name, False, p.role, champion_id=p.champion_id) for p in _ten()]
    card = lc.build(allen, frozenset(), None, True, resolve=NAMES.get)
    assert not card.teams_known and not card.enemy_rows
    clock = [0.0]
    ctl, win = _ctl(clock)
    w = FakeW(allen, me=(0, ""))
    import unittest.mock as um
    with um.patch.object(lc, "_resolver", lambda: NAMES.get):
        ctl.tick(w, None)
    assert not win.shown
    # two enemies on one lane: one only
    dup = _ten()
    dup[0] = P("Darius", "Darius", False, "TOP", champion_id=122)
    dup[1] = P("MasterYi", "Maître Yi", False, "TOP", champion_id=11)
    card = lc.build(dup, frozenset(), None, True, resolve=NAMES.get)
    assert [r.lane for r in card.enemy_rows].count("Top") == 1


def test_tags_short_and_bans_of_this_game_only(monkeypatch):
    p = P("Darius", "Darius", False, "TOP", True, "PLATINUM", 2, 0, 30, 28, True, 10, -3, champion_id=122,
          recent_all=20, champ_all=14, mains=((122, 14), (875, 4)))
    assert lc.player_row(p, frozenset({875}), NAMES.get).tag == "Main banni"
    assert lc.player_row(p, frozenset(), NAMES.get).tag == "OTP"
    q = P("Leblanc", "LeBlanc", False, "MIDDLE", mastery_loaded=True, champ_points=0, champion_id=7)
    assert lc.player_row(q, resolve=NAMES.get).tag == "1re partie"
    r = P("Lulu", "Lulu", True, "UTILITY", history_loaded=True, streak=-3, recent_all=10, champ_all=3)
    row = lc.player_row(r, resolve=NAMES.get)
    assert row.tag == "3 défaites" and row.tone == ""
    for row in lc.build(_ten(), frozenset(), ("Garen", "Garen", "TOP"), True, resolve=NAMES.get).enemy_rows:
        assert len(row.tag) <= 12 and row.tag == row.tag[:1] + row.tag[1:]   # short, not shouting
    monkeypatch.setattr(champ_select, "_bans", (100.0, frozenset({875})))
    assert lc.game_bans(130.0) == {875}
    assert lc.game_bans(100.0 + lc.BANS_MAX_AGE_S + 60) == frozenset()       # previous game's bans
    assert lc.game_bans(None) == frozenset()


@pytest.fixture()
def qapp():
    from PySide6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _full_card():
    ps = [P("Darius", "Darius", False, "TOP", True, "PLATINUM", 2, 0, 30, 28, True, 10, -3, 0, champion_id=122,
            recent_all=20, champ_all=14, last5=((122, True), (875, False)), mains=((122, 14), (875, 4))),
          P("MasterYi", "Maître Yi", False, "JUNGLE", True, "GRANDMASTER", 0, 412, 120, 98, champion_id=11,
            history_loaded=True, streak=4, recent_all=20, champ_all=5),
          P("Leblanc", "LeBlanc", False, "MIDDLE", True, "", champion_id=7, mastery_loaded=True, champ_points=0),
          P("Caitlyn", "Caitlyn", False, "BOTTOM", True, "EMERALD", 4, 10, 12, 10, champion_id=51),
          P("Thresh", "Thresh", False, "UTILITY", champion_id=412),
          P("LeeSin", "Lee Sin", True, "JUNGLE", True, "GOLD", 1, 0, 20, 22, champion_id=64),
          P("Ahri", "Ahri", True, "MIDDLE", True, "SILVER", 3, 0, 8, 6, champion_id=103,
            history_loaded=True, streak=-3, recent_all=10, champ_all=2),
          P("Jinx", "Jinx", True, "BOTTOM", True, "PLATINUM", 4, 0, 5, 9, champion_id=222),
          P("Lulu", "Lulu", True, "UTILITY", champion_id=117)]
    return lc.build(_slotted(ps), frozenset({875}), ("Garen", "Garen", "TOP"), False, resolve=NAMES.get,
                    me_slot=(1, 0))


def test_badges_by_session_order_and_never_a_wrong_card():
    card = _full_card()
    keys = [(b.team, b.slot) for b in card.badges]
    assert keys == [(1, 1), (1, 2), (1, 3), (1, 4), (2, 0), (2, 1), (2, 2), (2, 3), (2, 4)]   # me (1, 0): none
    d = next(b for b in card.badges if b.alias == "Darius")
    assert d.rank == "P2 · 52 %" and d.tag == "main banni" and d.verdict.startswith("Darius gagne")
    yi = next(b for b in card.badges if b.alias == "MasterYi")
    assert yi.rank.startswith("GM 412 LP") and yi.tag == "série de 4 victoires" and not yi.verdict
    assert next(b for b in card.badges if b.alias == "Leblanc").tag == "1re partie"
    assert next(b for b in card.badges if b.alias == "Leblanc").rank == "non classé"
    for b in card.badges:                                          # DESIGN.md: no caps, no emoji
        assert b.tag in ("", "OTP") or b.tag == b.tag.lower()
    from dataclasses import replace

    ps = _ten()
    assert lc.build(ps, me=("Garen", "Garen", "TOP"), resolve=NAMES.get, me_slot=(1, 0)).badges
    bad = [replace(p, slot=1) if p.alias == "Ahri" else p for p in ps]          # two players on one card
    assert lc.build(bad, me=("Garen", "Garen", "TOP"), resolve=NAMES.get, me_slot=(1, 0)).badges == ()
    bad = [replace(p, team=2) if p.alias == "Ahri" else p for p in ps]          # an ally on the enemy row
    assert lc.build(bad, me=("Garen", "Garen", "TOP"), resolve=NAMES.get, me_slot=(1, 0)).badges == ()
    bad = [replace(p, slot=-1) if p.alias == "Thresh" else p for p in ps]       # unknown card
    assert lc.build(bad, me=("Garen", "Garen", "TOP"), resolve=NAMES.get, me_slot=(1, 0)).badges == ()
    assert lc.build(ps, me=("Garen", "Garen", "TOP"), resolve=NAMES.get, me_slot=(1, 1)).badges == ()


def test_session_slots_follow_team_lists():
    s = {"phase": "GameStart", "gameData": {"gameId": 5, "queue": {"id": 420}, "teamOne": [
        {"puuid": "a", "championId": 86}, {"puuid": "0-0", "championId": 1}, {"puuid": "b", "championId": 64}],
        "teamTwo": [{"puuid": "c", "championId": 122}]}}
    info = pi.parse_session(s)
    assert info["slots"] == {"a": (1, 0), "b": (1, 2), "c": (2, 0)}       # a hidden entry keeps its card


def test_card_rects_scale_and_centre():
    for w, h in ((1920, 1080), (1280, 720), (2000, 1125), (1920, 1200), (3440, 1440), (2560, 1080)):
        r = lc.card_rects(w, h)
        xs = [r[(1, i)][0] for i in range(5)]
        left, right = xs[0], xs[4] + r[(1, 4)][2]
        assert abs((left + right) / 2 - w / 2) < 1 and left > 0 and right < w
        assert r[(2, 0)][1] > r[(1, 0)][1] + r[(1, 0)][3] and r[(2, 0)][1] + r[(2, 0)][3] <= h
        assert r[(1, 0)][1] >= 0
    a, b = lc.card_rects(1920, 1080)[(1, 2)], lc.card_rects(1280, 720)[(1, 2)]
    assert abs(a[0] * 720 / 1080 - b[0]) < 1 and abs(a[2] * 720 / 1080 - b[2]) < 1


def test_window_flags_full_screen_and_no_flicker(qapp):
    from PySide6 import QtCore

    card = _full_card()
    win = lc.make_window()
    Qt = QtCore.Qt
    win.place(QtCore.QRect(0, 0, 1280, 720))
    assert (win.width(), win.height()) == (1280, 720)
    f = win.windowFlags()
    for flag in (Qt.WindowType.WindowTransparentForInput, Qt.WindowType.WindowDoesNotAcceptFocus,
                 Qt.WindowType.WindowStaysOnTopHint, Qt.WindowType.Tool):
        assert f & flag
    assert win.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert win.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    win.set_card(card)
    win.show()
    qapp.processEvents()
    assert win.card is card
    win.set_card(_full_card())                            # equal data: no repaint
    assert win.card is card
    img = win.grab().toImage()
    corner = img.pixelColor(2, 2)
    assert corner.alpha() == 0                            # nothing but the badges is drawn
    win.close()


def _mock_loading_screen(w, h):
    """A stand-in loading screen: dark map backdrop + the 10 card frames where League draws them."""
    from PySide6 import QtCore, QtGui

    img = QtGui.QImage(w, h, QtGui.QImage.Format.Format_ARGB32)
    img.fill(QtGui.QColor("#10161c"))
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    hues = [20, 200, 120, 280, 40, 340, 180, 60, 240, 300]
    for i, ((team, slot), (x, y, cw, ch)) in enumerate(sorted(lc.card_rects(w, h).items())):
        g = QtGui.QLinearGradient(x, y, x, y + ch)
        g.setColorAt(0, QtGui.QColor.fromHsv(hues[i], 90, 150))
        g.setColorAt(1, QtGui.QColor.fromHsv(hues[i], 120, 60))
        p.fillRect(QtCore.QRectF(x, y, cw, ch), g)
        p.fillRect(QtCore.QRectF(x, y + ch * 0.80, cw, ch * 0.20), QtGui.QColor("#0b0f14"))
        p.setPen(QtGui.QPen(QtGui.QColor("#a08850" if team == 1 else "#8a3a3a"), max(2, h / 400)))
        p.drawRect(QtCore.QRectF(x, y, cw, ch))
        p.setPen(QtGui.QColor("#d8d0b0"))
        f = p.font()
        f.setPixelSize(max(9, round(h / 70)))
        p.setFont(f)
        p.drawText(QtCore.QRectF(x, y + ch * 0.81, cw, ch * 0.08), int(QtCore.Qt.AlignmentFlag.AlignCenter),
                   f"carte {team}-{slot + 1}")
    p.end()
    return img


def test_offscreen_screenshots(qapp, tmp_path):
    import os

    from PySide6 import QtCore, QtGui

    card = _full_card()
    out = Path(os.environ.get("TREEAI_LC_SHOTS", "") or tmp_path)
    out.mkdir(parents=True, exist_ok=True)
    for w, h in ((1280, 720), (1920, 1080), (2000, 1125), (1920, 1200)):
        win = lc.make_window()
        win.place(QtCore.QRect(0, 0, w, h))
        win.set_card(card)
        win.show()
        qapp.processEvents()
        shot = _mock_loading_screen(w, h)
        p = QtGui.QPainter(shot)
        p.drawImage(0, 0, win.grab().toImage())
        p.end()
        assert shot.save(str(out / f"loading_badges_{w}x{h}.png"))
        win.close()
