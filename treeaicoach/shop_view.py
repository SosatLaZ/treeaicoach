"""The in-game shop seen on screen: is it open, where is it, where is the recommended item in it.

Why: the item set written into the client (:mod:`treeaicoach.client_sync`) only lands in the shop's
third tab ("Sets d'objets"), never in "Conseillés" (Riot's own dynamic build), so a beginner does not
see it. When the shop is open, the overlay rings the recommended item's icon wherever the shop shows
it (Conseillés "Achats courants" row, the recipe tree, Tous les objets grid, our item set...). When the
shop is closed or the icon is not on screen, nothing is drawn: the HUD item card stays the guide.

Detection (screen capture only, any resolution / HUD scale, no fixed coordinates):

* the shop window is a dark navy panel (HSV hue ~100, V < 75) framed by a thin muted-gold border
  (hue 10-32, S 40-180, V 70-210, 1-3 px). Long horizontal / vertical gold runs (morphological opening
  with a kernel of a quarter of the screen) give the frame; a candidate is kept when its aspect ratio,
  its navy interior and the vertical gold divider between the item list and the item details agree
  with :data:`GEOMETRY` (measured on three references, see below);
* the recommended item is found by normalised template matching of its icon (Data Dragon art = shop
  art) at the icon size the frame implies (:data:`GEOMETRY` ``icon``), inside the frame only.

GEOMETRY, as fractions of the shop frame (outer gold border), measured on:
a real 2026 French client screenshot of the user (tests/fixtures/ingame4_2000x1125.jpg, frame
474..1558 x 164..938 at 2000x1125 = 0.688 of the screen height), Riot's preseason shop article
(1600x900, frame 326..1295 x 52..742 = 0.767 of the height: a larger HUD scale) and the League wiki
shop page crop. All three: width / height 1.40, list | details divider at 0.61 of the width, tabs
row 0 .. 0.072, item icons 0.054 .. 0.060 of the frame height. The frame is NOT at a fixed place
(top at 5.8 % and 14.6 % of the screen in the two full screenshots): it is searched for.

Pure functions on BGR ``np.ndarray`` frames; nothing raises (None / empty results on any failure).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

log = logging.getLogger(__name__)

#: shop frame geometry (fractions of the outer frame), see the module docstring
GEOMETRY = {
    "aspect": 1.40,             # width / height
    "aspect_tol": 0.12,
    "divider": 0.61,            # x of the vertical divider list | details
    "divider_tol": 0.035,
    "tabs": (0.0, 0.072),       # y band of the three tabs
    "list": (0.0, 0.61),        # x band of the item list (left part)
    "icon": (0.050, 0.064),     # icon side / frame height
    "min_h": 0.40,              # frame height / screen height (HUD scale 0.01 .. 1.0, any resolution)
    "max_h": 0.95,
}
#: minimum normalised correlation of the icon match to draw the ring
MATCH_MIN = 0.78
#: the screen is searched for the frame at this height (px): the 1-3 px border stays >= 1 px
WORK_H = 720.0
#: the shop frame is brought to this height (px) for the icon search (icons ~ 25 px: fast, enough)
MATCH_FRAME_H = 450.0
#: a look older than this is not drawn (the shop may have closed / scrolled since)
STALE_S = 2.5
#: seconds between two shop looks while the shop may be open (in base / dead)
LOOK_EVERY_S = 1.0
#: ... while the last look saw it closed (a full-window grab + ~80 ms of search per look: in base or
#: dead most of the time the shop is closed; the ring shows up to 2.5 s after it opens)
LOOK_CLOSED_S = 2.5


@dataclass(frozen=True)
class ShopFrame:
    rect: tuple[int, int, int, int]      # x, y, w, h of the outer gold frame, frame px
    score: float                         # 0..1

    @property
    def divider_x(self) -> int:
        return int(round(self.rect[0] + GEOMETRY["divider"] * self.rect[2]))


@dataclass(frozen=True)
class ItemSpot:
    rect: tuple[int, int, int, int]      # x, y, w, h of the item icon, frame px
    score: float
    in_list: bool                        # in the item list (left part), else the details part


def _masks(bgr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    import cv2

    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    gold = ((h >= 10) & (h <= 32) & (s >= 40) & (s <= 180) & (v >= 70) & (v <= 210)).astype(np.uint8)
    navy = ((h >= 88) & (h <= 118) & (s >= 90) & (v <= 75)).astype(np.uint8)
    return gold, navy


def _segments(mask: np.ndarray, horizontal: bool, min_len: int) -> list[tuple[int, int, int]]:
    """Long straight runs of ``mask``: (position across, start, end) along the run direction."""
    import cv2

    k = cv2.getStructuringElement(cv2.MORPH_RECT, (min_len, 1) if horizontal else (1, min_len))
    opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(opened, connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, h, _a = stats[i]
        if horizontal:
            out.append((int(y + h // 2), int(x), int(x + w)))
        else:
            out.append((int(x + w // 2), int(y), int(y + h)))
    return out


def _ridge(val: np.ndarray, x0: int, x1: int, y0: int, y1: int) -> float:
    """Best fraction of rows in [y0, y1) where column x (x0 <= x < x1) is a thin vertical line brighter
    than both sides (the grey divider between the item list and the item details)."""
    H, W = val.shape
    x0, x1, y0, y1 = max(2, x0), min(W - 2, x1), max(0, y0), min(H, y1)
    if x1 <= x0 or y1 - y0 < 10:
        return 0.0
    band = val[y0:y1, x0 - 2:x1 + 2]
    c = band[:, 2:-2]
    # a 1-2 px line: brighter than the pixels 2 px away on both sides
    ridge = (c - np.maximum(band[:, :-4], band[:, 4:])) >= 6
    ridge2 = ridge[:, :-1] | ridge[:, 1:] if ridge.shape[1] > 1 else ridge
    return float(ridge2.mean(axis=0).max()) if ridge2.size else 0.0


def find_shop(frame: Any) -> ShopFrame | None:
    """The open shop's frame in a BGR screen capture (the game window), None when not open."""
    try:
        import cv2

        img = np.asarray(frame)
        if img.ndim != 3 or img.shape[0] < 200 or img.shape[1] < 300:
            return None
        H0, W0 = img.shape[:2]
        f = min(1.0, WORK_H / H0)                        # work at <= 720 px high (border >= 1 px)
        small = cv2.resize(img, (int(W0 * f), int(H0 * f)), interpolation=cv2.INTER_AREA) if f < 1 else img
        H, W = small.shape[:2]
        gold, navy = _masks(small)
        val = small.max(axis=2).astype(np.int16)
        hmin = GEOMETRY["min_h"] * H
        hs = _segments(gold, True, max(20, int(0.18 * W)))
        vs = _segments(gold, False, max(20, int(0.12 * H)))
        if not hs or len(vs) < 2:
            return None
        navy_ii = cv2.integral(navy)
        best: ShopFrame | None = None
        tol = 0.03 * W
        for (yt, xt0, xt1) in hs:
            # left / right borders: long vertical runs starting at the top border's ends (the bottom
            # border is often cut by the HUD / stats panel: the sides give the height)
            def side(xs: float) -> tuple[int, int] | None:
                c = [(x, b) for (x, a, b) in vs if abs(x - xs) <= tol and yt - 6 <= a <= yt + 0.06 * W and b - yt >= hmin]
                return min(c, key=lambda t: abs(t[0] - xs)) if c else None
            left, right = side(xt0), side(xt1)
            if left is None or right is None or right[0] <= left[0]:
                continue
            xl, xr = left[0], right[0]
            yb = max(left[1], right[1])
            h, w = yb - yt, xr - xl
            if h < hmin or h > GEOMETRY["max_h"] * H:
                continue
            aspect = w / float(h)
            if abs(aspect - GEOMETRY["aspect"]) > GEOMETRY["aspect_tol"]:
                continue
            xd = xl + GEOMETRY["divider"] * w
            div = _ridge(val, int(xd - GEOMETRY["divider_tol"] * w), int(xd + GEOMETRY["divider_tol"] * w),
                         int(yt + 0.12 * h), int(yb - 0.12 * h)) >= 0.55
            bottom = any(abs(y - yb) <= 0.02 * h and min(b, xr) - max(a, xl) >= 0.3 * w for (y, a, b) in hs)
            ix0, iy0, ix1, iy1 = xl + 2, yt + 2, xr - 1, yb - 1
            area = max(1, (ix1 - ix0) * (iy1 - iy0))
            nav = float(navy_ii[iy1, ix1] - navy_ii[iy0, ix1] - navy_ii[iy1, ix0] + navy_ii[iy0, ix0]) / area
            if nav < 0.30:
                continue
            score = 0.35 * min(1.0, nav / 0.6) + 0.3 * (1.0 if div else 0.0) + 0.1 * (1.0 if bottom else 0.0) \
                + 0.25 * (1.0 - abs(aspect - GEOMETRY["aspect"]) / GEOMETRY["aspect_tol"])
            if best is None or score > best.score:
                h = max(h, w / GEOMETRY["aspect"])      # the sides may stop above a HUD-covered bottom
                best = ShopFrame((int(round(xl / f)), int(round(yt / f)), int(round(w / f)), int(round(h / f))),
                                 round(score, 3))
        return best if best is not None and best.score >= 0.6 else None
    except Exception:
        log.debug("find_shop failed", exc_info=True)
        return None


def _icon_bgr(icon: Any) -> np.ndarray | None:
    a = np.asarray(icon) if icon is not None else None
    if a is None or a.ndim != 3 or a.shape[0] < 8:
        return None
    if a.shape[2] == 4:          # RGBA (item_icons.rgba) -> BGR
        return np.ascontiguousarray(a[..., [2, 1, 0]])
    return np.ascontiguousarray(a[..., :3])


def find_item(frame: Any, shop: ShopFrame, icon: Any, icon_is_rgba: bool = True) -> ItemSpot | None:
    """Where ``icon`` (the item's Data Dragon art, RGBA by default) is shown inside ``shop``, None
    when it is not visible (other tab, scrolled away) or not clearly matched. The same item is often
    shown several times (popular row, recipe tree, details): the place in the item list (left part,
    where a click selects it) wins over the details part when both match."""
    try:
        import cv2

        img = np.asarray(frame)
        ico = _icon_bgr(icon) if icon_is_rgba else (np.asarray(icon)[..., :3] if icon is not None else None)
        if ico is None or shop is None or img.ndim != 3:
            return None
        x, y, w, h = shop.rect
        H0, W0 = img.shape[:2]
        x0, y0 = max(0, x), max(0, y + int(GEOMETRY["tabs"][1] * h))
        x1, y1 = min(W0, x + w), min(H0, y + h)
        roi = img[y0:y1, x0:x1]
        if roi.size == 0:
            return None
        f = min(1.0, MATCH_FRAME_H / max(1, h))         # fixed working scale: icons ~ 25 px
        roi_s = cv2.resize(roi, (max(1, int(roi.shape[1] * f)), max(1, int(roi.shape[0] * f))),
                           interpolation=cv2.INTER_AREA) if f < 1 else roi
        xdiv = (x + GEOMETRY["list"][1] * w - x0) * f
        found: list[tuple[float, float, float, int]] = []          # (score, x, y, side) per scale
        lo, hi = GEOMETRY["icon"]
        for k in np.linspace(lo, hi, 3):
            side = int(round(k * h * f))
            m = max(2, int(round(side * 0.14)))          # skip the icon's own border (the shop frames it)
            if side - 2 * m < 8:
                continue
            t = cv2.resize(ico, (side, side), interpolation=cv2.INTER_AREA)[m:side - m, m:side - m]
            if t.shape[0] >= roi_s.shape[0] or t.shape[1] >= roi_s.shape[1]:
                continue
            res = cv2.matchTemplate(roi_s, t, cv2.TM_CCOEFF_NORMED)
            for part in ((0, int(xdiv)), (int(xdiv), res.shape[1])):      # best in the list, best in the details
                sub = res[:, max(0, part[0]):max(0, part[1])]
                if sub.size == 0:
                    continue
                _mn, mx, _ml, loc = cv2.minMaxLoc(sub)
                px, py = loc[0] + max(0, part[0]), loc[1]
                patch = roi_s[py:py + t.shape[0], px:px + t.shape[1]]
                if patch.shape[:2] != t.shape[:2] or float(patch.std()) < 0.2 * float(t.std()):
                    continue                             # a flat dark area matches anything: not an icon
                found.append((float(mx), px - m, py - m, side))
        good = [c for c in found if c[0] >= MATCH_MIN]
        if not good:
            return None
        in_list = [c for c in good if c[1] + c[3] / 2 < xdiv]
        sc, bx, by, side = max(in_list or good, key=lambda c: c[0])
        rx, ry, rs = x0 + bx / f, y0 + by / f, side / f
        return ItemSpot((int(round(rx)), int(round(ry)), int(round(rs)), int(round(rs))), round(sc, 3), bool(in_list))
    except Exception:
        log.debug("find_item failed", exc_info=True)
        return None


@dataclass(frozen=True)
class ShopHighlight:
    """What the overlay draws: a ring around ``spot`` (screen px), nothing else."""
    item_id: int
    rect: tuple[int, int, int, int]      # x, y, w, h on the screen
    shop: tuple[int, int, int, int]      # the shop frame on the screen


class ShopWatcher:
    """Looks at the shop about every :data:`LOOK_EVERY_S` while I can shop (the item card's ``shop``
    flag: in base / dead), in its own daemon thread: :meth:`update` never waits for a capture (it is
    called by the overlay state at 12 Hz). Thread-safe, never raises.

    ``grab()`` -> (BGR frame of the game window, (left, top) of that window on the screen) or None
    (default: :func:`default_grab`). ``icon_of(item_id)`` -> RGBA icon or None (default:
    :func:`treeaicoach.item_icons.item_rgba`). ``threaded=False`` (tests): the look runs inline.
    """

    def __init__(self, grab: Callable[[], Any] | None = None, icon_of: Callable[[int], Any] | None = None,
                 clock: Callable[[], float] = time.monotonic, threaded: bool = True) -> None:
        self._grab = grab
        self._icon_of = icon_of
        self._clock = clock
        self._threaded = threaded
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._want: tuple[int, ...] = ()
        self._at = -1e18
        self._last: ShopHighlight | None = None
        self._last_t = -1e18
        self.shop_open = False
        self.looks = 0

    def _icon(self, iid: int) -> Any:
        if self._icon_of is not None:
            return self._icon_of(iid)
        from treeaicoach import item_icons

        return item_icons.item_rgba(iid)

    def update(self, item_ids: Any, can_shop: bool, now: float | None = None) -> ShopHighlight | None:
        """The ring to draw now (None: nothing), for the first of ``item_ids`` seen in the open shop."""
        try:
            now = self._clock() if now is None else float(now)
            ids = tuple(int(i) for i in (item_ids if isinstance(item_ids, (list, tuple)) else (item_ids,))
                        if i is not None and int(i) > 0)
            with self._lock:
                self._want = ids if can_shop else ()
                if not self._want:
                    self._last, self.shop_open = None, False
                    return None
                due = now - self._at >= (LOOK_EVERY_S if self.shop_open or self._at < -1e17 else LOOK_CLOSED_S)
                if due:
                    self._at = now
                last, last_t = self._last, self._last_t
            if due:
                if self._threaded:
                    self._ensure_thread()
                    self._wake.set()
                else:
                    self._look(now)
                    with self._lock:
                        last, last_t = self._last, self._last_t
            fresh = last is not None and last.item_id in ids and now - last_t <= STALE_S
            return last if fresh else None
        except Exception:
            log.debug("shop watcher failed", exc_info=True)
            return None

    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="shop-watcher", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            self._wake.wait(30.0)
            self._wake.clear()
            with self._lock:
                want = self._want
            if want:
                self._look(self._clock())

    def _look(self, now: float) -> None:
        try:
            with self._lock:
                ids = self._want
            grab = self._grab or default_grab
            got = grab() if ids else None
            hl, is_open = None, False
            if got is not None:
                frame, origin = got
                shop = find_shop(frame)
                is_open = shop is not None
                for iid in ids if shop is not None else ():
                    icon = self._icon(iid)
                    spot = find_item(frame, shop, icon) if icon is not None else None
                    if spot is not None:
                        ox, oy = int(origin[0]), int(origin[1])
                        sx, sy, sw, sh = shop.rect
                        hl = ShopHighlight(iid, (spot.rect[0] + ox, spot.rect[1] + oy, spot.rect[2], spot.rect[3]),
                                           (sx + ox, sy + oy, sw, sh))
                        break
            with self._lock:
                self.looks += 1
                if self._want:
                    self._last, self._last_t, self.shop_open = hl, now, is_open
        except Exception:
            log.debug("shop look failed", exc_info=True)


_grabber_local = threading.local()


def default_grab() -> tuple[np.ndarray, tuple[int, int]] | None:
    """The game window (client area) as BGR + its top-left on the screen; None off Windows / no
    game / black frame (exclusive fullscreen). Our overlay windows are excluded from capture."""
    try:
        from treeaicoach import capture

        rect = capture.find_game_window()
        if rect is None:
            return None
        cap = getattr(_grabber_local, "cap", None)
        if cap is None:
            cap = _grabber_local.cap = capture.ScreenCapture()
        img = cap.grab(rect)
        if img is None or getattr(img, "size", 0) == 0 or capture.is_black_frame(img):
            return None
        img = np.asarray(img)
        if img.ndim == 3 and img.shape[2] == 4:
            img = np.ascontiguousarray(img[..., :3])
        return img, (int(rect.x), int(rect.y))
    except Exception:
        log.debug("shop grab failed", exc_info=True)
        return None


#: ring colour (BGR) and width relative to the icon side
RING_BGR = (80, 230, 120)
RING_W = 0.09


def ring_patch(rect: Any) -> tuple[np.ndarray, int, int] | None:
    """Premultiplied BGRA patch of a ring around the screen rect ``(x, y, w, h)`` of the icon, and
    where to show it. A plain rounded square ring + soft glow, nothing over the icon itself."""
    try:
        import cv2

        x, y, w, h = (int(v) for v in rect)
        if w < 6 or h < 6:
            return None
        lw = max(2, int(round(RING_W * w)))
        pad = 2 * lw + 2
        W, H = w + 2 * pad, h + 2 * pad
        a = np.zeros((H, W), np.uint8)
        x0, y0, x1, y1 = pad - lw, pad - lw, pad + w + lw - 1, pad + h + lw - 1
        cv2.rectangle(a, (x0, y0), (x1, y1), 255, lw, cv2.LINE_AA)
        glow = cv2.GaussianBlur(a, (0, 0), max(1.0, lw * 0.9))
        alpha = np.maximum(a.astype(np.float32), glow.astype(np.float32) * 0.55) / 255.0
        alpha[pad:pad + h, pad:pad + w] = 0.0                   # the icon stays untouched
        out = np.zeros((H, W, 4), np.float32)
        for c in range(3):
            out[..., c] = RING_BGR[c] * alpha
        out[..., 3] = 255.0 * alpha
        return np.clip(out + 0.5, 0, 255).astype(np.uint8), x - pad, y - pad
    except Exception:
        log.debug("ring patch failed", exc_info=True)
        return None


__all__ = ["GEOMETRY", "ShopFrame", "ItemSpot", "ShopHighlight", "ShopWatcher", "find_shop", "find_item",
           "default_grab", "ring_patch"]
