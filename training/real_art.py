"""Training samples on REAL minimap art (the user's own client), for the fast detector recipe.

The synthetic generator (:mod:`training.synth`) renders the minimap from the game textures.
Real 2026 clients look different (tower glyphs with plate numbers, hourglass camp icons,
gold diamond / green square markers, a reddish wall outline late game, our own overlay
rings and labels...), and that gap is what limits the detector on real screenshots.

This module closes the gap cheaply:

* :func:`build_backgrounds` turns a handful of real minimap crops (``tests/fixtures/real``
  + ``ground_truth.json``) into clean backgrounds: per-pixel **median** over the crops (the
  icons and the overlay move between screenshots, the map does not), plus every crop with
  its labelled champion icons painted over with that median (keeps the real minions,
  markers, labels, pings... as hard negatives).
* :func:`distractor_sprites` cuts the saturated glyphs of the median (towers with plate
  numbers, inhibitors, camp icons, markers) so they can be pasted anywhere as hard negatives.
* :func:`generate_real_sample` composites champion portraits with 2026-style rings (colours
  measured on the real crops), stacks, our overlay's rings / labels, camera lines, and the
  capture degradations of :mod:`training.synth` on a random background.

Labels follow :func:`training.synth.generate_sample` (``u, v, r, cls, cls_valid, vis``).
Only numpy / OpenCV (no torch).
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from treeaicoach import hershey  # noqa: E402
from treeaicoach import render as R  # noqa: E402
from training import synth  # noqa: E402

REAL_DIR = _REPO_ROOT / "tests" / "fixtures" / "real"
#: Real icon radius (outer ring) / minimap side, measured on the 2026 crops.
REAL_ICON_R = 0.045
#: Crops that use the 2026 art and are not covered by another window (backgrounds).
BACKGROUND_SHOTS = ("shot3", "shot4", "shot6", "shot7", "shot8", "shot9", "shot10")

# Ring colours measured on the real crops (RGB, low / high), desaturated by the capture.
ALLY_RING_RGB = ((88, 118, 138), (135, 160, 182))
ENEMY_RING_RGB = ((120, 50, 52), (210, 105, 105))
SELF_OUTLINE_RGB = ((60, 170, 230), (120, 220, 255))    # bright blue / teal self outline
OVERLAY_RGB = {"ally": (80, 140, 230), "enemy": (225, 70, 70)}
LABELS = ("TOP", "JGL", "MID", "ADC", "SUP")


@dataclass
class RealImage:
    name: str
    img: np.ndarray                    # BGR
    gts: list[tuple[float, float, str]]  # (u, v, team)


@dataclass
class _Sprite:                          # what synth._labels needs
    u: float
    v: float
    r: float
    label_class: str | None
    grey: bool = False


def load_real(directory: Path = REAL_DIR, names: Sequence[str] | None = None) -> list[RealImage]:
    """Real crops + ground truth (``ground_truth.json``); [] when missing."""
    try:
        gt = json.loads((directory / "ground_truth.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for name, rec in gt.get("images", {}).items():
        if names is not None and name not in names:
            continue
        img = cv2.imread(str(directory / rec.get("file", name + ".png")), cv2.IMREAD_COLOR)
        if img is None:
            continue
        gts = [(float(c["u"]), float(c["v"]), str(c.get("team", "ally")))
               for c in rec.get("champions", [])]
        out.append(RealImage(name, img, gts))
    return out


def _disc_mask(shape: tuple[int, int], gts, r_px: float) -> np.ndarray:
    m = np.zeros(shape, np.uint8)
    h, w = shape
    for u, v, _ in gts:
        cv2.circle(m, (int(round(u * w)), int(round(v * h))), int(math.ceil(r_px)), 255, -1,
                   cv2.LINE_AA)
    return m


def build_backgrounds(reals: Sequence[RealImage], size: int = 300) -> list[np.ndarray]:
    """Median background + every crop with its icons painted over (all ``size`` px)."""
    ims = [cv2.resize(r.img, (size, size), interpolation=cv2.INTER_AREA) for r in reals]
    if not ims:
        return []
    med = np.median(np.stack(ims).astype(np.float32), axis=0).astype(np.uint8) \
        if len(ims) >= 3 else ims[0].copy()
    bgs = [med]
    r_px = REAL_ICON_R * size * 1.3
    for r, im in zip(reals, ims):
        m = _disc_mask(im.shape[:2], r.gts, r_px)
        a = cv2.GaussianBlur(m, (0, 0), 1.2).astype(np.float32)[:, :, None] / 255.0
        bgs.append((im * (1 - a) + med * a).astype(np.uint8))
    return bgs


def distractor_sprites(bg: np.ndarray, max_n: int = 40) -> list[np.ndarray]:
    """Saturated glyphs of a clean background as BGRA sprites (towers, camps, markers)."""
    hsv = cv2.cvtColor(bg, cv2.COLOR_BGR2HSV)
    m = ((hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 90)).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m)
    out = []
    side = bg.shape[0]
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < 12 or w > 0.14 * side or h > 0.14 * side:
            continue
        p = 3
        x0, y0 = max(0, x - p), max(0, y - p)
        x1, y1 = min(side, x + w + p), min(side, y + h + p)
        comp = (lab[y0:y1, x0:x1] == i).astype(np.uint8) * 255
        alpha = cv2.GaussianBlur(cv2.dilate(comp, np.ones((5, 5), np.uint8)), (0, 0), 0.8)
        out.append(np.dstack([bg[y0:y1, x0:x1], alpha]))
        if len(out) >= max_n:
            break
    return out


def _paste(dst: np.ndarray, spr: np.ndarray, cx: float, cy: float) -> None:
    h, w = spr.shape[:2]
    x0, y0 = int(round(cx - w / 2)), int(round(cy - h / 2))
    H, W = dst.shape[:2]
    sx0, sy0 = max(0, -x0), max(0, -y0)
    dx0, dy0 = max(0, x0), max(0, y0)
    dx1, dy1 = min(W, x0 + w), min(H, y0 + h)
    if dx1 <= dx0 or dy1 <= dy0:
        return
    s = spr[sy0:sy0 + dy1 - dy0, sx0:sx0 + dx1 - dx0]
    a = s[:, :, 3:4].astype(np.float32) / 255.0
    roi = dst[dy0:dy1, dx0:dx1]
    roi[:] = (roi * (1 - a) + s[:, :, :3] * a).astype(np.uint8)


def _lerp_bgr(rng: np.random.Generator, lo_hi) -> tuple[int, int, int]:
    lo, hi = (np.asarray(c, np.float32) for c in lo_hi)
    c = lo + (hi - lo) * rng.random() + rng.normal(0, 5, 3)
    r, g, b = np.clip(c, 0, 255).astype(int)
    return int(b), int(g), int(r)


def _text(img: np.ndarray, rng: np.random.Generator, x: float, y: float, scale: float) -> None:
    s = str(LABELS[int(rng.integers(len(LABELS)))])
    if rng.random() < 0.6:
        s += f" {int(rng.integers(1, 60))} s"
    if rng.random() < 0.15:
        s = f"{int(rng.integers(0, 40))}:{int(rng.integers(0, 60)):02d}"
    # (OpenCV-4 Hershey text under every OpenCV version: cv2 5's putText draws other glyphs)
    th = max(1, int(round(scale * 2.2)))
    org = (int(x), int(y))
    hershey.put_text(img, s, org, scale, (25, 25, 25), th + 2, cv2.LINE_AA)
    col = int(rng.integers(200, 256))
    hershey.put_text(img, s, org, scale, (col, col, col), th, cv2.LINE_AA)


class RealArt:
    """Backgrounds, distractors and portraits for :meth:`sample` (one per process)."""

    def __init__(self, reals: Sequence[RealImage] | None = None) -> None:
        if reals is None:
            reals = load_real(names=BACKGROUND_SHOTS)
        self.backgrounds = build_backgrounds(reals)
        self.sprites = distractor_sprites(self.backgrounds[0]) if self.backgrounds else []
        self.assets = synth.get_assets()

    def __bool__(self) -> bool:
        return bool(self.backgrounds)

    def _background(self, rng: np.random.Generator, native: int) -> np.ndarray:
        bg = self.backgrounds[int(rng.integers(len(self.backgrounds)))]
        if rng.random() < 0.35:
            bg = bg[::-1, ::-1]                       # 180 deg: the other side's view
        if rng.random() < 0.25:
            bg = bg.transpose(1, 0, 2)                # top lane <-> itself, map ~symmetric
        # small zoom / shift (crop misalignment), then resize to the native capture size
        n = bg.shape[0]
        z = rng.uniform(0.9, 1.06)
        c = n / 2 + rng.uniform(-0.03, 0.03, 2) * n
        M = cv2.getRotationMatrix2D((float(c[0]), float(c[1])), 0.0, 1.0 / z)
        M[:, 2] += (n / 2 - c)
        out = cv2.warpAffine(np.ascontiguousarray(bg), M, (n, n), flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_REFLECT)
        return cv2.resize(out, (native, native), interpolation=cv2.INTER_AREA
                          if native < n else cv2.INTER_LINEAR)

    def _portrait(self, rng: np.random.Generator) -> np.ndarray | None:
        A = self.assets
        if not A.icon_paths:
            return None
        return A.portrait(int(rng.integers(len(A.icon_paths))))

    def render(self, rng: np.random.Generator, native: int) -> tuple[np.ndarray, list[_Sprite]]:
        img = self._background(rng, native).copy()
        n = native
        # hard negatives first (under the icons): real glyphs pasted at random places
        for _ in range(int(rng.integers(0, 7)) if self.sprites else 0):
            spr = self.sprites[int(rng.integers(len(self.sprites)))]
            k = n / 300.0 * rng.uniform(0.85, 1.2)
            spr = cv2.resize(spr, None, fx=k, fy=k, interpolation=cv2.INTER_LINEAR)
            _paste(img, spr, rng.uniform(0.05, 0.95) * n, rng.uniform(0.05, 0.95) * n)
        r_base = rng.uniform(0.040, 0.051)
        count = int(rng.choice([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                               p=[.03, .05, .08, .12, .14, .14, .13, .11, .09, .06, .05]))
        sprites: list[_Sprite] = []
        centres: list[tuple[float, float]] = []
        for i in range(count):
            r = r_base * rng.uniform(0.96, 1.04)
            if centres and rng.random() < 0.35:       # stacks / fights
                cu, cv_ = centres[int(rng.integers(len(centres)))]
                a = rng.uniform(0, 2 * math.pi)
                d = rng.uniform(0.7, 2.0) * r
                u, v = cu + d * math.cos(a), cv_ + d * math.sin(a)
            else:
                u, v = rng.uniform(0.03, 0.97, 2)
            u, v = float(np.clip(u, 0.01, 0.99)), float(np.clip(v, 0.01, 0.99))
            team = "enemy" if rng.random() < 0.45 else "ally"
            ring = _lerp_bgr(rng, ENEMY_RING_RGB if team == "enemy" else ALLY_RING_RGB)
            R.draw_champion_icon(img, u * n, v * n, r * n, self._portrait(rng), ring,
                                 ring_frac=rng.uniform(0.09, 0.16),
                                 inner_line_bgr=None if rng.random() < 0.5 else R.INNER_LINE_BGR)
            if team == "ally" and i == 0 and rng.random() < 0.4:      # self outline
                cv2.circle(img, (int(u * n * 16), int(v * n * 16)), int(r * n * 1.08 * 16),
                           _lerp_bgr(rng, SELF_OUTLINE_RGB), max(1, int(round(n / 160))),
                           cv2.LINE_AA, 4)
            sprites.append(_Sprite(u, v, r, team))
            centres.append((u, v))
        self._overlay(img, rng, sprites, n)
        return img, sprites

    def _overlay(self, img: np.ndarray, rng: np.random.Generator, sprites: list[_Sprite],
                 n: int) -> None:
        """Our own overlay (rings, labels), camera rectangle, ping-like circles."""
        sh = 16
        for _ in range(int(rng.integers(0, 5))):
            if sprites and rng.random() < 0.6:
                s = sprites[int(rng.integers(len(sprites)))]
                u, v = s.u + rng.normal(0, 0.01), s.v + rng.normal(0, 0.01)
            else:
                u, v = rng.uniform(0.05, 0.95, 2)
            rr = rng.uniform(1.4, 4.0) * 0.045
            col = OVERLAY_RGB["enemy" if rng.random() < 0.5 else "ally"][::-1]
            col = tuple(int(c) for c in np.clip(np.asarray(col) * rng.uniform(0.5, 1.0), 0, 255))
            if rng.random() < 0.4:                    # dashed
                for a0 in np.arange(0, 360, 24):
                    cv2.ellipse(img, (int(u * n * sh), int(v * n * sh)),
                                (int(rr * n * sh), int(rr * n * sh)), 0, a0, a0 + 12, col, 1,
                                cv2.LINE_AA, 4)
            else:
                cv2.circle(img, (int(u * n * sh), int(v * n * sh)), int(rr * n * sh), col,
                           int(rng.integers(1, 3)), cv2.LINE_AA, 4)
        for _ in range(int(rng.integers(0, 5))):
            if sprites and rng.random() < 0.6:
                s = sprites[int(rng.integers(len(sprites)))]
                x, y = (s.u + rng.uniform(-0.12, 0.02)) * n, (s.v + rng.uniform(-0.05, 0.08)) * n
            else:
                x, y = rng.uniform(0.0, 0.85) * n, rng.uniform(0.05, 0.98) * n
            _text(img, rng, x, y, n / 300.0 * rng.uniform(0.33, 0.45))
        if rng.random() < 0.5:                        # camera rectangle
            u0, v0 = rng.uniform(-0.1, 0.85, 2)
            w, h = R.CAMERA_SIZE
            g = int(rng.integers(200, 256))
            cv2.rectangle(img, (int(u0 * n), int(v0 * n)), (int((u0 + w) * n), int((v0 + h) * n)),
                          (g, g, g), max(1, int(round(n / 200))))

    def sample(self, rng: np.random.Generator, size: int = 256,
               cfg: synth.SynthConfig | None = None) -> tuple[np.ndarray, list[dict]]:
        cfg = cfg or synth.DEFAULT_CONFIG
        native = int(np.clip(rng.normal(300, 50), 180, 440))
        img, sprites = self.render(rng, native)
        out, crop = synth._degrade(img, rng, size, cfg, self.assets)
        return out, synth._labels(sprites, [True] * len(sprites), native, crop)


_ART: RealArt | None = None


def generate_real_sample(rng: np.random.Generator, size: int = 256) -> tuple[np.ndarray, list[dict]]:
    """One real-art sample (``synth.generate_sample`` contract); falls back to synthetic."""
    global _ART
    if _ART is None:
        _ART = RealArt()
    if not _ART:
        return synth.generate_sample(rng, size)
    return _ART.sample(rng, size)
