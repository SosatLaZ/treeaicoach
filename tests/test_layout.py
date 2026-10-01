"""Placement of everything TreeAI draws in game (treeaicoach/layout.py) vs League's own UI.

Real data: ``tests/fixtures/layout_real_ui.json`` holds League UI boxes measured by hand on real
2026 captures (users' 2560 x 1440 games, 1080p / 720p streams, two minimap crops). The zone
model must cover every one of them, and no TreeAI element may touch a zone, a measured box or
another TreeAI element - on those captures and on synthetic screens (1366 x 768 .. 3440 x 1440,
minimap 220 / 300 / 384 px, left / right). ``tools/layout_audit.py`` draws the same thing.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

from treeaicoach import fx_render as fx
from treeaicoach import layout as L
from treeaicoach import overlay as ov
from treeaicoach import overlay_render as orr
from treeaicoach import toasts as tst
from treeaicoach.capture import Rect
from treeaicoach.config import Config
from treeaicoach.fx_overlay import screen_and_minimap

FIXTURE = Path(__file__).parent / "fixtures" / "layout_real_ui.json"
SHOTS = json.loads(FIXTURE.read_text(encoding="utf-8"))["shots"]


def _synthetic() -> list[tuple[str, tuple, tuple]]:
    out = []
    for sw, sh in ((1366, 768), (1920, 1080), (2560, 1440), (3440, 1440), (1899, 1344)):
        for px in (220, 300, 384):
            side_px = int(min(px, 0.34 * sh))          # League's largest minimap is ~0.31 x height
            m = max(4, round(sh * 0.009))
            for side in ("right", "left"):
                x = sw - side_px - m if side == "right" else m
                out.append((f"{sw}x{sh}_mm{px}_{side}", (0, 0, sw, sh), (x, sh - side_px - m, side_px, side_px)))
    return out


SYNTH = _synthetic()
CASES = [(s["name"], tuple(s["screen"]), tuple(s["minimap"]), [(k, tuple(b)) for k, b in s["ui"]]) for s in SHOTS] + [
    (n, scr, mm, []) for n, scr, mm in SYNTH]


def _check_layout(name: str, lay: L.Layout, measured: list) -> list[str]:
    """Everything that is wrong with a layout (empty = clean)."""
    bad = []
    for n, s in lay.slots.items():
        if not L.inside(s.content, lay.screen):
            bad.append(f"{name}: {n} leaves the screen {s.content}")
        for z in lay.zones:
            if not z.soft and L.overlap(s.content, z.rect):
                bad.append(f"{name}: {n} {s.content} over League's {z.key} {z.rect}")
        for key, box in measured:
            if key != "shop" and L.overlap(s.content, box):
                bad.append(f"{name}: {n} over the measured {key} {box}")
    names = list(lay.slots)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if {a, b} == {"badge_big", "badge_small"} and lay.slots["badge_big"].anchor == "as_small":
                continue           # one fx window plays the badges one after the other: same slot
            if L.overlap(lay.slots[a].content, lay.slots[b].content):
                bad.append(f"{name}: {a} over {b}")
    return bad


# ---------------------------------------------------------------------------------- zone model
def test_zone_model_covers_every_measured_box():
    missing = []
    for s in SHOTS:
        zones = L.game_zones(s["screen"], s["minimap"])
        assert {z.key for z in zones} >= {"minimap", "ally_row", "votes", "bottom_bar", "scoreboard", "announcer",
                                          "kill_feed", "chat", "team_frames", "death_recap", "respawn", "shop"}
        for key, (x, y, w, h) in s["ui"]:
            for px in np.linspace(x + 1, x + w - 2, 9):
                for py in np.linspace(y + 1, y + h - 2, 9):
                    if not any(z.rect[0] <= px < z.rect[0] + z.rect[2] and z.rect[1] <= py < z.rect[1] + z.rect[3]
                               for z in zones):
                        missing.append((s["name"], key, (round(px), round(py))))
    assert missing == []


def test_zones_follow_the_minimap_side_scale_and_screen():
    scr = (0, 0, 1920, 1080)
    right = {z.key: z.rect for z in L.game_zones(scr, (1653, 813, 256, 256))}
    left = {z.key: z.rect for z in L.game_zones(scr, (11, 813, 256, 256))}
    assert right["ally_row"][0] + right["ally_row"][2] == 1920 and left["ally_row"][0] == 0     # mirrored
    assert right["minimap_buttons"][0] < 1653 and left["minimap_buttons"][0] > 11 + 256
    assert right["bottom_bar"] == left["bottom_bar"]
    big = {z.key: z.rect for z in L.game_zones(scr, (1520, 680, 390, 390))}
    assert big["ally_row"][1] < right["ally_row"][1]                    # sits on a bigger minimap's frame
    uw = {z.key: z.rect for z in L.game_zones((0, 0, 3440, 1440), (2964, 1044, 384, 384))}
    assert uw["bottom_bar"][0] + uw["bottom_bar"][2] / 2 == pytest.approx(1720, abs=2)   # centred on 21:9
    assert uw["scoreboard"][0] + uw["scoreboard"][2] == 3440
    k = {z.key: z.rect for z in L.game_zones(scr, (1653, 813, 256, 256), hud_scale=1.2)}
    assert k["bottom_bar"][2] > right["bottom_bar"][2]                  # HUD scale grows HUD zones
    assert L.minimap_side(scr, (11, 813, 256, 256)) == "left" and L.minimap_side(scr, None, flip=True) == "left"
    assert L.game_zones(None, None) == []


# ---------------------------------------------------------------------------------- solver
@pytest.mark.parametrize("detailed", [False, True], ids=["compact", "F6"])
def test_no_overlap_on_real_captures_and_synthetic_screens(detailed):
    problems = []
    for name, scr, mm, measured in CASES:
        lay = L.layout_for(scr, mm, Config(), detailed=detailed)
        assert set(lay.slots) == set(L.ELEMENTS), name
        problems += _check_layout(name, lay, measured)
    assert problems == []


def test_default_slots_are_where_a_player_expects_them():
    scr, mm = (0, 0, 2000, 1125), (1687, 809, 300, 300)          # the users' screenshots
    lay = L.layout_for(scr, mm, Config())
    z = {k.key: k.rect for k in lay.zones}
    card, toasts, timers = lay.slot("card"), lay.slot("toasts"), lay.slot("timers")
    frame = z["minimap"]
    # card: left of the minimap, bottom-aligned above League's mute / camera / settings buttons
    assert card.rect[0] + card.rect[2] <= frame[0] and card.valign == "bottom"
    assert card.rect[1] + card.rect[3] <= z["minimap_buttons"][1] and card.rect[1] > frame[1]
    # timers: hang OUTSIDE the minimap frame, on its inner side, at its top
    assert timers.rect[0] + timers.rect[2] <= frame[0] and abs(timers.rect[1] - frame[1]) < 40
    # toasts: centred, right under the kill announcer (never over it)
    assert abs(toasts.rect[0] + toasts.rect[2] / 2 - 1000) <= 1
    assert toasts.content[1] >= z["announcer"][1] + z["announcer"][3] and toasts.content[1] < 0.2 * 1125
    # play badges: big one under the toasts, small one next to the minimap; never over the minimap
    big, small = lay.slot("badge_big"), lay.slot("badge_small")
    assert big.content[1] >= toasts.content[1] + toasts.content[3]
    assert small.rect[0] + small.rect[2] <= frame[0]
    for s in lay.slots.values():
        assert not L.overlap(s.content, mm)


def test_layout_is_stable_and_memoized():
    scr, mm = (0, 0, 1920, 1080), (1653, 813, 256, 256)
    a = L.layout_for(scr, mm, Config())
    assert L.layout_for(scr, mm, Config()) is a                       # same inputs: same object, no re-solve
    assert L.layout_for(scr, mm, Config(), detailed=True) is not a    # F6: its own variant
    slot = a.slot("card")
    # a 1-line card and a 2-line danger card keep the same bottom edge (nothing jumps)
    y1 = slot.place(slot.rect[2], 30)[1] + 30
    y2 = slot.place(slot.rect[2], slot.rect[3])[1] + slot.rect[3]
    assert y1 == y2 == slot.rect[1] + slot.rect[3]
    # the slot holds the tallest card of the mode
    for st in orr.sample_states().values():
        assert orr.render_hud(replace(st, hud_detailed=False), slot.rect[2], now=1.0).shape[0] <= slot.rect[3]
    det = L.layout_for(scr, mm, Config(), detailed=True).slot("card")
    for st in orr.sample_states().values():
        assert orr.render_hud(replace(st, hud_detailed=True), det.rect[2], now=1.0).shape[0] <= det.rect[3]


def test_user_positions_are_respected():
    scr, mm = (0, 0, 1920, 1080), (1653, 813, 256, 256)
    cfg = replace(Config(), hud_position="custom", hud_xy=[300, 400])
    assert L.layout_for(scr, mm, cfg).rect("card")[:2] == (300, 400)
    # dragged over the minimap: moved out of it, never under it
    card = L.layout_for(scr, mm, replace(Config(), hud_position="custom", hud_xy=[1700, 850])).slot("card")
    assert not L.overlap(card.content, mm)
    # a window dragged in move mode wins over the config
    assert L.layout_for(scr, mm, Config(), custom_card=(500, 120)).rect("card")[:2] == (500, 120)
    tr = L.layout_for(scr, mm, replace(Config(), hud_position="top_right")).slot("card")
    assert tr.rect[0] + tr.rect[2] == 1920 - 16 and tr.rect[1] < 0.15 * 1080
    tl = L.layout_for(scr, mm, replace(Config(), hud_position="top_left")).slot("card")
    assert tl.rect[0] < 200 and tl.rect[1] < 0.3 * 1080
    others = []
    for pos in ("above_minimap", "top_right", "left_middle"):
        lay = L.layout_for(scr, mm, replace(Config(), hud_position=pos))
        others += [p for p in _check_layout(pos, lay, []) if "card" not in p.split(":")[1].split(" ")[1]]
    assert others == []                                                # the rest still avoids the card
    off = L.layout_for(scr, mm, NS(overlay_timers=False, toasts_enabled=False, plays_enabled=False))
    assert set(off.slots) == {"card"}


def test_radar_is_an_obstacle_for_the_others():
    scr, mm = (0, 0, 1920, 1080), (1653, 813, 256, 256)
    radar = ov.radar_geometry(mm, scr)
    lay = L.layout_for(scr, mm, Config(), radar=(radar[0], radar[1], radar[2], radar[2]))
    for s in lay.slots.values():
        assert not L.overlap(s.content, (radar[0], radar[1], radar[2], radar[2]))


# ---------------------------------------------------------------------------------- consumers
def test_toasts_and_badges_use_the_layout_slots():
    scr, mm = (0, 0, 1920, 1080), (1653, 813, 256, 256)
    lay = L.layout_for(scr, mm, Config())
    assert tst.toast_layer_rect(scr, mm) == lay.rect("toasts")
    k = fx.scale_for_screen(scr)
    assert fx.fx_layer_rect(scr, mm, "top_center", "big", k) == lay.rect("badge_big")
    assert fx.fx_layer_rect(scr, mm, "top_center", "small", k) == lay.rect("badge_small")
    near = fx.fx_layer_rect(scr, mm, "minimap", "big", k, cfg=Config())
    assert near[0] + near[2] <= mm[0] and not L.overlap(near, lay.rect("card"))


def test_play_badges_never_laid_out_inside_the_minimap():
    # the engine gives (minimap, window); the badge thread read it as (screen, minimap): every
    # badge was drawn inside the minimap at 60 %. Either order now gives the screen first.
    assert screen_and_minimap(Rect(1653, 813, 256, 256), Rect(0, 0, 1920, 1080)) == ((0, 0, 1920, 1080),
                                                                                    (1653, 813, 256, 256))
    assert screen_and_minimap((0, 0, 1920, 1080), (1653, 813, 256, 256))[0] == (0, 0, 1920, 1080)
    assert screen_and_minimap(None, None) == (None, None)
    scr, mm = screen_and_minimap(Rect(1653, 813, 256, 256), Rect(0, 0, 1920, 1080))
    for size in ("big", "small"):
        r = fx.fx_layer_rect(scr, mm, "top_center", size, fx.scale_for_screen(scr))
        assert not L.overlap(r, mm) and fx.scale_for_screen(scr) == 1.0


def test_flash_spares_the_minimap_block_and_the_spell_bar():
    scr, mm = (0, 0, 1920, 1080), (1653, 813, 256, 256)
    lay = L.layout_for(scr, mm, Config())
    excl = lay.flash_exclusions()
    assert {tuple(r) for r in excl} == {z.rect for z in lay.zones if z.key in ("minimap", "minimap_buttons",
                                                                             "bottom_bar")}
    img = orr.render_flash(1920, 1080, 1.0, excl, thickness=10)
    bar = next(z.rect for z in lay.zones if z.key == "bottom_bar")
    assert img[bar[1]:bar[1] + bar[3], bar[0]:bar[0] + bar[2], 3].max() == 0     # HP / cooldowns readable
    assert img[1075, 200, 3] > 0 and img[5, 960, 3] > 0                         # the rest of the edges glows
    assert orr.render_flash(100, 100, 1.0, (10, 10, 20, 20))[15, 15, 3] == 0     # single rect still works


def test_world_markers_off_our_elements_and_hud():
    from treeaicoach.ward_guide import WorldMarker

    scr, mm = (0, 0, 1920, 1080), (1653, 813, 256, 256)
    lay = L.layout_for(scr, mm, Config())
    card = lay.slot("card").content
    ground = WorldMarker("ground", card[0] + card[2] / 2, card[1] - 10, label="Ward ici", age=2.0, left=10.0)
    edge = WorldMarker("edge", 1850.0, 0.3 * 1080, 1.0, 0.0, "Ward ici", "≈ 8 s", 2.0, 10.0)
    avoid = lay.avoid(hud_only=True)
    out = orr.render_world_guides([ground, edge], scr, 0.3, avoid=avoid,
                                  edge_avoid=avoid + lay.avoid(hud_only=False, slots=False))
    assert len(out) == 2
    boxes = []
    for img, x, y in out:
        h, w = img.shape[:2]
        boxes.append((x, y, w, h))
        assert L.inside((x, y, w, h), scr)
        for r in lay.avoid(hud_only=True):
            assert not L.overlap((x, y, w, h), r)
    assert not L.overlap(boxes[0], boxes[1])
    kf = next(z.rect for z in lay.zones if z.key == "kill_feed")
    assert not L.overlap(boxes[1], kf)                                   # the edge arrow slid off the kill feed


def test_place_world_patch_minimal_shift():
    scr = (0, 0, 1920, 1080)
    assert orr.place_world_patch(100, 100, 50, 50, scr, [(500, 500, 10, 10)]) == (100, 100)
    x, y = orr.place_world_patch(100, 100, 50, 50, scr, [(90, 120, 200, 200)])
    assert (x, y) == (100, 120 - 50 - 2)                                 # up is the shortest way out
    x, y = orr.place_world_patch(0, 0, 50, 50, scr, [(0, 0, 200, 30)])
    assert not L.overlap((x, y, 50, 50), (0, 0, 200, 30)) and L.inside((x, y, 50, 50), scr)


def test_minimap_labels_avoid_league_corner_buttons(monkeypatch):
    tags = []
    orig = orr._place_tag

    def spy(cv_, x, y, off, tw, th, taken, drop=False):
        pos = orig(cv_, x, y, off, tw, th, taken, drop)
        if pos is not None:
            tags.append((pos[0], pos[1], tw, th))
        return pos

    monkeypatch.setattr(orr, "_place_tag", spy)
    ev = orr.EnemyView(key="LeeSin", alias="LeeSin", name="Lee Sin", visible=False, uv=(0.05, 0.06),
                       last_seen_ago=12.0, is_jungler=True)
    for uv in ((0.05, 0.06), (0.95, 0.95), (0.08, 0.1)):
        tags.clear()
        orr.render_minimap(orr.OverlayState(enemies=[replace(ev, uv=uv)], my_team="CHAOS"), 300, 300, now=1.0)
        for t in tags:
            for c in orr.minimap_corner_rects(300, 300):
                assert not orr._rects_hit(t, [c]), (uv, t, c)


# ---------------------------------------------------------------------------------- overlay thread
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


def test_overlay_thread_puts_every_window_in_its_slot(monkeypatch):
    from treeaicoach.objectives import ObjectiveState

    monkeypatch.setattr(ov, "_monitor_rect_at", lambda api, x, y: None)
    m = ov.OverlayManager(Config(), lambda: None)
    wins = {n: _Win(n) for n in ("flash", "radar", "minimap", "timers", "hud", "toasts") + ov.WORLD_WINDOWS}
    api = NS(user32=NS(GetSystemMetrics=lambda i: 1080 if i else 1920))
    scr, mm = (0, 0, 1920, 1080), (1653, 813, 256, 256)
    drag = ObjectiveState(name="Dragon", next_spawn=645.0, alive=False, source="event", key="dragon")
    toast = tst.ToastView(tst.Toast("warning", "ENNEMI PLUS FORT", "Thresh a 2 objets", None, "k", 0.0, 4.0), 1.0)
    st = orr.OverlayState(minimap_rect=Rect(*mm), screen_rect=Rect(*scr), tip="Farme sous ta tour", my_team="ORDER",
                          my_role="BOTTOM", game_time=600.0, objectives=[drag], enemy_respawns=[618.0, 625.0],
                          toasts=[toast], skill_level="debutant", tip_curated=True)
    m._refresh(api, wins, st, Config(), False, {}, None, now=10.0)
    lay = m.layout
    assert lay is not None and L.published(scr, mm) is lay
    (_s, hx, hy), = wins["hud"].updates
    card = lay.slot("card")
    assert card.rect[0] == hx and L.inside((hx, hy, _s[1], _s[0]), card.rect)
    (ts, tx, ty), = wins["timers"].updates
    assert L.inside((tx, ty, ts[1], ts[0]), lay.slot("timers").rect) and tx + ts[1] <= mm[0]
    (_s2, ox, oy), = wins["toasts"].updates
    assert (ox, oy) == lay.rect("toasts")[:2]
    # nothing to time: the strip window hides; danger: no timers (the card says what to do)
    m._refresh(api, wins, replace(st, objectives=[], enemy_respawns=[]), Config(), False, {}, None, now=11.0)
    assert not wins["timers"].visible


def test_tall_window_keeps_the_card_off_the_item_panel_and_recall_bar():
    # 1899 x 1344 stream capture (taller than 16:9): our card sat right against the item / gold
    # panel at y ~1260-1295. League's HUD keeps its height-sized width there.
    scr, mm = (0, 0, 1899, 1344), (1899 - 360 - 12, 1344 - 360 - 12, 360, 360)
    assert L.ui_unit(scr) == 1344
    for detailed in (False, True):
        lay = L.layout_for(scr, mm, Config(), detailed=detailed)
        z = {k.key: k.rect for k in lay.zones}
        items_right = 1899 / 2 + 0.29 * 1344                # measured items / W-B-P column, height-scaled
        for s in lay.slots.values():
            assert not L.overlap(s.content, z["bottom_bar"]) and not L.overlap(s.content, z["cast_bar"])
            if s.content[1] + s.content[3] > 1344 - 0.135 * 1344:
                assert s.content[0] >= items_right + 4


def test_big_badge_shown_small_when_it_has_no_clean_place():
    scr, mm = (0, 0, 1899, 1344), (1899 - 300 - 12, 1344 - 300 - 12, 300, 300)
    lay = L.layout_for(scr, mm, Config())
    if lay.slot("badge_big").anchor == "as_small":
        assert lay.rect("badge_big") == lay.rect("badge_small")
        assert fx.badge_size(scr, mm, "big") == "small"
        assert fx.fx_layer_rect(scr, mm, "top_center", "big") == lay.rect("badge_small")
    assert fx.badge_size((0, 0, 1920, 1080), (1653, 813, 256, 256), "big") == "big"

