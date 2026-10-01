"""Replay of a recorded game, minute by minute (Analyses page of the UI).

Pure module (numpy + PIL, no Tk): :class:`ReplayModel` reads a game record written by
``recorder.py`` (my positions, enemy sightings, visible allies, enemy-jungler fog circle,
alerts, Live Client events) and answers "what did the minimap know at game time ``t``":
last known positions (with their age), the jungler's fog circle, recent alerts. The UI
scrubs it with a slider and shows :func:`render_frame` / :func:`render_timeline` images.

Only what was seen on screen is shown: last known positions fade out after
``ENEMY_MEMORY_S`` seconds (no prediction). Colours follow docs/DESIGN.md.
Every public function is defensive: a malformed record gives an empty but valid replay.
"""

from __future__ import annotations

import bisect
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

# docs/DESIGN.md tokens (RGB)
BG = (12, 14, 13)
SURFACE = (18, 21, 19)
LINE = (34, 39, 37)
TEXT = (228, 232, 229)
MUTED = (139, 148, 143)
DIM = (89, 97, 92)
ACCENT = (155, 216, 74)
DANGER = (229, 72, 77)
WARNING = (232, 162, 58)
ALLY = (74, 144, 217)

ENEMY_MEMORY_S = 45.0       # an enemy's last known position is drawn for this long, then dropped
ALLY_MEMORY_S = 20.0
ME_MEMORY_S = 8.0
FOG_MAX_AGE_S = 3.0         # a fog sample is "current" if recorded at most this long ago
RECENT_ALERT_S = 6.0
SPEEDS: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0)   # game seconds per real second x 10 (UI)

IconLoader = Callable[[str], "np.ndarray | None"]


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _series(raw: Any) -> list[tuple[float, float, float]]:
    out: list[tuple[float, float, float]] = []
    for p in raw if isinstance(raw, list) else []:
        if isinstance(p, (list, tuple)) and len(p) >= 3:
            t, u, v = _f(p[0]), _f(p[1]), _f(p[2])
            if t is not None and u is not None and v is not None:
                out.append((t, min(max(u, 0.0), 1.0), min(max(v, 0.0), 1.0)))
    out.sort(key=lambda r: r[0])
    return out


def fmt_clock(t: Any) -> str:
    v = _f(t)
    if v is None:
        return "--:--"
    v = max(0, int(v))
    return f"{v // 60}:{v % 60:02d}"


@dataclass
class Marker:
    """A moment on the timeline."""

    t: float
    kind: str          # "death" | "gank" | "alert" | "kill" | "objective" | "play"
    label: str
    cls: str = ""      # rated play class (plays.CLASSES) when kind == "play"


@dataclass
class Dot:
    alias: str
    uv: tuple[float, float]
    age: float                  # seconds since this position was seen
    relation: str               # "self" | "ally" | "enemy"
    jungler: bool = False


@dataclass
class Frame:
    t: float
    me: Dot | None
    allies: list[Dot] = field(default_factory=list)
    enemies: list[Dot] = field(default_factory=list)
    fog: tuple[float, float, float] | None = None       # (u, v, radius) of the enemy jungler
    alerts: list[tuple[float, int, str]] = field(default_factory=list)   # (t, level, text), recent first
    dead: bool = False


class _Track:
    def __init__(self, pts: list[tuple[float, float, float]]) -> None:
        self.pts = pts
        self.times = [p[0] for p in pts]

    def last_before(self, t: float) -> tuple[float, float, float] | None:
        i = bisect.bisect_right(self.times, t) - 1
        return self.pts[i] if i >= 0 else None


class ReplayModel:
    """What the coach knew at each moment of a recorded game."""

    def __init__(self, record: Any) -> None:
        rec = record if isinstance(record, dict) else {}
        self.record = rec
        meta = rec.get("meta") if isinstance(rec.get("meta"), dict) else {}
        summary = rec.get("summary") if isinstance(rec.get("summary"), dict) else {}
        self.champion = str(meta.get("champion") or summary.get("champion") or "")
        self.my_team = str(meta.get("team") or summary.get("team") or "") or None
        roster = [p for p in rec.get("roster") or [] if isinstance(p, dict)]
        self.names = {str(p.get("alias")): str(p.get("name") or p.get("alias")) for p in roster if p.get("alias")}
        self.jungler: str | None = None
        for p in roster:
            if p.get("team") and p.get("team") != self.my_team and (
                    p.get("has_smite") or str(p.get("position") or "").upper() == "JUNGLE"):
                self.jungler = str(p.get("alias") or "") or None
                break
        self.me = _Track(_series(rec.get("my_positions")))
        sightings = rec.get("sightings") if isinstance(rec.get("sightings"), dict) else {}
        self.enemies = {str(k): _Track(_series(v)) for k, v in sightings.items() if _series(v)}
        allies = rec.get("allies") if isinstance(rec.get("allies"), dict) else {}
        self.allies = {str(k): _Track(_series(v)) for k, v in allies.items() if _series(v)}
        fog = []
        for row in rec.get("fog") or []:
            if isinstance(row, (list, tuple)) and len(row) >= 5:
                t, u, v, r = _f(row[0]), _f(row[2]), _f(row[3]), _f(row[4])
                if None not in (t, u, v, r):
                    fog.append((t, u, v, r))
        fog.sort()
        self.fog = fog
        self._fog_t = [f[0] for f in fog]
        alerts = []
        for a in rec.get("alerts") or []:
            if isinstance(a, (list, tuple)) and len(a) >= 4:
                t = _f(a[0])
                if t is not None:
                    try:
                        lvl = int(a[2])
                    except (TypeError, ValueError):
                        lvl = 0
                    alerts.append((t, str(a[1]), lvl, str(a[3])))
        alerts.sort(key=lambda x: x[0])
        self.alerts = alerts
        self.markers, self.deaths = self._build_markers(rec)
        times = [p[0] for p in self.me.pts] + [a[0] for a in alerts] + [f[0] for f in fog]
        for tr in self.enemies.values():
            times.append(tr.times[-1])
        dur = _f(rec.get("duration")) or _f(summary.get("duration")) or 0.0
        self.start = max(0.0, min(times)) if times else 0.0
        self.start = min(self.start, _f(meta.get("start_game_time")) or self.start)
        self.end = max([dur] + times) if (times or dur) else 0.0
        if self.end <= self.start:
            self.end = self.start + 1.0

    @property
    def duration(self) -> float:
        return self.end

    @property
    def empty(self) -> bool:
        return not (self.me.pts or self.enemies or self.alerts)

    # ------------------------------------------------------------------ markers
    def _build_markers(self, rec: dict) -> tuple[list[Marker], list[float]]:
        markers: list[Marker] = []
        deaths: list[float] = []
        try:
            from treeaicoach import analysis  # noqa: PLC0415

            a = analysis.analyze_game(rec)
            for d in a.get("deaths") or []:
                t = _f(d.get("game_time"))
                if t is None:
                    continue
                deaths.append(t)
                who = ", ".join(d.get("involved_names") or []) or "?"
                markers.append(Marker(t, "death", f"Mort ({who})"))
            for g in a.get("ganks") or []:
                t = _f(g.get("game_time"))
                if t is None:
                    continue
                out = str(g.get("outcome_label") or "")
                names = ", ".join(g.get("names") or [])
                markers.append(Marker(t, "gank", f"Gank {names}".strip() + (f" : {out}" if out else "")))
        except Exception:
            log.debug("replay: analysis unavailable", exc_info=True)
        if not markers:
            for t, _k, lvl, text in self.alerts:
                if lvl >= 2:
                    markers.append(Marker(t, "gank", text))
        by_player: dict[str, str] = {}
        for p in rec.get("roster") or []:
            if isinstance(p, dict) and p.get("alias"):
                for k in ("summoner_name", "riot_id"):
                    if p.get(k):
                        by_player[str(p[k]).lower()] = str(p.get("name") or p["alias"])
                        by_player[str(p[k]).split("#")[0].lower()] = str(p.get("name") or p["alias"])
        names = {self.champion.lower()}
        meta = rec.get("meta") if isinstance(rec.get("meta"), dict) else {}
        for k in ("summoner_name", "riot_id"):
            if meta.get(k):
                names.add(str(meta[k]).lower())
                names.add(str(meta[k]).split("#")[0].lower())
        obj = {"DragonKill": "Dragon", "BaronKill": "Baron", "HeraldKill": "Héraut", "HordeKill": "Larves",
               "AtakhanKill": "Atakhan"}
        for ev in rec.get("events") or []:
            if not isinstance(ev, dict):
                continue
            t = _f(ev.get("EventTime"))
            if t is None:
                continue
            name = ev.get("EventName")
            if name == "ChampionKill" and str(ev.get("KillerName") or "").lower() in names:
                victim = str(ev.get("VictimName") or "?")
                markers.append(Marker(t, "kill", f"Kill sur {by_player.get(victim.lower(), victim)}"))
            elif name in obj:
                markers.append(Marker(t, "objective", obj[name]))
        try:
            from treeaicoach import plays as _plays  # noqa: PLC0415

            summ = _plays.summary_from_record(rec)
            for d in (summ or {}).get("plays") or []:
                t = _f(d.get("gt"))
                cls = str(d.get("cls") or "")
                if t is None or cls not in _plays.CLASSES:
                    continue
                title = str(d.get("title") or _plays.TITLE_FR.get(cls, cls)).capitalize()
                reason = str(d.get("reason") or "")
                markers.append(Marker(t, "play", f"{title} : {reason}" if reason else title, cls))
        except Exception:
            log.debug("replay: no rated plays", exc_info=True)
        markers.sort(key=lambda m: m.t)
        return markers, sorted(deaths)

    # ------------------------------------------------------------------ state
    def frame(self, t: float) -> Frame:
        t = float(t)
        me = None
        p = self.me.last_before(t)
        if p is not None and t - p[0] <= ME_MEMORY_S:
            me = Dot(self.champion or "me", (p[1], p[2]), t - p[0], "self")
        allies = []
        for alias, tr in self.allies.items():
            q = tr.last_before(t)
            if q is not None and t - q[0] <= ALLY_MEMORY_S:
                allies.append(Dot(alias, (q[1], q[2]), t - q[0], "ally"))
        enemies = []
        for alias, tr in self.enemies.items():
            q = tr.last_before(t)
            if q is not None and t - q[0] <= ENEMY_MEMORY_S:
                enemies.append(Dot(alias, (q[1], q[2]), t - q[0], "enemy", alias == self.jungler))
        fog = None
        i = bisect.bisect_right(self._fog_t, t) - 1
        if i >= 0 and t - self.fog[i][0] <= FOG_MAX_AGE_S:
            fog = self.fog[i][1:]
        recent = [(a[0], a[2], a[3]) for a in self.alerts if t - RECENT_ALERT_S <= a[0] <= t]
        recent.reverse()
        dead = any(d <= t <= d + 12.0 for d in self.deaths) and me is None
        return Frame(t, me, allies, enemies, fog, recent, dead)

    def next_marker(self, t: float, kinds: tuple[str, ...] = ("death", "gank")) -> Marker | None:
        for m in self.markers:
            if m.t > t + 0.5 and m.kind in kinds:
                return m
        return None

    def prev_marker(self, t: float, kinds: tuple[str, ...] = ("death", "gank")) -> Marker | None:
        for m in reversed(self.markers):
            if m.t < t - 0.5 and m.kind in kinds:
                return m
        return None


# ======================================================================================
# rendering
# ======================================================================================
_font_cache: dict[tuple[int, bool], Any] = {}


def _font(size: int, bold: bool = False) -> Any:
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
    names = (("segoeuib.ttf", "DejaVuSans-Bold.ttf") if bold else ("segoeui.ttf", "DejaVuSans.ttf"))
    f = None
    for n in names:
        try:
            f = ImageFont.truetype(n, size)
            break
        except OSError:
            continue
    if f is None:
        try:
            f = ImageFont.load_default(size)
        except TypeError:
            f = ImageFont.load_default()
    _font_cache[key] = f
    return f


_tex_cache: dict[int, Image.Image] = {}


def _texture(size: int) -> Image.Image:
    img = _tex_cache.get(size)
    if img is not None:
        return img.copy()
    try:
        from treeaicoach.overlay_render import default_radar_texture  # noqa: PLC0415

        tex = np.asarray(default_radar_texture())[..., ::-1]
        base = Image.fromarray(np.ascontiguousarray(tex), "RGB").resize((size, size), Image.LANCZOS)
        arr = np.asarray(base, np.float32) * 0.55 + np.array(BG, np.float32) * 0.45
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
    except Exception:
        log.debug("replay: no minimap texture", exc_info=True)
        img = Image.new("RGB", (size, size), SURFACE)
    if len(_tex_cache) > 6:
        _tex_cache.clear()
    _tex_cache[size] = img
    return img.copy()


def _portrait(icon: Any, d: int) -> Image.Image | None:
    try:
        a = np.asarray(icon)
        if a.ndim != 3 or a.shape[2] not in (3, 4):
            return None
        im = Image.fromarray(np.ascontiguousarray(a.astype(np.uint8)), "RGBA" if a.shape[2] == 4 else "RGB")
        im = im.convert("RGBA")
        w, h = im.size
        m = min(w, h)
        c = int(m * 0.08)
        im = im.crop(((w - m) // 2 + c, (h - m) // 2 + c, (w + m) // 2 - c, (h + m) // 2 - c))
        return im.resize((d, d), Image.LANCZOS)
    except Exception:
        return None


def render_frame(model: ReplayModel, t: float, size: int = 300, icon_loader: IconLoader | None = None,
                 ss: int = 2) -> Image.Image:
    """Minimap at game time ``t``: last known positions, jungler fog circle, my position (RGB)."""
    fr = model.frame(t)
    S = size * ss
    base = _texture(S).convert("RGBA")
    over = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(over)

    def px(uv: tuple[float, float]) -> tuple[float, float]:
        return uv[0] * S, uv[1] * S

    if fr.fog is not None:
        u, v, r = fr.fog
        cx, cy = px((u, v))
        rr = max(4.0, r * S)
        d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=WARNING + (38,), outline=WARNING + (200,),
                  width=max(1, ss))
    r_icon = S * 0.042
    dots = [(e, DANGER) for e in fr.enemies] + [(a, ALLY) for a in fr.allies]
    if fr.me is not None:
        dots.append((fr.me, ACCENT))
    for dot, col in dots:
        cx, cy = px(dot.uv)
        stale = dot.age > 2.5
        rad = r_icon * (1.25 if dot.relation == "self" else 1.0)
        alpha = 255 if not stale else int(max(70, 220 - dot.age * 3.5))
        icon = None
        if icon_loader is not None and dot.relation != "self":
            try:
                icon = icon_loader(dot.alias)
            except Exception:
                icon = None
        elif icon_loader is not None:
            try:
                icon = icon_loader(model.champion)
            except Exception:
                icon = None
        por = _portrait(icon, int(rad * 2)) if icon is not None else None
        if por is not None:
            if stale:
                g = por.convert("LA").convert("RGBA")
                g.putalpha(Image.eval(por.getchannel("A"), lambda x: int(x * 0.6)))
                por = g
            mask = Image.new("L", por.size, 0)
            ImageDraw.Draw(mask).ellipse((0, 0, por.size[0] - 1, por.size[1] - 1), fill=255)
            a = Image.composite(por.getchannel("A"), Image.new("L", por.size, 0), mask)
            por.putalpha(a)
            over.alpha_composite(por, (int(cx - por.size[0] / 2), int(cy - por.size[1] / 2)))
        else:
            d.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), fill=SURFACE + (alpha,))
        d.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), outline=(DIM if stale else col) + (alpha,),
                  width=max(2, int(rad * 0.18)))
        if dot.relation == "enemy" and dot.jungler and not stale:
            d.rectangle((cx + rad * 0.45, cy - rad * 1.15, cx + rad * 1.05, cy - rad * 0.55), fill=WARNING + (255,))
        if stale and dot.relation != "self":
            txt = f"{int(dot.age)} s"
            f = _font(int(10 * ss))
            tw = d.textlength(txt, font=f)
            d.rectangle((cx - tw / 2 - 2 * ss, cy + rad + ss, cx + tw / 2 + 2 * ss, cy + rad + 13 * ss),
                        fill=BG + (210,))
            d.text((cx - tw / 2, cy + rad + ss), txt, font=f, fill=MUTED + (255,))
    base.alpha_composite(over)
    out = base.convert("RGB")
    dd = ImageDraw.Draw(out)
    if fr.dead:
        f = _font(int(13 * ss), True)
        dd.rectangle((0, S - 26 * ss, S, S), fill=(58, 22, 24))
        dd.text((10 * ss, S - 22 * ss), "MORT", font=f, fill=DANGER)
    dd.rectangle((0, 0, S - 1, S - 1), outline=LINE, width=max(1, ss))
    return out.resize((size, size), Image.LANCZOS)


MARKER_COLORS = {"death": DANGER, "gank": WARNING, "alert": WARNING, "kill": ACCENT, "objective": MUTED}


def render_timeline(model: ReplayModel, width: int, height: int = 34, t: float | None = None,
                    ss: int = 2) -> Image.Image:
    """Game timeline: minute ticks, markers (deaths red crosses, ganks amber, kills green) and cursor."""
    W, H = max(40, width) * ss, max(20, height) * ss
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    pad = 6 * ss
    y = H * 0.55
    t0, t1 = model.start, model.end
    span = max(1.0, t1 - t0)

    def x_of(tt: float) -> float:
        return pad + (W - 2 * pad) * min(1.0, max(0.0, (tt - t0) / span))

    d.line((pad, y, W - pad, y), fill=LINE, width=max(1, ss))
    f = _font(int(9 * ss))
    step = 300 if span > 1500 else 120 if span > 600 else 60
    m = math.ceil(t0 / step) * step
    while m <= t1:
        x = x_of(m)
        d.line((x, y - 3 * ss, x, y + 3 * ss), fill=DIM, width=max(1, ss))
        d.text((x + 2 * ss, H - 11 * ss), f"{int(m // 60)}", font=f, fill=DIM)
        m += step
    if t is not None:
        x = x_of(t)
        d.rectangle((pad, y - ss, x, y + ss), fill=MUTED)
    for mk in model.markers:
        x = x_of(mk.t)
        col = MARKER_COLORS.get(mk.kind, MUTED)
        r = 4 * ss
        if mk.kind == "death":
            d.line((x - r, y - r, x + r, y + r), fill=col, width=2 * ss)
            d.line((x - r, y + r, x + r, y - r), fill=col, width=2 * ss)
        elif mk.kind == "gank":
            d.polygon([(x, y - r - 2 * ss), (x + r, y + r - 2 * ss), (x - r, y + r - 2 * ss)], fill=col)
        elif mk.kind == "kill":
            d.rectangle((x - r * 0.6, y - r * 0.6, x + r * 0.6, y + r * 0.6), fill=col)
        elif mk.kind == "play":
            pc = play_rgb(mk.cls)
            yy = y + 7 * ss
            d.polygon([(x, yy - r * 0.8), (x + r * 0.8, yy), (x, yy + r * 0.8), (x - r * 0.8, yy)], fill=pc)
        else:
            d.line((x, y - r, x, y + r), fill=col, width=ss)
    if t is not None:
        x = x_of(t)
        d.line((x, 2 * ss, x, H - 13 * ss), fill=TEXT, width=max(1, ss))
    return im.resize((W // ss, H // ss), Image.LANCZOS)


def play_rgb(cls: str) -> tuple[int, int, int]:
    """Colour of a rated-play class (fx_render.CLASS_RGB), grey if unknown."""
    try:
        from treeaicoach.fx_render import CLASS_RGB  # noqa: PLC0415

        return tuple(CLASS_RGB.get(cls, MUTED))  # type: ignore[return-value]
    except Exception:
        return MUTED


def time_at_x(model: ReplayModel, x: float, width: int, ss_pad: float = 6.0) -> float:
    """Inverse of the timeline mapping (click / drag on the timeline image)."""
    span = max(1.0, model.end - model.start)
    frac = (float(x) - ss_pad) / max(1.0, width - 2 * ss_pad)
    return model.start + span * min(1.0, max(0.0, frac))


def frame_caption(model: ReplayModel, t: float) -> str:
    """One line under the map: visible / hidden enemies and the latest alert (plain French)."""
    fr = model.frame(t)
    seen = [model.names.get(e.alias, e.alias) for e in fr.enemies if e.age <= 2.5]
    parts = [f"{len(seen)} ennemi{'s' if len(seen) > 1 else ''} visible{'s' if len(seen) > 1 else ''}"]
    if model.jungler:
        jn = model.names.get(model.jungler, model.jungler)
        j = next((e for e in fr.enemies if e.alias == model.jungler), None)
        if j is not None and j.age <= 2.5:
            parts.append(f"jungler {jn} visible")
        elif j is not None:
            parts.append(f"jungler {jn} vu il y a {int(j.age)} s")
        elif fr.fog is not None:
            parts.append(f"jungler {jn} dans le brouillard")
    if fr.alerts:
        parts.append(f"alerte : {fr.alerts[0][2]}")
    if fr.dead:
        parts.append("tu es mort")
    return " · ".join(parts)


__all__ = ["ReplayModel", "Frame", "Dot", "Marker", "render_frame", "render_timeline", "time_at_x", "play_rgb",
           "frame_caption", "fmt_clock", "SPEEDS"]
