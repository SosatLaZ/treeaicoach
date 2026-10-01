"""Ward guide: WHERE to put the next ward, shown on the minimap AND in the game view.

:mod:`treeaicoach.wards` decides when wards matter (after leaving base, every ~2.5 min, before
an objective) and which spots are best. This module turns that advice - or a hotkey press
(``cfg.hotkey_ward``, default F7) - into at most :data:`MAX_GUIDES` short-lived guides:

* on the minimap layer: the usual pulsing ward ring (``tactics.MapGuide`` kind "ward"), turned
  green when the ward is placed;
* in the game view (:meth:`WardGuideManager.world_markers`, drawn by
  ``overlay_render.render_world_guides`` in small click-through windows): a ground marker
  (ellipse ring + ward icon + "Ward ici") at the spot projected with
  :mod:`treeaicoach.camera_proj` when it is on screen, else an arrow at the screen edge pointing
  towards it with the walking time; a short check mark once the ward is placed.

A guide lasts :data:`GUIDE_S` at most, or until an ALLIED ward glyph appears on the minimap near
the spot (:class:`WardPlacedDetector`: template match of the three friendly ward glyphs on a
small patch, compared with the patch when the guide started; ~0.1 ms, run at
:data:`CHECK_HZ`). Nothing runs while no guide is active (no camera search, hidden windows).

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
WARD_SIZE = 0.036              # allied ward glyph size, fraction of the minimap width
PLACE_SCORE = 0.60             # template score of a ward glyph ...
PLACE_GAIN = 0.15              # ... clearly higher than when the guide started
PLACE_HITS = 2                 # consecutive checks
HOTKEY_COOLDOWN_S = 1.0
FRIENDLY_WARDS = ("minimap_ward_green_full.png", "minimap_ward_blue_full.png", "minimap_ward_pink_friendly.png")
LABEL = "Ward ici"
DONE_LABEL = "Balise posée"


# ======================================================================================
# Ward-placed detection
# ======================================================================================
class WardPlacedDetector:
    """Best match score (0..1) of a friendly ward glyph near a map point. Never raises."""

    def __init__(self, icons: Sequence[np.ndarray] | None = None) -> None:
        self._icons = list(icons) if icons is not None else None
        self._tpl: dict[int, list[np.ndarray]] = {}
        self._lock = threading.Lock()

    def _load_icons(self) -> list[np.ndarray]:
        if self._icons is None:
            from treeaicoach.overlay_render import load_asset_icon

            self._icons = [i for i in (load_asset_icon(n) for n in FRIENDLY_WARDS) if i is not None]
        return self._icons

    def _templates(self, size: int) -> list[np.ndarray]:
        with self._lock:
            hit = self._tpl.get(size)
            if hit is not None:
                return hit
            out = []
            for rgba in self._load_icons():
                img = cv2.resize(np.asarray(rgba), (size, size), interpolation=cv2.INTER_AREA).astype(np.float32)
                a = img[..., 3:4] / 255.0
                bg = np.array([45.0, 50.0, 40.0], np.float32)          # dark map ground (RGB)
                rgb = img[..., :3] * a + bg * (1.0 - a)
                out.append(np.ascontiguousarray(rgb[..., ::-1]).astype(np.uint8))   # -> BGR
            self._tpl[size] = out
            return out

    def score(self, minimap_bgr: Any, uv: Sequence[float], radius: float = PLACE_R) -> float:
        try:
            if not isinstance(minimap_bgr, np.ndarray) or minimap_bgr.ndim != 3:
                return 0.0
            H, W = minimap_bgr.shape[:2]
            size = max(6, int(round(WARD_SIZE * W)))
            tpls = self._templates(size)
            if not tpls:
                return 0.0
            cx, cy = float(uv[0]) * W, float(uv[1]) * H
            r = radius * W + size / 2 + 1
            x0, y0 = max(0, int(cx - r)), max(0, int(cy - r))
            x1, y1 = min(W, int(cx + r) + 1), min(H, int(cy + r) + 1)
            if x1 - x0 < size or y1 - y0 < size:
                return 0.0
            patch = np.ascontiguousarray(minimap_bgr[y0:y1, x0:x1, :3])
            best = 0.0
            for t in tpls:
                res = cv2.matchTemplate(patch, t, cv2.TM_CCOEFF_NORMED)
                m = float(res.max()) if res.size else 0.0
                if math.isfinite(m):
                    best = max(best, m)
            return best
        except Exception:
            log.debug("ward score failed", exc_info=True)
            return 0.0


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
    baseline: float | None = None
    hits: int = 0

    @property
    def placed(self) -> bool:
        return self.placed_t is not None


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


@dataclass(frozen=True)
class GuideView:
    """Minimap guide (duck-typed like ``tactics.MapGuide``)."""

    kind: str
    uv: tuple[float, float]
    label: str = ""
    priority: int = 40
    arrow: bool = False
    color: str = "gold"
    until: float = 0.0
    since: float = 0.0


def _beep() -> None:
    if sys.platform != "win32":
        return
    try:
        import winsound

        winsound.MessageBeep(getattr(winsound, "MB_OK", 0))
    except Exception:
        pass


class WardGuideManager:
    """Lifecycle of the ward guides + camera tracking while one is active. Thread-safe."""

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
        """Hotkey: show the best spots now (on the next :meth:`update`). False when debounced."""
        with self._lock:
            if t - self._last_request < HOTKEY_COOLDOWN_S:
                return False
            self._last_request = t
            self._request_t = t
            return True

    def start(self, t: float, picks: Sequence[Any], reason: str, until: float | None = None) -> int:
        """Start guides for ``picks`` (``wards.WardPick``-like: ``.spot.id``, ``.uv``, ``.label``)."""
        try:
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
               recommend: Callable[[], Sequence[Any]] | None = None) -> list[str]:
        """One engine tick: new advice / hotkey -> guides; camera + ward-placed detection while active.
        Returns events ("start", "placed", "end"). Never raises."""
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
                self.camera.update(minimap_bgr, t)
                if check:
                    for g in guides:
                        if not g.placed and self._check_placed(g, minimap_bgr, t):
                            events.append("placed")
            return events
        except Exception:
            log.exception("WardGuideManager.update failed")
            return events

    def _check_placed(self, g: WardGuide, frame: np.ndarray, t: float) -> bool:
        s = self.detector.score(frame, g.uv)
        with self._lock:
            if g.baseline is None:
                g.baseline = s
                return False
            if s >= PLACE_SCORE and s - g.baseline >= PLACE_GAIN:
                g.hits += 1
            else:
                g.hits = 0
                g.baseline = min(g.baseline, s) if s < PLACE_SCORE else g.baseline
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
        """Minimap ward rings (gold; green once placed)."""
        out = []
        for g in self.guides(t):
            out.append(GuideView("ward", g.uv, DONE_LABEL if g.placed else g.label, 40, False,
                                 "safe" if g.placed else "gold", g.until, g.since))
        return out

    def world_markers(self, t: float, screen: Any, minimap_rect: Any = None,
                      me_uv: Sequence[float] | None = None) -> list[WorldMarker]:
        """Game-view markers (empty without an active guide or a camera rectangle). Never raises."""
        try:
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
                if vis:
                    out.append(WorldMarker("ground", p[0], p[1], label=LABEL, sub=g.label, age=age, left=left,
                                           key=g.spot_id))
                    continue
                dx, dy = proj.direction(*g.uv)
                ex, ey = edge_point((sx, sy, sw, sh), dx, dy, 70 * k, exclude)
                secs = cp.walk_seconds(ref, g.uv)
                out.append(WorldMarker("edge", ex, ey, dx, dy, LABEL, f"≈ {max(1, int(round(secs)))} s", age, left,
                                       g.spot_id))
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
    for r in exclude or ():
        try:
            rx, ry, rw, rh = (float(c) for c in r[:4])
        except (TypeError, ValueError, IndexError):
            continue
        if rx - margin <= x <= rx + rw + margin and ry - margin <= y <= ry + rh + margin:
            # leave the rectangle by the shortest way that stays on screen
            cands = [(x, ry - margin), (rx - margin, y)]
            cands = [c for c in cands if sx + margin * 0.5 <= c[0] <= sx + sw and sy + margin * 0.5 <= c[1] <= sy + sh]
            if cands:
                x, y = min(cands, key=lambda c: math.hypot(c[0] - x, c[1] - y))
    return x, y


__all__ = ["WardGuideManager", "WardGuide", "WorldMarker", "GuideView", "WardPlacedDetector", "edge_point",
           "MAX_GUIDES", "GUIDE_S", "CONFIRM_S", "LABEL", "DONE_LABEL"]
