"""Tests for treeaicoach.champions (roster lookup, icons, skin icon prefetch)."""

from __future__ import annotations

import http.server
import json
import socket
import threading
from pathlib import Path

import cv2
import numpy as np
import pytest

from treeaicoach import champions
from treeaicoach.champions import ChampionDB, ChampionEntry, alias_from_raw, normalize_name
from treeaicoach.live_client import PlayerInfo

FIXTURE = Path(__file__).parent / "fixtures" / "allgamedata_sample.json"


@pytest.fixture(scope="module")
def db() -> ChampionDB:
    return ChampionDB(cache_dir=Path("/nonexistent-treeaicoach-cache"))


def _png_bytes(bgra: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", bgra)
    assert ok
    return buf.tobytes()


def _solid(size: int, bgr: tuple[int, int, int]) -> np.ndarray:
    img = np.zeros((size, size, 4), np.uint8)
    img[..., :3] = bgr
    img[..., 3] = 255
    return img


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ------------------------------------------------------------------------------ aliases
@pytest.mark.parametrize("raw,alias", [
    ("game_character_displayname_MonkeyKing", "MonkeyKing"),
    ("game_character_displayname_Nunu", "Nunu"),
    ("game_character_displayname_KSante", "KSante"),
    ("game_character_displayname_Belveth", "Belveth"),
    ("game_character_displayname_Chogath", "Chogath"),
    ("game_character_displayname_FiddleSticks", "FiddleSticks"),
    ("  game_character_displayname_Ahri ", "Ahri"),
    ("GAME_CHARACTER_DISPLAYNAME_Jinx", "Jinx"),
    ("game_character_skin_displayname_Annie_1", "Annie"),
    ("MonkeyKing", "MonkeyKing"),
    ("KSante", "KSante"),
    ("", ""),
    (None, ""),
])
def test_alias_from_raw(raw, alias):
    assert alias_from_raw(raw) == alias


def test_normalize_name():
    assert normalize_name("K'Santé") == "ksante"
    assert normalize_name("Nunu & Willump") == "nunuwillump"
    assert normalize_name("Dr. Mundo") == "drmundo"
    assert normalize_name(None) == ""


# ------------------------------------------------------------------------------ lookup
def test_bundled_index(db):
    entries = db.all()
    assert len(entries) >= 150 and len(db) == len(entries)
    assert [e.alias.lower() for e in entries] == sorted(e.alias.lower() for e in entries)
    assert all(isinstance(e, ChampionEntry) and e.icon_file.endswith(".png") for e in entries)
    for e in entries:                      # no alias is shadowed by another champion's name
        assert db.get(e.alias) is e
        assert db.get(e.alias.upper()) is e
    assert db.patch


@pytest.mark.parametrize("query,alias", [
    ("MonkeyKing", "MonkeyKing"), ("monkeyking", "MonkeyKing"), ("Wukong", "MonkeyKing"),
    ("game_character_displayname_MonkeyKing", "MonkeyKing"),
    ("KSante", "KSante"), ("K'Santé", "KSante"), ("k'sante", "KSante"),
    ("Nunu", "Nunu"), ("Nunu & Willump", "Nunu"), ("Nunu et Willump", "Nunu"),
    ("belveth", "Belveth"), ("Bel'Veth", "Belveth"), ("Cho'Gath", "Chogath"), ("CHOGATH", "Chogath"),
    ("fiddlesticks", "Fiddlesticks"), ("game_character_displayname_FiddleSticks", "Fiddlesticks"),
    ("Kai'Sa", "Kaisa"), ("Dr. Mundo", "DrMundo"), ("LeBlanc", "Leblanc"), ("Renata Glasc", "Renata"),
    ("Lee Sin", "LeeSin"), (62, "MonkeyKing"), ("62", "MonkeyKing"),
])
def test_get_variants(db, query, alias):
    entry = db.get(query)
    assert entry is not None and entry.alias == alias
    assert db.canonical_alias(query if isinstance(query, str) else alias) == alias


@pytest.mark.parametrize("query", [None, "", "   ", "NotAChampion", "game_character_displayname_", True, 3.5, []])
def test_get_unknown(db, query):
    assert db.get(query) is None  # type: ignore[arg-type]


def test_fixture_roster_resolves(db):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for p in data["allPlayers"]:
        assert db.get(p["rawChampionName"]) is not None, p["rawChampionName"]
        assert db.get(p["championName"]) is not None, p["championName"]
    assert db.by_key(897).alias == "KSante" and db.by_key("x") is None
    assert "Ahri" in db and "Nope" not in db


# ------------------------------------------------------------------------------ icons
def test_load_icon_bundled(db):
    icon = db.load_icon("Ahri")
    assert icon is not None and icon.dtype == np.uint8 and icon.shape == (64, 64, 4)
    assert icon[0, 0, 3] < 30 and icon[32, 32, 3] > 200          # round portrait: transparent corners
    icon[:] = 0                                                   # caller gets a private copy
    again = db.load_icon("ahri")
    assert again[32, 32, 3] > 200
    assert db.load_icon("Ahri", -4) is not None
    assert db.load_icon("Ahri", "abc") is not None                # type: ignore[arg-type]
    assert db.load_icon("NotAChampion") is None
    assert db.load_icon(None) is None                             # type: ignore[arg-type]


def test_load_icon_every_champion(db):
    for e in db.all():
        icon = db.load_icon(e.alias)
        assert icon is not None and icon.shape[2] == 4, e.alias


def test_load_icon_prefers_cached_skin(tmp_path):
    cache = tmp_path / "cache dir é"                              # non-ASCII path (Windows users)
    cache.mkdir()
    (cache / "Ahri_7.png").write_bytes(_png_bytes(_solid(64, (0, 0, 255))))   # red in BGR
    (cache / "Darius_3.png").write_bytes(b"not a png")
    local = ChampionDB(cache_dir=cache)
    red = local.load_icon("ahri", 7)
    assert red.shape == (64, 64, 4)
    assert tuple(red[10, 10]) == (255, 0, 0, 255)                 # RGBA order
    base = local.load_icon("Ahri", 0)
    assert tuple(base[10, 10]) != (255, 0, 0, 255)
    darius = local.load_icon("Darius", 3)                         # corrupt cache file -> base icon
    assert darius is not None and darius.shape == (64, 64, 4)


def test_missing_or_corrupt_assets(tmp_path):
    empty = ChampionDB(assets_dir=tmp_path / "missing", cache_dir=tmp_path)
    assert empty.all() == [] and empty.get("Ahri") is None and empty.load_icon("Ahri") is None
    empty.prefetch_skin_icons([PlayerInfo(champion_alias="Ahri", skin_id=3)])
    assert empty.wait_for_prefetch(2)

    icons = tmp_path / "assets" / "icons" / "champions"
    icons.mkdir(parents=True)
    (icons / "index.json").write_text("{corrupt", encoding="utf-8")
    (icons / "Ahri.png").write_bytes(_png_bytes(_solid(64, (0, 255, 0))))
    fallback = ChampionDB(assets_dir=tmp_path / "assets", cache_dir=tmp_path)
    assert [e.alias for e in fallback.all()] == ["Ahri"]
    assert fallback.load_icon("AHRI").shape == (64, 64, 4)
    direct = ChampionDB(assets_dir=icons, cache_dir=tmp_path)    # champion folder given directly
    assert direct.get("ahri") is not None


def test_icon_lru_is_bounded(db, monkeypatch):
    monkeypatch.setattr(champions, "ICON_LRU_SIZE", 5)
    local = ChampionDB(cache_dir=Path("/nonexistent-treeaicoach-cache"))
    for e in local.all()[:20]:
        local.load_icon(e.alias)
    assert len(local._lru) <= 5


# ------------------------------------------------------------------------------ prefetch
def test_prefetch_disabled_does_nothing(tmp_path):
    local = ChampionDB(cache_dir=tmp_path)
    local.prefetch_skin_icons([PlayerInfo(champion_alias="Ahri", skin_id=27)], allow_network=False)
    assert local._worker is None and list(tmp_path.iterdir()) == []


def test_prefetch_ignores_useless_players(tmp_path):
    local = ChampionDB(cache_dir=tmp_path)
    local.base_url = f"http://127.0.0.1:{_closed_port()}"
    local.prefetch_skin_icons(None)                               # type: ignore[arg-type]
    local.prefetch_skin_icons([None, 3, "x", PlayerInfo(champion_alias="Ahri", skin_id=0),
                               PlayerInfo(champion_alias="Unknown", skin_id=4), object()])
    assert local._worker is None


def test_prefetch_unreachable_url_finishes_quietly(tmp_path, monkeypatch):
    monkeypatch.setattr(champions, "CDRAGON_CHARACTERS_URL", f"http://127.0.0.1:{_closed_port()}/characters")
    local = ChampionDB(cache_dir=tmp_path)
    players = [PlayerInfo(champion_alias="Ahri", skin_id=27), PlayerInfo(champion_alias="Garen", skin_id=22),
               {"champion_alias": "Jinx", "skin_id": 4}]
    local.prefetch_skin_icons(players)
    assert local.wait_for_prefetch(15)
    assert list(tmp_path.glob("*.png")) == []
    assert local.load_icon("Ahri", 27).shape == (64, 64, 4)       # falls back to the base icon
    local.prefetch_skin_icons(players)                            # recently failed: not retried
    assert local._worker is None


class _CDragonHandler(http.server.BaseHTTPRequestHandler):
    requests: list[str] = []
    png: bytes = b""

    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        type(self).requests.append(self.path)
        if self.path in ("/characters/ahri/hud/ahri_circle_27.png", "/characters/kindred/hud/kindred_circle_3.png"):
            body, code, ctype = self.png, 200, "image/png"
        elif self.path == "/characters/kindred/hud/":
            body, code, ctype = (b'<a href="kindred_circle.png"></a><a href="kindred_circle_1.png"></a>'
                                 b'<a href="kindred_circle_3.png"></a><a href="kindred_circle_12.png"></a>'), 200, "text/html"
        elif self.path == "/characters/garen/hud/garen_circle_5.png":
            body, code, ctype = b"<html>oops</html>", 200, "text/html"
        else:
            body, code, ctype = b"Not Found", 404, "text/plain"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def cdragon_server():
    _CDragonHandler.requests = []
    _CDragonHandler.png = _png_bytes(_solid(120, (0, 255, 0)))    # green, CommunityDragon-like size
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _CDragonHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/characters"
    server.shutdown()
    server.server_close()
    thread.join(5)


def test_prefetch_downloads_and_remembers_404(tmp_path, cdragon_server, monkeypatch):
    for k in ("HTTP_PROXY", "http_proxy"):                        # loopback never goes through a proxy
        monkeypatch.setenv(k, f"http://127.0.0.1:{_closed_port()}")
    local = ChampionDB(cache_dir=tmp_path)
    local.base_url = cdragon_server
    before = local.load_icon("Ahri", 27)                          # base icon cached in memory first
    players = [PlayerInfo(champion_alias="Ahri", skin_id=27),
               PlayerInfo(champion_alias="MonkeyKing", skin_id=3),     # 404
               PlayerInfo(champion_alias="Garen", skin_id=5)]          # invalid image
    local.prefetch_skin_icons(players)
    assert local.wait_for_prefetch(15)
    cached = tmp_path / "Ahri_27.png"
    assert cached.is_file()
    assert not (tmp_path / "MonkeyKing_3.png").exists() and not (tmp_path / "Garen_5.png").exists()
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []
    stored = cv2.imread(str(cached), cv2.IMREAD_UNCHANGED)
    assert stored.shape == (champions.CACHED_ICON_SIZE, champions.CACHED_ICON_SIZE, 4)
    after = local.load_icon("Ahri", 27)                           # memory cache was invalidated
    assert tuple(after[32, 32]) == (0, 255, 0, 255)
    assert not np.array_equal(before, after)
    n_requests = len(_CDragonHandler.requests)
    assert n_requests == 4                                        # (+ the folder listing: chroma parent)
    local.prefetch_skin_icons(players)                            # 404 / invalid / done: no new request
    assert local.wait_for_prefetch(15)
    assert len(_CDragonHandler.requests) == n_requests
    fresh = ChampionDB(cache_dir=tmp_path)                        # new session reuses the file
    fresh.base_url = cdragon_server
    fresh.prefetch_skin_icons([PlayerInfo(champion_alias="Ahri", skin_id=27)])
    assert fresh.wait_for_prefetch(5)
    assert len(_CDragonHandler.requests) == n_requests
    assert tuple(fresh.load_icon("Ahri", 27)[5, 32]) == (0, 255, 0, 255)


def test_skin_icon_url(db):
    assert db.skin_icon_url("MonkeyKing", 3) == (
        "https://raw.communitydragon.org/latest/game/assets/characters/monkeyking/hud/monkeyking_circle_3.png")


def test_default_db_singleton():
    a = champions.get_default_db()
    assert a is champions.get_default_db() and a.get("Ahri") is not None


def test_prefetch_chroma_uses_its_parent_skin_icon(tmp_path, cdragon_server, monkeypatch):
    """A chroma (Kindred 9 = a Spirit Blossom chroma) has no circle icon of its own: the minimap
    shows the parent skin's (highest skin number below it with an icon: 3), cached as skin 9."""
    for k in ("HTTP_PROXY", "http_proxy"):
        monkeypatch.setenv(k, f"http://127.0.0.1:{_closed_port()}")
    local = ChampionDB(cache_dir=tmp_path)
    local.base_url = cdragon_server
    local.prefetch_skin_icons([PlayerInfo(champion_alias="Kindred", skin_id=9)])
    assert local.wait_for_prefetch(15)
    assert (tmp_path / "Kindred_9.png").is_file()
    assert "/characters/kindred/hud/kindred_circle_3.png" in _CDragonHandler.requests
    assert tuple(local.load_icon("Kindred", 9)[32, 32]) == (0, 255, 0, 255)
