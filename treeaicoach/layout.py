"""Where TreeAI draws in game: League's own UI zones + ONE small placement solver.

Pure geometry (no Win32, no image): shared by the overlay thread (:mod:`treeaicoach.overlay`),
the play badges (:mod:`treeaicoach.fx_overlay` / :mod:`treeaicoach.fx_render`), the toasts
(:mod:`treeaicoach.toasts`), the previews and ``tools/layout_audit.py``.

League's UI zones (:func:`game_zones`)
    Measured on real 2026 captures (users' 2560 x 1440 screenshots shown at 2000 x 1125, stream
    captures at 1920 x 1080 and 1280 x 720; see ``tools/layout_audit.py``). Sizes are in *UI
    units*: League scales its HUD with the screen height (16:9 reference, ``U = min(h, w*9/16)``),
    anchors it to the screen edges / centre, and attaches the ally portraits, the vote panels and
    the mute / camera / settings buttons to the minimap frame, whose rectangle we measure. The
    ``hud_scale`` factor (1.0 = the layout of those captures) grows / shrinks the HUD-sized zones.
    Zones: minimap + frame, its buttons, ally portraits row (above the minimap) or column (left
    edge), vote panels (surrender / Baron), kill feed, scoreboard (top right), kill announcer
    (top centre), spells / items bar, stats panel, chat, death recap, respawn panel ("RETOUR
    DANS"), shop (soft: modal, opened by the player).

Solver (:func:`solve`)
    Elements in priority order (HUD card, toast / banner layer, timers strip, big then small play
    badge). Each one has *rails*: segments along which its window may slide (e.g. the card: left
    of the minimap, bottom-aligned, sliding up), grouped in tiers tried in order of preference;
    the rails of a tier compete on cost (distance travelled + the rail's bias, + a small penalty
    when the position only overlaps the open shop, a soft zone). The cheapest position whose
    content box keeps a few px off every game zone and every element already placed wins. Zones
    that only exist in some states (votes, death recap, respawn panel, stats panel) are tolerated
    only for the player's named card position and the F6 card, as a last resort; nothing free at
    all: the least-overlap position (``Slot.hits`` says what it covers). Our elements never
    overlap each other. Slots are sized for the element's LARGEST content in the current mode
    (compact / detailed card, 3 / 5 timer rows, the badges' and toasts' animations), so a
    changing text never moves anything: the layout only changes with the screen, the minimap
    rectangle, the settings, the detailed mode or a dragged window (:class:`LayoutCache` /
    :func:`layout_for` memoize it).

Never raises from the public API (falls back to an empty layout / the inputs).
"""

from __future__ import annotations

import logging
import math
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

log = logging.getLogger(__name__)

RectT = tuple[int, int, int, int]

#: Reference height of League's UI (its HUD scales with the screen height, 16:9).
REF_H = 1080.0
#: Gap (UI px at 1080p) between our elements and the game's UI / each other, and screen margin.
GAP = 8.0
MARGIN = 16.0
#: Rail step (UI px at 1080p): a rail is scanned every STEP px (positions are stable anyway).
STEP = 4.0

ZONE_LABELS: dict[str, str] = {
    "minimap": "Minimap + cadre",
    "minimap_buttons": "Boutons micro / caméra / réglages",
    "ally_row": "Portraits alliés (au-dessus de la minimap)",
    "votes": "Votes (reddition, Baron)",
    "kill_feed": "Fil des kills",
    "scoreboard": "Score, KDA, chrono",
    "announcer": "Annonces (kills, objectifs)",
    "bottom_bar": "Sorts, objets, portrait",
    "stats_panel": "Statistiques (touche C)",
    "chat": "Chat",
    "team_frames": "Portraits alliés (colonne de gauche)",
    "death_recap": "Récap de mort",
    "respawn": "Retour dans (mort)",
    "shop": "Boutique",
}
#: Zones that only exist in some states (relaxed last, never for the default card).
CONDITIONAL = frozenset({"votes", "death_recap", "respawn", "stats_panel"})
SOFT = frozenset({"shop"})

#: Element names (priority order) and the rails of the named card positions.
ELEMENTS = ("card", "toasts", "timers", "badge_big", "badge_small")
CARD_POSITIONS = ("left_of_minimap", "above_minimap", "top_left", "top_right", "left_middle", "custom")


# ======================================================================================
# Small rectangle helpers
# ======================================================================================
def as_rect(r: Any) -> RectT | None:
    """``Rect`` / 4-sequence -> ``(x, y, w, h)`` ints, None when invalid / empty."""
    if r is None:
        return None
    try:
        vals = (r.x, r.y, r.w, r.h) if hasattr(r, "x") else tuple(r)[:4]
        if len(vals) != 4:
            return None
        fl = [float(v) for v in vals]
        if not all(math.isfinite(v) for v in fl):
            return None
        x, y, w, h = (int(round(v)) for v in fl)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


def overlap(a: Sequence[int], b: Sequence[int]) -> bool:
    """True when two ``(x, y, w, h)`` rectangles share at least one pixel."""
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def overlap_area(a: Sequence[int], b: Sequence[int]) -> int:
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return int(w * h) if w > 0 and h > 0 else 0


def inside(r: Sequence[int], screen: Sequence[int]) -> bool:
    return r[0] >= screen[0] and r[1] >= screen[1] and r[0] + r[2] <= screen[0] + screen[2] \
        and r[1] + r[3] <= screen[1] + screen[3]


def clamp_xy(x: float, y: float, w: int, h: int, screen: Sequence[int]) -> tuple[int, int]:
    sx, sy, sw, sh = screen[:4]
    return (int(min(max(round(x), sx), sx + max(0, sw - w))),
            int(min(max(round(y), sy), sy + max(0, sh - h))))


def _box(x0: float, y0: float, x1: float, y1: float) -> RectT:
    xa, xb = sorted((x0, x1))
    ya, yb = sorted((y0, y1))
    return int(math.floor(xa)), int(math.floor(ya)), max(1, int(math.ceil(xb - xa))), max(1, int(math.ceil(yb - ya)))


def ui_unit(screen: Sequence[int]) -> float:
    """League's UI unit: ``min(h, w * 9 / 16)`` px (1080 at 1080p, 1440 at 3440 x 1440)."""
    try:
        return float(max(200.0, min(float(screen[3]), float(screen[2]) * 9.0 / 16.0)))
    except Exception:
        return REF_H


def minimap_side(screen: Any, minimap: Any, flip: Any = None) -> str:
    """"left" / "right": ``FlipMiniMap`` from the game's settings when known, else the half of the
    screen holding the minimap's centre (right when unknown)."""
    if flip is not None:
        return "left" if bool(flip) else "right"
    scr, mm = as_rect(screen), as_rect(minimap)
    if scr is None or mm is None:
        return "right"
    return "left" if mm[0] + mm[2] / 2.0 < scr[0] + scr[2] / 2.0 else "right"


# ======================================================================================
# League's UI zones
# ======================================================================================
@dataclass(frozen=True)
class Zone:
    """One area of League's own UI on screen (``rect`` in screen px)."""

    key: str
    rect: RectT
    label: str = ""
    soft: bool = False          # modal (shop): avoided when possible
    conditional: bool = False   # only in some states (dead, vote...)


def minimap_frame(screen: Any, minimap: Any, side: str = "right") -> RectT | None:
    """The minimap with its frame and corner notch ("!" button), extended to the screen corner."""
    scr, mm = as_rect(screen), as_rect(minimap)
    if scr is None or mm is None:
        return None
    sx, sy, sw, sh = scr
    mx, my, mw, mh = mm
    fx = 0.06 * mw
    top = my - 0.11 * mh                      # the frame notch / "!" diamond sit above the map
    bottom = max(sy + sh, my + mh)
    if side == "left":
        return _box(min(sx, mx), top, mx + mw + fx, bottom)
    return _box(mx - fx, top, max(sx + sw, mx + mw), bottom)


def game_zones(screen: Any, minimap: Any, side: str | None = None, hud_scale: float = 1.0,
               flip: Any = None) -> list[Zone]:
    """League's UI zones for this screen / minimap (see the module docstring). Never raises."""
    try:
        scr = as_rect(screen)
        if scr is None:
            return []
        mm = as_rect(minimap)
        side = side or minimap_side(scr, mm, flip)
        sx, sy, sw, sh = scr
        U = ui_unit(scr)
        try:
            k = float(hud_scale) if hud_scale is not None else 1.0
            k = k if math.isfinite(k) and k > 0 else 1.0
        except (TypeError, ValueError):
            k = 1.0
        k = min(max(k, 0.6), 1.6)
        u = U * k
        cx = sx + sw / 2.0
        right, bottom = sx + sw, sy + sh
        out: list[Zone] = []

        def add(key: str, x0: float, y0: float, x1: float, y1: float) -> None:
            r = _box(x0, y0, x1, y1)
            # clip to the screen
            x, y = max(r[0], sx), max(r[1], sy)
            x2, y2 = min(r[0] + r[2], right), min(r[1] + r[3], bottom)
            if x2 > x and y2 > y:
                out.append(Zone(key, (x, y, x2 - x, y2 - y), ZONE_LABELS.get(key, key), key in SOFT,
                                key in CONDITIONAL))

        def mirror(x0: float, x1: float) -> tuple[float, float]:
            """x-range mirrored to the left screen edge for a left-side minimap."""
            if side != "left":
                return x0, x1
            return sx + (right - x1), sx + (right - x0)

        # ---- the minimap block (frame, buttons, ally portraits, vote panels)
        frame = minimap_frame(scr, mm, side)
        if frame is not None:
            out.append(Zone("minimap", frame, ZONE_LABELS["minimap"]))
            fx0, fy0 = frame[0], frame[1]
            if side == "left":
                inner = frame[0] + frame[2]
                add("minimap_buttons", inner, bottom - 0.042 * u, inner + 0.10 * u, bottom)
                col_w = frame[2] + 0.06 * u
            else:
                inner = fx0
                add("minimap_buttons", inner - 0.10 * u, bottom - 0.042 * u, inner, bottom)
                col_w = right - fx0 + 0.06 * u
            x0, x1 = mirror(right - max(0.35 * u, col_w), right)
            add("ally_row", x0, fy0 - 0.095 * u, x1, fy0 + 0.015 * u)      # level / HP bars reach the frame
            x0, x1 = mirror(right - max(0.30 * u, col_w - 0.06 * u), right)
            add("votes", x0, fy0 - 0.28 * u, x1, fy0)
        # ---- right edge / top
        add("kill_feed", right - 0.25 * u, sy + 0.20 * u, right, sy + 0.43 * u)
        add("scoreboard", right - 0.37 * u, sy, right, sy + 0.062 * u)
        add("announcer", cx - 0.32 * u, sy + 0.072 * u, cx + 0.32 * u, sy + 0.142 * u)
        # ---- bottom centre
        add("bottom_bar", cx - 0.30 * u, bottom - 0.135 * u, cx + 0.30 * u, bottom)
        add("stats_panel", cx - 0.42 * u, bottom - 0.18 * u, cx - 0.26 * u, bottom)
        add("respawn", cx - 0.14 * u, bottom - 0.335 * u, cx + 0.14 * u, bottom - 0.165 * u)
        # ---- left side (the chat moves away from a left-side minimap)
        if side == "left":
            add("chat", right - 0.34 * u, bottom - 0.455 * u, right - 0.03 * u, bottom - 0.285 * u)
        else:
            add("chat", sx + 0.03 * u, bottom - 0.455 * u, sx + 0.34 * u, bottom - 0.285 * u)
        add("team_frames", sx, sy + 0.145 * u, sx + 0.075 * u, sy + 0.53 * u)
        add("death_recap", sx + 0.215 * u, sy, sx + 0.475 * u, sy + 0.48 * u)
        add("shop", cx - 0.56 * u, sy + 0.14 * u, cx + 0.51 * u, sy + 0.84 * u)
        return out
    except Exception:
        log.exception("game_zones failed")
        return []


def zone_rects(zones: Iterable[Zone], *, soft: bool = True, conditional: bool = True,
               keys: Iterable[str] | None = None) -> list[RectT]:
    """Rectangles of ``zones`` (filters: keep soft / conditional ones, or only some keys)."""
    ks = set(keys) if keys is not None else None
    return [z.rect for z in zones if (soft or not z.soft) and (conditional or not z.conditional)
            and (ks is None or z.key in ks)]


#: Always-on parts of League's HUD (in-world ground markers must never cover them; the ally
#: portraits are either the row above the minimap or the column on the left, both kept).
HUD_KEYS = ("minimap", "minimap_buttons", "bottom_bar", "scoreboard", "ally_row", "team_frames")


# ======================================================================================
# Layout
# ======================================================================================
@dataclass(frozen=True)
class Slot:
    """Where one element goes: its window ``rect``, the ``content`` box where pixels can appear
    (collision box), the rail used, how a smaller image sits in the slot (``valign`` "top" /
    "bottom" / "center", ``halign`` "left" / "right" / "center") and what it still overlaps."""

    name: str
    rect: RectT
    content: RectT
    anchor: str = ""
    valign: str = "top"
    halign: str = "left"
    hits: tuple[str, ...] = ()

    def place(self, w: int, h: int) -> tuple[int, int]:
        """Top-left of a ``w x h`` image inside the slot (its alignment; never moves the slot)."""
        x, y, sw, sh = self.rect
        if self.halign == "right":
            px = x + sw - w
        elif self.halign == "center":
            px = x + (sw - w) // 2
        else:
            px = x
        if self.valign == "bottom":
            py = y + sh - h
        elif self.valign == "center":
            py = y + (sh - h) // 2
        else:
            py = y
        return int(px), int(py)


@dataclass(frozen=True)
class Layout:
    """One solved layout: the screen, the minimap, League's zones and our slots."""

    screen: RectT
    minimap: RectT | None
    side: str
    unit: float
    zones: tuple[Zone, ...] = ()
    slots: dict = field(default_factory=dict)
    key: tuple = ()

    def slot(self, name: str) -> Slot | None:
        return self.slots.get(name)

    def rect(self, name: str) -> RectT | None:
        s = self.slots.get(name)
        return s.rect if s is not None else None

    def avoid(self, *, zones: bool = True, hud_only: bool = False, slots: bool = True,
              except_slots: Iterable[str] = ()) -> list[RectT]:
        """Rectangles an in-world marker must stay off: League's zones (all of them, or only the
        always-on HUD) and our slots' content boxes."""
        out: list[RectT] = []
        if zones:
            out += zone_rects(self.zones, keys=HUD_KEYS if hud_only else None, soft=False)
        if slots:
            skip = set(except_slots)
            out += [s.content for n, s in self.slots.items() if n not in skip]
        return out

    def flash_exclusions(self) -> list[RectT]:
        """Screen rectangles the danger flash never covers (minimap block, its buttons, the
        spells / items bar: HP and cooldowns stay readable)."""
        return zone_rects(self.zones, keys=("minimap", "minimap_buttons", "bottom_bar"))


@dataclass(frozen=True)
class ElementSpec:
    """Size of one element's window and its content box inside it (``envelope``: x, y, w, h
    relative to the window; None = the whole window)."""

    w: int
    h: int
    envelope: RectT | None = None

    def content(self, x: int, y: int) -> RectT:
        if self.envelope is None:
            return x, y, self.w, self.h
        ex, ey, ew, eh = self.envelope
        return x + ex, y + ey, ew, eh


@dataclass(frozen=True)
class Prefs:
    """Placement settings (from the config): card position, plays position, enabled parts."""

    hud_enabled: bool = True
    hud_position: str = "left_of_minimap"
    hud_xy: tuple[int, int] | None = None
    plays_position: str = "top_center"
    timers: bool = True
    toasts: bool = True
    badges: bool = True
    detailed: bool = False
    hud_scale: float = 1.0
    flip: bool | None = None
    radar: RectT | None = None       # radar window when shown (fixed obstacle)

    @classmethod
    def from_cfg(cls, cfg: Any, detailed: bool | None = None, radar: Any = None,
                 custom_card: Any = None, flip: Any = None, hud_scale: Any = None) -> "Prefs":
        """Prefs of a ``Config``-like object (any missing field = default). ``custom_card``: an
        ``(x, y)`` the user just dragged the card to (move mode)."""
        try:
            pos = str(getattr(cfg, "hud_position", "left_of_minimap") or "left_of_minimap")
            pos = pos if pos in CARD_POSITIONS else "left_of_minimap"
            xy = custom_card if custom_card is not None else (getattr(cfg, "hud_xy", None) if pos == "custom" else None)
            if custom_card is not None:
                pos = "custom"
            xy_t = None
            if xy is not None:
                try:
                    xy_t = (int(round(float(list(xy)[0]))), int(round(float(list(xy)[1]))))
                except (TypeError, ValueError, IndexError):
                    xy_t = None
            if pos == "custom" and xy_t is None:
                pos = "left_of_minimap"
            try:
                hs = float(hud_scale) if hud_scale is not None else 1.0
                hs = hs if math.isfinite(hs) else 1.0
            except (TypeError, ValueError):
                hs = 1.0
            det = bool(getattr(cfg, "hud_detailed", False)) if detailed is None else bool(detailed)
            return cls(hud_enabled=bool(getattr(cfg, "hud_enabled", True)) and bool(getattr(cfg, "overlay_enabled", True)),
                       hud_position=pos, hud_xy=xy_t,
                       plays_position=str(getattr(cfg, "plays_position", "top_center") or "top_center"),
                       timers=bool(getattr(cfg, "overlay_timers", True)),
                       toasts=bool(getattr(cfg, "toasts_enabled", True)),
                       badges=bool(getattr(cfg, "plays_enabled", True)),
                       detailed=det, hud_scale=hs, flip=None if flip is None else bool(flip),
                       radar=as_rect(radar))
        except Exception:
            log.debug("Prefs.from_cfg failed", exc_info=True)
            return cls()


# ---------------------------------------------------------------------------------- rails
@dataclass(frozen=True)
class _Rail:
    """Positions (window top-left) along a segment, in order of preference. Rails sharing a
    ``tier`` compete on cost (distance travelled + ``bias`` px); tiers are tried in order (a rail
    without tier is a tier of its own)."""

    name: str
    start: tuple[float, float]
    end: tuple[float, float]
    tier: str | None = None
    bias: float = 0.0

    def points(self, step: float) -> list[tuple[float, float]]:
        (x0, y0), (x1, y1) = self.start, self.end
        d = math.hypot(x1 - x0, y1 - y0)
        n = int(d // max(1.0, step))
        pts = [(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) for i in range(n + 1)] if n > 0 else [(x0, y0)]
        if n > 0 and pts[-1] != (x1, y1):
            pts.append((x1, y1))
        return pts


class _Ctx:
    """Everything the rails need (screen, frame, zones, unit) for one solve."""

    def __init__(self, scr: RectT, mm: RectT | None, side: str, zones: list[Zone], prefs: Prefs) -> None:
        self.scr, self.mm, self.side, self.zones, self.prefs = scr, mm, side, zones, prefs
        self.U = ui_unit(scr)
        self.u = self.U / REF_H
        self.g = max(4, int(round(GAP * self.u)))
        self.pad = max(2, self.g // 2)          # breathing room around League's zones / our elements
        self.m = max(6, int(round(MARGIN * self.u)))
        self.step = max(2.0, STEP * self.u)
        z = {zz.key: zz.rect for zz in zones}
        self.z = z
        sx, sy, sw, sh = scr
        self.frame = z.get("minimap")
        # inner edge of the minimap block (towards the screen centre) and its top
        if self.frame is not None:
            self.inner = self.frame[0] if side != "left" else self.frame[0] + self.frame[2]
            self.ftop = self.frame[1]
        else:
            self.inner = sx + sw - self.m if side != "left" else sx + self.m
            self.ftop = sy + sh - self.m
        btn = z.get("minimap_buttons")
        self.ybot = (btn[1] if btn is not None else sy + sh) - self.g
        if self.frame is None:
            self.ybot = sy + sh - self.m
        ann = z.get("announcer")
        self.ann_bottom = (ann[1] + ann[3]) if ann is not None else sy + int(0.142 * self.U)
        self.ann_top = ann[1] if ann is not None else sy + int(0.072 * self.U)
        sb = z.get("scoreboard")
        self.score_bottom = (sb[1] + sb[3]) if sb is not None else sy + int(0.062 * self.U)

    def inner_x(self, w: int, edge: float | None = None) -> float:
        """x of a ``w`` wide window hugging ``edge`` (default: the minimap block) on the inner side."""
        e = self.inner if edge is None else edge
        return e - self.g - w if self.side != "left" else e + self.g

    def outer_x(self, w: int) -> float:
        """x of a ``w`` wide window against the minimap's screen edge (right edge for a right minimap)."""
        sx, _sy, sw, _sh = self.scr
        return sx + sw - self.m - w if self.side != "left" else sx + self.m


def _card_rails(c: _Ctx, w: int, h: int, pos: str) -> list[tuple[_Rail, str, int]]:
    """Rails of the HUD card for a named position: ``(rail, valign, max relax pass)``."""
    sx, sy, sw, sh = c.scr
    U = c.U
    x_in = c.inner_x(w)
    default = (_Rail("left_of_minimap", (x_in, c.ybot - h), (x_in, sy + c.m)), "bottom", 1)
    if c.prefs.detailed and pos != "custom":
        # the tall detailed card (held with F6, a few seconds): next to the minimap, else left of
        # League's portraits / votes column; it may cover a state-only zone as a last resort
        col = [c.z[k] for k in ("ally_row", "votes") if k in c.z]
        edge = (min(r[0] for r in col) if c.side != "left" else max(r[0] + r[2] for r in col)) if col else None
        x_col = c.inner_x(w, edge) if edge is not None else x_in
        kf = c.z.get("kill_feed")
        x_kf = (kf[0] - c.g - w) if kf is not None else sx + sw - c.m - w
        rails = [(_Rail("left_of_minimap", (x_in, c.ybot - h), (x_in, sy + c.m), "f6"), "bottom", 1),
                 (_Rail("left_of_column", (x_col, c.ybot - h), (x_col, sy + c.m), "f6", 0.05 * U), "bottom", 1),
                 (_Rail("under_scoreboard", (x_kf, c.score_bottom + c.g), (x_kf, c.score_bottom + c.g + 0.3 * U),
                        "f6", 0.25 * U), "top", 1),
                 (_Rail("left_of_minimap", (x_in, c.ybot - h), (x_in, sy + c.m), "f6_any"), "bottom", 2),
                 (_Rail("left_of_column", (x_col, c.ybot - h), (x_col, sy + c.m), "f6_any", 0.05 * U), "bottom", 2)]
        if pos in ("left_of_minimap", "above_minimap"):
            return rails
        return [r for r in _card_rails_named(c, w, h, pos, default)] + rails
    return _card_rails_named(c, w, h, pos, default)


def _card_rails_named(c: _Ctx, w: int, h: int, pos: str, default: tuple[_Rail, str, int]
                      ) -> list[tuple[_Rail, str, int]]:
    sx, sy, sw, sh = c.scr
    U = c.U
    x_in = c.inner_x(w)
    if pos == "above_minimap":
        return [(_Rail("above_minimap", (x_in, c.ftop + c.g), (x_in, c.ybot - h)), "top", 2), default]
    if pos == "top_right":
        x = sx + sw - c.m - w
        y0 = c.score_bottom + c.g
        return [(_Rail("top_right", (x, y0), (x, y0 + 0.25 * U)), "top", 2),
                (_Rail("top_right_left", (x, y0), (x - 0.35 * U, y0)), "top", 2), default]
    if pos == "top_left":
        tf = c.z.get("team_frames")
        x0 = (tf[0] + tf[2] + c.g) if tf is not None else sx + c.m
        return [(_Rail("top_left", (x0, sy + c.m), (x0, sy + c.m + 0.25 * U)), "top", 2),
                (_Rail("top_left_right", (x0, sy + c.m), (x0 + 0.40 * U, sy + c.m)), "top", 2), default]
    if pos == "left_middle":
        tf = c.z.get("team_frames")
        x0 = (tf[0] + tf[2] + c.g) if tf is not None else sx + c.m
        ym = sy + (sh - h) / 2.0
        return [(_Rail("left_middle", (x0, ym), (x0, ym - 0.2 * U)), "center", 2),
                (_Rail("left_middle_down", (x0, ym), (x0, ym + 0.2 * U)), "center", 2), default]
    return [default, (_Rail("top_right", (sx + sw - c.m - w, c.score_bottom + c.g),
                            (sx + sw - c.m - w, sy + sh * 0.5)), "top", 1)]


def _toast_rails(c: _Ctx, spec: ElementSpec) -> list[tuple[_Rail, str, int]]:
    sx, sy, sw, sh = c.scr
    ey = spec.envelope[1] if spec.envelope is not None else 0
    eh = spec.envelope[3] if spec.envelope is not None else spec.h
    x = sx + (sw - spec.w) / 2.0
    below = c.ann_bottom + c.g - ey               # content top right under the kill announcer
    top_band = sy + c.m * 0.5 - ey                # above the announcer (only if it fits there)
    return [(_Rail("below_announcer", (x, below), (x, below + 0.10 * c.U)), "top", 1),
            (_Rail("top_band", (x, top_band), (x, top_band)), "top", 1),
            (_Rail("below_announcer_far", (x, below), (x, sy + 0.42 * sh - ey - eh)), "top", 1)]


def _timers_rails(c: _Ctx, spec: ElementSpec, card: Slot | None) -> list[tuple[_Rail, str, int]]:
    sx, sy, sw, sh = c.scr
    x_in = c.inner_x(spec.w)
    rails = [(_Rail("minimap_top", (x_in, c.ftop + c.g), (x_in, c.ybot - spec.h)), "top", 1)]
    if card is not None:
        cx_ = card.rect[0] - c.g - spec.w if c.side != "left" else card.rect[0] + card.rect[2] + c.g
        rails.append((_Rail("beside_card", (cx_, card.content[1]), (cx_, card.content[1] - 0.25 * c.U)), "top", 1))
    rails.append((_Rail("top_right", (c.outer_x(spec.w), c.score_bottom + c.g),
                        (c.outer_x(spec.w), c.score_bottom + c.g + 0.1 * c.U)), "top", 1))
    return rails


def _badge_rails(c: _Ctx, spec: ElementSpec, small: bool, slots: dict[str, Slot]) -> list[tuple[_Rail, str, int]]:
    sx, sy, sw, sh = c.scr
    rails: list[tuple[_Rail, str, int]] = []
    x_c = sx + (sw - spec.w) / 2.0
    ey = spec.envelope[1] if spec.envelope is not None else 0
    toast = slots.get("toasts")
    y_top = (toast.content[1] + toast.content[3] + c.g - ey) if toast is not None else c.ann_bottom + c.g - ey
    centre = [(_Rail("top_center", (x_c, y_top), (x_c, y_top + 0.12 * c.U)), "center", 1)]
    near = []
    x_in = c.inner_x(spec.w)
    card = slots.get("card")
    y_hi = c.ybot - spec.h
    if card is not None and abs(card.rect[0] + card.rect[2] / 2 - (x_in + spec.w / 2)) < 0.6 * c.U:
        y_hi = card.content[1] - c.g - spec.h
    near.append((_Rail("minimap_side", (x_in, y_hi), (x_in, c.ftop + c.g), "near"), "bottom", 1))
    tim = slots.get("timers")
    if tim is not None:
        x_t = tim.content[0] - c.g - spec.w if c.side != "left" else tim.content[0] + tim.content[2] + c.g
        near.append((_Rail("beside_timers", (x_t, tim.content[1]), (x_t, tim.content[1] + 0.2 * c.U), "near",
                           0.04 * c.U), "top", 1))
        near.append((_Rail("beside_timers_up", (x_t, tim.content[1]), (x_t, tim.content[1] - 0.3 * c.U), "near",
                           0.06 * c.U), "bottom", 1))
    if card is not None:
        x_k = card.content[0] - c.g - spec.w if c.side != "left" else card.content[0] + card.content[2] + c.g
        yb = card.content[1] + card.content[3] - spec.h
        near.append((_Rail("beside_card", (x_k, yb), (x_k, yb - 0.25 * c.U), "near", 0.08 * c.U), "bottom", 1))
    if small or c.prefs.plays_position == "minimap":
        return near + centre
    return centre + near


def _hits(content: RectT, zones: list[Zone], placed: dict[str, Slot], scr: RectT, relax: int) -> list[str]:
    """Names of what ``content`` overlaps (zones allowed by the relax pass, placed slots, off-screen)."""
    out = []
    if not inside(content, scr):
        out.append("screen")
    for z in zones:
        if (z.soft and relax >= 1) or (z.conditional and relax >= 2):
            continue
        if overlap(content, z.rect):
            out.append(z.key)
    for n, s in placed.items():
        if overlap(content, s.content):
            out.append(n)
    return out


#: Extra "travel" (UI px at 1080p) an element accepts to stay off the open shop (soft zone): a
#: position that only overlaps the shop costs this much more than a free one on the same rail.
SOFT_COST = 65.0


def _grow(r: Sequence[int], d: int) -> RectT:
    return r[0] - d, r[1] - d, r[2] + 2 * d, r[3] + 2 * d


def _level(content: RectT, zones: list[Zone], placed: dict[str, Slot], scr: RectT, pad: int = 0) -> int | None:
    """0: free; 1: only overlaps the soft shop; 2: also zones that exist in some states only;
    None: overlaps League's always-on UI, one of our elements, or leaves the screen. ``pad``:
    breathing room kept around League's zones and our elements (px)."""
    if not inside(content, scr):
        return None
    probe = _grow(content, pad) if pad > 0 else content
    if any(overlap(probe, s.content) for s in placed.values()):
        return None
    lvl = 0
    for z in zones:
        if overlap(probe, z.rect):
            if z.soft:
                lvl = max(lvl, 1)
            elif z.conditional:
                lvl = 2
            else:
                return None
    return lvl


def _place(name: str, spec: ElementSpec, rails: list[tuple[_Rail, str, int]], c: _Ctx,
           placed: dict[str, Slot], halign: str) -> Slot:
    """Best position of the first tier of rails that has one (tiers in order of preference; the
    rails of one tier compete). On a rail, a point's cost is the distance travelled from the rail's
    start + the rail's bias, + :data:`SOFT_COST` when it overlaps the open shop; points overlapping
    a state-only zone (vote, death recap...) are allowed only on rails whose limit is 2 (the
    user's named card position, the F6 card) and only when the tier has nothing better. Nothing
    anywhere: the least bad position (overlap weighted: our own elements x16, League's always-on
    UI x4, the rest x1), its ``hits`` say what it covers."""
    best_bad: tuple[int, Slot] | None = None
    soft_cost = SOFT_COST * c.u
    tiers: list[list[tuple[_Rail, str, int]]] = []
    names: list[str | None] = []
    for item in rails:
        t = item[0].tier
        if t is not None and t in names:
            tiers[names.index(t)].append(item)
        else:
            tiers.append([item])
            names.append(t)
    for tier in tiers:
        best: tuple[float, int, Slot] | None = None
        for rail, valign, max_relax in tier:
            x_prev = y_prev = None
            travel = float(rail.bias)
            for px, py in rail.points(c.step):
                x, y = clamp_xy(px, py, spec.w, spec.h, c.scr)
                if x_prev is not None:
                    travel += math.hypot(x - x_prev, y - y_prev)
                x_prev, y_prev = x, y
                if best is not None and travel >= best[0]:
                    break                       # this rail can only do worse from here
                content = spec.content(x, y)
                lvl = _level(content, c.zones, placed, c.scr, c.pad)
                if lvl is not None and lvl <= max_relax:
                    cost = travel + (soft_cost if lvl == 1 else 0.0) + (1e6 if lvl == 2 else 0.0)
                    if best is None or cost < best[0]:
                        best = (cost, lvl, Slot(name, (x, y, spec.w, spec.h), content,
                                                rail.name if lvl == 0 else f"{rail.name}~{lvl}", valign, halign,
                                                tuple(z.key for z in c.zones if overlap(content, z.rect))))
                    if lvl == 0:
                        break                   # further points only travel more
                else:
                    bad = sum(overlap_area(content, z.rect) * (1 if (z.soft or z.conditional) else 4)
                              for z in c.zones) + 16 * sum(overlap_area(content, s.content) for s in placed.values())
                    if not inside(content, c.scr):
                        bad += 10 ** 7
                    if best_bad is None or bad < best_bad[0]:
                        hits = tuple(_hits(content, c.zones, placed, c.scr, 0))
                        best_bad = (bad, Slot(name, (x, y, spec.w, spec.h), content, rail.name + "!", valign, halign,
                                              hits))
        if best is not None:
            return best[2]
    if best_bad is not None:
        return best_bad[1]
    x, y = clamp_xy(c.scr[0], c.scr[1], spec.w, spec.h, c.scr)
    return Slot(name, (x, y, spec.w, spec.h), spec.content(x, y), "fallback!", "top", halign, ("unplaced",))


def solve(screen: Any, minimap: Any, specs: dict[str, ElementSpec], prefs: Prefs | None = None,
          zones: list[Zone] | None = None) -> Layout:
    """Place every element of ``specs`` ("card", "toasts", "timers", "badge_big", "badge_small";
    missing = not shown) on ``screen`` around ``minimap``. Pure; never raises."""
    prefs = prefs or Prefs()
    scr = as_rect(screen) or (0, 0, 1920, 1080)
    mm = as_rect(minimap)
    side = minimap_side(scr, mm, prefs.flip)
    try:
        zl = list(zones) if zones is not None else game_zones(scr, mm, side, prefs.hud_scale)
        c = _Ctx(scr, mm, side, zl, prefs)
        placed: dict[str, Slot] = {}
        if prefs.radar is not None:
            placed["radar"] = Slot("radar", prefs.radar, prefs.radar, "radar")
        halign_in = "right" if side != "left" else "left"
        # ---- the HUD card (the main channel): its named position first
        spec = specs.get("card")
        if spec is not None and prefs.hud_enabled:
            if prefs.hud_position == "custom" and prefs.hud_xy is not None:
                x, y = clamp_xy(prefs.hud_xy[0], prefs.hud_xy[1], spec.w, spec.h, scr)
                if c.frame is not None and overlap(spec.content(x, y), c.frame):
                    # never over the minimap itself: slide out of it (user positions are kept otherwise)
                    x = int(c.inner_x(spec.w))
                    x, y = clamp_xy(x, y, spec.w, spec.h, scr)
                content = spec.content(x, y)
                placed["card"] = Slot("card", (x, y, spec.w, spec.h), content, "custom", "top", "left",
                                      tuple(_hits(content, zl, {}, scr, 0)))
            else:
                rails = _card_rails(c, spec.w, spec.h, prefs.hud_position)
                halign = halign_in if prefs.hud_position in ("left_of_minimap", "above_minimap") else "left"
                placed["card"] = _place("card", spec, rails, c, placed, halign)
        # ---- toast / banner layer (top centre, under the kill announcer)
        spec = specs.get("toasts")
        if spec is not None and prefs.toasts:
            placed["toasts"] = _place("toasts", spec, _toast_rails(c, spec), c, placed, "center")
        # ---- timers strip (hangs outside the minimap frame, inner side, top)
        spec = specs.get("timers")
        if spec is not None and prefs.timers:
            placed["timers"] = _place("timers", spec, _timers_rails(c, spec, placed.get("card")), c, placed, halign_in)
        # ---- play badges
        if prefs.badges:
            spec = specs.get("badge_big")
            if spec is not None:
                placed["badge_big"] = _place("badge_big", spec, _badge_rails(c, spec, False, placed), c, placed, "center")
            spec = specs.get("badge_small")
            if spec is not None:
                placed["badge_small"] = _place("badge_small", spec, _badge_rails(c, spec, True, placed), c, placed,
                                               "center")
        placed.pop("radar", None)
        return Layout(scr, mm, side, c.U, tuple(zl), placed)
    except Exception:
        log.exception("layout solve failed")
        return Layout(scr, mm, side, ui_unit(scr), tuple(zones or ()), {})


# ======================================================================================
# Sizes of our elements (lazy imports of the renderers) + memoized layouts
# ======================================================================================
def element_specs(screen: Any, minimap: Any = None, detailed: bool = False,
                  parts: Iterable[str] = ELEMENTS) -> dict[str, ElementSpec]:
    """Window sizes + content boxes of our elements at this screen size (largest content of the
    mode: compact or detailed card, 3 or 5 timer rows). Never raises."""
    out: dict[str, ElementSpec] = {}
    scr = as_rect(screen) or (0, 0, 1920, 1080)
    want = set(parts)
    try:
        from treeaicoach import overlay as ov
        from treeaicoach import overlay_render as orr

        if "card" in want:
            w = ov.hud_width(scr)
            h = orr.card_max_height(w, detailed)
            out["card"] = ElementSpec(w, h, orr.card_envelope(w, h, detailed))
        if "timers" in want:
            tw, th = orr.timers_strip_size(scr, detailed)
            out["timers"] = ElementSpec(tw, th)
    except Exception:
        log.debug("card / timers specs failed", exc_info=True)
    try:
        if "toasts" in want:
            from treeaicoach import toasts as tst

            s = tst.scale_for_screen(scr)
            lw, lh = tst.layer_size(s)
            out["toasts"] = ElementSpec(lw, lh, tst.layer_envelope(s))
    except Exception:
        log.debug("toast spec failed", exc_info=True)
    try:
        from treeaicoach import fx_render as fx

        k = fx.scale_for_screen(scr)
        for size in ("big", "small"):
            if f"badge_{size}" in want:
                w, h = fx.layer_size(size, k)
                out[f"badge_{size}"] = ElementSpec(w, h, fx.layer_envelope(size, k))
    except Exception:
        log.debug("badge specs failed", exc_info=True)
    return out


class LayoutCache:
    """Memoized :func:`solve` (a layout only changes with its inputs). Thread-safe."""

    def __init__(self, maxsize: int = 16) -> None:
        self._lock = threading.Lock()
        self._d: OrderedDict[tuple, Layout] = OrderedDict()
        self._max = maxsize
        self.solves = 0

    def get(self, screen: Any, minimap: Any, prefs: Prefs) -> Layout:
        scr, mm = as_rect(screen) or (0, 0, 1920, 1080), as_rect(minimap)
        key = (scr, mm, prefs)
        with self._lock:
            hit = self._d.get(key)
            if hit is not None:
                self._d.move_to_end(key)
                return hit
        lay = solve(scr, mm, element_specs(scr, mm, prefs.detailed), prefs)
        lay = Layout(lay.screen, lay.minimap, lay.side, lay.unit, lay.zones, lay.slots, key)
        with self._lock:
            self.solves += 1
            self._d[key] = lay
            while len(self._d) > self._max:
                self._d.popitem(last=False)
        return lay


_cache = LayoutCache()
_published: Layout | None = None
_pub_lock = threading.Lock()


def layout_for(screen: Any, minimap: Any, cfg: Any = None, detailed: bool | None = None,
               radar: Any = None, custom_card: Any = None, flip: Any = None, hud_scale: Any = None) -> Layout:
    """The (memoized) layout of every element for ``cfg`` on this screen. Never raises."""
    try:
        prefs = Prefs.from_cfg(cfg, detailed, radar, custom_card, flip, hud_scale)
        return _cache.get(screen, minimap, prefs)
    except Exception:
        log.exception("layout_for failed")
        scr = as_rect(screen) or (0, 0, 1920, 1080)
        return Layout(scr, as_rect(minimap), "right", ui_unit(scr))


def publish(lay: Layout | None) -> None:
    """The overlay thread's current layout (read by the play badges' thread)."""
    global _published
    with _pub_lock:
        _published = lay


def published(screen: Any = None, minimap: Any = None) -> Layout | None:
    """The layout the overlay is using, if it was solved for this screen / minimap (or any when
    both are None)."""
    with _pub_lock:
        lay = _published
    if lay is None:
        return None
    if screen is None and minimap is None:
        return lay
    if as_rect(screen) == lay.screen and as_rect(minimap) == lay.minimap:
        return lay
    return None


__all__ = ["Zone", "Slot", "Layout", "ElementSpec", "Prefs", "LayoutCache", "game_zones", "minimap_frame",
           "minimap_side", "ui_unit", "zone_rects", "solve", "element_specs", "layout_for", "publish", "published",
           "overlap", "overlap_area", "as_rect", "ZONE_LABELS", "ELEMENTS", "CARD_POSITIONS", "HUD_KEYS"]
