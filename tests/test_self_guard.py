"""My position must be physically plausible (tracker.Tracker._self_guard, SELF_GUARD_*).

Real 2.5.0 / 2.5.1 Swain support games: 77-95 jumps of "me" across the map per game (camera on a
top fight / my base / mid / the top laner while my icon was under my ADC), one stay of ~47 s
on the top lane, "Swain vu à deux endroits" 7 times."""

from __future__ import annotations

import math

import pytest

from treeaicoach import tracker as T
from treeaicoach.detector import Detection
from treeaicoach.identifier import Identified
from treeaicoach.tracker import Tracker

FPS = 6.0
DT = 1.0 / FPS


def ident(u: float, v: float, relation: str = "self", alias: str | None = None,
          team: str | None = "ORDER", score: float = 0.8) -> Identified:
    cls = "enemy" if relation == "enemy" else "ally"
    det = Detection(u=u, v=v, r=0.045, score=score, cls=cls,
                    cls_probs=(0.9, 0.1, 0.0) if cls == "enemy" else (0.05, 0.95, 0.0))
    return Identified(det=det, alias=alias, relation=relation, team=team,
                      id_score=score if alias else 0.0)


def run(tr: Tracker, t0: float, t1: float, frame, out: list | None = None) -> float:
    t = t0
    while t < t1 - 1e-9:
        tr.update(t, frame(t))
        if out is not None:
            me = tr.me()
            out.append((t, me.position() if me is not None and me.visible else None))
        t += DT
    return t


def _jumps(trace: list, fountain=(0.04, 0.96)) -> int:
    """Impossible jumps of the drawn position (det_gym mjmp rule)."""
    n, last = 0, None
    for t, p in trace:
        if p is None:
            continue
        if last is not None:
            d = math.hypot(p[0] - last[1][0], p[1] - last[1][1])
            if d > 0.06 + 0.035 * (t - last[0]) and math.hypot(p[0] - fountain[0], p[1] - fountain[1]) > 0.06:
                n += 1
        last = (t, p)
    return n


def _swain_lane(t: float) -> tuple[float, float]:
    return (0.80 + 0.002 * t, 0.85)


def test_camera_claims_far_away_never_move_me() -> None:
    """Camera fallback ("self", no identity) and a wrong bootstrap of my portrait on the top
    laner, every frame for 10 s: I stay in my lane, no impossible jump."""
    tr = Tracker()
    tr.set_roster({"Swain": "self", "Fiora": "ally"})
    trace: list = []
    run(tr, 0.0, 5.0, lambda t: [ident(*_swain_lane(t), alias="Swain")], trace)
    run(tr, 5.0, 10.0, lambda t: [ident(0.10, 0.28, score=0.55)], trace)               # camera
    run(tr, 10.0, 15.0, lambda t: [ident(0.12, 0.26, alias="Swain", score=0.4)], trace)  # weak
    run(tr, 15.0, 17.0, lambda t: [ident(*_swain_lane(t), alias="Swain")], trace)
    assert _jumps(trace) == 0
    for _t, p in trace:
        assert p is None or p[0] > 0.7, p
    assert tr.self_rejected > 0 and tr.self_confirmed == 0
    me = tr.me()
    assert me is not None and me.alias == "Swain" and me.position()[0] > 0.7


def test_alternating_far_claim_is_not_confirmed() -> None:
    """A far identity flickering with my real sightings (1 frame out of 2): never "me"."""
    tr = Tracker()
    trace: list = []
    run(tr, 0.0, 3.0, lambda t: [ident(*_swain_lane(t), alias="Swain")], trace)
    k = {"i": 0}

    def frame(t: float) -> list:
        k["i"] += 1
        if k["i"] % 2:
            return [ident(0.18, 0.27, alias="Swain", score=0.7)]
        return [ident(*_swain_lane(t), alias="Swain")]

    run(tr, 3.0, 12.0, frame, trace)
    assert _jumps(trace) == 0
    assert tr.self_confirmed == 0


def test_teleport_landing_is_confirmed_quickly() -> None:
    """Garen top teleports bot (4 s channel, then my portrait at the landing spot): me is
    there within ~1.5 s of the landing, never drawn at two places."""
    tr = Tracker()
    trace: list = []
    run(tr, 0.0, 6.0, lambda t: [ident(0.10, 0.30, alias="Garen")], trace)    # channel at top
    land = run(tr, 6.0, 10.0, lambda t: [ident(0.80 + 0.003 * (t - 6.0), 0.75, alias="Garen")], trace)
    me = tr.me()
    assert me is not None and me.position()[0] > 0.75
    first = next(t for t, p in trace if p is not None and p[0] > 0.75)
    assert first - 6.0 <= T.SELF_GUARD_CONFIRM_S + 2 * DT
    assert tr.self_confirmed == 1
    # while unconfirmed the old place is kept or hidden, never both
    assert all(p is None or p[0] < 0.2 or p[0] > 0.75 for _t, p in trace)
    assert land >= 10.0 - 1e-6


def test_respawn_and_recall_at_my_fountain_are_accepted_at_once() -> None:
    tr = Tracker()
    tr.set_roster({"Swain": "self"})
    run(tr, 0.0, 3.0, lambda t: [ident(0.80, 0.80, alias="Swain")])
    # recall: my fountain is always reachable
    tr.update(3.0, [ident(0.05, 0.95, alias="Swain")])
    assert tr.me().position() == pytest.approx((0.05, 0.95), abs=0.01)
    run(tr, 3.2, 6.0, lambda t: [ident(0.06 + 0.02 * (t - 3.2), 0.93, alias="Swain")])
    # death far from home (Live Client), respawn: the fountain is my last place
    run(tr, 6.0, 9.0, lambda t: [ident(0.6, 0.4, alias="Swain")])   # (walked there: TP-like, confirmed)
    tr.set_dead(["Swain"])
    tr.update(9.5, [])
    tr.set_dead([])
    trace: list = []
    run(tr, 20.0, 23.0, lambda t: [ident(0.08 + 0.02 * (t - 20.0), 0.92, alias="Swain")], trace)
    assert trace[0][1] is not None and trace[0][1][0] < 0.12
    assert tr.self_anchor() is not None


def test_guard_off_switch_and_reset() -> None:
    tr = Tracker()
    run(tr, 0.0, 2.0, lambda t: [ident(0.8, 0.8, alias="Swain")])
    assert tr.self_anchor() is not None
    tr.reset()
    assert tr.self_anchor() is None
    old = T.SELF_GUARD
    try:
        T.SELF_GUARD = False
        run(tr, 0.0, 2.0, lambda t: [ident(0.8, 0.8, alias="Swain")])
        tr.update(2.0, [ident(0.1, 0.2, alias="Swain")])
        tr.update(2.1, [ident(0.1, 0.2, alias="Swain")])       # old TP rule: 2 frames
        assert tr.me().position()[0] < 0.2
    finally:
        T.SELF_GUARD = old
