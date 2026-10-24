"""Team brain (team_brain.py): per-friend calls of the bot Discord, their budget in CallFilter."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from treeaicoach import discord_bot as db
from treeaicoach import geometry
from treeaicoach import team_brain as TB
from treeaicoach.config import Config
from treeaicoach.jungler_map import GRID, JunglerMap

from tests.test_discord_bot import bot  # noqa: F401  (fixture)

BOT, TOP, MID = (0.93, 0.93), (0.07, 0.07), (0.5, 0.5)


def _p(alias, pos, team="ORDER", cs=0, k=0, a=0, dead=False, name=None):  # noqa: ANN001, ANN202
    return SimpleNamespace(champion_alias=alias, champion_name=name or alias, position=pos, team=team, is_dead=dead,
                           respawn_timer=0.0, level=6, items=[], scores={"creepScore": cs, "kills": k, "assists": a})


def _tr(alias, uv, visible=True, last_seen=0.0):  # noqa: ANN001, ANN202
    return SimpleNamespace(alias=alias, position=lambda: uv, visible=visible, last_seen=last_seen)


class _Tracker:
    def __init__(self, me, allies, enemies):  # noqa: ANN001
        self.m, self.a, self.e = me, allies, enemies

    def me(self):  # noqa: ANN201
        return self.m

    def allies(self, visible_only=False):  # noqa: ANN001, ANN201
        return self.a

    def enemies(self, visible_only=False):  # noqa: ANN001, ANN201
        return self.e


def _world(t, jinx_uv=BOT, ahri_uv=(0.4, 0.6), enemy_seen=None):  # noqa: ANN001, ANN202
    me = _p("Garen", "TOP")
    allies = [_p("Jinx", "BOTTOM"), _p("Thresh", "UTILITY"), _p("Ahri", "MIDDLE"), _p("Vi", "JUNGLE")]
    enemies = [_p(a, r, "CHAOS") for a, r in (("Darius", "TOP"), ("LeeSin", "JUNGLE"), ("Zed", "MIDDLE"),
                                               ("Caitlyn", "BOTTOM"), ("Lux", "UTILITY"))]
    game = SimpleNamespace(me=me, allies=allies, enemies=enemies, current_gold=0.0, is_summoners_rift=True)
    seen = enemy_seen or {}
    tracker = _Tracker(_tr("Garen", TOP, last_seen=t),
                       [_tr("Jinx", jinx_uv, last_seen=t), _tr("Thresh", jinx_uv, last_seen=t),
                        _tr("Ahri", ahri_uv, last_seen=t), _tr("Vi", (0.3, 0.6), last_seen=t)],
                       [_tr(a, (0.9, 0.1), visible=False, last_seen=seen.get(a, -1e9))
                        for a in ("Darius", "LeeSin", "Zed", "Caitlyn", "Lux")])
    return game, tracker


FRIENDS = ["Jinx", "Thresh", "Ahri", "Vi"]


def test_positions_are_lanes() -> None:
    assert [geometry.lane_of(geometry.classify_zone(*p)) for p in (BOT, TOP, MID)] == ["bot", "top", "mid"]


def test_opponent_based_line_once_and_merged_for_the_bot_duo() -> None:
    b = TB.TeamBrain()
    game, tr = _world(300.0)
    lines = b.update(300.0, 300.0, game, tr, FRIENDS, recalls=[("zed", 297.0)])
    assert [ln.text for ln in lines] == ["Ahri, ton adversaire est en base : plaque"]
    assert lines[0].targets == ("ahri",)
    b.ack(lines[0], True)
    assert b.update(301.0, 301.0, game, tr, FRIENDS, recalls=[("zed", 297.0)]) == []      # said once
    lines = b.update(302.0, 302.0, game, tr, FRIENDS, recalls=[("caitlyn", 300.0), ("lux", 300.5)])
    assert [ln.text for ln in lines] == ["Jinx et Thresh, vos deux adversaires sont en base : plaquez"]
    # seen since his purchase / old purchase / before 3:00: nothing
    b2 = TB.TeamBrain()
    game, tr = _world(400.0, enemy_seen={"Zed": 399.0})
    assert b2.update(400.0, 400.0, game, tr, FRIENDS, recalls=[("zed", 397.0)]) == []
    assert b2.update(410.0, 410.0, game, tr, FRIENDS, recalls=[("darius", 395.0)]) == []
    assert TB.TeamBrain().update(120.0, 120.0, *_world(120.0), FRIENDS, recalls=[("zed", 118.0)]) == []
    # after 14:00: "pousse ta lane"; the friend jungler: "leur jungler est en base"
    lines = TB.TeamBrain().update(900.0, 900.0, *_world(900.0), FRIENDS, recalls=[("zed", 898.0), ("leesin", 899.0)])
    assert sorted(ln.text for ln in lines) == ["Ahri, ton adversaire est en base : pousse ta lane",
                                               "Vi, leur jungler est en base : joue fort"]


def _grid_near(uv, p=0.9):  # noqa: ANN001, ANN202
    g = np.zeros((GRID, GRID), np.float32)
    m = JunglerMap.near_mask(uv, 300.0, 5.0)
    g[m] = p / max(1, int(m.sum()))
    g[0, GRID - 1] += 1.0 - p
    return g


def test_jungler_probably_on_the_friend_only_when_localised_and_unseen() -> None:
    b = TB.TeamBrain()
    game, tr = _world(300.0)
    jm = SimpleNamespace(visible=False, dead=False, grid=_grid_near((0.4, 0.6)))
    lines = b.update(300.0, 300.0, game, tr, FRIENDS, jmap=jm)
    assert [ln.text for ln in lines] == ["Ahri, jungler probablement chez toi"] and lines[0].rule == "jungler"
    b.ack(lines[0], True)
    assert b.update(301.0, 301.0, game, tr, FRIENDS, jmap=jm) == []                 # not re-armed
    # the bot duo: one line for both
    lines = TB.TeamBrain().update(300.0, 300.0, game, tr, FRIENDS,
                                  jmap=SimpleNamespace(visible=False, dead=False, grid=_grid_near(BOT)))
    assert [ln.text for ln in lines] == ["Jinx et Thresh, jungler probablement chez vous"]
    # visible, seen 5 s ago, or spread over the map: nothing
    assert TB.TeamBrain().update(300.0, 300.0, game, tr, FRIENDS,
                                 jmap=SimpleNamespace(visible=True, dead=False, grid=_grid_near((0.4, 0.6)))) == []
    game2, tr2 = _world(300.0, enemy_seen={"LeeSin": 295.0})
    assert TB.TeamBrain().update(300.0, 300.0, game2, tr2, FRIENDS, jmap=jm) == []
    flat = np.full((GRID, GRID), 1.0 / GRID ** 2, np.float32)
    assert TB.TeamBrain().update(300.0, 300.0, game, tr, FRIENDS,
                                 jmap=SimpleNamespace(visible=False, dead=False, grid=flat)) == []


def test_back_sync_from_income_since_last_purchase(monkeypatch) -> None:  # noqa: ANN001
    values = {"Jinx": 500.0, "Thresh": 400.0}
    monkeypatch.setattr(TB, "_items_value", lambda p: values.get(p.champion_alias, 0.0))
    b = TB.TeamBrain()
    game, tr = _world(100.0)
    b.update(100.0, 100.0, game, tr, FRIENDS)                  # starter items: the anchor
    assert b.unspent(game.allies[0], 100.0) == TB.GOLD_LEFT
    game, tr = _world(400.0)
    game.allies[0].scores.update(creepScore=40, kills=1)
    game.allies[1].scores.update(assists=2)
    lines = [ln for ln in b.update(400.0, 400.0, game, tr, FRIENDS) if ln.rule == "sync"]
    assert [ln.text for ln in lines] == ["Jinx et Thresh, rentrez ensemble : assez d'or pour un objet"]
    # Jinx bought: the anchor moves, no more sync
    values["Jinx"] = 2000.0
    b2_lines = [ln for ln in b.update(600.0, 600.0, game, tr, FRIENDS) if ln.rule == "sync"]
    assert b2_lines == []
    # an objective in 30 s: never "rentrez" (the objective line says what to do)
    obj = [SimpleNamespace(key="dragon", alive=False, remaining=30.0, next_spawn=430.0)]
    b3 = TB.TeamBrain()
    b3.update(100.0, 100.0, *_world(100.0), FRIENDS)
    assert [ln for ln in b3.update(400.0, 400.0, game, tr, FRIENDS, objectives=obj) if ln.rule == "sync"] == []
    # app started late: unknown gold, nothing
    b4 = TB.TeamBrain()
    assert [ln for ln in b4.update(400.0, 400.0, game, tr, FRIENDS) if ln.rule == "sync"] == []


def test_objective_team_line_and_retry(monkeypatch) -> None:  # noqa: ANN001
    b = TB.TeamBrain()
    game, tr = _world(240.0)
    obj = [SimpleNamespace(key="dragon", alive=False, remaining=60.0, next_spawn=300.0)]
    lines = b.update(240.0, 240.0, game, tr, FRIENDS, objectives=obj)
    assert [ln.text for ln in lines] == ["Dragon dans 1:00 : bot et jungle, rentrez maintenant"]
    ln = lines[0]
    assert ln.team and ln.key == "prep:dragon:300" and set(ln.targets) == {"jinx", "thresh", "ahri", "vi"}
    b.ack(ln, False)                                           # refused: offered again 2 s later, 10 s at most
    assert b.update(241.0, 241.0, game, tr, FRIENDS, objectives=obj) == []
    assert b.update(242.5, 242.5, game, tr, FRIENDS, objectives=obj) == [ln]
    b.ack(ln, True)
    assert b.update(245.0, 245.0, game, tr, FRIENDS, objectives=obj) == []
    # the fight is lost on paper: play the other side
    import treeaicoach.fog_intel as fi

    monkeypatch.setattr(fi, "fight_odds", lambda ctx, gold_of=None: {"verdict": "lose", "gold": -3200})
    lines = TB.TeamBrain().update(240.0, 240.0, game, tr, FRIENDS, objectives=obj)
    assert lines[0].text == "Dragon dans 1:00 : ils ont 3,2k d'or d'avance, jouez les larves ou le haut"
    assert all(len(p) <= db.TEAM_MAX_CHARS for p in TB.phrases())


def test_no_friend_or_garbage_never_raises() -> None:
    b = TB.TeamBrain()
    game, tr = _world(300.0)
    assert b.update(300.0, 300.0, game, tr, []) == []
    assert b.update(float("nan"), None, None, None, FRIENDS) == []
    assert b.update(300.0, 300.0, SimpleNamespace(me=None), tr, FRIENDS) == []


# ------------------------------------------------------------------------------ the bot's budget
def test_filter_one_line_per_friend_per_30s_gank_first() -> None:
    now = [1000.0]
    f = db.CallFilter(lambda: now[0])
    assert f.admit("team_brain", "a", targets=("jinx",), rule="opp")
    now[0] += 5
    assert not f.admit("team_brain", "b", targets=("jinx",), rule="sync")        # same friend within 30 s
    assert f.admit("gank", "bot", target="Jinx")                                   # a gank always goes first
    now[0] += 26
    assert not f.admit("team_brain", "c", targets=("jinx",), rule="opp")         # 26 s after his gank call
    assert f.admit("team_brain", "d", targets=("ahri",), rule="opp")             # another friend: ok
    now[0] += 1
    assert f.admit("team_brain", "e", targets=("vi",), rule="jungler")          # 27 s after a gank: ok
    now[0] += 30
    assert f.admit("team_brain", "f", targets=("jinx",), rule="opp")
    # the team line counts for every friend and is deduped with team_calls.prep_line (same key)
    now[0] += 40
    assert f.admit("team_brain", "prep:dragon:300", targets=("jinx", "ahri", "vi"), rule="objective")
    assert not f.admit("team_call", "prep:dragon:300")
    now[0] += 5
    assert not f.admit("team_brain", "g", targets=("vi",), rule="opp")
    assert not f.admit("team_brain", "h", targets=(), rule="opp")                # nobody addressed


def test_filter_jungler_line_never_near_a_gank_and_global_cap() -> None:
    now = [1000.0]
    f = db.CallFilter(lambda: now[0])
    assert f.admit("gank", "top")
    now[0] += 10
    assert not f.admit("team_brain", "j", targets=("ahri",), rule="jungler")     # within 20 s of a gank
    assert f.admit("team_brain", "o", targets=("ahri",), rule="opp")
    for i, w in enumerate(("a", "b", "c", "d")):
        now[0] += 4
        f.admit("team_brain", f"x{i}", targets=(w,), rule="opp")
    assert f._recent(f._all_times, now[0]) == db.CALLS_MAX_PER_MIN
    now[0] += 4
    assert not f.admit("team_brain", "y", targets=("e",), rule="opp")            # CALLS_MAX_PER_MIN
    assert f.admit("gank", "bot", target="Jinx")                                   # never refused by the cap


def test_switch_default_on_and_bot_queues_it(bot) -> None:  # noqa: ANN001
    assert Config().discord_team_brain is True
    s = db._Settings()
    assert s.wants("team_brain")
    assert not db._Settings(brain=False).wants("team_brain")
    from tests.test_discord_bot import _cfg, _wait

    bot.configure(_cfg())
    bot.on_call("game_start")
    assert bot.on_call("team_brain", "Ahri, jungler probablement chez toi",
                       {"key": "k", "rule": "jungler", "targets": ["ahri"]})
    assert not bot.on_call("team_brain", "Ahri, ton adversaire est en base : plaque",
                           {"key": "k2", "rule": "opp", "targets": ["ahri"]})      # 30 s per friend
    assert _wait(lambda: "Ahri, jungler probablement chez toi" in bot.spoken)
    bot.configure(_cfg(discord_team_brain=False))
    assert not bot.on_call("team_brain", "Vi, x", {"key": "k3", "rule": "opp", "targets": ["vi"]})


def test_engine_hook_sends_lines_and_acks() -> None:
    from tests.test_discord_friends import _Eng

    e = _Eng(Config(discord_enabled=True), {})
    e.brain_friends = lambda: {"Ahri": "Bob", "Jinx": "Paul"}
    e._objectives = SimpleNamespace(states=lambda: [SimpleNamespace(key="dragon", alive=False, remaining=60.0,
                                                                    next_spawn=300.0)])
    e._jungle_intel = SimpleNamespace(state=lambda: SimpleNamespace(recalls=(("zed", 238.0),)))
    e._jungler_map = None
    game, tr = _world(240.0)
    e._discord_team_brain(240.0, 240.0, game, tr)
    kinds = [(k, i["rule"]) for k, _t, i in e.calls]
    assert kinds == [("team_brain", "objective"), ("team_brain", "opp")]
    assert e.calls[1][1] == "Ahri, ton adversaire est en base : plaque" and e.calls[1][2]["targets"] == ["ahri"]
    e._discord_team_brain(241.0, 241.0, game, tr)
    assert len(e.calls) == 2                                   # acked: not offered again
    assert e.said == []
