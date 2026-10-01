"""Play-rating badge animation (fx_render.py) + its window manager / sounds (fx_overlay.py)."""

from __future__ import annotations

import sys
import wave

import numpy as np

from treeaicoach import fx_overlay, fx_render
from treeaicoach.config import Config
from treeaicoach.plays import CLASSES, TITLE_FR, Play


def test_anim_overshoot_shine_and_end():
    scales = [fx_render.anim_state(a)["scale"] for a in np.arange(0.0, 0.5, 0.01)]
    assert scales[0] < 0.6 and max(scales) > 1.05 and abs(scales[-1] - 1.0) < 1e-6
    assert fx_render.anim_state(0.0)["opacity"] == 0.0
    assert any(fx_render.anim_state(a)["shine"] >= 0 for a in np.arange(0.0, 1.0, 0.05))
    end = fx_render.anim_state(fx_render.DURATION["big"] - 0.05)
    assert end["opacity"] < 0.3 and end["dy"] < 0
    assert fx_render.anim_state(fx_render.DURATION["big"]) is None
    assert fx_render.anim_state(float("nan")) is None
    assert all(fx_render.anim_state(a, "small")["shine"] < 0 for a in (0.4, 0.6))
    n = len(fx_render.frame_times("big"))
    assert 1.5 * 30 <= n <= 2.5 * 30          # ~1.5-2.5 s at 30 fps


def test_frames_have_fixed_size_and_content():
    for cls in CLASSES:
        shape = None
        for age in (0.05, 0.3, 1.0, 2.1):
            img = fx_render.render_frame(cls, TITLE_FR[cls], "Raison courte en français.", age)
            assert img is not None and img.dtype == np.uint8 and img.shape[2] == 4
            shape = shape or img.shape
            assert img.shape == shape
            assert np.all(img[..., :3].max(axis=2) <= img[..., 3])   # premultiplied
        mid = fx_render.render_frame(cls, TITLE_FR[cls], "x", 1.0)
        assert (mid[..., 3] > 200).sum() > 1000
    assert fx_render.render_frame("great", "EXCELLENT", "x", 5.0) is None


def test_badge_colours_per_class():
    def icon_rgb(cls):
        img = fx_render.render_frame(cls, TITLE_FR[cls], "x", 1.2)
        ys, xs = np.nonzero(img[..., 3] > 250)
        y, x = int(ys.mean()), int(xs.min()) + 4
        return tuple(int(v) for v in img[y, x, 2::-1])
    seen = {cls: icon_rgb(cls) for cls in ("brilliant", "great", "inaccuracy", "mistake", "blunder")}
    assert len(set(seen.values())) == 5
    assert seen["blunder"][0] > 200 and seen["blunder"][1] < 100        # red
    assert seen["brilliant"][1] > seen["brilliant"][0]                   # teal


def test_layer_placement():
    scr, mm = (0, 0, 1920, 1080), (1640, 800, 280, 280)
    x, y, w, h = fx_render.fx_layer_rect(scr, mm, "top_center")
    assert abs((x + w / 2) - 960) <= 1 and 150 < y < 300
    x, y, w, h = fx_render.fx_layer_rect(scr, mm, "minimap")
    assert x + w <= mm[0] and y + h <= 1080 and x >= 0
    xs, ys, ws, hs = fx_render.fx_layer_rect(scr, mm, "top_center", size="small")
    assert xs + ws <= mm[0]                                              # small badges: by the minimap
    x, y, w, h = fx_render.fx_layer_rect((0, 0, 1280, 720), (0, 520, 200, 200), "minimap")
    assert x >= 200 and x + w <= 1280
    big = fx_render.fx_layer_rect((0, 0, 3840, 2160), None)
    assert big[2] > fx_render.fx_layer_rect(scr, None)[2]


def test_render_play_frame_and_speed():
    import time

    p = Play("brilliant", "steal", "Tu as volé le Baron !", 0.0, 1600.0, "k", size="big")
    t0 = time.perf_counter()
    for a in fx_render.frame_times("big"):
        fx_render.render_play_frame(p, a)
    per = (time.perf_counter() - t0) / len(fx_render.frame_times("big"))
    assert per < 0.02                                                    # far under 33 ms per frame
    small = Play("brilliant", "outplay", "x", 0.0, 0.0, "k2", size="small")
    assert fx_render.render_play_frame(small, 0.5).shape[1] < fx_render.render_play_frame(p, 0.5).shape[1]


def test_tones_and_sound_policy(tmp_path):
    for cls in CLASSES:
        path = tmp_path / f"{cls}.wav"
        assert fx_overlay.make_tone_wav(path, cls)
        with wave.open(str(path)) as w:
            assert w.getframerate() == fx_overlay.SAMPLE_RATE and 0.05 < w.getnframes() / w.getframerate() < 0.6
    cfg = Config()
    assert fx_overlay.sound_wanted(cfg, "brilliant") and not fx_overlay.sound_wanted(cfg, "blunder")
    cfg.plays_sound_negative = True
    assert fx_overlay.sound_wanted(cfg, "blunder")
    cfg.plays_sound = False
    assert not fx_overlay.sound_wanted(cfg, "brilliant")


def test_playfx_noop_off_windows():
    fx = fx_overlay.PlayFx(Config(), lambda: (None, None))
    fx.push(Play("great", "solo_kill", "x", 0.0, 0.0, "k"))
    assert len(fx.pushed) == 1
    if sys.platform != "win32":
        assert not fx.ok and fx._thread is None
    fx.stop()
