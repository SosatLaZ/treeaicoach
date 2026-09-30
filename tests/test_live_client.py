"""Tests for treeaicoach.live_client (pure parsing + HTTP client against local servers)."""

from __future__ import annotations

import copy
import http.server
import json
import logging
import math
import random
import shutil
import socket
import ssl
import subprocess
import threading
import time
from pathlib import Path

import pytest

from treeaicoach.live_client import (
    LIVE_URL,
    GameInfo,
    LiveClient,
    PlayerInfo,
    parse_allgamedata,
)

FIXTURE = Path(__file__).parent / "fixtures" / "allgamedata_sample.json"


@pytest.fixture()
def sample() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _player(data: dict, alias: str) -> dict:
    for p in data["allPlayers"]:
        if p["rawChampionName"].endswith("_" + alias):
            return p
    raise KeyError(alias)


def _spell(raw_key: str, display: str) -> dict:
    return {"displayName": display,
            "rawDescription": f"GeneratedTip_SummonerSpell_{raw_key}_Description",
            "rawDisplayName": f"GeneratedTip_SummonerSpell_{raw_key}_DisplayName"}


# ------------------------------------------------------------------------------ parsing
def test_parse_sample(sample):
    g = parse_allgamedata(sample, now=123.0)
    assert isinstance(g, GameInfo)
    assert g.fetched_at == 123.0
    assert g.game_time == pytest.approx(754.3125)
    assert g.game_mode == "CLASSIC"
    assert g.map_number == 11 and g.is_summoners_rift
    assert g.map_terrain == "Infernal"
    assert g.team_relative_colors is True
    assert len(g.events) == 7 and g.events[0]["EventName"] == "GameStart"
    me = g.me
    assert me is not None and not g.is_spectator
    assert (me.riot_id, me.champion_alias, me.team, me.position) == ("Sylvain#EUW", "Garen", "ORDER", "TOP")
    assert me.skin_id == 22 and me.level == 9 and not me.has_smite
    assert me.spells == ("Flash", "Téléportation")
    assert g.my_team == "ORDER" and g.enemy_team == "CHAOS"
    assert [p.champion_alias for p in g.allies] == ["MonkeyKing", "Ahri", "Kaisa", "Nunu"]
    assert [p.champion_alias for p in g.enemies] == ["Darius", "LeeSin", "KSante", "Jinx", "Thresh"]
    assert all(p.team == "ORDER" for p in g.allies) and all(p.team == "CHAOS" for p in g.enemies)
    wukong = g.allies[0]
    assert wukong.champion_name == "Wukong" and wukong.has_smite and wukong.position == "JUNGLE"
    lee = g.enemies[1]
    assert lee.has_smite and lee.is_dead and lee.respawn_timer == pytest.approx(17.5) and lee.skin_id == 11
    assert g.enemies[2].champion_name == "K'Santé"
    assert g.enemy_jungler() is lee
    assert len(g.all_players()) == 10 and g.all_players()[0] is me
    assert g.player_by_alias("monkeyking") is wukong
    assert g.player_by_alias("game_character_displayname_LeeSin") is lee
    assert g.player_by_alias("Wukong") is wukong          # localized name
    assert g.player_by_alias("Zed") is None and g.player_by_alias("") is None


def test_parse_is_pure(sample):
    before = copy.deepcopy(sample)
    parse_allgamedata(sample)
    assert sample == before


@pytest.mark.parametrize("payload", [
    {"errorCode": "RESOURCE_NOT_FOUND", "httpStatus": 404, "implementationDetails": {},
     "message": "Invalid URI format"},
    {"errorCode": "RPC_ERROR", "httpStatus": 503, "message": "Game is loading"},
    {},
    None,
    [],
    "not a dict",
    42,
])
def test_error_payloads_return_none(payload):
    assert parse_allgamedata(payload) is None  # type: ignore[arg-type]


def test_loading_screen_payloads(sample):
    for mutate in (
        lambda d: d.pop("gameData"),
        lambda d: d.__setitem__("gameData", {}),
        lambda d: d.__setitem__("gameData", "x"),
        lambda d: d.pop("allPlayers"),
        lambda d: d.__setitem__("allPlayers", []),
        lambda d: d.__setitem__("allPlayers", [None, 3, "x"]),
        lambda d: d.__setitem__("allPlayers", [{"championName": "Ahri"}]),   # no team
    ):
        d = copy.deepcopy(sample)
        mutate(d)
        assert parse_allgamedata(d) is None


@pytest.mark.parametrize("active", [
    {"error": "Spectator mode doesn't currently support this feature"},
    None,
    {},
    "garbage",
    {"riotId": "Nobody#0000", "summonerName": "Nobody#0000", "riotIdGameName": "Nobody"},
])
def test_spectator_or_unknown_active_player(sample, active):
    if active is None:
        sample.pop("activePlayer")
    else:
        sample["activePlayer"] = active
    g = parse_allgamedata(sample)
    assert g is not None and g.me is None and g.is_spectator and g.my_team is None
    assert [p.team for p in g.allies] == ["ORDER"] * 5
    assert [p.team for p in g.enemies] == ["CHAOS"] * 5
    assert g.team_relative_colors is True
    assert g.enemy_jungler().champion_alias == "LeeSin"


def test_red_side_player(sample):
    darius = _player(sample, "Darius")
    for k in ("riotId", "riotIdGameName", "riotIdTagLine", "summonerName"):
        sample["activePlayer"][k] = darius[k]
    g = parse_allgamedata(sample)
    assert g.me.champion_alias == "Darius" and g.my_team == "CHAOS" and g.enemy_team == "ORDER"
    assert {p.team for p in g.allies} == {"CHAOS"} and len(g.allies) == 4
    assert {p.team for p in g.enemies} == {"ORDER"} and len(g.enemies) == 5
    assert g.enemy_jungler().champion_alias == "MonkeyKing"


def test_me_matching_fallbacks(sample):
    # 1) riotId, case / unicode-insensitive
    d = copy.deepcopy(sample)
    d["activePlayer"]["riotId"] = "  sylvain#euw "
    assert parse_allgamedata(d).me.champion_alias == "Garen"
    # 2) no riotId anywhere -> summonerName
    d = copy.deepcopy(sample)
    for p in [d["activePlayer"], *d["allPlayers"]]:
        for k in ("riotId", "riotIdTagLine"):
            p.pop(k, None)
    d["activePlayer"]["summonerName"] = _player(d, "Ahri")["summonerName"]
    assert parse_allgamedata(d).me.champion_alias == "Ahri"
    # 3) only riotIdGameName
    d = copy.deepcopy(sample)
    d["activePlayer"] = {"riotIdGameName": "LeBûcheron", "teamRelativeColors": False}
    g = parse_allgamedata(d)
    assert g.me.champion_alias == "MonkeyKing" and g.team_relative_colors is False
    # 3b) Unicode normalization (decomposed accents, non-breaking space)
    import unicodedata
    d = copy.deepcopy(sample)
    d["activePlayer"] = {"riotId": unicodedata.normalize("NFD", "LeBûcheron#1234")}
    assert parse_allgamedata(d).me.champion_alias == "MonkeyKing"
    d["activePlayer"] = {"riotId": "Mid\u00a0Ahri#FR1"}
    assert parse_allgamedata(d).me.champion_alias == "Ahri"
    # 4) legacy summoner name without tag vs "Name#TAG" in allPlayers
    d = copy.deepcopy(sample)
    d["activePlayer"] = {"summonerName": "Tank Mid"}
    assert parse_allgamedata(d).me.champion_alias == "KSante"


def test_ambiguous_game_name_does_not_pick_a_random_player(sample):
    other = _player(sample, "Darius")
    other["riotIdGameName"] = "Sylvain"
    other["riotId"] = other["summonerName"] = "Sylvain#FR2"
    d = copy.deepcopy(sample)
    d["activePlayer"] = {"riotIdGameName": "Sylvain"}
    assert parse_allgamedata(d).me is None            # ambiguous -> spectator-like split
    d["activePlayer"] = {"riotId": "Sylvain#EUW", "riotIdGameName": "Sylvain"}
    assert parse_allgamedata(d).me.champion_alias == "Garen"


def test_minimal_player_fields_and_defaults():
    data = {
        "gameData": {"gameTime": "12.5"},
        "allPlayers": [
            {"team": "ORDER", "rawChampionName": "game_character_displayname_Ahri"},
            {"team": "chaos", "championName": "Wukong"},
            {"team": "CHAOS", "championName": "Totally New Champ"},
        ],
    }
    g = parse_allgamedata(data)
    assert g is not None and g.me is None
    assert g.game_time == 12.5 and g.game_mode == "" and g.map_number == 0 and not g.is_summoners_rift
    assert g.map_terrain == "Default" and g.team_relative_colors is True and g.events == []
    ahri = g.allies[0]
    assert (ahri.champion_alias, ahri.position, ahri.level, ahri.skin_id) == ("Ahri", "", 1, 0)
    assert not ahri.is_dead and not ahri.has_smite and not ahri.is_bot and ahri.riot_id == ""
    assert [p.champion_alias for p in g.enemies] == ["MonkeyKing", "TotallyNewChamp"]
    assert g.enemy_jungler() is None


def test_bad_field_values_are_sanitized(sample):
    p = _player(sample, "Jinx")
    p.update({"level": "x", "skinID": None, "respawnTimer": -5, "isDead": "true", "isBot": 1,
              "position": "bot", "summonerSpells": "garbage", "items": None, "scores": 3})
    sample["gameData"].update({"gameTime": float("nan"), "mapNumber": None, "mapName": "Map12",
                               "mapTerrain": None})
    sample["events"] = {"Events": [{"EventName": "GameStart"}, None, 5, "x"]}
    g = parse_allgamedata(sample)
    jinx = g.player_by_alias("Jinx")
    assert (jinx.level, jinx.skin_id, jinx.respawn_timer) == (1, 0, 0.0)
    assert jinx.is_dead is True and jinx.is_bot is True and jinx.position == "BOTTOM"
    assert jinx.has_smite is False and jinx.spells == ()
    assert g.game_time == 0.0 and g.map_number == 12 and not g.is_summoners_rift
    assert g.map_terrain == "Default"
    assert g.events == [{"EventName": "GameStart"}]


def test_events_missing_or_malformed(sample):
    for ev in (None, [], "x", {"Events": None}, {"Events": {"a": 1}}):
        d = copy.deepcopy(sample)
        d["events"] = ev
        assert parse_allgamedata(d).events == []


@pytest.mark.parametrize("spell,expected", [
    (_spell("SummonerSmite", "Châtiment"), True),
    (_spell("S5_SummonerSmitePlayerGanker", "Châtiment défiant"), True),
    (_spell("S5_SummonerSmiteDuel", "Châtiment provocateur"), True),
    (_spell("SummonerSmiteAvatarOffensive", "Châtiment déchaîné"), True),
    (_spell("SummonerSmiteAvatarUtility", "Châtiment primordial"), True),
    (_spell("SummonerSmiteAvatarDefensive", "Zerschmettern"), True),
    ({"displayName": "Smite"}, True),
    ({"displayName": "Châtiment"}, True),
    ({"displayName": "CHATIMENT"}, True),
    ({"rawDisplayName": "generatedtip_summonerspell_summonersmite_displayname"}, True),
    (_spell("SummonerFlash", "Flash"), False),
    (_spell("SummonerTeleport", "Téléportation"), False),
    ({"displayName": ""}, False),
    ({}, False),
    (None, False),
])
def test_has_smite_detection(sample, spell, expected):
    p = _player(sample, "Darius")
    p["summonerSpells"] = {"summonerSpellOne": _spell("SummonerFlash", "Flash"), "summonerSpellTwo": spell}
    g = parse_allgamedata(sample)
    assert g.player_by_alias("Darius").has_smite is expected


def test_enemy_jungler_rules():
    def pl(alias, pos, smite):
        return PlayerInfo(champion_alias=alias, team="CHAOS", position=pos, has_smite=smite)

    g = GameInfo(enemies=[pl("Darius", "TOP", True), pl("LeeSin", "JUNGLE", True)])
    assert g.enemy_jungler().champion_alias == "LeeSin"          # several smites -> JUNGLE one
    g = GameInfo(enemies=[pl("Darius", "TOP", True), pl("LeeSin", "JUNGLE", False)])
    assert g.enemy_jungler().champion_alias == "Darius"          # smite first
    g = GameInfo(enemies=[pl("Darius", "TOP", False), pl("LeeSin", "JUNGLE", False)])
    assert g.enemy_jungler().champion_alias == "LeeSin"          # then position
    g = GameInfo(enemies=[pl("Darius", "TOP", False)])
    assert g.enemy_jungler() is None
    assert GameInfo().enemy_jungler() is None and GameInfo().all_players() == []


def test_fuzzed_payloads_never_raise(sample, caplog):
    caplog.set_level(logging.ERROR, logger="treeaicoach.live_client")
    rng = random.Random(1234)
    junk = [None, "", "x", 0, -1, 1e308, float("nan"), True, [], {}, [None], {"a": None}]

    def mutate(obj, depth=0):
        if isinstance(obj, dict) and obj:
            k = rng.choice(list(obj))
            if rng.random() < 0.5 or depth > 4:
                obj[k] = rng.choice(junk)
            else:
                mutate(obj[k], depth + 1)
        elif isinstance(obj, list) and obj:
            i = rng.randrange(len(obj))
            if rng.random() < 0.5 or depth > 4:
                obj[i] = rng.choice(junk)
            else:
                mutate(obj[i], depth + 1)

    for _ in range(300):
        d = copy.deepcopy(sample)
        for _ in range(rng.randint(1, 6)):
            mutate(d)
        g = parse_allgamedata(d)
        assert g is None or isinstance(g, GameInfo)
        if g is not None:
            assert math.isfinite(g.game_time)
            g.enemy_jungler()
    # nothing was swallowed by the catch-all of parse_allgamedata
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


# ------------------------------------------------------------------------------ HTTP client
class _Handler(http.server.BaseHTTPRequestHandler):
    body: bytes = b""

    def log_message(self, *args) -> None:  # silence test output
        pass

    def _send(self, code: int, payload: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/liveclientdata/allgamedata":
            self._send(200, self.body)
        elif path == "/error":
            self._send(404, json.dumps({"errorCode": "RESOURCE_NOT_FOUND", "httpStatus": 404,
                                        "implementationDetails": {}, "message": "Invalid URI"}).encode())
        elif path == "/error200":
            self._send(200, json.dumps({"errorCode": "RESOURCE_NOT_FOUND", "httpStatus": 404}).encode())
        elif path == "/garbage":
            self._send(200, b"<html>not json</html>", "text/html")
        elif path == "/list":
            self._send(200, b"[1, 2, 3]")
        elif path == "/empty":
            self._send(200, b"")
        elif path == "/slow":
            time.sleep(2.0)
            try:
                self._send(200, self.body)
            except OSError:
                pass
        else:
            self._send(500, b"oops", "text/plain")


def _serve(tls_context: ssl.SSLContext | None = None):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    if tls_context is not None:
        server.socket = tls_context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    return server, thread


@pytest.fixture()
def http_server():
    _Handler.body = FIXTURE.read_bytes()
    server, thread = _serve()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join(5)


def _closed_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_fetch_from_local_http_server(http_server):
    client = LiveClient(http_server + "/liveclientdata/allgamedata", timeout=2.0)
    t0 = time.monotonic()
    g = client.fetch()
    assert isinstance(g, GameInfo)
    assert g.me.champion_alias == "Garen" and len(g.enemies) == 5
    assert t0 <= g.fetched_at <= time.monotonic()
    assert client.last_error == "" and client.last_ok is not None


@pytest.mark.parametrize("path", ["/error", "/error200", "/garbage", "/list", "/empty", "/nope"])
def test_fetch_bad_responses_return_none(http_server, path):
    client = LiveClient(http_server + path, timeout=2.0)
    assert client.fetch() is None
    if path != "/error200":
        assert client.last_error


def test_fetch_timeout(http_server):
    client = LiveClient(http_server + "/slow", timeout=0.3)
    t0 = time.monotonic()
    assert client.fetch() is None
    assert time.monotonic() - t0 < 1.8


def test_fetch_unreachable_and_invalid_urls():
    for url in (f"http://127.0.0.1:{_closed_port()}/liveclientdata/allgamedata",
                f"https://127.0.0.1:{_closed_port()}/liveclientdata/allgamedata",
                "ftp://127.0.0.1/x", "not a url", "http://[::1"):
        client = LiveClient(url, timeout=0.5)
        t0 = time.monotonic()
        assert client.fetch() is None
        assert client.fetch_raw() is None
        assert time.monotonic() - t0 < 3.0
        assert client.last_error


def test_system_proxies_are_ignored(http_server, monkeypatch):
    bogus = f"http://127.0.0.1:{_closed_port()}"
    for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(k, bogus)
    for k in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(k, raising=False)
    client = LiveClient(http_server + "/liveclientdata/allgamedata", timeout=2.0)
    assert client.fetch() is not None


def test_tls_verification_disabled_only_for_loopback():
    local = LiveClient()
    assert local.url == LIVE_URL
    assert local._ssl_context is not None
    assert local._ssl_context.verify_mode == ssl.CERT_NONE and not local._ssl_context.check_hostname
    remote = LiveClient("https://example.com/liveclientdata/allgamedata")
    assert remote._ssl_context.verify_mode == ssl.CERT_REQUIRED and remote._ssl_context.check_hostname
    assert LiveClient("http://127.0.0.1:1/x")._ssl_context is None


def test_default_client_without_game_returns_none():
    result = LiveClient(timeout=0.5).fetch()     # no game running on the test machine
    assert result is None or isinstance(result, GameInfo)


def test_fetch_over_https_with_self_signed_certificate(tmp_path):
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("openssl not available to create a throwaway certificate")
    key, cert = tmp_path / "key.pem", tmp_path / "cert.pem"
    try:
        subprocess.run([openssl, "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                        "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "2", "-subj", "/CN=riotgames"],
                       check=True, capture_output=True, timeout=30)
    except Exception as exc:  # pragma: no cover - depends on the local openssl
        pytest.skip(f"cannot create certificate: {exc}")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(key))
    _Handler.body = FIXTURE.read_bytes()
    server, thread = _serve(ctx)
    try:
        port = server.server_address[1]
        g = LiveClient(f"https://127.0.0.1:{port}/liveclientdata/allgamedata", timeout=2.0).fetch()
        assert g is not None and g.me.champion_alias == "Garen"
        g2 = LiveClient(f"https://localhost:{port}/liveclientdata/allgamedata", timeout=2.0).fetch()
        assert g2 is None or g2.me.champion_alias == "Garen"   # localhost may resolve to ::1 only
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


# ------------------------------------------------------------------------------ v1.1 fields (§6.3)
def test_gold_items_and_scores(sample):
    g = parse_allgamedata(sample)
    assert g.current_gold == pytest.approx(1234.56) and g.me.current_gold == pytest.approx(1234.56)
    assert g.items == [1055, 3071, 3047, 3340] and g.me.items == g.items
    assert g.scores == {"kills": 2, "deaths": 1, "assists": 1, "creepScore": 96, "wardScore": pytest.approx(4.21)}
    assert (g.me.kills, g.me.deaths, g.me.assists, g.me.creep_score) == (2, 1, 1, 96)
    assert g.me.ward_score == pytest.approx(4.21)
    lee = g.player_by_alias("LeeSin")
    assert lee.kills == 4 and lee.current_gold == 0.0 and 3340 in lee.items
    g.items.append(1)                                   # returned lists are copies
    assert 1 not in g.me.items
    assert g.game_result is None


def test_gold_items_scores_safe_defaults(sample):
    sample["activePlayer"]["currentGold"] = "lots"
    me = _player(sample, "Garen")
    me["items"] = [None, {"itemID": "x"}, {"itemID": 2055, "slot": 3}, {"itemID": 1001, "slot": 1}, {"slot": 2}]
    me["scores"] = {"kills": "7", "deaths": None, "wardScore": float("inf")}
    g = parse_allgamedata(sample)
    assert g.current_gold == 0.0
    assert g.items == [1001, 2055]
    assert g.scores == {"kills": 7, "deaths": 0, "assists": 0, "creepScore": 0, "wardScore": 0.0}
    spectator = GameInfo()
    assert spectator.current_gold == 0.0 and spectator.items == [] and spectator.scores["kills"] == 0
    assert PlayerInfo().scores == {"kills": 0, "deaths": 0, "assists": 0, "creepScore": 0, "wardScore": 0.0}


def test_game_result_from_game_end_event(sample):
    sample["events"]["Events"].append({"EventID": 99, "EventName": "GameEnd", "EventTime": 1800.0, "Result": "Lose"})
    assert parse_allgamedata(sample).game_result == "Lose"
    assert GameInfo(events=[None, {"EventName": "GameEnd", "Result": "Win"}]).game_result == "Win"  # type: ignore[list-item]
