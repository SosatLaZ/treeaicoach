"""Overlay page preview (ui.py): the real overlay layers, laid out like ``overlay.py`` does in game.

Pure numpy / OpenCV / PIL (no Tk). :func:`compose` renders one 1920 x 1080 game-like screen with
the same renderers and placement rules as the in-game overlay (``overlay_render.render_minimap``
/ ``render_radar`` / ``render_hud`` / ``render_flash``, ``fx_render`` play badge, ``overlay``
geometry helpers) for the current settings, and returns the full screen plus the screen
rectangle of every layer. :func:`preview_images` turns that into what the Overlay page shows:

* ``screen``: the whole screen, small (where each element sits);
* ``minimap`` (or ``radar``) and ``hud``: the layers cut out of that same screen at a readable
  scale, over what is really under them (the game's minimap);
* ``badge``: the rated-play badge ("Coup de maître") when rated plays are on.

The screen under the layers is flat (:func:`_background`): graphite, League's permanent HUD panels
as flat blocks and the real minimap texture, no fake terrain, no blur (docs/DESIGN.md).

Nothing here is mocked up: when the engine runs, the page passes the live ``OverlayState``;
outside a game it uses ``overlay_render.sample_states()["danger"]`` (a gank in progress).
Never raises from the public API (returns what it could render).
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
from PIL import Image

log = logging.getLogger(__name__)

SCREEN = (0, 0, 1920, 1080)
SAMPLE_PLAY = ("brilliant", "Coup de maître", "Baron volé sous le nez du jungler")
_sample_cache: dict[Any, Any] = {}


@dataclass
class Composition:
    """One rendered screen (RGB, screen size) and the screen rect (x, y, w, h) of each layer."""

    screen: np.ndarray
    rects: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)
    live: bool = False
    layers: np.ndarray | None = None      # same screen without the danger flash (the cut-out tiles)


def sample_state() -> Any:
    """The "danger" sample state (cached: building it loads champion icons)."""
    st = _sample_cache.get("danger")
    if st is None:
        from treeaicoach import overlay_render as orr  # noqa: PLC0415

        states = orr.sample_states()
        st = states.get("danger") or next(iter(states.values()))
        _sample_cache["danger"] = st
    return st


#: the flat preview screen (docs/DESIGN.md: one flat colour, no picture, no blur, no gradient):
#: the screen, League's permanent HUD panels as flat blocks, the real minimap texture (sharp)
SCREEN_RGB = (27, 28, 32)          # ui_widgets.GRAPHITE.group  #1B1C20
PANEL_RGB = (35, 37, 41)           # GRAPHITE.control           #232529
PANEL_ZONES = ("bottom_bar", "scoreboard")
_bg_cache: dict[Any, np.ndarray] = {}


def _background(w: int, h: int, minimap: tuple[int, int, int, int]) -> np.ndarray:
    """The preview screen (RGB, a fresh copy): a flat graphite screen, League's always-visible HUD
    panels (ability bar, scoreboard: ``layout.game_zones``, measured on real captures) as flat
    blocks, and the official minimap texture, sharp, where the minimap is. Not a fake game
    picture: the overlay layers drawn over it are the only detail."""
    key = (int(w), int(h), tuple(int(v) for v in minimap))
    hit = _bg_cache.get(key)
    if hit is None:
        img = np.empty((h, w, 3), np.uint8)
        img[:] = SCREEN_RGB
        mx, my, mw, mh = (int(v) for v in minimap)
        try:
            from treeaicoach import layout as layout_mod  # noqa: PLC0415

            for z in layout_mod.game_zones((0, 0, w, h), (mx, my, mw, mh)):
                if z.key in PANEL_ZONES:
                    x, y, zw, zh = z.rect
                    img[max(0, y):y + zh, max(0, x):x + zw] = PANEL_RGB
        except Exception:
            log.debug("preview HUD zones unavailable", exc_info=True)
        try:
            from treeaicoach import overlay_render as orr  # noqa: PLC0415

            x0, y0 = max(0, mx), max(0, my)
            x1, y1 = min(w, mx + mw), min(h, my + mh)
            if x1 > x0 and y1 > y0 and mw > 4 and mh > 4:
                tex = cv2.resize(orr.default_radar_texture(), (mw, mh), interpolation=cv2.INTER_AREA)[..., ::-1]
                img[y0:y1, x0:x1] = (tex[y0 - my:y1 - my, x0 - mx:x1 - mx].astype(np.float32) * 0.8).astype(np.uint8)
        except Exception:
            log.debug("preview minimap texture unavailable", exc_info=True)
        if len(_bg_cache) > 4:
            _bg_cache.clear()
        img.setflags(write=False)
        _bg_cache[key] = hit = img
    return hit.copy()


def _with_cfg(state: Any, cfg: Any, live: bool) -> Any:
    """A copy of ``state`` with the display toggles of ``cfg`` applied (as the engine does)."""
    st = dataclasses.replace(state)
    if not live:
        for attr, fld, default in (("show_allies", "overlay_show_allies", False),
                                   ("show_roles", "overlay_show_roles", False),
                                   ("show_ghosts", "overlay_show_ghosts", False),
                                   ("show_last_seen", "overlay_show_last_seen", True),
                                   ("hud_detailed", "hud_detailed", False)):
            if hasattr(st, attr) and cfg is not None and hasattr(cfg, fld):
                setattr(st, attr, bool(getattr(cfg, fld, default)))
        mode = str(getattr(cfg, "fog_mode", "jungler") or "jungler")
        if mode == "off":
            st.fogs = []
        elif mode == "jungler" and len(st.fogs or []) > 1:
            st.fogs = list(st.fogs)[:1]
    if not bool(getattr(cfg, "danger_flash", True)):
        st.flash = 0.0
    return st


def _rect(r: Any) -> tuple[int, int, int, int] | None:
    try:
        if r is None:
            return None
        if hasattr(r, "w"):
            return int(r.x), int(r.y), int(r.w), int(r.h)
        x, y, w, h = (int(v) for v in r)
        return (x, y, w, h) if w > 0 and h > 0 else None
    except Exception:
        return None


def compose(cfg: Any, state: Any = None, now: float = 0.3,
            play: tuple[str, str, str] | None = SAMPLE_PLAY) -> Composition:
    """Render the screen with every enabled overlay layer for ``cfg``. Never raises."""
    from treeaicoach import overlay_render as orr  # noqa: PLC0415

    live = state is not None
    try:
        from treeaicoach import overlay as ov  # noqa: PLC0415
    except Exception:          # pragma: no cover - overlay.py always importable in the app
        ov = None
    try:
        st = _with_cfg(state if live else sample_state(), cfg, live)
        scr = _rect(getattr(st, "screen_rect", None)) or SCREEN
        mm = _rect(getattr(st, "minimap_rect", None)) or tuple(
            a + b for a, b in zip(orr.default_minimap_rect(scr[2], scr[3]), (scr[0], scr[1], 0, 0)))
        if ov is not None:
            scr = tuple(int(v) for v in ov.effective_screen(scr, mm))
        sx, sy, sw, sh = scr
        if sw * sh > 3840 * 2160 or sw < 320 or sh < 240:      # absurd live rect: fall back to 1080p
            scr, mm = SCREEN, orr.default_minimap_rect(1920, 1080)
            sx, sy, sw, sh = scr
        loc = (mm[0] - sx, mm[1] - sy, mm[2], mm[3])
        img = _background(sw, sh, loc)
        out = Composition(img, {"screen": (0, 0, sw, sh), "minimap": loc}, live)
        if not bool(getattr(cfg, "overlay_enabled", True)):
            return out
        mode = ov.resolve_overlay_mode(getattr(cfg, "overlay_mode", "minimap")) if ov is not None else "minimap"
        radar = None
        if mode == "minimap":
            # no frame label: the in-game minimap layer never draws it (overlay.py, show_frame=False)
            orr.composite_over(img, orr.render_minimap(st, mm[2], mm[3], now, show_frame=False), loc[0], loc[1])
        elif mode == "radar" and ov is not None and getattr(cfg, "radar_enabled", True):
            rx, ry, rs = ov.radar_geometry(mm, scr, float(getattr(cfg, "radar_scale", 1.0) or 1.0),
                                           getattr(cfg, "radar_position", "above_minimap"),
                                           getattr(cfg, "radar_xy", None))
            orr.composite_over(img, orr.render_radar(st, rs, None, now), rx - sx, ry - sy)
            radar = (rx, ry, rs, rs)
            out.rects["radar"] = (rx - sx, ry - sy, rs, rs)
        if getattr(cfg, "hud_enabled", True):
            hw = ov.hud_width(scr) if ov is not None else 340
            hud = orr.render_hud(st, hw, now)
            try:            # the in-game layout (treeaicoach.layout, same solver as overlay.py)
                from treeaicoach import layout as layout_mod  # noqa: PLC0415

                slot = layout_mod.layout_for(scr, mm, cfg, detailed=bool(getattr(st, "hud_detailed", False)),
                                             radar=radar).slot("card")
            except Exception:
                slot = None
            if slot is not None:
                hx, hy = slot.place(hud.shape[1], hud.shape[0])
            elif ov is not None:
                hx, hy = ov.hud_placement(scr, hud.shape[1], hud.shape[0],
                                          getattr(cfg, "hud_position", "above_minimap"), getattr(cfg, "hud_xy", None),
                                          avoid=[r for r in (mm, radar) if r is not None], anchor=radar or mm)
            else:
                hx, hy = sx + sw - hud.shape[1] - 24, mm[1] - hud.shape[0] - 12
            orr.composite_over(img, hud, hx - sx, hy - sy)
            a = hud[..., 3]
            ys, xs = np.nonzero(a > 8)
            if len(xs):
                out.rects["hud"] = (hx - sx + int(xs.min()), hy - sy + int(ys.min()),
                                    int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1))
        if bool(getattr(cfg, "essentials_strip", True)):
            _compose_essentials(out, img, st, cfg, scr, mm, radar, live)
        if play is not None and bool(getattr(cfg, "plays_enabled", True)):
            try:
                from treeaicoach import fx_render as fx  # noqa: PLC0415

                k = fx.scale_for_screen(scr)
                x, y, _w, _h = fx.fx_layer_rect(scr, mm, str(getattr(cfg, "plays_position", "top_center")), "big", k,
                                                cfg=cfg)
                frame = fx.render_frame(play[0], play[1], play[2], 1.2, size="big", scale=k)
                if frame is not None:
                    orr.composite_over(img, frame, x - sx, y - sy)
                    ys, xs = np.nonzero(frame[..., 3] > 8)
                    if len(xs):
                        out.rects["badge"] = (x - sx + int(xs.min()), y - sy + int(ys.min()),
                                              int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1))
            except Exception:
                log.debug("play badge preview failed", exc_info=True)
        out.layers = img.copy()
        if getattr(st, "flash", 0.0) and getattr(cfg, "danger_flash", True):
            th = ov.flash_thickness(scr, str(getattr(cfg, "danger_flash_style", "subtle") or "subtle")) \
                if ov is not None else 4
            orr.composite_over(img, orr.render_flash(sw, sh, float(st.flash), loc, thickness=th), 0, 0)
        return out
    except Exception:
        log.exception("overlay preview composition failed")
        bg = np.full((SCREEN[3], SCREEN[2], 3), 20, np.uint8)
        return Composition(bg, {"screen": (0, 0, SCREEN[2], SCREEN[3])}, live)


def _sample_edge() -> Any:
    """Numbers of the sample game for the essentials strip (outside a game)."""
    from treeaicoach.live_edge import EdgeStats  # noqa: PLC0415

    return EdgeStats(gold_diff=850, opponent="Darius", cs_min=6.8, cs_target=7.5, gold_min=402, win=0.62,
                     win_delta=8.0, win_reason="Baron")


def _compose_essentials(out: Composition, img: np.ndarray, st: Any, cfg: Any, scr: tuple, mm: tuple,
                        radar: Any, live: bool) -> None:
    """The essentials strip at its in-game slot (layout "essentials", top centre). Never raises."""
    try:
        from treeaicoach import essentials as ess  # noqa: PLC0415
        from treeaicoach import layout as layout_mod  # noqa: PLC0415

        if not live and getattr(st, "edge_stats", None) is None:
            st = dataclasses.replace(st, edge_stats=_sample_edge())
        slot = layout_mod.layout_for(scr, mm, cfg, detailed=bool(getattr(st, "hud_detailed", False)),
                                     radar=radar).slot("essentials")
        strip = ess.render(ess.build_lines(st), scr)
        if slot is None or strip is None:
            return
        from treeaicoach import overlay_render as orr  # noqa: PLC0415

        x, y = slot.place(strip.shape[1], strip.shape[0])
        orr.composite_over(img, strip, x - scr[0], y - scr[1])
        out.rects["essentials"] = (x - scr[0], y - scr[1], strip.shape[1], strip.shape[0])
    except Exception:
        log.debug("essentials preview failed", exc_info=True)


def _crop(img: np.ndarray, r: tuple[int, int, int, int], pad: int) -> np.ndarray:
    H, W = img.shape[:2]
    x, y, w, h = r
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(W, x + w + pad), min(H, y + h + pad)
    return img[y0:y1, x0:x1]


def _resize(img: np.ndarray, w: int) -> np.ndarray:
    w = max(8, int(w))
    h = max(1, int(round(img.shape[0] * w / max(1, img.shape[1]))))
    interp = cv2.INTER_AREA if w < img.shape[1] else cv2.INTER_LINEAR
    return cv2.resize(img, (w, h), interpolation=interp)


def preview_images(comp: Composition, screen_w: int = 420, zoom: float = 0.62) -> dict[str, Image.Image]:
    """PIL images for the Overlay page: ``screen`` (whole screen, ``screen_w`` px wide), and the
    ``minimap`` / ``radar`` / ``hud`` / ``badge`` layers cut out of it at ``zoom``. Never raises."""
    out: dict[str, Image.Image] = {}
    try:
        out["screen"] = Image.fromarray(np.ascontiguousarray(_resize(comp.screen, screen_w)), "RGB")
        img = comp.layers if comp.layers is not None else comp.screen    # tiles: without the edge flash
        for key, pad in (("hud", 6), ("minimap", 6), ("radar", 6), ("badge", 4), ("essentials", 8)):
            r = comp.rects.get(key)
            if r is None:
                continue
            part = _crop(img, r, pad)
            if part.size:
                out[key] = Image.fromarray(np.ascontiguousarray(_resize(part, part.shape[1] * zoom)), "RGB")
    except Exception:
        log.exception("overlay preview images failed")
    return out


__all__ = ["Composition", "SAMPLE_PLAY", "compose", "preview_images", "sample_state"]
