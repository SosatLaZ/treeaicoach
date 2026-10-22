"""Keeps our own overlay marks out of the minimap frames the detector reads.

Real 2.10.1 / 2.11 captures: the rings, "JGL" tags, timers drawn by the overlay over the real
minimap were *inside* the captured frames (``SetWindowDisplayAffinity`` not effective, or the
setting off): the detector read its own drawings (a red ring around an ally = an enemy, the JGL
tag looping on a stale position). Three layers of defence, shared by the overlay thread and the
engine's capture stage through the module singleton :data:`GUARD`:

1. the overlay excludes EVERY window that can cover the minimap from capture and checks it with
   ``GetWindowDisplayAffinity`` (:meth:`CaptureGuard.note_exclusion`);
2. self-verification: every :data:`PROBE_PERIOD_S` the minimap layer carries a tiny checker
   (:func:`marker_pattern`, :data:`MARKER_PX` px, magenta / teal) at :data:`MARKER_XY` for one
   short moment; the next captured frame is searched for it (:func:`marker_visible`). Seen =
   exclusion failed -> :meth:`CaptureGuard.safe_mode`: the marks stay on the real minimap (never
   a radar copy, 2.12.1) and layer 3 keeps them out of the detector, "L'overlay
   apparaît dans la capture" in the self-check. ``overlay_hide_from_capture=False`` (streamers)
   puts the overlay in the same safe mode right away;
3. the overlay publishes the mask of the pixels it drew (minimap coordinates); while the
   exclusion is not proven by a probe, :meth:`CaptureGuard.clean_frame` inpaints those pixels in
   the captured frame before detection.

Every public method is thread-safe and never raises.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import numpy as np

log = logging.getLogger("treeaicoach.overlay")

#: marker: MARKER_PX x MARKER_PX px checker of 2 x 2 px cells, at MARKER_XY in the minimap layer
MARKER_PX = 4
MARKER_CELL = 2
MARKER_XY = (3, 3)
#: BGRA colours of the two checker cells (magenta / teal): never this pure on League's minimap
MARKER_A = (255, 0, 255, 255)
MARKER_B = (160, 160, 0, 255)
#: a captured cell matches its colour within this per-channel tolerance
MARKER_TOL = 48
#: probe every PROBE_PERIOD_S (first one PROBE_FIRST_S after the layer appears); while failed,
#: re-probed every PROBE_RETRY_S (the user may have fixed Windows / the setting)
PROBE_FIRST_S = 2.0
PROBE_PERIOD_S = 45.0
PROBE_RETRY_S = 30.0
#: a frame grabbed at least PROBE_SETTLE_S after the marker was shown counts (DWM composition);
#: the marker is withdrawn after PROBE_MAX_S without any frame
PROBE_SETTLE_S = 0.12
PROBE_MAX_S = 1.5
#: drawn-pixel masks older than MASK_KEEP_S are forgotten (the capture lags the overlay a bit:
#: the union of the recent masks is cleaned)
MASK_KEEP_S = 0.6
MASK_DILATE_PX = 2

PROBLEM_TEXT = "L'overlay apparaît dans la capture"


def marker_pattern() -> np.ndarray:
    """The BGRA probe checker (MARKER_PX x MARKER_PX, premultiplied = opaque colours)."""
    out = np.zeros((MARKER_PX, MARKER_PX, 4), np.uint8)
    for y in range(MARKER_PX):
        for x in range(MARKER_PX):
            a = ((x // MARKER_CELL) + (y // MARKER_CELL)) % 2 == 0
            out[y, x] = MARKER_A if a else MARKER_B
    return out


def stamp_marker(img: np.ndarray) -> np.ndarray:
    """``img`` (BGRA minimap layer) with the probe checker drawn at :data:`MARKER_XY` (copy)."""
    out = np.array(img, np.uint8, copy=True)
    x, y = MARKER_XY
    if out.ndim == 3 and out.shape[2] == 4 and out.shape[0] >= y + MARKER_PX and out.shape[1] >= x + MARKER_PX:
        out[y:y + MARKER_PX, x:x + MARKER_PX] = marker_pattern()
    return out


def marker_visible(frame: Any, scale: tuple[float, float] = (1.0, 1.0)) -> bool:
    """Is the probe checker in this captured BGR frame (same rect as the minimap layer;
    ``scale`` = frame px per layer px)? Every cell must match its colour. Never raises."""
    try:
        f = np.asarray(frame)
        if f.ndim != 3 or f.shape[2] < 3:
            return False
        sx, sy = float(scale[0]), float(scale[1])
        pat = marker_pattern()
        x0, y0 = MARKER_XY
        for cy in range(0, MARKER_PX, MARKER_CELL):
            for cx in range(0, MARKER_PX, MARKER_CELL):
                # centre of the cell, in frame pixels
                px = int((x0 + cx + MARKER_CELL / 2.0) * sx)
                py = int((y0 + cy + MARKER_CELL / 2.0) * sy)
                if not (0 <= py < f.shape[0] and 0 <= px < f.shape[1]):
                    return False
                want = pat[cy, cx, :3].astype(int)
                got = f[py, px, :3].astype(int)
                if np.abs(got - want).max() > MARKER_TOL:
                    return False
        return True
    except Exception:
        return False


def drawn_mask(img: Any) -> np.ndarray | None:
    """Boolean mask of the pixels a BGRA layer image draws (alpha > 0), dilated a little."""
    try:
        a = np.asarray(img)
        if a.ndim != 3 or a.shape[2] != 4:
            return None
        m = (a[..., 3] > 8).astype(np.uint8)
        if not m.any():
            return np.zeros(m.shape, bool)
        import cv2

        k = 2 * MASK_DILATE_PX + 1
        return cv2.dilate(m, np.ones((k, k), np.uint8)) > 0
    except Exception:
        return None


class CaptureGuard:
    """Shared state: exclusion per window, probe, drawn masks. See the module docstring."""

    def __init__(self, clock: Any = None, enforce: bool = True) -> None:
        self._lock = threading.Lock()
        #: False (fake windows / benchmarks): never safe mode, never probes; masks still kept
        self.enforce = bool(enforce)
        self._clock = clock or time.monotonic
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.wanted = True                     # cfg.overlay_hide_from_capture
            self.excluded: dict[str, bool] = {}    # window name -> affinity verified
            self.probe_state = "unknown"           # unknown | ok | failed
            self.probe_ok = 0
            self.probe_fail = 0
            self.last_reason = ""
            self._probe_shown: float | None = None   # marker on screen since (clock)
            self._probe_rect: tuple | None = None
            self._probe_size: tuple[int, int] | None = None
            self._next_probe: float | None = None
            self._masks: list[tuple[float, tuple, np.ndarray]] = []
            self.cleaned_frames = 0
            self._logged: str | None = None

    # ------------------------------------------------------------------ overlay side
    def set_wanted(self, on: bool) -> None:
        with self._lock:
            on = bool(on)
            if on != self.wanted:
                self.wanted = on
                self.probe_state = "unknown"
                self._next_probe = None
        self._log_mode()

    def note_exclusion(self, name: str, ok: bool) -> None:
        """Result of ``SetWindowDisplayAffinity`` + ``GetWindowDisplayAffinity`` for a window."""
        with self._lock:
            self.excluded[str(name)] = bool(ok)
            if not ok:
                self.last_reason = f"exclusion refusée par Windows ({name})"
        self._log_mode()

    def api_excluded(self, names: Any = None) -> bool:
        with self._lock:
            ex = dict(self.excluded)
        keys = list(names) if names is not None else list(ex)
        return bool(keys) and all(ex.get(k, False) for k in keys)

    def safe_mode(self) -> bool:
        """True = our marks may be in the captured frames (exclusion off, refused, or the probe saw
        our marker in the capture). The marks stay drawn ON the real minimap (2.12.1, real
        feedback 2.15 "pas de tracker"); :meth:`clean_frame` keeps them out of the detection."""
        with self._lock:
            if not self.enforce:
                return False
            if not self.wanted:
                return True
            if self.excluded.get("minimap") is False:
                return True
            return self.probe_state == "failed"

    def status(self) -> str:
        """"off" (setting), "refused" (Windows), "failed" (probe saw the marker), "ok" (probe
        passed), "unknown" (no probe yet)."""
        with self._lock:
            if not self.wanted:
                return "off"
            if self.excluded.get("minimap") is False:
                return "refused"
            return self.probe_state

    def problem(self) -> str | None:
        """French self-check problem text, or None."""
        st = self.status()
        if st in ("failed", "refused"):
            return (f"{PROBLEM_TEXT} : ses repères étaient vus par la détection "
                    f"({self.last_reason or st}). Mode sûr : nos repères sont effacés des images "
                    "avant la détection.")
        return None

    def want_probe(self, now: float | None = None) -> bool:
        """Overlay: draw the marker on this minimap frame? (keeps it until a frame was checked)."""
        now = self._clock() if now is None else float(now)
        with self._lock:
            if not self.enforce or not self.wanted or self.excluded.get("minimap") is False:
                return False
            if self._probe_shown is not None:
                if now - self._probe_shown > PROBE_MAX_S:
                    self._probe_shown = None          # no frame came: try again later
                    self._next_probe = now + PROBE_RETRY_S
                    return False
                return True
            if self._next_probe is None:
                self._next_probe = now + PROBE_FIRST_S
            return now >= self._next_probe

    def probe_shown(self, rect: Any, size: tuple[int, int], now: float | None = None) -> None:
        """Overlay: the marker was uploaded in a window at ``rect`` (x, y, w, h) of ``size``."""
        now = self._clock() if now is None else float(now)
        with self._lock:
            if self._probe_shown is None:
                self._probe_shown = now
            self._probe_rect = tuple(int(v) for v in rect)
            self._probe_size = (int(size[0]), int(size[1]))

    def probe_pending(self) -> bool:
        with self._lock:
            return self._probe_shown is not None

    def note_drawn(self, rect: Any, img: Any, now: float | None = None) -> None:
        """Overlay: the minimap window now shows ``img`` (BGRA) at ``rect``; empty = hidden."""
        now = self._clock() if now is None else float(now)
        m = drawn_mask(img) if img is not None else None
        with self._lock:
            self._masks = [e for e in self._masks if now - e[0] <= MASK_KEEP_S]
            if m is not None and m.any():
                self._masks.append((now, tuple(int(v) for v in rect), m))

    def note_hidden(self, now: float | None = None) -> None:
        """Overlay: the minimap window was hidden (its last image may still be in a frame)."""
        now = self._clock() if now is None else float(now)
        with self._lock:
            if self._masks:
                t, r, m = self._masks[-1]
                self._masks = [e for e in self._masks if now - e[0] <= MASK_KEEP_S] or [(now, r, m)]

    def mask_for(self, rect: Any, shape: tuple[int, int], now: float | None = None) -> np.ndarray | None:
        """Union of the recent drawn masks mapped onto a captured frame of ``rect`` / ``shape``."""
        now = self._clock() if now is None else float(now)
        with self._lock:
            masks = [e for e in self._masks if now - e[0] <= MASK_KEEP_S]
            self._masks = masks
        if not masks:
            return None
        try:
            import cv2

            H, W = int(shape[0]), int(shape[1])
            fx, fy, fw, fh = (int(v) for v in rect)
            out = np.zeros((H, W), np.uint8)
            for _t, (x, y, w, h), m in masks:
                if w <= 0 or h <= 0 or fw <= 0 or fh <= 0:
                    continue
                sx, sy = W / fw, H / fh          # frame px per screen px
                mm = m.astype(np.uint8)
                tw, th = max(1, int(round(w * sx))), max(1, int(round(h * sy)))
                if (tw, th) != (mm.shape[1], mm.shape[0]):
                    mm = cv2.resize(mm, (tw, th), interpolation=cv2.INTER_NEAREST)
                ox, oy = int(round((x - fx) * sx)), int(round((y - fy) * sy))
                x0, y0 = max(0, ox), max(0, oy)
                x1, y1 = min(W, ox + tw), min(H, oy + th)
                if x1 > x0 and y1 > y0:
                    out[y0:y1, x0:x1] |= mm[y0 - oy:y1 - oy, x0 - ox:x1 - ox]
            return out > 0 if out.any() else None
        except Exception:
            log.debug("overlay mask failed", exc_info=True)
            return None

    # ------------------------------------------------------------------ capture side
    def check_frame(self, frame: Any, rect: Any, now: float | None = None) -> str | None:
        """Engine: a minimap frame of screen ``rect`` was just grabbed. Settles a pending probe
        ("seen" / "clean"), else None. Never raises."""
        now = self._clock() if now is None else float(now)
        with self._lock:
            shown, prect, psize = self._probe_shown, self._probe_rect, self._probe_size
        if shown is None or now - shown < PROBE_SETTLE_S or prect is None:
            return None
        try:
            f = np.asarray(frame)
            fx, fy, fw, fh = (int(v) for v in rect)
            if fw <= 0 or fh <= 0:
                return None
            sx, sy = f.shape[1] / fw, f.shape[0] / fh
            # marker position in the frame: layer origin offset + MARKER_XY
            sub = f[max(0, int(round((prect[1] - fy) * sy))):, max(0, int(round((prect[0] - fx) * sx))):]
            seen = marker_visible(sub, (sx, sy))
        except Exception:
            return None
        with self._lock:
            self._probe_shown = None
            if seen:
                self.probe_fail += 1
                self.probe_state = "failed"
                self.last_reason = "repère de test retrouvé dans l'image capturée"
                self._next_probe = now + PROBE_RETRY_S
            else:
                self.probe_ok += 1
                self.probe_state = "ok"
                self._next_probe = now + PROBE_PERIOD_S
        self._log_mode()
        return "seen" if seen else "clean"

    def clean_frame(self, frame: Any, rect: Any, now: float | None = None) -> Any:
        """Engine: ``frame`` with the pixels our overlay drew inpainted, unless a probe proved the
        exclusion works. Returns the frame unchanged when there is nothing to clean."""
        try:
            with self._lock:
                proven = self.wanted and self.probe_state == "ok" and self.excluded.get("minimap") is True
                probing = self._probe_shown is not None
            if proven and not probing:
                return frame
            f = np.asarray(frame)
            if f.ndim != 3 or f.shape[2] != 3:
                return frame
            mask = self.mask_for(rect, f.shape[:2], now)
            if probing and not proven:
                # the probe marker itself never reaches the detector
                mk = np.zeros(f.shape[:2], bool)
                fx, fy, fw, fh = (int(v) for v in rect)
                with self._lock:
                    prect = self._probe_rect
                if prect is not None and fw > 0 and fh > 0:
                    sx, sy = f.shape[1] / fw, f.shape[0] / fh
                    x = int((prect[0] - fx + MARKER_XY[0]) * sx) - 1
                    y = int((prect[1] - fy + MARKER_XY[1]) * sy) - 1
                    n = int(MARKER_PX * max(sx, sy)) + 3
                    mk[max(0, y):max(0, y + n), max(0, x):max(0, x + n)] = True
                mask = mk if mask is None else (mask | mk)
            if mask is None or not mask.any():
                return frame
            import cv2

            out = cv2.inpaint(np.ascontiguousarray(f, np.uint8), mask.astype(np.uint8), 3, cv2.INPAINT_TELEA)
            with self._lock:
                self.cleaned_frames += 1
            return out
        except Exception:
            log.debug("overlay frame cleaning failed", exc_info=True)
            return frame

    # ------------------------------------------------------------------ diagnostics
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"wanted": self.wanted, "excluded": dict(self.excluded), "probe": self.probe_state,
                    "probe_ok": self.probe_ok, "probe_fail": self.probe_fail,
                    "cleaned_frames": self.cleaned_frames, "reason": self.last_reason}

    def _log_mode(self) -> None:
        st = self.status()
        if st != self._logged:
            self._logged = st
            msg = {"off": "réglage « cacher de la capture » désactivé",
                   "refused": "exclusion de capture refusée par Windows",
                   "failed": "repère de test vu dans la capture",
                   "ok": "exclusion de capture vérifiée (repère de test invisible)",
                   "unknown": "exclusion de capture demandée, pas encore vérifiée"}.get(st, st)
            safe = self.safe_mode()
            (log.warning if safe and st in ("failed", "refused") else log.info)(
                "Overlay capture guard: %s -> %s", msg,
                "marques sur la minimap, effacées des images avant la détection" if safe else "marques sur la minimap")


#: the one guard shared by the overlay thread and the engine
GUARD = CaptureGuard()
