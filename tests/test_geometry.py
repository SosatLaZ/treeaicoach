"""Tests for treeaicoach.geometry (zones measured on the official minimap texture)."""

from __future__ import annotations

import math
import threading
from pathlib import Path

import numpy as np
import pytest

from treeaicoach import geometry as g
from treeaicoach.geometry import Zone

TEXTURE = Path(__file__).resolve().parents[1] / "treeaicoach" / "assets" / "minimap" / "2dlevelminimap_base_baron1.png"


def gz(x: float, y: float) -> Zone:
    """Zone of a game-coordinate position."""
    return g.classify_zone(*g.game_to_uv(x, y))


# Official structure / camp coordinates (x right, y up).
LANE_TURRETS = {
    Zone.TOP_LANE: [(981, 10441), (1512, 6699), (4318, 13875), (7943, 13411)],
    Zone.MID_LANE: [(5846, 6396), (5048, 4812), (8955, 8510), (9767, 10113)],
    Zone.BOT_LANE: [(10504, 1029), (6919, 1483), (13866, 4505), (13327, 8226)],
}
BLUE_BASE_POINTS = [(1169, 4287), (3651, 3696), (4281, 1253), (1748, 2270), (2177, 1807),
                    (1171, 3571), (3203, 3208), (3452, 1236), (1551, 1659), (400, 400)]
RED_BASE_POINTS = [(10481, 13650), (11134, 11207), (13624, 10572), (12611, 13084), (13052, 12612),
                   (11261, 13676), (11598, 11667), (13604, 11316), (13052, 12997), (14400, 14500)]
CAMPS = {
    Zone.BLUE_JUNGLE_TOP: [(3821, 8101), (2288, 8448), (3783, 6495)],     # blue buff, gromp, wolves
    Zone.BLUE_JUNGLE_BOT: [(6823, 5508), (7765, 4020), (8394, 2641)],     # raptors, red buff, krugs
    Zone.RED_JUNGLE_BOT: [(11131, 6990), (12703, 6443), (11059, 8422)],   # blue buff, gromp, wolves
    Zone.RED_JUNGLE_TOP: [(7852, 9434), (7101, 10900), (6317, 12146)],    # raptors, red buff, krugs
    Zone.TOP_RIVER: [(4400, 9600), (5007, 10471)],                        # scuttle, baron
    Zone.BOT_RIVER: [(10500, 5170), (9866, 4414)],                        # scuttle, dragon
}

MIRROR = {
    Zone.BLUE_BASE: Zone.RED_BASE, Zone.RED_BASE: Zone.BLUE_BASE,
    Zone.TOP_LANE: Zone.BOT_LANE, Zone.BOT_LANE: Zone.TOP_LANE, Zone.MID_LANE: Zone.MID_LANE,
    Zone.TOP_RIVER: Zone.BOT_RIVER, Zone.BOT_RIVER: Zone.TOP_RIVER,
    Zone.BLUE_JUNGLE_TOP: Zone.RED_JUNGLE_BOT, Zone.RED_JUNGLE_BOT: Zone.BLUE_JUNGLE_TOP,
    Zone.BLUE_JUNGLE_BOT: Zone.RED_JUNGLE_TOP, Zone.RED_JUNGLE_TOP: Zone.BLUE_JUNGLE_BOT,
}


@pytest.mark.parametrize("zone,points", list(LANE_TURRETS.items()))
def test_lane_turrets_are_in_their_lane(zone, points):
    for x, y in points:
        assert gz(x, y) == zone, (x, y, g.game_to_uv(x, y))


def test_bases():
    for x, y in BLUE_BASE_POINTS:
        assert gz(x, y) == Zone.BLUE_BASE, (x, y)
    for x, y in RED_BASE_POINTS:
        assert gz(x, y) == Zone.RED_BASE, (x, y)
    assert g.classify_zone(0.07, 0.93) == Zone.BLUE_BASE
    assert g.classify_zone(0.93, 0.07) == Zone.RED_BASE


@pytest.mark.parametrize("zone,points", list(CAMPS.items()))
def test_camps_jungle_quadrants_and_river(zone, points):
    for x, y in points:
        assert gz(x, y) == zone, (x, y, g.game_to_uv(x, y))


@pytest.mark.parametrize("uv,zone", [
    ((0.5, 0.5), Zone.MID_LANE),
    ((0.0, 0.0), Zone.TOP_LANE),        # top-left corner (outside the lane loop)
    ((1.0, 1.0), Zone.BOT_LANE),
    ((0.0, 1.0), Zone.BLUE_BASE),
    ((1.0, 0.0), Zone.RED_BASE),
    ((0.082, 0.45), Zone.TOP_LANE),     # middle of the left lane band
    ((0.45, 0.082), Zone.TOP_LANE),
    ((0.918, 0.55), Zone.BOT_LANE),
    ((0.55, 0.918), Zone.BOT_LANE),
    ((0.12, 0.12), Zone.TOP_LANE),      # top lane bend
    ((0.88, 0.88), Zone.BOT_LANE),
    ((0.01, 0.5), Zone.TOP_LANE),       # outer rim
    ((0.5, 0.99), Zone.BOT_LANE),
    ((0.30, 0.34), Zone.TOP_RIVER),
    ((0.40, 0.42), Zone.TOP_RIVER),
    ((0.70, 0.66), Zone.BOT_RIVER),
    ((0.25, 0.50), Zone.BLUE_JUNGLE_TOP),
    ((0.55, 0.75), Zone.BLUE_JUNGLE_BOT),
    ((0.45, 0.25), Zone.RED_JUNGLE_TOP),
    ((0.75, 0.50), Zone.RED_JUNGLE_BOT),
])
def test_known_points(uv, zone):
    assert g.classify_zone(*uv) == zone
    assert g.classify_zone_exact(*uv) == zone


def test_clamping_and_invalid_values():
    assert g.classify_zone(-3.0, 5.0) == Zone.BLUE_BASE
    assert g.classify_zone(7.0, -1.0) == Zone.RED_BASE
    assert g.classify_zone(-1.0, -1.0) == Zone.TOP_LANE
    assert g.classify_zone(float("nan"), float("nan")) == Zone.MID_LANE
    assert g.classify_zone(float("inf"), float("-inf")) == Zone.RED_BASE
    assert g.classify_zone(None, "x") == Zone.MID_LANE          # type: ignore[arg-type]
    assert g.classify_zone(np.float32(0.5), np.int64(1)) in set(Zone)
    assert g.side_of(float("nan"), 0.2) == "top"


def test_every_point_has_a_zone_and_all_zones_exist():
    codes = g.zone_map(128)
    assert codes.shape == (128, 128)
    assert set(np.unique(codes).tolist()) == set(range(len(g.ZONES)))
    rng = np.random.default_rng(0)
    pts = rng.uniform(-0.2, 1.2, size=(2000, 2))
    zones = g.classify_zones(pts)
    assert len(zones) == 2000 and all(isinstance(z, Zone) for z in zones)
    assert g.classify_zones([]) == []
    assert g.classify_zones("garbage") == []


def test_lookup_table_matches_exact_classification():
    rng = np.random.default_rng(1)
    pts = rng.uniform(0, 1, size=(1500, 2))
    same = sum(g.classify_zone(u, v) == g.classify_zone_exact(u, v) for u, v in pts)
    assert same / len(pts) > 0.99


def test_point_symmetry_of_the_partition():
    rng = np.random.default_rng(2)
    pts = rng.uniform(0, 1, size=(1500, 2))
    ok = sum(MIRROR[g.classify_zone(u, v)] == g.classify_zone(1 - u, 1 - v) for u, v in pts)
    assert ok / len(pts) > 0.98


def test_side_consistency_of_rivers_and_jungles():
    rng = np.random.default_rng(3)
    for u, v in rng.uniform(0, 1, size=(1500, 2)):
        z = g.classify_zone(u, v)
        if z in (Zone.BLUE_JUNGLE_TOP, Zone.BLUE_JUNGLE_BOT):
            assert v >= u
        if z in (Zone.RED_JUNGLE_TOP, Zone.RED_JUNGLE_BOT):
            assert v < u
        if z in (Zone.BLUE_JUNGLE_TOP, Zone.RED_JUNGLE_TOP, Zone.TOP_RIVER):
            assert u + v < 1.0 + 1e-9
        if z in (Zone.BLUE_JUNGLE_BOT, Zone.RED_JUNGLE_BOT, Zone.BOT_RIVER):
            assert u + v >= 1.0 - 1e-9


def test_texture_lanes_and_river_match_zones():
    cv2 = pytest.importorskip("cv2")
    if not TEXTURE.is_file():
        pytest.skip("minimap texture not available")
    im = cv2.imread(str(TEXTURE), cv2.IMREAD_UNCHANGED)
    assert im is not None and im.shape[2] == 4
    b, gr, r, a = (im[..., i].astype(int) for i in range(4))
    n = im.shape[0]
    codes = g.zone_map(n)
    lanes = {g.ZONES.index(z) for z in (Zone.TOP_LANE, Zone.MID_LANE, Zone.BOT_LANE)}
    rivers = {g.ZONES.index(z) for z in (Zone.TOP_RIVER, Zone.BOT_RIVER)}
    bases = {g.ZONES.index(z) for z in (Zone.BLUE_BASE, Zone.RED_BASE)}
    river_px = (a > 0) & (b - r > 60)
    river_codes = codes[river_px]
    frac_river = np.isin(river_codes, list(rivers | lanes | bases)).mean()
    assert frac_river > 0.97, frac_river
    assert np.isin(river_codes, list(rivers)).mean() > 0.85
    # khaki lane bands along the four edges (outside the bases) are lane zones
    khaki = (a > 0) & (abs(b - 112) < 25) & (abs(gr - 154) < 25) & (abs(r - 145) < 25)
    band = np.zeros_like(khaki)
    lo, hi = int(0.30 * n), int(0.60 * n)
    band[lo:hi, : int(0.13 * n)] = True          # left edge
    band[: int(0.13 * n), lo:hi] = True          # top edge
    band[lo:hi, int(0.87 * n):] = True           # right edge
    band[int(0.87 * n):, lo:hi] = True           # bottom edge
    c = (np.arange(n) + 0.5) / n
    uu, vv = np.meshgrid(c, c)
    far_from_bases = (np.hypot(uu, vv - 1) > g.BASE_RADIUS) & (np.hypot(uu - 1, vv) > g.BASE_RADIUS)
    sel = khaki & band
    assert np.isin(codes[sel], list(lanes | bases)).mean() > 0.99
    assert np.isin(codes[sel & far_from_bases], list(lanes)).mean() > 0.99


def test_lane_of_and_predicates():
    assert g.lane_of(Zone.TOP_LANE) == "top"
    assert g.lane_of(Zone.MID_LANE) == "mid"
    assert g.lane_of(Zone.BOT_LANE) == "bot"
    for z in Zone:
        if z not in (Zone.TOP_LANE, Zone.MID_LANE, Zone.BOT_LANE):
            assert g.lane_of(z) is None
    assert g.lane_of(None) is None
    assert g.lane_of("TOP_LANE") == "top" and g.lane_of("mid_lane") == "mid"
    assert g.is_base(Zone.BLUE_BASE) and g.is_base(Zone.RED_BASE)
    assert not g.is_base(Zone.TOP_LANE) and not g.is_base(None)
    assert g.is_river(Zone.TOP_RIVER) and not g.is_river(Zone.MID_LANE)
    assert g.is_jungle(Zone.RED_JUNGLE_BOT) and not g.is_jungle(Zone.BOT_RIVER)
    assert g.zone_owner(Zone.BLUE_JUNGLE_TOP) == "ORDER"
    assert g.zone_owner(Zone.RED_BASE) == "CHAOS"
    assert g.zone_owner(Zone.MID_LANE) is None


def test_side_of():
    assert g.side_of(0.2, 0.3) == "top"
    assert g.side_of(0.7, 0.6) == "bot"
    assert g.side_of(0.5, 0.5) == "bot"


EXPECTED_LABELS = {
    # zone: (ORDER, CHAOS, None)
    Zone.TOP_LANE: ("en haut",) * 3,
    Zone.MID_LANE: ("au milieu",) * 3,
    Zone.BOT_LANE: ("en bas",) * 3,
    Zone.TOP_RIVER: ("dans la rivière du haut",) * 3,
    Zone.BOT_RIVER: ("dans la rivière du bas",) * 3,
    Zone.BLUE_JUNGLE_TOP: ("dans ta jungle du haut", "dans la jungle ennemie du haut", "dans la jungle bleue du haut"),
    Zone.BLUE_JUNGLE_BOT: ("dans ta jungle du bas", "dans la jungle ennemie du bas", "dans la jungle bleue du bas"),
    Zone.RED_JUNGLE_TOP: ("dans la jungle ennemie du haut", "dans ta jungle du haut", "dans la jungle rouge du haut"),
    Zone.RED_JUNGLE_BOT: ("dans la jungle ennemie du bas", "dans ta jungle du bas", "dans la jungle rouge du bas"),
    Zone.BLUE_BASE: ("dans ta base", "dans la base ennemie", "dans la base bleue"),
    Zone.RED_BASE: ("dans la base ennemie", "dans ta base", "dans la base rouge"),
}


@pytest.mark.parametrize("zone", list(Zone))
def test_zone_label_fr(zone):
    order, chaos, none = EXPECTED_LABELS[zone]
    assert g.zone_label_fr(zone, "ORDER") == order
    assert g.zone_label_fr(zone, "CHAOS") == chaos
    assert g.zone_label_fr(zone, None) == none
    assert g.zone_label_fr(zone, "order") == order
    assert g.zone_label_fr(zone, "???") == none


def test_zone_label_fr_invalid():
    assert g.zone_label_fr(None, "ORDER") == ""
    assert g.zone_label_fr("nowhere", None) == ""  # type: ignore[arg-type]
    assert g.zone_label_fr("BLUE_JUNGLE_TOP", "ORDER") == "dans ta jungle du haut"  # type: ignore[arg-type]


def test_distances_and_units():
    assert g.dist((0, 0), (0.3, 0.4)) == pytest.approx(0.5)
    assert g.dist((0.1, 0.1), (0.1, 0.1)) == 0.0
    assert math.isinf(g.dist((float("nan"), 0), (0, 0)))
    assert math.isinf(g.dist(None, (0, 0)))  # type: ignore[arg-type]
    assert g.to_game_units(1.0) == pytest.approx(g.MAP_GAME_UNITS)
    assert g.to_game_units(0.12) == pytest.approx(0.12 * 14870.0)
    u, v = g.game_to_uv(7435, 7490)
    assert (u, v) == pytest.approx((0.5, 0.5))
    assert g.uv_to_game(u, v) == pytest.approx((7435, 7490))
    assert g.distance_to_lane(0.082, 0.4, "top") == pytest.approx(0.0, abs=1e-9)
    assert g.distance_to_lane(0.5, 0.5, "mid") == pytest.approx(0.0, abs=1e-9)
    assert g.distance_to_lane(0.5, 0.5, "bot") > 0.3
    assert math.isinf(g.distance_to_lane(0.5, 0.5, "jungle"))


def test_fountain():
    assert g.in_fountain(0.03, 0.97, "ORDER")
    assert not g.in_fountain(0.03, 0.97, "CHAOS")
    assert g.in_fountain(0.97, 0.03, None)
    assert not g.in_fountain(0.3, 0.7, None)


def test_debug_image_and_thread_safe_rebuild():
    img = g.zone_debug_image(None, 64)
    assert img.shape == (64, 64, 3) and img.dtype == np.uint8
    bg = np.full((100, 100, 4), 128, np.uint8)
    img2 = g.zone_debug_image(bg, 50)
    assert img2.shape == (50, 50, 3)
    g.reset_zone_cache()
    results: list[Zone] = []

    def worker() -> None:
        results.append(g.classify_zone(0.2, 0.5))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert results == [Zone.BLUE_JUNGLE_TOP] * 8
    g.reset_zone_cache()
    g.warm_up()
    assert g.classify_zone(0.2, 0.5) == Zone.BLUE_JUNGLE_TOP


def test_zone_name_fr():
    assert g.zone_name_fr(Zone.TOP_LANE, "ORDER") == "voie du haut"
    assert g.zone_name_fr(Zone.MID_LANE, None) == "voie du milieu"
    assert g.zone_name_fr(Zone.BOT_LANE, None) == "voie du bas"
    assert g.zone_name_fr(Zone.TOP_RIVER, None) == "rivière du haut"
    assert g.zone_name_fr(Zone.BLUE_JUNGLE_BOT, "ORDER") == "ta jungle du bas"
    assert g.zone_name_fr(Zone.BLUE_JUNGLE_BOT, "CHAOS") == "jungle ennemie du bas"
    assert g.zone_name_fr(Zone.RED_JUNGLE_TOP, None) == "jungle rouge du haut"
    assert g.zone_name_fr(Zone.BLUE_BASE, "CHAOS") == "base ennemie"
    assert g.zone_name_fr(Zone.BLUE_BASE, None) == "base bleue"
    assert g.zone_name_fr(Zone.RED_BASE, "CHAOS") == "ta base"
    assert g.zone_name_fr(None, None) == ""
    for z in Zone:
        assert g.zone_name_fr(z, "ORDER") and not g.zone_name_fr(z, "ORDER").startswith("dans")
