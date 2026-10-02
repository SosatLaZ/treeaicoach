"""Bottom-centre ability bar reader: MY OWN spells, summoners, items and trinket, from the screen.

Riot policy: this reads only the local player's own HUD (what the player already sees on his
own screen), never anything about the enemies, and never sends any input to the game.

The LoL HUD draws, right of my portrait (:mod:`treeaicoach.hud_reader`): the passive, the four
ability squares (Q W E R), the two summoner squares (D F), then the 2 x 3 item grid and the
trinket square. Their geometry, measured on real 2000 x 1125 captures (HUD scale 100 %), is
:data:`SLOTS` (offsets from the portrait centre, in pixels at 1125 px height). The real HUD
scale / resolution changes it by one factor, so :meth:`AbilityBarReader.calibrate` fits
``(scale, dx, dy)`` once per window size: every slot is a square frame, the fit maximises the
weakest side's border gradient of each square (a box needs its four sides), coarse on a blurred
gradient, then sharp (~60-120 ms, once).

Per read (a small patch of the bar, :meth:`AbilityBarReader.roi`, < 1 ms) each slot gives a
:class:`SlotState`:

* ``cooldown`` / ``cd_frac``: the game paints the part of the square still on cooldown with a
  flat, saturated blue (HSV hue 99-108, S >= 160, V 75-140, measured on 9 real cooldowns) and
  writes the seconds in white; the fraction of that blue is the remaining fraction of the sweep.
  Real icons that are blue (Garen W, items) have another hue / texture: no confusion measured.
* ``castable``: the game draws a gold frame around a castable ability / an item active ready
  (grey when not learned, no mana, dead, on cooldown). None when the frame is not readable.
* ``value``: mean brightness of the icon (greyed when not castable / dead).

The trinket also gives ``charges`` (the small white digit in its bottom-right corner, 1 or 2;
0 when the recharge countdown is shown in its centre; None when unreadable).

Never raises: every public method returns None / False on any problem.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from treeaicoach.hud_reader import _as_bgr, locate_portrait

log = logging.getLogger(__name__)

REF_H = 1125.0
#: slot -> (dx, dy, size) from the portrait centre, px at REF_H (real 2000 x 1125 captures, HUD 100 %)
SLOTS: dict[str, tuple[float, float, float]] = {
    "P": (78.0, -21.0, 32.0),
    "Q": (121.0, -16.0, 42.0), "W": (170.5, -16.0, 42.0), "E": (219.0, -16.0, 42.0), "R": (268.5, -16.0, 42.0),
    "D": (318.0, -21.0, 32.0), "F": (356.4, -21.0, 32.0),
    "1": (412.0, -23.0, 29.0), "2": (447.0, -23.0, 29.0), "3": (483.4, -23.0, 29.0), "T": (518.5, -23.0, 29.0),
    "4": (412.0, 11.5, 29.0), "5": (447.0, 11.5, 29.0), "6": (483.4, 11.5, 29.0),
}
ABILITIES = ("Q", "W", "E", "R")
SUMMONERS = ("D", "F")
ITEMS = ("1", "2", "3", "4", "5", "6")
TRINKET = "T"
#: Live Client item slot (0..5) -> HUD cell
ITEM_CELL = {0: "1", 1: "2", 2: "3", 3: "4", 4: "5", 5: "6", 6: "T"}
FIT_KEYS = ("Q", "W", "E", "R", "D", "F", "1", "2", "3", "T", "4", "5", "6")
SCALE_RANGE = (0.70, 1.15)      # HUD scale x resolution, relative to REF_H (real captures: 0.87-0.99)
SEARCH_PX = 18                  # portrait centre error searched (px at REF_H)
MIN_FIT = 25.0                  # minimum fit score (mean weakest-side gradient) to trust the layout
VALID_FRAC = 0.40               # a read whose border score falls below this fraction of the calibration's: HUD hidden
# cooldown blue (OpenCV HSV, H 0-180)
CD_H = (99, 108)
CD_S = 160
CD_V = (75, 140)
CD_MIN = 0.05                   # fraction of the icon: on cooldown
# castable frame: dull gold, bright (a frame that is not castable is dark grey: V < 70)
GOLD_H = (12, 42)
GOLD_S = (25, 120)
GOLD_V = 95
GOLD_MIN = 0.35                 # fraction of the frame positions that are gold: castable
FRAME_DEPTHS = (0.44, 0.47, 0.50, 0.53)   # frame samples, distance from the centre / slot size
WHITE_S = 80                    # digits (cooldown seconds, trinket charges): white
WHITE_V = 170


@dataclass(frozen=True)
class SlotState:
    key: str
    cooldown: bool
    cd_frac: float              # 0..1 share of the icon under the cooldown sweep
    castable: bool | None       # gold frame (None: frame unreadable)
    value: float                # mean brightness 0..255
    charges: int | None = None  # trinket only


@dataclass
class BarRead:
    slots: dict[str, SlotState] = field(default_factory=dict)
    valid: bool = True
    ms: float = 0.0

    def get(self, key: str) -> SlotState | None:
        return self.slots.get(key)

    def on_cooldown(self, key: str) -> bool | None:
        s = self.slots.get(key)
        return None if s is None or not self.valid else s.cooldown

    def ready(self, key: str) -> bool | None:
        """Castable now (gold frame, no cooldown): True / False, None when unknown."""
        s = self.slots.get(key)
        if s is None or not self.valid:
            return None
        if s.cooldown:
            return False
        return s.castable

    @property
    def trinket_charges(self) -> int | None:
        s = self.slots.get(TRINKET)
        return None if s is None or not self.valid else s.charges


@dataclass(frozen=True)
class Layout:
    cx: float                   # portrait centre + fitted offset
    cy: float
    scale: float                # px per reference px
    fit: float                  # fit score

    def box(self, key: str) -> tuple[float, float, float]:
        dx, dy, size = SLOTS[key]
        return self.cx + self.scale * dx, self.cy + self.scale * dy, self.scale * size


def _border_points(n: int) -> np.ndarray:
    """``(u, v, axis)`` samples on the border of a unit square centred on 0 (axis 0: vertical
    side -> horizontal gradient)."""
    t = np.linspace(-0.38, 0.38, n)
    rows = [(-0.5, a, 0) for a in t] + [(0.5, a, 0) for a in t] + [(a, -0.5, 1) for a in t] + [(a, 0.5, 1) for a in t]
    return np.array(rows, dtype=np.float32)


_BP_COARSE = _border_points(4)
_BP_FINE = _border_points(8)


def _gradients(gray: np.ndarray, blur: float = 0.0) -> np.ndarray:
    gx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
    gy = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
    if blur > 0:
        gx = cv2.GaussianBlur(gx, (0, 0), blur)
        gy = cv2.GaussianBlur(gy, (0, 0), blur)
    return np.stack([gx, gy])


def _fit_scores(G: np.ndarray, bp: np.ndarray, base: tuple[float, float], s: float,
                oxs: np.ndarray, oys: np.ndarray, keys: tuple[str, ...] = FIT_KEYS) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Score of every offset ``(ox, oy)`` at scale ``s``: mean over the slots of the weakest side's
    median border gradient."""
    h, w = G.shape[1:]
    off = np.array([SLOTS[k][:2] for k in keys], dtype=np.float32)
    sz = np.array([SLOTS[k][2] for k in keys], dtype=np.float32)
    bx = base[0] + s * (off[:, 0][:, None] + sz[:, None] * bp[None, :, 0])
    by = base[1] + s * (off[:, 1][:, None] + sz[:, None] * bp[None, :, 1])
    OX, OY = np.meshgrid(oxs, oys, indexing="ij")
    OX, OY = OX.reshape(-1), OY.reshape(-1)
    xi = np.clip(np.rint(bx[None] + OX[:, None, None]).astype(np.int32), 0, w - 1)
    yi = np.clip(np.rint(by[None] + OY[:, None, None]).astype(np.int32), 0, h - 1)
    ax = np.broadcast_to(bp[:, 2].astype(np.int32), xi.shape)
    v = G[ax, yi, xi].reshape(len(OX), len(keys), 4, -1)
    sc = np.median(v, axis=3).min(axis=2).mean(axis=1)
    return sc, OX, OY


def fit_layout(screen_bgr: Any, portrait: tuple[int, int, int] | None = None) -> Layout | None:
    """Fit the bar geometry on a full-window capture. None if the HUD is not found. Never raises."""
    try:
        img = _as_bgr(screen_bgr)
        if img is None:
            return None
        H, W = img.shape[:2]
        loc = portrait or locate_portrait(img)
        if loc is None:
            return None
        cx, cy, _r = loc
        s0 = H / REF_H
        x0, x1 = max(0, int(cx)), min(W, int(cx + 580 * s0))
        y0, y1 = max(0, int(cy - 70 * s0)), min(H, int(cy + 45 * s0))
        if x1 - x0 < 50 or y1 - y0 < 30:
            return None
        gray = cv2.cvtColor(np.ascontiguousarray(img[y0:y1, x0:x1]), cv2.COLOR_BGR2GRAY).astype(np.float32)
        base = (cx - x0, cy - y0)
        Gb = _gradients(gray, blur=2.0 * s0)
        R = max(4, int(round(SEARCH_PX * s0)))
        step = max(2, int(round(3 * s0)))
        cands: list[tuple[float, float, int, int]] = []
        for s in np.arange(SCALE_RANGE[0] * s0, SCALE_RANGE[1] * s0, 0.008 * s0):
            sc, OX, OY = _fit_scores(Gb, _BP_COARSE, base, float(s), np.arange(-R, R + 1, step), np.arange(-R, R + 1, step))
            i = int(np.argmax(sc))
            cands.append((float(sc[i]), float(s), int(OX[i]), int(OY[i])))
        cands.sort(reverse=True)
        G = _gradients(gray)
        best: tuple[float, float, int, int] | None = None
        for _sc, s, ox, oy in cands[:4]:
            for s2 in np.arange(s - 0.008 * s0, s + 0.0081 * s0, 0.002 * s0):
                sc, OX, OY = _fit_scores(G, _BP_FINE, base, float(s2), np.arange(ox - step, ox + step + 1),
                                         np.arange(oy - step, oy + step + 1))
                i = int(np.argmax(sc))
                if best is None or sc[i] > best[0]:
                    best = (float(sc[i]), float(s2), int(OX[i]), int(OY[i]))
        if best is None or best[0] < MIN_FIT:
            return None
        return Layout(cx=cx + best[2], cy=cy + best[3], scale=best[1], fit=best[0])
    except Exception:
        log.debug("ability bar fit failed", exc_info=True)
        return None


class AbilityBarReader:
    """Calibrate once per window size (:meth:`calibrate`), then read the bar patch (:meth:`roi`)
    cheaply (:meth:`read_patch`, one gather + one colour conversion of ~4 k pixels). Never raises."""

    def __init__(self) -> None:
        self._lay: Layout | None = None
        self._size: tuple[int, int] | None = None
        self._roi: tuple[int, int, int, int] | None = None
        self._g: dict[str, Any] | None = None
        self._ref_border: float | None = None
        self.last_ms = 0.0

    @property
    def layout(self) -> Layout | None:
        return self._lay

    def reset(self) -> None:
        self._lay = self._size = self._roi = self._g = self._ref_border = None

    def calibrate(self, screen_bgr: Any, portrait: tuple[int, int, int] | None = None) -> bool:
        """Fit the layout on a full-window capture (once per window size). Never raises."""
        try:
            img = _as_bgr(screen_bgr)
            if img is None:
                return False
            lay = fit_layout(img, portrait)
            if lay is None:
                return False
            H, W = img.shape[:2]
            x0 = int(np.floor(lay.box("Q")[0] - lay.scale * 30))
            x1 = int(np.ceil(lay.box("T")[0] + lay.scale * 20))
            y0 = int(np.floor(lay.cy + lay.scale * (-23 - 20)))
            y1 = int(np.ceil(lay.cy + lay.scale * (11.5 + 20)))
            x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
            if x1 - x0 < 20 or y1 - y0 < 10:
                return False
            roi = (x0, y0, x1 - x0, y1 - y0)
            g = self._geometry(lay, roi)
            ref = self._border(np.ascontiguousarray(img[y0:y1, x0:x1]), g)
            # one assignment: a read running on another thread never mixes two calibrations
            self._lay, self._size, self._roi, self._g, self._ref_border = lay, (H, W), roi, g, ref
            return True
        except Exception:
            log.debug("ability bar calibration failed", exc_info=True)
            return False

    def roi(self) -> tuple[int, int, int, int] | None:
        """``(x, y, w, h)`` window pixels of the bar patch, None before calibration."""
        return self._roi

    def read(self, screen_bgr: Any) -> BarRead | None:
        """Full-window capture: calibrate if needed, then read. Never raises."""
        try:
            img = _as_bgr(screen_bgr)
            if img is None:
                return None
            if self._lay is None or self._size != img.shape[:2]:
                if not self.calibrate(img):
                    return None
            x, y, w, h = self._roi  # type: ignore[misc]
            return self.read_patch(img[y:y + h, x:x + w])
        except Exception:
            log.debug("ability bar read failed", exc_info=True)
            return None

    def read_patch(self, patch_bgr: Any) -> BarRead | None:
        """Read the patch grabbed at :meth:`roi` (calibrated reader). Never raises."""
        t0 = time.perf_counter()
        try:
            img = _as_bgr(patch_bgr)
            if img is None or self._g is None or self._roi is None:
                return None
            if img.shape[0] != self._roi[3] or img.shape[1] != self._roi[2]:
                return None
            out = self._read(img)
            out.ms = 1000 * (time.perf_counter() - t0)
            return out
        except Exception:
            log.debug("ability bar patch read failed", exc_info=True)
            return None
        finally:
            self.last_ms = 1000 * (time.perf_counter() - t0)

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _geometry(lay: Layout, roi: tuple[int, int, int, int]) -> dict[str, Any]:
        """Flat pixel indices (patch) of every sample, computed once per calibration."""
        x0, y0, w, h = roi
        keys = [k for k in SLOTS if k != "P"]
        inners: list[np.ndarray] = []
        frames: list[np.ndarray] = []

        def flat(xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
            return (np.clip(np.rint(ys), 0, h - 1).astype(np.int64) * w
                    + np.clip(np.rint(xs), 0, w - 1).astype(np.int64))

        for k in keys:
            cx, cy, size = lay.box(k)
            cx, cy = cx - x0, cy - y0
            st = max(1.0, size / 20.0)                    # ~16 x 16 interior samples whatever the scale
            gg = np.arange(-0.40 * size, 0.40 * size + 1e-6, st)
            GX, GY = np.meshgrid(cx + gg, cy + gg)
            inners.append(flat(GX.ravel(), GY.ravel()))
            fr = []
            for ai in np.linspace(-0.38, 0.38, 9):        # (position, depth, side) order
                for d in FRAME_DEPTHS:
                    fr += [(cx + ai * size, cy - d * size), (cx + ai * size, cy + d * size),
                           (cx - d * size, cy + ai * size), (cx + d * size, cy + ai * size)]
            F = np.array(fr)
            frames.append(flat(F[:, 0], F[:, 1]))
        lens = np.array([len(x) for x in inners])
        starts = np.concatenate([[0], np.cumsum(lens)[:-1]])
        n_in = int(lens.sum())
        n_fr = sum(len(x) for x in frames)
        idx: list[np.ndarray] = inners + frames
        n = n_in + n_fr
        # trinket digit zones: bottom-right corner (charges), centre (recharge countdown)
        cx, cy, size = lay.box(TRINKET)
        cx, cy = cx - x0, cy - y0
        st = max(1, int(round(size / 30.0)))
        zones = {}
        for name, (ax0, ay0, ax1, ay1) in (("corner", (0.06, 0.06, 0.60, 0.60)), ("centre", (-0.28, -0.28, 0.28, 0.28))):
            xs = np.arange(cx + ax0 * size, cx + ax1 * size, st)
            ys = np.arange(cy + ay0 * size, cy + ay1 * size, st)
            GX, GY = np.meshgrid(xs, ys)
            zi = flat(GX.ravel(), GY.ravel())
            zones[name] = (n, n + len(zi), GX.shape)
            n += len(zi)
            idx.append(zi)
        # border ring (validity): the 4 ability + 2 summoner squares
        rx, ry, rax = [], [], []
        for k in ("Q", "W", "E", "R", "D", "F"):
            cx, cy, size = lay.box(k)
            rx.append(np.clip(np.rint(cx - x0 + size * _BP_FINE[:, 0]), 1, w - 2).astype(np.int64))
            ry.append(np.clip(np.rint(cy - y0 + size * _BP_FINE[:, 1]), 1, h - 2).astype(np.int64))
            rax.append(_BP_FINE[:, 2].astype(bool))
        RX, RY, RA = np.concatenate(rx), np.concatenate(ry), np.concatenate(rax)
        d = np.where(RA, w, 1)                          # neighbour step across the side
        ring = (RY * w + RX - d, RY * w + RX + d)
        return {"idx": np.concatenate(idx), "keys": keys, "starts": starts, "lens": lens, "n_in": n_in, "n_fr": n_fr,
                "zones": zones, "ring": ring, "w": w, "h": h}

    def _border(self, img: np.ndarray, g: dict[str, Any] | None = None) -> float:
        """Mean weakest-side contrast of the ability / summoner squares (HUD visible?)."""
        g = g if g is not None else self._g
        if g is None:
            return 0.0
        flat = img.reshape(-1, 3)
        a, b = g["ring"]
        wts = np.array([0.114, 0.587, 0.299], dtype=np.float32)      # BGR -> grey
        v = np.abs(flat[a].astype(np.float32) @ wts - flat[b].astype(np.float32) @ wts).reshape(6, 4, -1)
        return float(np.median(v, axis=2).min(axis=1).mean())

    def _read(self, img: np.ndarray) -> BarRead:
        g, ref = self._g, self._ref_border
        if g is None or img.shape[0] != g["h"] or img.shape[1] != g["w"]:
            return BarRead(valid=False)
        px = img.reshape(-1, 3)[g["idx"]].reshape(-1, 1, 3)
        hsv = cv2.cvtColor(np.ascontiguousarray(px), cv2.COLOR_BGR2HSV).reshape(-1, 3)
        Hh, S, V = hsv[:, 0], hsv[:, 1], hsv[:, 2]
        cd_all = (Hh >= CD_H[0]) & (Hh <= CD_H[1]) & (S >= CD_S) & (V >= CD_V[0]) & (V <= CD_V[1])
        n_in, n_fr, keys = g["n_in"], g["n_fr"], g["keys"]
        cd = np.add.reduceat(cd_all[:n_in].astype(np.int32), g["starts"]) / g["lens"]
        val = np.add.reduceat(V[:n_in].astype(np.int32), g["starts"]) / g["lens"]
        fh, fs, fv = Hh[n_in:n_in + n_fr], S[n_in:n_in + n_fr], V[n_in:n_in + n_fr]
        gold = (fh >= GOLD_H[0]) & (fh <= GOLD_H[1]) & (fs >= GOLD_S[0]) & (fs <= GOLD_S[1]) & (fv >= GOLD_V)
        gold_frac = gold.reshape(len(keys), 9, len(FRAME_DEPTHS), 4).any(axis=2).mean(axis=(1, 2))
        slots: dict[str, SlotState] = {}
        for i, k in enumerate(keys):
            frac = float(cd[i])
            on_cd = frac >= CD_MIN
            slots[k] = SlotState(k, on_cd, round(frac, 3), bool(gold_frac[i] >= GOLD_MIN and not on_cd), float(val[i]))
        t = slots.get(TRINKET)
        if t is not None:
            white = (S <= WHITE_S) & (V >= WHITE_V)
            slots[TRINKET] = SlotState(t.key, t.cooldown, t.cd_frac, t.castable, t.value,
                                       charges=self._charges(white, g["zones"], t.cooldown))
        valid = True
        if ref:
            valid = self._border(img, g) >= VALID_FRAC * ref
        return BarRead(slots=slots, valid=valid)

    @staticmethod
    def _charges(white: np.ndarray, zones: dict[str, Any], cooling: bool = False) -> int | None:
        """Trinket charges from the white digit of its bottom-right corner (``1`` narrow, ``2``
        wide); 0 when the centre shows a recharge countdown and no corner digit; None if unsure."""
        a, b, shape = zones["corner"]
        corner = white[a:b].reshape(shape)
        ca, cb, _cs = zones["centre"]
        centre = white[ca:cb]
        if corner.sum() < max(3, 0.03 * corner.size):
            return 0 if (cooling or centre.mean() >= 0.03) else None
        ys, xs = np.nonzero(corner)
        bw, bh = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
        return 2 if bw >= 0.55 * bh else 1


__all__ = ["AbilityBarReader", "BarRead", "SlotState", "Layout", "fit_layout", "SLOTS", "ABILITIES",
           "SUMMONERS", "ITEMS", "TRINKET", "ITEM_CELL"]
