"""Tests for treeaicoach.team_calls (decisive team calls: cross-map objective, team lines, bot Discord)."""
from __future__ import annotations

from types import SimpleNamespace

from tests.test_macro import ctx_for, game, obj
from treeaicoach import discord_bot as db
from treeaicoach import game_changers as gcm
from treeaicoach import macro, team_calls
from treeaicoach.config import Config
from treeaicoach.fight import Seen

FAR_BOT = {"Darius": (0.86, 0.72), "Ahri": (0.88, 0.69), "Caitlyn": (0.84, 0.75), "Nautilus": (0.90, 0.66)}
NEAR_BARON = [Seen("Vi", (0.40, 0.44)), Seen("Lux", (0.44, 0.47))]


def _cross_ctx(gt=1500.0, enemies=None, dead=None, me_uv=(0.42, 0.45), allies=NEAR_BARON, **kw):
    g = game(gt=gt, dead=dead if dead is not None else {"LeeSin": 30.0})
    en = [Seen(a, uv, True, 0.0) for a, uv in (enemies if enemies is not None else FAR_BOT).items()]
    return ctx_for(g, me_uv=me_uv, enemies=en, allies=allies, objectives=[obj("baron", alive=True)], **kw)


def test_four_bot_baron_now() -> None:
    c = team_calls.rule_cross_objective(_cross_ctx())
    assert c is not None and c.kind == "gc_cross_obj" and c.title == "BARON !"
    assert c.text.startswith("Prends le Baron maintenant : ils sont 4 en bas (")
    assert c.voice == "Ils sont quatre en bas : Baron !" and c.voice in gcm.voice_phrases()
    assert team_calls.team_line(c) == "Ils sont 4 bot : Baron maintenant !"
    assert "gc_cross_obj" in [x.kind for x in macro.evaluate(_cross_ctx())]           # registered rule
    assert gcm.voice_for(c, _cross_ctx(), "debutant") == (f"gc:big:gc_cross_obj:{c.ident}", c.voice)


def test_cross_baron_guards() -> None:
    # their jungler alive and unseen: smite steal / fog ambush -> silent
    assert team_calls.rule_cross_objective(_cross_ctx(dead={})) is None
    # one of them seen next to the pit -> silent
    near = {**FAR_BOT, "Ahri": (0.36, 0.33)}
    assert team_calls.rule_cross_objective(_cross_ctx(enemies=near)) is None
    # only 2 seen far (2 unaccounted) -> silent
    two = {a: uv for a, uv in list(FAR_BOT.items())[:2]}
    assert team_calls.rule_cross_objective(_cross_ctx(enemies=two)) is None
    # we are bot with them (our walk >= theirs): no free Baron (the truth replay's usual case)
    assert team_calls.rule_cross_objective(_cross_ctx(me_uv=(0.80, 0.80), allies=[Seen("Vi", (0.82, 0.78)),
                                                                                 Seen("Lux", (0.78, 0.80))])) is None
    # 2 of us dead -> silent; before 20:00 no Baron
    g = game(gt=1500.0, dead={"LeeSin": 30.0, "Vi": 20.0, "Lux": 20.0})
    en = [Seen(a, uv, True, 0.0) for a, uv in FAR_BOT.items()]
    assert team_calls.rule_cross_objective(ctx_for(g, me_uv=(0.42, 0.45), enemies=en,
                                                   objectives=[obj("baron", alive=True)])) is None
    assert team_calls.rule_cross_objective(_cross_ctx(gt=1100.0)) is None


def test_three_top_dragon_now() -> None:
    top = {"Darius": (0.20, 0.10), "Ahri": (0.15, 0.20), "LeeSin": (0.25, 0.12), "Caitlyn": (0.10, 0.25)}
    g = game(gt=1000.0, me="Jinx", dead={"Nautilus": 25.0})
    en = [Seen(a, uv, True, 0.0) for a, uv in top.items()]
    c = ctx_for(g, me_uv=(0.62, 0.78), enemies=en, allies=[Seen("Thresh", (0.63, 0.80)), Seen("Vi", (0.60, 0.70))],
                objectives=[obj("dragon", alive=True)])
    call = team_calls.rule_cross_objective(c)
    assert call is not None and call.title == "DRAGON !"
    assert team_calls.team_line(call) == "Ils sont 4 top : dragon maintenant !"


def test_team_lines_only_for_team_calls() -> None:
    won = macro.GeniusCall("fight_won", "x", "BARON !", "Prends le Baron maintenant : 3 ennemis morts (35 s)", "")
    assert team_calls.team_line(won) == "3 morts : Baron maintenant (35 secondes) !"
    jd = macro.GeniusCall("jungler_dead", "x", "DRAGON !", "Prends le dragon maintenant : Lee Sin est mort (40 s)", "")
    assert team_calls.team_line(jd) == "Leur jungler est mort : dragon maintenant (40 secondes) !"
    free = macro.GeniusCall("free_dragon", "x", "DRAGON !", "Pousse ta vague puis va au dragon : il est libre (40 s)",
                            "Lee Sin est en haut : ils ne peuvent pas le contester à temps.")
    assert team_calls.team_line(free) == "Leur jungler est top : dragon gratuit (40 secondes) !"
    for kind in ("plates", "wave_recall", "gc_level", "side_wave"):              # personal: never to the team
        assert team_calls.team_line(macro.GeniusCall(kind, "x", "TOUR !", "Frappe la tour (20 s)", "")) is None
    assert team_calls.director_line("urgent:ace:push", "Frappe une tour avec ton équipe : 5 ennemis morts") \
        == "5 morts : poussez mid et prenez la tour !"
    assert team_calls.director_line("urgent:ace:baron", "Va au Baron maintenant : 4 ennemis morts") \
        == "4 morts : Baron maintenant !"
    assert team_calls.director_line("macro:phase:mid", "Rejoins ton équipe") is None
    assert team_calls.prep_line("dragon", 60.0) == "Dragon dans une minute : vision rivière du bas, soyez là."
    assert team_calls.prep_line("baron", 59.0).startswith("Baron dans une minute : vision rivière du haut")
    assert team_calls.prep_line("dragon", 30.0) is None


def test_free_dragon_states_the_window() -> None:
    g = game(gt=600.0, me="Jinx")
    c = ctx_for(g, me_uv=(0.80, 0.91), enemies=[Seen("LeeSin", (0.30, 0.25), True, 0.0)],
                objectives=[obj("dragon", alive=True)])
    calls = [x for x in macro.evaluate(c) if x.kind == "free_dragon"]
    assert calls and calls[0].text.startswith("Pousse ta vague puis va au dragon : il est libre (")


class _Eng:
    def __init__(self, cfg) -> None:  # noqa: ANN001
        from treeaicoach.engine_coaching import CoachingMixin

        self.__class__ = type("E", (CoachingMixin,), {})
        self._cfg = cfg
        self._demo = False
        self._frame_source = None
        self.calls: list = []
        self.on_call = lambda k, t, i: self.calls.append((k, t, i)) or True


def test_engine_team_call_switch_and_prep() -> None:
    call = team_calls.rule_cross_objective(_cross_ctx())
    off = _Eng(Config(discord_enabled=True))
    assert not off._team_call(call) and off.calls == []                       # off by default
    on = _Eng(Config(discord_enabled=True, discord_team_calls=True))
    assert on._team_call(call)
    assert on.calls == [("team_call", "Ils sont 4 bot : Baron maintenant !", {"key": call.ident})]
    on._team_call(macro.GeniusCall("plates", "p", "TOUR !", "Frappe la tour (20 s)", ""))
    assert len(on.calls) == 1                                                  # personal call: not sent
    on._team_call(None, key="urgent:ace:push", text="Frappe une tour avec ton équipe : 5 ennemis morts")
    assert on.calls[-1][1] == "5 morts : poussez mid et prenez la tour !"
    states = [SimpleNamespace(key="dragon", alive=False, remaining=70.0, next_spawn=900.0)]
    on._objectives = SimpleNamespace(states=lambda: states)
    on.calls.clear()
    on._discord_objectives()
    states[0].remaining = 60.0
    on._discord_objectives()
    on._discord_objectives()
    assert on.calls == [("team_call", "Dragon dans une minute : vision rivière du bas, soyez là.",
                         {"key": "prep:dragon:900"})]


def test_bot_filter_team_calls() -> None:
    now = [100.0]
    f = db.CallFilter(lambda: now[0])
    assert f.admit("team_call", "a")
    assert not f.admit("team_call", "a")                    # same call once
    now[0] += 5.0
    assert not f.admit("team_call", "b")                    # 20 s between two team calls
    now[0] += 20.0
    assert f.admit("team_call", "b")
    assert f.admit("gank", "top")
    now[0] += 21.0
    f2 = db.CallFilter(lambda: now[0])
    f2.admit("gank", "bot")
    assert not f2.admit("team_call", "c")                   # never on top of a gank call
    s = db._Settings()
    assert not s.wants("team_call") and db._Settings(team_calls=True).wants("team_call")
    assert "team_call" in db.SPOKEN_KINDS and db.PRIORITY["team_call"] < db.PRIORITY["gank"]
    assert Config().discord_team_calls is False
