"""The build strip: data, visibility, rendering, layout slot, overlay window, engine wiring."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

from treeaicoach import build_strip as bs
from treeaicoach import item_icons
from treeaicoach import itemization as iz
from treeaicoach import layout as L
from treeaicoach import meta_builds as mb
from treeaicoach import overlay as ov
from treeaicoach import overlay_render as orr
from treeaicoach.config import Config


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TREEAICOACH_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TREEAI_META_BUILDS", raising=False)
    mb._reset()
    item_icons._reset(lambda url: (_ for _ in ()).throw(OSError("offline")))
    yield
    mb._reset()
    item_icons._reset()


def _strip(owned=(3006,), gold=1200, nxt=6631):
    items = iz.load_items()
    return bs.make_strip(mb.get("Garen", "TOP"), owned, gold, items, nxt)


def test_strip_statuses_and_missing_gold():
    st = _strip()
    assert st is not None and 2 <= len(st.items) <= bs.MAX_ITEMS
    by = {i.item_id: i for i in st.items}
    assert by[3006].status == "owned" and by[6631].status == "next" and by[3142].status == "todo"
    assert by[6631].missing > 0 and by[3006].missing == 0 and by[3142].missing == 0
    assert _strip(gold=9999).items[1].missing == 0
    assert st.done == 1
    # no meta build / no item: nothing, never an exception
    assert bs.make_strip(None, [], 0) is None
    assert bs.make_strip(mb.get("Garen", "TOP"), None, None, None) is not None


def test_strip_visibility_base_dead_details_and_fights():
    st = _strip()
    base = orr.OverlayState(build_strip=st, game_time=600.0, in_base=True)
    assert orr.strip_visible(base)
    assert not orr.strip_visible(replace(base, in_base=False))                      # out of base: hidden
    assert orr.strip_visible(replace(base, in_base=False, hud_detailed=True))       # details key held
    assert orr.strip_visible(replace(base, in_base=False, me_dead=True, threat_level=2))   # dead: shown
    assert not orr.strip_visible(replace(base, threat_level=1))                     # fight: hidden
    assert not orr.strip_visible(replace(base, game_time=60.0))                     # start fountain
    assert not orr.strip_visible(orr.OverlayState(game_time=600.0, in_base=True))   # nothing to show


def test_strip_renders_and_fits_its_slot():
    st = _strip()
    for scr in ((0, 0, 1280, 720), (0, 0, 1920, 1080), (0, 0, 3840, 2160)):
        w, h = orr.build_strip_size(scr)
        img = orr.render_build_strip(orr.OverlayState(build_strip=st), scr)
        assert img is not None and img.shape[2] == 4 and img.shape[1] <= w and img.shape[0] <= h
        assert img[..., 3].max() > 0
        assert orr._strip_metrics(scr)["font"].size >= L.MIN_FONT_PX
    # owned items are dimmed (grey), the next one outlined in green
    img = orr.render_build_strip(orr.OverlayState(build_strip=st), (0, 0, 1920, 1080))
    assert img is not None
    assert orr.render_build_strip(orr.OverlayState(), (0, 0, 1920, 1080)) is None


@pytest.mark.parametrize("scr,mm", [((0, 0, 1920, 1080), (1653, 813, 256, 256)),
                                    ((0, 0, 1280, 720), (1100, 540, 170, 170)),
                                    ((0, 0, 2560, 1440), (11, 1160, 270, 270))])
def test_layout_has_a_clean_build_slot_beside_the_card(scr, mm):
    lay = L.layout_for(scr, mm, Config())
    slot, card = lay.slot("build"), lay.slot("card")
    assert slot is not None and not L._dirty(slot), slot.anchor
    assert not L.overlap(slot.content, card.content)
    assert all(not L.overlap(slot.content, z.rect) for z in lay.zones if not z.soft and not z.conditional)
    off = L.layout_for(scr, mm, NS(hud_build_strip=False))
    assert off.slot("build") is None and off.slot("card") is not None


class _Win:
    def __init__(self, name):
        self.name, self.visible, self.updates, self.failed = name, False, [], False

    def update(self, img, x, y, alpha=255):
        self.visible = True
        self.updates.append((img.shape, int(x), int(y)))

    def hide(self):
        self.visible = False

    def set_alpha(self, a):
        pass


def test_overlay_window_follows_visibility_and_toggle(monkeypatch):
    monkeypatch.setattr(ov, "_monitor_rect_at", lambda api, x, y: None)
    m = ov.OverlayManager(Config(), lambda: None)
    wins = {n: _Win(n) for n in ("flash", "radar", "minimap", "timers", "build", "hud", "toasts") + ov.WORLD_WINDOWS}
    api = NS(user32=NS(GetSystemMetrics=lambda i: 1080 if i else 1920))
    scr, mm = (0, 0, 1920, 1080), (1653, 813, 256, 256)
    from treeaicoach.capture import Rect

    st = orr.OverlayState(minimap_rect=Rect(*mm), screen_rect=Rect(*scr), my_team="ORDER", game_time=600.0,
                          in_base=True, build_strip=_strip())
    m._refresh(api, wins, st, Config(), False, {}, None, now=10.0)
    (shape, x, y), = wins["build"].updates
    slot = m.layout.slot("build")
    assert L.inside((x, y, shape[1], shape[0]), slot.rect)
    m._refresh(api, wins, replace(st, in_base=False), Config(), False, {}, None, now=11.0)
    assert not wins["build"].visible                                # out of base: gone
    m._refresh(api, wins, st, Config(hud_build_strip=False), False, {}, None, now=12.0)
    assert not wins["build"].visible                                # setting off


def test_engine_run_item_card_and_strip_in_base_and_affordable():
    """Simulated engine run: in base with the gold the HUD fields carry the item card AND the
    strip, and the compact card renders the highlight (the user's question: is it really there?)."""
    from tests.test_engine import game_info, make_engine
    from treeaicoach import itemization as iz2

    eng, _v, clock = make_engine(cfg=Config())
    g = game_info(700.0)
    g = replace(g, me=replace(g.me, items=[3006, 1036], level=9), current_gold=3500.0)
    eng._game, eng._game_t = g, 700.0
    adv = iz2.ItemAdvisor()
    adv.update(700.0, g, "TOP", in_base=True)
    eng._item_adv = adv
    clock.t = 700.0
    out = eng._hud_card_fields(g, (0.04, 0.96), None, 700.0)
    card, strip = out["item_card"], out["build_strip"]
    assert card is not None and card.shop and card.affordable, card
    assert strip is not None and any(i.status == "next" for i in strip.items)
    st = orr.OverlayState(**{**out, "game_time": 700.0})
    assert orr.compact_content(st)["mode"] == "item" and orr.strip_visible(st)
    img = orr.render_hud(st, 300)
    assert img[..., 3].max() > 0
    # toggles
    eng._cfg = replace(eng._cfg, hud_build_strip=False)
    assert eng._hud_card_fields(g, (0.04, 0.96), None, 701.0)["build_strip"] is None
