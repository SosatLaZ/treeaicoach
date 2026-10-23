"""player_intel.py: public rank / streaks of the current game's players (fake LCU JSON, no real ids)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from treeaicoach import player_intel as pi

FX = Path(__file__).parent / "fixtures" / "player_intel"
NAMES = {86: ("Garen", "Garen"), 64: ("LeeSin", "Lee Sin"), 103: ("Ahri", "Ahri"), 222: ("Jinx", "Jinx"),
         117: ("Lulu", "Lulu"), 122: ("Darius", "Darius"), 11: ("MasterYi", "Maître Yi"), 7: ("Leblanc", "LeBlanc"),
         51: ("Caitlyn", "Caitlyn"), 412: ("Thresh", "Thresh")}


def load(name: str):
    return json.loads((FX / name).read_text(encoding="utf-8"))


class FakeClient:
    def __init__(self, session="gameflow_session.json"):
        self.paths: list[str] = []
        self.session = session

    def get(self, path: str):
        self.paths.append(path)
        if path == pi.SESSION_PATH:
            return load(self.session)
        if path == pi.ME_PATH:
            return load("current_summoner.json")
        if path.startswith("/lol-ranked/"):
            return load("ranked_unranked.json" if "puuid-a" in path else "ranked_plat2.json")
        if "/matches?" in path:
            return load("history_loss4_other_champ.json" if "puuid-e2" in path else "history_win_remake.json")
        return None


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def watcher(client=None):
    c = Clock()
    w = pi.PlayerIntelWatcher(client or FakeClient(), resolve=NAMES.get, clock=c, sleep=c.sleep)
    return w, c


def run_all(w, n=60):
    for _ in range(n):
        if not w.step():
            break


def test_parse_session_only_in_progress():
    s = pi.parse_session(load("gameflow_session.json"))
    assert s["game_id"] == 7000000001 and s["queue_id"] == 420 and len(s["players"]) == 10
    assert pi.parse_session(load("gameflow_champselect.json")) is None     # never during champion select
    assert pi.parse_session(None) is None and pi.parse_session({"phase": "InProgress"}) is None


def test_parse_ranked_and_history():
    r = pi.parse_ranked(load("ranked_plat2.json"))
    assert r == {"tier": "PLATINUM", "division": 2, "lp": 45, "wins": 30, "losses": 28}
    assert pi.parse_ranked(load("ranked_plat2.json"), 440)["tier"] == "GOLD"       # flex game -> flex rank
    assert pi.parse_ranked(load("ranked_unranked.json"))["tier"] == ""
    h = pi.parse_history(load("history_loss4_other_champ.json"), "puuid-x", 11)
    assert h == {"games": 10, "streak": -4, "champ_games": 0, "champ_wins": 0}
    h = pi.parse_history(load("history_win_remake.json"), "puuid-x", 122)
    assert h["games"] == 9 and h["streak"] == 2 and h["champ_games"] == 7           # the remake is skipped
    assert pi.parse_history({"games": "x"}, "p", 1) is None


def test_watcher_fetches_rate_limited_and_cached():
    client = FakeClient()
    w, clock = watcher(client)
    t0 = clock.t
    run_all(w)
    assert w.ready()
    snap = w.snapshot()
    assert len(snap) == 9 and all(p.alias != "Garen" for p in snap)               # I am skipped
    assert sum(1 for p in snap if not p.ally) == 5
    # 1 session + 1 me + 9 ranked + 9 history, spaced by >= 1/3 s
    assert len(client.paths) == 20 and clock.t - t0 >= (len(client.paths) - 1) / pi.MAX_RPS - 1e-6
    assert not w.step()                                                          # cached: nothing to do
    clock.t += pi.SESSION_RECHECK_S + 1
    run_all(w)
    assert len(client.paths) == 21                                               # only the phase re-check


def test_watcher_forgets_when_game_ends():
    client = FakeClient()
    w, clock = watcher(client)
    run_all(w)
    client.session = "gameflow_champselect.json"
    clock.t += pi.SESSION_RECHECK_S + 1
    w.step()
    assert w.snapshot() == () and not w.ready()


def test_card_and_lines_by_champion_never_names():
    w, _c = watcher()
    run_all(w)
    w.set_roles({"Darius": "TOP", "MasterYi": "JUNGLE", "Leblanc": "MIDDLE", "Caitlyn": "BOTTOM", "Thresh": "UTILITY",
                 "LeeSin": "JUNGLE"})
    enemies, allies = pi.card(w.snapshot())
    assert enemies[0].startswith("Darius (top) · Platine 2 · 45 LP · 30 V 28 D (52 %) · 2 victoires d'affilée")
    assert "Maître Yi (jungler)" in enemies[1] and "4 défaites d'affilée" in enemies[1]
    assert "0 sur Maître Yi (10 dernières)" in enemies[1]
    assert allies[0].startswith("Lee Sin (jungler) · Non classé")
    lines = pi.decisive_lines(w.snapshot(), "TOP")
    assert lines == [("loss:MasterYi", "Leur jungler (Maître Yi) est en série de 4 défaites"),
                     ("new:Caitlyn", "Leur tireur n'a pas joué Caitlyn sur ses 9 dernières parties")]
    text = json.dumps([enemies, allies, lines])
    assert "puuid" not in text


def test_decisive_lines_cap_and_priority():
    P = pi.PlayerIntel
    ps = [P("A", "A", False, "MIDDLE", history_loaded=True, games=10, streak=-6),
          P("B", "B", False, "TOP", history_loaded=True, games=10, streak=5, champ_games=3),
          P("C", "C", False, "JUNGLE", history_loaded=True, games=9, streak=1, champ_games=0),
          P("D", "D", True, "TOP", history_loaded=True, games=10, streak=-9)]
    out = pi.decisive_lines(ps, "TOP")
    assert len(out) == pi.MAX_COACH_LINES
    assert out[0][0] == "win:B" and out[1][0] == "new:C"                 # lane opponent, then the jungler
    assert "Leur top (B) est en série de 5 victoires" in out[0][1]
    assert pi.decisive_lines([P("E", "E", False, "TOP", history_loaded=True, games=10, streak=-2, champ_games=4)]) == []


def _engine(gt, cfg_on=True):
    toasts = []
    me = SimpleNamespace(champion_alias="Garen", position="TOP")
    en = SimpleNamespace(champion_alias="MasterYi", position="JUNGLE")
    eng = SimpleNamespace(_cfg=SimpleNamespace(player_intel=cfg_on, lcu_enabled=True), _demo=False,
                          _game=SimpleNamespace(game_time=gt, me=me, allies=[], enemies=[en]),
                          _toast=lambda *a: toasts.append(a) or True)
    return eng, toasts


def test_engine_tick_off_by_default_and_capped():
    w, _c = watcher()
    w.want = lambda: None                     # no thread in the test
    run_all(w)
    eng, toasts = _engine(60.0, cfg_on=False)
    pi.engine_tick(eng, 1.0, w)
    assert toasts == []
    from treeaicoach.config import Config

    assert Config().player_intel is False
    eng, toasts = _engine(10.0)
    pi.engine_tick(eng, 1.0, w)
    assert toasts == []                        # too early
    for i, gt in enumerate((30.0, 31.0, 35.0, 45.0, 60.0, 90.0, 120.0)):
        eng._game.game_time = gt
        pi.engine_tick(eng, float(i), w)
    assert [x[4] for x in toasts] == ["intel:loss:MasterYi", "intel:new:Caitlyn"]    # 2 lines max, 10 s apart
    assert toasts[0][2] == "Leur jungler (Maître Yi) est en série de 4 défaites"


def test_engine_tick_never_raises():
    pi.engine_tick(None, 0.0)
    pi.engine_tick(SimpleNamespace(_cfg=SimpleNamespace(player_intel=True), _game=object()), 0.0)


def test_no_logging_of_ids_in_module():
    src = (Path(pi.__file__)).read_text(encoding="utf-8")
    for call in re.findall(r"log\.\w+\(([^)]*)\)", src):
        assert "puuid" not in call.lower() and "name" not in call.lower()


def test_fixtures_have_no_real_ids():
    for f in FX.glob("*.json"):
        t = f.read_text(encoding="utf-8")
        assert "#" not in t and "gameName" not in t and "summonerName" not in t
        assert set(re.findall(r'"puuid": "([^"]+)"', t)) <= {"puuid-me", "puuid-x"} | {f"puuid-{s}{i}" for s in "ae" for i in range(1, 6)}


def test_discord_ranks_message_and_voice_by_champion():
    P = pi.PlayerIntel
    ps = [P("LeeSin", "Lee Sin", False, "JUNGLE", True, "PLATINUM", 2, 40, 30, 28, True, 10, -4, 3),
          P("Darius", "Darius", False, "TOP", True, "GOLD", 1, 10, 5, 5, True, 10, 1, 0),
          P("Ahri", "Ahri", False, "MIDDLE", True, "", 0, 0, 0, 0, True, 10, 3, 4),
          P("Lulu", "Lulu", True, "UTILITY", True, "MASTER", 0, 120, 50, 40, True, 10, 0, 2)]
    text, voice = pi.discord_ranks(ps, "TOP")
    assert text.splitlines() == [
        "Rangs de la partie",
        "Ennemis : Darius Or 1, pas joué Darius récemment · Lee Sin Platine 2, 4 défaites d'affilée · "
        "Ahri non classé, 3 victoires d'affilée",
        "Alliés : Lulu Maître 120 LP"]
    assert voice == ["Leur top Darius : Or 1, pas joué Darius récemment",
                     "Leur jungler Lee Sin : Platine 2, 4 défaites d'affilée",
                     "Leur mid Ahri : non classé, 3 victoires d'affilée"]
    assert pi.discord_ranks([P("X", "X", False)]) == ("", [])


def test_ranks_tick_once_when_ready_only_with_the_switch():
    w, _c = watcher()
    w.want = lambda: None
    run_all(w)
    calls = []
    eng, _t = _engine(5.0)
    eng._call = lambda *a: calls.append(a) or True
    eng._cfg.discord_ranks = True
    for gt in (5.0, 6.0, 30.0):
        eng._game.game_time = gt
        pi.engine_tick(eng, gt, w)
    assert len(calls) == 1 and calls[0][0] == "ranks" and calls[0][1].startswith("Rangs de la partie")
    assert "puuid" not in json.dumps(calls[0][1:])
    eng2, _t = _engine(5.0)
    eng2._call = lambda *a: calls.append(a) or True
    eng2._cfg.discord_ranks = False
    pi.engine_tick(eng2, 5.0, w)
    assert len(calls) == 1
