"""Tests for the optional League Client module (lcu.py) and the ground truth (ground_truth.py).

A local fake LCU (HTTP, and HTTPS with a throwaway self-signed certificate when openssl exists)
serves realistic match-history / game / timeline fixtures (tests/fixtures/lcu_*.json).
"""

from __future__ import annotations

import base64
import copy
import http.server
import json
import shutil
import ssl
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

from treeaicoach import ground_truth as gt
from treeaicoach import lcu, report
from treeaicoach.analysis import analyze_game
from treeaicoach.config import Config

FIX = Path(__file__).parent / "fixtures"
PASSWORD = "s3cr3t-Pw_9"


def _load(name: str) -> Any:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


@pytest.fixture()
def record() -> dict:
    return _load("game_record_sample.json")


@pytest.fixture()
def lcu_data() -> dict:
    return {"list": _load("lcu_match_list.json"), "game": _load("lcu_game.json"),
            "timeline": _load("lcu_timeline.json")}


@pytest.fixture()
def truth(record: dict, lcu_data: dict) -> dict:
    t = gt.build_truth(lcu_data["game"], lcu_data["timeline"], me_participant_id=1, record=record,
                       alias_of=lcu.default_alias_of())
    assert t is not None
    return t


# ---------------------------------------------------------------------------------- fake LCU server
class FakeLcu:
    """Answers like the League Client: basic auth, match list, game, timeline (configurable delays)."""

    def __init__(self, data: dict) -> None:
        self.data = data
        self.requests: list[str] = []
        self.old_game_until = 0          # match list requests answered with an older game first
        self.timeline_404_until = 0      # timeline requests answered 404 first
        self.auth_failures = 0

    def handler(self) -> type:
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def _send(self, code: int, obj: Any = None) -> None:
                body = json.dumps(obj if obj is not None else {"errorCode": "RPC_ERROR"}).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                want = "Basic " + base64.b64encode(f"riot:{PASSWORD}".encode()).decode()
                if self.headers.get("Authorization") != want:
                    fake.auth_failures += 1
                    self._send(401)
                    return
                p = self.path
                fake.requests.append(p)
                if p == lcu.SUMMONER_PATH:
                    self._send(200, {"displayName": "Sylvain", "gameName": "Sylvain", "tagLine": "EUW",
                                     "puuid": "puuid-01-0000", "summonerId": 5001})
                elif p == lcu.MATCHES_PATH:
                    n = sum(1 for r in fake.requests if r == lcu.MATCHES_PATH)
                    data = copy.deepcopy(fake.data["list"])
                    if n <= fake.old_game_until:
                        g = data["games"]["games"][0]
                        g["gameId"] = 7200000001
                        g["gameCreation"] -= 3600 * 1000
                        g["gameDuration"] = 1500
                    self._send(200, data)
                elif p == lcu.GAME_PATH.format(game_id=fake.data["game"]["gameId"]):
                    self._send(200, fake.data["game"])
                elif p == lcu.TIMELINE_PATH.format(game_id=fake.data["game"]["gameId"]):
                    n = sum(1 for r in fake.requests if r.startswith("/lol-match-history/v1/game-timelines/"))
                    if n <= fake.timeline_404_until:
                        self._send(404)
                    else:
                        self._send(200, fake.data["timeline"])
                else:
                    self._send(404)

        return H


def _serve(fake: FakeLcu, ctx: ssl.SSLContext | None = None) -> tuple[http.server.HTTPServer, threading.Thread]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), fake.handler())
    if ctx is not None:
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    return server, th


@pytest.fixture()
def fake_server(lcu_data: dict):
    fake = FakeLcu(lcu_data)
    server, th = _serve(fake)
    try:
        yield fake, server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        th.join(5)


def _client(port: int, protocol: str = "http", password: str = PASSWORD) -> lcu.LcuClient:
    return lcu.LcuClient(lcu.LcuCredentials(port, password, protocol, source="test"), timeout=3.0)


# ---------------------------------------------------------------------------------- parsing / discovery
def test_parse_lockfile() -> None:
    c = lcu.parse_lockfile("LeagueClient:12345:61234:AbC-dEf_123:https")
    assert c is not None and (c.port, c.password, c.protocol, c.pid) == (61234, "AbC-dEf_123", "https", 12345)
    assert c.base_url == "https://127.0.0.1:61234"
    assert c.auth_header() == "Basic " + base64.b64encode(b"riot:AbC-dEf_123").decode()
    assert "AbC" not in repr(c)                      # the password is never logged
    for bad in ("", None, "LeagueClient:1:2", "LeagueClient:1:99999:pw:https", "LeagueClient:1:80::https",
                "LeagueClient:1:80:pw:ftp", 42):
        assert lcu.parse_lockfile(bad) is None


def test_parse_command_line_quoted_and_unquoted() -> None:
    cmd = ('"C:/Riot Games/League of Legends/LeagueClientUx.exe" "--riotclient-auth-token=zzz" '
           '"--riotclient-app-port=50000" "--remoting-auth-token=Tok_en-1" "--app-port=61234" "--app-pid=4242" '
           '"--install-directory=C:\\Riot Games\\League of Legends" "--locale=fr_FR"')
    c = lcu.parse_command_line(cmd)
    assert c is not None and (c.port, c.password, c.pid, c.protocol) == (61234, "Tok_en-1", 4242, "https")
    assert str(lcu.install_dir_from_command_line(cmd)) == str(Path("C:\\Riot Games\\League of Legends"))
    plain = "LeagueClientUx.exe --app-port=5555 --remoting-auth-token=abc --install-directory=D:\\Games\\LoL --x=1"
    assert lcu.parse_command_line(plain).port == 5555
    assert str(lcu.install_dir_from_command_line(plain)) == str(Path("D:\\Games\\LoL"))
    assert lcu.parse_command_line("LeagueClientUx.exe --app-port=5555") is None
    assert lcu.parse_command_line(None) is None and lcu.install_dir_from_command_line("") is None


def test_discover_from_lockfile_and_process(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (b / "lockfile").write_text("LeagueClient:1:4321:pw:https", encoding="utf-8")
    c = lcu.discover([a, b], use_process=False)
    assert c is not None and c.port == 4321 and c.source.endswith("lockfile")
    assert lcu.discover([a], use_process=False) is None

    # process command line: wmic gone -> PowerShell fallback; install dir lockfile preferred
    calls: list[list[str]] = []

    def runner(args: list[str]) -> str:
        calls.append(args)
        if args[0] == "wmic":
            return ""
        return f'"x" "--app-port=7777" "--remoting-auth-token=tt" "--install-directory={b}"\r\n'
    c = lcu.discover([a], runner=runner)
    assert c is not None and c.port == 4321                 # lockfile of the install directory
    assert [x[0] for x in calls] == ["wmic", "powershell"]

    def runner2(args: list[str]) -> str:
        return "CommandLine=LeagueClientUx.exe --app-port=7777 --remoting-auth-token=tt\r\n" if args[0] == "wmic" else ""
    c = lcu.discover([a], runner=runner2)
    assert c is not None and (c.port, c.password, c.source) == (7777, "tt", "process")
    assert lcu.discover([a], runner=lambda args: "") is None
    assert lcu.discover([a], runner=lambda args: 1 / 0) is None          # never raises


def test_candidate_dirs_env_and_riot_client_installs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pd = tmp_path / "pd"
    (pd / "Riot Games").mkdir(parents=True)
    (pd / "Riot Games" / "RiotClientInstalls.json").write_text(json.dumps({
        "associated_client": {"E:/Jeux/League of Legends/": "E:/Jeux/Riot Client/RiotClientServices.exe"},
        "rc_default": "F:/Riot Games/Riot Client/RiotClientServices.exe"}), encoding="utf-8")
    monkeypatch.setenv("PROGRAMDATA", str(pd))
    monkeypatch.setenv(lcu.ENV_DIR, str(tmp_path / "custom"))
    dirs = [str(d).replace("\\", "/").rstrip("/") for d in lcu.candidate_dirs()]
    assert dirs[0].endswith("custom")
    assert "E:/Jeux/League of Legends" in dirs and "F:/Riot Games/League of Legends" in dirs
    assert any(d.endswith("Riot Games/League of Legends") for d in dirs[3:])


def test_disabled_client_never_requests() -> None:
    c = lcu.LcuClient(enabled=False)
    assert c.get(lcu.SUMMONER_PATH) is None and not c.available()
    assert c.status() == lcu.STATUS_DISABLED and c.status_text() == "Client LoL : non trouvé"
    assert lcu.fetch_postgame_truth({}, c, timeout_s=0.1) is None


def test_not_found_rate_limited_discovery() -> None:
    calls: list[bool] = []
    clock = {"t": 0.0}
    c = lcu.LcuClient(discover_fn=lambda proc: calls.append(proc), clock=lambda: clock["t"])
    assert c.credentials() is None and c.credentials() is None
    assert calls == [True]                  # rescanned at most every REDISCOVER_S
    clock["t"] = lcu.REDISCOVER_S + 1
    assert c.credentials() is None and calls == [True, False]     # process query: every 60 s only
    assert c.status() == lcu.STATUS_NOT_FOUND


# ---------------------------------------------------------------------------------- HTTP client
def test_client_against_fake_lcu(fake_server, monkeypatch: pytest.MonkeyPatch) -> None:
    fake, port = fake_server
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")     # a proxy must never be used
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    c = _client(port)
    assert c.status() == lcu.STATUS_CONNECTED and c.status_text() == "Client LoL : connecté"
    assert c.current_summoner()["gameName"] == "Sylvain"
    last = c.last_game()
    assert last is not None and last["gameId"] == 7212345678
    assert len(c.game(last["gameId"])["participants"]) == 10
    assert len(c.timeline(last["gameId"])["frames"]) == 30
    assert c.get("/lol-nope/v1/x") is None and c.last_error == "HTTP 404"
    assert c.get("http://evil.example/x") is None and c.get("//x") is None      # loopback paths only
    assert c.timeline(None) is None and c.game("abc") is None
    bad = _client(port, password="wrong")
    assert bad.get(lcu.SUMMONER_PATH) is None and fake.auth_failures >= 1


def test_client_after_server_stops(lcu_data: dict) -> None:
    fake = FakeLcu(lcu_data)
    server, th = _serve(fake)
    port = server.server_address[1]
    c = _client(port)
    assert c.status() == lcu.STATUS_CONNECTED
    server.shutdown()
    server.server_close()
    th.join(5)
    assert c.get(lcu.SUMMONER_PATH) is None
    assert c.status() == lcu.STATUS_NOT_FOUND


def test_client_over_https_self_signed(tmp_path: Path, lcu_data: dict) -> None:
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("openssl not available to create a throwaway certificate")
    key, cert = tmp_path / "key.pem", tmp_path / "cert.pem"
    try:
        subprocess.run([openssl, "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                        "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "2", "-subj", "/CN=rclient"],
                       check=True, capture_output=True, timeout=30)
    except Exception as exc:  # pragma: no cover - depends on the local openssl
        pytest.skip(f"cannot create certificate: {exc}")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(key))
    server, th = _serve(FakeLcu(lcu_data), ctx)
    try:
        c = _client(server.server_address[1], protocol="https")
        assert c.status() == lcu.STATUS_CONNECTED
        assert c.last_game()["gameId"] == 7212345678
    finally:
        server.shutdown()
        server.server_close()
        th.join(5)


# ---------------------------------------------------------------------------------- post-game fetch
def test_game_matches_record(record: dict, lcu_data: dict) -> None:
    last = lcu_data["list"]["games"]["games"][0]
    alias_of = lcu.default_alias_of()
    assert lcu.game_matches_record(last, record, alias_of)
    other = copy.deepcopy(last)
    other["participants"][0]["championId"] = 64          # Lee Sin, not Garen
    assert not lcu.game_matches_record(other, record, alias_of)
    old = copy.deepcopy(last)
    old["gameCreation"] -= 2 * 3600 * 1000
    assert not lcu.game_matches_record(old, record, alias_of)
    short = copy.deepcopy(last)
    short["gameDuration"] = 900
    assert not lcu.game_matches_record(short, record, alias_of)
    assert not lcu.game_matches_record(None, record) and not lcu.game_matches_record(last, {})


def test_fetch_postgame_truth_waits_for_the_data(fake_server, record: dict) -> None:
    fake, port = fake_server
    fake.old_game_until = 2          # the list still shows the previous game twice
    fake.timeline_404_until = 1      # then the timeline is not ready once
    t = lcu.fetch_postgame_truth(record, _client(port), timeout_s=10.0, poll_s=0.02)
    assert t is not None and t["game_id"] == 7212345678 and t["me"] == 1
    assert sum(1 for r in fake.requests if r == lcu.MATCHES_PATH) == 4


def test_fetch_postgame_truth_timeout_and_cancel(fake_server, record: dict) -> None:
    fake, port = fake_server
    fake.old_game_until = 10 ** 6
    assert lcu.fetch_postgame_truth(record, _client(port), timeout_s=0.2, poll_s=0.05) is None
    ev = threading.Event()
    ev.set()
    assert lcu.fetch_postgame_truth(record, _client(port), timeout_s=60.0, poll_s=5.0, cancel=ev) is None


# ---------------------------------------------------------------------------------- ground truth
def test_build_truth_shape(truth: dict) -> None:
    assert truth["schema"] == 1 and truth["source"] == "lcu"
    assert (truth["me"], truth["my_team"], truth["enemy_jungler"], truth["my_jungler"], truth["lane_opponent"]) == \
        (1, "ORDER", 7, 2, 6)
    parts = {p["id"]: p for p in truth["participants"]}
    assert parts[7]["alias"] == "LeeSin" and parts[7]["smite"] and parts[7]["position"] == "JUNGLE"
    assert parts[10]["position"] == "UTILITY" and parts[1]["name"] == "Garen"
    assert all("puuid" not in json.dumps(p) for p in truth["participants"])       # no identities kept
    assert len(truth["frames"]) == 30 and truth["frames"][0][0] == 0.0
    u, v = truth["frames"][0][1]["1"][:2]
    assert u < 0.1 and v > 0.9                            # ORDER fountain, bottom-left
    assert len(truth["kills"]) == 19 and truth["monsters"] and truth["buildings"]
    assert json.loads(json.dumps(truth)) == truth


def test_build_truth_coordinates_and_bad_input() -> None:
    game = {"gameId": 1, "participants": [{"participantId": 1, "championId": 86, "teamId": 100}]}
    tl = {"frames": [{"timestamp": 60000, "participantFrames": {"1": {"participantId": 1,
                                                                    "position": {"x": 14870, "y": 0}}},
                      "events": [{"type": "CHAMPION_KILL", "timestamp": 61000, "position": {"x": 0, "y": 14980},
                                  "killerId": 0, "victimId": 1}]}]}
    t = gt.build_truth(game, tl, me_participant_id=1)
    assert t["frames"][0][1]["1"][:2] == [1.0, 1.0]
    assert t["kills"][0][4:] == [0.0, 0.0]
    for bad_game, bad_tl in ((None, tl), (game, None), (game, {"frames": []}), ({}, {"frames": [{}]}),
                             (game, {"frames": "x"})):
        assert gt.build_truth(bad_game, bad_tl) is None


def test_analyze_truth_report_data(record: dict, truth: dict) -> None:
    a = analyze_game(record, truth=truth)
    t = a["truth"]
    assert t["available"] and t["me"]["alias"] == "Garen"
    assert [d["time"] for d in t["deaths"]] == ["4:10", "9:20", "14:30", "21:05"]
    assert t["deaths"][0]["jungler_involved"] and t["deaths"][0]["warned"]
    assert not t["deaths"][1]["jungler_involved"]
    j = t["jungler"]
    assert j["alias"] == "LeeSin" and j["first_side"] == "top" and j["first_side_label"] == "côté haut"
    assert len(j["path"]) == 15 and j["path"][0]["time"] == "1:00"
    lane = t["lane"]
    assert lane["opponent"] == "Darius" and lane["cs10"] == 58 and lane["gold_diff10"] == 97
    assert [r["minute"] for r in lane["rows"]] == [10, 15]
    rel = t["reliability"]
    assert rel["confirmed"] == 2 and rel["precision"] == 1.0 and rel["unknown"] == 1
    assert rel["jungler_deaths"] == 3 and rel["missed"] == 1 and rel["missed_times"] == ["14:30"]
    assert rel["sightings_checked"] > 5 and rel["grade"] is not None
    assert "truth" not in analyze_game(record)                 # unchanged without the truth


def test_reliability_false_alerts_fog_and_suggestion(record: dict, truth: dict) -> None:
    rec = copy.deepcopy(record)
    # a jungler alert at 12:00 while I am top and Lee Sin is bot (12:00 frame) -> false alert
    rec["alerts"] += [[720.0, "jungler_approach", 2, "Gank ! Lee Sin, recule !", "LeeSin"],
                      [722.0, "jungler_approach", 2, "doublon (même épisode)", "LeeSin"],
                      [1200.0, "jungler_approach", 1, "Attention, Lee Sin approche.", None],
                      [1203.0, "collapse", 2, "3 ennemis", None]]
    rec["my_positions"] = [p for p in rec["my_positions"] if not 700 <= p[0] <= 740] + [[720.0, 0.1, 0.12]]
    # fog circle around the true position at 3:00, wrong place at 5:00, nothing else
    lee3 = truth["frames"][3][1]["7"][:2]
    rec["fog"] = [[180.0, "LeeSin", lee3[0], lee3[1], 0.05], [300.4, "LeeSin", 0.9, 0.9, 0.05],
                  [300.0, "Darius", 0.5, 0.5, 0.9]]
    rec["settings"] = {"sensitivity": 1.2}
    # a misidentified sighting: "LeeSin" seen far from the truth at 1:00
    rec["sightings"]["LeeSin"] = rec["sightings"]["LeeSin"] + [[60.2, 0.95, 0.95]]
    rel = analyze_game(rec, truth=truth)["truth"]["reliability"]
    by_time = {e["time"]: e["verdict"] for e in rel["episodes"]}
    assert by_time["12:00"] in ("false", "probable_false")
    assert "12:02" not in by_time                                   # merged episode
    assert rel["fog_checks"] == 2 and rel["fog_inside"] == 1 and rel["fog_coverage"] == 0.5
    assert rel["sightings_checked"] > rel["sightings_ok"]
    assert rel["suggestion"]["current"] == 1.2


def test_suggest_sensitivity_rules() -> None:
    up = gt.suggest_sensitivity({"precision": 0.8, "confirmed": 4, "false": 1, "missed": 2, "jungler_deaths": 3}, 1.0)
    assert up["delta"] == 0.1 and up["suggested"] == 1.1 and "augmente" in up["text"]
    down = gt.suggest_sensitivity({"precision": 0.25, "confirmed": 1, "false": 3, "missed": 0}, 1.0)
    assert down["delta"] == -0.1 and down["suggested"] == 0.9 and "baisse" in down["text"]
    det = gt.suggest_sensitivity({"precision": 0.2, "confirmed": 1, "false": 4, "missed": 3, "jungler_deaths": 3}, 1.0)
    assert det["delta"] == 0.0 and "détection" in det["text"]
    keep = gt.suggest_sensitivity({"precision": 0.9, "confirmed": 5, "false": 0, "missed": 0}, 1.0)
    assert keep["delta"] == 0.0 and keep["suggested"] is None
    cap = gt.suggest_sensitivity({"precision": 0.8, "confirmed": 4, "missed": 2, "jungler_deaths": 2}, 1.6)
    assert cap["delta"] == 0.0 and cap["suggested"] is None                # already at the maximum
    assert gt.suggest_sensitivity({}, None)["current"] is None


def test_truth_files_and_aggregate(tmp_path: Path, record: dict, truth: dict) -> None:
    games = tmp_path / "games"
    rp = games / "2026-09-12_2031_Garen.json"
    games.mkdir()
    rp.write_text(json.dumps(record), encoding="utf-8")
    rel = analyze_game(record, truth=truth)["truth"]["reliability"]
    truth = dict(truth, score=gt.compact_score(rel, dict(record, settings={"sensitivity": 1.0})))
    out = gt.save_truth(rp, truth)
    assert out == games / "truth" / "2026-09-12_2031_Garen.truth.json" and out.is_file()
    assert gt.truth_path_for(games / "x.partial.json") == games / "truth" / "x.truth.json"
    assert gt.load_truth(rp)["game_id"] == 7212345678
    assert gt.load_truth(games / "none.json") is None
    # the history list never shows the truth files
    assert [g["path"] for g in report.list_games(games_dir=games)] == [rp]
    # a second (older) game: aggregate
    t2 = dict(truth, game_creation=truth["game_creation"] - 86400000,
              score=dict(truth["score"], missed=2, jungler_deaths=2))
    (games / "truth" / "old.truth.json").write_text(json.dumps(t2), encoding="utf-8")
    (games / "truth" / "junk.truth.json").write_text("{", encoding="utf-8")
    agg = gt.aggregate_scores(games / "truth")
    assert agg["games"] == 2 and agg["missed"] == 3 and agg["jungler_deaths"] == 5
    assert agg["precision"] == 1.0 and agg["suggestion"]["delta"] == 0.1
    assert gt.aggregate_scores(tmp_path / "nothing") == {"games": 0}


# ---------------------------------------------------------------------------------- report
def test_report_with_truth_and_pending(tmp_path: Path, record: dict, truth: dict) -> None:
    rp = tmp_path / "2026-09-12_2031_Garen.json"
    rp.write_text(json.dumps(record), encoding="utf-8")
    pending = report.write_report(rp, lcu_pending=True).read_text(encoding="utf-8")
    assert 'http-equiv="refresh"' in pending and "client LoL en cours" in pending
    assert "Vérité terrain" not in pending
    gt.save_truth(rp, dict(truth, score=gt.compact_score({})))
    page = report.write_report(rp, lcu_pending=True).read_text(encoding="utf-8")   # truth there: final page
    assert 'http-equiv="refresh"' not in page and "<script" not in page
    for text in ("Vérité terrain (client LoL)", "Fiabilité de TreeAI cette partie", "Vrai parcours de Lee Sin",
                 "côté haut", "face à Darius", "Ganks manqués", "Cercle du brouillard", "14:30"):
        assert report.html.escape(text, quote=True) in page or text in page, text
    assert page.count("data:image/png;base64,") >= 4


def test_report_truth_section_degrades(record: dict) -> None:
    a = analyze_game(record)
    assert report._truth_section(record, a) == ""
    a["truth"] = {"available": True, "deaths": [], "jungler": {}, "lane": {}, "reliability": {}}
    html_part = report._truth_section(record, a)
    assert "Vérité terrain" in html_part and "Aucune mort" in html_part


# ---------------------------------------------------------------------------------- recorder / config
def test_recorder_fog_and_settings(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from treeaicoach.recorder import GameRecorder

    rec = GameRecorder(out_dir=tmp_path)
    rec.note_settings(Config(sensitivity=1.3))
    me = SimpleNamespace(champion_alias="Garen", champion_name="Garen", team="ORDER", position="TOP",
                         riot_id="Moi#EUW", summoner_name="Moi", skin_id=0, scores={}, items=[], level=1,
                         is_dead=False, has_smite=False, is_bot=False)
    game = SimpleNamespace(me=me, game_time=100.0, game_mode="CLASSIC", map_number=11, map_terrain="Default",
                           events=[], all_players=lambda: [me], current_gold=500.0)
    rec.on_game_info(game, 0.0)
    est = SimpleNamespace(is_jungler=True, alias="LeeSin", key="LeeSin", last_uv=(0.4, 0.3), radius=0.12)
    other = SimpleNamespace(is_jungler=False, alias="Darius", key="Darius", last_uv=(0.1, 0.1), radius=0.2)
    rec.on_fog([est, other], 100.0)
    rec.on_fog([est], 100.4)                     # <= 1 Hz
    rec.on_fog([est], 101.2)
    rec.on_fog(None, 102.0)
    rec.on_fog([object()], 103.0)                # never raises
    snap = rec.snapshot()
    assert snap["fog"] == [[100.0, "LeeSin", 0.4, 0.3, 0.12], [101.2, "LeeSin", 0.4, 0.3, 0.12]]
    assert snap["settings"]["sensitivity"] == 1.3 and snap["settings"]["fog_mode"] == "jungler"
    path = rec.finish()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["fog"]) == 2 and data["settings"]["sensitivity"] == 1.3


def test_config_lcu_enabled() -> None:
    assert Config().lcu_enabled is True
    assert Config.from_dict({"lcu_enabled": False}).lcu_enabled is False
    assert Config.from_dict({"lcu_enabled": "yes"}).lcu_enabled is True        # invalid -> default


# ---------------------------------------------------------------------------------- engine
def test_engine_postgame_fetches_truth_and_rewrites_report(tmp_path: Path, fake_server, record: dict) -> None:
    from treeaicoach.engine import CoachEngine

    fake, port = fake_server
    fake.timeline_404_until = 1
    rp = tmp_path / "2026-09-12_2031_Garen.json"
    rp.write_text(json.dumps(record), encoding="utf-8")
    seen: list[str] = []

    class Rec:
        def finish(self) -> Path:
            return rp

    class Voice:
        def say(self, *a: Any, **k: Any) -> None:
            pass

    eng = CoachEngine(Config(open_report_automatically=True), Voice(),
                      report_opener=lambda p: seen.append(Path(p).read_text(encoding="utf-8")),
                      enable_hotkeys=False, manage_overlay=False)
    eng._lcu = _client(port)
    eng._lcu_poll_s = 0.02
    eng._lcu_timeout_s = 10.0
    eng._finish_job(Rec())
    assert seen and 'http-equiv="refresh"' in seen[0]          # opened while the data was pending
    assert eng.last_truth_path == tmp_path / "truth" / "2026-09-12_2031_Garen.truth.json"
    saved = json.loads(eng.last_truth_path.read_text(encoding="utf-8"))
    assert saved["record"] == rp.name and saved["score"]["confirmed"] == 2
    final = eng.last_report_path.read_text(encoding="utf-8")
    assert "Vérité terrain" in final and 'http-equiv="refresh"' not in final

    # disabled in the settings / client not running: no pending page, no truth
    rp2 = tmp_path / "2026-09-12_2131_Garen.json"
    rp2.write_text(json.dumps(record), encoding="utf-8")
    Rec.finish = lambda self: rp2  # type: ignore[method-assign]
    eng.apply_config(Config(lcu_enabled=False, open_report_automatically=True))
    eng._finish_job(Rec())
    assert 'http-equiv="refresh"' not in seen[-1] and not gt.truth_path_for(rp2).exists()
    eng.apply_config(Config(open_report_automatically=True))
    eng._lcu = lcu.LcuClient(enabled=False)
    eng._finish_job(Rec())
    assert 'http-equiv="refresh"' not in seen[-1] and not gt.truth_path_for(rp2).exists()
