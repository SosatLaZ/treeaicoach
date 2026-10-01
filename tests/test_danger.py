"""Personal danger (treeaicoach.danger) on scripted scenarios taken from the first real game report
(Garen top, 1/15/5: 1v1 deaths to the lane opponent visible next to me, the enemy jungler visible on
screen 7 s before the death, deaths in my base during the siege) + anti-spam rules."""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Callable

import numpy as np
import pytest

from treeaicoach.alerts import AlertKind, AlertThrottler, Level
from treeaicoach.danger import RECULE_REPEAT_S, PersonalDanger, fog_mass_near, past_mid
from treeaicoach.fog_tracker import FogEstimate
from treeaicoach.live_client import GameInfo, PlayerInfo
from treeaicoach.tracker import Tracker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import test_gank as G  # noqa: E402

FPS = 8.0
DT = 1.0 / FPS
ME_TOP = G.ME_TOP
ROSTER = G.ROSTER


def game_at(gt: float, hp: float, my_level: int = 6, levels: dict | None = None, items: dict | None = None,
            dead: bool = False, me_alias: str = "Garen") -> GameInfo:
    levels = levels or {}
    items = items or {}
    players = [PlayerInfo(riot_id=f"{a}#EUW", summoner_name=a, champion_alias=a, champion_name=n, team=team,
                          position=pos, has_smite=smite, level=levels.get(a, my_level if a == me_alias else 6),
                          items=list(items.get(a, [])))
               for a, n, team, pos, smite in ROSTER]
    me = next(p for p in players if p.champion_alias == me_alias)
    me.is_dead = dead
    mx = 1500.0
    return GameInfo(game_time=gt, game_mode="CLASSIC", map_number=11, me=me,
                    allies=[p for p in players if p.team == me.team and p is not me],
                    enemies=[p for p in players if p.team != me.team], fetched_at=0.0,
                    champion_stats={"currentHealth": mx * hp, "maxHealth": mx})


def run(duration: float, enemies: Callable[[float], dict], hp: Callable[[float], float],
        me: Callable[[float], tuple] = lambda t: ME_TOP, allies: Callable[[float], dict] = lambda t: {},
        game_kw: Callable[[float], dict] = lambda t: {}, lane=("Darius",), fog=lambda t: (), **kw) -> list:
    tracker = Tracker()
    pd = PersonalDanger()
    out = []
    for i in range(int(round(duration * FPS))):
        t = i * DT
        items = [G.ident(*me(t), "self", "Garen", "ORDER")]
        for name, p in allies(t).items():
            items.append(G.ident(p[0], p[1], "ally", name, "ORDER"))
        for name, p in enemies(t).items():
            items.append(G.ident(p[0], p[1], "enemy", name, "CHAOS"))
        tracker.update(t, items)
        g = game_at(600.0 + t, hp(t), **game_kw(t))
        for a in pd.update(t, 600.0 + t, g, tracker, lane_opponents=lane, jungler="LeeSin", fog=fog(t), **kw):
            out.append((t, a))
    return out


def texts(out: list, level: Level | None = None) -> list[tuple[float, str]]:
    return [(t, a.text) for t, a in out if level is None or a.level == level]


# ------------------------------------------------------------------ lane 1v1 (2:37, 12:59, 15:00)
def test_lane_duel_written_warning_then_recule_before_the_death() -> None:
    """Darius (lane opponent, 2 levels and 1000 gold up) next to me, my HP falls from 100 % to
    5 % in 24 s: "Darius te domine" is written while I am still healthy enough, then "Recule !"
    is spoken >= 5 s before the death (HP 5 %)."""
    darius = lambda t: {"Darius": (ME_TOP[0] + 0.01, ME_TOP[1] - 0.06)}      # noqa: E731
    hp = lambda t: max(0.05, 1.0 - t / 24.0)                                   # noqa: E731
    kw = lambda t: {"my_level": 6, "levels": {"Darius": 8}, "items": {"Darius": [3071]}}  # noqa: E731
    out = run(26.0, darius, hp, game_kw=kw)
    written = texts(out, Level.WARNING)
    spoken = texts(out, Level.DANGER)
    assert written and written[0][1] == "Darius te domine : ne trade pas, farme sous la tour."
    assert written[0][0] < 9.0                         # while HP is still > 60 %
    assert [x for _t, x in spoken] == ["Recule !"]      # once (20 s cooldown)
    death_t = 24.0 * (1 - 0.05)
    assert death_t - spoken[0][0] >= 5.0
    assert all(a.kind == AlertKind.PERSONAL_DANGER for _t, a in out)


def test_lane_opponent_even_and_healthy_is_silent() -> None:
    out = run(30.0, lambda t: {"Darius": (ME_TOP[0] + 0.01, ME_TOP[1] - 0.06)}, lambda t: 0.9)
    assert out == []


# ------------------------------------------------------------------ on-screen jungler (9:05)
def test_jungler_visible_on_screen_and_low_hp_says_recule() -> None:
    """Lee Sin visible on my screen (0.06 away, not walking at me) while I am at 30 % HP: the gank
    analyser writes it (on screen, no approach), the personal danger SPEAKS "Recule !"."""
    out = run(8.0, lambda t: {"LeeSin": (ME_TOP[0] + 0.05, ME_TOP[1] + 0.03)}, lambda t: 0.3)
    assert texts(out, Level.DANGER)[:1] == [(texts(out, Level.DANGER)[0][0], "Recule !")]
    assert texts(out, Level.DANGER)[0][0] <= 1.0         # right away
    assert out[0][1].alias == "LeeSin"


def test_three_enemies_near_in_my_base_siege_says_recule() -> None:
    """33-35 min: dead in my base (not the fountain) with 3 enemies on me, no alert."""
    me = lambda t: (0.16, 0.84)                                    # noqa: E731 - my base, outside the fountain
    foes = lambda t: {"Ahri": (0.22, 0.80), "LeeSin": (0.20, 0.76), "Caitlyn": (0.24, 0.82)}  # noqa: E731
    out = run(4.0, foes, lambda t: 0.5, me=me)
    assert texts(out, Level.DANGER) and texts(out, Level.DANGER)[0][1] == "Recule !"


def test_outnumbered_written_when_healthy() -> None:
    foes = lambda t: {"Ahri": (ME_TOP[0] + 0.08, ME_TOP[1]), "LeeSin": (ME_TOP[0] + 0.05, ME_TOP[1] + 0.06)}  # noqa: E731
    out = run(4.0, foes, lambda t: 0.75)
    assert texts(out) == [(0.0, "2 ennemis près de toi : recule vers ta tour.")] or \
        [x for _t, x in texts(out)] == ["2 ennemis près de toi : recule vers ta tour."]


# ------------------------------------------------------------------ suppression / anti-spam
@pytest.mark.parametrize("case", ["fountain", "dead", "fight", "gank_danger"])
def test_suppressed(case: str) -> None:
    me = (lambda t: (0.06, 0.94)) if case == "fountain" else (lambda t: ME_TOP)
    near = (lambda t: {"LeeSin": (0.09, 0.91)}) if case == "fountain" else \
        (lambda t: {"LeeSin": (ME_TOP[0] + 0.04, ME_TOP[1])})
    kw: dict = {}
    game_kw = (lambda t: {"dead": True}) if case == "dead" else (lambda t: {})
    if case == "fight":
        kw["in_fight"] = True
    if case == "gank_danger":
        kw["gank_danger_t"] = 0.0
        out = run(3.0, near, lambda t: 0.2, me=me, game_kw=game_kw, **kw)
        assert texts(out, Level.DANGER) == []             # "Gank ! Lee Sin, recule !" said it already
        return
    out = run(6.0, near, lambda t: 0.2, me=me, game_kw=game_kw, **kw)
    assert texts(out, Level.DANGER) == []
    if case != "fight":
        assert out == []


def test_never_spammy_over_two_minutes() -> None:
    """Low HP going up and down for 2 minutes with Darius around: "Recule !" never twice within
    20 s, the same written line never twice within 20 s, a few messages per minute at most."""
    darius = lambda t: {"Darius": (ME_TOP[0] + 0.01 + 0.03 * math.sin(t / 3), ME_TOP[1] - 0.06)}  # noqa: E731
    hp = lambda t: 0.3 + 0.25 * math.sin(t / 7.0)                                            # noqa: E731
    out = run(120.0, darius, hp, game_kw=lambda t: {"levels": {"Darius": 8}})
    rec = [t for t, x in texts(out, Level.DANGER)]
    assert rec and all(b - a >= RECULE_REPEAT_S for a, b in zip(rec, rec[1:]))
    last: dict[str, float] = {}
    for t, x in texts(out):
        assert t - last.get(x, -99.0) >= 20.0, (t, x)
        last[x] = t
    assert len(out) <= 2 * 6 + 2                         # <= ~7 / min
    # and the throttler agrees (the engine's second safety net)
    th = AlertThrottler()
    said = [a for t, a in out if th.filter([a], t)]
    assert len(said) == len(out)


# ------------------------------------------------------------------ jungler in the fog
def _fog(center: tuple[float, float], elapsed: float, spread: float = 0.04) -> FogEstimate:
    g = 128
    c = (np.arange(g) + 0.5) / g
    heat = np.exp(-((c[None, :] - center[0]) ** 2 + (c[:, None] - center[1]) ** 2) / (2 * spread ** 2))
    heat = (heat / heat.sum()).astype(np.float32)
    return FogEstimate(key="LeeSin", alias="LeeSin", name="Lee Sin", last_uv=center, last_seen=0.0,
                       elapsed=elapsed, speed=0.026, radius=0.3, region=heat > 0, confidence=0.8,
                       is_jungler=True, heat=heat)


def test_unseen_jungler_probably_close_while_pushed_is_written_early() -> None:
    pushed = (0.12, 0.08)                           # top lane past the river diagonal (blue side)
    assert past_mid(pushed, "ORDER") and not past_mid(ME_TOP, "ORDER")
    fog = lambda t: [_fog((0.25, 0.16), 15.0)]      # noqa: E731 - his probable area: ~5 s from me
    out = run(3.0, lambda t: {}, lambda t: 1.0, me=lambda t: pushed, fog=fog)
    assert [x for _t, x in texts(out)] == ["Lee Sin peut arriver : recule vers ta tour."]
    # far (bot side) or me on my side of the map: nothing
    assert run(3.0, lambda t: {}, lambda t: 1.0, me=lambda t: pushed, fog=lambda t: [_fog((0.75, 0.8), 15.0)]) == []
    assert run(3.0, lambda t: {}, lambda t: 1.0, me=lambda t: ME_TOP, fog=fog) == []
    # just disappeared (< 8 s): the gank analyser / the last sighting speak
    assert run(3.0, lambda t: {}, lambda t: 1.0, me=lambda t: pushed, fog=lambda t: [_fog((0.25, 0.16), 3.0)]) == []


def test_fog_mass_near_bounds() -> None:
    est = _fog((0.25, 0.16), 15.0)
    near = fog_mass_near(est, (0.22, 0.15), 7.0)
    far = fog_mass_near(est, (0.8, 0.8), 7.0)
    assert near is not None and far is not None and near > 0.9 and far < 0.01


def test_never_raises_and_reset() -> None:
    pd = PersonalDanger()
    assert pd.update(0.0, 0.0, None, None) == []
    assert pd.update(float("nan"), 0.0, object(), object()) == []
    pd.reset()
    assert pd.state().rule is None


# ------------------------------------------------------------------ voice gate
def test_voice_gate_speaks_recule_and_writes_warnings_and_ganks_in_base() -> None:
    from treeaicoach.alerts import Alert
    from treeaicoach.voice_policy import (MessageGate, SpeechContext, VoiceGate, is_critical, route,
                                          triage_gank)

    gate = VoiceGate()
    rec = Alert(kind=AlertKind.PERSONAL_DANGER, level=Level.DANGER, text="Recule !", key="personal_danger:recule", t=0)
    warn = Alert(kind=AlertKind.PERSONAL_DANGER, level=Level.WARNING, text="Darius te domine : ne trade pas.",
                 key="personal_danger:lane:Darius", t=0)
    busy = SpeechContext(hp=0.2, enemies_near=2, enemy_in_danger=True)      # high concentration
    assert route(rec) == "voice" and route(warn) == "text"
    assert gate.decide(rec, 0.0, busy) == "voice" and is_critical(rec)
    assert gate.decide(warn, 0.0, busy) == "text" and not is_critical(warn)
    assert gate.decide(rec, 0.0, SpeechContext(dead=True)) == "drop"
    assert gate.filter_speech([rec], 0.0, busy) == [rec]                  # never budgeted away
    assert MessageGate().allow(rec, 0.0) and MessageGate().allow(warn, 0.0)
    # siege: a gank alert in my base is spoken (it used to be dropped "in base")
    gank = Alert(kind=AlertKind.JUNGLER_APPROACH, level=Level.WARNING, text="Lee Sin arrive !",
                 key="jungler_approach:LeeSin", t=0, alias="LeeSin", members=("LeeSin",))
    assert gate.decide(gank, 0.0, SpeechContext(in_base=True)) == "voice"
    assert triage_gank(gank, me_pos=(0.16, 0.84), allies=[], enemies=[], in_base=True)[0] == "speak"


# ------------------------------------------------------------------ the real engine (coach_sim)
def test_engine_speaks_recule_before_the_lane_death_in_the_full_game_sim() -> None:
    """coach_sim: Garen dies at 4:10 to Darius + Lee Sin with Darius visible next to him at 18 %
    HP for 6 s. Before: no warning at all. Now: "Recule !" spoken >= 5 s before the death."""
    from treeaicoach import coach_sim

    res = coach_sim.run("debutant", minutes=4.3, hz=4.0)
    recule = [gt for gt, x in res.voice if x == "Recule !"]
    assert recule and 250.0 - recule[0] >= 5.0 and 250.0 - recule[0] <= 12.0
    assert len(recule) == 1


def test_beginner_hears_gank_warnings_about_enemies_on_his_screen(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace as NS

    from treeaicoach import paths
    from treeaicoach.alerts import Alert
    from treeaicoach.config import Config
    from treeaicoach.engine import CoachEngine

    class _Voice:
        backend = "test"

        def say(self, text: str, level: int = 1) -> None:
            pass

        def set_muted(self, on: bool) -> None:
            pass

    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    paths._reset_cache()
    try:
        tr = Tracker()
        tr.update(0.0, [G.ident(0.20, 0.22, "enemy", "LeeSin", "CHAOS")])
        warn_on = Alert(kind=AlertKind.JUNGLER_APPROACH, level=Level.WARNING, text="Lee Sin arrive !",
                        key="jungler_approach:LeeSin", t=0.0, alias="LeeSin", members=("LeeSin",))
        for level, spoken in (("debutant", [warn_on]), ("intermediaire", [])):
            eng = CoachEngine(Config(skill_level=level), _Voice(), clock=lambda: 0.0, enable_hotkeys=False,
                              manage_overlay=False)
            eng._tracker = tr
            eng._camera = NS(current=lambda t: NS(u0=0.05, v0=0.15, u1=0.32, v1=0.30))
            assert eng._written_if_on_screen([warn_on], 1.0, 400.0, None) == spoken, level
    finally:
        paths._reset_cache()
