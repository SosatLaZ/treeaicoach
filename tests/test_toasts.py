"""Toast banners (toasts.py): rendering shapes, animation, placement, queue."""

from __future__ import annotations

import numpy as np
import pytest

from treeaicoach import toasts as T
from treeaicoach.overlay_render import OverlayState, default_minimap_rect


@pytest.mark.parametrize("kind", list(T.KINDS) + ["bogus"])
def test_render_toast_shape_and_alpha(kind):
    img = T.render_toast(kind, "Solo kill", "Joli solo kill sur Darius !", None, 1.0, age=0.5)
    w, h = T.toast_size(1.0)
    assert img.dtype == np.uint8 and img.ndim == 3 and img.shape[2] == 4
    assert img.shape[1] > w and img.shape[0] > h          # glow margin
    a = img[..., 3]
    assert a.max() > 200 and a[0, 0] == 0                   # opaque panel, transparent corner
    assert np.all(img[..., :3] <= a[..., None])            # premultiplied


def test_render_toast_icon_scale_static():
    icon = np.zeros((64, 64, 4), np.uint8)
    icon[..., 0] = 200
    icon[..., 3] = 255
    a = T.render_toast("praise", "X", "Y", icon, 1.5)
    b = T.render_toast("praise", "X", "Y", icon, 1.0)
    assert a.shape[1] > b.shape[1]
    assert T.render_toast("praise", "TITLE ONLY", "", None).shape[2] == 4


def test_anim_curve():
    assert T.toast_anim(-1)[0] == 0.0 and T.toast_anim(T.DURATION_S + 0.1)[0] == 0.0
    assert T.toast_anim(0.01)[0] < 0.3 and T.toast_anim(0.01)[1] < 0       # sliding in from above
    assert T.toast_anim(1.5) == (1.0, 0.0)
    assert 0.0 < T.toast_anim(T.DURATION_S - 0.2)[0] < 1.0
    assert T.toast_anim(float("nan"))[0] == 0.0


def test_layer_rect_top_centre_never_over_minimap():
    for sw, sh in ((1920, 1080), (2560, 1440), (1280, 720), (3440, 1440)):
        scr = (0, 0, sw, sh)
        mm = default_minimap_rect(sw, sh)
        x, y, w, h = T.toast_layer_rect(scr, mm)
        assert 0 <= x and x + w <= sw and 0 <= y
        assert abs((x + w / 2) - sw / 2) <= 2                               # centred
        assert y + h < sh * 0.3                                             # top area, not the champion
        mx, my, mw, mh = mm
        assert not (x < mx + mw and mx < x + w and y < my + mh and my < y + h)
        # the Tab / KDA block at the top-right (~ last 18 % of the width) stays free
        assert x + w < sw * 0.82
    lw, lh = T.layer_size(T.scale_for_screen((0, 0, 1920, 1080)))
    assert T.render_toast_layer([], T.scale_for_screen((0, 0, 1920, 1080))).shape == (lh, lw, 4)


def test_layer_render_and_queue():
    q = T.ToastQueue(clock=lambda: 0.0)
    assert q.push("praise", "SOLO KILL", "a", t=0.0)
    assert [v.age for v in q.active(0.0)] == [0.0]                         # display starts when shown
    assert not q.push("praise", "SOLO KILL", "a", t=1.0)                  # dedupe
    assert q.push("warning", "ENNEMI AVANCÉ", "b", t=0.5, key="fed:Darius")
    assert q.push("insight", "TAB", "c", t=0.6)
    views = q.active(1.0)
    assert [v.toast.title for v in views] == ["SOLO KILL"]                  # one at a time
    layer = T.render_toast_layer(views, 1.0)
    assert layer[..., 3].max() > 200
    later = q.active(4.5)                                                    # first expired (4 s) -> next
    assert [v.toast.title for v in later] == ["ENNEMI AVANCÉ"] and later[0].age == 0.0
    assert q.active(20.0) == [] and len(q) == 0
    for i in range(20):
        q.push("insight", f"t{i}", t=30.0)
    assert len(q) <= T.MAX_QUEUED
    assert q.active(45.0) == []                                              # stale waiting toasts dropped
    q.reset()
    assert len(q) == 0


def test_overlay_state_has_toasts_field():
    st = OverlayState()
    assert st.toasts == []


# ---- « Hey coach » listening chip (ToastQueue.listen, VoiceAssistant.on_listen)
def test_listen_text_lines():
    assert T.listen_text("listening") == "J'écoute…"
    assert T.listen_text("listening", "suite") == "J'écoute la suite…"
    assert T.listen_text("transcript", " quel  objet ? ") == "Tu as dit : « quel objet ? »"
    assert T.listen_text("transcript", "") == "" and T.listen_text("idle") == ""


def test_listen_chip_takes_slot_then_toasts_resume():
    q = T.ToastQueue(clock=lambda: 0.0)
    q.listen("listening", "", t=0.0)
    assert q.push("insight", "IA", "Un conseil", t=0.1)
    v = q.active(0.5)
    assert [x.toast.kind for x in v] == [T.LISTEN_KIND] and v[0].toast.subtitle == "J'écoute…"
    q.listen("transcript", "quel objet je fais ?", t=2.0)
    v = q.active(3.0)
    assert v[0].toast.subtitle == "Tu as dit : « quel objet je fais ? »"
    q.listen("idle", "", t=3.5)                       # idle never cuts the transcript short
    assert q.active(5.0)[0].toast.kind == T.LISTEN_KIND
    assert [x.toast.kind for x in q.active(6.1)] == ["insight"]     # 4 s later: the waiting toast


def test_listen_chip_idle_clears_and_danger_wins():
    q = T.ToastQueue(clock=lambda: 0.0)
    q.listen("listening", "", t=0.0)
    q.listen("idle", "", t=1.0)
    assert q.active(1.1) == []
    q.listen("listening", "", t=2.0)
    q.push("danger", "Danger", "Jungler en haut", t=2.1)
    assert [x.toast.kind for x in q.active(2.2)] == ["danger"]
    q2 = T.ToastQueue(clock=lambda: 0.0)
    q2.listen("listening", "", t=0.0)
    assert q2.active(T.LISTEN_MAX_S + 0.1) == []      # a lost « idle » never leaves it on screen


def test_listen_chip_renders_in_layer():
    q = T.ToastQueue(clock=lambda: 0.0)
    q.listen("transcript", "on prend le drake ?", t=0.0)
    chip = T.render_listen_chip("Tu as dit : « on prend le drake ? »", 1.0)
    w, h = T.toast_size(1.0)
    assert chip.dtype == np.uint8 and chip.shape[1] > w and chip.shape[0] > h
    a = chip[..., 3]
    assert a.max() > 200 and a[0, 0] == 0 and np.all(chip[..., :3] <= a[..., None])
    rows = np.where(a.max(axis=1) > 0)[0]
    assert rows.max() - rows.min() < h                # compact: smaller than a toast
    layer = T.render_toast_layer(q.active(1.0), 1.0)
    assert layer.shape[:2] == T.layer_size(1.0)[::-1] and layer[..., 3].max() > 200


def test_engine_listen_chip_hook():
    from types import SimpleNamespace

    from treeaicoach.engine_coaching import CoachingMixin

    q = T.ToastQueue(clock=lambda: 0.0)
    eng = SimpleNamespace(_toasts=q, _cfg=SimpleNamespace(toasts_enabled=True), _clock=lambda: 5.0)
    CoachingMixin._listen_chip(eng, "listening", "")
    assert q.active(5.1)[0].toast.subtitle == "J'écoute…"
    eng._toasts = None
    CoachingMixin._listen_chip(eng, "listening", "")       # never raises
