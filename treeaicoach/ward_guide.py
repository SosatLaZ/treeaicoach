"""Ward guide: WHERE to put the next ward, shown on the minimap AND in the game view.

:mod:`treeaicoach.wards` decides when wards matter (after leaving base, every ~2.5 min, before
an objective) and which spots are best. This module turns that advice - or a hotkey press
(``cfg.hotkey_ward``, default F7) - into at most :data:`MAX_GUIDES` short-lived guides:

* on the minimap layer: a pulsing ward ring (duck-typed ``tactics.MapGuide`` kind "ward"),
  turned green with a check mark once the ward is placed;
* in the game view (:meth:`WardGuideManager.world_markers`, drawn by
  ``overlay_render.render_world_guides`` in small click-through windows): a ground marker
  (ellipse ring + ward icon + "Ward ici") at the spot projected with
  :mod:`treeaicoach.camera_proj` when it is on screen, else an arrow at the screen edge pointing
  towards it with the walking time; a short check mark once the ward is placed.

Skill level (``cfg.skill_level``, :mod:`treeaicoach.skill`): "expert" only gets guides before an
objective or on the hotkey; "debutant" also gets a short text hint under the marker.

A guide lasts :data:`GUIDE_S` at most, or until an ALLIED ward glyph appears on the minimap near
the spot (:class:`WardPlacedDetector`: small bright-blue blobs - the friendly ward glyphs are
~5 px blue / cyan stars - in a ~30 px patch, not inside a champion icon, NEW compared with the
patch when the guide started and STATIC over :data:`PLACE_HITS` checks, which rejects walking
minions and pings; ~0.05 ms, run at :data:`CHECK_HZ`). Nothing runs while no guide is active (no
camera search, hidden windows).

Visual only: no voice. ``cfg.ward_sound`` (default False) plays one short system sound when a
guide starts (Windows only). Thread-safe, never raises from its public methods.
"""

from __future__ import annotations

import logging
import math
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import cv2
import numpy as np

from treeaicoach import camera_proj as cp

log = logging.getLogger(__name__)

MAX_GUIDES = 2
GUIDE_S = 20.0                 # a guide is shown this long at most
CONFIRM_S = 2.5                # "ward placed" check mark duration
CHECK_HZ = 2.0                 # ward-placed detection rate
PLACE_R = 0.045                # a ward within this map distance of the spot counts as placed
AVOID_R = 0.055                # ... but not inside a champion icon (ring colours look like wards)
BLOB_MAX = 0.016               # ward glyph (~5 px star) bbox <= this fraction of the width + 1 px (minion dots: 0.018-0.026)
BLOB_TOL = 0.008               # a blob "did not move" between two checks (fraction of the width)
BASELINE_CHECKS = 2            # the blobs seen in the first checks are the baseline (not new)
PLACE_HITS = 3                 # consecutive checks with the same new static blob
HOTKEY_COOLDOWN_S = 1.0
LABEL = "Ward ici"
DONE_LABEL = "Balise posée"
#: Short text hint for beginners (cfg.skill_level == "debutant"); 4 = default trinket key.
REASON_HINT = {"objective": "Vision avant l'objectif", "base": "Vision de ta voie",
               "periodic": "Balise rechargée", "hotkey": "Meilleur spot maintenant"}
KEY_HINT = "touche 4"
#: Expert players only get these guides (before an objective / on request).
EXPERT_REASONS = frozenset({"objective", "hotkey"})


# ======================================================================================
# Ward-placed detection
# ======================================================================================
def ward_mask(bgr: np.ndarray) -> np.ndarray:
    """uint8 0/1 mask of bright blue / cyan pixels (friendly ward glyphs, allied minions, rings)."""
    p = bgr[..., :3].astype(np.int16)
    b, g, r = p[..., 0], p[..., 1], p[..., 2]
    return ((b >= 110) & (b - r >= 40) & (g >= 60)).astype(np.uint8)


class WardPlacedDetector:
    """Small friendly-ward-like blobs near a map point (see the module doc). Never raises."""

    def blobs(self, minimap_bgr: Any, uv: Sequence[float], radius: float = PLACE_R,
              avoid: Sequence[Sequence[float]] = ()) -> list[tuple[float, float]] | None:
        """Centres (map u, v) of the small blue blobs within ``radius`` of ``uv``, away from the
        ``avoid`` points (champion icon centres). None when the frame is unusable."""
        try:
            if not isinstance(minimap_bgr, np.ndarray) or minimap_bgr.ndim != 3 or minimap_bgr.shape[2] < 3:
                return None
            H, W = minimap_bgr.shape[:2]
            if min(H, W) < 48:
                return None
            u, v = float(uv[0]), float(uv[1])
            if not (math.isfinite(u) and math.isfinite(v)):
                return None
            r = radius * W + 3
            x0, y0 = max(0, int(u * W - r)), max(0, int(v * H - r))
            x1, y1 = min(W, int(u * W + r) + 1), min(H, int(v * H + r) + 1)
            if x1 - x0 < 4 or y1 - y0 < 4:
                return []
            mask = ward_mask(minimap_bgr[y0:y1, x0:x1])
            if not mask.any():
                return []
            n, _lab, st, cen = cv2.connectedComponentsWithStats(mask, connectivity=8)
            big = max(3.0, BLOB_MAX * W + 1.0)
            av = [(float(a[0]), float(a[1])) for a in (avoid or ()) if a is not None]
            out: list[tuple[float, float]] = []
            for i in range(1, n):
                x, y, w, h, _area = (int(c) for c in st[i])
                if w > big or h > big:
                    continue                      # champion ring, turret, ping...
                if x == 0 or y == 0 or x + w >= x1 - x0 or y + h >= y1 - y0:
                    if w > 2 or h > 2:            # cut by the patch border: part of something bigger
                        continue
                cu, cv_ = (x0 + float(cen[i][0]) + 0.5) / W, (y0 + float(cen[i][1]) + 0.5) / H
                if math.hypot(cu - u, cv_ - v) > radius:
                    continue
                if any(math.hypot(cu - a[0], cv_ - a[1]) < AVOID_R for a in av):
                    continue
                out.append((cu, cv_))
            return out
        except Exception:
            log.debug("ward blobs failed", exc_info=True)
            return None


# ======================================================================================
# Guides
# ======================================================================================
@dataclass
class WardGuide:
    spot_id: str
    uv: tuple[float, float]
    label: str                       # French spot name ("buisson pixel (rivière du haut)")
    reason: str                      # "base" | "periodic" | "objective" | "hotkey"
    since: float
    until: float
    placed_t: float | None = None
    base: list = field(default_factory=list)     # blobs already there when the guide started
    base_n: int = 0                               # checks merged into ``base``
    cand: tuple[float, float] | None = None       # new static blob being confirmed
    hits: int = 0

    @property
    def placed(self) -> bool:
        return self.placed_t is not None

    @property
    def hint(self) -> str:
        why = REASON_HINT.get(self.reason, "")
        return f"{why} · {KEY_HINT}" if why else KEY_HINT


@dataclass(frozen=True)
class WorldMarker:
    """One marker of the game-view layer, absolute screen pixels."""

    kind: str                        # "ground" | "edge" | "done"
    x: float
    y: float
    dx: float = 0.0                  # edge: unit direction towards the spot
    dy: float = 0.0
    label: str = LABEL
    sub: str = ""                    # edge: walking time ("≈ 8 s"); ground: spot name
    age: float = 0.0                 # seconds since the guide started (fade in / confirmation)
    left: float = 99.0               # seconds before it disappears (fade out)
    key: str = ""
    hint: str = ""                   # beginner text hint ("Vision de ta voie · touche 4"), else ""


@dataclass(frozen=True)
class GuideView:
    """Minimap guide (duck-typed like ``tactics.MapGuide``; ``done`` = ward placed)."""

    kind: str
    uv: tuple[float, float]
    label: str = ""
    priority: int = 40
    arrow: bool = False
    color: str = "gold"
    until: float = 0.0
    since: float = 0.0
    done: bool = False


def _beep() -> None:
    if sys.platform != "win32":
        return
    try:
        import winsound

        winsound.MessageBeep(getattr(winsound, "MB_OK", 0))
    except Exception:
        pass


def _skill(cfg: Any) -> str:
    try:
        from treeaicoach.skill import normalize

        return normalize(getattr(cfg, "skill_level", "intermediaire"))
    except Exception:
        return "intermediaire"


class WardGuideManager:
    """Lifecycle of the ward guides + camera tracking while one is active. Thread-safe.

    ``cfg`` fields read (getattr, defaults in brackets): ``ward_guide`` [True] (the whole
    feature), ``ward_world`` [True] (game-view markers), ``ward_sound`` [False],
    ``skill_level`` ["intermediaire"]."""

    def __init__(self, cfg: Any = None, detector: WardPlacedDetector | None = None,
                 sound: Callable[[], None] | None = None) -> None:
        self.cfg = cfg
        self.detector = detector or WardPlacedDetector()
        self.camera = cp.CameraTracker()
        self._sound = sound or _beep
        self._lock = threading.Lock()
        self.reset()

    def apply_config(self, cfg: Any) -> None:
        self.cfg = cfg
        if not self.enabled:
            with self._lock:
                self._guides = []

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.cfg, "ward_guide", True))

    @property
    def world_enabled(self) -> bool:
        return self.enabled and bool(getattr(self.cfg, "ward_world", True))

    def allows(self, reason: str) -> bool:
        """Is a guide for ``reason`` shown at this skill level (expert: objective / hotkey only)?"""
        if not self.enabled:
            return False
        return _skill(self.cfg) != "expert" or str(reason) in EXPERT_REASONS

    def reset(self) -> None:
        with self._lock:
            self._guides: list[WardGuide] = []
            self._seen_advice: set = set()
            self._request_t: float | None = None
            self._last_request = -math.inf
            self._next_check = -math.inf
        self.camera.reset()

    # ------------------------------------------------------------------ inputs
    def request(self, t: float) -> bool:
        """Hotkey: show the best spots now (on the next :meth:`update`). False when debounced / off."""
        if not self.enabled:
            return False
        with self._lock:
            if t - self._last_request < HOTKEY_COOLDOWN_S:
                return False
            self._last_request = t
            self._request_t = t
            return True

    def start(self, t: float, picks: Sequence[Any], reason: str, until: float | None = None) -> int:
        """Start guides for ``picks`` (``wards.WardPick``-like: ``.spot.id``, ``.uv``, ``.label``)."""
        try:
            if not self.allows(reason):
                return 0
            end = t + GUIDE_S if until is None else min(float(until), t + GUIDE_S)
            new: list[WardGuide] = []
            for p in list(picks or [])[:MAX_GUIDES]:
                uv = (float(p.uv[0]), float(p.uv[1]))
                if not (math.isfinite(uv[0]) and math.isfinite(uv[1])):
                    continue
                sid = str(getattr(getattr(p, "spot", None), "id", "") or f"{uv[0]:.3f},{uv[1]:.3f}")
                new.append(WardGuide(sid, uv, str(getattr(p, "label", "") or ""), reason, t, max(t + 1.0, end)))
            if not new:
                return 0
            with self._lock:
                self._guides = new
                self._next_check = -math.inf
            if bool(getattr(self.cfg, "ward_sound", False)):
                try:
                    self._sound()
                except Exception:
                    pass
            return len(new)
        except Exception:
            log.exception("WardGuideManager.start failed")
            return 0

    def update(self, t: float, advice: Any = None, minimap_bgr: Any = None,
               recommend: Callable[[], Sequence[Any]] | None = None,
               avoid: Sequence[Sequence[float]] = ()) -> list[str]:
        """One engine tick: new advice / hotkey -> guides; camera + ward-placed detection while active.
        ``avoid`` = champion icon centres on the minimap (u, v). Returns events ("start", "placed",
        "end"). Never raises."""
        events: list[str] = []
        try:
            if advice is not None and getattr(advice, "picks", None):
                key = (getattr(advice, "key", ""), round(float(getattr(advice, "until", 0.0) or 0.0), 1))
                with self._lock:
                    fresh = key not in self._seen_advice
                    self._seen_advice.add(key)
                    if len(self._seen_advice) > 64:
                        self._seen_advice = {key}
                if fresh and self.start(t, advice.picks, str(getattr(advice, "reason", "") or ""),
                                        getattr(advice, "until", None)):
                    events.append("start")
            with self._lock:
                req, self._request_t = self._request_t, None
            if req is not None and recommend is not None:
                picks = list(recommend() or [])
                if picks and self.start(t, picks, "hotkey"):
                    events.append("start")
            with self._lock:
                had = bool(self._guides)
                self._guides = [g for g in self._guides if t < g.until]
                guides = list(self._guides)
                check = bool(guides) and t >= self._next_check
                if check:
                    self._next_check = t + 1.0 / CHECK_HZ
            if had and not guides:
                events.append("end")
            if not guides:
                return events
            if isinstance(minimap_bgr, np.ndarray):
                if self.world_enabled:
                    self.camera.update(minimap_bgr, t)
                if check:
                    for g in guides:
                        if not g.placed and self._check_placed(g, minimap_bgr, t, avoid):
                            events.append("placed")
            return events
        except Exception:
            log.exception("WardGuideManager.update failed")
            return events

    def _check_placed(self, g: WardGuide, frame: np.ndarray, t: float,
                      avoid: Sequence[Sequence[float]] = ()) -> bool:
        blobs = self.detector.blobs(frame, g.uv, PLACE_R, avoid)
        if blobs is None:
            return False
        with self._lock:
            if g.base_n < BASELINE_CHECKS:
                g.base.extend(blobs)
                g.base_n += 1
                return False
            new = [b for b in blobs if all(math.hypot(b[0] - o[0], b[1] - o[1]) > BLOB_TOL * 1.5 for o in g.base)]
            if g.cand is not None:
                same = [b for b in new if math.hypot(b[0] - g.cand[0], b[1] - g.cand[1]) <= BLOB_TOL]
            else:
                same = []
            if same:
                g.hits += 1
                g.cand = same[0]
            elif new:
                g.cand = min(new, key=lambda b: math.hypot(b[0] - g.uv[0], b[1] - g.uv[1]))
                g.hits = 1
            else:
                g.cand, g.hits = None, 0
            if g.hits >= PLACE_HITS:
                g.placed_t = t
                g.until = t + CONFIRM_S
                return True
            return False

    # ------------------------------------------------------------------ outputs
    def guides(self, t: float) -> list[WardGuide]:
        with self._lock:
            return [g for g in self._guides if t < g.until]

    def active(self, t: float) -> bool:
        return bool(self.guides(t))

    def minimap_guides(self, t: float) -> list[GuideView]:
        """Minimap ward rings (gold; green with a check mark once placed)."""
        out = []
        for g in self.guides(t):
            out.append(GuideView("ward", g.uv, DONE_LABEL if g.placed else g.label, 40, False,
                                 "safe" if g.placed else "gold", g.until, g.since, g.placed))
        return out

    def world_markers(self, t: float, screen: Any, minimap_rect: Any = None,
                      me_uv: Sequence[float] | None = None) -> list[WorldMarker]:
        """Game-view markers (empty without an active guide, a camera rectangle, or when
        ``cfg.ward_world`` is off). Never over the minimap / bottom HUD bar. Never raises."""
        try:
            if not self.world_enabled:
                return []
            guides = self.guides(t)
            if not guides or screen is None:
                return []
            proj = cp.make_projection(self.camera.current(t), screen)
            if proj is None:
                return []
            sx, sy, sw, sh = (float(c) for c in screen[:4])
            k = max(0.5, sh / 1080.0)
            exclude = [cp.hud_bar_rect(screen)]
            if minimap_rect is not None:
                exclude.append(tuple(float(c) for c in minimap_rect[:4]))
            ref = tuple(me_uv) if me_uv is not None else proj.cam_center
            beginner = _skill(self.cfg) == "debutant"
            out: list[WorldMarker] = []
            for g in guides:
                age, left = t - g.since, g.until - t
                p = proj.map_to_screen(*g.uv)
                vis = p is not None and proj.is_visible(g.uv[0], g.uv[1], 60 * k, exclude)
                if g.placed:
                    if vis:
                        out.append(WorldMarker("done", p[0], p[1], label=DONE_LABEL, age=t - (g.placed_t or t),
                                               left=left, key=g.spot_id))
                    continue
                hint = g.hint if beginner else ""
                if vis:
                    out.append(WorldMarker("ground", p[0], p[1], label=LABEL, sub=g.label, age=age, left=left,
                                           key=g.spot_id, hint=hint))
                    continue
                dx, dy = proj.direction(*g.uv)
                ex, ey = edge_point((sx, sy, sw, sh), dx, dy, 70 * k, exclude)
                secs = cp.walk_seconds(ref, g.uv)
                out.append(WorldMarker("edge", ex, ey, dx, dy, LABEL, f"≈ {max(1, int(round(secs)))} s", age, left,
                                       g.spot_id, hint))
            return out[:MAX_GUIDES]
        except Exception:
            log.exception("world_markers failed")
            return []


def edge_point(screen: Sequence[float], dx: float, dy: float, margin: float,
               exclude: Sequence[Sequence[float]] = ()) -> tuple[float, float]:
    """Where the ray from the screen centre along (dx, dy) leaves the screen shrunk by ``margin``,
    moved out of the ``exclude`` rectangles (minimap, HUD bar) along the border."""
    sx, sy, sw, sh = (float(c) for c in screen[:4])
    cx, cy = sx + sw / 2, sy + sh / 2
    hx, hy = max(1.0, sw / 2 - margin), max(1.0, sh / 2 - margin)
    s = min(hx / abs(dx) if abs(dx) > 1e-9 else math.inf, hy / abs(dy) if abs(dy) > 1e-9 else math.inf)
    if not math.isfinite(s):
        s = hy
    x, y = cx + dx * s, cy + dy * s
    lo_x, hi_x, lo_y, hi_y = cx - hx, cx + hx, cy - hy, cy + hy

    def inside(c: tuple[float, float]) -> bool:
        return lo_x - 1 <= c[0] <= hi_x + 1 and lo_y - 1 <= c[1] <= hi_y + 1

    for _ in range(3):
        moved = False
        for r in exclude or ():
            try:
                rx, ry, rw, rh = (float(c) for c in r[:4])
            except (TypeError, ValueError, IndexError):
                continue
            if not (rx - margin <= x <= rx + rw + margin and ry - margin <= y <= ry + rh + margin):
                continue
            # slide ALONG the border it sits on (stays an edge marker), else leave by the shortest way
            if abs(y - lo_y) < 1.0 or abs(y - hi_y) < 1.0:
                cands = [(rx - margin, y), (rx + rw + margin, y)]
            else:
                cands = [(x, ry - margin), (x, ry + rh + margin)]
            cands = [c for c in cands if inside(c)] or [c for c in ((x, ry - margin), (rx - margin, y)) if inside(c)]
            if cands:
                x, y = min(cands, key=lambda c: math.hypot(c[0] - x, c[1] - y))
                moved = True
        if not moved:
            break
    return x, y


__all__ = ["WardGuideManager", "WardGuide", "WorldMarker", "GuideView", "WardPlacedDetector", "edge_point",
           "ward_mask",
           "MAX_GUIDES", "GUIDE_S", "CONFIRM_S", "LABEL", "DONE_LABEL"]
