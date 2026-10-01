"""Deterministic scenario tests of the live macro coach (treeaicoach/coach.py)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from treeaicoach import geometry
from treeaicoach.alerts import AlertKind, Level
from treeaicoach.coach import ENEMY_RULES, GLOBAL_GAP_S, MapCoach, PITS, fmt_dec, map_side
from treeaicoach.live_client import GameInfo, PlayerInfo
from treeaicoach.objectives import ObjectiveState
from treeaicoach.tracker import Tracker

TOP_LANE = (0.10, 0.15)            # my top lane position (far from my turrets)
TOP_TOWER_SAFE = geometry.game_to_uv(1512, 6699)   # right on my inner top turret
BOT_LANE = (0.75, 0.92)
MID = (0.5, 0.5)
ENEMY_JUNGLE_RED_BOT = (0.80, 0.55)


def _scores(cs: float = 0, ward: float = 0.0) -> dict:
    return {"kills": 0, "deaths": 0, "assists": 0, "creepScore": cs, "wardScore": ward}


def make_game(gt: float, *, role: str = "TOP", cs: float = 0, ward: float = 0.0, level: int = 9,
              dead: bool = False, elevel: int | None = None, eitems: dict | None = None,
              ekills: int = 0, akills: int = 0, jlevel: int | None = None) -> GameInfo:
    elevel = level if elevel is None else elevel
    me = PlayerInfo(champion_alias="Garen", champion_name="Garen", team="ORDER", position=role,
                    level=level, is_dead=dead, scores=_scores(cs, ward))
    eitems = eitems or {}
    allies = [PlayerInfo(champion_alias=a, champion_name=a, team="ORDER", position=p, level=level,
                         has_smite=(p == "JUNGLE"), scores=_scores() | {"kills": akills if p == "MIDDLE" else 0})
              for a, p in (("Vi", "JUNGLE"), ("Lux", "MIDDLE"), ("Ashe", "BOTTOM"), ("Nami", "UTILITY"))]
    enemies = [PlayerInfo(champion_alias="LeeSin", champion_name="Lee Sin", team="CHAOS", position="JUNGLE",
                          has_smite=True, level=elevel if jlevel is None else jlevel,
                          scores=_scores() | {"kills": ekills})]
    enemies += [PlayerInfo(champion_alias=a, champion_name=a, team="CHAOS", position=p, level=elevel,
                           items=list(eitems.get(a, [])))
                for a, p in (("Darius", "TOP"), ("Ahri", "MIDDLE"), ("Jinx", "BOTTOM"), ("Thresh", "UTILITY"))]
    return GameInfo(game_time=gt, game_mode="CLASSIC", map_number=11, me=me, allies=allies, enemies=enemies,
                    fetched_at=gt)


def icon(alias: str, relation: str, uv: tuple[float, float]) -> SimpleNamespace:
    return SimpleNamespace(u=uv[0], v=uv[1], r=0.045, score=0.9, alias=alias, relation=relation,
                           team=None, id_score=0.9)


class Sim:
    """Runs the coach on a real Tracker at 2 Hz; engine time == game time."""

    def __init__(self, cfg=None, role: str = "TOP") -> None:
        self.coach = MapCoach(cfg)
        self.tracker = Tracker()
        self.said: list[tuple[float, str, object]] = []
        self.role = role

    def run(self, t0: float, t1: float, icons, *, objectives=(), threat=lambda t: 0, game=None,
            dt: float = 0.5, frame=None) -> "Sim":
        t = t0
        while t <= t1 + 1e-9:
            self.tracker.update(t, icons(t))
            me = self.tracker.me()
            pos = me.position() if me is not None else None
            g = game(t) if game is not None else make_game(t, role=self.role)
            objs = objectives(t) if callable(objectives) else objectives
            img = frame(t) if frame is not None else None
            for a in self.coach.update(t, self.tracker, g, None, objs, pos, threat=threat(t), minimap_bgr=img):
                self.said.append((t, a.text, a))
            t = round(t + dt, 3)
        return self

    def texts(self) -> list[str]:
        return [s[1] for s in self.said]


def me_at(uv):
    return [icon("Garen", "self", uv)]


# ------------------------------------------------------------------------------ basics
def test_helpers():
    assert fmt_dec(7.25) == "7,2" or fmt_dec(7.25) == "7,3"
    assert fmt_dec(8.0) == "8"
    assert map_side(*TOP_LANE) == "top"
    assert map_side(*BOT_LANE) == "bot"
    assert map_side(0.5, 0.5) == "mid"
    assert PITS["dragon"][1] == "bot" and PITS["baron"][1] == "top"


def test_alert_kind_and_never_raises():
    c = MapCoach(None)
    assert c.update(float("nan"), None, None) == []
    assert c.update(1.0, None, None) == []
    assert c.update(1.0, object(), object(), object(), [object()], ("x", None), threat="?") == []
    assert c.insight() is None
    assert AlertKind.MACRO_TIP.value == "macro_tip"


def test_quiet_game_says_nothing():
    sim = Sim().run(200, 400, lambda t: me_at(TOP_LANE) + [icon("Darius", "enemy", (0.13, 0.10))]
                    + [icon("LeeSin", "enemy", (0.30, 0.55))],
                    game=lambda t: make_game(t, ward=t // 60))
    # jungler always visible near: no unseen tip; no other condition met
    assert sim.texts() == []


# ------------------------------------------------------------------------------ jungler
def test_jungler_seen_on_other_side_gives_aggressive_tip():
    def icons(t):
        out = me_at(TOP_LANE)
        if t < 260:
            out.append(icon("LeeSin", "enemy", (0.30, 0.55)))   # his top-side jungle
        if t >= 300:
            out.append(icon("LeeSin", "enemy", (0.72, 0.80)))   # bot side
        return out
    sim = Sim().run(200, 320, icons, game=lambda t: make_game(t, ward=t // 60))
    texts = sim.texts()
    assert texts and texts[0] == "Leur jungler est en bas : tu peux jouer plus agressif en haut."
    a = sim.said[0][2]
    assert a.kind == AlertKind.MACRO_TIP and a.level == Level.INFO
    assert any("JGL en bas" in s for s in sim.coach.insights())


def test_jungler_same_side_no_tip():
    def icons(t):
        out = me_at(TOP_LANE)
        if t >= 300:
            out.append(icon("LeeSin", "enemy", (0.30, 0.15)))   # top side
        return out
    assert not [x for x in Sim().run(200, 320, icons).texts() if "agressif" in x]


def test_jungler_unseen_45s_once_per_disappearance():
    def icons(t):
        out = me_at(TOP_LANE)
        if t < 200:
            out.append(icon("LeeSin", "enemy", (0.30, 0.55)))
        return out
    sim = Sim().run(180, 400, icons)
    unseen = [s for s in sim.said if "pas vu depuis" in s[1]]
    assert len(unseen) == 1
    t, text, _ = unseen[0]
    assert 244 <= t <= 247
    assert text == "Jungler ennemi pas vu depuis 45 s : prudence."


# ------------------------------------------------------------------------------ missing
def test_three_missing_enemies_far_from_towers():
    seen = [("Darius", (0.1, 0.2)), ("Ahri", (0.5, 0.5)), ("Jinx", (0.8, 0.9)), ("LeeSin", (0.3, 0.5))]

    def icons(t):
        out = me_at(TOP_LANE)
        if t < 400:
            out += [icon(a, "enemy", p) for a, p in seen]
        else:
            out.append(icon("LeeSin", "enemy", (0.3, 0.5)))      # jungler stays visible
        return out
    sim = Sim().run(390, 420, icons)
    assert "3 ennemis disparus : reste prudent." in sim.texts()


def test_missing_enemies_silent_under_my_tower():
    seen = [("Darius", (0.1, 0.2)), ("Ahri", (0.5, 0.5)), ("Jinx", (0.8, 0.9)), ("LeeSin", (0.3, 0.5))]

    def icons(t):
        out = me_at(TOP_TOWER_SAFE)
        out += [icon(a, "enemy", p) for a, p in seen] if t < 400 else [icon("LeeSin", "enemy", (0.3, 0.5))]
        return out
    assert not [x for x in Sim().run(390, 420, icons).texts() if "disparus" in x]


# ------------------------------------------------------------------------------ objectives
def _dragon(next_spawn: float, alive: bool = False) -> list[ObjectiveState]:
    return [ObjectiveState(name="Dragon", next_spawn=next_spawn, alive=alive, source="schedule", key="dragon")]


def test_objective_setup_counts_enemies_bot():
    def icons(t):
        return me_at(MID) + [icon("Jinx", "enemy", (0.78, 0.88)), icon("Thresh", "enemy", (0.70, 0.75)),
                             icon("LeeSin", "enemy", (0.5, 0.45))]
    sim = Sim().run(240, 270, icons, objectives=_dragon(300.0))
    setup = [x for x in sim.texts() if x.startswith("Dragon dans")]
    assert setup == ["Dragon dans 55 s : préparez la vision, 2 ennemis visibles en bas."]
    assert any(s.startswith("Dragon 0:") and "2 ennemis en bas" in s for s in sim.coach.insights())


def test_objective_setup_safe_mode_uses_no_enemy_positions():
    cfg = SimpleNamespace(safe_mode=True)

    def icons(t):
        return me_at(MID) + [icon("Jinx", "enemy", (0.78, 0.88)), icon("Thresh", "enemy", (0.70, 0.75))]
    sim = Sim(cfg).run(240, 270, icons, objectives=_dragon(300.0))
    assert sim.texts() == ["Dragon dans 55 s : préparez la vision en bas."]
    assert all("ennemi" not in s for s in sim.coach.insights())
    assert sim.coach.pressure() is None


def test_baron_window_when_enemies_bot():
    objs = [ObjectiveState(name="Baron", next_spawn=1500.0, alive=True, source="schedule", key="baron")]

    def icons(t):
        return me_at((0.40, 0.45)) + [icon(a, "enemy", p) for a, p in (
            ("Jinx", (0.80, 0.90)), ("Thresh", (0.78, 0.86)), ("Ahri", (0.85, 0.80)), ("LeeSin", (0.70, 0.80)))]
    sim = Sim().run(1600, 1610, icons, objectives=objs)
    assert "Baron dispo et 4 ennemis visibles en bas : bonne fenêtre pour Baron." in sim.texts()


# ------------------------------------------------------------------------------ numbers
def test_outnumbered_warning_and_not_during_threat():
    enemies = [icon("Darius", "enemy", (0.12, 0.10)), icon("LeeSin", "enemy", (0.14, 0.18)),
               icon("Ahri", "enemy", (0.06, 0.20))]

    def icons(t):
        return me_at(TOP_LANE) + (enemies if t >= 500 else [icon("LeeSin", "enemy", (0.3, 0.5))])
    sim = Sim().run(490, 510, icons)
    assert "3 contre 1 autour de toi, recule." in sim.texts()
    t_said = [s[0] for s in sim.said if "contre" in s[1]][0]
    assert t_said >= 501.5                       # confirmation delay

    # the same situation during a gank threat -> nothing (no duplicate of the gank alert)
    sim2 = Sim().run(490, 510, icons, threat=lambda t: 2 if t >= 500 else 0)
    assert not [x for x in sim2.texts() if "contre" in x]


def test_numbers_advantage():
    def icons(t):
        return (me_at(TOP_LANE) + [icon("Vi", "ally", (0.13, 0.13)), icon("Lux", "ally", (0.07, 0.17)),
                                   icon("Darius", "enemy", (0.11, 0.10)), icon("LeeSin", "enemy", (0.3, 0.55))])
    sim = Sim().run(500, 510, icons)
    assert "3 contre 1 autour de toi : bonne fenêtre pour engager." in sim.texts()


# ------------------------------------------------------------------------------ pressure
def test_enemy_team_grouped_bot_push_top():
    def icons(t):
        return me_at(TOP_LANE) + [icon(a, "enemy", p) for a, p in (
            ("Jinx", (0.80, 0.90)), ("Thresh", (0.78, 0.86)), ("Ahri", (0.85, 0.82)), ("LeeSin", (0.75, 0.80)))]
    sim = Sim().run(900, 960, icons)
    assert "L'équipe ennemie est groupée en bas : tu peux pousser en haut." in sim.texts()
    p = sim.coach.pressure()
    assert p["grouped"] and p["side"] == "bot" and p["visible"] == 4


# ------------------------------------------------------------------------------ personal
def test_cs_checkpoint_at_10_min_and_safe_mode_ok():
    cfg = SimpleNamespace(safe_mode=True)

    def game(t):
        return make_game(t, cs=72)
    sim = Sim(cfg).run(598, 610, lambda t: me_at(TOP_TOWER_SAFE), game=game)
    assert sim.texts() == ["10 min : 7,2 CS par minute, bon farm, continue."]
    sim2 = Sim(cfg).run(598, 610, lambda t: me_at(TOP_TOWER_SAFE), game=lambda t: make_game(t, cs=55))
    assert sim2.texts() == ["10 min : 5,5 CS par minute, objectif 7."]


def test_vision_reminder_after_3_minutes_without_ward():
    def game(t):
        return make_game(t, ward=5.0 if t < 400 else 5.0)
    sim = Sim().run(390, 600, lambda t: me_at(TOP_TOWER_SAFE) + [icon("LeeSin", "enemy", (0.3, 0.15))],
                    game=game)
    vis = [s for s in sim.said if s[1] == "Pense à placer une balise."]
    assert len(vis) == 1 and 570 <= vis[0][0] <= 571

    def game2(t):   # ward score goes up every minute: never reminded
        return make_game(t, ward=5.0 + (t - 390) // 60)
    sim2 = Sim().run(390, 600, lambda t: me_at(TOP_TOWER_SAFE) + [icon("LeeSin", "enemy", (0.3, 0.15))],
                     game=game2)
    assert "Pense à placer une balise." not in sim2.texts()


def test_level6_once():
    sim = Sim().run(300, 400, lambda t: me_at(TOP_TOWER_SAFE) + [icon("LeeSin", "enemy", (0.3, 0.15))],
                    game=lambda t: make_game(t, level=5 if t < 320 else 6))
    assert sim.texts().count("Niveau 6 : cherche une action avec ton ultime.") == 1


# ------------------------------------------------------------------------------ deep in enemy jungle
def test_deep_in_enemy_jungle_with_jungler_unseen():
    def icons(t):
        out = me_at(ENEMY_JUNGLE_RED_BOT if t >= 700 else BOT_LANE)
        if t < 660:
            out.append(icon("LeeSin", "enemy", (0.2, 0.4)))
        return out
    sim = Sim(role="BOTTOM").run(650, 720, icons)
    assert "Tu es dans la jungle ennemie et leur jungler est invisible : attention." in sim.texts()


# ------------------------------------------------------------------------------ policy
def test_global_gap_40s_between_tips():
    # many conditions at once: grouped enemies + level 6 + cs checkpoint
    def icons(t):
        return me_at(TOP_LANE) + [icon(a, "enemy", p) for a, p in (
            ("Jinx", (0.80, 0.90)), ("Thresh", (0.78, 0.86)), ("Ahri", (0.85, 0.82)), ("LeeSin", (0.75, 0.80)))]
    sim = Sim().run(600, 700, icons, game=lambda t: make_game(t, cs=80, level=6))
    times = [s[0] for s in sim.said]
    assert len(times) >= 2
    assert all(b - a >= GLOBAL_GAP_S for a, b in zip(times, times[1:]))


def test_quiet_after_threat():
    def icons(t):
        out = me_at(TOP_LANE)
        if t >= 300:
            out.append(icon("LeeSin", "enemy", (0.72, 0.80)))
        return out
    sim = Sim().run(200, 320, icons, threat=lambda t: 1 if 296 <= t <= 302 else 0)
    # the fresh sighting at 300 happened during the threat: nothing said within the quiet time
    assert all(not (296 <= s[0] < 310) for s in sim.said)


def test_rule_classification():
    for rule in ("jungler_side", "jungler_unseen", "missing", "objective_window", "numbers_bad", "numbers_good",
                 "pressure", "deep", "wave_push", "wave_back", "wave_big", "lane_left", "lane_recall",
                 "bot_missing", "objective_trade"):
        assert rule in ENEMY_RULES
    for rule in ("objective_setup", "cs_check", "vision", "level6", "level_diff", "item_spike", "jg_level6",
                 "kill_lead"):
        assert rule not in ENEMY_RULES       # objective timing + public scoreboard: allowed in safe mode


def test_safe_mode_no_enemy_derived_tips_at_all():
    cfg = SimpleNamespace(safe_mode=True)

    def icons(t):
        out = me_at(TOP_LANE if t < 700 else ENEMY_JUNGLE_RED_BOT)
        if t < 610:
            out += [icon(a, "enemy", p) for a, p in (("Darius", (0.1, 0.25)), ("Ahri", (0.09, 0.33)),
                                                     ("Jinx", (0.05, 0.3)), ("LeeSin", (0.72, 0.8)))]
        return out
    sim = Sim(cfg).run(590, 800, icons, game=lambda t: make_game(t, cs=70))
    for text in sim.texts():
        assert "ennemi" not in text and "jungler" not in text.lower() and "contre" not in text
    assert all("ennemi" not in s and "JGL" not in s for s in sim.coach.insights())


def test_disabled_coach_still_gives_insights():
    cfg = SimpleNamespace(macro_coach=False)
    sim = Sim(cfg).run(240, 260, lambda t: me_at(MID), objectives=_dragon(300.0))
    assert sim.texts() == []
    assert sim.coach.insight().startswith("Dragon 0:")


def test_reset_and_new_game():
    sim = Sim().run(598, 605, lambda t: me_at(TOP_TOWER_SAFE), game=lambda t: make_game(t, cs=80))
    assert len(sim.said) == 1
    sim.coach.reset()
    sim.said.clear()
    sim.tracker.reset()
    sim.run(598, 605, lambda t: me_at(TOP_TOWER_SAFE), game=lambda t: make_game(t, cs=80))
    assert len(sim.said) == 1


# ------------------------------------------------------------------------------ HUD line
def test_hud_insight_line_adds_one_row():
    from treeaicoach.overlay_render import OverlayState, hud_size, render_hud

    base = OverlayState(game_time=600.0)
    with_line = OverlayState(game_time=600.0, insight="Dragon 0:45 · 2 ennemis en bas")
    w0, h0 = hud_size(base, 300)
    w1, h1 = hud_size(with_line, 300)
    assert w0 == w1 and h1 > h0
    img = render_hud(with_line, 300, now=0.0)
    assert img.shape[0] == h1 and img.shape[2] == 4 and img[..., 3].max() > 0


def test_engine_wires_coach_into_overlay_state(tmp_path, monkeypatch):
    from treeaicoach import paths
    from treeaicoach.coach import MapCoach as _MC
    from treeaicoach.config import Config
    from treeaicoach.demo import DemoSource
    from treeaicoach.detector import ClassicDetector
    from treeaicoach.engine import CoachEngine

    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    paths._reset_cache()

    class Voice:
        backend = "fake"

        def __init__(self):
            self.said = []

        def say(self, text, level=1):
            self.said.append(text)

    clock = SimpleNamespace(t=0.0)
    eng = CoachEngine(Config(), Voice(), detector=ClassicDetector(), frame_source=DemoSource(size=200),
                      clock=lambda: clock.t, enable_hotkeys=False, manage_overlay=False)
    try:
        for i in range(20):
            clock.t = i / 4.0
            eng.step(clock.t)
        assert isinstance(eng._coach, _MC)
        state = eng.get_overlay_state()
        assert state is not None and hasattr(state, "insight")
    finally:
        eng.stop()
        paths._reset_cache()


# ------------------------------------------------------------------------------ minion waves
def _lane_point(lane: str, frac: float) -> tuple[float, float]:
    """Point at ``frac`` of a lane measured from the BLUE base."""
    import math
    from treeaicoach.waves import _POLYS

    poly = _POLYS[lane]
    lens = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(poly, poly[1:])]
    x = frac * sum(lens)
    for (a, b), seg in zip(zip(poly, poly[1:]), lens):
        if x <= seg:
            k = x / seg
            return a[0] + k * (b[0] - a[0]), a[1] + k * (b[1] - a[1])
        x -= seg
    return poly[-1]


def _minimap(minions: list[tuple[str, float, str]], size: int = 256):
    from treeaicoach.render import MinimapRenderer, Scene

    pts = [(*_lane_point(lane, f), team) for lane, f, team in minions]
    return MinimapRenderer().render(Scene(size=size, minions=pts, vision=[(0.5, 0.5, 1.0)], fog_alpha=0.0))


@pytest.fixture(scope="module")
def wave_images():
    push = _minimap([("top", 0.66 + 0.02 * i, "ally") for i in range(4)]
                    + [("top", 0.76 + 0.02 * i, "enemy") for i in range(3)])
    back = _minimap([("top", 0.30 + 0.022 * i, "enemy") for i in range(5)] + [("top", 0.27, "ally")])
    big = _minimap([("mid", 0.43 + 0.02 * i, "enemy") for i in range(8)] + [("mid", 0.40, "ally")])
    return {"push": push, "back": back, "big": big}


def test_minion_detection_and_wave_state(wave_images):
    from treeaicoach.waves import analyze_waves, detect_minions

    w = analyze_waves(detect_minions(wave_images["push"]), "ORDER")["top"]
    assert w.ally >= 3 and w.enemy >= 2 and w.state == "pushing" and w.meet > 0.62
    w = analyze_waves(detect_minions(wave_images["back"]), "ORDER")["top"]
    assert w.state == "pushed_in" and w.enemy >= 3 and w.ally <= 1
    # same picture seen from the red side: the wave is near THEIR base -> pushing for them
    w = analyze_waves(detect_minions(wave_images["back"]), "CHAOS")["top"]
    assert w.state == "pushing"
    assert detect_minions(None) == [] and detect_minions(wave_images["push"][:20, :20]) == []


def test_wave_push_tip(wave_images):
    sim = Sim().run(300, 306, lambda t: me_at(TOP_LANE) + [icon("LeeSin", "enemy", (0.3, 0.55))],
                    frame=lambda t: wave_images["push"])
    assert sim.texts() == ["Ta vague pousse vers leur tour : bon moment pour rentrer après l'avoir poussée."]
    assert sim.coach.waves()["top"]["state"] == "pushing"


def test_wave_back_tip(wave_images):
    sim = Sim().run(300, 306, lambda t: me_at(TOP_LANE) + [icon("LeeSin", "enemy", (0.3, 0.55))],
                    frame=lambda t: wave_images["back"])
    assert sim.texts() == ["La vague revient vers toi : attends-la sous ta tour."]


def test_big_wave_tip(wave_images):
    sim = Sim().run(300, 306, lambda t: me_at(TOP_LANE) + [icon("LeeSin", "enemy", (0.3, 0.15))],
                    frame=lambda t: wave_images["big"])
    assert sim.texts() == ["Grosse vague ennemie qui arrive au milieu."]


def test_waves_ignored_in_safe_mode(wave_images):
    sim = Sim(SimpleNamespace(safe_mode=True)).run(300, 306, lambda t: me_at(TOP_LANE),
                                                    frame=lambda t: wave_images["push"])
    assert sim.texts() == [] and sim.coach.waves() == {}


# ------------------------------------------------------------------------------ lane opponents
def test_lane_opponent_left_lane():
    def icons(t):
        out = me_at(TOP_LANE) + [icon("LeeSin", "enemy", (0.3, 0.55))]
        if t < 300:
            out.append(icon("Darius", "enemy", (0.16, 0.085)))
        return out
    sim = Sim().run(290, 330, icons)
    left = [s for s in sim.said if "quitté la voie" in s[1]]
    assert [s[1] for s in left] == ["Darius a quitté la voie : pousse et prends des plaques, ping s'il roam."]
    assert 307.5 <= left[0][0] <= 309.5


def test_lane_opponent_recalled():
    def icons(t):
        out = me_at(TOP_LANE) + [icon("LeeSin", "enemy", (0.3, 0.55))]
        if t < 300:
            out.append(icon("Darius", "enemy", (0.16, 0.085) if t < 295 else (0.90, 0.10)))
        return out
    sim = Sim().run(285, 330, icons)
    assert "Darius est rentré : pousse ta vague et récupère des plaques." in sim.texts()


def test_both_bot_laners_missing_for_mid():
    def icons(t):
        out = me_at((0.45, 0.55)) + [icon("LeeSin", "enemy", (0.3, 0.15))]
        if t < 300:
            out += [icon("Jinx", "enemy", (0.75, 0.92)), icon("Thresh", "enemy", (0.80, 0.91))]
        return out
    sim = Sim(role="MIDDLE").run(290, 330, icons)
    assert "Les deux bot ennemis ont disparu : prudence, ils peuvent roam." in sim.texts()


# ------------------------------------------------------------------------------ scoreboard
def test_level_difference_vs_lane_opponent():
    sim = Sim().run(400, 405, lambda t: me_at(TOP_TOWER_SAFE), game=lambda t: make_game(t, level=9, elevel=7))
    assert sim.texts() == ["Tu as 2 niveaux d'avance sur Darius : joue agressif."]
    sim = Sim().run(400, 405, lambda t: me_at(TOP_TOWER_SAFE), game=lambda t: make_game(t, level=7, elevel=10))
    assert sim.texts() == ["Darius a 3 niveaux d'avance sur toi : joue prudent."]


def test_item_spike_also_in_safe_mode():
    def game(t):
        return make_game(t, eitems={"Darius": [1055, 3071] if t >= 402 else [1055]})
    for cfg in (None, SimpleNamespace(safe_mode=True)):
        sim = Sim(cfg).run(400, 406, lambda t: me_at(TOP_TOWER_SAFE), game=game)
        assert sim.texts() == ["Darius vient de finir Couperet noir : attention à son pic de puissance."]


def test_enemy_jungler_level6_first():
    sim = Sim().run(400, 405, lambda t: me_at(TOP_TOWER_SAFE), game=lambda t: make_game(t, level=5, jlevel=6))
    assert sim.texts() == ["Leur jungler est niveau 6 avant le vôtre : prudence."]


def test_kill_lead_summary():
    sim = Sim().run(898, 905, lambda t: me_at(TOP_TOWER_SAFE), game=lambda t: make_game(t, akills=12, ekills=5))
    assert sim.texts() == ["Vous menez 12 à 5 aux kills : jouez les objectifs."]
    sim = Sim().run(898, 905, lambda t: me_at(TOP_TOWER_SAFE), game=lambda t: make_game(t, akills=2, ekills=9))
    assert sim.texts() == ["Vous êtes derrière, 2 à 9 : jouez groupés et farmez."]


# ------------------------------------------------------------------------------ trading / jungler role
def test_objective_trade_cross_map():
    objs = [ObjectiveState(name="Dragon", next_spawn=1000.0, alive=True, source="schedule", key="dragon"),
            ObjectiveState(name="Larves", next_spawn=360.0, alive=True, source="schedule", key="grubs")]
    dx, dy = PITS["dragon"][0]

    def icons(t):
        return me_at((0.40, 0.45)) + [icon(a, "enemy", (dx + ox, dy + oy)) for a, ox, oy in (
            ("Jinx", 0.03, 0.02), ("Thresh", -0.03, 0.03), ("Ahri", 0.0, -0.04), ("LeeSin", 0.05, -0.02))]
    sim = Sim().run(1100, 1105, icons, objectives=objs)
    assert sim.texts() == ["4 ennemis au dragon : prenez les larves ou des tours en haut."]


def test_jungler_role_gets_invade_tip():
    def icons(t):
        out = me_at((0.28, 0.45))                    # my (blue) top-side jungle
        if t < 260:
            out.append(icon("LeeSin", "enemy", (0.55, 0.25)))
        if t >= 300:
            out.append(icon("LeeSin", "enemy", (0.72, 0.80)))
        return out
    sim = Sim(role="JUNGLE").run(240, 310, icons, game=lambda t: make_game(t, role="JUNGLE", ward=t // 60))
    assert "Leur jungler est en bas : envahis sa jungle du haut ou prends tes camps." in sim.texts()


def test_scoreboard_tips_can_be_disabled():
    cfg = SimpleNamespace(coach_scoreboard_tips=False)
    sim = Sim(cfg).run(400, 405, lambda t: me_at(TOP_TOWER_SAFE), game=lambda t: make_game(t, level=9, elevel=7))
    assert sim.texts() == []


def test_engine_speaks_end_of_game_summary(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path

    from treeaicoach import paths
    from treeaicoach.config import Config
    from treeaicoach.engine import CoachEngine

    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    paths._reset_cache()
    said = []
    voice = SimpleNamespace(backend="fake", say=lambda text, level=1: said.append(text))
    eng = CoachEngine(Config(), voice, clock=lambda: 0.0, enable_hotkeys=False, manage_overlay=False)
    src = Path(__file__).resolve().parent / "fixtures" / "game_record_sample.json"
    dst = tmp_path / "g.json"
    shutil.copy(src, dst)
    eng._say_game_summary(dst)
    eng._say_game_summary(tmp_path / "missing.json")          # never raises
    assert said and said[0].startswith("Victoire en 28 minutes.")
    paths._reset_cache()
