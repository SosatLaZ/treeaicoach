"""Tests for treeaicoach.reminders (recall gold / control ward reminders)."""

from __future__ import annotations

import numpy as np
import pytest

from treeaicoach import reminders as rem_mod
from treeaicoach.alerts import AlertKind, AlertThrottler, Level
from treeaicoach.config import Config
from treeaicoach.live_client import GameInfo, PlayerInfo
from treeaicoach.reminders import CONTROL_WARD_ID, PersonalReminders, format_gold

BASE = (0.07, 0.93)          # ORDER fountain
LANE = (0.10, 0.30)          # top lane
ENEMY_BASE = (0.93, 0.07)


def make_game(gt: float, gold: float = 0.0, items=(), dead: bool = False, team: str = "ORDER",
              map_number: int = 11, spectator: bool = False) -> GameInfo:
    me = PlayerInfo(riot_id="Moi#EUW", summoner_name="Moi#EUW", champion_alias="Garen", team=team,
                    is_dead=dead, items=list(items), current_gold=gold)
    other = "CHAOS" if team == "ORDER" else "ORDER"
    enemies = [PlayerInfo(riot_id=f"E{i}#EUW", champion_alias=f"C{i}", team=other) for i in range(5)]
    return GameInfo(game_time=gt, game_mode="CLASSIC", map_number=map_number, me=None if spectator else me,
                    enemies=enemies, current_gold=0.0 if spectator else gold, fetched_at=gt)


def step(r: PersonalReminders, t: float, pos=LANE, in_base: bool = False, **kw):
    return r.update(t, make_game(t, **kw), pos, in_base)


def kinds(alerts) -> list:
    return [a.kind for a in alerts]


# --------------------------------------------------------------------------- recall


def test_recall_reminder_threshold_and_repeat():
    r = PersonalReminders(Config())
    assert step(r, 300.0, gold=1299) == []
    a = step(r, 301.0, gold=1340)
    assert kinds(a) == [AlertKind.RECALL_GOLD]
    assert a[0].level == Level.INFO and a[0].key == "recall_gold" and a[0].t == 301.0
    assert a[0].text == "Tu as 1300 pièces d'or, pense à rentrer."
    assert r.hint() == "1 340 PO — pense à rentrer"
    for t in np.arange(302.0, 391.0, 0.5):                       # at most every 90 s
        assert step(r, float(t), gold=1500) == []
    assert kinds(step(r, 391.0, gold=1500)) == [AlertKind.RECALL_GOLD]


def test_no_recall_in_base_dead_unknown_position_or_early():
    r = PersonalReminders(Config())
    ward = [CONTROL_WARD_ID]                                         # (no control ward reminder)
    assert step(r, 300.0, pos=BASE, gold=2000, items=ward) == []     # in my base (position)
    assert step(r, 301.0, pos=(0.5, 0.5), in_base=True, gold=2000, items=ward) == []   # engine: base
    assert step(r, 302.0, pos=(0.5, 0.5), gold=2000, items=ward) == []   # just left (debounce)
    r = PersonalReminders(Config())
    assert step(r, 300.0, gold=2000, dead=True) == []
    assert step(r, 301.0, pos=None, gold=2000) == []                 # position unknown
    assert step(r, 301.5, pos=(float("nan"), 0.2), gold=2000) == []
    assert step(r, 305.0, gold=2000) == []                           # left the fountain (debounce)
    assert step(r, 309.0, gold=2000) == []                           # respawned with 2000: saving
    assert kinds(step(r, 310.0, gold=2250)) == [AlertKind.RECALL_GOLD]   # +250 gold
    r = PersonalReminders(Config())
    assert step(r, 80.0, gold=2000) == []                            # before 1:30
    r = PersonalReminders(Config(recall_reminder=False))
    assert step(r, 300.0, gold=5000) == [] and r.hint() is None


def test_recall_threshold_from_config_and_apply_config():
    r = PersonalReminders(Config(recall_gold_threshold=2000))
    assert step(r, 300.0, gold=1900) == []
    r.apply_config(Config(recall_gold_threshold=1500))
    assert kinds(step(r, 301.0, gold=1900)) == [AlertKind.RECALL_GOLD]


def test_leaving_base_with_gold_needs_more_gold_before_reminding():
    r = PersonalReminders(Config())
    step(r, 300.0, pos=BASE, gold=1600)                              # in base, saving gold
    for t in (301.0, 302.0, 303.0, 304.5):                           # leaves (debounced exit)
        assert step(r, t, gold=1600) == []
    assert step(r, 400.0, gold=1800) == []                           # +200 only
    assert kinds(step(r, 401.0, gold=1860)) == [AlertKind.RECALL_GOLD]


def test_enemy_base_is_not_my_base():
    r = PersonalReminders(Config())
    assert kinds(step(r, 300.0, pos=ENEMY_BASE, in_base=True, gold=1500)) == [AlertKind.RECALL_GOLD]
    r = PersonalReminders(Config())
    red = make_game(300.0, gold=1500, team="CHAOS")
    assert r.update(300.0, red, ENEMY_BASE, False) == []            # (0.93, 0.07) IS the CHAOS base


# --------------------------------------------------------------------------- control ward


def test_control_ward_once_per_base_visit():
    r = PersonalReminders(Config())
    assert step(r, 400.0, pos=BASE, gold=500) == []                  # just arrived (dwell)
    a = step(r, 401.0, pos=BASE, gold=500)
    assert kinds(a) == [AlertKind.CONTROL_WARD] and a[0].key == "control_ward"
    assert a[0].text == "Pense à acheter une balise de contrôle."
    for t in np.arange(401.5, 420.0, 0.5):
        assert step(r, float(t), pos=BASE, gold=500) == []            # once per visit
    for t in (420.0, 421.0):                                         # flicker out of base < 3 s
        step(r, t, gold=500)
    assert step(r, 422.0, pos=BASE, gold=500) == [] and step(r, 424.0, pos=BASE, gold=500) == []
    for t in (430.0, 431.0, 432.0, 434.0):                           # really leaves
        step(r, t, gold=50)
    assert step(r, 600.0, pos=BASE, gold=500) == []
    assert step(r, 601.5, pos=BASE, gold=500) == []                  # new visit but < 5 min: anti-spam
    for t in (610.0, 611.0, 612.0, 614.0):
        step(r, t, gold=50)
    assert step(r, 760.0, pos=BASE, gold=500) == []
    assert kinds(step(r, 761.5, pos=BASE, gold=500)) == [AlertKind.CONTROL_WARD]   # new visit after cooldown


@pytest.mark.parametrize("kw", [
    {"gold": 74},                                                    # cannot afford
    {"gold": 500, "items": [CONTROL_WARD_ID]},                       # already has one
    {"gold": 500, "items": [1055, 3071, 3047, 3006, 3031, 3036, 3340]},   # 6 items + trinket: full
])
def test_no_control_ward_reminder_when_not_needed(kw):
    r = PersonalReminders(Config())
    step(r, 400.0, pos=BASE, **kw)
    assert step(r, 402.0, pos=BASE, **kw) == []


def test_control_ward_rules_time_config_and_trinkets():
    r = PersonalReminders(Config())
    step(r, 170.0, pos=BASE, gold=500)
    assert step(r, 175.0, pos=BASE, gold=500) == []                  # before 3:00
    assert kinds(step(r, 181.0, pos=BASE, gold=500)) == [AlertKind.CONTROL_WARD]   # same visit, now 3:00+
    r = PersonalReminders(Config())
    items = [1055, 3071, 3047, 3006, 3031, 3340]                     # 5 items + trinket: room left
    step(r, 400.0, pos=BASE, gold=500, items=items)
    assert kinds(step(r, 401.5, pos=BASE, gold=500, items=items)) == [AlertKind.CONTROL_WARD]
    assert r.hint() == "Pense à la balise de contrôle"
    r = PersonalReminders(Config(control_ward_reminder=False))
    step(r, 400.0, pos=BASE, gold=500)
    assert step(r, 402.0, pos=BASE, gold=500) == []


def test_control_ward_when_dead_or_just_respawned():
    r = PersonalReminders(Config())
    step(r, 500.0, gold=300)
    assert step(r, 501.0, pos=None, gold=300, dead=True) == []       # death recap first
    assert step(r, 505.0, pos=None, gold=300, dead=True) == []
    assert step(r, 507.5, pos=None, gold=300, dead=True) == []       # dead >= 6 s: can shop (dwell)
    a = step(r, 508.5, pos=None, gold=300, dead=True)
    assert kinds(a) == [AlertKind.CONTROL_WARD]
    assert step(r, 520.0, pos=None, gold=300) == []                  # respawn: same visit
    r = PersonalReminders(Config(control_ward_reminder=False))
    step(r, 500.0, gold=300, dead=True)
    r.apply_config(Config())
    step(r, 501.0, pos=None, gold=300)                               # respawned, position unknown
    assert kinds(step(r, 502.5, pos=None, gold=300)) == [AlertKind.CONTROL_WARD]


def test_engine_in_base_flag_is_used():
    r = PersonalReminders(Config())
    step(r, 400.0, pos=(0.2, 0.8), in_base=True, gold=500)
    assert kinds(step(r, 401.5, pos=None, in_base=True, gold=500)) == [AlertKind.CONTROL_WARD]


# --------------------------------------------------------------------------- robustness


def test_other_modes_spectator_and_garbage():
    r = PersonalReminders(Config())
    assert step(r, 400.0, gold=5000, map_number=12) == []            # ARAM
    assert step(r, 401.0, gold=5000, spectator=True) == []
    assert r.update(402.0, None, LANE, False) == []
    assert r.update(float("nan"), make_game(400.0, gold=5000), LANE, False) == []
    assert r.update(403.0, object(), "garbage", "yes") == []         # type: ignore[arg-type]
    assert r.update(404.0, make_game(400.0, gold=5000), (np.float32(0.1), np.float64(0.3)), np.bool_(False))
    assert PersonalReminders(object()).update(405.0, make_game(405.0, gold=5000), LANE, False)


def test_new_game_resets_and_reset_method():
    r = PersonalReminders(Config())
    assert step(r, 1000.0, gold=2000) != []
    assert step(r, 1001.0, gold=2000) == []
    # next game (clock back): the 90 s cooldown is forgotten
    assert kinds(r.update(1002.0, make_game(200.0, gold=2000), LANE, False)) == [AlertKind.RECALL_GOLD]
    assert r.update(1003.0, make_game(201.0, gold=2000), LANE, False) == []
    r.reset()
    assert r.hint() is None
    assert kinds(step(r, 1004.0, gold=2000)) == [AlertKind.RECALL_GOLD]


def test_alerts_pass_the_throttler_and_format_gold():
    r = PersonalReminders(Config())
    a = step(r, 300.0, gold=1400)
    assert AlertThrottler().filter(a, 300.0) == a
    assert format_gold(1450.7) == "1 450" and format_gold(-3) == "0" and format_gold(950) == "950"
    assert rem_mod.CONTROL_WARD_ID == 2055
