"""Detection fixes from the real games of 2026-10 (support Swain glued on his ADC, game starts):

* dead me: no icon of mine on the map -> nothing becomes "me" (the icon next to where I died
  used to carry my position around until the respawn, then "Swain vu à deux endroits");
* the camera-centre "self" fallback never picks an ally far from where I was, nor any icon while
  my track is held under another icon;
* my team's champions (never in the fog) stay "together" under the icon drawn over them for
  minutes (support on ADC), enemies only STACK_HOLD_S;
* an ally never seen (hidden under his support from the first frame) does not take the
  identity of an ally-coloured blob across the map by elimination;
* the recall exception of the matcher needs a champion that really stood still (not a velocity
  zeroed by an inferred stacked position);
* icon scale: the ratio stored by the previous games no longer restricts the first sweep, and
  only confident multi-icon calibrations are stored (real reports: 0,104 / 0,108 for minutes);
* game start: no "Minimap introuvable" before the grace time, fast retries, no analysis of an
  unverified default square;
* tools/record_vs_truth.py classifies our record samples against the LCU truth.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from treeaicoach import tracker as T  # noqa: E402
from treeaicoach.tracker import Tracker  # noqa: E402


def _ident(u, v, alias=None, relation="enemy", score=0.9):
    from treeaicoach.detector import Detection
    from treeaicoach.identifier import Identified

    det = Detection(u=u, v=v, r=0.045, score=score, cls="enemy" if relation == "enemy" else "ally",
                    cls_probs=(0.9, 0.1, 0.0) if relation == "enemy" else (0.1, 0.9, 0.0), alias=alias)
    return Identified(det=det, alias=alias, relation=relation, team=None, id_score=score if alias else 0.0)


ROSTER = {"Swain": "self", "Tristana": "ally", "Galio": "ally", "Zed": "ally", "Shyvana": "ally",
          "Veigar": "enemy", "Nami": "enemy", "Udyr": "enemy", "Locke": "enemy", "Singed": "enemy"}


# ======================================================================================
# tracker
# ======================================================================================
def test_dead_me_has_no_position_and_no_self_claim():
    trk = Tracker()
    trk.set_roster(ROSTER)
    for k in range(6):
        trk.update(k * 0.2, [_ident(0.8, 0.8, "Swain", "self"), _ident(0.85, 0.8, "Tristana", "ally")])
    assert trk.me() is not None and trk.me().visible
    trk.set_dead({"Swain"})
    # while I am dead an unidentified icon is claimed "self" (camera fallback / sticky self):
    # it must not become my track (no position walking around the map until the respawn)
    for k in range(6, 30):
        trk.update(k * 0.2, [_ident(0.5 + 0.01 * k, 0.5, None, "self"),
                             _ident(0.85, 0.8, "Tristana", "ally")])
        me = trk.me()
        assert me is None or not me.visible
    trk.set_dead(set())
    trk.update(6.2, [_ident(0.04, 0.96, "Swain", "self")])          # respawn at the fountain
    assert trk.me() is not None and trk.me().visible
    assert math.hypot(trk.me().position()[0] - 0.04, trk.me().position()[1] - 0.96) < 0.01


def test_support_glued_on_his_adc_stays_together_for_minutes():
    trk = Tracker()
    trk.set_roster(ROSTER)
    t = 0.0
    for _ in range(10):                                              # both seen, side by side
        trk.update(t, [_ident(0.80, 0.85, "Tristana", "ally"), _ident(0.83, 0.85, "Swain", "self"),
                       _ident(0.86, 0.80, "Veigar", "enemy"), _ident(0.88, 0.80, "Nami", "enemy")])
        t += 0.2
    # then both supports are drawn under their ADC for a long time (the ADCs keep trading)
    while t < 40.0:
        x = 0.80 + 0.02 * math.sin(t)
        trk.update(t, [_ident(x, 0.85, "Tristana", "ally"), _ident(0.86, 0.80, "Veigar", "enemy")])
        t += 0.2
    me, nami = trk.me(), trk.get("Nami")
    assert me.visible and me.stacked_with == "Tristana"              # me: together, at the stack
    assert math.hypot(me.position()[0] - (0.80 + 0.02 * math.sin(t - 0.2)), me.position()[1] - 0.85) < 0.01
    assert not nami.visible                                          # an enemy: STACK_HOLD_S only
    assert T.STACK_HOLD_FRIEND_S > 30.0 >= T.STACK_HOLD_S


def test_unseen_ally_does_not_take_a_far_blob_by_elimination():
    trk = Tracker()
    trk.set_roster(ROSTER)
    allies = [("Galio", 0.1, 0.3), ("Zed", 0.5, 0.5), ("Shyvana", 0.3, 0.6)]
    for k in range(12):
        t = k * 0.2
        ids = [_ident(0.85, 0.85, "Swain", "self")] + [_ident(u, v, a, "ally") for a, u, v in allies]
        ids.append(_ident(0.84, 0.09, None, "ally", score=0.6))      # an ally-coloured glyph across the map
        trk.update(t, ids)
    # Tristana (hidden under me from the first frame, never seen) is not that glyph
    assert trk.get("Tristana") is None
    # ... while an ally seen a moment ago next to that blob is identified by elimination
    trk2 = Tracker()
    trk2.set_roster(ROSTER)
    for k in range(6):
        trk2.update(k * 0.2, [_ident(0.85, 0.85, "Swain", "self"), _ident(0.5, 0.5, "Tristana", "ally")]
                    + [_ident(u, v, a, "ally") for a, u, v in allies])
    for k in range(6, 14):
        trk2.update(k * 0.2, [_ident(0.85, 0.85, "Swain", "self"), _ident(0.51, 0.5, None, "ally", 0.6)]
                    + [_ident(u, v, a, "ally") for a, u, v in allies])
    assert trk2.get("Tristana") is not None and trk2.get("Tristana").visible


# ======================================================================================
# engine: "self" claims while dead / far from my track
# ======================================================================================
class _Eng:
    """Just what engine_vision.VisionMixin's stabilisation / self fallback read."""

    def __init__(self, tracker, dead=False):
        from treeaicoach.engine_vision import VisionMixin

        self.__class__ = type("E", (_Eng, VisionMixin), {})
        me = SimpleNamespace(champion_alias="Swain", is_dead=dead)
        self._game = SimpleNamespace(me=me)
        self._tracker = tracker
        self._detector = SimpleNamespace(matcher=SimpleNamespace(has_roster=True, last_dead=["Swain"] if dead else []))


def test_camera_self_fallback_respects_death_and_my_last_position(monkeypatch):
    from treeaicoach import engine_vision as EV

    monkeypatch.setattr(EV, "find_camera_center", lambda frame: (0.30, 0.30))
    trk = Tracker()
    for k in range(5):
        trk.update(k * 0.2, [_ident(0.80, 0.80, "Swain", "self")])
    frame = np.zeros((10, 10, 3), np.uint8)
    # an anonymous ally right at the camera centre, far from where I am: not me
    ids = [_ident(0.31, 0.30, None, "ally")]
    _Eng(trk)._camera_self_fallback(frame, ids)
    assert ids[0].relation == "ally"
    # dead: nothing is me, even an ally next to my last position
    ids = [_ident(0.80, 0.80, None, "ally")]
    monkeypatch.setattr(EV, "find_camera_center", lambda frame: (0.80, 0.80))
    _Eng(trk, dead=True)._camera_self_fallback(frame, ids)
    assert ids[0].relation == "ally"
    # alive, the camera on me, an unidentified ally icon where I was: me (custom skin...)
    _Eng(trk)._camera_self_fallback(frame, ids)
    assert ids[0].relation == "self"


def test_stabilize_never_relabels_an_icon_as_me_while_dead():
    trk = Tracker()
    trk.set_roster(ROSTER)
    for k in range(5):
        trk.update(k * 0.1, [_ident(0.50, 0.50, "Swain", "self")])
    eng = _Eng(trk, dead=True)
    eng._dead_aliases = lambda: {"Swain"}
    out = eng._stabilize(0.55, [_ident(0.505, 0.50, None, "enemy"), _ident(0.6, 0.6, None, "self")])
    assert all(getattr(x, "relation", None) != "self" for x in out)


# ======================================================================================
# matcher: recall exception, icon scale
# ======================================================================================
def test_recall_exception_needs_a_champion_that_stood_still():
    from treeaicoach import roster_matcher as RM

    m = RM.RosterMatcher()
    e = RM.RosterEntry("Shyvana", "ally", np.zeros((8, 8, 4), np.uint8), team="ORDER")
    fu, fv = RM._FOUNTAINS["ORDER"]
    tr = RM._Track(0.76, 0.90, 10.0)                  # velocity zeroed by a stacked position
    tr.still = (0.76, 0.90, 9.5)                      # ... but it was walking until 0.5 s ago
    assert m._jump_penalty(e, tr, fu + 0.03, fv - 0.03, 10.2) == RM.JUMP_PENALTY
    tr.still = (0.76, 0.90, 10.2 - RM.RECALL_STILL_S - 0.1)        # channelled a recall
    assert m._jump_penalty(e, tr, fu + 0.03, fv - 0.03, 10.2) == 0.0


REAL = ROOT / "tests" / "fixtures" / "real"


@pytest.mark.parametrize("shot", ["shot3", "shot4", "shot9"])
def test_wrong_stored_scale_does_not_bias_the_first_calibration(shot):
    """Real crops (true ratio ~0.093): with 0.108 stored by a previous game the first scale
    stays within 3 % of the scale found without any stored ratio (a narrow sweep around the
    stored value gave up to +7 %); the confident result replaces the stored one."""
    import cv2

    import real_minimap_bench as RB
    from treeaicoach.champions import get_default_db
    from treeaicoach.roster_matcher import RosterMatcher

    db = get_default_db()
    truth = RB.load_truth()
    spec = truth["images"][shot]
    img = cv2.imread(str(REAL / spec["file"]))
    key = f"{img.shape[1]}x{img.shape[0]}"
    ents = RB.roster_entries(truth["rosters"][spec["game"]], db)
    ref = RosterMatcher(db=db, scale_store={})
    ref.set_entries(ents)
    ref.detect(img, t=0.0)
    store = {key: 0.108}
    m = RosterMatcher(db=db, scale_store=store)
    m.set_entries(ents)
    for f in range(4):
        m.detect(img, t=f / 8.0)
    assert abs(math.log(m.scale / ref.scale)) < 0.03, (m.scale, ref.scale)
    assert m._state.confident and abs(store[key] - m.scale) < 1e-4


def test_unconfident_calibration_is_not_stored():
    """Two icons stacked in a corner (the base at 0:00): a provisional scale, the stored ratio
    of the previous games is left alone."""
    import cv2

    from treeaicoach import render as R
    from treeaicoach.champions import get_default_db
    from treeaicoach.roster_matcher import RosterEntry, RosterMatcher

    db = get_default_db()
    names = sorted(e.alias for e in db.all() if db.load_icon(e.alias) is not None)[:10]
    img = np.full((280, 280, 3), 40, np.uint8)
    for k, (u, v) in enumerate(((0.05, 0.95), (0.07, 0.93))):
        R.draw_champion_icon(img, u * 280, v * 280, 0.045 * 280, db.load_icon(names[k]), (200, 150, 80),
                             ring_frac=0.12, inner_line_bgr=R.INNER_LINE_BGR, outline_px=0.6)
    store = {"280x280": 0.095}
    m = RosterMatcher(db=db, scale_store=store)
    m.set_entries([RosterEntry(a, "self" if k == 0 else ("ally" if k < 5 else "enemy"), db.load_icon(a))
                   for k, a in enumerate(names)])
    for f in range(4):
        m.detect(cv2.GaussianBlur(img, (0, 0), 0.4), t=f / 6.0)
    assert not m._state.confident
    assert store == {"280x280": 0.095}


# ======================================================================================
# game start: minimap location
# ======================================================================================
def test_minimap_not_judged_during_the_game_start_grace():
    from treeaicoach.selfcheck import MINIMAP_START_GRACE_S, SelfCheck, Snapshot

    sc = SelfCheck()
    t = 0.0
    while t < MINIMAP_START_GRACE_S - 5:
        sc.evaluate(Snapshot(t=t, game_time=t, locate_method="fallback", loc_attempts=int(t // 2) + 1,
                             loc_fails=int(t // 2) + 1))
        t += 1.0
    assert sc.problems() == []
    while t < MINIMAP_START_GRACE_S + 10:
        sc.evaluate(Snapshot(t=t, game_time=t, locate_method="fallback", loc_attempts=int(t // 2) + 1,
                             loc_fails=int(t // 2) + 1))
        t += 1.0
    assert [p.rule for p in sc.problems()] == ["minimap"]


def test_engine_retries_fast_and_skips_the_unverified_square_at_game_start():
    import test_engine as TE

    cap = TE.FakeCapture()
    loc = TE.FakeLocator(cap, found=False)
    loc.score = 0.2                                   # loading screen: the default square is no minimap
    win = TE.Rect(0, 0, cap.W, cap.H)
    clock = TE.Clock()
    client = TE.FakeLiveClient(lambda: TE.game_info(1.0 + clock.t))
    eng = TE.CoachEngine(TE.Config(), TE.FakeVoice(), detector=TE.ClassicDetector(), live_client=client,
                         clock=clock, locator=loc, window_finder=lambda: win, screen_capture=cap,
                         recorder_factory=lambda: None, enable_hotkeys=False, manage_overlay=False)
    analysed = []
    orig = eng._vision
    eng._vision = lambda frame: analysed.append(1) or orig(frame)
    for i in range(0, 8 * 9):
        clock.t = i * 0.125
        eng.step(clock.t)
    assert loc.locates >= 4                           # every ~2 s, not every 10 s
    assert analysed == []                             # no phantom detections on the loading screen
    loc.found, loc.score = True, 0.9
    for i in range(8 * 9, 8 * 13):
        clock.t = i * 0.125
        eng.step(clock.t)
    assert eng.get_status().locate_method == "auto" and analysed


# ======================================================================================
# record vs truth analysis
# ======================================================================================
def test_record_vs_truth_classifies_samples():
    import record_vs_truth as RVT

    truth = {"me": 1, "my_team": "ORDER", "duration": 300.0,
             "participants": [{"id": 1, "team": "ORDER", "alias": "Swain", "position": "UTILITY"},
                              {"id": 2, "team": "ORDER", "alias": "Tristana", "position": "BOTTOM"},
                              {"id": 3, "team": "CHAOS", "alias": "Veigar", "position": "BOTTOM"},
                              {"id": 4, "team": "CHAOS", "alias": "Nami", "position": "UTILITY"}],
             "frames": [[120.0, {"1": [0.80, 0.85, 0, 0, 0, 3], "2": [0.82, 0.85, 0, 0, 0, 3],
                                 "3": [0.86, 0.80, 0, 0, 0, 3], "4": [0.30, 0.30, 0, 0, 0, 3]}],
                        [180.0, {"1": [0.80, 0.85, 0, 0, 0, 4], "2": [0.82, 0.85, 0, 0, 0, 4],
                                 "3": [0.86, 0.80, 0, 0, 0, 4], "4": [0.30, 0.30, 0, 0, 0, 4]}]],
             "kills": [[150.0, 3, 1, [], 0.80, 0.85]]}
    record = {"meta": {"app_version": "test"},
              "my_positions": [[120.0, 0.80, 0.85], [158.0, 0.5, 0.5], [180.0, 0.40, 0.40]],
              "sightings": {"Veigar": [[120.5, 0.86, 0.80], [180.5, 0.30, 0.31]],     # on Nami's spot
                            "enemy?3": [[121.0, 0.31, 0.30]]},
              "allies": {"Tristana": [[120.0, 0.82, 0.85], [181.0, 0.82, 0.85]]},
              "snapshots": [{"game_time": 140.0, "is_dead": False}, {"game_time": 155.0, "is_dead": True},
                            {"game_time": 162.0, "is_dead": True}, {"game_time": 170.0, "is_dead": False}]}
    r = RVT.analyse(record, truth)
    c = r["counts"]
    assert c["wrong_id"] == 1                        # Veigar drawn where Nami is
    assert c["dead"] == 1                            # my position while I am dead
    assert c["me_far"] + c["me_far_stacked"] == 1    # 0.40 vs 0.80 at 3:00 (stacked on my ADC)
    assert c["anon_lost"] == 1                       # an anonymous enemy that only Nami explains
    assert "wrong_id 1" in RVT.report({"g": r})
