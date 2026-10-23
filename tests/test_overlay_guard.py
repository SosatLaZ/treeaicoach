"""Our own overlay marks must never reach the detector (overlay_guard + overlay + engine glue)."""

from __future__ import annotations

from types import SimpleNamespace as NS

import cv2
import numpy as np

from treeaicoach import overlay as ov
from treeaicoach import overlay_guard as og
from treeaicoach import overlay_render as orr
from treeaicoach.capture import Rect
from treeaicoach.config import Config

MM = (1640, 800, 280, 280)


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


def composite(frame, layer):
    """What a capture sees when the layered window is NOT excluded (premultiplied BGRA over BGR)."""
    a = layer[..., 3:4].astype(np.float32) / 255.0
    return (layer[..., :3].astype(np.float32) + frame.astype(np.float32) * (1 - a)).clip(0, 255).astype(np.uint8)


def background(h=280, w=280):
    rng = np.random.default_rng(3)
    img = np.full((h, w, 3), (40, 60, 50), np.uint8)
    img += rng.integers(0, 20, img.shape, dtype=np.uint8)
    return img


def test_marker_found_only_when_composited():
    bg = background()
    assert not og.marker_visible(bg)
    layer = og.stamp_marker(np.zeros((280, 280, 4), np.uint8))
    assert og.marker_visible(composite(bg, layer))
    # scaled capture (frame twice the layer size)
    big = cv2.resize(composite(bg, layer), (560, 560), interpolation=cv2.INTER_NEAREST)
    assert og.marker_visible(big, (2.0, 2.0))
    assert not og.marker_visible(np.zeros((2, 2, 3), np.uint8)) and not og.marker_visible(None)


def test_probe_detects_leak_then_safe_mode_then_recovers():
    clk = Clock()
    g = og.CaptureGuard(clock=clk)
    g.note_exclusion("minimap", True)
    assert not g.want_probe() and g.status() == "unknown" and not g.safe_mode()
    clk.t += og.PROBE_FIRST_S
    assert g.want_probe()
    layer = og.stamp_marker(np.zeros((280, 280, 4), np.uint8))
    g.probe_shown(MM, (280, 280))
    leaked = composite(background(), layer)
    assert g.check_frame(leaked, MM) is None                  # too early (DWM not composed yet)
    clk.t += og.PROBE_SETTLE_S + 0.01
    assert g.check_frame(leaked, MM) == "seen"
    assert g.status() == "failed" and g.safe_mode()
    assert og.PROBLEM_TEXT in g.problem()
    assert not g.want_probe()
    clk.t += og.PROBE_RETRY_S
    assert g.want_probe()                                     # re-checked later
    g.probe_shown(MM, (280, 280))
    clk.t += og.PROBE_SETTLE_S + 0.01
    assert g.check_frame(background(), MM) == "clean"
    assert g.status() == "ok" and not g.safe_mode() and g.problem() is None


def test_probe_withdrawn_without_frames_and_setting_off_is_safe_mode():
    clk = Clock()
    g = og.CaptureGuard(clock=clk)
    assert not g.want_probe()
    clk.t += og.PROBE_FIRST_S
    assert g.want_probe()
    g.probe_shown(MM, (280, 280))
    clk.t += og.PROBE_MAX_S + 0.1
    assert not g.want_probe() and not g.probe_pending()
    g.set_wanted(False)                                       # streamer: overlay visible in captures
    assert g.safe_mode() and g.status() == "off" and g.problem() is None and not g.want_probe()
    g.set_wanted(True)
    g.note_exclusion("minimap", False)                        # Windows refused the affinity
    assert g.safe_mode() and g.status() == "refused" and g.problem()
    assert not og.CaptureGuard(enforce=False, clock=clk).safe_mode()


def test_clean_frame_inpaints_our_ring_until_proven():
    clk = Clock()
    g = og.CaptureGuard(clock=clk)
    bg = background()
    layer = np.zeros((280, 280, 4), np.uint8)
    cv2.circle(layer, (140, 140), 24, (73, 81, 248, 255), 2)
    cv2.putText(layer, "JGL", (120, 110), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (249, 245, 241, 255), 1)
    g.note_drawn(MM, layer)
    seen = composite(bg, layer)
    red = lambda f: int(((f[..., 2] > 170) & (f[..., 1] < 120)).sum())  # noqa: E731
    assert red(seen) > 100
    out = g.clean_frame(seen, MM)
    assert red(out) == 0
    assert np.abs(out.astype(int) - bg.astype(int)).mean() < 3
    # frame captured at another scale (mask resized onto it)
    big = cv2.resize(seen, (560, 560))
    assert red(g.clean_frame(big, MM)) < 10
    # proven by a probe: frames untouched
    g.note_exclusion("minimap", True)
    clk.t += og.PROBE_FIRST_S
    g.want_probe()
    g.probe_shown(MM, (280, 280))
    clk.t += og.PROBE_SETTLE_S + 0.01
    g.note_drawn(MM, layer)
    assert g.check_frame(bg, MM) == "clean"
    assert g.clean_frame(seen, MM) is seen
    # the layer on screen stays cleaned however long it is not re-uploaded (calm minimap: one
    # upload every 2 s; before 2.16.1 its mask expired after MASK_KEEP_S)
    g2 = og.CaptureGuard(clock=clk)
    g2.note_drawn(MM, layer)
    clk.t += 5.0
    assert red(g2.clean_frame(seen, MM)) == 0
    # hidden: kept MASK_KEEP_S more (capture lag), then forgotten
    g2.note_hidden()
    assert red(g2.clean_frame(seen, MM)) == 0
    clk.t += og.MASK_KEEP_S + 0.1
    assert g2.clean_frame(seen, MM) is seen


def test_translucent_layer_removed_exactly_and_lag_safe():
    """Safe mode caps the layer's alpha: the game pixels under our marks come back exactly (no
    inpainting smear on the game's own icon rings); a newer layer not captured yet, or a pixel
    that does not match our layer, is left alone."""
    clk = Clock()
    g = og.CaptureGuard(clock=clk)
    bg = background()
    cv2.circle(bg, (140, 140), 20, (40, 40, 220), 2)        # a game ring under ours
    layer = np.zeros((280, 280, 4), np.uint8)
    cv2.circle(layer, (146, 143), 24, (73, 81, 248, 255), 3, cv2.LINE_AA)
    cv2.putText(layer, "JGL 12 s", (100, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255, 255), 1, cv2.LINE_AA)
    capped = og.cap_alpha(layer)
    assert int(capped[..., 3].max()) <= round(255 * og.SAFE_LAYER_ALPHA)
    assert og.cap_alpha(capped) is capped
    seen = composite(bg, capped)
    g.note_drawn(MM, capped)
    out = g.clean_frame(seen, MM)
    drawn = capped[..., 3] > 0
    assert np.abs(out.astype(int) - bg.astype(int))[drawn].max() <= 4
    # lag: a moved layer was uploaded after this frame was captured -> the older one is removed
    moved = og.cap_alpha(np.roll(layer, 9, axis=1))
    g.note_drawn(MM, moved)
    out = g.clean_frame(seen, MM)
    err = np.abs(out.astype(int) - bg.astype(int))[drawn].mean()
    assert err < 10 and err < np.abs(seen.astype(int) - bg.astype(int))[drawn].mean() / 8
    # a frame without our marks: (almost) untouched
    clean = g.clean_frame(bg, MM)
    assert np.abs(clean.astype(int) - bg.astype(int)).mean() < 0.5


def test_probe_marker_itself_is_masked_while_unproven():
    clk = Clock()
    g = og.CaptureGuard(clock=clk)
    clk.t += og.PROBE_FIRST_S
    g.want_probe()
    layer = og.stamp_marker(np.zeros((280, 280, 4), np.uint8))
    g.probe_shown(MM, (280, 280))
    out = g.clean_frame(composite(background(), layer), MM)
    assert not og.marker_visible(out)


# ------------------------------------------------------------------ overlay manager (fake windows)
class FakeWin:
    def __init__(self, name, ok=True):
        self.name, self.visible, self.failed, self.hwnd = name, False, False, 1
        self.updates, self.last, self.ok, self.calls, self.aff = 0, None, ok, [], 0

    def update(self, img, x, y, alpha=255):
        self.updates += 1
        self.visible = True
        self.last = img

    def set_alpha(self, a):
        pass

    def hide(self):
        self.visible = False

    def keep_topmost(self):
        pass

    def exclude_from_capture(self):
        self.calls.append("exclude")
        self.aff = ov.WDA_EXCLUDEFROMCAPTURE if self.ok else 0
        return self.ok

    def include_in_capture(self):
        self.calls.append("include")
        self.aff = 0

    def capture_affinity(self):
        return self.aff


def setup(monkeypatch, guard):
    monkeypatch.setattr(ov, "_monitor_rect_at", lambda api, x, y: None)
    m = ov.OverlayManager(Config(), lambda: None, capture_guard=guard)
    wins = {n: FakeWin(n) for n in ov.FLASH_WINDOWS + ("radar", "minimap", "timers", "hud", "toasts")
            + ov.WORLD_WINDOWS}
    api = NS(user32=NS(GetSystemMetrics=lambda i: 1080 if i else 1920))
    return m, wins, api


def state():
    e = orr.EnemyView(key="LeeSin", alias="LeeSin", name="Lee Sin", visible=True, uv=(0.4, 0.4), is_jungler=True,
                      age=0.1)
    return orr.OverlayState(minimap_rect=Rect(*MM), screen_rect=Rect(0, 0, 1920, 1080), enemies=[e],
                            my_team="ORDER", me_uv=(0.3, 0.7))


def test_every_window_excluded_and_reapplied(monkeypatch):
    clk = Clock()
    g = og.CaptureGuard(clock=clk)
    m, wins, _api = setup(monkeypatch, g)
    m._sync_capture_exclusion(wins, Config(overlay_hide_from_capture=True), now=0.0)
    assert all(w.calls == ["exclude"] for w in wins.values())
    assert m.capture_excluded and g.api_excluded()
    m._sync_capture_exclusion(wins, Config(overlay_hide_from_capture=True), now=1.0)
    assert all(w.calls == ["exclude"] for w in wins.values())          # not re-applied every frame
    wins["timers"].hwnd = 2                                             # window re-created
    wins["hud"].aff = 0                                                 # affinity lost
    m._sync_capture_exclusion(wins, Config(overlay_hide_from_capture=True), now=2.0)
    assert wins["timers"].calls == ["exclude"] * 2 and wins["hud"].calls == ["exclude"]
    m._sync_capture_exclusion(wins, Config(overlay_hide_from_capture=True), now=2.0 + ov.EXCLUSION_RECHECK_S)
    assert wins["hud"].calls == ["exclude"] * 2 and wins["minimap"].calls == ["exclude"]
    m._sync_capture_exclusion(wins, Config(overlay_hide_from_capture=False), now=30.0)
    assert all(w.calls[-1] == "include" for w in wins.values()) and not m.capture_excluded
    assert g.safe_mode()


def test_safe_mode_keeps_marks_on_the_real_minimap_and_no_radar(monkeypatch):
    clk = Clock()
    g = og.CaptureGuard(clock=clk)
    m, wins, api = setup(monkeypatch, g)
    cfg = Config(overlay_hide_from_capture=False, overlay_mode="minimap")
    m._sync_capture_exclusion(wins, cfg, now=0.0)
    m._refresh(api, wins, state(), cfg, False, {}, None, now=clk.t)
    # the marks stay ON the real minimap, never a radar copy; the engine masks what we drew
    assert wins["minimap"].visible and m.effective_mode == "minimap"
    assert not wins["radar"].visible
    layer = wins["minimap"].last
    frame = composite(background(), layer)
    assert g.safe_mode() and not np.array_equal(g.clean_frame(frame, MM, now=clk.t), frame)


def test_probe_drawn_then_leak_keeps_minimap_marks_never_radar(monkeypatch):
    clk = Clock()
    g = og.CaptureGuard(clock=clk)
    m, wins, api = setup(monkeypatch, g)
    cfg = Config(overlay_hide_from_capture=True, overlay_mode="minimap")
    m._sync_capture_exclusion(wins, cfg, now=0.0)
    m._refresh(api, wins, state(), cfg, False, {}, None, now=clk.t)
    assert wins["minimap"].visible and not og.marker_visible(wins["minimap"].last[..., :3])
    clk.t += og.PROBE_FIRST_S
    m._refresh(api, wins, state(), cfg, False, {}, None, now=clk.t)
    layer = wins["minimap"].last
    assert g.probe_pending() and og.marker_visible(layer[..., :3])
    clk.t += og.PROBE_SETTLE_S + 0.01
    assert g.check_frame(composite(background(), layer), MM) == "seen"   # the capture saw it
    m._refresh(api, wins, state(), cfg, False, {}, None, now=clk.t)
    assert wins["minimap"].visible and not wins["radar"].visible and m.effective_mode == "minimap"
    assert g.safe_mode()
    # retry: the marker again, on the marks
    clk.t += og.PROBE_RETRY_S
    m._refresh(api, wins, state(), cfg, False, {}, None, now=clk.t)
    layer = wins["minimap"].last
    assert wins["minimap"].visible and og.marker_visible(layer[..., :3])
    clk.t += og.PROBE_SETTLE_S + 0.01
    assert g.check_frame(background(), MM) == "clean"
    m._refresh(api, wins, state(), cfg, False, {}, None, now=clk.t)
    assert m.effective_mode == "minimap" and wins["minimap"].visible
    assert not og.marker_visible(wins["minimap"].last[..., :3])


def test_selfcheck_reports_overlay_in_capture():
    from treeaicoach import selfcheck as sc

    chk = sc.SelfCheck(rules=("overlay",))
    chk.evaluate(sc.Snapshot(t=1.0, overlay_wanted=True, overlay_frames=1, overlay_capture="failed"))
    probs = chk.problems()
    assert probs and "L'overlay apparaît dans la capture" in probs[0].status
    chk.evaluate(sc.Snapshot(t=50.0, overlay_wanted=True, overlay_frames=2, overlay_capture="ok"))
    chk.evaluate(sc.Snapshot(t=100.0, overlay_wanted=True, overlay_frames=3, overlay_capture="ok"))
    assert not any("capture" in p.status for p in chk.problems())


def test_engine_grab_cleans_frame_with_guard():
    from treeaicoach.engine_capture import CaptureMixin

    class E(CaptureMixin):
        pass

    e = E()
    g = og.CaptureGuard()
    e.capture_guard = g
    assert e._overlay_guard() is g
    del e.capture_guard
    assert e._overlay_guard() is og.GUARD


def test_exclusion_failure_logged_once_per_window_and_error():
    ov._excl_fail_seen.clear()
    assert ov._excl_fail_first("minimap", 8) is True
    assert all(ov._excl_fail_first("minimap", 8) is False for _ in range(50))
    assert ov._excl_fail_first("hud", 8) is True and ov._excl_fail_first("minimap", 5) is True
