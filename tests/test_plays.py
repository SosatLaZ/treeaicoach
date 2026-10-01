"""Play ratings (plays.py): fake Live Client event streams -> chess.com-style classes."""

from __future__ import annotations

import json

from plays_sim import Sim, start, step

from treeaicoach import plays
from treeaicoach.objectives import ObjectiveState
from treeaicoach.plays import PlayClassifier


class Cfg:
    def __init__(self, skill: str = "debutant", enabled: bool = True) -> None:
        self.skill_level = skill
        self.plays_enabled = enabled


def rules(pc: PlayClassifier) -> list[tuple[str, str]]:
    return [(p.cls, p.rule) for p in pc.history()]


def test_past_events_are_never_rated():
    sim = Sim()
    sim.gt = 600.0
    sim.kill("Garen", "Darius")
    pc = PlayClassifier(Cfg())
    step(pc, sim)
    assert pc.history() == []


def test_solo_kill_great_and_outplay_brilliant():
    pc, sim = start(PlayClassifier(Cfg()))
    sim.gt = 200.0
    sim.kill("Garen", "Darius")
    out = step(pc, sim)
    assert rules(pc) == [("great", "solo_kill")]
    assert out and out[0].cls == "great" and "Darius" in out[0].reason
    # behind in levels: outplay
    sim.respawn_all()
    sim.player("Darius").level = 9
    sim.me.level = 7
    sim.gt = 400.0
    sim.kill("Garen", "Darius")
    step(pc, sim)
    assert pc.history()[-1].cls == "brilliant"
    assert "2 niveaux de retard" in pc.history()[-1].reason


def test_outnumbered_kill_is_brilliant_and_shutdown():
    pc, sim = start(PlayClassifier(Cfg()))
    sim.gt = 300.0
    sim.kill("Garen", "Zed")
    step(pc, sim, enemies_near=2)
    assert pc.history()[-1].rule == "outplay"
    # Zed gets 3 kills, then I kill him: shutdown
    for i, v in enumerate(("Ahri", "Jinx", "Thresh")):
        sim.gt = 320.0 + i
        sim.kill("Zed", v)
    sim.gt = 400.0
    sim.kill("Garen", "Zed", ("LeeSin",))
    step(pc, sim)
    assert pc.history()[-1].cls == "brilliant" and pc.history()[-1].rule == "shutdown"


def test_assisted_kill_good_and_multikill():
    pc, sim = start(PlayClassifier(Cfg()))
    sim.gt = 500.0
    sim.kill("Garen", "Lux", ("Jinx",))
    sim.kill("Garen", "Caitlyn", ("Jinx",))
    sim.kill("Garen", "Vi", ("Jinx",))
    sim.event("Multikill", KillerName=sim.rid("Garen"), KillStreak=3)
    step(pc, sim)
    assert rules(pc) == [("brilliant", "multikill")]       # the multikill rates the fight
    sim.gt = 900.0
    sim.respawn_all()
    sim.kill("Garen", "Lux", ("Jinx",))
    step(pc, sim)
    assert rules(pc)[-1] == ("good", "kill")


def test_death_classes():
    pc, sim = start(PlayClassifier(Cfg()))
    # death right after a gank warning
    sim.gt = 300.0
    step(pc, sim, threat=1)
    sim.gt = 305.0
    sim.me.is_dead = True
    sim.kill("Vi", "Garen")
    step(pc, sim)
    assert pc.history()[-1].cls == "blunder" and pc.history()[-1].rule == "death_after_warning"
    # dying with lots of gold
    sim.me.is_dead = False
    sim.gt = 600.0
    sim.gold = 2400.0
    step(pc, sim)
    sim.gt = 610.0
    sim.me.is_dead = True
    sim.kill("Darius", "Garen")
    step(pc, sim)
    assert pc.history()[-1].rule == "death_gold" and pc.history()[-1].cls == "blunder"
    assert "2 400 PO" in pc.history()[-1].reason
    # plain death
    sim.me.is_dead = False
    sim.gold = 300.0
    sim.gt = 900.0
    step(pc, sim)
    sim.gt = 910.0
    sim.me.is_dead = True
    sim.kill("Darius", "Garen", ("Lux",))
    step(pc, sim)
    assert pc.history()[-1].cls == "inaccuracy"


def test_shutdown_given_and_facecheck():
    pc, sim = start(PlayClassifier(Cfg()))
    for i, v in enumerate(("Darius", "Zed", "Lux")):
        sim.gt = 200.0 + i
        sim.kill("Garen", v)
    step(pc, sim)
    sim.gt = 300.0
    sim.me.is_dead = True
    sim.kill("Caitlyn", "Garen")
    step(pc, sim)
    assert pc.history()[-1].rule == "shutdown_given"
    # facecheck: the jungler's fog circle covered me, he took part in the kill
    sim.me.is_dead = False
    sim.gt = 600.0
    step(pc, sim, jungler_fog_near=True)
    sim.gt = 603.0
    sim.me.is_dead = True
    sim.kill("Vi", "Garen")
    step(pc, sim)
    assert pc.history()[-1].rule == "facecheck" and pc.history()[-1].cls == "blunder"


def test_gank_survived():
    pc, sim = start(PlayClassifier(Cfg()))
    for i in range(4):
        sim.gt = 400.0 + i
        step(pc, sim, threat=2, enemies_near=2)
    for i in range(12):
        sim.gt = 404.0 + i
        step(pc, sim)
    assert ("brilliant", "gank_survived") in rules(pc)
    # died during the gank: nothing to praise
    pc, sim = start(PlayClassifier(Cfg()))
    sim.gt = 400.0
    step(pc, sim, threat=2, enemies_near=1)
    sim.me.is_dead = True
    sim.gt = 403.0
    sim.kill("Vi", "Garen")
    step(pc, sim)
    for i in range(12):
        sim.gt = 404.0 + i
        step(pc, sim)
    assert "gank_survived" not in [r for _c, r in rules(pc)]


def test_recall_after_crashing_the_wave():
    pc, sim = start(PlayClassifier(Cfg()))
    sim.gt = 420.0
    sim.gold = 1350.0
    step(pc, sim, my_lane="top", waves={"top": {"state": "pushing"}}, me_uv=(0.1, 0.3))
    sim.gt = 421.0
    step(pc, sim, my_lane="top", me_uv=(0.08, 0.92), in_base=True)
    assert rules(pc) == [("great", "recall")]
    # a respawn in base is not a recall
    sim.gt = 700.0
    step(pc, sim, my_lane="top", me_uv=(0.1, 0.3))
    sim.me.is_dead = True
    sim.gt = 701.0
    step(pc, sim, my_lane="top", me_uv=(0.1, 0.3))
    sim.me.is_dead = False
    sim.gt = 730.0
    step(pc, sim, my_lane="top", me_uv=(0.08, 0.92), in_base=True)
    assert len(pc.history()) == 1


def test_objectives_steal_numbers_setup():
    pc, sim = start(PlayClassifier(Cfg()))
    sim.gt = 1600.0
    sim.event("BaronKill", KillerName=sim.rid("LeeSin"), Assisters=[], Stolen="True")
    step(pc, sim)
    assert pc.history()[-1].rule == "steal" and pc.history()[-1].cls == "brilliant"
    # dragon with numbers (2 enemies dead) and my participation
    sim.gt = 1900.0
    sim.player("Zed").is_dead = sim.player("Lux").is_dead = True
    sim.event("DragonKill", KillerName=sim.rid("LeeSin"), Assisters=[sim.rid("Garen")], DragonType="Fire",
              Stolen="False")
    step(pc, sim)
    assert pc.history()[-1].rule == "objective_numbers" and "5 contre 3" in pc.history()[-1].reason
    # perfect setup: ward score up + bot wave pushed before the dragon
    sim.respawn_all()
    sim.gt = 2150.0
    step(pc, sim, waves={"bot": {"state": "pushing"}})
    sim.me.scores = dict(sim.me.scores, wardScore=4.0)
    sim.gt = 2160.0
    step(pc, sim)
    sim.gt = 2190.0
    sim.event("DragonKill", KillerName=sim.rid("Garen"), Assisters=[], DragonType="Air", Stolen="False")
    step(pc, sim)
    assert pc.history()[-1].rule == "setup" and pc.history()[-1].cls == "brilliant"


def test_missed_objective_window():
    pc, sim = start(PlayClassifier(Cfg()))
    baron = ObjectiveState("Baron", 1500.0, True, "schedule", key="baron", remaining=0.0)
    sim.gt = 1700.0
    for name in ("Darius", "Zed", "Vi"):
        sim.player(name).is_dead = True
        sim.player(name).respawn_timer = 40.0
    for i in range(30):
        sim.gt = 1700.0 + i
        step(pc, sim, objectives=[baron])
    sim.respawn_all()
    sim.gt = 1731.0
    step(pc, sim, objectives=[baron])
    assert rules(pc) == [("miss", "missed_objective")]
    assert "Baron" in pc.history()[-1].reason
    # taken during the window: no miss
    pc, sim = start(PlayClassifier(Cfg()), gt=1690.0)
    for name in ("Darius", "Zed", "Vi"):
        sim.player(name).is_dead = True
        sim.player(name).respawn_timer = 40.0
    for i in range(25):
        sim.gt = 1700.0 + i
        if i == 24:
            sim.event("BaronKill", KillerName=sim.rid("LeeSin"), Assisters=[sim.rid("Garen")], Stolen="False")
        step(pc, sim, objectives=[baron])
    sim.respawn_all()
    sim.gt = 1730.0
    step(pc, sim, objectives=[baron])
    assert "missed_objective" not in [r for _c, r in rules(pc)]


def test_missed_tower_window():
    pc, sim = start(PlayClassifier(Cfg()), gt=900.0)
    sim.gt = 1000.0
    sim.player("Darius").is_dead = True
    sim.player("Darius").respawn_timer = 30.0
    for i in range(40):
        sim.gt = 1000.0 + i
        step(pc, sim, my_lane="top", me_uv=(0.08, 0.35), jungler_uv=(0.85, 0.75), jungler_seen_ago=2.0,
             waves={"top": {"state": "pushing"}})
    assert rules(pc) == [("miss", "missed_tower")]
    # V2 audit: no wave of mine at their tower = not a free tower, no "occasion ratée"
    pc, sim = start(PlayClassifier(Cfg()), gt=900.0)
    sim.player("Darius").is_dead = True
    sim.player("Darius").respawn_timer = 30.0
    for i in range(40):
        sim.gt = 1000.0 + i
        step(pc, sim, my_lane="top", me_uv=(0.08, 0.35), jungler_uv=(0.85, 0.75), jungler_seen_ago=2.0)
    assert rules(pc) == []


def test_rate_limit_fight_hold_and_small_brilliant():
    pc, sim = start(PlayClassifier(Cfg()))
    sim.gt = 300.0
    sim.kill("Garen", "Darius")
    assert step(pc, sim)[0].cls == "great"
    sim.respawn_all()
    sim.gt = 310.0
    sim.kill("Garen", "Darius", ("LeeSin",))          # good: rate-limited (45 s)
    assert step(pc, sim) == []
    # in a fight: a brilliant is shown small, a negative waits
    sim.respawn_all()
    sim.gt = 330.0
    sim.kill("Garen", "Zed")
    out = step(pc, sim, in_fight=True, enemies_near=3)
    assert out and out[0].cls == "brilliant" and out[0].size == "small"
    # everything is still counted
    assert [c for c, _r in rules(pc)] == ["great", "good", "brilliant"]
    # nothing else during the fight; a blunder after the fight (gap respected)
    sim.gt = 340.0
    sim.me.is_dead = True
    sim.kill("Vi", "Garen")
    assert step(pc, sim, in_fight=True) == []
    sim.gt = 342.0
    assert step(pc, sim) == []                           # quiet 2 s + min gap of 45 s
    sim.gt = 380.0
    out = step(pc, sim)
    assert out and out[0].cls in ("blunder", "inaccuracy") and out[0].size == "big"


def test_skill_level_filters_display_not_counts():
    pc, sim = start(PlayClassifier(Cfg("expert")))
    sim.gt = 300.0
    sim.kill("Garen", "Darius")
    assert step(pc, sim) == []                           # great: not shown to an expert
    assert pc.history()[0].cls == "great"
    pc2, sim2 = start(PlayClassifier(Cfg("debutant")))
    sim2.gt = 300.0
    sim2.kill("Garen", "Darius", ("LeeSin",))
    assert step(pc2, sim2)[0].cls == "good"
    pc3, sim3 = start(PlayClassifier(Cfg("intermediaire")))
    sim3.gt = 300.0
    sim3.kill("Garen", "Darius", ("LeeSin",))
    assert step(pc3, sim3) == []
    off, sim4 = start(PlayClassifier(Cfg(enabled=False)))
    sim4.gt = 300.0
    sim4.kill("Garen", "Darius")
    assert step(off, sim4) == [] and len(off.history()) == 1


def test_summary_precision_and_record(tmp_path):
    assert plays.precision({}) == 75
    s = plays.summarize([plays.Play("brilliant", "x", "r", 0, 10, "a"), plays.Play("blunder", "y", "r", 0, 20, "b"),
                         plays.Play("great", "z", "r", 0, 30, "c")])
    assert s["counts"]["brilliant"] == 1 and s["total"] == 3
    assert 0 <= s["precision"] <= 100
    assert s["best"][0]["cls"] == "brilliant" and s["worst"][0]["cls"] == "blunder"
    assert plays.precision({"blunder": 10}) < 20 < plays.precision({"great": 10})
    assert "Précision" in plays.summary_line(s) and "1 gaffe" in plays.summary_line(s)
    rec = tmp_path / "game.json"
    rec.write_text(json.dumps({"schema": 3, "meta": {}}), encoding="utf-8")
    assert plays.attach_to_record(rec, s)
    data = json.loads(rec.read_text(encoding="utf-8"))
    assert data["meta"] == {} and data["plays"]["counts"]["great"] == 1
    again = plays.summary_from_record(data)
    assert again["precision"] == s["precision"]
    assert plays.summary_from_record({"meta": {}}) is None
    assert not plays.attach_to_record(tmp_path / "missing.json", s)


def test_no_em_dash_or_emoji_in_texts():
    texts = list(plays.TITLE_FR.values()) + list(plays.LABEL_FR.values())
    for t in texts:
        assert "—" not in t
        assert all(ord(ch) < 0x2000 for ch in t)


def test_never_raises_on_garbage():
    pc = PlayClassifier(Cfg())
    assert pc.update(plays.PlayContext(t=0.0, gt=0.0, game=object())) == []
    assert pc.update(None) == []  # type: ignore[arg-type]


def test_missed_tower_with_jungle_intel():
    pc, sim = start(PlayClassifier(Cfg()), gt=900.0)
    sim.player("Darius").is_dead = True
    sim.player("Darius").respawn_timer = 30.0
    for i in range(40):
        sim.gt = 1000.0 + i
        step(pc, sim, my_lane="top", me_uv=(0.08, 0.35), jungler_far=True, waves={"top": {"state": "pushing"}})
    assert rules(pc) == [("miss", "missed_tower")]


def test_build_context_reads_engine_pieces():
    from types import SimpleNamespace as NS

    sim = Sim()
    sim.gt = 1000.0
    game = sim.snap()
    tr_me = NS(position=lambda: (0.1, 0.3), visible=True, last_seen=99.0)
    tr_vi = NS(position=lambda: (0.15, 0.32), visible=True, last_seen=100.0)
    tracker = NS(enemies=lambda visible_only=True: [tr_vi], allies=lambda visible_only=True: [],
                 get=lambda alias: tr_vi if alias == "Vi" else None, me=lambda: tr_me)
    fog = NS(estimates=lambda: [])
    fight = NS(state=lambda: NS(active=True, call="retreat"))
    eng = NS(_tracker=tracker, _fog=fog, _cfg=NS(safe_mode=False),
             _tactics=NS(in_fight=lambda: True, fight=fight), scoreboard_summary=lambda: None,
             _objectives=NS(states=lambda: []), _coach=NS(waves=lambda: {"top": {"state": "pushing"}}),
             _role_resolver=NS(my_role=lambda: "TOP"),
             _jungle_intel=NS(state=lambda: NS(alias="Vi", farm_side="bot", farming=True, dead=False,
                                               recalled=False)))
    ctx = plays.build_context(eng, 100.0, 1000.0, game, 1, (0.1, 0.3))
    assert ctx.enemies_near == 1 and ctx.jungler_uv == (0.15, 0.32) and ctx.jungler_seen_ago == 0.0
    assert ctx.in_fight and ctx.fight_call == "retreat" and ctx.my_lane == "top"
    assert ctx.waves["top"]["state"] == "pushing" and ctx.jungler_far and not ctx.in_base
    assert plays.build_context(None, 0.0, 0.0, None).game is None
