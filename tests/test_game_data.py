"""game_data.py: runtime Data Dragon refresh (items / champions) with the bundled data as fallback."""

from __future__ import annotations

import json

import pytest

from treeaicoach import coach, game_data, itemization, scoreboard


def _item(name: str, total: int, **kw) -> dict:
    d = {"name": name, "gold": {"total": total, "base": total, "purchasable": True}, "maps": {"11": True},
         "tags": kw.pop("tags", ["Damage"])}
    d.update(kw)
    return d


def _fake_cdn(version: str, n_items: int = 60, n_champs: int = 120, calls: list | None = None):
    fr_items = {str(4000 + i): _item(f"Objet {i}", 3000 + i) for i in range(n_items)}
    fr_items["6610"] = _item("Ciel éventré", 3100)
    fr_items["9001"] = _item("Nouvel objet 2027", 2900)
    en_items = {k: {"name": f"Item {k}"} for k in fr_items}
    fr_ch = {f"Champ{i}": {"id": f"Champ{i}", "key": str(900 + i), "name": f"Champion {i}", "tags": ["Mage"]}
             for i in range(n_champs)}
    fr_ch["Newchamp"] = {"id": "Newchamp", "key": "999", "name": "Nouvelle Championne", "tags": ["Assassin"]}
    en_ch = {k: {"name": v["name"].replace("Champion", "Champ EN")} for k, v in fr_ch.items()}

    def fetch(url: str):
        if calls is not None:
            calls.append(url)
        if url.endswith("/api/versions.json"):
            return [version, "16.19.1"]
        if url.endswith("fr_FR/item.json"):
            return {"data": fr_items}
        if url.endswith("en_US/item.json"):
            return {"data": en_items}
        if url.endswith("fr_FR/champion.json"):
            return {"data": fr_ch}
        if url.endswith("en_US/champion.json"):
            return {"data": en_ch}
        raise AssertionError(url)
    return fetch


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TREEAICOACH_HOME", str(tmp_path / "home"))
    game_data.invalidate()
    yield tmp_path / "home"
    game_data.invalidate()


def test_bundled_data_is_the_fallback(home):
    assert game_data.version_tuple(game_data.data_version()) >= (16, 19)
    assert game_data.item_name(6610) == "Ciel éventré"            # 26.x name (not "Ciel fracturé")
    assert game_data.item_name(123456789, "?") == "?" and game_data.item_name("x", "d") == "d"
    assert game_data.champion_name("MonkeyKing") == "Wukong"
    assert len(game_data.champions_data()["champions"]) >= 170


def test_version_tuple():
    assert game_data.version_tuple("16.19.1") == (16, 19, 1)
    assert game_data.version_tuple("lolpatch_7.20") == ()
    assert game_data.version_tuple("17.1.1") > game_data.version_tuple("16.24.1")


def test_refresh_downloads_newer_patch_once_a_day_and_notifies(home):
    calls: list[str] = []
    seen: list[int] = []
    game_data.add_listener(lambda: seen.append(1))
    fetch = _fake_cdn("99.1.1", calls=calls)
    assert game_data.refresh(fetch=fetch, now=1000.0) == "updated"
    assert seen and game_data.data_version() == "99.1.1"
    assert game_data.item_name(9001) == "Nouvel objet 2027"
    assert game_data.champion_name("Newchamp") == "Nouvelle Championne"
    assert (home / "ddragon" / "items.json").is_file() and (home / "ddragon" / "champions.json").is_file()
    # consumers read the refreshed table
    assert itemization.load_items()[9001].name == "Nouvel objet 2027"
    assert scoreboard.item_info(9001)[0] == "Nouvel objet 2027"
    assert "Newchamp" in [c.get("alias") for c in game_data.champions_data()["champions"]]
    # less than 24 h later: nothing is downloaded
    n = len(calls)
    assert game_data.refresh(fetch=fetch, now=1000.0 + 3600) == "skipped"
    assert len(calls) == n
    # a day later, same version: only versions.json
    assert game_data.refresh(fetch=fetch, now=1000.0 + 90000) == "fresh"
    assert len(calls) == n + 1


def test_refresh_errors_keep_the_bundled_data(home):
    def offline(url: str):
        raise OSError("offline")

    assert game_data.refresh(fetch=offline, now=5.0).startswith("error")
    assert game_data.item_name(6610) == "Ciel éventré"
    # a truncated / tiny CDN answer is rejected
    assert game_data.refresh(fetch=_fake_cdn("99.2.1", n_items=3), now=5.0, force=True).startswith("error")
    assert game_data.version_tuple(game_data.data_version()) < (99,)


def test_older_cache_never_beats_the_bundle(home):
    folder = home / "ddragon"
    folder.mkdir(parents=True, exist_ok=True)
    old = {"version": "15.1.1", "lang": "fr_FR",
           "items": {str(i): {"n": "Vieux", "g": 1, "k": "other", "p": 1} for i in range(100)}}
    (folder / "items.json").write_text(json.dumps(old), encoding="utf-8")
    game_data.invalidate()
    assert game_data.data_version() != "15.1.1"


def test_refresh_async_respects_the_switch(home):
    assert game_data.refresh_async(allow_network=False) is None


def test_coach_item_names_come_from_the_data():
    assert coach.ITEM_NAMES_FR[6610] == "Ciel éventré"
    assert coach.ITEM_NAMES_FR[3031] == game_data.item_name(3031)
    assert 1001 not in coach.ITEM_NAMES_FR                         # boots are not "major"
    assert all(name == game_data.item_name(i) for i, name in coach.ITEM_NAMES_FR.items())
