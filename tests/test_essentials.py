"""Essentials strip (treeaicoach/essentials.py): text builder, cached render, change-only redraw,
placement (top centre, never over League's UI) and capture exclusion (same windows dict)."""

from __future__ import annotations

from types import SimpleNamespace as NS

import numpy as np

from treeaicoach import essentials as E
from treeaicoach import layout as L
from treeaicoach import overlay as ov
from treeaicoach import overlay_render as orr
from treeaicoach.capture import Rect
from treeaicoach.config import Config
from treeaicoach.live_edge import EdgeStats

SCR, MM = (0, 0, 1920, 1080), (1653, 813, 256, 256)


def stats(**kw):
    base = dict(gold_diff=850, opponent="Darius", cs_min=6.8, cs_target=7.0, gold_min=380, win=0.62,
                win_delta=8.0, win_reason="Baron")
    base.update(kw)
    return EdgeStats(**base)


def ob(key, alive=False, nxt=None):
    return NS(key=key, name=key, alive=alive, next_spawn=nxt)


def jungler(**kw):
    base = dict(is_jungler=True, visible=False, uv=(0.25, 0.25), last_seen_ago=23.0, dead=False)
    base.update(kw)
    return NS(**base)


def state(**kw):
    base = dict(edge_stats=stats(), objectives=[ob("dragon", nxt=372.0)], game_time=300.0, enemies=[jungler()])
    base.update(kw)
    return NS(**base)


# ---------------------------------------------------------------------------------- text builder
def test_full_strip_text():
    assert E.lines_text(E.build_lines(state())) == \
        "VICTOIRE 62 % ↑ Baron  ·  OR +850  ·  CS 6,8/7\nDRAGON 1:12  ·  JGL haut 23 s"


def test_win_cell_arrow_only_on_a_real_swing():
    assert E.win_cell(stats(win_delta=3.0)).text() == "VICTOIRE 62 %"
    assert E.win_cell(stats(win_delta=None, win_reason=None)).text() == "VICTOIRE 62 %"
    c = E.win_cell(stats(win=0.41, win_delta=-9.0, win_reason="inhibiteurs"))
    assert c.arrow == -1 and c.text() == "VICTOIRE 41 % ↓ inhibs"
    assert E.win_cell(stats(win=None)) is None


def test_gold_and_farm_cells():
    assert E.gold_cell(stats(gold_diff=-1254)).text() == "OR -1 250" and E.gold_cell(stats(gold_diff=-1254)).tone == "down"
    assert E.gold_cell(stats(gold_diff=3)).text() == "OR ±0"
    assert E.gold_cell(stats(gold_diff=None)) is None
    assert E.farm_cell(stats(cs_min=4.1, cs_target=6.5)).tone == "warn"
    assert E.farm_cell(stats(cs_target=None)).text() == "CS 6,8"
    assert E.farm_cell(stats(cs_min=None)) is None                       # no CS read: no farming fact
    assert E.farm_cell(stats(support=True, cs_min=None, cs_target=None)).text() == "OR/MIN 380"


def test_objective_cell_rules():
    # a spawn within 90 s beats an objective already up
    assert E.objective_cell([ob("dragon", alive=True), ob("baron", nxt=360.0)], 300.0).text() == "BARON 1:00"
    # nothing soon: the most important one up
    assert E.objective_cell([ob("grubs", alive=True), ob("dragon", alive=True)], 300.0).text() == "DRAGON dispo"
    # nothing up: the next spawn, however far
    assert E.objective_cell([ob("baron", nxt=1200.0)], 300.0).text() == "BARON 15:00"
    assert E.objective_cell([NS(key="", name="Dragon ancestral", alive=False, next_spawn=320.0)], 300.0).text() \
        == "ANCESTRAL 0:20"
    assert E.objective_cell([], 300.0) is None and E.objective_cell([ob("dragon", nxt=400.0)], None) is None


def test_jungler_cell_cases():
    assert E.jungler_cell([jungler(uv=(0.75, 0.8))]).text() == "JGL bas 23 s"
    assert E.jungler_cell([jungler(uv=(0.5, 0.5))]).text().startswith("JGL mid")
    assert E.jungler_cell([jungler(visible=True)]).text() == "JGL haut · visible"
    assert E.jungler_cell([jungler(dead=True)]).text() == "JGL mort"
    assert E.jungler_cell([jungler(uv=None)]).text() == "JGL ?"
    assert E.jungler_cell([jungler(last_seen_ago=200.0)]).text() == "JGL ?"          # stale
    assert E.jungler_cell([jungler(last_seen_ago=87.0)]).text() == "JGL haut 1:25"   # 5 s steps past 1 min
    assert E.jungler_cell([NS(is_jungler=False)]) is None


def test_empty_and_partial_lines():
    assert E.build_lines(NS()) == ()
    only_map = E.build_lines(state(edge_stats=None))
    assert len(only_map) == 1 and E.lines_text(only_map) == "DRAGON 1:12  ·  JGL haut 23 s"
    assert E.build_lines(None) == ()


# ---------------------------------------------------------------------------------- render cache
def test_render_is_cached_by_text():
    lines = E.build_lines(state())
    a = E.render(lines, SCR)
    n = E.render_count
    assert a is not None and a.dtype == np.uint8 and a.shape[2] == 4
    assert E.render(lines, SCR) is a and E.render_count == n               # same text: no redraw
    b = E.render(E.build_lines(state(game_time=301.0)), SCR)
    assert b is not a and E.render_count == n + 1
    w, h = E.strip_size(SCR)
    assert a.shape[1] == w and a.shape[0] <= h and a[..., 3].any()
    assert E.render((), SCR) is None


# ---------------------------------------------------------------------------------- overlay: change-only
class FakeWin:
    def __init__(self, name):
        self.name, self.visible, self.failed, self.hwnd, self.updates, self.xy = name, False, False, 1, 0, None
        self.excluded = False

    def update(self, img, x, y, alpha=255):
        self.updates += 1
        self.visible, self.xy = True, (x, y)

    def hide(self):
        self.visible = False

    def exclude_from_capture(self):
        self.excluded = True
        return True

    def capture_affinity(self):
        return ov.WDA_EXCLUDEFROMCAPTURE if self.excluded else 0


def _mgr():
    m = ov.OverlayManager(Config(), lambda: None, window_factory=FakeWin)
    lay = L.layout_for(SCR, MM, Config())
    return m, lay


def test_strip_uploaded_only_when_text_changes_and_at_most_1_hz():
    m, lay = _mgr()
    win, cfg = FakeWin("essentials"), Config()
    st = state()
    m._refresh_essentials(win, st, cfg, SCR, lay, False, 10.0)
    assert win.updates == 1 and win.visible
    x, y = win.xy
    assert y < 80 and abs(x + lay.slot("essentials").rect[2] / 2 - 960) <= 2       # top centre
    for t in (10.2, 10.6, 11.5, 13.0, 20.0):                         # same text: nothing re-sent
        m._refresh_essentials(win, st, cfg, SCR, lay, False, t)
    assert win.updates == 1
    changed = state(game_time=301.0)
    m._refresh_essentials(win, changed, cfg, SCR, lay, False, 20.4)  # < 1 s since the last check: waits
    assert win.updates == 1
    m._refresh_essentials(win, changed, cfg, SCR, lay, False, 21.1)
    assert win.updates == 2
    m._refresh_essentials(win, changed, Config(essentials_strip=False), SCR, lay, False, 30.0)
    assert not win.visible                                           # toggle off: hidden
    m._refresh_essentials(win, changed, cfg, SCR, lay, True, 31.0)
    assert not win.visible                                           # move mode: hidden
    m._refresh_essentials(win, NS(), cfg, SCR, lay, False, 32.0)
    assert not win.visible                                           # nothing to show: no window


def test_strip_window_is_in_the_capture_excluded_set(monkeypatch):
    monkeypatch.setattr(ov, "_monitor_rect_at", lambda api, x, y: None)
    m = ov.OverlayManager(Config(overlay_hide_from_capture=True), lambda: None, window_factory=FakeWin)
    wins = {n: FakeWin(n) for n in ov.FLASH_WINDOWS + ("radar", "minimap", "timers", "build", "essentials", "hud",
                                                         "toasts") + ov.WORLD_WINDOWS}
    m._sync_capture_exclusion(wins, Config(overlay_hide_from_capture=True), now=0.0)
    assert wins["essentials"].excluded
    import inspect

    assert '"essentials"' in inspect.getsource(ov.OverlayManager._run)           # created with the others


def test_full_refresh_draws_the_strip_from_a_real_overlay_state(monkeypatch):
    monkeypatch.setattr(ov, "_monitor_rect_at", lambda api, x, y: None)
    m = ov.OverlayManager(Config(), lambda: None, window_factory=FakeWin)
    wins = {n: FakeWin(n) for n in ov.FLASH_WINDOWS + ("radar", "minimap", "timers", "build", "essentials", "hud",
                                                         "toasts") + ov.WORLD_WINDOWS}
    api = NS(user32=NS(GetSystemMetrics=lambda i: 1080 if i else 1920))
    st = orr.OverlayState(minimap_rect=Rect(*MM), screen_rect=Rect(*SCR), game_time=300.0, edge_stats=stats(),
                          objectives=[ob("dragon", nxt=372.0)])
    m._refresh(api, wins, st, Config(), False, {}, None, now=5.0)
    assert wins["essentials"].visible and wins["essentials"].updates == 1


# ---------------------------------------------------------------------------------- placement
def test_placement_top_centre_on_both_hud_sides_and_off_without_the_setting():
    for mm in (MM, (11, 813, 256, 256)):                              # minimap right / left
        lay = L.layout_for(SCR, mm, Config())
        s = lay.slot("essentials")
        z = {k.key: k.rect for k in lay.zones}
        assert s.anchor == "top_center" and s.hits == ()
        assert s.content[1] + s.content[3] <= z["announcer"][1]       # above League's kill announcer
        assert not L.overlap(s.content, z["scoreboard"])
    assert "essentials" not in L.layout_for(SCR, MM, Config(essentials_strip=False)).slots


# ------------------------------------------------------------------ next item of the build
def _with_item(missing, cost=3300, **kw):
    from treeaicoach.build_strip import BuildStrip, StripItem
    strip = BuildStrip((StripItem(3006, "owned"), StripItem(6631, "next", missing, cost), StripItem(3046, "todo")))
    return state(build_strip=strip, **kw)


def test_next_item_cell_gold_bar_and_achetable():
    c = E.item_cell(_with_item(450))
    assert c is not None and c.icon == 6631 and c.value == "450 PO" and c.tone == "" and 0.75 <= c.progress < 1.0
    ok = E.item_cell(_with_item(0))
    assert ok.value == "Achetable" and ok.tone == "accent" and ok.progress == 1.0
    assert E.fmt_missing(451) == "460 PO" and E.fmt_missing(1201) == "1 250 PO"     # rounded up, never early
    assert "Estropieur 450 PO" in E.lines_text(E.build_lines(_with_item(450)))
    # not in the fountain start, not without a build, not while the build strip shows it (in base)
    assert E.item_cell(_with_item(450, game_time=60.0)) is None
    assert E.item_cell(state()) is None
    assert E.item_cell(_with_item(450, in_base=True, me_dead=False, threat_level=0)) is None


def test_next_item_redraws_only_when_the_shown_gold_changes():
    a, b = E.build_lines(_with_item(452)), E.build_lines(_with_item(455))
    assert a == b                                  # same 460 PO, same bar step: same lines, no upload
    img = E.render(a)
    n = E.render_count
    assert E.render(b) is img and E.render_count == n
    assert E.build_lines(_with_item(300)) != a
    assert img.shape[1] <= E.strip_size()[0]
