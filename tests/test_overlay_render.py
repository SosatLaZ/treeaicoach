"""Tests of treeaicoach.overlay_render (pure numpy / PIL renderers)."""

from __future__ import annotations

from types import SimpleNamespace

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
        assert 0 < h < 300
    big = orr.render_hud(states["danger"], 510, now=0.2)
    assert big.shape[1] == 510 and big.shape[0] > orr.render_hud(states["danger"], 340).shape[0]


def _accent(img, k=1.0):
    """Mean BGRA of the HUD card's left accent bar."""
    x = int(round(5 * k + 6 * k))
    h = img.shape[0]
    return img[int(h * 0.35):int(h * 0.65), x - 1:x + 2].reshape(-1, 4).astype(int).mean(axis=0)


def test_hud_is_light_by_default_with_gauge_and_one_advice_line(states):
    st = states["danger"]
    light = orr.hud_size(st, 280)[1]
    detailed = orr.hud_size(orr.OverlayState(**{**st.__dict__, "hud_detailed": True}), 280)[1]
    assert light < detailed and light < 130                    # gauge/threat + advice + chips only
    calm = orr.OverlayState(**{**st.__dict__, "threat_level": 0, "tip": None, "insight": None})
    # gauge: ATTAQUE is green, SAFE red (accent bar + bars), stance is the fallback
    up = orr.render_hud(orr.OverlayState(**{**calm.__dict__, "gauge": 2}), 280, now=0.0)
    down = orr.render_hud(orr.OverlayState(**{**calm.__dict__, "gauge": -2}), 280, now=0.0)
    assert_premultiplied(up)
    g, r = _accent(up), _accent(down)
    assert g[1] > g[2] and r[2] > r[0] + 30                  # ATTAQUE green bar, SAFE amber (careful) bar
    # PRUDENT (from the stance) with nothing to say: no card (silence is a feature)
    assert not orr.hud_visible(orr.OverlayState(**{**calm.__dict__, "stance": "prudent"}), now=0.0)
    assert orr._gauge_step(orr.OverlayState(stance="agressif")) == 1
    assert orr._gauge_step(orr.OverlayState(gauge=9)) == 2 and orr._gauge_step(orr.OverlayState()) is None
    # danger: the danger word only (one thing), the advice line waits
    assert orr.hud_size(st, 280) == orr.hud_size(orr.OverlayState(**{**st.__dict__, "tip": "Farm"}), 280)
    # one advice line: the tip wins over the insight, same height
    st = orr.OverlayState(**{**st.__dict__, "threat_level": 0, "gauge": 0})
    t1 = orr.OverlayState(**{**st.__dict__, "insight": "Va top : Héraut dans 0:40", "tip": None})
    t2 = orr.OverlayState(**{**st.__dict__, "insight": "Va top : Héraut dans 0:40", "tip": "Pose une balise dans la rivière"})
    assert orr.hud_size(t1, 280) == orr.hud_size(t2, 280)
    assert np.abs(orr.render_hud(t1, 280, now=0.0).astype(int) - orr.render_hud(t2, 280, now=0.0).astype(int)).sum() > 0
    # long advice: one line (cut to the action)
    long = orr.OverlayState(**{**t2.__dict__, "tip": "mot " * 80})
    assert orr.hud_size(long, 280)[1] <= orr.hud_size(t2, 280)[1] + 20     # at most 2 lines
    # fade-in of a new advice line (~250 ms)
    t3 = orr.OverlayState(**{**t2.__dict__, "tip_since": 10.0})
    a0 = orr.render_hud(t3, 280, now=10.0)[..., 3].astype(int).sum()
    a1 = orr.render_hud(t3, 280, now=10.3)[..., 3].astype(int).sum()
    assert a0 < a1 and orr._fade(10.0, 10.3) == 1.0 and orr._fade(None, 0.0) == 1.0
    assert orr._fade(50.0, 10.0) == 1.0                        # other clock: no invisible text
    assert orr.OverlayState(stance="nimporte").stance and orr.render_hud(orr.OverlayState(stance="x"), 280).ndim == 3


def test_hud_chips_at_most_two():
    obj = SimpleNamespace(name="Dragon", key="dragon", next_spawn=300.0, alive=False)
    st = orr.OverlayState(game_time=250.0, objectives=[obj], tip="Pose une balise", item_hint="Achète Zhonya",
                          in_base=True, role_notice="Rôle détecté : MID (échange de voie)", hint="1 450 PO",
                          ai_counter="IA 3/5")
    chips = orr._hud_chips(st)
    assert [c[0] for c in chips] == ["objective", "item"]
    st2 = orr.OverlayState(**{**st.__dict__, "objectives": [], "in_base": False})
    assert [c[1] for c in orr._hud_chips(st2)] == ["Rôle : MID (échange de voie)", "1 450 PO"]
    assert orr._hud_chips(orr.OverlayState(ai_counter="IA 0/5")) == []      # AI budget: app only
    img = orr.render_hud(st, 280, now=0.0)
    assert_premultiplied(img) and img.shape[1] == 280


def test_hud_is_compact_and_shows_roles(states):
    st = orr.OverlayState(**{**states["danger"].__dict__, "hud_detailed": True})
    w, h = orr.hud_size(st, 280)
    assert w == 280 and h < 200                               # compact
    no_roles = orr.OverlayState(**{**st.__dict__, "roles": {},
                                   "enemies": [orr.EnemyView(**{**e.__dict__, "role": None}) for e in st.enemies]})
    assert orr.hud_size(no_roles, 280) == (w, h)
    a, b = orr.render_hud(st, 280, now=0.0).astype(int), orr.render_hud(no_roles, 280, now=0.0).astype(int)
    assert np.abs(a - b).sum() > 0                            # role badges drawn


def test_hud_threat_colours():
    imgs = {lvl: orr.render_hud(orr.OverlayState(threat_level=lvl, threat_text="", tip="Recule"), 340, now=0.0)
            for lvl in (0, 1, 2)}
    k = 340 / 280
    rows = {lvl: _accent(im, k) for lvl, im in imgs.items()}
    assert rows[1][2] > rows[1][0] + 30       # orange accent: red > blue (BGRA)
    assert rows[2][2] > rows[2][1] + 30       # red accent


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
    assert orr.hud_preview_rgba(states["safe"]).shape[1] == 280
    assert orr.minimap_preview_rgba(states["safe"], 128).shape == (128, 128, 4)
    radar = orr.render_preview(states["safe"], 480, mode="radar")
    assert radar.shape == rgba.shape and np.abs(radar.astype(int) - rgba.astype(int)).sum() > 0
    assert orr.main(["--demo", str(tmp_path / "demo")]) == 0
    names = {p.name for p in (tmp_path / "demo").iterdir()}
    assert {"radar_danger.png", "hud_late.png", "preview_warning.png", "radar_safe.png",
            "minimap_danger.png", "preview_radar_late.png"} <= names


# ---------------------------------------------------------------------------- minimap overlay
def _px(img, u, v):
    h, w = img.shape[:2]
    return img[int(v * h), int(u * w)].astype(int)


def test_minimap_overlay_is_transparent_and_subtle(states):
    for name, st in states.items():
        img = orr.render_minimap(st, 256, 256, now=0.2)
        assert img.shape == (256, 256, 4), name
        assert_premultiplied(img)
        alpha = img[..., 3] / 255.0
        # thin / translucent marks: the real minimap stays readable under them
        assert alpha.mean() < 0.12 and (alpha > 0.5).mean() < 0.08, name
    assert orr.render_minimap(states["danger"], 256, 256, now=0.2)[..., 3].any()
    safe = orr.render_minimap(states["safe"], 256, 256, now=0.2)
    assert (safe[..., 3] > 8).mean() < 0.12
    # rectangular minimap rects are supported
    assert orr.render_minimap(states["safe"], 300, 280).shape == (280, 300, 4)


def test_minimap_marks_enemies_red_allies_blue_me_teal():
    E = orr.EnemyView
    base = orr.OverlayState(me_uv=(0.2, 0.2),
                            enemies=[E("Darius", "Darius", "Darius", True, (0.7, 0.7), 0.0, role="TOP")],
                            allies=[E("Lux", "Lux", "Lux", True, (0.5, 0.2), 0.0, relation="ally", role="MIDDLE")])
    r = orr.MM_MARKER_R * 256
    # compact default: nothing on a visible, calm enemy (the game draws it)
    assert not orr.render_minimap(base, 256, 256, now=0.0).any()
    # detailed mode: the enemy only - no ally ring, no ring on me, no role tag
    base = orr.OverlayState(**{**base.__dict__, "hud_detailed": True})
    img = orr.render_minimap(base, 256, 256, now=0.0)
    assert img[int(0.7 * 256), int(0.7 * 256 + r), 3] > 100
    assert not img[int(0.2 * 256) - 30:int(0.2 * 256) + 30, int(0.5 * 256) - 30:int(0.5 * 256) + 30].any()
    assert not img[int(0.2 * 256) - 25:int(0.2 * 256) + 25, int(0.2 * 256) - 25:int(0.2 * 256) + 25].any()
    above = img[int(0.7 * 256 - r - 16):int(0.7 * 256 - r - 1), int(0.7 * 256) - 12:int(0.7 * 256) + 12]
    assert not above.any()                                    # no "TOP" tag
    roles = orr.render_minimap(orr.OverlayState(**{**base.__dict__, "show_roles": True}), 256, 256, now=0.0)
    assert roles[..., 3].sum() > img[..., 3].sum()
    st = orr.OverlayState(**{**base.__dict__, "show_allies": True})
    img = orr.render_minimap(st, 256, 256, now=0.0)

    def ring_px(u, v):
        return img[int(v * 256), int(u * 256 + r)].astype(int)   # on the ring, right of the centre

    b, g, rr, a = ring_px(0.7, 0.7)
    assert a > 100 and rr > b + 60                            # enemy: red
    b, g, rr, a = ring_px(0.5, 0.2)
    assert a > 80 and b > rr                                  # ally: blue
    b, g, rr, a = ring_px(0.2, 0.2)
    assert a > 100 and g > rr and b > rr                      # me: teal
    assert _px(img, 0.7, 0.7)[3] == 0                         # the champion icon itself is not covered
    # no danger / warn rings while safe
    assert img[int(0.2 * 256), int(0.2 * 256 + 0.22 * 256)][3] == 0


def test_minimap_threat_rings_only_when_threatened():
    st0 = orr.OverlayState(me_uv=(0.5, 0.5), threat_level=0)
    st2 = orr.OverlayState(me_uv=(0.5, 0.5), threat_level=2, warn_radius=0.3, danger_radius=0.15)
    a0 = orr.render_minimap(st0, 256, 256, now=0.0)
    a2 = orr.render_minimap(st2, 256, 256, now=0.0)
    assert a0[128, 128 + int(0.15 * 256), 3] == 0
    assert a2[128, 128 + int(0.15 * 256), 3] > 100            # danger ring
    assert a2[128, 128 + int(0.15 * 256), 2] > a2[128, 128 + int(0.15 * 256), 1] + 40
    assert a2[1, 128, 3] == 0                                  # no threat frame (decluttered)
    st1 = orr.OverlayState(me_uv=(0.5, 0.5), threat_level=1, warn_radius=0.3, danger_radius=0.15)
    assert not orr.render_minimap(st1, 256, 256, now=0.0).any()   # WARNING: no ring at all


def test_minimap_hidden_enemy_ghost_and_fog_timer():
    E = orr.EnemyView
    fog = _open_fog().simulate("LeeSin", "LeeSin", "Lee Sin", (0.5, 0.5), 10.0, is_jungler=True, game_time=400)
    st = orr.OverlayState(enemies=[E("LeeSin", "LeeSin", "Lee Sin", False, (0.5, 0.5), 10.0, True, role="JUNGLE"),
                                   E("Ahri", "Ahri", "Ahri", False, (0.2, 0.8), 12.0)],
                          fogs=[fog], show_last_seen=False)
    img = orr.render_minimap(st, 256, 256, now=0.0)
    assert_premultiplied(img)
    assert not img[190:218, 37:65].any()                      # default: no ghost for Ahri
    assert img[128, 128, 3] > 0                               # the jungler's fog timer
    # fog region of the jungler: soft outline, very light fill
    a = img[128 + 20, 128 - 20]
    assert a[3] < 60
    assert img[..., 3].max() > 0 and (img[..., 3] > 0).mean() < 0.5
    assert not orr.render_minimap(orr.OverlayState(**{**st.__dict__, "show_ghosts": True}), 256, 256,
                                  now=0.0)[190:218, 37:65].any()   # compact: show_ghosts needs detailed mode
    ghosts = orr.render_minimap(orr.OverlayState(**{**st.__dict__, "show_ghosts": True, "hud_detailed": True}),
                                256, 256, now=0.0)
    assert ghosts[204, 51, 3] > 0                              # Ahri ghost at her last position
    none = orr.render_minimap(orr.OverlayState(enemies=[E("Ahri", "Ahri", "Ahri", False, (0.2, 0.8), 90.0)],
                                               show_ghosts=True, hud_detailed=True), 256)
    assert not none.any()                                     # too old: nothing drawn
    last = orr.render_minimap(orr.OverlayState(enemies=[E("Ahri", "Ahri", "Ahri", False, (0.2, 0.8), 12.0)],
                                               show_last_seen=True, hud_detailed=True), 256)
    assert last[204, 51, 3] > 0                                # detailed: last seen position before the fog
    assert not orr.render_minimap(orr.OverlayState(enemies=[E("Ahri", "Ahri", "Ahri", False, (0.2, 0.8), 12.0)]),
                                  256).any()                  # compact: only the jungler's ghost


def test_role_tags():
    E = orr.EnemyView
    assert orr.role_tag(E("x", role="UTILITY")) == "SUP"
    assert orr.role_tag(E("x", role="bottom")) == "ADC"
    assert orr.role_tag(E("Ahri", "Ahri", "Ahri"), {"Ahri": "MIDDLE"}) == "MID"
    assert orr.role_tag(E("LeeSin", "LeeSin", "Lee Sin", is_jungler=True)) == "JGL"
    assert orr.role_tag(E("Kaisa", "Kaisa", "Kai'Sa")) == "KAIS"
    assert orr.role_tag(E("enemy?1", None, "enemy?1")) == "?"


def test_minimap_never_raises_on_junk():
    st = orr.OverlayState(me_uv=(float("nan"), 2.0), threat_level=None, enemies=[None, orr.EnemyView("a", uv=(9, 9),  # type: ignore
                          visible=True, velocity=(float("inf"), 0), approaching=True)], allies=[None],  # type: ignore
                          roles=None, fogs=[None])  # type: ignore
    img = orr.render_minimap(st, 0, -5)
    assert img.ndim == 3 and img.shape[2] == 4


def test_minimap_never_draws_portraits_and_rings_stay_outside_icons():
    """The minimap layer is captured: no portrait, and nothing inside the real icon (r < 0.05 S)."""
    E = orr.EnemyView
    icon = np.full((64, 64, 4), 255, np.uint8)
    st = orr.OverlayState(me_uv=(0.2, 0.2),
                          enemies=[E("Darius", "Darius", "Darius", True, (0.7, 0.7), 0.0, role="TOP", icon=icon),
                                   E("Ahri", "Ahri", "Ahri", False, (0.3, 0.75), 8.0, role="MIDDLE", icon=icon)],
                          allies=[E("Lux", "Lux", "Lux", True, (0.5, 0.2), 0.0, relation="ally", icon=icon)],
                          me_icon=icon)
    S = 306
    img = orr.render_minimap(st, S, S, now=0.0)
    assert orr.MM_MARKER_R >= 1.2 * 0.05 - 1e-9
    yy, xx = np.mgrid[0:S, 0:S] + 0.5
    for (u, v) in ((0.7, 0.7), (0.3, 0.75), (0.5, 0.2), (0.2, 0.2)):
        d = np.hypot(xx - u * S, yy - v * S)
        annulus = (d > 0.012 * S) & (d < 0.046 * S)            # the portrait area (minus a centre dot)
        assert img[..., 3][annulus].max() == 0, (u, v)


def test_minimap_show_frame_marks_an_empty_layer():
    empty = orr.render_minimap(orr.OverlayState(), 306, 306, now=0.0)
    assert not empty.any()
    framed = orr.render_minimap(orr.OverlayState(), 306, 306, now=0.0, show_frame=True)
    assert_premultiplied(framed)
    assert framed[2:16, 306 - 45:304, 3].max() > 100             # tiny "TreeAI" label, top-right
    assert framed[1, 1, 3] == 0 and framed[304, 304, 3] == 0     # no corner marks any more
    assert framed[153, 153, 3] == 0                              # centre untouched
    assert (framed[..., 3] > 0).mean() < 0.01
