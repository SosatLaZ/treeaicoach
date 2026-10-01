"""Placement audit: every TreeAI overlay element vs League's own UI, on real screenshots.

For each real capture listed in ``tests/fixtures/layout_real_ui.json`` (hand-measured boxes of
League's UI: minimap + frame + buttons, ally portraits, vote panels, item / spells bar,
scoreboard, kill feed, announcer, chat, team frames, shop, death recap, respawn panel) and for
synthetic screens (1366x768 .. 3440x1440, minimap 220-400 px, left / right), it:

1. computes League's zones (:func:`treeaicoach.layout.game_zones`) and draws them;
2. renders EVERY element we can show at its position: compact card (normal + danger form),
   detailed card (F6), toast and big banner, timers strip, big and small play badges, the
   minimap layer (labels / arrows / guides), the in-world "Ward ici" marker and edge arrow, the
   danger flash - with the NEW layout (:mod:`treeaicoach.layout`, what the overlay uses) and with
   the OLD placement code (``--before-rev``, loaded from git: what shipped before);
3. measures overlaps with League's zones / measured boxes and between our own elements,
   readability (text contrast over the real background, font size) and the distance to the
   eye path (champion at the centre, minimap, kill announcer);
4. writes composites (``<shot>_before.png`` / ``_after.png`` / ``_compare.png`` /
   ``zones_<shot>.png``) and ``report.txt`` + ``report.json`` into ``--out``.

``python -m tools.layout_audit --shots DIR [--shots DIR] [--out DIR] [--before-rev REV]``
(images looked up by file name in the ``--shots`` folders; missing ones use a synthetic game
background, the measured boxes still count). Development tool only: never imported by the app.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import cv2
import numpy as np

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from treeaicoach import fx_render as fx  # noqa: E402
from treeaicoach import layout as L  # noqa: E402
from treeaicoach import overlay as ov  # noqa: E402
from treeaicoach import overlay_render as orr  # noqa: E402
from treeaicoach import toasts as tst  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "layout_real_ui.json"
DEFAULT_BEFORE = "e321a1f"           # TreeAI Coach 2.2.0 (placement before the layout solver)
RectT = tuple[int, int, int, int]

#: Elements that can be on screen at the same time (variants of one slot are alternatives).
GROUPS = ("card", "toasts", "timers", "badge_big", "badge_small", "world_ground", "world_edge")
ZONE_RGB = {"minimap": (90, 200, 255), "minimap_buttons": (90, 200, 255), "ally_row": (255, 200, 60),
            "votes": (255, 140, 40), "kill_feed": (255, 90, 90), "scoreboard": (200, 200, 200),
            "announcer": (255, 120, 220), "bottom_bar": (120, 255, 140), "stats_panel": (120, 220, 140),
            "chat": (180, 160, 255), "team_frames": (255, 200, 60), "death_recap": (255, 90, 160),
            "respawn": (255, 90, 160), "shop": (150, 150, 150)}


# ======================================================================================
# Cases
# ======================================================================================
@dataclass
class Shot:
    name: str
    screen: RectT
    minimap: RectT
    ui: list[tuple[str, RectT]] = field(default_factory=list)
    image: np.ndarray | None = None          # RGB, full image (screen inside it)
    size: tuple[int, int] = (1920, 1080)     # image size
    state: str = ""
    real: bool = True
    old_overlay: list[RectT] = field(default_factory=list)   # the old app's drawings baked in the capture


def load_shots(dirs: list[Path], fixture: Path = FIXTURE) -> list[Shot]:
    data = json.loads(fixture.read_text(encoding="utf-8"))
    out = []
    for s in data["shots"]:
        img = None
        for d in dirs:
            p = Path(d) / s["file"]
            if p.is_file():
                try:
                    from PIL import Image

                    img = np.asarray(Image.open(p).convert("RGB")).copy()
                except Exception:
                    img = None
                break
        out.append(Shot(s["name"], tuple(s["screen"]), tuple(s["minimap"]),
                        [(k, tuple(b)) for k, b in s["ui"]], img, tuple(s["size"]), s.get("state", ""),
                        old_overlay=[tuple(b) for b in s.get("old_overlay", [])]))
    return out


def synthetic_shots() -> list[Shot]:
    """Synthetic screens: resolutions x minimap sizes x sides (minimap margin 0.9 % of the height)."""
    out = []
    for sw, sh, sizes in ((1366, 768, (150, 205, 260)), (1920, 1080, (220, 300, 384)),
                          (2560, 1440, (300, 384, 400)), (3440, 1440, (300, 384, 400))):
        for mm_side in sizes:
            for side in ("right", "left"):
                m = max(4, round(sh * 0.009))
                x = sw - mm_side - m if side == "right" else m
                out.append(Shot(f"synth_{sw}x{sh}_mm{mm_side}_{side}", (0, 0, sw, sh), (x, sh - mm_side - m, mm_side,
                                                                                          mm_side),
                                size=(sw, sh), state="synthetic", real=False))
    return out


# ======================================================================================
# Sample content of every element
# ======================================================================================
def _ev(key: str, uv: Any, visible: bool = True, ago: Any = None, jungler: bool = False, approaching: bool = False,
        vel: Any = None) -> orr.EnemyView:
    return orr.EnemyView(key=key, alias=key, name=key, visible=visible, uv=uv, last_seen_ago=ago, is_jungler=jungler,
                         approaching=approaching, velocity=vel, age=0.1 if visible else None)


def sample_states(shot: Shot) -> dict[str, Any]:
    from treeaicoach.objectives import ObjectiveState

    base = dict(minimap_rect=shot.minimap, screen_rect=shot.screen, my_team="ORDER", my_role="BOTTOM",
                game_time=600.0, me_uv=(0.70, 0.86))
    card = orr.OverlayState(**base, tip="Pose une balise dans la rivière avant le Dragon", tip_curated=True)
    danger = orr.OverlayState(**base, threat_level=2, threat_text="DANGER — GANK !", tip="Recule vers ta tour",
                              tip_tone="danger")
    detailed = replace(orr.sample_states()["warning"], minimap_rect=shot.minimap, screen_rect=shot.screen,
                       hud_detailed=True, tip="Pose une balise dans la rivière avant le Dragon")
    drag = ObjectiveState(name="Dragon", next_spawn=645.0, alive=False, source="event", key="dragon")
    timers = orr.OverlayState(**base, objectives=[drag], buffs=[("baron", "CHAOS", 700.0)],
                              enemy_respawns=[618.0, 625.0, 640.0])
    enemies = [_ev("LeeSin", (0.07, 0.07), visible=False, ago=12.0, jungler=True),
               _ev("Darius", (0.80, 0.80), approaching=True, vel=(-0.02, 0.02)),
               _ev("Ahri", (0.93, 0.95), approaching=True, vel=(-0.03, -0.01)),
               _ev("Jinx", (0.5, 0.5))]
    mmap = orr.OverlayState(**base, enemies=enemies, threat_level=2, danger_radius=0.12)
    return {"card": card, "card_danger": danger, "card_detailed": detailed, "timers": timers, "minimap": mmap}


def _toast_view(kind: str) -> Any:
    if kind == "banner":
        t = tst.Toast("retreat", "RECULE", "3 ennemis arrivent par la rivière", None, "banner:", 0.0, 4.0)
    else:
        t = tst.Toast("warning", "ENNEMI PLUS FORT", "Thresh a fini son 2e objet : ne trade pas", None, "k", 0.0, 4.0)
    return tst.ToastView(t, 1.5)


@dataclass
class Placed:
    """One rendered element on screen (premultiplied BGRA image at x, y)."""

    group: str
    variant: str
    img: np.ndarray
    x: int
    y: int
    font_px: float = 0.0

    def bbox(self) -> RectT | None:
        a = self.img[..., 3]
        ys, xs = np.nonzero(a > 8)
        if not len(xs):
            return None
        return (self.x + int(xs.min()), self.y + int(ys.min()), int(xs.max() - xs.min() + 1),
                int(ys.max() - ys.min() + 1))


def _badge(size: str, scale: float) -> np.ndarray:
    img = fx.render_frame("brilliant", "COUP DE MAÎTRE", "Baron volé sous le nez du jungler", 1.2, size=size,
                          scale=scale)
    return img if img is not None else np.zeros((2, 2, 4), np.uint8)


def _minimap_timers_bbox_legacy(legacy_orr: Any, st: Any, mm: RectT) -> tuple[np.ndarray, RectT | None]:
    """The legacy timers column inside the minimap layer: image of the difference."""
    with_t = legacy_orr.render_minimap(st, mm[2], mm[3], now=0.0)
    without = legacy_orr.render_minimap(replace(st, show_timers=False), mm[2], mm[3], now=0.0)
    diff = with_t.copy()
    diff[without[..., 3] == with_t[..., 3]] = 0
    ys, xs = np.nonzero(diff[..., 3] > 8)
    if not len(xs):
        return diff, None
    return diff, (int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1))


def place_new(shot: Shot, states: dict[str, Any], cfg: Any = None) -> tuple[dict[str, list[Placed]], Any]:
    """Every element at the NEW layout's slots (exactly what overlay.py / fx_overlay do), per scene:
    "normal" (compact card, its danger form, toast / banner, timers, badges, in-world markers) and
    "F6" (the detailed card held with F6: its own layout variant, the others re-placed around it)."""
    scr, mm = shot.screen, shot.minimap
    U = L.ui_unit(scr)
    scenes: dict[str, list[Placed]] = {}
    lay = L.layout_for(scr, mm, cfg)
    for scene, layout_, cards in (("normal", lay, (("compact", states["card"]), ("danger", states["card_danger"]))),
                                  ("F6", L.layout_for(scr, mm, cfg, detailed=True),
                                   (("detailed_F6", states["card_detailed"]),))):
        out: list[Placed] = []
        for variant, st in cards:
            img = orr.render_hud(st, ov.hud_width(scr), now=1.0)
            slot = layout_.slot("card")
            if slot is not None:
                x, y = slot.place(img.shape[1], img.shape[0])
                out.append(Placed("card", variant, img, x, y, 15.5 * ov.hud_width(scr) / 300.0))
        for variant, kind in (("toast", "toast"), ("banner", "banner")):
            img = tst.render_toast_layer([_toast_view(kind)], tst.scale_for_screen(scr))
            slot = layout_.slot("toasts")
            if slot is not None:
                out.append(Placed("toasts", variant, img, slot.rect[0], slot.rect[1], 14 * tst.scale_for_screen(scr)))
        slot = layout_.slot("timers")
        tst_state = replace(states["timers"], hud_detailed=scene == "F6")
        strip = orr.render_timers(tst_state, scr, slot.rect[2]) if slot is not None else None
        if strip is not None and slot is not None:
            x, y = slot.place(strip.shape[1], strip.shape[0])
            out.append(Placed("timers", "rows", strip, x, y, orr._timer_metrics(U)["font"].size))
        k = fx.scale_for_screen(scr)
        for size in ("big", "small"):
            slot = layout_.slot(f"badge_{size}")
            if slot is not None:
                out.append(Placed(f"badge_{size}", size, _badge(size, k), slot.rect[0], slot.rect[1], 17 * k))
        out += _world(shot, layout_, legacy=None)
        scenes[scene] = out
    return scenes, lay


def _world(shot: Shot, lay: Any, legacy: Any) -> list[Placed]:
    """A ground "Ward ici" marker wanted just above the card's corner (the worst case: next to our
    own element) and an edge arrow on the right border at the kill feed height."""
    from treeaicoach import camera_proj as cp
    from treeaicoach.ward_guide import WorldMarker

    scr, mm = shot.screen, shot.minimap
    U = L.ui_unit(scr)
    card = lay.slot("card").rect if lay is not None and lay.slot("card") else (scr[0] + scr[2] // 2, scr[1] + scr[3] // 2,
                                                                                  10, 10)
    ground = WorldMarker("ground", card[0] + card[2] * 0.5, card[1] - 0.02 * U, label="Ward ici", sub="Rivière",
                         age=2.0, left=10.0)
    edge = WorldMarker("edge", scr[0] + scr[2] - 0.065 * U, scr[1] + 0.30 * U, 1.0, 0.0, "Ward ici", "≈ 8 s", 2.0, 10.0)
    if legacy is not None:
        avoid = [cp.hud_bar_rect(scr), mm]
        patches = legacy.render_world_guides([ground, edge], scr, 0.3, avoid=avoid)
    else:
        avoid = [cp.hud_bar_rect(scr), mm] + lay.avoid(hud_only=True)
        patches = orr.render_world_guides([ground, edge], scr, 0.3, avoid=avoid,
                                          edge_avoid=avoid + lay.avoid(hud_only=False, slots=False))
    out = []
    for (img, x, y), name in zip(patches, ("world_ground", "world_edge")):
        out.append(Placed(name, name, img, x, y, 15 * orr.world_scale(scr)))
    return out


def place_old(shot: Shot, states: dict[str, Any], legacy: dict[str, Any]) -> dict[str, list[Placed]]:
    """Every element where the OLD code put it (git ``--before-rev``), incl. its known bugs; the
    old code had no layout variant: the F6 card simply replaced the compact one."""
    scr, mm = shot.screen, shot.minimap
    lov, ltst, lfx, lorr = legacy["overlay"], legacy["toasts"], legacy["fx_render"], legacy["overlay_render"]
    common: list[Placed] = []
    for variant, kind in (("toast", "toast"), ("banner", "banner")):
        img = tst.render_toast_layer([_toast_view(kind)], tst.scale_for_screen(scr))
        x, y, _w, _h = ltst.toast_layer_rect(scr, mm)
        common.append(Placed("toasts", variant, img, x, y, 14 * tst.scale_for_screen(scr)))
    diff, bb = _minimap_timers_bbox_legacy(lorr, states["timers"], mm)
    if bb is not None:
        common.append(Placed("timers", "in_minimap", diff, mm[0], mm[1], max(8, round(9 * min(mm[2], mm[3]) / 256.0))))
    # play badges as the game drew them: fx_overlay read engine._screen_rects() = (minimap, window)
    # as (screen, minimap) -> laid out inside the minimap rect at 60 %
    k_bug = lfx.scale_for_screen(mm)
    for size in ("big", "small"):
        x, y, _w, _h = lfx.fx_layer_rect(mm, scr, "top_center", size, k_bug)
        common.append(Placed(f"badge_{size}", f"{size}_ingame_bug", _badge(size, k_bug), x, y, 17 * k_bug))
    common += _world(shot, L.layout_for(scr, mm), legacy=lorr)
    scenes: dict[str, list[Placed]] = {}
    for scene, cards in (("normal", (("compact", states["card"]), ("danger", states["card_danger"]))),
                         ("F6", (("detailed_F6", states["card_detailed"]),))):
        out: list[Placed] = []
        for variant, st in cards:
            img = orr.render_hud(st, ov.hud_width(scr), now=1.0)
            x, y = lov.hud_placement(scr, img.shape[1], img.shape[0], "left_of_minimap", None, avoid=[mm], anchor=mm,
                                     anchor_gap=lov.minimap_clearance(mm), minimap=mm)
            out.append(Placed("card", variant, img, x, y, 15.5 * ov.hud_width(scr) / 300.0))
        scenes[scene] = out + common
    return scenes


# ======================================================================================
# Metrics
# ======================================================================================
def _lum(rgb: np.ndarray) -> np.ndarray:
    c = rgb.astype(np.float32) / 255.0
    c = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * c[..., 0] + 0.7152 * c[..., 1] + 0.0722 * c[..., 2]


def readability(bg: np.ndarray, p: Placed, origin: tuple[int, int]) -> dict[str, float] | None:
    """Text contrast (WCAG ratio) of the element over what is really under it (``contrast``) and
    over a white background (``contrast_white``: the worst case, bright terrain / spell effects
    seen through a translucent plate). Text = the brightest 2 % of the plate pixels, plate = its
    30th percentile."""
    H, W = bg.shape[:2]
    x, y = p.x - origin[0], p.y - origin[1]
    h, w = p.img.shape[:2]
    X0, Y0, X1, Y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if X0 >= X1 or Y0 >= Y1:
        return None
    src = p.img[Y0 - y:Y1 - y, X0 - x:X1 - x]
    a = src[..., 3]
    plate = a >= 150
    if plate.sum() < 30:
        return None
    out: dict[str, float] = {}
    for name, under in (("contrast", bg[Y0:Y1, X0:X1].copy()), ("contrast_white", np.full(src.shape[:2] + (3,), 255,
                                                                                         np.uint8))):
        comp = orr.composite_over(under, src, 0, 0)
        lum = _lum(comp)[plate]
        lt, lp = float(np.percentile(lum, 98)), float(np.percentile(lum, 30))
        out[name] = round((max(lt, lp) + 0.05) / (min(lt, lp) + 0.05), 1)
    out["plate_alpha"] = round(float(np.median(a[a > 8])) / 255.0, 2)
    return out


def eye_distance(shot: Shot, r: RectT) -> float:
    """Distance (UI units, 1.0 = screen height of a 16:9 UI) from the element's centre to the
    nearest place the eye goes anyway: the champion (screen centre), the minimap, the announcer."""
    sx, sy, sw, sh = shot.screen
    U = L.ui_unit(shot.screen)
    mm = shot.minimap
    anchors = [(sx + sw / 2, sy + sh / 2), (mm[0] + mm[2] / 2, mm[1] + mm[3] / 2), (sx + sw / 2, sy + 0.11 * U)]
    cx, cy = r[0] + r[2] / 2, r[1] + r[3] / 2
    d = min(math.hypot(cx - ax, cy - ay) for ax, ay in anchors)
    if mm[0] <= cx <= mm[0] + mm[2] and mm[1] <= cy <= mm[1] + mm[3]:
        d = 0.0
    return round(d / U, 3)


def overlaps(shot: Shot, placed: list[Placed], zones: list[L.Zone]) -> dict[str, Any]:
    """Overlaps (element variant -> zone / measured box / other element) with areas in px."""
    game: list[dict[str, Any]] = []
    shop: list[dict[str, Any]] = []
    info: list[dict[str, Any]] = []
    for p in placed:
        bb = p.bbox()
        if bb is None:
            continue
        hit = set()
        # a ground ward marker stays on its real spot: only League's always-on HUD counts for it
        # (it may cross a kill-feed line or the chat for a few seconds: reported as info)
        truthful = p.group == "world_ground"
        for z in zones:
            a = L.overlap_area(bb, z.rect)
            if a <= 0:
                continue
            o = {"element": f"{p.group}/{p.variant}", "with": f"zone:{z.key}", "area": a}
            if z.soft:
                shop.append(o)
            elif truthful and z.key not in L.HUD_KEYS:
                info.append(o)
            else:
                game.append(o)
            hit.add(z.key)
        for key, box in shot.ui:
            a = L.overlap_area(bb, box)
            o = {"element": f"{p.group}/{p.variant}", "with": f"measured:{key}", "area": a}
            if a > 0 and key not in hit and key != "shop":
                (info if truthful and key not in L.HUD_KEYS else game).append(o)
            elif a > 0 and key == "shop" and "shop" not in hit:
                shop.append(o)
    # our own elements: the union box of each group's variants (they are alternatives of one slot)
    boxes: dict[str, RectT] = {}
    for p in placed:
        bb = p.bbox()
        if bb is None:
            continue
        g = p.group
        if g in boxes:
            o = boxes[g]
            x0, y0 = min(o[0], bb[0]), min(o[1], bb[1])
            x1, y1 = max(o[0] + o[2], bb[0] + bb[2]), max(o[1] + o[3], bb[1] + bb[3])
            boxes[g] = (x0, y0, x1 - x0, y1 - y0)
        else:
            boxes[g] = bb
    mine = []
    names = [g for g in GROUPS if g in boxes]
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            ar = L.overlap_area(boxes[a], boxes[b])
            if ar > 0:
                mine.append({"element": a, "with": b, "area": ar})
    # minimap: our elements must never sit on the minimap itself (the layer drawn over it is the
    # only exception: it is the minimap layer)
    return {"game": game, "shop": shop, "ours": mine, "info": info}


# ======================================================================================
# Drawing
# ======================================================================================
def background(shot: Shot) -> tuple[np.ndarray, tuple[int, int]]:
    """RGB image to draw on + the screen origin inside it."""
    if shot.image is not None:
        img = shot.image.copy()
        for x, y, w, h in shot.old_overlay:          # what the OLD app drew when the capture was taken
            sub = img[max(0, y):y + h, max(0, x):x + w]
            sub[:] = (sub.astype(np.float32) * 0.18 + 12).astype(np.uint8)
            fs = max(0.35, img.shape[0] / 3000.0)
            cv2.putText(img, "ancien overlay (capture)", (max(0, x) + 4, max(0, y) + int(18 * fs * 2)),
                        cv2.FONT_HERSHEY_SIMPLEX, fs, (150, 150, 150), 1, cv2.LINE_AA)
        return img, (0, 0)
    sx, sy, sw, sh = shot.screen
    mm = shot.minimap
    return orr.game_background(sw, sh, (mm[0] - sx, mm[1] - sy, mm[2], mm[3])), (sx, sy)


def _rect(img: np.ndarray, r: RectT, rgb: Any, origin: tuple[int, int], thick: int = 2, label: str = "",
          dashed: bool = False) -> None:
    x, y, w, h = r[0] - origin[0], r[1] - origin[1], r[2], r[3]
    col = tuple(int(c) for c in rgb)
    if dashed:
        for i in range(0, w, 12):
            cv2.line(img, (x + i, y), (x + min(i + 6, w), y), col, thick)
            cv2.line(img, (x + i, y + h), (x + min(i + 6, w), y + h), col, thick)
        for i in range(0, h, 12):
            cv2.line(img, (x, y + i), (x, y + min(i + 6, h)), col, thick)
            cv2.line(img, (x + w, y + i), (x + w, y + min(i + 6, h)), col, thick)
    else:
        cv2.rectangle(img, (x, y), (x + w, y + h), col, thick)
    if label:
        fs = max(0.4, img.shape[0] / 2400.0)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, fs, 1)
        ty = y - 4 if y - th - 6 > 0 else y + th + 4
        cv2.rectangle(img, (x, ty - th - 3), (x + tw + 4, ty + 3), (0, 0, 0), -1)
        cv2.putText(img, label, (x + 2, ty), cv2.FONT_HERSHEY_SIMPLEX, fs, col, 1, cv2.LINE_AA)


def compose(shot: Shot, placed: list[Placed], zones: list[L.Zone], flash_excl: list[RectT] | None,
            minimap_img: np.ndarray | None, title: str, bad: set[str]) -> np.ndarray:
    img, origin = background(shot)
    if minimap_img is not None:
        orr.composite_over(img, minimap_img, shot.minimap[0] - origin[0], shot.minimap[1] - origin[1])
    if flash_excl is not None:
        sx, sy, sw, sh = shot.screen
        fl = orr.render_flash(sw, sh, 0.85, [(r[0] - sx, r[1] - sy, r[2], r[3]) for r in flash_excl],
                              thickness=ov.flash_thickness(shot.screen))
        orr.composite_over(img, fl, sx - origin[0], sy - origin[1])
    for z in zones:
        _rect(img, z.rect, ZONE_RGB.get(z.key, (200, 200, 200)), origin, 1, z.key, dashed=z.soft)
    for _k, box in shot.ui:
        _rect(img, box, (255, 255, 255), origin, 1, dashed=True)
    shown = {}
    for p in placed:                      # one variant per group on the picture (compact card, banner...)
        if p.group not in shown or p.variant in ("banner", "compact"):
            shown[p.group] = p
    for p in shown.values():
        orr.composite_over(img, p.img, p.x - origin[0], p.y - origin[1])
    for p in placed:
        bb = p.bbox()
        if bb is not None and p in shown.values():
            col = (255, 60, 60) if f"{p.group}/{p.variant}" in bad or p.group in bad else (80, 255, 120)
            _rect(img, bb, col, origin, 2, f"{p.group}")
    fs = max(0.6, img.shape[0] / 1400.0)
    cv2.rectangle(img, (0, 0), (img.shape[1], int(36 * fs)), (0, 0, 0), -1)
    cv2.putText(img, title, (10, int(26 * fs)), cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), 2, cv2.LINE_AA)
    return img


def zones_image(shot: Shot, zones: list[L.Zone]) -> np.ndarray:
    img, origin = background(shot)
    over = img.copy()
    for z in zones:
        x, y, w, h = z.rect[0] - origin[0], z.rect[1] - origin[1], z.rect[2], z.rect[3]
        if not z.soft:
            cv2.rectangle(over, (x, y), (x + w, y + h), ZONE_RGB.get(z.key, (200, 200, 200)), -1)
    img = cv2.addWeighted(over, 0.28, img, 0.72, 0)
    for z in zones:
        _rect(img, z.rect, ZONE_RGB.get(z.key, (200, 200, 200)), origin, 2, z.key, dashed=z.soft)
    for k, box in shot.ui:
        _rect(img, box, (255, 255, 255), origin, 2, dashed=True)
    return img


# ======================================================================================
# Legacy code (git) + driver
# ======================================================================================
def load_legacy(rev: str) -> dict[str, Any] | None:
    mods: dict[str, Any] = {}
    for name in ("overlay_render", "overlay", "toasts", "fx_render"):
        try:
            src = subprocess.run(["git", "show", f"{rev}:treeaicoach/{name}.py"], capture_output=True, text=True,
                                 cwd=REPO, check=True).stdout
        except Exception:
            return None
        modname = f"_layout_audit_legacy_{name}"
        spec = importlib.util.spec_from_loader(modname, loader=None)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[modname] = mod
        exec(compile(src, f"<{rev}:treeaicoach/{name}.py>", "exec"), mod.__dict__)
        mods[name] = mod
    return mods


def _merge(per_scene: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Zone overlaps of every scene (deduplicated) + element overlaps tagged with their scene."""
    out: dict[str, Any] = {"game": [], "shop": [], "ours": [], "info": []}
    seen: set[tuple[str, str]] = set()
    for scene, ov_ in per_scene.items():
        for kind in ("game", "shop", "info"):
            for o in ov_[kind]:
                key = (o["element"], o["with"])
                if key not in seen:
                    seen.add(key)
                    out[kind].append(o)
        out["ours"] += [{**o, "scene": scene} for o in ov_["ours"]]
    return out


def _metrics(shot: Shot, placed: list[Placed], bg: np.ndarray, origin: tuple[int, int]) -> dict[str, Any]:
    return {f"{p.group}/{p.variant}": {"eye": eye_distance(shot, p.bbox() or (p.x, p.y, 1, 1)),
                                       "font_px": round(p.font_px, 1), **(readability(bg, p, origin) or {})}
            for p in placed if p.bbox() is not None}


def audit(shots: list[Shot], out: Path, legacy: dict[str, Any] | None, images: bool = True) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for shot in shots:
        zones = L.game_zones(shot.screen, shot.minimap)
        states = sample_states(shot)
        new, lay = place_new(shot, states)
        res: dict[str, Any] = {"shot": shot.name, "real": shot.real, "state": shot.state,
                               "screen": shot.screen, "minimap": shot.minimap,
                               "slots": {n: list(s.rect) + [s.anchor] for n, s in lay.slots.items()}}
        res["after"] = _merge({sc: overlaps(shot, pl, zones) for sc, pl in new.items()})
        bg, origin = background(shot)
        res["after_metrics"] = _metrics(shot, [p for pl in new.values() for p in pl], bg, origin)
        mm_new, tags_new = _minimap_with_tags(orr, states["minimap"], shot.minimap)
        res["minimap_labels_on_buttons"] = {"after": _labels_on_corners(tags_new, shot.minimap)}
        if legacy is not None:
            old = place_old(shot, states, legacy)
            res["before"] = _merge({sc: overlaps(shot, pl, zones) for sc, pl in old.items()})
            res["before_metrics"] = _metrics(shot, [p for pl in old.values() for p in pl], bg, origin)
            mm_old, tags_old = _minimap_with_tags(legacy["overlay_render"], states["minimap"], shot.minimap)
            res["minimap_labels_on_buttons"]["before"] = _labels_on_corners(tags_old, shot.minimap)
        if images:
            def bad(r: dict[str, Any]) -> set[str]:
                return {o["element"] for o in r["game"]} | {o["element"] for o in r["ours"]} | {
                    o["with"] for o in r["ours"]}

            a = compose(shot, new["normal"], zones, lay.flash_exclusions() + [shot.minimap], mm_new,
                        f"APRES  {shot.name}: {len(res['after']['game'])} chevauchement(s) UI jeu, "
                        f"{len(res['after']['ours'])} entre nos elements", bad(res["after"]))
            cv2.imwrite(str(out / f"{shot.name}_after.png"), cv2.cvtColor(a, cv2.COLOR_RGB2BGR))
            f6 = compose(shot, new["F6"], zones, None, mm_new, f"APRES (F6 maintenu)  {shot.name}", bad(res["after"]))
            cv2.imwrite(str(out / f"{shot.name}_after_F6.png"), cv2.cvtColor(f6, cv2.COLOR_RGB2BGR))
            if shot.real:
                cv2.imwrite(str(out / f"zones_{shot.name}.png"), cv2.cvtColor(zones_image(shot, zones), cv2.COLOR_RGB2BGR))
            if legacy is not None:
                b = compose(shot, old["normal"], zones, [shot.minimap], mm_old,
                            f"AVANT  {shot.name}: {len(res['before']['game'])} chevauchement(s) UI jeu, "
                            f"{len(res['before']['ours'])} entre nos elements", bad(res["before"]))
                cv2.imwrite(str(out / f"{shot.name}_before.png"), cv2.cvtColor(b, cv2.COLOR_RGB2BGR))
                h = 1000
                ra = cv2.resize(a, (int(a.shape[1] * h / a.shape[0]), h), interpolation=cv2.INTER_AREA)
                rb = cv2.resize(b, (int(b.shape[1] * h / b.shape[0]), h), interpolation=cv2.INTER_AREA)
                cv2.imwrite(str(out / f"{shot.name}_compare.png"),
                            cv2.cvtColor(np.hstack([rb, np.full((h, 8, 3), 255, np.uint8), ra]), cv2.COLOR_RGB2BGR))
        rows.append(res)
    summary = _summary(rows)
    (out / "report.json").write_text(json.dumps({"summary": summary, "shots": rows}, indent=1, ensure_ascii=False),
                                     encoding="utf-8")
    (out / "report.txt").write_text(_text_report(summary, rows), encoding="utf-8")
    return {"summary": summary, "shots": rows}


def _minimap_with_tags(mod: Any, st: Any, mm: RectT) -> tuple[np.ndarray, list[tuple[float, float, float, float]]]:
    """The minimap layer rendered by ``mod`` (current or legacy overlay_render) + the label
    rectangles it placed (spy on its ``_place_tag``)."""
    tags: list[tuple[float, float, float, float]] = []
    orig = mod._place_tag

    def spy(cv_: Any, x: float, y: float, off: float, tw: float, th: float, taken: Any, drop: bool = False) -> Any:
        pos = orig(cv_, x, y, off, tw, th, taken, drop)
        if pos is not None:
            tags.append((pos[0], pos[1], tw, th))
        return pos

    mod._place_tag = spy
    try:
        img = mod.render_minimap(st, mm[2], mm[3], now=0.3)
    finally:
        mod._place_tag = orig
    return img, tags


def _labels_on_corners(tags: list[tuple[float, float, float, float]], mm: RectT) -> int:
    """Label pixels placed on League's minimap corner buttons ("!" ping diamond, zoom button)."""
    corners = orr.minimap_corner_rects(mm[2], mm[3])
    return int(sum(L.overlap_area(tuple(int(round(v)) for v in t), tuple(int(round(v)) for v in c))
                   for t in tags for c in corners))


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for when in ("before", "after"):
        if not all(when in r for r in rows):
            continue
        real = [r for r in rows if r["real"]]
        out[when] = {
            "game_ui_overlaps_real": sum(len(r[when]["game"]) for r in real),
            "game_ui_overlaps_all": sum(len(r[when]["game"]) for r in rows),
            "shop_overlaps_all": sum(len(r[when]["shop"]) for r in rows),
            "ground_marker_over_transient_zones": sum(len(r[when].get("info", [])) for r in rows),
            "element_overlaps_all": sum(len(r[when]["ours"]) for r in rows),
            "shots_clean": sum(1 for r in rows if not r[when]["game"] and not r[when]["ours"]),
            "shots": len(rows),
        }
        by: dict[str, int] = {}
        for r in rows:
            for o in r[when]["game"] + r[when]["ours"]:
                key = o["element"].split("/")[0] + " x " + o["with"]
                by[key] = by.get(key, 0) + 1
        out[when]["by_pair"] = dict(sorted(by.items(), key=lambda kv: -kv[1]))
        mk = f"{when}_metrics"
        agg: dict[str, dict[str, list[float]]] = {}
        for r in rows:
            for name, m in r.get(mk, {}).items():
                a = agg.setdefault(name, {})
                for k, v in m.items():
                    if isinstance(v, (int, float)):
                        a.setdefault(k, []).append(float(v))
        out[when]["metrics"] = {n: {k: round(float(np.mean(v)), 2) if k != "contrast_worst" else round(float(min(v)), 1)
                                    for k, v in m.items()} for n, m in sorted(agg.items())}
    lab = [r["minimap_labels_on_buttons"] for r in rows if "minimap_labels_on_buttons" in r]
    if lab:
        out["minimap_label_px_on_corner_buttons"] = {"before": sum(x["before"] for x in lab),
                                                     "after": sum(x["after"] for x in lab)}
    return out


def _text_report(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = ["TreeAI Coach - audit du placement (tools/layout_audit.py)", ""]
    for when in ("before", "after"):
        s = summary.get(when)
        if not s:
            continue
        lines.append(f"== {when.upper()} ==")
        lines.append(f"  chevauchements UI du jeu (captures reelles) : {s['game_ui_overlaps_real']}")
        lines.append(f"  chevauchements UI du jeu (tout, synthetiques compris) : {s['game_ui_overlaps_all']}")
        lines.append(f"  chevauchements entre nos elements : {s['element_overlaps_all']}")
        lines.append(f"  boutique ouverte (zone souple) : {s['shop_overlaps_all']}")
        lines.append(f"  balise au sol sur une zone passagere (info) : {s['ground_marker_over_transient_zones']}")
        lines.append(f"  ecrans sans aucun chevauchement : {s['shots_clean']} / {s['shots']}")
        for pair, n in list(s["by_pair"].items())[:25]:
            lines.append(f"    {n:4d}  {pair}")
        lines.append("  lisibilite / trajet de l'oeil (moyennes ; contraste le pire = min) :")
        for name, m in s["metrics"].items():
            lines.append("    " + name.ljust(28) + "  ".join(f"{k}={v}" for k, v in m.items()))
        lines.append("")
    if "minimap_label_px_on_corner_buttons" in summary:
        m = summary["minimap_label_px_on_corner_buttons"]
        lines.append(f"Etiquettes de la minimap sur les boutons de coin (px) : avant {m['before']} -> apres {m['after']}")
    lines.append("")
    for r in rows:
        a = r["after"]
        flag = "OK " if not a["game"] and not a["ours"] else "!! "
        lines.append(f"{flag}{r['shot']}: " + ", ".join(f"{n}={v[:4]}({v[4]})" for n, v in r["slots"].items()))
        for o in a["game"] + a["ours"]:
            lines.append(f"      {o['element']} x {o['with']} ({o['area']} px)")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.layout_audit", description=__doc__.split("\n")[0])
    ap.add_argument("--shots", action="append", default=[], help="folder holding the real captures (repeatable)")
    ap.add_argument("--out", default="layout_audit_out", help="output folder (images + report)")
    ap.add_argument("--before-rev", default=DEFAULT_BEFORE, help="git revision of the old placement ('' = none)")
    ap.add_argument("--no-synthetic", action="store_true", help="real captures only")
    ap.add_argument("--no-images", action="store_true", help="numbers only")
    args = ap.parse_args(argv)
    dirs = [Path(d) for d in args.shots] + [REPO / "tests" / "fixtures"]
    shots = load_shots(dirs) + ([] if args.no_synthetic else synthetic_shots())
    legacy = load_legacy(args.before_rev) if args.before_rev else None
    if args.before_rev and legacy is None:
        print(f"(git revision {args.before_rev} unavailable: no 'before')")
    res = audit(shots, Path(args.out), legacy, images=not args.no_images)
    sys.stdout.write(_text_report(res["summary"], res["shots"]))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
