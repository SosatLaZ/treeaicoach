"""Ward guide: camera rectangle finder / projection (camera_proj.py), ward guide lifecycle and
ward-placed detection (ward_guide.py), game-view marker rendering (overlay_render.py) and wiring."""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from treeaicoach import camera_proj as cp
from treeaicoach import overlay_render as orr
from treeaicoach import ward_guide as wg
from treeaicoach import wards
from treeaicoach.config import Config
from treeaicoach.render import ChampionSprite, MinimapRenderer, Scene

FIX = Path(__file__).parent / "fixtures"
SCREEN = (0, 0, 1920, 1080)
MINIMAP = (1655, 815, 255, 255)
WARD_ICONS = ("minimap_ward_green_full.png", "minimap_ward_blue_full.png", "minimap_ward_pink_friendly.png")


@pytest.fixture(scope="module")
def renderer() -> MinimapRenderer:
    return MinimapRenderer()


def _jpeg(img: np.ndarray, q: int = 75) -> np.ndarray:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    assert ok
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def _cam(cu: float, cv_: float, w: float = 0.275, h: float = 0.155) -> tuple[float, float, float, float]:
    return (cu - w / 2, cv_ - h / 2, cu + w / 2, cv_ + h / 2)


# ======================================================================================
# camera_proj: finder
# ======================================================================================
def test_camera_finder_rendered_including_clipped(renderer):
    rng = random.Random(3)
    tex = renderer.textures()
    ok = n = 0
    for i in range(48):
        size = rng.choice([220, 256, 306, 360])
        w, h = rng.uniform(0.272, 0.279), rng.uniform(0.151, 0.158)
        # every 4th one leaves the map by one side (clipped rectangle)
        if i % 4 == 0:
            cu, cv_ = rng.choice([(0.05, 0.5), (0.95, 0.5), (0.5, 0.03), (0.5, 0.97)])
        else:
            cu, cv_ = rng.uniform(0.15, 0.85), rng.uniform(0.1, 0.9)
        cam = _cam(cu, cv_, w, h)
        sc = Scene(texture=rng.choice(tex), size=size, camera=cam, camera_px=rng.choice([None, 1, 2]),
                   minions=[(rng.random(), rng.random(), "ally") for _ in range(20)],
                   texts=[(rng.uniform(0.2, 0.8), rng.uniform(0.2, 0.8), "10:29")])
        img = renderer.render(sc)
        if i % 2:
            img = _jpeg(cv2.GaussianBlur(img, (0, 0), 0.6))
        r = cp.find_camera_rect(img)
        n += 1
        if r is not None and max(abs(r.center[0] - cu), abs(r.center[1] - cv_)) < 0.02:
            ok += 1
    assert ok >= 0.95 * n


def test_camera_finder_one_side_hidden_by_icons(renderer):
    from treeaicoach.champions import ChampionDB

    db = ChampionDB(cache_dir=Path("/nonexistent-treeaicoach-cache"))
    icons = [db.load_icon(c.alias) for c in list(db.all())[:4]]
    if any(i is None for i in icons):
        pytest.skip("champion icons missing")
    cu, cv_ = 0.55, 0.5
    cam = _cam(cu, cv_)
    # two icons sitting on the left side of the rectangle
    champs = [ChampionSprite(cam[0], cv_ - 0.04, 0.047, "ally", icons[0]),
              ChampionSprite(cam[0], cv_ + 0.045, 0.047, "enemy", icons[1])]
    img = renderer.render(Scene(size=306, camera=cam, champions=champs))
    r = cp.find_camera_rect(img)
    assert r is not None and abs(r.center[0] - cu) < 0.02 and abs(r.center[1] - cv_) < 0.02


def test_camera_finder_no_false_positive(renderer):
    rng = random.Random(9)
    for _ in range(25):
        path = [(rng.random(), rng.random()) for _ in range(3)] if rng.random() < 0.5 else None
        img = renderer.render(Scene(texture=rng.choice(renderer.textures()), size=rng.choice([256, 306]), path=path,
                                    texts=[(0.4, 0.4, "1:24"), (0.6, 0.7, "3:20"), (0.3, 0.6, "10:29")],
                                    minions=[(rng.random(), rng.random(), "ally") for _ in range(30)]))
        assert cp.find_camera_rect(img) is None


def test_camera_finder_real_minimap_and_speed():
    img = cv2.imread(str(FIX / "real_minimap_306.png"))
    if img is None:
        pytest.skip("fixture missing")
    r = cp.find_camera_rect(img)
    # the player is in the top lane: the rectangle leaves the map by the top
    assert r is not None
    assert 0.12 < r.u0 < 0.17 and 0.39 < r.u1 < 0.44 and r.v0 < 0.0 < r.v1 < 0.16
    cp.find_camera_rect(img)
    t0 = time.perf_counter()
    for _ in range(20):
        cp.find_camera_rect(img)
    assert (time.perf_counter() - t0) / 20 < 0.006          # ~1 ms on a dev machine


@pytest.mark.parametrize("bad", [None, "x", np.zeros((10, 10, 3), np.uint8), np.zeros((100, 100), np.uint8),
                                 np.zeros((200, 200, 3), np.uint8), np.full((200, 200, 3), 255, np.uint8)])
def test_camera_finder_bad_inputs(bad):
    assert cp.find_camera_rect(bad) is None


def test_camera_tracker_smoothing_snap_and_hold():
    tr = cp.CameraTracker(hold_s=1.5)
    a = cp.CameraRect(*_cam(0.5, 0.5))
    assert tr.feed(a, 0.0).center == pytest.approx((0.5, 0.5))
    s = tr.feed(cp.CameraRect(*_cam(0.52, 0.5)), 0.1)        # small move: smoothed
    assert 0.5 < s.center[0] < 0.52
    s = tr.feed(cp.CameraRect(*_cam(0.2, 0.8)), 0.2)         # big jump (Space / click): snapped
    assert s.center == pytest.approx((0.2, 0.8))
    assert tr.feed(None, 1.0) is not None                    # held a while ...
    assert tr.current(1.6) is not None
    assert tr.feed(None, 2.0) is None and tr.current(2.0) is None     # ... then lost
    w, h = s.size
    assert w == pytest.approx(cp.CAM_W, abs=0.01) and h == pytest.approx(cp.CAM_H, abs=0.01)


# ======================================================================================
# camera_proj: projection
# ======================================================================================
def test_projection_plain_rectangle_k0():
    cam = _cam(0.5, 0.5)
    p = cp.CameraProjection(cam, SCREEN, persp=0.0)
    assert p.map_to_screen(cam[0], cam[1]) == pytest.approx((0, 0), abs=0.01)
    assert p.map_to_screen(cam[2], cam[3]) == pytest.approx((1920, 1080), abs=0.01)
    assert p.map_to_screen(0.5, 0.5) == pytest.approx((960, 540), abs=0.01)


def test_projection_perspective_sanity():
    cam = _cam(0.4, 0.3)
    p = cp.CameraProjection(cam, (100, 50, 1920, 1080))
    # monotonic: map right / down = screen right / down
    xs = [p.map_to_screen(u, 0.3)[0] for u in np.linspace(0.3, 0.5, 9)]
    ys = [p.map_to_screen(0.4, v)[1] for v in np.linspace(0.25, 0.35, 9)]
    assert all(b > a for a, b in zip(xs, xs[1:])) and all(b > a for a, b in zip(ys, ys[1:]))
    # the ground under the screen centre is below the middle of the rectangle (near half magnified)
    gu, gv = p.screen_to_map(100 + 960, 50 + 540)
    assert gu == pytest.approx(0.4, abs=1e-6)
    assert cam[1] + 0.55 * (cam[3] - cam[1]) < gv < cam[1] + 0.7 * (cam[3] - cam[1])
    # the bottom edge is narrower than the top edge on the ground: same map width -> wider on screen
    w_top = p.map_to_screen(0.45, cam[1])[0] - p.map_to_screen(0.35, cam[1])[0]
    w_bot = p.map_to_screen(0.45, cam[3])[0] - p.map_to_screen(0.35, cam[3])[0]
    assert w_bot > 1.3 * w_top
    # round trip
    for (u, v) in [(0.35, 0.27), (0.42, 0.33), (0.39, 0.3)]:
        x, y = p.map_to_screen(u, v)
        assert p.screen_to_map(x, y) == pytest.approx((u, v), abs=1e-6)
    assert p.cam_center == pytest.approx((0.4, 0.3))


def test_projection_visibility_direction_and_helpers():
    cam = cp.CameraRect(*_cam(0.5, 0.5))
    p = cp.make_projection(cam, SCREEN)
    assert p is not None
    assert p.is_visible(0.5, 0.5)
    assert not p.is_visible(0.9, 0.5) and not p.is_visible(0.5, 0.1)
    x, y = p.map_to_screen(0.5, 0.56)
    assert not p.is_visible(0.5, 0.56, exclude=[(x - 5, y - 5, 10, 10)])
    dx, dy = p.direction(0.9, 0.5)
    assert dx == pytest.approx(1.0) and dy == pytest.approx(0.0, abs=1e-9)
    dx, dy = p.direction(0.5, 0.1)
    assert dy == pytest.approx(-1.0)
    assert cp.make_projection(None, SCREEN) is None and cp.make_projection(cam, None) is None
    assert cp.make_projection(cam, (0, 0, 0, 0)) is None
    assert cp.map_to_screen(cam, SCREEN, 0.5, 0.5) == pytest.approx(p.map_to_screen(0.5, 0.5))
    assert cp.map_to_screen(None, SCREEN, 0.5, 0.5) is None
    assert cp.is_visible(cam, SCREEN, 0.5, 0.5) and not cp.is_visible(None, SCREEN, 0.5, 0.5)
    hx, hy, hw, hh = cp.hud_bar_rect(SCREEN)
    assert 0 < hx < 960 < hx + hw < 1920 and hy + hh >= 1080 and hh < 160
    assert cp.walk_seconds((0.0, 0.0), (0.1, 0.0)) == pytest.approx(0.1 * cp.MAP_UNITS / cp.MOVE_SPEED)


# ======================================================================================
# ward_guide: placed detection
# ======================================================================================
def test_ward_blobs_detects_ward_not_minion_nor_icon(renderer):
    det = wg.WardPlacedDetector()
    uv = wards.SPOT_BY_ID["pixel_top"].uv
    for icon in WARD_ICONS:
        img = renderer.render(Scene(size=306, wards=[(uv[0] + 0.01, uv[1] - 0.01, icon)]))
        blobs = det.blobs(img, uv)
        assert blobs and min(math.hypot(b[0] - uv[0] - 0.01, b[1] - uv[1] + 0.01) for b in blobs) < 0.01
        assert det.blobs(img, uv, avoid=[(uv[0] + 0.02, uv[1])]) == []        # under a champion icon
    plain = renderer.render(Scene(size=306))
    assert det.blobs(plain, uv) == []
    minion = renderer.render(Scene(size=306, minions=[(uv[0], uv[1] + 0.01, "ally")]))
    assert det.blobs(minion, uv) == []                                           # minion dots are bigger
    assert det.blobs(None, uv) is None and det.blobs(np.zeros((20, 20, 3), np.uint8), uv) is None


@dataclass
class _Spot:
    id: str


@dataclass
class _Pick:
    spot: _Spot
    uv: tuple
    label: str = "buisson pixel"


@dataclass
class _Advice:
    picks: tuple
    reason: str
    key: str
    until: float
    text: str | None = None


def _picks(*ids: str) -> list[_Pick]:
    return [_Pick(_Spot(i), wards.SPOT_BY_ID[i].uv, wards.SPOT_BY_ID[i].label) for i in ids]


def _cfg(**kw: Any) -> Config:
    return replace(Config(), **kw)


def test_guide_placed_on_new_static_ward(renderer):
    m = wg.WardGuideManager(_cfg())
    uv = wards.SPOT_BY_ID["pixel_top"].uv
    tex = renderer.textures()[0]
    plain = renderer.render(Scene(texture=tex, size=306))
    warded = renderer.render(Scene(texture=tex, size=306, wards=[(uv[0] + 0.012, uv[1], WARD_ICONS[0])]))
    assert m.start(0.0, _picks("pixel_top"), "hotkey") == 1
    events: list[str] = []
    t = 0.0
    for i in range(20):
        t = i * 0.5
        events += m.update(t, minimap_bgr=plain if t < 3.0 else warded)
        if "placed" in events:
            break
    assert "placed" in events and t <= 3.0 + wg.PLACE_HITS / wg.CHECK_HZ + 0.01
    g = m.guides(t)[0]
    assert g.placed and g.until == pytest.approx(t + wg.CONFIRM_S)
    mm = m.minimap_guides(t)
    assert mm[0].done and mm[0].color == "safe" and mm[0].label == wg.DONE_LABEL
    assert m.update(t + wg.CONFIRM_S + 0.1) == ["end"] and not m.active(t + wg.CONFIRM_S + 0.1)


def test_guide_not_placed_by_static_map_or_walking_minions(renderer):
    m = wg.WardGuideManager(_cfg())
    uv = wards.SPOT_BY_ID["river_bot_lane"].uv
    tex = renderer.textures()[0]
    m.start(0.0, _picks("river_bot_lane"), "hotkey")
    for i in range(36):
        t = i * 0.5
        # one allied minion walking through the spot (~0.011 / check), one ward already there from the start
        mins = [(uv[0] - 0.08 + 0.011 * i, uv[1] + 0.005, "ally")]
        img = renderer.render(Scene(texture=tex, size=306, minions=mins,
                                    wards=[(uv[0] - 0.02, uv[1] - 0.02, WARD_ICONS[1])]))
        assert "placed" not in m.update(t, minimap_bgr=_jpeg(img, 80) if i % 2 else img)


def test_guide_lifecycle_advice_hotkey_and_limits():
    m = wg.WardGuideManager(_cfg(), sound=lambda: None)
    adv = _Advice(tuple(_picks("pixel_top", "pixel_bot", "tri_own")), "base", "ward:base:pixel_top", 25.0)
    assert m.update(0.0, adv) == ["start"]
    assert len(m.guides(0.0)) == wg.MAX_GUIDES                      # 2 markers max
    assert m.update(1.0, adv) == []                                  # same advice: not restarted
    assert m.guides(1.0)[0].until == pytest.approx(wg.GUIDE_S)      # <= 20 s
    assert m.update(wg.GUIDE_S + 0.1) == ["end"]
    assert m.minimap_guides(wg.GUIDE_S + 0.1) == []
    # hotkey: recommend() called on the next update, debounced
    calls = []
    rec = lambda: calls.append(1) or _picks("baron_front")             # noqa: E731
    assert m.request(30.0) and not m.request(30.5)
    assert m.update(30.6, recommend=rec) == ["start"] and calls == [1]
    assert m.guides(30.6)[0].reason == "hotkey"
    m.reset()
    assert not m.active(30.7)


def test_guide_skill_levels_and_toggles():
    adv_periodic = _Advice(tuple(_picks("pixel_top")), "periodic", "ward:periodic:pixel_top", 18.0)
    adv_obj = _Advice(tuple(_picks("dragon_front")), "objective", "ward:objective:dragon_front", 40.0)
    expert = wg.WardGuideManager(_cfg(skill_level="expert"))
    assert expert.update(0.0, adv_periodic) == [] and not expert.active(0.0)
    assert expert.update(1.0, adv_obj) == ["start"]
    assert expert.request(2.0) and expert.update(2.1, recommend=lambda: _picks("pixel_bot")) == ["start"]
    off = wg.WardGuideManager(_cfg(ward_guide=False))
    assert off.update(0.0, adv_obj) == [] and not off.request(0.0)
    on = wg.WardGuideManager(_cfg())
    on.update(0.0, adv_obj)
    on.apply_config(_cfg(ward_guide=False))
    assert not on.active(0.1)


def _manager_with_camera(cu: float, cv_: float, **cfg: Any) -> wg.WardGuideManager:
    m = wg.WardGuideManager(_cfg(**cfg))
    m.camera.feed(cp.CameraRect(*_cam(cu, cv_)), 0.0)
    return m


def test_world_markers_ground_edge_done_and_exclusions():
    spot = wards.SPOT_BY_ID["pixel_top"].uv
    # camera centred a bit above the spot: ground marker on screen
    m = _manager_with_camera(spot[0], spot[1] - 0.02)
    m.start(0.0, _picks("pixel_top"), "base")
    ms = m.world_markers(0.5, SCREEN, MINIMAP, me_uv=spot)
    assert len(ms) == 1 and ms[0].kind == "ground" and ms[0].label == wg.LABEL and ms[0].hint == ""
    assert 0 < ms[0].x < 1920 and 0 < ms[0].y < 1080
    # far away camera (bottom-right): edge arrow on the border, pointing up-left, with the walking time
    m2 = _manager_with_camera(0.8, 0.85)
    m2.start(0.0, _picks("pixel_top", "river_top_lane"), "periodic")
    ms2 = m2.world_markers(0.5, SCREEN, MINIMAP, me_uv=(0.8, 0.85))
    assert [k.kind for k in ms2] == ["edge", "edge"]
    hud = cp.hud_bar_rect(SCREEN)
    for k in ms2:
        assert k.dx < 0 and k.dy < 0 and k.sub.startswith("≈ ") and k.sub.endswith(" s")
        assert 0 <= k.x <= 1920 and 0 <= k.y <= 1080
        for r in (MINIMAP, hud):
            assert not (r[0] <= k.x <= r[0] + r[2] and r[1] <= k.y <= r[1] + r[3])
    # spot projected under the minimap: never a ground marker there (edge arrow instead)
    mm_uv = None
    proj = cp.make_projection(m2.camera.current(0.5), SCREEN)
    for u in np.linspace(0.75, 1.0, 26):
        for v in np.linspace(0.8, 1.0, 21):
            x, y = proj.map_to_screen(u, v)
            if MINIMAP[0] + 30 < x < MINIMAP[0] + MINIMAP[2] - 30 and MINIMAP[1] + 30 < y < 1060:
                mm_uv = (float(u), float(v))
                break
        if mm_uv:
            break
    assert mm_uv is not None
    m3 = _manager_with_camera(0.8, 0.85)
    m3.start(0.0, [_Pick(_Spot("x"), mm_uv)], "hotkey")
    assert [k.kind for k in m3.world_markers(0.5, SCREEN, MINIMAP)] == ["edge"]
    # beginner: a short text hint; placed: "done" marker; ward_world off / no camera: nothing
    beg = _manager_with_camera(spot[0], spot[1] - 0.02, skill_level="debutant")
    beg.start(0.0, _picks("pixel_top"), "objective")
    hint = beg.world_markers(0.5, SCREEN, MINIMAP)[0].hint
    assert "touche 4" in hint and "objectif" in hint
    g = beg.guides(0.5)[0]
    g.placed_t, g.until = 1.0, 1.0 + wg.CONFIRM_S
    done = beg.world_markers(1.2, SCREEN, MINIMAP)
    assert done[0].kind == "done" and done[0].label == wg.DONE_LABEL
    off = _manager_with_camera(spot[0], spot[1], ward_world=False)
    off.start(0.0, _picks("pixel_top"), "base")
    assert off.world_markers(0.5, SCREEN, MINIMAP) == [] and off.minimap_guides(0.5)
    nocam = wg.WardGuideManager(_cfg())
    nocam.start(0.0, _picks("pixel_top"), "base")
    assert nocam.world_markers(0.5, SCREEN, MINIMAP) == []
    assert m.world_markers(0.5, None) == []


def test_camera_search_only_while_a_guide_is_active(renderer, monkeypatch):
    calls = []
    real = cp.find_camera_rect
    monkeypatch.setattr(cp, "find_camera_rect", lambda img: calls.append(1) or real(img))
    m = wg.WardGuideManager(_cfg())
    img = renderer.render(Scene(size=256, camera=_cam(0.5, 0.5)))
    for i in range(5):
        m.update(i * 0.1, minimap_bgr=img)
    assert calls == []
    m.start(1.0, _picks("pixel_top"), "hotkey")
    m.update(1.1, minimap_bgr=img)
    assert calls == [1] and m.camera.current(1.1) is not None


def test_edge_point_inside_screen_and_off_exclusions():
    hud = cp.hud_bar_rect(SCREEN)
    for ang in np.linspace(0, 2 * math.pi, 37):
        dx, dy = math.cos(ang), math.sin(ang)
        x, y = wg.edge_point(SCREEN, dx, dy, 70, [MINIMAP, hud])
        assert 0 <= x <= 1920 and 0 <= y <= 1080
        for r in (MINIMAP, hud):
            assert not (r[0] < x < r[0] + r[2] and r[1] < y < r[1] + r[3])


# ======================================================================================
# overlay_render: game-view markers
# ======================================================================================
def _marker(kind: str, **kw: Any) -> wg.WorldMarker:
    base = dict(x=900.0, y=500.0, age=2.0, left=10.0)
    if kind == "edge":
        base.update(x=1880.0, y=400.0, dx=1.0, dy=0.0, sub="≈ 8 s")
    if kind == "done":
        base.update(label=wg.DONE_LABEL, age=0.3, left=2.0)
    base.update(kw)
    return wg.WorldMarker(kind, **base)


@pytest.mark.parametrize("kind", ["ground", "edge", "done"])
@pytest.mark.parametrize("hint", ["", "Vision de ta voie · touche 4"])
def test_render_world_marker_shapes(kind, hint):
    img, ax, ay = orr.render_world_marker(_marker(kind, hint=hint), 1.0, 0.3)
    h, w = img.shape[:2]
    assert img.dtype == np.uint8 and img.shape[2] == 4
    assert 40 <= w <= 400 and 40 <= h <= 260 and 0 <= ax < w and 0 <= ay < h
    a = img[..., 3]
    assert a.max() >= 200 and 0.05 < (a > 0).mean() < 0.9
    assert (img[..., :3] <= a[..., None]).all()               # premultiplied
    big, _, _ = orr.render_world_marker(_marker(kind, hint=hint), 2.0, 0.3)
    assert big.shape[0] > 1.6 * h                              # scales with the screen


def test_render_world_marker_fades_and_bad_input():
    a_full = orr.render_world_marker(_marker("ground"), 1.0, 0.0)[0][..., 3].max()
    a_new = orr.render_world_marker(_marker("ground", age=0.05), 1.0, 0.0)[0][..., 3].max()
    a_end = orr.render_world_marker(_marker("ground", left=0.1), 1.0, 0.0)[0][..., 3].max()
    assert a_new < 0.4 * a_full and a_end < 0.3 * a_full
    img, _, _ = orr.render_world_marker(object(), 1.0)
    assert img.ndim == 3


def test_render_world_guides_inside_screen_and_off_minimap_hud():
    hud = cp.hud_bar_rect(SCREEN)
    markers = [_marker("ground", x=1700.0, y=830.0), _marker("edge", x=1000.0, y=1040.0, dx=0.0, dy=1.0),
               _marker("ground", x=100.0, y=100.0)]
    out = orr.render_world_guides(markers, SCREEN, 0.0, avoid=[MINIMAP, hud])
    assert len(out) == 2
    for img, x, y in out:
        h, w = img.shape[:2]
        assert 0 <= x and 0 <= y and x + w <= 1920 and y + h <= 1080
        for r in (MINIMAP, hud):
            assert not (x < r[0] + r[2] and r[0] < x + w and y < r[1] + r[3] and r[1] < y + h)
    assert orr.render_world_guides([], SCREEN) == []
    assert orr.render_world_guides([_marker("ground", x=float("nan"))], SCREEN) == []
    assert orr.place_world_patch(-50, 2000, 100, 80, SCREEN) == (0, 1000)


def test_minimap_layer_draws_done_ward_guide():
    st = orr.OverlayState(minimap_rect=MINIMAP, me_uv=(0.2, 0.8),
                          guides=[wg.GuideView("ward", (0.5, 0.5), "x", color="safe", done=True),
                                  wg.GuideView("ward", (0.3, 0.3), "y")])
    img = orr.render_minimap(st, 255, 255, show_frame=False)
    assert img[118:138, 118:138, 3].max() > 0 and img[71:83, 71:83, 3].max() > 0
    assert orr.OverlayState().world == []


# ======================================================================================
# Overlay / engine / config wiring
# ======================================================================================
class _Win:
    def __init__(self) -> None:
        self.visible = False
        self.updates: list = []

    def update(self, img: np.ndarray, x: int, y: int) -> None:
        self.visible = True
        self.updates.append((img.shape, x, y))

    def hide(self) -> None:
        self.visible = False


def test_overlay_world_windows_shown_only_with_markers():
    from treeaicoach import overlay as ov

    m = ov.OverlayManager(Config(), lambda: None)
    wins = {n: _Win() for n in ov.WORLD_WINDOWS}
    st = orr.OverlayState(minimap_rect=MINIMAP, screen_rect=SCREEN, world=[_marker("ground"), _marker("edge")])
    m._refresh_world(None, wins, st, Config(), False)
    assert all(w.visible for w in wins.values())
    m._refresh_world(None, wins, replace(st, world=[_marker("ground")]), Config(), False)
    assert wins["world0"].visible and not wins["world1"].visible
    m._refresh_world(None, wins, replace(st, world=[]), Config(), False)
    assert not any(w.visible for w in wins.values())
    m._refresh_world(None, wins, st, _cfg(ward_world=False), False)
    assert not any(w.visible for w in wins.values())
    m._refresh_world(None, wins, st, Config(), True)               # move mode: hidden
    assert not any(w.visible for w in wins.values())


def test_config_fields_and_hotkey():
    c = Config().validated()
    assert c.ward_guide and c.ward_world and not c.ward_sound and c.hotkey_ward == "F7"
    assert replace(c, hotkey_ward="f9").validated().hotkey_ward == ""      # clash with the jungler key


def test_engine_wiring_hotkey_and_overlay():
    from tests.test_engine import game_info, make_engine

    eng, _voice, clock = make_engine()
    eng._ensure_components()
    assert eng._ward_guide is not None
    assert eng._hotkey_bindings().get("F7") == eng.request_ward_guide
    assert eng.request_ward_guide() is False                            # not in game
    picks = eng._ward_recommend(game_info(300.0), eng._tracker)
    assert 1 <= len(picks) <= 2
    eng._ward_guide.start(0.0, picks, "hotkey")
    guides, world = eng._ward_overlay(0.5, eng._tactics, MINIMAP, SCREEN, None)
    assert [g.kind for g in guides].count("ward") == len(picks) and world == []   # no camera yet
    eng._ward_guide.camera.feed(cp.CameraRect(*_cam(*picks[0].uv)), 0.0)
    _g, world = eng._ward_overlay(0.5, eng._tactics, MINIMAP, SCREEN, None)
    assert world and world[0].kind == "ground"
    eng.apply_config(_cfg(ward_guide=False))
    guides, world = eng._ward_overlay(0.6, eng._tactics, MINIMAP, SCREEN, None)
    assert world == [] and not any(isinstance(g, wg.GuideView) for g in guides)
