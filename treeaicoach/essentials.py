"""In-game "essentials strip": the few numbers worth a glance, in one small pill at the top centre.

Two short lines, tabular figures, nothing else::

    VICTOIRE 62 % ▲ Baron    OR +850    CS 6,8/7
          DRAGON 1:12    JGL haut 23 s

* line 1 (live_edge.EdgeStats, Live Client data): win probability (+ an arrow and the term that
  moved it by >= 5 points over ~90 s), lane gold difference vs my opponent (Data Dragon value of
  both inventories), CS / min vs the target of my rank (gold / min for the support);
* line 2 (map): the next epic objective (countdown, or "dispo"), the enemy jungler's last seen
  side and how long ago (tracker; "mort" while dead, "?" when unknown / too old).

Placement (layout.py element "essentials", rail "top_center"): the band between the top edge of
the screen and League's kill announcer, horizontally centred. Why there, from League's HUD layout
(zones measured on real captures, layout.game_zones): the scoreboard / KDA / clock is anchored top
RIGHT, the ally portraits / votes hang on the minimap frame, the chat and kill feed sit on the
sides, the announcer and our toasts start ~7 % of the UI height down. The top-centre band is the
only always-empty strip of the HUD at every resolution and aspect ratio (it is centred on the
screen, League's top elements are anchored to its corners / the announcer below), it does not
depend on the minimap side (flip), never covers the minimap (so the detector's crop never sees
it, even if capture exclusion were refused), and it is where the eye already goes for kill /
objective announcements. "Just above the minimap" looks free on paper but holds the ally
portraits row and the vote panels in the current HUD. When that band is taken (tiny / odd
screens) the solver falls back to the minimap column on the user's HUD side.

Rendering (:func:`render`): sapphire glass pill (docs/DESIGN.md « arbre de vie, saphir »), small
muted labels, white values with tabular (monospace) digits so numbers never wobble, ONE accent
(the swing arrow / an objective up). The image is cached by its text: identical lines are never
re-rendered (and :class:`treeaicoach.overlay.OverlayManager` re-checks the text at most once per
second and uploads only when it changed). Pure, never raises.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

log = logging.getLogger(__name__)

#: Value font (px at 1080p; layout.SIZE / BASE_FONT_PX "essentials" scale it, >= 12 px).
FONT_PX = 13.0
LABEL_PX = 10.5
#: Enemy jungler last seen older than this (s) is not information any more ("JGL ?").
JUNGLER_MAX_S = 150.0
#: An objective spawning within this many seconds beats one that is already up.
OBJ_SOON_S = 90.0
#: Objective keys / names -> label, in the priority order of the "up" objectives.
OBJ_LABELS = (("baron", "BARON"), ("elder", "ANCESTRAL"), ("dragon", "DRAGON"), ("herald", "HÉRAUT"),
              ("grubs", "LARVES"))
_OBJ_NAME_KEYS = {"dragon ancestral": "elder", "ancestral": "elder", "héraut": "herald", "larves": "grubs",
                  "dragon": "dragon", "baron": "baron"}
#: Short reasons (live_edge.REASON_LABEL values) for the strip.
_REASON_SHORT = {"inhibiteurs": "inhibs", "ancestral": "ancestral"}

# sapphire tokens (ui_widgets.SAPPHIRE): plate, hairline, text, secondary, accent, danger, warning
PLATE = (7, 20, 48)            # #071430
EDGE = (43, 75, 128)           # #2B4B80
TEXT = (245, 248, 255)         # #F5F8FF
LABEL = (157, 175, 208)        # #9DAFD0
ACCENT = (116, 168, 250)       # #74A8FA sapphire
DOWN = (255, 125, 117)         # #FF7D75
WARN = (243, 208, 101)         # #F3D065
PLATE_ALPHA = 0.78


@dataclass(frozen=True)
class Cell:
    """One ``LABEL value`` pair; ``arrow`` +1 / -1 draws a small up / down triangle before
    ``extra`` (the swing reason); ``tone`` "" | "accent" | "down" | "warn" colours the value."""

    label: str
    value: str
    tone: str = ""
    arrow: int = 0
    extra: str = ""

    def text(self) -> str:
        arrow = " ↑" if self.arrow > 0 else " ↓" if self.arrow < 0 else ""
        tail = f"{arrow} {self.extra}" if (arrow and self.extra) else arrow
        return f"{self.label} {self.value}{tail}".strip()


Lines = tuple[tuple[Cell, ...], ...]


# ----------------------------------------------------------------------------- formatting
def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def fmt_dec(x: float) -> str:
    """6.8 -> "6,8", 7.0 -> "7"."""
    return f"{x:.1f}".rstrip("0").rstrip(".").replace(".", ",")


def fmt_gold(n: float) -> str:
    """Signed gold rounded to 10, French thin grouping: "+850", "-1 250", "±0"."""
    v = int(round(n / 10.0) * 10)
    s = f"{abs(v):,}".replace(",", " ")
    return f"{'+' if v > 0 else '-' if v < 0 else '±'}{s}"


def fmt_clock(s: float) -> str:
    s = max(0, int(math.ceil(s)))
    return f"{s // 60}:{s % 60:02d}"


def fmt_ago(s: float) -> str:
    """"23 s" below a minute, then "1:25" in 5 s steps (fewer redraws, same information)."""
    s = max(0, int(s))
    if s < 60:
        return f"{s} s"
    s -= s % 5
    return f"{s // 60}:{s % 60:02d}"


# ----------------------------------------------------------------------------- the cells
def win_cell(stats: Any) -> Cell | None:
    p = _f(getattr(stats, "win", None))
    if p is None:
        return None
    d = _f(getattr(stats, "win_delta", None))
    reason = getattr(stats, "win_reason", None)
    arrow = 0
    if d is not None and abs(d) >= 5.0:
        arrow = 1 if d > 0 else -1
    extra = _REASON_SHORT.get(str(reason), str(reason)) if (arrow and reason) else ""
    return Cell("VICTOIRE", f"{int(round(100 * min(1.0, max(0.0, p))))} %", "", arrow, extra)


def gold_cell(stats: Any) -> Cell | None:
    gd = _f(getattr(stats, "gold_diff", None))
    if gd is None:
        return None
    return Cell("OR", fmt_gold(gd), "down" if gd <= -500 else "")


def farm_cell(stats: Any) -> Cell | None:
    if bool(getattr(stats, "support", False)):
        g = _f(getattr(stats, "gold_min", None))
        return Cell("OR/MIN", str(int(g))) if g is not None and g > 0 else None
    cs = _f(getattr(stats, "cs_min", None))
    if cs is None or cs <= 0:
        return None
    tgt = _f(getattr(stats, "cs_target", None))
    if tgt is None or tgt <= 0:
        return Cell("CS", fmt_dec(cs))
    return Cell("CS", f"{fmt_dec(cs)}/{fmt_dec(tgt)}", "warn" if cs < 0.8 * tgt else "")


def _obj_key(ob: Any) -> str:
    key = str(getattr(ob, "key", "") or "").lower()
    if key:
        return key
    return _OBJ_NAME_KEYS.get(str(getattr(ob, "name", "") or "").lower(), "")


def objective_cell(objectives: Sequence[Any], game_time: Any) -> Cell | None:
    """The next epic objective: one spawning within :data:`OBJ_SOON_S`, else the most important
    one up ("dispo"), else the next spawn. None when nothing is known."""
    gt = _f(game_time)
    if gt is None:
        return None
    labels = dict(OBJ_LABELS)
    order = [k for k, _l in OBJ_LABELS]
    up: list[str] = []
    soon: list[tuple[float, str]] = []
    for ob in objectives or ():
        key = _obj_key(ob)
        if key not in labels:
            continue
        if bool(getattr(ob, "alive", False)):
            up.append(key)
            continue
        nxt = _f(getattr(ob, "next_spawn", None))
        if nxt is not None and nxt - gt > 0:
            soon.append((nxt - gt, key))
    soon.sort()
    if soon and soon[0][0] <= OBJ_SOON_S:
        rem, key = soon[0]
        return Cell(labels[key], fmt_clock(rem), "accent" if rem <= 30 else "")
    if up:
        key = min(up, key=order.index)
        return Cell(labels[key], "dispo", "accent")
    if soon:
        rem, key = soon[0]
        return Cell(labels[key], fmt_clock(rem))
    return None


def _side(uv: Any) -> str:
    try:
        from treeaicoach import geometry

        u, v = float(uv[0]), float(uv[1])
        if geometry.classify_zone(u, v) == geometry.Zone.MID_LANE:
            return "mid"
        return "haut" if geometry.side_of(u, v) == "top" else "bas"
    except Exception:
        return ""


def jungler_cell(enemies: Sequence[Any]) -> Cell | None:
    """Enemy jungler: "haut 23 s" (last seen side + how long ago), "haut · visible", "mort", "?"."""
    jg = next((e for e in enemies or () if bool(getattr(e, "is_jungler", False))), None)
    if jg is None:
        return None
    if bool(getattr(jg, "dead", False)):
        return Cell("JGL", "mort")
    uv = getattr(jg, "uv", None)
    side = _side(uv) if uv is not None else ""
    if not side:
        return Cell("JGL", "?")
    if bool(getattr(jg, "visible", False)):
        return Cell("JGL", f"{side} · visible", "warn")
    ago = _f(getattr(jg, "last_seen_ago", None))
    if ago is None or ago > JUNGLER_MAX_S:
        return Cell("JGL", "?")
    return Cell("JGL", f"{side} {fmt_ago(ago)}", "warn" if ago < 15 else "")


def build_lines(state: Any) -> Lines:
    """The strip's content for an ``OverlayState``-like object: at most two lines of cells (empty
    lines dropped; ``()`` = nothing to show). Pure, never raises."""
    try:
        stats = getattr(state, "edge_stats", None)
        l1 = tuple(c for c in (win_cell(stats), gold_cell(stats), farm_cell(stats)) if c is not None) \
            if stats is not None else ()
        l2 = tuple(c for c in (objective_cell(getattr(state, "objectives", None) or (), getattr(state, "game_time", None)),
                               jungler_cell(getattr(state, "enemies", None) or ())) if c is not None)
        return tuple(line for line in (l1, l2) if line)
    except Exception:
        log.debug("essentials lines failed", exc_info=True)
        return ()


def lines_text(lines: Lines) -> str:
    """Plain text of the strip (tests, logs): cells joined by "  ·  ", lines by a newline."""
    return "\n".join("  ·  ".join(c.text() for c in line) for line in lines)


# ----------------------------------------------------------------------------- rendering
#: Widest content per cell kind (the strip is sized for it: a changing number never moves anything).
_TEMPLATES: dict[str, str] = {"VICTOIRE": "100 %", "OR": "-8 888", "CS": "88,8/8,8", "OR/MIN": "888",
                              "JGL": "haut 88 s", "BARON": "8:88", "ANCESTRAL": "8:88", "DRAGON": "8:88",
                              "HÉRAUT": "8:88", "LARVES": "8:88"}
_SAMPLE_LINES: Lines = ((Cell("VICTOIRE", "100 %", "", 1, "ancestral"), Cell("OR", "-8 888"), Cell("CS", "88,8/8,8")),
                        (Cell("ANCESTRAL", "8:88"), Cell("JGL", "haut · visible")))


def _metrics(screen: Any) -> dict[str, Any]:
    from treeaicoach import overlay_render as orr

    try:
        from treeaicoach import layout as _lay

        k = _lay.overlay_scale(orr._rect_tuple(screen) or (0, 0, 1920, 1080), "essentials")
    except Exception:
        k = 1.0
    fv = orr.get_font(max(12, int(round(FONT_PX * k))), "semibold")
    fl = orr.get_font(max(10, int(round(LABEL_PX * k))), "semibold")
    digit = max(orr.text_width(d, fv) for d in "0123456789")
    cap = orr._cap_height(fv)
    return {"k": k, "fv": fv, "fl": fl, "digit": digit, "cap": cap,
            "padx": max(8.0, 11 * k), "pady": max(4.0, 5 * k), "row": cap + max(7.0, 8 * k),
            "gap_lv": max(3.0, 4 * k), "gap_cell": max(10.0, 14 * k), "arrow": max(4.0, 4.5 * k)}


def _mono_width(text: str, m: dict[str, Any]) -> float:
    from treeaicoach import overlay_render as orr

    return sum(m["digit"] if ch.isdigit() else orr.text_width(ch, m["fv"]) for ch in text)


def _cell_width(c: Cell, m: dict[str, Any], fixed: bool = True) -> float:
    from treeaicoach import overlay_render as orr

    w_val = _mono_width(c.value, m)
    if fixed and c.label in _TEMPLATES:
        w_val = max(w_val, _mono_width(_TEMPLATES[c.label], m))
    w = orr.text_width(c.label, m["fl"]) + m["gap_lv"] + w_val
    if c.arrow:
        w += m["gap_lv"] + 2 * m["arrow"]
        if c.extra:
            w += m["gap_lv"] + orr.text_width(c.extra, m["fl"])
    return w


def _line_width(line: Sequence[Cell], m: dict[str, Any]) -> float:
    return sum(_cell_width(c, m) for c in line) + m["gap_cell"] * max(0, len(line) - 1)


def strip_size(screen: Any = None) -> tuple[int, int]:
    """Window size of the strip (layout slot): the widest sample content, two lines."""
    try:
        m = _metrics(screen)
        w = max(_line_width(line, m) for line in _SAMPLE_LINES) + 2 * m["padx"] + 2
        h = 2 * m["row"] + 2 * m["pady"] + 2
        return int(math.ceil(w)), int(math.ceil(h))
    except Exception:
        return 340, 48


def _draw_mono(cv_: Any, x: float, y: float, text: str, m: dict[str, Any], rgb: Any) -> float:
    """Tabular figures: each digit centred in a fixed cell (no font feature needed)."""
    from treeaicoach import overlay_render as orr

    fv = m["fv"]
    for ch in text:
        if ch.isdigit():
            cw = orr.text_width(ch, fv)
            cv_.text(x + (m["digit"] - cw) / 2, y, ch, fv, rgb, 1.0, shadow=0.0)
            x += m["digit"]
        elif ch != " ":
            x += cv_.text(x, y, ch, fv, rgb, 1.0, shadow=0.0)
        else:
            x += orr.text_width(" ", fv)
    return x


_TONES = {"accent": ACCENT, "down": DOWN, "warn": WARN}
_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "img": None}
#: renders done (tests / perf: a redraw happens only when the text changed)
render_count = 0


def render(lines: Lines, screen: Any = None) -> np.ndarray | None:
    """Premultiplied BGRA pill of ``lines`` (None when empty), cached by text + scale: the same
    lines return the SAME array without drawing. Never raises."""
    global render_count
    if not lines:
        return None
    try:
        from treeaicoach import overlay_render as orr

        m = _metrics(screen)
        key = (lines, round(m["k"], 4))
        with _cache_lock:
            if _cache["key"] == key:
                return _cache["img"]
        W, H = strip_size(screen)
        cv_ = orr.Canvas(W, H)
        rad = (H - 2) / 2.0 if len(lines) == 1 else max(6.0, 9 * m["k"])
        hh = int(math.ceil(m["row"] * len(lines) + 2 * m["pady"] + 2))
        y_top = 0
        cv_.rrect(1, y_top + 1, W - 2, hh - 2, min(rad, (hh - 2) / 2.0), PLATE, PLATE_ALPHA, border=EDGE,
                  border_alpha=0.85, border_w=max(1.0, 0.9 * m["k"]))
        for i, line in enumerate(lines):
            cy = y_top + 1 + m["pady"] + m["row"] * (i + 0.5)
            x = (W - _line_width(line, m)) / 2.0
            for c in line:
                x0 = x
                x += cv_.text(x, cy, c.label, m["fl"], LABEL, 1.0, shadow=0.0) + m["gap_lv"]
                xe = _draw_mono(cv_, x, cy, c.value, m, _TONES.get(c.tone, TEXT))
                if c.arrow:
                    a = m["arrow"]
                    ax = xe + m["gap_lv"] + a
                    col = ACCENT if c.arrow > 0 else DOWN
                    pts = [(ax - a, cy + a * 0.6), (ax + a, cy + a * 0.6), (ax, cy - a * 0.9)] if c.arrow > 0 else \
                        [(ax - a, cy - a * 0.6), (ax + a, cy - a * 0.6), (ax, cy + a * 0.9)]
                    cv_.polygon(pts, col, 1.0)
                    if c.extra:
                        cv_.text(ax + a + m["gap_lv"], cy, c.extra, m["fl"], col, 1.0, shadow=0.0)
                x = x0 + _cell_width(c, m) + m["gap_cell"]
        img = cv_.to_bgra()[:hh] if hh < H else cv_.to_bgra()
        img = np.ascontiguousarray(img)
        with _cache_lock:
            _cache["key"], _cache["img"] = key, img
            render_count += 1
        return img
    except Exception:
        log.debug("essentials strip render failed", exc_info=True)
        return None


__all__ = ["Cell", "Lines", "build_lines", "lines_text", "render", "strip_size", "win_cell", "gold_cell",
           "farm_cell", "objective_cell", "jungler_cell", "fmt_gold", "fmt_ago", "fmt_clock", "fmt_dec"]
