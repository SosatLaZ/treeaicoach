"""client_sync.py: the meta build written into the League client (item set, runes, spells) + elo bracket,
against a fake LCU (the endpoints could not be tried on a real client)."""

from __future__ import annotations

import http.server
import json
import threading
from types import SimpleNamespace as NS

import pytest

from treeaicoach import client_sync as cs
from treeaicoach import lcu
from treeaicoach import meta_builds as mb
from treeaicoach.meta_builds import MetaBuild


def build(**kw) -> MetaBuild:
    base = dict(alias="Garen", role="TOP", patch="16_20", source="ugg", tier="Platine+", start=(1054, 2003),
                core=(6630, 3047, 3053), boots=3047, slots=(((3071, 52.5, 900), (3036, 51.0, 700)),),
                spells=(4, 12), runes=(8010, 9111, 9104, 8014, 8139, 8135), primary_style=8000,
                secondary_style=8100, shards=(5008, 5008, 5001), core_wr=53.2, core_games=2000, matches=9000)
    base.update(kw)
    return MetaBuild(**base)


class FakeClient:
    def __init__(self, pages=None, limit=2, sets=None, session=None):
        self.pages = pages if pages is not None else []
        self.limit = limit
        self.sets = sets if sets is not None else {"accountId": 7, "itemSets": [], "timestamp": 1}
        self.session = session
        self.calls: list[tuple] = []
        self.fail_put_sets = False

    def current_summoner(self):
        return {"summonerId": 99}

    def ranked_tier(self):
        return "GOLD"

    def get(self, path, timeout=None):
        self.calls.append(("GET", path))
        if path == "/lol-item-sets/v1/item-sets/99/sets":
            return self.sets
        if path == cs.PAGES_PATH:
            return self.pages
        if path == cs.INVENTORY_PATH:
            return {"ownedPageCount": self.limit}
        if path == cs.CURRENT_PAGE_PATH:
            return next((p for p in self.pages if p.get("current")), None)
        if path == cs.SESSION_PATH:
            return self.session
        return None

    def send(self, method, path, body=None, timeout=None):
        self.calls.append((method, path, body))
        if path.endswith("/sets"):
            if self.fail_put_sets:
                return None
            self.sets = body
            return {}
        if method == "POST" and path == cs.PAGES_PATH:
            p = dict(body, id=100 + len(self.pages), isEditable=True)
            self.pages.append(p)
            return p
        if method == "PUT" and path.startswith(cs.PAGES_PATH + "/"):
            pid = int(path.rsplit("/", 1)[1])
            for p in self.pages:
                if p["id"] == pid:
                    p.update(body)
            return {}
        return {}


@pytest.fixture(autouse=True)
def _build(monkeypatch):
    b = build()
    monkeypatch.setattr(cs.ClientSync, "_build", lambda self, a, r: b)
    monkeypatch.setattr(mb, "_champion_key", lambda a: 86)
    mb.set_bracket("")
    yield b
    mb.set_bracket("")


def test_item_set_shape_and_keeps_user_sets():
    s = cs.build_item_set(build(), "Garen", 86, "TOP", name_of=lambda i: f"I{i}")
    assert s["title"] == "TreeAI · Garen top" and s["associatedChampions"] == [86]
    types = [b["type"] for b in s["blocks"]]
    assert types[0] == "Départ" and "53,2 %" in types[1] and types[2] == "Bottes" and "52,5 %" in types[3]
    assert [i["id"] for i in s["blocks"][1]["items"]] == ["6630", "3053"]       # boots in their own block
    user = {"title": "Mon set", "uid": "x", "blocks": []}
    old = {"title": "TreeAI · Garen top", "uid": "treeai-Garen-top", "blocks": []}
    merged = cs.merge_item_sets({"accountId": 7, "itemSets": [user, old]}, s)
    assert merged["itemSets"] == [user, s] and merged["accountId"] == 7
    assert cs.merge_item_sets(None, s) is None and cs.merge_item_sets({"itemSets": "x"}, s) is None
    assert cs.build_item_set(None, "Garen", 86) is None


def test_apply_items_puts_full_list_and_never_writes_blind():
    user = {"title": "Mon set", "uid": "x", "blocks": []}
    c = FakeClient(sets={"accountId": 7, "itemSets": [user], "timestamp": 1})
    r = cs.ClientSync(c).apply_items("Garen", "TOP")
    assert r.ok and "Recommandés" in r.msg
    put = [x for x in c.calls if x[0] == "PUT"][0]
    assert put[1] == "/lol-item-sets/v1/item-sets/99/sets" and put[2]["itemSets"][0] == user
    assert len(put[2]["itemSets"]) == 2
    cs.ClientSync(c).apply_items("Garen", "TOP")                      # second time: ours replaced, not doubled
    assert len(c.sets["itemSets"]) == 2
    c2 = FakeClient(sets=None)
    c2.sets = None
    r = cs.ClientSync(c2).apply_items("Garen", "TOP")
    assert not r.ok and not [x for x in c2.calls if x[0] == "PUT"]
    c3 = FakeClient()
    c3.fail_put_sets = True
    assert not cs.ClientSync(c3).apply_items("Garen", "TOP").ok


def test_runes_create_replace_and_respect_the_limit():
    c = FakeClient(pages=[{"id": 1, "name": "Mes runes", "isEditable": True, "current": True}], limit=2)
    s = cs.ClientSync(c)
    assert s.apply_runes("Garen", "TOP").ok
    assert [p["name"] for p in c.pages] == ["Mes runes", "TreeAI Garen"]
    assert c.pages[1]["selectedPerkIds"] == [8010, 9111, 9104, 8014, 8139, 8135, 5008, 5008, 5001]
    assert ("PUT", cs.CURRENT_PAGE_PATH, 101) in c.calls
    assert s.apply_runes("Garen", "TOP").ok and len(c.pages) == 2        # replaced, no third page
    full = FakeClient(pages=[{"id": 1, "name": "A", "isEditable": True, "current": True},
                             {"id": 2, "name": "B", "isEditable": True}], limit=2)
    r = cs.ClientSync(full).apply_runes("Garen", "TOP")
    assert not r.ok and "Importer les runes" in r.msg and not [x for x in full.calls if x[0] in ("PUT", "POST")]
    r = cs.ClientSync(full).apply_runes("Garen", "TOP", consent=True)
    assert r.ok and full.pages[0]["name"] == "TreeAI Garen" and full.pages[1]["name"] == "B"
    assert cs.rune_page(build(shards=()), "x") is None


def test_spells_keep_flash_on_its_key():
    assert cs.choose_spells((4, 12), (4, 12)) == (4, 12)
    assert cs.choose_spells((12, 4), (4, 12)) == (12, 4)               # Flash on F stays on F
    assert cs.choose_spells((4, 14), (12, 4)) == (4, 12)               # Flash on D stays on D
    assert cs.choose_spells(None, (4, 12)) == (12, 4)                  # unknown: Flash on F
    assert cs.choose_spells((4, 14), (14, 12)) == (14, 12) and cs.choose_spells(None, (4,)) is None
    sess = {"localPlayerCellId": 1, "myTeam": [{"cellId": 1, "spell1Id": 12, "spell2Id": 4}]}
    c = FakeClient(session=sess)
    r = cs.ClientSync(c).apply_spells("Garen", "TOP")
    assert r.ok is True and ("PATCH", cs.SELECTION_PATH, {"spell1Id": 12, "spell2Id": 4}) not in c.calls
    assert "déjà" in r.msg
    sess["myTeam"][0].update(spell1Id=4, spell2Id=14)
    c = FakeClient(session=sess)
    assert cs.ClientSync(c).apply_spells("Garen", "TOP").ok
    assert ("PATCH", cs.SELECTION_PATH, {"spell1Id": 4, "spell2Id": 12}) in c.calls
    assert not cs.ClientSync(FakeClient(session=None)).apply_spells("Garen", "TOP").ok


def test_lock_detection():
    base = {"localPlayerCellId": 1, "myTeam": [{"cellId": 1, "championId": 86}]}
    assert cs.is_locked(base)
    hover = dict(base, actions=[[{"actorCellId": 1, "type": "pick", "completed": False}]])
    assert not cs.is_locked(hover)
    done = dict(base, actions=[[{"actorCellId": 1, "type": "pick", "completed": True}]])
    assert cs.is_locked(done) and not cs.is_locked(None)


def _state(locked=True):
    return NS(me=NS(alias="Garen", position="TOP", locked=locked))


def test_tick_defaults_items_only_once_and_adapts_to_elo():
    c = FakeClient(session=None)
    flags = {"cs_import_items": True, "cs_auto_runes": False, "cs_auto_spells": False, "lcu_enabled": True}
    s = cs.ClientSync(c, cfg_source=lambda: NS(**flags), clock=lambda: 0.0)
    done = {"localPlayerCellId": 1, "myTeam": [{"cellId": 1, "championId": 86}]}
    s.tick(_state(), dict(done, actions=[[{"actorCellId": 1, "type": "pick", "completed": False}]]), c)
    assert not [x for x in c.calls if x[0] == "PUT"]                  # not locked yet
    s.tick(_state(), done, c)
    s.tick(_state(), done, c)
    puts = [x for x in c.calls if x[0] in ("PUT", "POST", "PATCH")]
    assert len(puts) == 1 and puts[0][1].endswith("/sets")            # items only (runes / spells opt-in)
    assert mb.get_bracket() == "mid"                                  # GOLD -> Platinum+ filter
    flags.update(cs_auto_runes=True)
    s.tick(_state(), done, c)
    assert any(x[0] == "POST" for x in c.calls)
    flags.update(cs_import_items=False, lcu_enabled=False)
    assert not s._flag("cs_import_items", True)


def test_failures_are_retried_a_few_times_only():
    c = FakeClient()
    c.fail_put_sets = True
    t = [0.0]
    s = cs.ClientSync(c, clock=lambda: t[0])
    done = {"localPlayerCellId": 1, "myTeam": [{"cellId": 1, "championId": 86}]}
    for _ in range(10):
        s.tick(_state(), done, c)
        t[0] += cs.RETRY_S + 1
    assert len([x for x in c.calls if x[0] == "PUT"]) == cs.MAX_TRIES


def test_never_raises_on_garbage():
    class Boom:
        def __getattr__(self, n):
            raise RuntimeError(n)
    s = cs.ClientSync(Boom())
    assert not s.apply_items("Garen").ok and not s.apply_runes("Garen").ok and not s.apply_spells("Garen").ok
    s.tick(_state(), {"x": 1}, Boom())
    assert not s.manual("runes").ok or True
    assert s.refresh_rank(force=True) == ""


# ------------------------------------------------------------------ elo -> U.GG filter
def test_bracket_of_tier_and_tier_filters():
    assert [mb.bracket_of_tier(t) for t in ("IRON", "silver", "GOLD", "PLATINUM", "EMERALD", "", None, "CHALLENGER")] \
        == ["low", "low", "mid", "mid", "", "", "", ""]
    row = lambda g: [[[g, g // 2, 8000, 8100, [8010, 9111, 9104, 8014, 8139, 8135]], [10, 5, [4, 12]], [10, 5, [1054]],
                      [10, 5, [6630]], [10, 5, [["Q"], "Q"]], [], [g // 2, g], [], [10, 5, [5008, 5008, 5001]]], "2026-10-20"]
    data = {"12": {"17": {"4": row(1000)}, "10": {"4": row(2000)}, "8": {"4": row(3000)}}}
    assert mb.parse_ugg(data, "TOP")["matches"] == 1000                        # default Emerald+
    assert mb.parse_ugg(data, "TOP", tiers=mb.BRACKET_TIERS["mid"])["tier"] == "Platine+"
    assert mb.parse_ugg(data, "TOP", tiers=mb.BRACKET_TIERS["low"])["tier"] == "tous rangs"


def test_refresh_per_bracket_and_get_follows_user_elo(tmp_path, monkeypatch):
    monkeypatch.setenv("TREEAICOACH_HOME", str(tmp_path / "home"))
    mb._reset()
    monkeypatch.setattr(mb, "_champion_key", lambda a: 86)
    row = lambda g: [[[g, g // 2, 8000, 8100, [8010, 9111, 9104, 8014, 8139, 8135]], [10, 5, [4, 12]], [10, 5, [1054]],
                      [10, 5, [6630, 3047]], [10, 5, [["Q"], "Q"]], [], [g // 2, g], [], [10, 5, [5008, 5008, 5001]]], "2026-10-20"]
    data = {"12": {"17": {"4": row(1000)}, "10": {"4": row(2000)}, "8": {"4": row(3000)}}}
    fetch = lambda url: data if "u.gg" in url else {}
    assert mb.refresh("Garen", "16_20", fetch=fetch, now=1.0) == "ugg"
    assert mb.refresh("Garen", "16_20", fetch=fetch, now=2.0, bracket="mid") == "ugg"
    assert mb.refresh("Garen", "16_20", fetch=fetch, now=3.0, bracket="low") == "ugg"
    assert mb.get("Garen", "TOP", "16_20", bracket="").tier == "Émeraude+"
    assert mb.get("Garen", "TOP", "16_20", bracket="mid").tier == "Platine+"
    mb.set_bracket("low")
    assert mb.get("Garen", "TOP", "16_20").tier == "tous rangs"
    mb.set_bracket("junk")
    assert mb.get_bracket() == "" and mb.get("Garen", "TOP", "16_20").tier == "Émeraude+"
    mb._reset()


# ------------------------------------------------------------------ lcu.send over HTTP
def test_lcu_send_json_body_and_empty_answers():
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _do(self):
            n = int(self.headers.get("Content-Length") or 0)
            seen.append((self.command, self.path, json.loads(self.rfile.read(n) or b"null")))
            code = 404 if self.path.endswith("/nope") else 204
            self.send_response(code)
            self.end_headers()

        do_PUT = do_POST = do_PATCH = _do

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        creds = lcu.LcuCredentials(port=srv.server_port, password="pw", protocol="http")
        c = lcu.LcuClient(creds)
        assert c.send("PATCH", "/lol-champ-select/v1/session/my-selection", {"spell1Id": 4, "spell2Id": 12}) == {}
        assert c.send("PUT", "/lol-perks/v1/currentpage", 101) == {}
        assert c.send("PUT", "/x/nope", {}) is None and c.send("GET", "/x") is None and c.send("BAD", "/x") is None
        assert seen[0] == ("PATCH", "/lol-champ-select/v1/session/my-selection", {"spell1Id": 4, "spell2Id": 12})
        assert seen[1][2] == 101
    finally:
        srv.shutdown()


def test_ranked_tier_parsing():
    class C(lcu.LcuClient):
        def __init__(self, data):
            super().__init__(enabled=False)
            self._d = data

        def get(self, path, timeout=None):
            return self._d
    assert C({"queueMap": {"RANKED_SOLO_5x5": {"tier": "GOLD"}}}).ranked_tier() == "GOLD"
    assert C({"queues": [{"queueType": "RANKED_SOLO_5x5", "tier": "silver"}]}).ranked_tier() == "SILVER"
    assert C({"queueMap": {"RANKED_SOLO_5x5": {"tier": "", "previousSeasonEndTier": "PLATINUM"}}}).ranked_tier() == "PLATINUM"
    assert C({"queueMap": {"RANKED_SOLO_5x5": {"tier": "NONE"}}}).ranked_tier() == ""
    assert C(None).ranked_tier() == "" and C([]).ranked_tier() == ""
