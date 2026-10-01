"""Tab scoreboard analysis (scoreboard.py) on the Live Client fixture + scenarios."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from treeaicoach import scoreboard as sbm
from treeaicoach.live_client import parse_allgamedata
from treeaicoach.scoreboard import ScoreboardAnalyzer, fmt_gold, item_info, items_gold, major_items

FIXTURE = Path(__file__).parent / "fixtures" / "allgamedata_sample.json"


@pytest.fixture()
def raw() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _player(raw: dict, champ: str) -> dict:
    return next(p for p in raw["allPlayers"] if p["championName"] == champ)


def _game(raw: dict, now: float, gt: float | None = None):
    d = copy.deepcopy(raw)
    if gt is not None:
        d["gameData"]["gameTime"] = gt
    return parse_allgamedata(d, now=now)


def test_item_table_bundled():
    assert item_info(3071)[1] == 3000 and item_info(3071)[2] == "legendary"
    assert item_info(1055)[2] == "starter"
    assert item_info(3047)[2] == "boots"
    assert item_info(999999) is None and item_info("x") is None
    assert items_gold([3071, 1055, 3340]) == 3450
    assert major_items([1055, 3071, 3047]) == [3071]


def test_fixture_matchups_and_summary(raw):
    a = ScoreboardAnalyzer()
    a.update(_game(raw, 1.0), 1.0)
    s = a.summary()
    assert len(s.players) == 10
    roles = [m.role for m in s.matchups]
    assert roles == ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]
    top = s.matchups[0]
    assert (top.ally, top.enemy) == ("Garen", "Darius")
    assert top.involves_me and s.my_matchup is top
    assert top.cs_diff == 96 - 110 and top.level_diff == -1
    # Garen: Doran blade + Black Cleaver + Plated steelcaps; Darius: Doran shield + Stridebreaker
    assert top.gold_diff == items_gold([1055, 3071, 3047]) - items_gold([1054, 6631])
    garen = next(p for p in s.players if p.side == "self")
    assert garen.kda == "2/1/1" and garen.cs_per_min == pytest.approx(96 / (754.3125 / 60), abs=0.06)
    assert garen.est_gold == garen.item_gold + 1234       # + my unspent gold
    assert s.ally_kills == 7 and s.enemy_kills == 8
    assert s.team_gold_diff == s.ally_gold - s.enemy_gold
    lines = s.lines()
    assert lines[0].startswith("TOP Garen vs Darius") and "CS" in lines[0] and len(lines) == 6
    assert s.hud_line().startswith("Ta voie : -14 sbires")
    d = s.to_dict()
    json.dumps(d)                                          # JSON-ready
    assert d["matchups"][0]["role"] == "TOP" and d["lines"] == lines


def test_fed_enemy_announced_once_per_tier(raw):
    _player(raw, "Darius")["scores"].update(kills=5, deaths=0)
    a = ScoreboardAnalyzer()
    out = a.update(_game(raw, 1.0), 1.0)
    texts = [i.text for i in out]
    assert any("Darius est très avancé : 5/0" in t for t in texts), texts
    assert "Darius" in a.summary().fed
    # next polls: no repeat (Lee Sin 4/1 is fed too: one insight at a time, gap respected)
    seen = list(out)
    for k in range(2, 80):
        seen += a.update(_game(raw, float(k)), float(k))
    keys = [i.key for i in seen]
    assert len(keys) == len(set(keys))
    assert sum(1 for k in keys if k.startswith("fed:Darius")) == 1
    # tier 2
    _player(raw, "Darius")["scores"].update(kills=9, deaths=0)
    more = []
    for k in range(80, 140):
        more += a.update(_game(raw, float(k)), float(k))
    assert [i.key for i in more if i.key.startswith("fed:Darius")] == ["fed:Darius:2"]


def test_insights_gap_and_threat(raw):
    _player(raw, "Darius")["scores"].update(kills=5, deaths=0)
    a = ScoreboardAnalyzer()
    assert a.update(_game(raw, 1.0), 1.0, threat=2) == []          # never during a threat
    out = a.update(_game(raw, 2.0), 2.0)
    assert len(out) == 1
    assert a.update(_game(raw, 3.0), 3.0) == []                     # gap


def test_struggling_ally(raw):
    _player(raw, "Ahri")["scores"].update(kills=0, deaths=6, assists=1)
    a = ScoreboardAnalyzer()
    got = []
    for k in range(1, 120):
        got += a.update(_game(raw, float(k)), float(k))
    st = [i for i in got if i.kind == "struggle"]
    assert len(st) == 1 and "Ahri" in st[0].text and st[0].toast_kind == "insight"
    assert "Ahri" in a.summary().struggling


def test_lane_opponent_spike_item_and_level(raw):
    a = ScoreboardAnalyzer()
    a.update(_game(raw, 1.0), 1.0)
    for _ in range(3):
        a.update(_game(raw, 1.5), 1.5)
    darius = _player(raw, "Darius")
    darius["items"].append({"itemID": 3071, "slot": 3, "price": 3000, "displayName": "Couperet noir"})
    darius["level"] = 11
    darius["scores"].update(kills=2, deaths=2)       # not "fed": both spikes get announced
    got = []
    for k in range(30, 120, 1):
        got += a.update(_game(raw, float(k)), float(k))
    kinds = {i.key.split(":")[0] for i in got}
    assert "spike" in kinds
    assert any("niveau 11" in i.text for i in got)
    assert any("Darius" in s for s in a.summary().spikes)
    # an enemy who is not my lane opponent / jungler / fed: spike recorded, not announced
    a2 = ScoreboardAnalyzer()
    a2.update(_game(raw, 1.0), 1.0)
    _player(raw, "Thresh")["items"].append({"itemID": 3071, "slot": 3})
    got2 = []
    for k in range(30, 100):
        got2 += a2.update(_game(raw, float(k)), float(k))
    assert not any("Thresh" in i.text for i in got2)
    assert any("Thresh" in s for s in a2.summary().spikes)


def test_never_raises_and_spectator(raw):
    a = ScoreboardAnalyzer()
    assert a.update(None, 1.0) == []
    assert a.update(object(), 1.0) == []
    d = copy.deepcopy(raw)
    d.pop("activePlayer")
    assert a.update(parse_allgamedata(d, now=1.0), 1.0) == []
    assert a.summary().hud_line() is None


def test_fmt_gold():
    assert fmt_gold(1500) == "+1 500 PO"
    assert fmt_gold(-312) == "-310 PO"
    assert fmt_gold(0) == "0 PO"
    assert sbm.fmt_signed(-3) == "-3" and sbm.fmt_signed(4) == "+4"
