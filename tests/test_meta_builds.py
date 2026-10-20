"""treeaicoach.meta_builds: live meta builds (U.GG / Riot recommendations), cache, fallbacks, wiring."""
from __future__ import annotations

import json
import os
import threading

import pytest

from treeaicoach import meta_builds as mb
from treeaicoach.itemization import load_items

ITEMS = load_items()


def ugg_payload(role_id="4", games=5000, core=(3006, 6631, 3142), opts=None, tier="14"):
    """A U.GG overview JSON of the verified layout (patch 16.19 / 16.20), one role."""
    wins = int(games * 0.52)
    row = [
        [games, wins, 8000, 8200, [8010, 9111, 9105, 8299, 8224, 8234]],   # runes
        [games, wins, [4, 14]],                                            # spells
        [games, wins, [1055, 2003]],                                       # start
        [games, wins, list(core)],                                         # core
        [games, wins, ["E", "Q", "W", "E", "E", "R"], "EQW"],              # skills
        opts if opts is not None else [[[3742, 1160, 2000], [3033, 1100, 2000], [3031, 3, 10]],
                                       [[3046, 600, 1000]], [[4401, 100, 200]], [[2055, 500, 900]], [], []],
        [wins, games], False, [games, wins, ["5008", "5008", "5001"]], [], [], [], [700000.0],
    ]
    return {"12": {tier: {role_id: [row, "2026-10-07T02:30:10Z"]}}}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("TREEAICOACH_HOME", str(tmp_path))
    from treeaicoach import paths

    paths._reset_cache()
    mb._reset()
    yield
    mb._reset()


def test_parse_ugg_layout_and_tier_fallback():
    row = mb.parse_ugg(ugg_payload(), "TOP", ITEMS)
    assert row["core"] == [3006, 6631, 3142] and row["boots"] == 3006 and row["start"] == [1055, 2003]
    assert row["spells"] == [4, 14] and row["runes"][0] == 8010 and row["styles"] == [8000, 8200]
    assert row["max"] == "EQW" and row["tier"] == "Émeraude+" and row["fetched"] == "2026-10-07"
    assert row["matches"] == 5000 and 51 <= row["core_wr"] <= 53
    # the 4th-item options keep their win rate; a rare option (< 4 % of the slot) is dropped
    assert [o[0] for o in row["slots"][0]] == [3742, 3033] and row["slots"][0][0][1] == 58.0
    # too few games in Emerald+: the next tier (Platinum+) is used; no role data -> None
    small = ugg_payload(games=50)
    small["12"]["10"] = ugg_payload(games=900, tier="10")["12"]["10"]
    assert mb.parse_ugg(small, "TOP", ITEMS)["tier"] == "Platine+"
    assert mb.parse_ugg(ugg_payload(), "JUNGLE", ITEMS) is None
    assert mb.parse_ugg({"garbage": 1}, "TOP") is None and mb.parse_ugg(None, "TOP") is None


def test_parse_riot_and_compact():
    data = [{"championId": 86, "runeRecommendations": [
        {"position": "NONE", "mapId": 12, "isDefaultPosition": True, "perkIds": [1, 2, 3, 4, 5, 6, 7, 8, 9],
         "primaryPerkStyleId": 8000, "secondaryPerkStyleId": 8400, "summonerSpellIds": [4, 32]},
        {"position": "TOP", "mapId": 11, "isDefaultPosition": True, "perkIds": [8010, 9111, 9105, 8299, 8224, 8234,
                                                                                 5008, 5008, 5001],
         "primaryPerkStyleId": 8000, "secondaryPerkStyleId": 8200, "summonerSpellIds": [4, 14],
         "recommendationId": "2026-09-29.000710"}]}]
    row = mb.parse_riot(data, 86, "TOP")
    assert row["runes"] == [8010, 9111, 9105, 8299, 8224, 8234] and row["shards"] == [5008, 5008, 5001]
    assert row["spells"] == [4, 14] and row["fetched"] == "2026-09-29"
    assert mb.parse_riot(data, 86, "UTILITY")["runes"][0] == 8010      # unknown position: the Rift default
    assert mb.parse_riot(data, 999, "TOP") is None
    table = mb.compact_riot(data)
    assert set(table) == {"86"} and set(table["86"]) == {"TOP"} and "position" not in table["86"]["TOP"]


def test_refresh_caches_and_get_never_blocks(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        if "u.gg" in url:
            assert url == mb.UGG_URL.format(patch="16_19", key=86)
            return ugg_payload()
        if "communitydragon" in url:
            return [{"championId": 86, "runeRecommendations": [
                {"position": "TOP", "mapId": 11, "perkIds": [8010, 9111, 9105, 8299, 8224, 8234, 5008, 5008, 5001],
                 "primaryPerkStyleId": 8000, "secondaryPerkStyleId": 8200, "summonerSpellIds": [4, 14]}]}]
        if "runesReforged" in url:
            return [{"id": 8000, "name": "Précision", "slots": [{"runes": [{"id": 8010, "name": "Conquérant"}]}]}]
        if "summoner.json" in url:
            return {"data": {"SummonerFlash": {"key": "4", "name": "Saut éclair"},
                             "SummonerDot": {"key": "14", "name": "Embrasement"}}}
        raise AssertionError(url)

    assert mb.refresh("Garen", patch="16_19", fetch=fetch, now=1000.0) == "ugg"
    assert mb.refresh("Garen", patch="16_19", fetch=fetch, now=2000.0) == "skipped"      # once a day
    folder = mb.cache_folder("16_19")
    assert (folder / "Garen.json").is_file() and (folder / mb.RIOT_FILE).is_file() and (folder / mb.NAMES_FILE).is_file()
    b = mb.get("Garen", "TOP", patch="16_19")
    assert b is not None and b.source == "ugg" and b.core == (3006, 6631, 3142) and b.boots == 3006
    assert b.fetched == "2026-10-07" and b.tier == "Émeraude+" and b.has_items and 51 <= b.win_rate <= 53
    assert b.options[0] == (3046, 60.0, 1000) and b.options[1] == (3742, 58.0, 2000)
    # a role U.GG has no data for: Riot's recommendation of that role (runes / spells, no items)
    r = mb.get("Garen", "UTILITY", patch="16_19")
    assert r is not None and r.source == "riot" and not r.has_items and r.spells == (4, 14)
    # the French names of the cache are used by describe()
    d = mb.describe(b, ITEMS)
    assert d["spells"] == "Saut éclair + Embrasement" and d["runes"].startswith("Conquérant (Précision")
    assert d["core"].startswith("Jambières du berzerker → Estropieur") and "Plaque du mort (58 %)" in d["options"]
    assert d["skills"] == "E > Q > W" and d["source"].startswith("U.GG, patch 16.19, Émeraude+, 2026-10-07")
    # a network failure never raises, is stamped, and the cache stays usable
    def boom(url):
        raise OSError("offline")
    assert mb.refresh("Garen", patch="16_19", fetch=boom, now=200000.0, force=True) == "riot"   # riot file kept
    assert mb.get("Garen", "TOP", patch="16_19").core == (3006, 6631, 3142)


def test_ensure_async_runs_in_a_thread_once(monkeypatch):
    seen = []

    def fetch(url):
        seen.append(threading.current_thread().name)
        return ugg_payload()
    mb._reset(fetch)
    monkeypatch.setattr(mb, "_patch", lambda: "16_19")
    t = mb.ensure_async("Garen", "TOP")
    assert t is not None
    t.join(10.0)
    assert seen and all(n.startswith("meta-build-") for n in seen) and threading.current_thread().name not in seen
    assert mb.ensure_async("Garen", "TOP") is None        # cached now: no new thread
    assert mb.ensure_async("", "TOP") is None and mb.ensure_async("Garen", allow_network=False) is None


def test_bundled_fallback_and_next_item(monkeypatch):
    monkeypatch.setattr(mb, "_patch", lambda: "99_1")   # nothing cached for that patch
    b = mb.get("Garen", "TOP")
    assert b is not None and b.source.startswith("bundled:") and b.spells
    if b.has_items:                                       # the bundled table was fetched (tools/fetch_meta_builds.py)
        nxt = mb.next_item(b, [1055, 2003, 3340], ITEMS)
        assert nxt is not None and ITEMS[nxt[0]].kind != "boots" and nxt[1].startswith("objet ")
        done = mb.next_item(b, list(b.core), ITEMS)
        assert done is None or done[0] in {i for i, _w, _n in b.options}
    assert mb.get("", "TOP") is None and mb.get("NoSuchChampion", "TOP") is None


def test_compare_final_items():
    row = mb.parse_ugg(ugg_payload(), "TOP", ITEMS)
    b = mb.build_from_row("Garen", "TOP", row, patch="16_19", source="ugg")
    c = mb.compare(b, [3006, 6631, 3742, 3031, 2055, 1055], ITEMS)
    assert c["core_done"] == ["Jambières du berzerker", "Estropieur"] and c["core_missing"] == ["Lame spectre de Youmuu"]
    assert c["options_bought"] == [("Plaque du mort", 58.0)] and c["off_meta"] == ["Lame d'infini"]
    assert 0 <= c["score"] <= 100
    assert mb.compare(None, [1, 2])["score"] is None


def test_itemization_uses_the_meta_build_and_flags_it(monkeypatch):
    from treeaicoach import itemization as iz
    from treeaicoach.live_client import GameInfo, PlayerInfo

    row = mb.parse_ugg(ugg_payload(core=(3006, 6631, 3142)), "TOP", ITEMS)
    build = mb.build_from_row("Garen", "TOP", row, patch="16_19", source="ugg")
    monkeypatch.setattr(mb, "get", lambda alias, role=None, patch=None: build if alias == "Garen" else None)
    me = PlayerInfo(champion_alias="Garen", champion_name="Garen", team="ORDER", items=[1055, 2003], level=5,
                    position="TOP", scores={"kills": 0, "deaths": 0, "assists": 0})
    enemies = [PlayerInfo(champion_alias=a, champion_name=a, team="CHAOS", items=[], level=5, position="",
                          scores={"kills": 0, "deaths": 0, "assists": 0})
               for a in ("Darius", "LeeSin", "Ahri", "Jinx", "Thresh")]
    game = GameInfo(game_time=300.0, me=me, enemies=enemies, current_gold=800.0)
    rec = iz.recommend(game)
    assert rec is not None and rec.item_id == 6631 and rec.meta and rec.source == "ugg" and rec.win_rate > 50
    assert not rec.affordable and "build du moment" in rec.text and rec.buy_now      # components first
    rich = GameInfo(game_time=300.0, me=me, enemies=enemies, current_gold=ITEMS[6631].gold + 100.0)
    rec = iz.recommend(rich)
    assert rec.affordable and rec.buy_text.startswith("Achat immédiat : Estropieur")
    # disabled by the environment: the rules again
    monkeypatch.setenv("TREEAI_META_BUILDS", "0")
    rec2 = iz.recommend(game)
    assert rec2 is not None and not rec2.meta


def test_champ_select_card_shows_spells_runes_and_build(monkeypatch):
    from tests.test_champ_select import session
    from treeaicoach import champ_select as cs

    row = mb.parse_ugg(ugg_payload(), "TOP", ITEMS)
    build = mb.build_from_row("Garen", "TOP", row, patch="16_19", source="ugg")
    mb._names_mem = {"perks": {"8010": "Conquérant"}, "styles": {"8000": "Précision", "8200": "Sorcellerie"},
                     "spells": {"4": "Saut éclair", "14": "Embrasement"}}
    monkeypatch.setattr(mb, "get", lambda alias, role=None, patch=None: build if alias == "Garen" else None)
    card = cs.build_card(cs.parse_session(session(me="Garen", my_pos="top")))
    assert card is not None and card.spells == "Saut éclair + Embrasement" and card.runes.startswith("Conquérant")
    assert card.core_items.startswith("Jambières du berzerker → Estropieur") and card.meta_source.startswith("U.GG")
    assert card.start_items[0][0] == 1055 and card.start_why == "le départ le plus joué du moment"
    assert any(x.startswith("Build : ") for x in card.lines) and card.lines[-1].startswith("Saut éclair + Embrasement · ")
    assert len(card.lines) <= 7 and all(len(x) <= 110 for x in card.lines)


def test_resources_gold_line_names_the_affordable_item(monkeypatch):
    from treeaicoach import resources_coach as rc
    from treeaicoach.itemization import Recommendation

    rec = Recommendation(6631, "Estropieur", "core", "", (6631,), ("Estropieur",), True, 3200, meta=True)
    monkeypatch.setattr("treeaicoach.itemization.recommend", lambda game, role=None, **kw: rec)
    inp = rc.ResourceInputs(t=0.0, gt=600.0, game=object())
    assert rc.ResourcesCoach._affordable_item(inp) == "Estropieur"
    rec2 = Recommendation(6631, "Estropieur", "core", "", (1036,), ("Épée longue",), False, 500)
    monkeypatch.setattr("treeaicoach.itemization.recommend", lambda game, role=None, **kw: rec2)
    assert rc.ResourcesCoach._affordable_item(inp) is None


def test_bundled_file_schema():
    from treeaicoach.paths import asset_path

    p = asset_path(mb.BUNDLED_FILE)
    if not p.is_file():
        pytest.skip("assets/meta_builds.json not bundled")
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["schema"] == mb.SCHEMA and d["patch"] and d["generated"] and isinstance(d["champions"], dict)
    assert d["riot"] and d["names"]["perks"] and d["names"]["spells"]
    for alias, c in d["champions"].items():
        for role, row in c["roles"].items():
            assert role in mb.ROLES and row["matches"] >= mb.MIN_MATCHES
            b = mb.build_from_row(alias, role, row)
            assert b is not None and b.core and all(i in ITEMS for i in b.core)
    assert os.path.getsize(p) < 1_500_000
