"""Self-check watchdog (selfcheck.py + engine_selfcheck.py): every rule on fake engine states
(symptom -> action -> status), hysteresis (no flapping), at most one in-game notice per problem
per game, and the engine wiring (minimap backoff / last good rect, load levels, voice override,
AI block, Live Client outage, tracker reset, presenter notices, health, report, diagnostic)."""

from __future__ import annotations

import json
import math
import sys
import threading
import time
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

from treeaicoach import paths
from treeaicoach import selfcheck as SC
from treeaicoach.capture import Rect
from treeaicoach.config import Config
from treeaicoach.engine import CoachEngine
from treeaicoach.selfcheck import MSG, NOTICES, Action, SelfCheck, Snapshot, summary_text

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_engine as TE  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    paths._reset_cache()
    yield
    paths._reset_cache()


@pytest.fixture
def restore_knobs():
    """Module knobs pushed by the engine's load levels (shared by every matcher / overlay)."""
    from treeaicoach import engine_selfcheck, overlay, roster_matcher

    saved = (roster_matcher.RING_PROP_EVERY, roster_matcher.STACKV_EVERY, roster_matcher.LOST_EVERY,
             overlay._budget_fps, dict(engine_selfcheck._RM_SAVED))
    yield
    (roster_matcher.RING_PROP_EVERY, roster_matcher.STACKV_EVERY, roster_matcher.LOST_EVERY,
     overlay._budget_fps) = saved[:4]
    engine_selfcheck._RM_SAVED.clear()
    engine_selfcheck._RM_SAVED.update(saved[4])


# ======================================================================================
# helpers
# ======================================================================================
def drive(sc: SelfCheck, t0: float, t1: float, make, dt: float = 1.0, mark: bool = True):
    """Evaluate ``make(t) -> Snapshot`` from t0 to t1 (1 Hz); notices are marked shown (like the
    engine does when the presenter accepted them). Returns [(t, Action)]."""
    out = []
    t = t0
    while t <= t1 + 1e-9:
        for a in sc.evaluate(make(t)):
            out.append((t, a))
            if a.kind == "notice":
                sc.mark_notice(a.rule, mark, t)
        t += dt
    return out


def kinds(acts, rule=None):
    return [a.kind for _t, a in acts if rule is None or a.rule == rule]


def status(sc: SelfCheck, rule: str):
    p = {p.rule: p for p in sc.problems()}.get(rule)
    return (p.status, p.level) if p is not None else None


# ======================================================================================
# 1 capture
# ======================================================================================
def test_capture_black_ladder_switch_recreate_then_borderless_instruction():
    sc = SelfCheck()
    acts = drive(sc, 0, 2, lambda t: Snapshot(t=t, game_time=200 + t, capture_black=True, capture_alt="mss"))
    assert acts == [] and sc.problems() == []                   # < 3 s: nothing yet
    acts = drive(sc, 3, 3, lambda t: Snapshot(t=t, game_time=200 + t, capture_black=True, capture_alt="mss"))
    assert [(a.kind, a.arg) for _t, a in acts] == [("capture_switch", "mss")]
    assert status(sc, "capture") == (MSG["capture_try"], 1)
    acts = drive(sc, 4, 20, lambda t: Snapshot(t=t, game_time=200 + t, capture_black=True, capture_alt="mss"))
    assert kinds(acts) == ["capture_recreate", "notice"]          # +6 s fresh capture, +6 s: the player acts
    assert [t for t, a in acts if a.kind == "capture_recreate"] == [9]
    assert status(sc, "capture") == (MSG["capture_black"], 2)
    assert [a.arg for _t, a in acts if a.kind == "notice"] == [NOTICES["capture"]]
    # live frames again: cleared after 5 s (hysteresis), reported in the game record
    drive(sc, 21, 24, lambda t: Snapshot(t=t, game_time=200 + t))
    assert status(sc, "capture") is not None
    drive(sc, 25, 27, lambda t: Snapshot(t=t, game_time=200 + t))
    assert status(sc, "capture") is None
    rep = sc.game_report()
    p = rep["problems"][0]
    assert p["rule"] == "capture" and p["level"] == 2 and p["outcome"] == "resolved"
    assert p["actions"] == ["capture : passage à mss", "capture : nouvelle initialisation"]
    assert rep["notices"] == ["capture"] and rep["ok"] is False


def test_capture_auto_fixed_and_stale_wording():
    sc = SelfCheck()
    drive(sc, 0, 4, lambda t: Snapshot(t=t, capture_status="black", capture_alt="mss"))
    drive(sc, 5, 11, lambda t: Snapshot(t=t))          # the other backend works: fixed automatically
    assert status(sc, "capture") is None
    assert sc.game_report()["problems"][0]["outcome"] == "fixed"
    assert "capture rétablie" in sc.summary()["fixed"]
    sc2 = SelfCheck()
    drive(sc2, 0, 20, lambda t: Snapshot(t=t, capture_status="stale"))
    assert status(sc2, "capture") == (MSG["capture_stale"], 2)


def test_capture_not_judged_while_occluded_minimized_or_no_window_and_no_flapping():
    sc = SelfCheck(rules=("capture",))
    for kw in ({"occluded": True}, {"minimized": True}, {"window": False}, {"in_game": False}):
        acts = drive(sc, 0, 30, lambda t, kw=kw: Snapshot(t=t, capture_black=True, **kw))
        assert acts == [] and sc.problems() == []
    # black one second out of two: never 3 s in a row -> nothing
    acts = drive(sc, 100, 200, lambda t: Snapshot(t=t, capture_black=int(t) % 2 == 0))
    assert acts == [] and sc.problems() == []


# ======================================================================================
# 2 minimap
# ======================================================================================
def mm(t, **kw):
    base = dict(t=t, game_time=300 + t, locate_method="auto", minimap_score=0.9, locate_score=0.92)
    base.update(kw)
    return Snapshot(**base)


def test_minimap_lost_escalates_after_three_failures_with_one_notice():
    sc = SelfCheck()
    acts = drive(sc, 0, 2, lambda t: mm(t, locate_method="fallback", loc_attempts=1, loc_fails=1))
    assert sc.problems() == []
    drive(sc, 3, 9, lambda t: mm(t, locate_method="fallback", loc_attempts=1, loc_fails=1))
    assert status(sc, "minimap") == (MSG["minimap_search"], 1)
    acts = drive(sc, 10, 30, lambda t: mm(t, locate_method="fallback", loc_attempts=1 + int(t // 10),
                                          loc_fails=1 + int(t // 10)))
    assert status(sc, "minimap") == (MSG["minimap_lost"], 2)
    assert [a.arg for _t, a in acts if a.kind == "notice"] == [NOTICES["minimap"]]
    assert "relocalisation" in sc.game_report()["problems"][0]["actions"][-1]
    # covered minimap (verify low) counts the same way
    sc2 = SelfCheck()
    drive(sc2, 0, 5, lambda t: mm(t, bad_s=float(t), minimap_score=0.2))
    assert status(sc2, "minimap") == (MSG["minimap_search"], 1)


def test_minimap_found_again_is_resolved_with_hysteresis():
    sc = SelfCheck()
    drive(sc, 0, 10, lambda t: mm(t, bad_s=float(t), loc_attempts=2, loc_fails=1))
    assert status(sc, "minimap") is not None
    drive(sc, 11, 14, lambda t: mm(t, loc_attempts=3))
    assert status(sc, "minimap") is not None                     # < 5 s healthy: still shown
    drive(sc, 15, 17, lambda t: mm(t, loc_attempts=3))
    assert status(sc, "minimap") is None
    assert sc.game_report()["problems"][0]["outcome"] == "fixed"
    # a manual rectangle (the player calibrated) is never judged
    sc2 = SelfCheck()
    assert drive(sc2, 0, 60, lambda t: mm(t, locate_method="manual", bad_s=5.0, loc_fails=4)) == []
    assert sc2.problems() == []


def test_minimap_drift_relocates_cheaply_at_most_once_a_minute_and_three_times():
    sc = SelfCheck()
    acts = drive(sc, 0, 400, lambda t: mm(t, minimap_score=0.5, locate_score=0.9))
    rel = [t for t, a in acts if a.kind == "relocate"]
    assert len(rel) == 3 and rel[0] >= SC.DRIFT_ON_S
    assert all(b - a >= SC.DRIFT_EVERY_S for a, b in zip(rel, rel[1:]))
    sc2 = SelfCheck()                                # normal fluctuations: nothing
    assert kinds(drive(sc2, 0, 200, lambda t: mm(t, minimap_score=0.72 if int(t) % 3 else 0.58))) == []


# ======================================================================================
# 3 perf (detection starving -> load levels)
# ======================================================================================
def perf(t, **kw):
    base = dict(t=t, game_time=300 + t, target_fps=6.0, tick_p95_ms=20.0)
    base.update(kw)
    return Snapshot(**base)


def test_perf_low_rate_steps_down_then_back_up_when_healthy():
    sc = SelfCheck()
    acts = drive(sc, 0, 30, lambda t: perf(t, detect_fps=2.5))
    lv = [(t, a.arg) for t, a in acts if a.kind == "perf_level"]
    assert lv == [(26, 1)]                                    # 6 s to judge + 20 s starving
    assert sc.load_level == 1 and status(sc, "perf") == (MSG["perf_1"], 1)
    assert sc.summary()["profile"] == "allégé"
    acts = drive(sc, 31, 80, lambda t: perf(t, detect_fps=2.5))
    assert [(t, a.arg) for t, a in acts if a.kind == "perf_level"] == [(56, 2)]   # settled 30 s, then down
    assert status(sc, "perf") == (MSG["perf_2"], 1)
    acts = drive(sc, 81, 300, lambda t: perf(t, detect_fps=6.0))
    ups = [(t, a.arg) for t, a in acts if a.kind == "perf_level"]
    assert [lv for _t, lv in ups] == [1, 0]
    assert ups[1][0] - ups[0][0] >= SC.HEALTHY_ON_S
    assert status(sc, "perf") is None and sc.summary()["profile"] == "normal"
    assert sc.game_report()["profile_max"] == "minimal"


def test_perf_slow_ticks_step_down_and_relapse_doubles_the_wait():
    sc = SelfCheck()
    acts = drive(sc, 0, 30, lambda t: perf(t, detect_fps=6.0, tick_p95_ms=85.0))
    assert [a.arg for _t, a in acts if a.kind == "perf_level"] == [1]
    # healthy -> up (60 s); starving again soon after -> down, and the next up waits twice as long
    acts = drive(sc, 31, 100, lambda t: perf(t, detect_fps=6.0, tick_p95_ms=20.0))
    t_up = [t for t, a in acts if a.kind == "perf_level"]
    assert len(t_up) == 1 and sc.load_level == 0
    acts = drive(sc, 101, 130, lambda t: perf(t, detect_fps=6.0, tick_p95_ms=85.0))
    assert [a.arg for _t, a in acts if a.kind == "perf_level"] == [1]
    t_down = [t for t, a in acts if a.kind == "perf_level"][0]
    acts = drive(sc, 131, t_down + 2 * SC.HEALTHY_ON_S - 1, lambda t: perf(t, detect_fps=6.0, tick_p95_ms=20.0))
    assert kinds(acts, "perf") == []                          # 60 s healthy is no longer enough
    acts = drive(sc, t_down + 2 * SC.HEALTHY_ON_S, t_down + 2 * SC.HEALTHY_ON_S + 40,
                 lambda t: perf(t, detect_fps=6.0, tick_p95_ms=20.0))
    assert [a.arg for _t, a in acts if a.kind == "perf_level"] == [0]


def test_perf_not_judged_while_not_detecting_and_hysteresis_band():
    sc = SelfCheck()
    assert drive(sc, 0, 120, lambda t: perf(t, detect_fps=0.5, detecting=False)) == []
    # between starving and healthy (p95 50 ms): no change either way, no flapping
    sc2 = SelfCheck()
    drive(sc2, 0, 30, lambda t: perf(t, detect_fps=2.0))
    assert sc2.load_level == 1
    acts = drive(sc2, 31, 400, lambda t: perf(t, detect_fps=6.0, tick_p95_ms=50.0))
    assert kinds(acts, "perf") == [] and sc2.load_level == 1


# ======================================================================================
# 4 champions seen
# ======================================================================================
FRIENDS = ("Garen", "Vi", "Ahri", "Jinx", "Lulu")


def champs(t, seen=("Garen",), last=None, **kw):
    last = t if last is None else last
    base = dict(t=t, game_time=400 + t, friends_alive=FRIENDS, frames_window=360,
                friends_seen={a: last for a in seen})
    base.update(kw)
    return Snapshot(**base)


def test_champions_ladder_recalibrate_reload_then_diag_once_per_game():
    sc = SelfCheck()
    acts = drive(sc, 0, 200, lambda t: champs(t))
    assert kinds(acts, "champions") == ["recalibrate", "reload_icons", "auto_diag", "notice"]
    tt = {a.kind: t for t, a in acts}
    assert tt["recalibrate"] == SC.CHAMP_ON_S and tt["reload_icons"] - tt["recalibrate"] == SC.CHAMP_STEP_S
    assert tt["auto_diag"] - tt["reload_icons"] == SC.CHAMP_STEP_S
    assert status(sc, "champions") == (MSG["champ_weak"], 2)
    sc.diag_started(tt["auto_diag"], True)
    assert status(sc, "champions") == (MSG["champ_weak_auto"], 2)
    assert "diagnostic automatique (60 s)" in sc.game_report()["problems"][0]["actions"]
    # back to normal, then weak again in the same game: no second automatic diagnostic, no 2nd notice
    drive(sc, 201, 230, lambda t: champs(t, seen=FRIENDS))
    assert status(sc, "champions") is None
    acts = drive(sc, 231, 500, lambda t: champs(t))
    assert "auto_diag" not in kinds(acts) and "notice" not in kinds(acts)
    assert status(sc, "champions") == (MSG["champ_weak"], 2)
    # a new game: allowed again
    sc.new_game(600.0, 0.0)
    acts = drive(sc, 600, 800, lambda t: champs(t))
    assert kinds(acts, "champions").count("auto_diag") == 1


def test_champions_not_judged_too_early_with_few_friends_little_detection_or_recent_deaths():
    for make in (lambda t: champs(t, game_time=30 + t / 4),                      # before 1:30
                 lambda t: champs(t, friends_alive=("Garen", "Vi")),             # 2 friends only
                 lambda t: champs(t, frames_window=20),                          # detection barely ran
                 lambda t: champs(t, detecting=False),
                 lambda t: champs(t, seen=("Garen", "Vi", "Ahri"))):             # 3/5 seen: fine
        sc = SelfCheck()
        assert drive(sc, 0, 200, make) == []
    # allies who died recently are not expected (no icon while dead)
    sc = SelfCheck()
    acts = drive(sc, 0, 200, lambda t: champs(t, dead=("Ahri", "Jinx", "Lulu") if t < 150 else ()))
    assert acts == []
    # while I am dead (grey minimap) and for a window after my respawn: not judged
    sc = SelfCheck()
    acts = drive(sc, 0, 200, lambda t: champs(t, me_alias="Garen", dead=("Garen",) if t < 150 else ()))
    assert acts == []


def test_champions_recovery_after_recalibration_is_reported_fixed():
    sc = SelfCheck()
    drive(sc, 0, 25, lambda t: champs(t))
    assert status(sc, "champions") == (MSG["champ_recal"], 1)
    drive(sc, 26, 60, lambda t: champs(t, seen=FRIENDS))
    assert status(sc, "champions") is None
    assert sc.game_report()["problems"][0]["outcome"] == "fixed"


# ======================================================================================
# 5 identities
# ======================================================================================
class Tr:
    def __init__(self, alias, pos, t, visible=True, relation="enemy", key=None, score=0.8):
        self.alias, self._pos, self.last_seen, self.visible = alias, pos, t, visible
        self.relation, self.key, self.score = relation, key or alias, score

    def raw_position(self):
        return self._pos

    def position(self):
        return self._pos


def test_identity_flip_flop_resets_that_track_but_a_recall_does_not():
    sc = SelfCheck()
    seq = [(0.0, (0.30, 0.30)), (0.25, (0.31, 0.30)), (0.5, (0.75, 0.70)), (0.75, (0.31, 0.31))]
    for t, p in seq:
        sc.on_tracks(t, [Tr("Darius", p, t)])
    acts = sc.evaluate(Snapshot(t=1.0))
    assert [(a.kind, a.arg) for a in acts] == [("forget_track", "Darius")]
    assert status(sc, "identity") == (MSG["ident"], 0)              # a note: Santé stays OK
    assert sc.summary()["state"] == "ok"
    # a recall: far jump, no return -> nothing
    sc2 = SelfCheck()
    for t, p in [(0.0, (0.5, 0.5)), (0.3, (0.5, 0.51)), (0.6, (0.05, 0.95)), (0.9, (0.05, 0.95))]:
        sc2.on_tracks(t, [Tr("Garen", p, t, relation="ally")])
    assert sc2.evaluate(Snapshot(t=1.0)) == []
    # no new observation (same last_seen) -> ignored
    sc3 = SelfCheck()
    for t in (0.0, 0.2, 0.4):
        sc3.on_tracks(t, [Tr("Vi", (0.2 if t != 0.2 else 0.9, 0.2), 0.0)])
    assert sc3.evaluate(Snapshot(t=1.0)) == []


def test_identity_excess_visible_enemies_forget_lowest_score_anonymous_tracks():
    sc = SelfCheck()
    anon = (("enemy?1", 0.9), ("enemy?2", 0.4), ("enemy?3", 0.6))
    snap = lambda t: Snapshot(t=t, enemies_visible=7, enemies_alive=5, anon_enemies=anon)  # noqa: E731
    acts = drive(sc, 0, 1, snap)
    assert acts == []                                             # < 2 s
    acts = drive(sc, 2, 2, snap)
    assert [(a.kind, a.arg) for _t, a in acts] == [("forget_tracks", ("enemy?2", "enemy?3"))]
    # three resets within 2 min: "instables" (dégradé), cleared after 30 s without anomaly
    drive(sc, 3, 10, snap)
    assert status(sc, "identity") == (MSG["ident_unstable"], 1)
    drive(sc, 11, 60, lambda t: Snapshot(t=t, enemies_visible=4, enemies_alive=5))
    assert status(sc, "identity") is None


# ======================================================================================
# 6 overlay
# ======================================================================================
def test_overlay_unfocused_is_a_note_explained_once_occlusion_and_stopped_thread():
    sc = SelfCheck()
    drive(sc, 0, 7, lambda t: Snapshot(t=t, focused=False))
    assert status(sc, "overlay") is None
    drive(sc, 8, 9, lambda t: Snapshot(t=t, focused=False))
    assert status(sc, "overlay") == (MSG["unfocused"], 0) and sc.summary()["state"] == "ok"
    assert MSG["unfocused"] in sc.summary()["notes"]
    drive(sc, 10, 12, lambda t: Snapshot(t=t))
    assert status(sc, "overlay") is None
    n_ev = sum(1 for e in sc.events if e["rule"] == "overlay")
    drive(sc, 20, 30, lambda t: Snapshot(t=t, focused=False))         # second alt-tab: status, no new event
    assert status(sc, "overlay") == (MSG["unfocused"], 0)
    assert sum(1 for e in sc.events if e["rule"] == "overlay") == n_ev
    assert sc.game_report()["problems"] == []                          # alt-tabs are not a problem
    drive(sc, 31, 40, lambda t: Snapshot(t=t, occluded=True))
    assert status(sc, "overlay") == (MSG["occluded"], 1)
    sc2 = SelfCheck()
    drive(sc2, 0, 3, lambda t: Snapshot(t=t, overlay_wanted=True, overlay_frames=int(t * 30)))
    drive(sc2, 4, 12, lambda t: Snapshot(t=t, overlay_wanted=True, overlay_frames=90))
    assert status(sc2, "overlay") == (MSG["overlay_dead"], 2)


# ======================================================================================
# 7 voice
# ======================================================================================
def test_voice_failing_then_slow_beep_only_and_restored_after_two_minutes():
    sc = SelfCheck()
    acts = drive(sc, 0, 5, lambda t: Snapshot(t=t, voice_expected=True, voice_backend="print"))
    assert [(t, a.kind, a.arg) for t, a in acts] == [(2, "voice_beep_only", True)]
    assert status(sc, "voice") == (MSG["voice_dead"], 1)
    acts = drive(sc, 6, 120, lambda t: Snapshot(t=t, voice_expected=True, voice_backend="neural"))
    assert acts == []                                               # 120 s of health needed
    acts = drive(sc, 121, 130, lambda t: Snapshot(t=t, voice_expected=True, voice_backend="neural"))
    assert [(a.kind, a.arg) for _t, a in acts] == [("voice_beep_only", False)]
    assert status(sc, "voice") is None
    sc2 = SelfCheck()
    acts = drive(sc2, 0, 5, lambda t: Snapshot(t=t, voice_expected=True, voice_backend="neural",
                                                voice_alert_p95_ms=1800.0, voice_samples=4))
    assert kinds(acts) == ["voice_beep_only"] and status(sc2, "voice") == (MSG["voice_slow"], 1)
    sc3 = SelfCheck()                                               # synthesis failures count too
    acts = drive(sc3, 0, 5, lambda t: Snapshot(t=t, voice_expected=True, voice_backend="sapi",
                                                voice_failures=0 if t < 1 else 3))
    assert kinds(acts) == ["voice_beep_only"]


def test_voice_override_kept_across_games_and_ignored_when_not_expected_or_muted():
    sc = SelfCheck()
    drive(sc, 0, 5, lambda t: Snapshot(t=t, voice_expected=True, voice_backend="print"))
    sc.new_game(10.0, 0.0)
    assert status(sc, "voice") == (MSG["voice_dead"], 1)            # still beep-only: still said
    for kw in ({"voice_expected": False}, {"voice_expected": True, "voice_muted": True}):
        sc2 = SelfCheck()
        assert drive(sc2, 0, 30, lambda t, kw=kw: Snapshot(t=t, voice_backend="print", **kw)) == []


# ======================================================================================
# 8 AI provider
# ======================================================================================
def test_ai_refused_key_reported_and_new_quota_failure_blocks_the_game_once():
    from treeaicoach.ai_advisor import ERROR_FR

    def snap(t, code, until):
        return Snapshot(t=t, ai_enabled=True, ai_seq=1, ai_status=ERROR_FR[code], ai_code=SC._ai_code(ERROR_FR[code]),
                        ai_backoff_until=until)

    # key refused: the advisor stopped by itself (back-off = inf); the app says it at once
    sc = SelfCheck()
    acts = drive(sc, 0, 10, lambda t: snap(t, "key", math.inf))
    assert [(a.kind, a.arg) for _t, a in acts] == [("ai_block", True)]
    assert status(sc, "ai")[0] == MSG["ai"].format(why="clé refusée")
    # quota: a stale error of the previous game does not block; a new failure this game does
    sc = SelfCheck()
    assert drive(sc, 0, 10, lambda t: snap(t, "quota", 500.0)) == []
    acts = drive(sc, 11, 20, lambda t: snap(t, "quota", 500.0 if t < 15 else 1215.0))
    assert [(t, a.kind) for t, a in acts] == [(15, "ai_block")]
    assert status(sc, "ai")[0] == MSG["ai"].format(why="quota atteint")
    assert kinds(drive(sc, 21, 60, lambda t: snap(t, "quota", math.inf))) == []      # once per game
    # two new failures of any kind (offline, server...) block too; one is left to the back-off
    sc = SelfCheck()
    acts = drive(sc, 0, 20, lambda t: snap(t, "offline", 100.0 + (300.0 if t >= 5 else 0) + (300 if t >= 12 else 0)))
    assert [t for t, a in acts if a.kind == "ai_block"] == [12]
    assert status(sc, "ai")[0] == MSG["ai"].format(why="service injoignable")
    sc = SelfCheck()
    assert drive(sc, 0, 30, lambda t: snap(t, "offline", 100.0 if t < 5 else 400.0)) == []
    # settings changed (back-off cleared): not a failure
    sc = SelfCheck()
    assert drive(sc, 0, 30, lambda t: snap(t, "offline", 400.0 if t < 5 else -math.inf)) == []


# ======================================================================================
# 9 Live Client
# ======================================================================================
def test_api_silent_with_window_open_problem_backoff_recreate_and_recovery():
    sc = SelfCheck()
    acts = []
    for i in range(0, 130, 2):
        acts += [(i, a) for a in sc.api_update(float(i), ok=False, window=True)]
        if i == 58:
            assert status(sc, "api") is None                      # loading screen: 60 s of grace
    assert status(sc, "api") == (MSG["api"], 1)
    assert [(t, a.arg) for t, a in acts if a.kind == "api_backoff"] == [(30, 3.0), (60, 5.0)]
    assert [t for t, a in acts if a.kind == "api_recreate"] == [30, 90]
    acts = sc.api_update(130.0, ok=True, window=True)
    assert [(a.kind, a.arg) for a in acts] == [("api_backoff", None)]
    assert status(sc, "api") is not None                          # 3 s of health to clear
    sc.api_update(134.0, ok=True, window=True)
    assert status(sc, "api") is None
    sc2 = SelfCheck()                                              # no game window: nothing to say
    for i in range(0, 200, 2):
        assert sc2.api_update(float(i), ok=False, window=False) == []
    assert sc2.problems() == []
    sc3 = SelfCheck()                                              # in game: 5 s are enough
    for i in range(0, 7):
        sc3.api_update(float(i), ok=False, window=True, in_game=True)
    assert status(sc3, "api") == (MSG["api"], 1)
    sc4 = SelfCheck()                     # the API answers HTTP 404: loading screen, 4 min of grace
    acts = []
    for i in range(0, 300, 2):
        acts += sc4.api_update(float(i), ok=False, window=True, answering=True)
        if i == 238:
            assert status(sc4, "api") is None
    assert status(sc4, "api") == (MSG["api"], 1) and acts == []      # no backoff / recreate: the API is up


# ======================================================================================
# notices, summary, disabled
# ======================================================================================
def test_at_most_one_notice_per_problem_per_game_with_a_gap_and_retries():
    sc = SelfCheck()
    # not shown (fight / gank): offered again at the next evaluation
    acts = drive(sc, 0, 20, lambda t: Snapshot(t=t, capture_black=True), mark=False)
    notices = [int(t) for t, a in acts if a.kind == "notice"]
    assert notices and notices == list(range(notices[0], 21))
    acts = drive(sc, 21, 400, lambda t: Snapshot(t=t, capture_black=True,
                                                 locate_method="fallback", loc_fails=4, loc_attempts=4))
    shown = [(t, a.rule) for t, a in acts if a.kind == "notice"]
    assert [r for _t, r in shown] == ["capture"]                     # minimap: capture bad -> not judged
    # two different problems: one notice each, at least NOTICE_GAP_S apart; never twice per game
    sc2 = SelfCheck()
    acts = drive(sc2, 0, 600, lambda t: Snapshot(t=t, capture_black=t < 100,
                                                  locate_method="fallback", loc_fails=4, loc_attempts=4))
    shown = [(t, a.rule) for t, a in acts if a.kind == "notice"]
    assert [r for _t, r in shown] == ["capture", "minimap"]
    assert shown[1][0] - shown[0][0] >= SC.NOTICE_GAP_S
    sc2.new_game(700.0, 0.0)                                         # a new game: allowed again
    acts = drive(sc2, 700, 760, lambda t: Snapshot(t=t, capture_black=True))
    assert kinds(acts).count("notice") == 1


def test_summary_text_game_report_and_export_shapes():
    sc = SelfCheck()
    assert summary_text(sc.summary()) == ("Santé TreeAI : OK", 0)
    assert summary_text(None) == ("", 0) and summary_text({"state": "off"}) == ("", 0)
    drive(sc, 0, 30, lambda t: perf(t, detect_fps=2.0, voice_expected=True, voice_backend="print"))
    text, level = summary_text(sc.summary())
    assert level == 1 and text.startswith("Santé TreeAI : dégradé")
    assert MSG["perf_1"] in text and MSG["voice_dead"] in text
    exp = sc.export()
    json.dumps(exp)
    assert exp["summary"]["load_level"] == 1 and exp["events"] and exp["game"]["problems"]
    assert any("analyse allégée" in line for line in sc.log_lines())


def test_disabled_selfcheck_does_nothing():
    sc = SelfCheck()
    sc.enabled = False
    assert drive(sc, 0, 60, lambda t: Snapshot(t=t, capture_black=True, voice_expected=True,
                                               voice_backend="print")) == []
    assert sc.api_update(100.0, ok=False, window=True) == []
    assert summary_text(sc.summary()) == ("", 0)
    sc2 = SelfCheck(rules=("voice",))
    assert drive(sc2, 0, 60, lambda t: Snapshot(t=t, capture_black=True)) == []


# ======================================================================================
# record / report / diagnostic bundle
# ======================================================================================
def test_report_section_and_record_attach(tmp_path):
    from treeaicoach import report

    sc = SelfCheck()
    drive(sc, 0, 40, lambda t: Snapshot(t=t, game_time=600 + t, capture_black=t < 6, capture_alt="mss"))
    drive(sc, 41, 80, lambda t: perf(t, game_time=600 + t, detect_fps=2.0))
    rep = sc.game_report()
    rec_path = tmp_path / "game.json"
    rec_path.write_text(json.dumps({"meta": {"app_version": "x"}}), encoding="utf-8")
    assert SC.attach_to_record(rec_path, rep)
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    html = report._selfcheck_section(rec)
    assert "Santé de TreeAI pendant la partie" in html
    assert "Capture d&#x27;écran" in html or "Capture d'écran" in html
    assert "corrigé automatiquement" in html and "Analyse allégée" in html and "minimal" not in html
    assert "allégé" in html                                           # load level during the game
    ok = report._selfcheck_section({"selfcheck": SelfCheck().game_report()})
    assert "Rien à signaler" in ok
    assert report._selfcheck_section({}) == ""
    page = report.render_report_html(rec)
    assert "Santé de TreeAI pendant la partie" in page


def test_diag_bundle_contains_the_selfcheck_log(tmp_path):
    from treeaicoach import diag

    sc = SelfCheck()
    drive(sc, 0, 20, lambda t: Snapshot(t=t, capture_black=True))

    class Eng:
        cfg = Config()
        _settings_watcher = None

        def health(self):
            return {"selfcheck": sc.summary()}

        def request_diag_snapshot(self, full_screen=False):
            pass

        def diag_snapshot(self):
            return {"t": time.monotonic(), "frame": None, "preview": None, "health": self.health()}

        def selfcheck_report(self):
            return sc.export()

        def selfcheck_log(self):
            return sc.log_lines()

    rec = diag.DiagRecorder(Eng(), duration_s=1.0, interval_s=0.5, out_root=tmp_path, opener=lambda p: None)
    folder = rec.start()
    rec.join(10.0)
    with zipfile.ZipFile(rec.status()["zip"]) as zf:
        data = json.loads(zf.read(f"{folder.name}/selfcheck.json"))
        log_txt = zf.read(f"{folder.name}/selfcheck_log.txt").decode("utf-8")
    assert data["summary"]["level"] == 2 and data["events"]
    assert "Sans bordure" in log_txt


# ======================================================================================
# engine wiring
# ======================================================================================
def live(cfg=None, client_fn=None, window=None, voice=None, detector=None):
    cap = TE.FakeCapture()
    loc = TE.FakeLocator(cap)
    clock = TE.Clock()
    win = window if window is not None else [Rect(0, 0, cap.W, cap.H)]
    client = TE.FakeLiveClient(client_fn(clock) if client_fn else (lambda: TE.game_info(700 + clock.t)))
    eng = CoachEngine(cfg or Config(), voice or TE.FakeVoice(), detector=detector or TE.ClassicDetector(),
                      live_client=client, clock=clock, locator=loc, window_finder=lambda: win[0],
                      screen_capture=cap, recorder_factory=lambda: None, enable_hotkeys=False,
                      manage_overlay=False)
    return eng, clock, cap, loc, client


def run(eng, clock, seconds, dt=0.125):
    end = clock.t + seconds
    while clock.t < end - 1e-9:
        clock.t += dt
        eng.step(clock.t)


def test_engine_modes_and_health_exposes_selfcheck():
    from treeaicoach.demo import DemoSource

    eng, clock, *_ = live()
    assert eng._selfcheck.enabled and "perf" not in eng._selfcheck.rules       # fake clock: no cost rule
    run(eng, clock, 2.0)
    h = eng.health()
    assert h["selfcheck"]["title"] == "Santé TreeAI : OK"
    assert eng.get_status().health["selfcheck"]["state"] == "ok"
    demo, _v, _c = TE.make_engine(DemoSource(size=200))
    assert demo._selfcheck.enabled is False                                    # simulations: off
    off, *_ = live(cfg=Config(selfcheck_enabled=False))
    assert off._selfcheck.enabled is False
    real = CoachEngine(Config(), TE.FakeVoice(), enable_hotkeys=False, manage_overlay=False)
    assert real._selfcheck.rules == set(SC.RULES)                              # real capture: every rule


def test_engine_minimap_lost_keeps_last_rect_with_backoff_then_calibrate_instruction():
    eng, clock, cap, loc, _c = live()
    run(eng, clock, 2.0)
    good = eng.get_status().minimap_rect
    assert loc.locates == 1 and eng._loc_fails == 0 and eng._locate_score == pytest.approx(0.93)
    loc.score, loc.found = 0.1, False                       # covered / gone: verify low, location fails
    t_bad = clock.t
    run(eng, clock, 4.5)
    assert loc.locates == 2 and eng._loc_fails == 1
    st = eng.get_status()
    assert st.minimap_rect == good and st.locate_method == "auto"          # not an unverified default square
    run(eng, clock, 30.0)
    assert loc.locates == 4                                  # backoff: +6 s, +12 s (not every 3 s)
    summ = eng.selfcheck_summary()
    assert MSG["minimap_lost"] in summ["reasons"] and summ["level"] == 2
    assert eng._text_msg is not None and eng._text_msg[1] == NOTICES["minimap"]   # the HUD line, once
    # the minimap is back: verified again -> failures reset -> resolved after 5 s
    loc.score, loc.found = 0.9, True
    run(eng, clock, 7.0)
    assert eng._loc_fails == 0 and MSG["minimap_lost"] not in eng.selfcheck_summary()["reasons"]
    del t_bad


def test_engine_black_capture_reaches_the_borderless_instruction():
    eng, clock, cap, loc, _c = live()
    run(eng, clock, 1.0)
    cap.screen[:] = 0
    run(eng, clock, 20.0)
    summ = eng.selfcheck_summary()
    assert summ["level"] == 2 and MSG["capture_black"] in summ["reasons"]
    assert any(e["kind"] == "action" for e in eng.selfcheck_report()["events"] if e["rule"] == "capture")


def test_engine_load_levels_push_and_restore_the_cost_knobs(restore_knobs):
    from treeaicoach import overlay, roster_matcher

    base_ring, base_stack = roster_matcher.RING_PROP_EVERY, roster_matcher.STACKV_EVERY
    eng, clock, *_ = live(cfg=replace(Config(), perf_mode="normal"))
    eng._selfcheck.rules.add("perf")
    eng._selfcheck.tick_p95 = lambda since: 12.0            # (deterministic: the rate drives this test)
    run(eng, clock, 35.0, dt=0.5)                           # 2 img/s analysed for a 6-12 img/s target
    assert eng._budget.load_level == 1 and eng._budget.profile.load == "allege"
    assert roster_matcher.RING_PROP_EVERY == max(4, base_ring) and roster_matcher.STACKV_EVERY == max(4, base_stack)
    assert overlay._budget_fps == 20.0 and eng._heavy.hz == 1.0
    assert roster_matcher.LOST_EVERY == 8
    assert MSG["perf_1"] in eng.selfcheck_summary()["reasons"]
    assert eng.health()["budget"]["load"] == "allege"
    eng._selfcheck.detect_rate = lambda now, window=5.0: 8.0     # healthy now (the wiring is checked above)
    run(eng, clock, 75.0, dt=0.5)                           # healthy for 60 s -> back to normal
    assert eng._budget.load_level == 0
    assert (roster_matcher.RING_PROP_EVERY, roster_matcher.STACKV_EVERY) == (base_ring, base_stack)
    assert overlay._budget_fps == 30.0 and roster_matcher.LOST_EVERY == 4
    assert not any("Analyse" in r for r in eng.selfcheck_summary()["reasons"])


class HealthVoice(TE.FakeVoice):
    def __init__(self) -> None:
        super().__init__()
        self.backend = "print"
        self.danger_voice = "bip_voix"
        self.modes: list[str] = []
        self.beeps: list[str] = []

    def set_danger_voice(self, mode):
        self.danger_voice = mode
        self.modes.append(mode)

    def alert_beep(self, tone="gank"):
        self.beeps.append(tone)
        return True

    def health(self):
        return {"backend": self.backend, "expected": True, "alert_p95_ms": None, "alert_samples": 0, "failures": 0}


def test_engine_voice_beep_only_override_survives_beeps_and_settings():
    from treeaicoach.alerts import AlertKind, Level, make_alert

    voice = HealthVoice()
    eng, clock, *_ = live(voice=voice)
    run(eng, clock, 4.0)
    assert voice.danger_voice == "bip" and MSG["voice_dead"] in eng.selfcheck_summary()["reasons"]
    eng._danger_beep(make_alert(AlertKind.JUNGLER_APPROACH, Level.DANGER, clock.t, text="x", key="k"))
    assert voice.danger_voice == "bip" and voice.beeps
    eng.apply_config(Config())
    assert voice.danger_voice == "bip"
    eng._set_voice_override(None)
    assert voice.danger_voice == "bip_voix"


def test_engine_ai_block_for_the_game_then_restored_next_game():
    from treeaicoach.ai_advisor import ERROR_FR

    from treeaicoach.hype import HypeCaster

    class FakeAI:
        enabled = True

        def __init__(self) -> None:
            self._lock = threading.Lock()
            self._blocked_until = 1234.0                       # quota back-off of the last game
            self.text = ERROR_FR["quota"]

        def status(self):
            return 1, self.text

        def apply_config(self, cfg):
            pass

        def reset(self):
            pass

        def update(self, *a, **k):
            return False

        def poll(self):
            return None

        def note_play(self, p):
            pass

    game = {"gt": 700.0}
    eng, clock, *_ = live(client_fn=lambda clock: (lambda: TE.game_info(game["gt"] + clock.t)))
    eng._ai = FakeAI()
    eng._hype = HypeCaster(Config())
    run(eng, clock, 3.0)
    assert eng._ai._blocked_until == 1234.0                   # a stale error of the last game: not blocked
    eng._ai._blocked_until = 1834.0                           # quota again, in this game
    run(eng, clock, 1.5)
    assert eng._ai._blocked_until == math.inf
    assert MSG["ai"].format(why="quota atteint") in eng.selfcheck_summary()["reasons"]
    game["gt"] = -clock.t + 5.0                                # game time goes back: a new game
    run(eng, clock, 1.5)
    assert eng._ai._blocked_until == 1834.0 and eng._selfcheck.games >= 2


def test_engine_api_outage_keeps_the_game_while_the_window_exists():
    state = {"api": True}
    eng, clock, cap, loc, client = live(
        client_fn=lambda clock: (lambda: TE.game_info(700 + clock.t) if state["api"] else None))
    win = [Rect(0, 0, cap.W, cap.H)]
    eng._window_finder = lambda: win[0]
    run(eng, clock, 2.0)
    assert eng.in_game
    games = eng._selfcheck.games
    state["api"] = False
    run(eng, clock, 20.0)
    assert eng.in_game                                       # 20 s > GAME_GONE_S: kept (window open)
    assert MSG["api"] in eng.selfcheck_summary()["reasons"]
    state["api"] = True
    run(eng, clock, 5.0)
    assert eng.in_game and eng._selfcheck.games == games     # same game resumed, no split
    assert MSG["api"] not in eng.selfcheck_summary()["reasons"]
    state["api"] = False
    win[0] = None                                            # game closed: the usual end after 8 s
    run(eng, clock, 10.0)
    assert not eng.in_game


def test_engine_api_silent_outside_a_game_backoff_and_status():
    eng, clock, cap, loc, client = live(client_fn=lambda clock: (lambda: None))
    for _ in range(140):
        clock.t += 0.5
        eng.step(clock.t)
    assert MSG["api"] in eng.selfcheck_summary()["reasons"]
    assert eng._api_period == 5.0
    assert cap.grabs == []                                   # still no capture outside a game
    calls = client.calls
    for _ in range(20):
        clock.t += 0.5
        eng.step(clock.t)
    assert client.calls - calls == 2                         # polled every 5 s now


def test_engine_forget_track_and_excess_actions_use_the_tracker():
    from treeaicoach.tracker import Tracker

    eng, clock, *_ = live()
    run(eng, clock, 1.0)
    tr = Tracker()
    det = NS(u=0.5, v=0.5, r=0.03, score=0.9, cls="enemy")
    tr.update(1.0, [NS(det=det, alias="Darius", relation="enemy", team="CHAOS", id_score=0.9)])
    tr.update(1.2, [NS(det=det, alias="Darius", relation="enemy", team="CHAOS", id_score=0.9)])
    eng._tracker = tr
    assert tr.get("Darius") is not None
    SC.apply_actions(eng, [Action("forget_track", "identity", "Darius")], clock.t)
    assert tr.get("Darius") is None and tr.forget("Darius") is False
    SC.apply_actions(eng, [Action("forget_tracks", "identity", ("nope", "nada"))], clock.t)


def test_engine_notice_through_presenter_waits_during_a_gank():
    from treeaicoach.alerts import AlertKind, Level, make_alert

    eng, clock, *_ = live()
    run(eng, clock, 2.0)
    a = make_alert(AlertKind.JUNGLER_APPROACH, Level.DANGER, clock.t, text="x", key="k")
    eng._threat_hist.append((clock.t, 2, a))
    assert eng._selfcheck_notify("minimap", NOTICES["minimap"], clock.t) is False
    eng._threat_hist.clear()
    assert eng._selfcheck_notify("minimap", NOTICES["minimap"], clock.t) is True
    assert eng._text_msg[1] == NOTICES["minimap"]
    assert eng._presenter.log[-1][2] != "drop"
    eng._cfg.toasts_enabled = False                          # (read with getattr by the engine)
    assert eng._selfcheck_notify("capture", NOTICES["capture"], clock.t) is True    # nothing to show: done


def test_engine_recalibrate_and_reload_icons_with_the_real_roster_matcher():
    from treeaicoach.detector import create_detector

    det = create_detector("classic")
    eng, clock, *_ = live(detector=det)
    run(eng, clock, 2.0)
    m = det.matcher
    assert m.has_roster
    m._state.calib.append((0.12, 0.9))
    SC.apply_actions(eng, [Action("recalibrate", "champions")], clock.t)
    assert all(c[0] != 0.12 for c in m._state.calib)         # the new sweep alone decides
    SC.apply_actions(eng, [Action("reload_icons", "champions")], clock.t)
    assert not m.has_roster and eng._roster_sig is None
    run(eng, clock, 1.5)                                     # next poll: the roster is rebuilt
    assert m.has_roster


def test_engine_auto_diagnostic_is_quiet_and_does_not_open_a_folder(monkeypatch):
    eng, clock, *_ = live()
    calls = []
    monkeypatch.setattr(eng, "start_diagnostic", lambda **kw: calls.append(kw) or Path("x"))
    SC.apply_actions(eng, [Action("auto_diag", "champions")], 10.0)
    assert calls == [{"announce": False, "open_folder": False}]
    eng.apply_config(Config(selfcheck_auto_diag=False))
    SC.apply_actions(eng, [Action("auto_diag", "champions")], 11.0)
    assert len(calls) == 1                                   # opt-out respected
    # the real one: nothing spoken
    voice = TE.FakeVoice()
    eng2, clock2, *_ = live(voice=voice)
    path = eng2.start_diagnostic(duration_s=10.0, announce=False, open_folder=False)
    assert path is not None and voice.said == []
    eng2._diag.stop()
    eng2._diag.join(10.0)


def test_engine_record_gets_the_game_health(tmp_path):
    class Rec:
        def finish(self):
            p = tmp_path / "rec.json"
            p.write_text(json.dumps({"meta": {}}), encoding="utf-8")
            return p

    eng, clock, *_ = live()
    eng.apply_config(Config(post_game_report=False, lcu_enabled=False))
    sc = SelfCheck()
    drive(sc, 0, 20, lambda t: Snapshot(t=t, capture_black=True))
    eng._finish_job(Rec(), None, sc.game_report())
    data = json.loads((tmp_path / "rec.json").read_text(encoding="utf-8"))
    assert data["selfcheck"]["problems"][0]["rule"] == "capture"


def test_selfcheck_overhead_per_tick_is_small():
    eng, clock, *_ = live()
    run(eng, clock, 30.0)
    cost = eng._selfcheck.cost.summary()
    assert cost["n"] >= 200
    assert cost["mean"] < 1.0, cost                           # (measured ~0.03-0.1 ms; generous for CI)
