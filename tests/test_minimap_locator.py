"""Tests for treeaicoach.minimap_locator on synthetic game screenshots.

The screenshot generator below builds a random "game-like" frame (smooth colour fields,
blobs, noise, a dark ability bar, the minimap frame, ally portraits, buttons) and pastes a
minimap rendered by :mod:`treeaicoach.render` (random texture variant, fog of war with vision
circles, champions, minions, wards, pings, camera rectangle) at a random size / margin /
side, then optionally blurs and JPEG-compresses the whole frame.
"""

from __future__ import annotations

import math
import os
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from treeaicoach.capture import Rect
from treeaicoach.minimap_locator import (
    LOCATE_MIN_SCORE,
    SIZE_RANGE,
    MinimapLocation,
    MinimapLocator,
    fallback_rect,
)
from treeaicoach.render import CAMPS, STRUCTURES, ChampionSprite, MinimapRenderer, Scene, load_rgba

ASSETS = Path(__file__).resolve().parents[1] / "treeaicoach" / "assets"
RESOLUTIONS = [(1280, 720), (1920, 1080), (2560, 1440), (3840, 2160), (1600, 900), (2560, 1080)]
DEBUG_DIR = os.environ.get("TREEAICOACH_DEBUG_DIR")  # annotated PNGs written here if set

pytestmark = pytest.mark.skipif(
    not (ASSETS / "minimap" / "2dlevelminimap_base_baron1.png").is_file(),
    reason="minimap textures not available")


# ======================================================================================
# Synthetic screenshot generator
# ======================================================================================

_RENDERER: MinimapRenderer | None = None
_ICONS: list[np.ndarray] = []


def _renderer() -> MinimapRenderer:
    global _RENDERER
    if _RENDERER is None:
        _RENDERER = MinimapRenderer(ASSETS)
        files = sorted((ASSETS / "icons" / "champions").glob("*.png"))
        rng = np.random.default_rng(7)
        for p in rng.choice(files, size=min(24, len(files)), replace=False) if files else []:
            try:
                _ICONS.append(load_rgba(p))
            except Exception:
                pass
    return _RENDERER


def _smooth_field(rng: np.random.Generator, h: int, w: int, cells: int, amp: float,
                  channels: int = 3) -> np.ndarray:
    gh, gw = max(2, cells), max(2, int(cells * w / max(1, h)))
    g = rng.normal(0.0, amp, (gh, gw, channels)).astype(np.float32)
    return cv2.resize(g, (w, h), interpolation=cv2.INTER_CUBIC).reshape(h, w, channels)


def game_background(rng: np.random.Generator, W: int, H: int) -> np.ndarray:
    """Random game-like scene (terrain colours, blobs, paths, units, noise), BGR uint8."""
    lh = 360
    lw = max(8, int(round(lh * W / H)))
    base = rng.uniform([15, 30, 20], [90, 120, 110]).astype(np.float32)
    img = np.empty((lh, lw, 3), np.float32)
    img[:] = base
    img += _smooth_field(rng, lh, lw, int(rng.integers(3, 7)), 30.0)
    img += _smooth_field(rng, lh, lw, int(rng.integers(12, 40)), 14.0)
    yy, xx = np.mgrid[0:lh, 0:lw].astype(np.float32)
    ang = rng.uniform(0, 2 * math.pi)
    img += (np.cos(ang) * xx / lw + np.sin(ang) * yy / lh)[..., None] * rng.uniform(-40, 40)
    for _ in range(int(rng.integers(8, 40))):  # blobs: rocks, grass, water, spell effects
        c = (int(rng.integers(0, lw)), int(rng.integers(0, lh)))
        ax = (int(rng.integers(3, lh // 3)), int(rng.integers(3, lh // 3)))
        col = rng.uniform(20, 200, 3).tolist() if rng.random() < 0.1 else \
            rng.uniform([10, 25, 10], [100, 130, 110]).tolist()
        cv2.ellipse(img, c, ax, float(rng.uniform(0, 180)), 0, 360, col, -1, cv2.LINE_AA)
    img = cv2.GaussianBlur(img, (0, 0), float(rng.uniform(0.6, 3.0)))
    for _ in range(int(rng.integers(0, 6))):  # paths, walls
        pts = rng.integers(0, [lw, lh], (int(rng.integers(2, 6)), 2)).astype(np.int32)
        cv2.polylines(img, [pts], False, rng.uniform(40, 200, 3).tolist(),
                      int(rng.integers(1, 8)), cv2.LINE_AA)
    for _ in range(int(rng.integers(0, 12))):  # champions / minions / health bars
        c = (int(rng.integers(0, lw)), int(rng.integers(0, lh)))
        cv2.circle(img, c, int(rng.integers(2, 12)), rng.uniform(0, 255, 3).tolist(), -1)
        if rng.random() < 0.5:
            cv2.rectangle(img, (c[0] - 12, c[1] - 20), (c[0] + 12, c[1] - 17),
                          (40, 200, 40) if rng.random() < 0.5 else (40, 40, 200), -1)
    small = np.clip(img, 0, 255).astype(np.uint8)
    out = cv2.resize(small, (W, H), interpolation=cv2.INTER_LINEAR)
    # per-pixel sensor-like noise (int16 tile, saturated add: cheap even at 4K)
    tile = np.round(rng.normal(0.0, float(rng.uniform(1.0, 7.0)), (256, 256, 1)))
    tile = np.repeat(tile, 3, axis=2).astype(np.int16)
    for y in range(0, H, 256):
        for x in range(0, W, 256):
            blk = out[y:y + 256, x:x + 256]
            h, w = blk.shape[:2]
            out[y:y + h, x:x + w] = cv2.add(blk, tile[:h, :w], dtype=cv2.CV_8U)
    return out


def draw_hud(rng: np.random.Generator, img: np.ndarray, side: str,
             frame_rect: tuple[int, int, int] | None) -> None:
    """Ability bar, stats panel, score box, and (optionally) the minimap frame/portraits."""
    H, W = img.shape[:2]
    u = H / 1080.0
    # ability bar (bottom centre)
    bw, bh = int(rng.uniform(0.28, 0.40) * W), int(rng.uniform(0.09, 0.14) * H)
    bx = int(W / 2 - bw / 2 + rng.uniform(-0.05, 0.05) * W)
    by = H - bh
    cv2.rectangle(img, (bx, by), (bx + bw, H), (int(rng.integers(12, 30)),) * 3, -1)
    cv2.rectangle(img, (bx, by), (bx + bw, H), (90, 140, 170), max(1, int(2 * u)))
    for i in range(6):
        x = bx + int((0.2 + 0.1 * i) * bw)
        cv2.rectangle(img, (x, by + int(0.15 * bh)), (x + int(0.08 * bw), by + int(0.55 * bh)),
                      rng.uniform(20, 230, 3).tolist(), -1)
    cv2.rectangle(img, (bx + int(0.2 * bw), by + int(0.65 * bh)),
                  (bx + int(0.85 * bw), by + int(0.75 * bh)), (40, 190, 40), -1)
    cv2.rectangle(img, (bx + int(0.2 * bw), by + int(0.8 * bh)),
                  (bx + int(0.85 * bw), by + int(0.9 * bh)), (200, 120, 30), -1)
    # score / fps box at the top right
    cv2.rectangle(img, (int(W * 0.82), 0), (W, int(0.03 * H)), (15, 15, 15), -1)
    if frame_rect is None:
        return
    x, y, s = frame_rect
    band = max(3, int(round(rng.uniform(0.008, 0.016) * H)))
    teal = tuple(int(c) for c in rng.uniform([25, 30, 10], [55, 60, 30]))
    gold = tuple(int(c) for c in rng.uniform([70, 120, 150], [110, 170, 200]))
    cv2.rectangle(img, (x - band, y - band), (x + s + band, y + s + band), teal, -1)
    cv2.rectangle(img, (x - band, y - band), (x + s + band, y + s + band), gold,
                  max(1, int(round(1.5 * u))))
    # decorative notch + diamond button at the top-left corner of the map
    cx, cy = (x, y) if side == "right" else (x + s, y)
    d = max(4, int(0.012 * H))
    cv2.fillConvexPoly(img, np.array([[cx, cy - d], [cx + d, cy], [cx, cy + d], [cx - d, cy]],
                                     np.int32), (160, 170, 40))
    # allied portraits above the frame
    pr = int(0.022 * H)
    for i in range(4):
        px = x + s - int((i + 0.6) * 2.4 * pr) if side == "right" else x + int((i + 0.6) * 2.4 * pr)
        py = y - band - int(2.2 * pr)
        if py - pr < 0:
            continue
        cv2.circle(img, (px, py), pr, rng.uniform(30, 220, 3).tolist(), -1)
        cv2.circle(img, (px, py), pr, gold, max(1, int(u)))
        cv2.rectangle(img, (px - pr, py + pr + 2), (px + pr, py + pr + int(5 * u)), (50, 190, 50), -1)
    # mute / settings buttons next to the frame
    bxs = x - band - int(0.05 * H) if side == "right" else x + s + band + int(0.01 * H)
    for i in range(2):
        cv2.rectangle(img, (bxs, y + s - int((0.03 + 0.035 * i) * H)),
                      (bxs + int(0.025 * H), y + s - int((0.005 + 0.035 * i) * H)),
                      (70, 70, 70), -1)


def random_scene(rng: np.random.Generator, size: int) -> Scene:
    """Random in-game minimap content."""
    r = _renderer()
    textures = r.textures()
    team = "ORDER" if rng.random() < 0.5 else "CHAOS"
    vision = [(u, v, 0.09) for u, v, _k, t in STRUCTURES if t == team and rng.random() < 0.8]
    champs = []
    for i in range(int(rng.integers(3, 11))):
        u, v = rng.uniform(0.05, 0.95, 2)
        rel = "enemy" if i % 2 else "ally"
        icon = _ICONS[int(rng.integers(len(_ICONS)))] if _ICONS else None
        champs.append(ChampionSprite(float(u), float(v), float(rng.uniform(0.042, 0.052)), rel,
                                     icon, recall=bool(rng.random() < 0.08)))
        if rel == "ally":
            vision.append((float(u), float(v), 0.08))
    for _ in range(int(rng.integers(0, 6))):
        vision.append((float(rng.uniform(0.1, 0.9)), float(rng.uniform(0.1, 0.9)), 0.06))
    minions = []
    for _ in range(int(rng.integers(0, 4))):  # minion chains along a lane
        t0, lane = rng.uniform(0.15, 0.85), int(rng.integers(3))
        for k in range(int(rng.integers(3, 7))):
            t = t0 + 0.02 * k
            uv = [(0.07, 1 - t), (t, 0.07), (t, 1 - t)][lane]
            minions.append((float(uv[0]), float(uv[1]), "ally" if rng.random() < 0.5 else "enemy"))
    pings = [(float(rng.uniform(0.1, 0.9)), float(rng.uniform(0.1, 0.9)),
              str(rng.choice(["ring_red.png", "ring2_yellow.png", "ping.png", "caution.png"])))
             for _ in range(int(rng.integers(0, 3)))]
    cu, cvv = rng.uniform(0.0, 0.72), rng.uniform(0.0, 0.84)
    return Scene(texture=str(rng.choice(textures)), size=size,
                 fog_alpha=float(rng.uniform(0.55, 0.70)) if rng.random() < 0.9 else 0.0,
                 vision=vision, champions=champs, minions=minions, pings=pings,
                 camera=(float(cu), float(cvv), float(cu + 0.275), float(cvv + 0.155)),
                 my_team=team, camp_icons=None if rng.random() < 0.7 else
                 [(u, v, "smallcamp.png") for u, v, _n in CAMPS if rng.random() < 0.5])


def degrade(rng: np.random.Generator, img: np.ndarray) -> np.ndarray:
    """Blur / rescale / JPEG like a streamed or compressed capture."""
    if rng.random() < 0.3:
        img = cv2.GaussianBlur(img, (0, 0), float(rng.uniform(0.4, 1.0)))
    if rng.random() < 0.2:
        h, w = img.shape[:2]
        k = float(rng.uniform(0.6, 0.85))
        img = cv2.resize(cv2.resize(img, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA),
                         (w, h), interpolation=cv2.INTER_LINEAR)
    if rng.random() < 0.5:
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(rng.integers(55, 95))])
        if ok:
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return img


def make_screenshot(rng: np.random.Generator, W: int, H: int, side: str = "right",
                    with_minimap: bool = True, size_frac: float | None = None
                    ) -> tuple[np.ndarray, tuple[int, int, int] | None]:
    """(BGR screenshot, (x, y, size) of the minimap or None)."""
    img = game_background(rng, W, H)
    lo, hi = SIZE_RANGE
    frac = size_frac if size_frac is not None else float(rng.uniform(lo + 0.02, hi - 0.04))
    s = int(round(frac * H))
    mx = int(round(rng.uniform(0.004, 0.028) * H))
    my = int(round(rng.uniform(0.004, 0.028) * H))
    x = W - mx - s if side == "right" else mx
    y = H - my - s
    frame = (x, y, s) if (with_minimap or rng.random() < 0.6) else None
    draw_hud(rng, img, side, frame)
    truth = None
    if with_minimap:
        img[y:y + s, x:x + s] = _renderer().render(random_scene(rng, s))
        truth = (x, y, s)
    elif frame is not None and rng.random() < 0.5:
        # empty frame: dark panel with noise / blobs (e.g. another game's HUD)
        panel = game_background(rng, s, s) // 2
        img[y:y + s, x:x + s] = panel
    return degrade(rng, img), truth


def _annotate(img: np.ndarray, truth, loc: MinimapLocation | None, origin: Rect) -> np.ndarray:
    out = img.copy()
    if truth is not None:
        x, y, s = truth
        cv2.rectangle(out, (x, y), (x + s, y + s), (0, 255, 0), 2)
    if loc is not None:
        r = loc.rect
        cv2.rectangle(out, (r.x - origin.x, r.y - origin.y),
                      (r.x - origin.x + r.w, r.y - origin.y + r.h), (0, 0, 255), 2)
        cv2.putText(out, f"{loc.score:.2f}", (r.x - origin.x, r.y - origin.y - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8 * img.shape[0] / 1080, (0, 0, 255), 2)
    return out


def _save_debug(name: str, img: np.ndarray) -> None:
    if DEBUG_DIR:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        h, w = img.shape[:2]
        k = min(1.0, 1280.0 / w)
        cv2.imwrite(os.path.join(DEBUG_DIR, name),
                    cv2.resize(img, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA))


# ======================================================================================
# Tests
# ======================================================================================


@pytest.fixture(scope="module")
def locator() -> MinimapLocator:
    loc = MinimapLocator(ASSETS)
    loc.verify(np.zeros((64, 64, 3), np.uint8))  # build templates once
    return loc


def test_fallback_rect() -> None:
    win = Rect(100, 50, 1920, 1080)
    r = fallback_rect(win)
    assert r.w == r.h == round(0.265 * 1080)
    assert (r.x + r.w, r.y + r.h) == (100 + 1920, 50 + 1080)
    left = fallback_rect(win, "left")
    assert (left.x, left.y + left.h, left.w) == (100, 50 + 1080, r.w)
    tiny = fallback_rect(Rect(0, 0, 100, 400))
    assert tiny.w == tiny.h == 100 and tiny.x == 0


def test_invalid_inputs_never_raise(locator: MinimapLocator) -> None:
    origin = Rect(0, 0, 10, 10)
    for bad in (None, "x", np.zeros((0, 0, 3), np.uint8), np.zeros((50, 50, 3), np.uint8),
                np.zeros((300, 400, 5), np.uint8), np.full((720, 1280, 3), np.nan, np.float32)):
        assert locator.locate(bad, origin) is None  # type: ignore[arg-type]
        assert locator.verify(bad) == 0.0  # type: ignore[arg-type]
    black = np.zeros((720, 1280, 3), np.uint8)
    assert locator.locate(black, origin) is None
    assert locator.verify(black[:200, :200]) == 0.0


def test_missing_assets_disable_gracefully(tmp_path: Path) -> None:
    loc = MinimapLocator(tmp_path)  # no textures here
    img = np.random.default_rng(0).integers(0, 255, (720, 1280, 3), dtype=np.uint8)
    assert loc.locate(img, Rect(0, 0, 1280, 720)) is None
    assert loc.verify(img[:300, :300]) == 0.0


def test_verify_true_vs_random_crops(locator: MinimapLocator) -> None:
    rng = np.random.default_rng(11)
    true_scores, rand_scores = [], []
    for i in range(8):
        img, truth = make_screenshot(rng, 1920, 1080, side="right" if i % 2 else "left")
        x, y, s = truth
        true_scores.append(locator.verify(img[y:y + s, x:x + s]))
        for _ in range(3):  # random crops elsewhere (not overlapping the minimap much)
            cs = int(rng.integers(150, 450))
            cx, cy = int(rng.integers(0, 1920 - cs)), int(rng.integers(0, 1080 - cs))
            if abs(cx - x) < s // 2 and abs(cy - y) < s // 2:
                continue
            rand_scores.append(locator.verify(img[cy:cy + cs, cx:cx + cs]))
    print(f"verify: true min {min(true_scores):.3f} mean {np.mean(true_scores):.3f} | "
          f"random max {max(rand_scores):.3f} mean {np.mean(rand_scores):.3f}")
    assert min(true_scores) >= LOCATE_MIN_SCORE
    assert max(rand_scores) < 0.35
    assert min(true_scores) - max(rand_scores) > 0.2


def _check_located(loc: MinimapLocation | None, truth, origin: Rect, H: int) -> str | None:
    if loc is None:
        return "not found"
    x, y, s = truth
    r = loc.rect
    if abs(r.w - s) > 0.03 * s or r.w != r.h:
        return f"size {r.w} vs {s}"
    if abs(r.x - origin.x - x) > 0.015 * H or abs(r.y - origin.y - y) > 0.015 * H:
        return f"pos ({r.x - origin.x}, {r.y - origin.y}) vs ({x}, {y})"
    return None


def test_locate_random_screenshots(locator: MinimapLocator) -> None:
    """>= 30 random cases across resolutions, sizes and sides (side="auto")."""
    rng = np.random.default_rng(2024)
    n, failures, times_1080, scores = 32, [], [], []
    for i in range(n):
        W, H = RESOLUTIONS[i % len(RESOLUTIONS)]
        side = "left" if i % 3 == 2 else "right"
        img, truth = make_screenshot(rng, W, H, side=side)
        origin = Rect(int(rng.integers(-1920, 1920)), int(rng.integers(0, 400)), W, H)
        t0 = time.perf_counter()
        loc = locator.locate(img, origin, side="auto")
        dt = time.perf_counter() - t0
        if (W, H) == (1920, 1080):
            times_1080.append(dt)
        err = _check_located(loc, truth, origin, H)
        if loc is not None:
            scores.append(loc.score)
            if err is None:
                assert loc.side == side and loc.method == "auto"
        if err is not None:
            failures.append(f"case {i} {W}x{H} {side} size={truth[2] / H:.3f}: {err}")
        if i < 6 or err is not None:
            _save_debug(f"locate_{i:02d}_{W}x{H}_{side}{'_FAIL' if err else ''}.png",
                        _annotate(img, truth, loc, origin))
    print(f"located {n - len(failures)}/{n}; scores min {min(scores):.3f} "
          f"median {np.median(scores):.3f}; 1080p locate time: median "
          f"{1000 * np.median(times_1080):.0f} ms, max {1000 * max(times_1080):.0f} ms")
    assert not failures, "\n".join(failures)
    assert np.median(times_1080) < 0.4


def test_locate_explicit_side_and_extreme_sizes(locator: MinimapLocator) -> None:
    rng = np.random.default_rng(5)
    cases = [(1920, 1080, "right", 0.15), (1920, 1080, "left", 0.48), (1280, 720, "right", 0.48),
             (3840, 2160, "left", 0.16), (2560, 1440, "right", 0.237)]
    for W, H, side, frac in cases:
        img, truth = make_screenshot(rng, W, H, side=side, size_frac=frac)
        origin = Rect(0, 0, W, H)
        loc = locator.locate(img, origin, side=side)
        err = _check_located(loc, truth, origin, H)
        assert err is None, f"{W}x{H} {side} {frac}: {err}"
        # searching only the other corner must not find it there
        other = locator.locate(img, origin, side="left" if side == "right" else "right")
        assert other is None or _check_located(other, truth, origin, H) is None


def test_no_minimap_returns_none(locator: MinimapLocator) -> None:
    rng = np.random.default_rng(99)
    n, hits = 30, []
    for i in range(n):
        W, H = RESOLUTIONS[i % 4]
        img, _ = make_screenshot(rng, W, H, side="right" if i % 2 else "left", with_minimap=False)
        loc = locator.locate(img, Rect(0, 0, W, H))
        if loc is not None:
            hits.append(loc.score)
            _save_debug(f"negative_{i:02d}_FP.png", _annotate(img, None, loc, Rect(0, 0, W, H)))
    print(f"false positives: {len(hits)}/{n} {hits}")
    assert len(hits) <= 0.1 * n


def test_debug_render_and_threads(locator: MinimapLocator) -> None:
    """Concurrent locate() calls give the same answer (templates shared read-only)."""
    import threading

    rng = np.random.default_rng(3)
    img, truth = make_screenshot(rng, 1920, 1080)
    origin = Rect(0, 0, 1920, 1080)
    results: list[MinimapLocation | None] = []
    lock = threading.Lock()

    def run() -> None:
        r = locator.locate(img, origin)
        with lock:
            results.append(r)

    ths = [threading.Thread(target=run) for _ in range(3)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(30)
    assert len(results) == 3 and all(r is not None for r in results)
    assert len({r.rect for r in results if r is not None}) == 1
    assert _check_located(results[0], truth, origin, 1080) is None
