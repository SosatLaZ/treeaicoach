"""Tests of treeaicoach.tracker (ARCHITECTURE.md §4.11), deterministic clock."""

from __future__ import annotations

import math
import random
import threading
from dataclasses import dataclass

import pytest

from treeaicoach.geometry import Zone
from treeaicoach.tracker import (
    ANON_FORGET_S,
    HIDE_AFTER,
    OBS_MAXLEN,
    Track,
    Tracker,
)

try:  # real dataclasses when available (identifier is written in parallel)
    from treeaicoach.detector import Detection
    from treeaicoach.identifier import Identified
except Exception:  # pragma: no cover - light stand-ins, same fields as the contract
    @dataclass
    class Detection:  # type: ignore[no-redef]
        u: float
        v: float
        r: float
        score: float
        cls: str
        cls_probs: tuple[float, float, float]

    @dataclass
    class Identified:  # type: ignore[no-redef]
        det: Detection
        alias: str | None
        relation: str
        team: str | None
        id_score: float


_PROBS = {"enemy": (0.9, 0.05, 0.05), "ally": (0.05, 0.9, 0.05), "self": (0.05, 0.9, 0.05)}


def ident(u: float, v: float, relation: str = "enemy", alias: str | None = None,
          team: str | None = None, score: float = 0.9) -> Identified:
    cls = "ally" if relation == "self" else relation
    det = Detection(u=u, v=v, r=0.047, score=score, cls=cls, cls_probs=_PROBS[relation])
    return Identified(det=det, alias=alias, relation=relation, team=team,
                      id_score=0.8 if alias else 0.0)


FPS = 8.0
DT = 1.0 / FPS


def run(tr: Tracker, t0: float, t1: float, frame) -> float:
    """Feed frames at FPS between t0 and t1 (frame(t) -> list[Identified])."""
    t = t0
    while t < t1 - 1e-9:
        tr.update(t, frame(t))
        t += DT
    return t


def test_identity_track_position_velocity_visibility() -> None:
    tr = Tracker()
    run(tr, 0.0, 3.0, lambda t: [ident(0.3 + 0.02 * t, 0.5, alias="LeeSin", team="CHAOS")])
    track = tr.get("LeeSin")
    assert track is not None and track.visible and track.relation == "enemy"
    assert track.team == "CHAOS" and track.alias == "LeeSin"
    pos = track.position()
    assert pos is not None and abs(pos[0] - (0.3 + 0.02 * (3.0 - DT * 2))) < 0.01
    vx, vy = track.velocity()
    assert abs(vx - 0.02) < 1e-3 and abs(vy) < 1e-3
    assert track.appeared_at == 0.0 and track.hidden_since is None
    # hidden after HIDE_AFTER
    tr.update(3.0 + HIDE_AFTER + 0.05, [])
    track = tr.get("LeeSin")
    assert not track.visible and track.hidden_since == pytest.approx(3.0 - DT)
    assert [t.key for t in tr.enemies()] == []
    assert [t.key for t in tr.enemies(visible_only=False)] == ["LeeSin"]


def test_median_position_rejects_single_outlier() -> None:
    tr = Tracker()
    pts = [(0.50, 0.50), (0.51, 0.50), (0.56, 0.55), (0.52, 0.50)]
    for i, (u, v) in enumerate(pts):
        tr.update(i * DT, [ident(u, v, alias="Ahri")])
    pos = tr.get("Ahri").position()
    assert pos == pytest.approx((0.52, 0.50))


def test_velocity_needs_three_points_and_span() -> None:
    tr = Tracker()
    tr.update(0.0, [ident(0.5, 0.5, alias="Ahri")])
    tr.update(0.1, [ident(0.51, 0.5, alias="Ahri")])
    assert tr.get("Ahri").velocity() == (0.0, 0.0)
    tr.update(0.2, [ident(0.52, 0.5, alias="Ahri")])      # 3 points but span 0.2 s < 0.35
    assert tr.get("Ahri").velocity() == (0.0, 0.0)
    tr.update(0.4, [ident(0.54, 0.5, alias="Ahri")])
    vx, _ = tr.get("Ahri").velocity()
    assert vx == pytest.approx(0.1, abs=1e-6)


def test_teleport_resets_history() -> None:
    tr = Tracker()
    run(tr, 0.0, 2.0, lambda t: [ident(0.3 + 0.02 * t, 0.3, alias="Darius")])
    tr.update(2.0, [ident(0.9, 0.1, alias="Darius")])      # recall / TP: jump > 0.15 in 0.125 s
    track = tr.get("Darius")
    assert track.position() == pytest.approx((0.9, 0.1))
    assert track.velocity() == (0.0, 0.0)
    assert len(track.points()) == 1


def test_appeared_at_after_hidden() -> None:
    tr = Tracker()
    run(tr, 0.0, 1.0, lambda t: [ident(0.4, 0.4, alias="LeeSin")])
    # short occlusion (< 1.5 s): appeared_at unchanged
    tr.update(2.0, [ident(0.41, 0.4, alias="LeeSin")])
    assert tr.get("LeeSin").appeared_at == 0.0
    # long hide
    tr.update(40.0, [ident(0.7, 0.7, alias="LeeSin")])
    track = tr.get("LeeSin")
    assert track.appeared_at == 40.0
    assert track.prev_hidden_s == pytest.approx(38.0)
    assert track.visible


def test_anonymous_tracks_follow_nearest_and_merge_into_identity() -> None:
    tr = Tracker()
    # two unidentified enemies moving in parallel
    run(tr, 0.0, 2.0, lambda t: [ident(0.3 + 0.02 * t, 0.3), ident(0.6, 0.6 + 0.02 * t)])
    keys = sorted(t.key for t in tr.enemies())
    assert keys == ["enemy?1", "enemy?2"]
    a = tr.get("enemy?1")
    assert a.position()[1] == pytest.approx(0.3, abs=0.01) or a.position()[0] == pytest.approx(0.6, abs=0.01)
    # the first one is identified nearby -> merged, history kept
    anon_key = next(k for k in keys if abs(tr.get(k).position()[1] - 0.3) < 0.02)
    n_before = tr.get(anon_key).n_obs
    tr.update(2.0, [ident(0.34, 0.3, alias="Zed"), ident(0.6, 0.64)])
    assert tr.get(anon_key) is None
    zed = tr.get("Zed")
    assert zed is not None and zed.n_obs == n_before + 1
    assert zed.first_seen == 0.0 and zed.velocity()[0] == pytest.approx(0.02, abs=0.003)
    assert len(tr.enemies()) == 2


def test_unidentified_frame_continues_identity_track() -> None:
    tr = Tracker()
    run(tr, 0.0, 1.0, lambda t: [ident(0.3, 0.3, alias="Zed")])
    tr.update(1.0, [ident(0.305, 0.3)])                # identification miss for one frame
    assert [t.key for t in tr.enemies()] == ["Zed"]
    assert tr.get("Zed").last_seen == 1.0


def test_anonymous_forgotten_after_20s() -> None:
    tr = Tracker()
    tr.update(0.0, [ident(0.5, 0.5)])
    assert tr.get("enemy?1") is not None
    tr.update(ANON_FORGET_S - 1, [])
    assert tr.get("enemy?1") is not None
    tr.update(ANON_FORGET_S + 0.5, [])
    assert tr.get("enemy?1") is None and len(tr) == 0


def test_single_self_highest_score() -> None:
    tr = Tracker()
    tr.update(0.0, [ident(0.1, 0.1, "self", alias="Garen", team="ORDER", score=0.9),
                    ident(0.5, 0.5, "self", score=0.4)])
    me = tr.me()
    assert me is not None and me.alias == "Garen"
    assert sum(1 for t in tr.tracks() if t.relation == "self") == 1
    # fallback "self" claim (no identity) while the identified self is recent -> ignored
    tr.update(0.2, [ident(0.1, 0.1, "self", alias="Garen", team="ORDER"),
                    ident(0.6, 0.6, "self")])
    assert tr.me().alias == "Garen"
    assert sum(1 for t in tr.tracks() if t.relation == "self") == 1


def test_zone_fraction_and_bounded_memory() -> None:
    tr = Tracker()
    # 40 s in top lane then 20 s in mid
    run(tr, 0.0, 40.0, lambda t: [ident(0.08, 0.3, alias="Darius")])
    run(tr, 40.0, 60.0, lambda t: [ident(0.5, 0.5, alias="Darius")])
    d = tr.get("Darius")
    assert d.zone() == Zone.MID_LANE
    assert d.zone_fraction("top", 90.0, 60.0) == pytest.approx(40 / 60, abs=0.03)
    assert d.zone_fraction("MIDDLE", 90.0, 60.0) == pytest.approx(20 / 60, abs=0.03)
    assert d.zone_fraction("top", 10.0, 60.0) == 0.0
    assert d.observed_time(90.0, 60.0) == pytest.approx(60.0, abs=0.5)
    assert len(d.points()) <= OBS_MAXLEN


def test_snapshots_are_independent_and_thread_safe() -> None:
    tr = Tracker()
    stop = threading.Event()
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            while not stop.is_set():
                for track in tr.tracks():
                    track.position(); track.velocity(); track.zone_fraction("top", 90, 100)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    th = threading.Thread(target=reader)
    th.start()
    rng = random.Random(1)
    for i in range(300):
        tr.update(i * DT, [ident(rng.random(), rng.random()) for _ in range(4)]
                  + [ident(0.2, 0.2, "self", alias="Garen")])
    stop.set()
    th.join(5)
    assert not errors
    snap = tr.me()
    before = snap.n_obs
    tr.update(300 * DT, [ident(0.2, 0.2, "self", alias="Garen")])
    assert snap.n_obs == before            # snapshot not mutated


def test_never_raises_on_garbage() -> None:
    tr = Tracker()
    tr.update(float("nan"), [ident(0.5, 0.5)])
    tr.update(0.0, None)                                  # type: ignore[arg-type]
    tr.update(0.1, [object(), ident(float("nan"), 0.5), ident(0.5, 0.5, alias="Ahri")])  # type: ignore[list-item]
    assert tr.get("Ahri") is not None
    tr.update(-100.0, [])                                  # clock jumps back -> reset
    assert len(tr) == 0
    tr.reset()
    assert tr.me() is None and tr.tracks() == []


def test_track_is_dataclass_with_contract_fields() -> None:
    t = Track(key="enemy?1", alias=None, relation="enemy", team=None, first_seen=0.0, last_seen=0.0)
    for name in ("key", "alias", "relation", "team", "first_seen", "last_seen", "visible",
                 "appeared_at", "hidden_since"):
        assert hasattr(t, name)
    assert t.position() is None and t.velocity() == (0.0, 0.0) and t.zone() is None
    assert math.isclose(t.zone_fraction("top", 90, 0), 0.0)
