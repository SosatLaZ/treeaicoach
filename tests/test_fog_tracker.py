"""Tests of treeaicoach.fog_tracker (walkable mask, geodesic distances, fog estimates)."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np
import pytest

from treeaicoach import fog_tracker as ft
from treeaicoach import geometry


# ---------------------------------------------------------------------------- stand-ins
@dataclass
class FakeTrack:
    key: str
    alias: str | None
    visible: bool = True
    last_seen: float = 0.0
    pos: tuple[float, float] | None = (0.3, 0.3)
    vel: tuple[float, float] = (0.0, 0.0)
    relation: str = "enemy"
    team: str | None = "CHAOS"
    has_smite: bool = False

    def position(self):
        return self.pos

    def velocity(self):
        return self.vel


@dataclass
class FakeTracker:
    tracks: list = field(default_factory=list)

    def enemies(self, visible_only: bool = True):
        return [t for t in self.tracks if t.relation == "enemy" and (t.visible or not visible_only)]


@dataclass
class FakePlayer:
    champion_alias: str
    champion_name: str
    is_dead: bool = False
    has_smite: bool = False
    position: str = ""


@dataclass
class FakeGame:
    enemies: list
    game_time: float = 600.0

    def enemy_jungler(self):
        for p in self.enemies:
            if p.has_smite:
                return p
        return None

    def player_by_alias(self, alias):
        for p in self.enemies:
            if p.champion_alias.lower() == str(alias).lower():
                return p
        return None


def open_tracker() -> ft.FogTracker:
    tex = np.zeros((256, 256, 4), np.uint8)
    tex[..., 3] = 255
    return ft.FogTracker(tex)


def game_with_lee(dead: bool = False, gt: float = 600.0) -> FakeGame:
    return FakeGame([FakePlayer("LeeSin", "Lee Sin", is_dead=dead, has_smite=True, position="JUNGLE"),
                     FakePlayer("Darius", "Darius", position="TOP")], game_time=gt)


# ---------------------------------------------------------------------------- walkable mask
def test_walkable_mask_uses_alpha():
    tex = np.zeros((512, 512, 4), np.uint8)
    tex[:, :256, 3] = 255             # left half walkable
    tex[:, 256:, :3] = 200            # right half: colored but alpha 0 -> wall
    m = ft.walkable_mask(tex, 128)
    assert m.shape == (128, 128) and m.dtype == bool
    assert m[:, :64].all() and not m[:, 64:].any()


def test_walkable_mask_invalid_input_is_all_walkable():
    m = ft.walkable_mask(np.zeros((5,), np.uint8), 64)
    assert m.shape == (64, 64) and m.all()
    m = ft.walkable_mask(np.zeros((64, 64, 4), np.uint8))  # nothing walkable
    assert m.all()


def test_real_texture_mask_is_plausible():
    tex = ft.load_default_texture()
    if tex is None:
        pytest.skip("minimap texture asset missing")
    m = ft.walkable_mask(tex)
    frac = m.mean()
    assert 0.25 < frac < 0.85
    # both fountains are walkable, the outer black margin is not
    assert m[int(0.93 * 128), int(0.07 * 128)] and m[int(0.07 * 128), int(0.93 * 128)]
    assert not m[0, 0]


# ---------------------------------------------------------------------------- distances
def test_distance_field_open_grid_is_close_to_euclidean():
    r = ft.Reachability(np.ones((128, 128), bool))
    d = r.distance_field((0.5, 0.5))
    assert d.dtype == np.float32 and d.shape == (128, 128)
    assert d[64, 64] == 0.0
    ys, xs = np.mgrid[0:128, 0:128]
    eu = np.hypot(xs - 64, ys - 64) / 128.0
    far = eu > 0.1
    ratio = d[far] / eu[far]
    assert ratio.min() > 0.99 - 1e-6       # never shorter than straight line
    assert ratio.max() < 1.10              # octagonal metric: within ~8 %


def test_distance_field_goes_around_walls_and_inf_when_unreachable():
    walk = np.ones((128, 128), bool)
    walk[:, 60:68] = False                 # vertical wall...
    walk[120:, 60:68] = True               # ...with a gap at the bottom
    walk[0:10, 100:110] = False
    walk[2:8, 102:108] = True              # isolated pocket
    r = ft.Reachability(walk)
    d = r.distance_field((40 / 128, 10 / 128))
    target = d[10, 90]
    straight = 50 / 128
    assert math.isfinite(target) and target > 2.5 * straight   # detour through the gap
    assert np.isinf(d[4, 104])                                   # pocket unreachable
    assert np.isinf(d[~walk]).all()


def test_start_is_snapped_to_walkable():
    walk = np.zeros((64, 64), bool)
    walk[30:34, 10:50] = True
    r = ft.Reachability(walk)
    d = r.distance_field((0.5, 0.1))       # in the wall, above the corridor
    assert np.isfinite(d).sum() == walk.sum()
    assert d.min() == 0.0 and walk[np.unravel_index(np.argmin(d), d.shape)]


def test_distance_field_speed_on_real_texture():
    tex = ft.load_default_texture()
    if tex is None:
        pytest.skip("minimap texture asset missing")
    r = ft.Reachability(ft.walkable_mask(tex))
    r.distance_field((0.5, 0.5))           # warm-up
    t0 = time.perf_counter()
    for uv in ((0.1, 0.9), (0.3, 0.25), (0.7, 0.6)):
        d = r.distance_field(uv)
    dt = (time.perf_counter() - t0) / 3
    assert np.isfinite(d).mean() > 0.2
    assert dt < 0.08, f"distance field too slow: {dt * 1000:.1f} ms (noisy shared CPU)"


# ---------------------------------------------------------------------------- speeds
def test_nominal_and_clamped_speeds():
    early, late = ft.nominal_speed(60), ft.nominal_speed(400)
    assert early == pytest.approx(345 / geometry.MAP_GAME_UNITS)
    assert late == pytest.approx(390 / geometry.MAP_GAME_UNITS)
    assert ft.nominal_speed(None) == late
    assert ft.clamp_speed(0.0, 400) == pytest.approx(0.85 * late)
    assert ft.clamp_speed(1.0, 400) == pytest.approx(1.35 * late)
    assert ft.clamp_speed(float("nan"), 60) == pytest.approx(0.85 * early)
    mid = 1.1 * late
    assert ft.clamp_speed(mid, 400) == pytest.approx(mid)


# ---------------------------------------------------------------------------- tracker lifecycle
def test_estimate_lifecycle_jungler_mode():
    fog = open_tracker()
    lee = FakeTrack("LeeSin", "LeeSin", visible=True, last_seen=10.0, pos=(0.3, 0.3), vel=(0.03, 0.0))
    dar = FakeTrack("Darius", "Darius", visible=False, last_seen=5.0, pos=(0.1, 0.1))
    tr = FakeTracker([lee, dar])
    game = game_with_lee()
    assert fog.update(10.0, tr, game) == []        # Darius hidden but not the jungler
    lee.visible = False
    est = fog.update(15.0, tr, game)
    assert len(est) == 1
    e = est[0]
    assert e.key == "LeeSin" and e.is_jungler and e.name == "Lee Sin"
    assert e.elapsed == pytest.approx(5.0) and e.last_uv == (0.3, 0.3)
    assert e.speed == pytest.approx(0.03)          # within [0.85, 1.35] x nominal (0.0262)
    assert e.radius == pytest.approx(0.03 * 5 + ft.REACH_MARGIN + ft.FLASH_MARGIN)
    assert e.region is not None and e.region.dtype == bool and not e.region.flags.writeable
    assert e.region[int(0.3 * 128), int(0.3 * 128)]
    assert 0.9 < e.confidence < 1.0
    area5 = e.region.sum()
    e2 = fog.update(25.0, tr, game)[0]
    assert e2.region.sum() > area5 and e2.confidence < e.confidence
    assert fog.estimate_for("leesin") is not None
    lee.visible = True
    lee.last_seen = 26.0
    assert fog.update(26.0, tr, game) == []        # removed as soon as it is visible again
    assert fog.estimates() == []


def test_region_matches_geodesic_budget():
    fog = open_tracker()
    e = fog.simulate("X", "X", "X", (0.5, 0.5), 10.0, speed=0.02, game_time=600)
    ys, xs = np.nonzero(e.region)
    dist = np.hypot((xs + 0.5) / 128 - 0.5, (ys + 0.5) / 128 - 0.5)
    budget = 0.02 * 10 + ft.REACH_MARGIN + ft.FLASH_MARGIN
    assert dist.max() <= budget * 1.02 + 1.5 / 128
    assert dist.max() >= budget * 0.9


def test_confidence_and_expiry():
    fog = open_tracker()
    fog.set_max_s(40)
    lee = FakeTrack("LeeSin", "LeeSin", visible=False, last_seen=0.0)
    tr = FakeTracker([lee])
    game = game_with_lee()
    assert fog.update(20.0, tr, game)[0].confidence == pytest.approx(0.5)
    assert fog.update(45.0, tr, game)[0].confidence == 0.0   # faded, still kept
    assert fog.update(51.0, tr, game) == []                  # > max + 10 s
    assert fog.update(52.0, tr, game) == []                  # not restarted for the same loss


def test_dead_champion_is_removed():
    fog = open_tracker()
    lee = FakeTrack("LeeSin", "LeeSin", visible=False, last_seen=0.0)
    tr = FakeTracker([lee])
    assert len(fog.update(3.0, tr, game_with_lee())) == 1
    assert fog.update(4.0, tr, game_with_lee(dead=True)) == []
    assert fog.update(5.0, tr, game_with_lee()) == []        # same disappearance: stays closed


def test_new_disappearance_restarts():
    fog = open_tracker()
    lee = FakeTrack("LeeSin", "LeeSin", visible=False, last_seen=0.0, pos=(0.2, 0.2))
    tr = FakeTracker([lee])
    game = game_with_lee()
    fog.update(5.0, tr, game)
    lee.visible, lee.last_seen, lee.pos = True, 6.0, (0.6, 0.6)
    fog.update(6.0, tr, game)
    lee.visible = False
    e = fog.update(8.0, tr, game)[0]
    assert e.last_uv == (0.6, 0.6) and e.elapsed == pytest.approx(2.0)


def test_modes_all_and_off():
    fog = open_tracker()
    tr = FakeTracker([FakeTrack("LeeSin", "LeeSin", visible=False, last_seen=0.0),
                      FakeTrack("Darius", "Darius", visible=False, last_seen=1.0, pos=(0.1, 0.2)),
                      FakeTrack("enemy?1", None, visible=False, last_seen=1.0)])
    game = game_with_lee()
    est = fog.update(4.0, tr, game, mode="all")
    assert [e.key for e in est] == ["LeeSin", "Darius"]      # jungler first; anonymous ignored
    assert fog.update(5.0, tr, game, mode="off") == []
    assert fog.update(5.0, tr, game, mode="bogus")[0].key == "LeeSin"   # unknown -> jungler


def test_smite_track_fallback_without_game():
    fog = open_tracker()
    tr = FakeTracker([FakeTrack("Kayn", "Kayn", visible=False, last_seen=0.0, has_smite=True)])
    est = fog.update(3.0, tr, None)
    assert len(est) == 1 and est[0].is_jungler


def test_distance_field_is_computed_once_per_disappearance(monkeypatch):
    fog = open_tracker()
    calls = []
    orig = fog.reach.distance_field
    monkeypatch.setattr(fog.reach, "distance_field", lambda uv: calls.append(uv) or orig(uv))
    tr = FakeTracker([FakeTrack("LeeSin", "LeeSin", visible=False, last_seen=0.0)])
    game = game_with_lee()
    for t in np.arange(1.0, 20.0, 0.25):
        fog.update(float(t), tr, game)
    assert len(calls) == 1


def test_update_never_raises():
    fog = open_tracker()

    class Broken:
        def enemies(self, visible_only=True):
            raise RuntimeError("boom")

        def tracks(self):
            raise RuntimeError("boom")

    assert fog.update(1.0, Broken(), None) == []
    assert fog.update(1.0, None, None) == []
    bad = FakeTrack("LeeSin", "LeeSin", visible=False, last_seen=float("nan"))
    assert fog.update(2.0, FakeTracker([bad]), game_with_lee()) == []


def test_helpers_outline_and_union():
    fog = open_tracker()
    a = fog.simulate("A", "A", "A", (0.2, 0.2), 3.0)
    b = fog.simulate("B", "B", "B", (0.8, 0.8), 3.0)
    u = ft.union_region([a, b])
    assert u.sum() == a.region.sum() + b.region.sum()
    outl = ft.region_outline_points(a)
    assert outl and all(((c >= 0) & (c <= 1)).all() for c in outl)
    assert ft.union_region([]) is None
