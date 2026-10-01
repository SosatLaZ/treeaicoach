"""Tracking robustness v2: Kalman filter, impossible-jump gating, stacked icons, champion locker
(tracker.py), wall-aware gank travel times (gank.py), fog-circle re-anchoring from Live Client
facts (fog_tracker.py) and the "already on my screen" voice rule (engine.py)."""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from treeaicoach import fog_tracker as ft
from treeaicoach import geometry
from treeaicoach.alerts import Alert, AlertKind, Level
from treeaicoach.config import Config
from treeaicoach.gank import ETA_FLASH, ETA_REF_SPEED, GankAnalyzer, _PathDistance
from treeaicoach.tracker import (
    HIDE_AFTER,
    STACK_HOLD_S,
    TENTATIVE_FORGET_S,
    Track,
    Tracker,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_gank as G  # noqa: E402
from test_tracker import ident  # noqa: E402

DT = 1.0 / 8.0


def feed(tr: Tracker, t0: float, t1: float, frame, dt: float = DT) -> float:
    t = t0
    while t < t1 - 1e-9:
        tr.update(t, frame(t))
        t += dt
    return t


# ====================================================================== Kalman filter
def test_kalman_velocity_and_prediction_on_linear_motion() -> None:
    tr = Tracker()
    feed(tr, 0.0, 3.0, lambda t: [ident(0.3 + 0.025 * t, 0.5 - 0.01 * t, alias="LeeSin")])
    lee = tr.get("LeeSin")
    vx, vy = lee.kf_velocity()
    assert vx == pytest.approx(0.025, abs=0.002) and vy == pytest.approx(-0.01, abs=0.002)
    kp = lee.kf_position()
    t_last = lee.last_seen
    assert kp == pytest.approx((0.3 + 0.025 * t_last, 0.5 - 0.01 * t_last), abs=0.003)
    # short miss: prediction keeps moving (damped), never past the linear extrapolation
    p = lee.predict(t_last + 0.4)
    assert kp[0] < p[0] <= kp[0] + 0.025 * 0.4 + 1e-9
    # beyond the horizon the extrapolation stops growing
    assert lee.predict(t_last + 5.0) == pytest.approx(lee.predict(t_last + 0.5))


def test_kalman_soft_gate_limits_a_single_outlier() -> None:
    tr = Tracker()
    feed(tr, 0.0, 2.0, lambda t: [ident(0.5, 0.5, alias="Ahri")])
    tr.update(2.0, [ident(0.56, 0.55, alias="Ahri")])          # 0.08 off, plausible in distance
    kp = tr.get("Ahri").kf_position()
    assert math.dist(kp, (0.5, 0.5)) < 0.03                    # down-weighted, not followed


def test_kalman_noise_velocity_is_bounded() -> None:
    rng = random.Random(3)
    tr = Tracker()
    feed(tr, 0.0, 30.0, lambda t: [ident(0.5 + rng.uniform(-0.005, 0.005),
                                         0.5 + rng.uniform(-0.005, 0.005), alias="Ahri")])
    assert math.hypot(*tr.get("Ahri").kf_velocity()) < 0.03


# ====================================================================== impossible jumps
def test_impossible_jump_needs_confirmation_unless_fountain() -> None:
    tr = Tracker()
    feed(tr, 0.0, 2.0, lambda t: [ident(0.3 + 0.02 * t, 0.5, alias="Zed", team="CHAOS")])
    before = tr.get("Zed").raw_position()
    # 1) misidentified far icon for one frame: dropped, the track is unchanged
    tr.update(2.0, [ident(0.8, 0.8, alias="Zed", team="CHAOS")])
    assert tr.get("Zed").raw_position() == before
    tr.update(2.125, [ident(before[0] + 0.003, 0.5, alias="Zed", team="CHAOS")])
    assert tr.get("Zed").raw_position()[0] == pytest.approx(before[0] + 0.003)
    # 2) teleport: two consistent far observations -> accepted, history reset
    tr.update(2.25, [ident(0.8, 0.8, alias="Zed", team="CHAOS")])
    assert tr.get("Zed").raw_position()[0] < 0.5
    tr.update(2.375, [ident(0.801, 0.8, alias="Zed", team="CHAOS")])
    z = tr.get("Zed")
    assert z.raw_position() == pytest.approx((0.801, 0.8)) and len(z.points()) == 1
    # 3) recall to his own fountain: accepted at once
    tr.update(2.5, [ident(0.95, 0.05, alias="Zed", team="CHAOS")])
    assert tr.get("Zed").raw_position() == pytest.approx((0.95, 0.05))


def test_long_hidden_reappearance_far_away_is_gated_too() -> None:
    tr = Tracker()
    feed(tr, 0.0, 1.0, lambda t: [ident(0.2, 0.2, alias="Vi", team="CHAOS")])
    tr.update(3.0, [ident(0.9, 0.6, alias="Vi", team="CHAOS")])   # 0.76 in 2 s: impossible
    assert tr.get("Vi").raw_position() == pytest.approx((0.2, 0.2))
    tr.update(40.0, [ident(0.9, 0.6, alias="Vi", team="CHAOS")])  # 39 s later: walkable
    assert tr.get("Vi").raw_position() == pytest.approx((0.9, 0.6))


# ====================================================================== stacked icons
def stack_frames(t: float, until: float) -> list:
    """Darius at (0.30, 0.20); Lee Sin walks onto him and is drawn under him until ``until``."""
    out = [ident(0.30, 0.20, alias="Darius", team="CHAOS")]
    if t < 1.0 or t >= until:
        out.append(ident(0.30 + 0.02, 0.20 + 0.01, alias="LeeSin", team="CHAOS"))
    return out


def test_stacked_icon_stays_visible_and_follows_the_occluder() -> None:
    tr = Tracker()
    feed(tr, 0.0, 3.0, lambda t: stack_frames(t, until=99.0))
    lee = tr.get("LeeSin")
    assert lee.visible and lee.stacked_with == "Darius"
    assert lee.position() == pytest.approx(tr.get("Darius").position())
    assert lee.last_seen < 1.0 and lee.hidden_since is None
    assert [t.key for t in tr.enemies()] == ["Darius", "LeeSin"]


def test_stacked_hold_expires_and_never_starts_a_fog_circle_meanwhile() -> None:
    tr = Tracker()
    fog = ft.FogTracker()
    game = G.make_game(0.0)
    t = 0.0
    starts: list[float] = []
    while t < 1.0 + STACK_HOLD_S + 2.0:
        tr.update(t, stack_frames(t, until=99.0))
        starts += [t for e in fog.update(t, tr, game) if e.key == "LeeSin"]
        t += DT
    assert starts and min(starts) > 1.0 + STACK_HOLD_S - 1e-6      # only after the hold
    lee = tr.get("LeeSin")
    assert not lee.visible and lee.stacked_with is None
    # conservative: elapsed counted from the real last sighting, centred on the occluder
    e = fog.estimate_for("LeeSin")
    assert e is not None and e.last_seen < 1.0 and e.last_uv == pytest.approx((0.30, 0.20), abs=0.01)


def test_occluder_vanishing_ends_the_stack_at_its_last_sighting() -> None:
    tr = Tracker()
    feed(tr, 0.0, 3.0, lambda t: stack_frames(t, until=99.0))
    feed(tr, 3.0, 4.5, lambda t: [])                  # both go into the fog at ~3 s
    lee = tr.get("LeeSin")
    assert not lee.visible and lee.stacked_with is None
    assert lee.last_seen == pytest.approx(3.0 - DT)      # = Darius's last sighting
    assert lee.position() == pytest.approx((0.30, 0.20), abs=0.002)


def test_separating_from_a_stack_is_not_a_fog_reappearance() -> None:
    tr = Tracker()
    feed(tr, 0.0, 4.0, lambda t: stack_frames(t, until=3.0))
    lee = tr.get("LeeSin")
    assert lee.visible and lee.stacked_with is None and lee.appeared_at == 0.0
    assert lee.raw_position() == pytest.approx((0.32, 0.21))


def test_stacked_lane_opponent_is_not_missing() -> None:
    cfg = Config(alert_laner_mia=True)

    def enemies(t: float) -> dict:
        # Lee Sin stands on Darius; Darius's icon is drawn under Lee Sin's from 10 s, Lee Sin
        # leaves at 15.5 s and Darius shows up again at 19 s (9 s without his own icon)
        d = G.darius_wobble(t)
        lee = (d[0] + 0.012, d[1] + 0.008) if t < 15.5 else (d[0] + 0.012 + 0.025 * (t - 15.5), d[1])
        out = {"LeeSin": lee}
        if not 10.0 <= t < 19.0:
            out["Darius"] = d
        return out

    assert G.raw_of(G.simulate(22.0, enemies, cfg=cfg), AlertKind.LANER_MIA) == []
    # without the stacked hold (v1 behaviour) the same sequence said "Darius a disparu"
    import treeaicoach.tracker as T

    saved = T.STACK_RADII
    T.STACK_RADII = 0.0
    try:
        mia = G.raw_of(G.simulate(22.0, enemies, cfg=cfg), AlertKind.LANER_MIA)
    finally:
        T.STACK_RADII = saved
    assert [round(tk.t) for tk, _a in mia] == [16]


def test_icon_far_from_any_other_icon_is_not_stacked() -> None:
    tr = Tracker()
    feed(tr, 0.0, 1.0, lambda t: [ident(0.3, 0.2, alias="Darius"), ident(0.5, 0.5, alias="LeeSin")])
    tr.update(1.0 + HIDE_AFTER + 0.1, [ident(0.3, 0.2, alias="Darius")])
    lee = tr.get("LeeSin")
    assert not lee.visible and lee.stacked_with is None


# ====================================================================== champion locker
def test_sporadic_anonymous_detections_never_become_tracks() -> None:
    tr = Tracker()
    for i in range(200):                              # one blip every 5 frames, same place
        tr.update(i * DT, [ident(0.4, 0.4)] if i % 5 == 0 else [])
        assert tr.enemies(visible_only=False) == []
    assert all(not t.confirmed for t in [tr.get("enemy?%d" % k) for k in range(1, 60)] if t)


def test_low_confidence_anonymous_icon_is_never_confirmed() -> None:
    tr = Tracker()
    feed(tr, 0.0, 5.0, lambda t: [ident(0.4, 0.4, score=0.3)])
    assert tr.enemies(visible_only=False) == [] and tr.tracks() == []
    tentative = tr.get("enemy?1")
    assert tentative is not None and not tentative.confirmed


def test_steady_anonymous_icon_is_confirmed_on_its_third_frame() -> None:
    tr = Tracker()
    tr.update(0.0, [ident(0.4, 0.4)])
    tr.update(DT, [ident(0.4, 0.4)])
    assert tr.enemies() == []
    tr.update(2 * DT, [ident(0.4, 0.4)])
    assert [t.key for t in tr.enemies()] == ["enemy?1"]


def test_tentative_tracks_forgotten_fast_identities_and_self_exempt() -> None:
    tr = Tracker()
    tr.update(0.0, [ident(0.4, 0.4), ident(0.1, 0.9, "self"), ident(0.7, 0.7, alias="Ahri")])
    assert tr.me() is not None                        # "self" is never held back
    assert [t.key for t in tr.enemies()] == ["Ahri"]  # an identity is its own confirmation
    tr.update(TENTATIVE_FORGET_S + 0.5, [])
    assert tr.get("enemy?1") is None


def test_flickering_false_positives_raise_no_gank_alert() -> None:
    rng = random.Random(7)
    clusters: dict[int, tuple] = {}
    i = 0
    while i < 480:                                     # 2-frame clusters every ~1.5 s, near me
        p = (G.ME_TOP[0] + rng.uniform(-0.08, 0.08), G.ME_TOP[1] + rng.uniform(-0.08, 0.08))
        clusters[i] = clusters[i + 1] = p
        i += rng.randint(8, 16)

    def enemies(t: float) -> dict:
        p = clusters.get(int(round(t / DT)))
        return {"?1": p} if p is not None else {}

    assert G.gank_raw(G.simulate(60.0, enemies)) == []


# ====================================================================== wall-aware ETA
ME_BOT_WALL = (0.60, 0.915)
BEHIND_WALL = [(0.645, 0.715), (0.615, 0.752)]     # my bot-side jungle: 0.16-0.21 straight, >= 0.25 by path


def test_eta_thresholds_match_the_radii_in_the_open() -> None:
    an = GankAnalyzer(Config())
    warn_s, danger_s = an.eta_thresholds()
    assert warn_s == pytest.approx((0.22 - ETA_FLASH) / ETA_REF_SPEED) and 7.0 < warn_s < 7.6
    assert danger_s == pytest.approx((0.12 - ETA_FLASH) / ETA_REF_SPEED) and 3.4 < danger_s < 3.7
    sens = GankAnalyzer(Config(sensitivity=1.5)).eta_thresholds()
    assert sens[0] > warn_s and sens[1] > danger_s


def test_path_distance_goes_around_walls_and_is_cached() -> None:
    pd = _PathDistance()
    open_d = pd.distance(G.ME_TOP, (G.ME_TOP[0] + 0.1, G.ME_TOP[1] + 0.02), 0.4)
    assert open_d == pytest.approx(math.dist(G.ME_TOP, (G.ME_TOP[0] + 0.1, G.ME_TOP[1] + 0.02)), abs=0.01)
    field = pd._field
    wall = pd.distance(G.ME_TOP, (G.ME_TOP[0] + 0.05, G.ME_TOP[1]), 0.4)
    assert pd._field is field                           # same cell of mine: no recomputation
    behind = pd.distance(ME_BOT_WALL, BEHIND_WALL[1], 0.4)
    assert behind > 1.4 * math.dist(ME_BOT_WALL, BEHIND_WALL[1]) and wall is not None


def jungler_behind_wall(t: float) -> dict:
    return {"LeeSin": G.lerp_path(BEHIND_WALL, 0.025, t)}


def test_enemy_behind_a_wall_walking_towards_me_is_not_a_gank() -> None:
    assert all(math.dist(ME_BOT_WALL, G.lerp_path(BEHIND_WALL, 0.025, t)) < G.WARN for t in (0.0, 2.0))
    game = (lambda t: G.make_game(t, me_alias="Jinx"))
    ticks = G.simulate(4.0, jungler_behind_wall, me=lambda t: ME_BOT_WALL, me_alias="Jinx", game=game)
    assert G.gank_raw(ticks) == []


def test_same_straight_distance_in_the_open_is_a_gank() -> None:
    # same approach, rotated into the open top lane: WARNING
    d0 = math.dist(ME_BOT_WALL, BEHIND_WALL[0])
    path = [(G.ME_TOP[0] + d0 * 0.95, G.ME_TOP[1] + 0.03), G.ME_TOP]
    ticks = G.simulate(4.0, lambda t: {"LeeSin": G.lerp_path(path, 0.025, t)})
    assert any(a.level == Level.WARNING for _tk, a in G.gank_raw(ticks))


def test_straight_line_fallback_without_walkable_mask(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_PathDistance, "_get_reach", lambda self: None)
    game = (lambda t: G.make_game(t, me_alias="Jinx"))
    ticks = G.simulate(4.0, jungler_behind_wall, me=lambda t: ME_BOT_WALL, me_alias="Jinx", game=game)
    assert any(a.level == Level.WARNING for _tk, a in G.gank_raw(ticks))    # the v1 behaviour


def test_state_reports_travel_times() -> None:
    an = GankAnalyzer(Config())
    p = (G.ME_TOP[0] + 0.10, G.ME_TOP[1])
    G.simulate(1.0, lambda t: {"LeeSin": p}, analyzer=an)
    etas = dict(an.state().etas)
    assert etas["LeeSin"] == pytest.approx((0.10 - ETA_FLASH) / ETA_REF_SPEED, abs=0.5)


# ====================================================================== fog re-anchoring
def _game(t: float, *, dead: bool = False, timer: float = 0.0, events: list | None = None):
    g = G.make_game(t)
    lee = next(p for p in g.enemies if p.champion_alias == "LeeSin")
    lee.is_dead, lee.respawn_timer = dead, timer
    g.events = list(events or [])
    return g


def _lee_track(last_seen: float, pos=(0.40, 0.30)) -> Track:
    tr = Track(key="LeeSin", alias="LeeSin", relation="enemy", team="CHAOS", first_seen=0.0,
               last_seen=last_seen)
    tr.observe(last_seen, *pos, 0.047, 0.9, 0.8)
    tr.refresh(last_seen + 10.0)
    return tr


class _Trk:
    def __init__(self, *tracks: Track) -> None:
        self.ts = {t.key: t for t in tracks}

    def enemies(self, visible_only: bool = True):
        return [t for t in self.ts.values() if t.relation == "enemy" and (t.visible or not visible_only)]

    def get(self, key):
        return self.ts.get(key)

    def me(self):
        return next((t for t in self.ts.values() if t.relation == "self"), None)


def test_respawn_restarts_the_region_from_the_enemy_fountain() -> None:
    fog = ft.FogTracker()
    trk = _Trk(_lee_track(0.0))
    assert fog.update(5.0, trk, _game(5.0))[0].last_uv == pytest.approx((0.40, 0.30), abs=0.01)
    assert fog.update(6.0, trk, _game(6.0, dead=True, timer=10.0)) == []
    assert fog.update(15.0, trk, _game(15.0, dead=True, timer=1.0)) == []
    e = fog.update(16.5, trk, _game(16.5))[0]
    assert e.last_uv == pytest.approx(geometry.RED_FOUNTAIN)
    assert e.last_seen == pytest.approx(16.0) and e.elapsed == pytest.approx(0.5)


def test_kill_with_the_jungler_anchors_at_the_victim() -> None:
    fog = ft.FogTracker()
    vi = Track(key="Vi", alias="Vi", relation="ally", team="ORDER", first_seen=0.0, last_seen=0.0)
    for k in range(10):
        vi.observe(10.0 + k * 0.1, 0.62, 0.62, 0.047, 0.9, 0.8)
    trk = _Trk(_lee_track(0.0), vi)
    kill = {"EventName": "ChampionKill", "EventTime": 400.0 + 10.9, "KillerName": "Ahri",
            "VictimName": "Vi", "Assisters": ["LeeSin"]}
    e = fog.update(12.0, trk, _game(12.0, events=[kill]))[0]
    assert e.last_uv == pytest.approx((0.62, 0.62), abs=0.01)
    assert e.last_seen == pytest.approx(10.9, abs=0.01)
    assert fog.anchors()["leesin"][2] == "kill"


def test_objective_by_the_jungler_anchors_at_the_pit_and_old_facts_are_ignored() -> None:
    fog = ft.FogTracker()
    trk = _Trk(_lee_track(20.0))
    old = {"EventName": "BaronKill", "EventTime": 400.0 + 5.0, "KillerName": "LeeSin"}
    e = fog.update(25.0, trk, _game(25.0, events=[old]))[0]
    assert e.last_uv == pytest.approx((0.40, 0.30), abs=0.01)       # seen after the baron
    drake = {"EventName": "DragonKill", "EventTime": 400.0 + 30.0, "KillerName": "LeeSin"}
    e = fog.update(31.0, trk, _game(31.0, events=[old, drake]))[0]
    assert e.last_uv == pytest.approx(geometry.DRAGON_PIT[:2], abs=0.01) and e.elapsed == pytest.approx(1.0)


def test_anchor_without_any_sighting_still_gives_a_region_and_manual_anchor() -> None:
    fog = ft.FogTracker()
    drake = {"EventName": "DragonKill", "EventTime": 400.0 + 30.0, "KillerName": "LeeSin"}
    est = fog.update(32.0, _Trk(), _game(32.0, events=[drake]))
    assert [e.key for e in est] == ["LeeSin"] and est[0].is_jungler
    fog.anchor("LeeSin", (0.5, 0.5), 33.0, "test")
    assert fog.update(34.0, _Trk(), _game(34.0, events=[drake]))[0].last_uv == pytest.approx((0.5, 0.5))
    fog.reset()
    assert fog.anchors() == {}


# ====================================================================== on-screen voice rule
def _engine():
    from treeaicoach.engine import CoachEngine

    class _Voice:
        backend = "fake"

        def __init__(self) -> None:
            self.said: list = []

        def say(self, text: str, level: int = 1) -> None:
            self.said.append(text)

        def set_muted(self, on: bool) -> None:
            pass

    return CoachEngine(Config(), _Voice(), clock=lambda: 0.0, enable_hotkeys=False, manage_overlay=False)


def test_warning_about_an_enemy_on_my_screen_is_written_not_spoken(tmp_path, monkeypatch) -> None:
    from treeaicoach import paths

    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    paths._reset_cache()
    try:
        eng = _engine()
        tr = Tracker()
        tr.update(0.0, [ident(0.20, 0.22, alias="LeeSin", team="CHAOS"),
                        ident(0.60, 0.60, alias="Ahri", team="CHAOS")])
        eng._tracker = tr
        eng._camera = NS(current=lambda t: NS(u0=0.05, v0=0.15, u1=0.32, v1=0.30))
        warn_on = Alert(kind=AlertKind.JUNGLER_APPROACH, level=Level.WARNING, text="Lee Sin arrive par la rivière !",
                        key="jungler_approach:LeeSin", t=0.0, alias="LeeSin", members=("LeeSin",))
        warn_off = Alert(kind=AlertKind.ROAM_APPROACH, level=Level.WARNING, text="Ahri arrive !",
                         key="roam_approach:Ahri", t=0.0, alias="Ahri", members=("Ahri",))
        danger_on = Alert(kind=AlertKind.JUNGLER_APPROACH, level=Level.DANGER, text="Gank ! Lee Sin, recule !",
                          key="jungler_approach:LeeSin", t=0.0, alias="LeeSin", members=("LeeSin",))
        keep = eng._written_if_on_screen([warn_on, warn_off, danger_on], 1.0, 400.0, None)
        assert keep == [warn_off, danger_on]
        assert any(text == warn_on.text for _t, _k, text in eng.text_messages)
        eng._camera = NS(current=lambda t: None)            # no camera, no frame: everything spoken
        assert eng._written_if_on_screen([warn_on], 2.0, 400.0, None) == [warn_on]
    finally:
        paths._reset_cache()
