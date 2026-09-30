"""Tests of treeaicoach.overlay_render (pure numpy / PIL renderers)."""

from __future__ import annotations

import numpy as np
import pytest

from treeaicoach import overlay_render as orr
from treeaicoach.fog_tracker import FogTracker


@pytest.fixture(scope="module")
def states():
    return orr.sample_states()


def _open_fog() -> FogTracker:
    tex = np.zeros((128, 128, 4), np.uint8)
    tex[..., 3] = 255
    return FogTracker(tex)


def assert_premultiplied(img: np.ndarray) -> None:
    assert img.dtype == np.uint8 and img.ndim == 3 and img.shape[2] == 4
    # premultiplied: every colour channel <= alpha (1 unit of rounding tolerated)
    assert (img[..., :3].astype(int) <= img[..., 3:4].astype(int) + 1).all()


# ---------------------------------------------------------------------------- pixel formats
def test_premultiplied_conversion_roundtrip():
    rgba = np.array([[[255, 0, 0, 255], [0, 255, 0, 128], [10, 20, 30, 0], [200, 100, 50, 64]]], np.uint8)
    bgra = orr.to_premultiplied_bgra(rgba)
    assert bgra[0, 0].tolist() == [0, 0, 255, 255]
    assert bgra[0, 1].tolist() == [0, 128, 0, 128]
    assert bgra[0, 2].tolist() == [0, 0, 0, 0]
    assert_premultiplied(bgra)
    back = orr.premultiplied_to_rgba(bgra)
    assert back[0, 0].tolist() == [255, 0, 0, 255]
    assert abs(int(back[0, 3, 0]) - 200) <= 4 and back[0, 3, 3] == 64
    rgb = np.zeros((2, 2, 3), np.uint8)
    assert orr.to_premultiplied_bgra(rgb)[..., 3].min() == 255
    with pytest.raises(ValueError):
        orr.to_premultiplied_bgra(np.zeros((4, 4), np.uint8))


def test_formatting_helpers():
    assert orr.fmt_seconds(23.7) == "23 s"
    assert orr.fmt_seconds(65) == "1:05"
    assert orr.fmt_seconds(None) == "?"
    assert orr.fmt_clock(440) == "7:20"
    assert orr.fmt_clock(3725) == "1:02:05"
    assert orr.fmt_clock(float("nan")) == "--:--"


def test_fonts_render_french_accents():
    f = orr.get_font(16, "bold")
    assert f is orr.get_font(16, "bold")          # cached
    w = orr.text_width("Héraut — rivière, SÛR", f)
    assert w > 50
    cv = orr.Canvas(200, 40)
    cv.text(5, 20, "Dragon ancestral : élevé", f, orr.GOLD_LIGHT)
    out = cv.to_bgra()
    assert out[..., 3].max() > 200
    assert orr.fit_text("x" * 300, f, 100).endswith("…") or orr.text_width(orr.fit_text("x" * 300, f, 100), f) <= 100


# ---------------------------------------------------------------------------- radar
def test_radar_sample_states(states):
    for name, st in states.items():
        img = orr.render_radar(st, 256, None, now=0.5)
        assert img.shape == (256, 256, 4), name
        assert_premultiplied(img)
        assert img[..., 3].mean() > 150, name           # a filled (mostly opaque) radar


def test_radar_shows_fog_region_in_red(states):
    st = states["danger"]
    with_fog = orr.render_radar(st, 256, None, now=0.0).astype(int)
    st2 = orr.OverlayState(**{**st.__dict__, "fogs": []})
    no_fog = orr.render_radar(st2, 256, None, now=0.0).astype(int)
    # the jungler's reachable region (around its last seen point 0.27, 0.23) is redder
    ys, xs = slice(30, 90), slice(45, 110)
    red_gain = (with_fog[ys, xs, 2] - with_fog[ys, xs, 1]).mean() - (no_fog[ys, xs, 2] - no_fog[ys, xs, 1]).mean()
    assert red_gain > 8


def test_radar_danger_ring_around_me():
    st = orr.OverlayState(me_uv=(0.5, 0.5), danger_radius=0.2, warn_radius=0.35, threat_level=2)
    img = orr.render_radar(st, 200, None, now=0.0).astype(int)
    ring = img[100, 100 + 40]                     # on the danger circle (0.2 * 200 px)
    far = img[100, 100 + 55]                      # between the two circles
    assert ring[2] - ring[1] > far[2] - far[1]


def test_radar_never_raises_on_junk():
    bad = orr.OverlayState(me_uv=(float("nan"), 2), warn_radius=float("inf"), danger_radius=-1,
                           threat_level=7, enemies=[orr.EnemyView(key="x", uv=("a", None), visible=True,
                                                                  icon=np.zeros((3, 3), np.uint8)),
                                                    orr.EnemyView(key="y", uv=(0.2, 0.2), last_seen_ago=-5)],
                           fogs=[None])
    img = orr.render_radar(bad, 128, np.zeros((4, 4), np.uint8), now=1.0)
    assert img.shape == (128, 128, 4)
    img = orr.render_radar(orr.OverlayState(), 3)
    assert img.shape == (64, 64, 4)


def test_radar_jungler_halo_pulses():
    ev = orr.EnemyView(key="LeeSin", alias="LeeSin", name="Lee Sin", visible=True, uv=(0.5, 0.5),
                       last_seen_ago=0.0, is_jungler=True)
    st = orr.OverlayState(enemies=[ev])
    a = orr.render_radar(st, 200, None, now=0.0)
    b = orr.render_radar(st, 200, None, now=orr.HALO_PERIOD_S * 0.5)
    assert np.abs(a.astype(int) - b.astype(int)).sum() > 1000


def test_radar_fog_label_timer(states):
    fog = _open_fog().simulate("LeeSin", "LeeSin", "Lee Sin", (0.5, 0.5), 23.0, is_jungler=True)
    st = orr.OverlayState(fogs=[fog], enemies=[orr.EnemyView(key="LeeSin", name="Lee Sin", uv=(0.5, 0.5),
                                                            last_seen_ago=23.0, is_jungler=True)])
    img = orr.render_radar(st, 256, None, now=0.0)
    assert_premultiplied(img)
    # zero-confidence estimate is still drawn without error
    old = _open_fog().simulate("LeeSin", "LeeSin", "Lee Sin", (0.5, 0.5), 65.0, is_jungler=True)
    assert old.confidence == 0.0
    orr.render_radar(orr.OverlayState(fogs=[old]), 128, None, now=0.0)


# ---------------------------------------------------------------------------- HUD
def test_hud_sizes_and_format(states):
    for name, st in states.items():
        img = orr.render_hud(st, 340, now=0.2)
        w, h = orr.hud_size(st, 340)
        assert img.shape == (h, w, 4) == (img.shape[0], 340, 4), name
        assert_premultiplied(img)
        assert 180 < h < 420
    big = orr.render_hud(states["danger"], 510, now=0.2)
    assert big.shape[1] == 510 and big.shape[0] > orr.render_hud(states["danger"], 340).shape[0]


def test_hud_alert_fades_out(states):
    st = states["danger"]
    fresh = orr.OverlayState(**{**st.__dict__, "last_alert": ("Gank ! Lee Sin, recule !", 2, 0.1)})
    faded = orr.OverlayState(**{**st.__dict__, "last_alert": ("Gank ! Lee Sin, recule !", 2, 3.5)})
    gone = orr.OverlayState(**{**st.__dict__, "last_alert": ("Gank ! Lee Sin, recule !", 2, 9.0)})
    a, b, c = (orr.render_hud(s, 340, now=0.0).astype(int) for s in (fresh, faded, gone))
    assert a.shape == b.shape == c.shape
    assert np.abs(a - b).sum() > 0 and np.abs(b - c).sum() > 0


def test_hud_threat_colours():
    imgs = {lvl: orr.render_hud(orr.OverlayState(threat_level=lvl, threat_text=""), 340, now=0.0)
            for lvl in (0, 1, 2)}
    # threat bar row: dominant channel follows green / orange / red
    rows = {lvl: im[40:70, 40:300].reshape(-1, 4).astype(int).mean(axis=0) for lvl, im in imgs.items()}
    assert rows[0][1] > rows[0][2]            # green > red (BGRA)
    assert rows[1][2] > rows[1][0]            # orange: red > blue
    assert rows[2][2] > rows[2][1] + 30       # red


def test_hud_never_raises_on_junk():
    st = orr.OverlayState(threat_level=None, last_alert=("x",), objectives=[object(), None],  # type: ignore
                          game_time=float("nan"), enemies=[None] * 7, jungler_line="J" * 500, hint="é" * 300)
    img = orr.render_hud(st, 340)
    assert img.ndim == 3 and img.shape[1] == 340


# ---------------------------------------------------------------------------- flash
def test_flash_excludes_minimap_and_scales_with_intensity():
    ex = (1600, 800, 300, 260)
    img = orr.render_flash(1920, 1080, 1.0, ex, thickness=10)
    assert img.shape == (1080, 1920, 4)
    assert_premultiplied(img)
    assert img[0, 500, 3] > 200 and img[540, 0, 3] > 200 and img[1079, 900, 3] > 200
    assert img[540, 960, 3] == 0                                   # centre untouched
    assert not img[800:1060, 1600:1900].any()                      # never over the minimap
    half = orr.render_flash(1920, 1080, 0.5, ex, thickness=10)
    assert 0 < half[0, 500, 3] < img[0, 500, 3]
    assert not orr.render_flash(1920, 1080, 0.0, ex).any()
    assert orr.render_flash(0, -3, 1.0, None).shape == (1, 1, 4)


# ---------------------------------------------------------------------------- previews / demo
def test_preview_png_and_demo(tmp_path, states):
    data = orr.render_preview_png(states["danger"], tmp_path / "p.png", width=640, now=0.1)
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and (tmp_path / "p.png").stat().st_size == len(data)
    rgba = orr.render_preview(states["safe"], 480)
    assert rgba.shape == (270, 480, 4) and rgba[..., 3].min() == 255
    assert orr.radar_preview_rgba(states["safe"], 128).shape == (128, 128, 4)
    assert orr.hud_preview_rgba(states["safe"]).shape[1] == 340
    assert orr.main(["--demo", str(tmp_path / "demo")]) == 0
    names = {p.name for p in (tmp_path / "demo").iterdir()}
    assert {"radar_danger.png", "hud_late.png", "preview_warning.png", "radar_safe.png"} <= names
