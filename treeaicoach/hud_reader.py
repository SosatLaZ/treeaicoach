"""Bottom-centre champion HUD reader: my portrait (+ dead / alive), screen capture only.

The LoL HUD shows the local player's champion portrait in a round frame at the bottom of
the screen (left of the spell bar). Its size and place depend on the resolution and the HUD
scale, so :meth:`HudReader.calibrate` finds it once per window size: Hough circles in the
bottom band (y >= 82 % of the height, 20-70 % of the width, radius 2-5 % of the height),
the largest strong circle wins. Afterwards :meth:`HudReader.read` only crops it (well under
3 ms) and returns:

* ``portrait``: the inner disc as a square BGR crop (resized to ``PORTRAIT_PX``) - also
  usable as a learned reference of my real (possibly custom / modded) skin;
* ``saturation``: mean HSV saturation of the disc; the portrait of a dead champion is
  greyed, so ``dead`` is True when the saturation falls below ``DEAD_SAT_FRAC`` of the
  learned alive reference (EMA of the saturation while alive).

Never raises: every public method returns None / False on any problem.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

log = logging.getLogger(__name__)

BAND_TOP = 0.82          # search band: bottom 18 % of the window ...
BAND_X = (0.20, 0.70)    # ... between 20 % and 70 % of its width
R_MIN, R_MAX = 0.02, 0.05  # portrait frame radius / window height
INNER = 0.80             # cropped disc radius / detected frame radius
PORTRAIT_PX = 64
DEAD_SAT_FRAC = 0.45     # saturation below this fraction of the alive reference -> dead
MIN_ALIVE_SAT = 40.0     # minimum reference saturation (a naturally grey portrait: no verdict)
REF_RATE = 0.1


@dataclass
class HudRead:
    portrait: np.ndarray          # BGR PORTRAIT_PX x PORTRAIT_PX
    saturation: float
    dead: bool | None             # None while no alive reference exists
    centre: tuple[int, int]       # portrait centre in window pixels
    radius: int


def _as_bgr(img: Any) -> np.ndarray | None:
    if not isinstance(img, np.ndarray) or img.ndim != 3 or img.shape[2] < 3:
        return None
    if img.dtype != np.uint8:
        img = np.clip(img, 0, 255).astype(np.uint8)
    return img[:, :, :3]


def locate_portrait(screen_bgr: Any) -> tuple[int, int, int] | None:
    """``(cx, cy, r)`` of the HUD portrait frame in window pixels, or None. Never raises."""
    try:
        img = _as_bgr(screen_bgr)
        if img is None:
            return None
        H, W = img.shape[:2]
        if H < 200 or W < 300:
            return None
        y0, x0, x1 = int(BAND_TOP * H), int(BAND_X[0] * W), int(BAND_X[1] * W)
        g = cv2.cvtColor(np.ascontiguousarray(img[y0:, x0:x1]), cv2.COLOR_BGR2GRAY)
        g = cv2.GaussianBlur(g, (5, 5), 1.2)
        cs = cv2.HoughCircles(g, cv2.HOUGH_GRADIENT, dp=1, minDist=max(10, int(0.03 * H)),
                              param1=120, param2=30, minRadius=int(R_MIN * H),
                              maxRadius=int(R_MAX * H))
        if cs is None or not len(cs[0]):
            return None
        # the portrait frame is the left-most strong circle of the HUD (Hough order = votes);
        # among the 3 strongest, keep the largest, ties -> left-most
        top = sorted(cs[0][:3].tolist(), key=lambda c: (-round(c[2] / 3.0), c[0]))
        cx, cy, r = top[0]
        return int(round(cx + x0)), int(round(cy + y0)), int(round(r))
    except Exception:
        log.debug("HUD portrait location failed", exc_info=True)
        return None


class HudReader:
    """Calibrate once per window size, then crop / read the portrait cheaply. Never raises."""

    def __init__(self) -> None:
        self._loc: tuple[int, int, int] | None = None
        self._size: tuple[int, int] | None = None
        self._ref_sat: float | None = None
        self._mask: np.ndarray | None = None
        self.last_ms = 0.0

    @property
    def location(self) -> tuple[int, int, int] | None:
        return self._loc

    def reset(self) -> None:
        """New game: forget the location and the alive reference."""
        self._loc = self._size = self._ref_sat = None

    def calibrate(self, screen_bgr: Any) -> bool:
        img = _as_bgr(screen_bgr)
        if img is None:
            return False
        loc = locate_portrait(img)
        if loc is None:
            return False
        self._loc, self._size = loc, img.shape[:2]
        return True

    def read(self, screen_bgr: Any, alive_hint: bool | None = None) -> HudRead | None:
        """Portrait crop + dead / alive. ``alive_hint`` (Live API) updates the reference."""
        t0 = time.perf_counter()
        try:
            img = _as_bgr(screen_bgr)
            if img is None:
                return None
            if self._loc is None or self._size != img.shape[:2]:
                if not self.calibrate(img):
                    return None
            cx, cy, r = self._loc  # type: ignore[misc]
            ri = max(4, int(round(INNER * r)))
            H, W = img.shape[:2]
            if cx - ri < 0 or cy - ri < 0 or cx + ri >= W or cy + ri >= H:
                return None
            crop = cv2.resize(img[cy - ri:cy + ri, cx - ri:cx + ri], (PORTRAIT_PX, PORTRAIT_PX),
                              interpolation=cv2.INTER_AREA)
            if self._mask is None:
                yy, xx = np.mgrid[0:PORTRAIT_PX, 0:PORTRAIT_PX]
                c = (PORTRAIT_PX - 1) / 2.0
                self._mask = ((xx - c) ** 2 + (yy - c) ** 2) <= (0.95 * c) ** 2
            sat = float(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[:, :, 1][self._mask].mean())
            if alive_hint is not False and (alive_hint or self._ref_sat is None
                                            or sat >= DEAD_SAT_FRAC * self._ref_sat):
                self._ref_sat = sat if self._ref_sat is None else \
                    (1 - REF_RATE) * self._ref_sat + REF_RATE * sat
            dead: bool | None = None
            if self._ref_sat is not None and self._ref_sat >= MIN_ALIVE_SAT:
                dead = sat < DEAD_SAT_FRAC * self._ref_sat
            return HudRead(portrait=crop, saturation=sat, dead=dead, centre=(cx, cy), radius=r)
        except Exception:
            log.debug("HUD read failed", exc_info=True)
            return None
        finally:
            self.last_ms = 1000 * (time.perf_counter() - t0)


__all__ = ["HudReader", "HudRead", "locate_portrait"]
