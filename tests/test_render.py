"""Tests of treeaicoach.render (minimap rendering shared by training, demo and selftest)."""

from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np
import pytest

from treeaicoach import render as R


@pytest.fixture(scope="module")
def renderer() -> R.MinimapRenderer:
    return R.MinimapRenderer()


def _rgba_disc(n: int, rgb: tuple[int, int, int]) -> np.ndarray:
    yy, xx = np.mgrid[0:n, 0:n]
    img = np.zeros((n, n, 4), np.uint8)
    img[:, :, :3] = rgb
    img[:, :, 3] = np.where((xx + 0.5 - n / 2) ** 2 + (yy + 0.5 - n / 2) ** 2 <= (n / 2) ** 2, 255, 0)
    return img


# ----------------------------------------------------------------------------- constants


def test_contract_constants() -> None:
    assert set(R.RING_BGR) >= {"enemy", "ally", "self"}
    for col in R.RING_BGR.values():
        assert len(col) == 3 and all(0 <= c <= 255 for c in col)
    # enemy ring is red, ally ring is blue (BGR); self == ally (MINIMAP_FACTS.md)
    b, g, r = R.RING_BGR["enemy"]
    assert r > 150 and r > 2 * b and r > 2 * g
    b, g, r = R.RING_BGR["ally"]
    assert b > r and b > 150
    assert R.RING_BGR["self"] == R.RING_BGR["ally"]
    assert 0.0 < R.RING_FRAC < 0.3


def test_structures_and_camps_positions() -> None:
    assert len(R.STRUCTURES) == len(R.STRUCTURE_IDS) == 30
    kinds = [s[2] for s in R.STRUCTURES]
    assert kinds.count("turret") == 22 and kinds.count("inhibitor") == 6 and kinds.count("nexus") == 2
    for u, v, kind, team in R.STRUCTURES:
        assert 0.0 < u < 1.0 and 0.0 < v < 1.0
        assert team in ("ORDER", "CHAOS")
        # blue side (ORDER, bottom-left) is v > u ; CHAOS v < u
        if team == "ORDER":
            assert v > u
        else:
            assert v < u
    # symmetry of the map: each ORDER structure has a CHAOS mirror ~(1-u, 1-v)
    order = [(u, v, k) for u, v, k, t in R.STRUCTURES if t == "ORDER"]
    chaos = [(u, v, k) for u, v, k, t in R.STRUCTURES if t == "CHAOS"]
    for u, v, k in order:
        d = min(math.hypot(1 - u - cu, 1 - v - cv) for cu, cv, ck in chaos if ck == k)
        assert d < 0.06
    names = {c[2] for c in R.CAMPS}
    assert {"dragon", "baron", "blue", "red"} <= names
    for u, v, _ in R.CAMPS:
        assert 0.0 < u < 1.0 and 0.0 < v < 1.0
    # dragon pit is in the bottom half (u + v > 1), baron pit in the top half
    dragon = next(c for c in R.CAMPS if c[2] == "dragon")
    baron = next(c for c in R.CAMPS if c[2] == "baron")
    assert dragon[0] + dragon[1] > 1.0 > baron[0] + baron[1]


def test_game_to_uv() -> None:
    assert R.game_to_uv(0, 0) == (0.0, 1.0)
    u, v = R.game_to_uv(R.MAP_W, R.MAP_H)
    assert u == pytest.approx(1.0) and v == pytest.approx(0.0)


def test_turrets_on_lanes(renderer: R.MinimapRenderer) -> None:
    """Outer / inner turrets must sit on walkable ground of the texture (not in walls)."""
    tex = renderer.texture_rgba(R.DEFAULT_TEXTURE)
    if tex is None:
        pytest.skip("minimap texture not bundled")
    n = tex.shape[0]
    alpha = tex[:, :, 3]
    for sid, u, v, kind, _team in R.iter_structures(kind="turret"):
        x, y = int(u * n), int(v * n)
        win = alpha[max(0, y - 4):y + 5, max(0, x - 4):x + 5]
        assert win.max() > 128, f"{sid} at ({u:.3f}, {v:.3f}) is not on walkable ground"


# ----------------------------------------------------------------------------- load / blit


def test_load_rgba(tmp_path: Path) -> None:
    import cv2

    bgr = np.zeros((5, 7, 3), np.uint8)
    bgr[:, :, 2] = 200  # red in BGR
    p = tmp_path / "red é.png"   # unicode path
    ok, buf = cv2.imencode(".png", bgr)
    assert ok
    buf.tofile(str(p))
    rgba = R.load_rgba(p)
    assert rgba.shape == (5, 7, 4) and rgba.dtype == np.uint8
    assert tuple(rgba[0, 0]) == (200, 0, 0, 255)
    with pytest.raises(FileNotFoundError):
        R.load_rgba(tmp_path / "missing.png")
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not a png")
    with pytest.raises(ValueError):
        R.load_rgba(bad)


def test_alpha_blit_center_and_opacity() -> None:
    dst = np.zeros((20, 20, 3), np.uint8)
    src = np.zeros((4, 4, 4), np.uint8)
    src[:, :, 0] = 255   # red (RGBA)
    src[:, :, 3] = 255
    R.alpha_blit(dst, src, 10, 10)
    assert (dst[8:12, 8:12, 2] == 255).all()
    assert dst[:8].sum() == 0 and dst[12:].sum() == 0
    dst2 = np.zeros((20, 20, 3), np.uint8)
    R.alpha_blit(dst2, src, 10, 10, opacity=0.5)
    assert 120 <= int(dst2[10, 10, 2]) <= 135
    dst3 = np.zeros((20, 20, 3), np.uint8)
    R.alpha_blit(dst3, src, 10, 10, scale=2.0)
    assert (dst3[6:14, 6:14, 2] == 255).all() and dst3[5, 10, 2] == 0


@pytest.mark.parametrize("cx,cy", [(0, 0), (19.7, 3), (-3, 10), (10, 22), (-100, -100), (500, 5)])
def test_alpha_blit_clipping(cx: float, cy: float) -> None:
    dst = np.full((20, 20, 3), 7, np.uint8)
    src = np.full((9, 9, 4), 255, np.uint8)
    R.alpha_blit(dst, src, cx, cy)
    assert dst.shape == (20, 20, 3) and dst.dtype == np.uint8
    # only pixels covered by the (clipped) sprite changed
    x0, y0 = int(math.floor(cx - 4.5 + 0.5)), int(math.floor(cy - 4.5 + 0.5))
    expected = np.full((20, 20), 7, np.uint8)
    expected[max(0, y0):max(0, min(20, y0 + 9)), max(0, x0):max(0, min(20, x0 + 9))] = 255
    assert (dst[:, :, 0] == expected).all()


def test_alpha_blit_bad_inputs_never_raise() -> None:
    dst = np.zeros((10, 10, 3), np.uint8)
    src = np.full((3, 3, 4), 255, np.uint8)
    R.alpha_blit(dst, src, float("nan"), 5)
    R.alpha_blit(dst, src, 5, 5, scale=0)
    R.alpha_blit(dst, src, 5, 5, scale=-1)
    R.alpha_blit(dst, np.zeros((0, 0, 4), np.uint8), 5, 5)
    R.alpha_blit(dst, None, 5, 5)  # type: ignore[arg-type]
    R.alpha_blit(np.zeros((10, 10), np.uint8), src, 5, 5)
    assert dst.sum() == 0
    # grey / RGB sources are accepted
    R.alpha_blit(dst, np.full((3, 3), 200, np.uint8), 5, 5)
    R.alpha_blit(dst, np.full((3, 3, 3), 200, np.uint8), 2, 2)
    assert dst[5, 5, 0] == 200 and dst[2, 2, 1] == 200


# ----------------------------------------------------------------------------- icons


def test_draw_champion_icon_ring_and_portrait() -> None:
    dst = np.zeros((64, 64, 3), np.uint8)
    icon = _rgba_disc(64, (0, 200, 0))            # green portrait
    ring = R.RING_BGR["enemy"]
    R.draw_champion_icon(dst, 32.0, 32.0, 14.0, icon, ring, ring_frac=0.15)
    # centre = portrait (green), on the ring = ring colour, outside = untouched
    assert dst[32, 32, 1] > 150 and dst[32, 32, 2] < 60
    ring_px = dst[32, 32 + 13]
    assert abs(int(ring_px[2]) - ring[2]) < 40 and ring_px[1] < 90
    assert dst[32, 32 + 17].sum() == 0 and dst[2, 2].sum() == 0


def test_draw_champion_icon_grey_and_placeholder() -> None:
    dst = np.zeros((40, 40, 3), np.uint8)
    icon = _rgba_disc(32, (250, 30, 30))
    R.draw_champion_icon(dst, 20, 20, 10, icon, R.RING_BGR["ally"], grey=True)
    b, g, r = (int(c) for c in dst[20, 20])
    assert abs(b - g) <= 2 and abs(g - r) <= 2         # greyed portrait
    dst2 = np.zeros((40, 40, 3), np.uint8)
    R.draw_champion_icon(dst2, 20, 20, 10, None, R.RING_BGR["ally"])
    assert dst2[20, 20].sum() > 0                       # neutral disc


def test_draw_champion_icon_clipped_and_invalid() -> None:
    dst = np.zeros((30, 30, 3), np.uint8)
    icon = _rgba_disc(32, (200, 200, 200))
    for cx, cy in ((0, 0), (29.9, 15), (-8, 15), (15, 40), (-500, 3)):
        R.draw_champion_icon(dst, cx, cy, 9, icon, R.RING_BGR["enemy"])
    assert dst[0, 0].sum() > 0
    R.draw_champion_icon(dst, float("inf"), 3, 9, icon, R.RING_BGR["enemy"])
    R.draw_champion_icon(dst, 3, 3, -1, icon, R.RING_BGR["enemy"])
    R.draw_champion_icon(np.zeros((5, 5), np.uint8), 3, 3, 2, icon, (0, 0, 255))


# ----------------------------------------------------------------------------- renderer


def test_textures_listed(renderer: R.MinimapRenderer) -> None:
    tex = renderer.textures()
    assert tex == sorted(tex)
    for t in tex:
        assert t.startswith(R.TEXTURE_PREFIX) and t.endswith(".png")
    assert R.texture_variant("2dlevelminimap_ocean_baron2.png") == "ocean"
    assert R.texture_variant("weird.png") == "base"
    assert R.fog_for_texture("2dlevelminimap_hextech_baron1.png") == "fogofwaroverlay_srx_hextech.png"


@pytest.mark.parametrize("size", [8, 64, 171, 256, 300, 440])
def test_render_shape_dtype_sizes(renderer: R.MinimapRenderer, size: int) -> None:
    img = renderer.render(R.Scene(size=size))
    assert img.shape == (size, size, 3) and img.dtype == np.uint8


def test_render_all_textures(renderer: R.MinimapRenderer) -> None:
    textures = renderer.textures() or [R.DEFAULT_TEXTURE]
    for tex in textures:
        img = renderer.render(R.Scene(texture=tex, size=128, fog_alpha=0.0))
        assert img.shape == (128, 128, 3) and img.dtype == np.uint8
        assert 10 < img.mean() < 200, tex


def test_render_unknown_texture_and_bad_scene(renderer: R.MinimapRenderer) -> None:
    img = renderer.render(R.Scene(texture="does_not_exist.png", size=96))
    assert img.shape == (96, 96, 3)
    bad = R.Scene(size=64, vision=[("x", 1, 2), (float("nan"), 0.5, 0.1)],  # type: ignore[list-item]
                  minions=[(0.5,), (0.5, 0.5, "ally", "big")], wards=[None],  # type: ignore[list-item]
                  pings=[(0.5, 0.5)], camera=(0, 0, "a", 1))  # type: ignore[arg-type]
    img = renderer.render(bad)
    assert img.shape == (64, 64, 3)
    sc = R.Scene(size=64)
    sc.size = "abc"  # type: ignore[assignment]
    assert renderer.render(sc).shape == (256, 256, 3)


def test_render_missing_assets_dir(tmp_path: Path) -> None:
    r = R.MinimapRenderer(assets_dir=tmp_path)
    assert r.textures() == []
    sc = R.Scene(size=80, champions=[R.ChampionSprite(0.5, 0.5, 0.05, "enemy", None)],
                 minions=[(0.3, 0.3, "ally")], wards=[(0.4, 0.4, "minimap_ward_green_full")],
                 pings=[(0.6, 0.6, "caution")], camera=(0.1, 0.1, 0.4, 0.3))
    img = r.render(sc)
    assert img.shape == (80, 80, 3) and img.dtype == np.uint8


def test_fog_darkens_outside_vision(renderer: R.MinimapRenderer) -> None:
    base = renderer.render(R.Scene(size=200, fog_alpha=0.0, structures=False, camps=False))
    fog = renderer.render(R.Scene(size=200, fog_alpha=R.FOG_ALPHA_DEFAULT,
                                  vision=[(0.5, 0.5, 0.15)], structures=False, camps=False))
    b = base.astype(np.float32)
    f = fog.astype(np.float32)
    inside = (slice(90, 110), slice(90, 110))
    outside = (slice(0, 40), slice(80, 120))
    assert np.abs(f[inside] - b[inside]).mean() < 2.0
    ratio = f[outside].sum() / max(1.0, b[outside].sum())
    assert abs(ratio - (1.0 - R.FOG_ALPHA_DEFAULT)) < 0.05       # uniform multiply ~0.36


def test_champions_camera_minions_drawn(renderer: R.MinimapRenderer) -> None:
    empty = R.Scene(size=256, fog_alpha=0.0, structures=False, camps=False)
    base = renderer.render(empty)
    sc = R.Scene(size=256, fog_alpha=0.0, structures=False, camps=False,
                 champions=[R.ChampionSprite(0.3, 0.3, 0.05, "enemy", None),
                            R.ChampionSprite(0.7, 0.7, 0.05, "ally", None, recall=True)],
                 minions=[(0.5, 0.2, "enemy")], camera=(0.1, 0.6, 0.38, 0.76))
    img = renderer.render(sc)
    # enemy ring (red) at the ring radius
    px = img[int(0.3 * 256), int(0.3 * 256 + 0.05 * 256 - 1.5)]
    assert px[2] > 150 and px[0] < 100
    # ally ring blue
    px = img[int(0.7 * 256), int(0.7 * 256 - 0.05 * 256 + 1.5)]
    assert px[0] > 150
    # camera rectangle: white top edge
    assert (img[round(0.6 * 256), 40:90] > 220).all()
    # minion dot changed pixels
    assert np.abs(img[51, 128].astype(int) - base[51, 128].astype(int)).sum() > 60


def test_champion_partly_outside_map(renderer: R.MinimapRenderer) -> None:
    sc = R.Scene(size=128, champions=[R.ChampionSprite(0.0, 0.5, 0.05, "enemy", None),
                                      R.ChampionSprite(1.0, 1.0, 0.05, "ally", None),
                                      R.ChampionSprite(float("nan"), 0.5, 0.05, "ally", None)])
    img = renderer.render(sc)
    assert img.shape == (128, 128, 3)


def test_render_deterministic_and_thread_safe(renderer: R.MinimapRenderer) -> None:
    import threading

    sc = R.Scene(size=160, vision=[(0.3, 0.7, 0.1)],
                 champions=[R.ChampionSprite(0.3, 0.7, 0.045, "self", None)],
                 minions=[(0.2, 0.8, "ally")], camera=(0.2, 0.6, 0.47, 0.75))
    ref = renderer.render(sc)
    out: list[np.ndarray] = []

    def work() -> None:
        for _ in range(5):
            out.append(renderer.render(sc))

    threads = [threading.Thread(target=work) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(out) == 15
    for img in out:
        assert np.array_equal(img, ref)


def test_render_does_not_mutate_cache(renderer: R.MinimapRenderer) -> None:
    sc = R.Scene(size=100, fog_alpha=0.0, structures=False, camps=False)
    a = renderer.render(sc)
    a[:] = 0
    b = renderer.render(sc)
    assert b.sum() > 0


def test_extensions(renderer: R.MinimapRenderer) -> None:
    sc = R.Scene(size=200, destroyed=frozenset({"ORDER_top_outer", 3}), turret_plates={"ORDER_mid_outer": 2},
                 texts=[(0.5, 0.5, "1:17")], path=[(0.2, 0.8), (0.4, 0.6)],
                 sprites=[R.Sprite(0.5, 0.5, 0.05, "caution", layer="over")], my_team="CHAOS",
                 fog_texture="fogofwaroverlay.png", vision=[(0.5, 0.5, 0.2)])
    img = renderer.render(sc)
    assert img.shape == (200, 200, 3)
    for n in range(1, 10):
        name = renderer.plate_icon(n)
        if name is not None:
            assert renderer.icon(name) is not None
    assert renderer.plate_icon(0) is None and renderer.plate_icon(12) is None


def test_render_speed(renderer: R.MinimapRenderer) -> None:
    sc = R.Scene(size=256, vision=[(0.3, 0.7, 0.1), (0.5, 0.5, 0.08)],
                 champions=[R.ChampionSprite(0.1 * i, 0.5, 0.045, "enemy", None) for i in range(1, 9)],
                 minions=[(0.2 + 0.02 * i, 0.9, "ally") for i in range(6)], camera=(0.2, 0.6, 0.47, 0.75))
    renderer.render(sc)
    t0 = time.perf_counter()
    for _ in range(20):
        renderer.render(sc)
    dt = (time.perf_counter() - t0) / 20
    assert dt < 0.1   # generous: ~5 ms on a free core
